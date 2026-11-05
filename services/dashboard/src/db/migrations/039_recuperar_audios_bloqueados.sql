-- Recuperar solo notas de voz cuya recepción y bloqueo constan en auditoría.
-- No descargar ni transcribir medios, ni inferir contenido. Solo añadir las
-- posteriores al último evento visible para respetar la paginación por id.
INSERT INTO conversation_messages (
    proyecto_id, client_id, canal, direction, author, sender_id, sender_name,
    message_type, text, event_type, created_at, correlation_id
)
SELECT a.proyecto_id, a.client_id, a.canal, 'inbound', 'cliente', a.client_id,
       COALESCE(t.nombre, ''), 'audio', '', 'audio_bloqueado', a.occurred_at, a.correlation_id
FROM conversation_audit a
LEFT JOIN conversation_threads t USING (proyecto_id, client_id, canal)
WHERE a.event_type = 'message_received' AND a.metadata->>'message_type' = 'audio'
  AND COALESCE(a.correlation_id, '') <> ''
  AND EXISTS (
      SELECT 1 FROM conversation_audit b
      WHERE b.proyecto_id = a.proyecto_id AND b.client_id = a.client_id AND b.canal = a.canal
        AND b.correlation_id = a.correlation_id AND b.event_type = 'decision_blocked'
        AND b.metadata->>'branch' IN ('temporary_block', 'permanent_block')
  )
  AND (t.ultima_actividad_visible IS NULL OR a.occurred_at > t.ultima_actividad_visible)
  AND NOT EXISTS (
      SELECT 1 FROM conversation_messages m
      WHERE m.proyecto_id = a.proyecto_id AND m.client_id = a.client_id AND m.canal = a.canal
        AND m.direction = 'inbound' AND m.message_type = 'audio'
        AND (m.correlation_id = a.correlation_id OR
             m.created_at BETWEEN a.occurred_at AND a.occurred_at + INTERVAL '1 minute')
  )
ORDER BY a.occurred_at, a.id;
