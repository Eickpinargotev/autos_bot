from celery import Celery
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
from src.infrastructure.repositories.redis_state_repo import RedisStateRepo
from src.infrastructure.channels.senders import ChannelSenderRegistry
from src.infrastructure.repositories.conversation_state_repo import ConversationStateRepo
from src.infrastructure.repositories.postgres_user_repo import PostgresUserRepo
from src.infrastructure.repositories.report_repository import ReportRepository
from src.infrastructure.evals.conversation_shots import (
    ConversationShotBuilder,
    ConversationShotRepository,
    ShotTraceCollector,
)
from src.application.fsm import process_fsm

celery_app = Celery("bot_agent_tasks", broker=settings.REDIS_URL)

@celery_app.task
def process_buffered_messages(channel: str, user_id: str, user_name: str = "Desconocido", seq: int | None = None):
    channel_value = Channel(channel)
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
    state = RedisStateRepo.get_state(user_id, channel_value)
    with ShotTraceCollector() as shot_collector:
        result = process_fsm(user_id, state, text, channel=channel_value, user_name=user_name)
    RedisStateRepo.set_state(user_id, result.legacy_state, channel_value)
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
        schedule_flow_reminder_for_result(channel_value, user_id, result.reminder)


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

def schedule_flow_reminder_for_result(channel: Channel | str, user_id: str, reminder: dict):
    task = send_flow_reminder.apply_async(
        (Channel(channel).value if isinstance(channel, str) else channel.value, user_id, reminder["flow"], reminder["node"], reminder.get("level", 1)),
        countdown=reminder.get("seconds", 7200),
    )
    ReminderService.save_task(channel, user_id, task.id)

@celery_app.task
def send_flow_reminder(channel: str, user_id: str, flow: str, node: str, reminder_level: int = 1):
    # El usuario pudo responder justo cuando vence el recordatorio: su mensaje
    # sigue en el buffer (aún sin procesar), pero ya respondió. No enviamos el
    # recordatorio ni reprogramamos: el process_buffered_messages pendiente
    # cancelará/reprogramará los recordatorios según el nuevo estado.
    if BufferService.has_pending(user_id, channel):
        return

    node_data = get_node_data(flow, node)
    reminder = _reminder_at_level(node_data.get("recordatorio"), reminder_level)
    if not reminder:
        return

    messages = reminder.get("mensajes", [])
    for msg in messages:
        ChannelSenderRegistry.send(channel, user_id, msg)

    state = ConversationStateRepo.get(channel, user_id)
    state.pending_report = reminder.get("reporte", "")
    state.last_messages = messages
    state.last_question = state.last_question or _extract_last_question(messages)
    state.reminder_level = reminder_level
    ConversationStateRepo.set(channel, user_id, state)

    next_reminder = reminder.get("recordatorio")
    if next_reminder:
        task = send_flow_reminder.apply_async(
            (channel, user_id, flow, node, reminder_level + 1),
            countdown=next_reminder.get("segundos", 7200),
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

def _reminder_at_level(reminder: dict | None, level: int) -> dict | None:
    current = reminder
    current_level = 1
    while current and current_level < level:
        current = current.get("recordatorio")
        current_level += 1
    return current

def _extract_last_question(messages: list[str]) -> str:
    for msg in reversed(messages):
        for line in reversed([line.strip() for line in msg.splitlines() if line.strip()]):
            if "?" in line or "¿" in line:
                return line
    return messages[-1] if messages else ""

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
