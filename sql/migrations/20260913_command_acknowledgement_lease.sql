-- A callback acknowledgement may be delivered by multiple webhook attempts.
-- Lease one sender and mark an abandoned send uncertain rather than risk a duplicate.
ALTER TABLE public.portfolio_command_acknowledgements
  ADD COLUMN IF NOT EXISTS lease_token UUID,
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0);
ALTER TABLE public.portfolio_command_acknowledgements
  DROP CONSTRAINT IF EXISTS portfolio_command_acknowledgements_lease_pair_check;
ALTER TABLE public.portfolio_command_acknowledgements
  ADD CONSTRAINT portfolio_command_acknowledgements_lease_pair_check
  CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL));

CREATE OR REPLACE FUNCTION public.apply_portfolio_command_with_acknowledgement(
  p_action TEXT, p_command_id UUID, p_chat_id BIGINT, p_user_id BIGINT, p_telegram_update_id BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_ack public.portfolio_command_acknowledgements%ROWTYPE; v_lease UUID;
BEGIN
  IF p_action NOT IN ('confirm','cancel') OR p_telegram_update_id<0 THEN
    RAISE EXCEPTION 'invalid command acknowledgement' USING ERRCODE='22023';
  END IF;
  IF p_action='confirm' THEN
    v_result:=public.apply_portfolio_command(p_command_id,p_chat_id,p_user_id);
  ELSE
    v_result:=public.cancel_portfolio_command(p_command_id,p_chat_id,p_user_id);
  END IF;
  INSERT INTO public.portfolio_command_acknowledgements(command_id,telegram_update_id,status,result)
  VALUES(p_command_id,p_telegram_update_id,'pending',v_result)
  ON CONFLICT (command_id) DO NOTHING;
  SELECT * INTO v_ack FROM public.portfolio_command_acknowledgements
  WHERE command_id=p_command_id FOR UPDATE;
  IF NOT FOUND OR v_ack.telegram_update_id IS DISTINCT FROM p_telegram_update_id THEN
    RAISE EXCEPTION 'acknowledgement target mismatch' USING ERRCODE='22023';
  END IF;
  IF v_ack.status IN ('delivered','uncertain') THEN
    RETURN jsonb_build_object('result',v_ack.result,'acknowledgement_status',v_ack.status,
      'acknowledgement_claimed',false,'acknowledgement_lease_token',NULL);
  END IF;
  IF v_ack.lease_token IS NOT NULL THEN
    IF v_ack.lease_expires_at >= statement_timestamp() THEN
      RETURN jsonb_build_object('result',v_ack.result,'acknowledgement_status',v_ack.status,
        'acknowledgement_claimed',false,'acknowledgement_lease_token',NULL);
    END IF;
    UPDATE public.portfolio_command_acknowledgements
    SET status='uncertain',lease_token=NULL,lease_expires_at=NULL,
      error='ACKNOWLEDGEMENT_LEASE_EXPIRED',updated_at=now()
    WHERE command_id=v_ack.command_id;
    RETURN jsonb_build_object('result',v_ack.result,'acknowledgement_status','uncertain',
      'acknowledgement_claimed',false,'acknowledgement_lease_token',NULL);
  END IF;
  v_lease:=gen_random_uuid();
  UPDATE public.portfolio_command_acknowledgements
  SET lease_token=v_lease,lease_expires_at=statement_timestamp()+interval '5 minutes',
    attempt_count=attempt_count+1,error=NULL,updated_at=now()
  WHERE command_id=v_ack.command_id;
  RETURN jsonb_build_object('result',v_ack.result,'acknowledgement_status',v_ack.status,
    'acknowledgement_claimed',true,'acknowledgement_lease_token',v_lease);
END;
$$;

-- `CREATE OR REPLACE` cannot change an RPC signature. Remove the superseded,
-- lease-less completion function so service-role callers cannot bypass a lease.
DROP FUNCTION IF EXISTS public.finish_portfolio_command_acknowledgement(UUID, BIGINT, TEXT, TEXT);
CREATE OR REPLACE FUNCTION public.finish_portfolio_command_acknowledgement(
  p_command_id UUID, p_telegram_update_id BIGINT, p_lease_token UUID, p_status TEXT, p_error TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_ack public.portfolio_command_acknowledgements%ROWTYPE;
BEGIN
  IF p_lease_token IS NULL OR p_status NOT IN ('delivered','failed','uncertain')
     OR char_length(COALESCE(p_error,''))>1000 THEN
    RAISE EXCEPTION 'invalid acknowledgement completion' USING ERRCODE='22023';
  END IF;
  UPDATE public.portfolio_command_acknowledgements
  SET status='uncertain',error='ACKNOWLEDGEMENT_LEASE_EXPIRED',
    lease_token=NULL,lease_expires_at=NULL,updated_at=now()
  WHERE command_id=p_command_id AND telegram_update_id=p_telegram_update_id
    AND lease_token=p_lease_token AND lease_expires_at < statement_timestamp()
    AND status IN ('pending','failed')
  RETURNING * INTO v_ack;
  IF FOUND THEN
    RETURN jsonb_build_object('status',v_ack.status,'result',v_ack.result);
  END IF;
  UPDATE public.portfolio_command_acknowledgements
  SET status=p_status,error=p_error,lease_token=NULL,lease_expires_at=NULL,updated_at=now()
  WHERE command_id=p_command_id AND telegram_update_id=p_telegram_update_id
    AND lease_token=p_lease_token AND lease_expires_at >= statement_timestamp()
    AND status IN ('pending','failed')
  RETURNING * INTO v_ack;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'acknowledgement lease unavailable' USING ERRCODE='40001';
  END IF;
  RETURN jsonb_build_object('status',v_ack.status,'result',v_ack.result);
END;
$$;
REVOKE ALL ON FUNCTION public.apply_portfolio_command_with_acknowledgement(TEXT, UUID, BIGINT, BIGINT, BIGINT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_portfolio_command_acknowledgement(UUID, BIGINT, UUID, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_portfolio_command_with_acknowledgement(TEXT, UUID, BIGINT, BIGINT, BIGINT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_portfolio_command_acknowledgement(UUID, BIGINT, UUID, TEXT, TEXT) TO service_role;
