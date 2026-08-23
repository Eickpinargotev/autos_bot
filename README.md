# AUTOS — Bot de recepción (escuela de manejo)

Agente conversacional de recepción para Telegram/WhatsApp: un **agente único con LLM** orquestado con **LangGraph** (ver `docs/modelo_unico.md`).
Interpreta lenguaje natural (sin regex ni listas de palabras clave), responde con RAG sobre
una base de conocimiento y registra todo en Postgres, que se consulta desde un
**dashboard propio** (`services/dashboard/`) con roles, facturación en vivo,
catálogos editables y envíos manuales.

## Arquitectura

El proyecto es un monorepo orquestado con Docker Compose. El servicio de aplicación
del bot (`bot_agent`) se ejecuta en tres roles a partir de la **misma imagen**:

| Servicio            | Rol                                                              |
| ------------------- | --------------------------------------------------------------- |
| `bot_agent`         | Bot de Telegram (long-polling)                                  |
| `whatsapp_webhook`  | API FastAPI/uvicorn para el webhook de WhatsApp (puerto 8010)   |
| `celery_worker`     | Tareas en segundo plano (recordatorios, publicidad, purga de historial, cola de envíos manuales) |

Y un segundo servicio propio, con su imagen y su dominio:

| Servicio    | Rol                                                              |
| ----------- | ---------------------------------------------------------------- |
| `dashboard` | Panel de operación (FastAPI + Jinja, puerto 8020). Roles `admin` y `cliente`. |

Infraestructura de apoyo:

| Servicio    | Uso                                                          |
| ----------- | ------------------------------------------------------------ |
| `postgres`  | **Fuente de verdad**: datos operativos, todos con `proyecto_id`, y usuarios del panel |
| `redis`     | Estado activo y colas; cada clave comercial incluye proyecto + canal + cliente final |
| `qdrant`    | Base vectorial RAG con filtro obligatorio por proyecto       |

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
├── data/                       # Estado persistente local (qdrant)
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
`SESSION_SECRET` (dashboard), y opcionalmente `PUBLIC_WEBHOOK_BASE_URL` para WhatsApp
e `INTERNAL_API_TOKEN` para el reindexado inmediato del RAG. Ver los compose para la
lista completa.

> Las credenciales de WhatsApp **no** van en el `.env`: son de cada negocio y se
> administran desde el panel, en el perfil del cliente. El `.env` solo lleva lo que
> es del despliegue entero.

> **WhatsApp:** la URL del webhook de cada cliente y los eventos que hay que activar
> en WasenderAPI están en el perfil del cliente (`/admin/negocios/{id}`) del panel y explicados en
> `docs/whatsapp_wasender.md`.

> **Seguridad:** el dashboard **no arranca** sin `SESSION_SECRET` (con un secreto vacío
> las cookies serían falsificables). El webhook de WhatsApp y el endpoint interno del RAG
> quedan **deshabilitados** (503) mientras falte su secreto, nunca abiertos.
> Checklist completo en `docs/seguridad.md`.

### Local

```bash
docker compose -f docker-compose.local.yml up --build
```

Publica Postgres, Redis, Qdrant, el webhook y el dashboard, y monta el código de
ambos servicios para recarga en caliente.

**Los puertos del host son automáticos**: Docker elige uno libre para cada
servicio, así que esta pila no choca con otra que ya tenga ocupado el 5432 o el
8020. Para saber en cuál quedó el panel:

```bash
docker compose -f docker-compose.local.yml port dashboard 8020
```

`docker compose -f docker-compose.local.yml ps` los lista todos. El puerto cambia
cada vez que se recrea el contenedor; si quieres uno fijo, ponlo en el `.env`
(`DASHBOARD_PORT=8020`, `POSTGRES_PORT=5432`, `REDIS_PORT=6379`, `QDRANT_PORT`,
`QDRANT_GRPC_PORT`, `WEBHOOK_PORT`). Dentro de la red de Docker los servicios se
siguen hablando por el puerto de siempre (`postgres:5432`), así que esto no toca
ninguna configuración de la aplicación.

La primera vez, el dashboard crea el usuario administrador y **imprime en los logs
una contraseña temporal** (o usa `ADMIN_BOOTSTRAP_PASSWORD`). Se pide cambiarla al
entrar:

```bash
docker compose -f docker-compose.local.yml logs dashboard | grep -A2 "administrador"
```

### Nube (EasyPanel)

```bash
docker compose up --build -d
```

Usa volúmenes con nombre, la red externa `easypanel` y no publica puertos directamente
(el reverse proxy de EasyPanel los enruta). El `dashboard` expone el 8020 y está en la
red `easypanel`, listo para que le asignes su propio dominio.
Ver `docs/despliegue_docker_easypanel.md`.

## Dashboard

Panel propio en `services/dashboard/`: FastAPI + Jinja renderizado en el servidor, sin
Node, sin build y sin CDNs. Todo el JavaScript propio cabe en un archivo, y sirve para
mantener el panel al día, guardar celdas al editarlas y abrir los menús; el resto son
formularios HTML normales, que siguen funcionando aunque el script no cargue.

