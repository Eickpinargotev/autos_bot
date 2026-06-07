import json
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from src.core.config import settings
from src.domain.entities import Channel


_active_collector: ContextVar["ShotTraceCollector | None"] = ContextVar("active_shot_collector", default=None)


class ShotTraceCollector:
    def __init__(self):
        self.events: list[dict[str, Any]] = []
        self._order = 1

    def __enter__(self):
        self._token = _active_collector.set(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        _active_collector.reset(self._token)
        return False

    @classmethod
    def current(cls) -> "ShotTraceCollector | None":
        return _active_collector.get()

    @classmethod
    def record_tool_event(
        cls,
        *,
        tool_name: str,
        status: str,
        input_data: Any = None,
        output_data: Any = None,
        error: str = "",
        duration_ms: int | None = None,
    ):
        collector = cls.current()
        if not collector:
            return
        collector.events.append(
            {
                "type": "tool_call",
                "order": collector.next_order(),
                "tool_name": tool_name,
                "status": status,
                "input": input_data if input_data is not None else {},
                "output": output_data if output_data is not None else {},
                "error": error or "",
                "duration_ms": duration_ms,
            }
        )

    def next_order(self) -> int:
        value = self._order
        self._order += 1
        return value

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("type") == "tool_call"]


class ConversationShotBuilder:
    @staticmethod
    def build(
        *,
        channel: Channel | str,
        user_id: str,
        user_name: str,
        user_message: str,
        bot_replies: list[str],
        state_before: Any,
        state_after: Any,
        trace_events: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        current = now or datetime.now().astimezone()
        fecha_hora = current.strftime("%Y-%m-%d %H:%M:%S")
        shot_id = f"{user_id}_{current.strftime('%Y%m%d')}_{current.strftime('%H%M%S')}"
        channel_value = ConversationShotBuilder._channel_value(channel)
        before = ConversationShotBuilder._state_snapshot(state_before)
        after = ConversationShotBuilder._state_snapshot(state_after)
        shot = {
            "state_before": before,
            "history": ConversationShotBuilder._compact_history(getattr(state_before, "conversation_history", [])),
            "turn": {
                "user_message": user_message or "",
                "bot_replies": bot_replies or [],
                "events": ConversationShotBuilder._turn_events(
                    user_message=user_message or "",
                    trace_events=trace_events if trace_events is not None else tools or [],
                    bot_replies=bot_replies or [],
                ),
            },
            "state_after": after,
            "review": {
                "status": "unreviewed",
                "tags": [],
                "observed_error": "",
                "expected_behavior": "",
                "notes": "",
            },
        }
        return shot_id, fecha_hora, shot

    @staticmethod
    def _turn_events(
        *,
        user_message: str,
        trace_events: list[dict[str, Any]],
        bot_replies: list[str],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = [{"type": "user_message", "order": 0, "text": user_message}]
        max_order = 0
        for index, event in enumerate(trace_events or [], start=1):
            if not isinstance(event, dict):
                continue
            normalized = dict(event)
            normalized.setdefault("type", "tool_call")
            normalized["order"] = int(normalized.get("order") or index)
            max_order = max(max_order, normalized["order"])
            events.append(normalized)

        for offset, reply in enumerate(bot_replies or [], start=1):
            events.append(
                {
                    "type": "bot_message",
                    "order": max_order + offset,
                    "text": str(reply),
                }
            )
        return sorted(events, key=lambda item: int(item.get("order") or 0))

    @staticmethod
    def _state_snapshot(state: Any) -> dict[str, Any]:
        return {
            "flow": str(getattr(state, "flow", "") or ""),
            "node": str(getattr(state, "node", "") or ""),
            "last_question": str(getattr(state, "last_question", "") or ""),
            "awaiting_reply": bool(getattr(state, "awaiting_reply", False)),
        }

    @staticmethod
    def _compact_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact = []
        for turn in history or []:
            if not isinstance(turn, dict):
                continue
            compact.append(
                {
                    "user": str(turn.get("user") or ""),
                    "bot": ConversationShotBuilder._bot_messages(turn.get("bot")),
                }
            )
        return compact

    @staticmethod
    def _bot_messages(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if value:
            return [str(value)]
        return []

    @staticmethod
    def _channel_value(channel: Channel | str) -> str:
        return channel.value if isinstance(channel, Channel) else str(channel)


class ConversationShotRepository:
    @staticmethod
    def save(
        *,
        fecha_hora: str,
        id_user: str,
        chanel: Channel | str,
        shot: dict[str, Any],
    ) -> bool:
        if not settings.NOCODB_CONVERSATION_SHOTS_URL:
            return False

        try:
            response = httpx.post(
                ConversationShotRepository._insert_url(settings.NOCODB_CONVERSATION_SHOTS_URL),
                headers=ConversationShotRepository._headers(),
                json={
                    "fields": {
                        "fecha_hora": fecha_hora,
                        "id_user": str(id_user),
                        "chanel": ConversationShotBuilder._channel_value(chanel),
                        "reviewed": False,
                        "json": json.dumps(ConversationShotRepository._jsonable(shot), ensure_ascii=False),
                    }
                },
                timeout=10.0,
            )
            response.raise_for_status()
            return True
        except Exception as exc:
            print(f"Error guardando conversation shot en NocoDB: {exc}")
            return False

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
    def _jsonable(value: Any) -> Any:
        if is_dataclass(value):
            return ConversationShotRepository._jsonable(asdict(value))
        if isinstance(value, dict):
            return {str(key): ConversationShotRepository._jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [ConversationShotRepository._jsonable(item) for item in value]
        return value
