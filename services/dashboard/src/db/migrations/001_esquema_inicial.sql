-- Esquema inicial del dashboard y de la trazabilidad que antes vivía en NocoDB.
--
-- Estas tablas son propiedad del dashboard: él las crea y las versiona. El bot
-- solo lee y escribe (nunca hace DDL). Las tablas `users_blocked` y
-- `dictamen_registered_users` siguen siendo del bot y se crean en
-- postgres_user_repo.py; no se tocan aquí.
--
-- Todo el dinero se guarda como ENTERO en micro-USD (1 USD = 1_000_000). Sumar
-- enteros no acumula error de punto flotante; el valor legible se deriva al
-- mostrarlo. Es el mismo criterio que ya usaba seguimiento_service.

-- ===========================================================================
-- Autenticación y roles
-- ===========================================================================

CREATE TABLE IF NOT EXISTS dashboard_usuarios (
    id              SERIAL PRIMARY KEY,
    usuario         VARCHAR(80) NOT NULL UNIQUE,
    password_hash   TEXT        NOT NULL,
    rol             VARCHAR(20) NOT NULL CHECK (rol IN ('admin', 'cliente')),
    activo          BOOLEAN     NOT NULL DEFAULT TRUE,
    -- El usuario administrador inicial se crea con una contraseña de arranque;
    -- hasta cambiarla, toda navegación se redirige al cambio de contraseña.
    debe_cambiar_password BOOLEAN NOT NULL DEFAULT FALSE,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ultimo_acceso   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS dashboard_sesiones (
    token       VARCHAR(64) PRIMARY KEY,
    usuario_id  INTEGER     NOT NULL REFERENCES dashboard_usuarios(id) ON DELETE CASCADE,
    creada_en   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expira_en   TIMESTAMPTZ NOT NULL,
    ip          VARCHAR(64) NOT NULL DEFAULT '',
    user_agent  TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sesiones_usuario ON dashboard_sesiones (usuario_id);
CREATE INDEX IF NOT EXISTS idx_sesiones_expira  ON dashboard_sesiones (expira_en);

-- ===========================================================================
-- Log de conversaciones (antes: NocoDB `json_mensajes`)
-- ===========================================================================
--
-- Antes cada mensaje reescribía el JSON completo de la conversación: el costo
-- de guardar crecía con el largo del chat (O(n²) en tráfico) y por eso había un
-- tope artificial de 400 mensajes. Aquí cada mensaje es una fila: guardar es un
-- INSERT de costo constante y el visor de logs pagina por índice.

CREATE TABLE IF NOT EXISTS conversation_messages (
    id            BIGSERIAL PRIMARY KEY,
    client_id     VARCHAR(80)  NOT NULL,
    canal         VARCHAR(20)  NOT NULL,
    -- 'inbound' (cliente), 'outbound' (bot) o 'internal' (evento de herramienta)
    direction     VARCHAR(20)  NOT NULL,
    author        VARCHAR(20)  NOT NULL,
    sender_id     VARCHAR(120) NOT NULL DEFAULT '',
    sender_name   VARCHAR(200) NOT NULL DEFAULT '',
    message_type  VARCHAR(40)  NOT NULL DEFAULT 'text',
    text          TEXT         NOT NULL DEFAULT '',
    event_type    VARCHAR(40)  NOT NULL DEFAULT 'message',
    tool_name     VARCHAR(120) NOT NULL DEFAULT '',
    status        VARCHAR(40)  NOT NULL DEFAULT '',
    entrada       JSONB,
    salida        JSONB,
    error         TEXT         NOT NULL DEFAULT '',
    duration_ms   INTEGER,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- El visor lee "los últimos N mensajes de este cliente en este canal".
CREATE INDEX IF NOT EXISTS idx_conv_msg_cliente
    ON conversation_messages (client_id, canal, created_at DESC);
-- La purga por retención barre por fecha.
CREATE INDEX IF NOT EXISTS idx_conv_msg_created
    ON conversation_messages (created_at);

-- ===========================================================================
-- Seguimiento por cliente y resumen mensual
-- ===========================================================================

CREATE TABLE IF NOT EXISTS seguimiento_clientes (
    id                          SERIAL PRIMARY KEY,
    client_id                   VARCHAR(80) NOT NULL,
    canal                       VARCHAR(20) NOT NULL,
    nombre                      VARCHAR(200) NOT NULL DEFAULT '',
    conversaciones_iniciadas    INTEGER     NOT NULL DEFAULT 0,
    conversacion_actual_inicio  TIMESTAMPTZ,
    primera_interaccion         TIMESTAMPTZ,
    ultima_interaccion          TIMESTAMPTZ,
    derivaciones_asesor         INTEGER     NOT NULL DEFAULT 0,
    costo_microusd              BIGINT      NOT NULL DEFAULT 0,
    tokens_entrada              BIGINT      NOT NULL DEFAULT 0,
    tokens_salida               BIGINT      NOT NULL DEFAULT 0,
    historial                   JSONB       NOT NULL DEFAULT '{"mensajes": []}'::jsonb,
    UNIQUE (client_id, canal)
);

CREATE INDEX IF NOT EXISTS idx_seguimiento_ultima
    ON seguimiento_clientes (ultima_interaccion DESC);

CREATE TABLE IF NOT EXISTS resumen_mensual (
    mes              VARCHAR(7) PRIMARY KEY,  -- 'YYYY-MM'
    mensajes_bot     BIGINT      NOT NULL DEFAULT 0,
    mensajes_cliente BIGINT      NOT NULL DEFAULT 0,
    costo_microusd   BIGINT      NOT NULL DEFAULT 0,
    tokens_entrada   BIGINT      NOT NULL DEFAULT 0,
    tokens_salida    BIGINT      NOT NULL DEFAULT 0,
    actualizado_en   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ===========================================================================
-- Reportes, registros de keyword y preguntas sin respuesta
-- ===========================================================================

CREATE TABLE IF NOT EXISTS reportes (
    id             BIGSERIAL PRIMARY KEY,
    nombre         VARCHAR(200) NOT NULL DEFAULT '',
    numero         VARCHAR(80)  NOT NULL DEFAULT '',
    problema       TEXT         NOT NULL DEFAULT '',
    link_whatsapp  TEXT         NOT NULL DEFAULT '',
    revisado       BOOLEAN      NOT NULL DEFAULT FALSE,
    creado_en      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reportes_creado ON reportes (creado_en DESC);

CREATE TABLE IF NOT EXISTS keyword_registros (
    id             BIGSERIAL PRIMARY KEY,
    registro       VARCHAR(80)  NOT NULL,
    canal          VARCHAR(20)  NOT NULL,
    nombre         VARCHAR(200) NOT NULL DEFAULT '',
    palabra_clave  VARCHAR(60)  NOT NULL DEFAULT '',
    creado_en      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (registro, canal)
);

CREATE TABLE IF NOT EXISTS preguntas_sin_respuesta (
    id         BIGSERIAL PRIMARY KEY,
    pregunta   TEXT        NOT NULL,
    atendida   BOOLEAN     NOT NULL DEFAULT FALSE,
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_preguntas_creado ON preguntas_sin_respuesta (creado_en DESC);

-- ===========================================================================
-- Base de conocimiento del RAG
-- ===========================================================================
--
-- El texto que se embebe es "titulo\ncontenido". `actualizado_en` permite que
-- el bot sincronice a Qdrant solo lo que cambió.

CREATE TABLE IF NOT EXISTS rag_chunks (
    id              BIGSERIAL PRIMARY KEY,
    titulo          TEXT        NOT NULL DEFAULT '',
    contenido       TEXT        NOT NULL DEFAULT '',
    activo          BOOLEAN     NOT NULL DEFAULT TRUE,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rag_actualizado ON rag_chunks (actualizado_en DESC);

-- ===========================================================================
-- Invitaciones por ciudad (antes: Google Sheet + tabla NocoDB)
-- ===========================================================================
--
-- `ciudad` admite varios alias separados por coma (así funciona hoy el emparejado
-- difuso en publicidad_service). `mensaje_4` debe llevar el enlace del grupo de
-- WhatsApp: sin él, el flujo de publicidad se corta y genera un reporte.

CREATE TABLE IF NOT EXISTS invitaciones_ciudades (
    id                SERIAL PRIMARY KEY,
    ciudad            TEXT        NOT NULL,
    mensaje_1         TEXT        NOT NULL DEFAULT '',
    mensaje_2         TEXT        NOT NULL DEFAULT '',
    mensaje_3         TEXT        NOT NULL DEFAULT '',
    mensaje_4         TEXT        NOT NULL DEFAULT '',
    mensaje_5         TEXT        NOT NULL DEFAULT '',
    ciudad_mayuscula  TEXT        NOT NULL DEFAULT '',
    link_facebook     TEXT        NOT NULL DEFAULT '',
    activo            BOOLEAN     NOT NULL DEFAULT TRUE,
    actualizado_en    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_por   VARCHAR(80) NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ciudades_activo ON invitaciones_ciudades (activo);

-- ===========================================================================
-- Facturación
-- ===========================================================================

-- Historial de tarifas. Nunca se edita una fila: cada cambio inserta una nueva
-- con `vigente_desde`, para poder auditar qué precio regía en cada momento.
CREATE TABLE IF NOT EXISTS tarifas (
    id                              SERIAL PRIMARY KEY,
    vigente_desde                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modelo                          VARCHAR(80) NOT NULL DEFAULT '',
    -- Costo real del proveedor, USD por millón de tokens.
    precio_input_usd_1m             NUMERIC(12, 6) NOT NULL,
    precio_cached_input_usd_1m      NUMERIC(12, 6) NOT NULL,
    precio_output_usd_1m            NUMERIC(12, 6) NOT NULL,
    -- Lo que se le cobra al cliente por un turno con LLM: costo real x este factor.
    multiplicador_llm               NUMERIC(6, 3)  NOT NULL DEFAULT 1.600,
    -- Lo que se le cobra al cliente por un mensaje disparado solo por código.
    precio_mensaje_codigo_microusd  BIGINT         NOT NULL DEFAULT 2000,
    creado_por                      VARCHAR(80)    NOT NULL DEFAULT 'sistema',
    nota                            TEXT           NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tarifas_vigencia ON tarifas (vigente_desde DESC);

-- Un periodo es el intervalo que ve el cliente en su factura. "Resetear" no
-- borra nada: cierra el periodo abierto (congelando sus totales) y abre otro.
CREATE TABLE IF NOT EXISTS periodos_facturacion (
    id                          SERIAL PRIMARY KEY,
    abierto_en                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cerrado_en                  TIMESTAMPTZ,
    cerrado_por                 VARCHAR(80) NOT NULL DEFAULT '',
    -- Totales congelados al cerrar: el histórico no se recalcula nunca.
    total_real_microusd         BIGINT      NOT NULL DEFAULT 0,
    total_cliente_microusd      BIGINT      NOT NULL DEFAULT 0,
    total_eventos               BIGINT      NOT NULL DEFAULT 0,
    -- Si el admin decide "sumar al periodo actual" un periodo ya cerrado, aquí
    -- queda a cuál se reincorporó. Las vistas suman ambos.
    reincorporado_en_periodo_id INTEGER REFERENCES periodos_facturacion(id),
    nota                        TEXT        NOT NULL DEFAULT ''
);

-- Solo puede haber UN periodo abierto a la vez. El índice parcial lo garantiza
-- a nivel de base de datos: dos cierres simultáneos no pueden dejar dos abiertos.
CREATE UNIQUE INDEX IF NOT EXISTS idx_periodo_unico_abierto
    ON periodos_facturacion ((cerrado_en IS NULL)) WHERE cerrado_en IS NULL;

-- Libro mayor del consumo. Una fila por hecho facturable.
-- El costo se congela en la fila con la tarifa vigente al momento del evento:
-- cambiar precios más tarde NUNCA reescribe el pasado.
CREATE TABLE IF NOT EXISTS uso_eventos (
    id                     BIGSERIAL PRIMARY KEY,
    ts                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    periodo_id             INTEGER     NOT NULL REFERENCES periodos_facturacion(id),
    tarifa_id              INTEGER     REFERENCES tarifas(id),
    client_id              VARCHAR(80) NOT NULL DEFAULT '',
    canal                  VARCHAR(20) NOT NULL DEFAULT '',
    -- 'llm'    = el turno pasó por el modelo (se cobra sobre tokens)
    -- 'codigo' = mensaje disparado por algoritmo, sin modelo (tarifa por mensaje)
    categoria              VARCHAR(10) NOT NULL CHECK (categoria IN ('llm', 'codigo')),
    origen                 VARCHAR(30) NOT NULL DEFAULT '',
    modelo                 VARCHAR(80) NOT NULL DEFAULT '',
    tokens_entrada         INTEGER     NOT NULL DEFAULT 0,
    tokens_cacheados       INTEGER     NOT NULL DEFAULT 0,
    tokens_salida          INTEGER     NOT NULL DEFAULT 0,
    mensajes               INTEGER     NOT NULL DEFAULT 0,
    costo_real_microusd    BIGINT      NOT NULL DEFAULT 0,
    costo_cliente_microusd BIGINT      NOT NULL DEFAULT 0
);

-- Consulta caliente del dashboard: agregados del periodo (refresco cada 5s).
CREATE INDEX IF NOT EXISTS idx_uso_periodo    ON uso_eventos (periodo_id, ts);
CREATE INDEX IF NOT EXISTS idx_uso_cliente    ON uso_eventos (client_id, ts DESC);

-- ===========================================================================
-- Plantillas de mensaje y envíos manuales
-- ===========================================================================

CREATE TABLE IF NOT EXISTS plantillas_mensaje (
    id              SERIAL PRIMARY KEY,
    nombre          VARCHAR(120) NOT NULL,
    texto           TEXT         NOT NULL DEFAULT '',
    -- 'imagen', 'video' o '' (solo texto).
    media_tipo      VARCHAR(10)  NOT NULL DEFAULT '' CHECK (media_tipo IN ('', 'imagen', 'video')),
    -- ID de Google Drive o URL pública directa.
    media_ref       TEXT         NOT NULL DEFAULT '',
    creado_por      VARCHAR(80)  NOT NULL DEFAULT '',
    creado_en       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    actualizado_en  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Cola de envíos manuales. El dashboard inserta en 'pendiente'; una tarea Celery
-- del bot la drena. Así los dos servicios no se llaman por HTTP y un reinicio
-- de cualquiera de los dos no pierde envíos.
CREATE TABLE IF NOT EXISTS envios (
    id              BIGSERIAL PRIMARY KEY,
    plantilla_id    INTEGER     REFERENCES plantillas_mensaje(id) ON DELETE SET NULL,
    -- Copia del contenido al momento de encolar: editar la plantilla después no
    -- cambia lo que ya se envió (ni lo que está por enviarse).
    texto           TEXT        NOT NULL DEFAULT '',
    media_tipo      VARCHAR(10) NOT NULL DEFAULT '',
    media_ref       TEXT        NOT NULL DEFAULT '',
    canal           VARCHAR(20) NOT NULL,
    destino_id      VARCHAR(80) NOT NULL,
    estado          VARCHAR(20) NOT NULL DEFAULT 'pendiente'
                    CHECK (estado IN ('pendiente', 'enviando', 'enviado', 'error', 'en_revision')),
    intentos        INTEGER     NOT NULL DEFAULT 0,
    -- Mensaje accionable para el cliente ("no se pudo abrir la imagen, revisa
    -- el enlace"). Vacío si la causa no es cosa suya.
    error_cliente   TEXT        NOT NULL DEFAULT '',
    -- Traza completa. Solo visible para el administrador.
    error_tecnico   TEXT        NOT NULL DEFAULT '',
    creado_por      VARCHAR(80) NOT NULL DEFAULT '',
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    enviado_en      TIMESTAMPTZ
);

-- La tarea del worker busca "pendientes más antiguos primero".
CREATE INDEX IF NOT EXISTS idx_envios_pendientes
    ON envios (creado_en) WHERE estado = 'pendiente';
CREATE INDEX IF NOT EXISTS idx_envios_listado ON envios (creado_en DESC);

-- Errores que el cliente decidió escalar. Es la bandeja del administrador.
CREATE TABLE IF NOT EXISTS incidencias (
    id             BIGSERIAL PRIMARY KEY,
    envio_id       BIGINT      REFERENCES envios(id) ON DELETE CASCADE,
    reportado_por  VARCHAR(80) NOT NULL DEFAULT '',
    detalle        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    estado         VARCHAR(20) NOT NULL DEFAULT 'abierta'
                   CHECK (estado IN ('abierta', 'revisada')),
    nota_admin     TEXT        NOT NULL DEFAULT '',
    creado_en      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revisado_en    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_incidencias_abiertas
    ON incidencias (creado_en DESC) WHERE estado = 'abierta';
