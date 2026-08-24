-- Ritmo de las cadenas automáticas y recordatorios de publicidad por proyecto.
-- Los valores anteriores vivían en variables de entorno globales, por lo que
-- cambiar un negocio cambiaba también todos los demás.

CREATE TABLE IF NOT EXISTS proyecto_tiempos_mensajes (
    proyecto_id INTEGER PRIMARY KEY REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    intervalo_mensajes_segundos INTEGER NOT NULL DEFAULT 5
        CHECK (intervalo_mensajes_segundos BETWEEN 1 AND 60),
    publicidad_recordatorio_1_segundos INTEGER NOT NULL DEFAULT 7200
        CHECK (publicidad_recordatorio_1_segundos BETWEEN 5 AND 1209600),
    publicidad_recordatorio_2_segundos INTEGER NOT NULL DEFAULT 72000
        CHECK (publicidad_recordatorio_2_segundos BETWEEN 5 AND 1209600),
    publicidad_recordatorio_3_segundos INTEGER NOT NULL DEFAULT 82800
        CHECK (publicidad_recordatorio_3_segundos BETWEEN 5 AND 1209600),
    actualizado_por VARCHAR(120) NOT NULL DEFAULT '',
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT tiempos_publicidad_en_orden CHECK (
        publicidad_recordatorio_1_segundos < publicidad_recordatorio_2_segundos
        AND publicidad_recordatorio_2_segundos < publicidad_recordatorio_3_segundos
    )
);

INSERT INTO proyecto_tiempos_mensajes (proyecto_id, actualizado_por)
SELECT id, 'migración 028' FROM clientes_whatsapp
ON CONFLICT (proyecto_id) DO NOTHING;

CREATE OR REPLACE FUNCTION inicializar_tiempos_de_mensajes()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO proyecto_tiempos_mensajes (proyecto_id, actualizado_por)
    VALUES (NEW.id, 'sistema')
    ON CONFLICT (proyecto_id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_inicializar_tiempos_de_mensajes ON clientes_whatsapp;
CREATE TRIGGER trg_inicializar_tiempos_de_mensajes
AFTER INSERT ON clientes_whatsapp
FOR EACH ROW EXECUTE FUNCTION inicializar_tiempos_de_mensajes();
