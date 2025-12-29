import os
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse

def obtener_conexion():
    url = os.getenv("DATABASE_URL")

    parsed = urlparse(url)

    return psycopg2.connect(
        dbname=parsed.path[1:],
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port,
        sslmode="require",
        connect_timeout=5  # evita que Gunicorn mate el worker
    )
