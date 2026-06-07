import unittest

from src.core.prompts import RECEPTION_AGENT_PROMPT, REPLY_EVALUATION_PROMPT


class PromptContractTests(unittest.TestCase):
    def test_reception_prompt_keeps_intake_focused_on_flow_selection(self):
        self.assertIn("Tu objetivo principal en intake es elegir el flujo correcto", RECEPTION_AGENT_PROMPT)
        self.assertIn('usa action="start_flow" sin hacer preguntas extra', RECEPTION_AGENT_PROMPT)
        self.assertIn("intención o necesidad", RECEPTION_AGENT_PROMPT)
        self.assertIn("No hagas preguntas previas que dupliquen preguntas del flujo formal", RECEPTION_AGENT_PROMPT)
        self.assertIn("duda informativa", RECEPTION_AGENT_PROMPT)

    def test_reception_prompt_confirms_before_flow_when_initial_message_has_real_question(self):
        self.assertIn("mezcla intención comercial con una duda informativa real", RECEPTION_AGENT_PROMPT)
        self.assertIn('no inicies el flujo todavía', RECEPTION_AGENT_PROMPT)
        self.assertIn('action="answer_and_clarify"', RECEPTION_AGENT_PROMPT)
        self.assertIn("ya se respondió una duda y se le preguntó si desea recibir ayuda", RECEPTION_AGENT_PROMPT)
        self.assertIn('action="start_flow"', RECEPTION_AGENT_PROMPT)
        self.assertIn('usa action="close"', RECEPTION_AGENT_PROMPT)
        self.assertIn("la cortesía no cancela la confirmación", RECEPTION_AGENT_PROMPT)

    def test_reception_prompt_keeps_prompt_rules_out_of_business_knowledge(self):
        self.assertIn('No uses prompt_rules para conocimiento operativo o de negocio', RECEPTION_AGENT_PROMPT)
        self.assertIn("Si la pregunta requiere datos, políticas, disponibilidad", RECEPTION_AGENT_PROMPT)
        self.assertIn('usa answer_source="rag"', RECEPTION_AGENT_PROMPT)

    def test_reply_prompt_distinguishes_intent_from_side_questions(self):
        self.assertIn("Las expresiones de intención comercial o solicitud de ayuda", REPLY_EVALUATION_PROMPT)
        self.assertIn("clasifica la respuesta principal del flujo", REPLY_EVALUATION_PROMPT)
        self.assertIn("duda real que requiere una respuesta independiente", REPLY_EVALUATION_PROMPT)
        self.assertIn("intent decline", REPLY_EVALUATION_PROMPT)
        self.assertIn("intent change_intent", REPLY_EVALUATION_PROMPT)

    def test_prompt_contract_avoids_log_specific_terms(self):
        combined = f"{RECEPTION_AGENT_PROMPT}\n{REPLY_EVALUATION_PROMPT}"
        self.assertNotIn("casco", combined.lower())
        self.assertNotIn("programar cita", combined.lower())
        self.assertNotIn("qué pasa si pierde", combined.lower())


if __name__ == "__main__":
    unittest.main()
