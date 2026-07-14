from src.domain.entities import Channel
from src.infrastructure.repositories.postgres_user_repo import run_query, subject_id


class DictamenRegistryRepo:
    """Registro de usuarios que ya pasaron por el flujo de dictamen.

    Usa la conexión compartida de postgres_user_repo (que también crea la
    tabla dictamen_registered_users) para no abrir una conexión por instancia.
    """

    def is_registered(self, channel: Channel | str, user_id: str) -> bool:
        sid = subject_id(channel, user_id)

        def op(conn) -> bool:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM dictamen_registered_users WHERE subject_id = %s", (sid,))
                return cur.fetchone() is not None

        return run_query(op)

    def register(self, channel: Channel | str, user_id: str):
        sid = subject_id(channel, user_id)

        def op(conn):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO dictamen_registered_users (subject_id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (sid,),
                )

        run_query(op)
