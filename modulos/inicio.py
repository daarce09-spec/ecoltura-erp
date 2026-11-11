# -*- coding: utf-8 -*-
"""
Inicio del Admin (/menu): cómo va el negocio y qué hay que hacer ahora.

Inspirado en el inicio de Shopify, Uber Eats Manager y Square:
  · 4 cifras del periodo (hoy / semana / mes) comparadas con el periodo anterior
  · "Por hacer": pendientes con su cantidad, cada uno lleva a resolverlo
  · Pedidos recientes y el reparto del día

Todo es de SOLO LECTURA. Cada bloque va en su propio try: si una consulta falla,
el inicio igual carga (con ese bloque vacío) y nunca bloquea el Admin.
La base guarda la hora de Costa Rica, así que NOW() ya es hora local.
"""
from db.conexion import obtener_conexion

PERIODOS = {
    "hoy":    ("day",   "1 day",   "Hoy",         "Ayer"),
    "semana": ("week",  "7 days",  "Esta semana", "La semana pasada"),
    "mes":    ("month", "1 month", "Este mes",    "El mes pasado"),
}
ID_EXPRESS = 118
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def _seguro(cur, conn, fn, por_defecto):
    try:
        return fn(cur)
    except Exception as ex:
        print("Inicio: no se pudo leer un bloque:", ex)
        conn.rollback()
        return por_defecto


def _cifras(cur, periodo):
    trunc, intervalo, _, _ = PERIODOS[periodo]
    cur.execute(f"""
        WITH r AS (SELECT date_trunc('{trunc}', NOW()) AS ini)
        SELECT
          COALESCE(SUM(v.total) FILTER (WHERE v.fecha_venta >= r.ini), 0),
          COUNT(*)              FILTER (WHERE v.fecha_venta >= r.ini),
          COALESCE(SUM(v.total) FILTER (WHERE v.fecha_venta <  r.ini), 0),
          COUNT(*)              FILTER (WHERE v.fecha_venta <  r.ini)
        FROM ventas v, r
        WHERE COALESCE(v.estado, '') NOT ILIKE 'anul%%'
          AND v.fecha_venta >= r.ini - INTERVAL '{intervalo}'
    """)
    # "antes" = el periodo anterior COMPLETO (la semana pasada entera, ayer entero…).
    # Antes se comparaba solo hasta la misma hora, y como las entregas son los viernes
    # el porcentaje salía inflado (ej. +1271 % un miércoles).
    ventas, n_ventas, antes, n_antes = cur.fetchone()
    cur.execute(f"""
        SELECT COUNT(*) FROM pedidos
        WHERE creado >= date_trunc('{trunc}', NOW()) AND estado <> 'Rechazado'
    """)
    n_pedidos = cur.fetchone()[0]
    cur.execute("""
        SELECT COALESCE(SUM(total), 0),
               COUNT(*) FILTER (WHERE COALESCE(sinpe_pendiente, FALSE))
        FROM ventas
        WHERE (metodo_pago = 'Por cobrar' OR COALESCE(sinpe_pendiente, FALSE))
          AND COALESCE(estado, '') NOT ILIKE 'anul%%'
    """)
    por_cobrar, n_sinpe = cur.fetchone()
    ventas, antes = float(ventas), float(antes)
    cambio = None if antes <= 0 else round((ventas - antes) / antes * 100)
    return {
        "ventas": ventas, "cambio": cambio, "antes": antes, "n_antes": n_antes,
        "pedidos": n_pedidos, "n_ventas": n_ventas,
        "ticket": (ventas / n_ventas) if n_ventas else 0.0,
        "por_cobrar": float(por_cobrar), "n_sinpe": n_sinpe,
    }


