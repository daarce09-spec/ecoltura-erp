# -*- coding: utf-8 -*-
import os
import json
import threading
from flask import Blueprint, request, jsonify
from pywebpush import webpush, WebPushException
from db.conexion import obtener_conexion

push_bp = Blueprint("push_bp", __name__)


def _solo_8_digitos(valor):
    """Deja solo los 8 dígitos del celular (ignora espacios, guiones y el +506)."""
    d = "".join(c for c in str(valor or "") if c.isdigit())
    if len(d) > 8 and d.startswith("506"):
        d = d[3:]
    return d

# Mismo criterio del lado de la base: últimos 8 dígitos, sin espacios ni guiones.
def _sql_cel(columna):
    return f"RIGHT(REGEXP_REPLACE(COALESCE({columna}, ''), '[^0-9]', '', 'g'), 8)"

# py_vapid.Vapid.from_string() espera SOLO el contenido base64 de la llave,
# sin los encabezados "-----BEGIN/END PRIVATE KEY-----" — si se los pasamos,
# los trata como parte del base64 y la llave queda corrupta.
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY_B64")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")
VAPID_EMAIL = os.environ.get("VAPID_EMAIL", "mailto:info@ecoltura.local")


# ─────────────────────────────────────────────
#  La suscripción guarda también el celular con el que se activó la campana.
#  Así, si ese celular todavía no existía en Clientes, se puede vincular
#  más tarde (cuando el cliente haga su primer pedido o se registre).
# ─────────────────────────────────────────────
def _asegurar_columna_celular():
    try:
        with obtener_conexion() as conn:
            cur = conn.cursor()
            cur.execute("ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS celular VARCHAR(30)")
            conn.commit()
    except Exception as ex:
        print("No se pudo asegurar la columna celular en push_subscriptions:", ex)

_asegurar_columna_celular()


def vincular_suscripciones_huerfanas(cur):
    """Vincula con su cliente las suscripciones de clientes que quedaron sin
    cliente_id, comparando el celular. Si varios clientes comparten celular,
    se usa el más reciente (igual que al activar la campana). Devuelve cuántas vinculó."""
    cur.execute("""
        UPDATE push_subscriptions ps
           SET cliente_id = (SELECT MAX(c.id) FROM clientes c WHERE {c_cel} = {ps_cel})
         WHERE ps.tipo = 'cliente'
           AND ps.cliente_id IS NULL
           AND ps.celular IS NOT NULL
           AND EXISTS (SELECT 1 FROM clientes c WHERE {c_cel} = {ps_cel})
    """.format(c_cel=_sql_cel("c.celular"), ps_cel=_sql_cel("ps.celular")))
    return cur.rowcount


# ─────────────────────────────────────────────
#  API: entregar la llave pública al navegador
# ─────────────────────────────────────────────
@push_bp.route("/api/push/public-key")
def push_public_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


# ─────────────────────────────────────────────
#  API: guardar una suscripción nueva
# ─────────────────────────────────────────────
@push_bp.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    data = request.get_json(force=True)
    sub = data.get("subscription", {})
    tipo = data.get("tipo", "admin")
    cliente_id = data.get("cliente_id")

    endpoint = sub.get("endpoint")
    keys = sub.get("keys", {})
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    celular = _solo_8_digitos(data.get("celular")) or None

    if not endpoint or not p256dh or not auth:
        return jsonify({"ok": False, "error": "Suscripción incompleta"}), 400

    with obtener_conexion() as conn:
        cur = conn.cursor()

        # Si viene de la tienda pública, resolver cliente_id a partir del celular
        if celular and not cliente_id:
            cur.execute(f"SELECT id FROM clientes WHERE {_sql_cel('celular')} = %s ORDER BY id DESC LIMIT 1", (celular,))
            row = cur.fetchone()
            if row:
                cliente_id = row[0]

        # Un único registro por cliente+tipo: borra cualquier suscripción previa
        # de ese cliente antes de guardar la nueva (los endpoints pueden cambiar,
        # sobre todo en iOS, cada vez que se desuscribe y vuelve a suscribir).
        if cliente_id:
            cur.execute("""
                DELETE FROM push_subscriptions
                WHERE cliente_id = %s AND tipo = %s AND endpoint != %s
            """, (cliente_id, tipo, endpoint))

        cur.execute("""
            INSERT INTO push_subscriptions (cliente_id, endpoint, p256dh, auth, tipo, celular)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (endpoint) DO UPDATE
                SET p256dh = EXCLUDED.p256dh,
                    auth = EXCLUDED.auth,
                    activo = TRUE,
                    celular = COALESCE(EXCLUDED.celular, push_subscriptions.celular),
                    cliente_id = COALESCE(EXCLUDED.cliente_id, push_subscriptions.cliente_id)
        """, (cliente_id, endpoint, p256dh, auth, tipo, celular or None))
        conn.commit()

    return jsonify({"ok": True, "vinculado": cliente_id is not None})


