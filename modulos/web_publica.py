# -*- coding: utf-8 -*-
"""
Web Pública ECOLTURA
Ubicación: modulos/web_publica.py
Blueprint registrado en app.py como: from modulos.web_publica import web_bp
"""
from flask import Blueprint, render_template, jsonify, request, abort
from db.conexion import obtener_conexion
from datetime import datetime, timedelta

web_bp = Blueprint("web_bp", __name__)

# ─────────────────────────────────────────────
#  PÁGINA PRINCIPAL (sirve el HTML de la web)
# ─────────────────────────────────────────────
@web_bp.route("/")
def index():
    return render_template("web_index.html")


# ─────────────────────────────────────────────
#  API: PRODUCTOS VISIBLES EN LA WEB
# ─────────────────────────────────────────────
@web_bp.route("/api/productos")
def api_productos():
    """
    Devuelve productos con visible_web=TRUE y stock > 0.
    Incluye: id, nombre, categoria, unidad, precio, stock_real.
    El frontend construye la URL de la foto como /static/img/productos/{id}.jpg
    """
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                p.id,
                p.nombre,
                COALESCE(p.categoria, 'General')  AS categoria,
                p.unidad,
                p.precio,
                -- Stock real por kardex (mismo cálculo que el app interno)
                (
                    SELECT COALESCE(SUM(cantidad), 0)
                    FROM inventario_movimientos
                    WHERE producto_id = p.id AND tipo = 'C'
                ) -
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
                ) AS stock_real
            FROM productos p
            WHERE p.visible_web = TRUE
            ORDER BY p.categoria, p.nombre
        """)
        rows = cur.fetchall()

    productos = [
        {
            "id":        r[0],
            "nombre":    r[1],
            "categoria": r[2],
            "unidad":    r[3],
            "precio":    float(r[4]),
            "stock":     float(r[5]),
        }
        for r in rows
        if float(r[5]) > 0          # solo productos con stock disponible
    ]
    return jsonify(productos)


# ─────────────────────────────────────────────
#  API: ENVIAR PEDIDO
# ─────────────────────────────────────────────
#  API: BUSCAR CLIENTE POR CELULAR (precarga)
# ─────────────────────────────────────────────
@web_bp.route("/api/cliente/<celular>")
def api_cliente_por_celular(celular):
    """Si el celular ya existe, devuelve nombre y dirección para precargar el formulario."""
    celular = celular.strip()
    if len(celular) < 8:
        return jsonify({"existe": False})

    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT nombre, direccion FROM clientes WHERE celular = %s LIMIT 1",
            (celular,)
        )
        row = cur.fetchone()

    if row:
        return jsonify({"existe": True, "nombre": row[0], "direccion": row[1] or ""})
    return jsonify({"existe": False})


# ─────────────────────────────────────────────
@web_bp.route("/api/pedidos", methods=["POST"])
def api_pedidos():
    """
    Recibe el pedido de la web.
    Body JSON:
    {
        "nombre":  "María García",
        "cedula":  "123456789",
        "celular": "88887777",
        "notas":   "...",
        "items": [
            {"producto_id": 1, "cantidad": 2},
            ...
        ]
    }
    1. Busca al cliente por cédula; si no existe lo crea.
    2. Inserta en pedidos + pedidos_detalle.
    3. Retorna {"ok": true, "pedido_id": N}.
    """
    data = request.get_json(force=True)

    nombre    = (data.get("nombre")    or "").strip()
    celular   = (data.get("celular")   or "").strip()
    direccion = (data.get("direccion") or "").strip()
    notas     = (data.get("notas")     or "").strip()
    items     = data.get("items", [])

    if not nombre or not celular:
        return jsonify({"ok": False, "error": "Nombre y celular son obligatorios."}), 400
    if not direccion:
        return jsonify({"ok": False, "error": "La dirección de entrega es obligatoria."}), 400
    if not items:
        return jsonify({"ok": False, "error": "El pedido está vacío."}), 400

    with obtener_conexion() as conn:
        cur = conn.cursor()

        # 1. Buscar o crear cliente — MATCH POR CELULAR
        cliente_id = None
        cur.execute("SELECT id FROM clientes WHERE celular = %s LIMIT 1", (celular,))
        row = cur.fetchone()
        if row:
            cliente_id = row[0]
            # Actualizar nombre y dirección con los datos más recientes
            cur.execute(
                "UPDATE clientes SET nombre=%s, direccion=%s WHERE id=%s",
                (nombre, direccion, cliente_id)
            )
        else:
            # Cliente nuevo desde la web: cédula vacía (se completa luego desde el app),
            # marcado como cliente_domicilio = TRUE (la web siempre es entrega a domicilio)
            cur.execute("""
                INSERT INTO clientes
                    (nombre, cedula, celular, direccion,
                     cliente_feria, cliente_domicilio,
                     cliente_suscripcion, cliente_sin_modelo_venta)
                VALUES (%s, '', %s, %s, FALSE, TRUE, FALSE, FALSE)
                RETURNING id
            """, (nombre, celular, direccion))
            cliente_id = cur.fetchone()[0]

        # 2. Calcular total estimado y validar precios
        producto_ids = [i["producto_id"] for i in items]
        cur.execute(
            "SELECT id, precio FROM productos WHERE id = ANY(%s) AND visible_web = TRUE",
            (producto_ids,)
        )
        precios = {r[0]: float(r[1]) for r in cur.fetchall()}

        if len(precios) != len(producto_ids):
            return jsonify({"ok": False, "error": "Uno o más productos no están disponibles."}), 400

        total = sum(precios[i["producto_id"]] * float(i["cantidad"]) for i in items)

        # 3. Insertar pedido (la dirección va en notas para que el admin la vea)
        notas_completa = f"📍 {direccion}" + (f" — {notas}" if notas else "")
        cur.execute("""
            INSERT INTO pedidos
                (cliente_id, nombre_contacto, cedula, celular, notas, total_estimado, estado, creado)
            VALUES (%s, %s, '', %s, %s, %s, 'Pendiente', NOW())
            RETURNING id
        """, (cliente_id, nombre, celular, notas_completa, total))
        pedido_id = cur.fetchone()[0]

        # 4. Insertar detalle
        for item in items:
            cur.execute("""
                INSERT INTO pedidos_detalle (pedido_id, producto_id, cantidad, precio_unitario)
                VALUES (%s, %s, %s, %s)
            """, (pedido_id, item["producto_id"], float(item["cantidad"]), precios[item["producto_id"]]))

        conn.commit()

    return jsonify({"ok": True, "pedido_id": pedido_id})


# ─────────────────────────────────────────────
#  API: FECHAS DE VISITA DISPONIBLES
# ─────────────────────────────────────────────
@web_bp.route("/api/visitas/fechas")
def api_visitas_fechas():
    """
    Retorna fechas habilitadas con cupos disponibles (cupos - reservas confirmadas/pendientes).
    Solo fechas futuras.
    """
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                vf.id,
                vf.fecha,
                vf.cupos,
                vf.cupos - COALESCE(SUM(
                    CASE WHEN vr.estado IN ('Pendiente','Confirmada') THEN vr.personas ELSE 0 END
                ), 0) AS cupos_libres
            FROM visitas_fechas vf
            LEFT JOIN visitas_reservas vr ON vr.fecha_id = vf.id
            WHERE vf.activa = TRUE AND vf.fecha >= CURRENT_DATE
            GROUP BY vf.id, vf.fecha, vf.cupos
            HAVING vf.cupos - COALESCE(SUM(
                CASE WHEN vr.estado IN ('Pendiente','Confirmada') THEN vr.personas ELSE 0 END
            ), 0) > 0
            ORDER BY vf.fecha ASC
        """)
        rows = cur.fetchall()

    return jsonify([
        {
            "id":           r[0],
            "fecha":        r[1].strftime("%Y-%m-%d"),
            "cupos_libres": int(r[3]),
        }
        for r in rows
    ])


