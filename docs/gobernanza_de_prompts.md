# Gobernanza de prompts

Los prompts de `src/core/prompts.py` son la **lógica de negocio más sensible del
sistema**: un cambio de una frase puede alterar el ruteo de miles de conversaciones.
Este documento define el proceso obligatorio para crearlos o editarlos. **Ningún
prompt se edita "al vuelo".**

Los prompts vigentes y su rol (arquitectura supervisor/workers, ver
`docs/modelo_unico.md` y `docs/diseno_especialistas.md`):

La pantalla **Prompts** versiona de forma independiente el playbook editable de
cada agente (`supervisor`, `general`, `curso_teorico`, `alquiler`, `clases`,
`dictamen`, `tramites` y `recordatorio`). El playbook guardado reemplaza únicamente
el cuerpo de ese rol. El contrato transversal, las herramientas estrictas y el catálogo
dinámico se ensamblan después desde código y no son editables desde el dashboard.
Un rollback siempre crea otra versión; nunca reescribe el historial.

| Prompt | Rol | Contrato de salida |
| ------ | --- | ------------------ |
| `AGENT_COMMON_CONTRACT` | Reglas transversales de TODOS los agentes (fragmentos literales, anti-invención, queja/pago→humano, estilo) | — (se ensambla con el resto) |
| `SUPERVISOR_PROMPT_BODY` + `SUPERVISOR_OUTPUT_SCHEMA` | Coordinador: saludo, ambiguo, queja, WIN, dudas sueltas y enrutamiento | Herramienta estricta → adaptador `tool_decision` → `_validated_decision` |
| `AREA_PROMPT_BODIES[área]` + `SPECIALIST_OUTPUT_SCHEMA` | Playbook de cada especialista (GENERAL, CURSO_TEORICO, ALQUILER, CLASES, DICTAMEN, TRAMITES) | Herramienta estricta → adaptador `tool_decision` → `_validated_decision` |
| `FOLLOWUP_AGENT_PROMPT` | Recordatorio inteligente (decide si retomar y redacta UN mensaje) | JSON `{send, message}` |
| `EXTRACT_AD_INFO_PROMPT` | Extracción de datos de anuncios | JSON `{dia, valor, hora}` |
| Prompt del RAG (en `rag_service._answer_prompt`) | Generación con evidencia | JSON `{has_answer, answer, unanswered_queries}` |

---

## 1. Principios de diseño (no negociables)

Estos principios ya están encarnados en los prompts actuales; cualquier edición debe
preservarlos:

1. **Instrucciones por intención/contexto, nunca por frase exacta** (prohibido el
   *prompt overfitting*). ❌ "si dice 'hola' responde X" → ✅ "si el mensaje es un
   saludo, haz X". Los few-shot ilustran el principio; no son reglas por caso. Si un
   bug se arregló agregando la frase literal del cliente al prompt, el arreglo está
   mal: generaliza la instrucción.
2. **Un prompt = una responsabilidad.** El supervisor coordina y enruta; cada
   especialista ejecuta SOLO su playbook con el catálogo común del proyecto; el followup decide el
   recordatorio; el RAG genera con evidencia. No mezclar responsabilidades ni
   duplicar reglas entre prompts (dos fuentes de verdad divergen): lo transversal
   vive UNA vez en `AGENT_COMMON_CONTRACT`.
3. **Prioridades explícitas y excluyentes.** Ambos prompts de decisión eligen UNA
   acción/intención siguiendo un orden de prioridad numerado (queja > handoff >
   rechazo > …). Toda regla nueva debe insertarse en ese orden, no como excepción
   suelta al final.
4. **El dato y la instrucción viajan separados.** El mensaje del cliente entra como
   JSON en el turno de usuario; el system prompt no interpola texto del cliente.
5. **Decisiones por herramientas estrictas + validación local.** Supervisor y
   especialistas usan `tools`, `tool_choice="required"` y
   `parallel_tool_calls=False`. Cada definición lleva `strict=True` y no admite
   campos extra. `agent_tools.tool_decision` valida campos y nombres y adapta la
   operación a `AgentDecision`; `_validated_decision` conserva la coherencia del
   pipeline. Una llamada incompleta, desconocida o inválida usa aclaración segura.
   El RAG y los recordatorios mantienen sus esquemas JSON y su ejecución actual.
6. **Genéricos respecto al catálogo.** Los prompts no copian contenido de los
   fragmentos (precios, "casco", "programar cita"…); solo usan su referencia. Lo vigila
   `tests/unit/test_prompt_contracts.py`. El conocimiento de negocio vive en el RAG
   y en el catálogo de fragmentos, no en los prompts.
7. **Validaciones en código.** Todos los agentes acceden al catálogo activo
   del proyecto, sin asignaciones por área. Nombres inexistentes/inactivos,
   destinos desconocidos y derivaciones hacia sí mismo no se ejecutan. Se
   mantienen el límite de áreas por turno y el control anti ping-pong.
8. **Decisiones con `temperature=0`.** Supervisor, especialistas y followup son
   deterministas. Solo la generación libre (RAG, publicidad) puede usar otra
   temperatura.
9. **Sin conocimiento variable en el prompt.** Precios, horarios, links, requisitos →
   RAG (`answer_source="rag"`). Un prompt nunca "sabe" un dato de negocio que pueda
   cambiar.

## 2. Proceso obligatorio para editar un prompt

Checklist a seguir **en orden**; si un paso falla, no se avanza:

1. **Justificación por escrito** (en el mensaje del commit): qué caso real falla, por
   qué el comportamiento actual es incorrecto, y por qué la solución es una
   instrucción general y no una regla por frase.
