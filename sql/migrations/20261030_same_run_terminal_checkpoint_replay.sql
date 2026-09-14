BEGIN;

-- Cache TTL controls reuse by later runs. It must not erase a paid terminal
-- outcome when the same interrupted run resumes after that TTL has elapsed.
CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,p_phase TEXT,p_market_date DATE,p_policy_version INT,
  p_reservation_plan JSONB,p_request_window JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_result JSONB;
  v_window JSONB;
  v_terminal_entries JSONB;
  v_usage JSONB;
BEGIN
  PERFORM 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'analysis run is not running' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.market_intelligence_quote_attempts WHERE run_id=p_run_id AND status='pending') THEN
    RAISE EXCEPTION 'quote collection outcome uncertain' USING ERRCODE='55000'; END IF;
  IF p_request_window IS NULL OR jsonb_typeof(p_request_window)<>'object'
     OR NOT(p_request_window ?& ARRAY['start','end','timezone','market_date','phase'])
     OR (p_request_window-ARRAY['start','end','timezone','market_date','phase'])<>'{}'::jsonb
     OR p_request_window->>'timezone'<>'America/Chicago' OR p_request_window->>'phase'<>p_phase
     OR p_request_window->>'market_date'<>p_market_date::text
     OR (p_request_window->>'start')::timestamptz >= (p_request_window->>'end')::timestamptz THEN
    RAISE EXCEPTION 'invalid intelligence request window' USING ERRCODE='22023'; END IF;
  v_result:=public.start_market_intelligence_run(
    p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan
  );
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL THEN
    UPDATE public.market_intelligence_runs SET request_window=p_request_window
      WHERE id=p_run_id RETURNING request_window INTO v_window;
  END IF;
  SELECT COALESCE(
    jsonb_agg(payload||jsonb_build_object('cache_key',cache_key) ORDER BY cache_key),
    '[]'::jsonb
  ) INTO v_terminal_entries
  FROM public.market_collection_checkpoints
  WHERE run_id=p_run_id;
  SELECT COALESCE(jsonb_object_agg(reservation_id,used),'{}'::jsonb) INTO v_usage FROM (
    SELECT payload->'receipt'->>'reservation_id' reservation_id,
      sum((payload->'receipt'->>'request_cost')::int) used
    FROM (
      SELECT payload FROM public.market_collection_checkpoints WHERE run_id=p_run_id
      UNION ALL
      SELECT payload FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id
    ) paid GROUP BY 1
  ) usage;
  RETURN jsonb_build_object(
    'run_id',p_run_id,
    'reservation_ids',v_result->'reservation_ids',
    'cache_entries','[]'::jsonb,
    'terminal_checkpoint_entries',v_terminal_entries,
    'request_window',v_window,
    'reservation_usage',v_usage,
    'duplicate',(v_result->>'duplicate')::boolean
  );
END; $$;

REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB)
  TO service_role;

COMMIT;
