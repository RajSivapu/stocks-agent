-- Durable report-delivery outbox. A database commit and Telegram acceptance are separate receipts.
CREATE TABLE IF NOT EXISTS public.market_report_publications (
  report_id UUID PRIMARY KEY REFERENCES public.market_reports(id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL UNIQUE CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status IN ('pending','delivered','failed','uncertain','suppressed')),
  telegram_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(telegram_message_ids) = 'array' AND
    (status = 'delivered' OR jsonb_array_length(telegram_message_ids) = 0)
  ),
  telegram_accepted_at TIMESTAMPTZ,
  attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  lease_token UUID,
  lease_expires_at TIMESTAMPTZ,
  error TEXT CHECK (error IS NULL OR char_length(error) <= 1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((status = 'delivered') = (telegram_accepted_at IS NOT NULL)),
  CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL))
);
ALTER TABLE public.market_report_publications ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.create_market_report_publication(
  p_run_id UUID, p_report_id UUID, p_idempotency_key TEXT, p_market_date DATE,
  p_kind TEXT, p_rendered_body TEXT, p_rendered_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_report public.market_reports%ROWTYPE; v_existing public.market_report_publications%ROWTYPE;
BEGIN
  IF p_run_id IS NULL OR p_report_id IS NULL OR p_idempotency_key !~ '^[0-9a-f]{64}$'
     OR p_kind NOT IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')
     OR p_rendered_body IS NULL OR char_length(p_rendered_body)>14000
     OR p_rendered_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid report publication' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_report FROM public.market_reports WHERE id=p_report_id FOR UPDATE;
  IF NOT FOUND OR v_report.run_id IS DISTINCT FROM p_run_id
     OR v_report.idempotency_key IS DISTINCT FROM p_idempotency_key
     OR v_report.market_date IS DISTINCT FROM p_market_date OR v_report.kind IS DISTINCT FROM p_kind
     OR v_report.rendered_text IS DISTINCT FROM p_rendered_body
     OR v_report.rendered_hash IS DISTINCT FROM p_rendered_hash THEN
    RAISE EXCEPTION 'report publication mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_report_publications
  WHERE report_id=p_report_id OR idempotency_key=p_idempotency_key FOR UPDATE;
  IF FOUND THEN
    IF v_existing.report_id IS DISTINCT FROM p_report_id OR v_existing.idempotency_key IS DISTINCT FROM p_idempotency_key THEN
      RAISE EXCEPTION 'report publication idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('report_id',v_existing.report_id,'idempotency_key',v_existing.idempotency_key,
      'status',v_existing.status,'telegram_message_ids',v_existing.telegram_message_ids,
      'telegram_accepted_at',v_existing.telegram_accepted_at,'lease_token',v_existing.lease_token);
  END IF;
  INSERT INTO public.market_report_publications(report_id,idempotency_key,status)
  VALUES (p_report_id,p_idempotency_key,'pending');
  RETURN jsonb_build_object('report_id',p_report_id,'idempotency_key',p_idempotency_key,
    'status','pending','telegram_message_ids','[]'::jsonb,'telegram_accepted_at',NULL,'lease_token',NULL);
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_market_report_publication(p_idempotency_key TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_pub public.market_report_publications%ROWTYPE; v_lease UUID;
BEGIN
  IF p_idempotency_key !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid report publication key' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_pub FROM public.market_report_publications WHERE idempotency_key=p_idempotency_key FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'report publication unavailable' USING ERRCODE='22023'; END IF;
  IF v_pub.status IN ('delivered','uncertain','suppressed') THEN
    RETURN jsonb_build_object('claimed',false,'report_id',v_pub.report_id,'idempotency_key',v_pub.idempotency_key,
      'status',v_pub.status,'telegram_message_ids',v_pub.telegram_message_ids,
      'telegram_accepted_at',v_pub.telegram_accepted_at,'lease_token',NULL);
  END IF;
  IF v_pub.lease_token IS NOT NULL THEN
    IF v_pub.lease_expires_at >= statement_timestamp() THEN
      RETURN jsonb_build_object('claimed',false,'report_id',v_pub.report_id,'idempotency_key',v_pub.idempotency_key,
        'status','pending','telegram_message_ids','[]'::jsonb,'telegram_accepted_at',NULL,'lease_token',NULL);
    END IF;
    UPDATE public.market_report_publications SET status='uncertain',lease_token=NULL,lease_expires_at=NULL,
      error='SEND_LEASE_EXPIRED',updated_at=now() WHERE report_id=v_pub.report_id;
    RETURN jsonb_build_object('claimed',false,'report_id',v_pub.report_id,'idempotency_key',v_pub.idempotency_key,
      'status','uncertain','telegram_message_ids','[]'::jsonb,'telegram_accepted_at',NULL,'lease_token',NULL);
  END IF;
  IF v_pub.status NOT IN ('pending','failed') THEN RAISE EXCEPTION 'invalid report publication state' USING ERRCODE='22023'; END IF;
  v_lease := gen_random_uuid();
  UPDATE public.market_report_publications SET lease_token=v_lease,
    lease_expires_at=statement_timestamp()+interval '5 minutes',attempt_count=attempt_count+1,
    error=NULL,updated_at=now() WHERE report_id=v_pub.report_id;
  RETURN jsonb_build_object('claimed',true,'report_id',v_pub.report_id,'idempotency_key',v_pub.idempotency_key,
    'status','pending','telegram_message_ids','[]'::jsonb,'telegram_accepted_at',NULL,'lease_token',v_lease);
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_report_publication(
  p_idempotency_key TEXT, p_lease_token UUID, p_status TEXT, p_message_ids JSONB, p_error TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_ids_valid BOOLEAN; v_result public.market_report_publications%ROWTYPE;
BEGIN
  IF p_idempotency_key !~ '^[0-9a-f]{64}$' OR p_status NOT IN ('delivered','failed','uncertain')
     OR jsonb_typeof(p_message_ids)<>'array' OR char_length(COALESCE(p_error,''))>1000 THEN
    RAISE EXCEPTION 'invalid report publication completion' USING ERRCODE='22023';
  END IF;
  SELECT COALESCE(bool_and(jsonb_typeof(value)='number' AND value::text ~ '^[0-9]+$'),true)
    INTO v_ids_valid FROM jsonb_array_elements(p_message_ids);
  IF NOT v_ids_valid OR (p_status='delivered' AND jsonb_array_length(p_message_ids)=0) THEN
    RAISE EXCEPTION 'invalid telegram message ids' USING ERRCODE='22023';
  END IF;
  UPDATE public.market_report_publications SET status=p_status,
    telegram_message_ids=CASE WHEN p_status='delivered' THEN p_message_ids ELSE '[]'::jsonb END,
    telegram_accepted_at=CASE WHEN p_status='delivered' THEN now() ELSE NULL END,
    lease_token=NULL,lease_expires_at=NULL,error=CASE WHEN p_error IS NULL THEN NULL ELSE regexp_replace(left(p_error,1000),'[^A-Za-z0-9_ .:-]','?','g') END,
    updated_at=now()
  WHERE idempotency_key=p_idempotency_key AND lease_token=p_lease_token AND status IN ('pending','failed')
  RETURNING * INTO v_result;
  IF NOT FOUND THEN RAISE EXCEPTION 'report publication lease unavailable' USING ERRCODE='40001'; END IF;
  RETURN jsonb_build_object('report_id',v_result.report_id,'idempotency_key',v_result.idempotency_key,
    'status',v_result.status,'telegram_message_ids',v_result.telegram_message_ids,
    'telegram_accepted_at',v_result.telegram_accepted_at,'lease_token',NULL);
END;
$$;

REVOKE ALL ON TABLE public.market_report_publications FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.create_market_report_publication(UUID, UUID, TEXT, DATE, TEXT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.claim_market_report_publication(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_report_publication(TEXT, UUID, TEXT, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_market_report_publication(UUID, UUID, TEXT, DATE, TEXT, TEXT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.claim_market_report_publication(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_report_publication(TEXT, UUID, TEXT, JSONB, TEXT) TO service_role;
