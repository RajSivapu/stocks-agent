-- Preserve FIFO-style recorded accounting by refusing writes that would require a ledger replay.
-- Example: buy 10 @ 100 on 2026-09-01, sell 5 @ 110 on 2026-09-03, then reject a
-- late buy 10 @ 200 on 2026-09-02. The recorded +50 realized result remains unchanged.

DO $$
BEGIN
  IF to_regprocedure('public.apply_portfolio_command_without_chronology(uuid,bigint,bigint)') IS NULL THEN
    ALTER FUNCTION public.apply_portfolio_command(UUID, BIGINT, BIGINT)
      RENAME TO apply_portfolio_command_without_chronology;
  END IF;
END;
$$;

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
  v_has_holding BOOLEAN;
  v_current_shares NUMERIC;
  v_executed_on DATE;
  v_latest_transaction_on DATE;
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

  IF v_command.status = 'rejected'
      AND v_command.result->>'code' = 'TRANSACTION_OUT_OF_ORDER' THEN
    RETURN v_command.result || jsonb_build_object('duplicate', true);
  END IF;

  IF v_command.status = 'pending' AND v_command.operation IN ('buy', 'sell') THEN
    -- Preserve prerequisite receipts before assigning a chronology rejection.
    IF v_command.expires_at <= now() THEN
      RETURN public.apply_portfolio_command_without_chronology(p_command_id, p_chat_id, p_user_id);
    END IF;

    -- Hold the same ticker and holding locks through eligibility, chronology,
    -- and the delegated mutation; the legacy function can re-enter these locks.
    PERFORM pg_advisory_xact_lock(hashtextextended(v_command.ticker, 0));
    SELECT * INTO v_holding
    FROM public.holdings
    WHERE ticker = v_command.ticker
    FOR UPDATE;
    v_has_holding := FOUND;
    v_current_shares := CASE WHEN v_has_holding THEN v_holding.shares ELSE 0 END;

    IF v_current_shares IS DISTINCT FROM v_command.expected_shares THEN
      RETURN public.apply_portfolio_command_without_chronology(p_command_id, p_chat_id, p_user_id);
    END IF;
    IF v_command.operation = 'sell' AND (NOT v_has_holding OR v_command.qty > v_holding.shares) THEN
      RETURN public.apply_portfolio_command_without_chronology(p_command_id, p_chat_id, p_user_id);
    END IF;
    IF v_command.operation = 'buy' AND NOT v_has_holding AND v_command.bucket IS NULL THEN
      RETURN public.apply_portfolio_command_without_chronology(p_command_id, p_chat_id, p_user_id);
    END IF;

    v_executed_on := COALESCE(
      v_command.executed_on,
      (CURRENT_TIMESTAMP AT TIME ZONE 'America/Chicago')::date
    );
    IF v_executed_on >= DATE '2000-01-01'
        AND v_executed_on <= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Chicago')::date THEN
      -- Nullable columns can satisfy a CHECK through SQL NULL semantics.
      -- Reject corrupt amounts before chronology or legacy write arithmetic.
      IF v_command.qty IS NULL OR v_command.qty <= 0
          OR v_command.price IS NULL OR v_command.price <= 0 THEN
        v_result := jsonb_build_object(
          'ok', false, 'status', 'rejected',
          'reason', 'quantity and price must be positive'
        );
        UPDATE public.portfolio_commands
        SET status = 'rejected', updated_at = now(), error = 'invalid transaction amount', result = v_result
        WHERE id = v_command.id;
        RETURN v_result;
      END IF;

      SELECT MAX(COALESCE(executed_on, (ts AT TIME ZONE 'America/Chicago')::date))
      INTO v_latest_transaction_on
      FROM public.transactions
      WHERE ticker = v_command.ticker;

      IF v_executed_on < v_latest_transaction_on THEN
        v_result := jsonb_build_object(
          'ok', false,
          'status', 'rejected',
          'code', 'TRANSACTION_OUT_OF_ORDER',
          'reason', 'transaction execution date precedes the recorded ledger; reconciliation is required',
          'executed_on', v_executed_on,
          'latest_transaction_on', v_latest_transaction_on
        );
        UPDATE public.portfolio_commands
        SET status = 'rejected', updated_at = now(), error = 'TRANSACTION_OUT_OF_ORDER', result = v_result
        WHERE id = v_command.id;
        RETURN v_result;
      END IF;
    END IF;
  END IF;

  RETURN public.apply_portfolio_command_without_chronology(p_command_id, p_chat_id, p_user_id);
END;
$$;

REVOKE ALL ON FUNCTION public.apply_portfolio_command_without_chronology(UUID, BIGINT, BIGINT)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.apply_portfolio_command(UUID, BIGINT, BIGINT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_portfolio_command(UUID, BIGINT, BIGINT) TO service_role;
