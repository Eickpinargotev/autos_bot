import json
import os
import unittest
from unittest.mock import MagicMock, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.domain.entities import Channel
from src.application.unified_agent import SupervisorAgent
from src.infrastructure.logging.tool_call_logger import ToolCallLogger
from src.infrastructure.repositories.conversation_state_repo import ConversationState
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository


class ConversationToolLoggingTests(unittest.TestCase):
    def test_log_tool_event_inserta_una_fila_con_todo_el_detalle(self):
        """Cada evento es un INSERT: guardar no depende del largo del chat."""
        with patch(
            "src.infrastructure.repositories.conversation_log_repository.ejecutar",
            return_value=1,
        ) as ejecutar_mock:
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
        sql, params = ejecutar_mock.call_args.args
        self.assertIn("INSERT INTO conversation_messages", sql)

        (
            client_id, canal, direction, author, _sender_id, _sender_name,
            message_type, _text, event_type, tool_name, status,
            entrada, salida, _error, duration_ms,
        ) = params

        self.assertEqual(client_id, "5061")
        self.assertEqual(canal, "whatsapp")
        self.assertEqual(direction, "internal")
        self.assertEqual(author, "tool")
        self.assertEqual(message_type, "tool_event")
        self.assertEqual(event_type, "tool_call")
        self.assertEqual(tool_name, "rag.answer_question")
        self.assertEqual(status, "success")
        self.assertEqual(json.loads(entrada)["question"], "Tienen alquiler?")
        self.assertEqual(json.loads(salida)["has_answer"], True)
        self.assertEqual(duration_ms, 12)

    def test_log_tool_event_guarda_el_error(self):
        with patch(
            "src.infrastructure.repositories.conversation_log_repository.ejecutar",
            return_value=1,
        ) as ejecutar_mock:
            result = ConversationLogRepository.log_tool_event(
                client_id="5061",
                canal="whatsapp",
                tool_name="reception.decide",
                status="error",
                error="boom",
            )

        self.assertTrue(result)
        _, params = ejecutar_mock.call_args.args
        self.assertEqual(params[9], "reception.decide")
        self.assertEqual(params[10], "error")
        self.assertEqual(params[13], "boom")
        # Sin datos de entrada/salida se guarda NULL, no un "{}" inútil.
        self.assertIsNone(params[11])
        self.assertIsNone(params[12])

    def test_fallo_de_la_base_no_rompe_el_turno(self):
        """Perder una línea de log jamás debe tumbar la atención al cliente."""
        with patch(
            "src.infrastructure.repositories.conversation_log_repository.ejecutar",
            side_effect=RuntimeError("db caída"),
        ):
            self.assertFalse(
                ConversationLogRepository.log_tool_event(
                    client_id="5061", canal="whatsapp", tool_name="rag.search", status="success"
                )
            )

    def test_tool_call_logger_sanitizes_sensitive_and_long_values(self):
        with patch(
            "src.infrastructure.logging.tool_call_logger.ConversationLogRepository.log_tool_event",
            return_value=True,
        ) as log_mock:
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
