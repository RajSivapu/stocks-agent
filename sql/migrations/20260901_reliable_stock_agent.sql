-- Reliable stock-agent audit and Telegram portfolio workflow.
-- This migration is intentionally additive and safe to re-run.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS analysis_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running',
  data_as_of TIMESTAMPTZ,
  source_status JSONB NOT NULL DEFAULT '{}'::jsonb,
  symbols JSONB NOT NULL DEFAULT '[]'::jsonb,
  write_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
  telegram_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  summary TEXT,
  error TEXT
);

ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS evidence_as_of TIMESTAMPTZ;
ALTER TABLE stock_observations ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES analysis_runs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_analysis_runs_started ON analysis_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_kind_started ON analysis_runs(kind, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_suggestions_run_id ON suggestions(run_id);
CREATE INDEX IF NOT EXISTS idx_observations_run_id ON stock_observations(run_id);

ALTER TABLE analysis_runs ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS portfolio_commands (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  telegram_update_id BIGINT NOT NULL UNIQUE,
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  operation TEXT NOT NULL CHECK (operation IN ('buy', 'sell', 'stop')),
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'),
  qty NUMERIC,
  price NUMERIC,
  bucket TEXT CHECK (bucket IS NULL OR bucket IN ('core', 'growth', 'speculative')),
  expected_shares NUMERIC NOT NULL CHECK (expected_shares >= 0),
  stop NUMERIC,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'applied', 'cancelled', 'rejected', 'expired', 'error')),
  preview JSONB NOT NULL DEFAULT '{}'::jsonb,
  confirmation_message_id BIGINT,
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '15 minutes'),
  applied_at TIMESTAMPTZ,
  realized_pnl NUMERIC,
  result JSONB,
  error TEXT CHECK (error IS NULL OR char_length(error) <= 1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (
    (operation IN ('buy', 'sell') AND qty > 0 AND price > 0 AND stop IS NULL)
    OR (operation = 'stop' AND qty IS NULL AND price IS NULL AND stop > 0)
  )
);

