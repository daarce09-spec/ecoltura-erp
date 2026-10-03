# -*- coding: utf-8 -*-
"""
Historial de ventas con filtros, gráficos y reenvío de comprobantes — ECOLTURA
Ubicación: modulos/ventas_historial.py
En app.py agregar junto a los otros imports de ventas:
    import modulos.ventas_historial
"""
import json
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

    # Si no hay ningún filtro, mostrar el mes actual por defecto
    rango_por_defecto = not condiciones
    if rango_por_defecto:
        inicio_mes_actual = datetime.now().strftime("%Y-%m-01")
        where = "WHERE v.fecha_venta::date >= %s"
        params = [inicio_mes_actual]
        f_desde = inicio_mes_actual
        f_hasta = datetime.now().strftime("%Y-%m-%d")

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

    # Totales reales sobre TODO el conjunto filtrado (sin el LIMIT de arriba,
    # que es solo para no sobrecargar la tabla que se muestra en pantalla)
    sql_totales = f"""
        SELECT COUNT(*), COALESCE(SUM(v.total), 0)
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.cliente_id
        {where}
    """

    # Desglose por estado (sobre el mismo conjunto filtrado, sin LIMIT)
    sql_estados = f"""
        SELECT v.estado, COUNT(*), COALESCE(SUM(v.total), 0)
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.cliente_id
        {where}
        GROUP BY v.estado
    """

    # Tendencia por día (sobre el conjunto completo filtrado)
    sql_tendencia = f"""
        SELECT v.fecha_venta::date AS dia, COUNT(*) AS cantidad, COALESCE(SUM(v.total), 0) AS monto
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.cliente_id
        {where}
        GROUP BY dia
        ORDER BY dia
    """

    # Top clientes por MONTO (sobre el conjunto completo filtrado)
    sql_top_clientes_monto = f"""
        SELECT COALESCE(c.nombre, 'Cliente general') AS cliente,
               COUNT(*) AS cantidad, COALESCE(SUM(v.total), 0) AS monto
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.cliente_id
        {where}
        GROUP BY cliente
        ORDER BY monto DESC
        LIMIT 8
    """

    # Top clientes por # DE VENTAS — es una lista aparte (y no la misma
    # reordenada) porque el cliente que más compra en monto no es
    # necesariamente el que más veces compra: cada vista del gráfico debe
    # mostrar su propio top, de mayor a menor según esa métrica.
    sql_top_clientes_cantidad = f"""
        SELECT COALESCE(c.nombre, 'Cliente general') AS cliente,
               COUNT(*) AS cantidad, COALESCE(SUM(v.total), 0) AS monto
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.cliente_id
        {where}
        GROUP BY cliente
        ORDER BY cantidad DESC
        LIMIT 8
    """

    # Top productos (usa ventas_detalle, con el mismo filtro aplicado a la venta)
    sql_top_productos = f"""
        SELECT pr.nombre, pr.unidad,
               SUM(vd.cantidad) AS cantidad, COALESCE(SUM(vd.total_linea), 0) AS monto
        FROM ventas_detalle vd
        JOIN ventas v ON v.id = vd.venta_id
        JOIN productos pr ON pr.id = vd.producto_id
        LEFT JOIN clientes c ON c.id = v.cliente_id
        {where}
        GROUP BY pr.id, pr.nombre, pr.unidad
        ORDER BY cantidad DESC
        LIMIT 8
    """

    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        ventas = cur.fetchall()

        cur.execute(sql_totales, params)
        total_ventas, suma_total = cur.fetchone()
        suma_total = float(suma_total or 0)

        cur.execute(sql_estados, params)
        estados = {fila[0]: {"cantidad": fila[1], "monto": float(fila[2] or 0)}
                   for fila in cur.fetchall()}

        cur.execute(sql_tendencia, params)
        tendencia = [{"fecha": fila[0].strftime("%d %b"), "cantidad": fila[1], "monto": float(fila[2] or 0)}
                     for fila in cur.fetchall()]

        cur.execute(sql_top_clientes_monto, params)
        top_clientes_monto = [{"nombre": fila[0], "cantidad": fila[1], "monto": float(fila[2] or 0)}
                              for fila in cur.fetchall()]

        cur.execute(sql_top_clientes_cantidad, params)
        top_clientes_cantidad = [{"nombre": fila[0], "cantidad": fila[1], "monto": float(fila[2] or 0)}
                                 for fila in cur.fetchall()]

        cur.execute(sql_top_productos, params)
        top_productos = []
        for fila in cur.fetchall():
            nombre, unidad, cantidad, monto = fila
            cantidad = float(cantidad or 0)
            unidad_low = (unidad or "").strip().lower()
            if unidad_low in ("gramo", "gramos"):
                etiqueta_cant = f"{cantidad/1000:.1f} kg"
            else:
                etiqueta_cant = f"{cantidad:.0f} u"
            top_productos.append({"nombre": nombre, "cantidad": cantidad,
                                  "etiqueta": etiqueta_cant, "monto": float(monto or 0)})

    ticket_promedio = (suma_total / total_ventas) if total_ventas else 0
    mostrando_limitado = len(ventas) < total_ventas

    hoy = datetime.now().strftime("%Y-%m-%d")
    hace_7 = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    hace_30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    inicio_mes = datetime.now().strftime("%Y-%m-01")

    return render_template("ventas_historial.html",
                           ventas=ventas,
                           total_ventas=total_ventas,
                           suma_total=suma_total,
                           ticket_promedio=ticket_promedio,
                           estados=estados,
                           mostrando_limitado=mostrando_limitado,
                           filas_mostradas=len(ventas),
                           rango_por_defecto=rango_por_defecto,
                           tendencia_json=json.dumps(tendencia),
                           top_clientes_monto_json=json.dumps(top_clientes_monto),
                           top_clientes_cantidad_json=json.dumps(top_clientes_cantidad),
                           top_productos_json=json.dumps(top_productos),
                           hoy=hoy, hace_7=hace_7, hace_30=hace_30, inicio_mes=inicio_mes,
                           f_desde=f_desde, f_hasta=f_hasta,
                           f_venta=f_venta, f_cliente=f_cliente)
