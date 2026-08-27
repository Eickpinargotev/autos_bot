-- Cantidad máxima de seguimientos inteligentes configurable por proyecto.
-- El valor 2 conserva el comportamiento anterior; el panel permite bajarlo a
-- 1 sin cambiar a los demás negocios.

ALTER TABLE proyecto_recordatorios
    ADD COLUMN IF NOT EXISTS maximo_recordatorios SMALLINT NOT NULL DEFAULT 2;

ALTER TABLE proyecto_recordatorios
    DROP CONSTRAINT IF EXISTS proyecto_recordatorios_maximo_check;
ALTER TABLE proyecto_recordatorios
    ADD CONSTRAINT proyecto_recordatorios_maximo_check
    CHECK (maximo_recordatorios BETWEEN 1 AND 5);
