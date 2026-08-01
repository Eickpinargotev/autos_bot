-- Separación de las dos acepciones de "cliente" que estaban mezcladas.
--
-- Hasta aquí "cliente" significaba dos cosas distintas en el mismo panel:
--
--   1. La persona que le escribe al bot por WhatsApp (una fila de
--      `seguimiento_clientes`, identificada por su número).
--   2. El negocio al que le prestamos el servicio (la escuela de manejo).
--
-- Son cosas distintas y ahora se llaman distinto:
--
--   * **Negocio** (`clientes_whatsapp`): NUESTRO cliente. Tiene su cuenta para
--     entrar al panel, su webhook, sus credenciales de WasenderAPI y su zona
--     horaria. En el panel del administrador se lista como «Clientes», porque
--     desde ahí son eso: nuestros clientes.
--   * **Cliente**: la persona que le escribe al negocio. Solo el negocio los ve
--     como «Clientes» dentro de su propio panel.
--
-- La cuenta de acceso (`dashboard_usuarios`) y el negocio eran dos tablas sin
-- relación: no había forma de decir "esta cuenta es la de este negocio". Esa es
-- la relación que falta y la que permite entrar al perfil de un negocio desde
-- el panel del administrador.

ALTER TABLE clientes_whatsapp
    ADD COLUMN IF NOT EXISTS usuario_id INTEGER REFERENCES dashboard_usuarios(id) ON DELETE SET NULL;

-- Una cuenta pertenece como mucho a un negocio. El índice es PARCIAL porque
-- `usuario_id` puede quedar en NULL (negocio creado antes que su cuenta) y los
-- NULL no deben chocar entre sí.
CREATE UNIQUE INDEX IF NOT EXISTS idx_negocio_usuario
    ON clientes_whatsapp (usuario_id) WHERE usuario_id IS NOT NULL;

-- Zona horaria del negocio, no del servidor ni del panel: quien lee un chat
-- necesita la hora local de SU negocio. Antes era una variable global del
-- despliegue, que deja de servir en cuanto haya dos negocios en husos
-- distintos.
ALTER TABLE clientes_whatsapp
    ADD COLUMN IF NOT EXISTS zona_horaria VARCHAR(60) NOT NULL DEFAULT 'America/Costa_Rica';

-- Credenciales de envío por negocio. La global del entorno (WASENDER_API_KEY)
-- sigue siendo el respaldo mientras estas estén vacías.
ALTER TABLE clientes_whatsapp
    ADD COLUMN IF NOT EXISTS wasender_api_url VARCHAR(200) NOT NULL DEFAULT '';
ALTER TABLE clientes_whatsapp
    ADD COLUMN IF NOT EXISTS notas TEXT NOT NULL DEFAULT '';
