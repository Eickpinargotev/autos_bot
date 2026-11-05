-- La actividad técnica se conserva para diagnóstico/retención. El listado
-- se ordena por lo que el operador realmente puede ver en el chat.
ALTER TABLE conversation_threads ADD COLUMN ultima_actividad_visible TIMESTAMPTZ,
    ADD COLUMN ultimo_evento_visible_id BIGINT;
WITH ultimos AS (
    SELECT DISTINCT ON (proyecto_id, client_id, canal)
        proyecto_id, client_id, canal, created_at, id
    FROM conversation_messages
    WHERE direction <> 'internal' OR event_type = 'report_created'
    ORDER BY proyecto_id, client_id, canal, created_at DESC, id DESC
)
UPDATE conversation_threads t SET ultima_actividad_visible = u.created_at,
    ultimo_evento_visible_id = u.id
FROM ultimos u WHERE (t.proyecto_id, t.client_id, t.canal) = (u.proyecto_id, u.client_id, u.canal);
CREATE INDEX idx_threads_actividad_visible ON conversation_threads
    (proyecto_id, ultima_actividad_visible DESC, ultimo_evento_visible_id DESC)
    WHERE ultima_actividad_visible IS NOT NULL;

CREATE OR REPLACE FUNCTION actualizar_resumen_conversacion_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO conversation_threads (
        proyecto_id, client_id, canal, primera_actividad, ultima_actividad,
        ultimo_mensaje_id, nombre, mensajes, eventos, respuestas_bot, respuestas_dueno,
        ultima_actividad_visible, ultimo_evento_visible_id
    ) VALUES (
        NEW.proyecto_id, NEW.client_id, NEW.canal, NEW.created_at, NEW.created_at,
        NEW.id,
        CASE WHEN NEW.direction = 'inbound' THEN COALESCE(NEW.sender_name, '') ELSE '' END,
        CASE WHEN NEW.direction <> 'internal' THEN 1 ELSE 0 END,
        CASE WHEN NEW.direction = 'internal' THEN 1 ELSE 0 END,
        CASE WHEN NEW.direction <> 'internal' AND NEW.author = 'ia' THEN 1 ELSE 0 END,
        CASE WHEN NEW.direction <> 'internal' AND NEW.author = 'dueño' THEN 1 ELSE 0 END,
        CASE WHEN NEW.direction <> 'internal' OR NEW.event_type = 'report_created' THEN NEW.created_at END,
        CASE WHEN NEW.direction <> 'internal' OR NEW.event_type = 'report_created' THEN NEW.id END
    )
    ON CONFLICT (proyecto_id, client_id, canal) DO UPDATE SET
        primera_actividad = LEAST(conversation_threads.primera_actividad, EXCLUDED.primera_actividad),
        ultima_actividad = GREATEST(conversation_threads.ultima_actividad, EXCLUDED.ultima_actividad),
        ultimo_mensaje_id = GREATEST(conversation_threads.ultimo_mensaje_id, EXCLUDED.ultimo_mensaje_id),
        ultima_actividad_visible = GREATEST(conversation_threads.ultima_actividad_visible, EXCLUDED.ultima_actividad_visible),
        ultimo_evento_visible_id = CASE
            WHEN EXCLUDED.ultima_actividad_visible IS NOT NULL AND
                 (conversation_threads.ultima_actividad_visible IS NULL OR
                  (EXCLUDED.ultima_actividad_visible, EXCLUDED.ultimo_evento_visible_id) >
                  (conversation_threads.ultima_actividad_visible, conversation_threads.ultimo_evento_visible_id))
            THEN EXCLUDED.ultimo_evento_visible_id ELSE conversation_threads.ultimo_evento_visible_id END,
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
