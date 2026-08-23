-- Una fila hija no puede declarar un proyecto distinto al de su cabecera.
-- Los ids siguen siendo globales, pero estas restricciones hacen que el
-- aislamiento también sea una propiedad de la base y no solo del código.

ALTER TABLE plantillas_mensaje
    ADD CONSTRAINT uq_plantilla_id_proyecto UNIQUE (id, proyecto_id);
ALTER TABLE palabras_clave
    ADD CONSTRAINT uq_palabra_id_proyecto UNIQUE (id, proyecto_id);
ALTER TABLE envios_lote
    ADD CONSTRAINT uq_lote_id_proyecto UNIQUE (id, proyecto_id);
ALTER TABLE envios
    ADD CONSTRAINT uq_envio_id_proyecto UNIQUE (id, proyecto_id);

ALTER TABLE plantilla_partes
    ADD CONSTRAINT fk_parte_plantilla_proyecto
    FOREIGN KEY (plantilla_id, proyecto_id)
    REFERENCES plantillas_mensaje (id, proyecto_id) ON DELETE CASCADE;

ALTER TABLE palabra_clave_piezas
    ADD CONSTRAINT fk_pieza_palabra_proyecto
    FOREIGN KEY (palabra_id, proyecto_id)
    REFERENCES palabras_clave (id, proyecto_id) ON DELETE CASCADE;

ALTER TABLE envios
    ADD CONSTRAINT fk_envio_lote_proyecto
    FOREIGN KEY (lote_id, proyecto_id)
    REFERENCES envios_lote (id, proyecto_id) ON DELETE CASCADE;

ALTER TABLE incidencias
    ADD CONSTRAINT fk_incidencia_envio_proyecto
    FOREIGN KEY (envio_id, proyecto_id)
    REFERENCES envios (id, proyecto_id) ON DELETE CASCADE;
