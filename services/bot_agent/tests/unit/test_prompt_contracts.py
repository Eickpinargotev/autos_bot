import unittest

from src.core.prompts import FOLLOWUP_AGENT_PROMPT, UNIFIED_AGENT_PROMPT


class UnifiedAgentPromptContractTests(unittest.TestCase):
    """Contratos del prompt del agente único.

    Frases clave que el pipeline y los guardrails asumen presentes. Si se
    reescribe el prompt hay que conservarlas o actualizar este test de forma
    deliberada (CLAUDE.md §6/§7).
    """

    def test_prompt_defines_actions_and_tokens(self):
        self.assertIn('"action": "reply|handoff|close|city_invitation"', UNIFIED_AGENT_PROMPT)
        self.assertIn("[[frag:ID]]", UNIFIED_AGENT_PROMPT)
        self.assertIn("[[rag]]", UNIFIED_AGENT_PROMPT)
        self.assertIn("Nunca reescribas, resumas ni parafrasees el contenido de un fragmento", UNIFIED_AGENT_PROMPT)

    def test_prompt_priorities_cover_escalation_and_handoff(self):
        self.assertIn("QUEJA FUERTE O INSATISFACCIÓN", UNIFIED_AGENT_PROMPT)
        self.assertIn("CASO PARA HUMANO", UNIFIED_AGENT_PROMPT)
        self.assertIn("REPORTE PENDIENTE", UNIFIED_AGENT_PROMPT)
        self.assertIn("agente especializado", UNIFIED_AGENT_PROMPT)
        self.assertIn("Pregúntate qué diría un empleado real", UNIFIED_AGENT_PROMPT)

    def test_prompt_forbids_inventing_business_data(self):
        self.assertIn("No inventes NUNCA precios, enlaces, horarios", UNIFIED_AGENT_PROMPT)

    def test_prompt_requires_history_awareness(self):
        # El corazón del modelo único: no repreguntar datos ya dados y poder
        # saltar pasos (el caso "quiero alquilar una moto").
        self.assertIn("NUNCA vuelvas a preguntar un dato que el cliente ya dio", UNIFIED_AGENT_PROMPT)
        self.assertIn("sáltate los pasos ya resueltos", UNIFIED_AGENT_PROMPT)

    def test_prompt_has_anti_loop_rule(self):
        self.assertIn("No repitas la misma aclaración", UNIFIED_AGENT_PROMPT)

    def test_prompt_avoids_intent_by_exact_phrase(self):
        self.assertIn("nunca por palabras sueltas ni frases exactas", UNIFIED_AGENT_PROMPT)

    def test_prompt_keeps_playbooks_by_intention(self):
        for playbook in ("LICENCIA", "ALQUILER", "CLASES", "DICTAMEN", "WIN", "CURSO TEÓRICO POR CIUDAD"):
            self.assertIn(playbook, UNIFIED_AGENT_PROMPT)

    def test_prompts_keep_scope_generic_without_catalog_values(self):
        # El conocimiento de negocio variable (precios, sinpe, links, marcas)
        # vive en mensajes.json y el RAG, nunca en la constante del prompt.
        combined = f"{UNIFIED_AGENT_PROMPT}\n{FOLLOWUP_AGENT_PROMPT}".lower()
        for leaked in ("60023618", "61103205", "colones", "https://", "smart", "spark", "calendly", "casco"):
            self.assertNotIn(leaked, combined)

    def test_prompts_keep_instructions_separate_from_turn_data(self):
        # Instrucciones estáticas (system, cacheables); datos del turno como
        # JSON en el mensaje del usuario. Sin placeholders .format.
        for prompt in (UNIFIED_AGENT_PROMPT, FOLLOWUP_AGENT_PROMPT):
            for placeholder in ("{mensaje}", "{historial}", "{pendiente}", "{conversation_history}"):
                self.assertNotIn(placeholder, prompt)
            self.assertIn("llegan como JSON en el mensaje del usuario", prompt)


class FollowupPromptContractTests(unittest.TestCase):
    def test_prompt_defines_output_and_restraint(self):
        self.assertIn('{"send": true|false', FOLLOWUP_AGENT_PROMPT)
        self.assertIn("CUÁNDO NO ENVIAR", FOLLOWUP_AGENT_PROMPT)
        self.assertIn("no lo presiones", FOLLOWUP_AGENT_PROMPT)
        self.assertIn("No inventes datos", FOLLOWUP_AGENT_PROMPT)

    def test_prompt_keeps_house_style(self):
        self.assertIn("📌 Hola!!!", FOLLOWUP_AGENT_PROMPT)
        self.assertIn("Máximo una pregunta", FOLLOWUP_AGENT_PROMPT)


if __name__ == "__main__":
    unittest.main()
