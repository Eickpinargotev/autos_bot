"""Exclusión distribuida para las salidas de una cuenta del negocio.

Celery procesa tareas en varios hilos y el webhook vive en otro proceso. Un
candado de Python no impediría que ambos llamen al proveedor al mismo tiempo;
por eso la concesión vive en Redis y se renueva mientras dura el envío.
"""

from contextlib import contextmanager
from enum import Enum
import secrets
import threading
import time

from redis.exceptions import WatchError

from src.application.buffer_service import redis_client
from src.application.project_context import proyecto_actual
from src.domain.entities import Channel


class PrioridadSalida(str, Enum):
    INTERACTIVA = "interactiva"
    NORMAL = "normal"
    RECORDATORIO = "recordatorio"


class SalidaOcupada(RuntimeError):
    """La cuenta está atendiendo otra salida y la tarea debe reintentarse."""

    def __init__(self, espera_segundos: float = 2.0):
        super().__init__("La cuenta de salida está ocupada")
        self.espera_segundos = max(1.0, float(espera_segundos))


class CoordinacionSalidaNoDisponible(RuntimeError):
    """Redis no pudo garantizar la exclusión; no es seguro enviar."""


_CONCESION_SEGUNDOS = 120
_RENOVACION_SEGUNDOS = 30
_ESPERA_MAXIMA_SEGUNDOS = 115
_PAUSA_SONDEO_SEGUNDOS = 0.1


def _canal(channel: Channel | str) -> str:
    return channel.value if isinstance(channel, Channel) else Channel(channel).value


def _claves(channel: Channel | str) -> tuple[str, str]:
    base = f"salida:p{proyecto_actual()}:{_canal(channel)}"
    return f"{base}:candado", f"{base}:interactivas_esperando"


def hay_interactiva_esperando(channel: Channel | str) -> bool:
    """Permite que una tarea lenta ceda antes de intentar enviar."""
    _, espera_key = _claves(channel)
    try:
        return int(redis_client.get(espera_key) or 0) > 0
    except Exception as exc:
        raise CoordinacionSalidaNoDisponible(str(exc)) from exc


def _sumar_interactiva(espera_key: str, delta: int) -> None:
    try:
        while True:
            with redis_client.pipeline() as pipe:
                try:
                    pipe.watch(espera_key)
                    valor = int(pipe.get(espera_key) or 0) + delta
                    pipe.multi()
                    if valor <= 0:
                        pipe.delete(espera_key)
                    else:
                        pipe.set(
                            espera_key,
                            valor,
                            ex=_CONCESION_SEGUNDOS * 2,
                        )
                    pipe.execute()
                    return
                except WatchError:
                    continue
    except Exception as exc:
        raise CoordinacionSalidaNoDisponible(str(exc)) from exc


def _intentar_adquirir(lock_key: str, token: str) -> bool:
    try:
        return bool(
            redis_client.set(lock_key, token, nx=True, ex=_CONCESION_SEGUNDOS)
        )
    except Exception as exc:
        raise CoordinacionSalidaNoDisponible(str(exc)) from exc


def _renovar_hasta_terminar(lock_key: str, token: str, terminar: threading.Event) -> None:
    while not terminar.wait(_RENOVACION_SEGUNDOS):
        try:
            renovado = _actualizar_si_es_dueno(
                lock_key, token, expirar_en=_CONCESION_SEGUNDOS
            )
        except Exception:
            return
        if not renovado:
            return


def _actualizar_si_es_dueno(
    lock_key: str,
    token: str,
    *,
    expirar_en: int | None = None,
) -> bool:
    """Renueva o libera con WATCH/MULTI sin tocar una concesión ajena."""
    while True:
        with redis_client.pipeline() as pipe:
            try:
                pipe.watch(lock_key)
                if pipe.get(lock_key) != token:
                    pipe.unwatch()
                    return False
                pipe.multi()
                if expirar_en is None:
                    pipe.delete(lock_key)
                else:
                    pipe.expire(lock_key, expirar_en)
                pipe.execute()
                return True
            except WatchError:
                continue


@contextmanager
def turno_de_salida(
    channel: Channel | str,
    prioridad: PrioridadSalida = PrioridadSalida.NORMAL,
):
    """Concede un único turno físico por proyecto/canal.

    Las respuestas interactivas esperan el turno y anuncian su presencia. Los
    recordatorios no retienen hilos: si hay una respuesta esperando o la cuenta
    está ocupada, el llamador recibe ``SalidaOcupada`` para reagendar la tarea.
    """
    prioridad = PrioridadSalida(prioridad)
    lock_key, espera_key = _claves(channel)
    token = secrets.token_urlsafe(24)
    interactiva = prioridad == PrioridadSalida.INTERACTIVA
    adquirido = False
    anunciada = False

    if interactiva:
        _sumar_interactiva(espera_key, 1)
        anunciada = True

    try:
        limite = time.monotonic() + _ESPERA_MAXIMA_SEGUNDOS
        while True:
            if not interactiva and hay_interactiva_esperando(channel):
                if prioridad == PrioridadSalida.RECORDATORIO:
                    raise SalidaOcupada()
            elif _intentar_adquirir(lock_key, token):
                adquirido = True
                break

            if prioridad == PrioridadSalida.RECORDATORIO:
                raise SalidaOcupada()
            if time.monotonic() >= limite:
                raise SalidaOcupada()
            time.sleep(_PAUSA_SONDEO_SEGUNDOS)

        if anunciada:
            _sumar_interactiva(espera_key, -1)
            anunciada = False

        terminar = threading.Event()
        renovador = threading.Thread(
            target=_renovar_hasta_terminar,
            args=(lock_key, token, terminar),
            daemon=True,
        )
        renovador.start()
        try:
            yield
        finally:
            terminar.set()
            renovador.join(timeout=1.0)
    finally:
        if anunciada:
            _sumar_interactiva(espera_key, -1)
        if adquirido:
            try:
                _actualizar_si_es_dueno(lock_key, token)
            except Exception as exc:
                # El envío ya pudo haber sido confirmado. Propagar este fallo
                # provocaría un reintento y, con él, un mensaje duplicado. La
                # concesión tiene TTL y se recuperará sola.
                print(
                    "No se pudo liberar el candado de salida; "
                    f"proyecto={proyecto_actual()} canal={_canal(channel)} "
                    f"error={type(exc).__name__}"
                )
