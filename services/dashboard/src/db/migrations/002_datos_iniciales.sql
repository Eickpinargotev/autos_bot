-- Datos mínimos para que el sistema arranque en un estado consistente.
--
-- Ambos INSERT son condicionales: re-ejecutar las migraciones no duplica nada
-- ni pisa lo que el administrador haya configurado después.

-- Tarifa inicial: los precios reales de gpt-5.4-mini que ya estaban en
-- config.py del bot, más el margen de venta por defecto. El administrador los
-- cambia desde el dashboard (cada cambio inserta una fila nueva, no edita esta).
INSERT INTO tarifas (
    modelo,
    precio_input_usd_1m,
    precio_cached_input_usd_1m,
    precio_output_usd_1m,
    multiplicador_llm,
    precio_mensaje_codigo_microusd,
    creado_por,
    nota
)
SELECT
    'gpt-5.4-mini',
    0.75,
    0.075,
    4.50,
    1.600,
    2000,          -- 0.002 USD por mensaje disparado solo por código
    'sistema',
    'Tarifa inicial creada por la migración.'
WHERE NOT EXISTS (SELECT 1 FROM tarifas);

-- Primer periodo de facturación abierto. Sin él no hay dónde imputar el
-- consumo, así que se crea siempre que no exista ninguno abierto.
INSERT INTO periodos_facturacion (nota)
SELECT 'Periodo inicial creado por la migración.'
WHERE NOT EXISTS (SELECT 1 FROM periodos_facturacion WHERE cerrado_en IS NULL);
