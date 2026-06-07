# Conversation Evals

`conversation_evals` es la herramienta de evaluación conversacional del FSM. Sirve para capturar turnos reales humano-bot como `shots`, revisarlos cuando algo salió mal, y luego simular ese mismo punto del flujo para comprobar si una optimización de prompt, routing, RAG o lógica corrigió el problema.

La idea central es simple: cuando un usuario escribe y el bot responde, el sistema guarda una foto reproducible de ese turno.

## Para Que Sirve

Esta herramienta ayuda a responder preguntas como:

- En qué estado estaba el cliente cuando ocurrió el error.
- Qué escribió el cliente.
- Qué historial compacto cliente-bot veía el agente.
- Qué herramientas usó el bot.
- Con qué input llamó esas herramientas.
- Qué output devolvieron las herramientas.
- Qué mensajes terminó enviando el bot.
- A qué estado quedó el FSM después del turno.
- Qué comportamiento esperábamos realmente.

No está pensada para guardar toda la conversación con timestamps, entregas, estados de delivery o datos operativos externos. Está pensada para depurar comportamiento agentico y crear casos reproducibles.

## Donde Vive

La herramienta tiene dos partes:

- Captura en runtime: [conversation_shots.py](/Users/erick/Desktop/HOME/PROYECTOS/PERSONAL/GERMAN/GERMAN/AUTOS/services/bot_agent/src/infrastructure/evals/conversation_shots.py)
- Simulación y evaluación: esta carpeta, [conversation_evals](/Users/erick/Desktop/HOME/PROYECTOS/PERSONAL/GERMAN/GERMAN/AUTOS/services/bot_agent/tests/conversation_evals)

Archivos principales:

- `schemas.py`: modelos Pydantic para shots, expectativas y resultados.
- `runner.py`: convierte un shot en un caso simulable y ejecuta `FlowGraphRunner`.
- `judges.py`: juez LLM opcional para evaluar calidad semántica.
- `test_deterministic_evals.py`: prueba obligatoria de replay/simulación sin LLM.
- `test_llm_judge_evals.py`: prueba opcional con juez LLM.

## Como Se Captura Un Shot

La captura ocurre alrededor de `process_buffered_messages`, que es el punto donde se procesa un turno humano-bot real:

1. El usuario escribe.
2. El mensaje entra al buffer.
3. Celery ejecuta `process_buffered_messages`.
4. Se lee el `ConversationState` antes del FSM.
5. Se activa `ShotTraceCollector`.
6. Se ejecuta `process_fsm`.
7. Las herramientas llamadas durante el FSM quedan registradas en orden.
8. Se lee el `ConversationState` después del FSM.
9. Se construye el shot.
10. Se guarda en NocoDB.
11. Después de guardar el shot, Celery envía los mensajes del bot.

Importante: en la implementación actual el bot no envía mensajes físicamente mientras `process_fsm` está ejecutando herramientas. Primero resuelve herramientas y estado, luego devuelve `bot_replies`, y después Celery envía esos mensajes en orden.

## Que Se Guarda En NocoDB

Cada shot se guarda en una tabla NocoDB con estas columnas:

- `fecha_hora`: fecha y hora del shot en formato `YYYY-MM-DD HH:MM:SS`.
- `id_user`: id del usuario en Telegram o WhatsApp.
- `chanel`: canal de origen. Está escrito así porque la tabla fue definida con ese nombre.
- `reviewed`: checkbox para filtrar shots que ya fueron revisados por una persona. Los shots nuevos se crean con `false`.
- `json`: JSON del shot.

Variable de configuración:

```bash
NOCODB_CONVERSATION_SHOTS_URL=...
```

Si esta URL no existe o NocoDB falla, el bot no debe romperse. El sistema solo imprime/loguea el error y sigue respondiendo.

## Formato Del JSON

El JSON no repite `fecha_hora`, `id_user` ni `chanel`, porque esos datos ya están en columnas de NocoDB. El JSON guarda solo la escena reproducible:

