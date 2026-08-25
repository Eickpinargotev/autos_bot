"""Reloj compartido para recordatorios acumulados durante la noche."""

from dataclasses import dataclass
import math
import random
import secrets
import time

from redis.exceptions import WatchError

from src.application.buffer_service import redis_client
from src.application.project_context import proyecto_actual
from src.domain.entities import Channel


ESPERA_MINIMA_SEGUNDOS = 5 * 60
ESPERA_MAXIMA_SEGUNDOS = 10 * 60
_TTL_RELOJ_SEGUNDOS = 24 * 60 * 60
_TTL_RESERVA_SEGUNDOS = 180
_REINTENTO_RESERVA_SEGUNDOS = 2


class DrenajeNoDisponible(RuntimeError):
    """No se pudo coordinar el drenaje; el recordatorio no debe salir."""


@dataclass(frozen=True)
class TurnoDrenaje:
    espera_segundos: int = 0
    token: str | None = None

    @property
    def concedido(self) -> bool:
        return self.espera_segundos == 0


def _claves(channel: Channel | str) -> tuple[str, str]:
    channel_value = channel.value if isinstance(channel, Channel) else Channel(channel).value
    base = f"drenaje_recordatorios:p{proyecto_actual()}:{channel_value}"
    return f"{base}:siguiente_ms", f"{base}:reserva"


def solicitar_turno(
    channel: Channel | str,
    aplazado_por_silencio: bool | None,
    *,
    ahora_epoch: float | None = None,
) -> TurnoDrenaje:
    """Reserva el siguiente envío acumulado, sin fijar aún el intervalo.

    El reloj de 5–10 minutos se confirma DESPUÉS del envío real. Así una
    respuesta interactiva o una llamada lenta al proveedor no acorta la
    separación entre dos recordatorios.
    """
    if aplazado_por_silencio is False:
        return TurnoDrenaje()

    siguiente_key, reserva_key = _claves(channel)
    ahora_ms = int((ahora_epoch if ahora_epoch is not None else time.time()) * 1000)
    token = secrets.token_urlsafe(24)
    try:
        while True:
            with redis_client.pipeline() as pipe:
                try:
                    pipe.watch(siguiente_key, reserva_key)
                    siguiente_ms = int(pipe.get(siguiente_key) or 0)
                    if siguiente_ms > ahora_ms:
                        pipe.unwatch()
                        return TurnoDrenaje(
                            espera_segundos=max(
                                1, math.ceil((siguiente_ms - ahora_ms) / 1000)
                            )
                        )
                    if pipe.get(reserva_key):
                        pipe.unwatch()
                        return TurnoDrenaje(
                            espera_segundos=_REINTENTO_RESERVA_SEGUNDOS
                        )
                    pipe.multi()
                    pipe.set(reserva_key, token, ex=_TTL_RESERVA_SEGUNDOS)
                    pipe.execute()
                    return TurnoDrenaje(token=token)
                except WatchError:
                    continue
    except Exception as exc:
        raise DrenajeNoDisponible(str(exc)) from exc


def confirmar_envio(
    channel: Channel | str,
    turno: TurnoDrenaje,
    *,
    ahora_epoch: float | None = None,
) -> None:
    """Inicia el intervalo aleatorio desde la confirmación del envío real."""
    if not turno.token:
        return
    siguiente_key, reserva_key = _claves(channel)
    ahora_ms = int((ahora_epoch if ahora_epoch is not None else time.time()) * 1000)
    intervalo_ms = random.randint(
        ESPERA_MINIMA_SEGUNDOS * 1000,
        ESPERA_MAXIMA_SEGUNDOS * 1000,
    )
    _finalizar_reserva(
        siguiente_key,
        reserva_key,
        turno.token,
        siguiente_ms=ahora_ms + intervalo_ms,
    )


def liberar_turno(channel: Channel | str, turno: TurnoDrenaje) -> None:
    """Devuelve una reserva que no llegó a producir ningún envío."""
    if not turno.token:
        return
    siguiente_key, reserva_key = _claves(channel)
    _finalizar_reserva(siguiente_key, reserva_key, turno.token)


def _finalizar_reserva(
    siguiente_key: str,
    reserva_key: str,
    token: str,
    *,
    siguiente_ms: int | None = None,
) -> None:
    try:
        while True:
            with redis_client.pipeline() as pipe:
                try:
                    pipe.watch(reserva_key)
                    if pipe.get(reserva_key) != token:
                        pipe.unwatch()
                        return
                    pipe.multi()
                    if siguiente_ms is not None:
                        pipe.set(
                            siguiente_key,
                            siguiente_ms,
                            ex=_TTL_RELOJ_SEGUNDOS,
                        )
                    pipe.delete(reserva_key)
                    pipe.execute()
                    return
                except WatchError:
                    continue
    except Exception as exc:
        raise DrenajeNoDisponible(str(exc)) from exc
