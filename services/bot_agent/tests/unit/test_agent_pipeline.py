"""Tests deterministas del pipeline del agente único.

Regla del proyecto: la lógica de flujo/cableado se prueba SIN OpenAI real;
se mockea la decisión del agente (agent.decide) y el RAG.
"""

import os
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NOCODB_INVITACIONES_URL", "http://nocodb.test/invitaciones")
os.environ.setdefault("NOCODB_REPORTES_URL", "http://nocodb.test/reportes")
os.environ.setdefault("NOCODB_CONVERSATIONS_URL", "")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application.agent_pipeline import AgentPipeline
from src.application.fragment_catalog import get_fragment
from src.application.unified_agent import AgentDecision, UnifiedAgent
from src.core.config import settings
from src.domain.entities import UserState
from src.infrastructure.repositories.conversation_state_repo import ConversationState


def _fake_record(**kwargs):
    return kwargs["call"]()


class PipelineHarness(ExitStack):
    """Mockea repositorios y LLM; expone los mocks para asserts."""

    def __init__(self, stored: ConversationState, decision: AgentDecision, rag_answer=None):
        super().__init__()
        self.stored = stored
        self.decision = decision
        self.rag_answer = rag_answer or MagicMock(has_answer=False, answer="", sources=[])

    def __enter__(self):
        super().__enter__()
        self.set_mock = self.enter_context(
            patch("src.application.agent_pipeline.ConversationStateRepo.set")
        )
        self.clear_mock = self.enter_context(
            patch("src.application.agent_pipeline.ConversationStateRepo.clear")
        )
        self.enter_context(
            patch("src.application.agent_pipeline.ConversationStateRepo.get", return_value=self.stored)
        )
        self.report_mock = self.enter_context(
            patch("src.application.agent_pipeline.ReportRepository.create_report", return_value=(True, {}))
        )
        self.block_repo = MagicMock()
        self.enter_context(
            patch("src.application.agent_pipeline.PostgresUserRepo", return_value=self.block_repo)
        )
        self.unanswered_mock = self.enter_context(
            patch("src.application.agent_pipeline.UnansweredQuestionRepository.create", return_value=True)
        )
        self.enter_context(
            patch("src.application.agent_pipeline.ToolCallLogger.record", side_effect=_fake_record)
        )
        self.enter_context(
            patch("src.application.runtime_context.clear_user_runtime_context")
        )
        self.enter_context(patch.object(UnifiedAgent, "decide", return_value=self.decision))

        self.pipeline = AgentPipeline()
        self.enter_context(
            patch.object(self.pipeline.rag, "answer_question", return_value=self.rag_answer)
        )
        return self

    def run(self, text="hola", user_id="506", channel="whatsapp", user_name="Cliente"):
        return self.pipeline.run(channel, user_id, text, user_name=user_name)

    @property
    def saved_state(self) -> ConversationState:
        return self.set_mock.call_args.args[2]


class GraphOrchestrationTests(unittest.TestCase):
    def test_pipeline_is_orchestrated_with_langgraph(self):
        decision = AgentDecision(action="reply", messages=["hola"], pending="")
        with PipelineHarness(ConversationState(), decision) as h:
            self.assertTrue(hasattr(h.pipeline.graph, "invoke"))
            self.assertEqual(type(h.pipeline.graph).__name__, "CompiledStateGraph")


