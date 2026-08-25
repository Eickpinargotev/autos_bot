"""Cadencia compartida de los recordatorios acumulados por la noche."""

from unittest.mock import patch

from src.application import drenaje_recordatorios as drenaje
from src.application.project_context import ambito_proyecto


def test_el_primero_sale_y_el_siguiente_espera_entre_cinco_y_diez_minutos():
    with patch.object(drenaje.random, "randint", return_value=420_000) as azar:
        primero = drenaje.solicitar_turno("whatsapp", True, ahora_epoch=1000)
        assert primero.concedido
        drenaje.confirmar_envio("whatsapp", primero, ahora_epoch=1000)
        segundo = drenaje.solicitar_turno("whatsapp", True, ahora_epoch=1000)
        assert segundo.espera_segundos == 420

    azar.assert_called_with(300_000, 600_000)


def test_al_cumplirse_el_turno_se_abre_el_siguiente_intervalo():
    with patch.object(drenaje.random, "randint", side_effect=[300_000, 600_000]):
        primero = drenaje.solicitar_turno("whatsapp", None, ahora_epoch=1000)
        drenaje.confirmar_envio("whatsapp", primero, ahora_epoch=1000)
        assert drenaje.solicitar_turno(
            "whatsapp", None, ahora_epoch=1299
        ).espera_segundos == 1
        segundo = drenaje.solicitar_turno("whatsapp", None, ahora_epoch=1300)
        assert segundo.concedido
        drenaje.confirmar_envio("whatsapp", segundo, ahora_epoch=1300)


def test_recordatorios_diurnos_y_negocios_distintos_no_comparten_el_reloj():
    with patch.object(drenaje.random, "randint", return_value=300_000):
        assert drenaje.solicitar_turno("whatsapp", True, ahora_epoch=1000).concedido
        assert drenaje.solicitar_turno("whatsapp", False, ahora_epoch=1000).concedido
        with ambito_proyecto(2):
            assert drenaje.solicitar_turno("whatsapp", True, ahora_epoch=1000).concedido


def test_una_reserva_sin_confirmar_no_inicia_el_reloj_y_se_puede_liberar():
    primero = drenaje.solicitar_turno("whatsapp", True, ahora_epoch=1000)
    assert drenaje.solicitar_turno(
        "whatsapp", True, ahora_epoch=1000
    ).espera_segundos == 2

    drenaje.liberar_turno("whatsapp", primero)

    assert drenaje.solicitar_turno("whatsapp", True, ahora_epoch=1000).concedido
