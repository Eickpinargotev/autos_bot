# Reglas Para El Agente De Recepción

## Principio Principal
El bot no debe intentar entender conversaciones humanas con regex, excepciones o listas crecientes de palabras clave. Para lenguaje natural, dudas mezcladas con intención comercial, errores de escritura, contexto y ambigüedad, debe usarse un nodo LangGraph de recepción con un prompt estructurado y salida JSON.

El agente no debe modificar prompts existentes. Puede crear un prompt nuevo si no existe y la tarea lo requiere, pero la corrección o ajuste de prompts existentes queda reservada para el dueño del proyecto.

El regex y el hardcode de mensajes del cliente son el peor enemigo de una IA conversacional de nivel humano: la diversidad de respuestas humanas es demasiado grande para cubrirla con patrones. En cualquier parte del flujo que requiera interpretación conversacional, intención, respuesta a una pregunta, duda lateral, confirmación, rechazo, cambio de intención o avance comercial, debe usarse un agente con salida estructurada. No agregar regex, listas de palabras, ejemplos observados o frases literales del cliente para decidir esos casos.

Las reglas duras solo se permiten para comportamiento sistémico o de seguridad:
- comandos exactos como `/d`, `/block` o keywords exactas de sistema
- bloqueos, contexto de publicidad, recordatorios y estados técnicos
- señales administrativas sensibles como comprobantes, pagos realizados, revisión manual o solicitud explícita de asesor
- quejas fuertes, insultos, enojo claro o casos donde conviene bloquear y reportar
- validación defensiva de la salida JSON del agente

## Qué Debe Hacer El Nodo De Recepción
El nodo de recepción debe analizar el mensaje completo y devolver JSON estricto. No debe clasificar por una palabra aislada. Debe entender si el usuario:
- hizo una pregunta
- expresó una intención de flujo
- mezcló una pregunta con una intención de flujo
- necesita aclaración
- requiere asesor humano
- debe continuar un intake anterior

Orden de decisión:
1. Si hay queja fuerte, reclamo, insulto o caso sensible, derivar o iniciar `QUEJA` según corresponda.
2. Si hay pago realizado, comprobante, revisión de dinero, promesa previa, estado de trámite o solicitud manual, derivar a asesor.
3. Si hay pregunta, intentar responder primero usando información incluida en el prompt.
4. Si la respuesta no está en el prompt, consultar RAG.
5. Si RAG no tiene evidencia suficiente y la duda importa para avanzar, derivar a asesor; antes informar al cliente que un asesor le atenderá.
6. Si además hay intención clara de flujo, responder la pregunta y entrar al flujo correspondiente.
7. Si no hay intención clara de flujo, responder la pregunta y hacer una sola pregunta abierta para descubrir el servicio.
8. Si no hay pregunta y la intención está clara, iniciar el flujo.
9. Si no hay pregunta ni intención clara, pedir una aclaración breve.

## Flujos Disponibles
- `GENERAL`: proceso de licencia, teórico, prueba de manejo sin alquiler explícito, citas, COSEVI, MOPT, agendamiento, información general.
- `Alquiler`: intención explícita de alquiler/renta/reservación de vehículo para prueba de manejo.
- `CLASES`: clases prácticas, lecciones, práctica de conducción; no usar si el contexto es curso teórico.
- `DICTAMEN`: examen médico, dictamen, prueba médica, cita o formulario de dictamen.
- `QUEJA`: molestia, reclamo, devolución, mal servicio, enojo o frustración fuerte.
- `WIN`: usuario informa que ganó, aprobó o pasó una prueba/examen; negaciones no son `WIN`.

## Salida JSON Esperada
El agente debe devolver una estructura equivalente a:

```json
{
  "action": "answer_and_start_flow|answer_and_clarify|start_flow|clarify|handoff",
  "flow": "GENERAL|Alquiler|CLASES|DICTAMEN|QUEJA|WIN|",
  "has_question": true,
  "question": "pregunta detectada o vacío",
  "answer_source": "prompt_rules|rag|none",
  "answer": "respuesta breve para el cliente o vacío",
  "clarifying_question": "una sola pregunta breve o vacío",
  "handoff_reason": "resumen interno si se deriva o vacío",
  "confidence": 0.0
}
```

Reglas de salida:
- `answer` solo debe incluir información respaldada por prompt o RAG.
- `flow` debe estar vacío si no se debe iniciar flujo.
- `handoff_reason` es interno y no se envía literal al cliente.
- Si `action=handoff`, el mensaje al cliente debe ser breve: “En un momento le escribirá un agente especializado para atender su caso.”
- Si se requiere aclarar servicio, usar una pregunta amplia y natural, por ejemplo: “¿Lo necesita para prueba de manejo, clases, alquiler, dictamen o información de licencia?”

## Anti Sobreoptimización
No agregar regex o casos especiales para entender frases humanas como:
- errores de escritura comunes
- preguntas sin signos de interrogación
- ejemplos específicos de logs
- palabras aisladas extraídas de casos observados

Esos casos deben resolverse con el agente de recepción. Si un log nuevo falla, convertirlo en prueba de regresión del comportamiento esperado, no en una excepción textual.

Solo agregar reglas duras si son invariantes de negocio o de seguridad y no dependen de interpretar conversación natural.

## Anti Prompt Overfitting
Los prompts también pueden sobreajustarse. No optimizar el prompt para “pasar” ejemplos puntuales de logs, benchmarks o pruebas si eso vuelve la instrucción menos general.

Evitar:
- copiar frases reales del usuario al prompt como reglas
- convertir una pregunta específica en una categoría fija de decisión
- agregar listas crecientes de temas para forzar cuándo usar RAG
- escribir tests que solo validan que una frase exacta del caso fallido quedó dentro del prompt
- mezclar instrucciones de routing con respuestas de negocio que deberían venir de RAG o de una fuente de datos

Preferir:
- describir la capacidad general que el agente debe ejercer, por ejemplo distinguir intención comercial, pregunta informativa, respuesta a una pregunta del flujo y duda lateral
- definir criterios abstractos de decisión, no ejemplos cerrados
- mantener los ejemplos solo si ilustran una clase amplia de comportamiento y no se vuelven condición necesaria
- probar comportamiento esperado con escenarios variados, no presencia de frases literales
- revisar si cada nueva instrucción mejora la generalización o solo arregla un caso observado

Señales de prompt overfitting:
- el prompt crece con excepciones después de cada log
- aparecen palabras del log dentro de reglas del prompt
- el agente funciona en los casos vistos pero falla con formulaciones equivalentes
- `prompt_rules` empieza a reemplazar a RAG para conocimiento operativo
- el prompt contiene detalles de productos, disponibilidad, políticas o respuestas que pertenecen a la base de conocimiento

## Plan De Migración Recomendado
1. Crear un modelo/dataclass `ReceptionDecision` con el JSON anterior.
2. Crear un prompt `RECEPTION_AGENT_PROMPT` en `src/core/prompts.py`.
3. Crear un servicio pequeño, por ejemplo `ReceptionAgent`, que llame al modelo y valide JSON.
4. Modificar `FlowGraphRunner._handle_intake` para llamar primero al agente de recepción.
5. Mover RAG a una segunda etapa: solo consultarlo cuando el agente indique que existe una pregunta sin respuesta suficiente en el prompt.
6. Mantener `ResponseClassifier` solo para respuestas dentro de nodos existentes, o reducirlo progresivamente.
7. Cubrir logs reales con pruebas de regresión por comportamiento, no por regex específica.
