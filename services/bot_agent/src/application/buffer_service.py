import redis
from src.core.config import settings
from src.domain.entities import Channel

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

# TTL de seguridad para las claves del buffer (se borran solas si una tarea
# nunca llega a drenarlas). Holgado respecto a MESSAGE_BUFFER_SECONDS.
_BUFFER_TTL_SECONDS = 600

# Lee y borra el buffer en una sola operación atómica para que dos tareas de
# Celery concurrentes (una por cada mensaje recibido en la ráfaga) no puedan
# leer el mismo contenido y procesarlo dos veces. La que llega primero se lleva
# todos los mensajes; las demás reciben una lista vacía.
_DRAIN_BUFFER_LUA = """
local messages = redis.call('lrange', KEYS[1], 0, -1)
redis.call('del', KEYS[1])
return messages
"""
_drain_buffer = redis_client.register_script(_DRAIN_BUFFER_LUA)

# Debounce real: cada mensaje incrementa un contador de secuencia y agenda una
# tarea de procesamiento con ese número. Solo procesa la tarea cuyo número
# coincide con el contador actual (es decir, la del ÚLTIMO mensaje de la
# ráfaga); las tareas de mensajes anteriores quedan obsoletas y se descartan.
# Así el procesamiento ocurre una sola vez, 15s después del último mensaje.
# Todo en un único script para que la comprobación y el drenado sean atómicos.
_DRAIN_IF_CURRENT_LUA = """
local current = redis.call('get', KEYS[2])
if current == false or current ~= ARGV[1] then
    return nil
end
local messages = redis.call('lrange', KEYS[1], 0, -1)
redis.call('del', KEYS[1])
redis.call('del', KEYS[2])
return messages
"""
_drain_if_current = redis_client.register_script(_DRAIN_IF_CURRENT_LUA)

def scoped_key(prefix: str, channel: Channel | str, user_id: str) -> str:
    channel_value = channel.value if isinstance(channel, Channel) else channel
    return f"{prefix}:{channel_value}:{user_id}"

class BufferService:
    @staticmethod
    def add_message(user_id: str, text: str, channel: Channel | str = Channel.TELEGRAM) -> int:
        """Acumula el mensaje y devuelve el número de secuencia de la ráfaga.

        Ese número debe pasarse a la tarea de procesamiento para implementar el
        debounce: solo la tarea con el número más reciente terminará procesando.
        """
        buffer_key = scoped_key("buffer", channel, user_id)
        seq_key = scoped_key("buffer_seq", channel, user_id)
        pipe = redis_client.pipeline()
        pipe.rpush(buffer_key, text)
        pipe.incr(seq_key)
        pipe.expire(buffer_key, _BUFFER_TTL_SECONDS)
        pipe.expire(seq_key, _BUFFER_TTL_SECONDS)
        results = pipe.execute()
        return int(results[1])

    @staticmethod
    def drain_if_current(user_id: str, channel: Channel | str, seq: int) -> str | None:
        """Drena el buffer solo si `seq` sigue siendo el último mensaje recibido.

        Devuelve el texto concatenado si esta tarea es la vigente, o None si ya
        llegó un mensaje más nuevo (tarea obsoleta que debe descartarse).
        """
        buffer_key = scoped_key("buffer", channel, user_id)
        seq_key = scoped_key("buffer_seq", channel, user_id)
        messages = _drain_if_current(keys=[buffer_key, seq_key], args=[str(seq)])
        if messages is None:
            return None
        return " ".join(messages)

    @staticmethod
    def get_and_clear_buffer(user_id: str, channel: Channel | str = Channel.TELEGRAM) -> str:
        key = scoped_key("buffer", channel, user_id)
        messages = _drain_buffer(keys=[key]) or []
        return " ".join(messages)

    @staticmethod
    def add_image_info_count(user_id: str, channel: Channel | str = Channel.TELEGRAM) -> bool:
        """
        Returns True if can send info message, False if limit exceeded (2 per 5 min)
        """
        key = scoped_key("img_limit", channel, user_id)
        count = redis_client.get(key)
        if count is None:
            redis_client.setex(key, 300, 1) # 5 mins
            return True
        elif int(count) < settings.MAX_INFO_MESSAGES_PER_5_MIN:
            redis_client.incr(key)
            return True
        return False
