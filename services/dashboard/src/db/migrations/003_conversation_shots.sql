-- Shots de conversación: capturas de un turno completo (entrada, decisión del
-- agente, herramientas usadas y salida) que sirven de material para evaluar el
-- comportamiento del bot. Antes vivían en una tabla opcional de NocoDB.
--
-- Se purgan con la misma política de retención que el log de conversaciones
-- (`settings.CONVERSATION_RETENTION_DAYS`).

CREATE TABLE IF NOT EXISTS conversation_shots (
    id          BIGSERIAL PRIMARY KEY,
    fecha_hora  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    id_user     VARCHAR(80) NOT NULL DEFAULT '',
    canal       VARCHAR(20) NOT NULL DEFAULT '',
    revisado    BOOLEAN     NOT NULL DEFAULT FALSE,
    shot        JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_shots_fecha ON conversation_shots (fecha_hora DESC);
CREATE INDEX IF NOT EXISTS idx_shots_sin_revisar
    ON conversation_shots (fecha_hora DESC) WHERE NOT revisado;
