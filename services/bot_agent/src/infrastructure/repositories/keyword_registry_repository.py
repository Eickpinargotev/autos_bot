from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from src.core.config import settings
from src.domain.entities import Channel


class KeywordRegistryRepository:
    @staticmethod
    def register_if_missing(registro: str, nombre: str, canal: Channel | str, palabra_clave: str) -> bool:
        if not settings.NOCODB_KEYWORD_REGISTROS_URL:
            return False

        canal_value = KeywordRegistryRepository._channel_value(canal)
        try:
            if KeywordRegistryRepository.find_by_registro_channel(registro, canal_value):
                return True

            data = {
                "registro": str(registro),
                "nombre": nombre or "Desconocido",
                "canal": canal_value,
                "palabra clave": palabra_clave,
                "fecha de creacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            response = httpx.post(
                KeywordRegistryRepository._insert_url(settings.NOCODB_KEYWORD_REGISTROS_URL),
                headers=KeywordRegistryRepository._headers(),
                json={"fields": data},
                timeout=10.0,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Error registrando keyword en NocoDB: {e}")
            return False

    @staticmethod
    def delete(registro: str, canal: Channel | str) -> bool:
        if not settings.NOCODB_KEYWORD_REGISTROS_URL:
            return False

        canal_value = KeywordRegistryRepository._channel_value(canal)
        try:
            record = KeywordRegistryRepository.find_by_registro_channel(registro, canal_value)
            if not record:
                return True

            record_id = KeywordRegistryRepository._record_id(record)
            if not record_id:
                return False

            response = httpx.request(
                "DELETE",
                KeywordRegistryRepository._base_records_url(settings.NOCODB_KEYWORD_REGISTROS_URL),
                headers=KeywordRegistryRepository._headers(),
                json=[{"id": record_id}],
                timeout=10.0,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Error eliminando keyword en NocoDB: {e}")
            return False

    @staticmethod
    def find_by_registro_channel(registro: str, canal: Channel | str) -> dict[str, Any] | None:
        canal_value = KeywordRegistryRepository._channel_value(canal)
        where = (
            f'(registro,eq,{KeywordRegistryRepository._where_value(registro)})'
            f'~and(canal,eq,{KeywordRegistryRepository._where_value(canal_value)})'
        )
        url = KeywordRegistryRepository._url_with_params(
            settings.NOCODB_KEYWORD_REGISTROS_URL,
            {"where": where, "pageSize": 1},
        )
        response = httpx.get(url, headers=KeywordRegistryRepository._headers(), timeout=10.0)
        response.raise_for_status()
        records = KeywordRegistryRepository._records_from_response(response.json())
        return records[0] if records else None

    @staticmethod
    def exists(registro: str, canal: Channel | str) -> bool:
        if not settings.NOCODB_KEYWORD_REGISTROS_URL:
            return False

        try:
            return KeywordRegistryRepository.find_by_registro_channel(registro, canal) is not None
        except Exception as e:
            print(f"Error consultando keyword en NocoDB: {e}")
            return False

    @staticmethod
    def _records_from_response(data: dict[str, Any]) -> list[dict[str, Any]]:
        records = data.get("records") or data.get("list") or data.get("data") or []
        return [record for record in records if isinstance(record, dict)] if isinstance(records, list) else []

    @staticmethod
    def _record_id(record: dict[str, Any]) -> str:
        for key in ("id", "Id", "ID", "_id", "ncRecordId", "rowId"):
            value = record.get(key)
            if value:
                return str(value)
        fields = record.get("fields") or {}
        if isinstance(fields, dict):
            for key in ("id", "Id", "ID", "_id", "ncRecordId", "rowId"):
                value = fields.get(key)
                if value:
                    return str(value)
        return ""

    @staticmethod
    def _headers() -> dict[str, str]:
        return {"xc-token": settings.NOCODB_TOKEN, "Content-Type": "application/json"}

    @staticmethod
    def _insert_url(url: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        query["insertAt"] = ["0"]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    @staticmethod
    def _base_records_url(url: str) -> str:
        return urlunparse(urlparse(url)._replace(query=""))

    @staticmethod
    def _url_with_params(url: str, params: dict[str, Any]) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key, value in params.items():
            query[key] = [str(value)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    @staticmethod
    def _where_value(value: str) -> str:
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @staticmethod
    def _channel_value(canal: Channel | str) -> str:
        return canal.value if isinstance(canal, Channel) else str(canal)
