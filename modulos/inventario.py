# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db.conexion import obtener_conexion
from datetime import datetime # Importado para manejar fecha y hora exacta

inventario_bp = Blueprint("inventario_bp", __name__)

# --------------------------------------------------
# MENÚ PRINCIPAL DE INVENTARIO
# --------------------------------------------------
@inventario_bp.route("/inventario")
def inventario_menu():
    return render_template("inventario.html")


# --------------------------------------------------
# REGISTRAR / ACTUALIZAR COSECHA SEMANAL
# --------------------------------------------------
@inventario_bp.route("/inventario/registrar", methods=["GET", "POST"])
def inventario_registrar():

    if request.method == "POST":
        producto_id = int(request.form["producto"])
        # Se captura la fecha del form, pero le sumamos la hora actual del sistema
        ahora = datetime.now()
        fecha_hora_exacta = ahora.strftime("%Y-%m-%d %H:%M:%S")
        
        # Si el usuario eligió una fecha específica en el calendario, la respetamos pero agregamos la hora actual
        fecha_input = request.form["semana"] # yyyy-mm-dd
        fecha_final = f"{fecha_input} {ahora.strftime('%H:%M:%S')}"

        cantidad = int(request.form["cantidad"])

        with obtener_conexion() as conn:
            cur = conn.cursor()

            # 1. Revisar si ya existe en esa fecha exacta (opcional, según lógica de negocio)
            cur.execute("""
                SELECT id, cantidad_disponible 
                FROM inventario_semanal 
                WHERE producto_id = %s AND fecha_semana::date = %s::date
            """, (producto_id, fecha_input))
            existente = cur.fetchone()

            if existente:
                # A. REGISTRO EXISTENTE → Actualizar con nueva hora y cantidad
                cur.execute("""
                    UPDATE inventario_semanal 
                    SET cantidad_disponible = %s, fecha_semana = %s
                    WHERE id = %s
                """, (cantidad, fecha_final, existente[0]))

                # B. INSERTAR MOVIMIENTO DE AJUSTE
                delta = cantidad - existente[1]
                cur.execute("""
                    INSERT INTO inventario_movimientos (producto_id, fecha, tipo, cantidad)
                    VALUES (%s, %s, 'A', %s)
                """, (producto_id, fecha_final, delta))

                flash("Cosecha actualizada y ajuste de inventario aplicado.", "inventario")

            else:
                # A. INSERTAR HISTORIAL CON FECHA Y HORA
                cur.execute("""
                    INSERT INTO inventario_semanal (producto_id, fecha_semana, cantidad_disponible)
                    VALUES (%s, %s, %s)
                """, (producto_id, fecha_final, cantidad))

                # B. INSERTAR MOVIMIENTO REAL
                cur.execute("""
                    INSERT INTO inventario_movimientos (producto_id, fecha, tipo, cantidad)
                    VALUES (%s, %s, 'C', %s)
                """, (producto_id, fecha_final, cantidad))

                flash("Cosecha registrada correctamente.", "inventario")

            conn.commit()

        return redirect(url_for("inventario_bp.inventario_registrar"))

    # GET — cargar datos
    with obtener_conexion() as conn:
        cur = conn.cursor()

        cur.execute("SELECT id, nombre, unidad FROM productos ORDER BY nombre ASC")
        productos = cur.fetchall()

        cur.execute("""
            SELECT 
                i.id, p.nombre, p.unidad, 
                i.fecha_semana, i.cantidad_disponible
            FROM inventario_semanal i
            JOIN productos p ON p.id = i.producto_id
            ORDER BY i.fecha_semana DESC
        """)
        registros = cur.fetchall()

    return render_template(
        "inventario_registrar.html",
        productos=productos,
        registros=registros
    )


# --------------------------------------------------
# EDITAR COSECHA
# --------------------------------------------------
@inventario_bp.route("/inventario/editar/<int:id>", methods=["POST"])
def inventario_editar(id):
    ahora = datetime.now()
    nueva_fecha_input = request.form["semana_edit"]
    nueva_fecha_final = f"{nueva_fecha_input} {ahora.strftime('%H:%M:%S')}"
    nueva_cantidad = int(request.form["cantidad_edit"])

    with obtener_conexion() as conn:
        cur = conn.cursor()

        cur.execute("SELECT producto_id, cantidad_disponible FROM inventario_semanal WHERE id = %s", (id,))
        row = cur.fetchone()

        if not row:
            flash("No se encontró el registro.", "inventario")
            return redirect(url_for("inventario_bp.inventario_registrar"))

        producto_id, cantidad_anterior = row
        delta = nueva_cantidad - cantidad_anterior

        # Actualizar con nueva estampa de tiempo
        cur.execute("""
            UPDATE inventario_semanal
            SET fecha_semana = %s, cantidad_disponible = %s
            WHERE id = %s
        """, (nueva_fecha_final, nueva_cantidad, id))

        cur.execute("""
            INSERT INTO inventario_movimientos (producto_id, fecha, tipo, cantidad)
            VALUES (%s, %s, 'A', %s)
        """, (producto_id, nueva_fecha_final, delta))

        conn.commit()

    flash("Cosecha editada y ajuste aplicado.", "inventario")
    return redirect(url_for("inventario_bp.inventario_registrar"))


# --------------------------------------------------
# ELIMINAR COSECHA
# --------------------------------------------------
@inventario_bp.route("/inventario/eliminar/<int:id>", methods=["POST"])
def inventario_eliminar(id):
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with obtener_conexion() as conn:
        cur = conn.cursor()

        cur.execute("SELECT producto_id, cantidad_disponible FROM inventario_semanal WHERE id = %s", (id,))
        row = cur.fetchone()

        if row:
            producto_id, cantidad = row
            # Insertar movimiento de reversión con hora actual
            cur.execute("""
                INSERT INTO inventario_movimientos (producto_id, fecha, tipo, cantidad)
                VALUES (%s, %s, 'R', %s)
            """, (producto_id, ahora, -cantidad))

        cur.execute("DELETE FROM inventario_semanal WHERE id=%s", (id,))
        conn.commit()

    flash("Registro eliminado y stock ajustado.", "inventario")
    return redirect(url_for("inventario_bp.inventario_registrar"))