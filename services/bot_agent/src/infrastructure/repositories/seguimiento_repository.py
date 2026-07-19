"""Acceso a NocoDB para el seguimiento por cliente y el resumen mensual.

Tablas (base LOGs_Autos_Mensajes):
- seguimiento_clientes: una fila por (client_id, canal) con contadores de
  conversaciones (ventana de 24h), derivaciones a asesor, costo acumulado del
  LLM y el historial simplificado del chat.
- resumen_mensual: una fila por mes (YYYY-MM) con mensajes del bot, mensajes
  del cliente y costo total.

El costo se persiste como entero en micro-USD (`costo_microusd`): sumar
enteros no acumula error de punto flotante; el campo decimal legible
(`costo_*_usd`) se deriva de ese entero en cada escritura.
"""

from typing import Any

import httpx

from src.core.config import settings
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository

_helpers = ConversationLogRepository


class SeguimientoRepository:
    TIMEOUT = 10.0

    # ------------------------- seguimiento_clientes -------------------------

    @staticmethod
    def find_cliente(client_id: str, canal: str) -> dict[str, Any] | None:
        where = (
            f'(client_id,eq,{_helpers._where_value(client_id)})'
            f'~and(canal,eq,{_helpers._where_value(canal)})'
        )
        url = _helpers._url_with_params(
            settings.NOCODB_SEGUIMIENTO_CLIENTES_URL,
            {"where": where, "pageSize": 1},
        )
        response = httpx.get(url, headers=_helpers._headers(), timeout=SeguimientoRepository.TIMEOUT)
        response.raise_for_status()
        records = _helpers._records_from_response(response.json())
        return records[0] if records else None

    @staticmethod
    def create_cliente(fields: dict[str, Any]) -> bool:
        return SeguimientoRepository._create(settings.NOCODB_SEGUIMIENTO_CLIENTES_URL, fields)

    @staticmethod
    def update_cliente(record_id: str, fields: dict[str, Any]) -> bool:
        return SeguimientoRepository._update(settings.NOCODB_SEGUIMIENTO_CLIENTES_URL, record_id, fields)

    # --------------------------- resumen_mensual ----------------------------

    @staticmethod
    def find_mes(mes: str) -> dict[str, Any] | None:
        where = f'(mes,eq,{_helpers._where_value(mes)})'
        url = _helpers._url_with_params(
            settings.NOCODB_RESUMEN_MENSUAL_URL,
            {"where": where, "pageSize": 1},
        )
        response = httpx.get(url, headers=_helpers._headers(), timeout=SeguimientoRepository.TIMEOUT)
        response.raise_for_status()
        records = _helpers._records_from_response(response.json())
        return records[0] if records else None

    @staticmethod
    def create_mes(fields: dict[str, Any]) -> bool:
        return SeguimientoRepository._create(settings.NOCODB_RESUMEN_MENSUAL_URL, fields)

    @staticmethod
    def update_mes(record_id: str, fields: dict[str, Any]) -> bool:
        return SeguimientoRepository._update(settings.NOCODB_RESUMEN_MENSUAL_URL, record_id, fields)

    # ------------------------------ comunes ---------------------------------

    @staticmethod
    def record_fields(record: dict[str, Any]) -> dict[str, Any]:
        fields = record.get("fields")
        return fields if isinstance(fields, dict) else record

    @staticmethod
    def record_id(record: dict[str, Any]) -> str:
        return _helpers._record_id(record)

    @staticmethod
    def _create(url: str, fields: dict[str, Any]) -> bool:
        response = httpx.post(
            _helpers._base_records_url(url),
            headers=_helpers._headers(),
            json={"fields": fields},
            timeout=SeguimientoRepository.TIMEOUT,
        )
        response.raise_for_status()
        return True

    @staticmethod
    def _update(url: str, record_id: str, fields: dict[str, Any]) -> bool:
        if not record_id:
            return False
        response = httpx.patch(
            _helpers._base_records_url(url),
            headers=_helpers._headers(),
            json=[{"id": record_id, "fields": fields}],
            timeout=SeguimientoRepository.TIMEOUT,
        )
        response.raise_for_status()
        return True
