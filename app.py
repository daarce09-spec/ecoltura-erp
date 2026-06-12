# -*- coding: utf-8 -*-
from flask import Flask, render_template, redirect, url_for, jsonify

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

# ── Web pública ──
from modulos.web_publica import web_bp

app = Flask(__name__, template_folder="templates")
app.secret_key = "ecoltura_secret_key_2025"

# Registrar blueprints
app.register_blueprint(clientes_bp)
app.register_blueprint(productos_bp)
app.register_blueprint(inventario_bp)
app.register_blueprint(saldo_bp)
app.register_blueprint(ventas_bp)
app.register_blueprint(web_bp)      # sirve "/" y las APIs /api/*

# ── Menú administrativo ──
@app.route("/menu")
def menu():
    return render_template("menu.html")

# ── Contadores (devuelve 0 hasta que existan las tablas de fase 3/4) ──
@app.route("/admin/api/contadores")
def api_contadores():
    return jsonify({"pedidos_pendientes": 0, "visitas_pendientes": 0})

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
