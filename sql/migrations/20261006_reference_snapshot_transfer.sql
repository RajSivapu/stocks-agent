-- Bounded, resumable transfer of complete reference snapshots. Partial uploads
-- stay outside the finalized reference ledgers and are never readable as a
-- research reference. All records are provenance only and grant no action.

CREATE OR REPLACE FUNCTION public.market_reference_canonical_json_v1(p_value JSONB)
RETURNS TEXT LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE
  v_kind TEXT:=jsonb_typeof(p_value);
  v_result TEXT;
BEGIN
  IF v_kind='object' THEN
    SELECT '{'||COALESCE(string_agg(
      to_jsonb(e.key)::text||':'||public.market_reference_canonical_json_v1(e.value),
      ',' ORDER BY convert_to(e.key,'UTF8')
    ),'')||'}' INTO v_result FROM jsonb_each(p_value) e;
    RETURN v_result;
  ELSIF v_kind='array' THEN
    SELECT '['||COALESCE(string_agg(
      public.market_reference_canonical_json_v1(e.value),',' ORDER BY e.ordinality
    ),'')||']' INTO v_result
    FROM jsonb_array_elements(p_value) WITH ORDINALITY e(value,ordinality);
    RETURN v_result;
  END IF;
  RETURN p_value::text;
END;
$$;

