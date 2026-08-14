-- Un chunk no tiene tema: es un trozo de información, y punto.
--
-- La base de conocimiento nació con dos campos, `titulo` y `contenido`, como si
-- fuera un FAQ: una pregunta y su respuesta. No lo es. Lo que se guarda es un
-- trozo de texto que se vectoriza entero y que el RAG recupera por parecido
-- semántico; el «tema» no era un índice ni se buscaba por él, era un pedazo más
-- del mismo texto que se embebía pegado al resto (`rag_service.record_text`
-- unía los dos con un salto de línea).
--
-- Tenerlo separado solo conseguía dos cosas malas: obligaba a inventar un
-- titular para cada trozo, y con la carga inicial dejó 36 filas donde el tema
-- repetía la respuesta entera. El panel llegó a tener un aviso para eso; el
-- aviso sobra si el campo no existe.
--
-- El texto NO se pierde: se funde en el contenido antes de quitar la columna.

-- 1. El tema pasa al principio del contenido, pero solo si aporta algo. Cuando
--    el contenido ya lo dice (el caso de la carga inicial), pegarlo delante
--    duplicaría la frase dentro del mismo trozo y ensuciaría su vector.
--    La comparación ignora mayúsculas y espacios: el caso real es un tema
--    «Cancelar citas El plazo…» contra un contenido «Cancelar citas\n\nEl
--    plazo…», que es el mismo texto separado por dos saltos de línea.
UPDATE rag_chunks
SET contenido = btrim(titulo) || E'\n\n' || contenido
WHERE btrim(COALESCE(titulo, '')) <> ''
  AND btrim(COALESCE(contenido, '')) <> ''
  AND position(
        lower(regexp_replace(btrim(titulo), '\s+', ' ', 'g'))
        in lower(regexp_replace(btrim(contenido), '\s+', ' ', 'g'))
      ) = 0;

-- 2. Un chunk que solo tenía tema se queda con él como contenido: es su texto.
UPDATE rag_chunks
SET contenido = btrim(titulo)
WHERE btrim(COALESCE(contenido, '')) = ''
  AND btrim(COALESCE(titulo, '')) <> '';

-- 3. Ya no hace falta. El bot deja de leerla en el mismo cambio.
ALTER TABLE rag_chunks DROP COLUMN IF EXISTS titulo;

-- ===========================================================================
-- Una pregunta atendida caduca a las 24 horas
-- ===========================================================================
--
-- «Preguntas sin respuesta» es una bandeja de trabajo, no un archivo: son las
-- cosas que un cliente preguntó y el bot no supo contestar, para que el dueño
-- del negocio le cree el conocimiento que faltaba. Una vez entendida, la
-- pregunta ya cumplió: dejarla ahí para siempre convierte la lista en un muro
-- donde lo nuevo no se distingue.
--
-- Igual que los reportes (migración 013), el plazo cuenta desde que se atendió y
-- no desde que llegó; y lo que sigue pendiente NO caduca nunca.

ALTER TABLE preguntas_sin_respuesta
    ADD COLUMN IF NOT EXISTS atendida_en TIMESTAMPTZ;

UPDATE preguntas_sin_respuesta
SET atendida_en = creado_en
WHERE atendida AND atendida_en IS NULL;

CREATE INDEX IF NOT EXISTS idx_preguntas_caducidad
    ON preguntas_sin_respuesta (atendida_en) WHERE atendida;
