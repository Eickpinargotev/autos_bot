import json
from dataclasses import asdict, dataclass, field

from src.application.buffer_service import redis_client, scoped_key
from src.core.config import settings
from src.domain.entities import Channel


# TTL deslizante del estado/historial en Redis: cada vez que se guarda (es decir,
# cada interacción) se renueva. Tras N días sin actividad, Redis lo expira solo.
CONVERSATION_STATE_TTL_SECONDS = settings.CONVERSATION_RETENTION_DAYS * 24 * 60 * 60


@dataclass
class ConversationState:
    flow: str = "INICIO"
    node: str = ""
    last_question: str = ""
    awaiting_reply: bool = False
    pending_report: str = ""
    reminder_task_ids: list[str] = field(default_factory=list)
    last_messages: list[str] = field(default_factory=list)
    user_name: str = "Desconocido"
    reminder_level: int = 0
    conversation_history: list[dict] = field(default_factory=list)
    # Especialista dueño de la conversación (routing pegajoso del supervisor).
    # Vacío = sin especialista: el turno entra por el supervisor.
    active_agent: str = ""


class ConversationStateRepo:
    @staticmethod
    def get(channel: Channel | str, user_id: str) -> ConversationState:
        raw = redis_client.get(scoped_key("conversation_state", channel, user_id))
        if not raw:
            return ConversationState()
        try:
            data = json.loads(raw)
            return ConversationState(**data)
        except Exception:
            return ConversationState()

    @staticmethod
    def set(channel: Channel | str, user_id: str, state: ConversationState):
        redis_client.set(
            scoped_key("conversation_state", channel, user_id),
            json.dumps(asdict(state), ensure_ascii=False),
            ex=CONVERSATION_STATE_TTL_SECONDS,
        )

    @staticmethod
    def clear(channel: Channel | str, user_id: str):
        redis_client.delete(scoped_key("conversation_state", channel, user_id))
