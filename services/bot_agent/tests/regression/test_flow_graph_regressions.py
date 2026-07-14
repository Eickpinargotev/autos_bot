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

from src.application.flow_graph import FlowGraphRunner
from src.application.rag_service import RagAnswer
from src.application.reception_agent import ReceptionAgent, ReceptionDecision
from src.application.response_classifier import ReplyClassification, ResponseClassifier
from src.core.config import settings
from src.domain.entities import Channel, UserState
from src.infrastructure.repositories.conversation_state_repo import ConversationState


# Tests de INTEGRACIÓN que llaman al LLM real (reception/classifier sin mock).
# Se saltan si no hay OPENAI_API_KEY, igual que el juez LLM (SemanticJudge.enabled()).
# El flujo normal (docker compose ... run) inyecta la key desde .env y sí los ejecuta.
requires_llm = unittest.skipUnless(
    bool(settings.OPENAI_API_KEY),
    "requiere OPENAI_API_KEY (test de integración con LLM real)",
)


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

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(action="start_flow", flow="QUEJA", confidence=0.9),
        ):
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

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(action="start_flow", flow="QUEJA", confidence=0.9),
        ):
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

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(action="start_flow", flow="WIN", confidence=0.9),
        ):
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

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(action="start_flow", flow="GENERAL", confidence=0.9),
        ):
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
                with get_patch, set_patch, report_patch, block_patch, clear_patch, keyword_registry_patch, patch.object(
                    self.runner.reception,
                    "decide",
                    return_value=ReceptionDecision(action="start_flow", flow=flow, confidence=0.9),
                ):
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
        ) as registry_mock, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(action="start_flow", flow="DICTAMEN", confidence=0.9),
        ):
            result = self.runner.run(Channel.WHATSAPP, "50688888888", "ocupo dictamen", "Cliente")

        registry_mock.assert_called_once_with("50688888888", Channel.WHATSAPP)
        self.assertEqual(result.legacy_state, UserState.DICTAMEN)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "DICTAMEN")
        self.assertEqual(saved_state.node, "D1_1")

    def test_initial_ambiguous_helmet_question_clarifies_without_entering_flow(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="answer_and_clarify",
                has_question=True,
                question="ustedes me prestan el casco o tengo que llevar uno?",
                answer_source="rag",
                clarifying_question="¿Busca ayuda con su licencia, dictamen, clases o alquiler, o es para otro trámite o servicio?",
                confidence=0.6,
            ),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Para moto normalmente debe usar casco."),
        ) as rag_mock:
            result = self.runner.run(
                Channel.WHATSAPP,
                "50677777777",
                "Hola, ustedes me prestan el casco o tengo que llevar uno?",
                "Cliente",
            )

        self.assertEqual(result.replies[0], "Para moto normalmente debe usar casco.")
        self.assertEqual(len(result.replies), 2)
        self.assertIn("trámite", result.replies[-1])
        self.assertIn("servicio", result.replies[-1])
        rag_mock.assert_called_once()
        report_mock.assert_not_called()
        block_repo.block_user.assert_not_called()
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "INTAKE")
        self.assertEqual(saved_state.node, "I1")
        self.assertEqual(saved_state.last_question, result.replies[-1])

    @requires_llm
    def test_intake_followup_can_enter_alquiler_flow(self):
        stored = ConversationState(
            flow="INTAKE",
            node="I1",
            last_question="¿Lo ocupa para una prueba de manejo, para clases, o para alquiler?",
            user_name="Cliente",
            conversation_history=[
                {
                    "flow": "INTAKE",
                    "node": "I1",
                    "type": "intake_clarify",
                    "user": "Ustedes me prestan el casco?",
                    "bot": ["¿Lo ocupa para una prueba de manejo, para clases, o para alquiler?"],
                }
            ],
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch:
            result = self.runner.run(Channel.WHATSAPP, "50677777777", "Es para alquilar moto", "Cliente")

        self.assertEqual(result.legacy_state, UserState.ALQUILER)
        self.assertTrue(result.replies)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "Alquiler")
        self.assertEqual(saved_state.node, "A1")

    def test_intake_confirmation_with_extra_question_answers_then_enters_flow(self):
        stored = ConversationState(
            flow="INTAKE",
            node="I1",
            last_question="¿Desea conocer más sobre nuestro proceso para alquilar con nosotros?",
            awaiting_reply=True,
            user_name="Cliente",
            conversation_history=[
                {
                    "flow": "INTAKE",
                    "node": "I1",
                    "type": "intake_clarify",
                    "user": "Ustedes me prestan el casco?",
                    "bot": ["¿Desea conocer más sobre nuestro proceso para alquilar con nosotros?"],
                }
            ],
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="answer_and_start_flow",
                flow="Alquiler",
                has_question=True,
                question="ustedes sí ayudan con el casco o no ayudan?",
                answer_source="rag",
                confidence=0.9,
            ),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Sí, podemos facilitar casco según disponibilidad."),
        ) as rag_mock:
            result = self.runner.run(
                Channel.WHATSAPP,
                "50677777777",
                "Sí, deseo conocer más, pero ustedes sí ayudan con el casco o no ayudan?",
                "Cliente",
            )

        rag_mock.assert_called_once()
        self.assertEqual(result.replies[0], "Sí, podemos facilitar casco según disponibilidad.")
        self.assertIn("Ya tiene cita agendada", result.replies[1])
        self.assertEqual(result.legacy_state, UserState.ALQUILER)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "Alquiler")
        self.assertEqual(saved_state.node, "A1")

    def test_intake_followup_close_decision_closes_without_entering_general_flow(self):
        stored = ConversationState(
            flow="INTAKE",
            node="I1",
            last_question="¿Te gustaría más información sobre cómo inscribirte en el examen teórico?",
            awaiting_reply=True,
            user_name="Cliente",
            conversation_history=[
                {
                    "flow": "INTAKE",
                    "node": "I1",
                    "type": "intake_clarify",
                    "user": "Quiero cursar el examen teórico, pero tengo una duda.",
                    "bot": [
                        "Respuesta a la duda del cliente.",
                        "¿Te gustaría más información sobre cómo inscribirte en el examen teórico?",
                    ],
                }
            ],
        )
        set_mock = MagicMock()
        clear_mock = MagicMock()

        with patch("src.application.flow_graph.ConversationStateRepo.get", return_value=stored), patch(
            "src.application.flow_graph.ConversationStateRepo.set",
            set_mock,
        ), patch("src.application.flow_graph.ConversationStateRepo.clear", clear_mock), patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(action="close"),
        ):
            result = self.runner.run(
                Channel.WHATSAPP,
                "50677777777",
                "No, ya aprobé el teórico, solo preguntaba, gracias.",
                "Cliente",
            )

        self.assertEqual(result.replies, [FlowGraphRunner.INTAKE_CLOSE_MESSAGE])
        self.assertEqual(result.legacy_state, UserState.GENERAL)
        set_mock.assert_not_called()
        clear_mock.assert_called_once_with(Channel.WHATSAPP.value, "50677777777")

    def test_intake_followup_confirmation_with_thanks_is_not_forced_closed_by_code(self):
        stored = ConversationState(
            flow="INTAKE",
            node="I1",
            last_question="¿Te gustaría más información sobre cómo avanzar con el examen teórico?",
            awaiting_reply=True,
            user_name="Cliente",
            conversation_history=[
                {
                    "flow": "INTAKE",
                    "node": "I1",
                    "type": "intake_clarify",
                    "user": "Quiero cursar el examen teórico, pero tengo una duda.",
                    "bot": [
                        "Respuesta a la duda del cliente.",
                        "¿Te gustaría más información sobre cómo avanzar con el examen teórico?",
                    ],
                }
            ],
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(action="start_flow", flow="GENERAL"),
        ):
            result = self.runner.run(Channel.WHATSAPP, "50677777777", "Dale, de una, gracias", "Cliente")

        self.assertEqual(result.legacy_state, UserState.GENERAL)
        self.assertTrue(result.replies)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "GENERAL")
        self.assertEqual(saved_state.node, "G1")

    @requires_llm
    def test_intake_followup_obtener_licencia_enters_general_flow(self):
        stored = ConversationState(
            flow="INTAKE",
            node="I1",
            last_question="Claro, ¿busca sacar la licencia, alquilar el vehículo para la prueba o recibir clases?",
            user_name="Cliente",
            conversation_history=[
                {
                    "flow": "INTAKE",
                    "node": "I1",
                    "type": "intake_clarify",
                    "user": "Ustedes me prestan el casco?",
                    "bot": ["Claro, ¿busca sacar la licencia, alquilar el vehículo para la prueba o recibir clases?"],
                }
            ],
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch:
            result = self.runner.run(Channel.WHATSAPP, "50677777777", "Quiero obtener la licencia", "Cliente")

        self.assertEqual(result.legacy_state, UserState.GENERAL)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "GENERAL")
        self.assertEqual(saved_state.node, "G1")

    @requires_llm
    def test_intake_bare_word_selecting_offered_option_enters_flow(self):
        # Cuando el bot ya ofreció opciones nombradas ("teórico, clases o
        # alquiler") y el cliente responde con una sola palabra que nombra una
        # de esas opciones (sin verbo), es selección explícita: debe entrar al
        # flujo, no repetir la aclaración. Caso real (capturas 2026-07-14): con
        # DOS aclaraciones previas el anti-bucle está al límite; si el modelo
        # volviera a aclarar, escalaría a handoff y bloquearía al cliente.
        stored = ConversationState(
            flow="INTAKE",
            node="I1",
            last_question="Hola, ¿desea información sobre examen teórico, clases de manejo o alquiler de vehículos?",
            user_name="Cliente",
            conversation_history=[
                {
                    "flow": "INTAKE",
                    "node": "I1",
                    "type": "intake_clarify",
                    "user": "Hola",
                    "bot": [
                        "Hola, ¿cómo está? ¿Le gustaría información sobre licencias, clases de manejo o alquiler de vehículos?"
                    ],
                },
                {
                    "flow": "INTAKE",
                    "node": "I1",
                    "type": "intake_clarify",
                    "user": "Sobre el teórico",
                    "bot": [
                        "Hola, ¿desea información sobre examen teórico, clases de manejo o alquiler de vehículos?"
                    ],
                },
            ],
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch:
            result = self.runner.run(Channel.WHATSAPP, "50677777777", "Teorico", "Cliente")

        self.assertNotEqual(result.replies, [FlowGraphRunner.COMPLAINT_HANDOFF_MESSAGE])
        self.assertEqual(result.legacy_state, UserState.GENERAL)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "GENERAL")
        self.assertEqual(saved_state.node, "G1")
        report_mock.assert_not_called()

    @requires_llm
    def test_intake_positional_menu_selection_enters_selected_flow(self):
        # Variante natural de la selección de menú: el cliente elige por
        # posición ("la segunda opción") en vez de nombrar el servicio. Debe
        # entrar al flujo elegido, no volver a aclarar.
        stored = ConversationState(
            flow="INTAKE",
            node="I1",
            last_question="¿Le gustaría información sobre licencias, clases de manejo o alquiler de vehículos?",
            user_name="Cliente",
            conversation_history=[
                {
                    "flow": "INTAKE",
                    "node": "I1",
                    "type": "intake_clarify",
                    "user": "Hola",
                    "bot": [
                        "Hola, ¿cómo está? ¿Le gustaría información sobre licencias, clases de manejo o alquiler de vehículos?"
                    ],
                }
            ],
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch:
            result = self.runner.run(Channel.WHATSAPP, "50677777777", "La segunda opción", "Cliente")

        self.assertNotEqual(result.replies, [FlowGraphRunner.COMPLAINT_HANDOFF_MESSAGE])
        report_mock.assert_not_called()
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "CLASES")
        self.assertEqual(saved_state.node, "C1")

    @requires_llm
    def test_initial_practico_cita_request_enters_general_flow(self):
        # Caso real (capturas 2026-07-14): saludo + contexto + intención explícita
        # de sacar la cita de la prueba práctica b1, con un pedido genérico de
        # información ("dígame qué información ocupa"). Debe entrar a GENERAL:
        # el flujo ya pregunta por su cuenta lo que necesita. Se fuerza RAG miss
        # para verificar que la intención explícita no se pierde ni escala.
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(False),
        ):
            result = self.runner.run(
                Channel.TELEGRAM,
                "1049838038",
                "Hola buenos días como estas profe, ya ahora si tengo 18años, vengo para que me ayude "
                "a sacar la cita del practico b1, dígame que información ocupa y yo se la facilitó",
                "Erick",
            )

        self.assertNotEqual(result.replies, [FlowGraphRunner.COMPLAINT_HANDOFF_MESSAGE])
        report_mock.assert_not_called()
        block_repo.block_user.assert_not_called()
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "GENERAL")
        self.assertEqual(saved_state.node, "G1")

    @requires_llm
    def test_initial_generic_help_with_context_clarifies_without_flow(self):
        # I-CTX: "me ayudan?" es un pedido genérico de ayuda + contexto (tiene moto,
        # no licencia). Es ambiguo (licencia, clases o alquiler) -> se aclara con
        # opciones; no se asume un flujo. (Antes se forzaba GENERAL.)
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(False),
        ):
            result = self.runner.run(
                Channel.TELEGRAM,
                "1049838038",
                "Hola, tengo una moto, pero no tengo licencia, me ayudan ?",
                "Erick",
            )

        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "INTAKE")
        self.assertEqual(saved_state.node, "I1")

    @requires_llm
    def test_initial_prueba_de_manejo_context_clarifies_without_assuming_flow(self):
        # I-CTX: "tengo prueba de manejo" es contexto (no nombra un servicio
        # concreto: podría ser alquiler, clases o el proceso de licencia). No se
        # asume un flujo; se aclara con opciones. (Antes se forzaba GENERAL.)
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch:
            result = self.runner.run(Channel.TELEGRAM, "1049838038", "Mi prueba de manejo es en Liberia", "Erick")

        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "INTAKE")
        self.assertEqual(saved_state.node, "I1")

    @requires_llm
    def test_initial_explicit_alquiler_with_prueba_de_manejo_enters_alquiler(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch:
            result = self.runner.run(
                Channel.TELEGRAM,
                "1049838038",
                "Quiero alquilar una moto para mi prueba de manejo",
                "Erick",
            )

        self.assertEqual(result.legacy_state, UserState.ALQUILER)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "Alquiler")
        self.assertEqual(saved_state.node, "A1")

    def test_initial_deposit_question_clarifies_without_payment_instructions(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="clarify",
                clarifying_question="¿El pago es para qué servicio: licencia, dictamen, clases o alquiler?",
                confidence=0.4,
            ),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "SINPE AL 60023618"),
        ) as rag_mock:
            result = self.runner.run(Channel.WHATSAPP, "50612121212", "A qué dirección debo hacer el depósito?", "Cliente")

        self.assertEqual(len(result.replies), 1)
        self.assertIn("pago", result.replies[0])
        self.assertIn("servicio", result.replies[0])
        rag_mock.assert_not_called()
        report_mock.assert_not_called()
        block_repo.block_user.assert_not_called()
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "INTAKE")
        self.assertEqual(saved_state.node, "I1")

    def test_initial_paid_deposit_is_handed_off(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="handoff",
                handoff_reason="Recepción requiere revisión manual: pago ya realizado, seguimiento de trámite.",
                confidence=0.9,
            ),
        ):
            result = self.runner.run(
                Channel.WHATSAPP,
                "50613131313",
                "Hola Enrique, hace 6 meses hice el curso, hoy te deposité el dinero, puedes revisar?",
                "Cliente",
            )

        self.assertEqual(result.replies, [FlowGraphRunner.COMPLAINT_HANDOFF_MESSAGE])
        report_mock.assert_called_once()
        block_repo.block_user.assert_called_once()
        self.assertIn("revisión manual", report_mock.call_args.kwargs["problema"])
        set_mock.assert_not_called()

    def test_intake_clarify_loop_escalates_to_human(self):
        # Tras dos aclaraciones seguidas sin que el cliente concrete un servicio,
        # el intake no debe repetir la misma pregunta: deriva a un humano.
        prior_clarifies = [
            {"flow": "INTAKE", "node": "I1", "type": "intake_clarify",
             "user": "tengo una prueba de manejo en una semana", "bot": ["¿Con qué le ayudo?"]},
            {"flow": "INTAKE", "node": "I1", "type": "intake_clarify",
             "user": "como me pueden ayudar", "bot": ["¿Con qué le ayudo?"]},
        ]
        stored = ConversationState(flow="INTAKE", node="I1", conversation_history=prior_clarifies)
        get_patch, set_patch, set_mock = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="clarify",
                clarifying_question="¿Con qué le ayudo: licencia, clases o alquiler?",
                confidence=0.5,
            ),
        ):
            result = self.runner.run(
                Channel.WHATSAPP,
                "50614141414",
                "Claro, tengo una prueba de manejo, como me pueden ayudar?",
                "Cliente",
            )

        self.assertEqual(result.replies, [FlowGraphRunner.COMPLAINT_HANDOFF_MESSAGE])
        report_mock.assert_called_once()
        self.assertIn("varias aclaraciones", report_mock.call_args.kwargs["problema"])

    def test_intake_single_clarify_does_not_escalate(self):
        # Una sola aclaración previa no debe escalar: el cliente todavía puede concretar.
        prior = [
            {"flow": "INTAKE", "node": "I1", "type": "intake_clarify",
             "user": "hola", "bot": ["¿Con qué le ayudo?"]},
        ]
        stored = ConversationState(flow="INTAKE", node="I1", conversation_history=prior)
        get_patch, set_patch, set_mock = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="clarify",
                clarifying_question="¿Con qué le ayudo: licencia, clases o alquiler?",
                confidence=0.5,
            ),
        ):
            result = self.runner.run(
                Channel.WHATSAPP, "50614141414", "tengo una prueba de manejo", "Cliente",
            )

        self.assertIn("licencia, clases o alquiler", result.replies[0])
        report_mock.assert_not_called()

    def test_intake_real_question_after_two_clarifies_answers_not_escalates(self):
        # Tras 2 aclaraciones, si el cliente por fin trae una pregunta REAL
        # (answer_and_clarify), se responde; NO se escala a humano. answer_and_clarify
        # es progreso, no un bucle de aclaración.
        prior_clarifies = [
            {"flow": "INTAKE", "node": "I1", "type": "intake_clarify",
             "user": "buenas", "bot": ["¿Con qué le ayudo?"]},
            {"flow": "INTAKE", "node": "I1", "type": "intake_clarify",
             "user": "mañana tengo una prueba de manejo", "bot": ["¿Con qué le ayudo?"]},
        ]
        stored = ConversationState(flow="INTAKE", node="I1", conversation_history=prior_clarifies)
        get_patch, set_patch, set_mock = self._repo_patches(stored)
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="clarify",
                has_question=True,
                answer_source="rag",
                question="si alquilo una moto, ¿dan el casco?",
                clarifying_question="¿Desea alquilar moto o carro?",
                confidence=0.7,
            ),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Sí, le facilitamos casco si no trae el suyo."),
        ):
            result = self.runner.run(
                Channel.WHATSAPP,
                "50615151515",
                "Deseo alquilar, pero ¿si alquilo una moto dan el casco?",
                "Cliente",
            )

        self.assertNotEqual(result.replies, [FlowGraphRunner.COMPLAINT_HANDOFF_MESSAGE])
        self.assertEqual(result.replies[0], "Sí, le facilitamos casco si no trae el suyo.")
        report_mock.assert_not_called()

    def test_prompt_rules_operational_answer_is_rechecked_with_rag_instead_of_prompt_text(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="answer_and_start_flow",
                flow="Alquiler",
                has_question=True,
                question="ustedes ayudan con casco",
                answer_source="prompt_rules",
                answer="Sí, le ayudamos con esa parte.",
                confidence=0.9,
            ),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Puede traer su casco o consultar disponibilidad con el asesor."),
        ) as rag_mock:
            result = self.runner.run(Channel.TELEGRAM, "1049838038", "Quiero alquilar y tengo una duda", "Erick")

        rag_mock.assert_called_once()
        self.assertEqual(result.replies[0], "Puede traer su casco o consultar disponibilidad con el asesor.")
        self.assertEqual(result.legacy_state, UserState.GENERAL)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "INTAKE")
        self.assertEqual(saved_state.node, "I1")

    def test_reception_rag_question_without_answer_clarifies_without_blocking(self):
        # Una duda sin respaldo en RAG y SIN flujo concreto NO deriva ni bloquea:
        # registra la pregunta para el equipo y aclara, igual que F-5 en flujo.
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="answer_and_clarify",
                flow="",
                has_question=True,
                question="duda que no está en prompt",
                answer_source="rag",
                confidence=0.9,
            ),
        ), patch.object(self.runner.rag, "answer_question", return_value=RagAnswer(False)), patch(
            "src.application.flow_graph.UnansweredQuestionRepository.create"
        ) as unanswered_mock:
            result = self.runner.run(Channel.TELEGRAM, "1049838038", "Tengo una duda rara sobre mi caso", "Erick")

        unanswered_mock.assert_called_once_with("duda que no está en prompt")
        expected_clarify = self.runner.reception.clarifying_question_for("Tengo una duda rara sobre mi caso")
        self.assertEqual(result.replies, [expected_clarify])
        report_mock.assert_not_called()
        block_repo.block_user.assert_not_called()
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "INTAKE")

    def test_reception_rag_miss_with_explicit_flow_enters_flow_with_fallback(self):
        # Intención explícita ("quiero alquilar") + duda sin respaldo en RAG: la
        # duda no cancela la intención. Se antepone el fallback (asesor verá la
        # duda) y se entra igual al flujo, sin reporte ni bloqueo.
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, block_repo = self._report_block_patches()

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="start_flow",
                flow="Alquiler",
                has_question=True,
                question="duda que no está en prompt",
                answer_source="rag",
                confidence=0.9,
            ),
        ), patch.object(self.runner.rag, "answer_question", return_value=RagAnswer(False)), patch(
            "src.application.flow_graph.UnansweredQuestionRepository.create"
        ) as unanswered_mock:
            result = self.runner.run(Channel.TELEGRAM, "1049838038", "Quiero alquilar y tengo una duda rara", "Erick")

        unanswered_mock.assert_called_once_with("duda que no está en prompt")
        self.assertEqual(result.replies[0], FlowGraphRunner.RAG_FALLBACK_MESSAGE)
        self.assertEqual(result.legacy_state, UserState.ALQUILER)
        report_mock.assert_not_called()
        block_repo.block_user.assert_not_called()
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "Alquiler")
        self.assertEqual(saved_state.node, "A1")

    def test_side_question_without_advance_is_answered_not_discarded(self):
        # L1: el mensaje no avanza el paso (license en G1 no enruta) pero trae
        # una duda lateral real. La duda debe responderse y reanclar, no
        # descartarse retomando encima de una duda abierta.
        stored = ConversationState(
            flow="GENERAL",
            node="G1",
            last_question="Ya tiene el teórico ganado???",
            user_name="Erick",
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)

        with get_patch, set_patch, patch.object(
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification("license", "moto", True, "¿puedo pagar con sinpe?"),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Sí, aceptamos sinpe móvil."),
        ) as rag_mock:
            result = self.runner.run(Channel.WHATSAPP, "50677777777", "es para moto, ¿puedo pagar con sinpe?", "Erick")

        rag_mock.assert_called_once()
        self.assertEqual(rag_mock.call_args.args[0], "¿puedo pagar con sinpe?")
        self.assertEqual(result.replies[0], "Sí, aceptamos sinpe móvil.")
        self.assertEqual(len(result.replies), 2)  # respuesta + retomar la pregunta pendiente
        set_mock.assert_called_once()

    def test_side_question_without_advance_and_rag_miss_offers_advisor_without_retake(self):
        # L1 + F-5: duda lateral sin avance y sin respaldo → fallback de asesor,
        # registrar la pregunta y NO reanclar (la duda sigue abierta).
        stored = ConversationState(
            flow="GENERAL",
            node="G1",
            last_question="Ya tiene el teórico ganado???",
            user_name="Erick",
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)

        with get_patch, set_patch, patch.object(
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification("license", "moto", True, "¿duda sin respaldo?"),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(False),
        ), patch(
            "src.application.flow_graph.UnansweredQuestionRepository.create"
        ) as unanswered_mock:
            result = self.runner.run(Channel.WHATSAPP, "50677777777", "es para moto, ¿duda sin respaldo?", "Erick")

        unanswered_mock.assert_called_once_with("¿duda sin respaldo?")
        self.assertEqual(result.replies, [FlowGraphRunner.RAG_FALLBACK_MESSAGE])

    def test_decline_with_side_question_answers_before_closing(self):
        # L3: "no gracias, solo dígame X" responde la duda y luego cierra, en
        # vez de despedirse descartando lo único que el cliente pidió.
        stored = ConversationState(
            flow="GENERAL",
            node="G3",
            last_question="Ya tiene cita agendada para la prueba de manejo???",
            user_name="Erick",
        )
        get_patch, set_patch, _ = self._repo_patches(stored)
        clear_mock = MagicMock()

        with get_patch, set_patch, patch(
            "src.application.flow_graph.ConversationStateRepo.clear", clear_mock
        ), patch.object(
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification("decline", "", True, "¿cuánto vale el dictamen?"),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "El dictamen vale 22600 colones."),
        ):
            result = self.runner.run(
                Channel.WHATSAPP,
                "50677777777",
                "No gracias, ya no ocupo. Solo dígame cuánto vale el dictamen",
                "Erick",
            )

        self.assertEqual(
            result.replies,
            ["El dictamen vale 22600 colones.", FlowGraphRunner.DECLINE_CLOSE_MESSAGE],
        )
        clear_mock.assert_called_once_with(Channel.WHATSAPP.value, "50677777777")

    def test_intake_close_with_answered_question_replies_before_goodbye(self):
        # L3 en intake: un cierre con duda respondida responde primero y se
        # despide después; y con RAG miss avisa con el fallback, sin retener al
        # cliente con una aclaración que ya rechazó.
        for rag_answer, expected_first in [
            (RagAnswer(True, "El teórico se agenda en línea."), "El teórico se agenda en línea."),
            (RagAnswer(False), FlowGraphRunner.RAG_FALLBACK_MESSAGE),
        ]:
            with self.subTest(rag_hit=rag_answer.has_answer):
                get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
                clear_mock = MagicMock()

                with get_patch, set_patch, patch(
                    "src.application.flow_graph.ConversationStateRepo.clear", clear_mock
                ), patch.object(
                    self.runner.reception,
                    "decide",
                    return_value=ReceptionDecision(
                        action="close",
                        has_question=True,
                        question="¿cómo se agenda el teórico?",
                        answer_source="rag",
                    ),
                ), patch.object(
                    self.runner.rag, "answer_question", return_value=rag_answer
                ), patch(
                    "src.application.flow_graph.UnansweredQuestionRepository.create"
                ):
                    result = self.runner.run(
                        Channel.WHATSAPP,
                        "50677777777",
                        "Ya no ocupo nada más, solo dígame cómo se agenda el teórico",
                        "Cliente",
                    )

                self.assertEqual(
                    result.replies,
                    [expected_first, FlowGraphRunner.INTAKE_CLOSE_MESSAGE],
                )
                clear_mock.assert_called_once()

    def test_initial_question_with_clear_alquiler_enters_flow(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Tenemos alquiler según disponibilidad."),
        ) as rag_mock, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(action="start_flow", flow="Alquiler", confidence=0.9),
        ):
            result = self.runner.run(Channel.WHATSAPP, "50614141414", "Tienen alquiler de carro?", "Cliente")

        rag_mock.assert_not_called()
        self.assertEqual(result.legacy_state, UserState.ALQUILER)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "Alquiler")
        self.assertEqual(saved_state.node, "A1")

    def test_clear_alquiler_information_request_enters_flow_without_rag(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Tenemos alquiler según disponibilidad."),
        ) as rag_mock, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(action="start_flow", flow="Alquiler", confidence=0.9),
        ):
            result = self.runner.run(
                Channel.WHATSAPP,
                "50614141414",
                "Necesito información sobre el proceso para alquilar una moto",
                "Cliente",
            )

        rag_mock.assert_not_called()
        self.assertEqual(result.legacy_state, UserState.ALQUILER)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "Alquiler")
        self.assertEqual(saved_state.node, "A1")

    @requires_llm
    def test_rag_answer_is_logged_as_tool_event(self):
        get_patch, set_patch, _ = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Tenemos alquiler según disponibilidad.", ["src1"]),
        ), patch(
            "src.infrastructure.logging.tool_call_logger.ToolCallLogger._enabled",
            return_value=True,
        ), patch(
            "src.infrastructure.logging.tool_call_logger.ConversationLogRepository.log_tool_event",
            return_value=True,
        ) as log_mock:
            self.runner.run(
                Channel.WHATSAPP,
                "50614141414",
                "Ustedes me prestan el casco o tengo que llevar uno?",
                "Cliente",
            )

        rag_logs = [call.kwargs for call in log_mock.call_args_list if call.kwargs.get("tool_name") == "rag.answer_question"]
        self.assertTrue(rag_logs)
        self.assertEqual(rag_logs[-1]["status"], "success")
        self.assertEqual(rag_logs[-1]["output_data"]["has_answer"], True)

    def test_unanswered_rag_question_is_logged(self):
        get_patch, set_patch, _ = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="answer_and_start_flow",
                flow="Alquiler",
                has_question=True,
                question="duda que no está en prompt",
                answer_source="rag",
                confidence=0.9,
            ),
        ), patch.object(self.runner.rag, "answer_question", return_value=RagAnswer(False)), patch(
            "src.application.flow_graph.UnansweredQuestionRepository.create",
            return_value=True,
        ), patch(
            "src.infrastructure.logging.tool_call_logger.ToolCallLogger._enabled",
            return_value=True,
        ), patch(
            "src.infrastructure.logging.tool_call_logger.ConversationLogRepository.log_tool_event",
            return_value=True,
        ) as log_mock:
            self.runner.run(Channel.TELEGRAM, "1049838038", "Quiero alquilar y tengo una duda rara", "Erick")

        tool_names = [call.kwargs.get("tool_name") for call in log_mock.call_args_list]
        self.assertIn("rag.answer_question", tool_names)
        self.assertIn("unanswered_question.create", tool_names)

    def test_prompt_rules_operational_answer_is_rechecked_with_rag(self):
        get_patch, set_patch, _ = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="answer_and_start_flow",
                flow="Alquiler",
                has_question=True,
                question="ustedes ayudan con casco",
                answer_source="prompt_rules",
                answer="Sí, le ayudamos con esa parte.",
                confidence=0.9,
            ),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Puede traer su casco o consultar disponibilidad con el asesor."),
        ) as rag_mock, patch(
            "src.infrastructure.logging.tool_call_logger.ToolCallLogger._enabled",
            return_value=True,
        ), patch(
            "src.infrastructure.logging.tool_call_logger.ConversationLogRepository.log_tool_event",
            return_value=True,
        ) as log_mock:
            self.runner.run(Channel.TELEGRAM, "1049838038", "Quiero alquilar y tengo una duda", "Erick")

        rag_mock.assert_called_once()
        tool_names = [call.kwargs.get("tool_name") for call in log_mock.call_args_list]
        self.assertIn("rag.answer_question", tool_names)

    def test_initial_alquiler_with_helmet_question_answers_and_clarifies(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="answer_and_clarify",
                has_question=True,
                question="ustede me ayudan con el casco o lo tengo que llevar yo?",
                answer_source="rag",
                clarifying_question="¿Desea continuar con el proceso de alquiler?",
                confidence=0.6,
            ),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Puede traer su casco o consultar disponibilidad con el asesor."),
        ) as rag_mock:
            result = self.runner.run(
                Channel.TELEGRAM,
                "1049838038",
                "Hola, quiero alquilar una moto, ustede me ayudan con el casco o lo tengo que llevar yo, porque no lo tengo...",
                "Erick",
            )

        rag_mock.assert_called_once()
        self.assertEqual(
            result.replies[0],
            "Puede traer su casco o consultar disponibilidad con el asesor.",
        )
        # La aclaración se redacta de forma variada (no es una frase fija); basta
        # con que ofrezca la opción relevante al contexto de alquiler/vehículo.
        self.assertTrue(any(t in result.replies[1].lower() for t in ("alquil", "reserv", "veh", "moto")))
        self.assertEqual(result.legacy_state, UserState.GENERAL)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "INTAKE")
        self.assertEqual(saved_state.node, "I1")

    @requires_llm
    def test_initial_alquiler_typo_with_question_answers_and_clarifies(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(
                True,
                "Es conveniente traer un casco de su medida y gusto, pero si no lo trae, nosotros le proporcionaremos uno y cinta reflectiva.",
            ),
        ) as rag_mock:
            result = self.runner.run(
                Channel.TELEGRAM,
                "1049838038",
                "Hola buenas, quiero alguilar, el casco va incluido o lo tengo que llevar yo?",
                "Erick",
            )

        rag_mock.assert_called_once()
        self.assertEqual(
            result.replies[0],
            "Es conveniente traer un casco de su medida y gusto, pero si no lo trae, nosotros le proporcionaremos uno y cinta reflectiva.",
        )
        # La aclaración se redacta de forma variada (no es una frase fija); basta
        # con que ofrezca la opción relevante al contexto de alquiler/vehículo.
        self.assertTrue(any(t in result.replies[1].lower() for t in ("alquil", "reserv", "veh", "moto")))
        self.assertEqual(result.legacy_state, UserState.GENERAL)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "INTAKE")
        self.assertEqual(saved_state.node, "I1")

    @requires_llm
    def test_initial_alquiler_with_different_implicit_question_uses_rag_then_clarifies(self):
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "El recorrido de práctica va incluido en el paquete de alquiler."),
        ) as rag_mock:
            result = self.runner.run(
                Channel.TELEGRAM,
                "1049838038",
                "Hola, quiero alquilar una moto, ustedes brindan práctica antes de la prueba o debo practicar aparte",
                "Erick",
            )

        rag_mock.assert_called_once()
        self.assertEqual(result.replies[0], "El recorrido de práctica va incluido en el paquete de alquiler.")
        # La aclaración se redacta de forma variada (no es una frase fija); basta
        # con que ofrezca la opción relevante al contexto de alquiler/vehículo.
        self.assertTrue(any(t in result.replies[1].lower() for t in ("alquil", "reserv", "veh", "moto")))
        self.assertEqual(result.legacy_state, UserState.GENERAL)
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "INTAKE")
        self.assertEqual(saved_state.node, "I1")

    @requires_llm
    def test_general_moto_reply_uses_registered_variant_by_city(self):
        cases = [
            ("G11", True, "G16_1"),
            ("G11", False, "G16"),
            ("G12", True, "G28_1"),
            ("G12", False, "G28"),
        ]

        for current_node, is_registered, expected_node in cases:
            with self.subTest(current_node=current_node, is_registered=is_registered):
                stored = ConversationState(
                    flow="GENERAL",
                    node=current_node,
                    last_question="La licencia que usted va a sacar es moto o carro???",
                    user_name="Cliente",
                )
                get_patch, set_patch, set_mock = self._repo_patches(stored)
                report_patch, block_patch, clear_patch, _ = self._report_block_patches()

                with get_patch, set_patch, report_patch, block_patch, clear_patch, patch(
                    "src.infrastructure.repositories.keyword_registry_repository.KeywordRegistryRepository.exists",
                    return_value=is_registered,
                ) as registry_mock:
                    result = self.runner.run(Channel.WHATSAPP, "50688888888", "moto", "Cliente")

                registry_mock.assert_called_once_with("50688888888", Channel.WHATSAPP.value)
                self.assertTrue(result.replies)
                saved_state = set_mock.call_args.args[2]
                self.assertEqual(saved_state.flow, "GENERAL")
                self.assertEqual(saved_state.node, expected_node)

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

    @requires_llm
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

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification("unknown"),
        ), patch(
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

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification("complaint"),
        ):
            result = self.runner.run(Channel.WHATSAPP, "50655555555", "Esto es una estafa, estoy molesto", "Cliente")

        self.assertEqual(result.replies, [FlowGraphRunner.COMPLAINT_HANDOFF_MESSAGE])
        report_mock.assert_called_once()
        block_repo.block_user.assert_called_once()
        self.assertIn("GENERAL.G35", report_mock.call_args.kwargs["problema"])

    def test_human_help_request_hands_off_and_summarizes_with_history(self):
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

        with get_patch, set_patch, report_patch as report_mock, block_patch, clear_patch, patch.object(
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification("human_handoff"),
        ), patch.object(
            self.runner.classifier,
            "summarize_for_report",
            return_value="El usuario pide un asesor; el historial reciente muestra molestia: nadie responde.",
        ) as summary_mock:
            result = self.runner.run(Channel.WHATSAPP, "50666666666", "Pónganme en contacto con un asesor", "Cliente")

        self.assertEqual(result.replies, [FlowGraphRunner.COMPLAINT_HANDOFF_MESSAGE])
        report_mock.assert_called_once()
        block_repo.block_user.assert_called_once()
        # El historial se delega al LLM (summarize_for_report), no a keywords.
        self.assertEqual(summary_mock.call_args.kwargs["conversation_history"], stored.conversation_history)
        problema = report_mock.call_args.kwargs["problema"]
        self.assertIn("nadie responde", problema)

    @requires_llm
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
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification(
                "city",
                value="liberia",
                has_off_flow_question=True,
                off_flow_question="tengo una consulta, y si pierdo el examen teórico tengo que volver a pagar?",
            ),
        ), patch.object(
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

    def test_license_answer_with_side_question_answers_then_advances_flow(self):
        # "moto" responde la pregunta del flujo y, a la vez, trae una duda
        # lateral: el bot debe responder la duda y AVANZAR (no re-preguntar).
        stored = ConversationState(
            flow="GENERAL",
            node="G11",
            last_question="La licencia que usted va a sacar es moto o carro???",
            user_name="Cliente",
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)

        with get_patch, set_patch, patch.object(
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification(
                "license",
                value="moto",
                has_off_flow_question=True,
                off_flow_question="ustedes ofrecen el casco o tengo que llevarlo?",
            ),
        ), patch(
            "src.infrastructure.repositories.keyword_registry_repository.KeywordRegistryRepository.exists",
            return_value=False,
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Acá le damos casco y cinta reflectiva si no trae uno."),
        ) as rag_mock:
            result = self.runner.run(
                Channel.WHATSAPP,
                "50688888888",
                "moto\nustedes ofrecen el casco o tengo que llevarlo?",
                "Cliente",
            )

        rag_mock.assert_called_once()
        # Primero responde la duda lateral, luego envía el nodo de avance.
        self.assertEqual(result.replies[0], "Acá le damos casco y cinta reflectiva si no trae uno.")
        self.assertNotIn("retomemos la última pregunta", "\n".join(result.replies))
        saved_state = set_mock.call_args.args[2]
        self.assertEqual(saved_state.flow, "GENERAL")
        self.assertEqual(saved_state.node, "G16")

    def test_unanswered_off_flow_question_does_not_push_pending_step(self):
        # El usuario hace una duda dentro del flujo y no podemos responderla
        # (se ofrece asesor). NO debemos insistir con el paso pendiente: la
        # conversación pausa en la duda abierta, sin "retomemos la última...".
        stored = ConversationState(
            flow="CLASES",
            node="C2",
            last_question="Para continuar, envíeme la foto del comprobante de pago.",
            user_name="Cliente",
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)

        with get_patch, set_patch, patch.object(
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification("question"),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(False),
        ), patch("src.application.flow_graph.UnansweredQuestionRepository.create", return_value=True):
            result = self.runner.run(Channel.WHATSAPP, "50611112222", "¿Las clases incluyen seguro?", "Cliente")

        self.assertEqual(len(result.replies), 1)
        self.assertIn("no tengo esa información", result.replies[0].lower())
        self.assertNotIn("retomemos la última", "\n".join(result.replies).lower())

    def test_answered_off_flow_question_reanchors_to_pending_step(self):
        # Si la duda SÍ se resuelve, reanclamos al paso pendiente del flujo.
        stored = ConversationState(
            flow="CLASES",
            node="C2",
            last_question="Para continuar, envíeme la foto del comprobante de pago.",
            user_name="Cliente",
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)

        with get_patch, set_patch, patch.object(
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification("question"),
        ), patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Las clases son individuales y personalizadas."),
        ):
            result = self.runner.run(Channel.WHATSAPP, "50611112222", "¿Las clases son grupales?", "Cliente")

        self.assertEqual(result.replies[0], "Las clases son individuales y personalizadas.")
        self.assertEqual(len(result.replies), 2)

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

    @requires_llm
    def test_retake_uses_node_specific_short_message_for_long_sales_node(self):
        stored = ConversationState(
            flow="CLASES",
            node="C2",
            last_question="💡💡💡 Contamos con 3 opciones para la contratación de clases de manejo.\n\nhttps://calendly.com/clasesdemanejo/clases",
            user_name="Erick",
        )
        get_patch, set_patch, _ = self._repo_patches(stored)

        with get_patch, set_patch, patch.object(
            self.runner.rag,
            "answer_question",
            return_value=RagAnswer(True, "Puede traer su casco o podemos proporcionarle uno."),
        ):
            result = self.runner.run(
                Channel.TELEGRAM,
                "1049838038",
                "Pero si me ayudan con el casco o debo llevarlo yo ?",
                "Erick",
            )

        self.assertEqual(result.replies[0], "Puede traer su casco o podemos proporcionarle uno.")
        self.assertEqual(
            result.replies[-1],
            "No olvide utilizar el enlace que le dejé arriba para agendar su clase de manejo.",
        )
        self.assertNotIn("Contamos con 3 opciones", result.replies[-1])

    def test_reception_does_not_trust_prompt_rules_for_operational_questions(self):
        decision = ReceptionAgent()._validated_decision(
            {
                "action": "answer_and_start_flow",
                "flow": "CLASES",
                "has_question": True,
                "question": "consulta sobre una condición del servicio",
                "answer_source": "prompt_rules",
                "answer": "Respuesta operativa no respaldada por conocimiento.",
                "confidence": 1,
            },
            "consulta sobre una condición del servicio",
        )

        self.assertEqual(decision.action, "answer_and_clarify")
        self.assertEqual(decision.flow, "")
        self.assertEqual(decision.answer_source, "rag")
        self.assertEqual(decision.answer, "")
        self.assertTrue(decision.clarifying_question)

    def test_reception_fallback_degrades_to_clarify_without_keyword_guessing(self):
        # Sin LLM no se adivina la intención con reglas: se degrada de forma
        # segura pidiendo una aclaración. La interpretación del lenguaje natural
        # es responsabilidad exclusiva del modelo de IA.
        decision = ReceptionAgent()._fallback_decision(
            "Hola, quiero llevar un curso de licencia y tengo una duda sobre una condición del examen?"
        )

        self.assertEqual(decision.action, "clarify")
        self.assertEqual(decision.flow, "")
        self.assertTrue(decision.clarifying_question)

    def test_decline_reply_closes_flow_without_sending_next_sales_node(self):
        stored = ConversationState(
            flow="CLASES",
            node="C1",
            last_question="Pregunta de ubicación para continuar el flujo",
            awaiting_reply=True,
            user_name="Erick",
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)
        clear_mock = MagicMock()

        with get_patch, set_patch, patch(
            "src.application.flow_graph.ConversationStateRepo.clear",
            clear_mock,
        ), patch.object(
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification("decline"),
        ):
            result = self.runner.run(Channel.TELEGRAM, "1049838038", "Gracias, ya no deseo continuar", "Erick")

        self.assertEqual(result.legacy_state, UserState.GENERAL)
        self.assertEqual(result.replies, [FlowGraphRunner.DECLINE_CLOSE_MESSAGE])
        clear_mock.assert_called_once_with(Channel.TELEGRAM.value, "1049838038")
        set_mock.assert_not_called()

    def test_greeting_in_active_flow_retakes_question_without_rag_or_handoff(self):
        stored = ConversationState(
            flow="GENERAL",
            node="G1",
            last_question="Ya tiene el teórico ganado???",
            user_name="Erick",
        )
        get_patch, set_patch, set_mock = self._repo_patches(stored)

        with get_patch, set_patch, patch.object(
            self.runner.classifier,
            "classify_reply",
            return_value=ReplyClassification("greeting"),
        ), patch.object(self.runner.rag, "answer_question", return_value=RagAnswer(False)) as rag_mock, patch(
            "src.application.flow_graph.ReportRepository.create_report",
            return_value=(True, {}),
        ) as report_mock:
            result = self.runner.run(Channel.WHATSAPP, "50677777777", "Hola, buenas, ¿cómo está? ¿todo bien?", "Erick")

        # Un saludo no dispara RAG, ni handoff, ni reporte: solo retoma la pregunta.
        self.assertEqual(len(result.replies), 1)
        self.assertNotIn(FlowGraphRunner.COMPLAINT_HANDOFF_MESSAGE, result.replies)
        self.assertNotIn(
            "Por ahora no tengo esa información disponible",
            result.replies[0],
        )
        rag_mock.assert_not_called()
        report_mock.assert_not_called()
        set_mock.assert_called_once()

    def test_greeting_intake_sends_single_message_not_answer_plus_generic_clarify(self):
        # Un usuario nuevo que solo saluda debe recibir UN solo mensaje (saludo +
        # opciones), nunca el saludo del modelo seguido de la pregunta genérica.
        get_patch, set_patch, set_mock = self._repo_patches(ConversationState())
        report_patch, block_patch, clear_patch, _ = self._report_block_patches()

        with get_patch, set_patch, report_patch, block_patch, clear_patch, patch.object(
            self.runner.reception,
            "decide",
            return_value=ReceptionDecision(
                action="clarify",
                answer="Hola, estoy bien. ¿Buscas ayuda con tu licencia, dictamen, clases o alquiler?",
                confidence=0.3,
            ),
        ), patch.object(self.runner.rag, "answer_question", return_value=RagAnswer(False)) as rag_mock:
            result = self.runner.run(Channel.WHATSAPP, "50699999999", "Hola, ¿cómo estás? ¿todo bien?", "Cliente")

        self.assertEqual(len(result.replies), 1)
        self.assertNotIn("Con gusto. ¿Desea que sigamos", "\n".join(result.replies))
        rag_mock.assert_not_called()

    def test_reply_classifier_handles_question_and_mixed_cases(self):
        classifier = ResponseClassifier()

        def _completion(content):
            return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])

        completions = [
            _completion('{"intent": "question", "value": "", "has_off_flow_question": false, "off_flow_question": ""}'),
            _completion('{"intent": "city", "value": "liberia", "has_off_flow_question": false, "off_flow_question": ""}'),
            _completion(
                '{"intent": "city", "value": "liberia", "has_off_flow_question": true, '
                '"off_flow_question": "tengo una consulta, y si pierdo el examen teórico tengo que volver a pagar?"}'
            ),
        ]

        with patch("src.application.response_classifier.settings.OPENAI_API_KEY", "test-key"), patch(
            "src.application.response_classifier.client.chat.completions.create",
            side_effect=completions,
        ) as completion_mock:
            result = classifier.classify_reply(
                "Tengo una duda sobre el trámite",
                "GENERAL",
                "G1",
                "Ya tiene el teórico ganado???",
            )
            self.assertEqual(result.intent, "question")

            city = classifier.classify_reply("Es en Liberia", "GENERAL", "G35", "Donde es su prueba de manejo???")
            self.assertEqual(city.intent, "city")
            self.assertEqual(city.value, "liberia")

            mixed = classifier.classify_reply(
                "Es en Liberia, pero tengo una consulta, y si pierdo el examen teórico tengo que volver a pagar?",
                "GENERAL",
                "G35",
                "Donde es su prueba de manejo???",
            )

        self.assertEqual(completion_mock.call_count, 3)
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
