-- Confirmed recurring investment reminders. These records never place brokerage orders.

CREATE TABLE IF NOT EXISTS public.owner_investment_plans (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ticker TEXT NOT NULL UNIQUE CHECK (ticker ~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'),
  bucket TEXT NOT NULL CHECK (bucket = 'core'),
  amount NUMERIC NOT NULL CHECK (amount > 0),
  cadence TEXT NOT NULL CHECK (cadence = 'monthly'),
  next_due_on DATE NOT NULL CHECK (next_due_on >= DATE '2000-01-01'),
  due_day SMALLINT NOT NULL CHECK (due_day BETWEEN 1 AND 31),
  active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_owner_investment_plans_active_due
  ON public.owner_investment_plans(active, next_due_on);
ALTER TABLE public.owner_investment_plans ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.portfolio_commands ADD COLUMN IF NOT EXISTS amount NUMERIC;
ALTER TABLE public.portfolio_commands ADD COLUMN IF NOT EXISTS cadence TEXT;
ALTER TABLE public.portfolio_commands ADD COLUMN IF NOT EXISTS next_due_on DATE;
ALTER TABLE public.portfolio_commands ADD COLUMN IF NOT EXISTS expected_plan_updated_at TIMESTAMPTZ;
ALTER TABLE public.portfolio_commands ALTER COLUMN expected_shares DROP NOT NULL;

ALTER TABLE public.portfolio_commands DROP CONSTRAINT IF EXISTS portfolio_commands_operation_check;
ALTER TABLE public.portfolio_commands DROP CONSTRAINT IF EXISTS portfolio_commands_expected_shares_check;
ALTER TABLE public.portfolio_commands DROP CONSTRAINT IF EXISTS portfolio_commands_check;
ALTER TABLE public.portfolio_commands DROP CONSTRAINT IF EXISTS portfolio_commands_operation_v2_check;
ALTER TABLE public.portfolio_commands DROP CONSTRAINT IF EXISTS portfolio_commands_expected_shares_v2_check;
ALTER TABLE public.portfolio_commands DROP CONSTRAINT IF EXISTS portfolio_commands_shape_v2_check;

ALTER TABLE public.portfolio_commands ADD CONSTRAINT portfolio_commands_operation_v2_check
  CHECK (operation IN ('buy', 'sell', 'stop', 'plan', 'cancel_plan'));
ALTER TABLE public.portfolio_commands ADD CONSTRAINT portfolio_commands_expected_shares_v2_check
  CHECK (
    (operation IN ('buy', 'sell', 'stop') AND expected_shares IS NOT NULL AND expected_shares >= 0)
    OR (operation IN ('plan', 'cancel_plan') AND expected_shares IS NULL)
  );
ALTER TABLE public.portfolio_commands ADD CONSTRAINT portfolio_commands_shape_v2_check
  CHECK (
    (operation = 'buy' AND qty > 0 AND price > 0 AND stop IS NULL
      AND amount IS NULL AND cadence IS NULL AND next_due_on IS NULL
      AND expected_plan_updated_at IS NULL)
    OR
    (operation = 'sell' AND qty > 0 AND price > 0 AND stop IS NULL AND bucket IS NULL
      AND amount IS NULL AND cadence IS NULL AND next_due_on IS NULL
      AND expected_plan_updated_at IS NULL)
    OR
    (operation = 'stop' AND qty IS NULL AND price IS NULL AND executed_on IS NULL
      AND bucket IS NULL AND stop > 0 AND amount IS NULL AND cadence IS NULL
      AND next_due_on IS NULL AND expected_plan_updated_at IS NULL)
    OR
    (operation = 'plan' AND qty IS NULL AND price IS NULL AND executed_on IS NULL
      AND stop IS NULL AND bucket = 'core' AND amount > 0 AND cadence = 'monthly'
      AND next_due_on >= DATE '2000-01-01')
    OR
    (operation = 'cancel_plan' AND qty IS NULL AND price IS NULL AND executed_on IS NULL
      AND bucket IS NULL AND stop IS NULL AND amount IS NULL AND cadence IS NULL
      AND next_due_on IS NULL)
  );

CREATE OR REPLACE FUNCTION public.apply_portfolio_command(
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
  v_plan public.owner_investment_plans%ROWTYPE;
  v_has_holding BOOLEAN;
  v_has_plan BOOLEAN;
  v_current_shares NUMERIC;
  v_new_shares NUMERIC;
  v_new_avg NUMERIC;
  v_realized NUMERIC;
  v_transaction_id BIGINT;
  v_executed_on DATE;
  v_next_month DATE;
  v_last_day DATE;
  v_next_due DATE;
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

  PERFORM pg_advisory_xact_lock(hashtextextended(v_command.ticker, 0));

  IF v_command.operation IN ('plan', 'cancel_plan') THEN
    SELECT * INTO v_plan
    FROM public.owner_investment_plans
    WHERE ticker = v_command.ticker
    FOR UPDATE;
    v_has_plan := FOUND;

    IF (v_has_plan AND v_command.expected_plan_updated_at IS NULL)
        OR (NOT v_has_plan AND v_command.expected_plan_updated_at IS NOT NULL)
        OR (v_has_plan AND v_plan.updated_at IS DISTINCT FROM v_command.expected_plan_updated_at) THEN
      v_result := jsonb_build_object(
        'ok', false, 'status', 'rejected', 'reason', 'plan changed; submit the command again'
      );
      UPDATE public.portfolio_commands
      SET status = 'rejected', updated_at = now(), error = 'plan changed', result = v_result
      WHERE id = v_command.id;
      RETURN v_result;
    END IF;

    IF v_command.operation = 'plan' THEN
      INSERT INTO public.owner_investment_plans (
        ticker, bucket, amount, cadence, next_due_on, due_day, active
      ) VALUES (
        v_command.ticker, 'core', v_command.amount, 'monthly', v_command.next_due_on,
        EXTRACT(day FROM v_command.next_due_on)::smallint, true
      )
      ON CONFLICT (ticker) DO UPDATE SET
        bucket = EXCLUDED.bucket,
        amount = EXCLUDED.amount,
        cadence = EXCLUDED.cadence,
        next_due_on = EXCLUDED.next_due_on,
        due_day = EXCLUDED.due_day,
        active = true,
        updated_at = now()
      RETURNING * INTO v_plan;
      v_result := jsonb_build_object(
        'ok', true, 'status', 'applied', 'operation', 'plan', 'ticker', v_plan.ticker,
        'amount', v_plan.amount, 'cadence', v_plan.cadence,
        'next_due_on', v_plan.next_due_on, 'bucket', v_plan.bucket
      );
    ELSE
      IF NOT v_has_plan OR NOT v_plan.active THEN
        v_result := jsonb_build_object(
          'ok', false, 'status', 'rejected', 'reason', 'active plan not found'
        );
        UPDATE public.portfolio_commands
        SET status = 'rejected', updated_at = now(), error = 'active plan not found', result = v_result
        WHERE id = v_command.id;
        RETURN v_result;
      END IF;
      UPDATE public.owner_investment_plans
      SET active = false, updated_at = now()
      WHERE id = v_plan.id;
      v_result := jsonb_build_object(
        'ok', true, 'status', 'applied', 'operation', 'cancel_plan', 'ticker', v_plan.ticker
      );
    END IF;

    UPDATE public.portfolio_commands
    SET status = 'applied', applied_at = now(), updated_at = now(), result = v_result, error = NULL
    WHERE id = v_command.id;
    RETURN v_result;
  END IF;

  SELECT * INTO v_holding
  FROM public.holdings
  WHERE ticker = v_command.ticker
  FOR UPDATE;
  v_has_holding := FOUND;
  v_current_shares := CASE WHEN v_has_holding THEN v_holding.shares ELSE 0 END;

  IF v_current_shares IS DISTINCT FROM v_command.expected_shares THEN
    v_result := jsonb_build_object(
      'ok', false, 'status', 'rejected', 'reason', 'holding changed; submit the command again'
    );
    UPDATE public.portfolio_commands
    SET status = 'rejected', updated_at = now(), error = 'holding changed', result = v_result
    WHERE id = v_command.id;
    RETURN v_result;
  END IF;

  IF v_command.operation IN ('buy', 'sell') THEN
    v_executed_on := COALESCE(
      v_command.executed_on,
      (CURRENT_TIMESTAMP AT TIME ZONE 'America/Chicago')::date
    );
    IF v_executed_on < DATE '2000-01-01'
        OR v_executed_on > (CURRENT_TIMESTAMP AT TIME ZONE 'America/Chicago')::date THEN
      v_result := jsonb_build_object(
        'ok', false, 'status', 'rejected', 'reason', 'invalid or future execution date'
      );
      UPDATE public.portfolio_commands
      SET status = 'rejected', updated_at = now(), error = 'invalid execution date', result = v_result
      WHERE id = v_command.id;
      RETURN v_result;
    END IF;
  END IF;

  IF v_command.operation = 'buy' THEN
    IF v_has_holding THEN
      v_new_shares := v_holding.shares + v_command.qty;
      v_new_avg := ((v_holding.shares * v_holding.avg_cost)
        + (v_command.qty * v_command.price)) / v_new_shares;
      UPDATE public.holdings
      SET shares = v_new_shares,
          avg_cost = v_new_avg,
          opened_at = LEAST(COALESCE(opened_at, v_executed_on), v_executed_on),
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
        v_command.ticker, v_new_shares, v_new_avg, v_command.bucket, v_executed_on,
        v_command.price
      );
    END IF;

    INSERT INTO public.transactions (ticker, side, qty, price, source, executed_on)
    VALUES (v_command.ticker, 'buy', v_command.qty, v_command.price, 'telegram', v_executed_on)
    RETURNING id INTO v_transaction_id;

    v_result := jsonb_build_object(
      'ok', true, 'status', 'applied', 'operation', 'buy', 'ticker', v_command.ticker,
      'shares', v_new_shares, 'avg_cost', v_new_avg,
      'bucket', CASE WHEN v_has_holding THEN v_holding.bucket ELSE v_command.bucket END,
      'executed_on', v_executed_on, 'transaction_id', v_transaction_id
    );

    SELECT * INTO v_plan
    FROM public.owner_investment_plans
    WHERE ticker = v_command.ticker AND active = true
    FOR UPDATE;
    v_has_plan := FOUND;
    IF v_has_plan AND v_executed_on >= v_plan.next_due_on
        AND abs((v_command.qty * v_command.price) - v_plan.amount)
          <= GREATEST(1, v_plan.amount * 0.02) THEN
      v_next_month := (date_trunc('month', v_plan.next_due_on)::date + interval '1 month')::date;
      v_last_day := ((v_next_month + interval '1 month')::date - 1);
      v_next_due := v_next_month
        + (LEAST(v_plan.due_day::int, EXTRACT(day FROM v_last_day)::int) - 1);
      UPDATE public.owner_investment_plans
      SET next_due_on = v_next_due, updated_at = now()
      WHERE id = v_plan.id;
      v_result := v_result || jsonb_build_object('plan_advanced_to', v_next_due);
    END IF;

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
    INSERT INTO public.transactions (ticker, side, qty, price, source, executed_on)
    VALUES (v_command.ticker, 'sell', v_command.qty, v_command.price, 'telegram', v_executed_on)
    RETURNING id INTO v_transaction_id;
    IF v_new_shares = 0 THEN
      DELETE FROM public.holdings WHERE ticker = v_command.ticker;
    ELSE
      UPDATE public.holdings SET shares = v_new_shares WHERE ticker = v_command.ticker;
    END IF;
    v_result := jsonb_build_object(
      'ok', true, 'status', 'applied', 'operation', 'sell', 'ticker', v_command.ticker,
      'shares', v_new_shares,
      'avg_cost', CASE WHEN v_new_shares > 0 THEN v_holding.avg_cost ELSE NULL END,
      'realized_pnl', v_realized, 'executed_on', v_executed_on,
      'transaction_id', v_transaction_id
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

REVOKE ALL ON FUNCTION public.apply_portfolio_command(UUID, BIGINT, BIGINT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_portfolio_command(UUID, BIGINT, BIGINT) TO service_role;
