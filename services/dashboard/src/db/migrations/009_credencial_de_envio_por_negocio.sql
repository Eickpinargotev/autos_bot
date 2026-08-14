-- La credencial de SALIDA deja de vivir en el entorno y pasa a ser del negocio.
--
-- Hasta aquí los envíos se autenticaban con `WASENDER_API_KEY` del `.env`: una
-- sola clave global para todo el despliegue. Eso solo funciona con un cliente.
-- Con dos ya es imposible —cada negocio enlaza SU número en WasenderAPI y tiene
-- SU token— y obligaría a editar el `.env` y redesplegar por cada alta, que es
-- justo lo que el panel existe para evitar. La columna `wasender_api_key` de
-- `clientes_whatsapp` ya guardaba el token; lo que faltaba era saber, a la hora
-- de responder, CUÁL de los negocios es el que responde.
--
-- Falta un dato para eso: el mensaje entra por la URL de un negocio (y ahí se
-- sabe de quién es), pero el envío ocurre después, en el worker de Celery, que
-- solo recibe canal y número. Esta tabla es ese puente: al entrar el mensaje se
-- anota a qué negocio pertenece la conversación, y al responder se lee de aquí
-- con qué credencial hacerlo.
--
-- Es una tabla aparte y no una columna de `seguimiento_clientes` a propósito:
-- eso es el libro de facturación por cliente, y la pertenencia a un negocio es
-- enrutamiento. Mezclarlas ataría el envío a que la fila de facturación exista.

CREATE TABLE IF NOT EXISTS conversacion_negocio (
    canal          VARCHAR(20) NOT NULL,
    client_id      VARCHAR(80) NOT NULL,
    cliente_id     INTEGER     NOT NULL REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    creado_en      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (canal, client_id)
);

-- Se lee en CADA envío por el número de destino; se escribe en cada evento
-- entrante. La clave primaria ya sirve de índice para ambas.

-- Backfill: las conversaciones que ya existían nacieron antes de que se
-- anotara el negocio. Solo se puede resolver sin ambigüedad si hay UN negocio
-- activo; con dos o más no hay forma de saber a cuál pertenecía cada una, y
-- adivinar sería peor que dejarlo vacío (un mensaje saldría por el número
-- equivocado). Si quedan sin vincular, el envío avisa en vez de acertar por
-- casualidad.
INSERT INTO conversacion_negocio (canal, client_id, cliente_id)
SELECT 'whatsapp', s.client_id, (SELECT id FROM clientes_whatsapp WHERE activo)
FROM seguimiento_clientes s
WHERE s.canal = 'whatsapp'
  AND (SELECT COUNT(*) FROM clientes_whatsapp WHERE activo) = 1
ON CONFLICT (canal, client_id) DO NOTHING;