CREATE OR REPLACE FUNCTION public.market_reference_manifest_semantic_v1(p_row JSONB)
RETURNS JSONB LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
  SELECT jsonb_build_object(
    'kind','reference_manifest','semantic_encoding_version',1,
    'value',jsonb_build_object(
      'id',p_row->'id','reference_version',p_row->'reference_version',
      'revision',p_row->'revision','capability_version',p_row->'capability_version',
      'taxonomy_version',p_row->'taxonomy_version','source_hash',p_row->'source_hash',
      'valid_from',to_char((p_row->>'valid_from')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'valid_to',CASE WHEN p_row->>'valid_to' IS NULL THEN NULL ELSE
        to_char((p_row->>'valid_to')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') END,
      'manifest',p_row->'manifest'
    )
  )
$$;

CREATE OR REPLACE FUNCTION public.market_reference_security_semantic_v1(p_row JSONB)
RETURNS JSONB LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
  SELECT jsonb_build_object(
    'kind','security_revision','semantic_encoding_version',1,
    'value',jsonb_build_object(
      'revision',p_row->'revision','security_id',p_row->'security_id',
      'entity_id',p_row->'entity_id','ticker',p_row->'ticker','exchange',p_row->'exchange',
      'instrument_type',p_row->'instrument_type','eligible',p_row->'eligible',
      'exclusion_reasons',p_row->'exclusion_reasons','aliases',p_row->'aliases',
      'source_ids',p_row->'source_ids',
      'valid_from',to_char((p_row->>'valid_from')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'valid_to',CASE WHEN p_row->>'valid_to' IS NULL THEN NULL ELSE
        to_char((p_row->>'valid_to')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') END
    )
  )
$$;

-- Re-harden the legacy service-only reference writer from 20261005 with the
-- canonical semantic encoding before any first insert or idempotent replay.
CREATE OR REPLACE FUNCTION public.record_market_discovery_reference(p_run_id UUID, p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE m JSONB; r JSONB;
  v_existing_manifest public.market_reference_manifests%ROWTYPE;
  v_existing_security public.market_security_reference_revisions%ROWTYPE;
  v_count INT:=0; v_duplicate BOOLEAN:=true;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>1048576
     OR NOT(p_payload ?& ARRAY['manifest','security_revisions'])
     OR (p_payload-ARRAY['manifest','security_revisions'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'security_revisions')<>'array'
     OR jsonb_array_length(p_payload->'security_revisions')>15000 THEN
    RAISE EXCEPTION 'invalid discovery reference payload' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'security_revisions') child
    GROUP BY (child->>'id')::uuid HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate security revision id' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
                WHERE i.id=p_run_id AND a.status='running') THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  m:=p_payload->'manifest';
  IF jsonb_typeof(m)<>'object' OR NOT(m ?& ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])
     OR (m-ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])<>'{}'::jsonb
     OR m->>'source_hash' !~ '^[0-9a-f]{64}$' OR m->>'content_hash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(m->'manifest')<>'object' OR octet_length((m->'manifest')::text)>65536 THEN
    RAISE EXCEPTION 'invalid discovery reference manifest' USING ERRCODE='22023';
  END IF;
  IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
       public.market_reference_manifest_semantic_v1(m)
     ),'UTF8'),'sha256'),'hex')<>m->>'content_hash' THEN
    RAISE EXCEPTION 'discovery reference manifest hash mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing_manifest FROM public.market_reference_manifests WHERE id=(m->>'id')::uuid;
  IF FOUND THEN
    IF v_existing_manifest.run_id<>p_run_id
       OR v_existing_manifest.reference_version<>m->>'reference_version'
       OR v_existing_manifest.revision<>(m->>'revision')::int
       OR v_existing_manifest.capability_version<>(m->>'capability_version')::int
       OR v_existing_manifest.taxonomy_version<>(m->>'taxonomy_version')::int
       OR v_existing_manifest.source_hash<>m->>'source_hash'
       OR v_existing_manifest.valid_from<>(m->>'valid_from')::timestamptz
       OR v_existing_manifest.valid_to IS DISTINCT FROM (m->>'valid_to')::timestamptz
       OR v_existing_manifest.manifest<>m->'manifest'
       OR v_existing_manifest.content_hash<>m->>'content_hash' THEN
      RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    INSERT INTO public.market_reference_manifests(id,run_id,reference_version,revision,capability_version,taxonomy_version,source_hash,valid_from,valid_to,manifest,content_hash)
    VALUES((m->>'id')::uuid,p_run_id,m->>'reference_version',(m->>'revision')::int,(m->>'capability_version')::int,(m->>'taxonomy_version')::int,m->>'source_hash',(m->>'valid_from')::timestamptz,(m->>'valid_to')::timestamptz,m->'manifest',m->>'content_hash');
    v_duplicate:=false;
  END IF;
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'security_revisions') LOOP
    IF jsonb_typeof(r)<>'object' OR NOT(r ?& ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])
       OR (r-ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])<>'{}'::jsonb
       OR r->>'manifest_id'<>m->>'id' OR r->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(r->'source_ids')<>'array' OR jsonb_array_length(r->'source_ids') NOT BETWEEN 1 AND 16 THEN
      RAISE EXCEPTION 'invalid security reference revision' USING ERRCODE='22023';
    END IF;
    IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
         public.market_reference_security_semantic_v1(r)
       ),'UTF8'),'sha256'),'hex')<>r->>'content_hash' THEN
      RAISE EXCEPTION 'discovery reference security hash mismatch' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_existing_security FROM public.market_security_reference_revisions WHERE id=(r->>'id')::uuid;
    IF FOUND THEN
      IF v_existing_security.manifest_id<>(r->>'manifest_id')::uuid
         OR v_existing_security.run_id<>p_run_id
         OR v_existing_security.revision<>(r->>'revision')::int
         OR v_existing_security.security_id<>r->>'security_id'
         OR v_existing_security.entity_id<>r->>'entity_id'
         OR v_existing_security.ticker<>r->>'ticker'
         OR v_existing_security.exchange IS DISTINCT FROM r->>'exchange'
         OR v_existing_security.instrument_type<>r->>'instrument_type'
         OR v_existing_security.eligible<>(r->>'eligible')::boolean
         OR v_existing_security.exclusion_reasons<>r->'exclusion_reasons'
         OR v_existing_security.aliases<>r->'aliases'
         OR v_existing_security.source_ids<>r->'source_ids'
         OR v_existing_security.valid_from<>(r->>'valid_from')::timestamptz
         OR v_existing_security.valid_to IS DISTINCT FROM (r->>'valid_to')::timestamptz
         OR v_existing_security.content_hash<>r->>'content_hash' THEN
        RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
      END IF;
    ELSE
      IF v_duplicate THEN
        RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
      END IF;
      INSERT INTO public.market_security_reference_revisions(id,manifest_id,run_id,revision,security_id,entity_id,ticker,exchange,instrument_type,eligible,exclusion_reasons,aliases,source_ids,valid_from,valid_to,content_hash)
      VALUES((r->>'id')::uuid,(r->>'manifest_id')::uuid,p_run_id,(r->>'revision')::int,r->>'security_id',r->>'entity_id',r->>'ticker',r->>'exchange',r->>'instrument_type',(r->>'eligible')::boolean,r->'exclusion_reasons',r->'aliases',r->'source_ids',(r->>'valid_from')::timestamptz,(r->>'valid_to')::timestamptz,r->>'content_hash');
    END IF;
    v_count:=v_count+1;
  END LOOP;
  IF v_duplicate AND (
    SELECT count(*) FROM public.market_security_reference_revisions
    WHERE manifest_id=(m->>'id')::uuid
  )<>v_count THEN
    RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
  END IF;
  RETURN jsonb_build_object('manifest_id',m->>'id','security_revision_count',v_count,'duplicate',v_duplicate);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid discovery reference payload' USING ERRCODE='22023';
END;
$$;

