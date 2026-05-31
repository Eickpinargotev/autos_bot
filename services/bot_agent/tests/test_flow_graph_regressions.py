import os
import unittest
from unittest.mock import MagicMock, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NOCODB_INVITACIONES_URL", "http://nocodb.test/invitaciones")
os.environ.setdefault("NOCODB_REPORTES_URL", "http://nocodb.test/reportes")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application.flow_graph import FlowGraphRunner
from src.application.rag_service import RagAnswer
from src.application.response_classifier import ResponseClassifier
from src.domain.entities import Channel, UserState
from src.infrastructure.repositories.conversation_state_repo import ConversationState


class FlowGraphRegressionTests(unittest.TestCase):
    def setUp(self):
        self.runner = FlowGraphRunner()

    def _repo_patches(self, stored):
        set_mock = MagicMock()
        return (
            patch("src.application.flow_graph.ConversationStateRepo.get", return_value=stored),
            patch("src.application.flow_graph.ConversationStateRepo.set", set_mock),
            set_mock,
        )

    def _report_block_patches(self):
        block_repo = MagicMock()
        return (
            patch("src.application.flow_graph.ReportRepository.create_report", return_value=(True, {})),
            patch("src.application.flow_graph.PostgresUserRepo", return_value=block_repo),
            patch("src.application.runtime_context.clear_user_runtime_context"),
            block_repo,
        )

    def test_initial_complaint_enters_q1_without_immediate_report(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch:
            result = self.runner.run(Channel.WHATSAPP, "50611111111", "Estoy muy molesto con el servicio", "Cliente")

        self.assertEqual(result.legacy_state, UserState.QUEJAS)
        self.assertTrue(result.replies)
        self.assertIn("detalla toda la situación", result.replies[0])
        report_mock.assert_not_called()
        block_repo.block_user.assert_not_called()
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "QUEJA")
        self.assertEqual(saved_state.node, "Q1")
        self.assertTrue(saved_state.pending_report)

    def test_initial_terrible_service_enters_q1_without_general(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch:
            result = self.runner.run(
                Channel.TELEGRAM,
                "1049838038",
                "Terrible el servicio, no puedo ingresar a la sección de tareas, no se que hacer.",
                "Erick",
            )

        self.assertEqual(result.legacy_state, UserState.QUEJAS)
        self.assertTrue(result.replies)
        self.assertIn("detalla toda la situación", result.replies[0])
        self.assertNotIn("Ya tiene el teórico ganado", "\n".join(result.replies))
        report_mock.assert_not_called()
        block_repo.block_user.assert_not_called()
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "QUEJA")
        self.assertEqual(saved_state.node, "Q1")

    def test_initial_win_typo_aprove_sends_w1_report_and_block(self):
        get_patch, set_patch, _ = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch:
            result = self.runner.run(Channel.TELEGRAM, "1049838038", "Gracias por la ayuda aprove el examen", "Erick")

        self.assertEqual(result.legacy_state, UserState.GENERAL)
        self.assertTrue(result.replies)
        self.assertIn("buena calificación", result.replies[0])
        self.assertIn("facebook.com", result.replies[0])
        report_mock.assert_called_once()
        block_repo.block_user.assert_called_once()
        self.assertIn("ganó la prueba", report_mock.call_args.kwargs["problema"])

    def test_initial_negative_win_phrase_stays_general(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch:
            result = self.runner.run(Channel.TELEGRAM, "1049838038", "No aprobé el examen", "Erick")

        self.assertEqual(result.legacy_state, UserState.GENERAL)
        self.assertTrue(result.replies)
        self.assertIn("Ya tiene el teórico ganado", "\n".join(result.replies))
        report_mock.assert_not_called()
        block_repo.block_user.assert_not_called()
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "GENERAL")
        self.assertEqual(saved_state.node, "G1")

    def test_initial_core_business_flows_still_route(self):
        cases = [
            ("ocupo dictamen", UserState.DICTAMEN, "DICTAMEN", "D1"),
            ("quiero clases de manejo", UserState.CLASES, "CLASES", "C1"),
            ("quiero alquilar carro", UserState.ALQUILER, "Alquiler", "A1"),
        ]
        for text, legacy_state, flow, node in cases:
            with self.subTest(text=text):
                get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
                report_patch, block_patch, clear_patch, _ = self._report_block_patches()
                keyword_registry_patch = patch(
                    "src.infrastructure.repositories.keyword_registry_repository.KeywordRegistryRepository.exists",
                    return_value=False,
                )
                with get_patch, set_patch, report_patch, block_patch, clear_patch, keyword_registry_patch:
                    result = self.runner.run(Channel.TELEGRAM, "1049838038", text, "Erick")

                self.assertEqual(result.legacy_state, legacy_state)
                saved_state = set_mock.call_args.args[2]
                self.assertEqual(saved_state.flow, flow)
                self.assertEqual(saved_state.node, node)

    def test_initial_dictamen_uses_d1_1_for_keyword_registered_user(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch(
            "src.infrastructure.repositories.keyword_registry_repository.KeywordRegistryRepository.exists",
            return_value=True,
        ) as registry_mock:
            result = self.runner.run(Channel.WHATSAPP, "50688888888", "ocupo dictamen", "Cliente")

        registry_mock.assert_called_once_with("50688888888", Channel.WHATSAPP)
        self.assertEqual(result.legacy_state, UserState.DICTAMEN)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "DICTAMEN")
        self.assertEqual(saved_state.node, "D1_1")

    def test_initial_llm_fallback_maps_quejas_to_queja(self):
        classifier = ResponseClassifier()
        completion = MagicMock()
        completion.choices = [MagicMock(message=MagicMock(content='{"categoria": "QUEJAS"}'))]

        with patch("src.application.response_classifier.settings.OPENAI_API_KEY", "test-key"), patch(
            "src.application.response_classifier.client.chat.completions.create",
            return_value=completion,
        ):
            self.assertEqual(classifier.classify_initial_flow("Me parece injusto todo esto"), "QUEJA")

    def test_reply_to_q1_generates_report_and_block(self):
        stored = ConversationState(
            flow="QUEJA",
            node="Q1",
            last_question="Le agradezco si me detalla toda la situación",
            pending_report="El cliente tiene una queja y contestó al mensaje envíado por el agente",
            user_name="Cliente",
        )
        get_patch, set_patch, _ = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch:
            result = self.runner.run(Channel.WHATSAPP, "50622222222", "Me dejaron esperando ayer", "Cliente")

        self.assertEqual(result.legacy_state, UserState.GENERAL)
        report_mock.assert_called_once()
        block_repo.block_user.assert_called_once()
        self.assertIn("El cliente tiene una queja", report_mock.call_args.kwargs["problema"])

    def test_g4_city_uses_publicidad_invitation_flow(self):
        stored = ConversationState(
            flow="GENERAL",
            node="G4",
            last_question="Donde vives???",
            user_name="Cliente",
        )
        get_patch, set_patch, _ = self._repo_patches(stored)

        with get_patch, set_patch, patch(
            "src.application.publicidad_service.PublicidadService.handle_invitation_by_city",
            return_value=True,
        ) as invitation_mock:
            result = self.runner.run(Channel.WHATSAPP, "50633333333", "Atenas", "Cliente")

        invitation_mock.assert_called_once()
        self.assertEqual(invitation_mock.call_args.args[:3], ("50633333333", "Atenas", "Cliente"))
        self.assertEqual(result.legacy_state, UserState.PUBLICIDAD)
        self.assertEqual(result.replies, [])

    def test_g4_unknown_city_reasks_without_report_or_block(self):
        stored = ConversationState(
            flow="GENERAL",
            node="G4",
            last_question="Donde vives???",
            user_name="Cliente",
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch(
            "src.application.publicidad_service.PublicidadService.handle_invitation_by_city",
            return_value=False,
        ):
            result = self.runner.run(Channel.WHATSAPP, "50644444444", "Ciudad inventada", "Cliente")

        self.assertEqual(result.legacy_state, UserState.GENERAL)
        self.assertEqual(result.replies, ["Para continuar, retomemos la última pregunta:\n\nDonde vives???"])
        report_mock.assert_not_called()
        block_repo.block_user.assert_not_called()
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "GENERAL")
        self.assertEqual(saved_state.node, "G4")

    def test_complaint_inside_existing_flow_sends_handoff_report_and_block(self):
        stored = ConversationState(
            flow="GENERAL",
            node="G35",
            last_question="Donde es su prueba de manejo???",
            user_name="Cliente",
        )
        get_patch, set_patch, _ = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch:
            result = self.runner.run(Channel.WHATSAPP, "50655555555", "Esto es una estafa, estoy molesto", "Cliente")

        self.assertEqual(result.replies, [FlowGraphRunner.COMPLAINT_HANDOFF_MESSAGE])
        report_mock.assert_called_once()
        block_repo.block_user.assert_called_once()
        self.assertIn("GENERAL.G35", report_mock.call_args.kwargs["problema"])

    def test_human_help_report_includes_recent_angry_history(self):
        stored = ConversationState(
            flow="GENERAL",
            node="G35",
            last_question="Donde es su prueba de manejo???",
            user_name="Cliente",
            conversation_history=[
                {
                    "flow": "GENERAL",
                    "node": "G35",
                    "type": "retake",
                    "user": "Estoy molesto porque nadie responde",
                    "bot": ["Para continuar..."],
                }
            ],
        )
        get_patch, set_patch, _ = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch:
            result = self.runner.run(Channel.WHATSAPP, "50666666666", "Pónganme en contacto con un asesor", "Cliente")

        self.assertEqual(result.replies, [FlowGraphRunner.COMPLAINT_HANDOFF_MESSAGE])
        report_mock.assert_called_once()
        block_repo.block_user.assert_called_once()
        problema = report_mock.call_args.kwargs["problema"]
        self.assertIn("historial reciente", problema.lower())
        self.assertIn("nadie responde", problema)

    def test_indirect_question_does_not_advance_g1_as_positive(self):
        stored = ConversationState(
            flow="GENERAL",
            node="G1",
            last_question="Ya tiene el teórico ganado???",
            user_name="Erick",
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)

        with get_patch, set_patch, patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Puede traer su casco o consultar disponibilidad con el asesor."),
        ):
            result = self.runner.run(
                Channel.TELEGRAM,
                "1049838038",
                "Me gustaría, saber si tengo que llevar casco o ustedes lo ofrecen",
                "Erick",
            )

        self.assertEqual(
            result.replies,
            [
                "Puede traer su casco o consultar disponibilidad con el asesor.",
                "Para continuar, retomemos la última pregunta:\n\nYa tiene el teórico ganado???",
            ],
        )
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "GENERAL")
        self.assertEqual(saved_state.node, "G1")

    def test_city_answer_with_side_question_answers_then_continues_flow(self):
        stored = ConversationState(
            flow="GENERAL",
            node="G35",
            last_question="Donde es su prueba de manejo???",
            user_name="Erick",
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)

        with get_patch, set_patch, patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Si pierde el examen teórico, debe volver a pagar el derecho correspondiente."),
        ) as rag_mock:
            result = self.runner.run(
                Channel.TELEGRAM,
                "1049838038",
                "Es en liberia, pero tenguna consulta, y si puerdo el examen teorico tengo que vover a pagar ?",
                "Erick",
            )

        self.assertEqual(
            result.replies,
            [
                "Si pierde el examen teórico, debe volver a pagar el derecho correspondiente.",
                "La licencia que usted va a sacar es moto o carro???",
            ],
        )
        self.assertIn("consulta", rag_mock.call_args.args[0])
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "GENERAL")
        self.assertEqual(saved_state.node, "G11")
        self.assertEqual(saved_state.last_question, "La licencia que usted va a sacar es moto o carro???")

    def test_retake_cleans_reminder_wrapper_from_last_question(self):
        stored = ConversationState(
            flow="GENERAL",
            node="G1",
            last_question="📌 Hola!!!\n\nNo recibí tu respuesta\n\nYa tiene el teórico ganado???",
            user_name="Erick",
        )
        get_patch, set_patch, _ = self._repo_patches(stored)

        with get_patch, set_patch, patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Debe pagar nuevamente el monto indicado para activar el curso."),
        ):
            result = self.runner.run(
                Channel.TELEGRAM,
                "1049838038",
                "Tengo una pregunta, me ayuda que pasa si pierdo el examen teórico, tengo que pagar otra vez ?",
                "Erick",
            )

        self.assertEqual(
            result.replies[-1],
            "Para continuar, retomemos la última pregunta:\n\nYa tiene el teórico ganado???",
        )

    def test_reply_classifier_handles_question_and_mixed_cases(self):
        classifier = ResponseClassifier()

        for text in (
            "Me gustaría saber si tengo que llevar casco",
            "Y si pierdo el examen teórico tengo que volver a pagar",
            "Qué pasa si pierdo el examen teórico",
        ):
            with self.subTest(text=text):
                result = classifier.classify_reply(text, "GENERAL", "G1", "Ya tiene el teórico ganado???")
                self.assertEqual(result.intent, "question")

        self.assertEqual(classifier.classify_reply("Si", "GENERAL", "G1", "").intent, "positive")
        self.assertEqual(classifier.classify_reply("sí claro", "GENERAL", "G1", "").intent, "positive")

        city = classifier.classify_reply("Es en Liberia", "GENERAL", "G35", "Donde es su prueba de manejo???")
        self.assertEqual(city.intent, "city")
        self.assertEqual(city.value, "liberia")

        mixed = classifier.classify_reply(
            "Es en Liberia, pero tengo una consulta, y si pierdo el examen teórico tengo que volver a pagar?",
            "GENERAL",
            "G35",
            "Donde es su prueba de manejo???",
        )
        self.assertEqual(mixed.intent, "city")
        self.assertEqual(mixed.value, "liberia")
        self.assertTrue(mixed.has_off_flow_question)
        self.assertIn("consulta", mixed.off_flow_question)

    def test_flow_reminder_extracts_clean_question(self):
        from src.infrastructure.tasks.celery_app import _extract_last_question

        self.assertEqual(
            _extract_last_question(["📌 Hola!!!\n\nNo recibí tu respuesta\n\nYa tiene el teórico ganado???"]),
            "Ya tiene el teórico ganado???",
        )


if __name__ == "__main__":
    unittest.main()
