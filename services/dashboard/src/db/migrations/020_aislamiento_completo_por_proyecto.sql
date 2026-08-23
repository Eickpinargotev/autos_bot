-- Cada dato operativo pertenece a un proyecto.
--
-- Hasta esta migración el webhook sabía qué proyecto recibía un mensaje, pero
-- casi todas las tablas seguían identificando a una conversación únicamente
-- por (canal, número). Eso mezcla dos negocios en cuanto la misma persona
-- escribe a ambos. La columna se rellena únicamente cuando existe un solo
-- proyecto: con varios, atribuir el histórico por intuición sería una fuga de
-- datos y la migración se detiene antes de modificarlo.

DO $$
DECLARE
    proyectos INTEGER;
    filas_sin_proyecto BIGINT;
BEGIN
    SELECT COUNT(*) INTO proyectos FROM clientes_whatsapp;
    SELECT
        (SELECT COUNT(*) FROM conversation_messages) +
        (SELECT COUNT(*) FROM conversation_shots) +
        (SELECT COUNT(*) FROM seguimiento_clientes) +
        (SELECT COUNT(*) FROM resumen_mensual) +
        (SELECT COUNT(*) FROM users_blocked) +
        (SELECT COUNT(*) FROM reportes) +
        (SELECT COUNT(*) FROM keyword_registros) +
        (SELECT COUNT(*) FROM preguntas_sin_respuesta) +
        (SELECT COUNT(*) FROM rag_chunks) +
        (SELECT COUNT(*) FROM uso_eventos) +
        (SELECT COUNT(*) FROM plantillas_mensaje) +
        (SELECT COUNT(*) FROM palabras_clave) +
        (SELECT COUNT(*) FROM envios_lote) +
        (SELECT COUNT(*) FROM envios)
    INTO filas_sin_proyecto;

    IF filas_sin_proyecto > 0 AND proyectos <> 1 THEN
        RAISE EXCEPTION
            'No se puede atribuir el histórico global: existen % proyectos. Deje un único proyecto antes de aplicar la migración 020.',
            proyectos;
    END IF;
END $$;

ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE conversation_shots ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE seguimiento_clientes ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE resumen_mensual ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE users_blocked ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE users_blocked DROP CONSTRAINT IF EXISTS users_blocked_proyecto_id_fkey;
ALTER TABLE users_blocked ADD CONSTRAINT users_blocked_proyecto_id_fkey
    FOREIGN KEY (proyecto_id) REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE reportes ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE keyword_registros ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE preguntas_sin_respuesta ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE uso_eventos ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE plantillas_mensaje ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE palabras_clave ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE envios_lote ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE envios ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE incidencias ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;

DO $$
DECLARE unico INTEGER;
BEGIN
    SELECT id INTO unico FROM clientes_whatsapp LIMIT 1;
    IF unico IS NULL THEN
        RETURN;
    END IF;
    UPDATE conversation_messages SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE conversation_shots SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE seguimiento_clientes SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE resumen_mensual SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE users_blocked SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE reportes SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE keyword_registros SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE preguntas_sin_respuesta SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE rag_chunks SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE uso_eventos SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE plantillas_mensaje SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE palabras_clave SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE envios_lote SET proyecto_id = unico WHERE proyecto_id IS NULL;
    UPDATE envios SET proyecto_id = COALESCE(
        (SELECT l.proyecto_id FROM envios_lote l WHERE l.id = envios.lote_id), unico
    ) WHERE proyecto_id IS NULL;
    UPDATE incidencias SET proyecto_id = COALESCE(
        (SELECT e.proyecto_id FROM envios e WHERE e.id = incidencias.envio_id), unico
    ) WHERE proyecto_id IS NULL;
END $$;

-- Los dos nombres antiguos llamaban "cliente" al proyecto. Desde aquí la base
-- usa el mismo vocabulario que el resto de la aplicación.
ALTER TABLE conversacion_negocio RENAME COLUMN cliente_id TO proyecto_id;
ALTER TABLE bloqueos_permanentes RENAME COLUMN cliente_id TO proyecto_id;

