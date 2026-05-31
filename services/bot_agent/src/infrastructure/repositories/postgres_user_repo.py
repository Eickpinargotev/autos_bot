import psycopg2
from src.core.config import settings
from src.domain.entities import Channel

def subject_id(channel: Channel | str, user_id: str) -> str:
    channel_value = channel.value if isinstance(channel, Channel) else channel
    return f"{channel_value}:{user_id}"

class PostgresUserRepo:
    def __init__(self):
        self.conn = psycopg2.connect(settings.POSTGRES_URL)
        self._create_tables()

    def _create_tables(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users_blocked (
                    user_id VARCHAR(50) PRIMARY KEY,
                    reason TEXT,
                    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dictamen_registered_users (
                    subject_id VARCHAR(80) PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.commit()

    def block_user(self, user_id: str, reason: str = "", days: int = 0, hours: int = 0, channel: Channel | str = Channel.TELEGRAM):
        expires_at = None
        if days > 0 or hours > 0:
            import datetime
            expires_at = datetime.datetime.now() + datetime.timedelta(days=days, hours=hours)
            
        blocked_id = subject_id(channel, user_id)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users_blocked (user_id, reason, expires_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET reason = EXCLUDED.reason, expires_at = EXCLUDED.expires_at",
                (blocked_id, reason, expires_at)
            )
            self.conn.commit()

    def unblock_user(self, user_id: str, channel: Channel | str = Channel.TELEGRAM):
        blocked_id = subject_id(channel, user_id)
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM users_blocked WHERE user_id IN (%s, %s)", (blocked_id, user_id))
            self.conn.commit()

    def is_blocked(self, user_id: str, channel: Channel | str = Channel.TELEGRAM) -> bool:
        blocked_id = subject_id(channel, user_id)
        ids_to_check = [blocked_id]
        if (channel.value if isinstance(channel, Channel) else channel) == Channel.TELEGRAM.value:
            ids_to_check.append(user_id)

        with self.conn.cursor() as cur:
            cur.execute("SELECT user_id, expires_at FROM users_blocked WHERE user_id = ANY(%s)", (ids_to_check,))
            row = cur.fetchone()
            if row:
                stored_user_id, expires_at = row
                if expires_at:
                    import datetime
                    if datetime.datetime.now() > expires_at:
                        # Block expired, delete it
                        cur.execute("DELETE FROM users_blocked WHERE user_id = %s", (stored_user_id,))
                        self.conn.commit()
                        return False
                return True
            return False
