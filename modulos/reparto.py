# -*- coding: utf-8 -*-
"""
Reparto: la pantalla del repartidor.

Flujo del pedido:  Preparando  →  Alistado  →  Entregado
  · La oficina marca "Alistado" en el kanban cuando el pedido está listo para salir.
  · El repartidor ve solo los Alistados, en el orden de la ruta, y marca:
      Entregado + cómo pagó (Efectivo / SINPE / No pagó)  → pasa a Entregados en el kanban
      No se pudo entregar + motivo                        → sigue Alistado, con aviso rojo
  · SINPE marcado por el repartidor queda "por confirmar" hasta que la oficina vea el dinero.
  · Cierre: resumen del dinero por rango de fecha y hora.

Orden de la ruta: sale de la finca y visita primero lo más cercano, usando las
coordenadas que el cliente marcó en el mapa (distancia en línea recta).
  · Hasta 11 paradas: ruta óptima exacta (programación dinámica, Held-Karp).
  · Más paradas: vecino más cercano + mejora 2-opt (casi óptima).
Los pedidos sin ubicación van después, y los que ya fallaron hoy van al final.
"""
import math
import os
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db.conexion import obtener_conexion
from modulos.pedidos import convertir_en_venta, avisar_entrega, METODOS_PAGO

reparto_bp = Blueprint("reparto_bp", __name__)

# Punto de salida del repartidor (la finca). Se puede cambiar con la variable REPARTO_SALIDA="lat,lng".
FINCA = (10.087484, -84.476671)
try:
    _lat, _lng = (float(x) for x in os.environ.get("REPARTO_SALIDA", "").split(","))
    FINCA = (_lat, _lng)
except ValueError:
    pass

MOTIVOS = ["No estaba nadie", "No contestó", "Dirección incorrecta", "Pidió otro día"]


def _asegurar_columnas():
    try:
        with obtener_conexion() as conn:
            cur = conn.cursor()
            # Cómo pagó en la entrega (efectivo / sinpe / no): es lo que el repartidor rinde en el cierre,
            # aunque después la oficina cambie el estado de la venta.
            cur.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS pago_reparto VARCHAR(12)")
            conn.commit()
    except Exception as ex:
        print("No se pudieron asegurar las columnas de reparto:", ex)

_asegurar_columnas()


# ─────────────────────────────────────────────
#  ORDEN DE LA RUTA
# ─────────────────────────────────────────────
def _km(a, b):
    """Distancia en km entre dos puntos (lat, lng) sobre la Tierra (fórmula de Haversine)."""
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _orden_exacto(dist):
    """Held-Karp para un camino abierto que sale del punto 0. dist es una matriz (n+1)x(n+1)."""
    n = len(dist) - 1
    INF = float("inf")
    # costo[mascara][j] = menor distancia saliendo de 0, visitando 'mascara' y terminando en j
    costo = [[INF] * n for _ in range(1 << n)]
    previo = [[-1] * n for _ in range(1 << n)]
    for j in range(n):
        costo[1 << j][j] = dist[0][j + 1]
    for mascara in range(1 << n):
        for j in range(n):
            c = costo[mascara][j]
            if c == INF or not (mascara >> j) & 1:
                continue
            for k in range(n):
                if (mascara >> k) & 1:
                    continue
                nueva = mascara | (1 << k)
                d = c + dist[j + 1][k + 1]
                if d < costo[nueva][k]:
                    costo[nueva][k] = d
                    previo[nueva][k] = j
    completa = (1 << n) - 1
    fin = min(range(n), key=lambda j: costo[completa][j])
    orden, mascara = [], completa
    while fin != -1:
        orden.append(fin)
        mascara, fin = mascara ^ (1 << fin), previo[mascara][fin]
    return orden[::-1]


def _orden_aproximado(dist):
    """Vecino más cercano y luego 2-opt (deshace cruces mientras mejore)."""
    n = len(dist) - 1
    pendientes, actual, orden = set(range(n)), -1, []
    while pendientes:
        sig = min(pendientes, key=lambda k: dist[actual + 1][k + 1])
        orden.append(sig); pendientes.remove(sig); actual = sig

    def largo(o):
        return dist[0][o[0] + 1] + sum(dist[o[i] + 1][o[i + 1] + 1] for i in range(len(o) - 1))

    mejor, mejoro = largo(orden), True
    while mejoro:
        mejoro = False
        for i in range(len(orden) - 1):
            for k in range(i + 1, len(orden)):
                prueba = orden[:i] + orden[i:k + 1][::-1] + orden[k + 1:]
                lp = largo(prueba)
                if lp + 1e-9 < mejor:
                    orden, mejor, mejoro = prueba, lp, True
    return orden


def ordenar_ruta(puntos, salida=FINCA):
    """Recibe [(lat, lng), ...] y devuelve (orden de índices, km totales)."""
    if not puntos:
        return [], 0.0
    todos = [salida] + list(puntos)
    dist = [[_km(a, b) for b in todos] for a in todos]
    orden = _orden_exacto(dist) if len(puntos) <= 11 else _orden_aproximado(dist)
    km = dist[0][orden[0] + 1] + sum(dist[orden[i] + 1][orden[i + 1] + 1] for i in range(len(orden) - 1))
    return orden, km


