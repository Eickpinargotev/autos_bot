# Cambios de prompt propuestos — pendientes de aplicar por el usuario

El código ya admite modelos separados, varias consultas por turno y respuestas
parciales con auditoría de las partes pendientes. **Este documento no cambia
los prompts que se ejecutan ni publica versiones en el panel.**

## 1. Dónde se cambia cada parte

| Parte | Ubicación | Forma de aplicar |
|---|---|---|
| Reglas comunes para decidir cuándo consultar | `services/bot_agent/src/core/prompts.py`, `AGENT_COMMON_CONTRACT` | Editar código y redesplegar el bot y workers. |
| Descripción textual de los campos JSON del agente | Mismo archivo, `SUPERVISOR_OUTPUT_SCHEMA` y `SPECIALIST_OUTPUT_SCHEMA` | Editar código. El esquema técnico del API y el validador ya admiten los campos nuevos. |
| Proceso del agente GENERAL | Panel **Agente IA → Prompts → Agente general**; respaldo en `GENERAL_AGENT_BODY` | Crear y activar una versión en el panel. Editar solo el respaldo no reemplaza una versión activa guardada en la base. |
| Reglas y ejemplo JSON de quien redacta con los chunks | `services/bot_agent/src/application/rag_service.py`, método `_answer_prompt` | Editar ese texto en código y redesplegar. No es el prompt editable del agente GENERAL. |

No cambies precios, enlaces ni requisitos en estos textos: esos datos siguen
viviendo en los chunks y fragmentos. Mantén el resto de las reglas de pagos,
quejas, derivación humana, estilo y alcance de cada área.

## 2. Reemplazar las reglas generales de uso del RAG

En `AGENT_COMMON_CONTRACT`, dentro de **REGLAS DE LOS MENSAJES**, busca estas tres líneas consecutivas:

```text
- La etiqueta [[rag]] también va sola como un mensaje: el sistema la reemplaza por la respuesta de la base de conocimiento a "rag_query". Úsala para toda duda informativa (requisitos, costos, citas, trámites, vigencias, COSEVI, renovación, homologación, permisos, reingreso).
- Usa [[rag]] solo para una duda informativa expresada por el cliente; no inventes una pregunta informativa a partir de un dato de estado. Si solo responde tu pregunta o informa su estado, reconócelo y continúa el proceso con texto conversacional.
- Si la respuesta afirma datos del negocio o de un trámite (requisitos, procedimientos, tiempos, credenciales o pasos), usa [[rag]]. No respondas esos datos de memoria; si la base no los especifica, no los inventes.
```

Sustitúyelas por este bloque, en la misma posición:

```text
- Antes de consultar conocimiento, determina la intención del mensaje actual usando la última pregunta del bot y el historial. Esta decisión tiene prioridad sobre la mera presencia de un tema como licencia, precio, cita o dictamen.
- Si el cliente está eligiendo un servicio, identificando su categoría o sede, respondiendo una pregunta pendiente o informando su estado, continúa el siguiente paso del playbook. No conviertas esa respuesta por sí sola en una consulta sobre requisitos, costos o trámites.
- Si además formula una duda informativa real, atiende únicamente esa duda con [[rag]], conservando el estado del proceso. No preguntes nuevamente datos que ya dio.
- La etiqueta [[rag]] va sola como un mensaje y se envía una sola vez en el turno. La búsqueda y la respuesta se basan en rag_query y rag_queries.
- rag_query resume la duda informativa actual. Usa el historial solo para resolver referencias y recuperar datos ya establecidos; no agregues objetivos, servicios ni preguntas que el cliente no pidió.
- rag_queries contiene hasta tres consultas concretas, una por servicio o asunto independiente que el cliente realmente preguntó. Para una sola duda, usa una consulta. Para varias, sepáralas en la lista y no mezcles todos los asuntos en cada búsqueda.
- Conserva las distinciones entre clases prácticas, alquiler, entero del examen, gestión de la cita y dictamen. Sus precios y condiciones no son intercambiables. No amplíes una consulta de precio a requisitos, vigencia o pasos si el cliente no los pidió.
- Si una ambigüedad impide identificar qué servicio se debe consultar y el historial no la resuelve, haz una aclaración breve antes de buscar. No asumas un servicio distinto para completar la pregunta.
- Si no existe una duda informativa que consultar, deja rag_query="" y rag_queries=[] y no envíes [[rag]].
- Toda afirmación nueva sobre datos del negocio o de un trámite debe estar respaldada por los fragmentos autorizados o por [[rag]]. No inventes precios, gratuidad, inclusiones ni requisitos; la ausencia de un dato no equivale a una respuesta negativa ni a un servicio gratuito.
```

Estas reglas se expresan por intención y contexto. No agregues condiciones por
frases literales de los casos de diagnóstico.

## 3. Actualizar las descripciones JSON de ambos agentes

En **ambas** constantes `SUPERVISOR_OUTPUT_SCHEMA` y
`SPECIALIST_OUTPUT_SCHEMA`, reemplaza esta línea:

```text
  "rag_query": "duda informativa a resolver con la base de conocimiento, o vacío",
```

Por estas dos, conservando todos los demás campos:

```text
  "rag_query": "resumen preciso de la duda informativa actual, o vacío si corresponde continuar el playbook",
  "rag_queries": ["hasta tres consultas concretas e independientes; [] si no hay duda informativa"],
```

La descripción entre comillas explica el contrato: no debe convertirse en el
contenido de una consulta real. La lista contiene las preguntas concretas del
turno, no esos textos explicativos.

Ejemplo ilustrativo de un pedido de costo de dos servicios:

