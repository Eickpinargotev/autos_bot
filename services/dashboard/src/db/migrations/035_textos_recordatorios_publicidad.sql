-- Solo añade campos: NULL mantiene los textos originales del bot.
ALTER TABLE proyecto_tiempos_mensajes
    ADD COLUMN publicidad_recordatorio_1_texto TEXT CHECK (publicidad_recordatorio_1_texto IS NULL OR (length(btrim(publicidad_recordatorio_1_texto)) > 0 AND length(publicidad_recordatorio_1_texto) <= 2000)),
    ADD COLUMN publicidad_recordatorio_2_texto TEXT CHECK (publicidad_recordatorio_2_texto IS NULL OR (length(btrim(publicidad_recordatorio_2_texto)) > 0 AND length(publicidad_recordatorio_2_texto) <= 2000)),
    ADD COLUMN publicidad_recordatorio_3_texto TEXT CHECK (publicidad_recordatorio_3_texto IS NULL OR (length(btrim(publicidad_recordatorio_3_texto)) > 0 AND length(publicidad_recordatorio_3_texto) <= 2000));
