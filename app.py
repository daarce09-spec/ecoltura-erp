# -*- coding: utf-8 -*-
from flask import Flask, render_template, redirect, url_for, jsonify
from datetime import datetime

# ── Módulos del app administrativo ──
from modulos.clientes   import clientes_bp
from modulos.productos  import productos_bp
from modulos.inventario import inventario_bp
from modulos.saldo      import saldo_bp

# ── Blueprint de ventas ──
from modulos.ventas_menu import ventas_bp
import modulos.ventas_registrar
import modulos.ventas_anular
import modulos.ventas_ticket
import modulos.ventas_historial

# ── Web pública ──
from modulos.web_publica import web_bp

# ── Bandejas ──
from modulos.pedidos import pedidos_bp
from modulos.visitas import visitas_bp

app = Flask(__name__, template_folder="templates")
app.secret_key = "ecoltura_secret_key_2025"

app.register_blueprint(clientes_bp)
app.register_blueprint(productos_bp)
app.register_blueprint(inventario_bp)
app.register_blueprint(saldo_bp)
app.register_blueprint(ventas_bp)
app.register_blueprint(web_bp)
app.register_blueprint(pedidos_bp)
app.register_blueprint(visitas_bp)

@app.route("/menu")
def menu():
    return render_template("menu.html")

@app.route("/admin/api/contadores")
def api_contadores():
    from db.conexion import obtener_conexion
    with obtener_conexion() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM pedidos WHERE estado='Pendiente'")
        ped = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM visitas_reservas WHERE estado='Pendiente'")
        vis = cur.fetchone()[0]
    return jsonify({"pedidos_pendientes": ped, "visitas_pendientes": vis})

@app.context_processor
def inject_now():
    return {"now": datetime.now()}

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
