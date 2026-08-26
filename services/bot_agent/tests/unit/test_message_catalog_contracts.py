"""Contratos sobre mensajes.json (el catálogo curado de la FSM).

El caso real de las capturas del 2026-07-14 ("Ocupo alquilar un carro" →
saludo de obtención de licencia) NO era un bug del LLM ni del router: era
contenido duplicado entre nodos de flujos distintos en mensajes.json. Estos
tests fijan que el *framing* del nodo inicial de cada flujo coincida con la
intención que lleva a ese nodo.
"""

import os
import unittest


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application.message_catalog import get_messages_for_node


# Nodo inicial de cada flujo transaccional (ver FlowRouter.initial_node).
INITIAL_NODES = [
    ("GENERAL", "G1"),
    ("Alquiler", "A1"),
    ("CLASES", "C1"),
    ("DICTAMEN", "D1"),
    ("DICTAMEN", "D1_1"),
    ("QUEJA", "Q1"),
    ("WIN", "W1"),
]


class MessageCatalogContractTests(unittest.TestCase):
    def test_every_flow_initial_node_has_messages(self):
        # Si el nodo inicial de un flujo desaparece o queda vacío, el intake
        # enruta bien pero el cliente recibe silencio.
        for flow, node in INITIAL_NODES:
            with self.subTest(flow=flow, node=node):
                messages = get_messages_for_node(flow, node)
                self.assertTrue(messages, f"{flow}.{node} sin mensajes en mensajes.json")
                self.assertTrue(all(m.strip() for m in messages))

    def test_alquiler_a1_framing_matches_alquiler_intent(self):
        # Regresión (capturas 2026-07-14): A1 tenía pegado el saludo del flujo
        # de licencias ("estaré pendiente de su proceso de obtención de
        # licencia"), así que quien pedía alquilar recibía un framing de otro
        # servicio. El saludo de A1 debe hablar de vehículo/alquiler/reserva.
        text = "\n".join(get_messages_for_node("Alquiler", "A1")).lower()
        self.assertNotIn("obtención de licencia", text)
        self.assertTrue(
            any(term in text for term in ("vehículo", "vehiculo", "alquiler", "reserva")),
            "El saludo de Alquiler.A1 no menciona el servicio de alquiler/reservación",
        )

    def test_alquiler_liberia_keeps_only_the_vehicle_reservation_form(self):
        messages = get_messages_for_node("GENERAL", "G13")
        text = "\n".join(messages).lower()

        self.assertNotIn("calendly.com", text)
        self.assertNotIn("agendando vía formulario", text)
        self.assertNotIn("ambos formularios", text)
        self.assertIn("forms.gle/v8burkwgxlsvbga98", text)

    def test_each_flow_greeting_names_its_own_service(self):
        # Generalización del bug de A1: el saludo inicial de cada flujo debe
        # nombrar SU servicio. Si al editar el catálogo se pega el framing de
        # otro flujo, el cliente percibe que el bot entendió otra cosa.
        expected_terms = {
            ("GENERAL", "G1"): ("licencia",),
            ("Alquiler", "A1"): ("vehículo", "vehiculo", "alquiler", "reserva"),
            ("CLASES", "C1"): ("clases",),
            ("DICTAMEN", "D1"): ("dictamen",),
            ("DICTAMEN", "D1_1"): ("dictamen",),
        }
        for (flow, node), terms in expected_terms.items():
            with self.subTest(flow=flow, node=node):
                text = "\n".join(get_messages_for_node(flow, node)).lower()
                self.assertTrue(
                    any(term in text for term in terms),
                    f"El saludo de {flow}.{node} no menciona su servicio ({terms})",
                )

    def test_only_general_greeting_claims_license_process_framing(self):
        # El framing "estaré pendiente de su proceso de obtención de licencia"
        # pertenece al flujo GENERAL. Si aparece en el nodo inicial de otro
        # flujo, es contenido pegado de más (la causa raíz del caso A1).
        for flow, node in INITIAL_NODES:
            if flow == "GENERAL":
                continue
            with self.subTest(flow=flow, node=node):
                text = "\n".join(get_messages_for_node(flow, node)).lower()
                self.assertNotIn("obtención de licencia", text)
