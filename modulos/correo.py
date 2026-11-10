# -*- coding: utf-8 -*-
"""
Correos a clientes (confirmación de pedido) por medio de Resend.

Variables en Railway (servicio de la TIENDA):
  RESEND_API_KEY     → la key de Resend (obligatoria; sin ella no se envía nada)
  CORREO_REMITENTE   → opcional, por defecto "ECOLTURA <pedidos@ecoltura.com>"
  CORREO_RESPUESTA   → opcional, a dónde llegan las respuestas del cliente

El envío corre en un hilo aparte: si Resend falla o tarda, el pedido ya quedó guardado.
"""
import os
import re
import json
import threading
import urllib.request
from html import escape

from db.conexion import obtener_conexion

ID_EXPRESS = 118
RE_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
WHATSAPP = "https://wa.me/50689935973"
INSTAGRAM = "https://www.instagram.com/ecolturacr/"


def correo_valido(valor):
    valor = (valor or "").strip()
    return valor if len(valor) <= 120 and RE_CORREO.match(valor) else ""


def _colones(x):
    return "₡" + f"{float(x):,.0f}".replace(",", ".")


def _cantidad(cant, unidad):
    cant = float(cant)
    txt = f"{cant:g}"
    u = (unidad or "").strip().lower()
    if u in ("g", "gr", "gramos", "kg", "kilo", "kilos", "ml", "l", "litro", "litros"):
        return f"{txt} {unidad.strip()}"
    return txt


