from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from db.conexion import obtener_conexion
from modulos.categorias import asegurar_categorias, limpiar_categoria

productos_bp = Blueprint("productos_bp", __name__)

asegurar_categorias()


# Columna "destacado": los productos marcados salen primero en el carrusel de la tienda
def _asegurar_destacado():
    try:
        conn = obtener_conexion()
        cur = conn.cursor()
        cur.execute("ALTER TABLE productos ADD COLUMN IF NOT EXISTS destacado BOOLEAN DEFAULT FALSE")
        conn.commit()
        cur.close()
        conn.close()
    except Exception as ex:
        print("No se pudo asegurar la columna destacado:", ex)

_asegurar_destacado()

# Misma limpieza de categoría del lado de la base: sin espacios de más y sin distinguir mayúsculas
SQL_CAT = "LOWER(REGEXP_REPLACE(TRIM(COALESCE(categoria, '')), '\\s+', ' ', 'g'))"


def _asegurar_categoria(cur, nombre):
    """Si la categoría no está en la lista oficial, la agrega al final."""
    if not nombre:
        return
    cur.execute("SELECT 1 FROM categorias WHERE LOWER(nombre) = LOWER(%s)", (nombre,))
    if not cur.fetchone():
        cur.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM categorias")
        orden = cur.fetchone()[0]
        cur.execute("INSERT INTO categorias (nombre, orden) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (nombre, orden))


# =====================================================
# LISTAR Y AGREGAR PRODUCTOS
# =====================================================
@productos_bp.route("/productos", methods=["GET", "POST"])
def productos():
    if request.method == "POST":
        nombre    = request.form["nombre"]
        categoria = limpiar_categoria(request.form.get("categoria"))
        unidad    = request.form["unidad"]
        precio    = request.form["precio"]

        conn = obtener_conexion()
        cur  = conn.cursor()
        _asegurar_categoria(cur, categoria)
        cur.execute(
            "INSERT INTO productos (nombre, categoria, unidad, precio) VALUES (%s, %s, %s, %s)",
            (nombre, categoria, unidad, precio)
        )
        conn.commit()
        cur.close()
        conn.close()

        flash("✅ Producto agregado correctamente.", "success")
        return redirect(url_for("productos_bp.productos"))

    conn = obtener_conexion()
    cur  = conn.cursor()
    cur.execute(
        """SELECT id, nombre, categoria, unidad, precio, visible_web,
                  grupo_id, grupo_nombre, nombre_variante, COALESCE(destacado, FALSE)
           FROM productos ORDER BY nombre ASC"""
    )
    lista = cur.fetchall()
    cur.execute("SELECT nombre FROM categorias ORDER BY orden, LOWER(nombre)")
    categorias = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()

    return render_template("productos.html", productos=lista, categorias=categorias)


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
# TOGGLE DESTACADO (AJAX)
# =====================================================
@productos_bp.route("/productos/toggle-destacado/<int:id>", methods=["POST"])
def toggle_destacado(id):
    """Alterna destacado: el producto sale primero en el carrusel de la tienda."""
    conn = obtener_conexion()
    cur  = conn.cursor()
    cur.execute(
        """UPDATE productos
              SET destacado = NOT COALESCE(destacado, FALSE),
                  visible_web = CASE WHEN COALESCE(destacado, FALSE) = FALSE THEN TRUE ELSE visible_web END
            WHERE id = %s RETURNING destacado, visible_web""",
        (id,)
    )
    nuevo, visible = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    # Al destacar un producto, también queda visible en la web (si no, no saldría en la tienda)
    return jsonify({"destacado": nuevo, "visible_web": visible})


# =====================================================
# EDITAR PRODUCTO
# =====================================================
@productos_bp.route("/productos/editar/<int:id>", methods=["POST"])
def editar_producto(id):
    nombre    = request.form["nombre_edit"]
    categoria = limpiar_categoria(request.form.get("categoria_edit"))
    unidad    = request.form["unidad_edit"]
    precio    = request.form["precio_edit"]

    conn = obtener_conexion()
    cur  = conn.cursor()
    _asegurar_categoria(cur, categoria)
    cur.execute(
        "UPDATE productos SET nombre=%s, categoria=%s, unidad=%s, precio=%s WHERE id=%s",
        (nombre, categoria, unidad, precio, id)
    )
    # Si el producto está en un grupo, el grupo completo queda en la misma categoría
    cur.execute("SELECT grupo_id FROM productos WHERE id = %s", (id,))
    fila = cur.fetchone()
    if fila and fila[0]:
        cur.execute("UPDATE productos SET categoria = %s WHERE grupo_id = %s", (categoria, fila[0]))
    conn.commit()
    cur.close()
    conn.close()

    flash("✅ Su producto ha sido actualizado correctamente.", "success")
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
        flash("✅ Producto eliminado correctamente.", "success")
    except Exception as e:
        flash(f"No se puede eliminar el producto: {e}", "danger")
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


# =====================================================
# CATEGORÍAS (lista oficial, con orden)
# =====================================================
@productos_bp.route("/productos/categorias/api/lista")
def categorias_lista():
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT c.id, c.nombre,
                   (SELECT COUNT(*) FROM productos WHERE {SQL_CAT} = LOWER(c.nombre))
            FROM categorias c
            ORDER BY c.orden, LOWER(c.nombre)
        """)
        filas = [{"id": r[0], "nombre": r[1], "n": r[2]} for r in cur.fetchall()]
    return jsonify(categorias=filas)


@productos_bp.route("/productos/categorias/api/guardar", methods=["POST"])
def categorias_guardar():
    data = request.get_json(silent=True) or {}
    nombre = limpiar_categoria(data.get("nombre"))
    cid = data.get("id")
    if not nombre:
        return jsonify(ok=False, error="Escribí el nombre de la categoría."), 400
    if len(nombre) > 80:
        return jsonify(ok=False, error="El nombre es muy largo (máximo 80 letras)."), 400

    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT id FROM categorias
                       WHERE LOWER(nombre) = LOWER(%s) AND (%s IS NULL OR id <> %s)""",
                    (nombre, cid, cid))
        if cur.fetchone():
            return jsonify(ok=False, error="Ya existe una categoría con ese nombre."), 409
        if cid:
            cur.execute("SELECT nombre FROM categorias WHERE id = %s", (cid,))
            viejo = cur.fetchone()
            if not viejo:
                return jsonify(ok=False, error="No se encontró la categoría."), 404
            cur.execute("UPDATE categorias SET nombre = %s WHERE id = %s", (nombre, cid))
            # Los productos que usaban el nombre anterior pasan al nuevo
            cur.execute(f"UPDATE productos SET categoria = %s WHERE {SQL_CAT} = LOWER(%s)",
                        (nombre, viejo[0]))
        else:
            cur.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM categorias")
            orden = cur.fetchone()[0]
            cur.execute("INSERT INTO categorias (nombre, orden) VALUES (%s, %s)", (nombre, orden))
        conn.commit()
    return jsonify(ok=True)


