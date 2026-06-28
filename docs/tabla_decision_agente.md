# Tabla de decisión del agente (intake + flujo)

Este documento especifica **dónde interviene el agente (LLM) para tomar
decisiones**, **qué variables maneja en cada punto**, y **todas las
combinaciones posibles de esas variables** con su manejo correcto. Incluye los
**estados imposibles / inválidos** que el guardrail determinista debe normalizar.

Es la fuente de verdad para diseñar el guardrail. No describe el texto de los
mensajes (eso vive en `mensajes.json`), sino **qué decisión tomar** en cada caso.

Objetivo rector (regla de negocio): **el cliente nunca se queda sin respuesta**.
Toda combinación válida produce al menos un mensaje, y **toda duda real se aborda**
(se responde, o se deriva a un humano que la atenderá). Nunca silencio.

---

## 1. Puntos donde interviene el agente

| ID  | Punto | Quién decide | Entrada | Salida |
| --- | ----- | ------------ | ------- | ------ |
| **PD1** | **Recepción / Intake** — usuario sin flujo activo (`flow/node` vacío o `INTAKE.I1`). Código: [`_handle_intake`](../services/bot_agent/src/application/flow_graph.py:154), prompt `RECEPTION_AGENT_PROMPT`. | LLM (`reception.decide`) | mensaje del cliente + historial reciente | `ReceptionDecision` |
| **PD2** | **Evaluación de respuesta en flujo activo** — ya hay un nodo en curso. Código: [`_evaluate_reply`](../services/bot_agent/src/application/flow_graph.py:257), prompt `REPLY_EVALUATION_PROMPT`. | LLM (`classifier.classify_reply`) | mensaje + flujo/nodo + última pregunta | `ReplyClassification` |
| **PD3** | **Enrutamiento del flujo** — a qué nodo se avanza. Código: [`FlowRouter.next_node`](../services/bot_agent/src/application/flow_router.py:24). | **Determinista** (no LLM) | `intent`, `value`, nodo actual | `(next_flow, next_node)` o `("", "")` |
| **PD4** | **Respondibilidad del RAG** — ¿hay respaldo para responder la duda? Código: [`rag.answer_question`](../services/bot_agent/src/application/rag_service.py). | LLM + recuperación | la duda | `has_answer` (bool) + `answer` |

PD3 y PD4 son **subdecisiones deterministas/derivadas** que dependen de PD1/PD2.
El guardrail vive sobre PD1 y PD2 (donde se interpreta lenguaje natural), usando
PD3/PD4 como funciones puras de apoyo.

**Otras llamadas LLM (NO son puntos de decisión de routing, fuera de alcance del guardrail):**
- `classifier.summarize_for_report` (`REPORT_SUMMARY_PROMPT`): genera el resumen del
  reporte cuando hay handoff/queja. No decide ruta, solo redacta.
- `publicidad_service` (`EXTRACT_AD_INFO_PROMPT`): extrae fecha/valor/hora de un
  anuncio. Pertenece al flujo de publicidad, no a la conversación con el cliente.
- `OFF_FLOW_QUESTION_PROMPT`: **definido en `core/prompts.py` pero sin uso** (código
  muerto). Candidato a eliminar; no interviene en ninguna decisión.

---

## 2. Variables (contrato) de cada punto

### 2.1 PD1 — `ReceptionDecision`

| Variable | Dominio | Significado |
| -------- | ------- | ----------- |
| `action` | `start_flow` · `answer_and_start_flow` · `answer_and_clarify` · `clarify` · `handoff` · `close` | Qué hace el agente. |
| `flow` | `GENERAL` · `Alquiler` · `CLASES` · `DICTAMEN` · `QUEJA` · `WIN` · `""` | Flujo destino (si aplica). |
| `has_question` | `true` · `false` | ¿El mensaje trae una **duda informativa real**? |
| `question` | texto | La duda detectada. |
| `answer_source` | `prompt_rules` · `rag` · `none` | De dónde sale la respuesta. |
| `answer` | texto | Respuesta breve (si la hay). |
| `clarifying_question` | texto | Pregunta de descubrimiento/confirmación. |
| `handoff_reason` | texto | Motivo interno de derivación. |

### 2.2 PD2 — `ReplyClassification`

