"""Regresiones del JUICIO de los agentes (LLM real, supervisor/workers).

Estos tests verifican decisiones del modelo, no el cableado (eso se prueba
determinista en tests/unit/test_agent_pipeline.py). Se saltan sin API key.
Los asserts son de grano grueso (acción y señales clave), no de texto exacto.
"""

import os
import unittest

import pytest


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from src.core.config import settings
from src.application.unified_agent import FollowupAgent, SpecialistAgent, SupervisorAgent
from src.infrastructure.repositories.conversation_state_repo import ConversationState


# Estas pruebas consumen tokens de OpenAI: NO corren en la suite normal.
# Se habilitan explícitamente (y solo cuando el dueño lo pida):
#   docker compose -f docker-compose.local.yml run --rm -e RUN_LLM_TESTS=1 bot_agent pytest tests/regression
requires_llm = pytest.mark.skipif(
    not settings.OPENAI_API_KEY or os.environ.get("RUN_LLM_TESTS") != "1",
    reason="Pruebas con LLM real (consumen tokens): habilitar con RUN_LLM_TESTS=1",
)


def _joined(decision) -> str:
    return "\n".join(decision.messages)


def _turn(user: str, bot: list[str], pending: str = "", agent: str = "") -> dict:
    return {
        "flow": "AGENT", "node": "", "type": "agent_reply", "agent": agent,
        "user": user, "bot": bot, "pending": pending,
    }


@requires_llm
class SupervisorRoutingTests(unittest.TestCase):
    def test_rental_intent_routes_to_alquiler(self):
        decision = SupervisorAgent().decide("Hola, quiero alquilar una moto para la prueba", ConversationState())
        self.assertEqual(decision.action, "route")
        self.assertEqual(decision.target, "ALQUILER")

    def test_dictamen_intent_routes_to_dictamen(self):
        decision = SupervisorAgent().decide("ocupo hacer el dictamen médico para mi licencia", ConversationState())
        self.assertEqual(decision.action, "route")
        self.assertEqual(decision.target, "DICTAMEN")

    def test_greeting_is_answered_by_the_supervisor(self):
        decision = SupervisorAgent().decide("hola buenas", ConversationState())
        self.assertEqual(decision.action, "reply")

    def test_renewal_intent_routes_to_tramites(self):
        decision = SupervisorAgent().decide("buenas, quiero renovar mi licencia que ya se me vence", ConversationState())
        self.assertEqual(decision.action, "route")
        self.assertEqual(decision.target, "TRAMITES")

    def test_theory_course_intent_routes_to_curso_teorico(self):
        decision = SupervisorAgent().decide("quiero matricular el curso teorico para la licencia", ConversationState())
        self.assertEqual(decision.action, "route")
        self.assertEqual(decision.target, "CURSO_TEORICO")

    def test_isolated_theory_words_are_clarified_without_form(self):
        for text in ("teórico", "teórica"):
            with self.subTest(text=text):
                decision = SupervisorAgent().decide(text, ConversationState())
                self.assertEqual(decision.action, "reply")
                self.assertNotIn("[[rag]]", _joined(decision))
                self.assertFalse(decision.rag_query)

    def test_win_sends_review_fragment(self):
        decision = SupervisorAgent().decide("les cuento que aprobé mi prueba de manejo!!!", ConversationState())
        self.assertIn("[[frag:WIN.W1]]", _joined(decision))

    def test_specific_cita_request_is_routed_not_clarified(self):
        # Transcript real (2026-07-16): el cliente nombró exactamente lo que
        # quería (cita del práctico B1) y recibió la aclaración genérica.
        # Con intención clara NUNCA se re-pregunta: se enruta.
        msg = (
            "hola, buenos dias como esta profe, ya ahora si tengo 18 años, vengo para que "
            "me ayude a sacar la cita del practico b1, digame que información ocupa y yo se la facilito"
        )
        decision = SupervisorAgent().decide(msg, ConversationState())
        self.assertEqual(decision.action, "route")
        self.assertEqual(decision.target, "GENERAL")

        specialist = SpecialistAgent("GENERAL").decide(msg, ConversationState())
        self.assertEqual(specialist.action, "reply")
        # Guía con el material del proceso (requisito teórico / formulario de
        # cita), no con otra aclaración genérica, y deja el paso pendiente.
        self.assertIn("[[frag:GENERAL.G", _joined(specialist))
        self.assertTrue(specialist.pending)

    def test_angry_customer_is_handed_off_with_a_human_message(self):
        decision = SupervisorAgent().decide(
            "Me siento estafado, pagué y nadie me resuelve nada. Quiero mi dinero de vuelta.",
            ConversationState(),
        )
        self.assertEqual(decision.action, "handoff")
        self.assertTrue(decision.report)
        self.assertTrue(decision.messages)


