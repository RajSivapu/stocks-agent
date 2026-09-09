-- Preserve per-run count and byte ceilings while allowing an interrupted
-- reference transfer to resume after the client-side elapsed-time window.
-- A wall-clock deadline anchored to the first durable request permanently
-- rejected every later restart, even though exact replays and resumable page
-- reads are part of the transfer contract.

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
  SELECT count(*),COALESCE(sum(encoded_bytes),0)
  INTO v_count,v_bytes FROM public.market_reference_transfer_requests
  WHERE run_id=p_run_id;
  IF v_count>=384 OR v_bytes+p_encoded_bytes>50331648 THEN
    RAISE EXCEPTION 'reference transfer aggregate bound exceeded' USING ERRCODE='54000';
  END IF;
  INSERT INTO public.market_reference_transfer_requests(
    request_id,run_id,operation,encoded_bytes,request_hash,request_payload
  ) VALUES(p_request_id,p_run_id,p_operation,p_encoded_bytes,p_request_hash,p_payload);
  RETURN false;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_market_reference_transfer_request(UUID,TEXT,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;

-- A reference source request may finish durably before the worker records its
-- terminal task row. Permit only that reference task to recover, and only when
-- its result exactly describes the current pin whose request and response were
-- both durably recorded. No new provider attempt is authorized by this path.
CREATE OR REPLACE FUNCTION public.enforce_market_discovery_stage_task_transition()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE
  v_coverage JSONB;
  v_recovered_reference BOOLEAN:=false;
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'market discovery task deletion is forbidden' USING ERRCODE='55000';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.stage IS DISTINCT FROM OLD.stage OR NEW.capability_id IS DISTINCT FROM OLD.capability_id
     OR NEW.provider IS DISTINCT FROM OLD.provider OR NEW.query_kind IS DISTINCT FROM OLD.query_kind
     OR NEW.query_hash IS DISTINCT FROM OLD.query_hash OR NEW.dependency_ids IS DISTINCT FROM OLD.dependency_ids
     OR NEW.requested_window IS DISTINCT FROM OLD.requested_window
     OR NEW.request_budget IS DISTINCT FROM OLD.request_budget OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'discovery task identity mismatch' USING ERRCODE='22023';
  END IF;
  IF OLD.stage='reference' AND OLD.capability_id='sec_company_tickers_universe'
     AND OLD.state IN ('failed','uncertain') AND NEW.state='succeeded'
     AND NEW.attempt_count=OLD.attempt_count
     AND jsonb_typeof(NEW.result)='object'
     AND (NEW.result-ARRAY['reference_coverage'])='{}'::jsonb THEN
    v_coverage:=NEW.result->'reference_coverage';
    IF jsonb_typeof(v_coverage)='object'
       AND v_coverage ?& ARRAY[
         'coverage_status','reference_status','reference_manifest_id','reference_age_seconds'
       ]
       AND (v_coverage-ARRAY[
         'coverage_status','reference_status','reference_manifest_id','reference_age_seconds',
         'reference_revision','reference_expires_at'
       ])='{}'::jsonb
       AND v_coverage->>'coverage_status'='scope_not_guaranteed' THEN
      SELECT EXISTS(
        SELECT 1
        FROM public.market_reference_run_bindings binding
        JOIN public.market_reference_transfer_requests request
          ON request.run_id=binding.run_id
         AND request.operation='pin_discovery_reference'
         AND request.request_payload=binding.request_payload
        JOIN public.market_reference_transfer_responses response
          ON response.request_id=request.request_id
         AND response.run_id=request.run_id
        LEFT JOIN public.market_reference_manifests manifest
          ON manifest.id=binding.manifest_id
        WHERE binding.run_id=NEW.run_id
          AND binding.capability_id=NEW.capability_id
          AND v_coverage->>'reference_status'=binding.reference_status
          AND v_coverage->>'reference_manifest_id'
                IS NOT DISTINCT FROM binding.manifest_id::text
          AND (v_coverage->>'reference_age_seconds')::bigint
                IS NOT DISTINCT FROM binding.reference_age_seconds
          AND (
            NOT (v_coverage ? 'reference_revision')
            OR (v_coverage->>'reference_revision')::int=manifest.revision
          )
          AND (
            NOT (v_coverage ? 'reference_expires_at')
            OR (v_coverage->>'reference_expires_at')::timestamptz
                 =binding.source_retrieved_at+interval '24 hours'
          )
          AND ((v_coverage ? 'reference_revision')=(v_coverage ? 'reference_expires_at'))
      ) INTO v_recovered_reference;
    END IF;
  END IF;
  IF NOT (
    (OLD.state='planned' AND NEW.state='attempting'
      AND NEW.attempt_count=OLD.attempt_count+1 AND NEW.result=OLD.result)
    OR (OLD.state='attempting' AND NEW.state IN ('succeeded','failed','deferred','uncertain')
      AND NEW.attempt_count=OLD.attempt_count)
    OR v_recovered_reference
  ) OR NEW.updated_at<=OLD.updated_at THEN
    RAISE EXCEPTION 'invalid discovery task state transition' USING ERRCODE='22023';
  END IF;
  RETURN NEW;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid discovery task state transition' USING ERRCODE='22023';
END;
$$;
