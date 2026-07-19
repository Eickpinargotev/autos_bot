# AUTOS — Bot de recepción (escuela de manejo)

Agente conversacional de recepción para Telegram/WhatsApp: un **agente único con LLM** orquestado con **LangGraph** (ver `docs/modelo_unico.md`).
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
| `postgres`  | Usuarios bloqueados y registro de dictamen (no almacena conversaciones) |
| `redis`     | Estado/historial activo de la conversación, buffer de mensajes y broker/result backend de Celery |
| `qdrant`    | Base vectorial para el RAG (`escuela_manejo_kb`)             |
| `nocodb`    | Panel de administración y registro durable de conversaciones/datos |

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
│   ├── operacion_escala_y_trazabilidad.md  # concurrencia, anti-duplicados, capacidad, trazado
│   ├── seguridad.md                        # postura de seguridad + checklist de despliegue
│   ├── gobernanza_de_prompts.md            # proceso obligatorio para crear/editar prompts
│   ├── modelo_unico.md                     # arquitectura supervisor/workers vigente
│   ├── diseno_especialistas.md             # mapa de especialistas, mensajes fijos y prompts
│   ├── despliegue_docker_easypanel.md
│   ├── descripcion_funcional_bot.md
│   ├── arquitectura_general.md
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

> **Seguridad:** el webhook de sincronización RAG queda **deshabilitado** (503) hasta
> que definas `NOCODB_RAG_WEBHOOK_TOKEN`; sin él, el RAG se actualiza igual por
> sincronización lazy cada 5 min. Checklist completo en `docs/seguridad.md`.

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

## Persistencia y durabilidad de datos

Dónde vive cada cosa (importante para no confundir "memoria" con "se pierde al apagar"):

| Dato | Almacén | Durabilidad |
| ---- | ------- | ----------- |
| **Estado activo de la conversación** (en qué paso del flujo va cada usuario + historial reciente) | **Redis** (`conversation_state:*`, `state:*`) | En memoria **con persistencia a disco** (volumen + AOF). |
| **Registro durable de conversaciones** (el log que se ve en el panel) | **NocoDB** | Persistente en disco. |
| **Usuarios bloqueados y registro de dictamen** | **Postgres** | Persistente en disco. |
| **Base de conocimiento del RAG** | **Qdrant** | Persistente en disco. |

### ¿Por qué Redis con AOF (`appendonly yes`)?

Redis es en memoria, pero **no es volátil** en este proyecto: ambos compose lo corren
con `redis-server --appendonly yes` y un volumen montado (`/data`). Lo activamos por dos razones:

- **No perder el estado al reiniciar.** Con AOF, Redis reconstruye su estado desde
  disco al arrancar. Un reinicio planificado (deploy, `restart`) **no pierde nada**.
- **Minimizar la pérdida ante una caída dura.** Sin AOF (solo snapshots RDB por
  defecto), un corte de luz o crash podía perder hasta **varios minutos** de cambios
  (lo escrito desde el último snapshot). Con AOF la ventana de pérdida baja a **~1 segundo**.

Qué implica en la práctica: si Redis perdiera esa ventana, lo único afectado sería
"en qué nodo del flujo estaba cada conversación activa" (esos usuarios volverían al
intake). El **historial guardado de las conversaciones no se pierde**, porque su copia
durable está en **NocoDB**, no en Redis.

> Sobre carga: Redis maneja decenas de miles de operaciones por segundo. A ritmos como
> ~200 chats/minuto (≈3–4 mensajes/s) Redis no es el cuello de botella; lo son las
> llamadas al LLM y la concurrencia del `celery_worker`.

## Cola y concurrencia del worker

Cada mensaje del cliente se procesa como una tarea de **Celery**, con **Redis como
broker** (cola de tareas). Si todas las "ranuras" de procesamiento están ocupadas, las
tareas nuevas **esperan en la cola** hasta que se libere una.

El `celery_worker` corre con `--pool=threads --concurrency=20`: hasta **20 tareas en
paralelo**.

### ¿Por qué hilos y por qué 20 (y no se satura la CPU)?

- **Las tareas son I/O-bound, no CPU-bound.** Cada turno hace una o más llamadas al LLM
  (supervisor/especialista + RAG): el ~95% del tiempo el hilo está **esperando la
  respuesta de OpenAI por la red**, con la CPU ociosa.
- **Hilos, no procesos.** Con `--pool=threads` los 20 son hilos dentro de **un solo
  proceso**. Por el **GIL** de Python, solo un hilo ejecuta cálculo a la vez, así que
  20 hilos **no consumen 20 núcleos**: el trabajo real de CPU equivale a ~1 núcleo. Y
  mientras un hilo espera la red, **suelta el GIL** y consume ~0% CPU. Por eso 20 hilos
  son seguros incluso en una máquina de 4 núcleos ocupada: el límite es la red, no el
  cálculo. *(Lo contrario —`--pool=prefork --concurrency=20`— sí saturaría: serían 20
  procesos reales compitiendo por la CPU y 20× memoria.)*
- **El número 20** sale de la carga objetivo: ~200 chats/min ≈ 3,3 msg/s; si cada tarea
  tarda ~3 s, se necesitan ~10 concurrentes para no acumular (regla de Little), y 20 da
  ~2× de margen para picos y respuestas lentas del LLM. Se ajusta cambiando
  `--concurrency` en ambos compose.
- **El techo real** lo pone el **rate limit de OpenAI**, no la CPU ni Redis.

> **Thread-safety:** el código es seguro para el pool de hilos: los objetos compartidos
> son de solo lectura (catálogo de `mensajes.json`), clientes thread-safe (`redis`,
> `openai`) o aislados por hilo (el trazado de *shots* usa `contextvars`). El grafo se
> invoca con estado por llamada y las conexiones a Postgres se crean por uso.

> **Integridad por conversación:** además del debounce del buffer, cada turno corre
> bajo un **candado por conversación** (`processing:canal:user_id`): dos mensajes del
> mismo usuario nunca se procesan en paralelo (no se pisan el estado ni salen
> respuestas desordenadas), sin bloquear a los demás usuarios. Detalle completo en
> `docs/operacion_escala_y_trazabilidad.md`.

> **Escalado horizontal:** si más adelante hicieran falta más réplicas del
> `celery_worker`, el beat (`-B`) debe quedar en **una sola** réplica para no duplicar la
> purga diaria; habría que separar el `celery beat` en su propio servicio.

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

Hay **dos niveles** de tests:

1. **Deterministas** (la mayoría): mockean el LLM. Corren siempre, con o sin key.
2. **Integración con LLM real** (`@requires_llm` en
   `tests/regression/test_unified_agent_llm.py`): ejercitan el juicio real de los
   agentes y **consumen tokens de OpenAI**, así que están apagadas por defecto
   (se saltan). Se habilitan solo bajo demanda:

   ```bash
   docker compose -f docker-compose.local.yml run --rm -e RUN_LLM_TESTS=1 bot_agent pytest tests/regression
   ```

> Para forzar el modo sin LLM (solo deterministas), pasa `-e OPENAI_API_KEY=`:
> `docker compose -f docker-compose.local.yml run --rm -e OPENAI_API_KEY= bot_agent pytest`
