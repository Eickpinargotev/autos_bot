"""Utilidades compartidas para la purga por retención de tablas de NocoDB.

Centraliza el parseo de fechas, el recorrido paginado de registros y el borrado
por lotes para no duplicarlo entre los repositorios de conversaciones y de shots.
La política de retención (cuántos días se conserva el historial) la define
`settings.CONVERSATION_RETENTION_DAYS`; aquí solo está la mecánica genérica.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from src.core.config import settings


# --- Fechas -----------------------------------------------------------------

def to_naive_local(value: datetime) -> datetime:
    """Normaliza a hora local SIN tzinfo para poder comparar aware vs naive."""
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def parse_timestamp(value: Any) -> datetime | None:
    """Convierte una marca de tiempo de NocoDB a datetime local naive.

    Acepta tanto ISO 8601 (con o sin zona, `log_*` usa `isoformat`) como el
    formato `%Y-%m-%d %H:%M:%S` que usan los shots. Devuelve None si no se puede
    interpretar (en ese caso el llamador NO debe borrar: es la opción segura).
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


# --- Acceso a NocoDB --------------------------------------------------------

def _headers() -> dict[str, str]:
    return {"xc-token": settings.NOCODB_TOKEN, "Content-Type": "application/json"}


def _base_records_url(url: str) -> str:
    return urlunparse(urlparse(url)._replace(query=""))


def _url_with_params(url: str, params: dict[str, Any]) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key, value in params.items():
        query[key] = [str(value)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def records_from_response(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    records = data.get("records") or data.get("list") or data.get("data") or []
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def record_id(record: dict[str, Any]) -> str:
    keys = ("id", "Id", "ID", "_id", "ncRecordId", "rowId")
    for key in keys:
        value = record.get(key)
        if value:
            return str(value)
    fields = record.get("fields") or {}
    if isinstance(fields, dict):
        for key in keys:
            value = fields.get(key)
            if value:
                return str(value)
    return ""


def record_fields(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields")
    return fields if isinstance(fields, dict) else record


def iter_records(
    url: str,
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    timeout: float = 30.0,
) -> Iterator[dict[str, Any]]:
    """Recorre todos los registros de una tabla paginando por `page`."""
    page = 1
    while page <= max_pages:
        page_url = _url_with_params(url, {"page": page, "pageSize": page_size})
        response = httpx.get(page_url, headers=_headers(), timeout=timeout)
        response.raise_for_status()
        records = records_from_response(response.json())
        if not records:
            return
        yield from records
        if len(records) < page_size:
            return
        page += 1


def delete_records(
    url: str,
    ids: list[str],
    *,
    chunk_size: int = 50,
    timeout: float = 30.0,
) -> int:
    """Borra registros por id en lotes. Devuelve cuántos se eliminaron."""
    valid_ids = [str(rid) for rid in ids if rid]
    if not valid_ids:
        return 0

    base_url = _base_records_url(url)
    deleted = 0
    for start in range(0, len(valid_ids), chunk_size):
        batch = valid_ids[start : start + chunk_size]
        response = httpx.request(
            "DELETE",
            base_url,
            headers=_headers(),
            json=[{"id": rid} for rid in batch],
            timeout=timeout,
        )
        response.raise_for_status()
        deleted += len(batch)
    return deleted
