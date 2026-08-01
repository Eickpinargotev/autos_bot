-- Clientes (negocios) que conectan su WhatsApp, cada uno con su propio webhook.
--
-- Ojo con la palabra "cliente": en el resto del panel un cliente es la persona
-- que escribe al bot (una fila de `seguimiento_clientes`, identificada por su
-- número). Aquí "cliente" es el NEGOCIO al que le prestamos el servicio — hoy
-- la escuela de manejo. Se separan a propósito: cada negocio enlaza su propio
-- número de WhatsApp y recibe sus eventos en una URL distinta.
--
-- El token de la URL es la credencial: quien no lo tenga no puede empujar
-- eventos, y revocarlo es cambiar una fila (no redeplegar con otro .env). Por
-- eso va en la ruta y no en un query string: los proveedores de webhooks
-- guardan la URL entera, y así el secreto no queda repetido en dos sitios.

CREATE TABLE IF NOT EXISTS clientes_whatsapp (
    id                SERIAL PRIMARY KEY,
    nombre            VARCHAR(120) NOT NULL,
    -- Identificador legible en la URL. Sirve para reconocer de un vistazo a qué
    -- negocio pertenece un webhook sin tener que comparar tokens.
    slug              VARCHAR(60)  NOT NULL UNIQUE,
    webhook_token     VARCHAR(64)  NOT NULL UNIQUE,
    -- Clave de WasenderAPI de ESTE negocio (su propio número). Vacía = se usa
    -- la global del entorno (WASENDER_API_KEY), que es el caso de hoy.
    wasender_api_key  TEXT         NOT NULL DEFAULT '',
    -- Número de WhatsApp del negocio, solo informativo para el panel.
    numero            VARCHAR(40)  NOT NULL DEFAULT '',
    activo            BOOLEAN      NOT NULL DEFAULT TRUE,
    creado_en         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Diagnóstico: sin esto, "conecté el webhook y no pasa nada" no se puede
    -- distinguir de "el webhook llega pero el evento se ignora".
    ultimo_evento_en  TIMESTAMPTZ,
    ultimo_evento     VARCHAR(60)  NOT NULL DEFAULT '',
    eventos_recibidos BIGINT       NOT NULL DEFAULT 0
);

-- El webhook resuelve el negocio por token en cada evento: es el índice que
-- decide la latencia del camino caliente.
CREATE UNIQUE INDEX IF NOT EXISTS idx_cliente_wa_token
    ON clientes_whatsapp (webhook_token);

-- ===========================================================================
-- Visor de conversaciones: paginación por cursor
-- ===========================================================================
--
-- El visor lee el chat desde el final hacia atrás con `WHERE id < cursor
-- ORDER BY id DESC`. El índice por fecha no sirve para eso, y sin este
-- Postgres ordena a mano toda la conversación en cada tanda.

CREATE INDEX IF NOT EXISTS idx_conv_msg_cursor
    ON conversation_messages (client_id, canal, id DESC);

-- El primer (y por ahora único) negocio. El token se genera aquí para que la
-- URL exista desde el primer arranque y no haya que crearla a mano.
-- gen_random_uuid() es nativo desde Postgres 13: no hace falta pgcrypto.
INSERT INTO clientes_whatsapp (nombre, slug, webhook_token)
SELECT
    'Escuela de manejo',
    'escuela-de-manejo',
    replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', '')
WHERE NOT EXISTS (SELECT 1 FROM clientes_whatsapp);
