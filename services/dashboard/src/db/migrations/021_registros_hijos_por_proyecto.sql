-- Las piezas también declaran su proyecto, no solo lo heredan de la cabecera.
-- Esto permite imponer y auditar el aislamiento en cada fila operativa.

ALTER TABLE plantilla_partes
    ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;
ALTER TABLE palabra_clave_piezas
    ADD COLUMN IF NOT EXISTS proyecto_id INTEGER REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;

UPDATE plantilla_partes x
SET proyecto_id = p.proyecto_id
FROM plantillas_mensaje p
WHERE x.plantilla_id = p.id AND x.proyecto_id IS NULL;

UPDATE palabra_clave_piezas x
SET proyecto_id = p.proyecto_id
FROM palabras_clave p
WHERE x.palabra_id = p.id AND x.proyecto_id IS NULL;

ALTER TABLE plantilla_partes ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE palabra_clave_piezas ALTER COLUMN proyecto_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_partes_plantilla_proyecto
    ON plantilla_partes (proyecto_id, plantilla_id, orden);
CREATE INDEX IF NOT EXISTS idx_piezas_palabra_proyecto
    ON palabra_clave_piezas (proyecto_id, palabra_id, tipo, orden);
