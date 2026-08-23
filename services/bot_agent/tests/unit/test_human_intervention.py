"""La intervención desde teléfono y panel produce exactamente el mismo traspaso."""

from unittest.mock import MagicMock, patch

from src.application import human_intervention
from src.domain.entities import Channel


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
        "506", reason="Intervención de un asesor humano", days=12,
        channel=Channel.WHATSAPP,
    )
    limpiar.assert_called_once_with(
        Channel.WHATSAPP, "506", cancel_scheduled=True, clear_reports=False
    )
    vaciar.assert_called_once_with("506", Channel.WHATSAPP)
