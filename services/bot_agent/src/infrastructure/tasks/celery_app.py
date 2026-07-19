from celery import Celery
from celery.schedules import crontab
from src.core.config import settings
from src.application.buffer_service import BufferService, redis_client, scoped_key
from src.application.message_catalog import get_node_data
from src.application.reminder_service import ReminderService
from src.application.runtime_context import (
    RUNTIME_TTL_SECONDS,
    get_ad_task_id,
    has_ad_context,
    has_keyword_context,
    save_ad_task_id,
    set_ad_reminder_stage,
    set_keyword_active_report,
)
from src.domain.entities import Channel
from src.infrastructure.channels.senders import ChannelSenderRegistry
from src.infrastructure.repositories.conversation_state_repo import ConversationStateRepo
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository
from src.infrastructure.repositories.postgres_user_repo import PostgresUserRepo
from src.infrastructure.repositories.report_repository import ReportRepository
from src.infrastructure.evals.conversation_shots import (
    ConversationShotBuilder,
    ConversationShotRepository,
    ShotTraceCollector,
)
from src.application.agent_pipeline import run_agent_turn

celery_app = Celery("bot_agent_tasks", broker=settings.REDIS_URL)

# Con broker Redis, una tarea con countdown/ETA queda "sin ack" hasta que se
# ejecuta. Si el countdown supera el visibility_timeout (1h por defecto), Redis
# re-entrega la tarea y el cliente recibe el mensaje DUPLICADO una vez por hora.
# El timeout debe superar con margen el countdown más largo que agendamos
# (recordatorios de publicidad, hasta PUB_DELAY_3_SEC).
_max_countdown_seconds = max(
    settings.PUB_DELAY_1_SEC,
    settings.PUB_DELAY_2_SEC,
    settings.PUB_DELAY_3_SEC,
    settings.FOLLOWUP_FIRST_DELAY_SECONDS,
    settings.FOLLOWUP_NEXT_DELAY_SECONDS,
    86400,
)
celery_app.conf.broker_transport_options = {
    "visibility_timeout": _max_countdown_seconds * 2,
}
# Nadie consulta resultados de tareas (no hay result backend); esto evita que
# alguien lo configure a futuro y Redis se llene de claves celery-task-meta-*.
celery_app.conf.task_ignore_result = True
celery_app.conf.broker_connection_retry_on_startup = True

# Tarea periódica de retención: una vez al día purga el historial de
# conversaciones (NocoDB) vencido. La hora es UTC (~02:00 en Costa Rica), un
# horario de baja actividad. Requiere que el worker corra con beat (`-B`).
celery_app.conf.beat_schedule = {
    "purgar-conversaciones-vencidas": {
        "task": "src.infrastructure.tasks.celery_app.purge_expired_conversations",
        "schedule": crontab(hour=8, minute=0),
    },
}

# Candado de procesamiento por conversación. Debe superar el peor caso de un
# turno (varias llamadas al LLM encadenadas con timeout de 30s); si el worker
# muere a mitad de turno, el candado expira solo y la conversación no queda
# trabada.
_PROCESSING_LOCK_TTL_SECONDS = 120


@celery_app.task
def process_buffered_messages(channel: str, user_id: str, user_name: str = "Desconocido", seq: int | None = None):
    channel_value = Channel(channel)

    # Un solo turno en proceso a la vez por conversación. Sin este candado, un
    # mensaje que llega mientras el turno anterior sigue esperando al LLM crea
    # dos turnos del agente en paralelo del MISMO usuario: el turno más lento
    # pisa el estado del más nuevo y las respuestas salen desordenadas. Si el candado
    # está tomado, la tarea se reagenda SIN drenar el buffer: el debounce por
    # `seq` sigue decidiendo cuál tarea procesa la ráfaga.
    lock_key = scoped_key("processing", channel_value, user_id)
    if not redis_client.set(lock_key, "1", nx=True, ex=_PROCESSING_LOCK_TTL_SECONDS):
        process_buffered_messages.apply_async((channel, user_id, user_name, seq), countdown=2)
        return
    try:
        _process_buffered_messages_locked(channel_value, user_id, user_name, seq)
    finally:
        redis_client.delete(lock_key)


def _process_buffered_messages_locked(channel_value: Channel, user_id: str, user_name: str, seq: int | None):
    if seq is None:
        # Compatibilidad con tareas agendadas por versiones anteriores.
        text = BufferService.get_and_clear_buffer(user_id, channel_value)
    else:
        # Debounce: solo procesa la tarea del último mensaje de la ráfaga.
        drained = BufferService.drain_if_current(user_id, channel_value, seq)
        if drained is None:
            return  # Llegó un mensaje más nuevo; esta tarea quedó obsoleta.
        text = drained
    if not text.strip():
        return # Buffer was empty or cleared by a command

    ReminderService.cancel(channel_value, user_id)
    state_before = ConversationStateRepo.get(channel_value, user_id)
    with ShotTraceCollector() as shot_collector:
        result = run_agent_turn(channel_value, user_id, text, user_name=user_name)
    state_after = ConversationStateRepo.get(channel_value, user_id)

    _save_conversation_shot(
        channel=channel_value,
        user_id=user_id,
        user_name=user_name,
        user_message=text,
        bot_replies=result.replies,
        state_before=state_before,
        state_after=state_after,
        trace_events=shot_collector.events,
    )

    if result.replies:
        for msg in result.replies:
            ChannelSenderRegistry.send(channel_value, user_id, msg)

    if result.reminder:
        schedule_smart_reminder_for_result(channel_value, user_id, result.reminder)


