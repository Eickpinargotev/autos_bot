"""Guardrails deterministas de la tabla de decisión (docs/tabla_decision_agente.md).

Verifican los invariantes que corrigen los dos casos reportados, sin depender del
LLM (operan sobre la normalización pura de las decisiones).
"""

import os
import types
import unittest
from unittest.mock import patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application.flow_graph import FlowGraphRunner
from src.application.reception_agent import ReceptionAgent, ReceptionDecision
from src.application.response_classifier import ReplyClassification, ResponseClassifier
from src.infrastructure.repositories.conversation_state_repo import ConversationState


class ReceptionP2Tests(unittest.TestCase):
    """Invariante P2: sin pregunta no hay respuesta previa en un start_flow."""

    def setUp(self):
        self.agent = ReceptionAgent()

    def test_clears_filler_answer_when_no_question_on_answer_and_start_flow(self):
        # Caso 1 reportado: "Tengo prueba mañana" -> el modelo antepuso un
        # "¡Buena suerte!" sin que hubiera pregunta. Debe eliminarse.
        decision = self.agent._validated_decision(
            {
                "action": "answer_and_start_flow",
                "flow": "GENERAL",
                "has_question": False,
                "answer": "¡Buena suerte en tu prueba!",
                "answer_source": "prompt_rules",
            },
            text="Tengo prueba mañana",
        )
        self.assertEqual(decision.action, "start_flow")
        self.assertEqual(decision.answer, "")
        self.assertEqual(decision.answer_source, "none")

    def test_clears_answer_on_plain_start_flow_without_question(self):
        decision = self.agent._validated_decision(
            {
                "action": "start_flow",
                "flow": "Alquiler",
                "has_question": False,
                "answer": "Con gusto le ayudo",
                "answer_source": "prompt_rules",
            },
            text="Quiero alquilar una moto",
        )
        self.assertEqual(decision.action, "start_flow")
        self.assertEqual(decision.answer, "")

    def test_keeps_answer_when_there_is_a_real_question(self):
        # Control: con pregunta real, answer_and_start_flow conserva la respuesta.
        decision = self.agent._validated_decision(
            {
                "action": "answer_and_start_flow",
                "flow": "GENERAL",
                "has_question": True,
                "question": "¿cuánto dura el proceso?",
                "answer": "Suele tomar pocas semanas.",
                "answer_source": "rag",
            },
            text="sí, quiero seguir; ¿cuánto dura el proceso?",
        )
        self.assertEqual(decision.action, "answer_and_start_flow")
        self.assertEqual(decision.answer, "Suele tomar pocas semanas.")


class ClassifierCoherenceTests(unittest.TestCase):
    """F-i / F-iii: el saludo/queja/handoff no llevan duda lateral."""

    def test_greeting_drops_off_flow_question(self):
        result = ResponseClassifier._normalize(
            ReplyClassification("greeting", "", True, "¿algo?")
        )
        self.assertFalse(result.has_off_flow_question)
        self.assertEqual(result.off_flow_question, "")

    def test_complaint_and_handoff_drop_off_flow_question(self):
        for intent in ("complaint", "human_handoff"):
            result = ResponseClassifier._normalize(
                ReplyClassification(intent, "", True, "¿tienen campo?")
            )
            self.assertFalse(result.has_off_flow_question, intent)

    def test_empty_off_flow_text_lowers_flag(self):
        result = ResponseClassifier._normalize(
            ReplyClassification("positive", "", True, "   ")
        )
        self.assertFalse(result.has_off_flow_question)

    def test_real_side_question_is_preserved(self):
        # Control: una respuesta al flujo con duda real conserva la bandera.
        result = ResponseClassifier._normalize(
            ReplyClassification("license", "moto", True, "¿tienen campo el sábado?")
        )
        self.assertTrue(result.has_off_flow_question)
        self.assertEqual(result.off_flow_question, "¿tienen campo el sábado?")


class IntakeRagMissTests(unittest.TestCase):
    """Una duda en intake sin respuesta del RAG aclara, NO deriva ni bloquea."""

    def test_rag_miss_clarifies_instead_of_handoff(self):
        runner = FlowGraphRunner()
        decision = ReceptionDecision(
            action="answer_and_clarify",
            flow="",
            has_question=True,
            question="¿tienen sede en un lugar muy específico?",
            answer_source="rag",
            clarifying_question="¿Desea conocer más sobre nuestro proceso de licencia?",
        )
        state = {"user_id": "", "channel": ""}
        no_answer = types.SimpleNamespace(has_answer=False, answer="", sources=[])

        with patch.object(runner, "_answer_rag", return_value=no_answer), \
             patch.object(runner, "_create_unanswered_question") as register_mock:
            out = runner._resolve_reception_rag(decision, state, ConversationState())

        # Clave: NO handoff (handoff en intake bloquea 12 días).
        self.assertEqual(out.action, "clarify")
        self.assertNotEqual(out.action, "handoff")
        self.assertEqual(out.answer, "")
        self.assertFalse(out.has_question)
        register_mock.assert_called_once()  # la pregunta sin respuesta sí se registra

    def test_rag_hit_still_answers(self):
        # Control: si el RAG responde, se conserva la respuesta y se sube la acción.
        runner = FlowGraphRunner()
        decision = ReceptionDecision(
            action="clarify",
            flow="",
            has_question=True,
            question="¿cuánto dura el proceso?",
            answer_source="rag",
        )
        state = {"user_id": "", "channel": ""}
        answer = types.SimpleNamespace(has_answer=True, answer="Pocas semanas.", sources=[])

        with patch.object(runner, "_answer_rag", return_value=answer):
            out = runner._resolve_reception_rag(decision, state, ConversationState())

        self.assertEqual(out.action, "answer_and_clarify")
        self.assertEqual(out.answer, "Pocas semanas.")


if __name__ == "__main__":
    unittest.main()
