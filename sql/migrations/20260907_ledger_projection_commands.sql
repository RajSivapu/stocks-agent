-- Fee-aware immutable ledger and deterministic holdings projection.
-- Legacy transaction rows remain visible as history but do not pretend to be a complete ledger;
-- each current holding becomes one explicit migration opening event.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM app.holdings
    WHERE shares <= 0 OR shares > 1000000 OR shares <> round(shares, 8)
       OR avg_cost <= 0 OR avg_cost > 1000000 OR avg_cost <> round(avg_cost, 4)
  ) OR EXISTS (
    SELECT 1 FROM app.transactions
    WHERE qty <= 0 OR qty > 1000000 OR qty <> round(qty, 8)
       OR price <= 0 OR price > 1000000 OR price <> round(price, 4)
  ) THEN
    RAISE EXCEPTION 'ledger numeric preflight failed';
  END IF;
END
$$;

DROP VIEW api.holdings;
DROP VIEW api.transactions;

ALTER TABLE app.holdings
  ALTER COLUMN shares TYPE NUMERIC(20, 8),
  ALTER COLUMN avg_cost TYPE NUMERIC(20, 4),
  ALTER COLUMN stop TYPE NUMERIC(20, 4),
  ALTER COLUMN target TYPE NUMERIC(20, 4),
  ALTER COLUMN high_water_price TYPE NUMERIC(20, 4);
ALTER TABLE app.holdings ADD COLUMN IF NOT EXISTS projection_sequence BIGINT;

ALTER TABLE app.transactions ADD COLUMN IF NOT EXISTS event_type TEXT;
ALTER TABLE app.transactions ADD COLUMN IF NOT EXISTS fees NUMERIC(20, 2);
ALTER TABLE app.transactions ADD COLUMN IF NOT EXISTS ledger_sequence BIGINT;
ALTER TABLE app.transactions ADD COLUMN IF NOT EXISTS bucket TEXT;
ALTER TABLE app.transactions ADD COLUMN IF NOT EXISTS source_channel TEXT;
ALTER TABLE app.transactions ADD COLUMN IF NOT EXISTS actor_id UUID;
ALTER TABLE app.transactions ADD COLUMN IF NOT EXISTS command_id UUID;
ALTER TABLE app.transactions ADD COLUMN IF NOT EXISTS corrects_transaction_id BIGINT;

ALTER TABLE app.transactions DROP CONSTRAINT IF EXISTS transactions_side_check;
ALTER TABLE app.transactions ALTER COLUMN side DROP NOT NULL;
ALTER TABLE app.transactions ALTER COLUMN qty DROP NOT NULL;
ALTER TABLE app.transactions ALTER COLUMN price DROP NOT NULL;
ALTER TABLE app.transactions ALTER COLUMN qty TYPE NUMERIC(20, 8);
ALTER TABLE app.transactions ALTER COLUMN price TYPE NUMERIC(20, 4);

UPDATE app.transactions
SET event_type = 'legacy_record',
    fees = 0,
    executed_on = coalesce(executed_on, (ts AT TIME ZONE 'America/Chicago')::date),
    source_channel = CASE
      WHEN source IN ('web', 'telegram', 'operator', 'provider', 'migration') THEN source
      ELSE 'migration'
    END;

WITH ranked AS (
  SELECT id,
         row_number() OVER (PARTITION BY owner_id ORDER BY executed_on, ts, id) AS sequence
  FROM app.transactions
)
UPDATE app.transactions AS target
SET ledger_sequence = ranked.sequence
FROM ranked
WHERE ranked.id = target.id;

WITH opening_events AS (
  SELECT h.*,
         coalesce(existing.max_sequence, 0)
           + row_number() OVER (PARTITION BY h.owner_id ORDER BY h.ticker) AS sequence
  FROM app.holdings AS h
  LEFT JOIN (
    SELECT owner_id, max(ledger_sequence) AS max_sequence
    FROM app.transactions
    GROUP BY owner_id
  ) AS existing ON existing.owner_id = h.owner_id
)
INSERT INTO app.transactions (
  owner_id, ticker, event_type, side, qty, price, fees, executed_on,
  ledger_sequence, bucket, source, source_channel
)
SELECT owner_id, ticker, 'opening', 'buy', shares, avg_cost, 0,
       coalesce(opened_at, current_date), sequence,
       coalesce(bucket, 'unclassified'), 'migration', 'migration'
FROM opening_events;

