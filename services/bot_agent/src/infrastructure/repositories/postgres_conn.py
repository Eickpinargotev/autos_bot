"""Conexión compartida a Postgres para los repositorios del bot.

Este módulo era parte de `postgres_user_repo`; se extrajo cuando el resto de
repositorios (log de conversación, seguimiento, reportes, keyword, RAG,
facturación) dejaron NocoDB y pasaron a Postgres, para que todos usen la misma
conexión en vez de abrir una por repositorio.

Una conexión por PROCESO, en autocommit. psycopg2 permite compartir la conexión
entre hilos (los cursores no), y el worker de Celery corre con `--pool=threads`:
cada tarea abre su propio cursor sobre la conexión compartida. El candado solo
serializa la creación/reconexión, no las consultas.

El DDL de las tablas vive en el dashboard (`src/db/migrations/`). Aquí no se
crea ni se altera nada, salvo las dos tablas propias del bot que ya existían.
"""

import threading
from typing import Any

import psycopg2
import psycopg2.extras

from src.core.config import settings


class _SharedConnection:
    _lock = threading.Lock()
    _conn = None

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._conn is None or cls._conn.closed:
                conn = psycopg2.connect(settings.POSTGRES_URL)
                conn.autocommit = True
                _crear_tablas_del_bot(conn)
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


def _crear_tablas_del_bot(conn):
    """Tablas que el bot posee desde antes del dashboard.

    Se mantienen aquí (y no en las migraciones) para que el bot pueda arrancar
    contra una base limpia aunque el dashboard todavía no haya corrido.

    OJO con `users_blocked`: el panel la lee y la limpia (pantalla de bloqueos),
    así que está declarada TAMBIÉN en la migración 011 del dashboard, con la
    misma forma y con `IF NOT EXISTS` en los dos lados — arranque primero quien
    arranque, la tabla existe. Si cambia una columna, hay que cambiarla aquí y
    en esa migración.
    """
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users_blocked (
                proyecto_id INTEGER NOT NULL,
                user_id VARCHAR(50) NOT NULL,
                reason TEXT,
                blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                PRIMARY KEY (proyecto_id, user_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dictamen_registered_users (
                subject_id VARCHAR(80) PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


def run_query(operation):
    """Ejecuta `operation(conn)`; si la conexión murió, reconecta y reintenta una vez."""
    try:
        return operation(_SharedConnection.get())
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        _SharedConnection.discard()
        return operation(_SharedConnection.get())


# --- Atajos para consultas que devuelven diccionarios -------------------------

def consultar(sql: str, params: Any = None) -> list[dict[str, Any]]:
    def op(conn):
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(fila) for fila in cur.fetchall()]

    return run_query(op)


def consultar_uno(sql: str, params: Any = None) -> dict[str, Any] | None:
    def op(conn):
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            fila = cur.fetchone()
            return dict(fila) if fila else None

    return run_query(op)


def ejecutar(sql: str, params: Any = None) -> int:
    def op(conn):
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    return run_query(op)
