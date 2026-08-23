# Operación a escala: concurrencia, integridad de conversaciones y trazabilidad

Este documento responde tres preguntas operativas con base en el código actual:

1. ¿Puede el sistema recibir muchas conversaciones a la vez sin degradarse?
2. ¿Qué garantiza que no haya **mensajes duplicados** ni **conversaciones cruzadas**?
3. ¿Cómo se rastrea (trazabilidad) todo lo que pasó en una conversación?

Para el panorama general ver `README.md`; para seguridad ver `docs/seguridad.md`.

---

## 1. Camino de un mensaje (con garantías en cada paso)

```
Cliente → canal (Telegram long-polling / webhook)
       → ConversationOrchestrator (filtros: comandos, keywords, bloqueos)
       → BufferService (Redis, debounce 15s)
       → Celery (cola en Redis) → process_buffered_messages (worker, 20 hilos)
       → candado por conversación → drenado atómico → FlowGraph (LangGraph)
       → LLM (recepción / clasificador / RAG) → respuestas → canal
       → Postgres (log durable) + shots de trazado
```

### Garantías por capa

| Riesgo | Mecanismo que lo previene | Dónde |
| ------ | ------------------------- | ----- |
| **Webhook repetido** (WasenderAPI entrega dos veces el mismo mensaje o reintenta aunque ya se procesó) | Reclamación atómica por `message_id` con `SET NX` y vencimiento de 7 días. Solo la primera petición ejecuta el comando o mete el texto al buffer. | `inbound_registry.py`, antes de cualquier efecto en `webhooks/app.py` |
| **Conversaciones cruzadas** (estado de un usuario mezclado con otro negocio) | TODAS las claves Redis llevan `proyecto_id + canal + user_id` mediante `scoped_key`; Postgres exige `proyecto_id` y Qdrant filtra su payload por proyecto. | `project_context.py`, `buffer_service.py`, repositorios y migraciones 020–022 |
| **Procesar la misma ráfaga dos veces** | Drenado del buffer en **un solo script Lua atómico** (`lrange`+`del`): si dos tareas compiten, solo una se lleva los mensajes; la otra recibe lista vacía. | `_DRAIN_BUFFER_LUA` / `_DRAIN_IF_CURRENT_LUA` en `buffer_service.py` |
| **Responder varias veces a una ráfaga** ("hola" / "quiero info" / "de moto" en 3 mensajes) | Debounce por **número de secuencia**: cada mensaje incrementa `buffer_seq` y agenda una tarea con su número; solo procesa la tarea cuyo número sigue vigente (la del último mensaje). Las demás se descartan. | `BufferService.add_message` / `drain_if_current` |
| **Dos turnos del mismo usuario en paralelo** (usuario escribe mientras el turno anterior aún espera al LLM → el turno lento pisa el estado del nuevo) | **Candado por conversación** (`processing:canal:user_id`, `SET NX EX 120`). Si está tomado, la tarea se reagenda 2s después sin drenar; el TTL evita conversaciones trabadas si el worker muere. | `process_buffered_messages` en `celery_app.py`; tests en `tests/unit/test_processing_lock.py` |
| **Recordatorios duplicados** (tarea con countdown re-entregada por Redis) | `visibility_timeout` configurado a 2× el countdown más largo agendado. Sin esto, Redis re-entrega cada hora las tareas diferidas y el cliente recibe mensajes duplicados. | `broker_transport_options` en `celery_app.py` |
| **Recordatorios cancelados que reviven tras reinicio** | `--statedb` persiste la lista de tareas revocadas del worker. | comando `celery_worker` en ambos compose |
| **Recordatorio que llega justo cuando el usuario respondió** | `send_flow_reminder` verifica `BufferService.has_pending` antes de enviar: si hay mensaje sin procesar, no envía. | `celery_app.py` |
| **Mensajes fuera de orden dentro de una ráfaga** | El buffer es una lista Redis (`rpush`), se drena completa y se concatena en orden de llegada. | `buffer_service.py` |

### Aislamiento multi-canal

`proyecto_id` y `Channel` forman parte de la clave de TODO el estado (buffer, FSM,
publicidad, recordatorios y bloqueos). El mismo número en dos proyectos —o en Telegram
y WhatsApp— son conversaciones independientes por diseño.

---

## 2. Capacidad y escalado

Dimensionado actual (objetivo ~5.000 conversaciones/día, picos de ~200 chats/min):

