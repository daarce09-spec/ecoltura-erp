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
    """Cantidad como la ve el cliente en la tienda: 1500 gramos → 1,5 kg · 1 Unidad → 1 unidad."""
    cant = float(cant)
    u = (unidad or "").strip().lower()
    if u in ("g", "gr", "gramo", "gramos"):
        txt = f"{cant / 1000:g} kg" if cant >= 1000 else f"{cant:g} g"
    elif u:
        txt = f"{cant:g} {u}"
    else:
        txt = f"{cant:g}"
    return txt.replace(".", ",")


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
    F = "font-family:Arial,Helvetica,sans-serif;"
    SERIF = "font-family:Georgia,'Times New Roman',serif;"
    primer = escape(p["nombre"].split()[0]) if p["nombre"].strip() else ""
    if unido:
        titulo = f"¡Listo, {primer}!" if primer else "¡Listo!"
        subtitulo = "Agregamos los productos a tu pedido 🌱"
        parrafo = "Así queda tu pedido completo. Lo seguimos preparando con el mismo cariño de siempre."
    else:
        titulo = f"¡Gracias, {primer}!" if primer else "¡Gracias por tu pedido!"
        subtitulo = "Recibimos tu pedido 🌱"
        parrafo = ("Nos alegra mucho que nos elijas. Cada pedido nos ayuda a seguir cultivando "
                   "de forma regenerativa. Ya estamos preparando el tuyo con mucho cariño.")

    filas = ""
    for it in p["items"]:
        if it["envio"]:
            filas += (f'<tr><td style="padding:10px 0 4px;{F}font-size:14px;color:#8A8A84;">Envío a domicilio</td>'
                      f'<td align="right" style="padding:10px 0 4px;{F}font-size:14px;color:#8A8A84;white-space:nowrap;">{_colones(it["subtotal"])}</td></tr>')
        else:
            filas += (f'<tr><td style="padding:10px 0;border-bottom:1px solid #ECE7DC;{F}">'
                      f'<div style="font-size:15px;color:#2B2B2B;">{escape(it["nombre"])}</div>'
                      f'<div style="font-size:13px;color:#8A8A84;padding-top:2px;">{escape(it["cantidad"])}</div></td>'
                      f'<td align="right" valign="top" style="padding:10px 0;border-bottom:1px solid #ECE7DC;{F}font-size:15px;color:#2B2B2B;white-space:nowrap;">{_colones(it["subtotal"])}</td></tr>')

    entrega = ""
    if p["direccion"].strip():
        entrega = (f'<tr><td style="padding:16px 32px 0;{F}font-size:14px;line-height:1.6;color:#5A5A55;">'
                   f'📍 <b style="color:#2B2B2B;">Lo llevamos a:</b> {escape(p["direccion"])}</td></tr>')

    def paso(n, texto):
        return (f'<tr><td width="34" valign="top" style="padding:6px 0;">'
                f'<div style="width:26px;height:26px;line-height:26px;border-radius:13px;background:#E7F0DF;color:#3E6B22;'
                f'{F}font-size:13px;font-weight:bold;text-align:center;">{n}</div></td>'
                f'<td style="padding:6px 0 6px 6px;{F}font-size:14px;line-height:1.5;color:#4A4A45;">{texto}</td></tr>')

    pasos = (paso(1, "Preparamos tu pedido con productos frescos.") +
             paso(2, "Te escribimos por <b>WhatsApp</b> para coordinar la entrega.") +
             paso(3, "Lo recibís en tu casa y pagás en efectivo o SINPE Móvil."))

    inicio = escape(enlace.split("/pedido/")[0])
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{subtitulo}</title></head>
<body style="margin:0;padding:0;background:#F4EFE6;">
<div style="display:none;max-height:0;overflow:hidden;">{subtitulo} · Total estimado {_colones(p["total"])}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F4EFE6;">
<tr><td align="center" style="padding:28px 12px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#FFFDF9;border-radius:18px;overflow:hidden;">
    <tr><td style="height:6px;background:#3E6B22;line-height:6px;font-size:0;">&nbsp;</td></tr>
    <tr><td align="center" style="padding:30px 24px 6px;">
      <img src="{logo}" width="84" height="84" alt="ECOLTURA" style="display:block;border:0;">
    </td></tr>
    <tr><td align="center" style="padding:10px 28px 0;{SERIF}font-size:30px;color:#1F3A14;">{titulo}</td></tr>
    <tr><td align="center" style="padding:6px 28px 0;{F}font-size:15px;font-weight:bold;color:#D9742E;letter-spacing:.5px;">{subtitulo}</td></tr>
    <tr><td align="center" style="padding:14px 40px 24px;{F}font-size:15px;line-height:1.65;color:#5A5A55;">{parrafo}</td></tr>
    <tr><td style="padding:0 28px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F8F4EC;border-radius:14px;">
        <tr><td style="padding:18px 20px 4px;{F}font-size:12px;letter-spacing:2px;color:#3E6B22;font-weight:bold;">TU CANASTA 🧺</td></tr>
        <tr><td style="padding:0 20px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{filas}</table>
        </td></tr>
        <tr><td style="padding:4px 20px 18px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="{F}font-size:16px;font-weight:bold;color:#2B2B2B;padding-top:10px;border-top:2px solid #3E6B22;">Total estimado</td>
                <td align="right" style="{F}font-size:21px;font-weight:bold;color:#3E6B22;padding-top:10px;border-top:2px solid #3E6B22;white-space:nowrap;">{_colones(p["total"])}</td></tr>
          </table>
        </td></tr>
      </table>
    </td></tr>
    {entrega}
    <tr><td align="center" style="padding:24px 28px 6px;">
      <a href="{enlace}" style="display:inline-block;background:#3E6B22;color:#ffffff;text-decoration:none;{F}font-weight:bold;font-size:15px;padding:15px 34px;border-radius:30px;">Ver o editar mi pedido</a>
    </td></tr>
    <tr><td align="center" style="padding:4px 28px 0;{F}font-size:12px;color:#8A8A84;">¿Se te olvidó algo? Desde ahí podés agregarlo.</td></tr>
    <tr><td style="padding:26px 32px 4px;{SERIF}font-size:18px;color:#1F3A14;">¿Qué sigue?</td></tr>
    <tr><td style="padding:4px 32px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{pasos}</table></td></tr>
    <tr><td style="padding:24px 32px 30px;{F}font-size:15px;line-height:1.6;color:#4A4A45;">
      Gracias por ser parte de esto.<br>
      <span style="{SERIF}font-style:italic;font-size:17px;color:#1F3A14;">Con cariño, el equipo de Ecoltura 🌿</span>
    </td></tr>
    <tr><td align="center" style="background:#1A4226;padding:22px 24px;{F}font-size:13px;line-height:1.8;color:#CFE3D4;">
      <b style="color:#ffffff;letter-spacing:2px;">ECOLTURA</b> · Agricultura regenerativa · San Ramón<br>
      <a href="{inicio}" style="color:#CFE3D4;">www.ecoltura.com</a> &nbsp;·&nbsp; <a href="{WHATSAPP}" style="color:#CFE3D4;">WhatsApp</a> &nbsp;·&nbsp; <a href="{INSTAGRAM}" style="color:#CFE3D4;">Instagram</a>
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


def _asunto(p, unido):
    primer = p["nombre"].split()[0] if p["nombre"].strip() else ""
    if unido:
        return f"{primer}, agregamos los productos a tu pedido" if primer else "Agregamos los productos a tu pedido"
    return f"¡Gracias por tu pedido, {primer}!" if primer else "¡Gracias por tu pedido!"


def _texto(p, enlace, unido):
    lineas = [("Agregamos productos a tu pedido." if unido else "¡Recibimos tu pedido!"), ""]
    for it in p["items"]:
        nom = "Envío a domicilio" if it["envio"] else f'{it["nombre"]} · {it["cantidad"]}'
        lineas.append(f'- {nom}: {_colones(it["subtotal"])}')
    lineas += ["", f'Total estimado: {_colones(p["total"])}', "", f"Ver o editar tu pedido: {enlace}", "",
               "Te escribimos por WhatsApp para coordinar la entrega. El pago es al recibir, en efectivo o SINPE Móvil.",
               "", "Con cariño, el equipo de Ecoltura"]
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
            "subject": _asunto(p, unido),
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
