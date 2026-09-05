-- Scheduled lifecycle closure. Additive after the existing 20260922 controller contract.
-- A scheduled run is identified independently from its gateway request so concurrent
-- request IDs for one market-date/phase converge on one durable run.
ALTER TABLE public.analysis_runs
  ADD COLUMN IF NOT EXISTS scheduled_market_date DATE,
  ADD COLUMN IF NOT EXISTS scheduled_phase TEXT;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                 WHERE conrelid='public.analysis_runs'::regclass
                   AND conname='analysis_runs_scheduled_phase_valid') THEN
    ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_scheduled_phase_valid
      CHECK (scheduled_phase IS NULL OR scheduled_phase IN ('pre-market','intraday','post-market'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                 WHERE conrelid='public.analysis_runs'::regclass
                   AND conname='analysis_runs_scheduled_slot_complete') THEN
    ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_scheduled_slot_complete
      CHECK ((scheduled_market_date IS NULL) = (scheduled_phase IS NULL));
  END IF;
END;
$$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_runs_scheduled_slot
  ON public.analysis_runs(scheduled_market_date, scheduled_phase)
  WHERE scheduled_market_date IS NOT NULL AND scheduled_phase IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.market_scheduled_phase_deadlines (
  phase TEXT PRIMARY KEY CHECK (phase IN ('pre-market','intraday','post-market')),
  deadline_local TIME NOT NULL,
  grace_minutes INT NOT NULL DEFAULT 15 CHECK (grace_minutes BETWEEN 0 AND 60),
  effective_on DATE NOT NULL DEFAULT (timezone('America/Chicago', statement_timestamp())::date)
);
ALTER TABLE public.market_scheduled_phase_deadlines
  ADD COLUMN IF NOT EXISTS grace_minutes INT NOT NULL DEFAULT 15 CHECK (grace_minutes BETWEEN 0 AND 60);
ALTER TABLE public.market_scheduled_phase_deadlines ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_scheduled_phase_deadlines FROM PUBLIC, anon, authenticated;
INSERT INTO public.market_scheduled_phase_deadlines(phase,deadline_local,grace_minutes)
VALUES ('pre-market','06:30',15),('intraday','12:00',15),('post-market','15:10',15)
ON CONFLICT (phase) DO UPDATE SET deadline_local=EXCLUDED.deadline_local,
  grace_minutes=EXCLUDED.grace_minutes;

CREATE OR REPLACE FUNCTION public.start_market_analysis_run(
  p_request_id UUID, p_lease_token UUID, p_kind TEXT, p_market_date DATE
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_run public.analysis_runs%ROWTYPE;
BEGIN
  IF p_kind NOT IN ('pre-market','intraday','post-market','on-demand') OR p_market_date IS NULL THEN
    RAISE EXCEPTION 'invalid analysis run slot' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'start_run' OR v_request.lease_token<>p_lease_token
     OR v_request.status<>'claimed' THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001';
  END IF;
  SELECT * INTO v_run FROM public.analysis_runs WHERE gateway_request_id=p_request_id;
  IF FOUND THEN
    IF v_run.kind IS DISTINCT FROM p_kind
       OR COALESCE(v_run.scheduled_market_date,p_market_date) IS DISTINCT FROM p_market_date THEN
      RAISE EXCEPTION 'run identity mismatch' USING ERRCODE='22023';
    END IF;
    UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id;
    RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
  END IF;
  IF p_kind <> 'on-demand' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('scheduled-analysis-run:'||p_market_date::text||':'||p_kind,0));
    SELECT * INTO v_run FROM public.analysis_runs
      WHERE scheduled_market_date=p_market_date AND scheduled_phase=p_kind FOR UPDATE;
    IF FOUND THEN
      UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id;
      RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
    END IF;
    INSERT INTO public.analysis_runs(kind,status,gateway_request_id,scheduled_market_date,scheduled_phase)
    VALUES (p_kind,'running',p_request_id,p_market_date,p_kind) RETURNING * INTO v_run;
  ELSE
    INSERT INTO public.analysis_runs(kind,status,gateway_request_id)
    VALUES (p_kind,'running',p_request_id) RETURNING * INTO v_run;
  END IF;
  UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id;
  RETURN jsonb_build_object('run_id',v_run.id,'duplicate',false);
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_run public.analysis_runs%ROWTYPE; v_counts JSONB; v_statuses JSONB; v_ids JSONB; v_status TEXT;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM public.market_intelligence_runs i
                   WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase
                     AND i.market_date=v_run.scheduled_market_date)
       OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_run_events e
                      WHERE e.run_id=p_run_id AND e.status='completed')
       OR NOT EXISTS (SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id)
       OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id) THEN
      RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id) THEN
      RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q
                   WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish' AND q.status='completed')
       OR NOT EXISTS (SELECT 1 FROM public.market_publications p
                      WHERE p.run_id=p_run_id AND p.market_date=v_run.scheduled_market_date
                        AND p.phase=v_run.scheduled_phase AND p.status='suppressed') THEN
      RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q
                   WHERE q.run_id=p_run_id AND q.operation='record_report' AND q.status='completed')
       OR NOT EXISTS (SELECT 1 FROM public.market_reports r
                      WHERE r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date) THEN
      RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_reports r
                   JOIN public.market_report_publications p ON p.report_id=r.id
                   WHERE r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
                     AND p.status IN ('delivered','suppressed')) THEN
      RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023';
    END IF;
  END IF;
  SELECT jsonb_build_object(
    'evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),
    'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),
    'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),
    'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id)
  ) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb) INTO v_statuses FROM (
    SELECT status FROM public.market_publications WHERE run_id=p_run_id
    UNION ALL
    SELECT p.status FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id
     WHERE r.run_id=p_run_id
  ) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb) INTO v_ids FROM (
    SELECT value AS message_id FROM public.market_publications p
      CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE p.run_id=p_run_id
    UNION ALL
    SELECT value AS message_id FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id
      CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE r.run_id=p_run_id
  ) message_ids;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM public.market_gateway_requests
                           WHERE run_id=p_run_id AND status='failed')
                   OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain']
              THEN 'partial'
              WHEN NOT EXISTS(SELECT 1 FROM public.market_reports r
                              JOIN public.market_report_publications p ON p.report_id=r.id
                              WHERE r.run_id=p_run_id AND p.status='delivered')
                   AND EXISTS(SELECT 1 FROM public.market_reports r
                              JOIN public.market_report_publications p ON p.report_id=r.id
                              WHERE r.run_id=p_run_id AND p.status='suppressed')
              THEN 'suppressed' ELSE 'completed' END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),
    write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object('run_id',p_run_id,'status',v_status,'write_counts',v_counts,
    'publication_statuses',v_statuses,'telegram_message_ids',v_ids);