def _leer_pedido(token):
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.nombre_contacto, p.total_estimado, c.direccion
            FROM pedidos p LEFT JOIN clientes c ON c.id = p.cliente_id
            WHERE p.token = %s
        """, (token,))
        fila = cur.fetchone()
        if not fila:
            return None
        pid, nombre, total, direccion = fila
        cur.execute("""
            SELECT d.producto_id, pr.nombre, pr.unidad, d.cantidad
            FROM pedidos_detalle d JOIN productos pr ON pr.id = d.producto_id
            WHERE d.pedido_id = %s ORDER BY (d.producto_id = %s), pr.nombre
        """, (pid, ID_EXPRESS))
        lineas = cur.fetchall()
        # Subtotal de cada línea ya con su descuento, igual que en "Mi pedido"
        cur.execute("""
            SELECT producto_id, cantidad, precio_unitario,
                   COALESCE(descuento_tipo, 'monto'), COALESCE(descuento_valor, 0)
            FROM pedidos_detalle WHERE pedido_id = %s
        """, (pid,))
        subtot = {}
        for prod, cant, precio, tipo, valor in cur.fetchall():
            base = float(round(float(cant) * float(precio)))
            valor = float(valor or 0)
            if valor > 0 and base > 0:
                desc = round(base * min(valor, 100.0) / 100.0) if tipo == "porcentaje" else round(min(valor, base))
            else:
                desc = 0
            subtot[prod] = base - desc
    items = [{"nombre": n, "cantidad": _cantidad(c, u), "subtotal": subtot.get(p, 0), "envio": p == ID_EXPRESS}
             for p, n, u, c in lineas]
    return {"nombre": nombre or "", "total": float(total or 0), "direccion": direccion or "", "items": items}


def _html(p, enlace, logo, unido):
    nombre = escape(p["nombre"].split()[0] if p["nombre"].strip() else "")
    titulo = "¡Agregamos a tu pedido! 🌱" if unido else "¡Recibimos tu pedido! 🌱"
    intro = ("Sumamos los productos nuevos a tu pedido en preparación. Así queda completo:"
             if unido else
             "gracias por apoyar productos locales.<br>Ya lo estamos preparando con cariño.")
    saludo = f'Hola <b style="color:#1C1C1E;">{nombre}</b>, ' if nombre else ""
    if unido:
        saludo = (f'Hola <b style="color:#1C1C1E;">{nombre}</b>.<br>' if nombre else "")

    filas = ""
    for it in p["items"]:
        estilo = "color:#777;" if it["envio"] else "border-bottom:1px solid #E6E6E2;"
        texto = "Envío Express" if it["envio"] else f'{escape(it["nombre"])} · {escape(it["cantidad"])}'
        filas += (f'<tr><td style="padding:9px 0;{estilo}">{texto}</td>'
                  f'<td align="right" style="padding:9px 0;{estilo}white-space:nowrap;">{_colones(it["subtotal"])}</td></tr>')

    entrega = ""
    if p["direccion"].strip():
        entrega = (f'<tr><td style="padding:18px 28px 0;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.6;color:#555;">'
                   f'📍 <b style="color:#1C1C1E;">Entrega:</b> {escape(p["direccion"])}</td></tr>')

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{titulo}</title></head>
<body style="margin:0;padding:0;background:#F3F1EC;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F3F1EC;">
<tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:16px;overflow:hidden;">
    <tr><td align="center" style="padding:28px 24px 8px;">
      <img src="{logo}" width="96" height="96" alt="ECOLTURA" style="display:block;border:0;">
    </td></tr>
    <tr><td align="center" style="padding:8px 28px 4px;font-family:Georgia,'Times New Roman',serif;font-size:28px;color:#1C1C1E;">{titulo}</td></tr>
    <tr><td align="center" style="padding:6px 32px 22px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:#555;">{saludo}{intro}</td></tr>
    <tr><td style="padding:0 28px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F6F6F4;border-radius:12px;">
        <tr><td style="padding:16px 18px 6px;font-family:Arial,Helvetica,sans-serif;font-size:12px;letter-spacing:2px;color:#3E6B22;font-weight:bold;">TU PEDIDO</td></tr>
        <tr><td style="padding:0 18px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#333;">{filas}</table>
        </td></tr>
        <tr><td style="padding:6px 18px 16px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-family:Arial,Helvetica,sans-serif;">
            <tr><td style="font-size:17px;font-weight:bold;color:#1C1C1E;padding-top:8px;border-top:2px solid #3E6B22;">Total estimado</td>
                <td align="right" style="font-size:20px;font-weight:bold;color:#3E6B22;padding-top:8px;border-top:2px solid #3E6B22;">{_colones(p["total"])}</td></tr>
          </table>
        </td></tr>
      </table>
    </td></tr>
    {entrega}
    <tr><td align="center" style="padding:24px 28px 8px;">
      <a href="{enlace}" style="display:inline-block;background:#3E6B22;color:#ffffff;text-decoration:none;font-family:Arial,Helvetica,sans-serif;font-weight:bold;font-size:15px;padding:14px 30px;border-radius:10px;">Ver o editar mi pedido</a>
    </td></tr>
    <tr><td align="center" style="padding:12px 32px 26px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.6;color:#555;">
      Te escribimos por <b style="color:#1C1C1E;">WhatsApp</b> para coordinar la entrega.<br>
      El pago es al recibir, en efectivo o SINPE Móvil.
    </td></tr>
    <tr><td align="center" style="background:#1A4226;padding:20px 24px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.7;color:#CFE3D4;">
      <b style="color:#ffffff;letter-spacing:2px;">ECOLTURA</b> · Agricultura regenerativa<br>
      <a href="{escape(enlace.split('/pedido/')[0])}" style="color:#CFE3D4;">www.ecoltura.com</a> · <a href="{WHATSAPP}" style="color:#CFE3D4;">WhatsApp</a> · <a href="{INSTAGRAM}" style="color:#CFE3D4;">Instagram</a>
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


def _texto(p, enlace, unido):
    lineas = [("Agregamos productos a tu pedido." if unido else "¡Recibimos tu pedido!"), ""]
    for it in p["items"]:
        nom = "Envío Express" if it["envio"] else f'{it["nombre"]} · {it["cantidad"]}'
        lineas.append(f'- {nom}: {_colones(it["subtotal"])}')
    lineas += ["", f'Total estimado: {_colones(p["total"])}', "", f"Ver o editar tu pedido: {enlace}", "",
               "Te escribimos por WhatsApp para coordinar la entrega. El pago es al recibir, en efectivo o SINPE Móvil.",
               "", "ECOLTURA · Agricultura regenerativa"]
    return "\n".join(lineas)


def _enviar(destino, token, base, unido):
    key = os.environ.get("RESEND_API_KEY", "").strip()
    if not key:
        return
    try:
        p = _leer_pedido(token)
        if not p:
            return
        enlace = f"{base}/pedido/{token}"
        logo = f"{base}/static/img/icon-512.png"
        cuerpo = {
            "from": os.environ.get("CORREO_REMITENTE", "ECOLTURA <pedidos@ecoltura.com>"),
            "to": [destino],
            "subject": "Actualizamos tu pedido · ECOLTURA" if unido else "Recibimos tu pedido 🌱 · ECOLTURA",
            "html": _html(p, enlace, logo, unido),
            "text": _texto(p, enlace, unido),
        }
        respuesta = os.environ.get("CORREO_RESPUESTA", "").strip()
        if respuesta:
            cuerpo["reply_to"] = respuesta
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(cuerpo).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "User-Agent": "ecoltura-tienda/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except Exception as ex:
        # Nunca se muestra el correo en los logs, solo el error
        print(f"No se pudo enviar el correo del pedido: {ex}")


def enviar_confirmacion_pedido(destino, token, base_url, unido=False):
    """Manda el correo en segundo plano. base_url = dirección de la tienda (ej. https://www.ecoltura.com)."""
    destino = correo_valido(destino)
    if not destino or not token:
        return
    base = (base_url or "https://www.ecoltura.com").rstrip("/")
    threading.Thread(target=_enviar, args=(destino, token, base, unido), daemon=True).start()
