-- Repara instalaciones donde la migración 020 se aplicó antes de que incluyera
-- `users_blocked` y `resumen_mensual`. Esas tablas conservaron sus claves
-- globales, mientras el código ya las consulta por proyecto.
--
-- No se elimina ningún registro. Si existe histórico, solo puede atribuirse
-- automáticamente cuando hay exactamente un proyecto; con más de uno se
-- detiene la migración para evitar mezclar bloqueos o datos de facturación.

DO $$
DECLARE
    proyectos INTEGER;
    filas_sin_proyecto BIGINT;
BEGIN
    SELECT COUNT(*) INTO proyectos FROM clientes_whatsapp;
    SELECT
        (SELECT COUNT(*) FROM users_blocked) +
        (SELECT COUNT(*) FROM resumen_mensual)
    INTO filas_sin_proyecto;

    IF filas_sin_proyecto > 0 AND proyectos <> 1 THEN
        RAISE EXCEPTION
            'No se puede atribuir users_blocked/resumen_mensual: existen % proyectos.',
            proyectos;
    END IF;
END $$;

ALTER TABLE users_blocked
    ADD COLUMN IF NOT EXISTS proyecto_id INTEGER;
ALTER TABLE resumen_mensual
    ADD COLUMN IF NOT EXISTS proyecto_id INTEGER;

DO $$
DECLARE unico INTEGER;
BEGIN
    SELECT id INTO unico FROM clientes_whatsapp LIMIT 1;
    IF unico IS NULL THEN
        RETURN;
    END IF;

    UPDATE users_blocked
       SET proyecto_id = unico
     WHERE proyecto_id IS NULL;
    UPDATE resumen_mensual
       SET proyecto_id = unico
     WHERE proyecto_id IS NULL;
END $$;

ALTER TABLE users_blocked
    DROP CONSTRAINT IF EXISTS users_blocked_proyecto_id_fkey;
ALTER TABLE users_blocked
    ADD CONSTRAINT users_blocked_proyecto_id_fkey
    FOREIGN KEY (proyecto_id) REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;

ALTER TABLE resumen_mensual
    DROP CONSTRAINT IF EXISTS resumen_mensual_proyecto_id_fkey;
ALTER TABLE resumen_mensual
    ADD CONSTRAINT resumen_mensual_proyecto_id_fkey
    FOREIGN KEY (proyecto_id) REFERENCES clientes_whatsapp(id) ON DELETE CASCADE;

ALTER TABLE users_blocked ALTER COLUMN proyecto_id SET NOT NULL;
ALTER TABLE resumen_mensual ALTER COLUMN proyecto_id SET NOT NULL;

ALTER TABLE users_blocked DROP CONSTRAINT IF EXISTS users_blocked_pkey;
ALTER TABLE resumen_mensual DROP CONSTRAINT IF EXISTS resumen_mensual_pkey;

ALTER TABLE users_blocked
    ADD CONSTRAINT users_blocked_pkey PRIMARY KEY (proyecto_id, user_id);
ALTER TABLE resumen_mensual
    ADD CONSTRAINT resumen_mensual_pkey PRIMARY KEY (proyecto_id, mes);
