# -*- coding: utf-8 -*-
from flask import Flask, render_template, redirect, url_for, jsonify, send_from_directory, request, abort
from datetime import datetime
import os

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

# ── Notificaciones push ──
from modulos.push import push_bp

# ── Recordatorios de compra ──
from modulos.recordatorios import recordatorios_bp

app = Flask(__name__, template_folder="templates")
app.secret_key = "ecoltura_secret_key_2025"

# Los archivos estáticos (fotos, manifest, etc.) se revalidan siempre en vez de
# quedar guardados días en el navegador: si cambia una foto, se ve el cambio.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

app.register_blueprint(clientes_bp)
app.register_blueprint(productos_bp)
app.register_blueprint(inventario_bp)
app.register_blueprint(saldo_bp)
app.register_blueprint(ventas_bp)
app.register_blueprint(web_bp)
app.register_blueprint(pedidos_bp)
app.register_blueprint(visitas_bp)
app.register_blueprint(push_bp)
app.register_blueprint(recordatorios_bp)

# ── Un solo link público ───────────────────────────────────────────
# Dos servicios de Railway corren este mismo código. Se separan por variables:
#   Servicio de la TIENDA  →  SOLO_TIENDA=1
#       solo responde la tienda y sus APIs públicas; el admin da 404.
#   Servicio del ADMIN     →  TIENDA_URL=https://miecoltura.up.railway.app
#       si alguien abre la raíz "/" lo manda a la tienda (un solo link público).
RUTAS_PUBLICAS = (
    "/api/productos", "/api/cliente/", "/api/pedidos", "/api/visitas/",
    "/api/push/public-key", "/api/push/subscribe",
    "/api/recordatorios/marcar-leido/", "/api/recordatorios/pendiente",
    "/static/", "/sw.js", "/favicon.ico",
)

@app.before_request
def separar_tienda_y_admin():
    ruta = request.path
    if os.environ.get("SOLO_TIENDA") == "1":
        if ruta != "/" and not ruta.startswith(RUTAS_PUBLICAS):
            abort(404)
        return None
    tienda = os.environ.get("TIENDA_URL", "").rstrip("/")
    if tienda and ruta == "/":
        return redirect(tienda + "/", code=302)
    return None


# ── Nunca servir una versión vieja del sitio tras un deploy ──────────
@app.after_request
def sin_cache(resp):
    tipo = resp.headers.get("Content-Type", "")
    if tipo.startswith("text/html") or tipo.startswith("application/json") or request.path == "/sw.js":
        # Páginas, datos y service worker: siempre pedir la versión actual
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    elif request.path.startswith("/static/"):
        # Fotos, íconos, manifest: se guardan, pero el navegador pregunta antes de usarlas
        resp.headers["Cache-Control"] = "no-cache"
    return resp

@app.route("/sw.js")
def service_worker():
    """
    Sirve el mismo archivo static/js/sw.js pero desde la raíz del sitio.
    Necesario porque un service worker solo puede controlar (scope) carpetas
    iguales o debajo de donde vive su archivo — si se sirve desde /static/js/,
    nunca podría controlar la tienda pública que vive en /.
    """
    response = send_from_directory(
        os.path.join(app.static_folder, "js"), "sw.js", mimetype="application/javascript"
    )
    response.headers["Service-Worker-Allowed"] = "/"
    return response


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
