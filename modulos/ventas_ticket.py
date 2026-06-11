# -*- coding: utf-8 -*-
"""
Ticket de venta compartible por WhatsApp — ECOLTURA
Ubicación: modulos/ventas_ticket.py
En app.py agregar junto a los otros imports de ventas:
    import modulos.ventas_ticket
"""
from urllib.parse import quote
from flask import render_template, abort
from db.conexion import obtener_conexion
from modulos.ventas_menu import ventas_bp


def limpiar_celular(celular):
    """Deja solo dígitos y antepone 506 si vienen 8 dígitos (CR)."""
    if not celular:
        return None
    digitos = "".join(c for c in celular if c.isdigit())
    if len(digitos) == 8:
        return "506" + digitos
    return digitos or None


def construir_texto_ticket(venta, detalles):
    lineas = [
        "🌱 *ECOLTURA*",
        f"Ticket de venta #{venta['id']}",
        f"📅 {venta['fecha'].strftime('%d/%m/%Y %I:%M %p')}",
        f"👤 Cliente: {venta['cliente']}",
        "─────────────────",
    ]
    for d in detalles:
        cant = f"{d['cantidad']:g}"  # sin decimales innecesarios
        lineas.append(f"• {d['producto']} x{cant} {d['unidad']} — ₡{d['total_linea']:,.0f}")

    lineas.append("─────────────────")
    if venta["descuento"] and venta["descuento"] > 0:
        lineas.append(f"Subtotal: ₡{venta['subtotal']:,.0f}")
        lineas.append(f"Descuento: -₡{venta['descuento']:,.0f}")
    lineas += [
        f"*TOTAL: ₡{venta['total']:,.0f}*",
        "",
        "¡Gracias por su compra! 💚",
    ]
    return "\n".join(lineas)


@ventas_bp.route("/ventas/ticket/<int:venta_id>")
def ticket_venta(venta_id):
    with obtener_conexion() as conn:
        cur = conn.cursor()

        # Encabezado + cliente (LEFT JOIN: la venta puede no tener cliente)
        cur.execute("""
            SELECT v.id, v.fecha_venta, v.subtotal, v.descuento, v.total,
                   v.metodo_pago, v.estado,
                   COALESCE(c.nombre, 'Cliente general') AS cliente,
                   c.celular
            FROM ventas v
            LEFT JOIN clientes c ON c.id = v.cliente_id
            WHERE v.id = %s
        """, (venta_id,))
        row = cur.fetchone()

        if not row:
            abort(404)

        venta = {
            "id": row[0],
            "fecha": row[1],
            "subtotal": float(row[2] or 0),
            "descuento": float(row[3] or 0),
            "total": float(row[4] or 0),
            "metodo_pago": row[5],
            "estado": row[6],
            "cliente": row[7],
            "celular": row[8],
        }

        # Detalle de líneas
        cur.execute("""
            SELECT p.nombre, p.unidad, d.cantidad, d.precio_unitario,
                   d.descuento, d.total_linea
            FROM ventas_detalle d
            JOIN productos p ON p.id = d.producto_id
            WHERE d.venta_id = %s
            ORDER BY d.id
        """, (venta_id,))
        detalles = [
            {
                "producto": r[0],
                "unidad": r[1],
                "cantidad": float(r[2]),
                "precio_unitario": float(r[3]),
                "descuento": float(r[4] or 0),
                "total_linea": float(r[5]),
            }
            for r in cur.fetchall()
        ]

    texto = construir_texto_ticket(venta, detalles)

    telefono = limpiar_celular(venta["celular"])
    base = f"https://wa.me/{telefono}" if telefono else "https://wa.me/"
    enlace_wa = f"{base}?text={quote(texto)}"

    return render_template(
        "ticket_venta.html",
        venta=venta,
        detalles=detalles,
        texto_ticket=texto,
        enlace_wa=enlace_wa,
    )
