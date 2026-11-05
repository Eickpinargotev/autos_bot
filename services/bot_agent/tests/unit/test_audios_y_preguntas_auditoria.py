"""Registro de audios pausados y evidencias de preguntas, sin servicios externos."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.application.conversation_orchestrator import ConversationOrchestrator
from src.domain.entities import Channel, InboundMessage, MessageType
from src.infrastructure.repositories import conversation_audit_repository as audit
from src.infrastructure.repositories.unanswered_question_repository import UnansweredQuestionRepository


@pytest.mark.parametrize("bloqueo", ["permanente", "humano", "publicidad"])
def test_audio_bloqueado_siempre_se_registra_sin_transcribir_ni_responder(bloqueo):
    mensaje = InboundMessage(
        channel=Channel.WHATSAPP, user_id="50612345678", user_name="Jeniffer",
        message_type=MessageType.AUDIO, message_id="audio-123", raw_payload={"data": {}},
    )
    repo = MagicMock()
    repo.is_blocked_for_reason.return_value = bloqueo == "humano"
    repo.is_blocked.return_value = bloqueo == "publicidad"
    base = "src.application.conversation_orchestrator"
    with patch(
        "src.infrastructure.repositories.bloqueos_permanentes_repository.esta_bloqueado",
        return_value=bloqueo == "permanente",
    ), patch(f"{base}.PostgresUserRepo", return_value=repo), patch(
        f"{base}.ConversationLogRepository.log_inbound",
    ) as registro, patch(f"{base}.transcribir_nota_de_voz.apply_async") as transcribir:
        acciones = ConversationOrchestrator().handle(mensaje)
    assert acciones == []
    transcribir.assert_not_called()
    registro.assert_called_once_with(
        client_id="50612345678", canal=Channel.WHATSAPP, sender_name="Jeniffer",
        message_type=MessageType.AUDIO, event_type="audio_bloqueado",
    )


def test_pregunta_guarda_id_correlacion_y_evidencias_sanitizadas():
    modulo = "src.infrastructure.repositories.unanswered_question_repository"
    eventos = [{"id": 15, "tool_name": "rag.search", "salida": {
        "sources": [{"source_id": "pg:rag_chunks:9", "content": "Requisito", "score": 0.7}],
        "api_key": "SECRETO",
    }}]
    with audit.scope(correlation_id="interaccion-123"), patch(
        f"{modulo}.consultar", return_value=eventos,
    ) as consulta, patch(f"{modulo}.ejecutar", return_value=1) as insertar:
        assert UnansweredQuestionRepository.create(
            "Requisitos A2", client_id="50612345678", canal="whatsapp",
            current_message="¿Qué necesito para A2?", fallback_message="Un asesor le ayudará.",
        )
    assert consulta.call_args.args[1] == (1, "50612345678", "whatsapp", "interaccion-123")
    parametros = insertar.call_args.args[1]
    assert parametros[2:5] == ("50612345678", "whatsapp", "interaccion-123")
    copia = json.loads(parametros[5])
    assert copia["consulta_cliente"] == "¿Qué necesito para A2?"
    assert copia["eventos"][0]["salida"]["api_key"] == "[REDACTADO]"
    assert copia["eventos"][0]["salida"]["sources"][0]["content"] == "Requisito"