2. **¿Es de verdad el prompt?** Antes de editar, confirma que el problema no es de:
   - texto literal del bot → «Agente IA → Fragmentos»
   - disponibilidad del fragmento → catálogo activo del proyecto
   - cableado del grafo / guardrails (anti-bucle, defer, reporte) → `agent_pipeline.py`
   - normalización de salida → `_validated_decision` (`unified_agent.py`)
   La mayoría de los "bugs de prompt" son de otra capa.
3. **Localiza la sección correcta** del prompt (los bloques están separados por caso
   con `═══`). La regla nueva va dentro del caso al que pertenece y respetando el
   orden de prioridad; nunca como parche al final.
4. **Verifica los contratos**: `tests/unit/test_prompt_contracts.py` exige que ciertas
   frases clave existan y que no aparezcan términos del catálogo. Si tu edición los
   rompe, o ajustas la redacción o actualizas el test **de forma deliberada y
   explicada en el commit** — nunca "para que pase".
5. **Cubre el caso con un test antes de dar por bueno el cambio**:
   - el cableado (qué hace el sistema con la decisión) → test determinista con LLM
     mockeado;
   - el juicio del LLM (que el modelo decida bien el caso) → test `@requires_llm` en
     `tests/regression/`;
   - la calidad de la redacción → eval con juez (`RUN_LLM_EVALS=1`), opcional.
6. **Corre la suite completa** en el contenedor:
   `docker compose -f docker-compose.local.yml run --rm bot_agent pytest`
   (con key: incluye las regresiones de juicio del LLM). Un cambio de prompt **sin**
   correr las regresiones con LLM real no está verificado.
7. **Busca regresiones laterales**: los prompts de decisión gobiernan muchos casos a
   la vez. Revisa los tests de regresión relacionados con el caso vecino (p. ej.
   tocar la regla de `clarify` afecta el anti-bucle del intake; el historial de
   commits muestra esa cadena de efectos: `dc3dab3` → `42490a5` → `e342b4b` →
   `787111b`).
8. **Un cambio conceptual por commit.** No mezclar "afinar handoff" con "reordenar
   value de licencias" en el mismo commit: si algo regresa, no se puede biseccionar.

## 3. Proceso para crear un prompt nuevo

Además de todo lo anterior:

1. Definir el **contrato de salida** (JSON estricto) y escribir primero el validador
   en código con su test.
2. `temperature=0` si decide; libre solo si genera texto.
3. Registrar la llamada con `ToolCallLogger` (input/output/duración/errores) para que
   cada invocación quede trazada en el log de conversación.
4. Definir el **fallback sin LLM**: qué hace el sistema si no hay key o la llamada
   falla. La regla del proyecto es degradar a un comportamiento seguro y honesto
   (aclarar / `unknown`), nunca adivinar con heurísticas.
5. Añadirlo a la tabla de este documento y, si define un área nueva, actualizar
   `docs/diseno_especialistas.md` (mapa de especialistas y partición de fragmentos).

## 4. Qué NO hacer (errores ya cometidos y corregidos)

- **Regla por frase exacta** para arreglar un caso puntual → confunde al modelo en
  los mil casos vecinos.
- **Regex o listas de palabras clave** para interpretar lenguaje natural del cliente
  (la única excepción son los comandos estructurados y keywords de negocio, ver
  CLAUDE.md §5).
- **Instrucciones contradictorias entre secciones**: si agregas una regla en el PASO
  2, revisa que el PASO 1 y el PASO 3 no digan lo contrario para el mismo caso.
- **Responder desde el prompt datos de negocio** ("el curso cuesta X") — eso va al
  RAG.
- **Subir la temperatura** para "variar" una decisión: la variación de estilo es
  legítima solo en generación, nunca en clasificación/ruteo.

## Contratos técnicos de consultas RAG

La herramienta `consultar_conocimiento` recibe `pregunta` y `consultas` (máximo
tres). El adaptador conserva `rag_query` y `rag_queries` del pipeline existente. El RAG devuelve `unanswered_queries` para identificar
las partes sin respaldo de una respuesta parcial, sin inferir intenciones por
coincidencias literales. La selección de consultas y la decisión semántica de
responder siguen siendo responsabilidad del modelo y de sus instrucciones.

## Prompts en lenguaje natural y herramientas

Ejemplos de instrucciones comerciales válidas:

- «Cuando corresponda preguntar por la cita, envía GENERAL.G3».
- «Si ya tiene cita, deriva a ALQUILER con los datos conocidos».
- «Si necesita revisión de un pago, pasa el caso a un asesor humano».
- «Consulta conocimiento para responder dudas informativas».

No hace falta escribir JSON ni corchetes. Los nombres son literales y sensibles
a mayúsculas/minúsculas. Las herramientas son `enviar_fragmento`,
`responder_cliente`, `derivar_agente`, `handoff_asesor`,
`consultar_conocimiento`, `cerrar_conversacion` e `invitacion_ciudad`.
Los playbooks guardados con `action`, `target` o `[[frag:ID]]` siguen siendo
referencias semánticas mediante el contrato de compatibilidad; no se sobrescriben
sus versiones. Las etiquetas solo continúan en el adaptador y el historial.

`handoff_asesor` crea el reporte, pausa temporalmente el bot y limpia el contexto
como antes. No asigna una persona específica ni garantiza una respuesta humana.
`consultar_conocimiento` utiliza el mismo `RagService` (consultas múltiples,
respuestas parciales, registro de dudas sin respaldo y suspensión de pasos
posteriores si falta evidencia), sin una segunda generación que lo parafrasee.
