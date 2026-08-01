import json
import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application.agent_pipeline import TurnResult
from src.domain.entities import Channel
from src.infrastructure.evals.conversation_shots import (
    ConversationShotBuilder,
    ConversationShotRepository,
    ShotTraceCollector,
)
from src.infrastructure.logging.tool_call_logger import ToolCallLogger
from src.infrastructure.repositories.conversation_state_repo import ConversationState


class ConversationShotTests(unittest.TestCase):
    def test_builder_creates_compact_shot_without_history_dates(self):
        before = ConversationState(
            flow="GENERAL",
            node="G35",
            last_question="Donde es su prueba de manejo???",
            awaiting_reply=True,
            conversation_history=[
                {
                    "flow": "GENERAL",
                    "node": "G1",
                    "user": "hola",
                    "bot": ["Ya tiene el teórico ganado???"],
                    "created_at": "2026-01-01T00:00:00",
                }
            ],
        )
        after = ConversationState(flow="GENERAL", node="G35", last_question="Donde es su prueba de manejo???")

        shot_id, fecha_hora, shot = ConversationShotBuilder.build(
            channel=Channel.WHATSAPP,
            user_id="5061",
            user_name="Cliente",
            user_message="quiero saber si puedo llevar acompañante",
            bot_replies=["Puede consultarlo.", "Donde es su prueba de manejo???"],
            state_before=before,
            state_after=after,
            tools=[{"tool_name": "rag.answer_question", "status": "success"}],
            now=datetime(2026, 6, 6, 17, 45, 33),
        )

        self.assertEqual(shot_id, "5061_20260606_174533")
        self.assertEqual(fecha_hora, "2026-06-06 17:45:33")
        self.assertNotIn("shot_id", shot)
        self.assertNotIn("source", shot)
        self.assertEqual(shot["state_before"]["flow"], "GENERAL")
        self.assertEqual(shot["history"][0]["user"], "hola")
        self.assertEqual(shot["history"][0]["bot"], ["Ya tiene el teórico ganado???"])
        self.assertNotIn("flow", shot["history"][0])
        self.assertNotIn("node", shot["history"][0])
        self.assertNotIn("created_at", shot["history"][0])
        self.assertEqual(shot["turn"]["user_message"], "quiero saber si puedo llevar acompañante")
        self.assertEqual(
            [event["type"] for event in shot["turn"]["events"]],
            ["user_message", "tool_call", "bot_message", "bot_message"],
        )
        self.assertEqual(shot["turn"]["events"][1]["tool_name"], "rag.answer_question")
        self.assertEqual(shot["turn"]["events"][2]["text"], "Puede consultarlo.")
        self.assertEqual(shot["review"]["status"], "unreviewed")

    def test_repository_saves_expected_columns(self):
        shot = {"turn": {"user_message": "hola"}}

        with patch("src.infrastructure.evals.conversation_shots.ejecutar", return_value=1) as ejecutar_mock:
            result = ConversationShotRepository.save(
                fecha_hora="2026-06-06 17:45:33",
                id_user="5061",
                chanel=Channel.WHATSAPP,
                shot=shot,
            )

        self.assertTrue(result)
        sql, params = ejecutar_mock.call_args.args
        self.assertIn("INSERT INTO conversation_shots", sql)
        fecha_hora, id_user, canal, shot_json = params
        self.assertEqual(fecha_hora, "2026-06-06 17:45:33")
        self.assertEqual(id_user, "5061")
        self.assertEqual(canal, "whatsapp")
        self.assertEqual(json.loads(shot_json)["turn"]["user_message"], "hola")
        self.assertNotIn("shot_id", json.loads(shot_json))
        self.assertNotIn("tools", json.loads(shot_json))

    def test_tool_logger_records_sanitized_tool_event_in_active_shot(self):
        with ShotTraceCollector() as collector:
            ToolCallLogger.success(
                client_id="5061",
                canal=Channel.WHATSAPP,
                tool_name="rag.answer_question",
                input_data={"token": "secret", "question": "x" * 1200},
                output_data={"has_answer": True},
            )

        self.assertEqual(len(collector.tools), 1)
        event = collector.tools[0]
        self.assertEqual(event["tool_name"], "rag.answer_question")
        self.assertEqual(event["type"], "tool_call")
        self.assertEqual(event["order"], 1)
        self.assertEqual(event["status"], "success")
        self.assertEqual(event["input"]["token"], "[redacted]")
        self.assertTrue(event["input"]["question"].endswith("...[truncated]"))

    def test_process_buffered_messages_saves_human_bot_shot(self):
        before = ConversationState(
            flow="GENERAL",
            node="G35",
            last_question="Donde es su prueba de manejo???",
            awaiting_reply=True,
            conversation_history=[{"flow": "GENERAL", "node": "G1", "user": "hola", "bot": ["pregunta"]}],
        )
        after = ConversationState(flow="GENERAL", node="G35", last_question="Donde es su prueba de manejo???")

        with patch("src.infrastructure.tasks.celery_app.BufferService.get_and_clear_buffer", return_value="quiero saber algo"), patch(
            "src.infrastructure.tasks.celery_app.ReminderService.cancel"
        ), patch("src.infrastructure.tasks.celery_app.ConversationStateRepo.get", side_effect=[before, after]), patch(
            "src.infrastructure.tasks.celery_app.run_agent_turn",
            return_value=TurnResult(replies=["respuesta bot"]),
        ), patch(
            "src.infrastructure.tasks.celery_app.ConversationShotRepository.save",
            return_value=True,
        ) as save_mock, patch(
            "src.infrastructure.tasks.celery_app.ChannelSenderRegistry.send"
        ):
            from src.infrastructure.tasks.celery_app import process_buffered_messages

            process_buffered_messages("whatsapp", "5061", "Cliente")

        shot = save_mock.call_args.kwargs["shot"]
        self.assertNotIn("source", shot)
        self.assertEqual(shot["turn"]["user_message"], "quiero saber algo")
        self.assertEqual(shot["turn"]["bot_replies"], ["respuesta bot"])
        self.assertEqual(shot["turn"]["events"][0]["type"], "user_message")
        self.assertEqual(shot["turn"]["events"][-1]["type"], "bot_message")
        self.assertEqual(shot["state_before"]["flow"], "GENERAL")
        self.assertEqual(shot["state_after"]["node"], "G35")

    def test_smart_reminder_does_not_save_conversation_shot(self):
        state = ConversationState(
            last_question="¿Pudo llenar el formulario?",
            awaiting_reply=True,
            reminder_level=0,
        )
        blocked_repo = MagicMock()
        blocked_repo.is_blocked.return_value = False
        followup = MagicMock()
        followup.decide.return_value = MagicMock(send=True, message="📌 Hola!!! ¿Pudo llenar el formulario?")
        with patch(
            "src.infrastructure.tasks.celery_app.BufferService.has_pending", return_value=False
        ), patch(
            "src.infrastructure.tasks.celery_app.PostgresUserRepo", return_value=blocked_repo
        ), patch("src.infrastructure.tasks.celery_app.ChannelSenderRegistry.send"), patch(
            "src.infrastructure.tasks.celery_app.ConversationStateRepo.get",
            return_value=state,
        ), patch("src.infrastructure.tasks.celery_app.ConversationStateRepo.set"), patch(
            "src.application.unified_agent.FollowupAgent", return_value=followup
        ), patch(
            "src.infrastructure.tasks.celery_app.ReminderService.save_task"
        ), patch(
            "src.infrastructure.tasks.celery_app.send_smart_reminder.apply_async"
        ), patch(
            "src.infrastructure.tasks.celery_app.ConversationShotRepository.save"
        ) as save_mock:
            from src.infrastructure.tasks.celery_app import send_smart_reminder

            send_smart_reminder("whatsapp", "5061", 1)

        save_mock.assert_not_called()

    def test_repository_failure_does_not_raise(self):
        with patch(
            "src.infrastructure.evals.conversation_shots.ejecutar",
            side_effect=RuntimeError("base caída"),
        ):
            result = ConversationShotRepository.save(
                fecha_hora="2026-06-06 17:45:33",
                id_user="5061",
                chanel="whatsapp",
                shot={"turn": {"user_message": "hola"}},
            )

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
