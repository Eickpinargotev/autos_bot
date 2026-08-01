"""Periodos de facturación: el "reset" y su reversión.

Lo que se protege: cerrar un periodo NO puede perder información, y un cierre
por error debe poder deshacerse.
"""

import pytest

from src.db import pool
from src.services import facturacion


def _evento(real: int, cliente: int, categoria: str = "llm", periodo_id: int | None = None):
    periodo_id = periodo_id or facturacion.periodo_abierto()["id"]
    pool.ejecutar(
        """
        INSERT INTO uso_eventos (periodo_id, client_id, canal, categoria, origen,
                                 mensajes, costo_real_microusd, costo_cliente_microusd)
        VALUES (%s, '506', 'whatsapp', %s, 'agente', 1, %s, %s)
        """,
        (periodo_id, categoria, real, cliente),
    )


def test_los_totales_suman_lo_registrado():
    _evento(1000, 1600)
    _evento(500, 800)

    totales = facturacion.totales_de_periodo(facturacion.periodo_abierto()["id"])

    assert totales["real_microusd"] == 1500
    assert totales["cliente_microusd"] == 2400
    assert totales["margen_microusd"] == 900


def test_cerrar_congela_los_totales_y_deja_el_nuevo_en_cero():
    _evento(1000, 1600)
    anterior = facturacion.periodo_abierto()["id"]

    nuevo = facturacion.cerrar_periodo("admin", "cierre de julio")

    assert nuevo["id"] != anterior
    cerrado = pool.consultar_uno("SELECT * FROM periodos_facturacion WHERE id = %s", (anterior,))
    assert cerrado["cerrado_en"] is not None
    assert cerrado["cerrado_por"] == "admin"
    assert cerrado["total_cliente_microusd"] == 1600      # congelado, no recalculado
    assert facturacion.totales_de_periodo(nuevo["id"])["cliente_microusd"] == 0


def test_cerrar_no_borra_los_eventos():
    """El cliente ve cero, pero el dato sigue ahí para auditarlo."""
    _evento(1000, 1600)
    facturacion.cerrar_periodo("admin")

    fila = pool.consultar_uno("SELECT COUNT(*) AS total FROM uso_eventos")
    assert fila["total"] == 1


def test_reincorporar_devuelve_el_consumo_al_periodo_actual():
    _evento(1000, 1600)
    anterior = facturacion.periodo_abierto()["id"]
    nuevo = facturacion.cerrar_periodo("admin")
    _evento(200, 320)

    assert facturacion.totales_de_periodo(nuevo["id"])["cliente_microusd"] == 320
    assert facturacion.reincorporar_periodo(anterior) is True
    assert facturacion.totales_de_periodo(nuevo["id"])["cliente_microusd"] == 1920


def test_no_se_puede_reincorporar_dos_veces():
    anterior = facturacion.periodo_abierto()["id"]
    facturacion.cerrar_periodo("admin")

    assert facturacion.reincorporar_periodo(anterior) is True
    assert facturacion.reincorporar_periodo(anterior) is False


def test_no_se_puede_reincorporar_el_periodo_abierto():
    abierto = facturacion.periodo_abierto()["id"]
    assert facturacion.reincorporar_periodo(abierto) is False


def test_solo_puede_haber_un_periodo_abierto():
    """Lo garantiza un índice único parcial: dos abiertos partirían la factura en dos."""
    import psycopg2

    with pytest.raises(psycopg2.Error):
        pool.ejecutar("INSERT INTO periodos_facturacion (nota) VALUES ('segundo abierto')")


def test_el_desglose_separa_llm_de_codigo():
    _evento(1000, 1600, "llm")
    _evento(0, 2000, "codigo")

    por_categoria = {c["categoria"]: c for c in facturacion.desglose_por_categoria(
        facturacion.periodo_abierto()["id"]
    )}

    assert por_categoria["llm"]["real_microusd"] == 1000
    assert por_categoria["codigo"]["real_microusd"] == 0     # no le cuesta nada al proveedor
    assert por_categoria["codigo"]["cliente_microusd"] == 2000


def test_una_tarifa_nueva_no_reescribe_lo_ya_facturado():
    _evento(1000, 1600)
    antes = facturacion.totales_de_periodo(facturacion.periodo_abierto()["id"])["cliente_microusd"]

    facturacion.crear_tarifa(
        {
            "modelo": "gpt-5.4-mini",
            "precio_input_usd_1m": 0.75,
            "precio_cached_input_usd_1m": 0.075,
            "precio_output_usd_1m": 4.50,
            "multiplicador_llm": 3.0,          # margen muy distinto
            "precio_mensaje_codigo_microusd": 9999,
        },
        "admin",
    )

    despues = facturacion.totales_de_periodo(facturacion.periodo_abierto()["id"])["cliente_microusd"]
    assert despues == antes == 1600
    assert float(facturacion.tarifa_vigente()["multiplicador_llm"]) == 3.0
