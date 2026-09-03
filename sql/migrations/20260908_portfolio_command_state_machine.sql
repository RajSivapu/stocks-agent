-- Provider-neutral, preview-before-confirm portfolio commands.
-- Browser callers derive identity from auth.uid(); machine adapters must resolve their
-- own authenticated link before entering the private app functions below.

DROP FUNCTION IF EXISTS public.apply_portfolio_command(UUID, BIGINT, BIGINT);
DROP FUNCTION IF EXISTS public.cancel_portfolio_command(UUID, BIGINT, BIGINT);

ALTER TABLE app.transactions
  DROP CONSTRAINT IF EXISTS transactions_owner_command_fkey;
ALTER TABLE app.transactions
  ADD COLUMN IF NOT EXISTS public_id UUID NOT NULL DEFAULT extensions.gen_random_uuid();
ALTER TABLE app.transactions
  ADD CONSTRAINT transactions_owner_public_id_key UNIQUE (owner_id, public_id);

ALTER TABLE app.portfolio_commands RENAME TO portfolio_commands_legacy;
DROP INDEX IF EXISTS app.idx_portfolio_commands_status_expiry;
DROP INDEX IF EXISTS app.idx_portfolio_commands_owner_created;

CREATE TABLE app.portfolio_commands (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  channel TEXT NOT NULL CHECK (channel IN ('web', 'telegram', 'operator')),
  actor_key TEXT NOT NULL CHECK (char_length(actor_key) BETWEEN 1 AND 200),
  idempotency_key UUID NOT NULL,
  operation TEXT NOT NULL CHECK (
    operation IN ('buy', 'sell', 'sell_all', 'stop', 'plan', 'cancel_plan', 'correct_transaction')
  ),
  normalized_input JSONB NOT NULL CHECK (jsonb_typeof(normalized_input) = 'object'),
  input_digest TEXT NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
  expected_ledger_sequence BIGINT,
  before_projection JSONB,
  after_projection JSONB,
  warnings JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(warnings) = 'array'),
  preview_digest TEXT CHECK (preview_digest IS NULL OR preview_digest ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL DEFAULT 'submitted' CHECK (
    status IN ('submitted', 'previewed', 'confirmed', 'applied', 'cancelled', 'expired', 'error')
  ),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '15 minutes'),
  confirmed_at TIMESTAMPTZ,
  applied_at TIMESTAMPTZ,
  result JSONB,
  error_code TEXT CHECK (error_code IS NULL OR char_length(error_code) <= 100),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, channel, idempotency_key),
  UNIQUE (owner_id, id),
  CHECK (expires_at > created_at),
  CHECK (
    (status = 'submitted' AND preview_digest IS NULL)
    OR (status <> 'submitted' AND preview_digest IS NOT NULL)
  ),
  CHECK (status NOT IN ('confirmed','applied') OR confirmed_at IS NOT NULL),
  CHECK (status <> 'applied' OR (applied_at IS NOT NULL AND result IS NOT NULL))
);

INSERT INTO app.portfolio_commands (
  id, owner_id, channel, actor_key, idempotency_key, operation,
  normalized_input, input_digest, expected_ledger_sequence,
  before_projection, after_projection, warnings, preview_digest,
  status, expires_at, confirmed_at, applied_at, result, error_code,
  created_at, updated_at
)
SELECT id,
       owner_id,
       'telegram',
       'legacy-redacted',
       id,
       CASE WHEN operation IN ('buy','sell','stop','plan','cancel_plan')
            THEN operation ELSE 'stop' END,
       jsonb_build_object('legacy_migrated', true, 'ticker', ticker),
       encode(extensions.digest(
         jsonb_build_object('legacy_migrated', true, 'ticker', ticker)::text,
         'sha256'
       ), 'hex'),
       NULL,
       '{}'::jsonb,
       '{}'::jsonb,
       '[]'::jsonb,
       encode(extensions.digest(('legacy:' || id::text), 'sha256'), 'hex'),
       CASE status
         WHEN 'applied' THEN 'applied'
         WHEN 'cancelled' THEN 'cancelled'
         WHEN 'expired' THEN 'expired'
         ELSE 'error'
       END,
       GREATEST(expires_at, created_at + interval '1 microsecond'),
       CASE WHEN status = 'applied' THEN coalesce(applied_at, updated_at) END,
       CASE WHEN status = 'applied' THEN coalesce(applied_at, updated_at) END,
       CASE WHEN status = 'applied'
         THEN coalesce(result, jsonb_build_object('legacy_migrated', true))
         ELSE result END,
       CASE WHEN status IN ('applied','cancelled','expired') THEN NULL ELSE 'LEGACY_TERMINAL' END,
       created_at,
       updated_at
FROM app.portfolio_commands_legacy;

DROP TABLE app.portfolio_commands_legacy;

ALTER TABLE app.portfolio_commands OWNER TO stock_agent_migration_owner;
ALTER TABLE app.portfolio_commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.portfolio_commands FORCE ROW LEVEL SECURITY;
CREATE INDEX portfolio_commands_owner_created_idx
  ON app.portfolio_commands(owner_id, created_at DESC);
CREATE INDEX portfolio_commands_status_expiry_idx
  ON app.portfolio_commands(status, expires_at);

ALTER TABLE app.transactions
  ADD CONSTRAINT transactions_owner_command_fkey
  FOREIGN KEY (owner_id, command_id)
  REFERENCES app.portfolio_commands(owner_id, id) ON DELETE RESTRICT;

DROP TRIGGER IF EXISTS reject_owner_id_mutation ON app.portfolio_commands;
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.portfolio_commands
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

CREATE OR REPLACE FUNCTION app.enforce_portfolio_command_transition()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF NEW.status = OLD.status THEN
    RETURN NEW;
  END IF;
  IF NOT (
    (OLD.status = 'submitted' AND NEW.status IN ('previewed','cancelled','expired','error'))
    OR (OLD.status = 'previewed' AND NEW.status IN ('confirmed','cancelled','expired','error'))
    OR (OLD.status = 'confirmed' AND NEW.status IN ('applied','error'))
  ) THEN
    RAISE EXCEPTION 'illegal command transition: % to %', OLD.status, NEW.status
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$$;
ALTER FUNCTION app.enforce_portfolio_command_transition() OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.enforce_portfolio_command_transition()
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE TRIGGER enforce_portfolio_command_transition
  BEFORE UPDATE OF status ON app.portfolio_commands
  FOR EACH ROW EXECUTE FUNCTION app.enforce_portfolio_command_transition();

