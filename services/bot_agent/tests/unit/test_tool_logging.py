import json
import os
import unittest
from unittest.mock import MagicMock, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NOCODB_CONVERSATIONS_URL", "http://nocodb.test/conversations")
os.environ.setdefault("NOCODB_TOKEN", "test-token")

from src.domain.entities import Channel
from src.application.unified_agent import SupervisorAgent
from src.infrastructure.logging.tool_call_logger import ToolCallLogger
from src.infrastructure.repositories.conversation_state_repo import ConversationState
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository


class ConversationToolLoggingTests(unittest.TestCase):
    def test_log_tool_event_creates_new_conversation(self):
        with patch(
            "src.infrastructure.repositories.conversation_log_repository.ConversationLogRepository.find_by_client_channel",
            return_value=None,
        ), patch(
            "src.infrastructure.repositories.conversation_log_repository.ConversationLogRepository.create_conversation",
            return_value=True,
        ) as create_mock:
            result = ConversationLogRepository.log_tool_event(
                client_id="5061",
                canal=Channel.WHATSAPP,
                tool_name="rag.answer_question",
                status="success",
                input_data={"question": "Tienen alquiler?"},
                output_data={"has_answer": True},
                duration_ms=12,
            )

        self.assertTrue(result)
        conversation = create_mock.call_args.args[2]
        message = conversation["messages"][0]
        self.assertEqual(message["direction"], "internal")
        self.assertEqual(message["author"], "tool")
        self.assertEqual(message["message_type"], "tool_event")
        self.assertEqual(message["event_type"], "tool_call")
        self.assertEqual(message["tool_name"], "rag.answer_question")
        self.assertEqual(message["status"], "success")
        self.assertEqual(message["input"]["question"], "Tienen alquiler?")
        self.assertEqual(message["output"]["has_answer"], True)
        self.assertEqual(message["duration_ms"], 12)

    def test_log_tool_event_appends_to_existing_conversation(self):
        stored = {
            "id": "rec1",
            "fields": {
                "json_mensajes": json.dumps(
                    {
                        "schema_version": 1,
                        "client_id": "5061",
                        "canal": "whatsapp",
                        "created_at": "2026-01-01T00:00:00-06:00",
                        "updated_at": "2026-01-01T00:00:00-06:00",
                        "messages": [{"id": "old", "direction": "inbound"}],
                    }
                )
            },
        }

        with patch(
            "src.infrastructure.repositories.conversation_log_repository.ConversationLogRepository.find_by_client_channel",
            return_value=stored,
        ), patch(
            "src.infrastructure.repositories.conversation_log_repository.ConversationLogRepository.update_conversation",
            return_value=True,
        ) as update_mock:
            result = ConversationLogRepository.log_tool_event(
                client_id="5061",
                canal="whatsapp",
                tool_name="reception.decide",
                status="error",
                error="boom",
            )

        self.assertTrue(result)
        record_id, conversation = update_mock.call_args.args
        self.assertEqual(record_id, "rec1")
        self.assertEqual(len(conversation["messages"]), 2)
        self.assertEqual(conversation["messages"][0]["direction"], "inbound")
        self.assertEqual(conversation["messages"][1]["tool_name"], "reception.decide")
        self.assertEqual(conversation["messages"][1]["status"], "error")
        self.assertEqual(conversation["messages"][1]["error"], "boom")

    def test_tool_call_logger_sanitizes_sensitive_and_long_values(self):
        with patch(
            "src.infrastructure.logging.tool_call_logger.ConversationLogRepository.log_tool_event",
            return_value=True,
        ) as log_mock, patch(
            "src.infrastructure.logging.tool_call_logger.settings.NOCODB_TOKEN",
            "test-token",
        ):
            ToolCallLogger.success(
                client_id="5061",
                canal="whatsapp",
                tool_name="rag.search",
                input_data={"token": "secret", "question": "x" * 1200},
                output_data={"items": list(range(12))},
            )

        kwargs = log_mock.call_args.kwargs
        self.assertEqual(kwargs["input_data"]["token"], "[redacted]")
        self.assertTrue(kwargs["input_data"]["question"].endswith("...[truncated]"))
        self.assertEqual(kwargs["output_data"]["items"][-1]["_truncated_items"], 2)

    def test_agent_decide_logs_error_when_llm_falls_back(self):
        with patch(
            "src.infrastructure.logging.tool_call_logger.ConversationLogRepository.log_tool_event",
            return_value=True,
        ) as log_mock, patch(
            "src.application.unified_agent.settings.OPENAI_API_KEY",
            "test-key",
        ), patch(
            "src.application.unified_agent.client.chat.completions.create",
            side_effect=RuntimeError("openai failed"),
        ):
            decision = SupervisorAgent().decide(
                "Hola, quiero información",
                ConversationState(),
                client_id="5061",
                canal=Channel.WHATSAPP,
            )

        self.assertTrue(decision.action)
        kwargs = log_mock.call_args.kwargs
        self.assertEqual(kwargs["tool_name"], "agent.decide.supervisor")
        self.assertEqual(kwargs["status"], "error")
        self.assertIn("openai failed", kwargs["error"])


if __name__ == "__main__":
    unittest.main()
