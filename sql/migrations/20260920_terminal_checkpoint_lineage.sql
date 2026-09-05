-- Terminal cache-hit lineage resolves against durable checkpoints, not a source-receipt FK
-- that cannot exist before the resumed terminal transaction.
CREATE TABLE IF NOT EXISTS public.market_checkpoint_receipt_lineage (
  cache_receipt_id UUID PRIMARY KEY REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  cache_predecessor_receipt_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
    AND table_name='market_checkpoint_receipt_lineage' AND column_name='checkpoint_receipt_id') THEN
    ALTER TABLE public.market_checkpoint_receipt_lineage
      RENAME COLUMN checkpoint_receipt_id TO cache_predecessor_receipt_id;
  END IF;
END; $$;
ALTER TABLE public.market_checkpoint_receipt_lineage ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_checkpoint_receipt_lineage FROM PUBLIC,anon,authenticated;

-- The cache receipt is inserted by the terminal recorder before its durable
-- checkpoint lineage row can exist.  Keep the receipt table honest about cost
-- and let the controller below validate predecessor provenance atomically.
ALTER TABLE public.market_source_receipts
  DROP CONSTRAINT IF EXISTS market_source_receipts_cache_lineage_check;
ALTER TABLE public.market_source_receipts
  ADD CONSTRAINT market_source_receipts_cache_lineage_check CHECK (
    (status = 'cache_hit' AND request_cost = 0)
    OR (status <> 'cache_hit' AND cache_predecessor_receipt_id IS NULL)
  );

