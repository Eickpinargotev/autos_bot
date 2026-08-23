import datetime

from src.application.project_context import proyecto_actual
from src.domain.entities import Channel
from src.infrastructure.repositories import bloqueos_permanentes_repository
from src.infrastructure.repositories.postgres_conn import _SharedConnection, run_query


def subject_id(channel: Channel | str, user_id: str) -> str:
    channel_value = channel.value if isinstance(channel, Channel) else channel
    return f"{channel_value}:{user_id}"


class PostgresUserRepo:
    def __init__(self):
        # Calienta la conexión compartida (y crea las tablas la primera vez).
        _SharedConnection.get()

    def block_user(self, user_id: str, reason: str = "", days: int = 0, hours: int = 0, channel: Channel | str = Channel.TELEGRAM):
        proyecto_id = proyecto_actual()
        if not proyecto_id:
            return
        expires_at = None
        if days > 0 or hours > 0:
            expires_at = datetime.datetime.now() + datetime.timedelta(days=days, hours=hours)

        blocked_id = subject_id(channel, user_id)

        def op(conn):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users_blocked (proyecto_id, user_id, reason, expires_at) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (proyecto_id, user_id) DO UPDATE SET reason = EXCLUDED.reason, expires_at = EXCLUDED.expires_at",
                    (proyecto_id, blocked_id, reason, expires_at),
                )

        run_query(op)

    def unblock_user(self, user_id: str, channel: Channel | str = Channel.TELEGRAM):
        proyecto_id = proyecto_actual()
        if not proyecto_id:
            return
        blocked_id = subject_id(channel, user_id)

        def op(conn):
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM users_blocked WHERE proyecto_id = %s AND user_id IN (%s, %s)",
                    (proyecto_id, blocked_id, user_id),
                )

        run_query(op)

    def is_blocked(
        self,
        user_id: str,
        channel: Channel | str = Channel.TELEGRAM,
        include_permanent: bool = True,
    ) -> bool:
        proyecto_id = proyecto_actual()
        if not proyecto_id:
            return False
        blocked_id = subject_id(channel, user_id)
        ids_to_check = [blocked_id]
        if (channel.value if isinstance(channel, Channel) else channel) == Channel.TELEGRAM.value:
            ids_to_check.append(user_id)

        def op(conn) -> bool:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, expires_at FROM users_blocked WHERE proyecto_id = %s AND user_id = ANY(%s)",
                    (proyecto_id, ids_to_check),
                )
                row = cur.fetchone()
                if row:
                    stored_user_id, expires_at = row
                    if expires_at and datetime.datetime.now() > expires_at:
                        # Block expired, delete it
                        cur.execute(
                            "DELETE FROM users_blocked WHERE proyecto_id = %s AND user_id = %s",
                            (proyecto_id, stored_user_id),
                        )
                        return False
                    return True
                return False

        temporal = run_query(op)
        return bool(temporal) or (
            include_permanent
            and bloqueos_permanentes_repository.esta_bloqueado(user_id, channel)
        )
