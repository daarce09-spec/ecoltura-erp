# -*- coding: utf-8 -*-
from flask import Flask, render_template, redirect, url_for, jsonify, send_from_directory, request, abort, session
from datetime import datetime, timedelta, timezone
import os
import hmac
import time

# ── Módulos del app administrativo ──
from modulos.clientes   import clientes_bp
from modulos.productos  import productos_bp
from modulos.inventario import inventario_bp
from modulos.proveedores import proveedores_bp
from modulos.saldo      import saldo_bp

# ── Blueprint de ventas ──
from modulos.ventas_menu import ventas_bp
import modulos.ventas_registrar
import modulos.ventas_anular
import modulos.ventas_ticket
import modulos.ventas_historial

# ── Web pública ──
from modulos.tienda.web_publica import web_bp

# ── Bandejas ──
from modulos.pedidos import pedidos_bp
from modulos.visitas import visitas_bp

# ── Notificaciones push ──
from modulos.push import push_bp

# ── Recordatorios de compra ──
from modulos.recordatorios import recordatorios_bp

# ── Contabilidad ──
from modulos.contabilidad import contabilidad_bp
from modulos.reparto import reparto_bp

app = Flask(__name__, template_folder="templates")
# La llave que firma las sesiones se toma de la variable SECRET_KEY de Railway.
# (Si no existe se usa la antigua, para que nada se rompa mientras se configura.)
app.secret_key = os.environ.get("SECRET_KEY") or "ecoltura_secret_key_2025"
# La sesión del Admin queda guardada 90 días en el dispositivo.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=90)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("RAILWAY_ENVIRONMENT"))

# Los archivos estáticos (fotos, manifest, etc.) se revalidan siempre en vez de
# quedar guardados días en el navegador: si cambia una foto, se ve el cambio.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

app.register_blueprint(clientes_bp)
app.register_blueprint(productos_bp)
app.register_blueprint(inventario_bp)
app.register_blueprint(proveedores_bp)
app.register_blueprint(saldo_bp)
app.register_blueprint(ventas_bp)
app.register_blueprint(web_bp)
app.register_blueprint(pedidos_bp)
app.register_blueprint(visitas_bp)
app.register_blueprint(push_bp)
app.register_blueprint(recordatorios_bp)
app.register_blueprint(contabilidad_bp)
app.register_blueprint(reparto_bp)

# ── Un solo link público ───────────────────────────────────────────
# Dos servicios de Railway corren este mismo código. Se separan por variables:
#   Servicio de la TIENDA  →  SOLO_TIENDA=1
#       solo responde la tienda y sus APIs públicas; el admin da 404.
#   Servicio del ADMIN     →  TIENDA_URL=https://miecoltura.up.railway.app
#       si alguien abre la raíz "/" lo manda a la tienda (un solo link público).
RUTAS_PUBLICAS = (
    "/api/productos", "/api/version", "/api/cliente/", "/api/pedidos", "/api/pedido/", "/pedido/", "/api/visitas/",
    "/api/push/public-key", "/api/push/subscribe",
    "/api/recordatorios/marcar-leido/", "/api/recordatorios/pendiente",
    "/static/", "/sw.js", "/favicon.ico",
)

# ── Dominio principal de la tienda ────────────────────────────────
# Si en Railway existe la variable DOMINIO_PRINCIPAL (ej. https://www.ecoltura.com),
# toda página que se abra con otra dirección (miecoltura.up.railway.app o ecoltura.com
# sin www) se manda a esa. Sin la variable no hace nada, así se activa cuando se quiera.
# No se redirigen las APIs, los archivos ni el service worker: las apps ya instaladas
# con la dirección vieja siguen funcionando y recibiendo notificaciones.
_NO_REDIRIGIR = ("/api/", "/static/", "/sw.js", "/favicon.ico")

@app.before_request
def ir_al_dominio_principal():
    principal = os.environ.get("DOMINIO_PRINCIPAL", "").rstrip("/")
    if not principal or request.method != "GET" or request.path.startswith(_NO_REDIRIGIR):
        return None
    host_principal = principal.split("://", 1)[-1].split("/", 1)[0].lower()
    if request.host.lower() == host_principal:
        return None
    destino = principal + request.full_path.rstrip("?")
    return redirect(destino, code=302)


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