CREATE TABLE IF NOT EXISTS public.market_reference_transfer_requests (
  request_id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  operation TEXT NOT NULL CHECK (operation IN (
    'begin_discovery_reference','record_discovery_reference_chunk',
    'finalize_discovery_reference','pin_discovery_reference','read_discovery_reference'
  )),
  encoded_bytes INT NOT NULL CHECK (encoded_bytes BETWEEN 1 AND 262144),
  request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  request_payload JSONB NOT NULL CHECK (jsonb_typeof(request_payload)='object' AND octet_length(request_payload::text)<=196608),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS public.market_reference_chunk_receipts (
  manifest_id UUID NOT NULL,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  capability_id TEXT NOT NULL CHECK (capability_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  chunk_index INT NOT NULL CHECK (chunk_index BETWEEN -1 AND 511),
  chunk_count INT NOT NULL CHECK (chunk_count BETWEEN 1 AND 512),
  entry_count INT NOT NULL CHECK (entry_count BETWEEN 0 AND 200),
  chunk_hash TEXT NOT NULL CHECK (chunk_hash ~ '^[0-9a-f]{64}$'),
  predecessor_manifest_id UUID,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=196608),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (manifest_id,chunk_index)
);

CREATE TABLE IF NOT EXISTS public.market_reference_snapshot_memberships (
  manifest_id UUID NOT NULL REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  security_revision_id UUID NOT NULL REFERENCES public.market_security_reference_revisions(id) ON DELETE RESTRICT,
  security_id TEXT NOT NULL CHECK (security_id ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'),
  ordinal INT NOT NULL CHECK (ordinal BETWEEN 0 AND 14999),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (manifest_id,security_id),
  UNIQUE (manifest_id,security_revision_id),
  UNIQUE (manifest_id,ordinal)
);

CREATE TABLE IF NOT EXISTS public.market_reference_finalization_seals (
  manifest_id UUID PRIMARY KEY REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  capability_id TEXT NOT NULL CHECK (capability_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  predecessor_manifest_id UUID REFERENCES public.market_reference_finalization_seals(manifest_id) ON DELETE RESTRICT,
  chunk_count INT NOT NULL CHECK (chunk_count BETWEEN 1 AND 512),
  security_count INT NOT NULL CHECK (security_count BETWEEN 1 AND 15000),
  root_hash TEXT NOT NULL CHECK (root_hash ~ '^[0-9a-f]{64}$'),
  finalized_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id,capability_id)
);

CREATE TABLE IF NOT EXISTS public.market_reference_run_bindings (
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  capability_id TEXT NOT NULL CHECK (capability_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  manifest_id UUID REFERENCES public.market_reference_finalization_seals(manifest_id) ON DELETE RESTRICT,
  reference_status TEXT NOT NULL CHECK (reference_status IN ('healthy','reference_stale','reference_unavailable')),
  reference_as_of TIMESTAMPTZ NOT NULL,
  source_retrieved_at TIMESTAMPTZ,
  reference_age_seconds BIGINT CHECK (reference_age_seconds IS NULL OR reference_age_seconds>=0),
  request_payload JSONB NOT NULL CHECK (jsonb_typeof(request_payload)='object' AND octet_length(request_payload::text)<=196608),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (run_id,capability_id),
  CHECK (
    (reference_status='reference_unavailable' AND manifest_id IS NULL AND source_retrieved_at IS NULL AND reference_age_seconds IS NULL)
    OR (reference_status IN ('healthy','reference_stale') AND manifest_id IS NOT NULL AND source_retrieved_at IS NOT NULL AND reference_age_seconds IS NOT NULL)
  )
);

CREATE TABLE IF NOT EXISTS public.market_reference_predecessor_pins (
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  capability_id TEXT NOT NULL CHECK (capability_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  manifest_id UUID REFERENCES public.market_reference_finalization_seals(manifest_id) ON DELETE RESTRICT,
  reference_status TEXT NOT NULL CHECK (reference_status IN ('reference_stale','reference_unavailable')),
  reference_as_of TIMESTAMPTZ NOT NULL,
  source_retrieved_at TIMESTAMPTZ,
  reference_age_seconds BIGINT CHECK (reference_age_seconds IS NULL OR reference_age_seconds>=0),
  request_payload JSONB NOT NULL CHECK (jsonb_typeof(request_payload)='object' AND octet_length(request_payload::text)<=196608),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (run_id,capability_id),
  CHECK (
    (reference_status='reference_unavailable' AND manifest_id IS NULL AND source_retrieved_at IS NULL AND reference_age_seconds IS NULL)
    OR (reference_status='reference_stale' AND manifest_id IS NOT NULL AND source_retrieved_at IS NOT NULL AND reference_age_seconds IS NOT NULL)
  )
);

DROP TRIGGER IF EXISTS market_reference_chunk_receipts_append_only ON public.market_reference_chunk_receipts;
CREATE TRIGGER market_reference_chunk_receipts_append_only BEFORE UPDATE OR DELETE ON public.market_reference_chunk_receipts
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_reference_snapshot_memberships_append_only ON public.market_reference_snapshot_memberships;
CREATE TRIGGER market_reference_snapshot_memberships_append_only BEFORE UPDATE OR DELETE ON public.market_reference_snapshot_memberships
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_reference_finalization_seals_append_only ON public.market_reference_finalization_seals;
CREATE TRIGGER market_reference_finalization_seals_append_only BEFORE UPDATE OR DELETE ON public.market_reference_finalization_seals
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_reference_run_bindings_append_only ON public.market_reference_run_bindings;
CREATE TRIGGER market_reference_run_bindings_append_only BEFORE UPDATE OR DELETE ON public.market_reference_run_bindings
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_reference_predecessor_pins_append_only ON public.market_reference_predecessor_pins;
CREATE TRIGGER market_reference_predecessor_pins_append_only BEFORE UPDATE OR DELETE ON public.market_reference_predecessor_pins
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_reference_transfer_requests_append_only ON public.market_reference_transfer_requests;
CREATE TRIGGER market_reference_transfer_requests_append_only BEFORE UPDATE OR DELETE ON public.market_reference_transfer_requests
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();

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
  IF v_count>=160 OR v_bytes+p_encoded_bytes>33554432
     OR (v_started IS NOT NULL AND clock_timestamp()-v_started>interval '45 seconds') THEN
    RAISE EXCEPTION 'reference transfer aggregate bound exceeded' USING ERRCODE='54000';
  END IF;
  INSERT INTO public.market_reference_transfer_requests(
    request_id,run_id,operation,encoded_bytes,request_hash,request_payload
  ) VALUES(p_request_id,p_run_id,p_operation,p_encoded_bytes,p_request_hash,p_payload);
  RETURN false;
END;
$$;

CREATE OR REPLACE FUNCTION public.begin_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_manifest JSONB;
  v_manifest_id UUID;
  v_existing public.market_reference_chunk_receipts%ROWTYPE;
  v_predecessor UUID;
BEGIN
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'begin_discovery_reference',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest','capability_id','chunk_count','security_count','root_hash','predecessor_manifest_id'])
     OR (p_payload-ARRAY['manifest','capability_id','chunk_count','security_count','root_hash','predecessor_manifest_id'])<>'{}'::jsonb
     OR p_payload->>'capability_id' !~ '^[a-z][a-z0-9_]{2,79}$'
     OR (p_payload->>'chunk_count')::int NOT BETWEEN 1 AND 512
     OR (p_payload->>'security_count')::int NOT BETWEEN 1 AND 15000
     OR p_payload->>'root_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference begin payload' USING ERRCODE='22023';
  END IF;
  PERFORM 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
  WHERE i.id=p_run_id AND a.status='running' FOR UPDATE OF i,a;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  v_manifest:=p_payload->'manifest';
  IF jsonb_typeof(v_manifest)<>'object'
     OR NOT(v_manifest ?& ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])
     OR (v_manifest-ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])<>'{}'::jsonb
     OR v_manifest->>'source_hash' !~ '^[0-9a-f]{64}$'
     OR v_manifest->>'content_hash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(v_manifest->'manifest')<>'object'
     OR v_manifest->'manifest'->>'coverage_status'<>'scope_not_guaranteed'
     OR v_manifest->'manifest'->>'reference_status'<>'healthy'
     OR (v_manifest->'manifest'->>'security_count')::int<>(p_payload->>'security_count')::int THEN
    RAISE EXCEPTION 'invalid reference begin manifest' USING ERRCODE='22023';
  END IF;
  IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
       public.market_reference_manifest_semantic_v1(v_manifest)
     ),'UTF8'),'sha256'),'hex')<>v_manifest->>'content_hash' THEN
    RAISE EXCEPTION 'reference manifest hash mismatch' USING ERRCODE='22023';
  END IF;
  v_manifest_id:=(v_manifest->>'id')::uuid;
  IF p_payload->>'predecessor_manifest_id' IS NOT NULL THEN
    v_predecessor:=(p_payload->>'predecessor_manifest_id')::uuid;
    IF NOT EXISTS(
      SELECT 1 FROM public.market_reference_predecessor_pins b
      WHERE b.run_id=p_run_id AND b.capability_id=p_payload->>'capability_id'
        AND b.manifest_id=v_predecessor AND b.reference_status='reference_stale'
    ) THEN
      RAISE EXCEPTION 'reference predecessor membership mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    IF NOT EXISTS(
      SELECT 1 FROM public.market_reference_predecessor_pins b
      WHERE b.run_id=p_run_id AND b.capability_id=p_payload->>'capability_id'
        AND b.manifest_id IS NULL AND b.reference_status='reference_unavailable'
    ) THEN
      RAISE EXCEPTION 'reference predecessor membership mismatch' USING ERRCODE='22023';
    END IF;
  END IF;
  SELECT * INTO v_existing FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1;
  IF FOUND THEN
    IF v_existing.run_id<>p_run_id OR v_existing.capability_id<>p_payload->>'capability_id'
       OR v_existing.chunk_count<>(p_payload->>'chunk_count')::int
       OR v_existing.entry_count<>0 OR v_existing.chunk_hash<>p_payload->>'root_hash'
       OR v_existing.payload<>p_payload THEN
      RAISE EXCEPTION 'reference begin idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'predecessor_manifest_id',v_existing.predecessor_manifest_id::text,'duplicate',true);
  END IF;
  IF EXISTS(SELECT 1 FROM public.market_reference_manifests WHERE id=v_manifest_id)
     OR EXISTS(SELECT 1 FROM public.market_reference_chunk_receipts WHERE manifest_id=v_manifest_id) THEN
    RAISE EXCEPTION 'reference begin idempotency mismatch' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_reference_chunk_receipts(
    manifest_id,run_id,capability_id,chunk_index,chunk_count,entry_count,chunk_hash,
    predecessor_manifest_id,payload
  ) VALUES(
    v_manifest_id,p_run_id,p_payload->>'capability_id',-1,(p_payload->>'chunk_count')::int,
    0,p_payload->>'root_hash',v_predecessor,p_payload
  );
  RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'predecessor_manifest_id',v_predecessor::text,'duplicate',false);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid reference begin payload' USING ERRCODE='22023';
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
BEGIN
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'record_discovery_reference_chunk',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest_id','chunk_index','chunk_count','entries','chunk_hash'])
     OR (p_payload-ARRAY['manifest_id','chunk_index','chunk_count','entries','chunk_hash'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'entries')<>'array'
     OR jsonb_array_length(p_payload->'entries') NOT BETWEEN 1 AND 200
     OR (p_payload->>'chunk_index')::int NOT BETWEEN 0 AND 511
     OR (p_payload->>'chunk_count')::int NOT BETWEEN 1 AND 512
     OR p_payload->>'chunk_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference chunk payload' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(
    SELECT 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
    WHERE i.id=p_run_id AND a.status='running'
  ) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  v_manifest_id:=(p_payload->>'manifest_id')::uuid;
  SELECT * INTO v_begin FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1 FOR UPDATE;
  IF NOT FOUND OR v_begin.run_id<>p_run_id
     OR v_begin.chunk_count<>(p_payload->>'chunk_count')::int
     OR (p_payload->>'chunk_index')::int>=v_begin.chunk_count THEN
    RAISE EXCEPTION 'reference chunk has no matching begin' USING ERRCODE='22023';
  END IF;
  FOR v_entry IN SELECT value FROM jsonb_array_elements(p_payload->'entries') LOOP
    IF jsonb_typeof(v_entry)<>'object'
       OR NOT(v_entry ?& ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])
       OR (v_entry-ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])<>'{}'::jsonb
       OR v_entry->>'manifest_id'<>v_manifest_id::text
       OR v_entry->>'security_id' !~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'
       OR v_entry->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$'
       OR v_entry->>'content_hash' !~ '^[0-9a-f]{64}$' THEN
      RAISE EXCEPTION 'invalid reference chunk entry' USING ERRCODE='22023';
    END IF;
    IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
         public.market_reference_security_semantic_v1(v_entry)
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
    RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'chunk_index',v_existing.chunk_index,'duplicate',true);
  END IF;
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
  RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'chunk_index',(p_payload->>'chunk_index')::int,'duplicate',false);
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
  v_chunk_count INT;
  v_entry_count INT;
  v_min_chunk INT;
  v_max_chunk INT;
  v_root TEXT;
  v_entry JSONB;
  v_prior_revision UUID;
  v_revision_id UUID;
  v_ordinal INT:=0;
  v_manifest JSONB;
