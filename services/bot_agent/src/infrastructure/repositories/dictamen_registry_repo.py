import psycopg2

from src.core.config import settings
from src.domain.entities import Channel
from src.infrastructure.repositories.postgres_user_repo import subject_id


class DictamenRegistryRepo:
    def __init__(self):
        self.conn = psycopg2.connect(settings.POSTGRES_URL)
        self._create_tables()

    def _create_tables(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dictamen_registered_users (
                    subject_id VARCHAR(80) PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.commit()

    def is_registered(self, channel: Channel | str, user_id: str) -> bool:
        sid = subject_id(channel, user_id)
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM dictamen_registered_users WHERE subject_id = %s", (sid,))
            return cur.fetchone() is not None

    def register(self, channel: Channel | str, user_id: str):
        sid = subject_id(channel, user_id)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dictamen_registered_users (subject_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (sid,),
            )
            self.conn.commit()
