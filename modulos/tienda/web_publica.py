# -*- coding: utf-8 -*-
"""
Web Pública ECOLTURA
Ubicación: modulos/tienda/web_publica.py
Blueprint registrado en app.py como: from modulos.tienda.web_publica import web_bp
"""
import os
from flask import Blueprint, render_template, jsonify, request, abort
from db.conexion import obtener_conexion
from modulos.push import notificar_admins, vincular_suscripciones_huerfanas
from modulos.categorias import asegurar_categorias
from modulos.correo import correo_valido, enviar_confirmacion_pedido
from datetime import datetime, timedelta


def _es_viernes():
    """Los viernes los pedidos no se pueden modificar ni cancelar (hora de Costa Rica, UTC-6)."""
    return (datetime.utcnow() - timedelta(hours=6)).weekday() == 4


MSJ_VIERNES = "¡Ya estamos preparando tu pedido! 🌱 Para hacerle cambios, escribinos por WhatsApp y con gusto te ayudamos."

web_bp = Blueprint("web_bp", __name__)

# El orden de las categorías de la tienda sale de la tabla `categorias` (se administra desde el Admin)
asegurar_categorias()

# Columna "destacado" (la maneja el Admin); aquí se asegura por si la tienda arranca primero
def _asegurar_destacado():
    try:
        with obtener_conexion() as conn:
            cur = conn.cursor()
            cur.execute("ALTER TABLE productos ADD COLUMN IF NOT EXISTS destacado BOOLEAN DEFAULT FALSE")
            cur.execute("ALTER TABLE productos ADD COLUMN IF NOT EXISTS regenerativo BOOLEAN DEFAULT FALSE")
            # Correo opcional del cliente (para la confirmación del pedido)
            cur.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS correo VARCHAR(120)")
            # Ubicación de entrega marcada en el mapa (opcional)
            cur.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS lat NUMERIC(9,6), ADD COLUMN IF NOT EXISTS lng NUMERIC(9,6)")
            cur.execute("ALTER TABLE pedidos  ADD COLUMN IF NOT EXISTS lat NUMERIC(9,6), ADD COLUMN IF NOT EXISTS lng NUMERIC(9,6)")
            conn.commit()
    except Exception as ex:
        print("No se pudo asegurar la columna destacado:", ex)

_asegurar_destacado()


def normalizar_celular(valor):
    """Deja solo los 8 dígitos del número: ignora guiones, espacios, paréntesis y el +506."""
    digitos = "".join(c for c in str(valor or "") if c.isdigit())
    if len(digitos) > 8 and digitos.startswith("506"):
        digitos = digitos[3:]
    return digitos

# Compara contra los últimos 8 dígitos de lo guardado (aunque esté como 8888-7777 o +506 8888 7777)
SQL_CEL_LIMPIO = "RIGHT(REGEXP_REPLACE(COALESCE(celular, ''), '[^0-9]', '', 'g'), 8)"

