"""Efectos únicos de una intervención del dueño, venga del teléfono o del panel."""

from src.application import seguimiento_service
from src.application.buffer_service import BufferService
from src.application.runtime_context import clear_user_runtime_context
from src.domain.entities import Channel, MessageType
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository
from src.infrastructure.repositories.postgres_user_repo import PostgresUserRepo

DIAS_PAUSA_IA = 12
MOTIVO_PAUSA_IA = "Intervención de un asesor humano"


def registrar(
    channel: Channel | str,
    user_id: str,
    texto: str,
    message_type: MessageType | str = MessageType.TEXT,
    quoted_text: str = "",
) -> None:
    canal = channel if isinstance(channel, Channel) else Channel(channel)
    tipo = message_type.value if isinstance(message_type, MessageType) else str(message_type)
    texto_visible = str(texto or "").strip() or f"[{tipo.capitalize()}]"
    # Este es el efecto crítico y debe ocurrir ANTES que trazabilidad, métricas
    # o limpieza. Si alguno de esos efectos secundarios falla, el bot igual
    # queda en silencio desde el instante en que el dueño tomó la conversación.
    PostgresUserRepo().block_user(
        user_id,
        reason=MOTIVO_PAUSA_IA,
        days=DIAS_PAUSA_IA,
        channel=canal,
    )

    efectos = (
        lambda: clear_user_runtime_context(
            canal, user_id, cancel_scheduled=True, clear_reports=False
        ),
        lambda: BufferService.get_and_clear_buffer(user_id, canal),
        lambda: ConversationLogRepository.append_message(
            client_id=user_id,
            canal=canal,
            message={
                "direction": "outbound",
                "author": "dueño",
                "sender_id": "humano",
                "sender_name": "Asesor",
                "message_type": tipo,
                "text": texto_visible,
                "quoted_text": quoted_text or "",
                "event_type": "intervencion_humana",
            },
        ),
        lambda: seguimiento_service.registrar_mensaje(
            client_id=user_id, canal=canal, autor="dueño", texto=texto_visible
        ),
        lambda: seguimiento_service.registrar_intervencion_humana(user_id, canal),
    )
    for efecto in efectos:
        try:
            efecto()
        except Exception as exc:
            print(f"Error posterior al bloqueo por intervención humana: {exc}")
