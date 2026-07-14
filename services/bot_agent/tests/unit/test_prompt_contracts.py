import unittest

from src.core.prompts import RECEPTION_AGENT_PROMPT, REPLY_EVALUATION_PROMPT


class PromptContractTests(unittest.TestCase):
    def test_reception_prompt_routes_by_intent_and_questions(self):
        self.assertIn('usa action="start_flow" sin hacer preguntas extra', RECEPTION_AGENT_PROMPT)
        self.assertIn("No hagas preguntas previas que dupliquen preguntas del flujo formal", RECEPTION_AGENT_PROMPT)
        self.assertIn("duda informativa", RECEPTION_AGENT_PROMPT)

    def test_reception_prompt_confirms_before_flow_when_initial_message_has_real_question(self):
        self.assertIn("mezcla intención comercial con una duda informativa real", RECEPTION_AGENT_PROMPT)
        self.assertIn("no inicies el flujo todavía", RECEPTION_AGENT_PROMPT)
        self.assertIn('action="answer_and_clarify"', RECEPTION_AGENT_PROMPT)
        self.assertIn('action="start_flow"', RECEPTION_AGENT_PROMPT)
        self.assertIn('usa action="close"', RECEPTION_AGENT_PROMPT)
        self.assertIn("la cortesía no cancela la confirmación", RECEPTION_AGENT_PROMPT)

    def test_reception_prompt_keeps_prompt_rules_out_of_business_knowledge(self):
        self.assertIn("No uses prompt_rules para conocimiento operativo o de negocio", RECEPTION_AGENT_PROMPT)
        self.assertIn('usa answer_source="rag"', RECEPTION_AGENT_PROMPT)

    def test_reception_prompt_treats_greetings_without_handoff_or_rag(self):
        self.assertIn("solo un saludo o cortesía", RECEPTION_AGENT_PROMPT)
        self.assertIn("un saludo no es una pregunta informativa", RECEPTION_AGENT_PROMPT)

    def test_reply_prompt_distinguishes_intent_from_side_questions(self):
        self.assertIn("Las expresiones de intención comercial o solicitud de ayuda", REPLY_EVALUATION_PROMPT)
        self.assertIn("clasifica la respuesta principal del flujo", REPLY_EVALUATION_PROMPT)
        self.assertIn("duda real que requiere una respuesta independiente", REPLY_EVALUATION_PROMPT)
        self.assertIn("intent decline", REPLY_EVALUATION_PROMPT)
        self.assertIn("intent change_intent", REPLY_EVALUATION_PROMPT)

    def test_reply_prompt_delegates_greeting_and_handoff_to_the_model(self):
        self.assertIn("intent greeting", REPLY_EVALUATION_PROMPT)
        self.assertIn("intent human_handoff", REPLY_EVALUATION_PROMPT)
        self.assertIn("Nunca trates un saludo o una simple cortesía como pregunta", REPLY_EVALUATION_PROMPT)
        self.assertIn("human_handoff|greeting|unknown", REPLY_EVALUATION_PROMPT)

    def test_prompt_contract_avoids_log_specific_terms(self):
        combined = f"{RECEPTION_AGENT_PROMPT}\n{REPLY_EVALUATION_PROMPT}"
        self.assertNotIn("casco", combined.lower())
        self.assertNotIn("programar cita", combined.lower())
        self.assertNotIn("qué pasa si pierde", combined.lower())

    def test_reception_prompt_exposes_rag_scope_and_handoff_boundary(self):
        # El prompt no debe estar "ciego": conoce qué temas cubre el RAG (categorías,
        # no datos) y cuándo derivar a un humano por estar fuera de alcance.
        self.assertIn("ALCANCE DEL CONOCIMIENTO", RECEPTION_AGENT_PROMPT)
        self.assertIn("Temas que el RAG SÍ puede responder", RECEPTION_AGENT_PROMPT)
        self.assertIn("Fuera de alcance", RECEPTION_AGENT_PROMPT)
        self.assertIn("multas de tránsito", RECEPTION_AGENT_PROMPT)
        # Los tres modos de atención: ejecutar (flujo) / responder (rag) / derivar.
        self.assertIn("answer_source=rag", RECEPTION_AGENT_PROMPT)
        self.assertIn("Mencionar el tema NO es querer ejecutarlo", RECEPTION_AGENT_PROMPT)

    def test_reply_prompt_references_known_topics_for_side_questions(self):
        self.assertIn("Como referencia de qué es una duda informativa real", REPLY_EVALUATION_PROMPT)

    def test_reception_prompt_distinguishes_win_from_theory_exam(self):
        # Aprobar el TEÓRICO es parte del proceso (GENERAL), no WIN (que es la
        # prueba de manejo / examen práctico final). Evita el misrouting a WIN.
        self.assertIn("Aprobar el examen TEÓRICO no es WIN", RECEPTION_AGENT_PROMPT)

    def test_reception_prompt_knows_admin_tramites_have_no_flow(self):
        # renovación/homologación/etc. no tienen nodo de entrada en el router:
        # se responden por RAG y se derivan a humano para ejecutar.
        self.assertIn("NO tienen flujo", RECEPTION_AGENT_PROMPT)
        self.assertIn("No los fuerces dentro de GENERAL", RECEPTION_AGENT_PROMPT)

    def test_reception_prompt_does_not_pre_ask_flow_qualifiers(self):
        self.assertIn("No preguntes tú esos datos", RECEPTION_AGENT_PROMPT)

    def test_reception_prompt_treats_bare_menu_selection_as_explicit_intent(self):
        # Si el bot ya ofreció opciones nombradas y el cliente responde con una
        # sola palabra que nombra una de ellas (sin verbo), cuenta como
        # intención explícita (start_flow), no como contexto ambiguo que siga
        # aclarando o escale a handoff.
        self.assertIn("Seleccionar una opción ya ofrecida es concretar", RECEPTION_AGENT_PROMPT)

    def test_reception_clarify_is_adaptive_not_a_fixed_phrase(self):
        # Evita el overfitting: el modelo no debe copiar un ejemplo literal de
        # pregunta de aclaración (causaba el bucle de la misma frase repetida).
        self.assertIn("No copies una frase fija", RECEPTION_AGENT_PROMPT)
        self.assertIn("NO repitas la misma pregunta", RECEPTION_AGENT_PROMPT)
        # El ejemplo literal que el modelo loro-repetía ya no debe estar como guion.
        self.assertNotIn(
            "¿está buscando ayuda con su licencia, dictamen médico, clases de manejo o alquiler de vehículo?",
            RECEPTION_AGENT_PROMPT,
        )

    def test_prompts_keep_scope_generic_without_catalog_values(self):
        # El mapa de alcance debe ser de CATEGORÍAS, nunca datos del catálogo
        # (precios, sinpe, links, marcas de vehículo). Refuerza CLAUDE.md §6.
        combined = f"{RECEPTION_AGENT_PROMPT}\n{REPLY_EVALUATION_PROMPT}".lower()
        for leaked in ("60023618", "colones", "https://", "smart", "spark", "calendly"):
            self.assertNotIn(leaked, combined)

    def test_prompts_keep_instructions_separate_from_turn_data(self):
        # Las instrucciones son estáticas (van en el mensaje system, cacheables);
        # los datos del turno llegan como JSON en el mensaje del usuario. Por eso
        # los prompts NO deben interpolar datos del turno (placeholders .format).
        for prompt in (RECEPTION_AGENT_PROMPT, REPLY_EVALUATION_PROMPT):
            for placeholder in ("{mensaje}", "{flujo}", "{nodo}", "{pregunta}", "{conversation_history}"):
                self.assertNotIn(placeholder, prompt)
            self.assertIn("llegan como JSON en el mensaje del usuario", prompt)


if __name__ == "__main__":
    unittest.main()
