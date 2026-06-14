# -*- coding: utf-8 -*-
"""
Historial de ventas con filtros — ECOLTURA
Ubicación: modulos/ventas_historial.py
En app.py agregar junto a los otros imports de ventas:
    import modulos.ventas_historial
"""
from flask import render_template, request
from db.conexion import obtener_conexion
from datetime import datetime, timedelta
from modulos.ventas_menu import ventas_bp


@ventas_bp.route("/ventas/historial")
def ventas_historial():
    # Filtros opcionales por querystring
    f_desde   = request.args.get("desde", "").strip()
    f_hasta   = request.args.get("hasta", "").strip()
    f_venta   = request.args.get("venta_id", "").strip()
    f_cliente = request.args.get("cliente", "").strip()

    condiciones = []
    params = []

    if f_venta:
        condiciones.append("v.id = %s")
        params.append(int(f_venta) if f_venta.isdigit() else -1)

    if f_cliente:
        condiciones.append("(c.nombre ILIKE %s OR c.cedula ILIKE %s)")
        params.extend([f"%{f_cliente}%", f"%{f_cliente}%"])

    if f_desde:
        condiciones.append("v.fecha_venta::date >= %s")
        params.append(f_desde)

    if f_hasta:
        condiciones.append("v.fecha_venta::date <= %s")
        params.append(f_hasta)

    where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""

    # Si no hay ningún filtro, mostrar últimos 30 días por defecto
    if not condiciones:
        hace_30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        where = "WHERE v.fecha_venta::date >= %s"
        params = [hace_30]

    sql = f"""
        SELECT v.id, v.fecha_venta, v.total, v.metodo_pago, v.estado,
               COALESCE(c.nombre, 'Cliente general') AS cliente,
               c.celular
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.cliente_id
        {where}
        ORDER BY v.fecha_venta DESC, v.id DESC
        LIMIT 200
    """

    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        ventas = cur.fetchall()

        # Totales del resultado
        total_ventas = len(ventas)
        suma_total   = sum(float(v[2] or 0) for v in ventas)

    return render_template("ventas_historial.html",
                           ventas=ventas,
                           total_ventas=total_ventas,
                           suma_total=suma_total,
                           f_desde=f_desde, f_hasta=f_hasta,
                           f_venta=f_venta, f_cliente=f_cliente)