-- Final controller guard: checkpoint failures are durable actual attempts too.
-- A run can only be started for a currently-running analysis row; checkpointing
-- also requires the corresponding intelligence event stream to remain open.
CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,p_phase TEXT,p_market_date DATE,p_policy_version INT,p_reservation_plan JSONB,p_request_window JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_window JSONB; v_entries JSONB;
BEGIN
  PERFORM 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'analysis run is not running' USING ERRCODE='22023'; END IF;
  IF jsonb_typeof(p_request_window)<>'object' OR p_request_window->>'timezone'<>'America/Chicago'
     OR p_request_window->>'phase'<>p_phase OR p_request_window->>'market_date'<>p_market_date::text
     OR (p_request_window->>'start')::timestamptz >= (p_request_window->>'end')::timestamptz THEN
    RAISE EXCEPTION 'invalid intelligence request window' USING ERRCODE='22023'; END IF;
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NOT NULL AND v_window IS DISTINCT FROM p_request_window THEN
    RAISE EXCEPTION 'intelligence run window mismatch' USING ERRCODE='22023'; END IF;
  v_result:=public.start_market_intelligence_run(p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan);
  UPDATE public.market_intelligence_runs SET request_window=COALESCE(request_window,p_request_window)
    WHERE id=p_run_id RETURNING request_window INTO v_window;
  SELECT COALESCE(jsonb_agg(payload||jsonb_build_object('cache_key',cache_key) ORDER BY created_at),'[]'::jsonb)
    INTO v_entries FROM public.market_collection_checkpoints
    WHERE run_id=p_run_id AND ((payload->'receipt'->>'status')='failed'
      OR (payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp());
  RETURN jsonb_build_object('run_id',p_run_id,'reservation_ids',v_result->'reservation_ids',
    'cache_entries',v_entries,'request_window',v_window,'duplicate',(v_result->>'duplicate')::boolean);
END; $$;

CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(p_run_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_window JSONB; v_existing public.market_collection_checkpoints%ROWTYPE; v_receipt JSONB;
BEGIN
  IF jsonb_typeof(p_payload)<>'object' OR NOT(p_payload ?& ARRAY['cache_key','receipt','items'])
     OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'items')<>'array' OR jsonb_array_length(p_payload->'items')>50 THEN
    RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023'; END IF;
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  v_receipt:=p_payload->'receipt';
  IF v_receipt->>'status' NOT IN ('succeeded','failed') OR (v_receipt->>'request_cost')::int<1
     OR (v_receipt->>'source_receipt_id') !~ '^[0-9a-f-]{36}$'
     OR ((v_receipt->>'status')='succeeded' AND (v_receipt->>'expires_at')::timestamptz <= (v_receipt->>'retrieved_at')::timestamptz)
     OR ((v_receipt->>'status')='failed' AND COALESCE(v_receipt->>'error_code','')='') THEN
    RAISE EXCEPTION 'checkpoint must record an actual request outcome' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_existing FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key' FOR UPDATE;
  IF FOUND AND v_existing.payload IS DISTINCT FROM p_payload-'cache_key' THEN
    IF (v_existing.payload->'receipt'->>'status')='failed' OR (v_existing.payload->'receipt'->>'expires_at')::timestamptz<=statement_timestamp() THEN
      INSERT INTO public.market_collection_checkpoint_history(run_id,cache_key,source_receipt_id,payload)
        VALUES(v_existing.run_id,v_existing.cache_key,v_existing.source_receipt_id,v_existing.payload) ON CONFLICT DO NOTHING;
      UPDATE public.market_collection_checkpoints SET source_receipt_id=(v_receipt->>'source_receipt_id')::uuid,
        payload=p_payload-'cache_key',created_at=statement_timestamp() WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
    ELSE RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023'; END IF;
  ELSIF NOT FOUND THEN
    INSERT INTO public.market_collection_checkpoints(run_id,cache_key,request_window,source_receipt_id,payload)
      VALUES(p_run_id,p_payload->>'cache_key',v_window,(v_receipt->>'source_receipt_id')::uuid,p_payload-'cache_key');
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
END; $$;
REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(p_run_id UUID,p_completion_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_effective JSONB; result_row JSONB; v_receipt JSONB;
BEGIN
  -- The provider recorder owns exact completion-idempotency.  On a response
  -- loss it returns the stored packet receipt before the run-state guard.
  SELECT jsonb_set(p_payload,'{receipts}',COALESCE(jsonb_agg(CASE WHEN value->>'status'='cache_hit' THEN jsonb_set(value,'{cache_predecessor_receipt_id}','null'::jsonb) ELSE value END),'[]'::jsonb)) INTO v_effective FROM jsonb_array_elements(p_payload->'receipts');
  IF EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE id=p_completion_id) THEN
    RETURN public.record_market_intelligence_provider_v2(p_run_id,p_completion_id,v_effective);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.market_collection_checkpoints checkpoint WHERE checkpoint.run_id=p_run_id AND NOT EXISTS(
      SELECT 1 FROM jsonb_array_elements(p_payload->'receipts') receipt
      WHERE receipt->>'id'=checkpoint.source_receipt_id::text
         OR (receipt->>'status'='cache_hit' AND receipt->>'cache_predecessor_receipt_id'=checkpoint.source_receipt_id::text))) THEN
    RAISE EXCEPTION 'final packet omits collection checkpoint' USING ERRCODE='22023'; END IF;
  FOR v_receipt IN SELECT value FROM jsonb_array_elements(p_payload->'receipts') LOOP
    IF v_receipt->>'status'='cache_hit' AND NOT EXISTS(
      SELECT 1 FROM public.market_collection_checkpoints checkpoint WHERE checkpoint.run_id=p_run_id AND checkpoint.source_receipt_id::text=v_receipt->>'cache_predecessor_receipt_id'
        AND checkpoint.payload->'receipt'->>'provider'=v_receipt->>'provider'
        AND checkpoint.payload->'receipt'->>'cache_key'=v_receipt->>'cache_key'
        AND checkpoint.payload->'receipt'->'requested_window'=v_receipt->'requested_window'
        AND checkpoint.payload->'receipt'->>'response_hash'=v_receipt->>'response_hash'
      UNION ALL SELECT 1 FROM public.market_collection_checkpoint_history history WHERE history.run_id=p_run_id AND history.source_receipt_id::text=v_receipt->>'cache_predecessor_receipt_id'
        AND history.payload->'receipt'->>'provider'=v_receipt->>'provider'
        AND history.payload->'receipt'->>'cache_key'=v_receipt->>'cache_key'
        AND history.payload->'receipt'->'requested_window'=v_receipt->'requested_window'
        AND history.payload->'receipt'->>'response_hash'=v_receipt->>'response_hash') THEN
      RAISE EXCEPTION 'cache predecessor checkpoint unavailable' USING ERRCODE='22023'; END IF;
  END LOOP;
  result_row:=public.record_market_intelligence_provider_v2(p_run_id,p_completion_id,v_effective);
  INSERT INTO public.market_run_source_item_provenance(run_item_id,run_id,source_item_id,source_receipt_id,provider,request_url,retrieved_at,reporting_at,entity_ids,security_ids,discovery_status)
  SELECT (value->>'run_item_id')::uuid,p_run_id,(value->>'id')::uuid,(value->>'receipt_id')::uuid,value->>'provider',value->>'request_url',(value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,value->'entity_ids',value->'security_ids',value->>'discovery_status'
  FROM jsonb_array_elements(p_payload->'items') value ON CONFLICT(run_item_id) DO NOTHING;
  INSERT INTO public.market_checkpoint_receipt_lineage(cache_receipt_id,run_id,cache_predecessor_receipt_id)
  SELECT (value->>'id')::uuid,p_run_id,(value->>'cache_predecessor_receipt_id')::uuid FROM jsonb_array_elements(p_payload->'receipts') value WHERE value->>'status'='cache_hit' ON CONFLICT(cache_receipt_id) DO NOTHING;
  RETURN result_row;
END; $$;
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID,UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID,UUID,JSONB) TO service_role;
