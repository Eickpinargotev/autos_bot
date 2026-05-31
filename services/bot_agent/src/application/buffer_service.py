import redis
from src.core.config import settings
from src.domain.entities import Channel

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

def scoped_key(prefix: str, channel: Channel | str, user_id: str) -> str:
    channel_value = channel.value if isinstance(channel, Channel) else channel
    return f"{prefix}:{channel_value}:{user_id}"

class BufferService:
    @staticmethod
    def add_message(user_id: str, text: str, channel: Channel | str = Channel.TELEGRAM):
        key = scoped_key("buffer", channel, user_id)
        # Append message to buffer
        redis_client.rpush(key, text)
        # We don't set expire here; Celery task will handle processing.

    @staticmethod
    def get_and_clear_buffer(user_id: str, channel: Channel | str = Channel.TELEGRAM) -> str:
        key = scoped_key("buffer", channel, user_id)
        messages = redis_client.lrange(key, 0, -1)
        redis_client.delete(key)
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