def _por_hacer(cur):
    cur.execute("""
        SELECT
          COUNT(*) FILTER (WHERE COALESCE(seguimiento, 'Preparando') NOT IN ('Alistado', 'Listo')),
          COUNT(*) FILTER (WHERE COALESCE(seguimiento, 'Preparando') NOT IN ('Alistado', 'Listo')
                             AND creado >= NOW() - INTERVAL '24 hours'),
          MAX(creado) FILTER (WHERE COALESCE(seguimiento, 'Preparando') NOT IN ('Alistado', 'Listo')),
          COUNT(*) FILTER (WHERE seguimiento IN ('Alistado', 'Listo') AND entrega_fallida IS NULL),
          COUNT(*) FILTER (WHERE entrega_fallida IS NOT NULL),
          COUNT(*) FILTER (WHERE COALESCE(modificado, FALSE))
        FROM pedidos WHERE estado = 'Pendiente'
    """)
    por_alistar, nuevos, ultimo, alistados, fallidos, modificados = cur.fetchone()
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE COALESCE(sinpe_pendiente, FALSE)),
               COALESCE(SUM(total) FILTER (WHERE COALESCE(sinpe_pendiente, FALSE)), 0),
               COUNT(*) FILTER (WHERE metodo_pago = 'Por cobrar' AND NOT COALESCE(sinpe_pendiente, FALSE)),
               COALESCE(SUM(total) FILTER (WHERE metodo_pago = 'Por cobrar' AND NOT COALESCE(sinpe_pendiente, FALSE)), 0)
        FROM ventas WHERE COALESCE(estado, '') NOT ILIKE 'anul%%'
    """)
    n_sinpe, m_sinpe, n_cobrar, m_cobrar = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM visitas_reservas WHERE estado = 'Pendiente'")
    visitas = cur.fetchone()[0]
    cur.execute("SELECT LOCALTIMESTAMP")
    ahora = cur.fetchone()[0]

    def hace(t):
        if not t:
            return ""
        minutos = int((ahora - t).total_seconds() // 60)
        if minutos < 60:
            return f"El último entró hace {max(minutos, 1)} min"
        if minutos < 60 * 24:
            return f"El último entró hace {minutos // 60} h"
        return f"El último entró el {t.strftime('%d/%m')}"

    def colones(x):
        return "₡" + f"{float(x):,.0f}".replace(",", ".")

    tareas = [
        {"n": por_alistar, "titulo": "Pedidos por alistar", "detalle": hace(ultimo),
         "boton": "Alistar", "href": "/admin/pedidos", "urgente": nuevos > 0},
        {"n": alistados, "titulo": "Pedidos listos para repartir", "detalle": "Ya están alistados",
         "boton": "Repartir", "href": "/reparto", "urgente": False},
        {"n": fallidos, "titulo": "Entregas que no se pudieron hacer", "detalle": "Volver a coordinar con el cliente",
         "boton": "Ver", "href": "/admin/pedidos", "urgente": True},
        {"n": n_sinpe, "titulo": "SINPE por confirmar", "detalle": f"{colones(m_sinpe)} marcados por el repartidor",
         "boton": "Confirmar", "href": "/admin/pedidos", "urgente": False},
        {"n": n_cobrar, "titulo": "Ventas por cobrar", "detalle": f"{colones(m_cobrar)} pendientes de pago",
         "boton": "Cobrar", "href": "/admin/pedidos", "urgente": False},
        {"n": modificados, "titulo": "Pedidos que el cliente cambió", "detalle": "Revisá los cambios antes de alistar",
         "boton": "Revisar", "href": "/admin/pedidos", "urgente": False},
        {"n": visitas, "titulo": "Visitas a la finca por confirmar", "detalle": "Reservas pendientes",
         "boton": "Confirmar", "href": "/admin/visitas", "urgente": False},
    ]
    return [t for t in tareas if t["n"]]


def _recientes(cur):
    cur.execute("""
        SELECT p.id, COALESCE(c.nombre, p.nombre_contacto), p.creado, p.estado,
               COALESCE(p.seguimiento, 'Preparando'), p.notas, p.entrega_fallida,
               COALESCE(v.total, p.total_estimado),
               (SELECT COUNT(*) FROM pedidos_detalle d WHERE d.pedido_id = p.id AND d.producto_id <> %s),
               (p.creado >= NOW() - INTERVAL '24 hours')
        FROM pedidos p
        LEFT JOIN clientes c ON c.id = p.cliente_id
        LEFT JOIN ventas v   ON v.id = p.venta_id
        ORDER BY p.creado DESC
        LIMIT 6
    """, (ID_EXPRESS,))
    filas = []
    for pid, nombre, creado, estado, seg, notas, fallida, total, n, reciente in cur.fetchall():
        if estado == "Rechazado":
            etiqueta, clase = "Cancelado", "gris"
        elif estado == "Resuelto":
            etiqueta, clase = "Entregado", "gris"
        elif fallida:
            etiqueta, clase = "No entregado", "nuevo"
        elif seg in ("Alistado", "Listo"):
            etiqueta, clase = "Alistado", "al"
        elif reciente:
            etiqueta, clase = "Nuevo", "nuevo"
        else:
            etiqueta, clase = "Preparando", ""
        zona = (notas or "").strip()
        if zona.startswith("📍"):
            zona = zona[1:].strip().split(" — ", 1)[0]
        else:
            zona = ""
        filas.append({
            "id": pid, "nombre": nombre or "", "hora": creado, "total": float(total or 0),
            "n": n, "etiqueta": etiqueta, "clase": clase, "zona": zona[:40],
        })
    return filas


def _reparto(cur):
    from modulos.reparto import armar_ruta
    ruta, km = armar_ruta(cur)
    cur.execute("""
        SELECT COUNT(*) FROM pedidos
        WHERE COALESCE(entregado_reparto, FALSE) AND gestionado >= date_trunc('day', NOW())
    """)
    hechas = cur.fetchone()[0]
    return {"ruta": ruta[:4], "pendientes": len(ruta), "hechas": hechas,
            "total": hechas + len(ruta), "km": km}


def datos_inicio(periodo):
    if periodo not in PERIODOS:
        periodo = "semana"
    datos = {"periodo": periodo, "periodo_txt": PERIODOS[periodo][2], "comparado": PERIODOS[periodo][3],
             "cifras": None, "tareas": [], "recientes": [], "reparto": None, "hora": 12, "fecha": ""}
    with obtener_conexion() as conn:
        cur = conn.cursor()
        ahora = _seguro(cur, conn, lambda c: (c.execute("SELECT LOCALTIMESTAMP"), c.fetchone()[0])[1], None)
        if ahora:
            datos["hora"] = ahora.hour
            datos["fecha"] = f"{DIAS[ahora.weekday()]} {ahora.day} de {MESES[ahora.month - 1]}"
        datos["cifras"] = _seguro(cur, conn, lambda c: _cifras(c, periodo), None)
        datos["tareas"] = _seguro(cur, conn, _por_hacer, [])
        datos["recientes"] = _seguro(cur, conn, _recientes, [])
        datos["reparto"] = _seguro(cur, conn, _reparto, None)
    h = datos["hora"]
    datos["saludo"] = "Buenos días" if h < 12 else ("Buenas tardes" if h < 19 else "Buenas noches")
    return datos
