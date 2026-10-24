-- Evidencia operativa sin contenido del chat. Independiente de su retención.
CREATE TABLE conversation_audit (
    id BIGSERIAL PRIMARY KEY,
    proyecto_id INTEGER NOT NULL REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    canal TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    actor JSONB NOT NULL DEFAULT '{}',
    correlation_id TEXT,
    parent_event_id BIGINT,
    dedup_key TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    UNIQUE (proyecto_id, canal, client_id, dedup_key)
);
CREATE INDEX conversation_audit_subject ON conversation_audit(proyecto_id, canal, client_id, occurred_at, id);
CREATE INDEX conversation_audit_retention ON conversation_audit(occurred_at);
CREATE TABLE conversation_audit_coverage (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO conversation_audit_coverage(singleton) VALUES (TRUE);

-- Las razones de reportes pueden contener texto del cliente: nunca se copian.
CREATE FUNCTION conversation_audit_reason(reason TEXT) RETURNS TEXT
LANGUAGE SQL IMMUTABLE AS $$
 SELECT CASE
 WHEN reason = 'Intervención de un asesor humano' THEN 'human_intervention'
 WHEN reason = 'Flujo de publicidad' THEN 'advertising'
 WHEN reason LIKE 'Flujo keyword %' THEN 'keyword'
 WHEN reason = 'Ingreso a grupo' THEN 'group_join'
 WHEN reason = 'Bloqueado por comando' THEN 'command'
 WHEN reason LIKE 'Falta el enlace del grupo%' THEN 'missing_group_link'
 WHEN reason LIKE 'Falta informacion para programar publicidad%' THEN 'missing_ad_fields'
 ELSE 'handoff_or_legacy' END
$$;

CREATE FUNCTION conversation_audit_blocks(p INTEGER, ch TEXT, client TEXT) RETURNS JSONB
LANGUAGE SQL STABLE AS $$
 SELECT COALESCE(jsonb_agg(state || jsonb_build_object(
   'audit_event_id', (SELECT a.id FROM conversation_audit a
       WHERE a.proyecto_id=p AND a.canal=ch AND a.client_id=client
       AND a.event_type IN ('block_created','block_updated','block_observed')
       AND a.metadata->>'block_kind'=kind ORDER BY a.id DESC LIMIT 1)) ||
   COALESCE((SELECT jsonb_build_object('source',a.source,'actor',a.actor,
     'reason_code',COALESCE(a.metadata#>>'{after,reason_code}',state->>'reason_code'),
     'block_event_at',CASE WHEN a.event_type='block_observed' THEN NULL ELSE a.occurred_at END)
     FROM conversation_audit a WHERE a.proyecto_id=p AND a.canal=ch AND a.client_id=client
       AND a.event_type IN ('block_created','block_updated','block_observed')
       AND a.metadata->>'block_kind'=kind ORDER BY a.id DESC LIMIT 1),'{}'::jsonb)), '[]'::jsonb)
 FROM (
   SELECT 'temporary' AS kind, jsonb_build_object('block_kind','temporary',
     'reason_code',conversation_audit_reason(b.reason),
     'stored_initial_at',b.blocked_at AT TIME ZONE 'UTC',
     'expires_at',b.expires_at AT TIME ZONE 'UTC',
     'active',b.expires_at IS NULL OR b.expires_at > (clock_timestamp() AT TIME ZONE 'UTC')) AS state
   FROM users_blocked b WHERE b.proyecto_id=p AND (b.user_id=ch||':'||client OR (ch='telegram' AND b.user_id=client))
   UNION ALL
   SELECT 'permanent', jsonb_build_object('block_kind','permanent','reason_code','manual_permanent',
     'stored_initial_at',b.creado_en,'expires_at',NULL,'active',TRUE)
   FROM bloqueos_permanentes b WHERE b.proyecto_id=p AND b.canal=ch AND b.numero=client
 ) states
$$;

-- Una caída de auditoría no altera una respuesta ni una mutación del bloqueo.
CREATE FUNCTION conversation_audit_append(p INTEGER, ch TEXT, client TEXT, kind TEXT,
    origin TEXT, who JSONB DEFAULT '{}', correlation TEXT DEFAULT NULL,
    parent BIGINT DEFAULT NULL, unique_key TEXT DEFAULT NULL, details JSONB DEFAULT '{}',
    happened TIMESTAMPTZ DEFAULT NULL) RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE result BIGINT;
BEGIN
 IF kind='decision_blocked' THEN
   details := details || jsonb_build_object('blocks',conversation_audit_blocks(p,ch,client));
 END IF;
 INSERT INTO conversation_audit(proyecto_id,canal,client_id,event_type,source,actor,
    correlation_id,parent_event_id,dedup_key,metadata,occurred_at)
 VALUES(p,ch,client,kind,origin,who,correlation,parent,unique_key,details,COALESCE(happened,clock_timestamp()))
 ON CONFLICT (proyecto_id,canal,client_id,dedup_key) DO NOTHING RETURNING id INTO result;
 RETURN result;
EXCEPTION WHEN OTHERS THEN
 RAISE WARNING 'conversation_audit_append failed: %', SQLSTATE;
 RETURN NULL;
END $$;

CREATE FUNCTION conversation_audit_block_change() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE previous JSONB; following JSONB; subject JSONB; ctx JSONB;
  p INTEGER; ch TEXT; client TEXT; mode TEXT; kind TEXT; cause TEXT; expired BOOLEAN := FALSE;
BEGIN
 ctx := COALESCE(NULLIF(current_setting('app.conversation_audit',TRUE),''),'{}')::jsonb;
 IF TG_OP <> 'INSERT' THEN previous := to_jsonb(OLD); END IF;
 IF TG_OP <> 'DELETE' THEN following := to_jsonb(NEW); END IF;
 subject := COALESCE(following,previous);
 p := (subject->>'proyecto_id')::integer;
 IF TG_TABLE_NAME='users_blocked' THEN
   mode := 'temporary';
   ch := CASE WHEN position(':' IN subject->>'user_id') > 0 THEN split_part(subject->>'user_id',':',1) ELSE 'telegram' END;
   client := CASE WHEN position(':' IN subject->>'user_id') > 0 THEN split_part(subject->>'user_id',':',2) ELSE subject->>'user_id' END;
   cause := CASE WHEN ctx->>'reason_code' IN ('media_review','agent_handoff','scheduled_handoff')
     THEN ctx->>'reason_code' ELSE conversation_audit_reason(subject->>'reason') END;
   IF previous IS NOT NULL THEN
     expired := (previous->>'expires_at')::timestamp <= clock_timestamp() AT TIME ZONE 'UTC';
     previous := jsonb_build_object('reason_code',conversation_audit_reason(previous->>'reason'),
       'expires_at',(previous->>'expires_at')::timestamp AT TIME ZONE 'UTC',
       'stored_initial_at',(previous->>'blocked_at')::timestamp AT TIME ZONE 'UTC');
   END IF;
   IF following IS NOT NULL THEN
     following := jsonb_build_object('reason_code',cause,
       'expires_at',(following->>'expires_at')::timestamp AT TIME ZONE 'UTC',
       'stored_initial_at',(following->>'blocked_at')::timestamp AT TIME ZONE 'UTC');
   END IF;
 ELSE
   mode := 'permanent'; ch := subject->>'canal'; client := subject->>'numero'; cause := 'manual_permanent';
   IF following IS NOT NULL THEN following := jsonb_build_object('reason_code',cause,'stored_initial_at',subject->'creado_en'); END IF;
   IF previous IS NOT NULL THEN previous := jsonb_build_object('reason_code',cause,'stored_initial_at',subject->'creado_en'); END IF;
   IF ctx->'actor' IS NULL AND TG_OP='INSERT' THEN
     ctx := ctx || jsonb_build_object('actor',jsonb_build_object('kind','dashboard_user','username',subject->>'creado_por'));
   END IF;
 END IF;
 kind := CASE WHEN TG_OP='INSERT' THEN 'block_created' WHEN TG_OP='UPDATE' THEN 'block_updated'
   WHEN expired THEN 'block_expired' ELSE 'block_removed' END;
 PERFORM conversation_audit_append(p,ch,client,kind,COALESCE(ctx->>'source',cause),
   COALESCE(ctx->'actor', CASE WHEN cause='human_intervention' THEN '{"kind":"business_account"}'::jsonb ELSE '{"kind":"system"}'::jsonb END),
   ctx->>'correlation_id',(ctx->>'event_id')::bigint,NULL,jsonb_build_object('block_kind',mode,'before',previous,'after',following,
     'cause_event_id',ctx->'event_id',
     'previous_block_event_id',(SELECT id FROM conversation_audit WHERE proyecto_id=p AND canal=ch AND client_id=client
       AND event_type IN ('block_created','block_updated','block_observed') AND metadata->>'block_kind'=mode
       ORDER BY id DESC LIMIT 1)));
 RETURN NULL;
EXCEPTION WHEN OTHERS THEN
 RAISE WARNING 'conversation_audit_block_change failed: %', SQLSTATE;
 RETURN NULL;
END $$;
CREATE TRIGGER audit_temporary_block AFTER INSERT OR UPDATE OR DELETE ON users_blocked
 FOR EACH ROW EXECUTE FUNCTION conversation_audit_block_change();
CREATE TRIGGER audit_permanent_block AFTER INSERT OR UPDATE OR DELETE ON bloqueos_permanentes
 FOR EACH ROW EXECUTE FUNCTION conversation_audit_block_change();

-- Capturar también borrados masivos, una fila por conversación, sin su texto.
CREATE FUNCTION conversation_audit_chat_deleted() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE item RECORD; ctx JSONB;
BEGIN
 ctx := COALESCE(NULLIF(current_setting('app.conversation_audit',TRUE),''),'{}')::jsonb;
 FOR item IN SELECT proyecto_id,canal,client_id,count(*) AS rows FROM deleted_messages GROUP BY proyecto_id,canal,client_id LOOP
   PERFORM conversation_audit_append(item.proyecto_id,item.canal,item.client_id,'history_deleted',
     COALESCE(ctx->>'source','database'),COALESCE(ctx->'actor','{"kind":"system"}'::jsonb),
     ctx->>'correlation_id',NULL,NULL,jsonb_build_object('rows_deleted',item.rows,'blocks_preserved',TRUE));
 END LOOP;
 RETURN NULL;
EXCEPTION WHEN OTHERS THEN
 RAISE WARNING 'conversation_audit_chat_deleted failed: %', SQLSTATE;
 RETURN NULL;
END $$;
CREATE TRIGGER audit_chat_deleted AFTER DELETE ON conversation_messages
 REFERENCING OLD TABLE AS deleted_messages FOR EACH STATEMENT EXECUTE FUNCTION conversation_audit_chat_deleted();

-- Observaciones de estado, no reconstrucciones de hechos anteriores al despliegue.
INSERT INTO conversation_audit(proyecto_id,canal,client_id,event_type,source,metadata)
 SELECT b.proyecto_id,CASE WHEN position(':' IN b.user_id)>0 THEN split_part(b.user_id,':',1) ELSE 'telegram' END,
 CASE WHEN position(':' IN b.user_id)>0 THEN split_part(b.user_id,':',2) ELSE b.user_id END,
 'block_observed','migration',jsonb_build_object('block_kind','temporary','historical_origin_unknown',TRUE,
 'after',jsonb_build_object('reason_code',conversation_audit_reason(b.reason),
 'expires_at',b.expires_at AT TIME ZONE 'UTC','stored_initial_at',b.blocked_at AT TIME ZONE 'UTC')) FROM users_blocked b;
INSERT INTO conversation_audit(proyecto_id,canal,client_id,event_type,source,actor,metadata)
 SELECT proyecto_id,canal,numero,'block_observed','migration',jsonb_build_object('kind','dashboard_user','username',creado_por),jsonb_build_object('block_kind','permanent',
 'historical_origin_unknown',TRUE,'after',jsonb_build_object('reason_code','manual_permanent','stored_initial_at',creado_en))
 FROM bloqueos_permanentes;
