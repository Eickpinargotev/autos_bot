from src.application.buffer_service import redis_client, scoped_key
from src.domain.entities import Channel


class ReminderService:
    @staticmethod
    def reminder_key(channel: Channel | str, user_id: str) -> str:
        return scoped_key("flow_reminders", channel, user_id)

    @staticmethod
    def save_task(channel: Channel | str, user_id: str, task_id: str):
        key = ReminderService.reminder_key(channel, user_id)
        redis_client.rpush(key, task_id)
        redis_client.expire(key, 172800)

    @staticmethod
    def cancel(channel: Channel | str, user_id: str):
        from src.infrastructure.tasks.celery_app import celery_app

        key = ReminderService.reminder_key(channel, user_id)
        task_ids = redis_client.lrange(key, 0, -1)
        for task_id in task_ids:
            celery_app.control.revoke(task_id, terminate=True)
        redis_client.delete(key)
