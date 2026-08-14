-- El usuario de un proyecto es una PERSONA, y el proyecto tiene que saber cuál.
--
-- En el panel se leían dos nombres sin ninguna relación entre ellos: la cuenta
-- «cliente_german» abajo del lateral y «Escuela de manejo» como marca, sin que
-- nada dijera qué era cada cosa. Eran dos problemas distintos:
--
--   1. La cuenta se llamaba como el proyecto («cliente_german»), no como quien
--      entra. Se elegía al crearla y no había forma de corregirlo desde el
--      panel; ahora se renombra desde el perfil del proyecto, pestaña «Usuario».
--   2. El proyecto tenía `usuario_id` en NULL: la relación que la migración 006
--      añadió nunca se rellenó para el proyecto que ya existía. Sin ella el
--      panel no puede decir en qué proyecto estás, ni se puede entrar a su
--      cuenta desde su perfil.
--
-- Las dos partes son idempotentes y no suponen nada. La segunda solo actúa
-- cuando la respuesta es ÚNICA —un proyecto sin usuario y una sola cuenta de
-- proyecto libre—: con dos de cualquiera de los dos, adivinar la pareja sería
-- meter a alguien en el panel de un proyecto que no es el suyo.

-- 1. El nombre de usuario es de la persona, no del proyecto.
--    No se aplica si «Enrique» ya está ocupado: el nombre es la credencial de
--    ingreso y dos iguales dejarían al login sin saber cuál es cuál.
UPDATE dashboard_usuarios
SET usuario = 'Enrique'
WHERE usuario = 'cliente_german'
  AND rol = 'cliente'
  AND NOT EXISTS (SELECT 1 FROM dashboard_usuarios o WHERE o.usuario = 'Enrique');

-- 2. El proyecto que quedó sin usuario se queda con la única cuenta libre.
UPDATE clientes_whatsapp c
SET usuario_id = (
    SELECT u.id
    FROM dashboard_usuarios u
    WHERE u.rol = 'cliente'
      AND NOT EXISTS (SELECT 1 FROM clientes_whatsapp o WHERE o.usuario_id = u.id)
)
WHERE c.usuario_id IS NULL
  AND (SELECT COUNT(*) FROM clientes_whatsapp WHERE usuario_id IS NULL) = 1
  AND (
      SELECT COUNT(*)
      FROM dashboard_usuarios u
      WHERE u.rol = 'cliente'
        AND NOT EXISTS (SELECT 1 FROM clientes_whatsapp o WHERE o.usuario_id = u.id)
  ) = 1;
