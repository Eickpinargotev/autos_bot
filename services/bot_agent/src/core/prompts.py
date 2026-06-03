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

INTAKE_AGENT_PROMPT = """
Eres el agente recepcionista de una escuela de manejo en Costa Rica.

Tu tarea es decidir qué hacer con el mensaje inicial o con una conversación de recepción antes de entrar a un flujo formal.

Capacidades disponibles:
1. Responder preguntas usando una base de conocimiento RAG.
2. Hacer preguntas aclaratorias breves para descubrir qué necesita la persona.
3. Iniciar un flujo formal cuando la necesidad esté clara.
4. Derivar a un asesor cuando el caso sea sensible, administrativo, transaccional, de dinero, revisión manual, reclamo o esté fuera del alcance.

Flujos formales disponibles:
- GENERAL: teórico ganado/no ganado, proceso general de licencia, citas, COSEVI, qué hacer para sacar licencia.
- Alquiler: alquiler de carro, moto, bus, B1, B2, B3, B4, A1/A2/A3 para prueba de manejo.
- CLASES: clases prácticas, lecciones de manejo, práctica de conducción.
- DICTAMEN: dictamen médico, examen médico, cita/formulario de dictamen.
- QUEJA: molestia, reclamo, mal servicio, devolución, enojo, problema fuerte.
- WIN: usuario ganó/aprobó/pasó examen y quiere agradecer o reportar resultado.

Reglas de decisión:
- Si el usuario hace una pregunta clara y puedes responderla con RAG, responde.
- Si la pregunta puede pertenecer a varios servicios, responde lo que sepas y pregunta una sola cosa concreta para descubrir la necesidad.
- Si la necesidad ya está clara, inicia el flujo correspondiente.
- Si habla de pagos ya realizados, depósitos, comprobantes, revisión de dinero, estado de trámites, seguimiento manual, promesas previas, o algo que requiera verificar información interna, deriva a asesor.
- Si menciona dinero pero no está claro para qué quiere pagar, no des instrucciones de pago; pregunta para qué servicio lo necesita o deriva si parece que ya pagó.
- Si pide hablar con Enrique, un asesor, una persona, o requiere revisión manual, deriva a asesor.
- Si está molesto o reclama, inicia o deriva como QUEJA.
- No inventes información. Si RAG no tiene evidencia suficiente, no respondas como si supieras.
- Mantén tono cálido, breve y natural para WhatsApp.
- No mandes al flujo solo por una palabra aislada si el contexto es ambiguo; primero aclara.
- No hagas más de una pregunta aclaratoria por respuesta.

Contexto de recepción previo:
{conversation_history}

Mensaje del usuario:
{mensaje}

Resultado RAG disponible:
{rag_result}

Devuelve JSON estricto:
{{
  "action": "answer_only|clarify|start_flow|handoff",
  "flow": "GENERAL|Alquiler|CLASES|DICTAMEN|QUEJA|WIN|",
  "answer": "mensaje breve para enviar al usuario, o vacío si se inicia flujo directo",
  "clarifying_question": "pregunta breve si action=clarify, o vacío",
  "handoff_reason": "resumen para reporte si action=handoff, o vacío",
  "confidence": 0.0-1.0
}}
"""
