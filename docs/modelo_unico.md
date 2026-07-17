# Modelo único: agente conversacional sin FSM

Esta rama reemplaza la máquina de estados (recepción → clasificador → router →
grafo de nodos de negocio) por **un agente único**: una sola decisión LLM por
turno (`gpt-5.4-mini` sin razonamiento — `reasoning_effort="none"` —, `temperature=0`) con guardrails deterministas en código.
La orquestación del turno sigue siendo un **StateGraph de LangGraph**, pero con
UN punto de decisión en lugar de un grafo de estados de negocio:

```
load_state → decide ──┬─ city_invitation ─→ END
                      └─ expand (fragmentos/RAG + anti-bucle)
                             ├─ reply   ─→ END
                             ├─ handoff ─→ END
                             └─ close   ─→ END
```

Motivación: el FSM obligaba a todos los clientes a pasar por la misma secuencia
de preguntas aunque ya hubieran dado los datos ("quiero alquilar una moto"
terminaba respondiendo preguntas de licencia). El agente único lee el historial,
salta pasos resueltos y entra directo al material que corresponde, sin perder
los textos curados del negocio.

## Dónde quedaron guardados los flujos

Los flujos NO se perdieron; cambiaron de forma:

| Antes (FSM) | Ahora (modelo único) |
| ----------- | -------------------- |
| Nodos y aristas en `flow_router.py` | **Playbooks** por intención en `UNIFIED_AGENT_PROMPT` (`core/prompts.py`): qué datos necesita cada proceso y qué fragmento entregar en cada situación. |
| Textos curados por nodo en `mensajes.json` | Igual: `mensajes.json` sigue siendo la única fuente de los textos. El agente los referencia como **fragmentos literales** `[[frag:FLUJO.NODO]]` y `fragment_catalog.py` los expande sin reescribirlos (estilo y emojis intactos). |
| `reporte` del nodo terminal | El fragmento conserva su metadato `reporte`: al enviarse queda `pending_report` y la siguiente respuesta del cliente se deriva al equipo (reporte + bloqueo), salvo que sea solo una duda informativa. |
| Recordatorios fijos por nodo | **Recordatorios inteligentes** (ver abajo). |
| Docs históricos (`tabla_decision_agente.md`, `reglas_agente_recepcion.md`) | Se conservan como referencia del diseño anterior. |

## Pipeline por turno

1. `application/unified_agent.py` — `UnifiedAgent.decide()`: system prompt =
   instrucciones estáticas + catálogo de fragmentos (cacheable); los datos del
   turno (mensaje, historial, pendiente, reporte_pendiente, recordatorios)
   viajan como JSON en el mensaje del usuario. Salida validada por código.
2. `application/agent_pipeline.py` — `AgentPipeline.run()`: grafo LangGraph que
   ejecuta la decisión con guardrails deterministas en sus nodos:
   - Expande `[[frag:ID]]` al texto literal (resuelve variantes `_1` por
     registro de keyword, como hacía el router).
   - Expande `[[rag]]` con `RagService`; si el RAG no tiene respaldo, envía el
     fallback, registra la pregunta sin respuesta y NO empuja el flujo.
   - **Anti-bucle**: si el turno repite exactamente lo que el bot ya dijo en
     los últimos 2 turnos → handoff automático. También se deduplican mensajes
     idénticos dentro del mismo turno.
   - `handoff` → mensaje humano + reporte en NocoDB + bloqueo 12 días +
     limpieza de contexto (idéntico al comportamiento anterior).
   - `close` → despedida y limpieza de estado.
   - `city_invitation` → delega en `PublicidadService` (invitación por ciudad);
     si la ciudad no existe, reporte + bloqueo como antes.
3. El historial guarda los fragmentos como etiquetas (`[[frag:ID]]`), no el
   texto completo: el prompt y Redis se mantienen livianos y el modelo sabe
   exactamente qué recibió el cliente.

Acciones del modelo: `reply | handoff | close | city_invitation`. El código
nunca confía en el JSON del modelo (`_validated_decision` + fallback seguro que
pregunta con las opciones de servicio si el LLM falla).

## Recordatorios inteligentes

Tras cada respuesta con algo pendiente se agenda `send_smart_reminder`
(Celery, countdown `FOLLOWUP_FIRST_DELAY_SECONDS`, default 40s). Al vencer:

1. Guardas duras en código (anti-bucle): se omite si hay mensaje del cliente
   en buffer, si está bloqueado, si no hay nada pendiente, si ya se alcanzó
   `FOLLOWUP_MAX_REMINDERS` (default 2) o si la tarea quedó obsoleta.
2. `FollowupAgent` (LLM, `FOLLOWUP_AGENT_PROMPT`) analiza la conversación y
   decide si conviene retomar y redacta UN mensaje corto con el estilo de la
   casa ("📌 Hola!!!"), personalizado al punto exacto donde quedó el chat.
   Puede decidir NO enviar (cliente se despidió, dio un plazo, está molesto).
3. Si envía, sube `reminder_level` y agenda el siguiente nivel con
   `FOLLOWUP_NEXT_DELAY_SECONDS` (default 2h).

Los recordatorios se cancelan solos cuando el cliente escribe
(`ReminderService.cancel` al inicio de cada turno), igual que antes.

## Qué NO cambió

- Comandos (`/d`, `/block`, `grupo["…"]`, `add["…"]`) y keywords
  (`tareas`/`transporte`): siguen en `conversation_orchestrator.py`, con match
  exacto intencional.
- Publicidad, bienvenida a grupos, buffers, candado por conversación,
  `scoped_key`, retención de 20 días, RAG y su webhook.
- `mensajes.json` en la raíz, montado como `/mensajes.json:ro`.

## Tests

- Deterministas (sin OpenAI): `tests/unit/test_agent_pipeline.py`,
  `test_fragment_catalog.py`, `test_smart_reminder.py`, `test_prompt_contracts.py`.
- Juicio del LLM (skip sin key): `tests/regression/test_unified_agent_llm.py`
  (`@requires_llm`), incluye el caso "alquilar una moto" sin preguntas inútiles,
  escalamiento por enojo, reporte pendiente y contención del recordatorio.