| Recurso | Configuración | Racional |
| ------- | ------------- | -------- |
| Worker | `--pool=threads --concurrency=20` | Tareas I/O-bound (~95% espera al LLM). 20 hilos ≈ 2× el mínimo por regla de Little para 3,3 msg/s con turnos de ~3s. El GIL no es problema: los hilos sueltan el GIL en espera de red. |
| LLM | `OPENAI_TIMEOUT_SECONDS=30`, `OPENAI_MAX_RETRIES=1` | Sin esto, el default del SDK (600s, 2 reintentos) permite que una llamada colgada retenga un hilo ~30 min y atasque la cola. |
| Redis | AOF + `maxmemory` (default 512mb) + `volatile-lru` | Sin `maxmemory` un pico de tráfico termina en OOM-kill del proceso. `volatile-lru` desaloja solo claves con TTL (estados inactivos) y nunca las colas de Celery. |
| Postgres | Conexión compartida por proceso (`_SharedConnection`) con autocommit y reintento ante desconexión | Antes se abría una conexión por mensaje sin cerrarla: bajo carga se agotaban las conexiones del servidor. |
| Telegram | `concurrent_updates(True)` + orquestador en `asyncio.to_thread` | Un cliente lento no congela el event loop del bot para el resto. |
| Log de conversaciones | Una fila por mensaje en `conversation_messages` | Antes era un JSON por conversación que se reescribía entero en cada mensaje (crecimiento O(n²) en tráfico), y por eso había un tope artificial de 400 mensajes. Ahora guardar es un `INSERT` de costo constante y el tope desapareció. |
| Celery | `task_ignore_result=True` | Nadie consulta resultados; evita llenar Redis con `celery-task-meta-*`. |

**El techo real es el rate limit de OpenAI**, no CPU/RAM/Redis. Señales de saturación
y qué tocar:

- Cola de Celery crece (lag entre mensaje y respuesta > 30-40s sostenido) → subir
  `--concurrency` (hilos son baratos) o revisar latencia del LLM.
- Errores 429 de OpenAI → subir tier de la cuenta o reducir llamadas por turno.
- Redis cerca de `maxmemory` → subir `REDIS_MAXMEMORY` en `.env` (los desalojos
  `volatile-lru` son degradación aceptable: expiran estados inactivos).

**Escalado horizontal:** se pueden añadir réplicas del `celery_worker`, pero el beat
(`-B`) debe quedar en **una sola** réplica (si no, la purga diaria corre duplicada).
Para replicar: separar `celery beat` en su propio servicio.

---

## 3. Trazabilidad

Cada conversación es reconstruible desde `/conversaciones` dentro de la cuenta del
proyecto. Soporte debe usar la suplantación auditada; no existe un visor global admin.

| Qué se registra | Dónde | Detalle |
| --------------- | ----- | ------- |
| Mensajes del cliente (inbound) | `conversation_messages` | Autor, tipo, texto, timestamp. Una fila por mensaje. |
| Respuestas del bot (outbound) | Ídem | Se registran en el punto de envío (`ChannelSenderRegistry.send`, canal Telegram). |
| **Cada llamada a herramienta/LLM** | Ídem, como evento `tool_call` | `reception.decide`, `classifier.classify_reply`, `rag.search` (con chunks, scores y fuentes), `rag.generate_answer`, `publicidad.*`, `unanswered_question.create`. Incluye input, output, duración en ms y errores. |
| *Shots* de conversación (turno completo: estado antes/después + eventos) | `conversation_shots` (JSONB) | Los recolecta `ShotTraceCollector` con `contextvars` (aislado por hilo). Sirven como dataset de regresión/evaluación. |
| Preguntas que el RAG no pudo responder | Tabla de preguntas sin respuesta | Alimenta la mejora de la base de conocimiento. |
| Derivaciones a humano | Tabla de reportes | Con resumen generado por el LLM (contexto del historial incluido). |

Reglas del trazado (`ToolCallLogger`):

- **Redacción de secretos**: claves `token`, `api_key`, `password`, `authorization`,
  `headers`, `xc-token` se guardan como `[redacted]`.
- **Truncado**: textos a 1000 chars, listas a 10 ítems, dicts a 20 claves — el log no
  crece sin control.
- **El trazado nunca rompe el flujo**: los errores de registro se tragan (best effort);
  la conversación siempre tiene prioridad sobre el log.

### Límite resuelto al migrar a Postgres

Cuando el log vivía en NocoDB se actualizaba con **leer-modificar-escribir** sin
candado: si el proceso del bot (inbound) y el worker (outbound/tools) escribían el
mismo registro a la vez, se perdía una línea del log. Con una fila por mensaje ese
problema desapareció: dos `INSERT` concurrentes no compiten por nada.

---

## 4. Retención y durabilidad

- **Redis**: estado activo, TTL deslizante de `CONVERSATION_RETENTION_DAYS` (20 días);
  AOF acota la pérdida ante caída dura a ~1s. Perder Redis = los usuarios activos
  vuelven al intake; el historial durable no se toca.
