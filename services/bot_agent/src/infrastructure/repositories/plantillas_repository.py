"""Mensajes del negocio, tal como los dejó editados en el panel.

Los textos de la palabra clave, sus recordatorios y la bienvenida al grupo
estaban solo en `mensajes.json`, un archivo del repositorio: para cambiar una
palabra había que editarlo y redeplegar. Ahora el negocio los edita en
«Mensajes» y el bot los lee de ahí.

`mensajes.json` sigue siendo el respaldo, y eso es deliberado: si la clave no
está en la base —base recién creada, migración aún sin correr, alguien borró la
plantilla— el bot usa la del archivo en vez de quedarse mudo delante de un
cliente. Un mensaje algo desactualizado es mucho menos malo que ninguno.

Se cachea unos segundos porque esto corre en el camino caliente de la
conversación; el TTL es corto para que editar en el panel se note enseguida.
"""

import time
from typing import Any

from src.infrastructure.repositories.postgres_conn import consultar
from src.application.project_context import proyecto_actual

CACHE_TTL_SEGUNDOS = 30

_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_cache_claves: dict[int, tuple[float, list[str]]] = {}


def limpiar_cache() -> None:
    _cache.clear()
    _cache_claves.clear()


def claves() -> list[str]:
    """Las claves de todos los mensajes del panel, de la más larga a la más corta.

    La usa el flujo de publicidad para reconocer de qué mensaje habla el cliente.
    El orden por longitud no es cosmético: si existen «LIBERIA» y «LICENCIA EN
    LIBERIA», el texto completo tiene que ganarle al trozo, y buscando por
    subcadena gana el primero que se pruebe.
    """
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return []
    ahora = time.monotonic()
    guardado = _cache_claves.get(proyecto_id)
    if guardado and (ahora - guardado[0]) < CACHE_TTL_SEGUNDOS:
        return guardado[1]

    try:
        filas = consultar(
            "SELECT clave FROM plantillas_mensaje WHERE proyecto_id = %s ORDER BY clave",
            (proyecto_id,),
        )
    except Exception as e:
        print(f"Error leyendo las claves de los mensajes del panel: {e}")
        return []

    encontradas = sorted(
        (str(fila.get("clave") or "").strip().upper() for fila in filas if fila.get("clave")),
        key=len,
        reverse=True,
    )
    _cache_claves[proyecto_id] = (ahora, encontradas)
    return encontradas


def partes_de(clave: str) -> list[dict[str, Any]]:
    """Las partes del mensaje con esa clave, en orden. Vacío si no existe."""
    clave = str(clave or "").strip().upper()
    if not clave:
        return []

    ahora = time.monotonic()
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return []
    clave_cache = f"{proyecto_id}:{clave}"
    guardado = _cache.get(clave_cache)
    if guardado and (ahora - guardado[0]) < CACHE_TTL_SEGUNDOS:
        return guardado[1]

    try:
        filas = consultar(
            """
            SELECT pp.orden, pp.texto, pp.media_tipo, pp.media_ref
            FROM plantillas_mensaje p
            JOIN plantilla_partes pp ON pp.plantilla_id = p.id AND pp.proyecto_id = p.proyecto_id
            WHERE p.proyecto_id = %s AND p.clave = %s
            ORDER BY pp.orden
            """,
            (proyecto_id, clave),
        )
    except Exception as e:
        print(f"Error leyendo el mensaje '{clave}' del panel: {e}")
        return []

    _cache[clave_cache] = (ahora, filas)
    return filas


def textos_de(clave: str) -> list[str]:
    """Las partes ya montadas como texto listo para enviar.

    El adjunto viaja dentro del propio texto como marcador (`Imagen=<ref>`),
    que es el formato que ya entiende `outbound_attachments`: así el envío no
    tiene que saber si el mensaje vino del panel o del archivo.
    """
    mensajes = []
    for parte in partes_de(clave):
        texto = (parte.get("texto") or "").strip()
        referencia = (parte.get("media_ref") or "").strip()
        tipo = (parte.get("media_tipo") or "").strip()
        if referencia and tipo in ("imagen", "video"):
            marcador = "Imagen=" if tipo == "imagen" else "Video="
            texto = f"{texto}\n{marcador}{referencia}".strip()
        if texto:
            mensajes.append(texto)
    return mensajes
