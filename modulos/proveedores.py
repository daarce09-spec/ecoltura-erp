# -*- coding: utf-8 -*-
"""
Proveedores (dentro de Inventario).

- Un proveedor tiene nombre y teléfono y puede tener VARIOS productos.
- Cada producto puede estar ligado a varios proveedores, pero solo UNO es el
  proveedor OFICIAL (el que sale en el PDF de pedidos). Esto lo garantiza un
  índice único parcial en la base de datos, no solo la pantalla.
- Los productos agrupados (Piña Grande / Piña Pequeña, Yogurt aflanado Fresa /
  Mora...) se asignan juntos: al ligar uno, quedan ligadas todas sus opciones.
"""
from flask import Blueprint, render_template, jsonify, request
from db.conexion import obtener_conexion

proveedores_bp = Blueprint("proveedores_bp", __name__)


def asegurar_tablas():
    """Crea las tablas si no existen (seguro de correr muchas veces)."""
    try:
        with obtener_conexion() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS proveedores (
                    id       SERIAL PRIMARY KEY,
                    nombre   VARCHAR(120) NOT NULL,
                    telefono VARCHAR(40),
                    creado   TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_proveedores_nombre
                           ON proveedores (LOWER(nombre))""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS producto_proveedores (
                    producto_id  INTEGER NOT NULL REFERENCES productos(id)   ON DELETE CASCADE,
                    proveedor_id INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE CASCADE,
                    oficial      BOOLEAN NOT NULL DEFAULT FALSE,
                    PRIMARY KEY (producto_id, proveedor_id)
                )
            """)
            # Solo un proveedor oficial por producto
            cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_producto_proveedor_oficial
                           ON producto_proveedores (producto_id) WHERE oficial""")
            conn.commit()
    except Exception as ex:
        print("No se pudieron asegurar las tablas de proveedores:", ex)


asegurar_tablas()


def oficiales_por_producto(cur):
    """{producto_id: (proveedor_id, nombre, telefono)} del proveedor oficial."""
    cur.execute("""
        SELECT pp.producto_id, pv.id, pv.nombre, COALESCE(pv.telefono, '')
        FROM producto_proveedores pp
        JOIN proveedores pv ON pv.id = pp.proveedor_id
        WHERE pp.oficial
    """)
    return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}


# ─────────────────────────────────────────────
#  Página
# ─────────────────────────────────────────────
@proveedores_bp.route("/inventario/proveedores")
def proveedores_pagina():
    return render_template("proveedores.html")


