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

1. **Deterministas** (preferidos): mockean el LLM (`reception.decide`, `classify_reply`,
   `rag.answer_question`). Corren con o sin key. Para lógica de flujo/router usa SIEMPRE este
   estilo (regla del proyecto: *no depender de OpenAI real en tests determinísticos*).
2. **Integración con LLM real**: decóralos con `@requires_llm` (definido en
   `tests/regression/test_flow_graph_regressions.py`), que hace skip si no hay key. Úsalos
   solo cuando el objetivo sea verificar el juicio del LLM, no el cableado del grafo.
3. **Juez LLM** (`SemanticJudge`): corre solo con `RUN_LLM_EVALS=1` + key
   (`SemanticJudge.enabled()`).

- Las llamadas de **clasificación/recepción/juez** usan `temperature=0` (decisiones
  deterministas). No subas la temperatura en esas tareas; mantenlas estables. La generación
  de texto libre (RAG, publicidad) puede usar otra temperatura.
- Comandos: ver `README.md` (sección Tests) y `tests/conversation_evals/README.md`.

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

Pipeline de conversación:
1. `application/reception_agent.py` — intake con LLM para usuarios nuevos/sin flujo.
2. `application/response_classifier.py` — clasifica la respuesta del usuario dentro de un
   flujo activo (intent + value + pregunta lateral), vía LLM.
3. `application/flow_router.py` — enrutamiento **determinista** (estado → siguiente nodo).
4. `application/flow_graph.py` — orquestación con LangGraph (LangGraph StateGraph).

Reglas para cambios:
- Para cambiar **el comportamiento del flujo** (qué intención avanza, a qué nodo), edita el
  **prompt** (`core/prompts.py`) o el **router** (`flow_router.py`). No cablees lógica en el grafo.
- Para cambiar **el texto de los mensajes** del bot, edita `mensajes.json` (ver §6). No metas
  texto de negocio en el código ni en los prompts.

## 5. Diseño conversacional (FSM + LLM)

El reto del sistema es mezclar un **flujo automático de mensajes curados** (FSM) con un
**flujo conversacional** (responder dudas). Reglas para que sea natural y no robótico:

- **Separación de capas:**
  - *Esqueleto FSM (determinista):* qué nodo sigue (`flow_router.py`) y los **mensajes
    curados** del flujo (`mensajes.json`: precios, links, guiones). El LLM NO los reinventa.
  - *Capa conversacional (razonada con contexto):* cuando el usuario no avanza el flujo
    (duda, saludo…), se decide con el **paso pendiente como dato** (qué espera el bot)
    cómo responder. El contexto del estado se pasa como DATO, no como ramas hardcodeadas.
- **Re-anclaje consciente de la resolución:** no insistir con el paso del flujo ("retomemos…")
  si la duda del cliente **no quedó resuelta**. Solo reanclar cuando se resolvió y aún aporta
  (ver `_off_flow_replies` en `flow_graph.py`).
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

## 6. Restricciones duras (rompen si las ignoras)

- **`mensajes.json` vive en la RAÍZ del repo.** Ambos compose lo montan como
  `./mensajes.json:/mensajes.json:ro` en los 3 servicios. El loader es
  `src/application/message_catalog.py` (busca primero `/mensajes.json`). No lo muevas ni
  dupliques.
- **Los prompts deben ser genéricos.** El test `tests/unit/test_prompt_contracts.py` prohíbe
  términos específicos del catálogo en `REPLY_EVALUATION_PROMPT` y `RECEPTION_AGENT_PROMPT`
  (p. ej. `casco`, `programar cita`, `qué pasa si pierde`). Usa ejemplos genéricos.
- Ese mismo test exige que ciertas frases clave **existan** en los prompts. Si reescribes un
  prompt, conserva esas frases o actualiza el test de forma deliberada.

## 7. Convenciones del repo

- `docs/` está **versionado** (fuentes `.md`/`.mmd`; los PDF/SVG generados se ignoran).
- `_local/` está **ignorado** por git: notas personales, scripts scratch, overrides locales.
- Código y comentarios en **español**, para coincidir con el resto del codebase.
- No hagas commit ni push salvo que el usuario lo pida. Si hay que commitear, **ramifica
  desde `main`** primero.