```json
{
  "rag_query": "Costo del dictamen médico y de gestionar una cita de prueba práctica A2",
  "rag_queries": [
    "Precio del dictamen médico",
    "Costo de gestionar la cita de prueba práctica A2; si tiene cobro adicional o está incluida"
  ]
}
```

Este ejemplo solo ilustra los dos campos nuevos/relevantes: la respuesta real
del agente debe conservar el JSON completo exigido por su esquema. No afirma
que gestionar la cita sea gratis o esté incluido; busca confirmar ese dato.

## 4. Reforzar el comienzo del proceso GENERAL

En el prompt **activo** del agente general, localiza el paso equivalente a:

```text
- Primer contacto del proceso: envía [[frag:GENERAL.G1]] (presentación + pregunta si ya aprobó el teórico). Si el historial ya lo dice, no lo preguntes: continúa.
```

Sustituye ese paso por:

```text
- Cuando el cliente elige obtener su licencia o pide orientación general para comenzar, identifica primero qué etapa tiene resuelta. La elección del servicio, vehículo o categoría no es por sí sola una pregunta informativa para el RAG.
- Si no consta en el historial si tiene aprobado el examen teórico, envía [[frag:GENERAL.G1]] y deja pending con la confirmación de ese estado. Si ya lo indicó, continúa por la rama correspondiente sin repetir la pregunta.
- Si su mensaje también incluye una pregunta específica sobre un costo, requisito o trámite, resuelve esa duda con [[rag]] sin inventar otras preguntas y conserva el paso del proceso que siga pendiente.
```

No borres las ramas posteriores: teórico no aprobado → CURSO_TEORICO;
teórico aprobado → verificar la cita; cita disponible → área correspondiente.
Si tu versión activa tiene un texto diferente, conserva sus instrucciones de
negocio válidas y reemplaza el paso equivalente, no todo el playbook.

## 5. Permitir respuestas parciales con evidencia

En `_answer_prompt`, busca estas dos líneas de las reglas:

```text
- Responde SOLO con información respaldada por la base de conocimiento de abajo. Si no
  respalda una respuesta completa a la pregunta, devuelve has_answer=false; nunca inventes.
```

Sustitúyelas por:

```text
- Responde SOLO con información respaldada por la base de conocimiento de abajo. El historial sirve para interpretar la consulta, pero no convierte una afirmación anterior del bot en evidencia nueva de precios, requisitos o inclusiones.
- Evalúa por separado cada parte de lo que el cliente preguntó. No descartes una respuesta útil y respaldada porque otra parte carece de información.
- Si puedes responder toda la duda, devuelve has_answer=true, answer con la respuesta y unanswered_queries=[].
- Si solo puedes responder una parte, devuelve has_answer=true y answer con los datos confirmados. Indica con una frase breve qué dato requiere confirmación. En unanswered_queries enumera únicamente las consultas concretas solicitadas por el cliente que siguen sin respaldo.
- Si no puedes responder ninguna parte de forma útil y respaldada, devuelve has_answer=false, answer="" y unanswered_queries=[].
- No supongas que un servicio es gratuito, está incluido o tiene el mismo costo que otro. No calcules ni anuncies un total si falta un componente de ese total.
- No añadas como pendientes requisitos, costos o servicios que el cliente no preguntó. No registres nuevamente como pendiente lo que ya respondiste.
- No menciones campos JSON, chunks ni procesos internos al cliente. No prometas revisiones, contactos ni acciones humanas que no se hayan ejecutado. Expresa únicamente que el dato pendiente requiere confirmación.
```

Con esto `has_answer=true` significa que existe una respuesta útil, que puede
ser parcial. `unanswered_queries` permite al código auditar exclusivamente lo
que falta. El código evita seguir empujando el proceso o agendar recordatorios
del turno mientras quede una parte sin respuesta.

## 6. Actualizar el ejemplo JSON al final del prompt RAG

El método retorna una cadena **f-string**. Al editar el código, conserva las
llaves dobles. Reemplaza el bloque actual por este:

```text
Devuelve JSON estricto:
{{
  "has_answer": true|false,
  "answer": "el mensaje que se enviará literal al cliente",
  "unanswered_queries": ["solo las consultas concretas que siguen sin respaldo; [] si no queda ninguna"]
}}
```

Es una descripción del formato, igual que el ejemplo actual. El JSON real
debe llevar un booleano y una lista de consultas reales, no los textos
explicativos. El esquema técnico ya exige las tres propiedades.

## 7. Comprobar el cambio antes de generalizarlo

- Cliente que elige el servicio solicitado por el bot: debe continuar el
  playbook y no inventar una consulta informativa amplia.
- Cliente que pregunta un precio específico: una búsqueda de ese precio,
  sin agregar vigencia, requisitos ni otros servicios.
- Cliente que pregunta por dos servicios: dos búsquedas, una respuesta;
  cada búsqueda debe verse en el diagnóstico con sus chunks.
- Solo una parte tiene respaldo: debe comunicarla y registrar únicamente
  la consulta pendiente. No inventar un total ni afirmar que el otro servicio
  es gratis.
- Ninguna parte tiene respaldo: mantener el mensaje seguro y el registro de
  pregunta sin respuesta.
- Una corrección de categoría o sede no debe hacer que se repitan preguntas
  ya respondidas ni que se confundan clases con alquiler.

Las pruebas de cableado son deterministas y no llaman a OpenAI. La mejora del
juicio del modelo debe evaluarse después de aplicar estos textos con casos
representativos y paráfrasis, no solo con las frases de los diagnósticos.