@requires_llm
class AlquilerSpecialistTests(unittest.TestCase):
    """Incluye los bugs reales del transcript de producción (2026-07-16)."""

    PACKAGE_FRAGMENTS = (
        "GENERAL.G13", "GENERAL.G16", "GENERAL.G19", "GENERAL.G20", "GENERAL.G21",
        "GENERAL.G22", "GENERAL.G25", "GENERAL.G28", "GENERAL.G29", "GENERAL.G30",
        "GENERAL.G31", "GENERAL.G32",
    )

    def _agent(self):
        return SpecialistAgent("ALQUILER")

    def test_does_not_assume_vehicle_when_client_never_said_it(self):
        state = ConversationState(
            flow="AGENT",
            active_agent="ALQUILER",
            last_question="Si ya tiene cita agendada para la prueba de manejo",
            awaiting_reply=True,
            conversation_history=[
                _turn("Hola, quiero alquilar, tengo 18 años", ["[[frag:Alquiler.A1]]"],
                      "Si ya tiene cita agendada para la prueba de manejo", "ALQUILER"),
            ],
        )
        decision = self._agent().decide("Si, ya la tengo", state)
        self.assertEqual(decision.action, "reply")
        joined = _joined(decision)
        for package in self.PACKAGE_FRAGMENTS:
            self.assertNotIn(f"[[frag:{package}]]", joined, f"Entregó {package} sin saber el vehículo")

    def test_moto_and_sede_known_delivers_package_directly(self):
        state = ConversationState(
            flow="AGENT",
            active_agent="ALQUILER",
            last_question="¿Ya tiene cita agendada para la prueba de manejo?",
            awaiting_reply=True,
            conversation_history=[
                _turn("quiero alquilar una moto para la prueba", ["[[frag:Alquiler.A1]]"],
                      "Si ya tiene cita para la prueba de manejo", "ALQUILER"),
            ],
        )
        decision = self._agent().decide("sí tengo cita, la prueba es en liberia", state)
        self.assertEqual(decision.action, "reply")
        self.assertIn("[[frag:GENERAL.G16]]", _joined(decision))

    def test_data_correction_after_package_is_not_handed_off(self):
        state = ConversationState(
            flow="AGENT",
            active_agent="ALQUILER",
            pending_report="Contestaron mensaje sobre formulario de reservacion, revisar!",
            last_question="Que haga la reserva con el formulario del paquete",
            awaiting_reply=True,
            conversation_history=[
                _turn("Si, ya la tengo", ["[[frag:GENERAL.G16]]"],
                      "Que haga la reserva con el formulario del paquete", "ALQUILER"),
            ],
        )
        decision = self._agent().decide("Es para carro que quiero alquilar", state)
        self.assertEqual(decision.action, "reply")

    def test_payment_report_goes_to_a_human(self):
        state = ConversationState(
            flow="AGENT",
            active_agent="ALQUILER",
            pending_report="Contestaron mensaje sobre formulario de reservacion",
            last_question="Que envíe el comprobante del depósito",
            awaiting_reply=True,
            conversation_history=[
                _turn("en liberia, moto", ["[[frag:GENERAL.G16]]"],
                      "Que envíe el comprobante del depósito", "ALQUILER"),
            ],
        )
        decision = self._agent().decide("listo, ya hice el sinpe y llené el formulario", state)
        self.assertEqual(decision.action, "handoff")


