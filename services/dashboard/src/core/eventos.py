"""Hub de novedades: una sola consulta para todo el panel.

El problema que resuelve. Refrescar por polling desde el navegador cuesta
`pestañas × contenedores` consultas cada N segundos, se mire o no se mire la
pantalla. Con veinte pestañas abiertas eso son cientos de consultas por minuto
para decir «no ha cambiado nada».

Aquí se invierte: UNA tarea de fondo pregunta a Postgres cada `INTERVALO`
segundos por unos contadores baratos, compara con el tick anterior y avisa por
SSE **solo** de lo que cambió. El navegador entonces pide el fragmento afectado.
El coste en base de datos es constante: da igual que haya una pestaña o veinte.

Y si no hay nadie suscrito, NO se consulta nada. El panel cerrado no cuesta.

Lo que viaja al navegador es el NOMBRE de lo que cambió («reportes»), nunca el
dato. Así el permiso lo sigue decidiendo la ruta del fragmento, que ya tiene su
`requiere_admin` / `requiere_negocio`; esto no es una puerta nueva.

Nota para quien toque el arranque: el hub vive DENTRO del proceso. Con varios
workers de uvicorn cada uno tendría el suyo y haría su propio tick — funciona,
pero multiplica la consulta por worker. Por eso el Dockerfile arranca con uno.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from src.core import security
from src.db import pool

# Cada cuánto se pregunta a la base. Es el techo de latencia que verá el
# usuario: dos segundos entre que el bot escribe y la pantalla se entera.
INTERVALO = 2.0

# Qué señales componen cada tema. El navegador se suscribe a temas, no a
# columnas: `data-vivo="reportes"`.
TOPICS: dict[str, tuple[str, ...]] = {
    "reportes": ("reportes_ultimo", "reportes_pendientes"),
    "preguntas": ("preguntas_ultima", "preguntas_pendientes"),
    "conversaciones": ("conversaciones_ultimo",),
    "uso": ("uso_ultimo",),
    "bloqueos": ("bloqueos_total", "bloqueos_ultimo"),
    "incidencias": ("incidencias_ultima", "incidencias_abiertas"),
    "envios": ("envios_ultimo_lote", "envios_pendientes", "envios_cancelados"),
}

# Los roles son excluyentes (`security.requiere_admin` / `requiere_negocio`), así
# que la lista es un reparto, no una jerarquía. El administrador recibe
# `reportes` porque el resumen del perfil del proyecto cuenta los pendientes.
_TOPICS_ADMIN = frozenset({"uso", "conversaciones", "bloqueos", "incidencias", "reportes"})
_TOPICS_NEGOCIO = frozenset({"uso", "reportes", "preguntas", "envios"})

# Una consulta, todas las señales. Cada subconsulta es `MAX(id)` sobre la clave
# primaria (recorrido del índice hacia atrás: constante aunque la tabla tenga
# millones de filas) o un `COUNT` sobre tabla corta o índice parcial.
#
# Hacen falta las dos formas. `MAX(id)` ve lo que ENTRA; el `COUNT` ve lo que
# alguien marcó como atendido y lo que purga el bot, que no mueven el máximo.
_SQL_SENALES = """
SELECT (SELECT max(id) FROM reportes)                                    AS reportes_ultimo,
       (SELECT count(*) FROM reportes WHERE NOT revisado)                AS reportes_pendientes,
       (SELECT max(id) FROM preguntas_sin_respuesta)                     AS preguntas_ultima,
       (SELECT count(*) FROM preguntas_sin_respuesta WHERE NOT atendida) AS preguntas_pendientes,
       (SELECT max(id) FROM conversation_messages)                       AS conversaciones_ultimo,
       (SELECT max(id) FROM uso_eventos)                                 AS uso_ultimo,
       (SELECT count(*) FROM users_blocked)                              AS bloqueos_total,
       (SELECT max(blocked_at) FROM users_blocked)                       AS bloqueos_ultimo,
       (SELECT max(id) FROM incidencias)                                 AS incidencias_ultima,
       (SELECT count(*) FROM incidencias WHERE estado = 'abierta')       AS incidencias_abiertas,
       (SELECT max(id) FROM envios_lote)                                 AS envios_ultimo_lote,
       (SELECT count(*) FROM envios WHERE estado = 'pendiente')          AS envios_pendientes,
       (SELECT count(*) FROM envios_lote WHERE cancelado)                AS envios_cancelados
