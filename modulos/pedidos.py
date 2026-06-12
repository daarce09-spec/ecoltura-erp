# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash
from db.conexion import obtener_conexion
from datetime import datetime, timedelta

pedidos_bp = Blueprint("pedidos_bp", __name__)


# ─────────────────────────────────────────────
#  BANDEJA PRINCIPAL
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos")
def bandeja_pedidos():
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.nombre_contacto, p.celular, p.cedula,
                   p.total_estimado, p.estado, p.creado, p.notas,
                   COALESCE(c.nombre, p.nombre_contacto) AS cliente_nombre
            FROM pedidos p
            LEFT JOIN clientes c ON c.id = p.cliente_id
            ORDER BY
                CASE p.estado WHEN 'Pendiente' THEN 0 WHEN 'Resuelto' THEN 1 ELSE 2 END,
                p.creado DESC
        """)
        pedidos = cur.fetchall()

        cur.execute("SELECT COUNT(*) FROM pedidos WHERE estado='Pendiente'")
        pendientes = cur.fetchone()[0]

    return render_template("pedidos_bandeja.html",
                           pedidos=pedidos, pendientes=pendientes)


# ─────────────────────────────────────────────
#  DETALLE DE UN PEDIDO
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>")
def pedido_detalle(id):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.nombre_contacto, p.celular, p.cedula,
                   p.total_estimado, p.estado, p.creado, p.notas,
                   p.cliente_id, p.venta_id
            FROM pedidos p WHERE p.id = %s
        """, (id,))
        pedido = cur.fetchone()

        cur.execute("""
            SELECT pd.producto_id, pr.nombre, pr.unidad,
                   pd.cantidad, pd.precio_unitario,
                   pd.cantidad * pd.precio_unitario AS subtotal
            FROM pedidos_detalle pd
            JOIN productos pr ON pr.id = pd.producto_id
            WHERE pd.pedido_id = %s
            ORDER BY pr.nombre
        """, (id,))
        detalle = cur.fetchall()

    return render_template("pedido_detalle.html",
                           pedido=pedido, detalle=detalle)


# ─────────────────────────────────────────────
#  CONVERTIR PEDIDO EN VENTA
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/convertir", methods=["POST"])
def pedido_convertir(id):
    with obtener_conexion() as conn:
        cur = conn.cursor()

        # Verificar estado
        cur.execute("SELECT estado, cliente_id, nombre_contacto, celular, total_estimado FROM pedidos WHERE id=%s", (id,))
        row = cur.fetchone()
        if not row or row[0] != 'Pendiente':
            flash("Este pedido ya fue procesado.", "warning")
            return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

        estado, cliente_id, nombre, celular, total_est = row

        # Obtener detalle del pedido
        cur.execute("""
            SELECT producto_id, cantidad, precio_unitario
            FROM pedidos_detalle WHERE pedido_id = %s
        """, (id,))
        items = cur.fetchall()

        fecha_hora = (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")

        # Insertar venta
        cur.execute("""
            INSERT INTO ventas (cliente_id, fecha_venta, subtotal, descuento, iva, total, metodo_pago, estado)
            VALUES (%s, %s, 0, 0, 0, 0, 'Por cobrar', 'Facturado')
            RETURNING id
        """, (cliente_id, fecha_hora))
        venta_id = cur.fetchone()[0]

        subtotal_total = 0
        total_total    = 0

        for producto_id, cantidad, precio in items:
            sub_l = float(cantidad) * float(precio)
            subtotal_total += sub_l
            total_total    += sub_l

            # Detalle de venta
            cur.execute("""
                INSERT INTO ventas_detalle
                    (venta_id, producto_id, cantidad, precio_unitario,
                     subtotal_linea, descuento, total_linea)
                VALUES (%s, %s, %s, %s, %s, 0, %s)
            """, (venta_id, producto_id, cantidad, precio, sub_l, sub_l))

            # Movimiento de inventario (salida)
            cur.execute("""
                INSERT INTO inventario_movimientos (producto_id, fecha, tipo, cantidad)
                VALUES (%s, %s, 'F', %s)
            """, (producto_id, fecha_hora, cantidad))

            # Reducir inventario_semanal (FIFO)
            cur.execute("""
                UPDATE inventario_semanal
                SET cantidad_disponible = cantidad_disponible - %s
                WHERE id = (
                    SELECT id FROM inventario_semanal
                    WHERE producto_id = %s AND cantidad_disponible >= %s
                    ORDER BY fecha_semana ASC LIMIT 1
                )
            """, (cantidad, producto_id, cantidad))

        # Actualizar totales de la venta
        cur.execute("""
            UPDATE ventas SET subtotal=%s, total=%s WHERE id=%s
        """, (subtotal_total, total_total, venta_id))

        # Marcar pedido como Resuelto
        cur.execute("""
            UPDATE pedidos SET estado='Resuelto', venta_id=%s, gestionado=NOW()
            WHERE id=%s
        """, (venta_id, id))

        conn.commit()

    flash(f"Pedido #{id} convertido en venta #{venta_id} correctamente.", "success")
    return redirect(url_for("ventas_bp.ticket_venta", venta_id=venta_id))


# ─────────────────────────────────────────────
#  RECHAZAR PEDIDO
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/rechazar", methods=["POST"])
def pedido_rechazar(id):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE pedidos SET estado='Rechazado', gestionado=NOW()
            WHERE id=%s AND estado='Pendiente'
        """, (id,))
        conn.commit()

    flash(f"Pedido #{id} rechazado.", "danger")
    return redirect(url_for("pedidos_bp.bandeja_pedidos"))