| Variable | Dominio | Significado |
| -------- | ------- | ----------- |
| `intent` | `positive` · `negative` · `city` · `license` · `question` · `complaint` · `decline` · `change_intent` · `human_handoff` · `greeting` · `unknown` | Intención principal respecto al paso del flujo. |
| `value` | `liberia` · `other` · `car` · `moto` · `b2` · `b3` · `b4` · `bus` · `""` | Dato asociado a `city`/`license`. |
| `has_off_flow_question` | `true` · `false` | ¿Trae además una **duda lateral real**? |
| `off_flow_question` | texto | La duda lateral. |

---

## 3. Ingredientes semánticos (variables ortogonales de decisión)

Las variables del contrato son muchas y dependientes. Para razonar las
combinaciones usamos un conjunto reducido de **señales que un mensaje puede
contener**. Cada mensaje es una combinación (subconjunto) de estas señales:

| Señal | Símbolo | En PD1 (intake) | En PD2 (flujo) |
| ----- | ------- | --------------- | -------------- |
| **Avanza** | `A` | Intención clara de una línea de negocio → `start_flow`. | El mensaje **responde el paso** y el router devuelve nodo (PD3 ≠ vacío). |
| **Pregunta** | `Q` | `has_question=true` (duda real). | `has_off_flow_question=true` (duda lateral real). |
| **Handoff** | `H` | Pago/comprobante, pide humano, se dirige a Enrique, caso administrativo. | `intent=human_handoff`. |
| **Queja** | `C` | Enojo/reclamo/devolución fuerte. | `intent=complaint`. |
| **Rechazo** | `D` | "solo preguntaba" / "no necesito más" → cerrar. | `intent=decline`. |
| **Saludo** | `G` | Saludo/cortesía social sin contenido. | `intent=greeting`. |
| **Cambio** | `K` | (no aplica: en intake todo mensaje define el flujo) | `intent=change_intent` (quiere otro trámite). |
| **Nada** | `∅` | Sin intención, ni pregunta, ni saludo. | `intent=unknown` y sin avance ni duda. |

> `A` y `Q` son los dos ejes que más se combinan y donde estaban los bugs.
> `C/H/D/K/G/∅` son mayormente excluyentes entre sí en la práctica.

---

## 4. Principio de prioridad (la columna vertebral del guardrail)

Cuando un mensaje trae varias señales a la vez, se resuelven en este orden. La
regla de oro: **una duda (`Q`) siempre se aborda, salvo que un humano vaya a
tomar el caso completo (`C` o `H`), porque entonces el humano también la atiende.**

1. **`C` (Queja)** → `QUEJA` / handoff. Atender primero; no se responde RAG antes.
2. **`H` (Handoff)** → handoff. Un humano revisa (incluida cualquier duda `Q`).
3. **`D` (Rechazo)** → cerrar. *(Comportamiento actual: si además hay `Q`, hoy el
   código cierra y NO responde la duda — ver limitación L3. La intención de diseño
   es responder la duda y luego cerrar.)*
4. **`K` (Cambio, solo PD2)** → re-enrutar a intake con el mensaje completo.
5. **`A` + `Q` / `A` / `Q`** → matriz principal (sección 5/6).
6. **`G` (Saludo)** → bienvenida/retoma cálida, **sin RAG**.
7. **`∅` (Nada)** → `clarify` con opciones (PD1) / retomar la pregunta pendiente (PD2).

---

## 5. PD1 — Tabla de decisión del intake

Dimensión de contexto adicional: **`prev_confirm`** = el turno anterior del bot
fue una **pregunta de confirmación/aclaración** del intake (el cliente está
respondiendo a "¿desea continuar con el proceso X?"). Esto habilita
`answer_and_start_flow`.

### 5.1 Señales de override (independientes de `prev_confirm`)

| Caso | Señales | `action` | `flow` | Notas |
| ---- | ------- | -------- | ------ | ----- |
| I-C | `C` | `start_flow` | `QUEJA` | Queja fuerte manda. |
| I-H | `H` (con o sin `Q`) | `handoff` | `""` | Humano revisa; la duda la atiende la persona. |
| I-D | `D` sin `Q` | `close` | `""` | Cerrar cordialmente. |
| I-D+Q | `D` **y** `Q` | `close` (hoy) | `""` | **Comportamiento actual:** cierra; la duda NO se responde por separado (ver L3). Diseño deseado: `answer_and_clarify`. |