def _save_conversation_shot(
    *,
    channel: Channel | str,
    user_id: str,
    user_name: str,
    user_message: str,
    bot_replies: list[str],
    state_before,
    state_after,
    trace_events: list[dict],
):
    _, fecha_hora, shot = ConversationShotBuilder.build(
        channel=channel,
        user_id=user_id,
        user_name=user_name,
        user_message=user_message,
        bot_replies=bot_replies,
        state_before=state_before,
        state_after=state_after,
        trace_events=trace_events,
    )
    ConversationShotRepository.save(
        fecha_hora=fecha_hora,
        id_user=user_id,
        chanel=channel,
        shot=shot,
    )

import time
import random

@celery_app.task
def send_delayed_message_sequence(channel: str, user_id: str, messages: list[str]):
    for msg in messages:
        ChannelSenderRegistry.send(channel, user_id, msg)
        # Wait randomly between configured seconds before sending next message
        time.sleep(random.uniform(settings.MSG_DELAY_MIN, settings.MSG_DELAY_MAX))

@celery_app.task
def send_single_message(channel: str, user_id: str, message: str):
    ChannelSenderRegistry.send(channel, user_id, message)

@celery_app.task
def send_ad_reminder(channel: str, user_id: str, message: str, stage: int):
    if not has_ad_context(channel, user_id):
        return
    set_ad_reminder_stage(channel, user_id, stage)
    ChannelSenderRegistry.send(channel, user_id, message)

@celery_app.task
def send_keyword_reminder(channel: str, user_id: str, node: str, stage: int):
    if not has_keyword_context(channel, user_id):
        return

    node_data = get_node_data("KEYWORD", node)
    set_keyword_active_report(channel, user_id, stage, node_data.get("reporte", ""))
    for message in node_data.get("mensajes", []):
        ChannelSenderRegistry.send(channel, user_id, message)

def cancel_scheduled_tasks(channel: str, user_id: str):
    """
    Cancela todas las tareas de Celery programadas para un usuario.
    """
    key = scoped_key("scheduled_tasks", channel, user_id)
    task_ids = redis_client.lrange(key, 0, -1)
    if task_ids:
        for tid in task_ids:
            celery_app.control.revoke(tid, terminate=True)
        redis_client.delete(key)

def cancel_ad_reminder_stage(channel: str, user_id: str, stage: int):
    task_id = get_ad_task_id(channel, user_id, stage)
    if task_id:
        celery_app.control.revoke(task_id, terminate=True)
        redis_client.delete(f"{scoped_key('ad_reminder_task', channel, user_id)}:{stage}")

def schedule_smart_reminder_for_result(channel: Channel | str, user_id: str, reminder: dict):
    channel_value = Channel(channel).value if isinstance(channel, str) else channel.value
    task = send_smart_reminder.apply_async(
        (channel_value, user_id, reminder.get("level", 1)),
        countdown=reminder.get("seconds", settings.FOLLOWUP_FIRST_DELAY_SECONDS),
    )
    ReminderService.save_task(channel, user_id, task.id)