"""


def senales() -> dict[str, tuple[Any, ...]]:
    """Foto del estado de cada tema, como valores opacos que solo se comparan.

    Es una función normal y síncrona a propósito: se puede probar sin montar un
    bucle de asyncio, que es donde estaría la mitad del trabajo de un test.
    """
    fila = pool.consultar_uno(_SQL_SENALES) or {}
    return {tema: tuple(fila.get(col) for col in cols) for tema, cols in TOPICS.items()}


def topics_para(usuario: dict[str, Any] | None) -> frozenset[str]:
    """Los temas que esta cuenta puede llegar a recibir.

    Un proyecto no se entera de que hay una incidencia nueva ni de que se
    escribió una conversación. El aviso no lleva datos, pero saber que hay
    movimiento ya dice algo de otros negocios.
    """
    if not usuario:
        return frozenset()
    if usuario.get("rol") == security.ROL_ADMIN:
        return _TOPICS_ADMIN
    return _TOPICS_NEGOCIO


# --- El hub -------------------------------------------------------------------

_suscriptores: set[asyncio.Queue] = set()
_previas: dict[str, tuple[Any, ...]] | None = None
_tarea: asyncio.Task | None = None


def suscriptores() -> int:
    """Cuántas pestañas están escuchando ahora mismo."""
    return len(_suscriptores)


@asynccontextmanager
async def suscribirse():
    """Presta una cola con los temas que van cambiando, y la retira al salir.

    La cola es corta y descarta lo viejo cuando se llena: un navegador que se
    quedó colgado no puede hacer crecer la memoria del servidor. Perder un aviso
    intermedio no importa — lo que se manda es «esto cambió», no los cambios uno
    a uno, así que el siguiente aviso lo pone al día igual.
    """
    global _previas

    cola: asyncio.Queue = asyncio.Queue(maxsize=8)
    _suscriptores.add(cola)
    try:
        yield cola
    finally:
        _suscriptores.discard(cola)
        if not _suscriptores:
            # Sin nadie escuchando, la foto anterior se descarta. Si se guardara,
            # al volver a conectarse alguien el primer tick anunciaría como
            # «nuevo» todo lo ocurrido mientras el panel estuvo cerrado, y el
            # navegador se pediría todos los fragmentos de golpe. Al reconectar
            # ya hace su propia puesta al día.
            _previas = None


def _publicar(temas: frozenset[str]) -> None:
    for cola in list(_suscriptores):
        if cola.full():
            try:
                cola.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            cola.put_nowait(temas)
        except asyncio.QueueFull:
            pass


async def _bucle() -> None:
    """El tick. Solo consulta si hay alguien mirando.

    La espera es un `sleep` y no un `asyncio.Event` a nivel de módulo, aunque el
    Event pareciera más fino: un Event guarda futuros del bucle de eventos en el
    que se esperó, y este módulo se importa una vez pero se usa desde varios
    (cada `TestClient` monta el suyo). Al despertarlo desde otro bucle, los
    futuros del anterior están muertos y el aviso se pierde en silencio.

    Lo que había que evitar era la CONSULTA, no el temporizador: un `sleep` cada
    dos segundos sin nadie conectado no le cuesta nada a nadie.
    """
    global _previas

    while True:
        if not _suscriptores:
            _previas = None
            await asyncio.sleep(INTERVALO)
            continue

        try:
            # psycopg2 bloquea el hilo. Dentro del bucle de eventos congelaría
            # TODAS las peticiones del servidor mientras dura la consulta.
            actuales = await asyncio.to_thread(senales)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Un corte puntual de la base no puede matar el hub: se reintenta al
            # siguiente tick. El pool ya reconstruye la conexión por su cuenta.
            actuales = None

        if actuales is not None:
            if _previas is None:
                _previas = actuales  # primera foto: referencia, no hay nada que anunciar
            else:
                cambiados = frozenset(t for t, v in actuales.items() if _previas.get(t) != v)
                _previas = actuales
                if cambiados:
                    _publicar(cambiados)

        await asyncio.sleep(INTERVALO)


def arrancar() -> None:
    """Pone en marcha el hub. Se llama desde el lifespan de la aplicación."""
    global _tarea
    if _tarea is None or _tarea.done():
        _tarea = asyncio.create_task(_bucle(), name="hub-de-eventos")


async def detener() -> None:
    global _tarea
    if _tarea is None:
        return
    _tarea.cancel()
    try:
        await _tarea
    except (asyncio.CancelledError, Exception):
        pass
    _tarea = None
