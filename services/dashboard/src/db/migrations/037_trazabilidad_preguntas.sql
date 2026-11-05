-- Vínculo durable entre una pregunta y la interacción que la originó.
ALTER TABLE conversation_messages ADD COLUMN correlation_id TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_messages_correlacion ON conversation_messages
    (proyecto_id, client_id, canal, correlation_id, id) WHERE correlation_id <> '';
ALTER TABLE preguntas_sin_respuesta
    ADD COLUMN client_id VARCHAR(80) NOT NULL DEFAULT '',
    ADD COLUMN canal VARCHAR(20) NOT NULL DEFAULT '',
    ADD COLUMN correlation_id TEXT NOT NULL DEFAULT '',
    ADD COLUMN diagnostico JSONB;

-- Las trazas antiguas ya guardaban esta relación en su entrada.
UPDATE conversation_messages SET correlation_id = entrada->>'audit_correlation_id'
WHERE COALESCE(entrada->>'audit_correlation_id', '') <> '';

-- Solo recuperar vínculos históricos inequívocos. Nunca adivinar por cercanía.
WITH candidatos AS (
    SELECT p.id AS pregunta_id, m.id AS mensaje_id,
           COUNT(*) OVER (PARTITION BY p.id) AS cantidad,
           COUNT(*) OVER (PARTITION BY m.id) AS preguntas
    FROM preguntas_sin_respuesta p
    JOIN conversation_messages m ON m.proyecto_id = p.proyecto_id
      AND m.tool_name = 'unanswered_question.create'
      AND m.entrada->>'question' = p.pregunta
      AND m.salida->>'created' = 'true'
      AND m.created_at >= p.creado_en
      AND m.created_at < p.creado_en + INTERVAL '5 seconds'
)
UPDATE preguntas_sin_respuesta p
SET client_id = m.client_id, canal = m.canal, correlation_id = m.correlation_id
FROM candidatos c JOIN conversation_messages m ON m.id = c.mensaje_id
WHERE p.id = c.pregunta_id AND c.cantidad = 1 AND c.preguntas = 1;
