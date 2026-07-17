"""Tests deterministas del pipeline supervisor/workers.

Regla del proyecto: la lógica de flujo/cableado se prueba SIN OpenAI real;
se mockean las decisiones (SupervisorAgent/SpecialistAgent.decide) y el RAG.
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
from src.application.unified_agent import (
    SAFE_CLARIFY_MESSAGE,
    AgentDecision,
    SpecialistAgent,
    SupervisorAgent,
)
from src.core.config import settings
from src.domain.entities import UserState
from src.infrastructure.repositories.conversation_state_repo import ConversationState


def _fake_record(**kwargs):
    return kwargs["call"]()


class PipelineHarness(ExitStack):
    """Mockea repositorios y LLM; expone los mocks para asserts.

    `supervisor` y `specialist` aceptan una AgentDecision o una lista de
    decisiones (una por invocación, para turnos con defer/route encadenados).
    """

    def __init__(
        self,
        stored: ConversationState,
        supervisor: AgentDecision | list[AgentDecision] | None = None,
        specialist: AgentDecision | list[AgentDecision] | None = None,
        rag_answer=None,
    ):
        super().__init__()
        self.stored = stored
        self._supervisor_decisions = supervisor if isinstance(supervisor, list) else ([supervisor] if supervisor else [])
        self._specialist_decisions = specialist if isinstance(specialist, list) else ([specialist] if specialist else [])
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
        self.supervisor_mock = self.enter_context(
            patch.object(SupervisorAgent, "decide", side_effect=list(self._supervisor_decisions))
        )
        self.specialist_mock = self.enter_context(
            patch.object(SpecialistAgent, "decide", side_effect=list(self._specialist_decisions))
        )

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
        with PipelineHarness(ConversationState(), supervisor=decision) as h:
            self.assertTrue(hasattr(h.pipeline.graph, "invoke"))
            self.assertEqual(type(h.pipeline.graph).__name__, "CompiledStateGraph")


class RoutingTests(unittest.TestCase):
    def test_supervisor_routes_to_specialist_and_specialist_owns_conversation(self):
        route = AgentDecision(action="route", target="ALQUILER")
        specialist = AgentDecision(
            action="reply", messages=["[[frag:Alquiler.A1]]"], pending="Si ya tiene cita"
        )
        with PipelineHarness(ConversationState(), supervisor=route, specialist=specialist) as h:
            result = h.run(text="quiero alquilar una moto")

        fragment = get_fragment("Alquiler.A1")
        self.assertEqual(result.replies, fragment.messages)
        h.supervisor_mock.assert_called_once()
        h.specialist_mock.assert_called_once()
        # Routing pegajoso: el área queda dueña de la conversación.
        self.assertEqual(h.saved_state.active_agent, "ALQUILER")

    def test_active_specialist_skips_the_supervisor(self):
        specialist = AgentDecision(action="reply", messages=["¿En qué sede es su prueba?"], pending="La sede")
        with PipelineHarness(
            ConversationState(active_agent="ALQUILER"), specialist=specialist
        ) as h:
            result = h.run(text="sí tengo cita")

        h.supervisor_mock.assert_not_called()
        h.specialist_mock.assert_called_once()
        self.assertEqual(result.replies, ["¿En qué sede es su prueba?"])
        self.assertEqual(h.saved_state.active_agent, "ALQUILER")

    def test_supervisor_reply_does_not_bind_a_specialist(self):
        decision = AgentDecision(action="reply", messages=["Con gusto, ¿qué necesita?"], pending="Qué necesita")
        with PipelineHarness(ConversationState(), supervisor=decision) as h:
            h.run(text="hola")
        self.assertEqual(h.saved_state.active_agent, "")

    def test_defer_returns_the_turn_to_the_supervisor(self):
        defer = AgentDecision(action="defer", report="El cliente quiere dictamen, no clases.")
        supervisor_after = AgentDecision(action="route", target="DICTAMEN")
        dictamen_reply = AgentDecision(
            action="reply", messages=["[[frag:DICTAMEN.D1]]"], pending="El formulario del dictamen"
        )
        with PipelineHarness(
            ConversationState(active_agent="CLASES"),
            supervisor=supervisor_after,
            specialist=[defer, dictamen_reply],
        ) as h:
            result = h.run(text="mejor quiero el dictamen")

        # CLASES defirió → supervisor enrutó a DICTAMEN → respondió DICTAMEN.
        self.assertEqual(h.specialist_mock.call_count, 2)
        h.supervisor_mock.assert_called_once()
        # La nota interna del defer llega al supervisor.
        self.assertIn("CLASES", h.supervisor_mock.call_args.kwargs["internal_note"])
        self.assertEqual(result.replies, get_fragment("DICTAMEN.D1").messages)
        self.assertEqual(h.saved_state.active_agent, "DICTAMEN")

    def test_rerouting_to_the_same_area_degrades_to_safe_clarify(self):
        defer = AgentDecision(action="defer", report="No es mi área.")
        stubborn_route = AgentDecision(action="route", target="CLASES")
        with PipelineHarness(
            ConversationState(active_agent="CLASES"),
            supervisor=stubborn_route,
            specialist=[defer],
        ) as h:
            result = h.run(text="algo raro")

        # El guardrail impide el ping-pong: aclaración segura, sin segunda
        # llamada al especialista.
        self.assertEqual(result.replies, [SAFE_CLARIFY_MESSAGE])
        self.assertEqual(h.specialist_mock.call_count, 1)


class FragmentGuardrailTests(unittest.TestCase):
    def test_specialist_cannot_send_foreign_fragments(self):
        # CLASES intenta enviar el fragmento del dictamen: se descarta.
        decision = AgentDecision(
            action="reply",
            messages=["[[frag:DICTAMEN.D1]]", "¿Ocupa las clases en Liberia?"],
            pending="La sede de las clases",
        )
        with PipelineHarness(ConversationState(active_agent="CLASES"), specialist=decision) as h:
            result = h.run(text="quiero clases")

        self.assertEqual(result.replies, ["¿Ocupa las clases en Liberia?"])

    def test_fragment_tag_expands_to_literal_messages(self):
        decision = AgentDecision(
            action="reply",
            messages=["Con gusto le paso la información", "[[frag:CLASES.C2]]"],
            pending="Que el cliente agende su clase con el enlace",
        )
        with PipelineHarness(ConversationState(active_agent="CLASES"), specialist=decision) as h:
            result = h.run(text="sí, en liberia")

        fragment = get_fragment("CLASES.C2")
        self.assertEqual(result.replies[0], "Con gusto le paso la información")
        self.assertEqual(result.replies[1:], fragment.messages)

        # El historial guarda la etiqueta, no el texto completo del fragmento.
        history = h.saved_state.conversation_history[-1]
        self.assertIn("[[frag:CLASES.C2]]", history["bot"])
        self.assertEqual(history["agent"], "CLASES")
        # El "reporte" del fragmento queda pendiente para el equipo humano.
        self.assertEqual(h.saved_state.pending_report, fragment.report)
        self.assertTrue(h.saved_state.awaiting_reply)

    def test_keyword_variant_is_resolved_by_code_not_by_the_model(self):
        decision = AgentDecision(action="reply", messages=["[[frag:DICTAMEN.D1]]"], pending="El formulario")
        with patch(
            "src.infrastructure.repositories.keyword_registry_repository.KeywordRegistryRepository.exists",
            return_value=True,
        ):
            with PipelineHarness(ConversationState(active_agent="DICTAMEN"), specialist=decision) as h:
                result = h.run(text="quiero el dictamen")

        variant = get_fragment("DICTAMEN.D1_1")
        self.assertEqual(result.replies, variant.messages)
        self.assertIn("[[frag:DICTAMEN.D1_1]]", h.saved_state.conversation_history[-1]["bot"])

    def test_transcribed_fragment_is_reassembled_into_the_literal_one(self):
        # Bug real: el modelo escribió el contenido de GENERAL.G1 partido en
        # varios mensajes en vez de usar la etiqueta. El guardrail lo detecta
        # y lo reemplaza por el fragmento literal (2 mensajes, etiqueta en el
        # historial).
        g1 = get_fragment("GENERAL.G1")
        transcribed = AgentDecision(
            action="reply",
            messages=[
                "Hola!!!",
                "Gracias por tu mensaje",
                "Mi nombre es Enrique Guzmán y estaré pendiente de su proceso de obtención de licencias",
                "Ya tiene el teórico ganado???",
            ],
            pending="Si ya tiene el teórico ganado",
        )
        with PipelineHarness(ConversationState(active_agent="GENERAL"), specialist=transcribed) as h:
            result = h.run(text="vengo a sacar la cita del práctico b1")

        self.assertEqual(result.replies, g1.messages)
        self.assertEqual(h.saved_state.conversation_history[-1]["bot"], ["[[frag:GENERAL.G1]]"])

    def test_transcription_reassembly_respects_keyword_variant(self):
        d1 = get_fragment("DICTAMEN.D1")
        transcribed = AgentDecision(action="reply", messages=list(d1.messages), pending="El formulario")
        with patch(
            "src.infrastructure.repositories.keyword_registry_repository.KeywordRegistryRepository.exists",
            return_value=True,
        ):
            with PipelineHarness(ConversationState(active_agent="DICTAMEN"), specialist=transcribed) as h:
                result = h.run(text="quiero el dictamen")

        # Transcribió el D1 base, pero el cliente está registrado: se envía la
        # variante D1_1 con su sinpe correcto.
        self.assertEqual(result.replies, get_fragment("DICTAMEN.D1_1").messages)

    def test_unknown_fragment_tag_is_dropped_without_crashing(self):
        decision = AgentDecision(action="reply", messages=["[[frag:NO.EXISTE]]", "¿Moto o carro?"], pending="Tipo")
        with PipelineHarness(ConversationState(active_agent="ALQUILER"), specialist=decision) as h:
            result = h.run()

        self.assertEqual(result.replies, ["¿Moto o carro?"])


class ReminderSignalTests(unittest.TestCase):
    def test_reply_with_pending_schedules_smart_reminder(self):
        decision = AgentDecision(action="reply", messages=["¿Dónde es su prueba?"], pending="La sede de la prueba")
        with PipelineHarness(ConversationState(active_agent="ALQUILER"), specialist=decision) as h:
            result = h.run(text="quiero alquilar una moto")

        self.assertEqual(result.reminder, {"level": 1, "seconds": settings.FOLLOWUP_FIRST_DELAY_SECONDS})
        self.assertEqual(h.saved_state.reminder_level, 0)

    def test_reply_without_pending_does_not_schedule_reminder(self):
        decision = AgentDecision(action="reply", messages=["Con gusto, aquí estamos"], pending="")
        with PipelineHarness(ConversationState(), supervisor=decision) as h:
            result = h.run(text="gracias")

        self.assertIsNone(result.reminder)


class AgentRagTests(unittest.TestCase):
    def test_rag_hit_replaces_token_and_keeps_retake(self):
        decision = AgentDecision(
            action="reply",
            messages=["[[rag]]", "¿Seguimos con su reservación?"],
            rag_query="¿cuánto dura la prueba?",
            pending="Confirmar si sigue con la reservación",
        )
        rag_answer = MagicMock(has_answer=True, answer="La prueba dura 25 minutos.", sources=[])
        with PipelineHarness(ConversationState(), supervisor=decision, rag_answer=rag_answer) as h:
            result = h.run(text="¿cuánto dura la prueba?")

        self.assertEqual(result.replies, ["La prueba dura 25 minutos.", "¿Seguimos con su reservación?"])

    def test_rag_miss_registers_question_and_does_not_push_the_flow(self):
        decision = AgentDecision(
            action="reply",
            messages=["[[rag]]", "¿Seguimos con su reservación?"],
            rag_query="¿aceptan pagos con tarjeta internacional?",
            pending="Confirmar si sigue con la reservación",
        )
        with PipelineHarness(ConversationState(), supervisor=decision) as h:
            result = h.run(text="¿aceptan tarjeta internacional?")

        self.assertEqual(result.replies, [AgentPipeline.RAG_FALLBACK_MESSAGE])
        h.unanswered_mock.assert_called_once()
        self.assertIsNone(result.reminder)


class AgentHandoffTests(unittest.TestCase):
    def test_handoff_reports_blocks_and_sends_human_message(self):
        decision = AgentDecision(
            action="handoff",
            messages=["Lamento la molestia. En un momento le escribe un agente especializado para resolver su caso."],
            report="Cliente molesto por un cobro; pide devolución.",
        )
        with PipelineHarness(ConversationState(), supervisor=decision) as h:
            result = h.run(text="esto es una estafa, quiero mi dinero")

        self.assertEqual(result.replies, decision.messages)
        h.report_mock.assert_called_once()
        self.assertIn("Cliente molesto", h.report_mock.call_args.kwargs["problema"])
        h.block_repo.block_user.assert_called_once()
        self.assertEqual(h.block_repo.block_user.call_args.kwargs.get("days"), 12)

    def test_handoff_without_message_uses_default(self):
        decision = AgentDecision(action="handoff", messages=[], report="Pago reportado por revisar")
        with PipelineHarness(ConversationState(active_agent="ALQUILER"), specialist=decision) as h:
            result = h.run(text="ya hice el sinpe")

        self.assertEqual(result.replies, [AgentPipeline.HANDOFF_DEFAULT_MESSAGE])


class AgentCloseTests(unittest.TestCase):
    def test_close_clears_state(self):
        decision = AgentDecision(action="close", messages=["Con gusto, que le vaya muy bien."])
        with PipelineHarness(ConversationState(flow="AGENT", last_question="algo"), supervisor=decision) as h:
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
        with PipelineHarness(stored, supervisor=decision) as h:
            result = h.run(text="mmm no sé")

        self.assertEqual(result.replies, [AgentPipeline.HANDOFF_DEFAULT_MESSAGE])
        h.report_mock.assert_called_once()
        self.assertIn("Anti-bucle", h.report_mock.call_args.kwargs["problema"])
        h.block_repo.block_user.assert_called_once()


class CityInvitationTests(unittest.TestCase):
    def test_city_invitation_delegates_to_publicidad(self):
        decision = AgentDecision(action="city_invitation", messages=[], city="Nicoya")
        with PipelineHarness(ConversationState(active_agent="GENERAL"), specialist=decision) as h:
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
        with PipelineHarness(ConversationState(active_agent="GENERAL"), specialist=decision) as h:
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
        agent = SupervisorAgent()
        decision = agent._validated_decision({"action": "explode", "messages": []}, "hola")
        self.assertEqual(decision.action, "reply")
        self.assertTrue(decision.messages)

    def test_route_with_invalid_target_degrades_to_clarify(self):
        agent = SupervisorAgent()
        decision = agent._validated_decision({"action": "route", "target": "MARTE"}, "hola")
        self.assertEqual(decision.action, "reply")
        self.assertEqual(decision.target, "")
        self.assertTrue(decision.messages)

    def test_specialist_cannot_route_but_can_defer(self):
        agent = SpecialistAgent("CLASES")
        routed = agent._validated_decision({"action": "route", "target": "ALQUILER"}, "hola")
        self.assertEqual(routed.action, "reply")
        deferred = agent._validated_decision({"action": "defer"}, "quiero otra cosa")
        self.assertEqual(deferred.action, "defer")
        self.assertTrue(deferred.report)

    def test_rag_query_without_token_gets_token_inserted(self):
        agent = SupervisorAgent()
        decision = agent._validated_decision(
            {"action": "reply", "messages": ["¿Seguimos?"], "rag_query": "¿precio del dictamen?"},
            "¿precio del dictamen?",
        )
        self.assertIn("[[rag]]", decision.messages[0])

    def test_handoff_without_report_gets_default_reason(self):
        agent = SpecialistAgent("ALQUILER")
        decision = agent._validated_decision({"action": "handoff", "messages": ["ok"]}, "quiero un humano")
        self.assertTrue(decision.report)

    def test_reasoning_effort_only_goes_to_gpt5_models(self):
        # Un despliegue con OPENAI_MODEL viejo no debe recibir reasoning_effort:
        # haría fallar TODAS las llamadas y el bot solo respondería el fallback.
        from src.application.unified_agent import _decision_llm_kwargs

        with patch("src.application.unified_agent.settings.OPENAI_MODEL", "gpt-5.4-mini"):
            self.assertEqual(_decision_llm_kwargs(), {"reasoning_effort": "none"})
        with patch("src.application.unified_agent.settings.OPENAI_MODEL", "gpt-4o-mini"):
            self.assertEqual(_decision_llm_kwargs(), {})

    def test_no_api_key_falls_back_to_safe_clarify(self):
        agent = SupervisorAgent()
        with patch("src.application.unified_agent.settings.OPENAI_API_KEY", ""):
            decision = agent.decide("hola", ConversationState())
        self.assertEqual(decision.action, "reply")
        self.assertEqual(decision.source, "fallback")


if __name__ == "__main__":
    unittest.main()