class AgentReplyTests(unittest.TestCase):
    def test_fragment_tag_expands_to_literal_messages(self):
        decision = AgentDecision(
            action="reply",
            messages=["Con gusto le paso la información", "[[frag:CLASES.C2]]"],
            pending="Que el cliente agende su clase con el enlace",
        )
        with PipelineHarness(ConversationState(), decision) as h:
            result = h.run(text="quiero clases en liberia")

        fragment = get_fragment("CLASES.C2")
        self.assertEqual(result.replies[0], "Con gusto le paso la información")
        self.assertEqual(result.replies[1:], fragment.messages)

        # El historial guarda la etiqueta, no el texto completo del fragmento.
        history = h.saved_state.conversation_history[-1]
        self.assertIn("[[frag:CLASES.C2]]", history["bot"])
        # El "reporte" del fragmento queda pendiente para el equipo humano.
        self.assertEqual(h.saved_state.pending_report, fragment.report)
        self.assertEqual(h.saved_state.last_question, decision.pending)
        self.assertTrue(h.saved_state.awaiting_reply)

    def test_reply_with_pending_schedules_smart_reminder(self):
        decision = AgentDecision(action="reply", messages=["¿Dónde es su prueba?"], pending="La sede de la prueba")
        with PipelineHarness(ConversationState(), decision) as h:
            result = h.run(text="quiero alquilar una moto")

        self.assertEqual(result.reminder, {"level": 1, "seconds": settings.FOLLOWUP_FIRST_DELAY_SECONDS})
        self.assertEqual(h.saved_state.reminder_level, 0)

    def test_reply_without_pending_does_not_schedule_reminder(self):
        decision = AgentDecision(action="reply", messages=["Con gusto, aquí estamos"], pending="")
        with PipelineHarness(ConversationState(), decision) as h:
            result = h.run(text="gracias")

        self.assertIsNone(result.reminder)

    def test_keyword_variant_is_resolved_by_code_not_by_the_model(self):
        decision = AgentDecision(action="reply", messages=["[[frag:DICTAMEN.D1]]"], pending="El formulario del dictamen")
        with patch(
            "src.infrastructure.repositories.keyword_registry_repository.KeywordRegistryRepository.exists",
            return_value=True,
        ):
            with PipelineHarness(ConversationState(), decision) as h:
                result = h.run(text="quiero el dictamen")

        variant = get_fragment("DICTAMEN.D1_1")
        self.assertEqual(result.replies, variant.messages)
        self.assertIn("[[frag:DICTAMEN.D1_1]]", h.saved_state.conversation_history[-1]["bot"])

    def test_unknown_fragment_tag_is_dropped_without_crashing(self):
        decision = AgentDecision(action="reply", messages=["[[frag:NO.EXISTE]]", "¿Moto o carro?"], pending="Tipo de vehículo")
        with PipelineHarness(ConversationState(), decision) as h:
            result = h.run()

        self.assertEqual(result.replies, ["¿Moto o carro?"])


class AgentRagTests(unittest.TestCase):
    def test_rag_hit_replaces_token_and_keeps_retake(self):
        decision = AgentDecision(
            action="reply",
            messages=["[[rag]]", "¿Seguimos con su reservación?"],
            rag_query="¿cuánto dura la prueba?",
            pending="Confirmar si sigue con la reservación",
        )
        rag_answer = MagicMock(has_answer=True, answer="La prueba dura 25 minutos.", sources=[])
        with PipelineHarness(ConversationState(), decision, rag_answer=rag_answer) as h:
            result = h.run(text="¿cuánto dura la prueba?")

        self.assertEqual(result.replies, ["La prueba dura 25 minutos.", "¿Seguimos con su reservación?"])

    def test_rag_miss_registers_question_and_does_not_push_the_flow(self):
        decision = AgentDecision(
            action="reply",
            messages=["[[rag]]", "¿Seguimos con su reservación?"],
            rag_query="¿aceptan pagos con tarjeta internacional?",
            pending="Confirmar si sigue con la reservación",
        )
        with PipelineHarness(ConversationState(), decision) as h:
            result = h.run(text="¿aceptan tarjeta internacional?")

        self.assertEqual(result.replies, [AgentPipeline.RAG_FALLBACK_MESSAGE])
        h.unanswered_mock.assert_called_once()
        # Sin respaldo no reanclamos ni recordamos de inmediato.
        self.assertIsNone(result.reminder)


class AgentHandoffTests(unittest.TestCase):
    def test_handoff_reports_blocks_and_sends_human_message(self):
        decision = AgentDecision(
            action="handoff",
            messages=["Lamento la molestia. En un momento le escribe un agente especializado para resolver su caso."],
            report="Cliente molesto por un cobro; pide devolución.",
        )
        with PipelineHarness(ConversationState(), decision) as h:
            result = h.run(text="esto es una estafa, quiero mi dinero")

        self.assertEqual(result.replies, decision.messages)
        h.report_mock.assert_called_once()
        problema = h.report_mock.call_args.kwargs["problema"]
        self.assertIn("Cliente molesto", problema)
        h.block_repo.block_user.assert_called_once()
        self.assertEqual(h.block_repo.block_user.call_args.kwargs.get("days"), 12)

    def test_handoff_without_message_uses_default(self):
        decision = AgentDecision(action="handoff", messages=[], report="Pago reportado por revisar")
        with PipelineHarness(ConversationState(), decision) as h:
            result = h.run(text="ya hice el sinpe")

        self.assertEqual(result.replies, [AgentPipeline.HANDOFF_DEFAULT_MESSAGE])


