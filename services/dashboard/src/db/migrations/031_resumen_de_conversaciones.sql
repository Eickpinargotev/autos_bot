-- Una fila compacta por conversación para el listado del dashboard.
-- Agrupar conversation_messages completo en cada mensaje nuevo hacía que el
-- costo creciera con todo el historial. Esta tabla se mantiene al insertar y
-- convierte el listado en una lectura directa por índice.

CREATE TABLE IF NOT EXISTS conversation_threads (
    proyecto_id       INTEGER     NOT NULL REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    client_id         VARCHAR(80) NOT NULL,
    canal             VARCHAR(20) NOT NULL,
    primera_actividad TIMESTAMPTZ NOT NULL,
    ultima_actividad  TIMESTAMPTZ NOT NULL,
    ultimo_mensaje_id BIGINT      NOT NULL,
    nombre            VARCHAR(200) NOT NULL DEFAULT '',
    mensajes          BIGINT      NOT NULL DEFAULT 0,
    eventos           BIGINT      NOT NULL DEFAULT 0,
    respuestas_bot    BIGINT      NOT NULL DEFAULT 0,
    respuestas_dueno  BIGINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (proyecto_id, client_id, canal)
);

CREATE INDEX IF NOT EXISTS idx_threads_listado
    ON conversation_threads (proyecto_id, ultima_actividad DESC, ultimo_mensaje_id DESC);

-- Foto inicial para instalaciones que ya tienen historial.
INSERT INTO conversation_threads (
    proyecto_id, client_id, canal, primera_actividad, ultima_actividad,
    ultimo_mensaje_id, nombre, mensajes, eventos, respuestas_bot, respuestas_dueno
)
SELECT proyecto_id, client_id, canal,
       MIN(created_at), MAX(created_at), MAX(id),
       COALESCE((array_agg(sender_name ORDER BY id DESC)
                 FILTER (WHERE direction = 'inbound' AND sender_name <> ''))[1], ''),
       COUNT(*) FILTER (WHERE direction <> 'internal'),
       COUNT(*) FILTER (WHERE direction = 'internal'),
       COUNT(*) FILTER (WHERE direction <> 'internal' AND author = 'ia'),
       COUNT(*) FILTER (WHERE direction <> 'internal' AND author = 'dueño')
FROM conversation_messages
GROUP BY proyecto_id, client_id, canal
ON CONFLICT (proyecto_id, client_id, canal) DO UPDATE SET
    primera_actividad = EXCLUDED.primera_actividad,
    ultima_actividad = EXCLUDED.ultima_actividad,
    ultimo_mensaje_id = EXCLUDED.ultimo_mensaje_id,
    nombre = EXCLUDED.nombre,
    mensajes = EXCLUDED.mensajes,
    eventos = EXCLUDED.eventos,
    respuestas_bot = EXCLUDED.respuestas_bot,
    respuestas_dueno = EXCLUDED.respuestas_dueno;

CREATE OR REPLACE FUNCTION actualizar_resumen_conversacion_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO conversation_threads (
        proyecto_id, client_id, canal, primera_actividad, ultima_actividad,
        ultimo_mensaje_id, nombre, mensajes, eventos, respuestas_bot, respuestas_dueno
    ) VALUES (
        NEW.proyecto_id, NEW.client_id, NEW.canal, NEW.created_at, NEW.created_at,
        NEW.id,
        CASE WHEN NEW.direction = 'inbound' THEN COALESCE(NEW.sender_name, '') ELSE '' END,
        CASE WHEN NEW.direction <> 'internal' THEN 1 ELSE 0 END,
        CASE WHEN NEW.direction = 'internal' THEN 1 ELSE 0 END,
        CASE WHEN NEW.direction <> 'internal' AND NEW.author = 'ia' THEN 1 ELSE 0 END,
        CASE WHEN NEW.direction <> 'internal' AND NEW.author = 'dueño' THEN 1 ELSE 0 END
    )
    ON CONFLICT (proyecto_id, client_id, canal) DO UPDATE SET
        primera_actividad = LEAST(conversation_threads.primera_actividad, EXCLUDED.primera_actividad),
        ultima_actividad = GREATEST(conversation_threads.ultima_actividad, EXCLUDED.ultima_actividad),
        ultimo_mensaje_id = GREATEST(conversation_threads.ultimo_mensaje_id, EXCLUDED.ultimo_mensaje_id),
        nombre = CASE
            WHEN NEW.direction = 'inbound' AND COALESCE(NEW.sender_name, '') <> '' THEN NEW.sender_name
            ELSE conversation_threads.nombre
        END,
        mensajes = conversation_threads.mensajes + EXCLUDED.mensajes,
        eventos = conversation_threads.eventos + EXCLUDED.eventos,
        respuestas_bot = conversation_threads.respuestas_bot + EXCLUDED.respuestas_bot,
        respuestas_dueno = conversation_threads.respuestas_dueno + EXCLUDED.respuestas_dueno;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_resumen_conversacion_insert ON conversation_messages;
CREATE TRIGGER trg_resumen_conversacion_insert
AFTER INSERT ON conversation_messages
FOR EACH ROW EXECUTE FUNCTION actualizar_resumen_conversacion_insert();

-- Los borrados actuales eliminan conversaciones completas (manual o por
-- retención). Un trigger por sentencia evita recalcular miles de veces durante
-- un DELETE grande y retira solo los resúmenes que ya no tienen mensajes.
CREATE OR REPLACE FUNCTION limpiar_resumen_conversacion_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM conversation_threads t
    USING (
        SELECT DISTINCT proyecto_id, client_id, canal FROM mensajes_borrados
    ) b
    WHERE t.proyecto_id = b.proyecto_id
      AND t.client_id = b.client_id
      AND t.canal = b.canal
      AND NOT EXISTS (
          SELECT 1 FROM conversation_messages m
          WHERE m.proyecto_id = b.proyecto_id
            AND m.client_id = b.client_id
            AND m.canal = b.canal
      );
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_resumen_conversacion_delete ON conversation_messages;
CREATE TRIGGER trg_resumen_conversacion_delete
AFTER DELETE ON conversation_messages
REFERENCING OLD TABLE AS mensajes_borrados
FOR EACH STATEMENT EXECUTE FUNCTION limpiar_resumen_conversacion_delete();
