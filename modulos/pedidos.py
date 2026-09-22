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
            SELECT pd.id, pd.producto_id, pr.nombre, pr.unidad,
                   pd.cantidad, pd.precio_unitario,
                   pd.cantidad * pd.precio_unitario AS subtotal
            FROM pedidos_detalle pd
            JOIN productos pr ON pr.id = pd.producto_id
            WHERE pd.pedido_id = %s
            ORDER BY pr.nombre
        """, (id,))
        detalle = cur.fetchall()

        # Catálogo completo (no solo visible_web) para poder agregar
        # cualquier producto al pedido desde el admin.
        cur.execute("SELECT id, nombre, unidad, precio FROM productos ORDER BY nombre")
        catalogo = cur.fetchall()

    return render_template("pedido_detalle.html",
                           pedido=pedido, detalle=detalle, catalogo=catalogo)


# ─────────────────────────────────────────────
#  Helper: recalcular el total estimado tras cualquier edición
# ─────────────────────────────────────────────
def _recalcular_total(cur, pedido_id):
    cur.execute("""
        UPDATE pedidos SET total_estimado = (
            SELECT COALESCE(SUM(cantidad * precio_unitario), 0)
            FROM pedidos_detalle WHERE pedido_id = %s
        ) WHERE id = %s
    """, (pedido_id, pedido_id))


def _pedido_editable(cur, id):
    """Solo se puede editar mientras el pedido siga Pendiente."""
    cur.execute("SELECT estado FROM pedidos WHERE id = %s", (id,))
    row = cur.fetchone()
    return row and row[0] == 'Pendiente'


# ─────────────────────────────────────────────
#  EDITAR CANTIDAD DE UNA LÍNEA (0 o menos = eliminarla)
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/detalle/<int:detalle_id>/actualizar", methods=["POST"])
def detalle_actualizar(id, detalle_id):
    nueva_cantidad = float(request.form.get("cantidad", 0))

    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _pedido_editable(cur, id):
            flash("Este pedido ya no se puede editar.", "warning")
            return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

        if nueva_cantidad <= 0:
            cur.execute("DELETE FROM pedidos_detalle WHERE id=%s AND pedido_id=%s", (detalle_id, id))
            flash("Producto eliminado del pedido.", "success")
        else:
            cur.execute("UPDATE pedidos_detalle SET cantidad=%s WHERE id=%s AND pedido_id=%s",
                        (nueva_cantidad, detalle_id, id))
            flash("Cantidad actualizada.", "success")

        _recalcular_total(cur, id)
        conn.commit()

    return redirect(url_for("pedidos_bp.pedido_detalle", id=id))


# ─────────────────────────────────────────────
#  ELIMINAR UNA LÍNEA DEL PEDIDO
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/detalle/<int:detalle_id>/eliminar", methods=["POST"])
def detalle_eliminar(id, detalle_id):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _pedido_editable(cur, id):
            flash("Este pedido ya no se puede editar.", "warning")
            return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

        cur.execute("DELETE FROM pedidos_detalle WHERE id=%s AND pedido_id=%s", (detalle_id, id))
        _recalcular_total(cur, id)
        conn.commit()

    flash("Producto eliminado del pedido.", "success")
    return redirect(url_for("pedidos_bp.pedido_detalle", id=id))


# ─────────────────────────────────────────────
#  AGREGAR UN PRODUCTO NUEVO AL PEDIDO
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/detalle/agregar", methods=["POST"])
def detalle_agregar(id):
    try:
        producto_id = int(request.form.get("producto_id", 0))
        cantidad    = float(request.form.get("cantidad", 0))
    except ValueError:
        flash("Datos inválidos.", "danger")
        return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

    if not producto_id or cantidad <= 0:
        flash("Elegí un producto y una cantidad válida.", "warning")
        return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _pedido_editable(cur, id):
            flash("Este pedido ya no se puede editar.", "warning")
            return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

        cur.execute("SELECT precio FROM productos WHERE id = %s", (producto_id,))
        row = cur.fetchone()
        if not row:
            flash("Producto no encontrado.", "danger")
            return redirect(url_for("pedidos_bp.pedido_detalle", id=id))
        precio = float(row[0])

        # Si el producto ya está en el pedido, suma la cantidad en vez de duplicar la línea
        cur.execute("SELECT id FROM pedidos_detalle WHERE pedido_id=%s AND producto_id=%s",
                    (id, producto_id))
        existente = cur.fetchone()
        if existente:
            cur.execute("UPDATE pedidos_detalle SET cantidad = cantidad + %s WHERE id = %s",
                        (cantidad, existente[0]))
        else:
            cur.execute("""
                INSERT INTO pedidos_detalle (pedido_id, producto_id, cantidad, precio_unitario)
                VALUES (%s, %s, %s, %s)
            """, (id, producto_id, cantidad, precio))

        _recalcular_total(cur, id)
        conn.commit()

    flash("Producto agregado al pedido.", "success")
    return redirect(url_for("pedidos_bp.pedido_detalle", id=id))


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
