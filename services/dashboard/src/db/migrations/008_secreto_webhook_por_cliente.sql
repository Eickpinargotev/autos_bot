-- Las dos credenciales que WasenderAPI entrega por sesión, y solo esas.
--
-- El formulario del cliente pedía además una «URL de la API». Era un error de
-- diseño: ese dominio (`https://wasenderapi.com`) es del PROVEEDOR y es el mismo
-- para todos; WasenderAPI no entrega ninguna URL por sesión. Vive donde le
-- corresponde, en `WASENDER_API_URL` del entorno, y se quita de la ficha del
-- cliente para no pedir un dato que nadie tiene de dónde sacar.
--
-- Lo que sí es por cliente son las dos credenciales de su pantalla:
--
--   * **API Access Token** → `wasender_api_key`. Autentica los envíos.
--   * **Webhook Secret**   → `wasender_webhook_secret`. WasenderAPI lo manda en
--     la cabecera `X-Webhook-Signature` de cada evento.
--
-- El secreto es opcional y suma una segunda comprobación sobre el token de la
-- URL: el token demuestra a QUÉ negocio va dirigido el evento, y el secreto
-- demuestra que lo envió WasenderAPI de verdad y no alguien que consiguió la
-- URL. Si está vacío, manda solo el token (que es como funcionaba hasta ahora).

ALTER TABLE clientes_whatsapp
    ADD COLUMN IF NOT EXISTS wasender_webhook_secret TEXT NOT NULL DEFAULT '';

ALTER TABLE clientes_whatsapp DROP COLUMN IF EXISTS wasender_api_url;
