# -*- coding: utf-8 -*-
from flask import render_template, request, jsonify
from db.conexion import obtener_conexion
from datetime import datetime
from modulos.ventas_menu import ventas_bp

# ============================================================
# FORMULARIO PRINCIPAL
# ============================================================
@ventas_bp.route("/ventas/registrar")
def registrar_venta():
    hoy = datetime.now().strftime("%Y-%m-%d")
    return render_template("ventas_registrar.html", hoy=hoy)

# ============================================================
# AUTOCOMPLETADO CLIENTES
# ============================================================
@ventas_bp.route("/ventas/buscar_clientes/<texto>")
def buscar_clientes(texto):
    conn = obtener_conexion()
    cur = conn.cursor()
    like = f"%{texto}%"
    cur.execute("""
        SELECT id, nombre, cedula
        FROM clientes
        WHERE nombre ILIKE %s OR cedula ILIKE %s
        ORDER BY nombre ASC
        LIMIT 10
    """, (like, like))
    data = [{"id": r[0], "nombre": r[1], "cedula": r[2]} for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(data)

# ============================================================
# OBTENER PRECIO + STOCK REAL (KARDEX)
# ============================================================
@ventas_bp.route("/ventas/producto/<int:id>")
def ventas_producto(id):
    conn = obtener_conexion()
    cur = conn.cursor()

    # STOCK real: C - (F - A)
    cur.execute("""
        SELECT 
            p.precio,

            -- Entradas reales = solo C
            (
                SELECT COALESCE(SUM(cantidad), 0)
                FROM inventario_movimientos
                WHERE producto_id = p.id AND tipo = 'C'
            ) AS entradas,

            -- Salidas reales = F - A
            (
                SELECT COALESCE(SUM(
                    CASE 
                        WHEN tipo = 'F' THEN cantidad
                        WHEN tipo = 'A' THEN -cantidad
                        ELSE 0
                    END
                ), 0)
                FROM inventario_movimientos
                WHERE producto_id = p.id
            ) AS salidas

        FROM productos p
        WHERE p.id = %s
        LIMIT 1
    """, (id,))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if row:
        precio = float(row[0])
        entradas = float(row[1])
        salidas = float(row[2])
        stock_real = entradas - salidas  # ★ stock final correcto

        return jsonify({"precio": precio, "stock": stock_real})

    return jsonify({"precio": 0, "stock": 0}), 404

# ============================================================
# GUARDAR VENTA
# ============================================================
@ventas_bp.route("/ventas/guardar", methods=["POST"])
def guardar_venta():
    conn = None
    try:
        cliente_id = request.form.get("cliente_id")
        cliente_id = int(cliente_id) if cliente_id and cliente_id.strip() != "" else None
        
        fecha_hora_exacta = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lineas_raw = request.form.get("lineas", "")

        if not lineas_raw.strip():
            return jsonify({"status": "error", "message": "No hay productos seleccionados"}), 400

        # Normalización de líneas
        lineas_procesadas = []
        for linea in lineas_raw.split(","):
            l = linea.strip()
            if not l: continue
            partes = l.split("|")
            if len(partes) >= 4:
                lineas_procesadas.append(partes)

        conn = obtener_conexion()
        cur = conn.cursor()

        # Insertar encabezado
        cur.execute("""
            INSERT INTO ventas (cliente_id, fecha_venta, subtotal, descuento, iva, total, metodo_pago, estado)
            VALUES (%s, %s, 0, 0, 0, 0, 'Efectivo', 'Facturado')
            RETURNING id
        """, (cliente_id, fecha_hora_exacta))
        venta_id = cur.fetchone()[0]

        subtotal_total = 0
        descuento_total = 0
        total_total = 0

        # Procesar cada producto
        for partes in lineas_procesadas:
            p_id = int(partes[0])
            cant = float(partes[1])
            precio = float(partes[2])
            desc_col = float(partes[3])

            sub_l = precio * cant
            tot_l = sub_l - desc_col
            subtotal_total += sub_l
            descuento_total += desc_col
            total_total += tot_l

            # A. Detalle
            cur.execute("""
                INSERT INTO ventas_detalle (venta_id, producto_id, cantidad, precio_unitario, subtotal_linea, descuento, total_linea)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (venta_id, p_id, cant, precio, sub_l, desc_col, tot_l))

            # B. Movimiento F
            cur.execute("""
                INSERT INTO inventario_movimientos (producto_id, fecha, tipo, cantidad)
                VALUES (%s, %s, 'F', %s)
            """, (p_id, fecha_hora_exacta, cant))

            # C. Reducir inventario_semanal (FIFO)
            cur.execute("""
                UPDATE inventario_semanal 
                SET cantidad_disponible = cantidad_disponible - %s 
                WHERE id = (
                    SELECT id FROM inventario_semanal 
                    WHERE producto_id = %s AND cantidad_disponible >= %s 
                    ORDER BY fecha_semana ASC LIMIT 1
                )
            """, (cant, p_id, cant))

        # Actualizar totales
        cur.execute("""
            UPDATE ventas SET subtotal=%s, descuento=%s, total=%s WHERE id=%s
        """, (subtotal_total, descuento_total, total_total, venta_id))

        conn.commit()
        return jsonify({"status": "ok", "venta_id": venta_id})

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            cur.close()
            conn.close()
