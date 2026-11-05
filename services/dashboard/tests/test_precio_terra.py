"""Precio del auxiliar optativo y conservación de ajustes administrativos."""

from decimal import Decimal
from pathlib import Path

from src.db import pool


def test_migracion_registra_precio_standard_de_terra():
    fila = pool.consultar_uno("SELECT * FROM precios_modelo WHERE modelo = 'gpt-6.1-terra'")
    assert fila["precio_input_usd_1m"] == Decimal("1.00")
    assert fila["precio_cached_input_usd_1m"] == Decimal("0.05")
    assert fila["precio_output_usd_1m"] == Decimal("5.00")


def test_migracion_no_sobrescribe_precio_existente():
    sql = Path("src/db/migrations/040_precio_auxiliar_terra.sql").read_text()
    with pool.conexion() as conn:
        # Esta prueba usa una transacción que siempre se revierte: el precio
        # estándar queda intacto para los demás tests de la base aislada.
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE precios_modelo SET precio_input_usd_1m = 7 WHERE modelo = 'gpt-6.1-terra'")
                cur.execute(sql)
                cur.execute("SELECT precio_input_usd_1m FROM precios_modelo WHERE modelo = 'gpt-6.1-terra'")
                assert cur.fetchone()[0] == Decimal("7")
        finally:
            conn.rollback()