- **Postgres**: fuente de verdad — log durable, seguimiento, facturación, catálogos,
  envíos, bloqueos y registro de dictamen. La purga diaria por Celery beat
  (`purge_expired_conversations`) borra el historial vencido; el corte se calcula con
  `MAX(created_at)` **por conversación**, no mensaje a mensaje, para no borrarle el
  arranque del chat a un cliente activo.
- **Qdrant**: base de conocimiento del RAG; cada punto lleva `proyecto_id` y toda
  búsqueda o limpieza aplica ese filtro.

## 5. Seguimiento por cliente y resumen mensual

Tablas en Postgres (`services/dashboard/src/db/migrations/001_esquema_inicial.sql`):

- **`seguimiento_clientes`** (una fila por `proyecto_id` + `client_id` + `canal`): contador de
  conversaciones iniciadas (una "conversación" dura hasta
  `SEGUIMIENTO_VENTANA_CONVERSACION_HORAS` = 24h desde el primer mensaje del
  cliente; pasado el plazo, el siguiente mensaje abre otra), primera/última
  interacción del cliente, derivaciones a asesor, costo acumulado del LLM y un
  historial simplificado (`{hora, autor: cliente|bot|dueño, texto}`, con tope
  `SEGUIMIENTO_HISTORIAL_MAX_MENSAJES`).
- **`resumen_mensual`** (una fila por mes `YYYY-MM`): mensajes del bot,
  mensajes del cliente y costo total del mes.

Diseño (en `application/seguimiento_service.py`):

- **Costo exacto**: cada llamada a `chat.completions` registra su `usage`
  (entrada, entrada cacheada y salida) y se convierte a **micro-USD enteros**
  con los precios `OPENAI_PRICE_*_USD_PER_1M` (gpt-5.4-mini: 0.75 / 0.075 /
  4.50 por millón). Sumar enteros evita el error acumulado de punto flotante;
  el campo decimal legible se deriva del entero al escribir. Los embeddings
  del RAG (text-embedding-3-small) no se contabilizan (costo despreciable).
- **Robustez**: los eventos se acumulan primero en Redis con operaciones
  atómicas (`RPUSH`/`HINCRBY`, claves `seguimiento_*` con `scoped_key` y
  `resumen_mensual_deltas:<mes>`); el volcado a Postgres toma un candado no
  bloqueante por fila y solo descuenta del buffer lo efectivamente escrito.
  Si la base está caída, los deltas quedan en Redis y los re-intenta la tarea
  de Celery beat `flush_seguimiento_pendiente` (cada 5 minutos).
- Estas tablas NO entran en la purga de retención: son la vista de largo
  plazo para dar seguimiento a cada cliente.

## 6. Facturación y cola de envíos manuales

**Libro mayor (`uso_eventos`).** Una fila por hecho facturable, con el costo REAL y el
de venta **congelados en la fila**. Consecuencia deliberada: cambiar una tarifa nunca
reescribe el pasado, y el histórico es auditable.

- El periodo abierto y la tarifa vigente se resuelven **dentro del propio INSERT**
  (`billing_repository`), no con una caché en el proceso. Con caché, los eventos
  posteriores a un cierre de periodo o a un cambio de precios caerían en el sitio
  equivocado durante toda la ventana de caché.
- Un índice único parcial garantiza que **solo exista un periodo abierto** a la vez:
  dos cierres simultáneos no pueden partir la factura en dos.
- La fórmula del costo real vive en un único lugar (`seguimiento_service.costo_microusd`);
  el SQL solo aplica el margen de venta. No está duplicada.
- Un fallo al anotar el consumo **no interrumpe la atención al cliente**: se registra el
  error y el turno sigue.

**Cola de envíos (`envios`).** El dashboard solo inserta filas en estado `pendiente`;
enviarlas es cosa del bot, que es el proceso con los canales configurados. Los dos
servicios se comunican **por tabla, no por HTTP**: un reinicio de cualquiera de los dos
no pierde ni duplica envíos.

- `tomar_pendientes` usa `FOR UPDATE SKIP LOCKED`: si algún día corren dos workers, cada
  uno se lleva filas distintas y un mismo envío nunca sale dos veces.
- `rescatar_atascados` devuelve a la cola lo que lleva demasiado en `enviando` (solo
  puede pasar si el worker murió justo después de tomarlo); sin esto quedaría trabado
  para siempre en un estado que la interfaz no deja tocar.
- El error se guarda **separado**: `error_cliente` es la parte accionable que ve quien
  hizo el envío ("no se pudo abrir la imagen, revisa que el enlace sea público");
  `error_tecnico` es la traza completa y solo la ve el administrador. La causa real de
  un 4xx se lee del **cuerpo** de la respuesta, no del mensaje de la excepción.
- Los envíos manuales se facturan como categoría `codigo`: no pasan por el modelo.
