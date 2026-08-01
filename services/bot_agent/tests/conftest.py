"""Aislamiento global de los tests frente a servicios reales.

Los tests deterministas corren en el contenedor dev con Redis y Postgres
REALES alcanzables (el compose local los levanta). Cualquier camino de código
con efectos colaterales que un test ejerza sin mockear escribiría datos basura
en esos servicios —y ahora Postgres es la fuente de verdad del negocio, así que
ensuciarlo es peor que antes—. Este fixture autouse corta ese riesgo:

- Reemplaza el cliente Redis del seguimiento por un MagicMock (los tests que
  necesitan verificar llamadas siguen parcheando sobre ese mock).
- Neutraliza el acceso a Postgres en TODOS los repositorios. Cada módulo importa
  `ejecutar`/`consultar`/`consultar_uno` por nombre, así que hay que sustituir
  la referencia en cada módulo, no solo en `postgres_conn`.

Un test que quiera verificar el SQL que se emite re-parchea el nombre en su
propio módulo (p. ej. `...conversation_log_repository.consultar`), que es
exactamente lo que sustituye este fixture.
"""

from unittest.mock import MagicMock

import pytest

# Módulos que hablan con Postgres. Si añades un repositorio nuevo, agrégalo aquí
# o sus tests escribirán en la base real.
_MODULOS_CON_POSTGRES = (
    "src.infrastructure.repositories.conversation_log_repository",
    "src.infrastructure.repositories.seguimiento_repository",
    "src.infrastructure.repositories.keyword_registry_repository",
    "src.infrastructure.repositories.report_repository",
    "src.infrastructure.repositories.unanswered_question_repository",
    "src.infrastructure.repositories.billing_repository",
    "src.infrastructure.repositories.invitaciones_repository",
    "src.infrastructure.evals.conversation_shots",
    "src.application.rag_service",
    "src.application.seguimiento_service",
    "src.infrastructure.repositories.envios_repository",
    "src.infrastructure.repositories.clientes_whatsapp_repo",
    "src.infrastructure.repositories.plantillas_repository",
)

# Valor inocuo por función: "no escribió nada" / "no encontró nada".
_RETORNOS = {"ejecutar": 0, "consultar": [], "consultar_uno": None}


@pytest.fixture(autouse=True)
def _aisla_servicios(monkeypatch):
    import importlib

    from src.application import seguimiento_service

    monkeypatch.setattr(seguimiento_service, "redis_client", MagicMock())

    for nombre_modulo in _MODULOS_CON_POSTGRES:
        try:
            modulo = importlib.import_module(nombre_modulo)
        except Exception:
            continue
        for funcion, retorno in _RETORNOS.items():
            if hasattr(modulo, funcion):
                monkeypatch.setattr(modulo, funcion, MagicMock(return_value=retorno))

    yield
