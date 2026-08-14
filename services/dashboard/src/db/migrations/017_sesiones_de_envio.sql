-- Un envío masivo es una SESIÓN, no cien filas sueltas.
--
-- Hasta aquí, mandar un mensaje a cien números creaba cien filas en `envios` y
-- la pantalla las listaba una debajo de otra. No había forma de saber «¿cómo va
-- lo que mandé hace un rato?» sin contarlas a ojo, ni de decir «esta tanda
-- empieza a las 9», ni de marcarle un ritmo: salían todas de golpe, veinte por
-- pasada del worker, que es exactamente la firma de un bot.
--
-- La sesión es la unidad que le importa a quien envía: qué se mandó, a cuántos,
-- cuántos van, cuáles fallaron y por qué.

CREATE TABLE IF NOT EXISTS envios_lote (
    id             BIGSERIAL PRIMARY KEY,
    -- De dónde salió el contenido. Se guarda la categoría y no solo el id
    -- porque los tres orígenes viven en tablas distintas.
    categoria      VARCHAR(20) NOT NULL
                   CHECK (categoria IN ('mensaje', 'palabra_clave', 'ciudad')),
    referencia_id  INTEGER,
    -- El nombre con el que se eligió (la clave, la palabra o la ciudad),
    -- CONGELADO: si luego se renombra o se borra el origen, el histórico tiene
    -- que seguir diciendo qué se mandó.
    etiqueta       VARCHAR(120) NOT NULL DEFAULT '',
    canal          VARCHAR(20)  NOT NULL,
    creado_por     VARCHAR(80)  NOT NULL DEFAULT '',
    creado_en      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- A partir de cuándo empieza a mandar. Por defecto, ya.
    empieza_en     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Cuándo puede salir el SIGUIENTE mensaje de esta sesión. Es el ritmo, y
    -- vive en el lote y no en cada envío porque el ritmo es de la tanda: lo que
    -- se está espaciando es «este número, y dentro de un rato el siguiente».
    proximo_en     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Parar una tanda a medias: lo enviado queda, lo pendiente no sale.
    cancelado      BOOLEAN      NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_lotes_recientes ON envios_lote (creado_en DESC);

ALTER TABLE envios
    ADD COLUMN IF NOT EXISTS lote_id BIGINT REFERENCES envios_lote(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_envios_lote ON envios (lote_id);

-- El worker busca «pendientes de lotes a los que les toca». Sin este índice
-- parcial, cada pasada recorrería el histórico entero de envíos.
CREATE INDEX IF NOT EXISTS idx_envios_pendientes_lote
    ON envios (lote_id, creado_en) WHERE estado = 'pendiente';