# ── Inicio de sesión del Admin ─────────────────────────────────────
# Un solo usuario y contraseña para el equipo. Se activa cuando en Railway existe
# la variable ADMIN_PASSWORD (y opcionalmente ADMIN_USER). Sin esa variable el
# Admin queda abierto como antes, así se puede desplegar sin bloquear a nadie.
# La tienda pública (SOLO_TIENDA=1) nunca pide inicio de sesión.
_INTENTOS = {}            # ip -> [fallos, hora_del_bloqueo]
_MAX_FALLOS = 5
_BLOQUEO_SEG = 300

def _ip():
    return (request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]).strip()

def _bloqueado(ip):
    reg = _INTENTOS.get(ip)
    if reg and reg[0] >= _MAX_FALLOS:
        if time.time() - reg[1] < _BLOQUEO_SEG:
            return int(_BLOQUEO_SEG - (time.time() - reg[1])) // 60 + 1
        _INTENTOS.pop(ip, None)
    return 0

@app.before_request
def exigir_login():
    clave = os.environ.get("ADMIN_PASSWORD")
    if not clave or os.environ.get("SOLO_TIENDA") == "1":
        return None
    ruta = request.path
    if ruta == "/" or ruta == "/login" or ruta.startswith(RUTAS_PUBLICAS):
        return None
    if session.get("admin_ok"):
        return None
    if ruta.startswith("/api/") or "/api/" in ruta or request.method != "GET":
        return jsonify({"error": "No autorizado"}), 401
    return redirect(url_for("login", next=ruta))

@app.route("/login", methods=["GET", "POST"])
def login():
    clave = os.environ.get("ADMIN_PASSWORD")
    if os.environ.get("SOLO_TIENDA") == "1":
        abort(404)
    if not clave:
        return redirect("/menu")
    destino = request.values.get("next", "/menu")
    if not destino.startswith("/") or destino.startswith("//"):
        destino = "/menu"
    error = None
    if request.method == "POST":
        ip = _ip()
        espera = _bloqueado(ip)
        if espera:
            error = f"Demasiados intentos. Probá de nuevo en {espera} min."
        else:
            usuario = os.environ.get("ADMIN_USER", "ecoltura")
            ok_u = hmac.compare_digest(request.form.get("usuario", "").strip().lower().encode(), usuario.lower().encode())
            ok_p = hmac.compare_digest(request.form.get("clave", "").encode(), clave.encode())
            if ok_u and ok_p:
                _INTENTOS.pop(ip, None)
                session.clear()
                session["admin_ok"] = True
                session.permanent = True
                return redirect(destino)
            reg = _INTENTOS.setdefault(ip, [0, 0])
            reg[0] += 1
            reg[1] = time.time()
            error = "Usuario o contraseña incorrectos."
    return render_template("login.html", error=error, next=destino)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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


# ── Menú del Admin ─────────────────────────────────────────────────
# Cinco tarjetas principales; las que agrupan varias pantallas abren un
# submenú con el mismo diseño (/menu/clientes, /menu/inventario, /menu/ventas).
MENU_PRINCIPAL = [
    {"href": "/menu/clientes",   "icono": "bi-people",         "nombre": "Clientes",     "tag": "Gestión y avisos"},
    {"href": "/menu/inventario", "icono": "bi-box-seam",       "nombre": "Inventario",   "tag": "Productos y stock"},
    {"href": "/menu/ventas",     "icono": "bi-receipt-cutoff", "nombre": "Ventas",       "tag": "Pedidos y facturación", "badge": "badgeVentas"},
    {"href": "/reparto",         "icono": "bi-truck",          "nombre": "Reparto",      "tag": "Entregas y cierre"},
    {"href": "/contabilidad",    "icono": "bi-journal-text",   "nombre": "Contabilidad", "tag": "Resultados"},
]
MENU_GRUPOS = {
    "clientes": ("Clientes", [
        {"href": "/clientes",      "icono": "bi-people",  "nombre": "Clientes",      "tag": "Gestión"},
        {"href": "/recordatorios", "icono": "bi-bell",    "nombre": "Recordatorios", "tag": "Notificaciones"},
    ]),
    "inventario": ("Inventario", [
        {"href": "/productos",              "icono": "bi-basket3",        "nombre": "Productos",          "tag": "Catálogo"},
        {"href": "/inventario/proveedores", "icono": "bi-shop",           "nombre": "Proveedores",        "tag": "Compras"},
        {"href": "/inventario/registrar",   "icono": "bi-journal-plus",   "nombre": "Registrar cosecha",  "tag": "Semanal"},
        {"href": "/inventario/saldo",       "icono": "bi-clipboard-data", "nombre": "Saldo actual",       "tag": "Stock"},
    ]),
    "ventas": ("Ventas", [
        {"href": "/admin/pedidos", "icono": "bi-bag-check",      "nombre": "Pedidos Web", "tag": "Bandeja",     "badge": "badgePedidos"},
        {"href": "/ventas",        "icono": "bi-receipt-cutoff", "nombre": "Ventas",      "tag": "Facturación"},
        {"href": "/admin/visitas", "icono": "bi-calendar2-check","nombre": "Visitas",     "tag": "Finca",       "badge": "badgeVisitas"},
    ]),
}

