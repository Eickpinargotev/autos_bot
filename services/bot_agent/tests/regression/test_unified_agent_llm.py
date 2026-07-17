"""Regresiones del JUICIO del agente único (LLM real).

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
from src.application.unified_agent import FollowupAgent, UnifiedAgent
from src.infrastructure.repositories.conversation_state_repo import ConversationState


requires_llm = pytest.mark.skipif(
    not settings.OPENAI_API_KEY,
    reason="Requiere OPENAI_API_KEY para verificar el juicio del LLM",
)


def _decide(text: str, state: ConversationState | None = None):
    return UnifiedAgent().decide(text, state or ConversationState())


def _joined(decision) -> str:
    return "\n".join(decision.messages)


@requires_llm
class RentalSkipsIrrelevantStepsTests(unittest.TestCase):
    def test_moto_rental_does_not_start_the_license_flow(self):
        decision = _decide("Hola, quiero alquilar una moto para la prueba de manejo")
        self.assertEqual(decision.action, "reply")
        # No debe arrancar el proceso de licencia (teórico) ni repreguntar el
        # vehículo: la moto ya está dicha.
        self.assertNotIn("[[frag:GENERAL.G1]]", _joined(decision))
        self.assertTrue(decision.pending)

    def test_rental_with_all_data_jumps_to_the_package(self):
        state = ConversationState(
            flow="AGENT",
            last_question="¿Ya tiene cita agendada para la prueba de manejo?",
            awaiting_reply=True,
            conversation_history=[
                {
                    "flow": "AGENT",
                    "node": "",
                    "type": "agent_reply",
                    "user": "quiero alquilar una moto para la prueba",
                    "bot": ["[[frag:Alquiler.A1]]"],
                    "pending": "Si ya tiene cita para la prueba de manejo",
                }
            ],
        )
        decision = _decide("sí tengo cita, la prueba es en liberia", state)
        self.assertEqual(decision.action, "reply")
        self.assertIn("[[frag:GENERAL.G16]]", _joined(decision))


@requires_llm
class TranscriptRegressionTests(unittest.TestCase):
    """Bugs reales vistos en producción (transcript 2026-07-16)."""

    PACKAGE_FRAGMENTS = (
        "GENERAL.G13", "GENERAL.G16", "GENERAL.G19", "GENERAL.G20", "GENERAL.G21",
        "GENERAL.G22", "GENERAL.G25", "GENERAL.G28", "GENERAL.G29", "GENERAL.G30",
        "GENERAL.G31", "GENERAL.G32",
    )

    def test_does_not_assume_vehicle_when_client_never_said_it(self):
        # Bug: "quiero alquilar" + "ya tengo cita" → entregó el paquete de MOTO
        # sin saber vehículo ni sede. Debe preguntar, no adivinar.
        state = ConversationState(
            flow="AGENT",
            last_question="Si ya tiene cita agendada para la prueba de manejo",
            awaiting_reply=True,
            conversation_history=[
                {
                    "flow": "AGENT",
                    "node": "",
                    "type": "agent_reply",
                    "user": "Hola, quiero alquilar, tengo 18 años",
                    "bot": ["[[frag:Alquiler.A1]]"],
                    "pending": "Si ya tiene cita agendada para la prueba de manejo",
                }
            ],
        )
        decision = _decide("Si, ya la tengo", state)
        self.assertEqual(decision.action, "reply")
        joined = _joined(decision)
        for package in self.PACKAGE_FRAGMENTS:
            self.assertNotIn(f"[[frag:{package}]]", joined, f"Entregó {package} sin saber el vehículo")

    def test_data_correction_after_package_is_not_handed_off(self):
        # Bug: tras enviar el paquete (reporte_pendiente lleno), el cliente
        # corrigió "es para carro" y el agente derivó a humano. Una corrección
        # del pedido la atiende el agente.
        state = ConversationState(
            flow="AGENT",
            pending_report="Contestaron mensaje sobre formulario de reservacion, revisar!",
            last_question="Que haga la reserva con el formulario del paquete",
            awaiting_reply=True,
            conversation_history=[
                {
                    "flow": "AGENT",
                    "node": "",
                    "type": "agent_reply",
                    "user": "Si, ya la tengo",
                    "bot": ["[[frag:GENERAL.G16]]"],
                    "pending": "Que haga la reserva con el formulario del paquete",
                }
            ],
        )
        decision = _decide("Es para carro que quiero alquilar", state)
        self.assertEqual(decision.action, "reply")


@requires_llm
class EscalationTests(unittest.TestCase):
    def test_angry_customer_is_handed_off_with_a_human_message(self):
        decision = _decide("Me siento estafado, pagué y nadie me resuelve nada. Quiero mi dinero de vuelta.")
        self.assertEqual(decision.action, "handoff")
        self.assertTrue(decision.report)
        self.assertTrue(decision.messages)

    def test_payment_report_goes_to_a_human(self):
        state = ConversationState(
            flow="AGENT",
            pending_report="Contestaron mensaje sobre formulario de reservacion",
            last_question="Que envíe el comprobante del depósito",
            awaiting_reply=True,
            conversation_history=[
                {
                    "flow": "AGENT",
                    "node": "",
                    "type": "agent_reply",
                    "user": "en liberia, moto",
                    "bot": ["[[frag:GENERAL.G16]]"],
                    "pending": "Que envíe el comprobante del depósito",
                }
            ],
        )
        decision = _decide("listo, ya hice el sinpe y llené el formulario", state)
        self.assertEqual(decision.action, "handoff")

    def test_win_sends_review_fragment(self):
        decision = _decide("les cuento que aprobé mi prueba de manejo!!!")
        self.assertIn("[[frag:WIN.W1]]", _joined(decision))


@requires_llm
class FollowupJudgmentTests(unittest.TestCase):
    def test_reminds_a_pending_form_briefly(self):
        state = ConversationState(
            flow="AGENT",
            last_question="Que llene el formulario de reservación",
            awaiting_reply=True,
            reminder_level=0,
            conversation_history=[
                {
                    "flow": "AGENT",
                    "node": "",
                    "type": "agent_reply",
                    "user": "moto en liberia",
                    "bot": ["[[frag:GENERAL.G16]]"],
                    "pending": "Que llene el formulario de reservación",
                }
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
                {
                    "flow": "AGENT",
                    "node": "",
                    "type": "agent_reply",
                    "user": "gracias, luego les escribo, que tengan buen día",
                    "bot": ["Con gusto, aquí estamos para servirle."],
                    "pending": "",
                }
            ],
        )
        decision = FollowupAgent().decide(state)
        self.assertFalse(decision.send)


if __name__ == "__main__":
    unittest.main()
