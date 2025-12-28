import psycopg2
import os

def obtener_conexion():
    return psycopg2.connect(os.getenv("DATABASE_URL"))
