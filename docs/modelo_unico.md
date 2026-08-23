# Modelo único: agente conversacional sin FSM (supervisor / workers)

Esta rama reemplaza la máquina de estados (recepción → clasificador → router →
grafo de nodos de negocio) por una arquitectura **Supervisor / Workers** con
LLM (`gpt-5.4-mini` sin razonamiento — `reasoning_effort="none"` —,
`temperature=0`) y guardrails deterministas en código, orquestada con un
**StateGraph de LangGraph**:

```
load_state ──┬─(sin especialista)──→ SUPERVISOR ──── route ───→ ESPECIALISTA
             └─(especialista activo, sticky)─────────────────→ (GENERAL | CURSO_TEORICO | ALQUILER | CLASES | DICTAMEN | TRAMITES)
                      ▲                                              │
                      └───────────────── defer (máx. 1 vez) ─────────┘
    supervisor/especialista ──┬─ city_invitation ─→ END
                              └─ expand (fragmentos/RAG + anti-bucle)
                                     ├─ reply   ─→ END
                                     ├─ handoff ─→ END
                                     └─ close   ─→ END
```

Reparto de responsabilidades:
- **Supervisor** (coordinador/recepción): saludo, mensajes ambiguos, quejas
  (Q1/handoff), WIN, cierres, dudas informativas sueltas ([[rag]]) y el
  **enrutamiento** al especialista del área (`action="route"`).
- **Especialistas** (GENERAL/intake de licencia, CURSO_TEORICO, ALQUILER, CLASES,
  DICTAMEN, TRAMITES — mapa completo en `docs/diseno_especialistas.md`): ejecutan su
  proceso con SU playbook y SU catálogo particionado; si el tema no es suyo,
  devuelven el turno (`action="defer"`).
- Cada agente = CONTRATO COMÚN (reglas transversales: queja→humano,
  pago→humano, reporte pendiente, correcciones, estilo) + su playbook + su
  catálogo. Prompts cortos → menos alucinación y mejor caching.

Eficiencia: el routing es **pegajoso** (`ConversationState.active_agent`): una
vez enrutada, la conversación entra directo al especialista (UNA llamada LLM
por turno). Solo el primer turno o un cambio de área cuestan dos. Guardrails
anti ping-pong: un especialista solo puede deferir una vez por turno, el
supervisor no puede re-enrutar al área que ya defirió y máximo 2 áreas por
turno (luego aclaración segura).

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
| Docs históricos (`tabla_decision_agente.md`, `reglas_agente_recepcion.md`) | Eliminados (quedan en el historial de git); el diseño vigente está en `docs/diseno_especialistas.md`. |

## Pipeline por turno

1. `application/unified_agent.py` — `SupervisorAgent` y `SpecialistAgent(area)`
   (base común `_DecisionAgent`): system prompt = contrato común + playbook del
   rol + catálogo del área (cacheable); los datos del turno (mensaje,
   historial, pendiente, reporte_pendiente, recordatorios, nota_interna)
   viajan como JSON en el mensaje del usuario. Salida validada por código
   (acciones por rol: el supervisor puede `route`, el especialista `defer`).
2. `application/agent_pipeline.py` — `AgentPipeline.run()`: grafo LangGraph que
   ejecuta la decisión con guardrails deterministas en sus nodos:
   - Expande `[[frag:ID]]` al texto literal (resuelve variantes `_1` por
     registro de keyword, como hacía el router). **Un agente solo puede enviar
     fragmentos de su propio catálogo** (`AREA_FRAGMENTS`): etiquetas ajenas se
     descartan (guardrail contra alucinación cruzada).
   - Expande `[[rag]]` con `RagService`; si el RAG no tiene respaldo, envía el
     fallback, registra la pregunta sin respuesta y NO empuja el flujo.
   - **Anti-bucle**: si el turno repite exactamente lo que el bot ya dijo en
     los últimos 2 turnos → handoff automático. También se deduplican mensajes
     idénticos dentro del mismo turno.
   - `handoff` → mensaje humano + reporte en NocoDB + bloqueo 12 días +
     limpieza de contexto (idéntico al comportamiento anterior).
   - `close` → despedida y limpieza de estado.
   - `city_invitation` → delega en `PublicidadService`, que manda el mensaje del
     panel cuya clave es esa ciudad; si no existe, reporte + bloqueo como antes.
3. El historial guarda los fragmentos como etiquetas (`[[frag:ID]]`), no el
   texto completo: el prompt y Redis se mantienen livianos y el modelo sabe
   exactamente qué recibió el cliente.

Acciones del modelo: `reply | handoff | close | city_invitation`. El código
nunca confía en el JSON del modelo (`_validated_decision` + fallback seguro que
pregunta con las opciones de servicio si el LLM falla).

## Recordatorios inteligentes

Tras cada respuesta con algo pendiente se agenda `send_smart_reminder` con el
intervalo del proyecto (`proyecto_recordatorios`, una hora por defecto). El
dueño puede apagarlo o cambiarlo desde **Prompts**. Al vencer:

1. Guardas duras en código (anti-bucle): se omite si hay mensaje del cliente
   en buffer, si está bloqueado, si no hay nada pendiente, si ya se alcanzó
   `FOLLOWUP_MAX_REMINDERS` (default 2) o si la tarea quedó obsoleta.
2. `FollowupAgent` (LLM, `FOLLOWUP_AGENT_PROMPT`) analiza la conversación y
   decide si conviene retomar y redacta UN mensaje corto con el estilo de la
   casa ("📌 Hola!!!"), personalizado al punto exacto donde quedó el chat.
   Puede decidir NO enviar (cliente se despidió, dio un plazo, está molesto).
3. Si envía, sube `reminder_level` y agenda el siguiente nivel con el mismo
   intervalo vigente del proyecto.

Los recordatorios se cancelan solos cuando el cliente escribe
(`ReminderService.cancel` al inicio de cada turno). Una tarea ya agendada
también vuelve a leer el switch antes de enviar, así que apagarlo surte efecto
sin recorrer la cola de Celery.

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
