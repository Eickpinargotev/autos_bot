"""Regresión con LLM real: la respuesta del RAG no revela mecánica interna.

Reproduce el transcript del 2026-07-19: el cliente pidió la cita del práctico
B1 y el bot respondió "los requisitos que sí aparecen en los chunks son..." y
narró el proceso en tercera persona ("se le envía el formulario..."). Los
chunks del fixture están escritos a propósito en ese lenguaje meta (como los
reales de la base de conocimiento) para verificar que el modelo NO lo espeja.

Consume tokens reales: corre solo con RUN_LLM_TESTS=1 (ver CLAUDE.md §2).
"""

import os
import time
from unittest.mock import patch

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from openai import OpenAI

from src.application.rag_service import RagService
from src.core.config import settings
from tests.regression.test_unified_agent_llm import requires_llm

CHUNKS_B1 = [
    {
        "external_id": "kb:requisitos-b1",
        "score": 0.9,
        "text": (
            "Pregunta: ¿Cuáles son los requisitos para la prueba práctica B1?\n"
            "Respuesta: Tener 18 años cumplidos, aprobar el curso teórico de "
            "educación vial, presentar dictamen médico digital u oficial y "
            "tener identificación vigente y en perfecto estado."
        ),
    },
    {
        "external_id": "kb:citas-practico",
        "score": 0.85,
        "text": (
            "Pregunta: ¿Cómo se agenda la cita de la prueba de manejo?\n"
            "Respuesta: Se le pregunta al cliente si ya tiene cita agendada. "
            "Si no la tiene, se le envía el formulario de solicitud de citas "
            "para prueba de manejo. Si ya la tiene, se le envía el formulario "
            "de reservación de vehículos."
        ),
    },
]

HISTORIAL = [
    {
        "cliente": (
            "hola, buenos dias como esta profe, ya ahora si tengo 18 años, vengo "
            "para que me ayude a sacar la cita del practico b1, digame que "
            "información ocupa y yo se la facilito"
        ),
        "bot": [],
    }
]

# Vocabulario de maquinaria interna que jamás debe llegar al cliente.
JERGA_PROHIBIDA = (
    "chunk",
    "fragmento",
    "el sistema",
    "base de conocimiento",
    "información disponible",
    "según los datos",
    "se le envía",
)


def _service() -> RagService:
    service = RagService.__new__(RagService)
    service.openai = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=settings.OPENAI_TIMEOUT_SECONDS,
        max_retries=settings.OPENAI_MAX_RETRIES,
    )
    service.client = object()  # answer_question solo exige que exista
    service._last_sync = time.monotonic()
    return service


@requires_llm
def test_respuesta_rag_no_revela_mecanica_interna():
    service = _service()
    with patch.object(service, "sync_if_needed"), patch.object(service, "search", return_value=CHUNKS_B1):
        result = service.answer_question(
            question=(
                "vengo para que me ayude a sacar la cita del practico b1, "
                "digame que información ocupa y yo se la facilito"
            ),
            context="ALQUILER",
            conversation_history=HISTORIAL,
        )

    assert result.has_answer, "Con chunks que respaldan la respuesta, debe responder"
    respuesta = result.answer.lower()
    for termino in JERGA_PROHIBIDA:
        assert termino not in respuesta, (
            f"La respuesta al cliente reveló jerga interna ({termino!r}): {result.answer}"
        )
    # Debe seguir siendo una respuesta útil (los requisitos de verdad).
    assert "18" in respuesta
    assert "dictamen" in respuesta