# ─────────────────────────────────────────────
#  PANTALLA DEL REPARTIDOR
# ─────────────────────────────────────────────
def _ultimos_8(celular):
    return "".join(ch for ch in str(celular or "") if ch.isdigit())[-8:]


def _direccion(notas, dir_cliente):
    """Separa la dirección de las notas del cliente.
    Formato guardado: "📍 dirección — nota" y, si el cliente agregó productos
    después, " ➕ otra nota" al final. Devuelve (dirección, [notas])."""
    texto = (notas or "").strip()
    direccion = (dir_cliente or "").strip()
    if texto.startswith("📍"):
        texto = texto[1:].strip()
        corte = len(texto)
        for sep in (" — ", " ➕ "):
            i = texto.find(sep)
            if i != -1:
                corte = min(corte, i)
        direccion, texto = texto[:corte].strip(), texto[corte:].strip()
        if texto.startswith("—"):
            texto = texto[1:].strip()
    lista = [n.strip() for n in texto.replace("➕", "\n").split("\n") if n.strip()]
    return direccion, lista


def armar_ruta(cur):
    """Pedidos alistados en el orden de la ruta. Devuelve (ruta, km). La usan la
    pantalla de Reparto y el Inicio del Admin."""
    cur.execute("""
        SELECT p.id, COALESCE(c.nombre, p.nombre_contacto), p.celular, p.total_estimado,
               p.notas, c.direccion, p.lat, p.lng, p.entrega_fallida, p.creado
        FROM pedidos p LEFT JOIN clientes c ON c.id = p.cliente_id
        WHERE p.estado = 'Pendiente' AND p.seguimiento IN ('Alistado', 'Listo')
          AND COALESCE(p.entrega, 'domicilio') <> 'feria'
        ORDER BY p.creado
    """)
    filas = cur.fetchall()
    ids = [f[0] for f in filas]
    productos = {}
    huevos = {}
    if ids:
        cur.execute("""
            SELECT d.pedido_id, pr.nombre, pr.unidad, d.cantidad
            FROM pedidos_detalle d JOIN productos pr ON pr.id = d.producto_id
            WHERE d.pedido_id = ANY(%s) AND d.producto_id <> 118
            ORDER BY pr.nombre
        """, (ids,))
        for pid, nom, uni, cant in cur.fetchall():
            linea = _cantidad(cant, uni) + " · " + nom
            productos.setdefault(pid, []).append(linea)
            if "huevo" in (nom or "").lower():          # cualquier tipo de huevos
                huevos.setdefault(pid, []).append(linea)

    entregas = []
    for pid, nombre, cel, total, notas, dir_cli, lat, lng, fallida, creado in filas:
        direccion, nota = _direccion(notas, dir_cli)
        entregas.append({
            "id": pid, "nombre": nombre or "", "cel8": _ultimos_8(cel), "total": float(total or 0),
            "direccion": direccion, "nota": nota, "fallida": (fallida or "").strip(),
            "lat": float(lat) if lat is not None else None, "lng": float(lng) if lng is not None else None,
            "productos": productos.get(pid, []),
            "huevos": huevos.get(pid, []),
        })

    # Orden: con ubicación (ruta optimizada) → sin ubicación → los que ya fallaron, al final
    con_ubic = [e for e in entregas if e["lat"] is not None and not e["fallida"]]
    sin_ubic = [e for e in entregas if e["lat"] is None and not e["fallida"]]
    fallidas = [e for e in entregas if e["fallida"]]
    orden, km = ordenar_ruta([(e["lat"], e["lng"]) for e in con_ubic])
    ruta = [con_ubic[i] for i in orden] + sin_ubic + fallidas
    return ruta, km


@reparto_bp.route("/reparto")
def reparto():
    with obtener_conexion() as conn:
        ruta, km = armar_ruta(conn.cursor())
    return render_template("reparto.html", ruta=ruta, km=km, motivos=MOTIVOS,
                           total=sum(e["total"] for e in ruta))


def _cantidad(cant, unidad):
    cant = float(cant)
    u = (unidad or "").strip().lower()
    if u in ("gramo", "gramos", "g"):
        return f"{cant / 1000:g} kg".replace(".", ",") if cant >= 1000 else f"{cant:g} g"
    return f"{cant:g} {u}".strip().replace(".", ",")