ALTER TABLE app.transactions
  ALTER COLUMN event_type SET NOT NULL,
  ALTER COLUMN fees SET DEFAULT 0,
  ALTER COLUMN fees SET NOT NULL,
  ALTER COLUMN ledger_sequence SET NOT NULL,
  ALTER COLUMN executed_on SET NOT NULL,
  ALTER COLUMN source_channel SET NOT NULL;

ALTER TABLE app.transactions ADD CONSTRAINT transactions_event_type_check
  CHECK (event_type IN ('legacy_record', 'opening', 'trade', 'void'));
ALTER TABLE app.transactions ADD CONSTRAINT transactions_side_v2_check
  CHECK (side IS NULL OR side IN ('buy', 'sell'));
ALTER TABLE app.transactions ADD CONSTRAINT transactions_bucket_check
  CHECK (bucket IS NULL OR bucket IN ('core', 'growth', 'speculative', 'unclassified'));
ALTER TABLE app.transactions ADD CONSTRAINT transactions_source_channel_check
  CHECK (source_channel IN ('web', 'telegram', 'operator', 'provider', 'migration'));
ALTER TABLE app.transactions ADD CONSTRAINT transactions_shape_check CHECK (
  (
    event_type IN ('legacy_record', 'opening', 'trade')
    AND side IN ('buy', 'sell')
    AND qty > 0 AND qty <= 1000000
    AND price > 0 AND price <= 1000000
    AND fees >= 0 AND fees <= 1000000000
    AND (side = 'sell' OR bucket IS NOT NULL OR event_type = 'legacy_record')
  ) OR (
    event_type = 'void'
    AND side IS NULL AND qty IS NULL AND price IS NULL AND fees = 0
    AND bucket IS NULL AND corrects_transaction_id IS NOT NULL
  )
);
ALTER TABLE app.transactions ADD CONSTRAINT transactions_owner_sequence_key
  UNIQUE (owner_id, ledger_sequence);
