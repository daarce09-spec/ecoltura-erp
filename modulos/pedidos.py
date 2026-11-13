# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash, Response
from db.conexion import obtener_conexion
from datetime import datetime, timedelta
from urllib.parse import quote
import os
from modulos.proveedores import asegurar_tablas as _asegurar_tablas_proveedores

pedidos_bp = Blueprint("pedidos_bp", __name__)


# Descuento por producto: cada línea del pedido puede llevar su propio descuento
# (monto en colones o porcentaje), además del descuento general del pedido.
def _asegurar_columnas_descuento_linea():
    try:
        with obtener_conexion() as conn:
            cur = conn.cursor()
            cur.execute("""ALTER TABLE pedidos_detalle
                           ADD COLUMN IF NOT EXISTS descuento_tipo VARCHAR(12) DEFAULT 'monto',
                           ADD COLUMN IF NOT EXISTS descuento_valor NUMERIC DEFAULT 0""")
            conn.commit()
    except Exception as ex:
        print("No se pudieron asegurar las columnas de descuento por línea:", ex)

_asegurar_columnas_descuento_linea()


# Seguimiento del pedido (Preparando / Listo), enlace privado del cliente y marca de pago.
# El estado de siempre (Pendiente / Resuelto / Rechazado) no se toca.
def _asegurar_columnas_seguimiento():
    try:
        with obtener_conexion() as conn:
            cur = conn.cursor()
            cur.execute("""ALTER TABLE pedidos
                           ADD COLUMN IF NOT EXISTS seguimiento VARCHAR(20) DEFAULT 'Preparando',
                           ADD COLUMN IF NOT EXISTS token VARCHAR(64),
                           ADD COLUMN IF NOT EXISTS modificado BOOLEAN DEFAULT FALSE,
                           ADD COLUMN IF NOT EXISTS modificado_en TIMESTAMP,
                           ADD COLUMN IF NOT EXISTS pagado_en TIMESTAMP,
                           ADD COLUMN IF NOT EXISTS lat NUMERIC(9,6),
                           ADD COLUMN IF NOT EXISTS lng NUMERIC(9,6),
                           ADD COLUMN IF NOT EXISTS entrega_fallida TEXT,
                           ADD COLUMN IF NOT EXISTS entrega_fallida_en TIMESTAMP,
                           ADD COLUMN IF NOT EXISTS entregado_reparto BOOLEAN DEFAULT FALSE,
                           ADD COLUMN IF NOT EXISTS entrega VARCHAR(12) DEFAULT 'domicilio'""")
            # SINPE marcado por el repartidor: queda "por confirmar" hasta que la oficina vea el dinero
            cur.execute("ALTER TABLE ventas ADD COLUMN IF NOT EXISTS sinpe_pendiente BOOLEAN DEFAULT FALSE")
            # El paso "Listo" ahora se llama "Alistado"
            cur.execute("UPDATE pedidos SET seguimiento = 'Alistado' WHERE seguimiento = 'Listo'")
            try:
                cur.execute("SAVEPOINT tk")
                cur.execute("ALTER TABLE pedidos ALTER COLUMN token SET DEFAULT gen_random_uuid()::text")
                cur.execute("RELEASE SAVEPOINT tk")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT tk")
                cur.execute("""ALTER TABLE pedidos ALTER COLUMN token
                               SET DEFAULT md5(random()::text || clock_timestamp()::text)""")
            cur.execute("UPDATE pedidos SET token = DEFAULT WHERE token IS NULL")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_pedidos_token ON pedidos (token)")
            # Índices para que Pedidos, Inicio y Reparto carguen rápido
            cur.execute("CREATE INDEX IF NOT EXISTS ix_pedidos_detalle_pedido ON pedidos_detalle (pedido_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_pedidos_estado ON pedidos (estado)")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_pedidos_creado ON pedidos (creado)")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_ventas_fecha ON ventas (fecha_venta)")
            conn.commit()
    except Exception as ex:
        print("No se pudieron asegurar las columnas de seguimiento:", ex)

_asegurar_columnas_seguimiento()

# ─────────────────────────────────────────────
#  Aviso por WhatsApp al marcar "Alistado"
#  Abre WhatsApp con el mensaje ya escrito (la persona solo toca Enviar).
#  Datos de SINPE en Railway: SINPE_NUMERO y SINPE_NOMBRE (si no están, el
#  mensaje sale sin la parte de SINPE).
# ─────────────────────────────────────────────
SITIO_TIENDA = "https://www.ecoltura.com"
SINPE_NUMERO = "8993 5973"      # número de SINPE Móvil de Ecoltura


def _link_tienda():
    if os.environ.get("ENTORNO", "").lower() == "pruebas" and os.environ.get("TIENDA_URL"):
        return os.environ["TIENDA_URL"].rstrip("/")
    return SITIO_TIENDA


def _colones_txt(x):
    return "₡" + f"{float(x or 0):,.0f}".replace(",", ".")


def mensaje_alistado(nombre, total, token, entrega="domicilio", falta_ubicacion=False):
    primer = (nombre or "").strip().split(" ")[0].capitalize()
    if entrega == "feria":
        aviso = "Tu pedido de *Ecoltura* ya está alistado y te espera en la *Feria San Ramón*. 🧺"
    else:
        aviso = "Tu pedido de *Ecoltura* ya está alistado y pronto va camino a tu casa. 🚚"
    lineas = [
        f"¡Hola{(' ' + primer) if primer else ''}! 🌱",
        aviso,
        "",
        f"*Total a pagar: {_colones_txt(total)}*",
    ]
    if falta_ubicacion and entrega != "feria":
        lineas += [
            "",
            "📍 *Para entregártelo necesitamos tu ubicación.* Compartila por aquí: tocá el clip 📎 → *Ubicación* → *Enviar mi ubicación actual* (o buscá tu casa en el mapa).",
        ]
    numero = (os.environ.get("SINPE_NUMERO") or SINPE_NUMERO).strip()
    if numero:
        a_nombre = (os.environ.get("SINPE_NOMBRE") or "").strip()
        lineas += [
            "",
            "Si querés, podés adelantar el pago por *SINPE Móvil*:",
            f"📱 {numero}" + (f" ({a_nombre})" if a_nombre else ""),
        ]
        nombre_completo = " ".join((nombre or "").split())
        if nombre_completo:
            lineas.append(f"En el detalle del SINPE poné tu nombre: *{nombre_completo}*")
        lineas += [
            "Cuando lo hagás, enviános el comprobante por aquí. 🙏",
        ]
    if token:
        lineas += ["", "Mirá el detalle de tu pedido aquí:", f"{_link_tienda()}/pedido/{token}"]
    lineas += ["", "¡Gracias por apoyar la agricultura regenerativa! 💚"]
    return "\n".join(lineas)


def link_whatsapp_alistado(celular, nombre, total, token, entrega="domicilio", falta_ubicacion=False):
    cel8 = _ultimos_8(celular)
    if len(cel8) != 8:
        return ""
    return f"https://wa.me/506{cel8}?text=" + quote(mensaje_alistado(nombre, total, token, entrega, falta_ubicacion))


