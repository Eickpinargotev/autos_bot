"""Sanitización de trazas completas antes de persistirlas."""

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

MAX_PROVIDER_BYTES = 256 * 1024
MAX_MODEL_BYTES = 128 * 1024
_SECRET = re.compile(r"authorization|cookie|access[_-]?token|refresh[_-]?token|webhook[_-]?token|(?:^|[_-])token(?:$|[_-])|secret|password|api[_-]?key|media[_-]?key|signature|credential", re.I)
_BASE64 = re.compile(r"^[A-Za-z0-9+/=_-]{512,}$")
_SIGNED = re.compile(r"signature|sig|token|key|credential|expires|x-amz-", re.I)


def _default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return str(value)


def sanitize(value: Any, max_bytes: int) -> Any:
    def walk(current: Any, key: str = "") -> Any:
        if _SECRET.search(key):
            return "[REDACTADO]"
        if is_dataclass(current):
            current = asdict(current)
        elif hasattr(current, "model_dump"):
            current = current.model_dump()
        if isinstance(current, dict):
            return {str(k): walk(v, str(k)) for k, v in current.items()}
        if isinstance(current, (list, tuple)):
            return [walk(v, key) for v in current]
        if isinstance(current, bytes):
            return f"[BINARIO OMITIDO: {len(current)} bytes]"
        if isinstance(current, str):
            text = current
            if text.startswith(("http://", "https://")):
                parts = urlsplit(text)
                if _SIGNED.search(parts.query):
                    text = urlunsplit((parts.scheme, parts.netloc, parts.path, "[REDACTADO]", ""))
            if len(text) >= 512 and _BASE64.fullmatch(text):
                return f"[BASE64 OMITIDO: {len(text)} caracteres]"
            return text
        return current

    clean = walk(value)
    raw = json.dumps(clean, ensure_ascii=False, default=_default).encode("utf-8")
    if len(raw) <= max_bytes:
        return clean
    return {
        "truncated": True,
        "limit_bytes": max_bytes,
        "original_bytes": len(raw),
        "preview": raw[: max_bytes - 256].decode("utf-8", errors="ignore"),
    }
