-- Add issuer names to the existing resumable reference protocol without
-- changing the semantic bytes or stored shape of any version-1 revision.

ALTER TABLE public.market_security_reference_revisions
  ADD COLUMN IF NOT EXISTS semantic_encoding_version INT NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS issuer_names JSONB;

ALTER TABLE public.market_security_reference_revisions
  DROP CONSTRAINT IF EXISTS market_security_reference_revisions_semantic_version_check,
  ADD CONSTRAINT market_security_reference_revisions_semantic_version_check CHECK (
    (semantic_encoding_version=1 AND issuer_names IS NULL)
    OR (semantic_encoding_version=2 AND jsonb_typeof(issuer_names)='object'
        AND octet_length(issuer_names::text)<=32768)
  );

CREATE TABLE IF NOT EXISTS public.market_reference_transfer_responses (
  request_id UUID PRIMARY KEY REFERENCES public.market_reference_transfer_requests(request_id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  encoded_bytes INT NOT NULL CHECK (encoded_bytes BETWEEN 1 AND 196608),
  response_hash TEXT NOT NULL CHECK (response_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

DROP TRIGGER IF EXISTS market_reference_transfer_responses_append_only
  ON public.market_reference_transfer_responses;
CREATE TRIGGER market_reference_transfer_responses_append_only
BEFORE UPDATE OR DELETE ON public.market_reference_transfer_responses
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();

ALTER TABLE public.market_reference_transfer_responses ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_reference_transfer_responses
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader;
GRANT SELECT ON public.market_reference_transfer_responses TO stock_agent_release_reader;
DROP POLICY IF EXISTS release_evidence_select ON public.market_reference_transfer_responses;
CREATE POLICY release_evidence_select ON public.market_reference_transfer_responses
  FOR SELECT TO stock_agent_release_reader USING (true);

CREATE OR REPLACE FUNCTION public.market_reference_issuer_names_canonical_v2(p_names JSONB)
RETURNS JSONB LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE
  v_canonical TEXT;
  v_observed JSONB;
  v_former JSONB;
BEGIN
  IF jsonb_typeof(p_names)<>'object'
     OR NOT(p_names ?& ARRAY['canonical_name','observed_names','former_names'])
     OR (p_names-ARRAY['canonical_name','observed_names','former_names'])<>'{}'::jsonb
     OR jsonb_typeof(p_names->'canonical_name')<>'string'
     OR length(btrim(p_names->>'canonical_name')) NOT BETWEEN 1 AND 300
     OR jsonb_typeof(p_names->'observed_names')<>'array'
     OR jsonb_array_length(p_names->'observed_names') NOT BETWEEN 1 AND 16
     OR jsonb_typeof(p_names->'former_names')<>'array'
     OR jsonb_array_length(p_names->'former_names')>32 THEN
    RAISE EXCEPTION 'invalid issuer names' USING ERRCODE='22023';
  END IF;
  v_canonical:=p_names->>'canonical_name';
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_names->'observed_names') n
    WHERE jsonb_typeof(n)<>'string' OR length(btrim(n#>>'{}')) NOT BETWEEN 1 AND 300
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements_text(p_names->'observed_names') n
    GROUP BY n HAVING count(*)>1
  ) OR NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements_text(p_names->'observed_names') n
    WHERE n=v_canonical
  ) THEN
    RAISE EXCEPTION 'invalid issuer observed names' USING ERRCODE='22023';
  END IF;
  SELECT jsonb_agg(to_jsonb(n) ORDER BY convert_to(n,'UTF8')) INTO v_observed
  FROM jsonb_array_elements_text(p_names->'observed_names') n;
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_names->'former_names') f
    WHERE jsonb_typeof(f)<>'object'
      OR NOT(f ?& ARRAY['name','valid_from','valid_to'])
      OR (f-ARRAY['name','valid_from','valid_to'])<>'{}'::jsonb
      OR jsonb_typeof(f->'name')<>'string'
      OR length(btrim(f->>'name')) NOT BETWEEN 1 AND 300
      OR f->>'valid_from' !~ '^\d{4}-\d{2}-\d{2}$'
      OR f->>'valid_to' !~ '^\d{4}-\d{2}-\d{2}$'
      OR (f->>'valid_from')::date>(f->>'valid_to')::date
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_names->'former_names') f
    JOIN jsonb_array_elements_text(p_names->'observed_names') n ON n=f->>'name'
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_names->'former_names') f
    GROUP BY f->>'name',f->>'valid_from',f->>'valid_to' HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'invalid issuer former names' USING ERRCODE='22023';
  END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'name',f->>'name','valid_from',f->>'valid_from','valid_to',f->>'valid_to'
  ) ORDER BY convert_to(f->>'name','UTF8'),f->>'valid_from',f->>'valid_to'),'[]'::jsonb)
  INTO v_former FROM jsonb_array_elements(p_names->'former_names') f;
  RETURN jsonb_build_object(
    'canonical_name',v_canonical,'observed_names',v_observed,'former_names',v_former
  );
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
  RAISE EXCEPTION 'invalid issuer names' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.market_reference_security_semantic_v2(p_row JSONB)
