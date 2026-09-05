-- Durable, restart-safe controller boundary.  This intentionally overrides the
-- deployed 20260916 wrappers instead of editing historical migrations.
ALTER TABLE public.market_intelligence_runs
  ADD COLUMN IF NOT EXISTS request_window JSONB;
ALTER TABLE public.market_intelligence_runs
  DROP CONSTRAINT IF EXISTS market_intelligence_runs_request_window_check;
ALTER TABLE public.market_intelligence_runs
  ADD CONSTRAINT market_intelligence_runs_request_window_check CHECK (
    request_window IS NULL OR (
      jsonb_typeof(request_window)='object'
      AND request_window ?& ARRAY['start','end','timezone','market_date','phase']
      AND (request_window - ARRAY['start','end','timezone','market_date','phase'])='{}'::jsonb
      AND request_window->>'timezone'='America/Chicago'
      AND request_window->>'phase'=phase
      AND request_window->>'market_date'=market_date::text
      AND (request_window->>'start')::timestamptz < (request_window->>'end')::timestamptz
    )
  );

ALTER TABLE public.market_collection_checkpoints
  DROP CONSTRAINT IF EXISTS market_collection_checkpoints_source_receipt_id_fkey;
ALTER TABLE public.market_collection_checkpoints
  DROP CONSTRAINT IF EXISTS market_collection_checkpoints_payload_check;
ALTER TABLE public.market_collection_checkpoints
  ADD CONSTRAINT market_collection_checkpoints_payload_check CHECK (
    jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536
    AND payload ?& ARRAY['receipt','items']
    AND (payload - ARRAY['receipt','items'])='{}'::jsonb
    AND jsonb_typeof(payload->'receipt')='object'
    AND jsonb_typeof(payload->'items')='array' AND jsonb_array_length(payload->'items')<=50
  );

CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,p_phase TEXT,p_market_date DATE,p_policy_version INT,
  p_reservation_plan JSONB,p_request_window JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_existing public.market_intelligence_runs%ROWTYPE; v_result JSONB; v_entries JSONB;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_request_window)<>'object'
     OR NOT (p_request_window ?& ARRAY['start','end','timezone','market_date','phase'])
     OR (p_request_window-ARRAY['start','end','timezone','market_date','phase'])<>'{}'::jsonb
     OR p_request_window->>'timezone'<>'America/Chicago' OR p_request_window->>'phase'<>p_phase
     OR p_request_window->>'market_date'<>p_market_date::text
     OR (p_request_window->>'start')::timestamptz >= (p_request_window->>'end')::timestamptz THEN
    RAISE EXCEPTION 'invalid intelligence request window' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('market-intelligence-run:'||p_run_id::text,0));
  SELECT * INTO v_existing FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF FOUND AND v_existing.request_window IS NOT NULL AND v_existing.request_window IS DISTINCT FROM p_request_window THEN
    RAISE EXCEPTION 'intelligence run window mismatch' USING ERRCODE='22023';
  END IF;
  v_result:=public.start_market_intelligence_run(p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan);
  UPDATE public.market_intelligence_runs SET request_window=COALESCE(request_window,p_request_window)
    WHERE id=p_run_id RETURNING request_window INTO p_request_window;
  SELECT COALESCE(jsonb_agg(payload||jsonb_build_object('cache_key',cache_key) ORDER BY created_at),'[]'::jsonb)
    INTO v_entries FROM public.market_collection_checkpoints
    WHERE run_id=p_run_id AND (payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp();
  RETURN jsonb_build_object('run_id',p_run_id,'reservation_ids',v_result->'reservation_ids',
    'cache_entries',v_entries,'request_window',p_request_window,'duplicate',(v_result->>'duplicate')::boolean);
END; $$;

CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(
  p_run_id UUID,p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_run public.market_intelligence_runs%ROWTYPE; v_receipt JSONB; v_key TEXT;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR NOT (p_payload ?& ARRAY['cache_key','receipt','items'])
     OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb
     OR char_length(p_payload->>'cache_key') NOT BETWEEN 1 AND 512
     OR jsonb_typeof(p_payload->'receipt')<>'object' OR jsonb_typeof(p_payload->'items')<>'array'
     OR jsonb_array_length(p_payload->'items')>50 OR octet_length(p_payload::text)>65536 THEN
    RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_run FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS (SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  v_receipt:=p_payload->'receipt'; v_key:=p_payload->>'cache_key';
  IF v_receipt->>'status'<>'succeeded' OR (v_receipt->>'request_cost')::int<1
     OR (v_receipt->>'source_receipt_id') !~ '^[0-9a-f-]{36}$'
     OR (v_receipt->>'expires_at')::timestamptz <= (v_receipt->>'retrieved_at')::timestamptz THEN
    RAISE EXCEPTION 'checkpoint must contain an actual successful request' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_collection_checkpoints(run_id,cache_key,request_window,source_receipt_id,payload)
  VALUES(p_run_id,v_key,v_run.request_window,(v_receipt->>'source_receipt_id')::uuid,p_payload- 'cache_key')
  ON CONFLICT(run_id,cache_key) DO UPDATE SET payload=EXCLUDED.payload
    WHERE public.market_collection_checkpoints.payload IS NOT DISTINCT FROM EXCLUDED.payload;
  IF NOT FOUND AND EXISTS(SELECT 1 FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND cache_key=v_key) THEN
    RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023';
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',v_key);
END; $$;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(p_run_id UUID,p_completion_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result_row JSONB;
BEGIN
  IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs run
      JOIN public.market_intelligence_run_events event ON event.run_id=run.id AND event.status='started'
      WHERE run.id=p_run_id) THEN RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.market_collection_checkpoints checkpoint
      WHERE checkpoint.run_id=p_run_id AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(p_payload->'receipts') receipt
        WHERE receipt->>'id'=checkpoint.source_receipt_id::text)) THEN
    RAISE EXCEPTION 'final packet omits collection checkpoint' USING ERRCODE='22023';
  END IF;
  result_row:=public.record_market_intelligence_provider_v2(p_run_id,p_completion_id,p_payload);
  INSERT INTO public.market_run_source_item_provenance(run_item_id,run_id,source_item_id,source_receipt_id,provider,request_url,retrieved_at,reporting_at,entity_ids,security_ids,discovery_status)
  SELECT (value->>'run_item_id')::uuid,p_run_id,(value->>'id')::uuid,(value->>'receipt_id')::uuid,value->>'provider',value->>'request_url',(value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,value->'entity_ids',value->'security_ids',value->>'discovery_status' FROM jsonb_array_elements(p_payload->'items') ON CONFLICT(run_item_id) DO NOTHING;
  RETURN result_row;
END; $$;

REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;
