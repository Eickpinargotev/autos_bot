-- Precio Standard de GPT-6.1 Terra (USD por millón), 05/11/2026.
-- https://developers.openai.com/api/docs/models/gpt-6.1-terra
-- La configuración sigue siendo optativa; no cambia el modelo predeterminado
-- ni sobrescribe un precio que el administrador ya haya configurado.
INSERT INTO precios_modelo (
    modelo, precio_input_usd_1m, precio_cached_input_usd_1m,
    precio_output_usd_1m, precio_audio_usd_minuto, nota
)
SELECT 'gpt-6.1-terra', 1.00, 0.05, 5.00, 0,
       'Auxiliar opcional: GPT-6.1 Terra. Precio Standard, razonamiento none por defecto.'
WHERE NOT EXISTS (
    SELECT 1 FROM precios_modelo WHERE modelo = 'gpt-6.1-terra'
);