RETURNS JSONB LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
  SELECT jsonb_build_object(
    'kind','security_revision','semantic_encoding_version',2,
    'value',jsonb_build_object(
      'revision',p_row->'revision','security_id',p_row->'security_id',
      'entity_id',p_row->'entity_id','ticker',p_row->'ticker','exchange',p_row->'exchange',
      'instrument_type',p_row->'instrument_type','eligible',p_row->'eligible',
      'exclusion_reasons',p_row->'exclusion_reasons','aliases',p_row->'aliases',
      'source_ids',p_row->'source_ids',
      'valid_from',to_char((p_row->>'valid_from')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'valid_to',CASE WHEN p_row->>'valid_to' IS NULL THEN NULL ELSE
        to_char((p_row->>'valid_to')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') END,
      'issuer_names',public.market_reference_issuer_names_canonical_v2(p_row->'issuer_names')
    )
  )
$$;

CREATE OR REPLACE FUNCTION public.claim_market_reference_transfer_request(
  p_run_id UUID,p_operation TEXT,p_payload JSONB,p_request_id UUID,
  p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_existing public.market_reference_transfer_requests%ROWTYPE;
  v_canonical TEXT;
  v_count BIGINT;
  v_bytes BIGINT;
  v_started TIMESTAMPTZ;
BEGIN
  IF p_run_id IS NULL OR p_request_id IS NULL
     OR p_operation NOT IN ('begin_discovery_reference','record_discovery_reference_chunk',
       'finalize_discovery_reference','pin_discovery_reference','read_discovery_reference')
     OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR p_encoded_bytes NOT BETWEEN 1 AND 262144
     OR p_request_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference transfer claim' USING ERRCODE='22023';
  END IF;
  v_canonical:=public.market_reference_canonical_json_v1(jsonb_build_object(
    'dry_run',false,'operation',p_operation,'payload',p_payload,
    'request_id',p_request_id::text,'run_id',p_run_id::text,'schema_version',1
  ));
  IF octet_length(convert_to(v_canonical,'UTF8'))<>p_encoded_bytes
     OR encode(extensions.digest(convert_to(v_canonical,'UTF8'),'sha256'),'hex')<>p_request_hash THEN
    RAISE EXCEPTION 'reference transfer claim hash mismatch' USING ERRCODE='22023';
  END IF;
  PERFORM 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
  WHERE i.id=p_run_id AND a.status='running' FOR UPDATE OF i,a;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_reference_transfer_requests
  WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_existing.run_id<>p_run_id OR v_existing.operation<>p_operation
       OR v_existing.encoded_bytes<>p_encoded_bytes OR v_existing.request_hash<>p_request_hash
       OR v_existing.request_payload<>p_payload THEN
      RAISE EXCEPTION 'reference transfer request idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN true;
  END IF;
  SELECT count(*),COALESCE(sum(encoded_bytes),0),min(created_at)
  INTO v_count,v_bytes,v_started FROM public.market_reference_transfer_requests
  WHERE run_id=p_run_id;
  IF v_count>=384 OR v_bytes+p_encoded_bytes>50331648
     OR (v_started IS NOT NULL AND clock_timestamp()-v_started>interval '90 seconds') THEN
    RAISE EXCEPTION 'reference transfer aggregate bound exceeded' USING ERRCODE='54000';
  END IF;
  INSERT INTO public.market_reference_transfer_requests(
    request_id,run_id,operation,encoded_bytes,request_hash,request_payload
  ) VALUES(p_request_id,p_run_id,p_operation,p_encoded_bytes,p_request_hash,p_payload);
  RETURN false;
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_reference_transfer_request(
  p_request_id UUID,p_result JSONB
)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_request public.market_reference_transfer_requests%ROWTYPE;
  v_existing public.market_reference_transfer_responses%ROWTYPE;
  v_bytes INT;
  v_hash TEXT;
  v_total BIGINT;
BEGIN
  SELECT * INTO v_request FROM public.market_reference_transfer_requests
  WHERE request_id=p_request_id;
  IF NOT FOUND OR jsonb_typeof(p_result)<>'object' THEN
    RAISE EXCEPTION 'invalid reference transfer response' USING ERRCODE='22023';
  END IF;
  -- Exact retries intentionally flip only the advisory duplicate flag. Bind and
  -- budget the durable response content so the same request remains idempotent.
  p_result:=p_result-'duplicate';
  v_bytes:=octet_length(p_result::text);
  IF v_bytes NOT BETWEEN 1 AND 196608 THEN
    RAISE EXCEPTION 'reference response exceeds bound' USING ERRCODE='54000';
  END IF;
  v_hash:=encode(extensions.digest(convert_to(
    public.market_reference_canonical_json_v1(p_result),'UTF8'
  ),'sha256'),'hex');
  SELECT * INTO v_existing FROM public.market_reference_transfer_responses
  WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_existing.run_id<>v_request.run_id OR v_existing.encoded_bytes<>v_bytes
       OR v_existing.response_hash<>v_hash THEN
      RAISE EXCEPTION 'reference response idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN;
  END IF;
  SELECT COALESCE(sum(encoded_bytes),0) INTO v_total
  FROM public.market_reference_transfer_responses WHERE run_id=v_request.run_id;
  IF v_total+v_bytes>67108864 THEN
    RAISE EXCEPTION 'reference response aggregate bound exceeded' USING ERRCODE='54000';
  END IF;
  INSERT INTO public.market_reference_transfer_responses(
    request_id,run_id,encoded_bytes,response_hash
  ) VALUES(p_request_id,v_request.run_id,v_bytes,v_hash);
END;
$$;

ALTER FUNCTION public.begin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  RENAME TO begin_market_discovery_reference_v1_internal;
ALTER FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT)
  RENAME TO record_market_discovery_reference_chunk_v1_internal;
ALTER FUNCTION public.finalize_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  RENAME TO finalize_market_discovery_reference_v1_internal;
ALTER FUNCTION public.pin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  RENAME TO pin_market_discovery_reference_v1_internal;
ALTER FUNCTION public.read_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  RENAME TO read_market_discovery_reference_v1_internal;

CREATE OR REPLACE FUNCTION public.begin_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_format INT;
BEGIN
  v_format:=COALESCE((p_payload->'manifest'->'manifest'->>'format_version')::int,1);
  IF v_format NOT IN (1,2) THEN
    RAISE EXCEPTION 'invalid reference begin format_version' USING ERRCODE='22023';
  END IF;
  v_result:=public.begin_market_discovery_reference_v1_internal(
    p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_discovery_reference_chunk(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_manifest_id UUID;
  v_begin public.market_reference_chunk_receipts%ROWTYPE;
  v_existing public.market_reference_chunk_receipts%ROWTYPE;
  v_entry JSONB;
  v_count INT;
  v_hash TEXT;
  v_format INT;
  v_result JSONB;
BEGIN
  v_manifest_id:=(p_payload->>'manifest_id')::uuid;
  SELECT * INTO v_begin FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1;
  v_format:=COALESCE((v_begin.payload->'manifest'->'manifest'->>'format_version')::int,1);
  IF v_format=1 THEN
    v_result:=public.record_market_discovery_reference_chunk_v1_internal(
      p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
    );
    PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
    RETURN v_result;
  END IF;
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'record_discovery_reference_chunk',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF v_format<>2 OR v_begin.run_id<>p_run_id
     OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest_id','chunk_index','chunk_count','entries','chunk_hash'])
     OR (p_payload-ARRAY['manifest_id','chunk_index','chunk_count','entries','chunk_hash'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'entries')<>'array'
     OR jsonb_array_length(p_payload->'entries') NOT BETWEEN 1 AND 88
     OR (p_payload->>'chunk_index')::int NOT BETWEEN 0 AND 511
     OR (p_payload->>'chunk_count')::int NOT BETWEEN 1 AND 512
     OR p_payload->>'chunk_hash' !~ '^[0-9a-f]{64}$'
     OR v_begin.chunk_count<>(p_payload->>'chunk_count')::int
     OR (p_payload->>'chunk_index')::int>=v_begin.chunk_count THEN
    RAISE EXCEPTION 'invalid reference chunk payload' USING ERRCODE='22023';
  END IF;
  FOR v_entry IN SELECT value FROM jsonb_array_elements(p_payload->'entries') LOOP
    IF jsonb_typeof(v_entry)<>'object'
       OR NOT(v_entry ?& ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash','semantic_encoding_version','issuer_names'])
       OR (v_entry-ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash','semantic_encoding_version','issuer_names'])<>'{}'::jsonb
       OR (v_entry->>'semantic_encoding_version')::int<>2
       OR v_entry->>'manifest_id'<>v_manifest_id::text
       OR v_entry->>'security_id' !~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'
       OR v_entry->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$'
       OR v_entry->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR public.market_reference_issuer_names_canonical_v2(v_entry->'issuer_names')<>v_entry->'issuer_names'
       OR encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
            public.market_reference_security_semantic_v2(v_entry)
          ),'UTF8'),'sha256'),'hex')<>v_entry->>'content_hash' THEN
      RAISE EXCEPTION 'reference security hash mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'entries') e
    GROUP BY e->>'security_id' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'entries') e
    GROUP BY (e->>'id')::uuid HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate reference security identity' USING ERRCODE='22023';
  END IF;
  SELECT count(*),encode(extensions.digest(convert_to(COALESCE(string_agg(
    (e.value->>'security_id')||chr(31)||(e.value->>'id')||chr(31)||(e.value->>'content_hash'),
    E'\n' ORDER BY e.ordinality),''),'UTF8'),'sha256'),'hex')
  INTO v_count,v_hash FROM jsonb_array_elements(p_payload->'entries') WITH ORDINALITY e(value,ordinality);
  IF v_hash<>p_payload->>'chunk_hash' THEN
    RAISE EXCEPTION 'reference chunk hash mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=(p_payload->>'chunk_index')::int;
  IF FOUND THEN
    IF v_existing.run_id<>p_run_id OR v_existing.chunk_count<>(p_payload->>'chunk_count')::int
       OR v_existing.entry_count<>v_count OR v_existing.chunk_hash<>v_hash
       OR v_existing.payload<>p_payload THEN
      RAISE EXCEPTION 'reference chunk idempotency mismatch' USING ERRCODE='22023';
    END IF;
    v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'chunk_index',v_existing.chunk_index,'duplicate',true);
  ELSE
    IF EXISTS(SELECT 1 FROM public.market_reference_finalization_seals WHERE manifest_id=v_manifest_id) THEN
      RAISE EXCEPTION 'reference snapshot is already finalized' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.market_reference_chunk_receipts(
      manifest_id,run_id,capability_id,chunk_index,chunk_count,entry_count,chunk_hash,
      predecessor_manifest_id,payload
    ) VALUES(
      v_manifest_id,p_run_id,v_begin.capability_id,(p_payload->>'chunk_index')::int,
      v_begin.chunk_count,v_count,v_hash,v_begin.predecessor_manifest_id,p_payload
    );
    v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'chunk_index',(p_payload->>'chunk_index')::int,'duplicate',false);
  END IF;
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid reference chunk payload' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.finalize_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_manifest_id UUID;
  v_begin public.market_reference_chunk_receipts%ROWTYPE;
  v_seal public.market_reference_finalization_seals%ROWTYPE;
  v_chunk_count INT; v_entry_count INT; v_min_chunk INT; v_max_chunk INT;
  v_root TEXT; v_entry JSONB; v_prior_revision UUID; v_revision_id UUID;
  v_ordinal INT:=0; v_manifest JSONB; v_result JSONB; v_format INT;
