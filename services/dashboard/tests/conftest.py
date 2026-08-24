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
import inspect

import psycopg2
import pytest


class ServicioDeProyecto:
    """Adapta llamadas unitarias al proyecto aislado de cada caso.

    Las rutas no usan este adaptador: resuelven el proyecto desde la sesión. Es
    solo una forma compacta de mantener explícito el ámbito en pruebas de
    servicios con decenas de operaciones encadenadas.
    """

    def __init__(self, modulo, metodos: set[str], proyecto_id: int = 1):
        self._modulo = modulo
        self._metodos = metodos
        self._proyecto_id = proyecto_id

    def __getattr__(self, nombre):
        atributo = getattr(self._modulo, nombre)
        if nombre not in self._metodos or not callable(atributo):
            return atributo

        def en_proyecto(*args, **kwargs):
            parametro = inspect.signature(atributo).parameters.get("proyecto_id")
            if parametro and parametro.kind is inspect.Parameter.KEYWORD_ONLY:
                kwargs.setdefault("proyecto_id", self._proyecto_id)
                return atributo(*args, **kwargs)
            return atributo(self._proyecto_id, *args, **kwargs)

        return en_proyecto

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
    "diagnostico_descargas",
    "proyecto_tiempos_mensajes",
    "proyecto_recordatorios",
    "proyecto_instrucciones",
    "bloqueos_permanentes",
    "conversacion_negocio",
    "incidencias",
    "envios",
    # Las sesiones de envío. El CASCADE de `envios` no se las lleva (la clave
    # foránea va al revés), así que sin nombrarla los lotes de un caso salían en
    # la lista del siguiente.
    "envios_lote",
    "plantillas_mensaje",
    "sistema_config",
    "uso_eventos",
    "codigos_recuperacion",
    "accesos_suplantacion",
    "plantilla_partes",
    "dashboard_sesiones",
    "dashboard_usuarios",
    "conversation_messages",
    # No tiene clave foránea a nada, así que el CASCADE de las otras no se la
    # lleva: sin nombrarla aquí, los shots se acumularían entre casos.
    "conversation_shots",
    # Es del bot, pero el panel la lee y la limpia (pantalla de bloqueos): sin
    # vaciarla, un bloqueo de un caso callaría al usuario del siguiente.
    "users_blocked",
    "clientes_whatsapp",
    "reportes",
    "rag_chunks",
    "preguntas_sin_respuesta",
    # La ficha de cada persona que le escribe al bot. Tampoco tiene clave foránea
    # a nada, así que sobrevivía a los CASCADE: un cliente de un caso aparecía en
    # la tabla de actividad del siguiente y desordenaba lo que se estaba
    # comprobando (quién fue el último en escribir).
    "seguimiento_clientes",
    "resumen_mensual",
    "keyword_registros",
    # El CASCADE se lleva sus piezas, pero la tabla de cabecera hay que
    # nombrarla: una palabra clave de un caso dispararía el flujo en el siguiente.
    "palabras_clave",
)


@pytest.fixture(scope="session", autouse=True)
def _esquema():
    # Una ejecución anterior puede haber quedado en el esquema 019 después de
    # truncar el único proyecto. Para probar la migración del histórico se
    # repone ese proyecto legado; nunca se toca la base de desarrollo.
    try:
        aplicada = pool.consultar_uno(
            "SELECT 1 AS ok FROM schema_migrations WHERE nombre = %s",
            ("020_aislamiento_completo_por_proyecto.sql",),
        )
        proyectos = pool.consultar_uno("SELECT COUNT(*) AS total FROM clientes_whatsapp")
        if not aplicada and int((proyectos or {}).get("total") or 0) == 0:
            pool.ejecutar(
                """
                INSERT INTO clientes_whatsapp (nombre, slug, webhook_token)
                VALUES ('Proyecto legado de pruebas', 'proyecto-legado-pruebas',
                        'token-proyecto-legado-pruebas')
                """
            )
    except Exception:
        # En una base completamente nueva las tablas todavía no existen; la
        # migración 005 ya crea el único proyecto necesario.
        pass
    aplicar_migraciones()


