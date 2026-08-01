"""Acceso a Postgres para el dashboard.

Las rutas de FastAPI se declaran `def` (síncronas), así que Starlette las corre
en su pool de hilos. Por eso usamos un pool de conexiones con candado interno
(`ThreadedConnectionPool`) en vez de la conexión única compartida que usa el
bot: una petición lenta no debe bloquear a las demás.

Toda consulta pasa por `consulta()` / `ejecutar()`, que devuelven la conexión al
pool pase lo que pase y reconstruyen el pool si Postgres se reinició.
"""

import threading
from contextlib import contextmanager
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.pool

from src.core.config import settings

_lock = threading.Lock()
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _obtener_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    with _lock:
        if _pool is None or _pool.closed:
            _pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=settings.DB_MAX_CONNECTIONS,
                dsn=settings.POSTGRES_URL,
            )
        return _pool


def _descartar_pool() -> None:
    """Cierra el pool actual para que la siguiente llamada cree uno nuevo.

    Necesario cuando Postgres se reinicia: las conexiones cacheadas quedan
    muertas y todas fallarían con OperationalError hasta reconstruir el pool.
    """
    global _pool
    with _lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None


@contextmanager
def conexion(*, autocommit: bool = True):
    """Presta una conexión del pool y la devuelve siempre al terminar."""
    pool = _obtener_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = autocommit
        yield conn
    finally:
        try:
            pool.putconn(conn)
        except Exception:
            pass


def _ejecutar(sql: str, params: Any, modo: str) -> Any:
    def operacion() -> Any:
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                if modo == "todos":
                    return [dict(fila) for fila in cur.fetchall()]
                if modo == "uno":
                    fila = cur.fetchone()
                    return dict(fila) if fila else None
                return cur.rowcount

    try:
        return operacion()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        # Postgres se reinició o cortó la conexión: rehacer el pool y reintentar
        # una sola vez. Si vuelve a fallar, que el error suba.
        _descartar_pool()
        return operacion()


def consultar(sql: str, params: Any = None) -> list[dict[str, Any]]:
    """Devuelve todas las filas como diccionarios."""
    return _ejecutar(sql, params, "todos")


def consultar_uno(sql: str, params: Any = None) -> dict[str, Any] | None:
    """Devuelve la primera fila como diccionario, o None."""
    return _ejecutar(sql, params, "uno")


def ejecutar(sql: str, params: Any = None) -> int:
    """Ejecuta una sentencia sin resultado y devuelve las filas afectadas."""
    return _ejecutar(sql, params, "nada")