# ─────────────────────────────────────────────
#  PÁGINA PRINCIPAL (sirve el HTML de la web)
# ─────────────────────────────────────────────
@web_bp.route("/")
def index():
    return render_template("tienda/web_index.html")


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
                ) AS stock_real,
                p.grupo_id,
                p.grupo_nombre,
                p.nombre_variante,
                COALESCE(p.destacado, FALSE),
                COALESCE(p.regenerativo, FALSE)
            FROM productos p
            WHERE p.visible_web = TRUE OR p.id = 118   -- Express (envío) va siempre
            ORDER BY
                COALESCE((SELECT c.orden FROM categorias c
                          WHERE LOWER(c.nombre) = LOWER(REGEXP_REPLACE(TRIM(COALESCE(p.categoria, '')), '\\s+', ' ', 'g'))
                          LIMIT 1), 999),
                p.nombre
        """)
        rows = cur.fetchall()

        # "Más vendidos": cuántas ventas tuvo cada producto en los últimos 60 días.
        # Si algo falla aquí, la tienda sigue funcionando sin ese dato.
        ventas_por_producto = {}
        try:
            cur.execute("SAVEPOINT rk")
            cur.execute("""
                SELECT vd.producto_id, COUNT(DISTINCT v.id)
                FROM ventas_detalle vd JOIN ventas v ON v.id = vd.venta_id
                WHERE v.fecha_venta::timestamp >= NOW() - INTERVAL '60 days'
                  AND COALESCE(v.estado, '') NOT ILIKE 'anul%'
                GROUP BY vd.producto_id
            """)
            ventas_por_producto = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute("RELEASE SAVEPOINT rk")
        except Exception as ex:
            print("No se pudo calcular más vendidos:", ex)
            cur.execute("ROLLBACK TO SAVEPOINT rk")

        # Pesos preestablecidos (kg/g) por producto — parametrizables desde
        # la tabla producto_pesos, sin necesidad de tocar código para
        # agregar o quitar opciones.
        cur.execute("SELECT producto_id, gramos FROM producto_pesos ORDER BY producto_id, gramos")
        pesos_por_producto = {}
        for producto_id, gramos in cur.fetchall():
            pesos_por_producto.setdefault(producto_id, []).append(gramos)

    productos = [
        {
            "id":        r[0],
            "nombre":    r[1],
            "categoria": " ".join(str(r[2] or "General").split()) or "General",
            "unidad":    r[3],
            "precio":    float(r[4]),
            "stock":     float(r[5]),
            "pesos":     pesos_por_producto.get(r[0], []),
            "grupo_id":       r[6],
            "grupo_nombre":   r[7],
            "nombre_variante": r[8],
            "destacado": bool(r[9]),
            "regenerativo": bool(r[10]),
            "ventas": ventas_por_producto.get(r[0], 0),
        }
        for r in rows
        if float(r[5]) > 0 or r[0] == 118   # solo con stock; Express (118) siempre, aunque su stock llegue a 0
    ]

    # Agrupar productos que comparten grupo_id en una sola tarjeta con
    # variantes (ej. "Miel de abeja" con 1.4kg / 750g / 360g) — parametrizable
    # desde las columnas grupo_id/grupo_nombre/nombre_variante, sin tocar código.
    grupos = {}
    resultado = []
    for p in productos:
        gid = p.pop("grupo_id")
        gnom = p.pop("grupo_nombre")
        p["nombre_variante"] = p.pop("nombre_variante")
        if gid:
            if gid not in grupos:
                entrada = {
                    "esGrupo": True,
                    "id": f"g{gid}",
                    "nombre": gnom or p["nombre"],
                    "categoria": p["categoria"],
                    "variantes": [],
                    "destacado": False,
                    "regenerativo": False,
                    "ventas": 0,
                }
                grupos[gid] = entrada
                resultado.append(entrada)
            grupos[gid]["variantes"].append(p)
            grupos[gid]["destacado"] = grupos[gid]["destacado"] or p["destacado"]
            grupos[gid]["regenerativo"] = grupos[gid]["regenerativo"] or p["regenerativo"]
            grupos[gid]["ventas"] += p["ventas"]
        else:
            del p["nombre_variante"]
            resultado.append(p)

    return jsonify(resultado)


# ─────────────────────────────────────────────
#  API: ENVIAR PEDIDO
# ─────────────────────────────────────────────
#  API: BUSCAR CLIENTE POR CELULAR (precarga)
# ─────────────────────────────────────────────
@web_bp.route("/api/cliente/<celular>")
def api_cliente_por_celular(celular):
    """Si el celular ya existe, devuelve nombre, dirección Y el historial combinado
    de TODOS los clientes que comparten ese celular (familia que comparte teléfono)."""
    celular = normalizar_celular(celular)
    if len(celular) < 8:
        return jsonify({"existe": False})

    with obtener_conexion() as conn:
        cur = conn.cursor()

        # 1. Todos los clientes con ese celular (puede haber varios: familia)
        cur.execute(
            f"SELECT id, nombre, direccion FROM clientes WHERE {SQL_CEL_LIMPIO} = %s ORDER BY id",
            (celular,)
        )
        clientes = cur.fetchall()
        if not clientes:
            return jsonify({"existe": False})

        ids = [c[0] for c in clientes]
        # Nombre y dirección: usar el del registro más reciente (último id)
        nombre    = clientes[-1][1]
        direccion = clientes[-1][2] or ""

        # 2. Última compra — la venta más reciente entre TODOS los registros del celular
        cur.execute("""
            SELECT p.id, p.nombre, p.unidad, p.precio, d.cantidad,
                   (
                       (SELECT COALESCE(SUM(cantidad),0) FROM inventario_movimientos
                        WHERE producto_id = p.id AND tipo = 'C')
                       -
                       (SELECT COALESCE(SUM(CASE WHEN tipo='F' THEN cantidad
                                                 WHEN tipo='A' THEN -cantidad ELSE 0 END),0)
                        FROM inventario_movimientos WHERE producto_id = p.id)
                   ) AS stock
            FROM ventas v
            JOIN ventas_detalle d ON d.venta_id = v.id
            JOIN productos p      ON p.id = d.producto_id
            WHERE v.cliente_id = ANY(%s)
              AND v.estado != 'Anulado'
              AND p.visible_web = TRUE
              AND v.id = (
                  SELECT v2.id FROM ventas v2
                  WHERE v2.cliente_id = ANY(%s) AND v2.estado != 'Anulado'
                  ORDER BY v2.fecha_venta DESC, v2.id DESC
                  LIMIT 1
              )
            ORDER BY p.nombre
        """, (ids, ids))

        ultima_compra = []
        for r in cur.fetchall():
            stock = float(r[5] or 0)
            if stock > 0:   # solo productos que aún se pueden comprar
                ultima_compra.append({
                    "id": r[0],
                    "nombre": r[1],
                    "unidad": r[2],
                    "precio": float(r[3]),
                    "cantidad": float(r[4]),
                    "stock": stock
                })

        # 3. Frecuencia de compra — historial COMBINADO, solo productos visibles en web
        cur.execute("""
            SELECT d.producto_id, COUNT(DISTINCT v.id) AS veces
            FROM ventas v
            JOIN ventas_detalle d ON d.venta_id = v.id
            JOIN productos p      ON p.id = d.producto_id
            WHERE v.cliente_id = ANY(%s)
              AND v.estado != 'Anulado'
              AND p.visible_web = TRUE
            GROUP BY d.producto_id
            ORDER BY veces DESC, d.producto_id
        """, (ids,))
        productos_frecuentes = [r[0] for r in cur.fetchall()]

    return jsonify({
        "existe": True,
        "nombre": nombre,
        "direccion": direccion,
        "ultima_compra": ultima_compra,
        "productos_frecuentes": productos_frecuentes
    })


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
    celular   = normalizar_celular(data.get("celular"))
    direccion = (data.get("direccion") or "").strip()
    notas     = (data.get("notas")     or "").strip()
    items     = data.get("items", [])
    agregar_a = (data.get("agregar_a") or "").strip()   # token del pedido en curso que ve el cliente
    correo    = correo_valido(data.get("correo"))           # opcional: si viene mal escrito se ignora
    lat, lng  = _coordenadas(data)                         # opcional: punto de entrega en el mapa
    # Cómo lo recibe: a domicilio (con envío y mapa) o en la feria (lo recoge, sin envío)
    entrega   = "feria" if (data.get("entrega") or "").strip().lower() == "feria" else "domicilio"
    if entrega == "feria":
        lat, lng = None, None

    if not nombre or not celular:
        return jsonify({"ok": False, "error": "Nombre y celular son obligatorios."}), 400
    if len(celular) != 8:
        return jsonify({"ok": False, "error": "El celular debe tener 8 dígitos."}), 400
    if not direccion and entrega == "domicilio":
        return jsonify({"ok": False, "error": "La dirección de entrega es obligatoria."}), 400
    if not items:
        return jsonify({"ok": False, "error": "El pedido está vacío."}), 400

    with obtener_conexion() as conn:
        cur = conn.cursor()

        # 1. Buscar o crear cliente — MATCH POR CELULAR
        cliente_id = None
        cur.execute(f"SELECT id FROM clientes WHERE {SQL_CEL_LIMPIO} = %s ORDER BY id LIMIT 1", (celular,))
        row = cur.fetchone()
        if row:
            cliente_id = row[0]
            # Actualizar nombre y dirección con los datos más recientes.
            # Si recoge en la feria, la dirección de su casa NO se toca.
            if direccion:
                cur.execute(
                    "UPDATE clientes SET nombre=%s, direccion=%s WHERE id=%s",
                    (nombre, direccion, cliente_id)
                )
            else:
                cur.execute("UPDATE clientes SET nombre=%s WHERE id=%s", (nombre, cliente_id))
        else:
            # Cliente nuevo desde la web: cédula vacía (se completa luego desde el app),
            # marcado como cliente_domicilio = TRUE (la web siempre es entrega a domicilio)
            cur.execute("""
                INSERT INTO clientes
                    (nombre, cedula, celular, direccion,
                     cliente_feria, cliente_domicilio,
                     cliente_suscripcion, cliente_sin_modelo_venta)
                VALUES (%s, NULL, %s, %s, FALSE, TRUE, FALSE, FALSE)
                RETURNING id
            """, (nombre, celular, direccion))
            cliente_id = cur.fetchone()[0]

        if entrega == "feria":
            cur.execute("UPDATE clientes SET cliente_feria = TRUE WHERE id = %s", (cliente_id,))

        # 1c. Ubicación: si la marcó, queda guardada en su ficha para el próximo pedido
        if lat is not None:
            cur.execute("UPDATE clientes SET lat = %s, lng = %s WHERE id = %s", (lat, lng, cliente_id))

        # 1b. Correo: si lo escribió se guarda; si no, se usa el que ya teníamos
        if correo:
            cur.execute("UPDATE clientes SET correo = %s WHERE id = %s", (correo, cliente_id))
        else:
            cur.execute("SELECT correo FROM clientes WHERE id = %s", (cliente_id,))
            correo = correo_valido((cur.fetchone() or [None])[0])

        # 1a. Si este celular activó antes la campana sin ser cliente, se vincula ahora
        vincular_suscripciones_huerfanas(cur)

        # 2. Validar productos y agrupar cantidades (si un producto viene repetido, se suma)
        try:
            nuevos = _agrupar_items(items)
        except (ValueError, KeyError, TypeError):
            return jsonify({"ok": False, "error": "El pedido tiene datos inválidos."}), 400
        nuevos.pop(ID_EXPRESS, None)
        if not nuevos:
            return jsonify({"ok": False, "error": "El pedido está vacío."}), 400
        precios = _precios_disponibles(cur, list(nuevos.keys()))
        if precios is None:
            return jsonify({"ok": False, "error": "Uno o más productos no están disponibles."}), 400

        # 3. ¿Ya tiene un pedido abierto? Entonces se une a ese: así el cliente nunca
        #    tiene dos pedidos pendientes a la vez. Primero el pedido en curso que la
        #    tienda le muestra (por su enlace privado); si no, el de este celular.
        #    Los viernes no se une: se crea uno aparte.
        abierto = None
        if not _es_viernes():
            if agregar_a:
                cur.execute("SELECT id, token, lat FROM pedidos WHERE token = %s AND estado = 'Pendiente'", (agregar_a,))
                abierto = cur.fetchone()
            if not abierto:
                cur.execute(f"""
                    SELECT id, token, lat FROM pedidos
                    WHERE estado = 'Pendiente' AND {SQL_CEL_LIMPIO} = %s
                    ORDER BY id DESC LIMIT 1
                """, (celular,))
                abierto = cur.fetchone()

        # La ubicación en el mapa es obligatoria: sin ella el repartidor no puede armar la ruta.
        # Solo se acepta sin punto si se agrega a un pedido en curso que ya lo tiene.
        if entrega == "domicilio" and lat is None and not (abierto and abierto[2] is not None):
            conn.rollback()
            return jsonify({"ok": False, "falta_ubicacion": True,
                            "error": "Marcá en el mapa dónde te llevamos el pedido."}), 400

        if abierto:
            pedido_id, token = abierto[0], abierto[1]
            existentes = _leer_lineas(cur, pedido_id)
            for pid, cant in nuevos.items():
                if pid in existentes:
                    cant_vieja, precio, tipo, valor = existentes[pid]
                    cur.execute("""UPDATE pedidos_detalle SET cantidad = %s
                                   WHERE pedido_id = %s AND producto_id = %s""",
                                (cant_vieja + cant, pedido_id, pid))
                else:
                    cur.execute("""INSERT INTO pedidos_detalle (pedido_id, producto_id, cantidad, precio_unitario)
                                   VALUES (%s, %s, %s, %s)""", (pedido_id, pid, cant, precios[pid]))
            # La forma de entrega que elige ahora vale para todo el pedido
            cur.execute("SELECT COALESCE(entrega, 'domicilio'), notas FROM pedidos WHERE id = %s", (pedido_id,))
            entrega_antes, notas_antes = cur.fetchone()
            if entrega != entrega_antes:
                prefijo = PREFIJO_FERIA if entrega == "feria" else f"📍 {direccion}"
                cur.execute("UPDATE pedidos SET entrega = %s, notas = %s WHERE id = %s",
                            (entrega, _cambiar_prefijo(notas_antes, prefijo), pedido_id))
            _asegurar_express(cur, pedido_id)
            total = _recalcular_total_pedido(cur, pedido_id)
            extra = f" ➕ {notas}" if notas else ""
            cur.execute("""UPDATE pedidos SET modificado = TRUE, modificado_en = NOW(), creado = NOW(),
                                  notas = COALESCE(notas, '') || %s,
                                  lat = COALESCE(%s, lat), lng = COALESCE(%s, lng)
                           WHERE id = %s""", (extra, lat, lng, pedido_id))
            conn.commit()
            unido = True
        else:
            # 4. Pedido nuevo (la dirección va en notas para que el admin la vea)
            prefijo = PREFIJO_FERIA if entrega == "feria" else f"📍 {direccion}"
            notas_completa = prefijo + (f" — {notas}" if notas else "")
            cur.execute("""
                INSERT INTO pedidos
                    (cliente_id, nombre_contacto, cedula, celular, notas, total_estimado, estado, creado, lat, lng, entrega)
                VALUES (%s, %s, '', %s, %s, 0, 'Pendiente', NOW(), %s, %s, %s)
                RETURNING id, token
            """, (cliente_id, nombre, celular, notas_completa, lat, lng, entrega))
            pedido_id, token = cur.fetchone()
            for pid, cant in nuevos.items():
                cur.execute("""INSERT INTO pedidos_detalle (pedido_id, producto_id, cantidad, precio_unitario)
                               VALUES (%s, %s, %s, %s)""", (pedido_id, pid, cant, precios[pid]))
            _asegurar_express(cur, pedido_id)
            total = _recalcular_total_pedido(cur, pedido_id)
            conn.commit()
            unido = False

    # Aviso al celular de quienes tienen activadas las notificaciones del admin
    try:
        if unido:
            notificar_admins(f"🛒 Pedido #{pedido_id} actualizado", f"{nombre} agregó productos · ₡{total:,.0f}", f"/admin/pedidos/{pedido_id}")
        else:
            notificar_admins("🛒 Nuevo pedido web", f"{nombre} · ₡{total:,.0f}", f"/admin/pedidos/{pedido_id}")
    except Exception as ex:
        print(f"No se pudo avisar del pedido #{pedido_id}: {ex}")

    # Correo de confirmación al cliente (en segundo plano; nunca frena el pedido)
    try:
        enviar_confirmacion_pedido(correo, token, _base_tienda(), unido)
    except Exception as ex:
        print(f"No se pudo preparar el correo del pedido #{pedido_id}: {ex}")

    return jsonify({"ok": True, "pedido_id": pedido_id, "token": token, "unido": unido,
                    "correo": correo if os.environ.get("RESEND_API_KEY") else ""})


# ─────────────────────────────────────────────
#  SEGUIMIENTO DEL PEDIDO (lo que ve el cliente)
#  El enlace privado lleva un código largo (token) que solo conoce el cliente.
#  Preparando = Pendiente · Entregado = Resuelto · Cancelado = Rechazado
# ─────────────────────────────────────────────
ID_EXPRESS = 118
ESTADO_CLIENTE = {"Pendiente": "Preparando", "Resuelto": "Entregado", "Rechazado": "Cancelado"}


def _coordenadas(data):
    """(lat, lng) si el cliente marcó su casa en el mapa y el punto está en Costa Rica; si no, (None, None)."""
    try:
        lat, lng = float(data.get("lat")), float(data.get("lng"))
    except (TypeError, ValueError):
        return None, None
    if 5.0 <= lat <= 11.5 and -86.5 <= lng <= -82.4:
        return round(lat, 6), round(lng, 6)
    return None, None


def _base_tienda():
    """Dirección de la tienda desde donde llegó el pedido (para los enlaces del correo)."""
    base = request.url_root.rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[len("http://"):]
    return base


def _agrupar_items(items):
    """{producto_id: cantidad} sumando repetidos; ignora cantidades <= 0."""
    out = {}
    for i in items:
        pid = int(i["producto_id"])
        cant = float(i["cantidad"])
        if cant > 0:
            out[pid] = out.get(pid, 0.0) + cant
    return out


def _precios_disponibles(cur, ids):
    """{id: precio} si TODOS están visibles en la web; None si alguno no lo está."""
    if not ids:
        return {}
    cur.execute("SELECT id, precio FROM productos WHERE id = ANY(%s) AND visible_web = TRUE", (ids,))
    precios = {r[0]: float(r[1]) for r in cur.fetchall()}
    return precios if set(ids).issubset(precios.keys()) else None


def _leer_lineas(cur, pedido_id):
    cur.execute("""SELECT producto_id, cantidad, precio_unitario,
                          COALESCE(descuento_tipo, 'monto'), COALESCE(descuento_valor, 0)
                   FROM pedidos_detalle WHERE pedido_id = %s""", (pedido_id,))
    return {r[0]: (float(r[1]), float(r[2]), r[3], float(r[4])) for r in cur.fetchall()}


PREFIJO_FERIA = "🧺 Recoger en la Feria San Ramón"


def _cambiar_prefijo(notas, prefijo):
    """Cambia la primera parte de las notas ("📍 dirección" o "🧺 feria") y deja el resto."""
    texto = (notas or "").strip()
    if texto.startswith("📍") or texto.startswith("🧺"):
        corte = len(texto)
        for sep in (" — ", " ➕ "):
            i = texto.find(sep)
            if i != -1:
                corte = min(corte, i)
        texto = texto[corte:]
    else:
        texto = (" — " + texto) if texto else ""
    return prefijo + texto


def _asegurar_express(cur, pedido_id):
    """El envío (Express) va siempre una vez, al precio actual de la base.
    Si el pedido es para recoger en la feria, no lleva envío (se quita si estaba)."""
    cur.execute("SELECT COALESCE(entrega, 'domicilio') FROM pedidos WHERE id = %s", (pedido_id,))
    fila = cur.fetchone()
    if fila and fila[0] == "feria":
        cur.execute("DELETE FROM pedidos_detalle WHERE pedido_id = %s AND producto_id = %s", (pedido_id, ID_EXPRESS))
        return
    cur.execute("SELECT precio FROM productos WHERE id = %s", (ID_EXPRESS,))
    fila = cur.fetchone()
    if not fila:
        return
    cur.execute("SELECT 1 FROM pedidos_detalle WHERE pedido_id = %s AND producto_id = %s", (pedido_id, ID_EXPRESS))
    if not cur.fetchone():
        cur.execute("""INSERT INTO pedidos_detalle (pedido_id, producto_id, cantidad, precio_unitario)
                       VALUES (%s, %s, 1, %s)""", (pedido_id, ID_EXPRESS, float(fila[0])))


def _desc(subtotal, tipo, valor):
    valor = float(valor or 0)
    if valor <= 0 or subtotal <= 0:
        return 0.0
    if tipo == 'porcentaje':
        return float(round(subtotal * min(valor, 100.0) / 100.0))
    return float(round(min(valor, subtotal)))


def _recalcular_total_pedido(cur, pedido_id):
    """Igual que el Admin: suma de líneas (con su descuento) menos el descuento general."""
    subtotal = 0.0
    for cant, precio, tipo, valor in _leer_lineas(cur, pedido_id).values():
        base = float(round(cant * precio))
        subtotal += float(round(base - _desc(base, tipo, valor)))
    cur.execute("SELECT COALESCE(descuento_tipo, 'monto'), COALESCE(descuento_valor, 0) FROM pedidos WHERE id = %s", (pedido_id,))
    tipo, valor = cur.fetchone()
    total = float(round(subtotal - _desc(subtotal, tipo, valor)))
    cur.execute("UPDATE pedidos SET total_estimado = %s WHERE id = %s", (total, pedido_id))
    return total


@web_bp.route("/pedido/<token>")
def pagina_pedido(token):
    return render_template("tienda/web_pedido.html", token=token)


@web_bp.route("/api/pedido/<token>")
def api_pedido_estado(token):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.estado, p.nombre_contacto, p.total_estimado, p.creado, v.metodo_pago,
                   COALESCE(p.entrega, 'domicilio')
            FROM pedidos p LEFT JOIN ventas v ON v.id = p.venta_id
            WHERE p.token = %s
        """, (token,))
        fila = cur.fetchone()
        if not fila:
            return jsonify({"ok": False, "error": "No encontramos este pedido."}), 404
        pid, estado, nombre, total, creado, metodo, entrega = fila
        cur.execute("""
            SELECT d.producto_id, pr.nombre, pr.unidad, d.cantidad, d.precio_unitario,
                   COALESCE(d.descuento_tipo, 'monto'), COALESCE(d.descuento_valor, 0)
            FROM pedidos_detalle d JOIN productos pr ON pr.id = d.producto_id
            WHERE d.pedido_id = %s ORDER BY (d.producto_id = %s), pr.nombre
        """, (pid, ID_EXPRESS))
        items, suma = [], 0.0
        for prod, nom, uni, cant, precio, tipo, valor in cur.fetchall():
            base = float(round(float(cant) * float(precio)))
            neto = float(round(base - _desc(base, tipo, valor)))
            suma += neto
            items.append({"producto_id": prod, "nombre": nom, "unidad": uni, "cantidad": float(cant),
                          "subtotal": neto, "envio": prod == ID_EXPRESS})
    total = float(total or 0)
    return jsonify({
        "ok": True,
        "numero": pid,
        "estado": ESTADO_CLIENTE.get(estado, "Preparando"),
        "editable": estado == "Pendiente" and not _es_viernes(),
        "viernes": estado == "Pendiente" and _es_viernes(),
        "pagado": bool(metodo) and metodo != "Por cobrar",
        "entrega": entrega,
        "nombre": nombre,
        "total": total,
        "descuento": max(0.0, suma - total),
        "items": items,
    })


