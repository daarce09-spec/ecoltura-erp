from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from db.conexion import obtener_conexion

productos_bp = Blueprint("productos_bp", __name__)

# =====================================================
# LISTAR Y AGREGAR PRODUCTOS
# =====================================================
@productos_bp.route("/productos", methods=["GET", "POST"])
def productos():
    if request.method == "POST":
        nombre    = request.form["nombre"]
        categoria = request.form["categoria"]
        unidad    = request.form["unidad"]
        precio    = request.form["precio"]

        conn = obtener_conexion()
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO productos (nombre, categoria, unidad, precio) VALUES (%s, %s, %s, %s)",
            (nombre, categoria, unidad, precio)
        )
        conn.commit()
        cur.close()
        conn.close()

        flash("Producto agregado correctamente.", "productos")
        return redirect(url_for("productos_bp.productos"))

    conn = obtener_conexion()
    cur  = conn.cursor()
    cur.execute(
        "SELECT id, nombre, categoria, unidad, precio, visible_web FROM productos ORDER BY nombre ASC"
    )
    lista = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("productos.html", productos=lista)


# =====================================================
# TOGGLE VISIBLE EN WEB (AJAX)
# =====================================================
@productos_bp.route("/productos/toggle-web/<int:id>", methods=["POST"])
def toggle_web(id):
    """Alterna visible_web para un producto. Retorna JSON con el nuevo estado."""
    conn = obtener_conexion()
    cur  = conn.cursor()
    cur.execute(
        "UPDATE productos SET visible_web = NOT visible_web WHERE id = %s RETURNING visible_web",
        (id,)
    )
    nuevo = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"visible_web": nuevo})


# =====================================================
# EDITAR PRODUCTO
# =====================================================
@productos_bp.route("/productos/editar/<int:id>", methods=["POST"])
def editar_producto(id):
    nombre    = request.form["nombre_edit"]
    categoria = request.form["categoria_edit"]
    unidad    = request.form["unidad_edit"]
    precio    = request.form["precio_edit"]

    conn = obtener_conexion()
    cur  = conn.cursor()
    cur.execute(
        "UPDATE productos SET nombre=%s, categoria=%s, unidad=%s, precio=%s WHERE id=%s",
        (nombre, categoria, unidad, precio, id)
    )
    conn.commit()
    cur.close()
    conn.close()

    flash("Producto actualizado correctamente.", "productos")
    return redirect(url_for("productos_bp.productos"))


# =====================================================
# ELIMINAR PRODUCTO
# =====================================================
@productos_bp.route("/productos/eliminar/<int:id>", methods=["POST"])
def eliminar_producto(id):
    try:
        conn = obtener_conexion()
        cur  = conn.cursor()
        cur.execute("DELETE FROM productos WHERE id=%s", (id,))
        conn.commit()
        cur.close()
        conn.close()
        flash("Producto eliminado correctamente.", "productos")
    except Exception as e:
        flash(f"No se puede eliminar el producto: {e}", "productos")
    return redirect(url_for("productos_bp.productos"))


# =====================================================
# LISTA SIMPLE PARA VENTAS (JSON) — sin cambios
# =====================================================
@productos_bp.route("/productos/listar")
def productos_listar():
    conn = obtener_conexion()
    cur  = conn.cursor()
    cur.execute("SELECT id, nombre FROM productos ORDER BY nombre ASC")
    data = [{"id": r[0], "nombre": r[1]} for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(data)
