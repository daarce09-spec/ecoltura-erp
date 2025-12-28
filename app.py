# -*- coding: utf-8 -*-
from flask import Flask, render_template, redirect, url_for

# Blueprints de otros módulos
from modulos.clientes import clientes_bp
from modulos.productos import productos_bp
from modulos.inventario import inventario_bp
from modulos.saldo import saldo_bp

# Blueprint oficial del módulo de ventas
from modulos.ventas_menu import ventas_bp

# Cargar rutas extras del módulo de ventas (necesario)
import modulos.ventas_registrar
import modulos.ventas_anular

app = Flask(__name__, template_folder="templates")
app.secret_key = "ecoltura_secret_key_2025"

# Registrar blueprints
app.register_blueprint(clientes_bp)
app.register_blueprint(productos_bp)
app.register_blueprint(inventario_bp)
app.register_blueprint(saldo_bp)
app.register_blueprint(ventas_bp)

# --- INICIO Y NAV ---
@app.route("/")
def home():
    return redirect(url_for("menu"))

@app.route("/menu")
def menu():
    return render_template("menu.html")

# --- EJECUCIÓN ---
if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
