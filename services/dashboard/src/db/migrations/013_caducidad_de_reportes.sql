-- Un reporte revisado caduca; uno pendiente, nunca.
--
-- La lista de reportes crecía para siempre: lo ya atendido se quedaba mezclado
-- con lo que falta por mirar hasta que lo importante dejaba de verse. A partir
-- de aquí, marcar uno como revisado le pone fecha de caducidad (7 días) y una
-- tarea del bot lo borra al cumplirse.
--
-- La fecha es NUEVA y no se reutiliza `creado_en`: el plazo cuenta desde que
-- alguien lo atendió, no desde que llegó. Con `creado_en`, un reporte de hace
-- dos semanas que se revisa hoy desaparecería en el acto, sin dejar margen para
-- volver a mirarlo.
--
-- A los que YA estaban revisados se les pone la fecha de creación como fecha de
-- revisión: es lo único que se sabe de ellos, y son los más viejos, así que
-- caducarán en la primera purga. Poner NOW() les regalaría 7 días más a cosas
-- resueltas hace meses.

ALTER TABLE reportes
    ADD COLUMN IF NOT EXISTS revisado_en TIMESTAMPTZ;

UPDATE reportes SET revisado_en = creado_en WHERE revisado AND revisado_en IS NULL;

-- La purga busca "revisados con fecha vencida". Es un índice PARCIAL: los
-- pendientes no se borran nunca, así que no tienen por qué ocupar el índice.
CREATE INDEX IF NOT EXISTS idx_reportes_caducidad
    ON reportes (revisado_en) WHERE revisado;
