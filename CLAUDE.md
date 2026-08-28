# CLAUDE.md — Guía para agentes en este proyecto

Reglas operativas para cualquier agente (Claude u otro) que trabaje en este repo.
Léelas antes de tocar código. Para el panorama general del producto, ver `README.md`.

El proyecto tiene dos servicios propios, orquestados con **Docker Compose**:

- `services/bot_agent/` — bot de recepción (Telegram/WhatsApp) hecho con **LangGraph**.
- `services/dashboard/` — panel de operación (FastAPI + Jinja, sin Node): facturación
  en vivo, catálogos editables, envíos manuales y toda la trazabilidad.

**La fuente de verdad es Postgres.** NocoDB fue retirado del sistema: sus datos ya
están migrados y no queda ni el servicio ni las variables. No agregues dependencias
nuevas hacia él.

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
docker compose -f docker-compose.local.yml run --rm dashboard pytest              # dashboard
```

Los tests del **dashboard** corren contra Postgres de verdad, a propósito: lo que
prueban es SQL (índices únicos parciales, `ON CONFLICT`, `FOR UPDATE SKIP LOCKED`) y
con la base simulada no se probaría nada de eso. Pero usan una base **aparte**
(`<base>_test`, creada sola la primera vez): vacían tablas antes de cada caso, y
hacerlo sobre la base de desarrollo borraría los usuarios del panel y tus datos.
No cambies eso en `tests/conftest.py`.

Los tests del **bot**, en cambio, tienen el acceso a Postgres neutralizado por
`tests/conftest.py`: cada módulo importa `ejecutar`/`consultar` por nombre, así
que el fixture los sustituye módulo por módulo. **Si agregas un repositorio
nuevo, añádelo a `_MODULOS_CON_POSTGRES`** o sus tests escribirán en la base real.

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
  El servicio `dashboard` expone el 8020 y va en la red `easypanel` (tiene su propio dominio).
- `docker-compose.local.yml` → **LOCAL**. Publica puertos, bind-mount del código y build
  `target: dev`. El dashboard queda en http://localhost:8020.
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
3. `application/fragment_catalog.py` — fragmentos literales por proyecto leídos de Postgres,
   con permisos por agente y variantes; `mensajes.json` es el respaldo si la base falla.

Reglas para cambios:
- Para cambiar **el comportamiento del agente** (qué intención hace qué, playbooks), edita el
  **prompt** (`core/prompts.py`, siguiendo §6). Los efectos duros (bloqueos, reportes,
  anti-bucle, expansión) viven en `agent_pipeline.py`; no los muevas al prompt.
- Para cambiar **los fragmentos del agente**, usa «Agente IA → Fragmentos» en el panel. No metas
  texto de negocio en el código ni en los prompts; `mensajes.json` es solo semilla y respaldo.

El **dashboard** (`services/dashboard/src/`) sigue la misma separación:
- `db/migrations/*.sql` — el esquema COMPLETO de Postgres, incluidas las tablas que
  usa el bot. El bot nunca hace DDL (solo `users_blocked` y `dictamen_registered_users`,
  que son suyas desde antes). Las migraciones se aplican al arrancar el dashboard.
- `services/` — la lógica (facturación, mensajería, palabras clave, usuarios, trazabilidad).
- `routes/` — solo HTTP: validar, delegar y redirigir. Toda ruta de `/admin/*` depende
  de `security.requiere_admin`, que es la ÚNICA puerta del rol administrador.
- Sin Node ni build de frontend: Jinja del lado del servidor y un solo archivo de JS
  propio (`static/app.js`) para los fragmentos que se actualizan solos, la edición de
  celdas y los dos menús (lateral y cuenta). No metas un framework de frontend ni CDNs:
  el panel manda HTML ya pintado y el navegador no ejecuta ningún runtime; meter React
  o htmx lo haría más pesado, no más rápido.
- **El panel se actualiza solo, y el coste no crece con las pestañas abiertas.**
  `core/eventos.py` es UNA tarea de fondo que cada `INTERVALO` (2 s) hace UNA consulta
  de contadores (`MAX(id)` por clave primaria + `COUNT` sobre tablas que se purgan
  solas), compara con el tick anterior y publica por SSE (`GET /eventos`) los NOMBRES
  de los temas que cambiaron. El navegador pide entonces el fragmento afectado.
  Cuatro cosas que no se pueden romper:
  - **Sin suscriptores no se consulta.** El bucle comprueba `_suscriptores` antes de
    ir a la base: el panel cerrado no le cuesta nada a Postgres.
  - **La espera es un `sleep`, no un `asyncio.Event` de módulo.** Un Event guarda
    futuros del bucle en el que se esperó; este módulo se importa una vez pero se usa
    desde varios (cada `TestClient` monta el suyo), y despertarlo desde otro bucle
    pierde el aviso en silencio. Lo que había que evitar era la CONSULTA, no el
    temporizador.
  - **Por el flujo no viajan datos, solo nombres de tema**, y `topics_para()` los
    reparte por rol. El permiso lo sigue decidiendo la ruta de cada fragmento.
  - **El hub vive en el proceso**: con varios workers de uvicorn cada uno haría su
    propio tick. Por eso el Dockerfile arranca con uno.
  Una página «viva» se parte en dos —parcial + `{% include %}`— y su ruta hermana
  devuelve ese mismo parcial (patrón de `panel.py:factura_totales`). El marcado se
  escribe UNA vez: si se duplicara, un día la tabla que llega refrescando dejaría de
  parecerse a la que estaba. En la plantilla:
  `<div data-vivo="reportes" data-refrescar="/reportes/lista">`, con `data-cada` como
  respaldo por si el flujo no se pudiera establecer.
- **Al repintar un fragmento no se pisa lo que estabas haciendo.** `pintar()` en
  `app.js` no toca nada si el HTML es idéntico, aparca el repintado si el foco está
  dentro o hay un `<dialog open>` dentro, y conserva el scroll. Antes un refresco te
  borraba lo que estuvieras escribiendo.
- **El chat NO se repinta: se le añade lo nuevo.** `mensajes_de(desde_id=...)` y
  `?desde=<id>` devuelven solo las burbujas posteriores, y el navegador las pega al
  final; el scroll solo baja si ya estabas abajo. Hay que pasarle también el día del
  último mensaje ya pintado (`?dia=`) o cada tanda repetiría el separador de fecha.
  Con `?antes=` (leyendo hacia atrás) la cola se APAGA: «lo posterior a lo que veo»
  sería media conversación de golpe.
- El visor del dueño (`/conversaciones/{canal}/{client_id}`) **pagina por cursor**
  (`?antes=<id>`), no por OFFSET: el chat se lee desde el final y con OFFSET habría que
  descartar todas las filas nuevas en cada tanda, además de descolocarse si llega un
  mensaje mientras se lee. La búsqueda del listado es **solo por número** a propósito
  (buscar texto obliga a recorrer todo el historial). Las horas se muestran en
  `settings.ZONA_HORARIA` (Costa Rica), no en la del servidor.
- Las conversaciones se ven únicamente dentro de la cuenta del proyecto. La ruta
  obtiene `proyecto_id` de la sesión y cualquier id ajeno responde 404. El administrador
  entra mediante suplantación auditada; no hay listado ni visor global.
- `core/navegacion.py` — el menú lateral por secciones y la miga de pan salen de ahí,
  no de la plantilla. Una página nueva del panel se declara en esa lista (con su
  icono, de `templates/_iconos.html`); ocultar un enlace NO restringe nada, el acceso
  lo sigue decidiendo `requiere_admin` en la ruta. Tres cosas que conviene saber
  antes de tocarla:
  - **Lo del sistema y la cuenta NO va en el lateral**: cuentas de acceso, ajustes
    y «Mi cuenta» viven en el menú de la cuenta (al pie del lateral) y se declaran
    en `SECCIONES_DE_CUENTA`. Estaban en los dos sitios: la misma lista dos veces
    en la misma pantalla.
  - `migas()` devuelve tramos CON enlace, y una página de detalle añade el suyo
    pasando `miga_final` al render (el perfil del cliente manda su nombre). La
    miga era texto muerto: decía «Operación › Clientes» y no llevaba a ninguna parte.
- El perfil del proyecto (`/admin/negocios/{id}`) contiene cifras agregadas,
  configuración técnica, facturación, usuario y la entrada auditada a su cuenta.
  Toda la configuración es UNA ventana con categorías
  (`.dialogo-secciones`), no cuatro botones sueltos; el webhook y las credenciales
  de WasenderAPI van en la MISMA pestaña porque son un solo servicio, y la cuenta
  de acceso del cliente **se crea desde ahí** (`/cuenta/crear`), no dando la vuelta
  por «Cuentas de acceso» para volver a vincularla a mano.
- `templates/base.html` define el armazón (lateral + barra superior + contenido). Las
  clases que usan las páginas (`panel`, `cifra`, `pastilla`, `tabla-scroll`,
  `titulo-con-acciones`, `tarjeta`, `dialogo-secciones`…) son un vocabulario cerrado:
  reutilízalas en vez de inventar estilos por página. Si cambias
  `static/app.css` o `app.js`, sube el `?v=` de ambos enlaces en `base.html` o los
  navegadores servirán la versión vieja en caché.
- **Un envío masivo es una SESIÓN (`envios_lote`), no cien filas sueltas.** El
  ritmo vive en el lote, no en cada envío: `envios_repository.tomar_pendientes`
  toma **una sola por sesión y por pasada** y adelanta `proximo_en` con una espera
  aleatoria de 15 s ±60 %. Sin eso salían veinte de golpe por pasada del worker,
  que es la firma más obvia de un bot. `DISTINCT ON` y `FOR UPDATE` no se combinan
  en Postgres: primero se elige y luego se bloquea por id, volviendo a comprobar el
  estado bajo el candado. Las sesiones caducan a los 12 días
  (`envios.RETENCION_DIAS`, purgadas por el bot); `uso_eventos` no se toca.
- **En «Enviar», los ids de las dos categorías COLISIONAN.** Mensajes y palabras
  clave son tablas distintas: el id 1 existe en las dos. El desplegable
  se mueve por `selectedIndex`, **nunca** por `value` — asignando por valor se
  posaba en la opción de otra categoría y la pantalla enseñaba un nombre mientras se
  enviaba otro. Y el servidor resuelve siempre con `categoria` + `referencia_id`,
  jamás con el id solo.
- **Una palabra clave NO es un mensaje: es una fila de `palabras_clave`.** Se
  parecen (los dos son una cadena de textos con adjunto), pero un mensaje lo mandas
  TÚ a quien elijas y una palabra clave la dispara el CLIENTE escribiéndola, y
  arrastra el bloqueo de la conversación y unos recordatorios. Ya no están escritas
  en el bot: `palabras_clave_repository.buscar` las lee de la base (con caché de 30 s
  porque corre con cada mensaje entrante). El match sigue siendo EXACTO y sobre el
  mensaje entero — eso es reconocer un disparador anunciado, no interpretar lenguaje
  natural (§5).
- **La publicidad por ciudad sale de «Mensajes»: la CLAVE es el nombre de la
  ciudad.** Hubo una tabla aparte (`invitaciones_ciudades`, cinco columnas de
  texto por ciudad) con los mismos textos y las mismas claves; la migración 018
  la eliminó. Cuando alguien llega por un anuncio,
  `publicidad_service._buscar_clave` reconoce de qué mensaje habla —subcadena sin
  tildes y, si no, un parecido del 0,7 para los errores de tipeo— y la cadena se
  lee con `plantillas_repository.textos_de`, con adjuntos y todo. Dos cosas que
  no se pueden romper: las claves se prueban **de la más larga a la más corta**
  (si no, «LIBERIA» le gana a «LICENCIA EN LIBERIA») y las de menos de cuatro
  letras no se buscan por subcadena (aparecerían dentro de cualquier texto). Que
  el enlace del grupo se busque en **toda** la cadena y no en un mensaje
  concreto es lo que permite que cada negocio la haga tan larga como quiera.
- **Los minutos de un recordatorio se cuentan desde que se disparó la palabra**, no
  en cascada desde el anterior, y cada uno debe ser mayor que el previo
  (`palabras_clave._minutos_validos`). El texto se **relee al enviarse**, no viaja
  dentro de la tarea: entre agendar y salir pueden pasar días, y apagar o borrar un
  recordatorio en el panel tiene que servir también para los ya agendados.
- **La apertura de las 07:00 no libera una ráfaga.** Los recordatorios que se
  aplazaron por la franja 23:00–07:00 comparten un reloj Redis por proyecto y
  canal: el primero puede salir al abrir y cada envío confirmado abre el
  siguiente turno con 5–10 minutos aleatorios. Las tareas antiguas sin la marca
  de procedencia se tratan como acumuladas. No metas aquí un `sleep`: la tarea
  se reagenda para no ocupar uno de los 20 hilos del worker.
- **Toda salida pasa por `ChannelSenderRegistry`.** Ahí vive el candado Redis
  renovable por proyecto/canal que impide dos llamadas físicas simultáneas,
  incluida media, respuestas del panel y Telegram. Las respuestas interactivas
  anuncian que esperan; un recordatorio debe ceder, liberar su reserva de
  drenaje y reagendarse, nunca enviar por fuera del candado.
- **El tope de minutos existe por Celery, no por gusto.** `palabras_clave.MAX_MINUTOS`
  (14 días) y `celery_app.MAX_RECORDATORIO_MINUTOS` son el MISMO número: el
  `visibility_timeout` se calcula como el doble del countdown más largo, y una tarea
  que lo supere la re-entrega Redis — el cliente recibiría el mismo recordatorio una y
  otra vez. Si sube en un sitio, sube en el otro.
- **Un mensaje se identifica por su CLAVE, y la unicidad se comprueba en el
  servicio.** `mensajeria._clave_valida` la normaliza a mayúsculas y rechaza las
  repetidas al crear Y al renombrar — solo estaba al crear, y renombrar llegaba al
  índice único con un 500 sin explicación. El campo «nombre» que la acompañaba no
  lo usa nadie (ni el bot, ni la búsqueda, ni el envío).
- **El verde de un mensaje significa «se puede enviar tal cual», no «alguien lo
  escribió».** Lo decide `mensajeria.problema_de_parte`: texto vacío, adjunto que no
  se pudo abrir o adjunto sin comprobar lo dejan en rojo. El ID de Drive se comprueba
  **en cada guardado** (`media.revisar_parte`), no al enviar: un enlace roto tiene que
  descubrirse mientras se escribe, no cuando el cliente ya recibió media cadena. La
  descarga es solo para comprobar; no se guarda ninguna copia.
- **Lo que se edita se edita en una ventana; la página se lee.** Vale para el
  conocimiento (tarjetas con visto y papelera, `Editar` abre la ventana) y para los
  mensajes de una cadena (que se llaman «Mensaje N», no «parte»: al enviarse, cada
  uno sale como un mensaje suelto). Antes cada elemento era un formulario abierto
  con su «Guardar» aunque no hubieras tocado nada, y la página era un muro de campos.
- Las confirmaciones son `<dialog>`, no `confirm()` del navegador: entrar a la cuenta
  de un cliente, cerrarle las sesiones o borrar algo son cosas que conviene leer.

Facturación (lo más sensible del dashboard):
- `uso_eventos` es el libro mayor: una fila por hecho facturable, con el costo YA
  congelado. **Nunca se recalcula el pasado**; cambiar una tarifa solo afecta a lo
  que venga después.
- El costo REAL se calcula en un solo lugar: `seguimiento_service.costo_microusd`.
  SQL solo aplica el margen de venta. No dupliques esa fórmula.
- El periodo abierto y la tarifa vigente se resuelven DENTRO del INSERT
  (`billing_repository`), sin caché: así un cierre de periodo o un cambio de precios
  no deja eventos mal imputados.
- Todo el dinero es un ENTERO en micro-USD. Nada de flotantes acumulados.

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
- **Postgres** (log durable y *shots*) se purga con la tarea Celery `purge_expired_conversations`,
  agendada por **Celery beat**. Por eso el `celery_worker` corre con `-B` en ambos compose: si
  tocas ese comando, se pierden la purga Y el drenaje de la cola de envíos manuales.
- La retención es **por conversación, no por mensaje**: el corte se compara con
  `MAX(created_at)` de cada `(client_id, canal)`. Si se comparara mensaje a mensaje, a un
  cliente activo se le borraría el arranque de su conversación. Helpers de fechas:
  `infrastructure/repositories/fechas.py`. Tests: `tests/unit/test_conversation_retention.py`.

## 5. Diseño conversacional (playbooks + mensajes curados)

El reto del sistema es mezclar **mensajes curados del negocio** (plantillas literales) con un
**flujo conversacional** razonado. Reglas para que sea natural y no robótico:

- **Separación de capas:**
  - *Textos curados (deterministas):* viven en el catálogo de fragmentos de Postgres y se envían LITERALES vía
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
   catálogo de fragmentos o de la normalización de salida (`_validated_decision`).
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
   eso vive en el RAG o en los fragmentos. Y `temperature=0` en toda decisión.

## 7. Restricciones duras (rompen si las ignoras)

- **`mensajes.json` vive en la RAÍZ del repo.** Ambos compose lo montan en el dashboard y
  los 3 servicios del bot. Es la semilla y el respaldo de emergencia; la fuente vigente de
  los fragmentos es Postgres. No lo muevas ni lo dupliques.
- **WhatsApp (WasenderAPI) manda la media por URL, no como binario.** Telegram sube el
  archivo; Wasender recibe un enlace. Por eso `ChannelSenderRegistry` pregunta si el sender
  tiene `send_image_url_sync` antes de descargar nada. Los marcadores `Imagen=` y `Video=`
  aceptan tanto un ID de Drive como una URL completa.
- **Los prompts deben ser genéricos.** El test `tests/unit/test_prompt_contracts.py` prohíbe
  términos específicos del catálogo en `UNIFIED_AGENT_PROMPT` y `FOLLOWUP_AGENT_PROMPT`
  (p. ej. `casco`, `programar cita`, `qué pasa si pierde`). Usa ejemplos genéricos.
- **El dueño edita capas comerciales, no estos contratos.** La pantalla «Prompts» guarda
  `principal` y `recordatorio` en `proyecto_instrucciones`; se anexan al prompt fijo y su
  rollback crea una versión nueva. El switch y el intervalo viven aparte en
  `proyecto_recordatorios`.
- Ese mismo test exige que ciertas frases clave **existan** en los prompts. Si reescribes un
  prompt, conserva esas frases o actualiza el test de forma deliberada.
- **Invariantes de concurrencia** (garantizan cero duplicados/cruces; detalle en
  `docs/operacion_escala_y_trazabilidad.md`). No los debilites al tocar el pipeline:
  - Todo estado comercial en Redis usa `scoped_key(prefijo, canal, user_id)` y la
    función incorpora obligatoriamente el proyecto activo.
  - El buffer se drena SOLO con los scripts Lua atómicos de `buffer_service.py` y el
    debounce por `seq`.
  - `process_buffered_messages` corre bajo el **candado por conversación**
    (`processing:*`, tests en `tests/unit/test_processing_lock.py`): un solo turno en
    proceso por usuario.
  - El `visibility_timeout` de Celery debe superar el countdown más largo agendado
    (`celery_app.py`); si agregas un delay mayor, inclúyelo en `_max_countdown_seconds`.
- **Los endpoints con efectos se apagan si falta su secreto, nunca se abren.**
  `POST /webhooks/wasender/{token}` resuelve el proyecto; los endpoints internos
  `/internal/proyectos/{proyecto_id}/rag/sync/{id}` y
  `/internal/proyectos/{proyecto_id}/conversaciones/{canal}/{id}/olvidar` exigen `INTERNAL_API_TOKEN`
  (borra estado). Sin el secreto responden **503**. No los "arregles" quitando el
  guardarraíl (ver `docs/seguridad.md`).
- **Borrar una conversación son dos mitades, y una no es del panel.** El dashboard
  borra en Postgres lo que ES la conversación (`conversation_messages`,
  `conversation_shots`, `conversacion_negocio`) y le pide al bot que suelte la otra
  mitad —el hilo en Redis y los recordatorios agendados— por el endpoint interno de
  arriba, porque el esquema de claves y los ids de tarea son suyos
  (`application/conversation_reset.py`). Si esa llamada falla **se dice en pantalla**:
  callarlo haría creer que se borró algo que el bot sigue recordando. Lo que NUNCA se
  borra al eliminar un chat es `uso_eventos` (el libro mayor: el pasado no se recalcula)
  ni `seguimiento_clientes` (se borra el chat, no el cliente).
- **Hay dos bloqueos distintos.** `users_blocked` contiene pausas automáticas
  temporales y no tiene pantalla pública. `bloqueos_permanentes` pertenece al proyecto:
  el dueño bloquea desde el hilo y administra la lista diferida en Configuración del
  proyecto. El administrador no dispone de rutas directas de bloqueos.
- **Un enlace de plantilla que apunte a una ruta inexistente rompe en silencio.**
  Pasó de verdad: al mover el conocimiento, los reportes y las preguntas al panel del
  negocio, las plantillas siguieron apuntando a `/admin/*` y tres botones daban 404 sin
  que nada fallara al arrancar. `tests/test_enlaces.py` recorre las plantillas y
  comprueba cada `action=`/`href=` interno contra el esquema OpenAPI de la app. Si
  mueves una ruta, ese test te dice qué plantilla quedó atrás.
- **Tres palabras, tres cosas. No las mezcles** (el detalle en `core/navegacion.py`):
  - **Base de Control** es la PLATAFORMA. Es la marca del panel; no es ningún cliente.
    En la interfaz nunca aparece el nombre de un proyecto como si fuera el producto.
  - Un **proyecto** es cada cliente nuestro (hoy «Escuela de Manejo»): su número, su
    bot, su conocimiento y su factura. Vive en `clientes_whatsapp`. Tiene **un único
    usuario asignado** (`usuario_id`, con índice único parcial desde la 006): quien lo
    administra es una persona, y esa cuenta es su llave. No es un sistema de permisos.
  - Un **cliente** es la persona que le escribe al bot (`seguimiento_clientes`). Solo
    aparece dentro del panel de un proyecto.

  Las URLs siguen diciendo `/admin/negocios` a propósito: renombrarlas rompería enlaces
  guardados sin ganar nada. Lo que cambió son las etiquetas.
- **Un webhook por proyecto, y el token de la ruta ES la credencial.**
  `POST /webhooks/wasender/{token}` resuelve la fila de `clientes_whatsapp`; un token
  desconocido o inactivo responde **401**. Se administra en el perfil del proyecto
  (`/admin/negocios/{id}`).
- **«Mi cuenta» no es una página, es una ventana.** Se declara en `templates/base.html`
  y se abre desde el menú de la cuenta en cualquier pantalla: ver con qué cuenta estás
  dentro y cambiar la contraseña no son motivo para salir de donde estabas. `/password`
  sigue existiendo, pero SOLO para el cambio obligatorio del primer ingreso, que sí
  necesita pantalla propia. El nombre de usuario se corrige desde el perfil del
  proyecto (pestaña «Usuario»), no desde «Cuentas de acceso».
- **Un modelo por tipo de tarea, y cada uno con SUS parámetros y SU precio.**
  `OPENAI_MODEL_SUPERVISOR` (gpt-5.6-terra), `_ESPECIALISTA` (gpt-5.4-mini),
  `_AUXILIAR` (gpt-5.4-nano, para recordatorio/RAG/publicidad). Los parámetros los
  resuelve `core/modelos.py:kwargs_de_decision`, NO el agente: gpt-5.6 **rechaza
  `temperature`** con un 400 y gpt-4o no conoce `reasoning_effort` — mandar el que
  no toca no degrada la respuesta, tumba TODAS las llamadas y el bot contesta solo
  con el fallback. `temperature=0` se sigue enviando donde el modelo la acepte.
  Los precios viven en `precios_modelo` (editables desde el panel, migración 010),
  y `registrar_uso_llm` exige el `modelo` que atendió: entre niveles hay 10x de
  diferencia y un precio único falsea la factura del cliente en los dos sentidos.
- **Los tests no pueden tocar servicios reales, y el agujero no es Postgres.**
  `tests/conftest.py` corta tres cosas: Postgres, Redis (en los 8 módulos que lo
  importan por nombre) y **`apply_async` de Celery**. Esto último es lo crítico:
  encolar manda trabajo al worker, que lo ejecuta en OTRO proceso donde no hay
  mocks — llamadas pagadas a OpenAI y escrituras en la base del cliente. Ya pasó.
  `tests/unit/test_aislamiento_de_tests.py` lo vigila; si lo tocas, corre ese
  archivo. El Redis de tests es `fakeredis`, no un mock, porque varios flujos usan
  la semántica real (`SET NX`, TTL) como garantía.
- **Lo que el bot no puede leer se acusa con texto fijo; el sticker no se responde.**
  Imagen, documento, video y enlace reciben el acuse de su nodo `AUTOMATICO` en
  `mensajes.json` (uno por tipo: nombran lo que llegó), sin LLM y **sin cobrar**.
  El sticker no se responde pero SÍ se registra, porque cada envío consume la cuota
  de ritmo del plan de WasenderAPI y contestarle dejaría sin respuesta al mensaje
  que importaba. Detectar una URL por estructura no viola la regla de §5 (eso es
  reconocer un formato, no interpretar lenguaje natural), pero los correos quedan
  fuera a propósito.
- **De una nota de voz solo sobrevive la transcripción.** El audio no se guarda:
  ni en disco, ni en la base, ni en el historial. `transcribir_nota_de_voz` corre
  en el WORKER —descifrar la media con WasenderAPI, bajarla y transcribirla tarda
  segundos, y hacerlo en el webhook haría que el proveedor reintente el evento—.
  La etiqueta que ve el panel ("Audio transcrito") viaja en `event_type`, **nunca
  en el texto**: al LLM le llega texto plano, como si el cliente lo hubiera
  escrito. Se factura por SEGUNDO (categoría `audio`), no por minuto entero.
- **Lo que es de un negocio se guarda en la base y se administra desde el panel; el
  `.env` es solo para lo que vale para TODO el despliegue.** La API key de envío de
  WhatsApp vive en `clientes_whatsapp.wasender_api_key`, nunca en el entorno: una
  clave global obligaría a redesplegar por cada alta y dejaría a todos los negocios
  compartiendo credencial. El envío la resuelve por destinatario con
  `clientes_whatsapp_repo.api_key_de_envio`, que se apoya en `conversacion_negocio`
  (el webhook anota ahí la pertenencia, porque es el único punto donde se sabe). Si
  la pertenencia falta y hay más de un negocio, **no se envía**: mandar el mensaje
  desde el número equivocado es peor que no responder.
- **En WhatsApp el bot y el dueño comparten número: los mensajes salientes son
  ambiguos.** Antes de tratar un `fromMe` como intervención humana hay que preguntarle a
  `outbound_registry.es_envio_del_bot`. Si se salta ese paso, el bot lee su propia
  respuesta como una intervención y se bloquea solo 12 días en el primer turno.
- **El dashboard no arranca sin `SESSION_SECRET`.** Es deliberado: con un secreto vacío
  las cookies de sesión serían falsificables y nadie lo notaría.
- **El costo real no se le oculta al proyecto: no se le consulta.** `Costo real` es
  lo que nos cobra el proveedor; junto a `Facturado` deja el margen a la vista de
  quien lo paga. `facturacion.actividad_por_cliente` recibe `incluir_costo_real` y
  sin él la columna sale `NULL` desde SQL. Esconderlo en la plantilla no vale: el
  dato seguiría viajando en el HTML.
- **La hora es la del PROYECTO, no la del despliegue ni la del servidor.** Los
  filtros `fecha`/`hora`/`dia_largo`/`dia_clave` van con `@pass_context` y sacan la
  zona de `proyecto.zona_horaria` (lo pone `render`; `settings.ZONA_HORARIA` es el
  respaldo). Una página de administrador que muestre datos de UN proyecto debe
  pasarle `proyecto=` para que sus horas cuadren con las que ve su dueño.
- **Lo ATENDIDO de las dos bandejas caduca; lo pendiente no, nunca.** Reportes
  revisados a las 24 horas (`revisado_en`, migración 013) y preguntas entendidas a las
  24 horas (`atendida_en`, migración 014). El plazo cuenta desde que se atendió, no
  desde que llegó, y marcar dos veces no lo reinicia (`WHERE NOT revisado`). Quien
  borra es el bot: la tarea `purge_bandejas` corre **cada hora** en Celery beat,
  aparte de la retención diaria de conversaciones — con una pasada al día, «se borra
  en 24 horas» podían ser 48. El dashboard no tiene proceso periódico, y purgar al
  abrir la página dejaría la caducidad a merced de que alguien la mire. Los plazos
  están en el bot y en el dashboard: si cambian, cámbialos en los dos.
- **Un chunk del RAG es UN TROZO DE TEXTO: no tiene tema ni título.** Hubo una
  columna `titulo` que se embebía pegada delante del contenido; no era un índice
  (nadie buscaba por ella) y solo obligaba a inventarle un titular a cada trozo. La
  migración 014 la fundió dentro de `contenido` y la eliminó — no la vuelvas a
  añadir. El tope es `LIMITE_CHUNK` (2000 caracteres) y se aplica **al escribir**: un
  trozo largo mezcla asuntos, su vector queda en el promedio y deja de parecerse a
  ninguna pregunta concreta. Lo que YA estaba por encima se marca en el panel pero
  **no se recorta solo**: cortar el conocimiento de un negocio sin preguntarle puede
  dejar fuera justo el precio o el requisito que importaba.
- **`requiere_admin` es la única puerta del rol administrador.** Toda ruta que muestre
  costo real, logs, tarifas, periodos, incidencias o usuarios debe depender de ella.
  Los tests `tests/test_acceso.py` recorren la lista completa de rutas `/admin/*`:
  si agregas una, agrégala también ahí.
- **Un nombre de columna que llegue de un formulario se valida contra una lista
  blanca antes de interpolarlo en el SQL.** Sin eso es inyección. Hoy no queda
  ninguna pantalla que lo haga (la última era la tabla de ciudades, ver §7), y la
  regla sigue aquí para la próxima que lo necesite.

## 8. Convenciones del repo

- `docs/` está **versionado** (fuentes `.md`/`.mmd`; los PDF/SVG generados se ignoran).
- `_local/` está **ignorado** por git: notas personales, scripts scratch, overrides locales.
- Código y comentarios en **español**, para coincidir con el resto del codebase.
- No hagas commit ni push salvo que el usuario lo pida. Si hay que commitear, **ramifica
  desde `main`** primero.
