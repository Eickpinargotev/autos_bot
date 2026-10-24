"""Auditoría operativa: campos permitidos, sin mensajes ni payloads del chat."""
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from src.application.project_context import proyecto_actual
from src.infrastructure.repositories.postgres_conn import consultar_uno, ejecutar

log = logging.getLogger(__name__)
_context = ContextVar('conversation_audit_context', default=None)
_FIELDS = frozenset({
    'message_type', 'provider_sent_at', 'branch', 'keyword_id', 'template_id',
    'stage', 'task_id', 'task_name', 'attempt', 'delay_seconds', 'error_code',
    'provider_message_id', 'status', 'operation', 'reference',
})


def context():
    return dict(_context.get() or {})


@contextmanager
def scope(**values):
    token = _context.set({**context(), **values})
    try:
        yield
    finally:
        _context.reset(token)


def sql_context():
    return json.dumps(context(), default=str)


def record(client_id, canal, event_type, *, source=None, metadata=None, dedup_key=None):
    """La lista permitida impide copiar accidentalmente texto, razones o errores."""
    if not proyecto_actual() or not client_id:
        return None
    ctx = context()
    details = {k: ctx[k] for k in ("task_id", "task_name") if k in ctx}
    details.update(metadata or {})
    safe = {k: v for k, v in details.items()
            if k in _FIELDS and (v is None or isinstance(v, (str, int, float, bool)))}
    try:
        row = consultar_uno(
            'SELECT conversation_audit_append(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb) AS id',
            (proyecto_actual(), getattr(canal, 'value', canal), str(client_id), event_type,
             source or ctx.get('source', 'bot'), json.dumps(ctx.get('actor', {'kind': 'system'})),
             ctx.get('correlation_id'), ctx.get('event_id'), dedup_key, json.dumps(safe, default=str)),
        )
        return (row or {}).get('id')
    except Exception:
        log.exception('No se pudo registrar auditoría: tipo=%s proyecto=%s', event_type, proyecto_actual())
        return None


def new_correlation():
    return str(uuid4())


def purge():
    try:
        return ejecutar("DELETE FROM conversation_audit WHERE occurred_at < clock_timestamp() - INTERVAL '3 months'")
    except Exception:
        log.exception('No se pudo purgar auditoría de conversaciones')
        return 0