# Métodos de pago de las ventas que vienen de pedidos
PAGO_RECIBIDO = "Efectivo"
METODO_SINPE  = "SINPE Móvil"
METODOS_PAGO  = {"efectivo": PAGO_RECIBIDO, "si": PAGO_RECIBIDO, "sinpe": METODO_SINPE, "no": "Por cobrar"}


def _neto_linea(cantidad, precio, tipo, valor):
    """(base, descuento_de_la_linea, neto). Todo en colones enteros."""
    base = float(round(float(cantidad) * float(precio)))
    desc = _calcular_descuento(base, tipo or 'monto', float(valor or 0))
    return base, desc, float(round(base - desc))


# ─────────────────────────────────────────────
#  BANDEJA PRINCIPAL (tablero Kanban)
#    Pendientes -> (pasar a venta) -> Entregados (por cobrar / cobrado)
# ─────────────────────────────────────────────
def _ultimos_8(celular):
    digitos = "".join(ch for ch in str(celular or "") if ch.isdigit())
    return digitos[-8:]


def _inicio_periodo(periodo):
    """Inicio del periodo en hora UTC (como se guarda en la base), o None para 'todo'."""
    ahora_cr = datetime.utcnow() - timedelta(hours=6)
    hoy = ahora_cr.replace(hour=0, minute=0, second=0, microsecond=0)
    if periodo == "hoy":
        inicio = hoy
    elif periodo == "mes":
        inicio = hoy.replace(day=1)
    elif periodo == "todo":
        return None
    else:  # semana: hoy y los 6 días anteriores
        inicio = hoy - timedelta(days=6)
    return inicio + timedelta(hours=6)