### 5.2 Matriz principal `A` × `Q` × `prev_confirm`

| Caso | `A` | `Q` | `prev_confirm` | `action` | `flow` | `answer` |
| ---- | --- | --- | -------------- | -------- | ------ | -------- |
| I-1 | sí | no | — | `start_flow` | flujo detectado | **vacío** ← *(corrige el "¡Buena suerte!")* |
| I-2 | no | sí | — | `answer_and_clarify` | `""` | respuesta a la duda + 1 pregunta de confirmación |
| I-3 | sí | sí | no | `answer_and_clarify` | `""` | responder duda; **no** iniciar flujo aún (confirmar primero) |
| I-4 | sí | sí | sí | `answer_and_start_flow` | flujo confirmado | responder duda **y** entrar al flujo |
| I-5 | sí (confirmación) | no | sí | `start_flow` | flujo confirmado | **vacío** |

### 5.3 Residuales

| Caso | Señales | `action` | Notas |
| ---- | ------- | -------- | ----- |
| I-G | `G` solo | `clarify` | Saludo cálido + opciones de servicio en `clarifying_question`. `answer` vacío. |
| I-CTX | Solo contexto / pedido genérico de ayuda (sin `Q`, sin intención explícita de UN servicio) | `clarify` | El cliente narra una situación ("tengo prueba mañana") o pide ayuda en general. **NO RAG, NO flujo.** Ofrece las opciones relevantes al contexto (p. ej. prueba de manejo → alquiler / clases / proceso de licencia) para que elija. `has_question=false`. |
| I-∅ | `∅` | `clarify` | Pregunta con opciones explícitas de servicios. Nunca abierta ("¿en qué te ayudo?"). |

> **Clave (corrige el caso "Tengo prueba mañana"):** una afirmación de contexto o un
> "¿cómo me pueden ayudar?" **no** es `Q`. Antes el LLM lo marcaba como pregunta → RAG
> volcaba requisitos y, si no podía, escalaba a humano. Ahora se clasifica como I-CTX →
> `clarify`. La intención explícita de un servicio concreto sigue yendo a `start_flow`
> (I-1) y una pregunta real sigue yendo a RAG (I-2).

### 5.4 Respondibilidad del RAG en intake (dimensión `rag_ok` que faltaba)

Los casos con `Q` (I-2, I-3, I-4) asumían que el RAG responde. Pero el intake
también resuelve el RAG en [`_resolve_reception_rag`](../services/bot_agent/src/application/flow_graph.py:217),
y si **no** hay respuesta cambia la decisión:

| Caso | `Q` | `rag_ok` | Resultado real |
| ---- | --- | -------- | -------------- |
| I-2/3/4 | sí | sí | Se responde la duda (como indican esas filas). |
| **I-RAG✗** | sí | no | **`action=clarify`** (sin bloquear): se registra la pregunta sin respuesta para el equipo y se pide precisar. NO se inventa respuesta ni se inicia flujo. |