BEGIN
  v_manifest_id:=(p_payload->>'manifest_id')::uuid;
  SELECT * INTO v_begin FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1;
  v_format:=COALESCE((v_begin.payload->'manifest'->'manifest'->>'format_version')::int,1);
  IF v_format=1 THEN
    v_result:=public.finalize_market_discovery_reference_v1_internal(
      p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
    );
    PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
    RETURN v_result;
  END IF;
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'finalize_discovery_reference',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF v_format<>2 OR v_begin.run_id<>p_run_id OR jsonb_typeof(p_payload)<>'object'
     OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest_id','root_hash'])
     OR (p_payload-ARRAY['manifest_id','root_hash'])<>'{}'::jsonb
     OR p_payload->>'root_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference finalize payload' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_seal FROM public.market_reference_finalization_seals
  WHERE manifest_id=v_manifest_id;
  IF FOUND THEN
    IF v_seal.run_id<>p_run_id OR v_seal.root_hash<>p_payload->>'root_hash' THEN
      RAISE EXCEPTION 'reference finalization idempotency mismatch' USING ERRCODE='22023';
    END IF;
    v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_seal.security_count,'duplicate',true);
    PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
    RETURN v_result;
  END IF;
  SELECT count(*),COALESCE(sum(entry_count),0),min(chunk_index),max(chunk_index),
         encode(extensions.digest(convert_to(COALESCE(string_agg(chunk_hash,'' ORDER BY chunk_index),''),'UTF8'),'sha256'),'hex')
  INTO v_chunk_count,v_entry_count,v_min_chunk,v_max_chunk,v_root
  FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index>=0;
  IF v_chunk_count<>v_begin.chunk_count OR v_min_chunk<>0 OR v_max_chunk<>v_begin.chunk_count-1 THEN
    RAISE EXCEPTION 'reference chunk sequence mismatch' USING ERRCODE='22023';
  END IF;
  IF v_entry_count<>(v_begin.payload->>'security_count')::int THEN
    RAISE EXCEPTION 'reference snapshot count mismatch' USING ERRCODE='22023';
  END IF;
  IF v_root<>v_begin.chunk_hash OR v_root<>p_payload->>'root_hash' THEN
    RAISE EXCEPTION 'reference snapshot root mismatch' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    GROUP BY e->>'security_id' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    GROUP BY (e->>'id')::uuid HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate reference security identity' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    GROUP BY e->>'entity_id' HAVING count(DISTINCT e->'issuer_names')>1
  ) THEN
    RAISE EXCEPTION 'same entity must have identical issuer names' USING ERRCODE='22023';
  END IF;
  IF v_begin.predecessor_manifest_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.market_reference_finalization_seals s
    WHERE s.manifest_id=v_begin.predecessor_manifest_id AND s.capability_id=v_begin.capability_id
  ) THEN
    RAISE EXCEPTION 'reference predecessor membership mismatch' USING ERRCODE='22023';
  END IF;
  v_manifest:=v_begin.payload->'manifest';
  IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
       public.market_reference_manifest_semantic_v1(v_manifest)
     ),'UTF8'),'sha256'),'hex')<>v_manifest->>'content_hash' THEN
    RAISE EXCEPTION 'reference manifest hash mismatch' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_reference_manifests(
    id,run_id,reference_version,revision,capability_version,taxonomy_version,source_hash,
    valid_from,valid_to,manifest,content_hash
  ) VALUES(
    v_manifest_id,p_run_id,v_manifest->>'reference_version',(v_manifest->>'revision')::int,
    (v_manifest->>'capability_version')::int,(v_manifest->>'taxonomy_version')::int,
    v_manifest->>'source_hash',(v_manifest->>'valid_from')::timestamptz,
    (v_manifest->>'valid_to')::timestamptz,v_manifest->'manifest',v_manifest->>'content_hash'
  );
  FOR v_entry IN
    SELECT e.value FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') WITH ORDINALITY e(value,item_ordinal)
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    ORDER BY c.chunk_index,e.item_ordinal
  LOOP
    IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
         public.market_reference_security_semantic_v2(v_entry)
       ),'UTF8'),'sha256'),'hex')<>v_entry->>'content_hash' THEN
      RAISE EXCEPTION 'reference security hash mismatch' USING ERRCODE='22023';
    END IF;
    v_prior_revision:=NULL;
    IF v_begin.predecessor_manifest_id IS NOT NULL THEN
      SELECT m.security_revision_id INTO v_prior_revision
      FROM public.market_reference_snapshot_memberships m
      JOIN public.market_security_reference_revisions s ON s.id=m.security_revision_id
      WHERE m.manifest_id=v_begin.predecessor_manifest_id
        AND m.security_id=v_entry->>'security_id'
        AND s.content_hash=v_entry->>'content_hash'
        AND s.semantic_encoding_version=2
        AND s.issuer_names=v_entry->'issuer_names';
    END IF;
    IF v_prior_revision IS NULL THEN
      v_revision_id:=(v_entry->>'id')::uuid;
      INSERT INTO public.market_security_reference_revisions(
        id,manifest_id,run_id,revision,security_id,entity_id,ticker,exchange,instrument_type,
        eligible,exclusion_reasons,aliases,source_ids,valid_from,valid_to,content_hash,
        semantic_encoding_version,issuer_names
      ) VALUES(
        v_revision_id,v_manifest_id,p_run_id,(v_entry->>'revision')::int,
        v_entry->>'security_id',v_entry->>'entity_id',v_entry->>'ticker',v_entry->>'exchange',
        v_entry->>'instrument_type',(v_entry->>'eligible')::boolean,v_entry->'exclusion_reasons',
        v_entry->'aliases',v_entry->'source_ids',(v_entry->>'valid_from')::timestamptz,
        (v_entry->>'valid_to')::timestamptz,v_entry->>'content_hash',2,v_entry->'issuer_names'
      );
    ELSE
      v_revision_id:=v_prior_revision;
    END IF;
    INSERT INTO public.market_reference_snapshot_memberships(
      manifest_id,security_revision_id,security_id,ordinal
    ) VALUES(v_manifest_id,v_revision_id,v_entry->>'security_id',v_ordinal);
    v_ordinal:=v_ordinal+1;
  END LOOP;
  INSERT INTO public.market_reference_finalization_seals(
    manifest_id,run_id,capability_id,predecessor_manifest_id,chunk_count,security_count,root_hash
  ) VALUES(
    v_manifest_id,p_run_id,v_begin.capability_id,v_begin.predecessor_manifest_id,
    v_begin.chunk_count,v_entry_count,v_root
  );
  v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_entry_count,'duplicate',false);
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range OR unique_violation OR foreign_key_violation THEN
  RAISE EXCEPTION 'reference finalization conflict' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.pin_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB;