@requires_llm
class GeneralSpecialistTests(unittest.TestCase):
    def test_rental_phase_is_deferred(self):
        # Teórico aprobado + cita lista → la fase de vehículo/paquetes es de
        # ALQUILER: el especialista de GENERAL devuelve el turno.
        state = ConversationState(
            flow="AGENT",
            active_agent="GENERAL",
            last_question="¿Ya tiene cita agendada para la prueba de manejo?",
            awaiting_reply=True,
            conversation_history=[
                _turn("quiero sacar mi licencia, ya aprobé el teórico", ["[[frag:GENERAL.G3]]"],
                      "Si ya tiene cita para la prueba de manejo", "GENERAL"),
            ],
        )
        decision = SpecialistAgent("GENERAL").decide("sí, ya tengo la cita", state)
        self.assertEqual(decision.action, "defer")

    def test_missing_theory_is_deferred_to_curso_teorico(self):
        # "No tengo el teórico" no se atiende en GENERAL: pasa al área del
        # curso teórico con el contexto en el defer (diseño v3).
        state = ConversationState(
            flow="AGENT",
            active_agent="GENERAL",
            last_question="¿Ya tiene el teórico ganado?",
            awaiting_reply=True,
            conversation_history=[
                _turn("quiero sacar la licencia", ["[[frag:GENERAL.G1]]"],
                      "Si ya tiene el teórico ganado", "GENERAL"),
            ],
        )
        decision = SpecialistAgent("GENERAL").decide("no, todavía no lo tengo", state)
        self.assertEqual(decision.action, "defer")
        self.assertEqual(decision.target, "CURSO_TEORICO")


@requires_llm
class CursoTeoricoSpecialistTests(unittest.TestCase):
    def test_isolated_theory_words_do_not_request_appointment_form(self):
        for text in ("teórico", "teórica"):
            with self.subTest(text=text):
                state = ConversationState(flow="AGENT", active_agent="CURSO_TEORICO")
                decision = SpecialistAgent("CURSO_TEORICO").decide(text, state)
                self.assertEqual(decision.action, "reply")
                self.assertNotIn("[[rag]]", _joined(decision))
                self.assertFalse(decision.rag_query)

    def test_city_answer_triggers_city_invitation(self):
        state = ConversationState(
            flow="AGENT",
            active_agent="CURSO_TEORICO",
            last_question="¿Dónde vive para el curso teórico?",
            awaiting_reply=True,
            conversation_history=[
                _turn("no tengo el teórico", ["[[frag:GENERAL.G4]]"],
                      "La ciudad donde hará el curso teórico", "CURSO_TEORICO"),
            ],
        )
        decision = SpecialistAgent("CURSO_TEORICO").decide("vivo en nicoya", state)
        self.assertEqual(decision.action, "city_invitation")
        self.assertTrue(decision.city)


@requires_llm
class TramitesSpecialistTests(unittest.TestCase):
    def test_renewal_is_informed_with_rag_not_handed_off(self):
        decision = SpecialistAgent("TRAMITES").decide(
            "que ocupo para renovar la licencia?", ConversationState(flow="AGENT", active_agent="TRAMITES")
        )
        self.assertEqual(decision.action, "reply")
        self.assertIn("[[rag]]", _joined(decision))

    def test_accepting_dictamen_defers_to_dictamen(self):
        state = ConversationState(
            flow="AGENT",
            active_agent="TRAMITES",
            last_question="¿Desea que le gestionemos el dictamen médico?",
            awaiting_reply=True,
            conversation_history=[
                _turn("quiero renovar mi licencia", ["[[rag]]"],
                      "Si desea que le gestionemos el dictamen médico", "TRAMITES"),
            ],
        )
        decision = SpecialistAgent("TRAMITES").decide("sí, lo ocupo, ayúdeme con eso", state)
        self.assertEqual(decision.action, "defer")
        self.assertEqual(decision.target, "DICTAMEN")


@requires_llm
class FollowupJudgmentTests(unittest.TestCase):
    def test_reminds_a_pending_form_briefly(self):
        state = ConversationState(
            flow="AGENT",
            last_question="Que llene el formulario de reservación",
            awaiting_reply=True,
            reminder_level=0,
            conversation_history=[
                _turn("moto en liberia", ["[[frag:GENERAL.G16]]"],
                      "Que llene el formulario de reservación", "ALQUILER"),
            ],
        )
        decision = FollowupAgent().decide(state)
        self.assertTrue(decision.send)
        self.assertTrue(decision.message)
        self.assertLessEqual(len(decision.message), 220)

    def test_does_not_remind_after_a_farewell(self):
        state = ConversationState(
            flow="AGENT",
            last_question="Si desea algo más",
            awaiting_reply=True,
            reminder_level=0,
            conversation_history=[
                _turn("gracias, luego les escribo, que tengan buen día",
                      ["Con gusto, aquí estamos para servirle."], ""),
            ],
        )
        decision = FollowupAgent().decide(state)
        self.assertFalse(decision.send)


if __name__ == "__main__":
    unittest.main()
