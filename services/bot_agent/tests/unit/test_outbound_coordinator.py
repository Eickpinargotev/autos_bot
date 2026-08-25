"""Exclusión distribuida y prioridad de las salidas del negocio."""

import threading
import time
from unittest.mock import patch

import pytest

from src.application.project_context import ambito_proyecto
from src.infrastructure.channels import outbound_coordinator as coordinador
from src.infrastructure.channels.senders import ChannelSenderRegistry


def test_un_mismo_negocio_nunca_ejecuta_dos_envios_simultaneos():
    barrera = threading.Barrier(3)
    estado = {"activos": 0, "maximo": 0}
    mutex = threading.Lock()

    def enviar():
        with ambito_proyecto(1):
            barrera.wait()
            with coordinador.turno_de_salida("whatsapp"):
                with mutex:
                    estado["activos"] += 1
                    estado["maximo"] = max(estado["maximo"], estado["activos"])
                time.sleep(0.04)
                with mutex:
                    estado["activos"] -= 1

    with patch.object(coordinador, "_PAUSA_SONDEO_SEGUNDOS", 0.001):
        hilos = [threading.Thread(target=enviar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        barrera.wait()
        for hilo in hilos:
            hilo.join(timeout=2)

    assert estado["maximo"] == 1
    assert all(not hilo.is_alive() for hilo in hilos)


def test_negocios_distintos_pueden_enviar_en_paralelo():
    dentro = threading.Barrier(2)
    errores = []

    def enviar(proyecto_id):
        try:
            with ambito_proyecto(proyecto_id):
                with coordinador.turno_de_salida("whatsapp"):
                    dentro.wait(timeout=1)
        except Exception as exc:  # pragma: no cover - se informa abajo
            errores.append(exc)

    hilos = [threading.Thread(target=enviar, args=(proyecto,)) for proyecto in (1, 2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=2)

    assert not errores
    assert all(not hilo.is_alive() for hilo in hilos)


def test_un_recordatorio_cede_si_hay_una_respuesta_interactiva_esperando():
    soltar = threading.Event()
    candado_tomado = threading.Event()
    interactiva_termino = threading.Event()

    def ocupante():
        with ambito_proyecto(1):
            with coordinador.turno_de_salida("whatsapp"):
                candado_tomado.set()
                soltar.wait(timeout=2)

    def interactiva():
        with ambito_proyecto(1):
            with coordinador.turno_de_salida(
                "whatsapp", coordinador.PrioridadSalida.INTERACTIVA
            ):
                interactiva_termino.set()

    with patch.object(coordinador, "_PAUSA_SONDEO_SEGUNDOS", 0.001):
        primero = threading.Thread(target=ocupante)
        primero.start()
        assert candado_tomado.wait(timeout=1)
        prioritaria = threading.Thread(target=interactiva)
        prioritaria.start()

        limite = time.monotonic() + 1
        while not coordinador.hay_interactiva_esperando("whatsapp"):
            assert time.monotonic() < limite
            time.sleep(0.001)

        with pytest.raises(coordinador.SalidaOcupada):
            with coordinador.turno_de_salida(
                "whatsapp", coordinador.PrioridadSalida.RECORDATORIO
            ):
                pass

        soltar.set()
        primero.join(timeout=1)
        prioritaria.join(timeout=1)

    assert interactiva_termino.is_set()


def test_solo_el_dueno_puede_liberar_la_concesion():
    lock_key, _ = coordinador._claves("whatsapp")
    with coordinador.turno_de_salida("whatsapp"):
        coordinador.redis_client.set(lock_key, "otro-proceso", ex=30)

    assert coordinador.redis_client.get(lock_key) == "otro-proceso"


def test_la_concesion_se_renueva_y_un_candado_huerfano_expira():
    lock_key, _ = coordinador._claves("whatsapp")
    with patch.object(coordinador, "_CONCESION_SEGUNDOS", 1), patch.object(
        coordinador, "_RENOVACION_SEGUNDOS", 0.1
    ):
        with coordinador.turno_de_salida("whatsapp"):
            time.sleep(1.15)
            assert coordinador.redis_client.ttl(lock_key) > 0
            with pytest.raises(coordinador.SalidaOcupada):
                with coordinador.turno_de_salida(
                    "whatsapp", coordinador.PrioridadSalida.RECORDATORIO
                ):
                    pass

        coordinador.redis_client.set(lock_key, "worker-caido", ex=1)
        time.sleep(1.05)
        with coordinador.turno_de_salida(
            "whatsapp", coordinador.PrioridadSalida.RECORDATORIO
        ):
            pass


def test_si_redis_no_puede_coordinar_no_se_llama_al_proveedor():
    sender = ChannelSenderRegistry.get("whatsapp")
    with patch(
        "src.infrastructure.channels.senders.turno_de_salida",
        side_effect=coordinador.CoordinacionSalidaNoDisponible("redis caído"),
    ), patch.object(sender, "send_message_sync") as envio_fisico:
        with pytest.raises(coordinador.CoordinacionSalidaNoDisponible):
            ChannelSenderRegistry.send("whatsapp", "506", "hola")

    envio_fisico.assert_not_called()