# ─────────────────────────────────────────────
#  API: lista de proveedores
# ─────────────────────────────────────────────
@proveedores_bp.route("/inventario/proveedores/api/lista")
def api_lista():
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT pv.id, pv.nombre, COALESCE(pv.telefono, ''),
                   COUNT(pp.producto_id)                       AS n_prod,
                   COUNT(pp.producto_id) FILTER (WHERE pp.oficial) AS n_ofi
            FROM proveedores pv
            LEFT JOIN producto_proveedores pp ON pp.proveedor_id = pv.id
            GROUP BY pv.id, pv.nombre, pv.telefono
            ORDER BY LOWER(pv.nombre)
        """)
        proveedores = [{"id": r[0], "nombre": r[1], "telefono": r[2],
                        "n_productos": r[3], "n_oficiales": r[4]} for r in cur.fetchall()]
        # Productos visibles en la tienda que todavía no tienen proveedor oficial
        cur.execute("""
            SELECT COUNT(*) FROM productos p
            WHERE p.visible_web = TRUE
              AND NOT EXISTS (SELECT 1 FROM producto_proveedores pp
                              WHERE pp.producto_id = p.id AND pp.oficial)
        """)
        sin_oficial = cur.fetchone()[0]
    return jsonify(proveedores=proveedores, sin_oficial=sin_oficial)


# ─────────────────────────────────────────────
#  API: crear / editar proveedor
# ─────────────────────────────────────────────
@proveedores_bp.route("/inventario/proveedores/api/guardar", methods=["POST"])
def api_guardar():
    data = request.get_json(silent=True) or {}
    nombre = " ".join(str(data.get("nombre", "")).split())
    telefono = " ".join(str(data.get("telefono", "")).split())
    if not nombre:
        return jsonify(ok=False, error="Escribí el nombre del proveedor."), 400

    with obtener_conexion() as conn:
        cur = conn.cursor()
        pid = data.get("id")
        cur.execute("""SELECT id FROM proveedores
                       WHERE LOWER(nombre) = LOWER(%s) AND (%s IS NULL OR id <> %s)""",
                    (nombre, pid, pid))
        if cur.fetchone():
            return jsonify(ok=False, error="Ya existe un proveedor con ese nombre."), 409
        if pid:
            cur.execute("UPDATE proveedores SET nombre=%s, telefono=%s WHERE id=%s RETURNING id",
                        (nombre, telefono or None, pid))
        else:
            cur.execute("INSERT INTO proveedores (nombre, telefono) VALUES (%s, %s) RETURNING id",
                        (nombre, telefono or None))
        row = cur.fetchone()
        conn.commit()
    if not row:
        return jsonify(ok=False, error="No se encontró el proveedor."), 404
    return jsonify(ok=True, id=row[0])


@proveedores_bp.route("/inventario/proveedores/api/<int:pid>/eliminar", methods=["POST"])
def api_eliminar(pid):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM proveedores WHERE id=%s", (pid,))
        conn.commit()
    return jsonify(ok=True)


# ─────────────────────────────────────────────
#  API: productos de un proveedor (agrupados por opciones)
# ─────────────────────────────────────────────
@proveedores_bp.route("/inventario/proveedores/api/<int:pid>/productos")
def api_productos(pid):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.nombre, TRIM(COALESCE(p.categoria, '')), p.grupo_id,
                   p.grupo_nombre, p.nombre_variante, COALESCE(p.visible_web, FALSE),
                   EXISTS (SELECT 1 FROM producto_proveedores x
                           WHERE x.producto_id = p.id AND x.proveedor_id = %s)        AS asignado,
                   EXISTS (SELECT 1 FROM producto_proveedores x
                           WHERE x.producto_id = p.id AND x.proveedor_id = %s AND x.oficial) AS oficial,
                   (SELECT pv.nombre FROM producto_proveedores x
                    JOIN proveedores pv ON pv.id = x.proveedor_id
                    WHERE x.producto_id = p.id AND x.oficial AND x.proveedor_id <> %s
                    LIMIT 1)                                                           AS oficial_otro
            FROM productos p
            ORDER BY TRIM(COALESCE(p.categoria, '')), COALESCE(p.grupo_nombre, p.nombre), p.nombre
        """, (pid, pid, pid))
        filas = cur.fetchall()

    unidades, orden = {}, []
    for (id_, nombre, cat, gid, gnombre, variante, visible,
         asignado, oficial, oficial_otro) in filas:
        clave = ("g", gid) if gid else ("p", id_)
        u = unidades.get(clave)
        if u is None:
            u = {"ids": [], "nombre": gnombre if gid and gnombre else nombre,
                 "categoria": cat or "Sin categoría", "variantes": [],
                 "visible": False, "asignado": False, "oficial": False, "oficial_otro": None}
            unidades[clave] = u
            orden.append(clave)
        u["ids"].append(id_)
        if variante:
            u["variantes"].append(variante)
        u["visible"] = u["visible"] or visible
        u["asignado"] = u["asignado"] or asignado
        u["oficial"] = u["oficial"] or oficial
        u["oficial_otro"] = u["oficial_otro"] or oficial_otro
    return jsonify(productos=[unidades[k] for k in orden])


# ─────────────────────────────────────────────
#  API: ligar / desligar productos a un proveedor
# ─────────────────────────────────────────────
def _ids(data):
    try:
        return sorted({int(x) for x in (data.get("ids") or [])})
    except (TypeError, ValueError):
        return []


@proveedores_bp.route("/inventario/proveedores/api/<int:pid>/asignar", methods=["POST"])
def api_asignar(pid):
    data = request.get_json(silent=True) or {}
    ids = _ids(data)
    if not ids:
        return jsonify(ok=False, error="Producto inválido."), 400
    with obtener_conexion() as conn:
        cur = conn.cursor()
        if data.get("asignado"):
            for prod in ids:
                cur.execute("""INSERT INTO producto_proveedores (producto_id, proveedor_id, oficial)
                               VALUES (%s, %s, FALSE) ON CONFLICT DO NOTHING""", (prod, pid))
        else:
            cur.execute("""DELETE FROM producto_proveedores
                           WHERE proveedor_id = %s AND producto_id = ANY(%s)""", (pid, ids))
        conn.commit()
    return jsonify(ok=True)


@proveedores_bp.route("/inventario/proveedores/api/<int:pid>/oficial", methods=["POST"])
def api_oficial(pid):
    """Marca (o quita) a este proveedor como el oficial. Solo puede haber uno por producto."""
    data = request.get_json(silent=True) or {}
    ids = _ids(data)
    if not ids:
        return jsonify(ok=False, error="Producto inválido."), 400
    with obtener_conexion() as conn:
        cur = conn.cursor()
        if data.get("oficial"):
            # Quita al oficial anterior (si lo hay) y deja a este
            cur.execute("""UPDATE producto_proveedores SET oficial = FALSE
                           WHERE producto_id = ANY(%s) AND proveedor_id <> %s AND oficial""",
                        (ids, pid))
            for prod in ids:
                cur.execute("""INSERT INTO producto_proveedores (producto_id, proveedor_id, oficial)
                               VALUES (%s, %s, TRUE)
                               ON CONFLICT (producto_id, proveedor_id) DO UPDATE SET oficial = TRUE""",
                            (prod, pid))
        else:
            cur.execute("""UPDATE producto_proveedores SET oficial = FALSE
                           WHERE producto_id = ANY(%s) AND proveedor_id = %s""", (ids, pid))
        conn.commit()
    return jsonify(ok=True)
