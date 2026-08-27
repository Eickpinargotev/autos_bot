"""Aislamiento global de los tests frente a servicios reales.

Los tests deterministas corren en el contenedor dev con Redis, Postgres y el
broker de Celery REALES alcanzables (el compose local los levanta). Cualquier
camino de código con efectos colaterales que un test ejerza sin mockear
escribiría datos basura en esos servicios —y Postgres es la fuente de verdad del
negocio—. Este fixture autouse corta ese riesgo en tres frentes:

1. **Postgres.** Cada módulo importa `ejecutar`/`consultar`/`consultar_uno`/
   `run_query` por nombre, así que hay que sustituir la referencia en cada
   módulo, no solo en `postgres_conn`.
2. **Redis.** Tanto el del seguimiento como el de `buffer_service`, que es el
   que usan el orquestador y los candados.
3. **La cola de Celery.** El más importante y el menos evidente: `apply_async`
   encola en el broker REAL, y el worker que está corriendo toma esa tarea y la
   ejecuta **en otro proceso**, donde nada de lo de arriba está parcheado. Un
   test que llegue a encolar provoca así un turno real del agente: llamadas
   pagadas a OpenAI, escrituras en la base del cliente y hasta bloqueos de
   usuario. Pasó de verdad (01/08/2026): un test de detección de enlaces encoló
   "buenas, cuanto cuesta el curso? gracias" y el worker lo procesó como un
   cliente real. Por eso se corta el envío a la cola, no solo la base.

Un test que quiera verificar el SQL que se emite, o que se encoló una tarea,
re-parchea el nombre en su propio módulo (p. ej.
`...conversation_log_repository.consultar`), que es exactamente lo que sustituye
este fixture.
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
    "src.infrastructure.repositories.postgres_user_repo",
    "src.infrastructure.repositories.bloqueos_permanentes_repository",
    "src.infrastructure.repositories.instrucciones_repository",
    "src.infrastructure.repositories.fragmentos_repository",
    "src.infrastructure.evals.conversation_shots",
    "src.application.rag_service",
    "src.application.seguimiento_service",
    "src.infrastructure.repositories.envios_repository",
    "src.infrastructure.repositories.clientes_whatsapp_repo",
    "src.infrastructure.repositories.plantillas_repository",
    "src.infrastructure.repositories.palabras_clave_repository",
    "src.infrastructure.repositories.precios_repository",
)

# Módulos que hacen `from ... import redis_client`. Igual que con Postgres, el
# nombre queda copiado en CADA módulo al importarlo, así que sustituirlo solo en
# `buffer_service` no sirve: el que ya lo importó sigue con el cliente real.
_MODULOS_CON_REDIS = (
    "src.application.buffer_service",
    "src.application.seguimiento_service",
    "src.application.reminder_service",
    "src.application.runtime_context",
    "src.application.conversation_orchestrator",
    "src.application.conversation_reset",
    "src.infrastructure.repositories.conversation_state_repo",
    "src.infrastructure.tasks.celery_app",
    "src.infrastructure.channels.inbound_registry",
    "src.infrastructure.channels.outbound_registry",
    "src.infrastructure.channels.outbound_coordinator",
    "src.application.drenaje_recordatorios",
)

# Valor inocuo por función: "no escribió nada" / "no encontró nada".
#
# `run_query` devuelve None y NO un MagicMock a propósito: lo usa
# `postgres_user_repo.is_blocked`, y un MagicMock es truthy — daría "bloqueado"
# en todos los tests y cortaría los flujos justo antes de lo que miden.
_RETORNOS = {"ejecutar": 0, "consultar": [], "consultar_uno": None, "run_query": None}


@pytest.fixture(autouse=True)
def _aisla_servicios(monkeypatch):
    import importlib
    from src.application.project_context import ambito_proyecto

    # Redis en memoria, UNO solo compartido por todos los módulos. Dos motivos
    # para que sea `fakeredis` y no un MagicMock:
    #
    # - Si cada módulo tuviera su doble, lo que un test escribe entrando por
    #   `buffer_service` no lo vería `conversation_orchestrator`.
    # - Varios flujos se apoyan en la SEMÁNTICA de Redis como garantía: el
    #   `SET NX` que hace que un reporte se cree una sola vez, el TTL del
    #   candado, los scripts Lua del buffer. Con un MagicMock todo devuelve algo
    #   truthy y esas garantías pasarían el test sin estar probadas.
    import fakeredis

    redis_falso = fakeredis.FakeRedis(decode_responses=True)
    for nombre_modulo in _MODULOS_CON_REDIS:
        try:
            modulo = importlib.import_module(nombre_modulo)
        except Exception:
            continue
        if hasattr(modulo, "redis_client"):
            monkeypatch.setattr(modulo, "redis_client", redis_falso)

    # Nada sale hacia el worker. Se parchea en la clase base de Celery para que
    # cubra TODAS las tareas, presentes y futuras, sin lista que mantener.
    from celery.app.task import Task

    monkeypatch.setattr(Task, "apply_async", MagicMock(name="apply_async"))
    monkeypatch.setattr(Task, "delay", MagicMock(name="delay"))

    # Los tests existentes de tareas no deben depender de la hora a la que se
    # ejecuta pytest. Los casos específicos del horario repatchan estas tres
    # funciones; el cálculo puro se prueba sin dobles en su propio módulo.
    tareas = importlib.import_module("src.infrastructure.tasks.celery_app")
    monkeypatch.setattr(tareas, "segundos_hasta_horario_permitido", MagicMock(return_value=0))
    from src.application.horario_recordatorios import PlanificacionRecordatorio

    monkeypatch.setattr(
        tareas,
        "planificar_recordatorio",
        MagicMock(side_effect=lambda segundos: PlanificacionRecordatorio(int(segundos), False)),
    )
    monkeypatch.setattr(
        tareas,
        "planificar_secuencia",
        MagicMock(
            side_effect=lambda segundos: [
                PlanificacionRecordatorio(int(valor), False) for valor in segundos
            ]
        ),
    )
    from src.application.drenaje_recordatorios import TurnoDrenaje

    monkeypatch.setattr(
        tareas,
        "solicitar_turno_drenaje",
        MagicMock(return_value=TurnoDrenaje()),
    )

    for nombre_modulo in _MODULOS_CON_POSTGRES:
        try:
            modulo = importlib.import_module(nombre_modulo)
        except Exception:
            continue
        for funcion, retorno in _RETORNOS.items():
            if hasattr(modulo, funcion):
                monkeypatch.setattr(modulo, funcion, MagicMock(return_value=retorno))

    # Sin una base simulada completa, el catálogo debe ejercer el respaldo
    # histórico de mensajes.json. Los casos específicos de Postgres repatchan
    # estas tres fronteras con filas concretas.
    fragmentos_repo = importlib.import_module(
        "src.infrastructure.repositories.fragmentos_repository"
    )
    monkeypatch.setattr(fragmentos_repo, "permitidos", MagicMock(return_value=None))
    monkeypatch.setattr(fragmentos_repo, "obtener", MagicMock(return_value=False))
    monkeypatch.setattr(fragmentos_repo, "variante_de", MagicMock(return_value=None))

    with ambito_proyecto(1):
        yield
