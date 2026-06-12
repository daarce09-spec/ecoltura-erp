# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash
from db.conexion import obtener_conexion
from datetime import datetime

visitas_bp = Blueprint("visitas_bp", __name__)


# ─────────────────────────────────────────────
#  BANDEJA DE VISITAS
# ─────────────────────────────────────────────
@visitas_bp.route("/admin/visitas")
def bandeja_visitas():
    with obtener_conexion() as conn:
        cur = conn.cursor()

        # Fechas habilitadas con conteo de reservas
        cur.execute("""
            SELECT vf.id, vf.fecha, vf.cupos, vf.activa,
                   COUNT(vr.id) FILTER (WHERE vr.estado IN ('Pendiente','Confirmada')) AS ocupados
            FROM visitas_fechas vf
            LEFT JOIN visitas_reservas vr ON vr.fecha_id = vf.id
            GROUP BY vf.id, vf.fecha, vf.cupos, vf.activa
            ORDER BY vf.fecha DESC
        """)
        fechas = cur.fetchall()

        # Reservas pendientes
        cur.execute("""
            SELECT vr.id, vf.fecha, vr.nombre, vr.celular,
                   vr.personas, vr.estado, vr.creado
            FROM visitas_reservas vr
            JOIN visitas_fechas vf ON vf.id = vr.fecha_id
            ORDER BY
                CASE vr.estado WHEN 'Pendiente' THEN 0 WHEN 'Confirmada' THEN 1 ELSE 2 END,
                vf.fecha ASC
        """)
        reservas = cur.fetchall()

        cur.execute("SELECT COUNT(*) FROM visitas_reservas WHERE estado='Pendiente'")
        pendientes = cur.fetchone()[0]

    return render_template("visitas_bandeja.html",
                           fechas=fechas, reservas=reservas, pendientes=pendientes)


# ─────────────────────────────────────────────
#  CREAR FECHA DE VISITA
# ─────────────────────────────────────────────
@visitas_bp.route("/admin/visitas/fecha/crear", methods=["POST"])
def fecha_crear():
    fecha  = request.form["fecha"]
    cupos  = int(request.form.get("cupos", 10))
    with obtener_conexion() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO visitas_fechas (fecha, cupos, activa)
                VALUES (%s, %s, TRUE)
            """, (fecha, cupos))
            conn.commit()
            flash(f"Fecha {fecha} habilitada con {cupos} cupos.", "success")
        except Exception as e:
            flash(f"Error: esa fecha ya existe o datos inválidos.", "danger")
    return redirect(url_for("visitas_bp.bandeja_visitas"))


# ─────────────────────────────────────────────
#  TOGGLE ACTIVA/INACTIVA UNA FECHA
# ─────────────────────────────────────────────
@visitas_bp.route("/admin/visitas/fecha/<int:id>/toggle", methods=["POST"])
def fecha_toggle(id):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE visitas_fechas SET activa = NOT activa WHERE id=%s RETURNING activa
        """, (id,))
        nuevo = cur.fetchone()[0]
        conn.commit()
    estado = "activada" if nuevo else "desactivada"
    flash(f"Fecha {estado}.", "info")
    return redirect(url_for("visitas_bp.bandeja_visitas"))


# ─────────────────────────────────────────────
#  CONFIRMAR RESERVA
# ─────────────────────────────────────────────
@visitas_bp.route("/admin/visitas/reserva/<int:id>/confirmar", methods=["POST"])
def reserva_confirmar(id):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE visitas_reservas SET estado='Confirmada' WHERE id=%s AND estado='Pendiente'
        """, (id,))
        conn.commit()
    flash(f"Reserva #{id} confirmada.", "success")
    return redirect(url_for("visitas_bp.bandeja_visitas"))


# ─────────────────────────────────────────────
#  CANCELAR RESERVA
# ─────────────────────────────────────────────
@visitas_bp.route("/admin/visitas/reserva/<int:id>/cancelar", methods=["POST"])
def reserva_cancelar(id):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE visitas_reservas SET estado='Cancelada' WHERE id=%s
        """, (id,))
        conn.commit()
    flash(f"Reserva #{id} cancelada.", "danger")
    return redirect(url_for("visitas_bp.bandeja_visitas"))
