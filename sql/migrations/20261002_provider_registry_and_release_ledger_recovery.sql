-- All reviewed non-Yahoo outbound reservations checkpoint before transport.
-- Keep the exact list synchronized with the reviewed Python provider registry.
CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(
  p_run_id UUID,
  p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_window JSONB;
  v_existing public.market_collection_checkpoints%ROWTYPE;
  r JSONB;
  v_reserved public.market_source_quota_reservations%ROWTYPE;
  v_used INT;
  v_existing_receipt JSONB;
  v_existing_uncertain BOOLEAN;
  v_incoming_uncertain BOOLEAN;
  v_same_attempt_identity BOOLEAN;
  v_valid_attempt_transition BOOLEAN;
BEGIN
  IF p_payload IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR NOT(p_payload ?& ARRAY['cache_key','receipt','items'])
     OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb
     OR octet_length(p_payload::text)>65536
     OR jsonb_typeof(p_payload->'items')<>'array'
     OR jsonb_array_length(p_payload->'items')>50 THEN
    RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023';
  END IF;
  SELECT request_window INTO v_window
  FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL
     OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;

  r:=p_payload->'receipt';
  IF r IS NULL OR NOT(r ?& ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])
     OR (r-ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])<>'{}'::jsonb
     OR r->>'status' NOT IN ('succeeded','failed','quota_blocked')
     OR (r->>'request_cost')::int NOT BETWEEN 1 AND 100
     OR r->'cache_predecessor_receipt_id'<>'null'::jsonb
     OR r->>'cache_key'<>p_payload->>'cache_key'
     OR (r->'requested_window'->>'start')::timestamptz<>(v_window->>'start')::timestamptz
     OR (r->'requested_window'->>'end')::timestamptz<>(v_window->>'end')::timestamptz
     OR (r->>'source_receipt_id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
     OR ((r->>'status')='succeeded'
       AND ((r->>'expires_at')::timestamptz<=(r->>'retrieved_at')::timestamptz
         OR r->>'response_hash' !~ '^[0-9a-f]{64}$'))
     OR ((r->>'status')<>'succeeded'
       AND (r->'expires_at'<>'null'::jsonb OR COALESCE(r->>'error_code','')='')) THEN
    RAISE EXCEPTION 'checkpoint must record an actual request outcome' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_reserved
  FROM public.market_source_quota_reservations
  WHERE id=(r->>'reservation_id')::uuid AND run_id=p_run_id AND provider=r->>'provider';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE='22023';
  END IF;
  v_incoming_uncertain := r->>'status'='failed'
    AND r->>'error_code'='TRANSPORT_OUTCOME_UNCERTAIN';
  IF v_incoming_uncertain AND (
      v_reserved.provider NOT IN ('gdelt','alpha_vantage','finnhub','sec_edgar','federal_register','white_house','doe','dod','eia','fred','bls','bea')
      OR jsonb_array_length(p_payload->'items')<>0
      OR r->'observed_at'<>'null'::jsonb
      OR r->'expires_at'<>'null'::jsonb
      OR r->'upstream_remaining'<>'null'::jsonb
      OR r->'response_hash'<>'null'::jsonb
      OR (r->>'returned_count')::int<>0
      OR (r->>'accepted_count')::int<>0
      OR (r->>'duplicate_count')::int<>0
      OR (r->>'dropped_count')::int<>0
  ) THEN
    RAISE EXCEPTION 'invalid provider attempt barrier' USING ERRCODE='22023';
  END IF;

  SELECT * INTO v_existing
  FROM public.market_collection_checkpoints
  WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key' FOR UPDATE;
  IF FOUND AND v_existing.payload=p_payload-'cache_key' THEN
    RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
  END IF;
  SELECT COALESCE(sum((payload->'receipt'->>'request_cost')::int),0) INTO v_used FROM (
    SELECT payload FROM public.market_collection_checkpoints WHERE run_id=p_run_id
    UNION ALL SELECT payload FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id
  ) paid WHERE payload->'receipt'->>'reservation_id'=r->>'reservation_id';
  IF v_existing.run_id IS NOT NULL THEN
    v_existing_receipt := v_existing.payload->'receipt';
    v_existing_uncertain := v_existing_receipt->>'status'='failed'
      AND v_existing_receipt->>'error_code'='TRANSPORT_OUTCOME_UNCERTAIN';
    v_same_attempt_identity := v_existing.source_receipt_id::text=r->>'source_receipt_id'
      AND v_existing_receipt->>'provider'=r->>'provider'
      AND v_existing_receipt->>'reservation_id'=r->>'reservation_id'
      AND v_existing_receipt->>'cache_key'=r->>'cache_key'
      AND v_existing_receipt->'requested_window'=r->'requested_window'
      AND v_existing_receipt->>'requested_limit'=r->>'requested_limit';
    v_valid_attempt_transition := v_existing_uncertain AND v_same_attempt_identity AND (
      (v_incoming_uncertain
       AND (r->>'request_cost')::int=(v_existing_receipt->>'request_cost')::int+1)
      OR (NOT v_incoming_uncertain
       AND (r->>'request_cost')::int=(v_existing_receipt->>'request_cost')::int)
    );
    IF v_valid_attempt_transition THEN
      IF v_used-(v_existing_receipt->>'request_cost')::int+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
        RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
      END IF;
      -- Never archive an attempt barrier: history plus its terminal row would
      -- count the same provider request twice during restart reconciliation.
      UPDATE public.market_collection_checkpoints
      SET payload=p_payload-'cache_key',created_at=statement_timestamp()
      WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
    ELSE
      IF v_existing.source_receipt_id=(r->>'source_receipt_id')::uuid
         OR (v_existing_receipt->>'expires_at')::timestamptz>statement_timestamp() THEN
        RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023';
      END IF;
      IF v_used+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
        RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
      END IF;
      INSERT INTO public.market_collection_checkpoint_history(
        run_id,cache_key,source_receipt_id,payload
      ) VALUES(
        v_existing.run_id,v_existing.cache_key,v_existing.source_receipt_id,v_existing.payload
      );
      UPDATE public.market_collection_checkpoints
      SET source_receipt_id=(r->>'source_receipt_id')::uuid,
          payload=p_payload-'cache_key',created_at=statement_timestamp()
      WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
    END IF;
  ELSE
    IF v_used+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
      RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
    END IF;
    INSERT INTO public.market_collection_checkpoints(
      run_id,cache_key,request_window,source_receipt_id,payload
    ) VALUES(
      p_run_id,p_payload->>'cache_key',v_window,
      (r->>'source_receipt_id')::uuid,p_payload-'cache_key'
    );
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
END;
$$;

REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB)
  TO service_role;


-- Preserve the private release ledger separately from Supabase's native prefix.
-- Match the protected release reconciler's existing four-column table exactly.
CREATE TABLE IF NOT EXISTS public.stock_agent_release_migration_ledger (
  path TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
  UNIQUE (version, path)
);
ALTER TABLE public.stock_agent_release_migration_ledger ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.stock_agent_release_migration_ledger
  FROM PUBLIC,anon,authenticated,stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT SELECT ON public.stock_agent_release_migration_ledger TO stock_agent_release_reader;
DROP POLICY IF EXISTS release_evidence_select ON public.stock_agent_release_migration_ledger;
CREATE POLICY release_evidence_select ON public.stock_agent_release_migration_ledger
  FOR SELECT TO stock_agent_release_reader USING (true);
