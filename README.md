# AUTOS — Bot de recepción (escuela de manejo)

Agente conversacional de recepción para Telegram/WhatsApp construido con **LangGraph**.
Interpreta lenguaje natural (sin regex ni listas de palabras clave), responde con RAG sobre
una base de conocimiento y registra las conversaciones en NocoDB.

## Arquitectura

El proyecto es un monorepo orquestado con Docker Compose. Un único servicio de aplicación
(`bot_agent`) se ejecuta en tres roles a partir de la **misma imagen**:

| Servicio            | Rol                                                              |
| ------------------- | --------------------------------------------------------------- |
| `bot_agent`         | Bot de Telegram (long-polling)                                  |
| `whatsapp_webhook`  | API FastAPI/uvicorn para el webhook de WhatsApp (puerto 8010)   |
| `celery_worker`     | Tareas en segundo plano (recordatorios, publicidad, purga de historial) |

Infraestructura de apoyo:

| Servicio    | Uso                                                          |
| ----------- | ------------------------------------------------------------ |
| `postgres`  | Estado de conversación y checkpoints de LangGraph            |
| `redis`     | Buffer de mensajes y broker/result backend de Celery         |
| `qdrant`    | Base vectorial para el RAG (`escuela_manejo_kb`)             |
| `nocodb`    | Panel de administración y registro de logs/datos             |

El código del bot sigue una organización por capas en `services/bot_agent/src/`
(`domain/`, `application/`, `infrastructure/`, `core/`).

## Estructura del repositorio

```
.
├── docker-compose.yml          # Despliegue en la NUBE (EasyPanel) — archivo por defecto
├── docker-compose.local.yml    # Despliegue LOCAL (puertos expuestos, hot-reload del código)
├── mensajes.json               # Catálogo de mensajes del bot (montado en /mensajes.json)
├── services/
│   └── bot_agent/              # Servicio del bot (Dockerfile, src/, tests/)
├── data/                       # Estado persistente local (nocodb metadata, qdrant)
├── docs/                       # Documentación versionada
│   ├── reglas_agente_recepcion.md
│   ├── despliegue_docker_easypanel.md
│   ├── descripcion_funcional_bot.md
│   ├── diagramas/              # .mmd (fuentes); PDFs/SVGs se ignoran en git
│   └── ejemplos/               # logs de conversación de ejemplo
└── _local/                     # Notas, scripts scratch y overrides locales (ignorado por git)
```

> **Nota sobre `mensajes.json`:** es el catálogo de mensajes que el bot carga al iniciar.
> Vive en la raíz y ambos compose lo montan como `./mensajes.json:/mensajes.json:ro` en los
> tres servicios. El loader (`src/application/message_catalog.py`) lo busca primero en
> `/mensajes.json` (la ruta montada).

## Cómo ejecutar

Requiere un archivo `.env` en la raíz (no versionado). Variables principales:
`TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `POSTGRES_USER/PASSWORD/DB`,
`NOCODB_TOKEN`, `NOCODB_API_KEY` y las URLs `NOCODB_*` (ver los compose para la lista completa).

### Local

```bash
docker compose -f docker-compose.local.yml up --build
```

Expone Postgres (5432), Redis (6379), Qdrant (6333/6334), NocoDB (8080) y monta
`./services/bot_agent` en `/app` para recarga del código.

### Nube (EasyPanel)

```bash
docker compose up --build -d
```

Usa volúmenes con nombre, la red externa `easypanel` y no publica puertos directamente
(el reverse proxy de EasyPanel los enruta). Ver `docs/despliegue_docker_easypanel.md`.

## Retención del historial de conversaciones

El historial de conversaciones se conserva **hasta 20 días desde la última
interacción** de cada usuario; pasado ese plazo de inactividad se elimina
**automáticamente**. Es una ventana **deslizante**: cada mensaje nuevo reinicia
la cuenta, así que solo se borra el historial que lleve 20 días sin actividad.

El plazo es configurable con la variable `CONVERSATION_RETENTION_DAYS` (por
defecto `20`). Se aplica en dos capas, según dónde vive el historial:

| Dónde vive                          | Cómo se borra                                                                 |
| ----------------------------------- | ---------------------------------------------------------------------------- |
| **Redis** — estado/historial activo (`conversation_state:*`, `state:*`) | TTL deslizante: cada interacción reescribe la clave con un vencimiento de 20 días, así Redis la expira sola tras la inactividad. |
| **NocoDB** — log durable (tabla de conversaciones y, si está activa, la de *shots*) | Purga programada: una tarea de Celery (`purge_expired_conversations`) recorre la tabla a diario y elimina los registros cuya última actividad supere los 20 días. |

La purga la dispara **Celery beat**, embebido en el `celery_worker` (se ejecuta
con `worker -B`). Corre una vez al día (≈02:00 hora de Costa Rica). No hace falta
ningún servicio extra. La lógica de borrado es determinista y está cubierta por
`tests/unit/test_conversation_retention.py`.

> No afecta otros datos (usuarios bloqueados, dictámenes, base de conocimiento
> del RAG): la retención de 20 días aplica **solo** al historial de conversaciones.

## Tests

**Las dependencias de test viven en la imagen, no en el host.** No instales nada con
`pip` en tu máquina: los tests se ejecutan dentro del contenedor, en el mismo entorno
que la app.

- Dependencias de producción: `services/bot_agent/requirements.txt`
- Dependencias de desarrollo/test: `services/bot_agent/requirements-dev.txt` (incluye `pytest`)

El `Dockerfile` es multi-stage:
- etapa `prod` (final, por defecto) → la usa la nube (`docker-compose.yml`), sin pytest.
- etapa `dev` → la usa el local (`docker-compose.local.yml`), con las deps de test.

El código se monta por **bind mount** (`./services/bot_agent:/app`), así editas en tu
editor y el contenedor ve los cambios al instante; **solo reconstruyes la imagen cuando
cambian las dependencias**, no al cambiar código.

### Correr los tests

`docker compose` inyecta automáticamente las variables de `.env` (incluida
`OPENAI_API_KEY`) al contenedor, así que el LLM queda integrado en los tests:

```bash
# Suite completa (levanta dependencias y usa la key de .env):
docker compose -f docker-compose.local.yml run --rm bot_agent pytest

# Subconjunto / un archivo:
docker compose -f docker-compose.local.yml run --rm bot_agent pytest tests/unit
```

Hay **tres niveles** de tests:

1. **Deterministas** (la mayoría): mockean el LLM. Corren siempre, con o sin key.
2. **Integración con LLM real** (`@requires_llm` en `tests/regression`): ejercitan el
   clasificador/recepción reales. Con `OPENAI_API_KEY` se ejecutan; **sin key se saltan**
   (no fallan). Las llamadas de clasificación/recepción usan `temperature=0` para ser
   estables.
3. **Juez LLM semántico** (`tests/conversation_evals/test_llm_judge_evals.py`): evalúa la
   calidad de la respuesta con un LLM como juez. Solo corre con `RUN_LLM_EVALS=1` + key:

   ```bash
   docker compose -f docker-compose.local.yml run --rm -e RUN_LLM_EVALS=1 bot_agent pytest
   ```

> Para forzar el modo sin LLM (solo deterministas), pasa `-e OPENAI_API_KEY=`:
> `docker compose -f docker-compose.local.yml run --rm -e OPENAI_API_KEY= bot_agent pytest`
