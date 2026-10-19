"""El buscador real entrega las variantes al mismo flujo, con la misma identidad."""

from unittest.mock import MagicMock, patch

import pytest

from src.application.conversation_orchestrator import ConversationOrchestrator
from src.domain.entities import Channel, InboundMessage, MessageType
from src.infrastructure.repositories import palabras_clave_repository


@pytest.fixture
def catalogo(monkeypatch):
    palabras_clave_repository.limpiar_cache()
    palabra = {"id": 71, "palabra": "tareas"}
    piezas = [{"texto": "Texto original", "media_tipo": "imagen", "media_ref": "adjunto"}]

    def consultar(sql, parametros):
        if "FROM palabras_clave " in sql:
            return [palabra]
        assert "FROM palabra_clave_piezas" in sql
        assert parametros == (1, 71, "mensaje")
        return piezas

    monkeypatch.setattr(palabras_clave_repository, "consultar", consultar)
    yield piezas
    palabras_clave_repository.limpiar_cache()


def _mensaje(texto, canal=Channel.WHATSAPP):
    return InboundMessage(
        channel=canal, user_id="50688888888", user_name="Cliente",
        message_type=MessageType.TEXT, text=texto,
    )


@pytest.mark.parametrize("canal", [Channel.WHATSAPP, Channel.TELEGRAM])
@pytest.mark.parametrize("pausado", [False, True])
@pytest.mark.parametrize("texto", ["tareas", "tarea", "TAREAS", "tareas.", "¡tareas!",
                                  "tares", "terea", "tereas"])
def test_variante_reutiliza_flujo_original_una_sola_vez(catalogo, canal, pausado, texto):
    repo = MagicMock()
    repo.is_blocked.return_value = pausado
    repo.is_blocked_for_reason.return_value = pausado
    with patch("src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo), patch(
        "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
    ) as entrada, patch(
        "src.application.conversation_orchestrator.cancel_scheduled_tasks"
    ) as cancelar, patch(
        "src.application.conversation_orchestrator.register_keyword_context"
    ) as contexto, patch(
        "src.application.conversation_orchestrator.KeywordRegistryRepository.register_if_missing"
    ) as registro, patch(
        "src.application.conversation_orchestrator.schedule_keyword_programmed_messages.apply_async"
    ) as agendar, patch(
        "src.application.conversation_orchestrator.seguimiento_service.registrar_uso_codigo"
    ) as uso:
        acciones = ConversationOrchestrator().handle(_mensaje(texto, canal))

    entrada.assert_called_once()
    assert entrada.call_args.kwargs["text"] == texto
    cancelar.assert_called_once_with(canal.value, "50688888888")
    repo.block_user.assert_called_once_with("50688888888", reason="Flujo keyword tareas", channel=canal)
    registro.assert_called_once_with("50688888888", "Cliente", canal, "tareas")
    contexto.assert_called_once_with(canal, "50688888888")
    agendar.assert_called_once_with((canal.value, "50688888888", 71))
    uso.assert_called_once_with("50688888888", canal, origen="keyword", mensajes=1)
    assert len(acciones) == 1
    assert acciones[0].text == "Texto original\nImagen=adjunto"
    assert acciones[0].channel == canal


def test_variante_respeta_bloqueo_permanente(catalogo):
    with patch(
        "src.infrastructure.repositories.bloqueos_permanentes_repository.esta_bloqueado",
        return_value=True,
    ), patch(
        "src.application.conversation_orchestrator.PostgresUserRepo"
    ) as repo, patch(
        "src.application.conversation_orchestrator.schedule_keyword_programmed_messages.apply_async"
    ) as agendar:
        assert ConversationOrchestrator().handle(_mensaje("terea")) == []
    repo.assert_not_called()
    agendar.assert_not_called()


def test_variante_sin_mensajes_no_bloquea_ni_agenda(catalogo):
    catalogo.clear()
    with patch(
        "src.application.conversation_orchestrator.PostgresUserRepo"
    ) as repo, patch(
        "src.application.conversation_orchestrator.schedule_keyword_programmed_messages.apply_async"
    ) as agendar, patch(
        "src.application.conversation_orchestrator.cancel_scheduled_tasks"
    ) as cancelar:
        assert ConversationOrchestrator().handle(_mensaje("terea")) == []
    repo.return_value.block_user.assert_not_called()
    agendar.assert_not_called()
    cancelar.assert_not_called()
