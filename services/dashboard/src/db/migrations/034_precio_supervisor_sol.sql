-- Precio Standard de GPT-6.1 Sol, OpenAI, 13/10/2026 (USD por millón).
-- https://developers.openai.com/api/docs/models/gpt-6.1-sol
-- Conserva los precios históricos y cualquier precio configurado para Sol.
INSERT INTO precios_modelo (
    modelo, precio_input_usd_1m, precio_cached_input_usd_1m,
    precio_output_usd_1m, precio_audio_usd_minuto, nota
)
SELECT 'gpt-6.1-sol', 2.00, 0.10, 10.00, 0,
       'Supervisor: GPT-6.1 Sol con razonamiento low. Precio Standard.'
WHERE NOT EXISTS (
    SELECT 1 FROM precios_modelo WHERE modelo = 'gpt-6.1-sol'
);