@web_bp.route("/api/pedido/activo", methods=["POST"])
def api_pedido_activo():
    """Recupera el pedido en preparación cuando el celular no lo tiene guardado
    (lo hizo en otro navegador, en Safari y no en la app instalada, o lo registró el Admin).
    La prueba de identidad es la suscripción de notificaciones de ESTE celular: su
    dirección (endpoint) es larga y secreta, solo la conocen el navegador y el servidor.
    Por eso no se busca solo por número de celular: cualquiera podría escribir uno ajeno."""
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"ok": False})
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT cliente_id, celular FROM push_subscriptions
                       WHERE endpoint = %s AND tipo = 'cliente' AND COALESCE(activo, TRUE)""", (endpoint,))
        fila = cur.fetchone()
        if not fila:
            return jsonify({"ok": False})
        cliente_id, cel = fila[0], normalizar_celular(fila[1])
        cur.execute(f"""SELECT id, token FROM pedidos
                        WHERE estado = 'Pendiente'
                          AND (cliente_id = %s OR (%s <> '' AND {SQL_CEL_LIMPIO} = %s))
                        ORDER BY id DESC LIMIT 1""", (cliente_id, cel, cel))
        pedido = cur.fetchone()
    if not pedido:
        return jsonify({"ok": False})
    return jsonify({"ok": True, "numero": pedido[0], "token": pedido[1]})


@web_bp.route("/api/pedido/<token>/editar", methods=["POST"])
def api_pedido_editar(token):
    """Reemplaza las líneas del pedido por las que manda el cliente (mientras siga Preparando)."""
    data = request.get_json(force=True)
    try:
        nuevos = _agrupar_items(data.get("items", []))
    except (ValueError, KeyError, TypeError):
        return jsonify({"ok": False, "error": "El pedido tiene datos inválidos."}), 400
    nuevos.pop(ID_EXPRESS, None)
    if not nuevos:
        return jsonify({"ok": False, "error": "El pedido no puede quedar vacío. Si ya no lo quiere, puede cancelarlo."}), 400

    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, estado, nombre_contacto FROM pedidos WHERE token = %s FOR UPDATE", (token,))
        fila = cur.fetchone()
        if not fila:
            return jsonify({"ok": False, "error": "No encontramos este pedido."}), 404
        pedido_id, estado, nombre = fila
        if estado != "Pendiente":
            return jsonify({"ok": False, "error": "Este pedido ya no se puede modificar."}), 409
        if _es_viernes():
            return jsonify({"ok": False, "error": MSJ_VIERNES}), 409

        # Los productos que ya estaban en el pedido se pueden dejar aunque hoy no estén en la tienda
        existentes = _leer_lineas(cur, pedido_id)
        por_validar = [p for p in nuevos if p not in existentes]
        precios = _precios_disponibles(cur, por_validar)
        if precios is None:
            return jsonify({"ok": False, "error": "Uno o más productos no están disponibles."}), 400

        for pid in list(existentes.keys()):
            if pid != ID_EXPRESS and pid not in nuevos:
                cur.execute("DELETE FROM pedidos_detalle WHERE pedido_id = %s AND producto_id = %s", (pedido_id, pid))
        for pid, cant in nuevos.items():
            if pid in existentes:
                cur.execute("UPDATE pedidos_detalle SET cantidad = %s WHERE pedido_id = %s AND producto_id = %s",
                            (cant, pedido_id, pid))
            else:
                cur.execute("""INSERT INTO pedidos_detalle (pedido_id, producto_id, cantidad, precio_unitario)
                               VALUES (%s, %s, %s, %s)""", (pedido_id, pid, cant, precios[pid]))
        _asegurar_express(cur, pedido_id)
        total = _recalcular_total_pedido(cur, pedido_id)
        cur.execute("UPDATE pedidos SET modificado = TRUE, modificado_en = NOW() WHERE id = %s", (pedido_id,))
        # Correo guardado del cliente de este pedido (para mandarle el pedido actualizado)
        cur.execute("SELECT c.correo FROM pedidos p JOIN clientes c ON c.id = p.cliente_id WHERE p.id = %s", (pedido_id,))
        correo = correo_valido((cur.fetchone() or [None])[0])
        conn.commit()

    try:
        notificar_admins(f"✏️ Pedido #{pedido_id} modificado", f"{nombre} cambió su pedido · ₡{total:,.0f}", f"/admin/pedidos/{pedido_id}")
    except Exception as ex:
        print(f"No se pudo avisar de la edición del pedido #{pedido_id}: {ex}")
    try:
        enviar_confirmacion_pedido(correo, token, _base_tienda(), "editado")
    except Exception as ex:
        print(f"No se pudo preparar el correo del pedido #{pedido_id}: {ex}")
    return jsonify({"ok": True, "pedido_id": pedido_id, "total": total,
                    "correo": correo if os.environ.get("RESEND_API_KEY") else ""})


@web_bp.route("/api/pedido/<token>/cancelar", methods=["POST"])
def api_pedido_cancelar(token):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, estado, nombre_contacto FROM pedidos WHERE token = %s FOR UPDATE", (token,))
        fila = cur.fetchone()
        if not fila:
            return jsonify({"ok": False, "error": "No encontramos este pedido."}), 404
        pedido_id, estado, nombre = fila
        if estado != "Pendiente":
            return jsonify({"ok": False, "error": "Este pedido ya no se puede cancelar."}), 409
        if _es_viernes():
            return jsonify({"ok": False, "error": MSJ_VIERNES}), 409
        cur.execute("UPDATE pedidos SET estado = 'Rechazado', gestionado = NOW() WHERE id = %s", (pedido_id,))
        cur.execute("SELECT c.correo FROM pedidos p JOIN clientes c ON c.id = p.cliente_id WHERE p.id = %s", (pedido_id,))
        correo = correo_valido((cur.fetchone() or [None])[0])
        conn.commit()
    try:
        notificar_admins(f"❌ Pedido #{pedido_id} cancelado", f"{nombre} canceló su pedido", f"/admin/pedidos/{pedido_id}")
    except Exception as ex:
        print(f"No se pudo avisar de la cancelación del pedido #{pedido_id}: {ex}")
    try:
        enviar_confirmacion_pedido(correo, token, _base_tienda(), "cancelado")
    except Exception as ex:
        print(f"No se pudo preparar el correo de cancelación del pedido #{pedido_id}: {ex}")
    return jsonify({"ok": True})


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
    celular  = normalizar_celular(data.get("celular"))
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
