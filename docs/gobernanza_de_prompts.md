# Gobernanza de prompts

Los prompts de `src/core/prompts.py` son la **lógica de negocio más sensible del
sistema**: un cambio de una frase puede alterar el ruteo de miles de conversaciones.
Este documento define el proceso obligatorio para crearlos o editarlos. **Ningún
prompt se edita "al vuelo".**

Los prompts vigentes y su rol:

| Prompt | Rol | Contrato de salida |
| ------ | --- | ------------------ |
| `RECEPTION_AGENT_PROMPT` | Intake (PD1): decide action/flow del primer contacto | JSON validado por `ReceptionAgent._validated_decision` |
| `REPLY_EVALUATION_PROMPT` | Clasificador dentro de flujo (PD2): intent + value + duda lateral | JSON normalizado por `ResponseClassifier._normalize` |
| `REPORT_SUMMARY_PROMPT` | Resumen para reportes a asesor | JSON `{"resumen": ...}` |
| `EXTRACT_AD_INFO_PROMPT` | Extracción de datos de anuncios | JSON `{dia, valor, hora}` |
| Prompt del RAG (en `rag_service._answer_prompt`) | Generación con evidencia | JSON `{has_answer, answer}` |

---

## 1. Principios de diseño (no negociables)

Estos principios ya están encarnados en los prompts actuales; cualquier edición debe
preservarlos:

1. **Instrucciones por intención/contexto, nunca por frase exacta** (prohibido el
   *prompt overfitting*). ❌ "si dice 'hola' responde X" → ✅ "si el mensaje es un
   saludo, haz X". Los few-shot ilustran el principio; no son reglas por caso. Si un
   bug se arregló agregando la frase literal del cliente al prompt, el arreglo está
   mal: generaliza la instrucción.
2. **Un prompt = una decisión.** Recepción decide el ruteo inicial; el clasificador
   interpreta la respuesta al paso; el RAG genera con evidencia. No mezclar
   responsabilidades ni duplicar reglas entre prompts (dos fuentes de verdad
   divergen).
3. **Prioridades explícitas y excluyentes.** Ambos prompts de decisión eligen UNA
   acción/intención siguiendo un orden de prioridad numerado (queja > handoff >
   rechazo > …). Toda regla nueva debe insertarse en ese orden, no como excepción
   suelta al final.
4. **El dato y la instrucción viajan separados.** El mensaje del cliente entra como
   JSON en el turno de usuario; el system prompt no interpola texto del cliente.
5. **Salida = JSON estricto contra contrato validado en código.** Si cambias el
   esquema de salida, cambia en el mismo commit el validador
   (`_validated_decision` / `_normalize`) y sus tests. El código nunca debe confiar
   en que el modelo cumpla el esquema.
6. **Genéricos respecto al catálogo.** Los prompts no mencionan contenido de
   `mensajes.json` (precios, "casco", "programar cita"…). Lo vigila
   `tests/unit/test_prompt_contracts.py`. El conocimiento de negocio vive en el RAG
   y en `mensajes.json`, no en los prompts.
7. **Estados imposibles se bloquean en código, no solo en el prompt.** Ejemplos
   vigentes: un `greeting` nunca lleva duda lateral (`_normalize`), un `start_flow`
   sin pregunta no antepone respuesta (invariante P2 en `_normalized_decision`). Si
   detectas una combinación inválida nueva, agrega la normalización en código además
   de la instrucción.
8. **Decisiones con `temperature=0`.** Recepción, clasificador y juez son
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
   - ruteo → `flow_router.py`
   - texto del bot → `mensajes.json`
   - cableado del grafo → `flow_graph.py`
   - normalización de salida → `_validated_decision` / `_normalize`
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
5. Añadirlo a la tabla de este documento y, si toma decisiones de ruteo, a
   `docs/tabla_decision_agente.md`.

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
