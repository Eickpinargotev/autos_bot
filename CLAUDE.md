# CLAUDE.md — Guía para agentes en este proyecto

Reglas operativas para cualquier agente (Claude u otro) que trabaje en este repo.
Léelas antes de tocar código. Para el panorama general del producto, ver `README.md`.

El proyecto es un bot de recepción (Telegram/WhatsApp) para una escuela de manejo,
hecho con **LangGraph**, orquestado con **Docker Compose**. El código del bot está en
`services/bot_agent/`.

---

## 1. Entorno y dependencias (regla de oro)

- **Las dependencias viven en la imagen Docker, NO en el host.** Nunca corras `pip install`
  en la máquina del usuario ni asumas que `python` del host tiene las librerías.
- Producción vs desarrollo están separadas:
  - `services/bot_agent/requirements.txt` → dependencias de producción.
  - `services/bot_agent/requirements-dev.txt` → producción + herramientas de test (`pytest`).
- El `Dockerfile` es **multi-stage**:
  - `base`: deps de producción + código.
  - `dev`: `base` + deps de test. La usa `docker-compose.local.yml` (`target: dev`).
  - `prod`: etapa **final/por defecto**, sin deps de test. La usa `docker-compose.yml` (`target: prod`).
  - Como `prod` es la última etapa, `docker build` sin `--target` produce la imagen de producción.
- El código se monta por **bind mount** (`./services/bot_agent:/app`): editas en tu editor y
  el contenedor ve los cambios al instante. **Solo reconstruyes la imagen si cambian las
  dependencias** (`requirements*.txt`) o el `Dockerfile`, no al cambiar código.

## 2. Cómo correr los tests

Siempre dentro del contenedor (etapa `dev`):

```bash
docker compose -f docker-compose.local.yml run --rm bot_agent pytest
docker compose -f docker-compose.local.yml run --rm bot_agent pytest tests/unit   # subconjunto
```

- Config de pytest: `services/bot_agent/pytest.ini` (`pythonpath = .` para resolver `from src...`).
- `docker compose run` inyecta `.env` (incl. `OPENAI_API_KEY`) en el contenedor, así que el
  LLM queda integrado en los tests sin pasos extra.

Hay **tres niveles** de tests; respétalos al escribir nuevos:

1. **Deterministas** (preferidos): mockean el LLM (`agent.decide`, `followup.decide`,
   `rag.answer_question`). Corren con o sin key. Para lógica de pipeline/guardrails usa
   SIEMPRE este estilo (regla del proyecto: *no depender de OpenAI real en tests
   determinísticos*). Ver `tests/unit/test_agent_pipeline.py` y `test_smart_reminder.py`.
2. **Integración con LLM real**: decóralos con `@requires_llm` (definido en
   `tests/regression/test_unified_agent_llm.py`). Úsalos solo cuando el objetivo sea
   verificar el juicio del LLM, no el cableado del pipeline. **Consumen tokens de
   OpenAI del dueño**: están apagados por defecto (skip) y solo corren con
   `RUN_LLM_TESTS=1`, ÚNICAMENTE cuando el usuario lo pida de forma explícita.
   Lo mismo aplica a scripts de humo con LLM real: no los corras sin pedido expreso.

- Las llamadas de **decisión del agente y recordatorio** usan `temperature=0` (decisiones
  deterministas). No subas la temperatura en esas tareas; mantenlas estables. La generación
  de texto libre (RAG, publicidad) puede usar otra temperatura.
- Comandos: ver `README.md` (sección Tests).

## 3. Los dos docker-compose (no romper ninguno)

- `docker-compose.yml` → **NUBE** (EasyPanel). Es el archivo por defecto. Usa volúmenes con
  nombre, red externa `easypanel`, `expose` sin publicar puertos, y build `target: prod`.
- `docker-compose.local.yml` → **LOCAL**. Publica puertos, bind-mount del código y build
  `target: dev`.
- Tras cualquier cambio a compose/Dockerfile, valida **ambos**:
  ```bash
  docker compose -f docker-compose.yml config -q
  docker compose -f docker-compose.local.yml config -q
  ```

## 4. Arquitectura del bot y dónde tocar

Capas en `services/bot_agent/src/`: `domain/`, `application/`, `infrastructure/`, `core/`.