ALTER TABLE app.transactions ADD CONSTRAINT transactions_owner_corrects_fkey
  FOREIGN KEY (owner_id, corrects_transaction_id)
  REFERENCES app.transactions(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE app.transactions ADD CONSTRAINT transactions_owner_command_fkey
  FOREIGN KEY (owner_id, command_id)
  REFERENCES app.portfolio_commands(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE app.transactions ADD CONSTRAINT transactions_actor_fkey
  FOREIGN KEY (actor_id) REFERENCES auth.users(id) ON DELETE RESTRICT;
CREATE INDEX transactions_owner_ticker_order_idx
  ON app.transactions(owner_id, ticker, executed_on, ledger_sequence);
CREATE UNIQUE INDEX transactions_one_void_per_target
  ON app.transactions(owner_id, corrects_transaction_id)
  WHERE event_type = 'void';

CREATE TABLE app.owner_ledger_counters (
  owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  next_sequence BIGINT NOT NULL CHECK (next_sequence > 0)
);
ALTER TABLE app.owner_ledger_counters OWNER TO stock_agent_migration_owner;
ALTER TABLE app.owner_ledger_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.owner_ledger_counters FORCE ROW LEVEL SECURITY;
REVOKE ALL ON app.owner_ledger_counters FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
DROP TRIGGER IF EXISTS reject_owner_id_mutation ON app.owner_ledger_counters;
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.owner_ledger_counters
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

INSERT INTO app.owner_ledger_counters (owner_id, next_sequence)
SELECT owner_id, max(ledger_sequence) + 1
FROM app.transactions
GROUP BY owner_id;

CREATE OR REPLACE FUNCTION app.next_ledger_sequence(p_owner_id UUID)
RETURNS BIGINT
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  INSERT INTO app.owner_ledger_counters(owner_id, next_sequence)
  VALUES (p_owner_id, 2)
  ON CONFLICT (owner_id) DO UPDATE
  SET next_sequence = app.owner_ledger_counters.next_sequence + 1
  RETURNING next_sequence - 1
$$;

CREATE OR REPLACE FUNCTION app.fold_holding(p_owner_id UUID, p_ticker TEXT)
RETURNS JSONB
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
  latest_sequence BIGINT := 0;
BEGIN
  IF p_owner_id IS NULL OR p_ticker !~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$' THEN
    RAISE EXCEPTION 'invalid fold identity';
  END IF;
  SELECT coalesce(max(ledger_sequence), 0)
  INTO latest_sequence
  FROM app.transactions
  WHERE owner_id = p_owner_id AND ticker = p_ticker;

  FOR ledger_event IN
    SELECT t.*
    FROM app.transactions AS t
    WHERE t.owner_id = p_owner_id
      AND t.ticker = p_ticker
      AND t.event_type IN ('opening', 'trade')
      AND NOT EXISTS (
        SELECT 1 FROM app.transactions AS void_event
        WHERE void_event.owner_id = t.owner_id
          AND void_event.event_type = 'void'
          AND void_event.corrects_transaction_id = t.id
      )
    ORDER BY t.executed_on, t.ledger_sequence
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
        RAISE EXCEPTION 'negative historical balance for %', p_ticker;
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
    ELSE
      RAISE EXCEPTION 'unsupported ledger side';
    END IF;
  END LOOP;

  average_cost := CASE WHEN shares = 0 THEN 0 ELSE cost_basis / shares END;
  RETURN jsonb_build_object(
    'shares', round(shares, 8),
    'avg_cost', round(average_cost, 4),
    'realized_pnl', round(realized_pnl, 2),
    'opened_at', opened_on,
    'bucket', holding_bucket,
    'projection_sequence', latest_sequence
  );
END
$$;

CREATE OR REPLACE FUNCTION app.rebuild_holding(p_owner_id UUID, p_ticker TEXT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  folded JSONB;
  folded_shares NUMERIC(20, 8);
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_owner_id::text || ':' || p_ticker, 0));
  folded := app.fold_holding(p_owner_id, p_ticker);
  folded_shares := (folded->>'shares')::numeric;
  IF folded_shares = 0 THEN
    DELETE FROM app.holdings WHERE owner_id = p_owner_id AND ticker = p_ticker;
  ELSE
    INSERT INTO app.holdings (
      owner_id, ticker, shares, avg_cost, bucket, opened_at, projection_sequence
    ) VALUES (
      p_owner_id, p_ticker, folded_shares, (folded->>'avg_cost')::numeric,
      folded->>'bucket', (folded->>'opened_at')::date,
      (folded->>'projection_sequence')::bigint
    )
    ON CONFLICT (owner_id, ticker) DO UPDATE
    SET shares = EXCLUDED.shares,
        avg_cost = EXCLUDED.avg_cost,
        bucket = EXCLUDED.bucket,
        opened_at = EXCLUDED.opened_at,
        projection_sequence = EXCLUDED.projection_sequence;
  END IF;
  RETURN folded;
END
$$;

CREATE OR REPLACE FUNCTION app.append_ledger_trade(p_owner_id UUID, p_input JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  ticker_value TEXT;
  side_value TEXT;
  qty_value NUMERIC(20, 8);
  price_value NUMERIC(20, 4);
  fees_value NUMERIC(20, 2);
  executed_value DATE;
  bucket_value TEXT;
  source_value TEXT;
  actor_value UUID;
  command_value UUID;
  sequence_value BIGINT;
  transaction_value BIGINT;
  folded JSONB;
BEGIN
  IF p_owner_id IS NULL OR jsonb_typeof(p_input) <> 'object'
     OR p_input - ARRAY[
       'ticker','side','qty','price','fees','executed_on','bucket',
       'source_channel','actor_id','command_id'
     ] <> '{}'::jsonb THEN
    RAISE EXCEPTION 'invalid ledger trade payload';
  END IF;
  ticker_value := p_input->>'ticker';
  side_value := p_input->>'side';
  bucket_value := p_input->>'bucket';
  source_value := p_input->>'source_channel';
  IF ticker_value IS NULL OR ticker_value !~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'
     OR side_value NOT IN ('buy', 'sell')
     OR p_input->>'qty' !~ '^(0|[1-9][0-9]{0,6})(\.[0-9]{1,8})?$'
     OR p_input->>'price' !~ '^(0|[1-9][0-9]{0,6})(\.[0-9]{1,4})?$'
     OR p_input->>'fees' !~ '^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$'
     OR p_input->>'executed_on' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
     OR source_value NOT IN ('web', 'telegram', 'operator', 'provider', 'migration')
     OR (side_value = 'buy' AND bucket_value NOT IN ('core','growth','speculative','unclassified'))
     OR (side_value = 'sell' AND bucket_value IS NOT NULL) THEN
    RAISE EXCEPTION 'invalid ledger trade payload';
  END IF;
  qty_value := (p_input->>'qty')::numeric;
  price_value := (p_input->>'price')::numeric;
  fees_value := (p_input->>'fees')::numeric;
  executed_value := (p_input->>'executed_on')::date;
  IF qty_value <= 0 OR qty_value > 1000000 OR price_value <= 0 OR price_value > 1000000
     OR fees_value < 0 OR executed_value < DATE '2000-01-01' OR executed_value > current_date THEN
    RAISE EXCEPTION 'invalid ledger trade payload';
  END IF;
  actor_value := CASE WHEN p_input->>'actor_id' IS NULL THEN NULL ELSE (p_input->>'actor_id')::uuid END;
  command_value := CASE WHEN p_input->>'command_id' IS NULL THEN NULL ELSE (p_input->>'command_id')::uuid END;
  IF NOT EXISTS (SELECT 1 FROM app.profiles WHERE id = p_owner_id AND status = 'active') THEN
    RAISE EXCEPTION 'owner is not active';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(p_owner_id::text || ':' || ticker_value, 0));
  sequence_value := app.next_ledger_sequence(p_owner_id);
  INSERT INTO app.transactions (
    owner_id, ticker, event_type, side, qty, price, fees, executed_on,
    ledger_sequence, bucket, source, source_channel, actor_id, command_id
  ) VALUES (
    p_owner_id, ticker_value, 'trade', side_value, qty_value, price_value,
    fees_value, executed_value, sequence_value, bucket_value, source_value,
    source_value, actor_value, command_value
  ) RETURNING id INTO transaction_value;
  folded := app.rebuild_holding(p_owner_id, ticker_value);
  RETURN jsonb_build_object(
    'transaction_id', transaction_value,
    'ledger_sequence', sequence_value,
    'holding', folded
  );
END
$$;

CREATE OR REPLACE FUNCTION app.correct_ledger_trade(
  p_owner_id UUID,
  p_transaction_id BIGINT,
  p_replacement JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  original app.transactions%ROWTYPE;
  replacement_input JSONB;
  void_sequence BIGINT;
  replacement_sequence BIGINT;
  replacement_id BIGINT;
  folded JSONB;
BEGIN
  SELECT * INTO original
  FROM app.transactions
  WHERE owner_id = p_owner_id AND id = p_transaction_id
    AND event_type IN ('opening', 'trade');
  IF NOT FOUND THEN RAISE EXCEPTION 'correctable transaction not found'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_owner_id::text || ':' || original.ticker, 0));
  IF EXISTS (
    SELECT 1 FROM app.transactions
    WHERE owner_id = p_owner_id AND event_type = 'void'
      AND corrects_transaction_id = p_transaction_id
  ) THEN
    RAISE EXCEPTION 'transaction is already corrected';
  END IF;
  IF jsonb_typeof(p_replacement) <> 'object'
     OR p_replacement - ARRAY[
       'qty','price','fees','executed_on','bucket','source_channel','actor_id','command_id'
     ] <> '{}'::jsonb THEN
    RAISE EXCEPTION 'invalid correction payload';
  END IF;
  replacement_input := p_replacement || jsonb_build_object(
    'ticker', original.ticker,
    'side', original.side
  );

  -- Validate the replacement through the same strict parser without retaining its event.
  IF replacement_input->>'qty' !~ '^(0|[1-9][0-9]{0,6})(\.[0-9]{1,8})?$'
     OR replacement_input->>'price' !~ '^(0|[1-9][0-9]{0,6})(\.[0-9]{1,4})?$'
     OR replacement_input->>'fees' !~ '^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$'
     OR replacement_input->>'executed_on' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
     OR replacement_input->>'source_channel' NOT IN ('web','telegram','operator','provider','migration')
     OR (original.side = 'buy' AND replacement_input->>'bucket' NOT IN ('core','growth','speculative','unclassified'))
     OR (original.side = 'sell' AND replacement_input->>'bucket' IS NOT NULL) THEN
    RAISE EXCEPTION 'invalid correction payload';
  END IF;

  void_sequence := app.next_ledger_sequence(p_owner_id);
  INSERT INTO app.transactions (
    owner_id, ticker, event_type, side, qty, price, fees, executed_on,
    ledger_sequence, bucket, source, source_channel, actor_id, command_id,
    corrects_transaction_id
  ) VALUES (
    p_owner_id, original.ticker, 'void', NULL, NULL, NULL, 0,
    original.executed_on, void_sequence, NULL,
    p_replacement->>'source_channel', p_replacement->>'source_channel',
    CASE WHEN p_replacement->>'actor_id' IS NULL THEN NULL ELSE (p_replacement->>'actor_id')::uuid END,
    CASE WHEN p_replacement->>'command_id' IS NULL THEN NULL ELSE (p_replacement->>'command_id')::uuid END,
    p_transaction_id
  );
  replacement_sequence := app.next_ledger_sequence(p_owner_id);
  INSERT INTO app.transactions (
    owner_id, ticker, event_type, side, qty, price, fees, executed_on,
    ledger_sequence, bucket, source, source_channel, actor_id, command_id,
    corrects_transaction_id
  ) VALUES (
    p_owner_id, original.ticker, 'trade', original.side,
    (p_replacement->>'qty')::numeric, (p_replacement->>'price')::numeric,
    (p_replacement->>'fees')::numeric, (p_replacement->>'executed_on')::date,
    replacement_sequence, p_replacement->>'bucket', p_replacement->>'source_channel',
    p_replacement->>'source_channel',
    CASE WHEN p_replacement->>'actor_id' IS NULL THEN NULL ELSE (p_replacement->>'actor_id')::uuid END,
    CASE WHEN p_replacement->>'command_id' IS NULL THEN NULL ELSE (p_replacement->>'command_id')::uuid END,
    p_transaction_id
  ) RETURNING id INTO replacement_id;
  folded := app.rebuild_holding(p_owner_id, original.ticker);
  RETURN jsonb_build_object(
    'voided_transaction_id', p_transaction_id,
    'replacement_transaction_id', replacement_id,
    'ledger_sequence', replacement_sequence,
    'holding', folded
  );
