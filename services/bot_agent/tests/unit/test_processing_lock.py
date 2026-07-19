import os
import unittest
from unittest.mock import patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NOCODB_CONVERSATIONS_URL", "")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application.buffer_service import scoped_key
from src.domain.entities import Channel
from src.infrastructure.tasks import celery_app as tasks


class ProcessingLockTests(unittest.TestCase):
    """Candado por conversación en process_buffered_messages.

    Garantiza que dos turnos del MISMO usuario nunca se procesan en paralelo
    (el turno lento pisaría el estado del nuevo), sin bloquear a otros usuarios.
    """

    def test_reschedules_without_draining_when_lock_is_held(self):
        with patch.object(tasks.redis_client, "set", return_value=False) as set_mock, patch.object(
            tasks.process_buffered_messages, "apply_async"
        ) as reschedule_mock, patch.object(
            tasks.BufferService, "drain_if_current"
        ) as drain_mock, patch.object(tasks, "run_agent_turn") as turn_mock:
            tasks.process_buffered_messages("whatsapp", "5061", "Cliente", 3)

        lock_key = scoped_key("processing", Channel.WHATSAPP, "5061")
        set_mock.assert_called_once_with(lock_key, "1", nx=True, ex=tasks._PROCESSING_LOCK_TTL_SECONDS)
        # La tarea se reagenda con los MISMOS argumentos (el debounce por seq
        # decide después) y no toca el buffer ni el grafo.
        reschedule_mock.assert_called_once_with(("whatsapp", "5061", "Cliente", 3), countdown=2)
        drain_mock.assert_not_called()
        turn_mock.assert_not_called()

    def test_releases_lock_after_processing(self):
        with patch.object(tasks.redis_client, "set", return_value=True), patch.object(
            tasks.redis_client, "delete"
        ) as delete_mock, patch.object(
            tasks.BufferService, "drain_if_current", return_value=""
        ):
            tasks.process_buffered_messages("whatsapp", "5061", "Cliente", 3)

        delete_mock.assert_called_once_with(scoped_key("processing", Channel.WHATSAPP, "5061"))

    def test_releases_lock_even_if_processing_fails(self):
        with patch.object(tasks.redis_client, "set", return_value=True), patch.object(
            tasks.redis_client, "delete"
        ) as delete_mock, patch.object(
            tasks.BufferService, "drain_if_current", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                tasks.process_buffered_messages("whatsapp", "5061", "Cliente", 3)

        delete_mock.assert_called_once_with(scoped_key("processing", Channel.WHATSAPP, "5061"))


if __name__ == "__main__":
    unittest.main()
