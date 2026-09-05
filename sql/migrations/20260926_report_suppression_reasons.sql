-- Explicit suppression provenance. Existing reasonless rows remain unverified;
-- NOT VALID preserves history without fabricating a retrospective reason.
ALTER TABLE public.market_report_publications ADD COLUMN IF NOT EXISTS suppression_reason TEXT;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid='public.market_report_publications'::regclass
      AND conname='report_suppression_reason_required'
  ) THEN
    ALTER TABLE public.market_report_publications ADD CONSTRAINT report_suppression_reason_required
      CHECK ((status='suppressed' AND suppression_reason IS NOT NULL AND suppression_reason IN ('no_trigger','not_actionable','REPORT_POLICY_MISMATCH'))
             OR (status<>'suppressed' AND suppression_reason IS NULL)) NOT VALID;
  END IF;
END $$;

DROP FUNCTION IF EXISTS public.suppress_market_report_publication(TEXT);
CREATE OR REPLACE FUNCTION public.suppress_market_report_publication(p_idempotency_key TEXT,p_reason TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result public.market_report_publications%ROWTYPE;
BEGIN
  IF p_idempotency_key IS NULL OR p_idempotency_key !~ '^[0-9a-f]{64}$' OR p_reason IS NULL
     OR p_reason NOT IN ('no_trigger','not_actionable','REPORT_POLICY_MISMATCH') THEN
    RAISE EXCEPTION 'invalid report suppression reason' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_result FROM public.market_report_publications WHERE idempotency_key=p_idempotency_key FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'report publication unavailable' USING ERRCODE='55000'; END IF;
  IF v_result.status='suppressed' THEN
    IF v_result.suppression_reason IS DISTINCT FROM p_reason THEN
      RAISE EXCEPTION 'report suppression reason is immutable' USING ERRCODE='55000';
    END IF;
  ELSIF v_result.status IN ('pending','failed') AND v_result.lease_token IS NULL THEN
    UPDATE public.market_report_publications SET status='suppressed',suppression_reason=p_reason,error=NULL,
      telegram_message_ids='[]'::jsonb,telegram_accepted_at=NULL,updated_at=statement_timestamp()
      WHERE report_id=v_result.report_id RETURNING * INTO v_result;
  ELSE
    RAISE EXCEPTION 'report publication cannot be suppressed' USING ERRCODE='55000';
  END IF;
  RETURN jsonb_build_object('report_id',v_result.report_id,'idempotency_key',v_result.idempotency_key,
    'status',v_result.status,'suppression_reason',v_result.suppression_reason,
    'telegram_message_ids',v_result.telegram_message_ids,'telegram_accepted_at',v_result.telegram_accepted_at);
END;
$$;
REVOKE ALL ON FUNCTION public.suppress_market_report_publication(TEXT,TEXT) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.suppress_market_report_publication(TEXT,TEXT) TO service_role;
GRANT SELECT (report_id,idempotency_key,status,telegram_message_ids,telegram_accepted_at,suppression_reason)
  ON public.market_report_publications TO stock_agent_dashboard;
DROP POLICY IF EXISTS owner_dashboard_select_report_publications ON public.market_report_publications;
CREATE POLICY owner_dashboard_select_report_publications ON public.market_report_publications
  FOR SELECT TO stock_agent_dashboard USING (true);