END
$$;

WITH folded AS (
  SELECT owner_id, ticker, app.fold_holding(owner_id, ticker) AS value
  FROM app.holdings
)
UPDATE app.holdings AS holding
SET projection_sequence = (folded.value->>'projection_sequence')::bigint
FROM folded
WHERE folded.owner_id = holding.owner_id AND folded.ticker = holding.ticker;

DO $$
DECLARE
  mismatch_count BIGINT;
BEGIN
  SELECT count(*) INTO mismatch_count
  FROM app.holdings AS holding
  CROSS JOIN LATERAL (
    SELECT app.fold_holding(holding.owner_id, holding.ticker) AS value
  ) AS folded
  WHERE holding.shares IS DISTINCT FROM (folded.value->>'shares')::numeric
     OR holding.avg_cost IS DISTINCT FROM (folded.value->>'avg_cost')::numeric;
  IF mismatch_count > 0 THEN
    RAISE EXCEPTION 'legacy holding projection parity failed';
  END IF;
END
$$;
ALTER TABLE app.holdings ALTER COLUMN projection_sequence SET NOT NULL;

CREATE OR REPLACE FUNCTION app.reject_ledger_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  RAISE EXCEPTION 'ledger is append-only' USING ERRCODE = '55000';
END
$$;
ALTER FUNCTION app.reject_ledger_mutation() OWNER TO stock_agent_migration_owner;
DROP TRIGGER IF EXISTS transactions_append_only ON app.transactions;
CREATE TRIGGER transactions_append_only
  BEFORE UPDATE OR DELETE ON app.transactions
  FOR EACH ROW EXECUTE FUNCTION app.reject_ledger_mutation();

