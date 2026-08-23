-- La migración 025 sembró temporalmente el prompt efectivo completo del
-- recordatorio. Separamos su contrato JSON fijo del playbook editable, igual
-- que ya se hace con supervisor y especialistas.

CREATE OR REPLACE FUNCTION activar_playbook_recordatorio(
    p_proyecto_id INTEGER,
    p_autor VARCHAR DEFAULT 'sistema'
) RETURNS VOID AS $$
DECLARE siguiente INTEGER;
BEGIN
    IF EXISTS (
        SELECT 1 FROM proyecto_instrucciones
        WHERE proyecto_id = p_proyecto_id AND tipo = 'recordatorio' AND activa
          AND contenido LIKE '%Los datos llegan como JSON%'
    ) THEN
        UPDATE proyecto_instrucciones SET activa = FALSE
        WHERE proyecto_id = p_proyecto_id AND tipo = 'recordatorio' AND activa;

        SELECT COALESCE(MAX(version), 0) + 1 INTO siguiente
        FROM proyecto_instrucciones
        WHERE proyecto_id = p_proyecto_id AND tipo = 'recordatorio';

        INSERT INTO proyecto_instrucciones
            (proyecto_id, tipo, version, contenido, activa, creado_por)
        VALUES (p_proyecto_id, 'recordatorio', siguiente, BTRIM($prompt$
═══ CUÁNDO NO ENVIAR (send=false) ═══
- No quedó nada realmente pendiente del cliente, o la conversación ya se cerró o se despidió.
- El cliente dijo que lo hará después o dio un plazo propio: respétalo, no lo presiones.
- El último mensaje del cliente muestra molestia o rechazo: un recordatorio empeora.
- Por el contexto, insistir se sentiría invasivo o repetitivo.

═══ CÓMO REDACTARLO (send=true) ═══
- UN solo mensaje corto (máximo 25 palabras) que retome exactamente lo que quedó pendiente, con el estilo de la casa: puede iniciar con "📌 Hola!!!" como los recordatorios existentes.
- Trato de usted SIEMPRE (nunca tutees): "¿pudo…?", "le agradezco…", como los mensajes de la casa.
- Retoma solo lo que ya está pendiente; no ofrezcas cosas nuevas ni cambies la pregunta por otra distinta.
- Personalízalo al punto exacto donde quedó la conversación (el comprobante, el formulario, la ciudad, la respuesta a una pregunta), sin regañar ni culpar.
- No repitas literalmente un recordatorio ya enviado en el historial: varía la forma.
- No inventes datos (precios, enlaces, fechas): solo retoma lo que ya se dijo.
- Máximo una pregunta.
$prompt$), TRUE, p_autor);
    END IF;
END;
$$ LANGUAGE plpgsql;

SELECT activar_playbook_recordatorio(id, 'migración 026')
FROM clientes_whatsapp;

CREATE OR REPLACE FUNCTION inicializar_prompts_del_proyecto()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM sembrar_playbooks_del_proyecto(NEW.id, 'sistema');
    PERFORM activar_playbook_recordatorio(NEW.id, 'sistema');
    INSERT INTO proyecto_recordatorios
        (proyecto_id, habilitado, intervalo_minutos, actualizado_por)
    VALUES (NEW.id, TRUE, 60, 'sistema')
    ON CONFLICT (proyecto_id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
