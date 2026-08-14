"""Olvidar una conversación: lo que el bot recuerda de un cliente, fuera.

Lo pide el dashboard cuando el administrador borra una conversación. El borrado
del historial durable (Postgres) lo hace el propio dashboard, que es el dueño de
esas tablas; lo que no puede tocar es lo que vive en Redis y en la cola de
Celery, porque el esquema de claves y los ids de tarea son del bot. Si el panel
borrara solo Postgres, el bot seguiría con el hilo en memoria: contestaría al
siguiente mensaje como si nada se hubiera borrado, y hasta llegaría un
recordatorio de una conversación que ya no existe.

Lo que NO se borra aquí, a propósito:

* `uso_eventos` — el libro mayor de facturación. Nunca se recalcula el pasado:
  borrar una conversación no es motivo para dejar de cobrar lo ya consumido.
* `seguimiento_clientes` — la ficha del cliente y su actividad. La conversación
  es el chat, no el cliente.
* El bloqueo (`users_blocked`), si lo hay. Se levanta desde su propia pantalla,
  a propósito: borrar el chat de alguien a quien se bloqueó no debería
  desbloquearlo sin querer.
"""

from src.application.buffer_service import redis_client, scoped_key
from src.application.reminder_service import ReminderService
from src.domain.entities import Channel
from src.infrastructure.repositories.conversation_state_repo import ConversationStateRepo

# Claves por conversación que dejan de tener sentido cuando el hilo se borra.
# El estado se limpia aparte (`ConversationStateRepo.clear`) y los recordatorios
# también, porque además de la clave hay que revocar la tarea en el broker.
_CLAVES = ("buffer", "buffer_seq", "processing", "img_limit")


def olvidar_conversacion(channel: Channel | str, user_id: str) -> dict[str, int]:
    """Cancela lo agendado y borra el rastro de esa conversación en Redis.

    Devuelve cuántas claves se borraron, solo para que el panel pueda decir algo
    concreto. Es idempotente: olvidar dos veces no falla ni cambia nada.
    """
    # Primero lo agendado: si se borrara el estado antes, un recordatorio a
    # punto de dispararse podría volver a escribir la clave que acabamos de
    # limpiar.
    ReminderService.cancel(channel, user_id)
    _cancelar_tareas_programadas(channel, user_id)

    ConversationStateRepo.clear(channel, user_id)

    canal = channel.value if isinstance(channel, Channel) else channel
    borradas = redis_client.delete(*[scoped_key(clave, canal, user_id) for clave in _CLAVES])
    return {"claves_borradas": int(borradas or 0)}


def _cancelar_tareas_programadas(channel: Channel | str, user_id: str) -> None:
    """Los flujos programados (publicidad, palabra clave) que quedaran en cola.

    El import es local porque `celery_app` importa medio `application` al
    cargarse; a nivel de módulo daría una importación circular.
    """
    from src.infrastructure.tasks.celery_app import cancel_scheduled_tasks

    canal = channel.value if isinstance(channel, Channel) else channel
    cancel_scheduled_tasks(canal, user_id)