REVOKE ALL ON FUNCTION app.next_ledger_sequence(UUID) FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION app.fold_holding(UUID, TEXT) FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION app.rebuild_holding(UUID, TEXT) FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION app.append_ledger_trade(UUID, JSONB) FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION app.correct_ledger_trade(UUID, BIGINT, JSONB) FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION app.reject_ledger_mutation() FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

REVOKE ALL ON app.transactions FROM authenticated;
GRANT SELECT (projection_sequence) ON app.holdings TO authenticated;
GRANT SELECT (
  owner_id, id, ts, ticker, event_type, side, qty, price, fees, executed_on,
  ledger_sequence, bucket, source_channel, actor_id, command_id, corrects_transaction_id
) ON app.transactions TO authenticated;
CREATE VIEW api.holdings WITH (security_invoker = true) AS
SELECT ticker, shares, avg_cost, bucket, opened_at, stop, target,
       high_water_price, hold_override_until, projection_sequence
FROM app.holdings;
ALTER VIEW api.holdings OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.holdings FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.holdings TO authenticated;

CREATE VIEW api.transactions WITH (security_invoker = true) AS
SELECT id, ts AS created_at, ticker, event_type, side, qty, price, fees,
       executed_on, ledger_sequence, bucket, source_channel,
       corrects_transaction_id
FROM app.transactions;
ALTER VIEW api.transactions OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.transactions FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.transactions TO authenticated;
