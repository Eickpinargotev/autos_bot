"""Las dos cosas que el panel le pide al bot por HTTP.

El dashboard y el bot se comunican casi siempre por Postgres (el panel escribe
una fila, el bot la lee). Quedan dos casos en los que eso no basta porque el
efecto vive en la memoria del bot, no en la base:

* **Reindexar un chunk** que se acaba de editar, para que el RAG lo use ya y no
  en la siguiente sincronización perezosa.
* **Olvidar una conversación** que se acaba de borrar: el hilo en Redis y los
  recordatorios agendados solo los conoce el bot.

Las dos rutas se autentican con `INTERNAL_API_TOKEN` y responden 503 si el bot
no lo tiene puesto. La diferencia está en qué hacer cuando falla la llamada:
el reindexado se puede perder (el RAG se pone al día solo), pero un borrado a
medias hay que decirlo — el usuario cree que borró algo que el bot todavía
recuerda.
"""

import httpx

from src.core.config import settings

_TIEMPO_LIMITE = 5.0


def _llamar(ruta: str) -> str:
    """Llama al bot. Devuelve "" si salió bien, o el motivo del fallo."""
    if not settings.INTERNAL_API_TOKEN or not settings.BOT_WEBHOOK_URL:
        return "el bot no tiene configurado el canal interno (INTERNAL_API_TOKEN / BOT_WEBHOOK_URL)"
    try:
        respuesta = httpx.post(
            f"{settings.BOT_WEBHOOK_URL.rstrip('/')}/{ruta.lstrip('/')}",
            params={"token": settings.INTERNAL_API_TOKEN},
            timeout=_TIEMPO_LIMITE,
        )
    except Exception as e:
        return f"no se pudo hablar con el bot: {e}"

    if respuesta.status_code >= 400:
        return f"el bot respondió {respuesta.status_code}"
    return ""


def reindexar_chunk(chunk_id: int) -> None:
    """Optimización, no requisito: si falla, el RAG se actualiza solo después."""
    fallo = _llamar(f"/internal/rag/sync/{chunk_id}")
    if fallo:
        print(f"No se pudo pedir el reindexado inmediato del chunk {chunk_id}: {fallo}")


def olvidar_conversacion(canal: str, client_id: str) -> str:
    """Pide al bot que suelte el hilo en memoria. Devuelve "" o el motivo del fallo.

    Quien llama TIENE que mirar el resultado: si esto falla, en Postgres ya no
    queda historial pero el bot sigue con la conversación en Redis, y al
    siguiente mensaje contestará como si nada se hubiera borrado.
    """
    return _llamar(f"/internal/conversaciones/{canal}/{client_id}/olvidar")
