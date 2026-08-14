-- La «Publicidad por ciudad» era un segundo catálogo de lo mismo.
--
-- `invitaciones_ciudades` guardaba, por ciudad, hasta cinco columnas de texto:
-- la secuencia que el bot manda cuando alguien llega por un anuncio preguntando
-- por su zona. Eso es exactamente un mensaje del panel —una cadena de textos
-- identificada por su CLAVE— y de hecho eran los MISMOS textos con las MISMAS
-- claves, importados en su día a `plantillas_mensaje`. Dos sitios donde editar
-- lo mismo terminan siempre igual: uno de los dos queda viejo y nadie sabe cuál
-- está mandando.
--
-- Se queda el catálogo de mensajes, que además es mejor: ahí el adjunto es un
-- adjunto de verdad, que se comprueba al guardar, y no un `Imagen=...` escrito
-- dentro del texto.
--
-- Lo que el bot reconoce ahora es la clave del mensaje (`publicidad_service`).

-- 1. Red de seguridad: cualquier ciudad que NO estuviera ya como mensaje se
--    convierte en uno antes de borrar nada. En la base de este proyecto esto no
--    mueve una sola fila (las 72 ciudades ya existen como mensaje), pero una
--    base que nunca corrió la importación perdería sus textos sin esto.
--
--    El texto se copia TAL CUAL, con el `Imagen=` dentro si lo llevaba: el envío
--    entiende ese marcador igual que antes. Queda como estaba, no peor.

INSERT INTO plantillas_mensaje (clave, creado_por)
SELECT UPPER(TRIM(c.ciudad)), 'migración 018'
FROM invitaciones_ciudades c
WHERE TRIM(COALESCE(c.ciudad, '')) <> ''
GROUP BY UPPER(TRIM(c.ciudad))
ON CONFLICT (clave) DO NOTHING;

INSERT INTO plantilla_partes (plantilla_id, orden, texto)
SELECT p.id, v.orden, v.texto
FROM invitaciones_ciudades c
JOIN plantillas_mensaje p ON p.clave = UPPER(TRIM(c.ciudad))
CROSS JOIN LATERAL (
    VALUES (1, c.mensaje_1), (2, c.mensaje_2), (3, c.mensaje_3),
           (4, c.mensaje_4), (5, c.mensaje_5)
) AS v(orden, texto)
WHERE TRIM(COALESCE(v.texto, '')) <> ''
  -- Solo para los mensajes que esta migración acaba de crear: a uno que ya
  -- tenía partes no se le añade nada, porque las suyas son las buenas.
  AND NOT EXISTS (SELECT 1 FROM plantilla_partes x WHERE x.plantilla_id = p.id);

-- 2. Fuera la tabla y su copia vieja de los textos.
--
-- `ciudad_mayuscula` y `link_facebook` se van con ella sin sustituto: eran dos
-- columnas que no leía nadie, ni el panel ni el bot.

DROP TABLE IF EXISTS invitaciones_ciudades;

-- 3. Queda una categoría menos en «Enviar».
--
-- El histórico manda sobre la limpieza: si alguna sesión se hubiera mandado
-- desde una ciudad, no se borra ni se le cambia la etiqueta (que va congelada y
-- sigue diciendo qué salió); solo se reclasifica como lo que hoy es, un
-- mensaje, para que la pantalla sepa dibujarla.

UPDATE envios_lote SET categoria = 'mensaje' WHERE categoria = 'ciudad';

ALTER TABLE envios_lote DROP CONSTRAINT IF EXISTS envios_lote_categoria_check;
ALTER TABLE envios_lote ADD CONSTRAINT envios_lote_categoria_check
    CHECK (categoria IN ('mensaje', 'palabra_clave'));
