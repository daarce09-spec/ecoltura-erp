# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db.conexion import obtener_conexion

clientes_bp = Blueprint("clientes_bp", __name__)


# ============================
# LISTAR Y REGISTRAR CLIENTES
# ============================
@clientes_bp.route("/clientes", methods=["GET", "POST"])
def clientes():
    if request.method == "POST":

        nombre = request.form["nombre"]
        cedula = request.form["cedula"]
        celular = request.form["celular"]
        direccion = request.form["direccion"]

        # ======== Bits ========
        feria = request.form.get("cliente_feria") == "on"
        domicilio = request.form.get("cliente_domicilio") == "on"
        suscripcion = request.form.get("cliente_suscripcion") == "on"
        sin_modelo = request.form.get("cliente_sin_modelo_venta") == "on"

        # ======== Validaciones backend ========

        # Sin modelo cancela todo
        if sin_modelo and (feria or domicilio or suscripcion):
            flash("‘Sin modelo de venta’ no puede combinarse con otros.", "danger")
            return redirect(url_for("clientes_bp.clientes"))

        # Domicilio + Suscripción prohibido siempre
        if domicilio and suscripcion:
            flash("Domicilio y Suscripción no pueden estar juntos.", "danger")
            return redirect(url_for("clientes_bp.clientes"))

        conn = obtener_conexion()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO clientes
            (nombre, cedula, celular, direccion,
             cliente_feria, cliente_domicilio, cliente_suscripcion, cliente_sin_modelo_venta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (nombre, cedula, celular, direccion,
             feria, domicilio, suscripcion, sin_modelo)
        )

        conn.commit()
        cur.close()
        conn.close()

        flash("Cliente registrado con éxito", "success")
        return redirect(url_for("clientes_bp.clientes"))

    conn = obtener_conexion()
    cur = conn.cursor()

    # Traer también los bits
    cur.execute(
        """
        SELECT id, nombre, cedula, celular, direccion,
               cliente_feria, cliente_domicilio, cliente_suscripcion, cliente_sin_modelo_venta
        FROM clientes
        ORDER BY id DESC
        """
    )

    lista = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("clientes.html", clientes=lista)



# ============================
# ELIMINAR CLIENTE
# ============================
@clientes_bp.route("/clientes/eliminar/<int:id>", methods=["POST"])
def clientes_eliminar(id):

    conn = obtener_conexion()
    cur = conn.cursor()

    cur.execute("DELETE FROM clientes WHERE id=%s", (id,))

    conn.commit()
    cur.close()
    conn.close()

    flash("Cliente eliminado", "danger")
    return redirect(url_for("clientes_bp.clientes"))



# ============================
# EDITAR CLIENTE
# ============================
@clientes_bp.route("/clientes/editar/<int:id>", methods=["POST"])
def clientes_editar(id):

    nombre = request.form["nombre_edit"]
    cedula = request.form["cedula_edit"]
    celular = request.form["celular_edit"]
    direccion = request.form["direccion_edit"]

    # ======== Bits ========
    feria = request.form.get("cliente_feria_edit") == "on"
    domicilio = request.form.get("cliente_domicilio_edit") == "on"
    suscripcion = request.form.get("cliente_suscripcion_edit") == "on"
    sin_modelo = request.form.get("cliente_sin_modelo_venta_edit") == "on"

    # ======== Validaciones backend ========

    if sin_modelo and (feria or domicilio or suscripcion):
        flash("‘Sin modelo de venta’ no puede combinarse con otros.", "danger")
        return redirect(url_for("clientes_bp.clientes"))

    if domicilio and suscripcion:
        flash("Domicilio y Suscripción no pueden estar juntos.", "danger")
        return redirect(url_for("clientes_bp.clientes"))

    conn = obtener_conexion()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE clientes
        SET nombre=%s,
            cedula=%s,
            celular=%s,
            direccion=%s,
            cliente_feria=%s,
            cliente_domicilio=%s,
            cliente_suscripcion=%s,
            cliente_sin_modelo_venta=%s
        WHERE id=%s
        """,
        (nombre, cedula, celular, direccion,
         feria, domicilio, suscripcion, sin_modelo,
         id)
    )

    conn.commit()
    cur.close()
    conn.close()

    flash("Cliente actualizado", "info")
    return redirect(url_for("clientes_bp.clientes"))
