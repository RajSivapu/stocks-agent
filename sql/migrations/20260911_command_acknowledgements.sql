-- The portfolio mutation and its owner acknowledgement are separate, durable receipts.
CREATE TABLE IF NOT EXISTS public.portfolio_command_acknowledgements (
  command_id UUID PRIMARY KEY REFERENCES public.portfolio_commands(id) ON DELETE RESTRICT,
  telegram_update_id BIGINT NOT NULL UNIQUE CHECK (telegram_update_id >= 0),
  status TEXT NOT NULL CHECK (status IN ('pending','delivered','failed','uncertain')),
  result JSONB NOT NULL CHECK (jsonb_typeof(result)='object'),
  error TEXT CHECK (error IS NULL OR char_length(error)<=1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.portfolio_command_acknowledgements ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.apply_portfolio_command_with_acknowledgement(
  p_action TEXT, p_command_id UUID, p_chat_id BIGINT, p_user_id BIGINT, p_telegram_update_id BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_ack public.portfolio_command_acknowledgements%ROWTYPE;
BEGIN
  IF p_action NOT IN ('confirm','cancel') OR p_telegram_update_id<0 THEN RAISE EXCEPTION 'invalid command acknowledgement' USING ERRCODE='22023'; END IF;
  IF p_action='confirm' THEN v_result:=public.apply_portfolio_command(p_command_id,p_chat_id,p_user_id);
  ELSE v_result:=public.cancel_portfolio_command(p_command_id,p_chat_id,p_user_id); END IF;
  INSERT INTO public.portfolio_command_acknowledgements(command_id,telegram_update_id,status,result)
  VALUES(p_command_id,p_telegram_update_id,'pending',v_result)
  ON CONFLICT (command_id) DO NOTHING;
  SELECT * INTO v_ack FROM public.portfolio_command_acknowledgements WHERE command_id=p_command_id FOR UPDATE;
  IF NOT FOUND OR v_ack.telegram_update_id IS DISTINCT FROM p_telegram_update_id THEN RAISE EXCEPTION 'acknowledgement target mismatch' USING ERRCODE='22023'; END IF;
  RETURN jsonb_build_object('result',v_ack.result,'acknowledgement_status',v_ack.status);
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_portfolio_command_acknowledgement(
  p_command_id UUID, p_telegram_update_id BIGINT, p_status TEXT, p_error TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_ack public.portfolio_command_acknowledgements%ROWTYPE;
BEGIN
  IF p_status NOT IN ('delivered','failed','uncertain') OR char_length(COALESCE(p_error,''))>1000 THEN RAISE EXCEPTION 'invalid acknowledgement completion' USING ERRCODE='22023'; END IF;
  UPDATE public.portfolio_command_acknowledgements SET status=p_status,error=p_error,updated_at=now()
  WHERE command_id=p_command_id AND telegram_update_id=p_telegram_update_id AND status IN ('pending','failed') RETURNING * INTO v_ack;
  IF NOT FOUND THEN SELECT * INTO v_ack FROM public.portfolio_command_acknowledgements WHERE command_id=p_command_id AND telegram_update_id=p_telegram_update_id; END IF;
  IF NOT FOUND THEN RAISE EXCEPTION 'acknowledgement unavailable' USING ERRCODE='40001'; END IF;
  RETURN jsonb_build_object('status',v_ack.status,'result',v_ack.result);
END;
$$;
REVOKE ALL ON TABLE public.portfolio_command_acknowledgements FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_portfolio_command_with_acknowledgement(TEXT, UUID, BIGINT, BIGINT, BIGINT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_portfolio_command_acknowledgement(UUID, BIGINT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_portfolio_command_with_acknowledgement(TEXT, UUID, BIGINT, BIGINT, BIGINT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_portfolio_command_acknowledgement(UUID, BIGINT, TEXT, TEXT) TO service_role;