@reparto_bp.route("/reparto/<int:id>/entregar", methods=["POST"])
def entregar(id):
    pago = request.form.get("pago")
    if pago not in ("efectivo", "sinpe", "no"):
        flash("Elegí cómo pagó el cliente.", "danger")
        return redirect(url_for("reparto_bp.reparto"))
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT celular, COALESCE(nombre_contacto, '') FROM pedidos WHERE id = %s", (id,))
        fila = cur.fetchone()
        venta_id, error = convertir_en_venta(cur, id, METODOS_PAGO[pago],
                                             sinpe_pendiente=(pago == "sinpe"), por_reparto=True)
        if error:
            flash(error, "warning")
            return redirect(url_for("reparto_bp.reparto"))
        cur.execute("UPDATE pedidos SET pago_reparto = %s WHERE id = %s", (pago, id))
        conn.commit()
    avisar_entrega(fila[0] if fila else None, id)
    texto = {"efectivo": "pagó en efectivo", "sinpe": "pagó por SINPE (por confirmar)", "no": "queda por cobrar"}[pago]
    flash(f"✓ {fila[1] if fila else 'Pedido'} entregado, {texto}.", "success")
    return redirect(url_for("reparto_bp.reparto"))


@reparto_bp.route("/reparto/<int:id>/no-entregado", methods=["POST"])
def no_entregado(id):
    motivo = (request.form.get("motivo") or "").strip()
    otro = (request.form.get("otro") or "").strip()
    motivo = (otro if motivo == "otro" else motivo)[:200] or "Sin motivo"
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""UPDATE pedidos SET entrega_fallida = %s, entrega_fallida_en = NOW()
                       WHERE id = %s AND estado = 'Pendiente'""", (motivo, id))
        conn.commit()
    flash("Anotado. El pedido queda al final de la ruta y la oficina lo ve en el kanban.", "warning")
    return redirect(url_for("reparto_bp.reparto"))


# ─────────────────────────────────────────────
#  CIERRE: resumen del dinero por rango de fecha y hora
#  (la base guarda la hora de Costa Rica, así que se compara directo)
# ─────────────────────────────────────────────
def _leer_fecha(valor, por_defecto):
    try:
        return datetime.strptime(valor, "%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        return por_defecto


@reparto_bp.route("/reparto/cierre")
def cierre():
    ahora = datetime.utcnow() - timedelta(hours=6)
    desde = _leer_fecha(request.args.get("desde"), ahora.replace(hour=0, minute=0, second=0, microsecond=0))
    hasta = _leer_fecha(request.args.get("hasta"), ahora.replace(second=0, microsecond=0))
    if hasta < desde:
        desde, hasta = hasta, desde

    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, COALESCE(c.nombre, p.nombre_contacto), v.total, p.pago_reparto,
                   v.metodo_pago, COALESCE(v.sinpe_pendiente, FALSE), p.gestionado
            FROM pedidos p
            JOIN ventas v ON v.id = p.venta_id
            LEFT JOIN clientes c ON c.id = p.cliente_id
            WHERE p.entregado_reparto AND p.estado = 'Resuelto'
              AND p.gestionado BETWEEN %s AND %s
              AND COALESCE(v.estado, '') NOT ILIKE 'anul%%'
            ORDER BY p.gestionado
        """, (desde, hasta + timedelta(seconds=59)))
        entregas = cur.fetchall()
        cur.execute("""
            SELECT p.id, COALESCE(c.nombre, p.nombre_contacto), p.total_estimado, p.entrega_fallida,
                   p.entrega_fallida_en
            FROM pedidos p LEFT JOIN clientes c ON c.id = p.cliente_id
            WHERE p.entrega_fallida_en BETWEEN %s AND %s AND p.estado = 'Pendiente'
            ORDER BY p.entrega_fallida_en
        """, (desde, hasta + timedelta(seconds=59)))
        fallidas = cur.fetchall()

    filas, tot = [], {"efectivo": 0.0, "sinpe": 0.0, "sinpe_pend": 0.0, "no": 0.0}
    for pid, nombre, total, pago, metodo, sinpe_pend, cuando in entregas:
        total = float(total or 0)
        pago = pago or ("sinpe" if metodo == "SINPE Móvil" else "no" if metodo == "Por cobrar" else "efectivo")
        tot[pago] += total
        if pago == "sinpe" and sinpe_pend:
            tot["sinpe_pend"] += total
        filas.append({"id": pid, "nombre": nombre or "", "total": total, "pago": pago,
                      "sinpe_pend": bool(sinpe_pend), "cobrado_luego": pago == "no" and metodo != "Por cobrar",
                      "hora": cuando})
    resumen = {
        "entregas": len(filas), "facturado": tot["efectivo"] + tot["sinpe"] + tot["no"],
        "efectivo": tot["efectivo"], "sinpe": tot["sinpe"], "sinpe_pend": tot["sinpe_pend"],
        "no": tot["no"], "fallidas": len(fallidas),
    }
    return render_template("reparto_cierre.html", filas=filas, fallidas=fallidas, resumen=resumen,
                           desde=desde.strftime("%Y-%m-%dT%H:%M"), hasta=hasta.strftime("%Y-%m-%dT%H:%M"),
                           desde_txt=desde.strftime("%d/%m/%Y %I:%M %p"), hasta_txt=hasta.strftime("%d/%m/%Y %I:%M %p"))