@pedidos_bp.route("/admin/pedidos")
def bandeja_pedidos():
    periodo = request.args.get("periodo", "todo")
    if periodo not in ("hoy", "semana", "mes", "todo"):
        periodo = "todo"
    inicio = _inicio_periodo(periodo)

    filtro_fecha = ""
    params = []
    if inicio is not None:
        filtro_fecha = "p.gestionado >= %s"
        params = [inicio, inicio]

    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT p.id, COALESCE(c.nombre, p.nombre_contacto), p.celular,
                   p.total_estimado, p.estado, COALESCE(p.seguimiento, 'Preparando'),
                   p.creado, p.gestionado, COALESCE(p.modificado, FALSE),
                   v.metodo_pago, v.total, v.id, p.notas,
                   COALESCE(nd.n, 0),
                   p.entrega_fallida, COALESCE(v.sinpe_pendiente, FALSE), p.token,
                   COALESCE(p.entrega, 'domicilio'), (p.lat IS NULL)
            FROM pedidos p
            LEFT JOIN clientes c ON c.id = p.cliente_id
            LEFT JOIN ventas v   ON v.id = p.venta_id
            LEFT JOIN (SELECT pedido_id, COUNT(*) AS n FROM pedidos_detalle GROUP BY pedido_id) nd
                   ON nd.pedido_id = p.id
            WHERE p.estado = 'Pendiente'
               OR (p.estado = 'Resuelto' AND (v.metodo_pago = 'Por cobrar' OR v.sinpe_pendiente
                                              {"OR " + filtro_fecha if filtro_fecha else "OR TRUE"}))
               OR (p.estado = 'Rechazado' {"AND " + filtro_fecha if filtro_fecha else ""})
            ORDER BY p.creado DESC
        """, params)
        filas = cur.fetchall()

    # Viernes desde la 1:00 p. m.: lo que entra desde esa hora es para el viernes siguiente
    ahora_cr = datetime.utcnow() - timedelta(hours=6)
    corte_hoy = (ahora_cr.replace(hour=13, minute=0, second=0, microsecond=0)
                 if ahora_cr.weekday() == 4 and ahora_cr.hour >= 13 else None)

    columnas = {"pendientes": [], "entregados": []}
    rechazados = []
    por_entregar = facturado = cobrado = por_cobrar = 0.0
    for (pid, nombre, celular, total_est, estado, seg, creado, gestionado, modificado,
         metodo, total_venta, venta_id, notas, n_items, fallida, sinpe_pend, token, entrega, sin_punto) in filas:
        total = float(total_venta if total_venta is not None else (total_est or 0))
        tarjeta = {
            "id": pid, "nombre": nombre or "", "celular": celular or "", "cel8": _ultimos_8(celular),
            "total": total, "creado": creado, "gestionado": gestionado, "modificado": modificado,
            "metodo": metodo, "venta_id": venta_id, "notas": (notas or "").strip(), "n_items": n_items,
            "seguimiento": "Alistado" if seg in ("Alistado", "Listo") else "Preparando",
            "fallida": (fallida or "").strip(), "sinpe_pendiente": bool(sinpe_pend),
            "feria": entrega == "feria",
            "proximo_viernes": bool(corte_hoy and estado == "Pendiente" and creado and creado >= corte_hoy),
            "falta_ubicacion": estado == "Pendiente" and entrega != "feria" and bool(sin_punto),
            "wa_alistado": link_whatsapp_alistado(celular, nombre, total, token, entrega,
                                                  entrega != "feria" and bool(sin_punto)) if estado == "Pendiente" else "",
        }
        if estado == "Pendiente":
            por_entregar += float(total_est or 0)
            columnas["pendientes"].append(tarjeta)
        elif estado == "Resuelto":
            es_cobrar = (metodo == "Por cobrar") or bool(sinpe_pend)   # SINPE sin confirmar todavía no es plata en mano
            tarjeta["por_cobrar"] = es_cobrar
            columnas["entregados"].append(tarjeta)
            if es_cobrar:
                por_cobrar += total
            if inicio is None or (gestionado and gestionado >= inicio):
                facturado += total
                if not es_cobrar:
                    cobrado += total
        else:
            rechazados.append(tarjeta)

    # Entregados: primero los que faltan por cobrar, después los cobrados (lo más reciente arriba)
    columnas["entregados"].sort(key=lambda t: (not t["por_cobrar"],
                                               -(t["gestionado"].timestamp() if t["gestionado"] else 0)))
    n_cobrar = sum(1 for t in columnas["entregados"] if t["por_cobrar"])
    n_cobrados = len(columnas["entregados"]) - n_cobrar
    # Con "Todo" se juntan cientos de tarjetas ya cobradas y la pantalla se pone lenta:
    # se muestran todas las por cobrar y solo las 40 cobradas más recientes.
    n_entregados = len(columnas["entregados"])
    MAX_COBRADOS = 40
    if n_cobrados > MAX_COBRADOS:
        columnas["entregados"] = columnas["entregados"][:n_cobrar + MAX_COBRADOS]
    ocultos = n_entregados - len(columnas["entregados"])

    resumen = {"por_entregar": por_entregar, "facturado": facturado,
               "cobrado": cobrado, "por_cobrar": por_cobrar}
    return render_template("pedidos_bandeja.html", columnas=columnas, rechazados=rechazados,
                           resumen=resumen, periodo=periodo,
                           n_cobrar=n_cobrar, n_cobrados=n_cobrados,
                           n_entregados=n_entregados, ocultos=ocultos,
                           pendientes=len(columnas["pendientes"]))


# ─────────────────────────────────────────────
#  SEGUIMIENTO: Preparando <-> Alistado (el repartidor ve los Alistados)
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/seguimiento", methods=["POST"])
def pedido_seguimiento(id):
    valor = request.form.get("valor", "")
    if valor not in ("Preparando", "Alistado"):
        flash("Estado inválido.", "danger")
        return redirect(url_for("pedidos_bp.bandeja_pedidos"))
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE pedidos SET seguimiento = %s WHERE id = %s AND estado = 'Pendiente'",
                    (valor, id))
        conn.commit()
    flash(f"Pedido #{id}: {valor.lower()}.", "success")
    if request.form.get("volver") == "detalle":
        return redirect(url_for("pedidos_bp.pedido_detalle", id=id))
    return redirect(url_for("pedidos_bp.bandeja_pedidos"))


# ─────────────────────────────────────────────
#  MARCAR PAGADO (venta que estaba por cobrar)
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/pagar", methods=["POST"])
def pedido_pagar(id):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT venta_id FROM pedidos WHERE id = %s AND estado = 'Resuelto'", (id,))
        fila = cur.fetchone()
        if not fila or not fila[0]:
            flash("Este pedido no tiene una venta por cobrar.", "warning")
        else:
            metodo = METODO_SINPE if request.form.get("metodo") == "sinpe" else PAGO_RECIBIDO
            cur.execute("UPDATE ventas SET metodo_pago = %s WHERE id = %s AND metodo_pago = 'Por cobrar'",
                        (metodo, fila[0]))
            cur.execute("UPDATE pedidos SET pagado_en = NOW() WHERE id = %s", (id,))
            conn.commit()
            flash(f"Pedido #{id} marcado como cobrado.", "success")
    if request.form.get("volver") == "detalle":
        return redirect(url_for("pedidos_bp.pedido_detalle", id=id))
    return redirect(url_for("pedidos_bp.bandeja_pedidos"))


def _formato_cantidad(unidad, total):
    """Texto legible: gramos -> g / kg; el resto en unidades."""
    total = float(total)
    if (unidad or "").lower() in ("gramo", "gramos"):
        if total >= 1000:
            return f"{total / 1000:.2f}".rstrip("0").rstrip(".") + " kg"
        return f"{total:.0f} g"
    cant = int(total) if total == int(total) else round(total, 2)
    u = (unidad or "unid.").lower()
    if u == "unidad" and cant != 1:
        u = "unidades"
    return f"{cant} {u}"


def _clase_caja():
    """Casilla vacía (cuadrito) para marcar a mano en el PDF."""
    from reportlab.platypus.flowables import Flowable
    from reportlab.lib.units import mm

    class Caja(Flowable):
        def wrap(self, w, h):
            return 6*mm, 6*mm

        def draw(self):
            self.canv.setLineWidth(1.2)
            self.canv.rect(0, 0, 5*mm, 5*mm)
    return Caja


def _texto_pdf(txt):
    """Quita emojis y símbolos que la fuente estándar del PDF no puede dibujar."""
    salida = []
    for ch in (txt or ""):
        try:
            ch.encode("cp1252")
            salida.append(ch)
        except UnicodeEncodeError:
            pass
    return " ".join("".join(salida).split(" ")).strip()


def _miles(n):
    return f"{float(n):,.0f}".replace(",", " ")


# ─────────────────────────────────────────────
#  LISTA DE PREPARACIÓN (pedidos pendientes, para imprimir / PDF)
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/lista-preparacion")
def lista_preparacion():
    with obtener_conexion() as conn:
        cur = conn.cursor()
        items, n_pendientes = _datos_lista(cur)

    ahora = datetime.utcnow() - timedelta(hours=6)  # hora de Costa Rica
    pdf = _generar_pdf_lista(items, n_pendientes, ahora.strftime("%d/%m/%Y %I:%M %p"))
    nombre_archivo = f"lista_preparacion_{ahora.strftime('%Y-%m-%d')}.pdf"
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'})


def _elementos_lista(items, n_pendientes, fecha):
    from xml.sax.saxutils import escape
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, Table, TableStyle, Spacer

    est = getSampleStyleSheet()
    plural = "" if n_pendientes == 1 else "s"
    elementos = [
        Paragraph("Lista de preparación — Ecoltura", est["Title"]),
        Paragraph(f"{n_pendientes} pedido{plural} pendiente{plural} · {fecha}", est["Normal"]),
        Spacer(1, 6*mm),
    ]
    if not items:
        elementos.append(Paragraph("No hay pedidos pendientes.", est["Normal"]))
    else:
        Caja = _clase_caja()

        datos = [["", "Producto", "Cantidad total", "En pedidos"]]
        for nombre, cant, n in items:
            datos.append([Caja(), Paragraph(escape(nombre), est["Normal"]), cant,
                          f"{n} pedido{'' if n == 1 else 's'}"])
        t = Table(datos, colWidths=[12*mm, 85*mm, 45*mm, 38*mm], repeatRows=1)
        estilo = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3E6B22")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (2, 1), (2, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 11),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 1), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#999999")),
        ]
        t.setStyle(TableStyle(estilo))
        elementos.append(t)
    return elementos


def _nuevo_documento(titulo):
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm, title=titulo)
    return doc, buf


def _generar_pdf_lista(items, n_pendientes, fecha):
    doc, buf = _nuevo_documento("Lista de preparación - Ecoltura")
    doc.build(_elementos_lista(items, n_pendientes, fecha))
    return buf.getvalue()


# ─────────────────────────────────────────────
#  HOJAS POR PEDIDO (una página por pedido pendiente, en un solo PDF)
# ─────────────────────────────────────────────
def _datos_lista(cur):
    cur.execute("""
        SELECT pr.nombre, pr.unidad,
               SUM(pd.cantidad) AS total,
               COUNT(DISTINCT pd.pedido_id) AS n_pedidos
        FROM pedidos_detalle pd
        JOIN pedidos p   ON p.id  = pd.pedido_id
        JOIN productos pr ON pr.id = pd.producto_id
        WHERE p.estado = 'Pendiente'
        GROUP BY pr.id, pr.nombre, pr.unidad
        ORDER BY pr.nombre
    """)
    filas = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM pedidos WHERE estado='Pendiente'")
    n_pendientes = cur.fetchone()[0]
    items = [(nombre, _formato_cantidad(unidad, total), n_pedidos)
             for nombre, unidad, total, n_pedidos in filas]
    return items, n_pendientes


def _datos_hojas(cur):
    cur.execute("""
        SELECT p.id, COALESCE(c.nombre, p.nombre_contacto), p.celular, p.cedula,
               p.notas, p.creado
        FROM pedidos p
        LEFT JOIN clientes c ON c.id = p.cliente_id
        WHERE p.estado = 'Pendiente'
        ORDER BY p.creado, p.id
    """)
    cabeceras = cur.fetchall()

    pedidos = []
    for pid, nombre, celular, cedula, notas, creado in cabeceras:
        cur.execute("""
            SELECT pr.nombre, pr.unidad, pd.cantidad, pd.precio_unitario,
                   COALESCE(pd.descuento_tipo, 'monto'), COALESCE(pd.descuento_valor, 0)
            FROM pedidos_detalle pd
            JOIN productos pr ON pr.id = pd.producto_id
            WHERE pd.pedido_id = %s
            ORDER BY pr.nombre
        """, (pid,))
        lineas = []
        for n, u, c, pu, dt, dv in cur.fetchall():
            _, d_l, neto = _neto_linea(c, pu, dt, dv)
            lineas.append((n, _formato_cantidad(u, c), neto, d_l))
        subtotal, desc, total, _, _ = _totales(cur, pid)
        _, desc_lineas = _subtotal_y_desc_lineas(cur, pid)
        pedidos.append({
            "id": pid, "cliente": nombre or "", "celular": celular or "",
            "cedula": cedula or "", "notas": (notas or "").strip(),
            "fecha": creado.strftime("%d/%m/%Y %I:%M %p") if creado else "",
            "lineas": lineas, "subtotal": subtotal, "descuento": desc, "desc_lineas": desc_lineas, "total": total,
        })
    return pedidos


# ─────────────────────────────────────────────
#  HOJAS POR PROVEEDOR (qué comprarle a cada proveedor oficial)
# ─────────────────────────────────────────────
def _datos_proveedores(cur):
    """
    Agrupa lo pendiente por proveedor OFICIAL de cada producto.
    Devuelve (lista_de_proveedores, sin_proveedor):
      proveedor = {"nombre", "telefono", "items": [(producto, cantidad_texto, "#12, #15")]}
    "Express" (el envío) no se compra, así que no aparece.
    """
    try:
        cur.execute("""
            SELECT pv.id, pv.nombre, COALESCE(pv.telefono, ''),
                   pr.nombre, pr.unidad, SUM(pd.cantidad),
                   STRING_AGG(DISTINCT pd.pedido_id::text, ',')
            FROM pedidos_detalle pd
            JOIN pedidos p    ON p.id  = pd.pedido_id
            JOIN productos pr ON pr.id = pd.producto_id
            LEFT JOIN producto_proveedores pp ON pp.producto_id = pr.id AND pp.oficial
            LEFT JOIN proveedores pv          ON pv.id = pp.proveedor_id
            WHERE p.estado = 'Pendiente' AND LOWER(TRIM(pr.nombre)) <> 'express'
            GROUP BY pv.id, pv.nombre, pv.telefono, pr.id, pr.nombre, pr.unidad
            ORDER BY LOWER(pv.nombre) NULLS LAST, pr.nombre
        """)
        filas = cur.fetchall()
    except Exception as ex:
        print("No se pudieron leer los proveedores para el PDF:", ex)
        cur.connection.rollback()
        return [], []

    proveedores, sin_proveedor, por_id = [], [], {}
    for pv_id, pv_nombre, pv_tel, prod, unidad, total, pedidos_ids in filas:
        ids = sorted({int(x) for x in (pedidos_ids or "").split(",") if x}) 
        item = (prod, _formato_cantidad(unidad, total), ", ".join(f"#{i}" for i in ids))
        if pv_id is None:
            sin_proveedor.append(item)
            continue
        if pv_id not in por_id:
            por_id[pv_id] = {"nombre": pv_nombre, "telefono": pv_tel, "items": []}
            proveedores.append(por_id[pv_id])
        por_id[pv_id]["items"].append(item)
    return proveedores, sin_proveedor


def _elementos_proveedores(proveedores, sin_proveedor, fecha):
    """Una hoja por proveedor (y una final con lo que no tiene proveedor oficial)."""
    from xml.sax.saxutils import escape
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, Table, TableStyle, Spacer, PageBreak

    Caja = _clase_caja()
    est = getSampleStyleSheet()
    elementos = []

    hojas = [(p["nombre"], p["telefono"], p["items"], False) for p in proveedores]
    if sin_proveedor:
        hojas.append(("Sin proveedor oficial", "", sin_proveedor, True))

    for i, (nombre, telefono, items, es_sin) in enumerate(hojas):
        if i > 0:
            elementos.append(PageBreak())
        titulo = "Sin proveedor oficial" if es_sin else f"Compra a {escape(_texto_pdf(nombre))}"
        elementos.append(Paragraph(titulo, est["Title"]))
        partes = []
        if telefono:
            partes.append(f"Tel. {escape(_texto_pdf(telefono))}")
        partes.append(f"{len(items)} producto{'' if len(items) == 1 else 's'}")
        partes.append(fecha)
        elementos.append(Paragraph(" · ".join(partes), est["Normal"]))
        if es_sin:
            elementos.append(Spacer(1, 2*mm))
            elementos.append(Paragraph("<font size=9 color='#b45309'>Estos productos pendientes no tienen "
                                       "proveedor oficial asignado (Inventario → Proveedores).</font>",
                                       est["Normal"]))
        elementos.append(Spacer(1, 6*mm))

        filas = [["", "Producto", "Cantidad a comprar", "Pedidos"]]
        for prod, cant, ped in items:
            filas.append([Caja(), Paragraph(escape(_texto_pdf(prod)), est["Normal"]), cant,
                          Paragraph(f"<font size=9 color='#555555'>{escape(ped)}</font>", est["Normal"])])
        t = Table(filas, colWidths=[12*mm, 78*mm, 42*mm, 48*mm], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3E6B22")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (2, 1), (2, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 11),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 1), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#999999")),
        ]))
        elementos.append(t)
    return elementos


@pedidos_bp.route("/admin/pedidos/hojas-pedidos")
def hojas_pedidos():
    with obtener_conexion() as conn:
        cur = conn.cursor()
        pedidos = _datos_hojas(cur)

    ahora = datetime.utcnow() - timedelta(hours=6)  # hora de Costa Rica
    pdf = _generar_pdf_hojas(pedidos, ahora.strftime("%d/%m/%Y %I:%M %p"))
    nombre_archivo = f"hojas_pedidos_{ahora.strftime('%Y-%m-%d')}.pdf"
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'})


# ─────────────────────────────────────────────
#  EXPORTAR TODO: lista de preparación + hojas por proveedor + una hoja por pedido, en un solo PDF
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/exportar-pdf")
def exportar_pdf():
    from reportlab.platypus import PageBreak
    with obtener_conexion() as conn:
        cur = conn.cursor()
        items, n_pendientes = _datos_lista(cur)
        pedidos = _datos_hojas(cur)
        proveedores, sin_proveedor = _datos_proveedores(cur)

    ahora = datetime.utcnow() - timedelta(hours=6)  # hora de Costa Rica
    fecha = ahora.strftime("%d/%m/%Y %I:%M %p")
    elementos = _elementos_lista(items, n_pendientes, fecha)
    # Hojas de compra por proveedor (solo si ya hay proveedores oficiales asignados)
    if proveedores:
        elementos.append(PageBreak())
        elementos += _elementos_proveedores(proveedores, sin_proveedor, fecha)
    if pedidos:
        elementos.append(PageBreak())
        elementos += _elementos_hojas(pedidos, fecha)
    doc, buf = _nuevo_documento("Pedidos pendientes - Ecoltura")
    doc.build(elementos)
    nombre_archivo = f"pedidos_pendientes_{ahora.strftime('%Y-%m-%d')}.pdf"
    return Response(buf.getvalue(), mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'})


def _elementos_hojas(pedidos, fecha):
    from xml.sax.saxutils import escape
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (Paragraph, Table, TableStyle,
                                    Spacer, PageBreak)

    Caja = _clase_caja()
    est = getSampleStyleSheet()
    elementos = []

    if not pedidos:
        elementos.append(Paragraph("No hay pedidos pendientes.", est["Normal"]))

    for i, p in enumerate(pedidos):
        if i > 0:
            elementos.append(PageBreak())
        elementos.append(Paragraph(f"Pedido #{p['id']} — {escape(_texto_pdf(p['cliente']))}", est["Title"]))
        datos_cliente = []
        if p["celular"]:
            datos_cliente.append(f"Celular: {escape(p['celular'])}")
        if p["cedula"]:
            datos_cliente.append(f"Cédula: {escape(p['cedula'])}")
        if p["fecha"]:
            datos_cliente.append(f"Pedido: {p['fecha']}")
        if datos_cliente:
            elementos.append(Paragraph(" · ".join(datos_cliente), est["Normal"]))
        if p["notas"]:
            elementos.append(Spacer(1, 3*mm))
            elementos.append(Paragraph(f"<b>Notas / dirección:</b> {escape(_texto_pdf(p['notas'])).replace(chr(10), '<br/>')}",
                                       est["Normal"]))
        elementos.append(Spacer(1, 6*mm))

        filas = [["", "Producto", "Cantidad", "Subtotal (colones)"]]
        for nombre, cant, sub, d_l in p["lineas"]:
            texto = escape(_texto_pdf(nombre))
            if d_l > 0:
                texto += f"<br/><font size=8 color='#b3261e'>Descuento: - {_miles(d_l)}</font>"
            filas.append([Caja(), Paragraph(texto, est["Normal"]), cant, _miles(sub)])
        t = Table(filas, colWidths=[12*mm, 85*mm, 40*mm, 43*mm], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3E6B22")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (2, 1), (2, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 11),
            ("ALIGN", (2, 0), (3, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 1), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 7),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#999999")),
        ]))
        elementos.append(t)
        elementos.append(Spacer(1, 4*mm))

        totales = [["Subtotal" + (" (ya con descuentos por producto)" if p["desc_lineas"] > 0 else ""), _miles(p["subtotal"])]]
        if p["descuento"] > 0:
            totales.append(["Descuento", "- " + _miles(p["descuento"])])
        totales.append(["Total estimado (colones)", _miles(p["total"])])
        tt = Table(totales, colWidths=[137*mm, 43*mm])
        tt.setStyle(TableStyle([
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("FONTSIZE", (0, 0), (-1, -1), 11),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
        ]))
        elementos.append(tt)
        elementos.append(Spacer(1, 6*mm))
        elementos.append(Paragraph(f"<font size=8 color='#777777'>Generado {fecha}</font>", est["Normal"]))

    return elementos


def _generar_pdf_hojas(pedidos, fecha):
    doc, buf = _nuevo_documento("Hojas de pedidos - Ecoltura")
    doc.build(_elementos_hojas(pedidos, fecha))
    return buf.getvalue()


# ─────────────────────────────────────────────
#  DETALLE DE UN PEDIDO
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>")
def pedido_detalle(id):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        # Al abrir el pedido se quita la marca "Modificado" (ya lo vieron)
        cur.execute("UPDATE pedidos SET modificado = FALSE WHERE id = %s AND modificado = TRUE", (id,))
        conn.commit()
        cur.execute("""
            SELECT p.id, p.nombre_contacto, p.celular, p.cedula,
                   p.total_estimado, p.estado, p.creado, p.notas,
                   p.cliente_id, p.venta_id,
                   COALESCE(p.descuento_tipo, 'monto'), COALESCE(p.descuento_valor, 0),
                   COALESCE(p.seguimiento, 'Preparando'), COALESCE(p.modificado, FALSE), v.metodo_pago,
                   p.lat, p.lng, COALESCE(p.entrega, 'domicilio')
            FROM pedidos p LEFT JOIN ventas v ON v.id = p.venta_id
            WHERE p.id = %s
        """, (id,))
        pedido = cur.fetchone()

        cur.execute("""
            SELECT pd.id, pd.producto_id, pr.nombre, pr.unidad,
                   pd.cantidad, pd.precio_unitario,
                   ROUND(pd.cantidad * pd.precio_unitario) AS subtotal,
                   COALESCE(pd.descuento_tipo, 'monto'), COALESCE(pd.descuento_valor, 0)
            FROM pedidos_detalle pd
            JOIN productos pr ON pr.id = pd.producto_id
            WHERE pd.pedido_id = %s
            ORDER BY pr.nombre
        """, (id,))
        detalle = []
        for d in cur.fetchall():
            base, desc_l, neto = _neto_linea(d[4], d[5], d[7], d[8])
            # índices 0-5 igual que antes; 6 = neto de la línea; 7/8 = descuento; 9 = base; 10 = monto descontado
            detalle.append((d[0], d[1], d[2], d[3], d[4], d[5], neto, d[7], float(d[8]), base, desc_l))

        # Catálogo completo (no solo visible_web) para poder agregar
        # cualquier producto al pedido desde el admin. precio se convierte
        # a float porque Decimal no es serializable directo a JSON.
        cur.execute("SELECT id, nombre, unidad, precio FROM productos ORDER BY nombre")
        catalogo = [(r[0], r[1], r[2], float(r[3])) for r in cur.fetchall()]

        # Pesos preestablecidos por producto (los mismos botones "1 kg / 500 g"
        # que usa la tienda pública), para poder elegirlos igual desde el admin.
        cur.execute("SELECT producto_id, gramos FROM producto_pesos ORDER BY producto_id, gramos")
        pesos_por_producto = {}
        for producto_id, gramos in cur.fetchall():
            pesos_por_producto.setdefault(producto_id, []).append(gramos)

    return render_template("pedido_detalle.html",
                           pedido=pedido, detalle=detalle, catalogo=catalogo,
                           pesos_por_producto=pesos_por_producto)


# ─────────────────────────────────────────────
#  Helper: recalcular el total estimado tras cualquier edición
# ─────────────────────────────────────────────
def _calcular_descuento(subtotal, tipo, valor):
    """Monto del descuento (nunca mayor al subtotal)."""
    valor = float(valor or 0)
    if valor <= 0 or subtotal <= 0:
        return 0.0
    if tipo == 'porcentaje':
        valor = min(valor, 100.0)
        return float(round(subtotal * valor / 100.0))
    return float(round(min(valor, subtotal)))


def _totales(cur, pedido_id):
    """Devuelve (subtotal, descuento_monto, total, tipo, valor) del pedido."""
    subtotal, _ = _subtotal_y_desc_lineas(cur, pedido_id)
    cur.execute("""
        SELECT COALESCE(descuento_tipo, 'monto'), COALESCE(descuento_valor, 0)
        FROM pedidos WHERE id = %s
    """, (pedido_id,))
    tipo, valor = cur.fetchone()
    desc = _calcular_descuento(subtotal, tipo, valor)
    return subtotal, desc, float(round(subtotal - desc)), tipo, float(valor)


def _subtotal_y_desc_lineas(cur, pedido_id):
    """(subtotal ya con los descuentos por producto, suma de esos descuentos)."""
    cur.execute("""
        SELECT cantidad, precio_unitario, COALESCE(descuento_tipo, 'monto'), COALESCE(descuento_valor, 0)
        FROM pedidos_detalle WHERE pedido_id = %s
    """, (pedido_id,))
    neto_total = 0.0
    desc_total = 0.0
    for c, p, t, v in cur.fetchall():
        _, d, n = _neto_linea(c, p, t, v)
        neto_total += n
        desc_total += d
    return neto_total, desc_total


def _recalcular_total(cur, pedido_id):
    """total_estimado = subtotal - descuento (lo que ve la bandeja)."""
    _, _, total, _, _ = _totales(cur, pedido_id)
    cur.execute("UPDATE pedidos SET total_estimado = %s WHERE id = %s", (total, pedido_id))


def _pedido_editable(cur, id):
    """Solo se puede editar mientras el pedido siga Pendiente."""
    cur.execute("SELECT estado FROM pedidos WHERE id = %s", (id,))
    row = cur.fetchone()
    return row and row[0] == 'Pendiente'


# ─────────────────────────────────────────────
#  EDITAR CANTIDAD DE UNA LÍNEA (0 o menos = eliminarla)
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/detalle/<int:detalle_id>/actualizar", methods=["POST"])
def detalle_actualizar(id, detalle_id):
    nueva_cantidad = float(request.form.get("cantidad", 0))

    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _pedido_editable(cur, id):
            flash("Este pedido ya no se puede editar.", "warning")
            return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

        if nueva_cantidad <= 0:
            cur.execute("DELETE FROM pedidos_detalle WHERE id=%s AND pedido_id=%s", (detalle_id, id))
            flash("Producto eliminado del pedido.", "success")
        else:
            cur.execute("UPDATE pedidos_detalle SET cantidad=%s WHERE id=%s AND pedido_id=%s",
                        (nueva_cantidad, detalle_id, id))
            flash("Cantidad actualizada.", "success")

        _recalcular_total(cur, id)
        conn.commit()

    return redirect(url_for("pedidos_bp.pedido_detalle", id=id))


# ─────────────────────────────────────────────
#  ELIMINAR UNA LÍNEA DEL PEDIDO
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/detalle/<int:detalle_id>/eliminar", methods=["POST"])
def detalle_eliminar(id, detalle_id):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _pedido_editable(cur, id):
            flash("Este pedido ya no se puede editar.", "warning")
            return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

        cur.execute("DELETE FROM pedidos_detalle WHERE id=%s AND pedido_id=%s", (detalle_id, id))
        _recalcular_total(cur, id)
        conn.commit()

    flash("Producto eliminado del pedido.", "success")
    return redirect(url_for("pedidos_bp.pedido_detalle", id=id))


# ─────────────────────────────────────────────
#  JSON: cambiar cantidad (0 = eliminar) y descuento, devuelven totales
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/detalle/<int:detalle_id>/cantidad", methods=["POST"])
def detalle_cantidad_json(id, detalle_id):
    data = request.get_json(silent=True) or {}
    try:
        cantidad = float(data.get("cantidad", 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Cantidad inválida"), 400

    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _pedido_editable(cur, id):
            return jsonify(ok=False, error="Este pedido ya no se puede editar."), 409

        linea = None
        if cantidad <= 0:
            cur.execute("DELETE FROM pedidos_detalle WHERE id=%s AND pedido_id=%s", (detalle_id, id))
        else:
            cur.execute("""
                UPDATE pedidos_detalle SET cantidad=%s WHERE id=%s AND pedido_id=%s
                RETURNING cantidad, precio_unitario, COALESCE(descuento_tipo, 'monto'), COALESCE(descuento_valor, 0)
            """, (cantidad, detalle_id, id))
            r = cur.fetchone()
            linea = _neto_linea(*r)[2] if r else None

        _recalcular_total(cur, id)
        subtotal, desc, total, _, _ = _totales(cur, id)
        _, desc_lineas = _subtotal_y_desc_lineas(cur, id)
        conn.commit()

    return jsonify(ok=True, eliminada=cantidad <= 0, linea=linea,
                   subtotal=subtotal, descuento=desc, total=total, desc_lineas=desc_lineas)


# ─────────────────────────────────────────────
#  JSON: descuento de UN producto del pedido
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/detalle/<int:detalle_id>/descuento", methods=["POST"])
def detalle_descuento_json(id, detalle_id):
    data = request.get_json(silent=True) or {}
    tipo = data.get("tipo", "monto")
    if tipo not in ("monto", "porcentaje"):
        tipo = "monto"
    try:
        valor = max(0.0, float(data.get("valor", 0) or 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Descuento inválido"), 400
    if tipo == "porcentaje":
        valor = min(valor, 100.0)

    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _pedido_editable(cur, id):
            return jsonify(ok=False, error="Este pedido ya no se puede editar."), 409
        cur.execute("""
            UPDATE pedidos_detalle SET descuento_tipo=%s, descuento_valor=%s
            WHERE id=%s AND pedido_id=%s
            RETURNING cantidad, precio_unitario, descuento_tipo, descuento_valor
        """, (tipo, valor, detalle_id, id))
        r = cur.fetchone()
        if not r:
            return jsonify(ok=False, error="Producto no encontrado en el pedido"), 404
        base, desc_l, neto = _neto_linea(*r)
        _recalcular_total(cur, id)
        subtotal, desc, total, _, _ = _totales(cur, id)
        _, desc_lineas = _subtotal_y_desc_lineas(cur, id)
        conn.commit()

    return jsonify(ok=True, linea=neto, linea_base=base, linea_descuento=desc_l,
                   subtotal=subtotal, descuento=desc, total=total, desc_lineas=desc_lineas)


@pedidos_bp.route("/admin/pedidos/<int:id>/ubicacion", methods=["POST"])
def pedido_ubicacion(id):
    """Pegar la ubicación que mandó el cliente por WhatsApp: acepta "10.05, -84.48",
    un enlace de Google Maps o grados (10°03'21.6"N 84°29'09.7"W)."""
    lat, lng = _leer_coordenadas(request.form.get("coordenadas", ""))
    if lat is None:
        flash("No reconocí esa ubicación. Pegá las coordenadas (ej. 10.0561, -84.4860) o el enlace de Google Maps.", "danger")
        return redirect(url_for("pedidos_bp.pedido_detalle", id=id))
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE pedidos SET lat = %s, lng = %s WHERE id = %s RETURNING cliente_id", (lat, lng, id))
        fila = cur.fetchone()
        if fila and fila[0]:
            cur.execute("UPDATE clientes SET lat = %s, lng = %s WHERE id = %s", (lat, lng, fila[0]))
        conn.commit()
    flash(f"Pedido #{id}: ubicación guardada.", "success")
    return redirect(url_for("pedidos_bp.pedido_detalle", id=id))


def _leer_coordenadas(texto):
    import re
    t = (texto or "").strip()
    # Grados, minutos y segundos: 10°03'21.6"N 84°29'09.7"W
    gms = re.findall(r"(\d+(?:\.\d+)?)\s*°\s*(\d+(?:\.\d+)?)\s*['’′]\s*(\d+(?:\.\d+)?)\s*[\"”″]?\s*([NSEWO])", t, re.I)
    if len(gms) >= 2:
        def a_decimal(g, m, s, d):
            v = float(g) + float(m) / 60 + float(s) / 3600
            return -v if d.upper() in ("S", "W", "O") else v
        lat, lng = a_decimal(*gms[0]), a_decimal(*gms[1])
    else:
        nums = re.findall(r"-?\d{1,3}\.\d+", t)
        if len(nums) < 2:
            return None, None
        lat, lng = float(nums[0]), float(nums[1])
        if lat < 0 and lng > 0:          # pegaron "-84.48, 10.05"
            lat, lng = lng, lat
        if lng > 0:                      # Costa Rica siempre es longitud oeste
            lng = -lng
    if not (5.0 <= lat <= 11.5 and -87.5 <= lng <= -82.5):   # dentro de Costa Rica
        return None, None
    return round(lat, 6), round(lng, 6)


@pedidos_bp.route("/admin/pedidos/<int:id>/notas", methods=["POST"])
def pedido_notas(id):
    """Editar las notas del pedido desde el Admin (dirección, referencias, feria…)."""
    notas = (request.form.get("notas") or "").strip()[:1000]
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE pedidos SET notas = %s WHERE id = %s", (notas or None, id))
        conn.commit()
    flash(f"Pedido #{id}: notas guardadas.", "success")
    return redirect(url_for("pedidos_bp.pedido_detalle", id=id))


@pedidos_bp.route("/admin/pedidos/<int:id>/descuento", methods=["POST"])
def pedido_descuento_json(id):
    data = request.get_json(silent=True) or {}
    tipo = data.get("tipo", "monto")
    if tipo not in ("monto", "porcentaje"):
        tipo = "monto"
    try:
        valor = max(0.0, float(data.get("valor", 0) or 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Descuento inválido"), 400
    if tipo == "porcentaje":
        valor = min(valor, 100.0)

    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _pedido_editable(cur, id):
            return jsonify(ok=False, error="Este pedido ya no se puede editar."), 409
        cur.execute("UPDATE pedidos SET descuento_tipo=%s, descuento_valor=%s WHERE id=%s",
                    (tipo, valor, id))
        _recalcular_total(cur, id)
        subtotal, desc, total, _, _ = _totales(cur, id)
        _, desc_lineas = _subtotal_y_desc_lineas(cur, id)
        conn.commit()

    return jsonify(ok=True, subtotal=subtotal, descuento=desc, total=total, desc_lineas=desc_lineas)


# ─────────────────────────────────────────────
#  AGREGAR UN PRODUCTO NUEVO AL PEDIDO
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/detalle/agregar", methods=["POST"])
def detalle_agregar(id):
    if not (request.form.get("producto_id") or "").strip():
        flash("Elegí un producto de la lista que aparece al escribir el nombre.", "warning")
        return redirect(url_for("pedidos_bp.pedido_detalle", id=id))
    try:
        producto_id = int(request.form.get("producto_id", 0))
        # Hay dos campos "cantidad" (simple y por peso); se toma el primero con valor.
        # Se acepta coma decimal por si el teclado del celular la manda así.
        crudos = [c.strip().replace(",", ".") for c in request.form.getlist("cantidad") if c.strip()]
        cantidad = float(crudos[0]) if crudos else 0
    except (ValueError, IndexError):
        flash("Datos inválidos: revisá el producto y la cantidad.", "danger")
        return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

    if not producto_id or cantidad <= 0:
        flash("Elegí un producto y una cantidad válida.", "warning")
        return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

    with obtener_conexion() as conn:
        cur = conn.cursor()
        if not _pedido_editable(cur, id):
            flash("Este pedido ya no se puede editar.", "warning")
            return redirect(url_for("pedidos_bp.pedido_detalle", id=id))

        cur.execute("SELECT precio FROM productos WHERE id = %s", (producto_id,))
        row = cur.fetchone()
        if not row:
            flash("Producto no encontrado.", "danger")
            return redirect(url_for("pedidos_bp.pedido_detalle", id=id))
        precio = float(row[0])

        # Si el producto ya está en el pedido, suma la cantidad en vez de duplicar la línea
        cur.execute("SELECT id FROM pedidos_detalle WHERE pedido_id=%s AND producto_id=%s",
                    (id, producto_id))
        existente = cur.fetchone()
        if existente:
            cur.execute("UPDATE pedidos_detalle SET cantidad = cantidad + %s WHERE id = %s",
                        (cantidad, existente[0]))
        else:
            cur.execute("""
                INSERT INTO pedidos_detalle (pedido_id, producto_id, cantidad, precio_unitario)
                VALUES (%s, %s, %s, %s)
            """, (id, producto_id, cantidad, precio))

        _recalcular_total(cur, id)
        conn.commit()

    flash("Producto agregado al pedido.", "success")
    return redirect(url_for("pedidos_bp.pedido_detalle", id=id))


# ─────────────────────────────────────────────
#  CONVERTIR PEDIDO EN VENTA
# ─────────────────────────────────────────────
def convertir_en_venta(cur, id, metodo_venta, sinpe_pendiente=False, por_reparto=False):
    """Pasa un pedido Pendiente a venta (descuenta inventario y lo marca Entregado).
    La usan el kanban ("Pasar a venta") y el repartidor ("Entregado").
    Devuelve (venta_id, None) o (None, motivo). No hace commit."""
    cur.execute("SELECT estado, cliente_id FROM pedidos WHERE id=%s FOR UPDATE", (id,))
    row = cur.fetchone()
    if not row or row[0] != 'Pendiente':
        return None, "Este pedido ya fue procesado."
    cliente_id = row[1]
    ya_pago = metodo_venta != "Por cobrar"

    # Obtener detalle del pedido
    cur.execute("""
        SELECT producto_id, cantidad, precio_unitario,
               COALESCE(descuento_tipo, 'monto'), COALESCE(descuento_valor, 0)
        FROM pedidos_detalle WHERE pedido_id = %s ORDER BY id
    """, (id,))
    items = cur.fetchall()
    if not items:
        return None, "El pedido no tiene productos."

    # subtotal_total = suma de las líneas ya con su descuento por producto;
    # descuento_total = descuento general del pedido (sobre ese subtotal).
    subtotal_total, descuento_total, total_total, _, _ = _totales(cur, id)
    bruto_total = float(sum(round(float(c) * float(p)) for _, c, p, _, _ in items))
    desc_lineas_total = float(round(bruto_total - subtotal_total))

    fecha_hora = (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")

    # Insertar venta (con el descuento del pedido)
    cur.execute("""
        INSERT INTO ventas (cliente_id, fecha_venta, subtotal, descuento, iva, total, metodo_pago, estado)
        VALUES (%s, %s, %s, %s, 0, %s, %s, 'Facturado')
        RETURNING id
    """, (cliente_id, fecha_hora, bruto_total, desc_lineas_total + descuento_total, total_total, metodo_venta))
    venta_id = cur.fetchone()[0]

    desc_asignado = 0.0
    for i, (producto_id, cantidad, precio, d_tipo, d_valor) in enumerate(items):
        sub_l, desc_prod, neto_l = _neto_linea(cantidad, precio, d_tipo, d_valor)

        # Descuento general repartido proporcionalmente al neto de cada línea;
        # la última línea absorbe el redondeo. Se suma al descuento propio del producto.
        if i == len(items) - 1:
            desc_gral = float(round(descuento_total - desc_asignado))
        else:
            desc_gral = float(round(descuento_total * neto_l / subtotal_total)) if subtotal_total else 0.0
            desc_asignado += desc_gral
        desc_l = desc_prod + desc_gral
        total_l = float(round(sub_l - desc_l))

        # Detalle de venta
        cur.execute("""
            INSERT INTO ventas_detalle
                (venta_id, producto_id, cantidad, precio_unitario,
                 subtotal_linea, descuento, total_linea)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (venta_id, producto_id, cantidad, precio, sub_l, desc_l, total_l))

        # Movimiento de inventario (salida)
        cur.execute("""
            INSERT INTO inventario_movimientos (producto_id, fecha, tipo, cantidad)
            VALUES (%s, %s, 'F', %s)
        """, (producto_id, fecha_hora, cantidad))

        # Reducir inventario_semanal (FIFO)
        cur.execute("""
            UPDATE inventario_semanal
            SET cantidad_disponible = cantidad_disponible - %s
            WHERE id = (
                SELECT id FROM inventario_semanal
                WHERE producto_id = %s AND cantidad_disponible >= %s
                ORDER BY fecha_semana ASC LIMIT 1
            )
        """, (cantidad, producto_id, cantidad))

    if sinpe_pendiente:
        cur.execute("UPDATE ventas SET sinpe_pendiente = TRUE WHERE id = %s", (venta_id,))

    # Marcar pedido como Resuelto (Entregado); se limpia cualquier aviso de "no entregado"
    cur.execute("""
        UPDATE pedidos SET estado='Resuelto', venta_id=%s, gestionado=NOW(),
               pagado_en = CASE WHEN %s THEN NOW() ELSE NULL END,
               entregado_reparto = %s, entrega_fallida = NULL, entrega_fallida_en = NULL
        WHERE id=%s
    """, (venta_id, ya_pago, por_reparto, id))
    return venta_id, None


def avisar_entrega(celular, id):
    """Aviso al cliente (si activó la campana): su pedido fue entregado."""
    try:
        from modulos.push import notificar_cliente
        notificar_cliente(celular, "ECOLTURA", "Tu pedido fue entregado. ¡Gracias por apoyar productos locales! 🌱", "/")
    except Exception as ex:
        print(f"No se pudo avisar al cliente del pedido #{id}: {ex}")


@pedidos_bp.route("/admin/pedidos/<int:id>/convertir", methods=["POST"])
def pedido_convertir(id):
    clave = request.form.get("pago", "no")
    metodo_venta = METODOS_PAGO.get(clave, "Por cobrar")
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT celular FROM pedidos WHERE id=%s", (id,))
        fila = cur.fetchone()
        celular = fila[0] if fila else None
        venta_id, error = convertir_en_venta(cur, id, metodo_venta)
        if error:
            flash(error, "warning")
            return redirect(url_for("pedidos_bp.pedido_detalle", id=id))
        conn.commit()

    avisar_entrega(celular, id)

    estado_pago = "por cobrar" if metodo_venta == "Por cobrar" else f"cobrada ({metodo_venta})"
    flash(f"Pedido #{id} convertido en venta #{venta_id}, {estado_pago}.", "success")
    if request.form.get("volver") == "bandeja":
        return redirect(url_for("pedidos_bp.bandeja_pedidos"))
    return redirect(url_for("ventas_bp.ticket_venta", venta_id=venta_id))


# ─────────────────────────────────────────────
#  SINPE POR CONFIRMAR (lo marcó el repartidor): la oficina confirma que entró
#  el dinero, o lo devuelve a "Por cobrar" si no llegó.
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/sinpe", methods=["POST"])
def pedido_sinpe(id):
    llego = request.form.get("accion") == "confirmar"
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT venta_id FROM pedidos WHERE id = %s", (id,))
        fila = cur.fetchone()
        if fila and fila[0]:
            if llego:
                cur.execute("UPDATE ventas SET sinpe_pendiente = FALSE WHERE id = %s", (fila[0],))
                flash(f"Pedido #{id}: SINPE confirmado.", "success")
            else:
                cur.execute("""UPDATE ventas SET sinpe_pendiente = FALSE, metodo_pago = 'Por cobrar'
                               WHERE id = %s""", (fila[0],))
                cur.execute("UPDATE pedidos SET pagado_en = NULL WHERE id = %s", (id,))
                flash(f"Pedido #{id}: el SINPE no llegó, quedó Por cobrar.", "warning")
            conn.commit()
    if request.form.get("volver") == "detalle":
        return redirect(url_for("pedidos_bp.pedido_detalle", id=id))
    return redirect(url_for("pedidos_bp.bandeja_pedidos"))


# ─────────────────────────────────────────────
#  RECHAZAR PEDIDO
# ─────────────────────────────────────────────
@pedidos_bp.route("/admin/pedidos/<int:id>/rechazar", methods=["POST"])
def pedido_rechazar(id):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE pedidos SET estado='Rechazado', gestionado=NOW()
            WHERE id=%s AND estado='Pendiente'
        """, (id,))
        conn.commit()

    flash(f"Pedido #{id} rechazado.", "danger")
    return redirect(url_for("pedidos_bp.bandeja_pedidos"))
