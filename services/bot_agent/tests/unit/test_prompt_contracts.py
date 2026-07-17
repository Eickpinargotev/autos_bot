import unittest

from src.core.prompts import (
    AGENT_COMMON_CONTRACT,
    AREA_PROMPT_BODIES,
    FOLLOWUP_AGENT_PROMPT,
    SPECIALIST_OUTPUT_SCHEMA,
    SUPERVISOR_OUTPUT_SCHEMA,
    SUPERVISOR_PROMPT_BODY,
)


ALL_PROMPT_PARTS = [
    AGENT_COMMON_CONTRACT,
    SUPERVISOR_OUTPUT_SCHEMA,
    SPECIALIST_OUTPUT_SCHEMA,
    SUPERVISOR_PROMPT_BODY,
    *AREA_PROMPT_BODIES.values(),
    FOLLOWUP_AGENT_PROMPT,
]


class CommonContractTests(unittest.TestCase):
    """Contratos del prompt compartido (supervisor + especialistas).

    Frases clave que el pipeline y los guardrails asumen presentes. Si se
    reescribe un prompt hay que conservarlas o actualizar este test de forma
    deliberada (CLAUDE.md §6).
    """

    def test_contract_defines_tokens_and_literal_fragments(self):
        self.assertIn("[[frag:ID]]", AGENT_COMMON_CONTRACT)
        self.assertIn("[[rag]]", AGENT_COMMON_CONTRACT)
        self.assertIn("Nunca reescribas, resumas ni parafrasees un fragmento", AGENT_COMMON_CONTRACT)
        self.assertIn("Solo puedes usar fragmentos de TU catálogo", AGENT_COMMON_CONTRACT)

    def test_contract_keeps_transversal_escalation(self):
        self.assertIn("QUEJA FUERTE O INSATISFACCIÓN", AGENT_COMMON_CONTRACT)
        self.assertIn("CASO PARA HUMANO", AGENT_COMMON_CONTRACT)
        self.assertIn("REPORTE PENDIENTE", AGENT_COMMON_CONTRACT)
        self.assertIn("agente especializado", AGENT_COMMON_CONTRACT)
        self.assertIn("Pregúntate qué diría un empleado real", AGENT_COMMON_CONTRACT)
        # Una corrección del pedido no se deriva (bug del transcript real).
        self.assertIn("NO te quita la conversación", AGENT_COMMON_CONTRACT)

    def test_contract_forbids_inventing_business_data(self):
        self.assertIn("No inventes NUNCA precios, enlaces, horarios", AGENT_COMMON_CONTRACT)

    def test_contract_requires_history_awareness(self):
        self.assertIn("NUNCA vuelvas a preguntar un dato que el cliente ya dio", AGENT_COMMON_CONTRACT)
        self.assertIn("sáltate los pasos ya resueltos", AGENT_COMMON_CONTRACT)

    def test_contract_infers_state_from_requests_and_respects_branches(self):
        # Lecciones generales del transcript 2026-07-16: pedir OBTENER algo
        # implica no tenerlo, y la respuesta a una pregunta sí/no elige la
        # rama; nunca se envía el material de la rama contraria.
        self.assertIn("ya te dijo que NO lo tiene", AGENT_COMMON_CONTRACT)
        self.assertIn("RAMAS del proceso", AGENT_COMMON_CONTRACT)
        self.assertIn("NUNCA envíes el material de la rama contraria", AGENT_COMMON_CONTRACT)

    def test_contract_avoids_intent_by_exact_phrase(self):
        self.assertIn("nunca por palabras sueltas ni frases exactas", AGENT_COMMON_CONTRACT)


