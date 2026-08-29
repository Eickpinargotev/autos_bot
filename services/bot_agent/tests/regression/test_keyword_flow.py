import os
import unittest
from unittest.mock import MagicMock, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application import human_intervention
from src.application.conversation_orchestrator import ConversationOrchestrator
from src.domain.entities import Channel, InboundMessage, MessageType


# Las palabras clave dejaron de estar escritas en el código: son filas que el
# negocio administra desde el panel. Estos tests prueban el FLUJO (bloquear,
# enviar, agendar, cancelar lo anterior), no de dónde salen los textos, así que
# la tabla se simula con este diccionario en vez de montar una base.
_PALABRAS = {
    "tareas": {
        "id": 1,
        "palabra": "tareas",
        "textos": ["Para iniciar con el curso teórico...\nMOTOCICLETA: enlace"],
    },
    "transporte": {
        "id": 2,
        "palabra": "transporte",
        "textos": ["Para iniciar con el curso teórico de transporte público..."],
    },
}


def _buscar(texto):
    return _PALABRAS.get(" ".join(str(texto or "").split()).lower())


def _textos_de(palabra_id):
    for palabra in _PALABRAS.values():
        if palabra["id"] == palabra_id:
            return palabra["textos"]
    return []


class KeywordFlowTests(unittest.TestCase):
    def setUp(self):
        for nombre, doble in (("buscar", _buscar), ("textos_de", _textos_de)):
            parche = patch(
                f"src.application.conversation_orchestrator.palabras_clave_repository.{nombre}",
                side_effect=doble,
            )
            parche.start()
            self.addCleanup(parche.stop)

    def _message(self, text: str) -> InboundMessage:
        return InboundMessage(
            channel=Channel.WHATSAPP,
            user_id="50688888888",
            user_name="Cliente",
            message_type=MessageType.TEXT,
            text=text,
        )

    def test_tareas_exact_keyword_blocks_sends_first_message_and_schedules_reminders(self):
        repo = MagicMock()
        repo.is_blocked.return_value = False

        with patch("src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo), patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch("src.application.conversation_orchestrator.register_keyword_context") as register_mock, patch(
            "src.application.conversation_orchestrator.schedule_keyword_programmed_messages.apply_async"
        ) as schedule_mock, patch(
            "src.application.conversation_orchestrator.KeywordRegistryRepository.register_if_missing"
        ) as registry_mock, patch("src.application.conversation_orchestrator.cancel_scheduled_tasks") as cancel_mock:
            actions = ConversationOrchestrator().handle(self._message("TaReAs"))

        cancel_mock.assert_called_once_with(Channel.WHATSAPP.value, "50688888888")
        repo.block_user.assert_called_once_with(
            "50688888888",
            reason="Flujo keyword tareas",
            channel=Channel.WHATSAPP,
        )
        registry_mock.assert_called_once_with("50688888888", "Cliente", Channel.WHATSAPP, "tareas")
        register_mock.assert_called_once_with(Channel.WHATSAPP, "50688888888")
        schedule_mock.assert_called_once_with((Channel.WHATSAPP.value, "50688888888", 1))
        self.assertEqual(len(actions), 1)
        self.assertIn("curso teórico", actions[0].text)
        self.assertIn("MOTOCICLETA", actions[0].text)

    def test_transporte_exact_keyword_uses_transport_first_message(self):
        repo = MagicMock()
        repo.is_blocked.return_value = False

        with patch("src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo), patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch("src.application.conversation_orchestrator.register_keyword_context"), patch(
            "src.application.conversation_orchestrator.schedule_keyword_programmed_messages.apply_async"
        ), patch("src.application.conversation_orchestrator.KeywordRegistryRepository.register_if_missing"), patch(
            "src.application.conversation_orchestrator.cancel_scheduled_tasks"
        ):
            actions = ConversationOrchestrator().handle(self._message(" transporte "))

        repo.block_user.assert_called_once()
        self.assertEqual(len(actions), 1)
        self.assertIn("transporte público", actions[0].text)
        self.assertNotIn("MOTOCICLETA", actions[0].text)

    def test_keyword_with_extra_text_does_not_start_keyword_flow(self):
        repo = MagicMock()
        repo.is_blocked.return_value = False

        with patch("src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo), patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch("src.application.conversation_orchestrator.BufferService.add_message") as add_message, patch(
            "src.application.conversation_orchestrator.process_buffered_messages.apply_async"
        ), patch("src.application.conversation_orchestrator.register_keyword_context") as register_mock, patch(
            "src.application.conversation_orchestrator.KeywordRegistryRepository.register_if_missing"
        ) as registry_mock:
            actions = ConversationOrchestrator().handle(self._message("quiero tareas"))

        repo.block_user.assert_not_called()
        register_mock.assert_not_called()
        registry_mock.assert_not_called()
        add_message.assert_called_once()
        self.assertEqual(actions, [])

    def test_blocked_user_exact_keywords_start_keyword_flow(self):
        cases = [
            ("tareas", "Flujo keyword tareas", "curso teórico", None),
            (" transporte ", "Flujo keyword transporte", "transporte público", "MOTOCICLETA"),
        ]

        for text, reason, expected_text, unexpected_text in cases:
            with self.subTest(text=text):
                repo = MagicMock()
                repo.is_blocked.return_value = True

                with patch("src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo), patch(
                    "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
                ), patch("src.application.conversation_orchestrator.register_keyword_context") as register_mock, patch(
                    "src.application.conversation_orchestrator.schedule_keyword_programmed_messages.apply_async"
                ) as schedule_mock, patch(
                    "src.application.conversation_orchestrator.KeywordRegistryRepository.register_if_missing"
                ) as registry_mock, patch(
                    "src.application.conversation_orchestrator.cancel_scheduled_tasks"
                ) as cancel_mock, patch(
                    "src.application.conversation_orchestrator.consume_keyword_report"
                ) as consume_keyword_report_mock, patch(
                    "src.application.conversation_orchestrator.ReportRepository.create_report"
                ) as report_mock:
                    actions = ConversationOrchestrator().handle(self._message(text))

                keyword = text.strip().lower()
                cancel_mock.assert_called_once_with(Channel.WHATSAPP.value, "50688888888")
                repo.block_user.assert_called_once_with(
                    "50688888888",
                    reason=reason,
                    channel=Channel.WHATSAPP,
                )
                registry_mock.assert_called_once_with("50688888888", "Cliente", Channel.WHATSAPP, keyword)
                register_mock.assert_called_once_with(Channel.WHATSAPP, "50688888888")
                schedule_mock.assert_called_once_with(
                    (Channel.WHATSAPP.value, "50688888888", _PALABRAS[keyword]["id"])
                )
                consume_keyword_report_mock.assert_not_called()
                report_mock.assert_not_called()
                self.assertEqual(len(actions), 1)
                self.assertIn(expected_text, actions[0].text)
                if unexpected_text:
                    self.assertNotIn(unexpected_text, actions[0].text)

    def test_keyword_works_during_owner_intervention_pause(self):
        for keyword, expected_text in (
            ("tareas", "curso teórico"),
            ("transporte", "transporte público"),
        ):
            with self.subTest(keyword=keyword):
                repo = MagicMock()
                repo.is_blocked_for_reason.return_value = True

                with patch(
                    "src.application.conversation_orchestrator.PostgresUserRepo",
                    return_value=repo,
                ), patch(
                    "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
                ), patch(
                    "src.application.conversation_orchestrator.register_keyword_context"
                ), patch(
                    "src.application.conversation_orchestrator.schedule_keyword_programmed_messages.apply_async"
                ) as schedule_mock, patch(
                    "src.application.conversation_orchestrator.KeywordRegistryRepository.register_if_missing"
                ), patch("src.application.conversation_orchestrator.cancel_scheduled_tasks"):
                    actions = ConversationOrchestrator().handle(self._message(keyword))

                repo.is_blocked_for_reason.assert_not_called()
                repo.block_user.assert_called_once_with(
                    "50688888888",
                    reason=f"Flujo keyword {keyword}",
                    channel=Channel.WHATSAPP,
                )
                schedule_mock.assert_called_once()
                self.assertEqual(len(actions), 1)
                self.assertIn(expected_text, actions[0].text)

    def test_non_keyword_stays_silent_during_owner_intervention_pause(self):
        repo = MagicMock()
        repo.is_blocked_for_reason.return_value = True

        with patch("src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo), patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch("src.application.conversation_orchestrator.BufferService.add_message") as add_message:
            actions = ConversationOrchestrator().handle(self._message("Necesito ayuda"))

        repo.is_blocked_for_reason.assert_called_once_with(
            "50688888888",
            human_intervention.MOTIVO_PAUSA_IA,
            channel=Channel.WHATSAPP,
        )
        add_message.assert_not_called()
        self.assertEqual(actions, [])

    def test_permanent_block_still_suppresses_keyword(self):
        repo = MagicMock()

        with patch("src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo), patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch(
            "src.infrastructure.repositories.bloqueos_permanentes_repository.esta_bloqueado",
            return_value=True,
        ), patch(
            "src.application.conversation_orchestrator.schedule_keyword_programmed_messages.apply_async"
        ) as schedule_mock:
            actions = ConversationOrchestrator().handle(self._message("transporte"))

        repo.block_user.assert_not_called()
        schedule_mock.assert_not_called()
        self.assertEqual(actions, [])

    def test_new_keyword_cancels_previous_scheduled_keyword_reminders(self):
        repo = MagicMock()
        repo.is_blocked.return_value = True

        with patch("src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo), patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch("src.application.conversation_orchestrator.register_keyword_context"), patch(
            "src.application.conversation_orchestrator.schedule_keyword_programmed_messages.apply_async"
        ) as schedule_mock, patch(
            "src.application.conversation_orchestrator.KeywordRegistryRepository.register_if_missing"
        ), patch("src.application.conversation_orchestrator.cancel_scheduled_tasks") as cancel_mock:
            orchestrator = ConversationOrchestrator()
            tareas_actions = orchestrator.handle(self._message("tareas"))
            transporte_actions = orchestrator.handle(self._message("transporte"))

        self.assertEqual(cancel_mock.call_count, 2)
        cancel_mock.assert_called_with(Channel.WHATSAPP.value, "50688888888")
        self.assertEqual(schedule_mock.call_count, 2)
        self.assertIn("MOTOCICLETA", tareas_actions[0].text)
        self.assertIn("transporte público", transporte_actions[0].text)
        self.assertNotIn("MOTOCICLETA", transporte_actions[0].text)

    def test_blocked_keyword_user_generates_one_report_from_active_reminder(self):
        repo = MagicMock()
        repo.is_blocked.return_value = True

        with patch("src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo), patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.log_inbound"
        ), patch(
            "src.application.conversation_orchestrator.consume_keyword_report",
            side_effect=["Contestaron recordatorio 3 días despues de palabra clave", ""],
        ), patch("src.application.conversation_orchestrator.ReportRepository.create_report") as report_mock:
            orchestrator = ConversationOrchestrator()
            orchestrator.handle(self._message("Ya pude entrar"))
            orchestrator.handle(self._message("Otro mensaje"))

        report_mock.assert_called_once()
        self.assertEqual(
            report_mock.call_args.kwargs["problema"],
            "Contestaron recordatorio 3 días despues de palabra clave",
        )

    def test_d_command_deletes_keyword_registry_entry(self):
        repo = MagicMock()

        with patch("src.application.conversation_orchestrator.PostgresUserRepo", return_value=repo), patch(
            "src.application.conversation_orchestrator.KeywordRegistryRepository.delete"
        ) as delete_mock, patch(
            "src.application.conversation_orchestrator.ConversationLogRepository.delete_conversation"
        ), patch(
            "src.application.conversation_orchestrator.clear_user_runtime_context"
        ):
            actions = ConversationOrchestrator().handle(self._message("/d"))

        delete_mock.assert_called_once_with("50688888888", Channel.WHATSAPP)
        repo.unblock_user.assert_called_once_with("50688888888", channel=Channel.WHATSAPP)
        self.assertEqual(actions[0].text, "Historial y bloqueos limpiados.")


if __name__ == "__main__":
    unittest.main()