# ─────────────────────────────────────────────
#  Función interna: enviar una notificación a una suscripción
# ─────────────────────────────────────────────
def enviar_push(subscription_row, titulo, cuerpo, url="/"):
    """subscription_row = (endpoint, p256dh, auth)"""
    endpoint, p256dh, auth = subscription_row
    try:
        webpush(
            subscription_info={
                "endpoint": endpoint,
                "keys": {"p256dh": p256dh, "auth": auth}
            },
            data=json.dumps({"title": titulo, "body": cuerpo, "url": url}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_EMAIL}
        )
        return True
    except WebPushException as ex:
        print(f"Error enviando push: {ex}")
        status = getattr(ex.response, "status_code", None)
        if status in (404, 410):
            # La suscripción expiró o fue invalidada por el navegador
            # (frecuente en iOS al reinstalar la app) — se desactiva para
            # que no se siga intentando usar, y el cliente pueda
            # reactivar notificaciones la próxima vez que abra la tienda.
            try:
                with obtener_conexion() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "UPDATE push_subscriptions SET activo = FALSE WHERE endpoint = %s",
                        (endpoint,)
                    )
                    conn.commit()
            except Exception as db_ex:
                print(f"Error desactivando suscripción expirada: {db_ex}")
        return False


# ─────────────────────────────────────────────
#  Avisar a TODOS los celulares con las notificaciones del admin activadas
#  (se usa cuando llega un pedido web nuevo). Corre en segundo plano para
#  no hacer esperar al cliente que está confirmando su pedido.
# ─────────────────────────────────────────────
def notificar_admins(titulo, cuerpo, url="/"):
    def _enviar():
        try:
            with obtener_conexion() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT endpoint, p256dh, auth FROM push_subscriptions
                    WHERE tipo = 'admin' AND activo = TRUE
                """)
                subs = cur.fetchall()
            for s in subs:
                enviar_push(s, titulo, cuerpo, url=url)
        except Exception as ex:
            print(f"Error notificando a los admins: {ex}")
    threading.Thread(target=_enviar, daemon=True).start()


# ─────────────────────────────────────────────
#  Avisar a UN cliente por su celular (últimos 8 dígitos), en segundo plano.
#  Se usa, por ejemplo, cuando su pedido pasa a Entregado.
# ─────────────────────────────────────────────
def notificar_cliente(celular, titulo, cuerpo, url="/"):
    cel = _solo_8_digitos(celular)[-8:]
    if len(cel) != 8:
        return
    def _enviar():
        try:
            with obtener_conexion() as conn:
                cur = conn.cursor()
                cur.execute(f"""
                    SELECT endpoint, p256dh, auth FROM push_subscriptions
                    WHERE tipo = 'cliente' AND activo = TRUE
                      AND ({_sql_cel('celular')} = %s
                           OR cliente_id IN (SELECT id FROM clientes WHERE {_sql_cel('celular')} = %s))
                """, (cel, cel))
                subs = cur.fetchall()
            for s in subs:
                enviar_push(s, titulo, cuerpo, url=url)
        except Exception as ex:
            print(f"Error notificando al cliente: {ex}")
    threading.Thread(target=_enviar, daemon=True).start()


# ─────────────────────────────────────────────
#  API: enviar notificación manual a UN cliente (desde /menu)
# ─────────────────────────────────────────────
@push_bp.route("/api/push/enviar-cliente", methods=["POST"])
def push_enviar_cliente():
    data = request.get_json(force=True)
    cliente_id = data.get("cliente_id")
    mensaje = (data.get("mensaje") or "").strip()

    if not cliente_id or not mensaje:
        return jsonify({"ok": False, "error": "Faltan datos"}), 400

    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT endpoint, p256dh, auth FROM push_subscriptions
            WHERE cliente_id = %s AND tipo = 'cliente' AND activo = TRUE
        """, (cliente_id,))
        subs = cur.fetchall()

    if not subs:
        return jsonify({"ok": False, "error": "Este cliente no tiene notificaciones activadas"}), 400

    enviados = 0
    for s in subs:
        if enviar_push(s, "ECOLTURA", mensaje):
            enviados += 1

    return jsonify({"ok": True, "enviados": enviados})


# ─────────────────────────────────────────────
#  API: enviar una notificación de PRUEBA al admin
# ─────────────────────────────────────────────
@push_bp.route("/api/push/test", methods=["POST"])
def push_test():
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT endpoint, p256dh, auth FROM push_subscriptions
            WHERE tipo = 'admin' AND activo = TRUE
        """)
        subs = cur.fetchall()

    if not subs:
        return jsonify({"ok": False, "error": "No hay suscripciones de admin registradas"}), 400

    enviados = 0
    for s in subs:
        if enviar_push(s, "ECOLTURA", "🔔 Notificación de prueba — si ves esto, funciona!"):
            enviados += 1

    return jsonify({"ok": True, "enviados": enviados, "total": len(subs)})