**El panel se actualiza solo.** Un reporte que entra, una pregunta que el agente no supo
responder, un bloqueo que se pone, un envío que avanza o un mensaje que llega al chat
aparecen en pantalla en un par de segundos, sin recargar. Por debajo no hay polling desde
el navegador: el servidor mantiene UNA tarea que consulta unos contadores baratos cada
dos segundos y avisa por SSE (`GET /eventos`) de qué cambió; cada pestaña pide entonces
solo el trozo de página afectado. Dos consecuencias que importan: el coste en base de
datos es el mismo con una pestaña abierta que con veinte, y con el panel cerrado no se
consulta nada. El chat es el único que no se repinta — se le añaden los mensajes nuevos
al final, así que no se pierde el sitio en el que ibas leyendo.

La navegación está organizada por rol, con una barra superior que indica dónde estás y
el menú de la cuenta. Las secciones y sus páginas se declaran en un solo sitio,
`src/core/navegacion.py`: añadir una página al panel es añadir una línea ahí (y su ruta,
que es quien decide de verdad el acceso). `/admin/configuracion` reúne en una pantalla
todo lo que hay configurado —tarifa vigente, periodo abierto, cuentas, sesiones,
recuperación por Telegram y envíos— sin mostrar nunca el valor de un secreto.

### Roles

| | Dueño (`cliente`) | Administrador |
| --- | --- | --- |
| Conversaciones y bloqueos permanentes de su proyecto | ✅ | Solo mediante suplantación auditada |
| Conocimiento, preguntas y prompts comerciales versionados | ✅ | Solo mediante suplantación auditada |
| Mensajes, palabras clave, envíos e historial propios | ✅ | Solo mediante suplantación auditada |
| Consumo facturado del proyecto | ✅ | Agregado global y desglose por proyecto |
| **Costo real del proveedor y margen** | ❌ | ✅ |
| Proyectos, incidencias, tarifas, periodos y cuentas | ❌ | ✅ |

Cada entidad operativa pertenece explícitamente a un proyecto. El mismo número puede
escribir a dos proyectos sin compartir historial, memoria Redis, bloqueo, consumo,
contenido ni envíos. Las rutas del dueño resuelven el proyecto desde la sesión; no
aceptan un `proyecto_id` enviado por el navegador y responden 404 ante ids ajenos.

`security.requiere_admin` es la única puerta del rol administrador: un `cliente` recibe
403 aunque escriba la URL a mano, y los tests recorren la lista completa de rutas
`/admin/*` para que no se escape ninguna.

### Cómo se factura

Dos categorías, porque no cuestan lo mismo:

- **`llm`** — el turno pasó por el modelo. Se cobra el costo real de los tokens
  multiplicado por el margen de la tarifa vigente.
- **`codigo`** — mensaje disparado por un algoritmo, sin modelo de por medio: la palabra
  clave `tareas`/`transporte`, la bienvenida al grupo, las secuencias programadas y los
  envíos manuales. No tiene costo de proveedor y se cobra una tarifa fija por mensaje.

Cada hecho facturable es una fila de `uso_eventos` con **el costo ya congelado**. Cambiar
una tarifa mañana no reescribe lo de hoy: el histórico es auditable. Todo el dinero se
guarda como entero en micro-USD, así que las sumas no acumulan error de punto flotante.

**El "reset" no borra nada.** Cerrar un periodo congela sus totales y abre uno nuevo: el
cliente ve su contador en cero, el administrador conserva el historial completo con quién
lo cerró y cuándo, y puede volver a sumar un periodo cerrado al actual si el clic fue un
error.

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
| **Postgres** — log durable (`conversation_messages` y `conversation_shots`) | Purga programada: una tarea de Celery (`purge_expired_conversations`) borra a diario las conversaciones cuya **última** actividad supere los 20 días. El corte es por conversación, no por mensaje: a un cliente activo no se le borra el arranque de su chat. |

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
| **Registro durable de conversaciones** (el log que se ve en el dashboard) | **Postgres** (`conversation_messages`) | Persistente en disco. |
| **Facturación** (`uso_eventos`, `tarifas`, `periodos_facturacion`) | **Postgres** | Persistente en disco. |
| **Catálogos y envíos** (mensajes, palabras clave, envíos, incidencias) | **Postgres** | Persistente en disco. |
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
durable está en **Postgres**, no en Redis.

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

- Dependencias de producción: `services/<servicio>/requirements.txt`
- Dependencias de desarrollo/test: `services/<servicio>/requirements-dev.txt` (incluye `pytest`)

Ambos servicios (`bot_agent` y `dashboard`) siguen la misma estructura.

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

# Suite del dashboard:
docker compose -f docker-compose.local.yml run --rm dashboard pytest
```

Los tests del **dashboard** corren contra Postgres de verdad —lo que prueban es SQL
(índices únicos parciales, `ON CONFLICT`, `FOR UPDATE SKIP LOCKED`)— pero sobre una
base **aparte** (`<base>_test`, creada automáticamente), para no tocar tus datos de
desarrollo. Cubren el aislamiento por rol, la máquina de estados de los envíos y el
ciclo de los periodos de facturación.

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
