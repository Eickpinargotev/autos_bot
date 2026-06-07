import time
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from typing import Any

from src.core.config import settings
from src.domain.entities import Channel
from src.infrastructure.evals.conversation_shots import ShotTraceCollector
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository


class ToolCallLogger:
    MAX_TEXT_LENGTH = 1000
    MAX_LIST_ITEMS = 10
    MAX_DICT_ITEMS = 20
    REDACTED_KEYS = {
        "api_key",
        "authorization",
        "headers",
        "password",
        "token",
        "xc-token",
    }

    @classmethod
    def success(
        cls,
        *,
        client_id: str,
        canal: Channel | str,
        tool_name: str,
        input_data: Any = None,
        output_data: Any = None,
        text: str = "",
        duration_ms: int | None = None,
    ) -> bool:
        clean_input = cls.sanitize(input_data)
        clean_output = cls.sanitize(output_data)
        ShotTraceCollector.record_tool_event(
            tool_name=tool_name,
            status="success",
            input_data=clean_input,
            output_data=clean_output,
            duration_ms=duration_ms,
        )
        if not cls._enabled(client_id, canal):
            return False
        return ConversationLogRepository.log_tool_event(
            client_id=client_id,
            canal=canal,
            tool_name=tool_name,
            status="success",
            input_data=clean_input,
            output_data=clean_output,
            text=text or f"{tool_name} completado",
            duration_ms=duration_ms,
        )

    @classmethod
    def error(
        cls,
        *,
        client_id: str,
        canal: Channel | str,
        tool_name: str,
        input_data: Any = None,
        error: Exception | str = "",
        output_data: Any = None,
        text: str = "",
        duration_ms: int | None = None,
    ) -> bool:
        clean_input = cls.sanitize(input_data)
        clean_output = cls.sanitize(output_data)
        clean_error = str(error)[: cls.MAX_TEXT_LENGTH]
        ShotTraceCollector.record_tool_event(
            tool_name=tool_name,
            status="error",
            input_data=clean_input,
            output_data=clean_output,
            error=clean_error,
            duration_ms=duration_ms,
        )
        if not cls._enabled(client_id, canal):
            return False
        return ConversationLogRepository.log_tool_event(
            client_id=client_id,
            canal=canal,
            tool_name=tool_name,
            status="error",
            input_data=clean_input,
            output_data=clean_output,
            error=clean_error,
            text=text or f"{tool_name} falló",
            duration_ms=duration_ms,
        )

    @classmethod
    def record(
        cls,
        *,
        client_id: str,
        canal: Channel | str,
        tool_name: str,
        input_data: Any = None,
        output_mapper: Callable[[Any], Any] | None = None,
        text_mapper: Callable[[Any], str] | None = None,
        call: Callable[[], Any],
    ) -> Any:
        started = time.monotonic()
        try:
            result = call()
        except Exception as exc:
            cls.error(
                client_id=client_id,
                canal=canal,
                tool_name=tool_name,
                input_data=input_data,
                error=exc,
                duration_ms=cls._duration_ms(started),
            )
            raise

        output_data = output_mapper(result) if output_mapper else result
        text = text_mapper(result) if text_mapper else ""
        cls.success(
            client_id=client_id,
            canal=canal,
            tool_name=tool_name,
            input_data=input_data,
            output_data=output_data,
            text=text,
            duration_ms=cls._duration_ms(started),
        )
        return result

    @classmethod
    def sanitize(cls, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return cls._truncate(value)
        if is_dataclass(value):
            return cls.sanitize(asdict(value))
        if isinstance(value, dict):
            clean: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= cls.MAX_DICT_ITEMS:
                    clean["_truncated_items"] = len(value) - cls.MAX_DICT_ITEMS
                    break
                key_text = str(key)
                if key_text.lower() in cls.REDACTED_KEYS:
                    clean[key_text] = "[redacted]"
                else:
                    clean[key_text] = cls.sanitize(item)
            return clean
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            clean_list = [cls.sanitize(item) for item in items[: cls.MAX_LIST_ITEMS]]
            if len(items) > cls.MAX_LIST_ITEMS:
                clean_list.append({"_truncated_items": len(items) - cls.MAX_LIST_ITEMS})
            return clean_list
        return cls._truncate(str(value))

    @classmethod
    def _truncate(cls, text: str) -> str:
        if len(text) <= cls.MAX_TEXT_LENGTH:
            return text
        return f"{text[: cls.MAX_TEXT_LENGTH]}...[truncated]"

    @staticmethod
    def _enabled(client_id: str, canal: Channel | str) -> bool:
        return bool(client_id and canal and settings.NOCODB_CONVERSATIONS_URL)

    @staticmethod
    def _duration_ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)
