-- Follow-up closure for the deployed scheduled lifecycle. Schedule changes are
-- append-only by phase/effective date; report receipts are bound through the
-- immutable report identity returned by the intentionally runless request.
ALTER TABLE public.market_scheduled_phase_deadlines
  DROP CONSTRAINT IF EXISTS market_scheduled_phase_deadlines_pkey;
ALTER TABLE public.market_scheduled_phase_deadlines
  ADD CONSTRAINT market_scheduled_phase_deadlines_pkey PRIMARY KEY (phase, effective_on);

INSERT INTO public.market_scheduled_phase_deadlines(phase,deadline_local,grace_minutes,effective_on)
VALUES
  ('pre-market','06:30',15,timezone('America/Chicago', statement_timestamp())::date),
  ('intraday','12:00',15,timezone('America/Chicago', statement_timestamp())::date),
  ('post-market','15:10',15,timezone('America/Chicago', statement_timestamp())::date)
ON CONFLICT (phase,effective_on) DO NOTHING;

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
    IF NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END
      WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed'
        AND jsonb_typeof(q.response)='object'
        AND q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND q.response->>'report_hash'=r.report_hash
        AND q.response->>'rendered_hash'=r.rendered_hash
        AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
        AND ((v_run.scheduled_phase='pre-market' AND r.kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND r.kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND r.kind IN ('weekly','monthly','theme','urgent')))
    ) THEN
      RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END
      JOIN public.market_report_publications p ON p.report_id=r.id
      WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed'
        AND jsonb_typeof(q.response)='object'
        AND q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND q.response->>'report_hash'=r.report_hash
        AND q.response->>'rendered_hash'=r.rendered_hash
        AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
        AND ((v_run.scheduled_phase='pre-market' AND r.kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND r.kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND r.kind IN ('weekly','monthly','theme','urgent')))
        AND p.status IN ('delivered','suppressed')
    ) THEN
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
    UNION ALL SELECT p.status FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id
      WHERE r.run_id=p_run_id
  ) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb) INTO v_ids FROM (
    SELECT value AS message_id FROM public.market_publications p CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE p.run_id=p_run_id
    UNION ALL SELECT value AS message_id FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE r.run_id=p_run_id
  ) message_ids;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM public.market_gateway_requests WHERE run_id=p_run_id AND status='failed')
                   OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain'] THEN 'partial'
              WHEN NOT EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND p.status='delivered')
                   AND EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND p.status='suppressed') THEN 'suppressed'
              ELSE 'completed' END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object('run_id',p_run_id,'status',v_status,'write_counts',v_counts,'publication_statuses',v_statuses,'telegram_message_ids',v_ids);
END;
$$;

CREATE OR REPLACE FUNCTION public.read_overdue_scheduled_market_phases(
  p_now TIMESTAMPTZ DEFAULT statement_timestamp()
) RETURNS TABLE(market_date DATE, phase TEXT, deadline_at TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_policy JSONB; v_today DATE := timezone('America/Chicago',p_now)::date; v_year TEXT := extract(year FROM v_today)::text;
BEGIN
  SELECT config INTO v_policy FROM public.market_policy_config WHERE active;
  IF NOT FOUND OR jsonb_typeof(v_policy->'nyse_holidays') IS DISTINCT FROM 'array'
     OR COALESCE(v_policy->>'market_calendar_year','') !~ '^[0-9]{4}$'
     OR v_policy->>'market_calendar_year' IS DISTINCT FROM v_year
     OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_policy->'nyse_holidays') AS holiday(value)
                WHERE jsonb_typeof(holiday.value)<>'string'
                   OR holiday.value #>> '{}' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                   OR left(holiday.value #>> '{}',4)<>v_year
                   OR to_char(to_date(holiday.value #>> '{}','FXYYYY-MM-DD'),'YYYY-MM-DD')<>holiday.value #>> '{}') THEN
    RAISE EXCEPTION 'calendar coverage missing' USING ERRCODE='22023';
  END IF;
  RETURN QUERY
    SELECT slot_days.market_date, scheduled.phase,
           (slot_days.market_date::timestamp + deadline.deadline_local + make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago'
    FROM generate_series((SELECT min(effective_on) FROM public.market_scheduled_phase_deadlines),v_today,interval '1 day') AS days(value)
    CROSS JOIN LATERAL (SELECT days.value::date AS market_date) slot_days
    CROSS JOIN (VALUES ('pre-market'::text),('intraday'::text),('post-market'::text)) AS scheduled(phase)
    JOIN LATERAL (
      SELECT deadline_local,grace_minutes FROM public.market_scheduled_phase_deadlines deadline
      WHERE deadline.phase=scheduled.phase AND deadline.effective_on<=slot_days.market_date
      ORDER BY deadline.effective_on DESC LIMIT 1
    ) deadline ON true
    WHERE extract(isodow FROM slot_days.market_date) BETWEEN 1 AND 5
      AND ((slot_days.market_date::timestamp + deadline.deadline_local + make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago') < p_now
      AND NOT (v_policy->'nyse_holidays' ? slot_days.market_date::text)
      AND NOT EXISTS (SELECT 1 FROM public.analysis_runs run WHERE run.scheduled_market_date=slot_days.market_date
                      AND run.scheduled_phase=scheduled.phase AND run.status IN ('completed','suppressed'))
    ORDER BY slot_days.market_date, scheduled.phase;
END;
$$;

REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID),public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) TO stock_agent_dashboard;
