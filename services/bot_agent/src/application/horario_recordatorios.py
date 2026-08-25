"""Horario permitido para cualquier recordatorio automático.

Las respuestas normales del agente no pasan por aquí. Solo se ajustan las
tareas de recordatorio para que la franja 23:00–07:00 del negocio no produzca
envíos. Cuando una hora calculada cae dentro de esa franja, se mueve a las
07:00; los recordatorios posteriores conservan su separación desde ahí.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.infrastructure.repositories import clientes_whatsapp_repo


HORA_INICIO_SILENCIO = 23
HORA_FIN_SILENCIO = 7
ZONA_RESPALDO = "America/Costa_Rica"


@dataclass(frozen=True)
class PlanificacionRecordatorio:
    segundos: int
    aplazado_por_silencio: bool


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
    return planificar_recordatorio(
        demora, ahora=ahora, zona_horaria=zona_horaria
    ).segundos


def planificar_recordatorio(
    demora: int | float,
    *,
    ahora: datetime | None = None,
    zona_horaria: str | None = None,
) -> PlanificacionRecordatorio:
    """Countdown y procedencia para distinguir la cola acumulada de noche."""
    inicio = ahora or datetime.now(timezone.utc)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=timezone.utc)
    zona_horaria = zona_horaria or clientes_whatsapp_repo.zona_horaria_del_proyecto()
    destino_original = inicio + timedelta(seconds=max(0, float(demora)))
    destino = ajustar_hora(destino_original, zona_horaria)
    return PlanificacionRecordatorio(
        segundos=max(0, math.ceil((destino - inicio).total_seconds())),
        aplazado_por_silencio=destino != destino_original.astimezone(timezone.utc),
    )


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
    return [
        plan.segundos
        for plan in planificar_secuencia(
            demoras, ahora=ahora, zona_horaria=zona_horaria
        )
    ]


def planificar_secuencia(
    demoras: list[int | float],
    *,
    ahora: datetime | None = None,
    zona_horaria: str | None = None,
) -> list[PlanificacionRecordatorio]:
    """Planifica la secuencia y marca toda pieza afectada por un cruce nocturno."""
    inicio = ahora or datetime.now(timezone.utc)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=timezone.utc)
    zona_horaria = zona_horaria or clientes_whatsapp_repo.zona_horaria_del_proyecto()
    cursor = inicio
    demora_anterior = 0.0
    resultado: list[PlanificacionRecordatorio] = []
    hubo_silencio = False
    for demora in demoras:
        demora_actual = max(demora_anterior, float(demora))
        cursor_original = cursor + timedelta(seconds=demora_actual - demora_anterior)
        cursor = ajustar_hora(cursor_original, zona_horaria)
        hubo_silencio = hubo_silencio or cursor != cursor_original.astimezone(timezone.utc)
        resultado.append(
            PlanificacionRecordatorio(
                segundos=max(0, math.ceil((cursor - inicio).total_seconds())),
                aplazado_por_silencio=hubo_silencio,
            )
        )
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
