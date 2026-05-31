EXTRACT_AD_INFO_PROMPT = """
Extrae la siguiente información del mensaje del anuncio.
Necesitamos:
- día (fecha en formato dd/mm/2026)
- valor (valor del curso en colones, solo el número)
- hora (hora de inicio en formato 24 horas HH:MM)

Si falta alguno, indica null.
Devuelve un JSON estricto con las claves "dia", "valor", "hora".
Mensaje:
{mensaje}
"""

CATEGORIZATION_PROMPT = """
Analiza el siguiente texto y determina su intención basándote en las siguientes categorías:

1. DICTAMEN: si menciona examen médico, dictamen, prueba médica, cita dictamen, requisitos dictamen, formulario dictamen.
2. CLASES: si menciona clases de manejo, manejo, lecciones, practica, conducción. (Excepto si menciona 'teórico', entonces es GENERAL).
3. ALQUILER: si menciona alquiler, auto, carro, moto, B1, B2, A1...
4. QUEJAS: si menciona queja, molestia, devolucion, problemas o muestra enojo/frustración.
5. WIN: si expresa haber ganado/aprobado/pasado (teórico/práctico). (Excepto si hay negación, entonces es GENERAL).
6. GENERAL: preguntas generales sobre exámenes teóricos, citas, COSEVI, licencia, pedir información, qué hay que hacer.

Devuelve SOLO la palabra de la categoría (DICTAMEN, CLASES, ALQUILER, QUEJAS, WIN, GENERAL).
Texto:
{mensaje}
"""

REPLY_EVALUATION_PROMPT = """
Evalúa la respuesta del usuario dentro de una máquina de estados de una escuela de manejo.

Flujo actual: {flujo}
Nodo actual: {nodo}
Última pregunta enviada:
{pregunta}

Mensaje del usuario:
{mensaje}

Devuelve JSON estricto:
{{
  "intent": "positive|negative|city|license|question|complaint|unknown",
  "value": "liberia|other|car|moto|b2|b3|b4|bus|",
  "has_off_flow_question": true|false,
  "off_flow_question": "pregunta lateral del usuario o vacío"
}}

Reglas:
- Si el usuario responde sí/ya tiene/listo/correcto, intent positive.
- Si responde no/todavía no/aún no, intent negative.
- No clasifiques como positive solo por la palabra "si" cuando forma parte de "saber si", "y si", "si pierdo", "si puedo" o una pregunta.
- Si menciona Liberia, intent city value liberia.
- Si menciona otra sede o ciudad, intent city value other.
- Si menciona B1/carro/auto, intent license value car.
- Si menciona moto/A1/A2/A3, intent license value moto.
- Si menciona B2, B3, B4/trailer o bus/C2, usa el value correspondiente.
- Si hace una pregunta fuera de la última pregunta enviada y no responde la pregunta del flujo, intent question.
- Si el mensaje responde la pregunta del flujo y además incluye otra pregunta, conserva la respuesta en intent/value, pon has_off_flow_question=true y copia la pregunta lateral en off_flow_question.
- Si muestra enojo, queja, insulto, frustración o devolución, intent complaint.
"""

OFF_FLOW_QUESTION_PROMPT = """
Determina si el mensaje del usuario es una pregunta fuera del flujo actual.

Flujo: {flujo}
Nodo: {nodo}
Última pregunta enviada:
{pregunta}

Mensaje:
{mensaje}

Devuelve JSON estricto con:
{{"is_question": true|false}}
"""

REPORT_SUMMARY_PROMPT = """
Resume en una oración corta la duda o problema del usuario para que un asesor lo contacte.

Flujo: {flujo}
Nodo: {nodo}
Mensaje:
{mensaje}

Devuelve JSON estricto:
{{"resumen": "texto breve"}}
"""