# ─────────────────────────────────────────────
#  API: CREAR RESERVA DE VISITA
# ─────────────────────────────────────────────
@web_bp.route("/api/visitas/reservar", methods=["POST"])
def api_visitas_reservar():
    """
    Body JSON:
    {
        "fecha_id": 3,
        "nombre":   "Ana López",
        "celular":  "88887777",
        "personas": 4
    }
    """
    data = request.get_json(force=True)

    fecha_id = data.get("fecha_id")
    nombre   = (data.get("nombre")  or "").strip()
    celular  = (data.get("celular") or "").strip()
    personas = int(data.get("personas", 1))

    if not all([fecha_id, nombre, celular]):
        return jsonify({"ok": False, "error": "Datos incompletos."}), 400

    with obtener_conexion() as conn:
        cur = conn.cursor()

        # Verificar cupos disponibles
        cur.execute("""
            SELECT
                vf.cupos - COALESCE(SUM(
                    CASE WHEN vr.estado IN ('Pendiente','Confirmada') THEN vr.personas ELSE 0 END
                ), 0) AS libres
            FROM visitas_fechas vf
            LEFT JOIN visitas_reservas vr ON vr.fecha_id = vf.id
            WHERE vf.id = %s AND vf.activa = TRUE
            GROUP BY vf.cupos
        """, (fecha_id,))
        row = cur.fetchone()

        if not row or int(row[0]) < personas:
            return jsonify({"ok": False, "error": "No hay suficientes cupos para esa fecha."}), 400

        cur.execute("""
            INSERT INTO visitas_reservas (fecha_id, nombre, celular, personas, estado, creado)
            VALUES (%s, %s, %s, %s, 'Pendiente', NOW())
            RETURNING id
        """, (fecha_id, nombre, celular, personas))
        reserva_id = cur.fetchone()[0]
        conn.commit()

    return jsonify({"ok": True, "reserva_id": reserva_id})
