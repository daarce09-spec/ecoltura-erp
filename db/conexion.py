import psycopg2
import os
import urllib.parse as up

def obtener_conexion():
    url = os.getenv("DATABASE_URL")

    # Parsear URL para extraer componentes
    up.uses_netloc.append("postgres")
    parsed = up.urlparse(url)

    return psycopg2.connect(
        database=parsed.path[1:],    # sin "/"
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port,
        sslmode="require"           # obligatorio en Railway
    )
