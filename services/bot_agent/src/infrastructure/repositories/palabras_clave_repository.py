"""Las palabras clave que el negocio administra desde el panel.

Antes estaban escritas en el código (`if keyword in {"tareas", "transporte"}`) y
sus recordatorios se agendaban con los segundos de `mensajes.json`. Añadir una
palabra nueva era un cambio de código y un redespliegue.

Esto corre en el camino caliente: se consulta con CADA mensaje de texto que
entra, antes de decidir nada. Por eso se cachea la lista de palabras activas
unos segundos — el TTL es corto para que crear una en el panel se note enseguida
y largo para no preguntarle a Postgres por cada «hola» que llega.

Si la base falla, se devuelve vacío y el mensaje sigue su camino normal hacia el
agente: quedarse sin palabras clave un rato degrada el servicio, pero tirar la
conversación entera por una consulta caída lo rompe.
"""

import time
from typing import Any

from src.infrastructure.repositories.postgres_conn import consultar

CACHE_TTL_SEGUNDOS = 30

_cache: tuple[float, dict[str, dict[str, Any]]] | None = None


def limpiar_cache() -> None:
    global _cache
    _cache = None


def _activas() -> dict[str, dict[str, Any]]:
    """Las palabras activas, indexadas por la palabra en minúsculas."""
    global _cache
    ahora = time.monotonic()
    if _cache and (ahora - _cache[0]) < CACHE_TTL_SEGUNDOS:
        return _cache[1]

    try:
        filas = consultar("SELECT id, palabra FROM palabras_clave WHERE activa")
    except Exception as e:
        print(f"Error leyendo las palabras clave: {e}")
        return {}

    indice = {str(fila["palabra"]).strip().lower(): fila for fila in filas}
    _cache = (ahora, indice)
    return indice


def buscar(texto: str) -> dict[str, Any] | None:
    """La palabra clave que dispara ESE texto, o None.

    El match es exacto y sobre el mensaje entero, con los espacios de los bordes
    quitados y sin distinguir mayúsculas: «Examen» y «examen» son la misma, pero
    «tengo dudas del examen» no dispara nada. Es un disparador anunciado por el
    negocio, no interpretación de lenguaje natural (ver CLAUDE.md §5).
    """
    limpio = " ".join(str(texto or "").split()).lower()
    if not limpio:
        return None
    return _activas().get(limpio)


def piezas_de(palabra_id: int, tipo: str) -> list[dict[str, Any]]:
    """Los mensajes o los recordatorios de una palabra, en orden.

    De los recordatorios solo salen los ACTIVOS: apagar uno lo deja escrito en el
    panel pero fuera del envío. Los que están rotos (adjunto que no se pudo
    abrir) se filtran arriba, en quien envía.
    """
    condicion = "AND activo" if tipo == "recordatorio" else ""
    try:
        return consultar(
            f"""
            SELECT id, orden, minutos, texto, media_tipo, media_ref, reporte
            FROM palabra_clave_piezas
            WHERE palabra_id = %s AND tipo = %s {condicion}
            ORDER BY orden
            """,
            (int(palabra_id), tipo),
        )
    except Exception as e:
        print(f"Error leyendo las piezas de la palabra clave {palabra_id}: {e}")
        return []


def pieza(pieza_id: int) -> dict[str, Any] | None:
    """Una pieza concreta, tal como está AHORA.

    El recordatorio se relee al dispararse y no se guarda su texto en la tarea:
    entre que se agenda y que sale pueden pasar días, y lo que tiene que llegar
    es lo que el negocio tenga escrito en ese momento, no lo que había cuando el
    cliente escribió la palabra.
    """
    try:
        filas = consultar(
            """
            SELECT id, orden, minutos, activo, texto, media_tipo, media_ref, reporte
            FROM palabra_clave_piezas WHERE id = %s
            """,
            (int(pieza_id),),
        )
    except Exception as e:
        print(f"Error leyendo la pieza {pieza_id}: {e}")
        return None
    return filas[0] if filas else None


def texto_para_enviar(pieza: dict[str, Any]) -> str:
    """El texto con su adjunto pegado como marcador.

    `Imagen=<ref>` es el formato que ya entiende el envío, así que no se abre un
    segundo camino para la media de las palabras clave.
    """
    texto = (pieza.get("texto") or "").strip()
    referencia = (pieza.get("media_ref") or "").strip()
    tipo = (pieza.get("media_tipo") or "").strip()
    if referencia and tipo in ("imagen", "video"):
        marcador = "Imagen=" if tipo == "imagen" else "Video="
        texto = f"{texto}\n{marcador}{referencia}".strip()
    return texto


def textos_de(palabra_id: int) -> list[str]:
    """Los mensajes que salen al instante, listos para enviar."""
    return [t for p in piezas_de(palabra_id, "mensaje") if (t := texto_para_enviar(p))]