> **Corrección importante (antes bloqueaba 12 días).** Esta rama hacía `action=handoff`,
> y en intake el handoff dispara [`_create_report_and_block`](../services/bot_agent/src/application/flow_graph.py:590)
> → `block_user(days=12)`. Es decir, una pregunta vaga **mal clasificada** ("¿cómo me
> pueden ayudar?") podía **bloquear al cliente 12 días**. Ahora intake nunca deriva-y-bloquea
> por un fallo de RAG; aclara sin bloquear, igual que F-5 dentro de un flujo. El `handoff`
> (con bloqueo) queda reservado a disparadores humanos explícitos que marca el LLM:
> pago, comprobante, "quiero hablar con alguien" (CASO DERIVACIÓN A HUMANO del prompt).
> Cubierto por [`tests/unit/test_decision_guardrails.py`](../services/bot_agent/tests/unit/test_decision_guardrails.py) (`IntakeRagMissTests`).

**Invariante clave de PD1 (raíz del caso 1):**
`A` sin `Q` (I-1, I-5) ⇒ **`answer` debe ir vacío**. Una respuesta previa solo
existe para contestar una `Q` detectada. Sin pregunta, no hay relleno de cortesía.

---

## 6. PD2 — Tabla de decisión en flujo activo

Dimensiones de apoyo:
- **`R`** = PD3 devuelve nodo (`next_node` ≠ ""), es decir el `intent`+`value`
  encaja con una transición del nodo actual.
- **`pending_report`** = el nodo actual tenía un reporte pendiente (p. ej. nodo de queja).
- **`rag_ok`** = PD4 (`has_answer`) para la `Q`.

### 6.1 Señales de override

| Caso | Señales (`intent`) | Manejo |
| ---- | ------------------ | ------ |
| F-C | `complaint` | Si `flow=QUEJA` y `pending_report` → reporte directo. Si no → mensaje de handoff + reporte. |
| F-H | `human_handoff` | Mensaje de handoff + reporte (resumen LLM). La duda la atiende la persona. |
| F-D | `decline` | Mensaje de cierre + limpiar estado. Si traía `Q`, hoy NO se responde (ver L3). |
| F-K | `change_intent` | Reiniciar estado conservando historial → re-ejecutar **PD1** con el mensaje. |
| F-G | `greeting` | Retomar la pregunta pendiente, **sin RAG**. |

### 6.2 Matriz principal `R` × `Q`

| Caso | `R` | `Q` | `rag_ok` | Manejo |
| ---- | --- | --- | -------- | ------ |
| F-1 | sí | no | — | Avanzar al nodo. **Solo** el/los mensaje(s) del flujo. ← *(corrige el "Por ahora no tengo…")* |
| F-2 | sí | sí | sí | Responder la duda lateral **+** mensajes del nodo siguiente. |
| F-3 | sí | sí | no | Avanzar al nodo **+** registrar pregunta sin respuesta **+** ofrecer asesor por la duda lateral. |
| F-4 | no | sí | sí | Responder la duda; reanclar a la pregunta pendiente (retomar). |
| F-5 | no | sí | no | Responder con fallback (ofrecer asesor) + registrar pregunta sin respuesta. **No** reanclar (la duda sigue abierta). |
| F-6 | no | no | — | No avanza y no pregunta → **retomar** la pregunta pendiente (o reporte si `pending_report`). Nunca silencio. |

> Diferencia F-1 vs F-3: si el cliente **avanzó** el flujo, el `Q` solo dispara
> respuesta/asesor cuando es una **duda real**. Con la detección de `Q` corregida,
> F-1 deja de inyectar el "no tengo esa información" (caso 2), y F-3 sí lo hace
> pero **solo** ante una duda lateral genuina e irresoluble.

**Invariante clave de PD2 (raíz del caso 2):**
`has_off_flow_question=true` debe reservarse a una **duda informativa real**. Una
afirmación, comentario o plan ("ya mañana hago el trámite") **no** es `Q` →
`has_off_flow_question=false`.

---

## 7. Estados imposibles / inválidos

El LLM puede devolver combinaciones incoherentes. Algunas se corrigen con un
**guardrail determinista** (código que valida la salida del LLM); otras solo se
previenen en el **prompt** porque distinguirlas requiere interpretar lenguaje, o
porque la combinación ni siquiera puede ocurrir (estados vacíos). Cada fila marca
de qué tipo es.

> Nota importante: en PD2, **`R` se deriva de `intent`+`value`** (lo calcula el
> router `PD3`), no es una señal independiente. Por eso una fila como "intent X
> **y** `R`" puede ser **imposible/vacía** cuando ese intent nunca produce avance.

### 7.1 PD1 (intake)

| # | Estado inválido | Por qué es imposible | Normalización |
| - | --------------- | -------------------- | ------------- |
| P1 | `action ∈ {start_flow, answer_and_start_flow}` con `flow=""` | No se puede entrar a un flujo inexistente. | → `answer_and_clarify` si hay `answer`/`Q`, si no `clarify`. *(ya existe parcialmente, [reception_agent.py:141](../services/bot_agent/src/application/reception_agent.py:141))* |
| **P2** | `has_question=false` **y** `answer≠""` en `start_flow`/`answer_and_start_flow` | Una respuesta previa solo responde una pregunta; sin `Q` es relleno. | **→ `answer=""`** y `answer_and_start_flow`→`start_flow`. *(implementado, raíz del caso 1, [reception_agent.py:165](../services/bot_agent/src/application/reception_agent.py:165))* |
| P3 | `has_question=true` con `answer` de `prompt_rules` que sea conocimiento de negocio | `prompt_rules` no debe traer datos operativos. | → `answer=""`, `answer_source=rag` (re-resolver por RAG). *(ya existe, [reception_agent.py:134](../services/bot_agent/src/application/reception_agent.py:134))* |
| P4 | `has_question=true` y `question=""` | Hay pregunta sin texto. | → `question = mensaje del cliente`. *(ya existe, [reception_agent.py:101](../services/bot_agent/src/application/reception_agent.py:101))* |
| P5 | `has_question=false` y `answer_source=rag` | No hay pregunta que justifique RAG. | → `answer_source=none`. *(ya existe, [reception_agent.py:163](../services/bot_agent/src/application/reception_agent.py:163))* |
| P6 | `action=handoff` sin `handoff_reason` | Reporte sin motivo. | → motivo por defecto. *(ya existe, [reception_agent.py:155](../services/bot_agent/src/application/reception_agent.py:155))* |
| P7 | `action ∈ {clarify, answer_and_clarify}` sin `clarifying_question` ni `answer` | No habría nada que enviar (silencio). | → `clarifying_question` de descubrimiento. *(ya existe, [reception_agent.py:148](../services/bot_agent/src/application/reception_agent.py:148))* |
| P8 | `action=close` junto con `A` (intención clara de continuar) | Cerrar contradice avanzar. | La cortesía no cancela la confirmación: si hay aceptación real → `start_flow`; cierre solo ante rechazo explícito. |

### 7.2 PD2 (flujo)

| # | Estado inválido | Por qué es imposible | Normalización | Tipo |
| - | --------------- | -------------------- | ------------- | ---- |
| **F-i** | `has_off_flow_question=true` con `intent=greeting` | Un saludo no contiene una duda real. | → `has_off_flow_question=false`. | **Código** ([response_classifier._normalize](../services/bot_agent/src/application/response_classifier.py)) |
| F-iii | `has_off_flow_question=true` y `off_flow_question=""` | Bandera de duda sin texto. | → bajar la bandera. | **Código** (`_normalize`) |
| F-vi | `intent=complaint`/`human_handoff` **y** `has_off_flow_question=true` | El humano domina; la duda la ve la persona. | → bajar `Q`; el override (F-C/F-H) manda. | **Código** (`_normalize`) |
| F-ii | `intent=question` **y** `R` | `question` y avance se excluyen. | El router nunca avanza con `intent=question` → **combinación vacía**. | Prompt (no puede ocurrir) |
| F-vii | `intent=decline` **y** `R` | Rechazo y avance se excluyen. | `decline` nunca produce avance → **combinación vacía**; además `decline` se evalúa antes que el router. | Prompt (no puede ocurrir) |
| F-iv-lic | `intent=license` y `value=""` | Falta el tipo de licencia. | El router devuelve "" → cae a F-6 (retomar pidiendo el dato). | Determinista (router) |
| F-iv-city | `intent=city` y `value=""` | — | **Ojo: el router SÍ avanza**: `city` sin `value` se trata como "otra ciudad" (p. ej. G35→G12, C1→C5). No cae a F-6. | Determinista (router) — comportamiento a revisar |
| F-v | `intent=positive/negative` pero `R=no` | El "sí/no" no aplica al paso actual. | El router devuelve "" → cae a F-6 (retomar). | Determinista (router) |

### 7.3 Reglas de coherencia transversales

- **Nunca silencio:** toda hoja de las tablas produce ≥1 mensaje. Si una rama
  quedara vacía, el fallback es retomar (PD2) o `clarify` (PD1).
- **`Q` siempre se aborda:** responder (`rag_ok`) o registrar + ofrecer asesor
  (`¬rag_ok`); o la atiende el humano (`C`/`H`). Nunca se descarta una duda real.
- **Sin `Q` no hay `answer`:** invariantes P2 (PD1) y "F-1 sin fallback" (PD2).

### 7.4 Limitaciones conocidas (huecos reales del código)

| # | Hueco | Detalle | Estado |
| - | ----- | ------- | ------ |
| **L1** | **Duda lateral descartada si no avanza y el intent no es `question`** | En [`_evaluate_reply`](../services/bot_agent/src/application/flow_graph.py:312), cuando el router no avanza (`R=no`), solo se atiende la duda lateral si `intent=question`. Si el intent es `positive/negative/city/license/unknown` y NO avanza pero trae `has_off_flow_question=true`, la duda se **descarta** y solo se retoma la pregunta pendiente. Contradice "`Q` siempre se aborda". | Mitigado por el prompt (debe clasificar como `question` si no responde el paso), pero **no garantizado** por código. Pendiente de decidir si se enruta a `answer_off_flow` siempre que haya `Q` sin avance. |
| L2 | **`city` sin `value` avanza como "otra ciudad"** | Ver F-iv-city. Un `intent=city` ambiguo no pide aclarar la sede; entra al ramal de ciudad no-Liberia. | A revisar si es el comportamiento deseado. |
| L3 | **`D` + `Q` cierra sin responder la duda** | Tanto en intake ([`_handle_intake` close](../services/bot_agent/src/application/flow_graph.py:166)) como en flujo ([`_evaluate_reply` decline](../services/bot_agent/src/application/flow_graph.py:298)), un rechazo con duda adjunta cierra y descarta la duda. No existe acción "responder y cerrar". | Pendiente decidir: ¿responder la duda antes de cerrar? |

---

## 8. Resumen de cambios que habilita esta tabla

1. **Detección de `Q` más estricta** (prompts): una afirmación/comentario/plan no
   es pregunta. Corrige el origen de ambos casos.
2. **Invariante P2** en `_normalized_decision`: sin pregunta ⇒ sin `answer` en
   start_flow. Backstop determinista del caso 1.
3. **F-1 sin fallback**: el "no tengo información / asesor" solo se emite ante una
   `Q` real (F-3/F-5), no cuando el cliente solo avanzó el flujo. Backstop del caso 2.
4. **Normalización F-i / F-iii / F-vi**: bajar `has_off_flow_question` en saludos,
   quejas y handoff, y cuando la bandera viene sin texto.

## 9. Estado de implementación

**Prompts (instrucciones separadas por caso).** `core/prompts.py`:
- `REPLY_EVALUATION_PROMPT` (PD2): bloques separados por intent (queja, handoff,
  rechazo, cambio, saludo, respuesta al paso, pregunta, unknown) + sección
  aparte para `has_off_flow_question` y reglas de coherencia.
- `RECEPTION_AGENT_PROMPT` (PD1): PASO 1 (¿hay pregunta?) → PASO 2 (action por
  prioridad, un caso por bloque, incluido **I-CTX** "solo contexto / pedido genérico
  de ayuda" → `clarify` sin RAG) → PASO 3 (answer_source) + estilo/seguridad.
  PASO 1 endurecido: no inferir pregunta implícita de una afirmación de contexto.

**Guardrails deterministas (backstop, validan y corrigen al LLM):**
- **P2** — [`reception_agent._normalized_decision`](../services/bot_agent/src/application/reception_agent.py:161):
  sin `has_question`, un `start_flow`/`answer_and_start_flow` queda con `answer`
  vacío (corrige el caso 1).
- **F-i / F-iii** — [`response_classifier._normalize`](../services/bot_agent/src/application/response_classifier.py):
  saludo/queja/handoff no llevan duda lateral; bandera de duda sin texto se baja.
- **F-1 (caso 2)** se resuelve en el prompt: detectar bien `has_off_flow_question`
  (un comentario/plan no es pregunta). No se añade supresión determinista porque
  distinguir una duda real de una falsa es interpretación de lenguaje (responsabilidad
  del LLM, CLAUDE.md §5); cuando la duda es real e irresoluble, F-3/F-5 sí ofrecen asesor.

Tests deterministas: [`tests/unit/test_decision_guardrails.py`](../services/bot_agent/tests/unit/test_decision_guardrails.py)
y el contrato de prompts [`tests/unit/test_prompt_contracts.py`](../services/bot_agent/tests/unit/test_prompt_contracts.py).