Pipeline de conversación (**supervisor/workers**, ver `docs/modelo_unico.md` y el
diseño de áreas en `docs/diseno_especialistas.md`):
1. `application/unified_agent.py` — `SupervisorAgent` (coordina: saludo, ambiguo, queja,
   WIN, cierre, dudas sueltas, y enruta con `route`) y `SpecialistAgent(area)` (GENERAL,
   CURSO_TEORICO, ALQUILER, CLASES, DICTAMEN, TRAMITES; devuelve el turno con `defer`).
   `gpt-5.4-mini` sin razonamiento, `temperature=0`; prompts = contrato común + playbook +
   catálogo del área. También `FollowupAgent` (recordatorios).
2. `application/agent_pipeline.py` — StateGraph de **LangGraph** con routing pegajoso
   (`ConversationState.active_agent`) y guardrails **deterministas** en sus nodos:
   expansión de fragmentos literales y RAG, fragmentos ajenos rechazados por área,
   anti-bucle y anti ping-pong (defer), reporte + bloqueo en handoff, estado e historial.
3. `application/fragment_catalog.py` — fragmentos literales derivados de `mensajes.json`,
   particionados por área (`AREA_FRAGMENTS`; variantes `_1` resueltas por código).

Reglas para cambios:
- Para cambiar **el comportamiento del agente** (qué intención hace qué, playbooks), edita el
  **prompt** (`core/prompts.py`, siguiendo §6). Los efectos duros (bloqueos, reportes,
  anti-bucle, expansión) viven en `agent_pipeline.py`; no los muevas al prompt.
- Para cambiar **el texto de los mensajes** del bot, edita `mensajes.json` (ver §7). No metas
  texto de negocio en el código ni en los prompts.

Documentación de referencia (mantenerla al día si tocas esas áreas):
- `docs/operacion_escala_y_trazabilidad.md` — concurrencia, buffers, garantías
  anti-duplicados/cruces, capacidad y trazado de herramientas.
- `docs/seguridad.md` — postura de seguridad y checklist de despliegue.
- `docs/gobernanza_de_prompts.md` — proceso completo para crear/editar prompts.

Retención del historial (20 días desde la última interacción, ventana deslizante):
- El plazo lo controla `settings.CONVERSATION_RETENTION_DAYS` (por defecto 20). Para cambiarlo,
  ajusta esa variable; no hardcodees el número en otra parte.
- **Redis** (`conversation_state:*`) usa TTL deslizante: `ConversationStateRepo.set`
  reescribe la clave con `ex=...` en cada interacción.
- **NocoDB** (log durable y *shots*) se purga con la tarea Celery `purge_expired_conversations`,
  agendada por **Celery beat**. Por eso el `celery_worker` corre con `-B` en ambos compose: si
  tocas ese comando, conserva el beat o la purga deja de ejecutarse. Helpers de borrado:
  `infrastructure/repositories/nocodb_retention.py`. Tests: `tests/unit/test_conversation_retention.py`.

## 5. Diseño conversacional (playbooks + mensajes curados)

El reto del sistema es mezclar **mensajes curados del negocio** (plantillas literales) con un
**flujo conversacional** razonado. Reglas para que sea natural y no robótico:

- **Separación de capas:**
  - *Textos curados (deterministas):* viven en `mensajes.json` y se envían LITERALES vía
    `[[frag:ID]]` (`fragment_catalog.py` los expande; el LLM NO los reinventa ni parafrasea).
    Todo dato variable (precios, links, requisitos) sale de un fragmento o del RAG.
  - *Capa conversacional (razonada con contexto):* el agente decide con el historial y el
    **paso pendiente como dato** (qué espera el bot) qué sigue: responder la duda, avanzar
    el playbook o ambas. El contexto del estado se pasa como DATO, no como ramas hardcodeadas.
- **Re-anclaje consciente de la resolución:** no insistir con el paso del proceso ("retomemos…")
  si la duda del cliente **no quedó resuelta** (un RAG sin respaldo no empuja el flujo; los
  recordatorios inteligentes solo retoman lo pendiente cuando conviene).
- **Prohibido el prompt overfitting:** las instrucciones se dan por **contexto/intención**,
  nunca por frase exacta. ❌ "si el usuario dice 'hola', responde X". ✅ "si el mensaje
  contiene un saludo, haz X". La diversidad de expresión humana es infinita; una regla por
  frase confunde al modelo. Los ejemplos *few-shot* se permiten solo como ilustración del
  principio, no como reglas rígidas por caso.
