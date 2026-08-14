-- Las palabras clave dejan de estar escritas en el código.
--
-- Hasta aquí, «tareas» y «transporte» estaban puestas a mano en el bot
-- (`conversation_orchestrator`: `if keyword in {"tareas", "transporte"}`) y sus
-- recordatorios se agendaban leyendo los nodos T2/T3/T4 de `mensajes.json`, con
-- los segundos escritos en ese archivo. Añadir una palabra nueva —«examen»—
-- era un cambio de código y un redespliegue, y el dueño del negocio no podía
-- tocar ni los textos ni los tiempos.
--
-- Ahora una palabra clave es una FILA. El dueño la crea desde el panel, escribe
-- sus mensajes y programa sus recordatorios, y el bot la reconoce sin que nadie
-- toque nada.
--
-- Ojo con dónde vive esto: una palabra clave NO es un mensaje de
-- `plantillas_mensaje`. Se parecían (los dos son una cadena de textos con
-- adjunto), pero hacen cosas distintas: un mensaje se envía a mano a quien tú
-- elijas, y una palabra clave la dispara el cliente escribiéndola y arrastra
-- consigo el bloqueo de la conversación y unos recordatorios a futuro. Meterlas
-- en la misma tabla obligaba a que la mitad de las columnas estuvieran vacías en
-- cada fila.

