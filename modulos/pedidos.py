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
                   p.cliente_id, p.venta_id,
                   COALESCE(p.descuento_tipo, 'monto'), COALESCE(p.descuento_valor, 0)
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
        # cualquier producto al pedido desde el admin. precio se convierte
        # a float porque Decimal no es serializable directo a JSON.
        cur.execute("SELECT id, nombre, unidad, precio FROM productos ORDER BY nombre")
        catalogo = [(r[0], r[1], r[2], float(r[3])) for r in cur.fetchall()]

        # Pesos preestablecidos por producto (los mismos botones "1 kg / 500 g"
        # que usa la tienda pública), para poder elegirlos igual desde el admin.
        cur.execute("SELECT producto_id, gramos FROM producto_pesos ORDER BY producto_id, orden")
        pesos_por_producto = {}
        for producto_id, gramos in cur.fetchall():
            pesos_por_producto.setdefault(producto_id, []).append(gramos)

    return render_template("pedido_detalle.html",
                           pedido=pedido, detalle=detalle, catalogo=catalogo,
                           pesos_por_producto=pesos_por_producto)


# ─────────────────────────────────────────────
#  Helper: recalcular el total estimado tras cualquier edición
# ─────────────────────────────────────────────
def _calcular_descuento(subtotal, tipo, valor):
    """Monto del descuento (nunca mayor al subtotal)."""
    valor = float(valor or 0)
    if valor <= 0 or subtotal <= 0:
        return 0.0
    if tipo == 'porcentaje':
        valor = min(valor, 100.0)
        return round(subtotal * valor / 100.0, 2)
    return round(min(valor, subtotal), 2)


def _totales(cur, pedido_id):
    """Devuelve (subtotal, descuento_monto, total, tipo, valor) del pedido."""
    cur.execute("""
        SELECT COALESCE(SUM(cantidad * precio_unitario), 0)
        FROM pedidos_detalle WHERE pedido_id = %s
    """, (pedido_id,))
    subtotal = float(cur.fetchone()[0])
    cur.execute("""
        SELECT COALESCE(descuento_tipo, 'monto'), COALESCE(descuento_valor, 0)
        FROM pedidos WHERE id = %s
    """, (pedido_id,))
    tipo, valor = cur.fetchone()
    desc = _calcular_descuento(subtotal, tipo, valor)
    return subtotal, desc, round(subtotal - desc, 2), tipo, float(valor)


def _recalcular_total(cur, pedido_id):
    """total_estimado = subtotal - descuento (lo que ve la bandeja)."""
    _, _, total, _, _ = _totales(cur, pedido_id)
    cur.execute("UPDATE pedidos SET total_estimado = %s WHERE id = %s", (total, pedido_id))


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
#  JSON: cambiar cantidad (0 = eliminar) y descuento, devuelven totales
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/detalle/<int:detalle_id>/cantidad", methods=["POST"])
def detalle_cantidad_json(id, detalle_id):
    data = request.get_json(silent=True) or {}
    try:
        cantidad = float(data.get("cantidad", 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Cantidad inválida"), 400

    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _pedido_editable(cur, id):
            return jsonify(ok=False, error="Este pedido ya no se puede editar."), 409

        linea = None
        if cantidad <= 0:
            cur.execute("DELETE FROM pedidos_detalle WHERE id=%s AND pedido_id=%s", (detalle_id, id))
        else:
            cur.execute("""
                UPDATE pedidos_detalle SET cantidad=%s WHERE id=%s AND pedido_id=%s
                RETURNING cantidad * precio_unitario
            """, (cantidad, detalle_id, id))
            r = cur.fetchone()
            linea = float(r[0]) if r else None

        _recalcular_total(cur, id)
        subtotal, desc, total, _, _ = _totales(cur, id)
        conn.commit()

    return jsonify(ok=True, eliminada=cantidad <= 0, linea=linea,
                   subtotal=subtotal, descuento=desc, total=total)


@pedidos_bp.route("/admin/pedidos/<int:id>/descuento", methods=["POST"])
def pedido_descuento_json(id):
    data = request.get_json(silent=True) or {}
    tipo = data.get("tipo", "monto")
    if tipo not in ("monto", "porcentaje"):
        tipo = "monto"
    try:
        valor = max(0.0, float(data.get("valor", 0) or 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Descuento inválido"), 400
    if tipo == "porcentaje":
        valor = min(valor, 100.0)

    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _pedido_editable(cur, id):
            return jsonify(ok=False, error="Este pedido ya no se puede editar."), 409
        cur.execute("UPDATE pedidos SET descuento_tipo=%s, descuento_valor=%s WHERE id=%s",
                    (tipo, valor, id))
        _recalcular_total(cur, id)
        subtotal, desc, total, _, _ = _totales(cur, id)
        conn.commit()

    return jsonify(ok=True, subtotal=subtotal, descuento=desc, total=total)


# ─────────────────────────────────────────────
#  AGREGAR UN PRODUCTO NUEVO AL PEDIDO
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/detalle/agregar", methods=["POST"])
def detalle_agregar(id):
    if not (request.form.get("producto_id") or "").strip():
        flash("Elegí un producto de la lista que aparece al escribir el nombre.", "warning")
        return redirect(url_for("pedidos_bp.pedido_detalle", id=id))
    try:
        producto_id = int(request.form.get("producto_id", 0))
        # Hay dos campos "cantidad" (simple y por peso); se toma el primero con valor.
        # Se acepta coma decimal por si el teclado del celular la manda así.
        crudos = [c.strip().replace(",", ".") for c in request.form.getlist("cantidad") if c.strip()]
        cantidad = float(crudos[0]) if crudos else 0
    except (ValueError, IndexError):
        flash("Datos inválidos: revisá el producto y la cantidad.", "danger")
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
            FROM pedidos_detalle WHERE pedido_id = %s ORDER BY id
        """, (id,))
        items = cur.fetchall()
        if not items:
            flash("El pedido no tiene productos.", "warning")
            return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

        subtotal_total, descuento_total, total_total, _, _ = _totales(cur, id)

        fecha_hora = (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")

        # Insertar venta (con el descuento del pedido)
        cur.execute("""
            INSERT INTO ventas (cliente_id, fecha_venta, subtotal, descuento, iva, total, metodo_pago, estado)
            VALUES (%s, %s, %s, %s, 0, %s, 'Por cobrar', 'Facturado')
            RETURNING id
        """, (cliente_id, fecha_hora, subtotal_total, descuento_total, total_total))
        venta_id = cur.fetchone()[0]

        desc_asignado = 0.0
        for i, (producto_id, cantidad, precio) in enumerate(items):
            sub_l = float(cantidad) * float(precio)

            # Descuento repartido proporcionalmente; la última línea absorbe el redondeo
            if i == len(items) - 1:
                desc_l = round(descuento_total - desc_asignado, 2)
            else:
                desc_l = round(descuento_total * sub_l / subtotal_total, 2) if subtotal_total else 0.0
                desc_asignado += desc_l
            total_l = round(sub_l - desc_l, 2)

            # Detalle de venta
            cur.execute("""
                INSERT INTO ventas_detalle
                    (venta_id, producto_id, cantidad, precio_unitario,
                     subtotal_linea, descuento, total_linea)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (venta_id, producto_id, cantidad, precio, sub_l, desc_l, total_l))

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
