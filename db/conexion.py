import os
import threading
import time
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras

# ─────────────────────────────────────────────────────────────────────
#  Conexiones reutilizables (pool)
#
#  Antes, cada pantalla abría una conexión nueva a la base (con su saludo
#  cifrado SSL), y eso hacía que cambiar de pantalla tardara. Ahora las
#  conexiones se guardan abiertas y se reutilizan.
#
#  El resto del código no cambia: se sigue usando igual
#      with obtener_conexion() as conn: ...
#      conn = obtener_conexion(); ...; conn.close()
#  Cuando la función termina (o se llama close()), la conexión vuelve al pool
#  en vez de cerrarse; si quedó una transacción a medias, se deshace.
# ─────────────────────────────────────────────────────────────────────
_MAX_LIBRES = int(os.getenv("DB_POOL_MAX", "6"))   # conexiones quietas que se guardan
_PING_SI_INACTIVA_SEG = 60      # si estuvo quieta más de 1 min, se prueba antes de usarla

_libres = []                    # [(conexión, hora en que volvió)]
_candado = threading.Lock()


def _parametros():
    parsed = urlparse(os.getenv("DATABASE_URL"))
    return dict(
        dbname=parsed.path[1:],
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port,
        sslmode="require",
        connect_timeout=5,  # evita que Gunicorn mate el worker
        options="-c timezone=America/Costa_Rica",
        # Mantiene viva la conexión aunque esté quieta un rato
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
    )


class _Conexion:
    """Envuelve la conexión real: se usa igual que una conexión de psycopg2,
    pero al cerrarla (o al dejar de usarla) vuelve al pool."""

    def __init__(self, real, del_pool):
        self._real = real
        self._del_pool = del_pool
        self._devuelta = False

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, tipo, valor, traza):
        # Igual que psycopg2: confirma si todo salió bien, deshace si hubo error.
        return self._real.__exit__(tipo, valor, traza)

    def close(self):
        self._devolver()

    @property
    def closed(self):
        return self._devuelta or self._real.closed

    def _devolver(self):
        if self._devuelta:
            return
        self._devuelta = True
        real = self._real
        try:
            if self._del_pool and not real.closed:
                if real.info.transaction_status != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
                    real.rollback()
                with _candado:
                    if len(_libres) < _MAX_LIBRES:
                        _libres.append((real, time.time()))
                        return
            real.close()
        except Exception:
            try:
                real.close()
            except Exception:
                pass

    def __del__(self):
        try:
            self._devolver()
        except Exception:
            pass


def _sana(conn, desde):
    """True si la conexión sirve. Solo se prueba si estuvo quieta un rato."""
    if conn.closed:
        return False
    if time.time() - desde < _PING_SI_INACTIVA_SEG:
        return True
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.rollback()
        return True
    except Exception:
        return False


def obtener_conexion():
    while True:
        with _candado:
            par = _libres.pop() if _libres else None
        if par is None:
            break
        real, desde = par
        if _sana(real, desde):
            return _Conexion(real, del_pool=True)
        try:
            real.close()
        except Exception:
            pass
    return _Conexion(psycopg2.connect(**_parametros()), del_pool=True)
