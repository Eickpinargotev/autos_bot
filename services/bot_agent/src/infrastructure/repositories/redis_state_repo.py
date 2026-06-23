import redis
from src.core.config import settings
from src.domain.entities import Channel, UserState
from src.application.buffer_service import scoped_key

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

# Misma ventana de retención que el historial: el estado FSM expira tras N días
# de inactividad (se renueva en cada interacción al volver a escribirlo).
STATE_TTL_SECONDS = settings.CONVERSATION_RETENTION_DAYS * 24 * 60 * 60

class RedisStateRepo:
    @staticmethod
    def get_state(user_id: str, channel: Channel | str = Channel.TELEGRAM) -> UserState:
        state_str = redis_client.get(scoped_key("state", channel, user_id))
        if state_str:
            try:
                return UserState(state_str)
            except ValueError:
                pass
        return UserState.INICIO

    @staticmethod
    def set_state(user_id: str, state: UserState, channel: Channel | str = Channel.TELEGRAM):
        redis_client.set(scoped_key("state", channel, user_id), state.value, ex=STATE_TTL_SECONDS)

    @staticmethod
    def reset_state(user_id: str, channel: Channel | str = Channel.TELEGRAM):
        redis_client.delete(scoped_key("state", channel, user_id))
