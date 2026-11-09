# -*- coding: utf-8 -*-
"""
Contabilidad de gestión de ECOLTURA (Entrega 1).

- Estado de resultados por periodo, con desglose por cuenta y subcuenta y % sobre ventas.
- Las ventas salen solas de la tabla `ventas` (sin anuladas):
    · las que vienen de un pedido web  → subcuenta "Tienda web"
    · las registradas a mano           → subcuenta "Feria y venta directa"
- Todo lo demás (compras a la Finca, gastos, otros) se anota como movimiento.
- La Finca es solo un proveedor: sus costos de producción no entran aquí.

Tablas propias (se crean solas al arrancar):
    cuentas      → catálogo de dos niveles (cuenta → subcuenta), cada una con su sección fija
    movimientos  → montos anotados a mano, siempre sobre una subcuenta
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db.conexion import obtener_conexion
from datetime import datetime, timedelta, date

contabilidad_bp = Blueprint("contabilidad_bp", __name__)


# ─────────────────────────────────────────────
#  SECCIONES DEL ESTADO DE RESULTADOS (fijas)
#  signo: +1 suma a la utilidad, -1 resta
# ─────────────────────────────────────────────
SECCIONES = [
    ("venta",       "Ventas",                   +1),
    ("costo",       "Costo de ventas",          -1),
    ("gasto_venta", "Gastos de venta",          -1),
    ("gasto_admin", "Gastos de administración", -1),
    ("otro_ingreso", "Otros ingresos",          +1),
    ("otro_gasto",  "Otros gastos",             -1),
]
NOMBRE_SECCION = {s[0]: s[1] for s in SECCIONES}
SIGNO = {s[0]: s[2] for s in SECCIONES}

# Subcuentas que se llenan solas con las ventas (no admiten movimientos a mano)
AUTO_WEB = "venta_web"
AUTO_DIRECTA = "venta_directa"

# Catálogo inicial: (sección, cuenta, [subcuentas]); una subcuenta puede ser (nombre, clave)
CATALOGO_INICIAL = [
    ("venta", "Ventas", [("Tienda web", AUTO_WEB), ("Feria y venta directa", AUTO_DIRECTA), "Visitas a la finca"]),
    ("costo", "Compras a proveedores", ["Finca Ecoltura", "Otros proveedores"]),
    ("costo", "Empaque", ["Bolsas, cajas y etiquetas"]),
    ("costo", "Transporte de compras", ["Fletes"]),
    ("gasto_venta", "Gastos de venta", ["Salarios de operación", "Transporte de entregas", "Puesto de feria", "Publicidad"]),
    ("gasto_admin", "Gastos de administración", ["Salarios administrativos", "Luz", "Internet y teléfono", "Sistemas", "Mantenimiento"]),
    ("otro_ingreso", "Otros ingresos", ["Otros ingresos"]),
    ("otro_gasto", "Otros gastos", ["Comisiones bancarias", "Otros gastos"]),
]


def asegurar_tablas():
    """Crea las tablas si no existen y, si el catálogo está vacío, lo siembra."""
    try:
        with obtener_conexion() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cuentas (
                    id        SERIAL PRIMARY KEY,
                    nombre    VARCHAR(80) NOT NULL,
                    seccion   VARCHAR(20) NOT NULL,
                    padre_id  INTEGER REFERENCES cuentas(id),
                    orden     INTEGER NOT NULL DEFAULT 999,
                    activa    BOOLEAN NOT NULL DEFAULT TRUE,
                    clave     VARCHAR(30) UNIQUE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS movimientos (
                    id         SERIAL PRIMARY KEY,
                    fecha      DATE NOT NULL,
                    cuenta_id  INTEGER NOT NULL REFERENCES cuentas(id),
                    monto      NUMERIC(14,2) NOT NULL,
                    nota       TEXT,
                    creado     TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS ix_movimientos_fecha ON movimientos (fecha)")
            cur.execute("SELECT COUNT(*) FROM cuentas")
            if cur.fetchone()[0] == 0:
                for i, (seccion, cuenta, subs) in enumerate(CATALOGO_INICIAL, start=1):
                    cur.execute("""INSERT INTO cuentas (nombre, seccion, orden) VALUES (%s, %s, %s)
                                   RETURNING id""", (cuenta, seccion, i))
                    padre = cur.fetchone()[0]
                    for j, sub in enumerate(subs, start=1):
                        nombre, clave = (sub if isinstance(sub, tuple) else (sub, None))
                        cur.execute("""INSERT INTO cuentas (nombre, seccion, padre_id, orden, clave)
                                       VALUES (%s, %s, %s, %s, %s)""", (nombre, seccion, padre, j, clave))
            conn.commit()
    except Exception as ex:
        print("No se pudieron asegurar las tablas de contabilidad:", ex)


asegurar_tablas()


# ─────────────────────────────────────────────
#  PERIODO (hora de Costa Rica)
# ─────────────────────────────────────────────
def _hoy_cr():
    return (datetime.utcnow() - timedelta(hours=6)).date()


def _leer_fecha(texto):
    try:
        return datetime.strptime(texto or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def _periodo(args):
    """(clave, desde, hasta, etiqueta) según ?periodo=... y, si es rango, ?desde=&hasta="""
    hoy = _hoy_cr()
    clave = args.get("periodo", "mes")
    inicio_mes = hoy.replace(day=1)
    if clave == "anterior":
        hasta = inicio_mes - timedelta(days=1)
        desde = hasta.replace(day=1)
    elif clave == "anio":
        desde, hasta = hoy.replace(month=1, day=1), hoy
    elif clave == "rango":
        desde = _leer_fecha(args.get("desde")) or inicio_mes
        hasta = _leer_fecha(args.get("hasta")) or hoy
        if hasta < desde:
            desde, hasta = hasta, desde
    else:
        clave, desde, hasta = "mes", inicio_mes, hoy
    etiqueta = f"{desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
    return clave, desde, hasta, etiqueta


def _catalogo(cur, solo_activas=False):
    """Lista de cuentas (nivel 1) con sus subcuentas, en orden de sección."""
    cur.execute("""SELECT id, nombre, seccion, padre_id, orden, activa, clave
                   FROM cuentas ORDER BY orden, id""")
    filas = cur.fetchall()
    cuentas, subs = [], {}
    for cid, nombre, seccion, padre, orden, activa, clave in filas:
        if solo_activas and not activa:
            continue
        item = {"id": cid, "nombre": nombre, "seccion": seccion, "activa": activa,
                "clave": clave, "auto": clave in (AUTO_WEB, AUTO_DIRECTA)}
        if padre is None:
            item["subcuentas"] = []
            cuentas.append(item)
        else:
            subs.setdefault(padre, []).append(item)
    for c in cuentas:
        c["subcuentas"] = subs.get(c["id"], [])
    orden_sec = {s[0]: i for i, s in enumerate(SECCIONES)}
    cuentas.sort(key=lambda c: orden_sec.get(c["seccion"], 99))
    return cuentas


# ─────────────────────────────────────────────
#  MENÚ DE CONTABILIDAD (3 opciones)
# ─────────────────────────────────────────────
@contabilidad_bp.route("/contabilidad")
def inicio():
    return render_template("contabilidad_menu.html")


# ─────────────────────────────────────────────
#  ESTADO DE RESULTADOS
# ─────────────────────────────────────────────
@contabilidad_bp.route("/contabilidad/estado")
def estado_resultados():
    clave, desde, hasta, etiqueta = _periodo(request.args)
    with obtener_conexion() as conn:
        cur = conn.cursor()
        catalogo = _catalogo(cur)

        # Movimientos anotados a mano, sumados por subcuenta
        cur.execute("""SELECT cuenta_id, COALESCE(SUM(monto), 0) FROM movimientos
                       WHERE fecha BETWEEN %s AND %s GROUP BY cuenta_id""", (desde, hasta))
        montos = {r[0]: float(r[1]) for r in cur.fetchall()}

        # Ventas automáticas: web (vienen de un pedido) o directas (registradas a mano)
        cur.execute("""
            SELECT EXISTS (SELECT 1 FROM pedidos p WHERE p.venta_id = v.id) AS es_web,
                   COALESCE(SUM(v.total), 0),
                   COALESCE(SUM(v.total) FILTER (WHERE v.metodo_pago = 'Por cobrar'), 0)
            FROM ventas v
            WHERE v.fecha_venta::date BETWEEN %s AND %s
              AND COALESCE(v.estado, '') NOT ILIKE 'anul%%'
            GROUP BY 1
        """, (desde, hasta))
        ventas_auto = {AUTO_WEB: 0.0, AUTO_DIRECTA: 0.0}
        web_por_cobrar = 0.0   # parte de las ventas web del periodo que aún no se cobra
        for es_web, total, sin_cobrar in cur.fetchall():
            ventas_auto[AUTO_WEB if es_web else AUTO_DIRECTA] += float(total)
            if es_web:
                web_por_cobrar += float(sin_cobrar)

        # Informativo: todo lo que está por cobrar (sin importar el periodo)
        cur.execute("""SELECT COALESCE(SUM(total), 0) FROM ventas
                       WHERE metodo_pago = 'Por cobrar' AND COALESCE(estado, '') NOT ILIKE 'anul%%'""")
        por_cobrar = float(cur.fetchone()[0])

    # Armar secciones → cuentas → subcuentas, ocultando lo que está en cero
    totales = {s[0]: 0.0 for s in SECCIONES}
    por_seccion = {s[0]: [] for s in SECCIONES}
    for c in catalogo:
        lineas = []
        for s in c["subcuentas"]:
            monto = ventas_auto.get(s["clave"], 0.0) if s["auto"] else montos.get(s["id"], 0.0)
            if monto:
                linea = {"id": s["id"], "nombre": s["nombre"], "monto": monto, "auto": s["auto"]}
                # Tienda web: desglose informativo de cobradas y por cobrar (no cambia los totales)
                if s["clave"] == AUTO_WEB:
                    linea["cobradas"] = monto - web_por_cobrar
                    linea["por_cobrar"] = web_por_cobrar
                lineas.append(linea)
        total_cuenta = sum(l["monto"] for l in lineas)
        if total_cuenta:
            por_seccion[c["seccion"]].append({"nombre": c["nombre"], "total": total_cuenta, "lineas": lineas})
            totales[c["seccion"]] += total_cuenta

    ventas = totales["venta"]
    utilidad_bruta = ventas - totales["costo"]
    utilidad_operacion = utilidad_bruta - totales["gasto_venta"] - totales["gasto_admin"]
    otros_neto = totales["otro_ingreso"] - totales["otro_gasto"]
    utilidad_neta = utilidad_operacion + otros_neto

    def pct(x):
        return (x / ventas * 100.0) if ventas else None

    return render_template(
        "contabilidad.html",
        periodo=clave, desde=desde.isoformat(), hasta=hasta.isoformat(), etiqueta=etiqueta,
        secciones=SECCIONES, por_seccion=por_seccion, totales=totales,
        ventas=ventas, utilidad_bruta=utilidad_bruta, utilidad_operacion=utilidad_operacion,
        otros_neto=otros_neto, utilidad_neta=utilidad_neta, por_cobrar=por_cobrar, pct=pct,
    )


# ─────────────────────────────────────────────
#  MOVIMIENTOS
# ─────────────────────────────────────────────
@contabilidad_bp.route("/contabilidad/movimientos")
def movimientos():
    clave, desde, hasta, etiqueta = _periodo(request.args)
    cuenta_filtro = request.args.get("cuenta", type=int)
    with obtener_conexion() as conn:
        cur = conn.cursor()
        catalogo = _catalogo(cur)
        sql = """SELECT m.id, m.fecha, m.monto, m.nota, s.nombre, c.nombre, s.seccion, s.id
                 FROM movimientos m
                 JOIN cuentas s ON s.id = m.cuenta_id
                 LEFT JOIN cuentas c ON c.id = s.padre_id
                 WHERE m.fecha BETWEEN %s AND %s"""
        params = [desde, hasta]
        if cuenta_filtro:
            sql += " AND (s.id = %s OR s.padre_id = %s)"
            params += [cuenta_filtro, cuenta_filtro]
        sql += " ORDER BY m.fecha DESC, m.id DESC"
        cur.execute(sql, params)
        filas = [{"id": r[0], "fecha": r[1], "monto": float(r[2]), "nota": r[3] or "",
                  "subcuenta": r[4], "cuenta": r[5] or "", "seccion": r[6],
                  "signo": SIGNO.get(r[6], -1)} for r in cur.fetchall()]
    total_ingresos = sum(f["monto"] for f in filas if f["signo"] > 0)
    total_egresos = sum(f["monto"] for f in filas if f["signo"] < 0)
    return render_template("contabilidad_movimientos.html", filas=filas, catalogo=catalogo,
                           nombre_seccion=NOMBRE_SECCION, periodo=clave, desde=desde.isoformat(),
                           hasta=hasta.isoformat(), etiqueta=etiqueta, cuenta_filtro=cuenta_filtro,
                           total_ingresos=total_ingresos, total_egresos=total_egresos,
                           editar=None, hoy=_hoy_cr().isoformat())


def _datos_formulario():
    """Lee y valida el formulario de movimiento. Devuelve (datos, error)."""
    fecha = _leer_fecha(request.form.get("fecha"))
    cuenta_id = request.form.get("cuenta_id", type=int)
    try:
        monto = float((request.form.get("monto") or "").replace("₡", "").replace(" ", ""))
    except ValueError:
        monto = 0
    nota = (request.form.get("nota") or "").strip()[:300]
    if not fecha:
        return None, "Poné una fecha válida."
    if not cuenta_id:
        return None, "Elegí una subcuenta."
    if monto <= 0:
        return None, "El monto debe ser mayor que cero."
    return {"fecha": fecha, "cuenta_id": cuenta_id, "monto": round(monto, 2), "nota": nota}, None


def _subcuenta_valida(cur, cuenta_id):
    """Solo subcuentas activas y que no sean de ventas automáticas."""
    cur.execute("""SELECT 1 FROM cuentas WHERE id = %s AND padre_id IS NOT NULL AND activa
                   AND COALESCE(clave, '') NOT IN (%s, %s)""", (cuenta_id, AUTO_WEB, AUTO_DIRECTA))
    return cur.fetchone() is not None


def _volver_movimientos():
    destino = request.form.get("volver") or ""
    if destino.startswith("/contabilidad"):
        return redirect(destino)
    return redirect(url_for("contabilidad_bp.movimientos"))


@contabilidad_bp.route("/contabilidad/movimientos/nuevo", methods=["POST"])
def movimiento_nuevo():
    datos, error = _datos_formulario()
    if error:
        flash(error, "danger")
        return _volver_movimientos()
    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _subcuenta_valida(cur, datos["cuenta_id"]):
            flash("Esa subcuenta no admite movimientos.", "danger")
            return _volver_movimientos()
        cur.execute("""INSERT INTO movimientos (fecha, cuenta_id, monto, nota)
                       VALUES (%s, %s, %s, %s)""",
                    (datos["fecha"], datos["cuenta_id"], datos["monto"], datos["nota"] or None))
        conn.commit()
    flash(f"Movimiento guardado: ₡{datos['monto']:,.0f}", "success")
    return _volver_movimientos()


@contabilidad_bp.route("/contabilidad/movimientos/<int:mid>/editar", methods=["GET", "POST"])
def movimiento_editar(mid):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        if request.method == "POST":
            datos, error = _datos_formulario()
            if error:
                flash(error, "danger")
                return redirect(url_for("contabilidad_bp.movimiento_editar", mid=mid))
            if not _subcuenta_valida(cur, datos["cuenta_id"]):
                flash("Esa subcuenta no admite movimientos.", "danger")
                return redirect(url_for("contabilidad_bp.movimiento_editar", mid=mid))
            cur.execute("""UPDATE movimientos SET fecha=%s, cuenta_id=%s, monto=%s, nota=%s
                           WHERE id=%s""",
                        (datos["fecha"], datos["cuenta_id"], datos["monto"], datos["nota"] or None, mid))
            conn.commit()
            flash("Movimiento actualizado.", "success")
            return _volver_movimientos()
        cur.execute("SELECT id, fecha, cuenta_id, monto, nota FROM movimientos WHERE id = %s", (mid,))
        fila = cur.fetchone()
        catalogo = _catalogo(cur)
    if not fila:
        flash("Ese movimiento ya no existe.", "danger")
        return redirect(url_for("contabilidad_bp.movimientos"))
    editar = {"id": fila[0], "fecha": fila[1].isoformat(), "cuenta_id": fila[2],
              "monto": float(fila[3]), "nota": fila[4] or ""}
    return render_template("contabilidad_movimientos.html", filas=[], catalogo=catalogo,
                           nombre_seccion=NOMBRE_SECCION, periodo="mes", desde="", hasta="",
                           etiqueta="", cuenta_filtro=None, total_ingresos=0, total_egresos=0,
                           editar=editar, hoy=_hoy_cr().isoformat())


@contabilidad_bp.route("/contabilidad/movimientos/<int:mid>/eliminar", methods=["POST"])
def movimiento_eliminar(mid):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM movimientos WHERE id = %s", (mid,))
        conn.commit()
    flash("Movimiento eliminado.", "success")
    return _volver_movimientos()


# ─────────────────────────────────────────────
#  CATÁLOGO DE CUENTAS (Entrega 1: agregar subcuentas y renombrar)
# ─────────────────────────────────────────────
@contabilidad_bp.route("/contabilidad/catalogo")
def catalogo():
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cuentas = _catalogo(cur)
    return render_template("contabilidad_catalogo.html", cuentas=cuentas,
                           secciones=SECCIONES, nombre_seccion=NOMBRE_SECCION)


@contabilidad_bp.route("/contabilidad/catalogo/<int:padre_id>/subcuenta", methods=["POST"])
def subcuenta_nueva(padre_id):
    nombre = " ".join((request.form.get("nombre") or "").split())[:80]
    if not nombre:
        flash("Escribí el nombre de la subcuenta.", "danger")
        return redirect(url_for("contabilidad_bp.catalogo"))
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT seccion FROM cuentas WHERE id = %s AND padre_id IS NULL", (padre_id,))
        fila = cur.fetchone()
        if not fila:
            flash("Esa cuenta no existe.", "danger")
            return redirect(url_for("contabilidad_bp.catalogo"))
        cur.execute("""SELECT 1 FROM cuentas WHERE padre_id = %s AND LOWER(nombre) = LOWER(%s)""",
                    (padre_id, nombre))
        if cur.fetchone():
            flash(f"Ya existe la subcuenta «{nombre}» en esa cuenta.", "danger")
            return redirect(url_for("contabilidad_bp.catalogo"))
        cur.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM cuentas WHERE padre_id = %s", (padre_id,))
        orden = cur.fetchone()[0]
        cur.execute("""INSERT INTO cuentas (nombre, seccion, padre_id, orden)
                       VALUES (%s, %s, %s, %s)""", (nombre, fila[0], padre_id, orden))
        conn.commit()
    flash(f"Subcuenta «{nombre}» agregada.", "success")
    return redirect(url_for("contabilidad_bp.catalogo"))


@contabilidad_bp.route("/contabilidad/catalogo/<int:cid>/renombrar", methods=["POST"])
def cuenta_renombrar(cid):
    nombre = " ".join((request.form.get("nombre") or "").split())[:80]
    if not nombre:
        flash("El nombre no puede quedar vacío.", "danger")
        return redirect(url_for("contabilidad_bp.catalogo"))
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE cuentas SET nombre = %s WHERE id = %s", (nombre, cid))
        conn.commit()
    flash("Nombre actualizado.", "success")
    return redirect(url_for("contabilidad_bp.catalogo"))
