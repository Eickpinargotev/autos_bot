"""Hub SSE aislado por proyecto.

Una única consulta toma las señales de todos los proyectos. Cada suscriptor
queda asociado a un ámbito y solo recibe nombres de temas de ese ámbito.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from src.core import security
from src.db import pool

INTERVALO = 2.0

TOPICS: dict[str, tuple[str, ...]] = {
    "reportes": ("reportes_ultimo", "reportes_pendientes"),
    "preguntas": ("preguntas_ultima", "preguntas_pendientes"),
    # El máximo detecta mensajes nuevos. El total detecta también un `/d` o un
    # borrado desde otra pestaña aunque la conversación eliminada no tuviera el
    # id máximo global del proyecto.
    "conversaciones": ("conversaciones_ultimo", "conversaciones_total"),
    "uso": ("uso_ultimo",),
    "bloqueos": ("bloqueos_total", "bloqueos_ultimo"),
    "incidencias": ("incidencias_ultima", "incidencias_abiertas"),
    "envios": ("envios_ultimo_lote", "envios_pendientes", "envios_cancelados"),
}

_TOPICS_ADMIN = frozenset({"uso", "incidencias"})
_TOPICS_NEGOCIO = frozenset({"uso", "reportes", "preguntas", "conversaciones", "bloqueos", "envios"})

_SQL_SENALES = """
SELECT scope, proyecto_id,
       (SELECT max(id) FROM reportes r WHERE scope = 'admin' OR r.proyecto_id = base.proyecto_id) AS reportes_ultimo,
       (SELECT count(*) FROM reportes r WHERE NOT revisado AND (scope = 'admin' OR r.proyecto_id = base.proyecto_id)) AS reportes_pendientes,
       (SELECT max(id) FROM preguntas_sin_respuesta p WHERE scope <> 'admin' AND p.proyecto_id = base.proyecto_id) AS preguntas_ultima,
       (SELECT count(*) FROM preguntas_sin_respuesta p WHERE NOT atendida AND scope <> 'admin' AND p.proyecto_id = base.proyecto_id) AS preguntas_pendientes,
       (SELECT max(ultimo_mensaje_id) FROM conversation_threads t WHERE scope = 'admin' OR t.proyecto_id = base.proyecto_id) AS conversaciones_ultimo,
       (SELECT count(*) FROM conversation_threads t WHERE scope = 'admin' OR t.proyecto_id = base.proyecto_id) AS conversaciones_total,
       (SELECT max(id) FROM uso_eventos u WHERE scope = 'admin' OR u.proyecto_id = base.proyecto_id) AS uso_ultimo,
       (SELECT count(*) FROM bloqueos_permanentes b WHERE scope <> 'admin' AND b.proyecto_id = base.proyecto_id) AS bloqueos_total,
       (SELECT max(creado_en) FROM bloqueos_permanentes b WHERE scope <> 'admin' AND b.proyecto_id = base.proyecto_id) AS bloqueos_ultimo,
       (SELECT max(id) FROM incidencias i WHERE scope = 'admin') AS incidencias_ultima,
       (SELECT count(*) FROM incidencias i WHERE scope = 'admin' AND estado = 'abierta') AS incidencias_abiertas,
       (SELECT max(id) FROM envios_lote l WHERE scope <> 'admin' AND l.proyecto_id = base.proyecto_id) AS envios_ultimo_lote,
       (SELECT count(*) FROM envios e WHERE scope <> 'admin' AND e.proyecto_id = base.proyecto_id AND estado = 'pendiente') AS envios_pendientes,
       (SELECT count(*) FROM envios_lote l WHERE scope <> 'admin' AND l.proyecto_id = base.proyecto_id AND cancelado) AS envios_cancelados
FROM (
    SELECT 'admin'::text AS scope, NULL::integer AS proyecto_id
    UNION ALL
    SELECT 'proyecto', id FROM clientes_whatsapp
) base
"""


def senales() -> dict[str, tuple[Any, ...]]:
    salida: dict[str, tuple[Any, ...]] = {}
    for fila in pool.consultar(_SQL_SENALES):
        prefijo = "admin" if fila["scope"] == "admin" else f"p{fila['proyecto_id']}"
        for tema, columnas in TOPICS.items():
            salida[f"{prefijo}:{tema}"] = tuple(fila.get(columna) for columna in columnas)
    return salida


def topics_para(usuario: dict[str, Any] | None) -> frozenset[str]:
    if not usuario:
        return frozenset()
    return _TOPICS_ADMIN if usuario.get("rol") == security.ROL_ADMIN else _TOPICS_NEGOCIO


def ambito_para(usuario: dict[str, Any]) -> str:
    if usuario.get("rol") == security.ROL_ADMIN:
        return "admin"
    fila = pool.consultar_uno(
        "SELECT id FROM clientes_whatsapp WHERE usuario_id = %s",
        (usuario["id"],),
    )
    return f"p{fila['id']}" if fila else "sin-proyecto"


_suscriptores: dict[asyncio.Queue, str] = {}
_previas: dict[str, tuple[Any, ...]] | None = None
_tarea: asyncio.Task | None = None


def suscriptores() -> int:
    return len(_suscriptores)


@asynccontextmanager
async def suscribirse(ambito: str = "admin"):
    global _previas
    cola: asyncio.Queue = asyncio.Queue(maxsize=8)
    _suscriptores[cola] = ambito
    try:
        yield cola
    finally:
        _suscriptores.pop(cola, None)
        if not _suscriptores:
            _previas = None


def _publicar(claves: frozenset[str]) -> None:
    for cola, ambito in list(_suscriptores.items()):
        prefijo = f"{ambito}:"
        temas = frozenset(clave[len(prefijo):] for clave in claves if clave.startswith(prefijo))
        if not temas:
            continue
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
    global _previas
    while True:
        if not _suscriptores:
            _previas = None
            await asyncio.sleep(INTERVALO)
            continue
        try:
            actuales = await asyncio.to_thread(senales)
        except asyncio.CancelledError:
            raise
        except Exception:
            actuales = None
        if actuales is not None:
            if _previas is None:
                _previas = actuales
            else:
                cambiados = frozenset(
                    clave for clave, valor in actuales.items() if _previas.get(clave) != valor
                )
                _previas = actuales
                if cambiados:
                    _publicar(cambiados)
        await asyncio.sleep(INTERVALO)


def arrancar() -> None:
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