CREATE OR REPLACE FUNCTION app.reject_confirmed_command_rewrite()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF OLD.status <> 'submitted' AND (
    NEW.owner_id IS DISTINCT FROM OLD.owner_id
    OR NEW.channel IS DISTINCT FROM OLD.channel
    OR NEW.actor_key IS DISTINCT FROM OLD.actor_key
    OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
    OR NEW.operation IS DISTINCT FROM OLD.operation
    OR NEW.normalized_input IS DISTINCT FROM OLD.normalized_input
    OR NEW.input_digest IS DISTINCT FROM OLD.input_digest
    OR NEW.expected_ledger_sequence IS DISTINCT FROM OLD.expected_ledger_sequence
    OR NEW.before_projection IS DISTINCT FROM OLD.before_projection
    OR NEW.after_projection IS DISTINCT FROM OLD.after_projection
    OR NEW.warnings IS DISTINCT FROM OLD.warnings
    OR NEW.preview_digest IS DISTINCT FROM OLD.preview_digest
    OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
  ) THEN
    RAISE EXCEPTION 'previewed command is immutable' USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END
$$;
ALTER FUNCTION app.reject_confirmed_command_rewrite() OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.reject_confirmed_command_rewrite()
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE TRIGGER reject_confirmed_command_rewrite
  BEFORE UPDATE ON app.portfolio_commands
  FOR EACH ROW EXECUTE FUNCTION app.reject_confirmed_command_rewrite();

CREATE POLICY portfolio_commands_executor_all ON app.portfolio_commands
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY portfolio_commands_owner_select ON app.portfolio_commands
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY transactions_executor_all ON app.transactions
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY holdings_executor_all ON app.holdings
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY owner_ledger_counters_executor_all ON app.owner_ledger_counters
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY owner_investment_plans_executor_all ON app.owner_investment_plans
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY profiles_executor_select ON app.profiles
  FOR SELECT TO stock_agent_migration_owner USING (true);
CREATE POLICY telegram_links_executor_select ON app.telegram_links
  FOR SELECT TO stock_agent_migration_owner USING (true);

GRANT USAGE ON SCHEMA auth TO stock_agent_migration_owner;
GRANT EXECUTE ON FUNCTION auth.uid() TO stock_agent_migration_owner;
GRANT USAGE ON SCHEMA extensions TO stock_agent_migration_owner;
GRANT EXECUTE ON FUNCTION extensions.digest(BYTEA, TEXT), extensions.digest(TEXT, TEXT),
  extensions.gen_random_uuid() TO stock_agent_migration_owner;

REVOKE ALL ON app.portfolio_commands FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT SELECT (
  owner_id, id, operation, before_projection, after_projection, warnings,
  preview_digest, status, expires_at, confirmed_at, applied_at, result,
  error_code, created_at
) ON app.portfolio_commands TO authenticated;

CREATE OR REPLACE FUNCTION app.jsonb_has_exact_keys(
  p_value JSONB,
  p_required TEXT[],
  p_optional TEXT[] DEFAULT ARRAY[]::TEXT[]
) RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog
AS $$
  SELECT jsonb_typeof(p_value) = 'object'
    AND p_value ?& p_required
    AND p_value - (p_required || p_optional) = '{}'::jsonb
$$;

