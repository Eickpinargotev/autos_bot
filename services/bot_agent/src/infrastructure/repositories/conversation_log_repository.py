import json
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from src.core.config import settings
from src.domain.entities import Channel, MessageType


class ConversationLogRepository:
    @staticmethod
    def log_inbound(
        *,
        client_id: str,
        canal: Channel | str,
        sender_name: str,
        message_type: MessageType | str,
        text: str = "",
        event_type: str = "message",
    ) -> bool:
        return ConversationLogRepository.append_message(
            client_id=client_id,
            canal=canal,
            message={
                "id": str(uuid.uuid4()),
                "direction": "inbound",
                "author": "cliente",
                "sender_id": client_id,
                "sender_name": sender_name or "Desconocido",
                "message_type": ConversationLogRepository._message_type_value(message_type),
                "text": text or "",
                "event_type": event_type or "message",
                "created_at": ConversationLogRepository._now_iso(),
            },
        )

    @staticmethod
    def log_outbound(
        *,
        client_id: str,
        canal: Channel | str,
        text: str,
        event_type: str = "bot_reply",
    ) -> bool:
        return ConversationLogRepository.append_message(
            client_id=client_id,
            canal=canal,
            message={
                "id": str(uuid.uuid4()),
                "direction": "outbound",
                "author": "ia",
                "sender_id": "bot",
                "sender_name": "IA",
                "message_type": "text",
                "text": text or "",
                "event_type": event_type or "bot_reply",
                "created_at": ConversationLogRepository._now_iso(),
            },
        )

    @staticmethod
    def append_message(client_id: str, canal: Channel | str, message: dict[str, Any]) -> bool:
        if not settings.NOCODB_CONVERSATIONS_URL:
            return False

        canal_value = ConversationLogRepository._channel_value(canal)
        try:
            record = ConversationLogRepository.find_by_client_channel(client_id, canal_value)
            if record:
                record_id = ConversationLogRepository._record_id(record)
                conversation = ConversationLogRepository._conversation_from_record(record, client_id, canal_value)
                conversation["messages"].append(message)
                conversation["updated_at"] = ConversationLogRepository._now_iso()
                return ConversationLogRepository.update_conversation(record_id, conversation)

            now = ConversationLogRepository._now_iso()
            conversation = {
                "schema_version": 1,
                "client_id": str(client_id),
                "canal": canal_value,
                "created_at": now,
                "updated_at": now,
                "messages": [message],
            }
            return ConversationLogRepository.create_conversation(client_id, canal_value, conversation)
        except Exception as e:
            print(f"Error guardando conversacion en NocoDB: {e}")
            return False

    @staticmethod
    def find_by_client_channel(client_id: str, canal: Channel | str) -> dict[str, Any] | None:
        canal_value = ConversationLogRepository._channel_value(canal)
        where = (
            f'(client_id,eq,{ConversationLogRepository._where_value(client_id)})'
            f'~and(canal,eq,{ConversationLogRepository._where_value(canal_value)})'
        )
        url = ConversationLogRepository._url_with_params(
            settings.NOCODB_CONVERSATIONS_URL,
            {"where": where, "pageSize": 1},
        )
        response = httpx.get(url, headers=ConversationLogRepository._headers(), timeout=10.0)
        response.raise_for_status()
        records = ConversationLogRepository._records_from_response(response.json())
        return records[0] if records else None

    @staticmethod
    def create_conversation(client_id: str, canal: Channel | str, conversation: dict[str, Any]) -> bool:
        data = {
            "client_id": str(client_id),
            "canal": ConversationLogRepository._channel_value(canal),
            "json_mensajes": json.dumps(conversation, ensure_ascii=False),
        }
        response = httpx.post(
            ConversationLogRepository._insert_url(settings.NOCODB_CONVERSATIONS_URL),
            headers=ConversationLogRepository._headers(),
            json={"fields": data},
            timeout=10.0,
        )
        response.raise_for_status()
        return True

    @staticmethod
    def update_conversation(record_id: str, conversation: dict[str, Any]) -> bool:
        if not record_id:
            return False

        response = httpx.patch(
            ConversationLogRepository._base_records_url(settings.NOCODB_CONVERSATIONS_URL),
            headers=ConversationLogRepository._headers(),
            json=[
                {
                    "id": record_id,
                    "fields": {
                        "json_mensajes": json.dumps(conversation, ensure_ascii=False),
                    },
                }
            ],
            timeout=10.0,
        )
        response.raise_for_status()
        return True

    @staticmethod
    def delete_conversation(client_id: str, canal: Channel | str) -> bool:
        if not settings.NOCODB_CONVERSATIONS_URL:
            return False

        canal_value = ConversationLogRepository._channel_value(canal)
        try:
            record = ConversationLogRepository.find_by_client_channel(client_id, canal_value)
            if not record:
                return True
            record_id = ConversationLogRepository._record_id(record)
            if not record_id:
                return False
            response = httpx.request(
                "DELETE",
                ConversationLogRepository._base_records_url(settings.NOCODB_CONVERSATIONS_URL),
                headers=ConversationLogRepository._headers(),
                json=[{"id": record_id}],
                timeout=10.0,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Error eliminando conversacion en NocoDB: {e}")
            return False

    @staticmethod
    def _conversation_from_record(record: dict[str, Any], client_id: str, canal: str) -> dict[str, Any]:
        fields = record.get("fields", record)
        raw = fields.get("json_mensajes")
        if isinstance(raw, dict):
            data = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
        else:
            data = {}

        now = ConversationLogRepository._now_iso()
        messages = data.get("messages")
        if not isinstance(messages, list):
            messages = []

        return {
            "schema_version": data.get("schema_version") or 1,
            "client_id": str(data.get("client_id") or client_id),
            "canal": str(data.get("canal") or canal),
            "created_at": data.get("created_at") or now,
            "updated_at": data.get("updated_at") or now,
            "messages": messages,
        }

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

    @staticmethod
    def _message_type_value(message_type: MessageType | str) -> str:
        return message_type.value if isinstance(message_type, MessageType) else str(message_type)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")
