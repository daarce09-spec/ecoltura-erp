# -*- coding: utf-8 -*-
from flask import Blueprint, render_template
from db.conexion import obtener_conexion

saldo_bp = Blueprint("saldo_bp", __name__)

@saldo_bp.route("/inventario/saldo")
def inventario_saldo():
    with obtener_conexion() as conn:
        cursor = conn.cursor()

        # --- 1. Consumo promedio (solo F en últimas 4 semanas) ---
        cursor.execute("""
            SELECT 
                producto_id, 
                COALESCE(SUM(cantidad), 0) / 4 AS promedio_semanal
            FROM inventario_movimientos
            WHERE tipo = 'F'
              AND fecha >= CURRENT_DATE - INTERVAL '28 days'
            GROUP BY producto_id
        """)
        consumo_map = {row[0]: int(row[1]) for row in cursor.fetchall()}

        # --- 2. Entradas reales (C), Salidas reales (F - A) y Stock final ---
        cursor.execute("""
            SELECT 
                p.id,
                p.nombre,
                p.unidad,

                -- ENTRADAS reales = solo C
                (
                    SELECT COALESCE(SUM(cantidad), 0)
                    FROM inventario_movimientos
                    WHERE producto_id = p.id AND tipo = 'C'
                ) AS entradas,

                -- SALIDAS reales = F - A
                (
                    SELECT COALESCE(SUM(
                        CASE 
                            WHEN tipo = 'F' THEN cantidad
                            WHEN tipo = 'A' THEN -cantidad
                            ELSE 0
                        END
                    ), 0)
                    FROM inventario_movimientos
                    WHERE producto_id = p.id
                ) AS salidas,

                -- STOCK = entradas - salidas
                (
                    (SELECT COALESCE(SUM(cantidad), 0)
                     FROM inventario_movimientos
                     WHERE producto_id = p.id AND tipo = 'C')
                    -
                    (SELECT COALESCE(SUM(
                        CASE 
                            WHEN tipo = 'F' THEN cantidad
                            WHEN tipo = 'A' THEN -cantidad
                            ELSE 0
                        END
                    ), 0)
                     FROM inventario_movimientos
                     WHERE producto_id = p.id)
                ) AS stock

            FROM productos p
            ORDER BY p.nombre ASC;
        """)
        productos_raw = cursor.fetchall()

    # --- 3. Procesamiento en Python ---
    datos_final = []
    bajo_inventario = 0
    suma_stock_global = 0

    for row in productos_raw:
        prod_id, nombre, unidad, entradas, salidas, stock = row

        entradas = int(entradas)
        salidas = int(salidas)
        stock = int(stock)

        promedio = consumo_map.get(prod_id, 0)
        minimo_sugerido = int(promedio * 1.5)

        if stock <= minimo_sugerido:
            bajo_inventario += 1
        
        suma_stock_global += stock

        datos_final.append({
            "nombre": nombre,
            "unidad": unidad,
            "entradas": entradas,
            "salidas": salidas,
            "stock": stock,
            "saldo": stock,  # ← Alias para que el HTML viejo no falle
            "minimo": minimo_sugerido
        })

    # --- 4. Renderizado ---
    return render_template(
        "inventario_saldo.html",
        datos=datos_final,
        total_productos=len(datos_final),
        total_stock=int(suma_stock_global),
        bajos=bajo_inventario
    )
