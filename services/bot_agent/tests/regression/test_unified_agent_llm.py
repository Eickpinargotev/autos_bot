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


requires_llm = pytest.mark.skipif(
    not settings.OPENAI_API_KEY,
    reason="Requiere OPENAI_API_KEY para verificar el juicio del LLM",
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

    def test_win_sends_review_fragment(self):
        decision = SupervisorAgent().decide("les cuento que aprobé mi prueba de manejo!!!", ConversationState())
        self.assertIn("[[frag:WIN.W1]]", _joined(decision))

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