@productos_bp.route("/productos/categorias/api/<int:cid>/eliminar", methods=["POST"])
def categorias_eliminar(cid):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT nombre FROM categorias WHERE id = %s", (cid,))
        fila = cur.fetchone()
        if not fila:
            return jsonify(ok=True)
        cur.execute(f"SELECT COUNT(*) FROM productos WHERE {SQL_CAT} = LOWER(%s)", (fila[0],))
        n = cur.fetchone()[0]
        if n:
            return jsonify(ok=False, error=f"Tiene {n} producto(s). Pasalos a otra categoría antes de eliminarla."), 409
        cur.execute("DELETE FROM categorias WHERE id = %s", (cid,))
        conn.commit()
    return jsonify(ok=True)


@productos_bp.route("/productos/categorias/api/orden", methods=["POST"])
def categorias_orden():
    data = request.get_json(silent=True) or {}
    try:
        ids = [int(x) for x in (data.get("ids") or [])]
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Orden inválido."), 400
    with obtener_conexion() as conn:
        cur = conn.cursor()
        for i, cid in enumerate(ids, start=1):
            cur.execute("UPDATE categorias SET orden = %s WHERE id = %s", (i, cid))
        conn.commit()
    return jsonify(ok=True)


# =====================================================
# GRUPOS DE PRODUCTOS (opciones: sabores, tamaños...)
#   Un grupo son varios productos con el mismo grupo_id. En la tienda salen
#   como una sola tarjeta con sus opciones (ej. "Yogurt aflanado": Fresa, Mora).
# =====================================================
def _opciones(data):
    """[{id, variante}] limpio. Devuelve (lista, error)."""
    items = []
    vistos = set()
    for it in (data.get("items") or []):
        try:
            pid = int(it.get("id"))
        except (TypeError, ValueError, AttributeError):
            return None, "Producto inválido."
        variante = limpiar_categoria(it.get("variante"))
        if not variante:
            return None, "Cada producto necesita el nombre de su opción (ej. Fresa, Grande)."
        if variante.lower() in vistos:
            return None, f"La opción «{variante}» está repetida dentro del grupo."
        vistos.add(variante.lower())
        items.append({"id": pid, "variante": variante})
    return items, None


