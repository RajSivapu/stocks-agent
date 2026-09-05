-- Final controller override: bind collection to an existing running analysis run,
-- retain replaced checkpoints, and make cache-hit predecessor provenance durable.
CREATE TABLE IF NOT EXISTS public.market_collection_checkpoint_history (
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  cache_key TEXT NOT NULL, source_receipt_id UUID NOT NULL,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536),
  replaced_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY(run_id,cache_key,source_receipt_id)
);
ALTER TABLE public.market_collection_checkpoint_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_collection_checkpoint_history FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,p_phase TEXT,p_market_date DATE,p_policy_version INT,p_reservation_plan JSONB,p_request_window JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_window JSONB; v_entries JSONB;
BEGIN
  PERFORM 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'analysis run is not running' USING ERRCODE='22023'; END IF;
  IF jsonb_typeof(p_request_window)<>'object' OR p_request_window->>'timezone'<>'America/Chicago' OR p_request_window->>'phase'<>p_phase OR p_request_window->>'market_date'<>p_market_date::text OR (p_request_window->>'start')::timestamptz >= (p_request_window->>'end')::timestamptz THEN RAISE EXCEPTION 'invalid intelligence request window' USING ERRCODE='22023'; END IF;
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NOT NULL AND v_window IS DISTINCT FROM p_request_window THEN RAISE EXCEPTION 'intelligence run window mismatch' USING ERRCODE='22023'; END IF;
  v_result:=public.start_market_intelligence_run(p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan);
  UPDATE public.market_intelligence_runs SET request_window=COALESCE(request_window,p_request_window) WHERE id=p_run_id RETURNING request_window INTO v_window;
  SELECT COALESCE(jsonb_agg(payload||jsonb_build_object('cache_key',cache_key) ORDER BY created_at),'[]'::jsonb) INTO v_entries FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND (payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp();
  RETURN jsonb_build_object('run_id',p_run_id,'reservation_ids',v_result->'reservation_ids','cache_entries',v_entries,'request_window',v_window,'duplicate',(v_result->>'duplicate')::boolean);
END; $$;

CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(p_run_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_window JSONB; v_existing public.market_collection_checkpoints%ROWTYPE; v_receipt JSONB;
BEGIN
  IF jsonb_typeof(p_payload)<>'object' OR NOT(p_payload ?& ARRAY['cache_key','receipt','items']) OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb OR jsonb_typeof(p_payload->'items')<>'array' OR jsonb_array_length(p_payload->'items')>50 THEN RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023'; END IF;
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running') THEN RAISE EXCEPTION 'analysis run is not running' USING ERRCODE='22023'; END IF;
  v_receipt:=p_payload->'receipt';
  IF (v_receipt->>'request_cost')::int<1 OR (v_receipt->>'source_receipt_id') !~ '^[0-9a-f-]{36}$' THEN RAISE EXCEPTION 'checkpoint must record an actual attempt' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_existing FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key' FOR UPDATE;
  IF FOUND AND v_existing.payload IS DISTINCT FROM p_payload-'cache_key' THEN
    IF (v_existing.payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp() THEN RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023'; END IF;
    INSERT INTO public.market_collection_checkpoint_history(run_id,cache_key,source_receipt_id,payload) VALUES(v_existing.run_id,v_existing.cache_key,v_existing.source_receipt_id,v_existing.payload) ON CONFLICT DO NOTHING;
    UPDATE public.market_collection_checkpoints SET source_receipt_id=(v_receipt->>'source_receipt_id')::uuid,payload=p_payload-'cache_key',created_at=statement_timestamp() WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
  ELSIF NOT FOUND THEN
    INSERT INTO public.market_collection_checkpoints(run_id,cache_key,request_window,source_receipt_id,payload) VALUES(p_run_id,p_payload->>'cache_key',v_window,(v_receipt->>'source_receipt_id')::uuid,p_payload-'cache_key');
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
END; $$;
REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;
