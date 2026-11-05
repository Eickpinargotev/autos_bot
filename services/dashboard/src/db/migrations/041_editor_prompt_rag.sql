-- Exponer las instrucciones actuales del RAG con el mismo versionado del panel.
-- El contenido conserva el comportamiento anterior; no aplica correcciones.
ALTER TABLE proyecto_instrucciones DROP CONSTRAINT IF EXISTS proyecto_instrucciones_tipo_check;
ALTER TABLE proyecto_instrucciones ADD CONSTRAINT proyecto_instrucciones_tipo_check CHECK (tipo IN (
    'principal', 'supervisor', 'general', 'curso_teorico', 'alquiler',
    'clases', 'dictamen', 'tramites', 'recordatorio', 'rag'
));

CREATE OR REPLACE FUNCTION prompt_rag_inicial() RETURNS TEXT
LANGUAGE SQL IMMUTABLE AS $funcion$
    SELECT $rag_prompt$Eres la recepcionista de una escuela de manejo y estás redactando la respuesta que se
enviará TAL CUAL al cliente por WhatsApp/Telegram.

Reglas:
- "answer" es el mensaje exacto que leerá el cliente: háblale directamente y en el mismo
  tono cercano del historial. No es un informe ni un análisis.
- Responde SOLO con información respaldada por la base de conocimiento de abajo. Si no
  respalda una respuesta completa a la pregunta, devuelve has_answer=false; nunca inventes.
- La mecánica interna es invisible para el cliente: jamás menciones de dónde sacas la
  información ni con qué la comparas (nada de "chunks", "fragmentos", "el sistema",
  "la información disponible", "según los datos"). Simplemente responde como quien se
  sabe la respuesta.
- No narres el proceso en tercera persona ("se le envía el formulario…"): dile al cliente
  directamente qué sigue o qué necesitas de él.
- Si hay una lista de requisitos o pasos, sepárala con saltos de línea (\n) para que sea
  fácil de leer.
- El mensaje actual tiene prioridad sobre la pregunta pendiente. Si ya la respondió, no
  repitas esa pregunta.
- Responde únicamente la duda actual. No agregues información relacionada que el cliente
  no solicitó.
- No muestres nombres, cuentas, números SINPE ni datos de pago salvo que el mensaje actual
  los solicite explícitamente.
- Mantén el trato de usted.$rag_prompt$;
$funcion$;

INSERT INTO proyecto_instrucciones
    (proyecto_id, tipo, version, contenido, activa, creado_por)
SELECT c.id, 'rag', 1, prompt_rag_inicial(), TRUE, 'migración 041'
FROM clientes_whatsapp c
WHERE NOT EXISTS (
    SELECT 1 FROM proyecto_instrucciones p WHERE p.proyecto_id = c.id AND p.tipo = 'rag'
);

CREATE OR REPLACE FUNCTION inicializar_prompts_del_proyecto()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM sembrar_playbooks_del_proyecto(NEW.id, 'sistema');
    PERFORM activar_playbook_recordatorio(NEW.id, 'sistema');
    INSERT INTO proyecto_recordatorios
        (proyecto_id, habilitado, intervalo_minutos, actualizado_por)
    VALUES (NEW.id, TRUE, 60, 'sistema')
    ON CONFLICT (proyecto_id) DO NOTHING;
    INSERT INTO proyecto_instrucciones
        (proyecto_id, tipo, version, contenido, activa, creado_por)
    VALUES (NEW.id, 'rag', 1, prompt_rag_inicial(), TRUE, 'sistema')
    ON CONFLICT (proyecto_id, tipo, version) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
