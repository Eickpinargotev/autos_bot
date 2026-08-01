"""Normalización de marcas de tiempo compartida por los repositorios.

Vivía en `nocodb_retention.py`, que desapareció al migrar a Postgres. La
mecánica de fechas sigue haciendo falta: el seguimiento por cliente guarda horas
como texto ISO dentro del historial y necesita compararlas con la hora actual.

La política de retención (cuántos días se conserva el historial) la define
`settings.CONVERSATION_RETENTION_DAYS`; aquí solo está la mecánica genérica.
"""

from datetime import datetime, timedelta
from typing import Any


def to_naive_local(value: datetime) -> datetime:
    """Normaliza a hora local SIN tzinfo para poder comparar aware vs naive."""
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def parse_timestamp(value: Any) -> datetime | None:
    """Convierte una marca de tiempo a datetime local naive.

    Acepta datetime, ISO 8601 (con o sin zona) y `%Y-%m-%d %H:%M:%S`. Devuelve
    None si no se puede interpretar; en ese caso el llamador NO debe borrar ni
    descartar nada: es la opción segura.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_naive_local(value)
    text = str(value).strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    parsers = (
        lambda t: datetime.fromisoformat(t),
        lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M:%S"),
        lambda t: datetime.strptime(t, "%Y-%m-%d"),
    )
    for parser in parsers:
        try:
            return to_naive_local(parser(candidate))
        except (ValueError, TypeError):
            continue
    return None


def cutoff(days: int, now: datetime | None = None) -> datetime:
    """Fecha límite: todo lo anterior a este instante está vencido."""
    base = to_naive_local(now) if now else datetime.now()
    return base - timedelta(days=days)


def is_expired(timestamp: Any, days: int, now: datetime | None = None) -> bool:
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return False  # No se pudo fechar: conservador, no se borra.
    return parsed < cutoff(days, now)


def a_iso(value: Any) -> str:
    """Representación ISO estable de una marca de tiempo (o cadena vacía)."""
    if isinstance(value, datetime):
        return value.astimezone().isoformat(timespec="seconds")
    return str(value or "")