END;
$$;

CREATE OR REPLACE FUNCTION public.read_overdue_scheduled_market_phases(
  p_now TIMESTAMPTZ DEFAULT statement_timestamp()
) RETURNS TABLE(market_date DATE, phase TEXT, deadline_at TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_policy JSONB; v_today DATE := timezone('America/Chicago',p_now)::date;
BEGIN
  SELECT config INTO v_policy FROM public.market_policy_config WHERE active;
  IF NOT FOUND OR COALESCE((v_policy->>'market_calendar_year')::int,0) <> extract(year FROM v_today) THEN
    RAISE EXCEPTION 'calendar coverage missing' USING ERRCODE='22023';
  END IF;
  RETURN QUERY
    SELECT slot_days.market_date, deadline.phase,
           (slot_days.market_date::timestamp + deadline.deadline_local + make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago'
    FROM generate_series((SELECT min(effective_on) FROM public.market_scheduled_phase_deadlines),v_today,interval '1 day') AS days(value)
    CROSS JOIN LATERAL (SELECT days.value::date AS market_date) slot_days
    JOIN public.market_scheduled_phase_deadlines deadline ON deadline.effective_on<=slot_days.market_date
    WHERE extract(isodow FROM slot_days.market_date) BETWEEN 1 AND 5
      AND ((slot_days.market_date::timestamp + deadline.deadline_local + make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago') < p_now
      AND NOT (v_policy->'nyse_holidays' ? slot_days.market_date::text)
      AND NOT EXISTS (SELECT 1 FROM public.analysis_runs run WHERE run.scheduled_market_date=slot_days.market_date
                      AND run.scheduled_phase=deadline.phase AND run.status IN ('completed','suppressed'))
    ORDER BY slot_days.market_date, deadline.phase;
END;
$$;

DROP FUNCTION IF EXISTS public.start_market_analysis_run(UUID, UUID, TEXT);
REVOKE ALL ON FUNCTION public.start_market_analysis_run(UUID,UUID,TEXT,DATE) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_analysis_run(UUID,UUID,TEXT,DATE) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) TO stock_agent_dashboard;