@pytest.fixture(autouse=True)
def _base_limpia():
    for tabla in _TABLAS:
        pool.ejecutar(f"TRUNCATE {tabla} RESTART IDENTITY CASCADE")

    # Los periodos se recrean en vez de truncarse: `uso_eventos` los referencia
    # y siempre debe existir exactamente uno abierto.
    pool.ejecutar("DELETE FROM periodos_facturacion")
    pool.ejecutar("INSERT INTO periodos_facturacion (nota) VALUES ('periodo de pruebas')")

    proyecto = pool.consultar_uno(
        """
        INSERT INTO clientes_whatsapp (nombre, slug, webhook_token)
        VALUES ('Proyecto de pruebas', 'proyecto-de-pruebas', 'token-proyecto-de-pruebas')
        RETURNING id
        """
    )
    # Muchos tests de repositorio insertan filas crudas para preparar un caso.
    # En producción los servicios exigen proyecto_id; aquí el default apunta al
    # proyecto aislado recién creado y evita que esas semillas repitan ruido.
    for tabla in (
        "conversation_messages", "conversation_shots", "seguimiento_clientes",
        "resumen_mensual", "users_blocked", "reportes", "keyword_registros",
        "preguntas_sin_respuesta", "rag_chunks", "uso_eventos",
        "plantillas_mensaje", "plantilla_partes", "palabras_clave",
        "palabra_clave_piezas", "envios_lote", "envios",
        "incidencias",
    ):
        pool.ejecutar(
            f"ALTER TABLE {tabla} ALTER COLUMN proyecto_id SET DEFAULT {int(proyecto['id'])}"
        )

    # Los mensajes del negocio (palabras clave, bienvenida) los siembra una
    # migración, y el TRUNCATE de arriba se los lleva. Se vuelven a insertar
    # porque el bot depende de que existan: probar contra una tabla vacía no
    # reflejaría el sistema real.
    _sembrar_mensajes_del_negocio(proyecto["id"])
    yield


def _sembrar_mensajes_del_negocio(proyecto_id: int) -> None:
    """Re-aplica la semilla de palabras clave y mensajes del negocio.

    Se replica la migración 016 y NO la 007, que fue la primera semilla: aquella
    escribía en la columna `nombre` de `plantillas_mensaje`, que la 016 elimina,
    así que volver a aplicarla contra el esquema actual falla. La 016 es
    idempotente entera (CREATE IF NOT EXISTS, INSERT condicionado, DROP IF
    EXISTS), justo para poder usarse así.

    Hace falta porque el bot depende de que estas filas existan: probar contra
    tablas vacías no reflejaría el sistema real.
    """
    from pathlib import Path

    from src.db.migrate import DIRECTORIO_MIGRACIONES

    sql = (Path(DIRECTORIO_MIGRACIONES) / "016_palabras_clave.sql").read_text(encoding="utf-8")
    sql = sql.replace(
        "ON CONFLICT (clave) DO NOTHING",
        "ON CONFLICT (proyecto_id, clave) DO NOTHING",
    )
    # La migración histórica no conocía proyecto_id. Solo durante esta semilla
    # de tests se da un default explícito y se retira en la misma transacción.
    pool.ejecutar(
        f"""
        ALTER TABLE palabras_clave ALTER COLUMN proyecto_id SET DEFAULT {int(proyecto_id)};
        ALTER TABLE plantillas_mensaje ALTER COLUMN proyecto_id SET DEFAULT {int(proyecto_id)};
        ALTER TABLE palabra_clave_piezas ALTER COLUMN proyecto_id SET DEFAULT {int(proyecto_id)};
        ALTER TABLE plantilla_partes ALTER COLUMN proyecto_id SET DEFAULT {int(proyecto_id)};
        {sql}
        """
    )


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
    from src.services import clientes_whatsapp, usuarios

    cuenta = usuarios.crear(usuario, password, rol, debe_cambiar=False)
    if rol == "cliente":
        proyecto = pool.consultar_uno(
            "SELECT id FROM clientes_whatsapp WHERE usuario_id IS NULL ORDER BY id LIMIT 1"
        )
        if proyecto:
            clientes_whatsapp.vincular_cuenta(proyecto["id"], cuenta["id"])
    respuesta = cliente_http.post(
        "/login", data={"usuario": usuario, "password": password}, follow_redirects=False
    )
    assert respuesta.status_code == 303, respuesta.text
    return cliente_http


def token_csrf(cliente_http) -> str:
    from src.core import security

    return security.token_csrf(cliente_http.cookies.get("dash_sesion", ""))
