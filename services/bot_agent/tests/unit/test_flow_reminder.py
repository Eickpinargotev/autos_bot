import os
import unittest
from unittest.mock import MagicMock, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NOCODB_INVITACIONES_URL", "http://nocodb.test/invitaciones")
os.environ.setdefault("NOCODB_REPORTES_URL", "http://nocodb.test/reportes")
os.environ.setdefault("NOCODB_CONVERSATIONS_URL", "")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.infrastructure.tasks import celery_app as tasks


class FlowReminderBufferRaceTests(unittest.TestCase):
    def test_skips_reminder_when_user_has_a_pending_buffered_reply(self):
        # El usuario respondió justo al vencer el recordatorio: su mensaje sigue
        # en el buffer. No debe enviarse el recordatorio ni tocar el estado.
        with patch.object(tasks.BufferService, "has_pending", return_value=True), patch.object(
            tasks.ChannelSenderRegistry, "send"
        ) as send_mock, patch.object(tasks, "get_node_data") as node_mock, patch.object(
            tasks.ConversationStateRepo, "set"
        ) as set_mock:
            tasks.send_flow_reminder("telegram", "123", "GENERAL", "G1", 1)

        send_mock.assert_not_called()
        node_mock.assert_not_called()
        set_mock.assert_not_called()

    def test_sends_reminder_when_buffer_is_empty(self):
        node_data = {"recordatorio": {"mensajes": ["¿Sigue ahí?"], "reporte": ""}}
        stored = MagicMock(last_question="", last_messages=[], reminder_level=0, pending_report="")
        with patch.object(tasks.BufferService, "has_pending", return_value=False), patch.object(
            tasks.ChannelSenderRegistry, "send"
        ) as send_mock, patch.object(tasks, "get_node_data", return_value=node_data), patch.object(
            tasks.ConversationStateRepo, "get", return_value=stored
        ), patch.object(tasks.ConversationStateRepo, "set"):
            tasks.send_flow_reminder("telegram", "123", "GENERAL", "G1", 1)

        send_mock.assert_called_once_with("telegram", "123", "¿Sigue ahí?")


if __name__ == "__main__":
    unittest.main()