BEGIN
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'finalize_discovery_reference',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest_id','root_hash'])
     OR (p_payload-ARRAY['manifest_id','root_hash'])<>'{}'::jsonb
     OR p_payload->>'root_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference finalize payload' USING ERRCODE='22023';
  END IF;
  v_manifest_id:=(p_payload->>'manifest_id')::uuid;
  SELECT * INTO v_seal FROM public.market_reference_finalization_seals
  WHERE manifest_id=v_manifest_id;
  IF FOUND THEN
    IF v_seal.run_id<>p_run_id OR v_seal.root_hash<>p_payload->>'root_hash' THEN
      RAISE EXCEPTION 'reference finalization idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_seal.security_count,'duplicate',true);
  END IF;
  IF NOT EXISTS(
    SELECT 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
    WHERE i.id=p_run_id AND a.status='running'
  ) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_begin FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1 FOR UPDATE;
  IF NOT FOUND OR v_begin.run_id<>p_run_id OR v_begin.chunk_hash<>p_payload->>'root_hash' THEN
    RAISE EXCEPTION 'reference finalization has no matching begin' USING ERRCODE='22023';
  END IF;
  -- A concurrent exact retry can pass the optimistic seal lookup before the
  -- first caller commits. Recheck while holding the begin receipt lock so the
  -- loser observes the committed seal instead of surfacing a false conflict.
  SELECT * INTO v_seal FROM public.market_reference_finalization_seals
  WHERE manifest_id=v_manifest_id;
  IF FOUND THEN
    IF v_seal.run_id<>p_run_id OR v_seal.root_hash<>p_payload->>'root_hash' THEN
      RAISE EXCEPTION 'reference finalization idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_seal.security_count,'duplicate',true);
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
  IF v_root<>v_begin.chunk_hash THEN
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
         public.market_reference_security_semantic_v1(v_entry)
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
        AND s.content_hash=v_entry->>'content_hash';
    END IF;
    IF v_prior_revision IS NULL THEN
      v_revision_id:=(v_entry->>'id')::uuid;
      INSERT INTO public.market_security_reference_revisions(
        id,manifest_id,run_id,revision,security_id,entity_id,ticker,exchange,instrument_type,
        eligible,exclusion_reasons,aliases,source_ids,valid_from,valid_to,content_hash
      ) VALUES(
        v_revision_id,v_manifest_id,p_run_id,(v_entry->>'revision')::int,
        v_entry->>'security_id',v_entry->>'entity_id',v_entry->>'ticker',v_entry->>'exchange',
        v_entry->>'instrument_type',(v_entry->>'eligible')::boolean,v_entry->'exclusion_reasons',
        v_entry->'aliases',v_entry->'source_ids',(v_entry->>'valid_from')::timestamptz,
        (v_entry->>'valid_to')::timestamptz,v_entry->>'content_hash'
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
  RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_entry_count,'duplicate',false);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range OR unique_violation OR foreign_key_violation THEN
  RAISE EXCEPTION 'reference finalization conflict' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.pin_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_existing public.market_reference_run_bindings%ROWTYPE;
  v_predecessor_existing public.market_reference_predecessor_pins%ROWTYPE;
  v_manifest_id UUID;
  v_status TEXT;
  v_as_of TIMESTAMPTZ;
  v_source TIMESTAMPTZ;
  v_age BIGINT;
  v_capability TEXT;
  v_role TEXT;