@app.route("/menu")
def menu():
    return render_template("menu.html", modulos=MENU_PRINCIPAL, titulo=None)

@app.route("/menu/<grupo>")
def menu_grupo(grupo):
    if grupo not in MENU_GRUPOS:
        abort(404)
    titulo, modulos = MENU_GRUPOS[grupo]
    return render_template("menu.html", modulos=modulos, titulo=titulo)

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

# ── Versión visible: número consecutivo por cada deploy ──────────────
# Cada deploy trae un commit distinto (Railway lo da en RAILWAY_GIT_COMMIT_SHA).
# La primera vez que se ve un commit se guarda en la tabla `versiones` y la base
# le asigna el siguiente número (1, 2, 3...). Los dos servicios comparten base,
# así que ambos muestran el mismo número y la misma fecha. Sin intervención manual.
_CR = timezone(timedelta(hours=-6))
_COMMIT = (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "local")[:7]

def _registrar_version():
    try:
        from db.conexion import obtener_conexion
        conn = obtener_conexion()
        try:
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS versiones (
                               numero SERIAL PRIMARY KEY,
                               commit_sha VARCHAR(40) UNIQUE NOT NULL,
                               creado_en TIMESTAMPTZ DEFAULT NOW())""")
            cur.execute("INSERT INTO versiones (commit_sha) VALUES (%s) ON CONFLICT (commit_sha) DO NOTHING", (_COMMIT,))
            cur.execute("SELECT numero, creado_en FROM versiones WHERE commit_sha = %s", (_COMMIT,))
            numero, creado = cur.fetchone()
            conn.commit()
            return str(numero), creado.astimezone(_CR).strftime("%d/%m/%Y %H:%M")
        finally:
            conn.close()
    except Exception as e:
        print("No se pudo registrar la versión:", e)
        return "?", datetime.now(_CR).strftime("%d/%m/%Y %H:%M")

_NUM, _FECHA = _registrar_version()
# Deploy 1 → 1.01, deploy 2 → 1.02 ... deploy 99 → 1.99, deploy 100 → 1.100
APP_RELEASE = f"1.{int(_NUM):02d}" if _NUM.isdigit() else "?"
APP_VERSION = f"{APP_RELEASE} · {_COMMIT} · {_FECHA}"
APP_VERSION_ID = f"{_NUM}-{_COMMIT}"

@app.route("/api/version")
def api_version():
    return jsonify({"id": APP_VERSION_ID, "version": APP_VERSION})

@app.context_processor
def inject_now():
    return {"now": datetime.now(), "APP_VERSION": APP_VERSION, "APP_VERSION_ID": APP_VERSION_ID, "APP_RELEASE": APP_RELEASE, "LOGIN_ACTIVO": bool(os.environ.get("ADMIN_PASSWORD")) and os.environ.get("SOLO_TIENDA") != "1", "ENTORNO_PRUEBAS": os.environ.get("ENTORNO", "").lower() == "pruebas"}

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