CREATE INDEX IF NOT EXISTS idx_portfolio_commands_status_expiry
  ON portfolio_commands(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_portfolio_commands_owner_created
  ON portfolio_commands(chat_id, user_id, created_at DESC);
ALTER TABLE portfolio_commands ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION apply_portfolio_command(
  p_command_id UUID,
  p_chat_id BIGINT,
  p_user_id BIGINT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  v_command public.portfolio_commands%ROWTYPE;
  v_holding public.holdings%ROWTYPE;
  v_has_holding BOOLEAN;
  v_current_shares NUMERIC;
  v_new_shares NUMERIC;
  v_new_avg NUMERIC;
  v_realized NUMERIC;
  v_transaction_id BIGINT;
  v_result JSONB;
BEGIN
  SELECT * INTO v_command
  FROM public.portfolio_commands
  WHERE id = p_command_id
  FOR UPDATE;

  IF NOT FOUND OR v_command.chat_id IS DISTINCT FROM p_chat_id
      OR v_command.user_id IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'command unavailable' USING ERRCODE = '42501';
  END IF;

  IF v_command.status = 'applied' THEN
    RETURN COALESCE(v_command.result, jsonb_build_object('ok', true, 'status', 'applied'))
      || jsonb_build_object('duplicate', true);
  ELSIF v_command.status <> 'pending' THEN
    RETURN jsonb_build_object('ok', false, 'status', v_command.status);
  END IF;

  IF v_command.expires_at <= now() THEN
    v_result := jsonb_build_object('ok', false, 'status', 'expired');
    UPDATE public.portfolio_commands
    SET status = 'expired', updated_at = now(), error = 'confirmation expired', result = v_result
    WHERE id = v_command.id;
    RETURN v_result;
  END IF;

  -- Serialize by ticker even when no holdings row exists yet.
  PERFORM pg_advisory_xact_lock(hashtextextended(v_command.ticker, 0));

  SELECT * INTO v_holding
  FROM public.holdings
  WHERE ticker = v_command.ticker
  FOR UPDATE;
  v_has_holding := FOUND;
  v_current_shares := CASE WHEN v_has_holding THEN v_holding.shares ELSE 0 END;

  IF v_current_shares IS DISTINCT FROM v_command.expected_shares THEN
    v_result := jsonb_build_object(
      'ok', false,
      'status', 'rejected',
      'reason', 'holding changed; submit the command again'
    );
    UPDATE public.portfolio_commands
    SET status = 'rejected', updated_at = now(), error = 'holding changed', result = v_result
    WHERE id = v_command.id;
    RETURN v_result;
  END IF;

  IF v_command.operation = 'buy' THEN
    IF v_has_holding THEN
      v_new_shares := v_holding.shares + v_command.qty;
      v_new_avg := ((v_holding.shares * v_holding.avg_cost)
        + (v_command.qty * v_command.price)) / v_new_shares;
      UPDATE public.holdings
      SET shares = v_new_shares,
          avg_cost = v_new_avg,
          high_water_price = GREATEST(COALESCE(high_water_price, v_command.price), v_command.price)
      WHERE ticker = v_command.ticker;
    ELSE
      IF v_command.bucket IS NULL THEN
        RAISE EXCEPTION 'bucket is required for a new holding' USING ERRCODE = '22023';
      END IF;
      v_new_shares := v_command.qty;
      v_new_avg := v_command.price;
      INSERT INTO public.holdings (
        ticker, shares, avg_cost, bucket, opened_at, high_water_price
      ) VALUES (
        v_command.ticker, v_new_shares, v_new_avg, v_command.bucket, current_date,
        v_command.price
      );
    END IF;

    INSERT INTO public.transactions (ticker, side, qty, price, source)
    VALUES (v_command.ticker, 'buy', v_command.qty, v_command.price, 'telegram')
    RETURNING id INTO v_transaction_id;

    v_result := jsonb_build_object(
      'ok', true, 'status', 'applied', 'operation', 'buy', 'ticker', v_command.ticker,
      'shares', v_new_shares, 'avg_cost', v_new_avg,
      'bucket', CASE WHEN v_has_holding THEN v_holding.bucket ELSE v_command.bucket END,
      'transaction_id', v_transaction_id
    );

  ELSIF v_command.operation = 'sell' THEN
    IF NOT v_has_holding THEN
      v_result := jsonb_build_object('ok', false, 'status', 'rejected', 'reason', 'holding not found');
      UPDATE public.portfolio_commands
      SET status = 'rejected', updated_at = now(), error = 'holding not found', result = v_result
      WHERE id = v_command.id;
      RETURN v_result;
    ELSIF v_command.qty > v_holding.shares THEN
      v_result := jsonb_build_object('ok', false, 'status', 'rejected', 'reason', 'sell exceeds recorded shares');
      UPDATE public.portfolio_commands
      SET status = 'rejected', updated_at = now(), error = 'sell exceeds recorded shares', result = v_result
      WHERE id = v_command.id;
      RETURN v_result;
    END IF;

    v_new_shares := v_holding.shares - v_command.qty;
    v_realized := (v_command.price - v_holding.avg_cost) * v_command.qty;

    INSERT INTO public.transactions (ticker, side, qty, price, source)
    VALUES (v_command.ticker, 'sell', v_command.qty, v_command.price, 'telegram')
    RETURNING id INTO v_transaction_id;

    IF v_new_shares = 0 THEN
      DELETE FROM public.holdings WHERE ticker = v_command.ticker;
    ELSE
      UPDATE public.holdings SET shares = v_new_shares WHERE ticker = v_command.ticker;
    END IF;

    v_result := jsonb_build_object(
      'ok', true, 'status', 'applied', 'operation', 'sell', 'ticker', v_command.ticker,
      'shares', v_new_shares, 'avg_cost', CASE WHEN v_new_shares > 0 THEN v_holding.avg_cost ELSE NULL END,
      'realized_pnl', v_realized, 'transaction_id', v_transaction_id
    );

  ELSIF v_command.operation = 'stop' THEN
    IF NOT v_has_holding THEN
      v_result := jsonb_build_object('ok', false, 'status', 'rejected', 'reason', 'holding not found');
      UPDATE public.portfolio_commands
      SET status = 'rejected', updated_at = now(), error = 'holding not found', result = v_result
      WHERE id = v_command.id;
      RETURN v_result;
    END IF;

    UPDATE public.holdings SET stop = v_command.stop WHERE ticker = v_command.ticker;
    v_new_shares := v_holding.shares;
    v_new_avg := v_holding.avg_cost;
    v_result := jsonb_build_object(
      'ok', true, 'status', 'applied', 'operation', 'stop', 'ticker', v_command.ticker,
      'shares', v_new_shares, 'avg_cost', v_new_avg, 'stop', v_command.stop
    );
  END IF;

  UPDATE public.portfolio_commands
  SET status = 'applied', applied_at = now(), updated_at = now(),
      realized_pnl = v_realized, result = v_result, error = NULL
  WHERE id = v_command.id;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION cancel_portfolio_command(
  p_command_id UUID,
  p_chat_id BIGINT,
  p_user_id BIGINT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  v_command public.portfolio_commands%ROWTYPE;
  v_result JSONB;
BEGIN
  SELECT * INTO v_command
  FROM public.portfolio_commands
  WHERE id = p_command_id
  FOR UPDATE;

  IF NOT FOUND OR v_command.chat_id IS DISTINCT FROM p_chat_id
      OR v_command.user_id IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'command unavailable' USING ERRCODE = '42501';
  END IF;

  IF v_command.status = 'cancelled' THEN
    RETURN jsonb_build_object('ok', true, 'status', 'cancelled', 'duplicate', true);
  ELSIF v_command.status <> 'pending' THEN
    RETURN jsonb_build_object('ok', false, 'status', v_command.status);
  ELSIF v_command.expires_at <= now() THEN
    v_result := jsonb_build_object('ok', false, 'status', 'expired');
    UPDATE public.portfolio_commands
    SET status = 'expired', updated_at = now(), error = 'confirmation expired', result = v_result
    WHERE id = v_command.id;
    RETURN v_result;
  END IF;

  v_result := jsonb_build_object('ok', true, 'status', 'cancelled');
  UPDATE public.portfolio_commands
  SET status = 'cancelled', updated_at = now(), result = v_result, error = NULL
  WHERE id = v_command.id;
  RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION apply_portfolio_command(UUID, BIGINT, BIGINT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION cancel_portfolio_command(UUID, BIGINT, BIGINT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION apply_portfolio_command(UUID, BIGINT, BIGINT) TO service_role;
GRANT EXECUTE ON FUNCTION cancel_portfolio_command(UUID, BIGINT, BIGINT) TO service_role;
