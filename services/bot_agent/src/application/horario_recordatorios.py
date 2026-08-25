"""Horario permitido para cualquier recordatorio automático.

Las respuestas normales del agente no pasan por aquí. Solo se ajustan las
tareas de recordatorio para que la franja 23:00–07:00 del negocio no produzca
envíos. Cuando una hora calculada cae dentro de esa franja, se mueve a las
07:00; los recordatorios posteriores conservan su separación desde ahí.
"""

from datetime import datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.infrastructure.repositories import clientes_whatsapp_repo


HORA_INICIO_SILENCIO = 23
HORA_FIN_SILENCIO = 7
ZONA_RESPALDO = "America/Costa_Rica"


def _zona(nombre: str | None = None) -> ZoneInfo:
    nombre = nombre or clientes_whatsapp_repo.zona_horaria_del_proyecto()
    try:
        return ZoneInfo(nombre or ZONA_RESPALDO)
    except ZoneInfoNotFoundError:
        return ZoneInfo(ZONA_RESPALDO)


def ajustar_hora(fecha: datetime, zona_horaria: str | None = None) -> datetime:
    """Devuelve ``fecha`` o la siguiente apertura a las 07:00 del negocio."""
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    zona = _zona(zona_horaria)
    local = fecha.astimezone(zona)

    if local.hour >= HORA_INICIO_SILENCIO:
        local = (local + timedelta(days=1)).replace(
            hour=HORA_FIN_SILENCIO, minute=0, second=0, microsecond=0
        )
    elif local.hour < HORA_FIN_SILENCIO:
        local = local.replace(
            hour=HORA_FIN_SILENCIO, minute=0, second=0, microsecond=0
        )
    return local.astimezone(timezone.utc)


def segundos_para_recordatorio(
    demora: int | float,
    *,
    ahora: datetime | None = None,
    zona_horaria: str | None = None,
) -> int:
    """Countdown hasta ``ahora + demora``, movido a las 07:00 si hace falta."""
    inicio = ahora or datetime.now(timezone.utc)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=timezone.utc)
    zona_horaria = zona_horaria or clientes_whatsapp_repo.zona_horaria_del_proyecto()
    destino = ajustar_hora(
        inicio + timedelta(seconds=max(0, float(demora))), zona_horaria
    )
    return max(0, math.ceil((destino - inicio).total_seconds()))


def segundos_para_secuencia(
    demoras: list[int | float],
    *,
    ahora: datetime | None = None,
    zona_horaria: str | None = None,
) -> list[int]:
    """Ajusta una secuencia sin juntar sus piezas a las 07:00.

    Las demoras configuradas son acumuladas desde el inicio. Se conserva la
    diferencia entre una y la siguiente, pero si una cae de noche se toma la
    apertura de las 07:00 como su nuevo punto de partida.
    """
    inicio = ahora or datetime.now(timezone.utc)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=timezone.utc)
    zona_horaria = zona_horaria or clientes_whatsapp_repo.zona_horaria_del_proyecto()
    cursor = inicio
    demora_anterior = 0.0
    resultado: list[int] = []
    for demora in demoras:
        demora_actual = max(demora_anterior, float(demora))
        cursor = ajustar_hora(
            cursor + timedelta(seconds=demora_actual - demora_anterior),
            zona_horaria,
        )
        resultado.append(max(0, math.ceil((cursor - inicio).total_seconds())))
        demora_anterior = demora_actual
    return resultado


def segundos_hasta_horario_permitido(
    *,
    ahora: datetime | None = None,
    zona_horaria: str | None = None,
) -> int:
    """Cero si se puede enviar ahora; si no, segundos hasta las 07:00."""
    inicio = ahora or datetime.now(timezone.utc)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=timezone.utc)
    zona_horaria = zona_horaria or clientes_whatsapp_repo.zona_horaria_del_proyecto()
    permitido = ajustar_hora(inicio, zona_horaria)
    return max(0, math.ceil((permitido - inicio).total_seconds()))
