# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, jsonify, request
from db.conexion import obtener_conexion
from modulos.push import enviar_push

recordatorios_bp = Blueprint("recordatorios_bp", __name__)


# ─────────────────────────────────────────────
#  PÁGINA PRINCIPAL
# ─────────────────────────────────────────────
@recordatorios_bp.route("/recordatorios")
def recordatorios():
    with obtener_conexion() as conn:
        cur = conn.cursor()

        # Clientes + su estado de suscripción push
        cur.execute("""
            SELECT c.id, c.nombre, c.celular, c.dias_recordatorio,
                   EXISTS(
                       SELECT 1 FROM push_subscriptions ps
                       WHERE ps.cliente_id = c.id AND ps.tipo = 'cliente' AND ps.activo = TRUE
                   ) AS suscrito,
                   (SELECT MAX(v.fecha_venta) FROM ventas v WHERE v.cliente_id = c.id AND v.estado != 'Anulado') AS ultima_compra
            FROM clientes c
            ORDER BY c.nombre
        """)
        clientes = cur.fetchall()

        # Historial reciente (últimos 50 envíos)
        cur.execute("""
            SELECT nh.creado, c.nombre, nh.mensaje, nh.enviado
            FROM notificaciones_historial nh
            JOIN clientes c ON c.id = nh.cliente_id
            ORDER BY nh.creado DESC
            LIMIT 50
        """)
        historial = cur.fetchall()

    return render_template("recordatorios.html", clientes=clientes, historial=historial)


# ─────────────────────────────────────────────
#  Función interna: arma el mensaje personalizado según la última compra
# ─────────────────────────────────────────────
def generar_mensaje(cliente_id, nombre):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.nombre
            FROM ventas v
            JOIN ventas_detalle d ON d.venta_id = v.id
            JOIN productos p ON p.id = d.producto_id
            WHERE v.cliente_id = %s AND v.estado != 'Anulado'
              AND v.id = (
                  SELECT v2.id FROM ventas v2
                  WHERE v2.cliente_id = %s AND v2.estado != 'Anulado'
                  ORDER BY v2.fecha_venta DESC, v2.id DESC
                  LIMIT 1
              )
            ORDER BY d.cantidad DESC
            LIMIT 2
        """, (cliente_id, cliente_id))
        productos = [r[0].strip() for r in cur.fetchall()]

    primer_nombre = (nombre or "").split(" ")[0]

    if not productos:
        return None  # sin compras previas, no aplica este tipo de recordatorio

    if len(productos) == 1:
        return f"🍅 Hola {primer_nombre}, tu {productos[0]} de siempre ya casi se acaba — ¿armamos tu pedido de esta semana?"

    return f"🍅 Hola {primer_nombre}, tu {productos[0]} y {productos[1]} de siempre ya casi se acaban — ¿armamos tu pedido de esta semana?"


# ─────────────────────────────────────────────
#  API: vista previa del envío masivo (no envía nada todavía)
# ─────────────────────────────────────────────
@recordatorios_bp.route("/api/recordatorios/preview")
def recordatorios_preview():
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT c.id, c.nombre,
                   EXISTS(
                       SELECT 1 FROM push_subscriptions ps
                       WHERE ps.cliente_id = c.id AND ps.tipo = 'cliente' AND ps.activo = TRUE
                   ) AS suscrito
            FROM clientes c
            ORDER BY c.nombre
        """)
        clientes = cur.fetchall()

    resultado = []
    for cid, nombre, suscrito in clientes:
        mensaje = generar_mensaje(cid, nombre)
        if mensaje is None:
            continue  # sin compras, se excluye de la vista previa
        resultado.append({
            "cliente_id": cid,
            "nombre": nombre,
            "mensaje": mensaje,
            "suscrito": suscrito
        })

    return jsonify(resultado)


# ─────────────────────────────────────────────
#  API: enviar masivo a los clientes seleccionados
# ─────────────────────────────────────────────
@recordatorios_bp.route("/api/recordatorios/enviar-masivo", methods=["POST"])
def recordatorios_enviar_masivo():
    data = request.get_json(force=True)
    items = data.get("items", [])  # [{cliente_id, mensaje}, ...]

    enviados = 0
    fallidos = 0

    with obtener_conexion() as conn:
        cur = conn.cursor()
        for item in items:
            cliente_id = item.get("cliente_id")
            mensaje = item.get("mensaje")

            cur.execute("""
                SELECT endpoint, p256dh, auth FROM push_subscriptions
                WHERE cliente_id = %s AND tipo = 'cliente' AND activo = TRUE
            """, (cliente_id,))
            subs = cur.fetchall()

            exito = False
            for s in subs:
                if enviar_push(s, "ECOLTURA", mensaje):
                    exito = True

            cur.execute("""
                INSERT INTO notificaciones_historial (cliente_id, mensaje, enviado)
                VALUES (%s, %s, %s)
            """, (cliente_id, mensaje, exito))

            if exito:
                enviados += 1
            else:
                fallidos += 1

        conn.commit()

    return jsonify({"ok": True, "enviados": enviados, "fallidos": fallidos})


# ─────────────────────────────────────────────
#  API: enviar individual (con registro en historial)
# ─────────────────────────────────────────────
@recordatorios_bp.route("/api/recordatorios/enviar-individual", methods=["POST"])
def recordatorios_enviar_individual():
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

        exito = False
        for s in subs:
            if enviar_push(s, "ECOLTURA", mensaje):
                exito = True

        cur.execute("""
            INSERT INTO notificaciones_historial (cliente_id, mensaje, enviado)
            VALUES (%s, %s, %s)
        """, (cliente_id, mensaje, exito))
        conn.commit()

    return jsonify({"ok": exito})
