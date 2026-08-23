-- Dos capas comerciales editables por proyecto: agente principal y recordatorio.
-- Las reglas técnicas y de seguridad continúan en código.

ALTER TABLE proyecto_instrucciones
    ADD COLUMN IF NOT EXISTS tipo VARCHAR(20) NOT NULL DEFAULT 'principal';

ALTER TABLE proyecto_instrucciones
    DROP CONSTRAINT IF EXISTS proyecto_instrucciones_proyecto_id_version_key;
ALTER TABLE proyecto_instrucciones
    DROP CONSTRAINT IF EXISTS proyecto_instrucciones_tipo_check;
ALTER TABLE proyecto_instrucciones
    ADD CONSTRAINT proyecto_instrucciones_tipo_check
    CHECK (tipo IN ('principal', 'recordatorio'));
ALTER TABLE proyecto_instrucciones
    ADD CONSTRAINT uq_proyecto_instruccion_version
    UNIQUE (proyecto_id, tipo, version);

DROP INDEX IF EXISTS uq_instruccion_activa;
CREATE UNIQUE INDEX uq_instruccion_activa
    ON proyecto_instrucciones (proyecto_id, tipo) WHERE activa;

-- Un contenido vacío era el marcador creado por la migración 020, no un prompt
-- operativo. Se conserva como historial y se crea encima una versión real.
DO $$
DECLARE
    objetivo RECORD;
    siguiente_version INTEGER;
BEGIN
    FOR objetivo IN
        SELECT c.id AS proyecto_id
        FROM clientes_whatsapp c
        WHERE NOT EXISTS (
            SELECT 1 FROM proyecto_instrucciones a
            WHERE a.proyecto_id = c.id AND a.tipo = 'principal'
              AND a.activa AND BTRIM(a.contenido) <> ''
        )
    LOOP
        SELECT COALESCE(MAX(version), 0) + 1 INTO siguiente_version
        FROM proyecto_instrucciones
        WHERE proyecto_id = objetivo.proyecto_id AND tipo = 'principal';

        -- Son sentencias separadas a propósito. En una sola sentencia con CTE,
        -- el índice parcial puede comprobar el INSERT antes de hacer visible el
        -- UPDATE y detectar dos activas aunque la intención sea reemplazar una.
        UPDATE proyecto_instrucciones
        SET activa = FALSE
        WHERE proyecto_id = objetivo.proyecto_id AND tipo = 'principal' AND activa;

        INSERT INTO proyecto_instrucciones
            (proyecto_id, tipo, version, contenido, activa, creado_por)
        VALUES (
            objetivo.proyecto_id, 'principal', siguiente_version,
            'Eres Enrique, asesor de una escuela de manejo en Costa Rica. Atiende con un tono directo, cálido y profesional. Trata siempre al cliente de usted; nunca lo tutees. Ayuda a entender qué servicio necesita y a avanzar al siguiente paso, sin inventar precios, horarios, enlaces ni requisitos.',
            TRUE, 'migración 024'
        );
    END LOOP;
END $$;

INSERT INTO proyecto_instrucciones
    (proyecto_id, tipo, version, contenido, activa, creado_por)
SELECT c.id, 'recordatorio', 1,
       'Retoma la conversación de manera breve, cordial y natural. Recuerda únicamente el paso que quedó pendiente y formula como máximo una pregunta. Usa “usted”, nunca tutees, y no presiones al cliente. Cuando envíes un recordatorio, inicia con: 📌 Hola!!!',
       TRUE, 'migración 024'
FROM clientes_whatsapp c
WHERE NOT EXISTS (
    SELECT 1 FROM proyecto_instrucciones i
    WHERE i.proyecto_id = c.id AND i.tipo = 'recordatorio'
);

CREATE TABLE IF NOT EXISTS proyecto_recordatorios (
    proyecto_id       INTEGER PRIMARY KEY REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    habilitado        BOOLEAN NOT NULL DEFAULT TRUE,
    intervalo_minutos INTEGER NOT NULL DEFAULT 60
        CHECK (intervalo_minutos BETWEEN 1 AND 20160),
    actualizado_por   VARCHAR(120) NOT NULL DEFAULT '',
    actualizado_en    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO proyecto_recordatorios (proyecto_id, habilitado, intervalo_minutos, actualizado_por)
SELECT id, TRUE, 60, 'migración 024' FROM clientes_whatsapp
ON CONFLICT (proyecto_id) DO NOTHING;

-- Los proyectos creados después de esta migración también nacen listos: la
-- pantalla nunca debe volver a mostrar una bandeja vacía.
CREATE OR REPLACE FUNCTION inicializar_prompts_del_proyecto()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO proyecto_instrucciones
        (proyecto_id, tipo, version, contenido, activa, creado_por)
    VALUES
        (NEW.id, 'principal', 1,
         'Eres Enrique, asesor de una escuela de manejo en Costa Rica. Atiende con un tono directo, cálido y profesional. Trata siempre al cliente de usted; nunca lo tutees. Ayuda a entender qué servicio necesita y a avanzar al siguiente paso, sin inventar precios, horarios, enlaces ni requisitos.',
         TRUE, 'sistema'),
        (NEW.id, 'recordatorio', 1,
         'Retoma la conversación de manera breve, cordial y natural. Recuerda únicamente el paso que quedó pendiente y formula como máximo una pregunta. Usa “usted”, nunca tutees, y no presiones al cliente. Cuando envíes un recordatorio, inicia con: 📌 Hola!!!',
         TRUE, 'sistema');
    INSERT INTO proyecto_recordatorios
        (proyecto_id, habilitado, intervalo_minutos, actualizado_por)
    VALUES (NEW.id, TRUE, 60, 'sistema');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_inicializar_prompts_del_proyecto ON clientes_whatsapp;
CREATE TRIGGER trg_inicializar_prompts_del_proyecto
AFTER INSERT ON clientes_whatsapp
FOR EACH ROW EXECUTE FUNCTION inicializar_prompts_del_proyecto();