class AgentCloseTests(unittest.TestCase):
    def test_close_clears_state(self):
        decision = AgentDecision(action="close", messages=["Con gusto, que le vaya muy bien."])
        with PipelineHarness(ConversationState(flow="AGENT", last_question="algo"), decision) as h:
            result = h.run(text="gracias, era solo eso")

        self.assertEqual(result.replies, ["Con gusto, que le vaya muy bien."])
        h.clear_mock.assert_called_once()
        h.set_mock.assert_not_called()


class AntiLoopTests(unittest.TestCase):
    def test_repeating_the_same_reply_escalates_to_handoff(self):
        stored = ConversationState(
            flow="AGENT",
            conversation_history=[
                {"flow": "AGENT", "node": "", "type": "agent_reply", "user": "x", "bot": ["¿Busca licencia, clases o alquiler?"]},
            ],
        )
        decision = AgentDecision(
            action="reply",
            messages=["¿Busca licencia, clases o alquiler?"],
            pending="Qué servicio necesita",
        )
        with PipelineHarness(stored, decision) as h:
            result = h.run(text="mmm no sé")

        self.assertEqual(result.replies, [AgentPipeline.HANDOFF_DEFAULT_MESSAGE])
        h.report_mock.assert_called_once()
        self.assertIn("Anti-bucle", h.report_mock.call_args.kwargs["problema"])
        h.block_repo.block_user.assert_called_once()


class CityInvitationTests(unittest.TestCase):
    def test_city_invitation_delegates_to_publicidad(self):
        decision = AgentDecision(action="city_invitation", messages=[], city="Nicoya")
        with PipelineHarness(ConversationState(), decision) as h:
            with patch(
                "src.application.publicidad_service.PublicidadService.handle_invitation_by_city",
                return_value=True,
            ) as invite_mock:
                result = h.run(text="soy de Nicoya")

        invite_mock.assert_called_once()
        self.assertEqual(invite_mock.call_args.args[1], "Nicoya")
        self.assertEqual(result.legacy_state, UserState.PUBLICIDAD)
        self.assertEqual(result.replies, [])
        h.clear_mock.assert_called_once()

    def test_city_not_found_reports_and_blocks(self):
        decision = AgentDecision(action="city_invitation", messages=[], city="Atlantis")
        with PipelineHarness(ConversationState(), decision) as h:
            with patch(
                "src.application.publicidad_service.PublicidadService.handle_invitation_by_city",
                return_value=False,
            ):
                result = h.run(text="Atlantis")

        self.assertEqual(result.replies, [AgentPipeline.HANDOFF_DEFAULT_MESSAGE])
        h.report_mock.assert_called_once()
        self.assertIn("Atlantis", h.report_mock.call_args.kwargs["problema"])


class DecisionValidationTests(unittest.TestCase):
    def test_invalid_action_and_empty_messages_degrade_safely(self):
        agent = UnifiedAgent()
        decision = agent._validated_decision({"action": "explode", "messages": []}, "hola")
        self.assertEqual(decision.action, "reply")
        self.assertTrue(decision.messages)

    def test_rag_query_without_token_gets_token_inserted(self):
        agent = UnifiedAgent()
        decision = agent._validated_decision(
            {"action": "reply", "messages": ["¿Seguimos?"], "rag_query": "¿precio del dictamen?"},
            "¿precio del dictamen?",
        )
        self.assertIn("[[rag]]", decision.messages[0])

    def test_handoff_without_report_gets_default_reason(self):
        agent = UnifiedAgent()
        decision = agent._validated_decision({"action": "handoff", "messages": ["ok"]}, "quiero un humano")
        self.assertTrue(decision.report)

    def test_no_api_key_falls_back_to_safe_clarify(self):
        agent = UnifiedAgent()
        with patch("src.application.unified_agent.settings.OPENAI_API_KEY", ""):
            decision = agent.decide("hola", ConversationState())
        self.assertEqual(decision.action, "reply")
        self.assertEqual(decision.source, "fallback")


if __name__ == "__main__":
    unittest.main()
