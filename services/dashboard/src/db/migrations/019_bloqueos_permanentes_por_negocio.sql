-- Números a los que un negocio decidió no responder nunca con el bot.
--
-- No se mezclan con `users_blocked`: esa tabla representa pausas operativas
-- (por ejemplo, 12 días tras una intervención humana). Si ambos conceptos
-- compartieran fila, quitar un bloqueo permanente podría levantar sin querer
-- una pausa temporal que todavía debe seguir vigente.

CREATE TABLE IF NOT EXISTS bloqueos_permanentes (
    id          BIGSERIAL   PRIMARY KEY,
    cliente_id  INTEGER     NOT NULL REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    canal       VARCHAR(20) NOT NULL DEFAULT 'whatsapp',
    numero      VARCHAR(80) NOT NULL,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    creado_por  VARCHAR(120) NOT NULL DEFAULT '',
    CONSTRAINT uq_bloqueo_permanente UNIQUE (cliente_id, canal, numero),
    CONSTRAINT ck_bloqueo_permanente_numero CHECK (numero <> '')
);

CREATE INDEX IF NOT EXISTS idx_bloqueos_permanentes_cliente
    ON bloqueos_permanentes (cliente_id, creado_en DESC);

-- Importación inicial solicitada para el proyecto actual. De las claves
-- antiguas `<numero>_bloked_siempre_autos` solo importa el número; el sufijo y
-- el texto "true" eran la representación del sistema anterior, no datos del
-- bloqueo.
INSERT INTO bloqueos_permanentes (cliente_id, canal, numero, creado_por)
SELECT c.id, 'whatsapp', semilla.numero, 'importación inicial'
FROM clientes_whatsapp c
CROSS JOIN (VALUES
    ('50685774095'),
    ('5491123505900'),
    ('50663445336'),
    ('50660137256'),
    ('50670124152'),
    ('50671813466'),
    ('50684088424'),
    ('50650010101'),
    ('50686927261'),
    ('50661712826'),
    ('50650010500'),
    ('50683619566'),
    ('50685619257'),
    ('0292716817276')
) AS semilla(numero)
WHERE c.slug = 'escuela-de-manejo'
ON CONFLICT (cliente_id, canal, numero) DO NOTHING;
