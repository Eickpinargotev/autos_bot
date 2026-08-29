"""La intervención desde teléfono y panel produce exactamente el mismo traspaso."""

from unittest.mock import MagicMock, patch

from src.application import human_intervention
from src.application.conversation_orchestrator import ConversationOrchestrator
from src.domain.entities import Channel, InboundMessage, MessageType


def test_registra_como_dueno_pausa_ia_y_cancela_recordatorios():
    repo = MagicMock()
    with patch.object(
        human_intervention.ConversationLogRepository, "append_message"
    ) as log, patch.object(
        human_intervention.seguimiento_service, "registrar_mensaje"
    ), patch.object(
        human_intervention.seguimiento_service, "registrar_intervencion_humana"
    ), patch.object(
        human_intervention, "PostgresUserRepo", return_value=repo
    ), patch.object(
        human_intervention, "clear_user_runtime_context"
    ) as limpiar, patch.object(
        human_intervention.BufferService, "get_and_clear_buffer"
    ) as vaciar:
        human_intervention.registrar(Channel.WHATSAPP, "506", "Le atiendo yo")

    assert log.call_args.kwargs["message"]["author"] == "dueño"
    repo.block_user.assert_called_once_with(
        "506", reason=human_intervention.MOTIVO_PAUSA_IA, days=12,
        channel=Channel.WHATSAPP,
    )
    limpiar.assert_called_once_with(
        Channel.WHATSAPP, "506", cancel_scheduled=True, clear_reports=False
    )
    vaciar.assert_called_once_with("506", Channel.WHATSAPP)


def test_la_media_del_dueno_se_guarda_con_tipo_y_contexto_visibles():
    repo = MagicMock()
    with patch.object(
        human_intervention.ConversationLogRepository, "append_message"
    ) as log, patch.object(
        human_intervention.seguimiento_service, "registrar_mensaje"
    ), patch.object(
        human_intervention.seguimiento_service, "registrar_intervencion_humana"
    ), patch.object(
        human_intervention, "PostgresUserRepo", return_value=repo
    ), patch.object(
        human_intervention, "clear_user_runtime_context"
    ), patch.object(
        human_intervention.BufferService, "get_and_clear_buffer"
    ):
        human_intervention.registrar(
            Channel.WHATSAPP,
            "506",
            "",
            message_type=MessageType.IMAGE,
            quoted_text="Este es el vehículo",
        )

    mensaje = log.call_args.kwargs["message"]
    assert mensaje["message_type"] == "image"
    assert mensaje["text"] == "[Image]"
    assert mensaje["quoted_text"] == "Este es el vehículo"


def test_el_bloqueo_ocurre_aunque_falle_el_registro_del_mensaje():
    repo = MagicMock()
    with patch.object(
        human_intervention, "PostgresUserRepo", return_value=repo
    ), patch.object(
        human_intervention, "clear_user_runtime_context"
    ), patch.object(
        human_intervention.BufferService, "get_and_clear_buffer"
    ), patch.object(
        human_intervention.ConversationLogRepository,
        "append_message",
        side_effect=RuntimeError("trazabilidad no disponible"),
    ), patch.object(
        human_intervention.seguimiento_service, "registrar_mensaje"
    ), patch.object(
        human_intervention.seguimiento_service, "registrar_intervencion_humana"
    ):
        human_intervention.registrar(Channel.WHATSAPP, "506", "Le atiendo yo")

    repo.block_user.assert_called_once()


def test_despues_de_escribir_el_dueno_las_ramas_no_prioritarias_no_intervienen():
    class RepoConEstado:
        bloqueado_por_humano = False

        def block_user(self, user_id, reason="", **kwargs):
            if reason == human_intervention.MOTIVO_PAUSA_IA:
                self.bloqueado_por_humano = True

        def is_blocked_for_reason(self, user_id, reason, **kwargs):
            return self.bloqueado_por_humano and reason == human_intervention.MOTIVO_PAUSA_IA

        def is_blocked(self, *args, **kwargs):
            return self.bloqueado_por_humano

    repo = RepoConEstado()
    dueno = InboundMessage(
        channel=Channel.WHATSAPP,
        user_id="50688888888",
        user_name="Asesor",
        message_type=MessageType.TEXT,
        text="Yo continúo con el cliente",
        from_me=True,
    )
    entradas = (
        (MessageType.TEXT, "una consulta normal"),
        (MessageType.TEXT, "mira https://ejemplo.com"),
        (MessageType.AUDIO, ""),
        (MessageType.IMAGE, ""),
        (MessageType.DOCUMENT, ""),
        (MessageType.VIDEO, ""),
        (MessageType.OTHER, ""),
        (MessageType.STICKER, ""),
    )

    with patch.object(
        human_intervention, "PostgresUserRepo", return_value=repo
    ), patch(
        "src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo
    ), patch.object(
        human_intervention, "clear_user_runtime_context"
    ), patch.object(
        human_intervention.BufferService, "get_and_clear_buffer"
    ), patch.object(
        human_intervention.ConversationLogRepository, "append_message"
    ), patch.object(
        human_intervention.seguimiento_service, "registrar_mensaje"
    ), patch.object(
        human_intervention.seguimiento_service, "registrar_intervencion_humana"
    ), patch(
        "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
    ), patch(
        "src.application.conversation_orchestrator.ConversationLogRepository.log_tool_event"
    ), patch(
        "src.infrastructure.repositories.bloqueos_permanentes_repository.esta_bloqueado",
        return_value=False,
    ), patch.object(
        ConversationOrchestrator, "_responder_por_media"
    ) as media, patch.object(
        ConversationOrchestrator, "_handle_audio"
    ) as audio:
        assert ConversationOrchestrator().handle(dueno) == []
        assert repo.bloqueado_por_humano is True

        for tipo, contenido in entradas:
            mensaje = InboundMessage(
                channel=Channel.WHATSAPP,
                user_id=dueno.user_id,
                user_name="Cliente",
                message_type=tipo,
                text=contenido,
            )
            assert ConversationOrchestrator().handle(mensaje) == []

        ingreso_grupo = InboundMessage(
            channel=Channel.WHATSAPP,
            user_id=dueno.user_id,
            user_name="Cliente",
            message_type=MessageType.OTHER,
            text="",
            event_type="group_join",
        )
        assert ConversationOrchestrator().handle(ingreso_grupo) == []

    media.assert_not_called()
    audio.assert_not_called()
