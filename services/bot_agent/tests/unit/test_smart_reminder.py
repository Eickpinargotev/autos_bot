"""Medidas de seguridad del recordatorio inteligente (anti-bucle).

El LLM solo redacta; las condiciones duras (buffer pendiente, bloqueado,
nada pendiente, tope de recordatorios, tarea obsoleta) las decide el código
y se prueban aquí de forma determinista.
"""

import os
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.core.config import settings
from src.infrastructure.repositories.conversation_state_repo import ConversationState
from src.infrastructure.tasks import celery_app as tasks


def _pending_state(level: int = 0) -> ConversationState:
    return ConversationState(
        flow="AGENT",
        last_question="¿Pudo llenar el formulario de reservación?",
        awaiting_reply=True,
        reminder_level=level,
        conversation_history=[{"flow": "AGENT", "node": "", "type": "agent_reply", "user": "ok", "bot": ["[[frag:GENERAL.G16]]"]}],
    )


class SmartReminderHarness(ExitStack):
    def __init__(self, state: ConversationState, *, buffer_pending=False, blocked=False,
                 followup=None, enabled=True, interval=60, max_reminders=2):
        super().__init__()
        self.state = state
        self.buffer_pending = buffer_pending
        self.blocked = blocked
        self.followup_decision = followup or MagicMock(send=True, message="📌 Hola!!! ¿Pudo llenar el formulario?")
        self.enabled = enabled
        self.interval = interval
        self.max_reminders = max_reminders

    def __enter__(self):
        super().__enter__()
        self.enter_context(patch.object(tasks.BufferService, "has_pending", return_value=self.buffer_pending))
        blocked_repo = MagicMock()
        blocked_repo.is_blocked.return_value = self.blocked
        self.enter_context(patch.object(tasks, "PostgresUserRepo", return_value=blocked_repo))
        self.enter_context(patch.object(tasks.ConversationStateRepo, "get", return_value=self.state))
        self.enter_context(patch.object(
            tasks.instrucciones_repository,
            "configuracion_recordatorios",
            return_value={
                "habilitado": self.enabled,
                "intervalo_minutos": self.interval,
                "maximo_recordatorios": self.max_reminders,
            },
        ))
        self.set_mock = self.enter_context(patch.object(tasks.ConversationStateRepo, "set"))
        self.send_mock = self.enter_context(patch.object(tasks.ChannelSenderRegistry, "send"))
        followup_agent = MagicMock()
        followup_agent.decide.return_value = self.followup_decision
        self.followup_cls = self.enter_context(
            patch("src.application.unified_agent.FollowupAgent", return_value=followup_agent)
        )
        self.apply_async = self.enter_context(patch.object(tasks.send_smart_reminder, "apply_async"))
        self.save_task = self.enter_context(patch.object(tasks.ReminderService, "save_task"))
        return self


class SmartReminderGuardrailTests(unittest.TestCase):
    def test_skips_when_project_disabled_reminders_after_task_was_scheduled(self):
        with SmartReminderHarness(_pending_state(), enabled=False) as h:
            tasks.send_smart_reminder("whatsapp", "506", 1)
        h.send_mock.assert_not_called()
        h.followup_cls.assert_not_called()

    def test_skips_when_user_reply_is_buffered(self):
        with SmartReminderHarness(_pending_state(), buffer_pending=True) as h:
            tasks.send_smart_reminder("whatsapp", "506", 1)
        h.send_mock.assert_not_called()
        h.followup_cls.assert_not_called()

    def test_skips_when_user_is_blocked(self):
        with SmartReminderHarness(_pending_state(), blocked=True) as h:
            tasks.send_smart_reminder("whatsapp", "506", 1)
        h.send_mock.assert_not_called()

    def test_skips_when_nothing_is_pending(self):
        state = ConversationState(flow="AGENT", awaiting_reply=False, last_question="")
        with SmartReminderHarness(state) as h:
            tasks.send_smart_reminder("whatsapp", "506", 1)
        h.send_mock.assert_not_called()

    def test_skips_when_reminder_cap_reached(self):
        with SmartReminderHarness(_pending_state(level=1), max_reminders=1) as h:
            tasks.send_smart_reminder("whatsapp", "506", 2)
        h.send_mock.assert_not_called()

    def test_skips_already_queued_level_above_new_project_limit(self):
        with SmartReminderHarness(_pending_state(), max_reminders=1) as h:
            tasks.send_smart_reminder("whatsapp", "506", 2)
        h.send_mock.assert_not_called()
        h.followup_cls.assert_not_called()

    def test_skips_stale_task_for_already_sent_level(self):
        with SmartReminderHarness(_pending_state(level=1)) as h:
            tasks.send_smart_reminder("whatsapp", "506", 1)
        h.send_mock.assert_not_called()

    def test_respects_llm_decision_not_to_remind(self):
        quiet = MagicMock(send=False, message="")
        with SmartReminderHarness(_pending_state(), followup=quiet) as h:
            tasks.send_smart_reminder("whatsapp", "506", 1)
        h.send_mock.assert_not_called()
        h.set_mock.assert_not_called()


class SmartReminderRaceTests(unittest.TestCase):
    def test_skips_if_user_wrote_while_llm_was_thinking(self):
        # El buffer estaba vacío al inicio, pero el cliente escribió durante la
        # llamada al LLM: el segundo chequeo debe abortar el envío.
        with SmartReminderHarness(_pending_state()) as h:
            h.enter_context(
                patch.object(tasks.BufferService, "has_pending", side_effect=[False, True])
            )
            tasks.send_smart_reminder("whatsapp", "506", 1)
        h.send_mock.assert_not_called()
        h.set_mock.assert_not_called()

    def test_skips_if_state_changed_while_llm_was_thinking(self):
        # Otro turno procesó y cambió lo pendiente: el recordatorio quedó viejo.
        stale = _pending_state()
        fresh = _pending_state()
        fresh.last_question = "Otra cosa distinta quedó pendiente"
        with SmartReminderHarness(stale) as h:
            h.enter_context(
                patch.object(tasks.ConversationStateRepo, "get", side_effect=[stale, fresh])
            )
            tasks.send_smart_reminder("whatsapp", "506", 1)
        h.send_mock.assert_not_called()


class SmartReminderSendTests(unittest.TestCase):
    def test_sends_updates_state_and_schedules_next_level(self):
        state = _pending_state()
        with SmartReminderHarness(state) as h:
            tasks.send_smart_reminder("whatsapp", "506", 1)

        h.send_mock.assert_called_once()
        saved = h.set_mock.call_args.args[2]
        self.assertEqual(saved.reminder_level, 1)
        self.assertEqual(saved.conversation_history[-1]["type"], "smart_reminder")
        h.apply_async.assert_called_once()
        args, kwargs = h.apply_async.call_args
        self.assertEqual(args[0][2], 2)
        self.assertEqual(kwargs["countdown"], 3600)
        h.save_task.assert_called_once()

    def test_uses_same_configured_interval_for_next_level(self):
        with SmartReminderHarness(_pending_state(), interval=20) as h:
            tasks.send_smart_reminder("whatsapp", "506", 1)
        self.assertEqual(h.apply_async.call_args.kwargs["countdown"], 1200)

    def test_last_allowed_level_does_not_reschedule(self):
        state = _pending_state()
        with SmartReminderHarness(state, max_reminders=1) as h:
            tasks.send_smart_reminder("whatsapp", "506", 1)

        h.send_mock.assert_called_once()
        h.apply_async.assert_not_called()


if __name__ == "__main__":
    unittest.main()
