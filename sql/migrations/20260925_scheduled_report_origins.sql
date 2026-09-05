-- A report delivery may promote its final kind to urgent or intraday. Keep the
-- scheduled request's original deterministic report identity separately so the
-- lifecycle can attest to its scheduled origin without trusting a rewritten kind.
CREATE TABLE IF NOT EXISTS public.market_report_request_origins (
  request_id UUID PRIMARY KEY REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  scheduled_phase TEXT NOT NULL CHECK (scheduled_phase IN ('pre-market','intraday','post-market')),
  market_date DATE NOT NULL,
  requested_kind TEXT NOT NULL CHECK (requested_kind IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')),
  requested_report_id UUID NOT NULL,
  requested_packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  requested_idempotency_key TEXT NOT NULL CHECK (requested_idempotency_key ~ '^[0-9a-f]{64}$'),
  requested_report_hash TEXT NOT NULL CHECK (requested_report_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_market_report_request_origins_run
  ON public.market_report_request_origins(run_id, scheduled_phase, market_date);
ALTER TABLE public.market_report_request_origins ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_report_request_origins FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.record_market_report_origin(
  p_request_id UUID,
  p_lease_token UUID,
  p_run_id UUID,
  p_market_date DATE,
  p_requested_kind TEXT,
  p_requested_report_id UUID,
  p_requested_packet_id UUID,
  p_requested_idempotency_key TEXT,
  p_requested_report_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_run public.analysis_runs%ROWTYPE;
  v_origin public.market_report_request_origins%ROWTYPE;
  v_packet_hash TEXT;
  v_expected_key TEXT;
  v_expected_id UUID;
BEGIN
  IF p_request_id IS NULL OR p_lease_token IS NULL OR p_run_id IS NULL
     OR p_market_date IS NULL OR p_requested_packet_id IS NULL
     OR p_requested_report_id IS NULL
     OR p_requested_kind NOT IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')
     OR p_requested_idempotency_key !~ '^[0-9a-f]{64}$'
     OR p_requested_report_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid report origin' USING ERRCODE='22023';
  END IF;

  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'record_report'
     OR v_request.status<>'claimed' OR v_request.lease_token<>p_lease_token
     OR v_request.run_id IS NOT NULL THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001';
  END IF;

  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NULL THEN
    RETURN jsonb_build_object('scheduled',false,'duplicate',false);
  END IF;
  IF v_run.scheduled_market_date IS DISTINCT FROM p_market_date
     OR NOT ((v_run.scheduled_phase='pre-market' AND p_requested_kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND p_requested_kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND p_requested_kind IN ('weekly','monthly','theme','urgent'))) THEN
    RAISE EXCEPTION 'scheduled report origin mismatch' USING ERRCODE='22023';
  END IF;

  SELECT packet_hash INTO v_packet_hash FROM public.market_evidence_packets
  WHERE id=p_requested_packet_id AND run_id=p_run_id AND status='completed';
  IF NOT FOUND THEN RAISE EXCEPTION 'completed evidence packet unavailable' USING ERRCODE='22023'; END IF;
  v_expected_key := encode(extensions.digest(convert_to(
    'v2:' || p_requested_kind || ':' || p_market_date::text || ':' ||
      v_packet_hash || ':' || p_requested_report_hash,
    'UTF8'
  ),'sha256'),'hex');
  v_expected_id := (
    substr(v_expected_key,1,8) || '-' || substr(v_expected_key,9,4) || '-5' ||
    substr(v_expected_key,14,3) || '-8' || substr(v_expected_key,18,3) || '-' ||
    substr(v_expected_key,21,12)
  )::uuid;
  IF p_requested_idempotency_key<>v_expected_key OR p_requested_report_id<>v_expected_id THEN
    RAISE EXCEPTION 'scheduled report identity mismatch' USING ERRCODE='22023';
  END IF;

  SELECT * INTO v_origin FROM public.market_report_request_origins
  WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_origin.run_id IS DISTINCT FROM p_run_id
       OR v_origin.scheduled_phase IS DISTINCT FROM v_run.scheduled_phase
       OR v_origin.market_date IS DISTINCT FROM p_market_date
       OR v_origin.requested_kind IS DISTINCT FROM p_requested_kind
       OR v_origin.requested_report_id IS DISTINCT FROM p_requested_report_id
       OR v_origin.requested_packet_id IS DISTINCT FROM p_requested_packet_id
       OR v_origin.requested_idempotency_key IS DISTINCT FROM p_requested_idempotency_key
       OR v_origin.requested_report_hash IS DISTINCT FROM p_requested_report_hash THEN
      RAISE EXCEPTION 'scheduled report origin idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('scheduled',true,'duplicate',true);
  END IF;

  INSERT INTO public.market_report_request_origins(
    request_id,run_id,scheduled_phase,market_date,requested_kind,
    requested_report_id,requested_packet_id,requested_idempotency_key,requested_report_hash
  ) VALUES (
    p_request_id,p_run_id,v_run.scheduled_phase,p_market_date,p_requested_kind,
    p_requested_report_id,p_requested_packet_id,p_requested_idempotency_key,p_requested_report_hash
  );
  RETURN jsonb_build_object('scheduled',true,'duplicate',false);
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_run public.analysis_runs%ROWTYPE; v_counts JSONB; v_statuses JSONB; v_ids JSONB; v_status TEXT;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM public.market_intelligence_runs i WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase AND i.market_date=v_run.scheduled_market_date) OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_run_events e WHERE e.run_id=p_run_id AND e.status='completed') OR NOT EXISTS (SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id) OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish' AND q.status='completed') OR NOT EXISTS (SELECT 1 FROM public.market_publications p WHERE p.run_id=p_run_id AND p.market_date=v_run.scheduled_market_date AND p.phase=v_run.scheduled_phase AND p.status='suppressed') THEN RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_report_request_origins o ON o.request_id=q.request_id
      JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END
      WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed' AND jsonb_typeof(q.response)='object' AND q.response->>'report_hash'=r.report_hash AND q.response->>'rendered_hash'=r.rendered_hash AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase AND o.market_date=v_run.scheduled_market_date AND o.requested_packet_id=r.packet_id AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly')) OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent')) OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent')))
    ) THEN RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_report_request_origins o ON o.request_id=q.request_id
      JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END
      JOIN public.market_report_publications p ON p.report_id=r.id
      WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed' AND jsonb_typeof(q.response)='object' AND q.response->>'report_hash'=r.report_hash AND q.response->>'rendered_hash'=r.rendered_hash AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase AND o.market_date=v_run.scheduled_market_date AND o.requested_packet_id=r.packet_id AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly')) OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent')) OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent'))) AND p.status IN ('delivered','suppressed')
    ) THEN RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023'; END IF;
  END IF;
  SELECT jsonb_build_object('evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id)) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb) INTO v_statuses FROM (SELECT status FROM public.market_publications WHERE run_id=p_run_id UNION ALL SELECT p.status FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb) INTO v_ids FROM (SELECT value AS message_id FROM public.market_publications p CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE p.run_id=p_run_id UNION ALL SELECT value AS message_id FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE r.run_id=p_run_id) message_ids;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM public.market_gateway_requests WHERE run_id=p_run_id AND status='failed') OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain'] THEN 'partial' WHEN NOT EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND p.status='delivered') AND EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id AND r.run_id=p_run_id WHERE p.status='suppressed') THEN 'suppressed' ELSE 'completed' END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object('run_id',p_run_id,'status',v_status,'write_counts',v_counts,'publication_statuses',v_statuses,'telegram_message_ids',v_ids);
END; $$;

REVOKE ALL ON FUNCTION public.record_market_report_origin(UUID,UUID,UUID,DATE,TEXT,UUID,UUID,TEXT,TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_report_origin(UUID,UUID,UUID,DATE,TEXT,UUID,UUID,TEXT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;
