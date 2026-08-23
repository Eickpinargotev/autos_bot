"""Efectos únicos de una intervención del dueño, venga del teléfono o del panel."""

from src.application import seguimiento_service
from src.application.buffer_service import BufferService
from src.application.runtime_context import clear_user_runtime_context
from src.domain.entities import Channel
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository
from src.infrastructure.repositories.postgres_user_repo import PostgresUserRepo

DIAS_PAUSA_IA = 12


def registrar(channel: Channel | str, user_id: str, texto: str) -> None:
    canal = channel if isinstance(channel, Channel) else Channel(channel)
    ConversationLogRepository.append_message(
        client_id=user_id,
        canal=canal,
        message={
            "direction": "outbound",
            "author": "dueño",
            "sender_id": "humano",
            "sender_name": "Asesor",
            "message_type": "text",
            "text": texto,
            "event_type": "intervencion_humana",
        },
    )
    seguimiento_service.registrar_mensaje(
        client_id=user_id, canal=canal, autor="dueño", texto=texto
    )
    seguimiento_service.registrar_intervencion_humana(user_id, canal)
    PostgresUserRepo().block_user(
        user_id,
        reason="Intervención de un asesor humano",
        days=DIAS_PAUSA_IA,
        channel=canal,
    )
    clear_user_runtime_context(canal, user_id, cancel_scheduled=True, clear_reports=False)
    BufferService.get_and_clear_buffer(user_id, canal)