BEGIN
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'pin_discovery_reference',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['capability_id','binding_role','manifest_id','reference_status','reference_as_of'])
     OR (p_payload-ARRAY['capability_id','binding_role','manifest_id','reference_status','reference_as_of'])<>'{}'::jsonb
     OR p_payload->>'capability_id' !~ '^[a-z][a-z0-9_]{2,79}$'
     OR p_payload->>'binding_role' NOT IN ('predecessor','current')
     OR p_payload->>'reference_status' NOT IN ('healthy','reference_stale','reference_unavailable') THEN
    RAISE EXCEPTION 'invalid reference pin payload' USING ERRCODE='22023';
  END IF;
  v_capability:=p_payload->>'capability_id';
  v_role:=p_payload->>'binding_role';
  v_status:=p_payload->>'reference_status';
  v_as_of:=(p_payload->>'reference_as_of')::timestamptz;
  -- Serialize the one binding for this run before checking for an exact retry.
  -- This also prevents the run from finishing between validation and insert.
  PERFORM 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
  WHERE i.id=p_run_id AND a.status='running' FOR UPDATE OF i,a;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  IF v_role='predecessor' THEN
    IF v_status<>'reference_stale' OR p_payload->>'manifest_id' IS NOT NULL THEN
      RAISE EXCEPTION 'predecessor pin must request latest finalized reference' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_predecessor_existing FROM public.market_reference_predecessor_pins
    WHERE run_id=p_run_id AND capability_id=v_capability;
    IF FOUND THEN
      IF v_predecessor_existing.request_payload<>p_payload
         OR v_predecessor_existing.reference_as_of<>v_as_of THEN
        RAISE EXCEPTION 'reference pin idempotency mismatch' USING ERRCODE='22023';
      END IF;
      RETURN jsonb_build_object(
        'binding_role','predecessor','manifest_id',v_predecessor_existing.manifest_id::text,
        'reference_status',v_predecessor_existing.reference_status,
        'source_retrieved_at',v_predecessor_existing.source_retrieved_at,
        'reference_age_seconds',v_predecessor_existing.reference_age_seconds,'duplicate',true
      );
    END IF;
    SELECT s.manifest_id INTO v_manifest_id
    FROM public.market_reference_finalization_seals s
    WHERE s.capability_id=v_capability AND s.run_id<>p_run_id
    ORDER BY s.finalized_at DESC,s.manifest_id DESC LIMIT 1;
    IF v_manifest_id IS NULL THEN
      v_status:='reference_unavailable';
    ELSE
      SELECT (m.manifest->>'source_retrieved_at')::timestamptz INTO v_source
      FROM public.market_reference_manifests m WHERE m.id=v_manifest_id;
      IF v_source IS NULL OR v_source>v_as_of THEN
        RAISE EXCEPTION 'reference age is invalid' USING ERRCODE='22023';
      END IF;
      v_age:=floor(extract(epoch FROM (v_as_of-v_source)))::bigint;
    END IF;
    INSERT INTO public.market_reference_predecessor_pins(
      run_id,capability_id,manifest_id,reference_status,reference_as_of,
      source_retrieved_at,reference_age_seconds,request_payload
    ) VALUES(p_run_id,v_capability,v_manifest_id,v_status,v_as_of,v_source,v_age,p_payload);
    RETURN jsonb_build_object(
      'binding_role','predecessor','manifest_id',v_manifest_id::text,
      'reference_status',v_status,'source_retrieved_at',v_source,
      'reference_age_seconds',v_age,'duplicate',false
    );
  END IF;
  SELECT * INTO v_existing FROM public.market_reference_run_bindings
  WHERE run_id=p_run_id AND capability_id=v_capability;
  IF FOUND THEN
    IF v_existing.request_payload<>p_payload
       OR v_existing.reference_as_of<>v_as_of
       OR (v_status='healthy' AND (
         p_payload->>'manifest_id' IS NULL OR v_existing.reference_status<>'healthy'
         OR v_existing.manifest_id IS DISTINCT FROM (p_payload->>'manifest_id')::uuid
       ))
       OR (v_status='reference_stale' AND (
         v_existing.reference_status NOT IN ('reference_stale','reference_unavailable')
         OR (p_payload->>'manifest_id' IS NOT NULL
             AND v_existing.manifest_id IS DISTINCT FROM (p_payload->>'manifest_id')::uuid)
       ))
       OR (v_status='reference_unavailable' AND (
         p_payload->>'manifest_id' IS NOT NULL
         OR v_existing.reference_status<>'reference_unavailable'
       )) THEN
      RAISE EXCEPTION 'reference pin idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object(
      'binding_role','current','manifest_id',v_existing.manifest_id::text,
      'reference_status',v_existing.reference_status,
      'source_retrieved_at',v_existing.source_retrieved_at,
      'reference_age_seconds',v_existing.reference_age_seconds,'duplicate',true
    );
  END IF;
  IF v_status='healthy' THEN
    IF p_payload->>'manifest_id' IS NULL THEN
      RAISE EXCEPTION 'healthy reference pin requires a manifest' USING ERRCODE='22023';
    END IF;
    v_manifest_id:=(p_payload->>'manifest_id')::uuid;
    IF NOT EXISTS(
      SELECT 1 FROM public.market_reference_finalization_seals s
      WHERE s.manifest_id=v_manifest_id AND s.run_id=p_run_id AND s.capability_id=v_capability
    ) THEN
      RAISE EXCEPTION 'reference snapshot is not finalized' USING ERRCODE='22023';
    END IF;
  ELSIF v_status='reference_stale' THEN
    IF p_payload->>'manifest_id' IS NULL THEN
      SELECT s.manifest_id INTO v_manifest_id
      FROM public.market_reference_finalization_seals s
      WHERE s.capability_id=v_capability AND s.run_id<>p_run_id
      ORDER BY s.finalized_at DESC,s.manifest_id DESC LIMIT 1;
    ELSE
      v_manifest_id:=(p_payload->>'manifest_id')::uuid;
    END IF;
    IF v_manifest_id IS NULL THEN
      v_status:='reference_unavailable';
    ELSIF NOT EXISTS(
      SELECT 1 FROM public.market_reference_finalization_seals s
      WHERE s.manifest_id=v_manifest_id AND s.capability_id=v_capability
    ) THEN
      RAISE EXCEPTION 'reference snapshot is not finalized' USING ERRCODE='22023';
    END IF;
  ELSE
    IF p_payload->>'manifest_id' IS NOT NULL OR EXISTS(
      SELECT 1 FROM public.market_reference_finalization_seals s WHERE s.capability_id=v_capability
    ) THEN
      RAISE EXCEPTION 'reference unavailable conflicts with finalized snapshot' USING ERRCODE='22023';
    END IF;
    v_manifest_id:=NULL;
  END IF;
  IF v_manifest_id IS NOT NULL THEN
    SELECT (m.manifest->>'source_retrieved_at')::timestamptz INTO v_source
    FROM public.market_reference_manifests m
    JOIN public.market_reference_finalization_seals s ON s.manifest_id=m.id
    WHERE m.id=v_manifest_id;
    IF v_source IS NULL OR v_source>v_as_of THEN
      RAISE EXCEPTION 'reference age is invalid' USING ERRCODE='22023';
    END IF;
    v_age:=floor(extract(epoch FROM (v_as_of-v_source)))::bigint;
  END IF;
  INSERT INTO public.market_reference_run_bindings(
    run_id,capability_id,manifest_id,reference_status,reference_as_of,
    source_retrieved_at,reference_age_seconds,request_payload
  ) VALUES(p_run_id,v_capability,v_manifest_id,v_status,v_as_of,v_source,v_age,p_payload);
  RETURN jsonb_build_object(
    'binding_role','current','manifest_id',v_manifest_id::text,'reference_status',v_status,
    'source_retrieved_at',v_source,'reference_age_seconds',v_age,'duplicate',false
  );
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid reference pin payload' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.read_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_capability TEXT;
  v_role TEXT;
  v_manifest_id UUID;
  v_status TEXT;
  v_source TIMESTAMPTZ;
  v_age BIGINT;
  v_after TEXT;
  v_limit INT;
  v_row JSONB;
  v_rows JSONB:='[]'::jsonb;
  v_candidate JSONB;
  v_result JSONB;
  v_last TEXT;
  v_seen INT:=0;
  v_more BOOLEAN:=false;