ALTER TABLE conversation_messages ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE conversation_shots ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE seguimiento_clientes ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE resumen_mensual ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE users_blocked ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE reportes ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE keyword_registros ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE preguntas_sin_respuesta ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE rag_chunks ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE uso_eventos ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE plantillas_mensaje ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE palabras_clave ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE envios_lote ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE envios ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE incidencias ALTER COLUMN proyecto_id SET NOT NULL;

-- Las unicidades que antes eran globales pasan a ser propias de cada proyecto.
ALTER TABLE seguimiento_clientes DROP CONSTRAINT IF EXISTS seguimiento_clientes_client_id_canal_key;
ALTER TABLE resumen_mensual DROP CONSTRAINT IF EXISTS resumen_mensual_pkey;
ALTER TABLE users_blocked DROP CONSTRAINT IF EXISTS users_blocked_pkey;
ALTER TABLE keyword_registros DROP CONSTRAINT IF EXISTS keyword_registros_registro_canal_key;
ALTER TABLE conversacion_negocio DROP CONSTRAINT IF EXISTS conversacion_negocio_pkey;
DROP INDEX IF EXISTS idx_plantilla_clave;
DROP INDEX IF EXISTS idx_palabra_clave_unica;

ALTER TABLE seguimiento_clientes ADD CONSTRAINT uq_seguimiento_proyecto UNIQUE (proyecto_id, client_id, canal);
ALTER TABLE resumen_mensual ADD CONSTRAINT resumen_mensual_pkey PRIMARY KEY (proyecto_id, mes);
ALTER TABLE users_blocked ADD CONSTRAINT users_blocked_pkey PRIMARY KEY (proyecto_id, user_id);
ALTER TABLE keyword_registros ADD CONSTRAINT uq_keyword_registro_proyecto UNIQUE (proyecto_id, registro, canal);
ALTER TABLE conversacion_negocio ADD CONSTRAINT conversacion_negocio_pkey PRIMARY KEY (proyecto_id, canal, client_id);
CREATE UNIQUE INDEX idx_plantilla_clave ON plantillas_mensaje (proyecto_id, clave);
CREATE UNIQUE INDEX idx_palabra_clave_unica ON palabras_clave (proyecto_id, lower(palabra));

DROP INDEX IF EXISTS idx_conv_msg_cliente;
DROP INDEX IF EXISTS idx_conv_msg_cursor;
DROP INDEX IF EXISTS idx_uso_cliente;
CREATE INDEX idx_conv_msg_cliente ON conversation_messages (proyecto_id, client_id, canal, created_at DESC);
CREATE INDEX idx_conv_msg_cursor ON conversation_messages (proyecto_id, client_id, canal, id DESC);
CREATE INDEX idx_uso_cliente ON uso_eventos (proyecto_id, client_id, ts DESC);
CREATE INDEX idx_reportes_proyecto ON reportes (proyecto_id, revisado, creado_en DESC);
CREATE INDEX idx_preguntas_proyecto ON preguntas_sin_respuesta (proyecto_id, atendida, creado_en DESC);
CREATE INDEX idx_rag_proyecto ON rag_chunks (proyecto_id, actualizado_en DESC);
CREATE INDEX idx_lotes_proyecto ON envios_lote (proyecto_id, creado_en DESC);

-- El dueño edita una capa comercial; las reglas internas siguen versionadas en
-- código. Las versiones son append-only para poder volver a una anterior.
CREATE TABLE IF NOT EXISTS proyecto_instrucciones (
    id             BIGSERIAL PRIMARY KEY,
    proyecto_id    INTEGER     NOT NULL REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    version        INTEGER     NOT NULL,
    contenido      TEXT        NOT NULL DEFAULT '',
    activa         BOOLEAN     NOT NULL DEFAULT TRUE,
    creado_por     VARCHAR(120) NOT NULL DEFAULT '',
    creado_en      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (proyecto_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_instruccion_activa
    ON proyecto_instrucciones (proyecto_id) WHERE activa;

INSERT INTO proyecto_instrucciones (proyecto_id, version, contenido, creado_por)
SELECT c.id, 1, '', 'migración 020'
FROM clientes_whatsapp c
WHERE NOT EXISTS (
    SELECT 1 FROM proyecto_instrucciones i WHERE i.proyecto_id = c.id
);
