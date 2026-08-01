"""Base de los tests del dashboard.

Corren contra un Postgres REAL, porque la lógica que se prueba aquí es SQL
—índices únicos parciales, ON CONFLICT, FOR UPDATE SKIP LOCKED— y con la base
simulada no se probaría nada de eso.

Pero NO contra la base de desarrollo: los tests vacían tablas antes de cada
caso, y hacerlo sobre la base real borraría los usuarios del panel y los datos
con los que estás trabajando. Se usa una base aparte (`..._test`), que se crea
sola la primera vez.
"""

import os

import psycopg2
import pytest

_BASE_DEV = os.environ.get(
    "POSTGRES_URL", "postgresql://mi_usuario_db:mi_password_seguro@postgres:5432/mi_base_de_datos"
)


def _base_de_pruebas(url: str) -> str:
    """Misma conexión, pero apuntando a `<base>_test`."""
    if url.endswith("_test"):
        return url
    return f"{url}_test"


def _crear_si_falta(url_dev: str, nombre: str) -> None:
    """`CREATE DATABASE` no admite IF NOT EXISTS ni corre dentro de transacción."""
    conexion = psycopg2.connect(url_dev)
    conexion.autocommit = True
    try:
        with conexion.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nombre,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{nombre}"')
    finally:
        conexion.close()


_URL_TEST = _base_de_pruebas(_BASE_DEV)
_crear_si_falta(_BASE_DEV, _URL_TEST.rsplit("/", 1)[-1])

os.environ["POSTGRES_URL"] = _URL_TEST
os.environ.setdefault("SESSION_SECRET", "secreto-de-pruebas")
os.environ.setdefault("COOKIE_SECURE", "false")

from src.db import pool  # noqa: E402
from src.db.migrate import aplicar_migraciones  # noqa: E402

# Orden importante: las hijas antes que las padres por las claves foráneas.
_TABLAS = (
    "incidencias",
    "envios",
    "plantillas_mensaje",
    "sistema_config",
    "uso_eventos",
    "codigos_recuperacion",
    "accesos_suplantacion",
    "plantilla_partes",
    "dashboard_sesiones",
    "dashboard_usuarios",
    "invitaciones_ciudades",
    "conversation_messages",
    "clientes_whatsapp",
    "reportes",
    "rag_chunks",
    "preguntas_sin_respuesta",
)


@pytest.fixture(scope="session", autouse=True)
def _esquema():
    aplicar_migraciones()


@pytest.fixture(autouse=True)
def _base_limpia():
    for tabla in _TABLAS:
        pool.ejecutar(f"TRUNCATE {tabla} RESTART IDENTITY CASCADE")

    # Los periodos se recrean en vez de truncarse: `uso_eventos` los referencia
    # y siempre debe existir exactamente uno abierto.
    pool.ejecutar("DELETE FROM periodos_facturacion")
    pool.ejecutar("INSERT INTO periodos_facturacion (nota) VALUES ('periodo de pruebas')")

    # Los mensajes del negocio (palabras clave, bienvenida) los siembra una
    # migración, y el TRUNCATE de arriba se los lleva. Se vuelven a insertar
    # porque el bot depende de que existan: probar contra una tabla vacía no
    # reflejaría el sistema real.
    _sembrar_mensajes_del_negocio()
    yield


def _sembrar_mensajes_del_negocio() -> None:
    from pathlib import Path

    from src.db.migrate import DIRECTORIO_MIGRACIONES

    sql = (Path(DIRECTORIO_MIGRACIONES) / "007_mensajes_del_negocio.sql").read_text(encoding="utf-8")
    pool.ejecutar(sql)


@pytest.fixture
def cliente_http():
    from starlette.testclient import TestClient

    from src.main import app

    # `raise_server_exceptions=False` para poder comprobar los 403/401 tal como
    # los ve el navegador, en vez de que la excepción suba al test.
    with TestClient(app, raise_server_exceptions=False) as cliente:
        yield cliente


@pytest.fixture
def sesion_admin(cliente_http):
    return _ingresar(cliente_http, "admin_test", "clave-de-pruebas-1", "admin")


@pytest.fixture
def sesion_cliente(cliente_http):
    return _ingresar(cliente_http, "cliente_test", "clave-de-pruebas-2", "cliente")


def _ingresar(cliente_http, usuario: str, password: str, rol: str):
    from src.services import usuarios

    usuarios.crear(usuario, password, rol, debe_cambiar=False)
    respuesta = cliente_http.post(
        "/login", data={"usuario": usuario, "password": password}, follow_redirects=False
    )
    assert respuesta.status_code == 303, respuesta.text
    return cliente_http


def token_csrf(cliente_http) -> str:
    from src.core import security

    return security.token_csrf(cliente_http.cookies.get("dash_sesion", ""))