CREATE OR REPLACE FUNCTION app.fold_holding_with_candidate(
  p_owner_id UUID,
  p_ticker TEXT,
  p_candidate JSONB,
  p_excluded_transaction_id BIGINT DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  ledger_event RECORD;
  shares NUMERIC := 0;
  cost_basis NUMERIC := 0;
  average_cost NUMERIC := 0;
  realized_pnl NUMERIC := 0;
  opened_on DATE := NULL;
  holding_bucket TEXT := NULL;
  candidate_sequence BIGINT;
BEGIN
  SELECT coalesce(max(ledger_sequence), 0) + 1
  INTO candidate_sequence
  FROM app.transactions
  WHERE owner_id = p_owner_id;

  FOR ledger_event IN
    SELECT event.side, event.qty, event.price, event.fees,
           event.executed_on, event.bucket, event.ledger_sequence
    FROM (
      SELECT t.side, t.qty, t.price, t.fees, t.executed_on, t.bucket, t.ledger_sequence
      FROM app.transactions AS t
      WHERE t.owner_id = p_owner_id
        AND t.ticker = p_ticker
        AND t.event_type IN ('opening', 'trade')
        AND (p_excluded_transaction_id IS NULL OR t.id <> p_excluded_transaction_id)
        AND NOT EXISTS (
          SELECT 1 FROM app.transactions AS void_event
          WHERE void_event.owner_id = t.owner_id
            AND void_event.event_type = 'void'
            AND void_event.corrects_transaction_id = t.id
        )
      UNION ALL
      SELECT p_candidate->>'side',
             (p_candidate->>'qty')::numeric,
             (p_candidate->>'price')::numeric,
             (p_candidate->>'fees')::numeric,
             (p_candidate->>'executed_on')::date,
             p_candidate->>'bucket',
             candidate_sequence
    ) AS event
    ORDER BY event.executed_on, event.ledger_sequence
  LOOP
    IF ledger_event.side = 'buy' THEN
      IF shares = 0 THEN
        opened_on := ledger_event.executed_on;
        holding_bucket := coalesce(ledger_event.bucket, 'unclassified');
      ELSIF ledger_event.bucket IS NOT NULL THEN
        holding_bucket := ledger_event.bucket;
      END IF;
      shares := shares + ledger_event.qty;
      cost_basis := cost_basis + ledger_event.qty * ledger_event.price + ledger_event.fees;
    ELSIF ledger_event.side = 'sell' THEN
      IF ledger_event.qty > shares THEN
        RAISE EXCEPTION 'sell exceeds recorded shares or creates negative historical balance';
      END IF;
      IF ledger_event.qty = shares THEN
        realized_pnl := realized_pnl
          + ledger_event.qty * ledger_event.price - cost_basis - ledger_event.fees;
        shares := 0;
        cost_basis := 0;
        opened_on := NULL;
        holding_bucket := NULL;
      ELSE
        average_cost := round(cost_basis / shares, 8);
        realized_pnl := realized_pnl
          + ledger_event.qty * (ledger_event.price - average_cost) - ledger_event.fees;
        shares := shares - ledger_event.qty;
        cost_basis := cost_basis - average_cost * ledger_event.qty;
      END IF;
    END IF;
  END LOOP;

  average_cost := CASE WHEN shares = 0 THEN 0 ELSE cost_basis / shares END;
  RETURN jsonb_build_object(
    'shares', round(shares, 8),
    'avg_cost', round(average_cost, 4),
    'realized_pnl', round(realized_pnl, 2),
    'opened_at', opened_on,
    'bucket', holding_bucket,
    'projection_sequence', candidate_sequence
  );
END
$$;

CREATE OR REPLACE FUNCTION app.normalize_portfolio_command(
  p_owner_id UUID,
  p_input JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  operation_value TEXT;
  ticker_value TEXT;
  quantity_value NUMERIC(20, 8);
  price_value NUMERIC(20, 4);
  fees_value NUMERIC(20, 2);
  cash_value NUMERIC(20, 2);
  expected_cash NUMERIC;
  tolerance NUMERIC;
  executed_value DATE;
  bucket_value TEXT;
  current_holding JSONB;
  original app.transactions%ROWTYPE;
  replacement_value JSONB;
BEGIN
  IF p_owner_id IS NULL OR jsonb_typeof(p_input) <> 'object' THEN
    RAISE EXCEPTION 'invalid command payload';
  END IF;
  operation_value := p_input->>'operation';

  IF operation_value IN ('buy', 'sell') THEN
    IF NOT app.jsonb_has_exact_keys(
      p_input,
      ARRAY['operation','ticker','quantity','fill_price','fees','cash_total','executed_on'],
      CASE WHEN operation_value = 'buy'
        THEN ARRAY['bucket','plan_deposit_amount'] ELSE ARRAY[]::TEXT[] END
    ) OR jsonb_typeof(p_input->'quantity') <> 'string'
      OR jsonb_typeof(p_input->'fill_price') <> 'string'
      OR jsonb_typeof(p_input->'fees') <> 'string'
      OR (p_input->'cash_total' <> 'null'::jsonb AND jsonb_typeof(p_input->'cash_total') <> 'string')
      OR (p_input ? 'plan_deposit_amount'
          AND jsonb_typeof(p_input->'plan_deposit_amount') <> 'string') THEN
      RAISE EXCEPTION 'invalid trade command payload';
    END IF;
  ELSIF operation_value = 'sell_all' THEN
    IF NOT app.jsonb_has_exact_keys(
      p_input,
      ARRAY['operation','ticker','fill_price','fees','cash_total','executed_on']
    ) OR jsonb_typeof(p_input->'fill_price') <> 'string'
      OR jsonb_typeof(p_input->'fees') <> 'string'
      OR (p_input->'cash_total' <> 'null'::jsonb AND jsonb_typeof(p_input->'cash_total') <> 'string') THEN
      RAISE EXCEPTION 'invalid trade command payload';
    END IF;
  ELSIF operation_value = 'stop' THEN
    IF NOT app.jsonb_has_exact_keys(p_input, ARRAY['operation','ticker','stop'])
       OR jsonb_typeof(p_input->'stop') <> 'string'
       OR p_input->>'stop' !~ '^(0|[1-9][0-9]{0,6})(\.[0-9]{1,4})?$'
       OR (p_input->>'stop')::numeric <= 0 THEN
      RAISE EXCEPTION 'invalid stop command payload';
    END IF;
  ELSIF operation_value = 'plan' THEN
    IF NOT app.jsonb_has_exact_keys(
      p_input,
      ARRAY['operation','ticker','deposit_amount','cadence','next_due_on','bucket']
    ) OR jsonb_typeof(p_input->'deposit_amount') <> 'string'
      OR p_input->>'deposit_amount' !~ '^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$'
      OR (p_input->>'deposit_amount')::numeric <= 0
      OR p_input->>'cadence' <> 'monthly'
      OR p_input->>'bucket' <> 'core' THEN
      RAISE EXCEPTION 'invalid recurring plan payload';
    END IF;
  ELSIF operation_value = 'cancel_plan' THEN
    IF NOT app.jsonb_has_exact_keys(p_input, ARRAY['operation','ticker']) THEN
      RAISE EXCEPTION 'invalid cancel plan payload';
    END IF;
  ELSIF operation_value = 'correct_transaction' THEN
    IF NOT app.jsonb_has_exact_keys(
      p_input, ARRAY['operation','transaction_id','replacement']
    ) OR p_input->>'transaction_id' !~
      '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$' THEN
      RAISE EXCEPTION 'invalid correction command payload';
    END IF;
    SELECT * INTO original
    FROM app.transactions
    WHERE owner_id = p_owner_id
      AND public_id = (p_input->>'transaction_id')::uuid
      AND event_type IN ('opening','trade');
    IF NOT FOUND THEN
      RAISE EXCEPTION 'correctable transaction not found';
    END IF;
    replacement_value := app.normalize_portfolio_command(p_owner_id, p_input->'replacement');
    IF replacement_value->>'operation' NOT IN ('buy','sell')
       OR replacement_value->>'ticker' <> original.ticker
       OR replacement_value->>'operation' <> original.side THEN
      RAISE EXCEPTION 'replacement must preserve transaction ticker and side';
    END IF;
    RETURN jsonb_build_object(
      'operation', 'correct_transaction',
      'transaction_id', lower(p_input->>'transaction_id'),
      'replacement', replacement_value
    );
  ELSE
    RAISE EXCEPTION 'operation is invalid';
  END IF;

  ticker_value := p_input->>'ticker';
  IF ticker_value IS NULL OR char_length(ticker_value) > 15
     OR ticker_value !~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)?$' THEN
    RAISE EXCEPTION 'ticker must be canonical uppercase text';
  END IF;

  IF operation_value IN ('buy','sell','sell_all') THEN
    price_value := (p_input->>'fill_price')::numeric;
    fees_value := (p_input->>'fees')::numeric;
    IF p_input->>'fill_price' !~ '^(0|[1-9][0-9]{0,6})(\.[0-9]{1,4})?$'
       OR p_input->>'fees' !~ '^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$'
       OR price_value <= 0 OR fees_value < 0 THEN
      RAISE EXCEPTION 'invalid trade numeric value';
    END IF;
    executed_value := (p_input->>'executed_on')::date;
    IF p_input->>'executed_on' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
       OR executed_value < DATE '2000-01-01' OR executed_value > current_date THEN
      RAISE EXCEPTION 'invalid or future execution date';
    END IF;
    current_holding := app.fold_holding(p_owner_id, ticker_value);
    IF operation_value = 'sell_all' THEN
      quantity_value := (current_holding->>'shares')::numeric;
      IF quantity_value <= 0 THEN
        RAISE EXCEPTION 'holding not found';
      END IF;
    ELSE
      IF p_input->>'quantity' !~ '^(0|[1-9][0-9]{0,6})(\.[0-9]{1,8})?$' THEN
        RAISE EXCEPTION 'invalid trade numeric value';
      END IF;
      quantity_value := (p_input->>'quantity')::numeric;
      IF quantity_value <= 0 OR quantity_value > 1000000 THEN
        RAISE EXCEPTION 'invalid trade numeric value';
      END IF;
    END IF;
    IF operation_value IN ('sell','sell_all')
       AND (current_holding->>'shares')::numeric <= 0 THEN
      RAISE EXCEPTION 'holding not found';
    END IF;
    IF operation_value = 'buy' THEN
      bucket_value := p_input->>'bucket';
      IF (current_holding->>'shares')::numeric = 0
         AND (bucket_value IS NULL
              OR bucket_value NOT IN ('core','growth','speculative','unclassified')) THEN
        RAISE EXCEPTION 'bucket is required for a first buy';
      END IF;
      IF (current_holding->>'shares')::numeric > 0 THEN
        IF bucket_value IS NOT NULL
           AND bucket_value NOT IN ('core','growth','speculative','unclassified') THEN
          RAISE EXCEPTION 'bucket is invalid';
        END IF;
        bucket_value := coalesce(bucket_value, current_holding->>'bucket');
      END IF;
    END IF;
    expected_cash := CASE WHEN operation_value = 'buy'
      THEN quantity_value * price_value + fees_value
      ELSE quantity_value * price_value - fees_value END;
    IF p_input->'cash_total' <> 'null'::jsonb THEN
      IF p_input->>'cash_total' !~ '^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$' THEN
        RAISE EXCEPTION 'invalid cash total';
      END IF;
      cash_value := (p_input->>'cash_total')::numeric;
      tolerance := greatest(0.05, abs(expected_cash) * 0.001);
      IF abs(cash_value - expected_cash) > tolerance THEN
        RAISE EXCEPTION 'cash total does not reconcile with fill and fees';
      END IF;
    END IF;
    IF p_input ? 'plan_deposit_amount' THEN
      IF p_input->>'plan_deposit_amount' !~ '^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$'
         OR (p_input->>'plan_deposit_amount')::numeric <= 0 THEN
        RAISE EXCEPTION 'invalid recurring deposit amount';
      END IF;
    END IF;
    RETURN jsonb_strip_nulls(jsonb_build_object(
      'operation', operation_value,
      'ticker', ticker_value,
      'quantity', CASE WHEN operation_value = 'sell_all'
                       THEN NULL ELSE quantity_value::text END,
      'fill_price', price_value::text,
      'fees', fees_value::text,
      'cash_total', CASE WHEN p_input->'cash_total' = 'null'::jsonb
                         THEN NULL ELSE cash_value::text END,
      'executed_on', executed_value::text,
      'bucket', bucket_value,
      'plan_deposit_amount', CASE WHEN p_input ? 'plan_deposit_amount'
        THEN ((p_input->>'plan_deposit_amount')::numeric)::text END
    )) || CASE WHEN p_input->'cash_total' = 'null'::jsonb
               THEN jsonb_build_object('cash_total', NULL) ELSE '{}'::jsonb END;
  END IF;

  IF operation_value = 'stop' THEN
    IF (app.fold_holding(p_owner_id, ticker_value)->>'shares')::numeric <= 0 THEN
      RAISE EXCEPTION 'holding not found';
    END IF;
    RETURN jsonb_build_object(
      'operation', operation_value,
      'ticker', ticker_value,
      'stop', ((p_input->>'stop')::numeric)::text
    );
  ELSIF operation_value = 'plan' THEN
    executed_value := (p_input->>'next_due_on')::date;
    IF p_input->>'next_due_on' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
       OR executed_value < current_date THEN
      RAISE EXCEPTION 'next due date must be today or later';
    END IF;
    RETURN jsonb_build_object(
      'operation', operation_value,
      'ticker', ticker_value,
      'deposit_amount', ((p_input->>'deposit_amount')::numeric)::text,
      'cadence', 'monthly',
      'next_due_on', executed_value::text,
      'bucket', 'core'
    );
  ELSIF operation_value = 'cancel_plan' THEN
    IF NOT EXISTS (
      SELECT 1 FROM app.owner_investment_plans
      WHERE owner_id = p_owner_id AND ticker = ticker_value AND active
    ) THEN
      RAISE EXCEPTION 'active recurring plan not found';
    END IF;
    RETURN jsonb_build_object('operation', operation_value, 'ticker', ticker_value);
  END IF;

  RAISE EXCEPTION 'unsupported command';
END
$$;

CREATE OR REPLACE FUNCTION app.build_portfolio_command_preview(
  p_owner_id UUID,
  p_actor_key TEXT,
  p_input JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  normalized JSONB;
  operation_value TEXT;
  ticker_value TEXT;
  before_value JSONB;
  after_value JSONB;
  warnings_value JSONB := '[]'::jsonb;
  expected_sequence BIGINT;
  candidate JSONB;
  original app.transactions%ROWTYPE;
  digest_value TEXT;
  plan_value app.owner_investment_plans%ROWTYPE;
BEGIN
  normalized := app.normalize_portfolio_command(p_owner_id, p_input);
  operation_value := normalized->>'operation';
  IF operation_value = 'correct_transaction' THEN
    SELECT * INTO original
    FROM app.transactions
    WHERE owner_id = p_owner_id
      AND public_id = (normalized->>'transaction_id')::uuid;
    ticker_value := original.ticker;
  ELSE
    ticker_value := normalized->>'ticker';
  END IF;
  SELECT coalesce(max(ledger_sequence), 0)
  INTO expected_sequence
  FROM app.transactions
  WHERE owner_id = p_owner_id AND ticker = ticker_value;

  IF operation_value IN ('buy','sell','sell_all') THEN
    before_value := app.fold_holding(p_owner_id, ticker_value);
    candidate := jsonb_build_object(
      'side', CASE WHEN operation_value = 'buy' THEN 'buy' ELSE 'sell' END,
      'qty', CASE WHEN operation_value = 'sell_all'
                  THEN before_value->>'shares' ELSE normalized->>'quantity' END,
      'price', normalized->>'fill_price',
      'fees', normalized->>'fees',
      'executed_on', normalized->>'executed_on',
      'bucket', normalized->>'bucket'
    );
    after_value := app.fold_holding_with_candidate(p_owner_id, ticker_value, candidate);
    after_value := after_value || jsonb_build_object(
      'fill_price', normalized->>'fill_price',
      'fees', normalized->>'fees',
      'executed_on', normalized->>'executed_on',
      'cash_total', CASE WHEN normalized->'cash_total' = 'null'::jsonb
                         THEN NULL ELSE normalized->>'cash_total' END,
      'expected_cash_total', round(
        (candidate->>'qty')::numeric * (candidate->>'price')::numeric
        + CASE WHEN operation_value = 'buy'
               THEN (candidate->>'fees')::numeric
               ELSE -(candidate->>'fees')::numeric END,
        2
      )::text,
      'cash_reconciled', normalized->'cash_total' <> 'null'::jsonb,
      'estimated_realized_pnl', after_value->>'realized_pnl',
      'plan_impact', CASE
        WHEN operation_value = 'buy' AND normalized ? 'plan_deposit_amount'
          AND EXISTS (
            SELECT 1 FROM app.owner_investment_plans AS active_plan
            WHERE active_plan.owner_id = p_owner_id
              AND active_plan.ticker = ticker_value
              AND active_plan.active
              AND (normalized->>'executed_on')::date >= active_plan.next_due_on
              AND abs(active_plan.amount - (normalized->>'plan_deposit_amount')::numeric)
                <= greatest(0.05, active_plan.amount * 0.001)
          ) THEN 'advance_if_confirmed'
        ELSE 'none'
      END
    );
    IF operation_value = 'buy' AND normalized->>'bucket' = 'unclassified' THEN
      warnings_value := jsonb_build_array('UNCLASSIFIED_BUCKET');
    END IF;
  ELSIF operation_value = 'correct_transaction' THEN
    before_value := jsonb_build_object(
      'transaction_id', normalized->>'transaction_id',
      'holding', app.fold_holding(p_owner_id, ticker_value)
    );
    candidate := jsonb_build_object(
      'side', normalized#>>'{replacement,operation}',
      'qty', normalized#>>'{replacement,quantity}',
      'price', normalized#>>'{replacement,fill_price}',
      'fees', normalized#>>'{replacement,fees}',
      'executed_on', normalized#>>'{replacement,executed_on}',
      'bucket', normalized#>>'{replacement,bucket}'
    );
    after_value := app.fold_holding_with_candidate(
      p_owner_id, ticker_value, candidate, original.id
    );
  ELSIF operation_value = 'stop' THEN
    before_value := app.fold_holding(p_owner_id, ticker_value)
      || jsonb_build_object('stop', (
        SELECT stop FROM app.holdings
        WHERE owner_id = p_owner_id AND ticker = ticker_value
      ));
    after_value := before_value || jsonb_build_object('stop', (normalized->>'stop')::numeric);
  ELSIF operation_value IN ('plan','cancel_plan') THEN
    SELECT * INTO plan_value
    FROM app.owner_investment_plans
    WHERE owner_id = p_owner_id AND ticker = ticker_value;
    before_value := CASE WHEN FOUND
      THEN to_jsonb(plan_value) - 'owner_id' - 'id' - 'created_at' - 'updated_at'
      ELSE jsonb_build_object('ticker', ticker_value, 'active', false) END;
    IF operation_value = 'plan' THEN
      after_value := jsonb_build_object(
        'ticker', ticker_value,
        'bucket', 'core',
        'amount', normalized->>'deposit_amount',
        'cadence', 'monthly',
        'next_due_on', normalized->>'next_due_on',
        'due_day', extract(day from (normalized->>'next_due_on')::date)::int,
        'active', true
      );
    ELSE
      after_value := before_value || jsonb_build_object('active', false);
    END IF;
  END IF;

  digest_value := encode(extensions.digest(
    jsonb_build_object(
      'owner_id', p_owner_id::text,
      'actor_key', p_actor_key,
      'normalized_input', normalized,
      'before', before_value,
      'after', after_value,
      'warnings', warnings_value,
      'expected_ledger_sequence', expected_sequence
    )::text,
    'sha256'
  ), 'hex');
  RETURN jsonb_build_object(
    'normalized_input', normalized,
    'operation', operation_value,
    'ticker', ticker_value,
    'expected_ledger_sequence', expected_sequence,
    'before', before_value,
    'after', after_value,
    'warnings', warnings_value,
    'preview_digest', digest_value
  );
END
$$;

CREATE OR REPLACE FUNCTION app.preview_portfolio_command(
  p_owner_id UUID,
  p_channel TEXT,
  p_actor_key TEXT,
  p_idempotency_key UUID,
  p_input JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  preview_value JSONB;
  command_value app.portfolio_commands%ROWTYPE;
  input_digest_value TEXT;
  inserted_id UUID;
BEGIN
  IF p_owner_id IS NULL OR p_idempotency_key IS NULL
     OR p_channel NOT IN ('web','telegram','operator')
     OR p_actor_key IS NULL OR char_length(p_actor_key) NOT BETWEEN 1 AND 200 THEN
    RAISE EXCEPTION 'invalid command authority' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM app.profiles WHERE id = p_owner_id AND status = 'active') THEN
    RAISE EXCEPTION 'owner is not active' USING ERRCODE = '42501';
  END IF;

  preview_value := app.build_portfolio_command_preview(p_owner_id, p_actor_key, p_input);
  input_digest_value := encode(extensions.digest(
    (preview_value->'normalized_input')::text, 'sha256'
  ), 'hex');

  INSERT INTO app.portfolio_commands (
    owner_id, channel, actor_key, idempotency_key, operation,
    normalized_input, input_digest, expires_at
  ) VALUES (
    p_owner_id, p_channel, p_actor_key, p_idempotency_key,
    preview_value->>'operation', preview_value->'normalized_input',
    input_digest_value, now() + interval '15 minutes'
  )
  ON CONFLICT (owner_id, channel, idempotency_key) DO NOTHING
  RETURNING id INTO inserted_id;

  SELECT * INTO command_value
  FROM app.portfolio_commands
  WHERE owner_id = p_owner_id
    AND channel = p_channel
    AND idempotency_key = p_idempotency_key
  FOR UPDATE;

  IF command_value.input_digest <> input_digest_value THEN
    RAISE EXCEPTION 'idempotency key was already used for different input';
  END IF;
  IF inserted_id IS NULL THEN
    IF command_value.status <> 'previewed' THEN
      RAISE EXCEPTION 'idempotency key was already consumed by a terminal command';
    END IF;
    RETURN jsonb_build_object(
      'command_id', command_value.id,
      'status', 'previewed',
      'preview_digest', command_value.preview_digest,
      'expires_at', command_value.expires_at,
      'operation', command_value.operation,
      'before', command_value.before_projection,
      'after', command_value.after_projection,
      'warnings', command_value.warnings
    );
  END IF;

  UPDATE app.portfolio_commands
  SET expected_ledger_sequence = (preview_value->>'expected_ledger_sequence')::bigint,
      before_projection = preview_value->'before',
      after_projection = preview_value->'after',
      warnings = preview_value->'warnings',
      preview_digest = preview_value->>'preview_digest',
      status = 'previewed',
      updated_at = now()
  WHERE id = command_value.id
  RETURNING * INTO command_value;

  RETURN jsonb_build_object(
    'command_id', command_value.id,
    'status', 'previewed',
    'preview_digest', command_value.preview_digest,
    'expires_at', command_value.expires_at,
    'operation', command_value.operation,
    'before', command_value.before_projection,
    'after', command_value.after_projection,
    'warnings', command_value.warnings
  );
END
$$;

CREATE OR REPLACE FUNCTION app.confirm_portfolio_command(
  p_owner_id UUID,
  p_channel TEXT,
  p_actor_key TEXT,
  p_command_id UUID,
  p_preview_digest TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  command_value app.portfolio_commands%ROWTYPE;
  derived_preview JSONB;
  normalized JSONB;
  operation_value TEXT;
  ticker_value TEXT;
  ledger_result JSONB;
  result_value JSONB;
  internal_transaction_id BIGINT;
  original_transaction_id BIGINT;
  public_transaction_id UUID;
  public_replacement_id UUID;
  public_void_id UUID;
  replacement JSONB;
  plan_value app.owner_investment_plans%ROWTYPE;
  next_month DATE;
  last_day DATE;
  next_due DATE;
  deposit_value NUMERIC;
BEGIN
  IF p_preview_digest IS NULL OR p_preview_digest !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'preview digest is invalid';
  END IF;
  SELECT * INTO command_value
  FROM app.portfolio_commands
  WHERE owner_id = p_owner_id
    AND id = p_command_id
    AND channel = p_channel
    AND actor_key = p_actor_key
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'command unavailable' USING ERRCODE = '42501';
  END IF;
  IF command_value.status = 'applied' THEN
    IF command_value.preview_digest <> p_preview_digest THEN
      RAISE EXCEPTION 'preview digest does not match';
    END IF;
    RETURN jsonb_build_object(
      'command_id', command_value.id,
      'status', 'applied',
      'result', command_value.result,
      'duplicate', true
    );
  END IF;
  IF command_value.status = 'cancelled' THEN
    RAISE EXCEPTION 'command was cancelled';
  ELSIF command_value.status = 'expired' OR command_value.expires_at <= now() THEN
    RAISE EXCEPTION 'command expired';
  ELSIF command_value.status <> 'previewed' THEN
    RAISE EXCEPTION 'command is not confirmable';
  ELSIF command_value.preview_digest <> p_preview_digest THEN
    RAISE EXCEPTION 'preview digest does not match';
  END IF;

  normalized := command_value.normalized_input;
  operation_value := command_value.operation;
  IF operation_value = 'correct_transaction' THEN
    SELECT ticker INTO ticker_value
    FROM app.transactions
    WHERE owner_id = p_owner_id
      AND public_id = (normalized->>'transaction_id')::uuid;
  ELSE
    ticker_value := normalized->>'ticker';
  END IF;
  PERFORM pg_advisory_xact_lock(
    hashtextextended(p_owner_id::text || ':' || ticker_value, 0)
  );
  derived_preview := app.build_portfolio_command_preview(
    p_owner_id, p_actor_key, normalized
  );
  IF (derived_preview->>'expected_ledger_sequence')::bigint
       <> command_value.expected_ledger_sequence THEN
    RAISE EXCEPTION 'stale ledger sequence; preview the command again';
  END IF;
  IF derived_preview->>'preview_digest' <> command_value.preview_digest THEN
    RAISE EXCEPTION 'portfolio changed; preview the command again';
  END IF;

  UPDATE app.portfolio_commands
  SET status = 'confirmed', confirmed_at = now(), updated_at = now()
  WHERE id = command_value.id;

  IF operation_value IN ('buy','sell','sell_all') THEN
    ledger_result := app.append_ledger_trade(
      p_owner_id,
      jsonb_build_object(
        'ticker', ticker_value,
        'side', CASE WHEN operation_value = 'buy' THEN 'buy' ELSE 'sell' END,
        'qty', CASE WHEN operation_value = 'sell_all'
                    THEN derived_preview#>>'{before,shares}' ELSE normalized->>'quantity' END,
        'price', normalized->>'fill_price',
        'fees', normalized->>'fees',
        'executed_on', normalized->>'executed_on',
        'bucket', normalized->>'bucket',
        'source_channel', p_channel,
        'actor_id', CASE WHEN p_channel = 'web' THEN p_owner_id::text ELSE NULL END,
        'command_id', command_value.id
      )
    );
    internal_transaction_id := (ledger_result->>'transaction_id')::bigint;
    SELECT public_id INTO public_transaction_id
    FROM app.transactions
    WHERE owner_id = p_owner_id AND id = internal_transaction_id;
    result_value := jsonb_build_object(
      'operation', operation_value,
      'ticker', ticker_value,
      'transaction_id', public_transaction_id,
      'ledger_sequence', (ledger_result->>'ledger_sequence')::bigint,
      'holding', ledger_result->'holding'
    );

    IF operation_value = 'buy' AND normalized ? 'plan_deposit_amount' THEN
      SELECT * INTO plan_value
      FROM app.owner_investment_plans
      WHERE owner_id = p_owner_id AND ticker = ticker_value AND active
      FOR UPDATE;
      IF FOUND THEN
        deposit_value := (normalized->>'plan_deposit_amount')::numeric;
        IF (normalized->>'executed_on')::date >= plan_value.next_due_on
           AND abs(deposit_value - plan_value.amount)
             <= greatest(0.05, plan_value.amount * 0.001) THEN
          next_month := (
            date_trunc('month', plan_value.next_due_on)::date + interval '1 month'
          )::date;
          last_day := ((next_month + interval '1 month')::date - 1);
          next_due := next_month
            + (least(plan_value.due_day::int, extract(day from last_day)::int) - 1);
          UPDATE app.owner_investment_plans
          SET next_due_on = next_due, updated_at = now()
          WHERE owner_id = p_owner_id AND id = plan_value.id;
          result_value := result_value || jsonb_build_object('plan_advanced_to', next_due);
        END IF;
      END IF;
    END IF;
  ELSIF operation_value = 'correct_transaction' THEN
    SELECT id INTO original_transaction_id
    FROM app.transactions
    WHERE owner_id = p_owner_id
      AND public_id = (normalized->>'transaction_id')::uuid
      AND event_type IN ('opening','trade');
    replacement := normalized->'replacement';
    ledger_result := app.correct_ledger_trade(
      p_owner_id,
      original_transaction_id,
      jsonb_build_object(
        'qty', replacement->>'quantity',
        'price', replacement->>'fill_price',
        'fees', replacement->>'fees',
        'executed_on', replacement->>'executed_on',
        'bucket', replacement->>'bucket',
        'source_channel', p_channel,
        'actor_id', CASE WHEN p_channel = 'web' THEN p_owner_id::text ELSE NULL END,
        'command_id', command_value.id
      )
    );
    SELECT public_id INTO public_replacement_id
    FROM app.transactions
    WHERE owner_id = p_owner_id
      AND id = (ledger_result->>'replacement_transaction_id')::bigint;
    SELECT public_id INTO public_void_id
    FROM app.transactions
    WHERE owner_id = p_owner_id
      AND event_type = 'void'
      AND corrects_transaction_id = original_transaction_id;
    result_value := jsonb_build_object(
      'operation', operation_value,
      'ticker', ticker_value,
      'voided_transaction_id', normalized->>'transaction_id',
      'void_event_id', public_void_id,
      'replacement_transaction_id', public_replacement_id,
      'holding', ledger_result->'holding'
    );
  ELSIF operation_value = 'stop' THEN
    UPDATE app.holdings
    SET stop = (normalized->>'stop')::numeric
    WHERE owner_id = p_owner_id AND ticker = ticker_value;
    IF NOT FOUND THEN RAISE EXCEPTION 'holding not found'; END IF;
    result_value := jsonb_build_object(
      'operation', operation_value,
      'ticker', ticker_value,
      'stop', normalized->>'stop'
    );
  ELSIF operation_value = 'plan' THEN
    INSERT INTO app.owner_investment_plans (
      owner_id, ticker, bucket, amount, cadence, next_due_on, due_day, active
    ) VALUES (
      p_owner_id, ticker_value, 'core', (normalized->>'deposit_amount')::numeric,
      'monthly', (normalized->>'next_due_on')::date,
      extract(day from (normalized->>'next_due_on')::date)::smallint, true
    )
    ON CONFLICT (owner_id, ticker) DO UPDATE
    SET bucket = 'core',
        amount = EXCLUDED.amount,
        cadence = 'monthly',
        next_due_on = EXCLUDED.next_due_on,
        due_day = EXCLUDED.due_day,
        active = true,
        updated_at = now();
    result_value := jsonb_build_object(
      'operation', operation_value,
      'ticker', ticker_value,
      'deposit_amount', normalized->>'deposit_amount',
      'cadence', 'monthly',
      'next_due_on', normalized->>'next_due_on',
      'bucket', 'core'
    );
  ELSIF operation_value = 'cancel_plan' THEN
    UPDATE app.owner_investment_plans
    SET active = false, updated_at = now()
    WHERE owner_id = p_owner_id AND ticker = ticker_value AND active;
    IF NOT FOUND THEN RAISE EXCEPTION 'active recurring plan not found'; END IF;
    result_value := jsonb_build_object(
      'operation', operation_value, 'ticker', ticker_value, 'active', false
    );
  END IF;

  UPDATE app.portfolio_commands
  SET status = 'applied', applied_at = now(), updated_at = now(),
      result = result_value, error_code = NULL
  WHERE id = command_value.id;
  RETURN jsonb_build_object(
    'command_id', command_value.id,
    'status', 'applied',
    'result', result_value
  );
END
$$;

CREATE OR REPLACE FUNCTION app.cancel_portfolio_command(
  p_owner_id UUID,
  p_channel TEXT,
  p_actor_key TEXT,
  p_command_id UUID,
  p_preview_digest TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  command_value app.portfolio_commands%ROWTYPE;
BEGIN
  SELECT * INTO command_value
  FROM app.portfolio_commands
  WHERE owner_id = p_owner_id AND id = p_command_id
    AND channel = p_channel AND actor_key = p_actor_key
  FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'command unavailable' USING ERRCODE = '42501'; END IF;
  IF command_value.preview_digest <> p_preview_digest THEN
    RAISE EXCEPTION 'preview digest does not match';
  END IF;
  IF command_value.status = 'cancelled' THEN
    RETURN jsonb_build_object('command_id', command_value.id, 'status', 'cancelled');
  ELSIF command_value.status = 'applied' THEN
    RAISE EXCEPTION 'applied command cannot be cancelled';
  ELSIF command_value.expires_at <= now() THEN
    RAISE EXCEPTION 'command expired';
  ELSIF command_value.status <> 'previewed' THEN
    RAISE EXCEPTION 'command is not cancellable';
  END IF;
  UPDATE app.portfolio_commands
  SET status = 'cancelled', updated_at = now()
  WHERE id = command_value.id;
  RETURN jsonb_build_object('command_id', command_value.id, 'status', 'cancelled');
END
$$;

CREATE OR REPLACE FUNCTION api.preview_portfolio_command(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID := auth.uid();
BEGIN
  IF owner_value IS NULL THEN RAISE EXCEPTION 'authentication required' USING ERRCODE = '42501'; END IF;
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['idempotency_key','command'])
     OR p_request->>'idempotency_key' !~
       '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$' THEN
    RAISE EXCEPTION 'invalid command request';
  END IF;
  RETURN app.preview_portfolio_command(
    owner_value,
    'web',
    owner_value::text,
    (p_request->>'idempotency_key')::uuid,
    p_request->'command'
  );
END
$$;

CREATE OR REPLACE FUNCTION api.confirm_portfolio_command(
  p_command_id UUID,
  p_preview_digest TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID := auth.uid();
BEGIN
  IF owner_value IS NULL THEN RAISE EXCEPTION 'authentication required' USING ERRCODE = '42501'; END IF;
  RETURN app.confirm_portfolio_command(
    owner_value, 'web', owner_value::text, p_command_id, p_preview_digest
  );
END
$$;

CREATE OR REPLACE FUNCTION api.cancel_portfolio_command(
  p_command_id UUID,
  p_preview_digest TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID := auth.uid();
BEGIN
  IF owner_value IS NULL THEN RAISE EXCEPTION 'authentication required' USING ERRCODE = '42501'; END IF;
  RETURN app.cancel_portfolio_command(
    owner_value, 'web', owner_value::text, p_command_id, p_preview_digest
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_preview_command(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['chat_id','user_id','idempotency_key','command']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,18}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,18}$'
    OR p_request->>'idempotency_key' !~
      '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$' THEN
    RAISE EXCEPTION 'invalid telegram command request';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  RETURN app.preview_portfolio_command(
    owner_value,
    'telegram',
    'telegram:' || owner_value::text,
    (p_request->>'idempotency_key')::uuid,
    p_request->'command'
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_confirm_command(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['chat_id','user_id','command_id','preview_digest']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,18}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,18}$'
    OR p_request->>'command_id' !~
      '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
    OR p_request->>'preview_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid telegram confirmation request';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  RETURN app.confirm_portfolio_command(
    owner_value,
    'telegram',
    'telegram:' || owner_value::text,
    (p_request->>'command_id')::uuid,
    p_request->>'preview_digest'
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_cancel_command(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['chat_id','user_id','command_id','preview_digest']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,18}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,18}$'
    OR p_request->>'command_id' !~
      '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
    OR p_request->>'preview_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid telegram cancellation request';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  RETURN app.cancel_portfolio_command(
    owner_value,
    'telegram',
    'telegram:' || owner_value::text,
    (p_request->>'command_id')::uuid,
    p_request->>'preview_digest'
  );
END
$$;

ALTER FUNCTION app.jsonb_has_exact_keys(JSONB, TEXT[], TEXT[])
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.fold_holding_with_candidate(UUID, TEXT, JSONB, BIGINT)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.normalize_portfolio_command(UUID, JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.build_portfolio_command_preview(UUID, TEXT, JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.preview_portfolio_command(UUID, TEXT, TEXT, UUID, JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.confirm_portfolio_command(UUID, TEXT, TEXT, UUID, TEXT)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.cancel_portfolio_command(UUID, TEXT, TEXT, UUID, TEXT)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION api.preview_portfolio_command(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION api.confirm_portfolio_command(UUID, TEXT)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION api.cancel_portfolio_command(UUID, TEXT)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_preview_command(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_confirm_command(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_cancel_command(JSONB)
  OWNER TO stock_agent_migration_owner;

ALTER FUNCTION app.next_ledger_sequence(UUID) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.fold_holding(UUID, TEXT) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.rebuild_holding(UUID, TEXT) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.append_ledger_trade(UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.correct_ledger_trade(UUID, BIGINT, JSONB) OWNER TO stock_agent_migration_owner;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION api.preview_portfolio_command(JSONB)
  FROM PUBLIC, anon, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION api.confirm_portfolio_command(UUID, TEXT)
  FROM PUBLIC, anon, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION api.cancel_portfolio_command(UUID, TEXT)
  FROM PUBLIC, anon, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION api.preview_portfolio_command(JSONB) TO authenticated;
GRANT EXECUTE ON FUNCTION api.confirm_portfolio_command(UUID, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION api.cancel_portfolio_command(UUID, TEXT) TO authenticated;
REVOKE ALL ON FUNCTION machine.telegram_preview_command(JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_backup;
REVOKE ALL ON FUNCTION machine.telegram_confirm_command(JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_backup;
REVOKE ALL ON FUNCTION machine.telegram_cancel_command(JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_backup;
GRANT EXECUTE ON FUNCTION machine.telegram_preview_command(JSONB) TO stock_agent_telegram;
GRANT EXECUTE ON FUNCTION machine.telegram_confirm_command(JSONB) TO stock_agent_telegram;
GRANT EXECUTE ON FUNCTION machine.telegram_cancel_command(JSONB) TO stock_agent_telegram;

DROP VIEW api.transactions;
REVOKE ALL ON app.transactions FROM authenticated;
GRANT SELECT (
  owner_id, id, public_id, ts, ticker, event_type, side, qty, price, fees,
  executed_on, ledger_sequence, bucket, source_channel, actor_id, command_id,
  corrects_transaction_id
) ON app.transactions TO authenticated;
CREATE VIEW api.transactions WITH (security_invoker = true) AS
SELECT transaction_row.public_id AS id,
       transaction_row.ts AS created_at,
       transaction_row.ticker,
       transaction_row.event_type,
       transaction_row.side,
       transaction_row.qty::text AS qty,
       transaction_row.price::text AS price,
       transaction_row.fees::text AS fees,
       transaction_row.executed_on,
       transaction_row.ledger_sequence::text AS ledger_sequence,
       transaction_row.bucket,
       transaction_row.source_channel,
       corrected.public_id AS corrects_transaction_id
FROM app.transactions AS transaction_row
LEFT JOIN app.transactions AS corrected
  ON corrected.owner_id = transaction_row.owner_id
 AND corrected.id = transaction_row.corrects_transaction_id;
ALTER VIEW api.transactions OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.transactions FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.transactions TO authenticated;

CREATE VIEW api.commands WITH (security_invoker = true) AS
SELECT id,
       operation,
       before_projection AS before,
       after_projection AS after,
       warnings,
       preview_digest,
       status,
       expires_at,
       confirmed_at,
       applied_at,
       result,
       error_code,
       created_at
FROM app.portfolio_commands;
ALTER VIEW api.commands OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.commands FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.commands TO authenticated;
