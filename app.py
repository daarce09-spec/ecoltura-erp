# -*- coding: utf-8 -*-
from flask import Flask, render_template, redirect, url_for, jsonify

# ── Módulos del app administrativo ──
from modulos.clientes   import clientes_bp
from modulos.productos  import productos_bp
from modulos.inventario import inventario_bp
from modulos.saldo      import saldo_bp

# ── Blueprint de ventas (rutas extra se cargan con import) ──
from modulos.ventas_menu import ventas_bp
import modulos.ventas_registrar
import modulos.ventas_anular
import modulos.ventas_ticket

# ── Web pública ──
from modulos.web_publica import web_bp

# ── Bandejas del app (Fases 3 y 4 — se crean después) ──
from modulos.pedidos  import pedidos_bp
from modulos.visitas  import visitas_bp

app = Flask(__name__, template_folder="templates")
app.secret_key = "ecoltura_secret_key_2025"

# Registrar blueprints
app.register_blueprint(clientes_bp)
app.register_blueprint(productos_bp)
app.register_blueprint(inventario_bp)
app.register_blueprint(saldo_bp)
app.register_blueprint(ventas_bp)
app.register_blueprint(web_bp)        # sirve "/" (web pública + APIs /api/*)
app.register_blueprint(pedidos_bp)    # bandeja /admin/pedidos
app.register_blueprint(visitas_bp)    # bandeja /admin/visitas

# ── Menú administrativo ──
@app.route("/menu")
def menu():
    return render_template("menu.html")

# ── API de contadores para el menú (badges de pendientes) ──
@app.route("/admin/api/contadores")
def api_contadores():
    from db.conexion import obtener_conexion
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM pedidos       WHERE estado = 'Pendiente'")
        ped = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM visitas_reservas WHERE estado = 'Pendiente'")
        vis = cur.fetchone()[0]
    return jsonify({"pedidos_pendientes": ped, "visitas_pendientes": vis})


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