def _grupo_datos(cur, gid):
    cur.execute("""SELECT id, nombre, grupo_nombre, nombre_variante,
                          REGEXP_REPLACE(TRIM(COALESCE(categoria, '')), '\\s+', ' ', 'g')
                   FROM productos WHERE grupo_id = %s ORDER BY nombre_variante, nombre""", (gid,))
    filas = cur.fetchall()
    if not filas:
        return None
    return {"gid": gid, "nombre": filas[0][2] or "", "categoria": filas[0][4],
            "items": [{"id": r[0], "nombre": r[1], "variante": r[3] or ""} for r in filas]}


@productos_bp.route("/productos/grupos/api/<int:gid>")
def grupo_detalle(gid):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        datos = _grupo_datos(cur, gid)
    if not datos:
        return jsonify(ok=False, error="Ese grupo ya no existe."), 404
    return jsonify(ok=True, **datos)


@productos_bp.route("/productos/grupos/api/crear", methods=["POST"])
def grupo_crear():
    data = request.get_json(silent=True) or {}
    nombre = limpiar_categoria(data.get("nombre"))
    categoria = limpiar_categoria(data.get("categoria"))
    items, error = _opciones(data)
    if not nombre:
        return jsonify(ok=False, error="Escribí el nombre del grupo (ej. Yogurt aflanado)."), 400
    if error:
        return jsonify(ok=False, error=error), 400
    if len(items) < 2:
        return jsonify(ok=False, error="Un grupo necesita al menos 2 productos."), 400

    ids = [i["id"] for i in items]
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, nombre, grupo_id FROM productos WHERE id = ANY(%s)", (ids,))
        filas = cur.fetchall()
        if len(filas) != len(ids):
            return jsonify(ok=False, error="Algún producto ya no existe. Recargá la página."), 404
        ya = [f[1] for f in filas if f[2]]
        if ya:
            return jsonify(ok=False, error="Ya están en otro grupo: " + ", ".join(ya) + "."), 409
        _asegurar_categoria(cur, categoria)
        cur.execute("SELECT COALESCE(MAX(grupo_id), 0) + 1 FROM productos")
        gid = cur.fetchone()[0]
        for it in items:
            cur.execute("""UPDATE productos
                           SET grupo_id = %s, grupo_nombre = %s, nombre_variante = %s,
                               categoria = COALESCE(NULLIF(%s, ''), categoria)
                           WHERE id = %s""", (gid, nombre, it["variante"], categoria, it["id"]))
        conn.commit()
    return jsonify(ok=True, gid=gid)


@productos_bp.route("/productos/grupos/api/<int:gid>/guardar", methods=["POST"])
def grupo_guardar(gid):
    data = request.get_json(silent=True) or {}
    nombre = limpiar_categoria(data.get("nombre"))
    categoria = limpiar_categoria(data.get("categoria"))
    items, error = _opciones(data)
    if not nombre:
        return jsonify(ok=False, error="Escribí el nombre del grupo."), 400
    if error:
        return jsonify(ok=False, error=error), 400
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM productos WHERE grupo_id = %s", (gid,))
        miembros = {r[0] for r in cur.fetchall()}
        if not miembros:
            return jsonify(ok=False, error="Ese grupo ya no existe."), 404
        _asegurar_categoria(cur, categoria)
        cur.execute("""UPDATE productos
                       SET grupo_nombre = %s, categoria = COALESCE(NULLIF(%s, ''), categoria)
                       WHERE grupo_id = %s""", (nombre, categoria, gid))
        for it in items:
            if it["id"] in miembros:
                cur.execute("UPDATE productos SET nombre_variante = %s WHERE id = %s AND grupo_id = %s",
                            (it["variante"], it["id"], gid))
        conn.commit()
    return jsonify(ok=True)


