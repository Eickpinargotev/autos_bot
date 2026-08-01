-- Recuperación de la cuenta de administrador, impersonación, mensajes en cadena
-- y contadores de intervención humana.

-- ===========================================================================
-- Configuración interna del sistema
-- ===========================================================================
-- Pares clave/valor que el propio sistema necesita recordar entre arranques.
-- Hoy guarda la huella de la contraseña de administrador definida en el entorno,
-- para saber si cambió (ver §admin más abajo).

CREATE TABLE IF NOT EXISTS sistema_config (
    clave           VARCHAR(80) PRIMARY KEY,
    valor           TEXT        NOT NULL DEFAULT '',
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ===========================================================================
-- Recuperación de contraseña por código de un solo uso
-- ===========================================================================
--
-- El código se guarda HASHEADO, igual que una contraseña: si alguien lee la
-- tabla no puede usarlo. Solo viaja en claro dentro del mensaje de Telegram.

CREATE TABLE IF NOT EXISTS codigos_recuperacion (
    id           SERIAL PRIMARY KEY,
    usuario_id   INTEGER     NOT NULL REFERENCES dashboard_usuarios(id) ON DELETE CASCADE,
    codigo_hash  TEXT        NOT NULL,
    creado_en    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expira_en    TIMESTAMPTZ NOT NULL,
    usado_en     TIMESTAMPTZ,
    intentos     INTEGER     NOT NULL DEFAULT 0,
    ip           VARCHAR(64) NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_recuperacion_usuario
    ON codigos_recuperacion (usuario_id, creado_en DESC);

-- ===========================================================================
-- Impersonación: el admin entra al panel de un cliente sin su contraseña
-- ===========================================================================
--
-- Guardar una segunda contraseña "de administrador" por cada cliente obligaría a
-- almacenarla de forma recuperable, y eso anula el hash: quien leyera la base
-- tendría las claves de todos. En vez de eso, la sesión del admin recuerda a
-- quién está suplantando; sus credenciales siguen siendo las suyas y el cambio
-- queda registrado.

ALTER TABLE dashboard_sesiones
    ADD COLUMN IF NOT EXISTS suplantando_a INTEGER REFERENCES dashboard_usuarios(id) ON DELETE SET NULL;

-- Bitácora de accesos del admin a perfiles ajenos. Es una acción sensible:
-- tiene que dejar rastro aunque nadie la esté mirando.
CREATE TABLE IF NOT EXISTS accesos_suplantacion (
    id           BIGSERIAL PRIMARY KEY,
    admin_id     INTEGER     REFERENCES dashboard_usuarios(id) ON DELETE SET NULL,
    objetivo_id  INTEGER     REFERENCES dashboard_usuarios(id) ON DELETE SET NULL,
    inicio_en    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fin_en       TIMESTAMPTZ,
    ip           VARCHAR(64) NOT NULL DEFAULT ''
);

-- ===========================================================================
-- Mensajes en cadena
-- ===========================================================================
--
-- Un "mensaje" del panel puede ser en realidad VARIOS mensajes que se envían
-- uno tras otro. Antes era un único texto; ahora la plantilla es la cabecera y
-- las partes son las piezas ordenadas.

ALTER TABLE plantillas_mensaje
    ADD COLUMN IF NOT EXISTS clave VARCHAR(120);

-- La clave es con la que se identifica el mensaje al enviarlo (p. ej. el nombre
-- de la ciudad). Se rellena desde el nombre para las filas que ya existían.
UPDATE plantillas_mensaje SET clave = UPPER(nombre) WHERE clave IS NULL OR clave = '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_plantilla_clave ON plantillas_mensaje (clave);

CREATE TABLE IF NOT EXISTS plantilla_partes (
    id            BIGSERIAL PRIMARY KEY,
    plantilla_id  INTEGER     NOT NULL REFERENCES plantillas_mensaje(id) ON DELETE CASCADE,
    orden         INTEGER     NOT NULL DEFAULT 1,
    texto         TEXT        NOT NULL DEFAULT '',
    media_tipo    VARCHAR(10) NOT NULL DEFAULT '' CHECK (media_tipo IN ('', 'imagen', 'video')),
    media_ref     TEXT        NOT NULL DEFAULT '',
    -- Resultado de comprobar el adjunto AL GUARDAR, para que un enlace roto o
    -- privado se descubra ahí y no cuando el mensaje ya salió mal al cliente.
    media_ok      BOOLEAN,
    media_error   TEXT        NOT NULL DEFAULT '',
    media_revisada_en TIMESTAMPTZ,
    UNIQUE (plantilla_id, orden)
);

CREATE INDEX IF NOT EXISTS idx_partes_plantilla ON plantilla_partes (plantilla_id, orden);

-- Migra el contenido de las plantillas que ya existían a su primera parte.
INSERT INTO plantilla_partes (plantilla_id, orden, texto, media_tipo, media_ref)
SELECT id, 1, texto, media_tipo, media_ref
FROM plantillas_mensaje p
WHERE NOT EXISTS (SELECT 1 FROM plantilla_partes pp WHERE pp.plantilla_id = p.id);

ALTER TABLE plantillas_mensaje DROP COLUMN IF EXISTS texto;
ALTER TABLE plantillas_mensaje DROP COLUMN IF EXISTS media_tipo;
ALTER TABLE plantillas_mensaje DROP COLUMN IF EXISTS media_ref;

-- ===========================================================================
-- Envíos: una cadena completa por destinatario
-- ===========================================================================
--
-- El envío guarda una COPIA de las partes al momento de encolarse: editar la
-- plantilla después no cambia lo que ya se mandó ni lo que está por salir.

ALTER TABLE envios ADD COLUMN IF NOT EXISTS partes JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE envios ADD COLUMN IF NOT EXISTS partes_enviadas INTEGER NOT NULL DEFAULT 0;

UPDATE envios
SET partes = jsonb_build_array(
        jsonb_build_object('texto', texto, 'media_tipo', media_tipo, 'media_ref', media_ref)
    )
WHERE partes = '[]'::jsonb;

ALTER TABLE envios DROP COLUMN IF EXISTS texto;
ALTER TABLE envios DROP COLUMN IF EXISTS media_tipo;
ALTER TABLE envios DROP COLUMN IF EXISTS media_ref;

-- ===========================================================================
-- Intervención humana en la conversación
-- ===========================================================================
--
-- Cuando el dueño del negocio escribe con su propio número, el bot debe
-- callarse: ya hay una persona atendiendo. Se cuenta para poder reportarlo.

ALTER TABLE seguimiento_clientes
    ADD COLUMN IF NOT EXISTS intervenciones_humano INTEGER NOT NULL DEFAULT 0;
ALTER TABLE seguimiento_clientes
    ADD COLUMN IF NOT EXISTS ultima_intervencion_humano TIMESTAMPTZ;