- **Prohibido regex / coincidencia exacta para interpretar lenguaje natural** del cliente.
  Interpretar intención es responsabilidad exclusiva del LLM con instrucciones contextuales.
  *Excepción consciente:* comandos estructurados (`/d`, `/block`, `grupo["…"]`, `add["…"]`) y
  disparadores de keyword del negocio ("tareas"/"transporte") NO son interpretación de NL; ahí
  el match exacto es intencional.

## 6. Política de edición de prompts (OBLIGATORIA)

Los prompts (`core/prompts.py`) son la lógica de negocio más sensible del sistema:
**no se editan "así no más"**. El proceso completo está en
`docs/gobernanza_de_prompts.md`; resumen ejecutable:

1. **Confirma que el bug es del prompt** y no del router, del grafo, de
   `mensajes.json` o de la normalización de salida (`_validated_decision`).
   La mayoría de "bugs de prompt" son de otra capa.
2. **La regla nueva se escribe por intención/contexto, nunca por frase exacta**, y va
   dentro del bloque del caso correspondiente (secciones `═══`), respetando el orden
   de prioridad — nunca como parche al final.
3. **Si cambias el esquema de salida**, cambia en el mismo commit el validador en
   código y sus tests. El código nunca confía en que el modelo cumpla el JSON.
4. **Cubre el caso con un test** (determinista para el cableado, `@requires_llm` para
   el juicio del LLM) y **corre la suite completa con key** — un cambio de prompt sin
   regresiones LLM no está verificado.
5. **Un cambio conceptual por commit**, con justificación del caso real que falla.
6. Nada de conocimiento de negocio variable (precios, links, horarios) en el prompt:
   eso vive en el RAG o en `mensajes.json`. Y `temperature=0` en toda decisión.

## 7. Restricciones duras (rompen si las ignoras)

- **`mensajes.json` vive en la RAÍZ del repo.** Ambos compose lo montan como
  `./mensajes.json:/mensajes.json:ro` en los 3 servicios. El loader es
  `src/application/message_catalog.py` (busca primero `/mensajes.json`). No lo muevas ni
  dupliques.
- **Los prompts deben ser genéricos.** El test `tests/unit/test_prompt_contracts.py` prohíbe
  términos específicos del catálogo en `UNIFIED_AGENT_PROMPT` y `FOLLOWUP_AGENT_PROMPT`
  (p. ej. `casco`, `programar cita`, `qué pasa si pierde`). Usa ejemplos genéricos.
- Ese mismo test exige que ciertas frases clave **existan** en los prompts. Si reescribes un
  prompt, conserva esas frases o actualiza el test de forma deliberada.
- **Invariantes de concurrencia** (garantizan cero duplicados/cruces; detalle en
  `docs/operacion_escala_y_trazabilidad.md`). No los debilites al tocar el pipeline:
  - Todo estado por usuario en Redis usa `scoped_key(prefijo, canal, user_id)` —
    nunca claves sin canal.
  - El buffer se drena SOLO con los scripts Lua atómicos de `buffer_service.py` y el
    debounce por `seq`.
  - `process_buffered_messages` corre bajo el **candado por conversación**
    (`processing:*`, tests en `tests/unit/test_processing_lock.py`): un solo turno en
    proceso por usuario.
  - El `visibility_timeout` de Celery debe superar el countdown más largo agendado
    (`celery_app.py`); si agregas un delay mayor, inclúyelo en `_max_countdown_seconds`.
- **El webhook de RAG exige `NOCODB_RAG_WEBHOOK_TOKEN`** (503 si falta). No lo
  "arregles" abriéndolo: escribe en la base de conocimiento (ver `docs/seguridad.md`).

## 8. Convenciones del repo

- `docs/` está **versionado** (fuentes `.md`/`.mmd`; los PDF/SVG generados se ignoran).
- `_local/` está **ignorado** por git: notas personales, scripts scratch, overrides locales.
- Código y comentarios en **español**, para coincidir con el resto del codebase.
- No hagas commit ni push salvo que el usuario lo pida. Si hay que commitear, **ramifica
  desde `main`** primero.