@celery_app.task
def send_smart_reminder(channel: str, user_id: str, level: int = 1):
    """Recordatorio inteligente: retoma la conversación si quedó algo pendiente.

    El LLM decide si conviene recordar y redacta el mensaje; el código aplica
    las medidas de seguridad duras (anti-bucle): buffer pendiente, cliente
    bloqueado, nada pendiente, tope de recordatorios o tarea obsoleta.
    """
    # El usuario pudo responder justo cuando vence el recordatorio: su mensaje
    # sigue en el buffer (aún sin procesar), pero ya respondió. No enviamos el
    # recordatorio: el process_buffered_messages pendiente cancelará/reagendará
    # según el nuevo estado.
    if BufferService.has_pending(user_id, channel):
        return

    if PostgresUserRepo().is_blocked(user_id, channel=channel):
        return

    state = ConversationStateRepo.get(channel, user_id)
    if not state.awaiting_reply or not state.last_question:
        return
    if state.reminder_level >= settings.FOLLOWUP_MAX_REMINDERS:
        return
    if state.reminder_level >= level:
        return  # Tarea vieja: este nivel ya se envió.

    from src.application.unified_agent import FollowupAgent

    decision = FollowupAgent().decide(state, client_id=user_id, canal=channel)
    if not decision.send or not decision.message:
        return

    # La llamada al LLM tarda segundos: el cliente pudo escribir en ese lapso.
    # Se re-verifica el buffer y que el estado no haya cambiado antes de enviar,
    # para no cruzar un recordatorio con una conversación ya avanzada.
    if BufferService.has_pending(user_id, channel):
        return
    current = ConversationStateRepo.get(channel, user_id)
    if (
        current.last_question != state.last_question
        or current.reminder_level != state.reminder_level
        or not current.awaiting_reply
    ):
        return
    state = current

    ChannelSenderRegistry.send(channel, user_id, decision.message)

    state.reminder_level = level
    state.last_messages = [decision.message]
    state.conversation_history = [
        *state.conversation_history,
        {
            "flow": "AGENT",
            "node": "",
            "type": "smart_reminder",
            "user": "",
            "bot": [decision.message],
            "pending": state.last_question,
        },
    ][-settings.AGENT_HISTORY_LIMIT:]
    ConversationStateRepo.set(channel, user_id, state)

    if level < settings.FOLLOWUP_MAX_REMINDERS:
        task = send_smart_reminder.apply_async(
            (channel, user_id, level + 1),
            countdown=settings.FOLLOWUP_NEXT_DELAY_SECONDS,
        )
        ReminderService.save_task(channel, user_id, task.id)

@celery_app.task
def create_flow_report_and_block(channel: str, user_id: str, report_reason: str):
    state = ConversationStateRepo.get(channel, user_id)
    ReportRepository.create_report(
        nombre=state.user_name,
        numero=user_id,
        problema=f"[{channel}] {report_reason}",
        link_whatsapp=f"https://wa.me/{user_id}",
    )
    PostgresUserRepo().block_user(user_id, reason=report_reason, days=12, channel=channel)
    from src.application.runtime_context import clear_user_runtime_context

    clear_user_runtime_context(channel, user_id, cancel_scheduled=False, clear_reports=False)

@celery_app.task
def schedule_ad_programmed_messages(channel: str, user_id: str, dia: str, valor: str, hora: str, enlace: str):
    # This prepares the 3 messages with the extracted details
    msg1 = "📌 Hola!!!  No recibí respuesta a nuestra conversación.  Podemos continuar???"
    msg2 = "👋🏻Vi que no se unió al grupo👋🏻\n\n*¿¿¿Tiene alguna duda duda antes de unirse al grupo???"
    msg3 = f"📌Hola!!!\n\nLe comparto la información de nuestro curso.\n\nFecha: {dia}\n\nHora: {hora}\nValor: {valor} colones\n\nUnirse al grupo: {enlace}\n\nLe esperamos"
    
    # Schedule with Celery Countdown
    t1 = send_ad_reminder.apply_async((channel, user_id, msg1, 1), countdown=settings.PUB_DELAY_1_SEC)
    t2 = send_ad_reminder.apply_async((channel, user_id, msg2, 2), countdown=settings.PUB_DELAY_2_SEC)
    t3 = send_ad_reminder.apply_async((channel, user_id, msg3, 3), countdown=settings.PUB_DELAY_3_SEC)
    
    # Store IDs to allow cancellation
    key = scoped_key("scheduled_tasks", channel, user_id)
    redis_client.rpush(key, t1.id, t2.id, t3.id)
    redis_client.expire(key, 86400) # Expire in 24h
    save_ad_task_id(channel, user_id, 1, t1.id)
    save_ad_task_id(channel, user_id, 2, t2.id)
    save_ad_task_id(channel, user_id, 3, t3.id)

@celery_app.task
def purge_expired_conversations():
    """Borra el historial de conversaciones que lleva inactivo más de N días.

    Política: `settings.CONVERSATION_RETENTION_DAYS` días desde la última
    interacción. El estado/historial en Redis ya expira solo por TTL; aquí se
    limpia el registro durable en NocoDB (log de conversaciones y, si está
    configurada, la tabla de shots). La agenda la dispara Celery beat a diario.
    """
    days = settings.CONVERSATION_RETENTION_DAYS
    conversations = ConversationLogRepository.purge_older_than(days)
    shots = ConversationShotRepository.purge_older_than(days)
    print(
        f"[retención] Purga >{days}d: {conversations} conversaciones y "
        f"{shots} shots eliminados de NocoDB"
    )
    return {"conversations": conversations, "shots": shots}


@celery_app.task
def schedule_keyword_programmed_messages(channel: str, user_id: str):
    tasks = []
    for stage, node in enumerate(("T2", "T3", "T4"), start=1):
        node_data = get_node_data("KEYWORD", node)
        delay = node_data.get("segundos", 7200)
        task = send_keyword_reminder.apply_async((channel, user_id, node, stage), countdown=delay)
        tasks.append(task.id)

    key = scoped_key("scheduled_tasks", channel, user_id)
    if tasks:
        redis_client.rpush(key, *tasks)
        redis_client.expire(key, RUNTIME_TTL_SECONDS)
