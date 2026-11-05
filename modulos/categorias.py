# -*- coding: utf-8 -*-
"""
Categorías de productos (tabla propia).

Antes la categoría era solo un texto suelto en cada producto, así que
"Fruta" y "Frutas" (o un espacio al final) creaban pestañas duplicadas en la tienda.
Ahora hay una lista oficial de categorías con su orden, que se administra
desde el Admin (Productos > Categorías) y que la tienda usa para ordenar.

Este módulo lo usan tanto el Admin (productos.py) como la tienda (web_publica.py),
así que la tabla se crea sola donde sea que arranque primero.
"""
from db.conexion import obtener_conexion

# Orden que la tienda usaba antes (fijo en el código); sirve para sembrar la tabla
ORDEN_BASE = ["Verduras", "Frutas", "Hierbas", "Lácteos", "Huevos", "Miel", "Hongos"]


def limpiar_categoria(valor):
    """Quita espacios de más (al inicio, al final y repetidos)."""
    return " ".join(str(valor or "").split())


def asegurar_categorias():
    """Crea la tabla si no existe y, si está vacía, la llena con las categorías que ya usan los productos."""
    try:
        with obtener_conexion() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS categorias (
                    id     SERIAL PRIMARY KEY,
                    nombre VARCHAR(80) NOT NULL,
                    orden  INTEGER NOT NULL DEFAULT 999
                )
            """)
            cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_categorias_nombre
                           ON categorias (LOWER(nombre))""")
            cur.execute("SELECT COUNT(*) FROM categorias")
            if cur.fetchone()[0] == 0:
                cur.execute("""SELECT DISTINCT REGEXP_REPLACE(TRIM(categoria), '\\s+', ' ', 'g')
                               FROM productos
                               WHERE categoria IS NOT NULL AND TRIM(categoria) <> ''""")
                usadas = sorted({r[0] for r in cur.fetchall() if r[0]}, key=str.lower)
                base = {n.lower(): i for i, n in enumerate(ORDEN_BASE)}
                usadas.sort(key=lambda n: (base.get(n.lower(), 100), n.lower()))
                for i, nombre in enumerate(usadas, start=1):
                    cur.execute("""INSERT INTO categorias (nombre, orden) VALUES (%s, %s)
                                   ON CONFLICT DO NOTHING""", (nombre, i))
            conn.commit()
    except Exception as ex:
        print("No se pudo asegurar la tabla de categorías:", ex)
