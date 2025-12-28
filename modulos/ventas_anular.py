# -*- coding: utf-8 -*-
from flask import render_template, request, redirect, url_for, flash
from db.conexion import obtener_conexion
from datetime import datetime
from modulos.ventas_menu import ventas_bp

# ===============================================
# PANTALLA PRINCIPAL
# ===============================================
@ventas_bp.route("/ventas/anular")
def anular_venta():
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, nombre FROM clientes ORDER BY nombre ASC")
        clientes = cur.fetchall()
    return render_template("ventas_anular.html", ventas=None, clientes=clientes)

# ===============================================
# BÚSQUEDA (ID, CLIENTE, RANGO DE FECHAS)
# ===============================================
@ventas_bp.route("/ventas/anular/buscar", methods=["POST"])
def anular_buscar():
    id_venta = request.form.get("id_venta")
    cliente_id = request.form.get("cliente_id")
    fecha_inicio = request.form.get("fecha_inicio")
    fecha_fin = request.form.get("fecha_fin")

    query = """
        SELECT v.id, v.fecha_venta, v.total, v.estado,
               COALESCE(c.nombre, 'Sin cliente')
        FROM ventas v
        LEFT JOIN clientes c ON v.cliente_id = c.id
        WHERE 1 = 1
    """
    params = []

    if id_venta:
        query += " AND v.id = %s"
        params.append(id_venta)

    if cliente_id:
        query += " AND v.cliente_id = %s"
        params.append(cliente_id)

    if fecha_inicio and fecha_fin:
        query += " AND DATE(v.fecha_venta) BETWEEN %s AND %s"
        params.extend([fecha_inicio, fecha_fin])
    elif fecha_inicio:
        query += " AND DATE(v.fecha_venta) >= %s"
        params.append(fecha_inicio)
    elif fecha_fin:
        query += " AND DATE(v.fecha_venta) <= %s"
        params.append(fecha_fin)

    query += " ORDER BY v.id DESC"

    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        ventas = cur.fetchall()
        cur.execute("SELECT id, nombre FROM clientes ORDER BY nombre ASC")
        clientes = cur.fetchall()

    return render_template("ventas_anular.html", ventas=ventas, clientes=clientes)

# ===============================================
# CONFIRMAR ANULACIÓN
# ===============================================
@ventas_bp.route("/ventas/anular/confirmar/<int:id>", methods=["POST"])
def anular_confirmar(id):
    conn = obtener_conexion()
    cur = conn.cursor()
    ahora = datetime.now()

    try:
        cur.execute("SELECT estado FROM ventas WHERE id = %s", (id,))
        venta = cur.fetchone()

        if not venta:
            flash("La venta no existe.", "danger")
            return redirect(url_for("ventas_bp.anular_venta"))

        if venta[0] == "Anulado":
            flash(f"La venta #{id} ya está anulada.", "warning")
            return redirect(url_for("ventas_bp.anular_venta"))

        cur.execute("SELECT producto_id, cantidad FROM ventas_detalle WHERE venta_id = %s", (id,))
        detalles = cur.fetchall()

        for producto_id, cantidad in detalles:
            cur.execute("""
                INSERT INTO inventario_movimientos 
                (producto_id, fecha, tipo, cantidad, venta_id)
                VALUES (%s, %s, 'A', %s, %s)
            """, (producto_id, ahora, cantidad, id))

        cur.execute("""
            UPDATE ventas 
            SET estado = 'Anulado', total = 0, subtotal = 0, descuento = 0
            WHERE id = %s
        """, (id,))

        conn.commit()
        # CATEGORÍA CLAVE: exito_anulacion
        flash(f"Venta #{id} anulada correctamente.", "exito_anulacion")

    except Exception as e:
        conn.rollback()
        flash(f"Error técnico: {str(e)}", "danger")

    finally:
        cur.close()
        conn.close()

    return redirect(url_for("ventas_bp.anular_venta"))