class SupervisorPromptTests(unittest.TestCase):
    def test_schema_defines_route_and_targets(self):
        self.assertIn('"action": "route|reply|handoff|close|city_invitation"', SUPERVISOR_OUTPUT_SCHEMA)
        self.assertIn("GENERAL|ALQUILER|CLASES|DICTAMEN", SUPERVISOR_OUTPUT_SCHEMA)

    def test_supervisor_owns_cross_cutting_cases(self):
        for owned in ("QUEJA", "WIN", "SALUDO", "AMBIGUO", "VARIOS SERVICIOS"):
            self.assertIn(owned, SUPERVISOR_PROMPT_BODY)
        self.assertIn("no hagas tú esas preguntas ni pidas confirmación antes de enrutar".lower(), SUPERVISOR_PROMPT_BODY.lower())
        self.assertIn("NO vuelvas a enrutar a esa misma área", SUPERVISOR_PROMPT_BODY)

    def test_supervisor_anti_loop_clarify(self):
        self.assertIn("No repitas la misma aclaración", SUPERVISOR_PROMPT_BODY)


class SpecialistPromptTests(unittest.TestCase):
    def test_schema_defines_defer(self):
        self.assertIn('"action": "reply|defer|handoff|close|city_invitation"', SPECIALIST_OUTPUT_SCHEMA)
        self.assertIn("ACCIÓN defer", SPECIALIST_OUTPUT_SCHEMA)

    def test_every_area_has_a_body(self):
        self.assertEqual(set(AREA_PROMPT_BODIES), {"GENERAL", "ALQUILER", "CLASES", "DICTAMEN"})

    def test_alquiler_keeps_transcript_lessons(self):
        body = AREA_PROMPT_BODIES["ALQUILER"]
        self.assertIn("NUNCA asumas el vehículo", body)
        self.assertIn("NUNCA preguntes la subcategoría", body)
        self.assertIn("sigue vigente todo el proceso", body)

    def test_general_defers_rental_phase(self):
        self.assertIn('action="defer"', AREA_PROMPT_BODIES["GENERAL"])
        self.assertIn("city_invitation", AREA_PROMPT_BODIES["GENERAL"])


class HygieneTests(unittest.TestCase):
    def test_prompts_keep_scope_generic_without_catalog_values(self):
        # El conocimiento de negocio variable (precios, sinpe, links, marcas)
        # vive en mensajes.json y el RAG, nunca en las constantes de prompt.
        combined = "\n".join(ALL_PROMPT_PARTS).lower()
        for leaked in ("60023618", "61103205", "colones", "https://", "smart", "spark", "calendly", "casco"):
            self.assertNotIn(leaked, combined)

    def test_prompts_keep_instructions_separate_from_turn_data(self):
        # Instrucciones estáticas (system, cacheables); datos del turno como
        # JSON en el mensaje del usuario. Sin placeholders .format.
        for prompt in ALL_PROMPT_PARTS:
            for placeholder in ("{mensaje}", "{historial}", "{pendiente}", "{conversation_history}"):
                self.assertNotIn(placeholder, prompt)
        self.assertIn("llegan como JSON en el mensaje del usuario", AGENT_COMMON_CONTRACT)
        self.assertIn("llegan como JSON en el mensaje del usuario", FOLLOWUP_AGENT_PROMPT)


class FollowupPromptContractTests(unittest.TestCase):
    def test_prompt_defines_output_and_restraint(self):
        self.assertIn('{"send": true|false', FOLLOWUP_AGENT_PROMPT)
        self.assertIn("CUÁNDO NO ENVIAR", FOLLOWUP_AGENT_PROMPT)
        self.assertIn("no lo presiones", FOLLOWUP_AGENT_PROMPT)
        self.assertIn("No inventes datos", FOLLOWUP_AGENT_PROMPT)

    def test_prompt_keeps_house_style(self):
        self.assertIn("📌 Hola!!!", FOLLOWUP_AGENT_PROMPT)
        self.assertIn("Máximo una pregunta", FOLLOWUP_AGENT_PROMPT)
        self.assertIn("nunca tutees", FOLLOWUP_AGENT_PROMPT)


if __name__ == "__main__":
    unittest.main()
