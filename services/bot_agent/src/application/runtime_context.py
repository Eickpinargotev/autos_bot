from src.application.buffer_service import BufferService, redis_client, scoped_key
from src.domain.entities import Channel


AD_REPORT_TEXT = "El usuario respondio a un recordatorio para unirse al grupo"
WELCOME_REPORT_CONTEXT = "welcome"
AD_REPORT_CONTEXT = "ad_group_reminder"
KEYWORD_REPORT_CONTEXT = "keyword_reminder"
MEDIA_REPORT_CONTEXT = "media_review"
RUNTIME_TTL_SECONDS = 60 * 60 * 24 * 14
REPORT_TTL_SECONDS = 60 * 60 * 24 * 60


# Etapas posibles de recordatorios de publicidad (claves ad_reminder_task:N).
AD_REMINDER_STAGES = (1, 2, 3)
# Contextos posibles de claves report_fired:<context>. Se enumeran para poder
# borrarlas por clave exacta: un SCAN por patrón recorre TODO el keyspace de
# Redis y en el camino caliente de mensajes se vuelve lento con muchos usuarios.
REPORT_CONTEXTS = (
    WELCOME_REPORT_CONTEXT,
    AD_REPORT_CONTEXT,
    KEYWORD_REPORT_CONTEXT,
    MEDIA_REPORT_CONTEXT,
)


def _key(prefix: str, channel: Channel | str, user_id: str) -> str:
    return scoped_key(prefix, channel, user_id)


def _delete_ad_reminder_tasks(channel: Channel | str, user_id: str):
    base = _key("ad_reminder_task", channel, user_id)
    redis_client.delete(*[f"{base}:{stage}" for stage in AD_REMINDER_STAGES])


def mark_report_once(channel: Channel | str, user_id: str, context: str) -> bool:
    key = f"{_key('report_fired', channel, user_id)}:{context}"
    return bool(redis_client.set(key, "1", nx=True, ex=REPORT_TTL_SECONDS))


def clear_report_markers(channel: Channel | str, user_id: str):
    base = _key("report_fired", channel, user_id)
    redis_client.delete(*[f"{base}:{context}" for context in REPORT_CONTEXTS])


def set_welcome_context(channel: Channel | str, user_id: str):
    redis_client.setex(_key("welcome_report_context", channel, user_id), RUNTIME_TTL_SECONDS, "1")
    redis_client.delete(
        _key("joined_group", channel, user_id),
        _key("ad_report_context", channel, user_id),
        _key("ad_report_consumed", channel, user_id),
        _key("ad_reminder_stage", channel, user_id),
    )
    _delete_ad_reminder_tasks(channel, user_id)


def consume_welcome_context(channel: Channel | str, user_id: str) -> bool:
    key = _key("welcome_report_context", channel, user_id)
    if redis_client.delete(key):
        return True

    # Delete stale legacy markers without reporting; they are the source of residual leaks.
    redis_client.delete(_key("joined_group", channel, user_id))
    return False


def register_ad_context(channel: Channel | str, user_id: str):
    redis_client.setex(_key("ad_report_context", channel, user_id), RUNTIME_TTL_SECONDS, "1")
    redis_client.delete(_key("ad_report_consumed", channel, user_id))


def register_keyword_context(channel: Channel | str, user_id: str):
    redis_client.setex(_key("keyword_report_context", channel, user_id), RUNTIME_TTL_SECONDS, "1")
    redis_client.delete(
        _key("keyword_report_consumed", channel, user_id),
        _key("keyword_active_report", channel, user_id),
        _key("keyword_reminder_stage", channel, user_id),
    )
    redis_client.delete(f"{_key('report_fired', channel, user_id)}:{KEYWORD_REPORT_CONTEXT}")


def has_ad_context(channel: Channel | str, user_id: str) -> bool:
    return bool(redis_client.exists(_key("ad_report_context", channel, user_id)))


def has_keyword_context(channel: Channel | str, user_id: str) -> bool:
    return bool(redis_client.exists(_key("keyword_report_context", channel, user_id)))


def consume_ad_report(channel: Channel | str, user_id: str) -> bool:
    if not has_ad_context(channel, user_id):
        return False
    if redis_client.exists(_key("ad_report_consumed", channel, user_id)):
        return False
    redis_client.setex(_key("ad_report_consumed", channel, user_id), REPORT_TTL_SECONDS, "1")
    return mark_report_once(channel, user_id, AD_REPORT_CONTEXT)


def consume_keyword_report(channel: Channel | str, user_id: str) -> str:
    if not has_keyword_context(channel, user_id):
        return ""
    if redis_client.exists(_key("keyword_report_consumed", channel, user_id)):
        return ""

    report_text = redis_client.get(_key("keyword_active_report", channel, user_id)) or ""
    if not report_text:
        return ""

    redis_client.setex(_key("keyword_report_consumed", channel, user_id), REPORT_TTL_SECONDS, "1")
    if not mark_report_once(channel, user_id, KEYWORD_REPORT_CONTEXT):
        return ""
    return report_text


def set_ad_reminder_stage(channel: Channel | str, user_id: str, stage: int):
    redis_client.setex(_key("ad_reminder_stage", channel, user_id), RUNTIME_TTL_SECONDS, stage)


def set_keyword_active_report(channel: Channel | str, user_id: str, stage: int, report_text: str):
    redis_client.setex(_key("keyword_reminder_stage", channel, user_id), RUNTIME_TTL_SECONDS, stage)
    if report_text:
        redis_client.setex(_key("keyword_active_report", channel, user_id), RUNTIME_TTL_SECONDS, report_text)


def get_ad_reminder_stage(channel: Channel | str, user_id: str) -> int:
    value = redis_client.get(_key("ad_reminder_stage", channel, user_id))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def save_ad_task_id(channel: Channel | str, user_id: str, stage: int, task_id: str):
    redis_client.setex(f"{_key('ad_reminder_task', channel, user_id)}:{stage}", RUNTIME_TTL_SECONDS, task_id)


def get_ad_task_id(channel: Channel | str, user_id: str, stage: int) -> str:
    return redis_client.get(f"{_key('ad_reminder_task', channel, user_id)}:{stage}") or ""


def clear_user_runtime_context(
    channel: Channel | str,
    user_id: str,
    *,
    cancel_scheduled: bool = False,
    clear_reports: bool = False,
):
    from src.application.reminder_service import ReminderService
    from src.infrastructure.repositories.conversation_state_repo import ConversationStateRepo

    ReminderService.cancel(channel, user_id)
    BufferService.get_and_clear_buffer(user_id, channel)
    ConversationStateRepo.clear(channel, user_id)

    redis_client.delete(
        _key("joined_group", channel, user_id),
        _key("welcome_report_context", channel, user_id),
        _key("ad_report_context", channel, user_id),
        _key("ad_report_consumed", channel, user_id),
        _key("ad_reminder_stage", channel, user_id),
        _key("keyword_report_context", channel, user_id),
        _key("keyword_report_consumed", channel, user_id),
        _key("keyword_active_report", channel, user_id),
        _key("keyword_reminder_stage", channel, user_id),
    )
    _delete_ad_reminder_tasks(channel, user_id)

    if clear_reports:
        clear_report_markers(channel, user_id)

    if cancel_scheduled:
        from src.infrastructure.tasks.celery_app import cancel_scheduled_tasks

        channel_value = channel.value if isinstance(channel, Channel) else channel
        cancel_scheduled_tasks(channel_value, user_id)
