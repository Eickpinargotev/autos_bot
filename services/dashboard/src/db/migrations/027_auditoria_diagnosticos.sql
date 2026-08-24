-- Toda exportación de trazas administrativas queda auditada. El proyecto se
-- elimina en cascada; el administrador se conserva como identidad histórica
-- mientras exista su cuenta.
CREATE TABLE IF NOT EXISTS diagnostico_descargas (
    id              BIGSERIAL PRIMARY KEY,
    proyecto_id     INTEGER NOT NULL REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    administrador_id INTEGER NOT NULL REFERENCES dashboard_usuarios(id),
    client_id       VARCHAR(80) NOT NULL,
    canal           VARCHAR(20) NOT NULL,
    ip              VARCHAR(64) NOT NULL DEFAULT '',
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_diagnostico_descargas_proyecto
    ON diagnostico_descargas (proyecto_id, creado_en DESC);
