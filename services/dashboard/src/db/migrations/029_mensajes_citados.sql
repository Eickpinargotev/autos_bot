-- Conserva el texto al que una persona respondió en WhatsApp. El proveedor ya
-- entrega este dato; guardarlo permite que el visor muestre el contexto citado
-- sin intentar reconstruir relaciones entre mensajes antiguos.

ALTER TABLE conversation_messages
    ADD COLUMN IF NOT EXISTS quoted_text TEXT NOT NULL DEFAULT '';