```json
{
  "state_before": {
    "flow": "GENERAL",
    "node": "G35",
    "last_question": "Donde es su prueba de manejo???",
    "awaiting_reply": true
  },
  "history": [
    {
      "user": "hola necesito licencia",
      "bot": ["Ya tiene el teórico ganado???"]
    }
  ],
  "turn": {
    "user_message": "quiero saber si puedo llevar acompañante",
    "bot_replies": [
      "Puede consultarlo con el asesor.",
      "Donde es su prueba de manejo???"
    ],
    "events": [
      {
        "type": "user_message",
        "order": 0,
        "text": "quiero saber si puedo llevar acompañante"
      },
      {
        "type": "tool_call",
        "order": 1,
        "tool_name": "rag.answer_question",
        "status": "success",
        "input": {
          "question": "quiero saber si puedo llevar acompañante",
          "context": "GENERAL.G35"
        },
        "output": {
          "has_answer": true,
          "sources": [
            {
              "content": "Pregunta: Puede llevar acompañante?\nRespuesta: Puede consultarlo con el asesor según el trámite.",
              "score": 0.8123,
              "source_id": "nocodb:mlk30zxjzj4lfd8:abc"
            }
          ]
        },
        "error": "",
        "duration_ms": 123
      },
      {
        "type": "bot_message",
        "order": 2,
        "text": "Puede consultarlo con el asesor."
      },
      {
        "type": "bot_message",
        "order": 3,
        "text": "Donde es su prueba de manejo???"
      }
    ]
  },
  "state_after": {
    "flow": "GENERAL",
    "node": "G35",
    "last_question": "Donde es su prueba de manejo???",
    "awaiting_reply": true
  },
  "review": {
    "status": "unreviewed",
    "tags": [],
    "observed_error": "",
    "expected_behavior": "",
    "notes": ""
  }
}
```

## Por Que `flow` Y `node` Se Guardan Juntos

Aunque el nodo a veces parece identificar el flujo, por ejemplo `D1` para dictamen, el FSM trabaja con la pareja `flow + node`. Guardar ambos en `state_before` y `state_after` evita inferencias frágiles cuando se simula el shot.

Para replay, `flow` y `node` permiten reconstruir el `ConversationState` inicial de forma directa.

El historial previo no guarda `flow`, `node`, herramientas ni timestamps. Solo guarda pares `user` / `bot`, porque su función es dar contexto de conversación, no explicar la ejecución interna pasada.

## Que No Se Captura Como Shot

Solo se capturan turnos iniciados por una persona y procesados por `process_buffered_messages`.

No generan shots nuevos:

- `send_flow_reminder`
- `send_ad_reminder`
- `send_keyword_reminder`
- `send_single_message`
- secuencias programadas
- recordatorios automáticos de Celery

Estos mensajes pueden aparecer en `history` si ya forman parte del contexto que ve el agente, pero no crean un shot propio porque no son una interacción humano-bot iniciada por un mensaje humano.

## Como Leer `turn.events`

`turn.events` es la línea de tiempo del turno.

Tipos actuales:

- `user_message`: mensaje humano que inició el turno.
- `tool_call`: herramienta interna o externa usada por el bot.
- `bot_message`: mensaje que el bot terminó enviando al usuario.

El campo `order` indica el orden del evento dentro del turno.

En la implementación actual, normalmente verás:

```text
user_message -> tool_call(s) -> bot_message(s)
```

Si más adelante el sistema permite enviar mensajes durante el procesamiento antes de terminar el FSM, esta estructura ya permite representar eventos intercalados.

## Herramientas Registradas

Los eventos `tool_call` pueden incluir herramientas como:

- `reception.decide`
- `response_classifier.classify_reply`
- `rag.answer_question`
- `unanswered_question.create`
- `publicidad.handle_invitation_by_city`
- llamadas internas de `PublicidadService`
- acciones de reporte/bloqueo cuando pasan por el runner o logging correspondiente

El input y output se sanitizan:

- textos largos se truncan
- tokens y headers se reemplazan por `[redacted]`
- listas y diccionarios grandes se recortan
- no se guardan payloads completos sensibles

Cuando una herramienta RAG devuelve `sources`, cada fuente debe ser legible para depuración. Por eso se guarda el contenido recuperado del chunk en `content`, el puntaje en `score` y el identificador técnico en `source_id` solo como trazabilidad:

```json
{
  "sources": [
    {
      "content": "Pregunta: Atienden domingos?\nRespuesta: Atendemos según disponibilidad.",
      "score": 0.8123,
      "source_id": "nocodb:mlk30zxjzj4lfd8:abc"
    }
  ]
}
```

El prefijo `nocodb:` no significa que la respuesta se busque en NocoDB en vivo. Significa que ese chunk tuvo origen en NocoDB, fue sincronizado a Qdrant y luego recuperado por búsqueda vectorial.

## Como Revisar Un Shot

Cuando encuentres un shot con problema, edita el bloque `review` dentro del JSON y activa el checkbox `reviewed` en NocoDB.

```json
{
  "status": "reviewed",
  "tags": ["question_before_answer", "rag_required", "bad_resume"],
  "observed_error": "El bot no respondió la pregunta del usuario y avanzó el flujo.",
  "expected_behavior": "Debía responder con RAG y retomar la última pregunta pendiente sin avanzar de estado.",
  "notes": "Caso útil para ajustar el prompt de clasificación dentro de flujo."
}
```

Campos:

- columna `reviewed`: filtro operativo en NocoDB para saber que una persona ya revisó el shot.
- `status`: `unreviewed`, `reviewed`, `fixed`, `ignored`.
- `tags`: etiquetas cortas para agrupar errores.
- `observed_error`: qué hizo mal el bot.
- `expected_behavior`: qué debía hacer.
- `notes`: contexto adicional para quien optimice.

Etiquetas útiles:

- `question_before_answer`
- `answer_plus_question`
- `change_intent`
- `decline`
- `wrong_flow`
- `wrong_state_transition`
- `rag_required`
- `rag_missing_answer`
- `bad_resume`
- `hallucination`
- `human_request`
- `wrong_handoff`
- `prompt_rules_misuse`
- `sales_push`

## Como Dejar Un Shot Listo Para Evaluar

Flujo recomendado:

1. Buscar en NocoDB un shot problemático.
2. Leer el JSON.
3. Completar el bloque `review`.
4. Activar el checkbox `reviewed`.
5. Definir una expectativa concreta con `EvalExpected`.
6. Ejecutar el runner con ese shot.
7. Optimizar prompt/código/RAG.
8. Volver a correr el mismo shot.
9. Marcar el review como `fixed` si ya se comporta bien.

Por ahora V1 no guarda una carpeta de JSONs versionados de shots revisados. Si se quiere hacer permanente un shot problemático dentro del repo, la evolución natural sería agregar una carpeta como `reviewed_shots/`, pero todavía no es implementación activa.

## Como Simular Un Shot

Ejemplo básico:

```python
from tests.conversation_evals.schemas import CapturedConversationShot, EvalExpected
from tests.conversation_evals.runner import ConversationEvalRunner

shot = CapturedConversationShot.model_validate(shot_json)

result = ConversationEvalRunner().run_shot(
    shot,
    metadata={
        "chanel": "telegram",
        "id_user": "1049838038",
        "shot_id": "1049838038_20260606_222046"
    },
    expected=EvalExpected(
        next_flow="GENERAL",
        next_node="G35",
        must_call_tools=["rag.answer_question"],
        must_not_advance_state=True,
        must_resume_pending_question=True
    ),
)
```

La simulación inyecta:

- `state_before`
- `history`
- `turn.user_message`
- metadata externa como canal e id de usuario

Luego ejecuta `FlowGraphRunner` actual.

## Checks Determinísticos

`EvalExpected` permite validar sin LLM:

- `legacy_state`
- `next_flow`
- `next_node`
- `must_call_tools`
- `must_not_call_tools`
- `must_not_advance_state`
- `must_resume_pending_question`
- `must_not_handoff`
- `required_reply_substrings`
- `forbidden_reply_substrings`
- `resume_contains`

Estos checks son obligatorios y baratos. Si uno falla, no hace falta usar juez LLM para saber que el comportamiento cambió mal.

## Mocks De Herramientas

El runner permite simular herramientas para evitar depender de OpenAI, RAG real o NocoDB real en tests determinísticos.

Mocks soportados por el schema:

- `rag_answer`
- `reply_classification`
- `reception_decision`

Esto permite fijar condiciones como:

```json
{
  "mocked_tools": {
    "rag_answer": {
      "has_answer": true,
      "answer": "Sí, depende de disponibilidad.",
      "sources": [
        {
          "content": "Pregunta: Atienden domingos?\nRespuesta: Sí, depende de disponibilidad.",
          "score": 0.8,
          "source_id": "nocodb:mlk30zxjzj4lfd8:abc"
        }
      ]
    }
  }
}
```

## Juez LLM

`judges.py` contiene `SemanticJudge`, un juez semántico opcional.

El juez revisa criterios donde los asserts rígidos no bastan:

- si la respuesta contesta la duda del usuario
- si está grounded en el contexto/herramientas
- si retoma naturalmente el estado pendiente
- si no deriva humano sin razón
- si no cambia la intención del usuario

El juez devuelve JSON:

```json
{
  "passed": true,
  "answers_user_question": true,
  "is_grounded": true,
  "resumes_pending_state": true,
  "has_unwanted_handoff": false,
  "score": 0.92,
  "failure_reason": ""
}
```

## Modelos

Modelo principal del bot:

```bash
OPENAI_MODEL=gpt-4o-mini
```

Modelo del juez:

```bash
EVAL_JUDGE_MODEL=gpt-4o-mini
```

Si `EVAL_JUDGE_MODEL` no está definido, el juez usa `OPENAI_MODEL`; si tampoco está, usa `gpt-4o-mini`.

El juez LLM solo corre si:

```bash
RUN_LLM_EVALS=1
OPENAI_API_KEY=...
```

La suite normal deja el juez saltado para no depender de OpenAI ni gastar tokens.

## Comandos

Tests determinísticos de conversation evals:

```bash
docker compose -f docker-compose.local.yml exec -T -e OPENAI_API_KEY= bot_agent python -m unittest tests.conversation_evals.test_deterministic_evals
```

Tests con juez LLM:

```bash
docker compose -f docker-compose.local.yml exec -T -e RUN_LLM_EVALS=1 bot_agent python -m unittest tests.conversation_evals.test_llm_judge_evals
```

Tests de shots:

```bash
docker compose -f docker-compose.local.yml exec -T -e OPENAI_API_KEY= bot_agent python -m unittest tests.unit.test_conversation_shots
```

Compilación:

```bash
docker compose -f docker-compose.local.yml exec -T bot_agent python -m compileall src tests
```

Después de cambios de código/configuración:

```bash
docker compose -f docker-compose.local.yml up -d --build --remove-orphans
```

## Reglas De Mantenimiento

- No agregar regex o hardcode para pasar un shot específico.
- No sobreajustar prompts a un ejemplo literal.
- Guardar instrucciones generales que mejoren la distribución real de casos.
- Mantener `history` compacto y sin fechas.
- Mantener metadata externa en columnas NocoDB, no duplicada dentro del JSON.
- Usar la columna `reviewed` para filtrar revisión humana; usar `review.status` para describir el estado del caso.
- Mantener herramientas dentro de `turn.events`, no como lista separada.
- No capturar recordatorios como shots.
- No depender de NocoDB real ni OpenAI real en tests determinísticos.
- Si se agrega una herramienta nueva importante, registrarla con `ToolCallLogger` para que aparezca en `turn.events`.

## Limitaciones Actuales

- V1 captura shots en NocoDB, pero no tiene todavía una carpeta versionada de shots revisados.
- El runner puede simular un shot, pero todavía no descarga shots automáticamente desde NocoDB.
- El juez LLM es opcional y no reemplaza los checks determinísticos.
- Los mensajes automáticos no generan shots propios.
- Si una herramienta no pasa por `ToolCallLogger`, no aparecerá en `turn.events`.

## Ideas Futuras

Estas ideas no son implementación activa en V1:

- `reviewed_shots/`: carpeta para guardar shots revisados como casos permanentes.
- `regression`: shots problemáticos aprobados que nunca deben volver a fallar.
- `validation`: variaciones para comparar mejoras de prompts o routing.
- `holdout`: casos reservados para detectar prompt overfitting.
- auditor automático de logs: leer shots, asignar `risk_score` y sugerir tags.
- importador desde NocoDB: seleccionar shots `reviewed` y convertirlos en tests.
- juez LLM masivo: revisar shots nuevos y priorizar cuáles necesitan revisión humana.
