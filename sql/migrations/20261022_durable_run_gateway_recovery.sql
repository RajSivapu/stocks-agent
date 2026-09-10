-- Keep the gateway's bounded rolling rate limit while allowing an incomplete
-- run to resume through stable request identities. A lifetime per-run request
-- count permanently locked a durable run after failed manual recovery attempts.
CREATE OR REPLACE FUNCTION public.claim_market_gateway_request(
  p_request_id UUID, p_operation TEXT, p_run_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_lease UUID;
  v_stored_run UUID := CASE WHEN p_operation='record_report' THEN NULL ELSE p_run_id END;
BEGIN
  IF p_operation NOT IN ('start_run','read_context','record_artifacts','grade_due_decisions','evaluate_and_publish','evaluate_alert_rules','finish_run','record_report')
     OR (p_operation='start_run' AND p_run_id IS NOT NULL) OR (p_operation<>'start_run' AND p_run_id IS NULL) THEN
    RAISE EXCEPTION 'invalid request identity' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('market_gateway_rate',0));
    IF (SELECT count(*) FROM public.market_gateway_requests WHERE created_at>=now()-interval '1 hour')>=100 THEN
      RAISE EXCEPTION 'gateway rate limit exceeded' USING ERRCODE='54000';
    END IF;
    v_lease:=gen_random_uuid(); INSERT INTO public.market_gateway_requests(request_id,operation,run_id,status,lease_token)
      VALUES(p_request_id,p_operation,v_stored_run,'claimed',v_lease);
    RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,'attempt_count',1);
  END IF;
  IF v_request.operation<>p_operation OR v_request.run_id IS DISTINCT FROM v_stored_run THEN RAISE EXCEPTION 'request identity mismatch' USING ERRCODE='22023'; END IF;
  IF v_request.status='failed' AND p_operation='record_report' THEN
    v_lease:=gen_random_uuid(); UPDATE public.market_gateway_requests SET status='claimed',lease_token=v_lease,claimed_at=now(),attempt_count=attempt_count+1 WHERE request_id=p_request_id;
    RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,'attempt_count',v_request.attempt_count+1);
  END IF;
  IF v_request.status IN ('completed','failed') THEN RETURN jsonb_build_object('claimed',false,'status',v_request.status,'response',v_request.response,'response_digest',v_request.response_digest); END IF;
  IF v_request.claimed_at>now()-interval '5 minutes' THEN RETURN jsonb_build_object('claimed',false,'status','REQUEST_IN_PROGRESS'); END IF;
  v_lease:=gen_random_uuid(); UPDATE public.market_gateway_requests SET lease_token=v_lease,claimed_at=now(),attempt_count=attempt_count+1 WHERE request_id=p_request_id;
  RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,'attempt_count',v_request.attempt_count+1);
END;
$$;
REVOKE ALL ON FUNCTION public.claim_market_gateway_request(UUID, TEXT, UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_market_gateway_request(UUID, TEXT, UUID) TO service_role;
