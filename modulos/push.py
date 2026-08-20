# -*- coding: utf-8 -*-
import os
import json
from flask import Blueprint, request, jsonify
from pywebpush import webpush, WebPushException
from db.conexion import obtener_conexion

push_bp = Blueprint("push_bp", __name__)

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")
VAPID_EMAIL = os.environ.get("VAPID_EMAIL", "mailto:info@ecoltura.local")


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
    celular = data.get("celular")

    if not endpoint or not p256dh or not auth:
        return jsonify({"ok": False, "error": "Suscripción incompleta"}), 400

    with obtener_conexion() as conn:
        cur = conn.cursor()

        # Si viene de la tienda pública, resolver cliente_id a partir del celular
        if celular and not cliente_id:
            cur.execute("SELECT id FROM clientes WHERE celular = %s ORDER BY id DESC LIMIT 1", (celular,))
            row = cur.fetchone()
            if row:
                cliente_id = row[0]

        cur.execute("""
            INSERT INTO push_subscriptions (cliente_id, endpoint, p256dh, auth, tipo)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (endpoint) DO UPDATE
                SET p256dh = EXCLUDED.p256dh,
                    auth = EXCLUDED.auth,
                    activo = TRUE
        """, (cliente_id, endpoint, p256dh, auth, tipo))
        conn.commit()

    return jsonify({"ok": True})


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
        return False


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
