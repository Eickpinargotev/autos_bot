import datetime
import threading

import psycopg2

from src.core.config import settings
from src.domain.entities import Channel


def subject_id(channel: Channel | str, user_id: str) -> str:
    channel_value = channel.value if isinstance(channel, Channel) else channel
    return f"{channel_value}:{user_id}"


class _SharedConnection:
    """Conexión a Postgres compartida por proceso.

    Antes cada instancia de repositorio abría su propia conexión (una por
    mensaje entrante) y nunca la cerraba: bajo carga se agotan las conexiones
    del servidor. psycopg2 permite compartir una conexión entre hilos (los
    cursores no); el candado solo serializa la creación/reconexión.

    La conexión trabaja en autocommit: todas las operaciones de estos repos son
    sentencias sueltas, y así dos hilos no pueden confirmar transacciones a
    medias del otro.
    """

    _lock = threading.Lock()
    _conn = None

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._conn is None or cls._conn.closed:
                conn = psycopg2.connect(settings.POSTGRES_URL)
                conn.autocommit = True
                _create_tables(conn)
                cls._conn = conn
            return cls._conn

    @classmethod
    def discard(cls):
        with cls._lock:
            if cls._conn is not None:
                try:
                    cls._conn.close()
                except Exception:
                    pass
                cls._conn = None


def run_query(operation):
    """Ejecuta `operation(conn)`; si la conexión murió, reconecta y reintenta una vez."""
    try:
        return operation(_SharedConnection.get())
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        _SharedConnection.discard()
        return operation(_SharedConnection.get())


def _create_tables(conn):
    with conn.cursor() as cur:
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


class PostgresUserRepo:
    def __init__(self):
        # Calienta la conexión compartida (y crea las tablas la primera vez).
        _SharedConnection.get()

    def block_user(self, user_id: str, reason: str = "", days: int = 0, hours: int = 0, channel: Channel | str = Channel.TELEGRAM):
        expires_at = None
        if days > 0 or hours > 0:
            expires_at = datetime.datetime.now() + datetime.timedelta(days=days, hours=hours)

        blocked_id = subject_id(channel, user_id)

        def op(conn):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users_blocked (user_id, reason, expires_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET reason = EXCLUDED.reason, expires_at = EXCLUDED.expires_at",
                    (blocked_id, reason, expires_at),
                )

        run_query(op)

    def unblock_user(self, user_id: str, channel: Channel | str = Channel.TELEGRAM):
        blocked_id = subject_id(channel, user_id)

        def op(conn):
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users_blocked WHERE user_id IN (%s, %s)", (blocked_id, user_id))

        run_query(op)

    def is_blocked(self, user_id: str, channel: Channel | str = Channel.TELEGRAM) -> bool:
        blocked_id = subject_id(channel, user_id)
        ids_to_check = [blocked_id]
        if (channel.value if isinstance(channel, Channel) else channel) == Channel.TELEGRAM.value:
            ids_to_check.append(user_id)

        def op(conn) -> bool:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, expires_at FROM users_blocked WHERE user_id = ANY(%s)", (ids_to_check,))
                row = cur.fetchone()
                if row:
                    stored_user_id, expires_at = row
                    if expires_at and datetime.datetime.now() > expires_at:
                        # Block expired, delete it
                        cur.execute("DELETE FROM users_blocked WHERE user_id = %s", (stored_user_id,))
                        return False
                    return True
                return False

        return run_query(op)
