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
| `celery_worker`     | Tareas en segundo plano (recordatorios, publicidad, etc.)       |

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

## Tests

```bash
cd services/bot_agent
pytest
```