CREATE TABLE IF NOT EXISTS palabras_clave (
    id              SERIAL PRIMARY KEY,
    -- Lo que el cliente escribe, EXACTO y él solo, para disparar el flujo.
    palabra         VARCHAR(60)  NOT NULL,
    activa          BOOLEAN      NOT NULL DEFAULT TRUE,
    creado_por      VARCHAR(80)  NOT NULL DEFAULT '',
    creado_en       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    actualizado_en  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- El índice va sobre `lower(palabra)`: el cliente escribe «Examen», «examen» o
-- «EXAMEN» y todas son la misma palabra. Sin esto se podrían crear tres filas
-- que compiten por el mismo mensaje entrante.
CREATE UNIQUE INDEX IF NOT EXISTS idx_palabra_clave_unica
    ON palabras_clave (lower(palabra));

-- Los textos de una palabra clave, de los dos tipos que tiene:
--
--   * `mensaje`      — sale al instante, en cuanto se escribe la palabra.
--   * `recordatorio` — sale `minutos` DESPUÉS, si el cliente no ha vuelto.
--
-- Van en la misma tabla porque son lo mismo (un texto con adjunto opcional) y
-- se comprueban con el mismo código; lo único que añade el recordatorio es
-- cuándo sale. Partirlas en dos tablas sería duplicar seis columnas de media
-- para no compartir una.
CREATE TABLE IF NOT EXISTS palabra_clave_piezas (
    id                 BIGSERIAL PRIMARY KEY,
    palabra_id         INTEGER     NOT NULL REFERENCES palabras_clave(id) ON DELETE CASCADE,
    tipo               VARCHAR(12) NOT NULL CHECK (tipo IN ('mensaje', 'recordatorio')),
    orden              INTEGER     NOT NULL,
    -- Minutos desde que se disparó la palabra clave, NO desde el recordatorio
    -- anterior: contar en cascada obliga a rehacer la cuenta mental cada vez que
    -- se cambia uno de en medio. Nulo en los mensajes, que salen al instante.
    minutos            INTEGER,
    -- Solo para recordatorios: apagarlo lo deja escrito pero sin enviarse.
    activo             BOOLEAN     NOT NULL DEFAULT TRUE,
    texto              TEXT        NOT NULL DEFAULT '',
    media_tipo         VARCHAR(10) NOT NULL DEFAULT '' CHECK (media_tipo IN ('', 'imagen', 'video')),
    media_ref          TEXT        NOT NULL DEFAULT '',
    media_ok           BOOLEAN,
    media_error        TEXT        NOT NULL DEFAULT '',
    media_revisada_en  TIMESTAMPTZ,
    -- Qué se le reporta al asesor si el cliente contesta a ESTE recordatorio.
    reporte            TEXT        NOT NULL DEFAULT '',
    UNIQUE (palabra_id, tipo, orden),
    -- Un recordatorio SIEMPRE tiene minutos y un mensaje NUNCA los tiene. Sin
    -- esto, un recordatorio sin hora se quedaría sin agendar en silencio.
    CONSTRAINT minutos_solo_en_recordatorios
        CHECK ((tipo = 'recordatorio') = (minutos IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_piezas_palabra ON palabra_clave_piezas (palabra_id, tipo, orden);

-- ===========================================================================
-- Las dos palabras que ya existían
-- ===========================================================================
--
-- Los textos son los mismos que estaban en `mensajes.json` y en la semilla 007.
-- Los tiempos vienen de los `segundos` de ese archivo (23, 33 y 43), que eran
-- valores de PRUEBA: contradicen los propios textos de los reportes, que hablan
-- de «1 día», «3 días» y «1 semana». Como la unidad ahora son minutos y cada
-- recordatorio tiene que salir después del anterior, se migran como 1, 2 y 3 —
-- lo más cercano que conserva el orden. Se cambian desde el panel.
--
-- «transporte» se lleva los mismos recordatorios que «tareas»: lo único que
-- cambia entre las dos es el primer mensaje.

INSERT INTO palabras_clave (palabra, creado_por) VALUES
    ('tareas', 'sistema'),
    ('transporte', 'sistema')
ON CONFLICT DO NOTHING;

INSERT INTO palabra_clave_piezas (palabra_id, tipo, orden, minutos, texto, reporte)
SELECT p.id, v.tipo, v.orden, v.minutos, v.texto, v.reporte
FROM (VALUES
    ('tareas', 'mensaje', 1, NULL::INTEGER, '💡💡💡Hola!!!

Para iniciar con el curso teórico solamente debes ingresar al siguiente enlace
👇🏻👇🏻👇🏻👇🏻👇🏻
De inmediato comenzarás a recibir el estudio del curso teórico para licencias

Usuario de estudio: (PONER CÉDULA)
Contraseña: (PONER TELÉFONO)

MOTOCICLETA: https://app.escuelasdemanejocr.com/course/view.php?id=24

AUTOMOVIL: https://app.escuelasdemanejocr.com/course/view.php?id=25

Bendiciones', ''),
    ('transporte', 'mensaje', 1, NULL, '💡💡💡Hola!!!

Para iniciar con el curso teórico de transporte público solamente debes ingresar al siguiente enlace
👇🏻👇🏻👇🏻👇🏻👇🏻
https://app.escuelasdemanejocr.com/course/view.php?id=22

De inmediato comenzarás a recibir el estudio del curso teórico para transporte público

Usuario de estudio: (PONER CÉDULA)
Contraseña: (PONER TELÉFONO)

Bendiciones', ''),
    ('tareas', 'recordatorio', 1, 1, '📌 Pudo ingresar a la página de estudio???',
     'Contestaron el recordatorio 1 de «tareas»'),
    ('tareas', 'recordatorio', 2, 2, '📌 Como va con su programa de estudio???',
     'Contestaron el recordatorio 2 de «tareas»'),
    ('tareas', 'recordatorio', 3, 3, 'Recuerde que puede solicitar su cita teórica en el siguiente enlace

En la pregunta que dice "INGRESO COSEVI" debe poner la clave de su usuario

Si llena el formulario con los datos incompletos o incorrectos será eliminado de la lista sin previo aviso.

Es su responsabilidad velar por la veracidad de los datos suministrados

https://forms.gle/AKn9QGgByCEbBDDx8', 'Contestaron el recordatorio 3 de «tareas»'),
    ('transporte', 'recordatorio', 1, 1, '📌 Pudo ingresar a la página de estudio???',
     'Contestaron el recordatorio 1 de «transporte»'),
    ('transporte', 'recordatorio', 2, 2, '📌 Como va con su programa de estudio???',
     'Contestaron el recordatorio 2 de «transporte»'),
    ('transporte', 'recordatorio', 3, 3, 'Recuerde que puede solicitar su cita teórica en el siguiente enlace

En la pregunta que dice "INGRESO COSEVI" debe poner la clave de su usuario

Si llena el formulario con los datos incompletos o incorrectos será eliminado de la lista sin previo aviso.

Es su responsabilidad velar por la veracidad de los datos suministrados

https://forms.gle/AKn9QGgByCEbBDDx8', 'Contestaron el recordatorio 3 de «transporte»')
) AS v(palabra, tipo, orden, minutos, texto, reporte)
JOIN palabras_clave p ON p.palabra = v.palabra
WHERE NOT EXISTS (
    SELECT 1 FROM palabra_clave_piezas x
    WHERE x.palabra_id = p.id AND x.tipo = v.tipo AND x.orden = v.orden
);

-- ===========================================================================
-- Limpieza de `plantillas_mensaje`
-- ===========================================================================
--
-- Las claves de las palabras clave se van de ahí: ya no las lee nadie y dejarlas
-- confundiría, porque aparecerían en «Mensajes» como algo que se puede enviar a
-- mano cuando en realidad las dispara el cliente.
--
-- BIENVENIDA_GRUPO se QUEDA: no es una palabra clave, es el texto que sale
-- cuando alguien entra al grupo del curso. Se re-inserta aquí para que esta
-- migración sea la única semilla de mensajes del negocio: la 007 ya no se puede
-- volver a aplicar porque escribía en la columna `nombre`, que se elimina abajo.

INSERT INTO plantillas_mensaje (clave) VALUES ('BIENVENIDA_GRUPO')
ON CONFLICT (clave) DO NOTHING;

INSERT INTO plantilla_partes (plantilla_id, orden, texto)
SELECT p.id, 1, '📲 Gracias por unirse a nuestro grupo del curso teórico!!!

Recuerde:

🎯 Por política de transparencia no cobramos nada antes del curso y pagas en efectivo hasta ese mismo día.

🎯 Traer documento de identidad

🎯 Traer material para tomar notas (Cuaderno y lapicero)

Por favor presentarse unos 10 minutos antes para hacer la matrícula e iniciar de la mejor manera la obtención de su licencia.

Bendiciones'
FROM plantillas_mensaje p
WHERE p.clave = 'BIENVENIDA_GRUPO'
  AND NOT EXISTS (SELECT 1 FROM plantilla_partes pp WHERE pp.plantilla_id = p.id AND pp.orden = 1);

DELETE FROM plantillas_mensaje
WHERE clave IN ('TAREAS', 'TRANSPORTE', 'TAREAS_R1', 'TAREAS_R2', 'TAREAS_R3');

-- Y ahora sí: el rótulo descriptivo que acompañaba a la clave y que no usaba
-- nadie (ver migración 015).
ALTER TABLE plantillas_mensaje DROP COLUMN IF EXISTS nombre;