BEGIN
  v_result:=public.pin_market_discovery_reference_v1_internal(
    p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.read_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_result JSONB; v_manifest UUID; v_format INT; v_rows JSONB; v_count INT;
BEGIN
  v_result:=public.read_market_discovery_reference_v1_internal(
    p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  v_manifest:=(v_result->'binding'->>'manifest_id')::uuid;
  IF v_manifest IS NULL THEN
    v_format:=1;
  ELSE
    SELECT COALESCE((manifest->>'format_version')::int,1) INTO v_format
    FROM public.market_reference_manifests WHERE id=v_manifest;
  END IF;
  IF v_format=2 THEN
    SELECT COALESCE(jsonb_agg(
      row.value||jsonb_build_object(
        'semantic_encoding_version',s.semantic_encoding_version,
        'issuer_names',s.issuer_names
      ) ORDER BY row.ordinality
    ),'[]'::jsonb) INTO v_rows
    FROM jsonb_array_elements(v_result->'securities') WITH ORDINALITY row(value,ordinality)
    JOIN public.market_security_reference_revisions s ON s.id=(row.value->>'id')::uuid;
    v_result:=jsonb_set(v_result,'{securities}',v_rows);
  END IF;
  v_result:=jsonb_set(
    v_result,'{binding,issuer_names_status}',
    to_jsonb(CASE WHEN v_format=2 THEN 'available' ELSE 'issuer_names_unavailable' END)
  );
  -- The v1 reader already packs pages to the limit. Make room for the explicit
  -- name availability state without changing its cursor or ordering contract.
  WHILE octet_length(v_result::text)>196608 LOOP
    v_count:=jsonb_array_length(v_result->'securities');
    IF v_count=0 THEN
      RAISE EXCEPTION 'one reference row exceeds page bound' USING ERRCODE='54000';
    END IF;
    v_result:=jsonb_set(v_result,'{securities}',
      (v_result->'securities')-(v_count-1));
    v_result:=jsonb_set(v_result,'{complete}','false'::jsonb);
    v_result:=jsonb_set(v_result,'{next_after_security_id}',to_jsonb(
      v_result->'securities'->(v_count-2)->>'security_id'
    ));
  END LOOP;
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.market_reference_issuer_names_canonical_v2(JSONB)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.market_reference_security_semantic_v2(JSONB)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.finish_market_reference_transfer_request(UUID,JSONB)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.begin_market_discovery_reference_v1_internal(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.record_market_discovery_reference_chunk_v1_internal(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.finalize_market_discovery_reference_v1_internal(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.pin_market_discovery_reference_v1_internal(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_reference_v1_internal(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.claim_market_reference_transfer_request(UUID,TEXT,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.begin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.finalize_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.pin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.begin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finalize_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.pin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