BEGIN
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'read_discovery_reference',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['capability_id','binding_role','after_security_id','limit'])
     OR (p_payload-ARRAY['capability_id','binding_role','after_security_id','limit'])<>'{}'::jsonb
     OR p_payload->>'capability_id' !~ '^[a-z][a-z0-9_]{2,79}$'
     OR p_payload->>'binding_role' NOT IN ('predecessor','current')
     OR (p_payload->>'limit')::int NOT BETWEEN 1 AND 500 THEN
    RAISE EXCEPTION 'invalid reference read payload' USING ERRCODE='22023';
  END IF;
  v_capability:=p_payload->>'capability_id';
  v_role:=p_payload->>'binding_role';
  v_after:=p_payload->>'after_security_id';
  v_limit:=(p_payload->>'limit')::int;
  IF v_role='predecessor' THEN
    SELECT manifest_id,reference_status,source_retrieved_at,reference_age_seconds
    INTO v_manifest_id,v_status,v_source,v_age
    FROM public.market_reference_predecessor_pins
    WHERE run_id=p_run_id AND capability_id=v_capability;
  ELSE
    SELECT manifest_id,reference_status,source_retrieved_at,reference_age_seconds
    INTO v_manifest_id,v_status,v_source,v_age
    FROM public.market_reference_run_bindings
    WHERE run_id=p_run_id AND capability_id=v_capability;
  END IF;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'reference snapshot is not pinned' USING ERRCODE='22023';
  END IF;
  IF v_manifest_id IS NULL THEN
    RETURN jsonb_build_object(
      'binding',jsonb_build_object(
        'binding_role',v_role,'manifest_id',NULL,'reference_status','reference_unavailable',
        'source_retrieved_at',NULL,'reference_age_seconds',NULL
      ),
      'manifest',NULL,'securities','[]'::jsonb,'next_after_security_id',NULL,'complete',true
    );
  END IF;
  IF NOT EXISTS(
    SELECT 1 FROM public.market_reference_finalization_seals s
    WHERE s.manifest_id=v_manifest_id
  ) THEN
    RAISE EXCEPTION 'reference snapshot is not finalized' USING ERRCODE='22023';
  END IF;
  FOR v_row IN
    SELECT to_jsonb(x) FROM (
      SELECT s.id,m.manifest_id,s.revision,s.security_id,s.entity_id,s.ticker,s.exchange,
             s.instrument_type,s.eligible,s.exclusion_reasons,s.aliases,s.source_ids,
             s.valid_from,s.valid_to,s.content_hash
      FROM public.market_reference_snapshot_memberships m
      JOIN public.market_security_reference_revisions s ON s.id=m.security_revision_id
      WHERE m.manifest_id=v_manifest_id
        AND (v_after IS NULL OR m.security_id>v_after)
      ORDER BY m.security_id
      LIMIT v_limit+1
    ) x
  LOOP
    IF v_seen>=v_limit THEN
      v_more:=true;
      EXIT;
    END IF;
    v_candidate:=v_rows||jsonb_build_array(v_row);
    SELECT jsonb_build_object(
      'binding',jsonb_build_object(
        'binding_role',v_role,'manifest_id',v_manifest_id::text,
        'reference_status',v_status,
        'source_retrieved_at',v_source,
        'reference_age_seconds',v_age
      ),
      'manifest',(SELECT to_jsonb(y) FROM (
        SELECT id,reference_version,revision,capability_version,taxonomy_version,source_hash,
               valid_from,valid_to,manifest,content_hash
        FROM public.market_reference_manifests WHERE id=v_manifest_id
      ) y),
      'securities',v_candidate,'next_after_security_id',v_row->>'security_id','complete',false
    ) INTO v_result;
    IF octet_length(v_result::text)>196608 THEN
      v_more:=true;
      EXIT;
    END IF;
    v_rows:=v_candidate;
    v_last:=v_row->>'security_id';
    v_seen:=v_seen+1;
  END LOOP;
  IF v_seen=0 AND v_more THEN
    RAISE EXCEPTION 'one reference row exceeds page bound' USING ERRCODE='54000';
  END IF;
  SELECT jsonb_build_object(
    'binding',jsonb_build_object(
      'binding_role',v_role,'manifest_id',v_manifest_id::text,
      'reference_status',v_status,
      'source_retrieved_at',v_source,
      'reference_age_seconds',v_age
    ),
    'manifest',(SELECT to_jsonb(y) FROM (
      SELECT id,reference_version,revision,capability_version,taxonomy_version,source_hash,
             valid_from,valid_to,manifest,content_hash
      FROM public.market_reference_manifests WHERE id=v_manifest_id
    ) y),
    'securities',v_rows,'next_after_security_id',CASE WHEN v_more THEN v_last ELSE NULL END,
    'complete',NOT v_more
  ) INTO v_result;
  IF octet_length(v_result::text)>196608 THEN
    RAISE EXCEPTION 'reference page exceeds bound' USING ERRCODE='54000';
  END IF;
  RETURN v_result;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid reference read payload' USING ERRCODE='22023';
