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
import unicodedata
from typing import Any

from src.infrastructure.repositories.postgres_conn import consultar
from src.application.project_context import proyecto_actual

CACHE_TTL_SEGUNDOS = 30

_cache: dict[int, tuple[float, dict[str, dict[str, Any]]]] = {}


def limpiar_cache() -> None:
    _cache.clear()


def _activas() -> dict[str, dict[str, Any]]:
    """Las palabras activas, indexadas por la palabra en minúsculas."""
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return {}
    ahora = time.monotonic()
    guardado = _cache.get(proyecto_id)
    if guardado and (ahora - guardado[0]) < CACHE_TTL_SEGUNDOS:
        return guardado[1]

    try:
        filas = consultar(
            "SELECT id, palabra FROM palabras_clave WHERE proyecto_id = %s AND activa",
            (proyecto_id,),
        )
    except Exception as e:
        print(f"Error leyendo las palabras clave: {e}")
        return {}

    indice = {str(fila["palabra"]).strip().lower(): fila for fila in filas}
    _cache[proyecto_id] = (ahora, indice)
    return indice


def _sin_puntuacion_exterior(texto: str) -> str:
    """Quita signos exteriores, conservando tildes, símbolos y signos internos."""
    texto = " ".join(str(texto or "").split()).lower()
    inicio, fin = 0, len(texto)
    while inicio < fin and (texto[inicio].isspace() or unicodedata.category(texto[inicio]).startswith("P")):
        inicio += 1
    while fin > inicio and (texto[fin - 1].isspace() or unicodedata.category(texto[fin - 1]).startswith("P")):
        fin -= 1
    return texto[inicio:fin]


def _formas_simples(palabra: str) -> set[str]:
    """Solo vocal + s: no intenta resolver toda la morfología del español."""
    formas = {palabra}
    vocales = "aeiouáéíóúü"
    if len(palabra) >= 5 and palabra[-1] in vocales:
        formas.add(palabra + "s")
    elif len(palabra) >= 6 and palabra.endswith("s") and palabra[-2] in vocales:
        formas.add(palabra[:-1])
    return formas


def _distancia_hasta_un_cambio(a: str, b: str) -> int:
    """Levenshtein acotada: 0, 1 o 2 (más de un cambio), en tiempo lineal."""
    if abs(len(a) - len(b)) > 1:
        return 2
    i = j = cambios = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        cambios += 1
        if cambios > 1:
            return 2
        if len(a) >= len(b):
            i += 1
        if len(b) >= len(a):
            j += 1
    return cambios + int(i < len(a) or j < len(b))


def buscar(texto: str) -> dict[str, Any] | None:
    """Busca el disparador completo y devuelve siempre su fila original.

    La coincidencia histórica gana; después se toleran signos exteriores y,
    solo en palabras individuales de cinco letras o más, plural simple y un
    error de escritura. Un empate no dispara nada. No interpreta frases.
    """
    limpio = " ".join(str(texto or "").split()).lower()
    if not limpio:
        return None
    activas = _activas()
    if limpio in activas:
        return activas[limpio]
    # Nunca convertir un comando desconocido en una palabra clave al quitar /.
    if limpio.startswith("/"):
        return None

    normalizado = _sin_puntuacion_exterior(limpio)
    if not normalizado:
        return None
    candidatas = [(fila, _sin_puntuacion_exterior(clave)) for clave, fila in activas.items()]
    exactas = [fila for fila, clave in candidatas if clave == normalizado]
    if exactas:
        return exactas[0] if len(exactas) == 1 else None

    # Las frases configuradas conservan la coincidencia completa, pero nunca
    # reciben tolerancia a errores. El límite acota el trabajo por mensaje.
    if not normalizado.isalpha() or not 5 <= len(normalizado) <= 61:
        return None
    formas = _formas_simples(normalizado)
    mejores = []
    menor_distancia = 2
    for fila, clave in candidatas:
        if not clave.isalpha() or len(clave) < 5:
            continue
        distancia = min(
            _distancia_hasta_un_cambio(a, b)
            for a in formas for b in _formas_simples(clave)
        )
        if distancia < menor_distancia:
            menor_distancia = distancia
            mejores = [fila]
        elif distancia == menor_distancia and distancia < 2:
            mejores.append(fila)
    return mejores[0] if len(mejores) == 1 else None


def piezas_de(palabra_id: int, tipo: str) -> list[dict[str, Any]]:
    """Los mensajes o los recordatorios de una palabra, en orden.

    De los recordatorios solo salen los ACTIVOS: apagar uno lo deja escrito en el
    panel pero fuera del envío. Los que están rotos (adjunto que no se pudo
    abrir) se filtran arriba, en quien envía.
    """
    condicion = "AND activo" if tipo == "recordatorio" else ""
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return []
    try:
        return consultar(
            f"""
            SELECT id, orden, minutos, texto, media_tipo, media_ref, reporte
            FROM palabra_clave_piezas
            WHERE proyecto_id = %s AND palabra_id = %s AND tipo = %s {condicion}
            ORDER BY orden
            """,
            (proyecto_id, int(palabra_id), tipo),
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
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return None
    try:
        filas = consultar(
            """
            SELECT id, orden, minutos, activo, texto, media_tipo, media_ref, reporte
            FROM palabra_clave_piezas WHERE proyecto_id = %s AND id = %s
            """,
            (proyecto_id, int(pieza_id)),
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
