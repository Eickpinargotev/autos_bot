"""Ámbito del proyecto durante un evento o una tarea del bot.

El webhook lo fija antes de entrar al orquestador. Las tareas de Celery lo
propagan en sus cabeceras, de modo que repositorios y claves Redis no tengan que
adivinar el negocio a partir del número del cliente.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_proyecto: ContextVar[int] = ContextVar("proyecto_id", default=0)


def proyecto_actual() -> int:
    return int(_proyecto.get() or 0)


@contextmanager
def ambito_proyecto(proyecto_id: int | None):
    token = _proyecto.set(int(proyecto_id or 0))
    try:
        yield
    finally:
        _proyecto.reset(token)