@productos_bp.route("/productos/grupos/api/<int:gid>/agregar", methods=["POST"])
def grupo_agregar(gid):
    data = request.get_json(silent=True) or {}
    variante = limpiar_categoria(data.get("variante"))
    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Producto inválido."), 400
    if not variante:
        return jsonify(ok=False, error="Escribí el nombre de la opción (ej. Fresa, Grande)."), 400
    with obtener_conexion() as conn:
        cur = conn.cursor()
        datos = _grupo_datos(cur, gid)
        if not datos:
            return jsonify(ok=False, error="Ese grupo ya no existe."), 404
        if variante.lower() in {i["variante"].lower() for i in datos["items"]}:
            return jsonify(ok=False, error=f"Ya hay una opción llamada «{variante}» en este grupo."), 409
        cur.execute("SELECT grupo_id FROM productos WHERE id = %s", (pid,))
        fila = cur.fetchone()
        if not fila:
            return jsonify(ok=False, error="Ese producto ya no existe."), 404
        if fila[0]:
            return jsonify(ok=False, error="Ese producto ya está en otro grupo."), 409
        cur.execute("""UPDATE productos
                       SET grupo_id = %s, grupo_nombre = %s, nombre_variante = %s, categoria = %s
                       WHERE id = %s""", (gid, datos["nombre"], variante, datos["categoria"], pid))
        conn.commit()
    return jsonify(ok=True)


def _sacar_de_grupo(cur, pid):
    cur.execute("SELECT grupo_id FROM productos WHERE id = %s", (pid,))
    fila = cur.fetchone()
    if not fila or not fila[0]:
        return
    gid = fila[0]
    cur.execute("""UPDATE productos SET grupo_id = NULL, grupo_nombre = NULL, nombre_variante = NULL
                   WHERE id = %s""", (pid,))
    # Un grupo de un solo producto no tiene sentido: se deshace solo
    cur.execute("SELECT COUNT(*) FROM productos WHERE grupo_id = %s", (gid,))
    if cur.fetchone()[0] <= 1:
        cur.execute("""UPDATE productos SET grupo_id = NULL, grupo_nombre = NULL, nombre_variante = NULL
                       WHERE grupo_id = %s""", (gid,))


@productos_bp.route("/productos/grupos/api/quitar", methods=["POST"])
def grupo_quitar():
    data = request.get_json(silent=True) or {}
    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Producto inválido."), 400
    with obtener_conexion() as conn:
        cur = conn.cursor()
        _sacar_de_grupo(cur, pid)
        conn.commit()
    return jsonify(ok=True)


@productos_bp.route("/productos/grupos/api/<int:gid>/deshacer", methods=["POST"])
def grupo_deshacer(gid):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""UPDATE productos SET grupo_id = NULL, grupo_nombre = NULL, nombre_variante = NULL
                       WHERE grupo_id = %s""", (gid,))
        conn.commit()
    return jsonify(ok=True)


# =====================================================
# PESOS PREESTABLECIDOS (en gramos) de un producto
#   La tienda los ofrece como botones (ej. 250 g, 500 g, 1 kg).
# =====================================================
@productos_bp.route("/productos/pesos/api/<int:pid>")
def pesos_lista(pid):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT nombre, unidad FROM productos WHERE id = %s", (pid,))
        prod = cur.fetchone()
        if not prod:
            return jsonify(ok=False, error="Ese producto ya no existe."), 404
        cur.execute("SELECT gramos FROM producto_pesos WHERE producto_id = %s ORDER BY gramos", (pid,))
        gramos = [int(r[0]) for r in cur.fetchall()]
    return jsonify(ok=True, nombre=prod[0], unidad=prod[1], gramos=gramos)


@productos_bp.route("/productos/pesos/api/<int:pid>/guardar", methods=["POST"])
def pesos_guardar(pid):
    data = request.get_json(silent=True) or {}
    try:
        gramos = sorted({int(x) for x in (data.get("gramos") or [])})
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Los pesos deben ser números."), 400
    if any(g <= 0 or g > 100000 for g in gramos):
        return jsonify(ok=False, error="Cada peso debe estar entre 1 y 100,000 gramos."), 400
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM productos WHERE id = %s", (pid,))
        if not cur.fetchone():
            return jsonify(ok=False, error="Ese producto ya no existe."), 404
        cur.execute("DELETE FROM producto_pesos WHERE producto_id = %s", (pid,))
        for g in gramos:
            cur.execute("INSERT INTO producto_pesos (producto_id, gramos) VALUES (%s, %s)", (pid, g))
        conn.commit()
    return jsonify(ok=True)
