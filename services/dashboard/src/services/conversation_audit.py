"""Contexto de responsables para auditoría; nunca incluye el texto del chat."""
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from uuid import uuid4
import json

_context = ContextVar('conversation_audit', default=None)


def context():
    return dict(_context.get() or {})


def actor_from_request(function):
    parameters = signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        usuario = parameters.bind_partial(*args, **kwargs).arguments.get('usuario') or {}
        actor = {'kind': 'dashboard_user', 'user_id': usuario.get('id'), 'username': usuario.get('usuario'),
                 'real_admin_id': usuario.get('admin_real_id')}
        token = _context.set({'source': 'dashboard', 'actor': actor, 'correlation_id': str(uuid4())})
        try:
            return function(*args, **kwargs)
        finally:
            _context.reset(token)
    return wrapped


def sql_context():
    return json.dumps(context(), default=str)
