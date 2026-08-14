-- El precio deja de ser uno solo: ahora hay un modelo por tipo de tarea.
--
-- Hasta aquí el costo REAL salía de tres variables del entorno
-- (OPENAI_PRICE_INPUT/CACHED/OUTPUT) y `tarifas` guardaba UN modelo. Eso valía
-- mientras todo el bot usaba `gpt-5.4-mini`. Al repartir el trabajo en tres
-- niveles —supervisor caro, especialista medio, auxiliar barato— ese precio
-- único le cobraría al cliente lo mismo por una llamada de $2.00/millón que por
-- una de $0.20/millón. La factura quedaría mal en ambos sentidos.
--
-- Va en la base y no en el `.env` por la misma razón que las credenciales de
-- WhatsApp: es información del negocio, cambia cuando el proveedor cambia sus
-- precios, y se administra desde el panel sin redesplegar.
--
-- Separado de `tarifas` a propósito. Son dos cosas distintas:
--   * `tarifas`         → lo que el negocio COBRA (el margen de venta).
--   * `precios_modelo`  → lo que el proveedor nos CUESTA.
-- Mezclarlas obligaría a crear una tarifa nueva —y por tanto un corte de
-- facturación— cada vez que OpenAI mueve un precio, que son hechos sin relación.

CREATE TABLE IF NOT EXISTS precios_modelo (
    id                         SERIAL PRIMARY KEY,
    modelo                     VARCHAR(80)  NOT NULL,
    vigente_desde              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Por millón de tokens. NUMERIC y no float: son dinero.
    precio_input_usd_1m        NUMERIC(12,6) NOT NULL DEFAULT 0,
    precio_cached_input_usd_1m NUMERIC(12,6) NOT NULL DEFAULT 0,
    precio_output_usd_1m       NUMERIC(12,6) NOT NULL DEFAULT 0,
    -- Por MINUTO de audio. Los modelos de transcripción no cobran por token, y
    -- forzarlos a la misma unidad obligaría a inventar una equivalencia falsa.
    precio_audio_usd_minuto    NUMERIC(12,6) NOT NULL DEFAULT 0,
    creado_por                 VARCHAR(80)  NOT NULL DEFAULT 'sistema',
    nota                       TEXT         NOT NULL DEFAULT ''
);

-- Se resuelve "el precio vigente de ESTE modelo" en cada evento facturable.
CREATE INDEX IF NOT EXISTS idx_precios_modelo_vigencia
    ON precios_modelo (modelo, vigente_desde DESC);

-- Precios de partida (OpenAI, agosto 2026). Si el proveedor los mueve, se añade
-- una fila nueva desde el panel: NUNCA se edita la vieja, porque los eventos ya
-- facturados quedaron congelados con el precio de su momento.
INSERT INTO precios_modelo (modelo, precio_input_usd_1m, precio_cached_input_usd_1m,
                            precio_output_usd_1m, precio_audio_usd_minuto, nota)
SELECT * FROM (VALUES
    ('gpt-5.6-terra',    2.00::numeric, 0.20::numeric, 12.00::numeric, 0::numeric,      'Supervisor: enruta sobre el prompt grande.'),
    ('gpt-5.4-mini',     0.75,          0.075,          4.50,           0,              'Especialistas: temperature=0.'),
    ('gpt-5.4-nano',     0.20,          0.02,           1.25,           0,              'Auxiliar: recordatorio y RAG.'),
    ('gpt-4o-transcribe', 0,            0,              0,              0.006,          'Transcripción de notas de voz, por minuto.')
) AS v(modelo, i, c, o, a, n)
WHERE NOT EXISTS (SELECT 1 FROM precios_modelo);

-- El audio es una tercera categoría de gasto. No es `llm` (no hay tokens ni
-- margen sobre tokens) ni `codigo` (sí tiene costo real de proveedor). El
-- cliente quiere verlo separado: "cuánto pago por tokens" y "cuánto por audios".
ALTER TABLE uso_eventos DROP CONSTRAINT IF EXISTS uso_eventos_categoria_check;
ALTER TABLE uso_eventos ADD CONSTRAINT uso_eventos_categoria_check
    CHECK (categoria IN ('llm', 'codigo', 'audio'));

-- Duración transcrita, en segundos. Entero: es la unidad que devuelve el
-- proveedor y evita acumular flotantes en algo que se factura.
ALTER TABLE uso_eventos
    ADD COLUMN IF NOT EXISTS segundos_audio INTEGER NOT NULL DEFAULT 0;