END;
$$;

ALTER TABLE public.market_reference_chunk_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reference_snapshot_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reference_finalization_seals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reference_run_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reference_predecessor_pins ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reference_transfer_requests ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.market_reference_chunk_receipts,public.market_reference_snapshot_memberships,
  public.market_reference_finalization_seals,public.market_reference_run_bindings,
  public.market_reference_predecessor_pins,public.market_reference_transfer_requests
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader;
GRANT SELECT ON public.market_reference_chunk_receipts,public.market_reference_snapshot_memberships,
  public.market_reference_finalization_seals,public.market_reference_run_bindings,
  public.market_reference_predecessor_pins,public.market_reference_transfer_requests
  TO stock_agent_release_reader;

REVOKE ALL ON FUNCTION public.market_reference_canonical_json_v1(JSONB) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.market_reference_manifest_semantic_v1(JSONB) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.market_reference_security_semantic_v1(JSONB) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.record_market_discovery_reference(UUID,JSONB) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.claim_market_reference_transfer_request(UUID,TEXT,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.begin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.finalize_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.pin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.record_market_discovery_reference(UUID,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.begin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finalize_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.pin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;

DO $$ DECLARE name TEXT; BEGIN
  FOREACH name IN ARRAY ARRAY[
    'market_reference_chunk_receipts','market_reference_snapshot_memberships',
    'market_reference_finalization_seals','market_reference_run_bindings',
    'market_reference_predecessor_pins','market_reference_transfer_requests'
  ] LOOP
    EXECUTE format('DROP POLICY IF EXISTS release_evidence_select ON public.%I',name);
    EXECUTE format('CREATE POLICY release_evidence_select ON public.%I FOR SELECT TO stock_agent_release_reader USING (true)',name);
  END LOOP;
END; $$;
