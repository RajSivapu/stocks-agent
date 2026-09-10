-- A finalized partial or failed scheduled run may receive one fresh same-day
-- retry. Successful, suppressed, running, and second-attempt runs remain
-- deduplicated so manual recovery cannot create an execution loop.
ALTER TABLE public.analysis_runs
  ADD COLUMN IF NOT EXISTS scheduled_attempt INT NOT NULL DEFAULT 1;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint
    WHERE conrelid='public.analysis_runs'::regclass
      AND conname='analysis_runs_scheduled_attempt_valid'
  ) THEN
    ALTER TABLE public.analysis_runs
      ADD CONSTRAINT analysis_runs_scheduled_attempt_valid
      CHECK (scheduled_attempt BETWEEN 1 AND 2);
  END IF;
END;
$$;

DROP INDEX IF EXISTS public.uq_analysis_runs_scheduled_slot;
CREATE UNIQUE INDEX uq_analysis_runs_scheduled_slot
  ON public.analysis_runs(scheduled_market_date,scheduled_phase,scheduled_attempt)
  WHERE scheduled_market_date IS NOT NULL AND scheduled_phase IS NOT NULL;

CREATE OR REPLACE FUNCTION public.start_market_analysis_run(
  p_request_id UUID, p_lease_token UUID, p_kind TEXT, p_market_date DATE
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_run public.analysis_runs%ROWTYPE;
  v_attempt INT := 1;
BEGIN
  IF p_kind NOT IN ('pre-market','intraday','post-market','on-demand')
     OR p_market_date IS NULL THEN
    RAISE EXCEPTION 'invalid analysis run slot' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_request
  FROM public.market_gateway_requests
  WHERE request_id=p_request_id
  FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'start_run'
     OR v_request.lease_token<>p_lease_token OR v_request.status<>'claimed' THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001';
  END IF;

  SELECT * INTO v_run
  FROM public.analysis_runs
  WHERE gateway_request_id=p_request_id;
  IF FOUND THEN
    IF v_run.kind IS DISTINCT FROM p_kind
       OR COALESCE(v_run.scheduled_market_date,p_market_date) IS DISTINCT FROM p_market_date THEN
      RAISE EXCEPTION 'run identity mismatch' USING ERRCODE='22023';
    END IF;
    UPDATE public.market_gateway_requests SET run_id=v_run.id
    WHERE request_id=p_request_id;
    RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
  END IF;

  IF p_kind <> 'on-demand' THEN
    PERFORM pg_advisory_xact_lock(
      hashtextextended('scheduled-analysis-run:'||p_market_date::text||':'||p_kind,0)
    );
    SELECT * INTO v_run
    FROM public.analysis_runs
    WHERE scheduled_market_date=p_market_date AND scheduled_phase=p_kind
    ORDER BY scheduled_attempt DESC
    LIMIT 1
    FOR UPDATE;
    IF FOUND THEN
      IF v_run.finished_at IS NOT NULL
         AND v_run.status IN ('partial','failed')
         AND v_run.scheduled_attempt=1 THEN
        v_attempt:=2;
      ELSE
        UPDATE public.market_gateway_requests SET run_id=v_run.id
        WHERE request_id=p_request_id;
        RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
      END IF;
    END IF;
    INSERT INTO public.analysis_runs(
      kind,status,gateway_request_id,scheduled_market_date,scheduled_phase,scheduled_attempt
    ) VALUES (
      p_kind,'running',p_request_id,p_market_date,p_kind,v_attempt
    ) RETURNING * INTO v_run;
  ELSE
    INSERT INTO public.analysis_runs(kind,status,gateway_request_id)
    VALUES (p_kind,'running',p_request_id)
    RETURNING * INTO v_run;
  END IF;
  UPDATE public.market_gateway_requests SET run_id=v_run.id
  WHERE request_id=p_request_id;
  RETURN jsonb_build_object('run_id',v_run.id,'duplicate',false);
END;
$$;

REVOKE ALL ON FUNCTION public.start_market_analysis_run(UUID,UUID,TEXT,DATE)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_analysis_run(UUID,UUID,TEXT,DATE)
  TO service_role;
