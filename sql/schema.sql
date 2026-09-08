CREATE TABLE IF NOT EXISTS holdings (
  ticker TEXT PRIMARY KEY, shares NUMERIC NOT NULL, avg_cost NUMERIC NOT NULL,
  bucket TEXT, opened_at DATE, notes TEXT);

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

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
CREATE INDEX IF NOT EXISTS idx_analysis_runs_started ON analysis_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_kind_started ON analysis_runs(kind, started_at DESC);

-- v2.1: trailing-stop fields on holdings
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS stop NUMERIC;
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS target NUMERIC;
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS high_water_price NUMERIC;

-- v2.2: stop-hit alert de-dup (edge-triggered, not level-triggered) + owner hold-override
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS stop_alert_active BOOLEAN DEFAULT false;
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS hold_override_until DATE;

-- v2.2: approaching-stop / approaching-target / target-hit alert de-dup (same edge-triggered pattern)
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS stop_near_alert_active BOOLEAN DEFAULT false;
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS target_near_alert_active BOOLEAN DEFAULT false;
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS target_alert_active BOOLEAN DEFAULT false;

CREATE TABLE IF NOT EXISTS transactions (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  ticker TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  qty NUMERIC NOT NULL, price NUMERIC NOT NULL, source TEXT DEFAULT 'owner',
  executed_on DATE);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS executed_on DATE;

CREATE TABLE IF NOT EXISTS portfolio_commands (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  telegram_update_id BIGINT NOT NULL UNIQUE,
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  operation TEXT NOT NULL CHECK (operation IN ('buy', 'sell', 'stop')),
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'),
  qty NUMERIC,
  price NUMERIC,
  executed_on DATE CHECK (executed_on IS NULL OR executed_on >= DATE '2000-01-01'),
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
ALTER TABLE portfolio_commands ADD COLUMN IF NOT EXISTS executed_on DATE;
CREATE INDEX IF NOT EXISTS idx_portfolio_commands_status_expiry
  ON portfolio_commands(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_portfolio_commands_owner_created
  ON portfolio_commands(chat_id, user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS telegram_updates (
  telegram_update_id BIGINT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('message', 'callback_query')),
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS suggestions (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  date DATE NOT NULL, ticker TEXT NOT NULL, action TEXT NOT NULL, bucket TEXT,
  depth TEXT, entry_zone_low NUMERIC, entry_zone_high NUMERIC, valid_until DATE,
  stop NUMERIC, target NUMERIC, confidence TEXT, bull TEXT, bear TEXT,
  decisive_factor TEXT, risk_verdict TEXT, invalidation_level TEXT, reason TEXT,
  score INT, score_growth INT, score_health INT, score_valuation INT,
  risk_band TEXT, score_inputs TEXT, score_partial BOOLEAN DEFAULT false,
  price_at_suggestion NUMERIC);
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS evidence_as_of TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_suggestions_run_id ON suggestions(run_id);

CREATE TABLE IF NOT EXISTS suggestion_grades (
  id BIGSERIAL PRIMARY KEY, suggestion_id BIGINT REFERENCES suggestions(id),
  graded_at TIMESTAMPTZ DEFAULT now(), result TEXT, price_then NUMERIC,
  price_later NUMERIC, horizon_days INT, note TEXT);

CREATE TABLE IF NOT EXISTS stock_observations (
  id BIGSERIAL PRIMARY KEY, ticker TEXT NOT NULL, obs_date DATE NOT NULL,
  event_type TEXT, summary TEXT, price_reaction TEXT, confidence TEXT,
  source TEXT, created_at TIMESTAMPTZ DEFAULT now());
ALTER TABLE stock_observations ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES analysis_runs(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_obs_ticker ON stock_observations(ticker);
CREATE INDEX IF NOT EXISTS idx_observations_run_id ON stock_observations(run_id);

CREATE TABLE IF NOT EXISTS daily_snapshots (
  id BIGSERIAL PRIMARY KEY, snap_date DATE NOT NULL, ticker TEXT NOT NULL,
  close NUMERIC, day_move_pct NUMERIC, rsi14 NUMERIC, sma50 NUMERIC,
  sma200 NUMERIC, macd_hist NUMERIC,
  UNIQUE(snap_date, ticker));

CREATE TABLE IF NOT EXISTS dry_powder (
  month TEXT PRIMARY KEY, growth_available NUMERIC DEFAULT 0,
  spec_available NUMERIC DEFAULT 0, rolled_months INT DEFAULT 0);

CREATE TABLE IF NOT EXISTS radar (
  ticker TEXT PRIMARY KEY, added DATE, last_seen DATE, days_relevant INT,
  reason TEXT, bucket_guess TEXT, promoted BOOLEAN DEFAULT false, promoted_on DATE);

CREATE TABLE IF NOT EXISTS lessons (
  id BIGSERIAL PRIMARY KEY,
  entry_date DATE NOT NULL,
  category TEXT NOT NULL DEFAULT 'regime',
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lessons_date ON lessons(entry_date DESC, id DESC);

-- v2.1: owner's personal paper-watch hypotheses (separate from radar + holdings)
CREATE TABLE IF NOT EXISTS paper_watches (
  id BIGSERIAL PRIMARY KEY, ticker TEXT NOT NULL, created DATE NOT NULL,
  entry_ref_price NUMERIC, target_price NUMERIC, hypothetical_amount NUMERIC,
  thesis TEXT, horizon TEXT, status TEXT NOT NULL DEFAULT 'active',
  closed_date DATE, close_price NUMERIC,
  agent_view_at_open TEXT, agent_score_at_open INT,
  created_at TIMESTAMPTZ DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_watches(status);

-- RLS: block anon-key access on all tables; service role key bypasses this automatically
ALTER TABLE holdings          ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE suggestions       ENABLE ROW LEVEL SECURITY;
ALTER TABLE suggestion_grades ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_snapshots   ENABLE ROW LEVEL SECURITY;
ALTER TABLE dry_powder        ENABLE ROW LEVEL SECURITY;
ALTER TABLE radar              ENABLE ROW LEVEL SECURITY;
ALTER TABLE lessons            ENABLE ROW LEVEL SECURITY;
ALTER TABLE paper_watches      ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_runs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE telegram_updates    ENABLE ROW LEVEL SECURITY;

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
  v_new_shares NUMERIC;
  v_new_avg NUMERIC;
  v_realized NUMERIC;
  v_transaction_id BIGINT;
  v_executed_on DATE;
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
      'executed_on', v_executed_on,
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
      'shares', v_new_shares, 'avg_cost', CASE WHEN v_new_shares > 0 THEN v_holding.avg_cost ELSE NULL END,
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

-- Durable command acknowledgement outbox; 20260913 adds the sender lease below.
CREATE TABLE IF NOT EXISTS public.portfolio_command_acknowledgements (
  command_id UUID PRIMARY KEY REFERENCES public.portfolio_commands(id) ON DELETE RESTRICT,
  telegram_update_id BIGINT NOT NULL UNIQUE CHECK (telegram_update_id >= 0),
  status TEXT NOT NULL CHECK (status IN ('pending','delivered','failed','uncertain')),
  result JSONB NOT NULL CHECK (jsonb_typeof(result)='object'),
  error TEXT CHECK (error IS NULL OR char_length(error)<=1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.portfolio_command_acknowledgements ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.portfolio_command_acknowledgements FROM PUBLIC, anon, authenticated;

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

CREATE OR REPLACE FUNCTION public.cancel_portfolio_command(
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

REVOKE ALL ON FUNCTION public.apply_portfolio_command(UUID, BIGINT, BIGINT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.cancel_portfolio_command(UUID, BIGINT, BIGINT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_portfolio_command(UUID, BIGINT, BIGINT) TO service_role;


-- Durable report delivery outbox. This mirrors 20260910_delivery_outbox.sql.
CREATE TABLE IF NOT EXISTS public.market_report_publications (
  report_id UUID PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status IN ('pending','delivered','failed','uncertain','suppressed')),
  telegram_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(telegram_message_ids) = 'array' AND (status = 'delivered' OR jsonb_array_length(telegram_message_ids) = 0)),
  telegram_accepted_at TIMESTAMPTZ,
  attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  lease_token UUID, lease_expires_at TIMESTAMPTZ,
  error TEXT CHECK (error IS NULL OR char_length(error) <= 1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((status = 'delivered') = (telegram_accepted_at IS NOT NULL)),
  CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL))
);
ALTER TABLE public.market_report_publications ENABLE ROW LEVEL SECURITY;

-- The report table is declared by the intelligence ledger below. Its %ROWTYPE
-- outbox functions must follow that declaration in a fresh schema apply.

GRANT EXECUTE ON FUNCTION public.cancel_portfolio_command(UUID, BIGINT, BIGINT) TO service_role;
-- Deterministic market-decision safety gateway, audit ledger, and transactional outbox.
-- Additive and idempotent. Apply only after 20260901_reliable_stock_agent.sql.

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE TABLE IF NOT EXISTS public.market_gateway_requests (
  request_id UUID PRIMARY KEY,
  operation TEXT NOT NULL CHECK (operation IN (
    'start_run','read_context','record_artifacts','grade_due_decisions',
    'evaluate_and_publish','finish_run'
  )),
  run_id UUID REFERENCES public.analysis_runs(id) ON DELETE SET NULL,
  status TEXT NOT NULL CHECK (status IN ('claimed','completed','failed')),
  lease_token UUID NOT NULL,
  attempt_count INT NOT NULL DEFAULT 1 CHECK (attempt_count > 0),
  response JSONB CHECK (response IS NULL OR octet_length(response::text) <= 524288),
  response_digest TEXT CHECK (response_digest IS NULL OR response_digest ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS public.market_policy_config (
  version INT PRIMARY KEY CHECK (version > 0),
  config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
  active BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  activated_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_market_policy
  ON public.market_policy_config ((active)) WHERE active;

CREATE TABLE IF NOT EXISTS public.decision_evaluations (
  id UUID PRIMARY KEY,
  request_id UUID REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  run_id UUID REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  candidate_id UUID NOT NULL,
  policy_version INT REFERENCES public.market_policy_config(version),
  input_digest TEXT NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
  raw_action TEXT NOT NULL CHECK (raw_action IN (
    'buy','add','hold','reduce','sell','watch','avoid'
  )),
  final_action TEXT CHECK (final_action IS NULL OR final_action IN (
    'buy','add','hold','reduce','sell','watch','avoid'
  )),
  policy_status TEXT NOT NULL CHECK (policy_status IN (
    'approved','downgraded','vetoed','legacy_unverified'
  )),
  reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
  explanations JSONB NOT NULL DEFAULT '[]'::jsonb,
  normalized JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  analyst JSONB NOT NULL DEFAULT '{}'::jsonb,
  checker JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (
    (policy_status = 'legacy_unverified' AND request_id IS NULL AND run_id IS NULL
      AND policy_version IS NULL)
    OR
    (policy_status <> 'legacy_unverified' AND request_id IS NOT NULL AND run_id IS NOT NULL
      AND policy_version IS NOT NULL)
  ),
  UNIQUE(request_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS public.market_publications (
  id UUID PRIMARY KEY,
  idempotency_key UUID NOT NULL UNIQUE
    REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  run_id UUID REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  market_date DATE NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  kind TEXT NOT NULL CHECK (kind IN (
    'brief','new_idea','entry_trigger','stop_near','stop_breach','target_near',
    'target_hit','thesis_break','data_warning','holiday'
  )),
  template_version INT NOT NULL CHECK (template_version > 0),
  rendered_body TEXT NOT NULL CHECK (char_length(rendered_body) <= 14000),
  rendered_hash TEXT NOT NULL CHECK (rendered_hash ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status IN (
    'ready','sending','delivered','delivery_failed','delivery_unknown','suppressed'
  )),
  telegram_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  lease_token UUID,
  sending_started_at TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ,
  error TEXT CHECK (error IS NULL OR char_length(error) <= 1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS one_market_publication_per_run
  ON public.market_publications (run_id) WHERE run_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS one_holiday_publication_per_market_date
  ON public.market_publications (market_date, phase, kind) WHERE kind = 'holiday';

ALTER TABLE public.analysis_runs ADD COLUMN IF NOT EXISTS gateway_request_id UUID UNIQUE
  REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT;
ALTER TABLE public.daily_snapshots ADD COLUMN IF NOT EXISTS run_id UUID
  REFERENCES public.analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE public.lessons ADD COLUMN IF NOT EXISTS run_id UUID
  REFERENCES public.analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE public.radar ADD COLUMN IF NOT EXISTS updated_run_id UUID
  REFERENCES public.analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE public.paper_watches ADD COLUMN IF NOT EXISTS opened_run_id UUID
  REFERENCES public.analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE public.paper_watches ADD COLUMN IF NOT EXISTS closed_run_id UUID
  REFERENCES public.analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE public.suggestions ADD COLUMN IF NOT EXISTS invalidation_price NUMERIC;
ALTER TABLE public.suggestions ADD COLUMN IF NOT EXISTS evaluation_id UUID;
ALTER TABLE public.suggestions
  ADD COLUMN IF NOT EXISTS decision_source TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE public.suggestions
  ADD COLUMN IF NOT EXISTS decision_mode TEXT NOT NULL DEFAULT 'discretionary';

-- Fail closed before changing historical labels. No unrecognized legacy row is guessed.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM public.suggestions
    WHERE lower(trim(action)) NOT IN (
      'buy','add','add slowly','add/dca','dca','dca/add slowly',
      'hold','hold/wait','reduce','trim','sell','exit','study','watch',
      'watch - alert at $285','watch - alert at $610','watch/add on pullback','avoid'
    )
       OR (confidence IS NOT NULL AND lower(trim(confidence)) NOT IN (
         'low','low-medium','medium','medium-high','high'
       ))
       OR (bucket IS NOT NULL AND lower(trim(bucket)) NOT IN ('core','growth','speculative'))
  ) THEN
    RAISE EXCEPTION 'legacy suggestion preflight failed';
  END IF;
END;
$$;

-- Command acknowledgement lease functions are intentionally last: both portfolio
-- command mutation functions must exist before this wrapper is compiled.
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
/* Superseded early mirror; the executable fresh-schema mirror follows all of its dependencies.

-- Scheduled lifecycle closure. Mirrors 20260923_scheduled_run_lifecycle.sql.
ALTER TABLE public.analysis_runs
  ADD COLUMN IF NOT EXISTS scheduled_market_date DATE,
  ADD COLUMN IF NOT EXISTS scheduled_phase TEXT;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conrelid='public.analysis_runs'::regclass AND conname='analysis_runs_scheduled_phase_valid') THEN
    ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_scheduled_phase_valid CHECK (scheduled_phase IS NULL OR scheduled_phase IN ('pre-market','intraday','post-market'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conrelid='public.analysis_runs'::regclass AND conname='analysis_runs_scheduled_slot_complete') THEN
    ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_scheduled_slot_complete CHECK ((scheduled_market_date IS NULL) = (scheduled_phase IS NULL));
  END IF;
END; $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_runs_scheduled_slot ON public.analysis_runs(scheduled_market_date, scheduled_phase)
  WHERE scheduled_market_date IS NOT NULL AND scheduled_phase IS NOT NULL;
CREATE TABLE IF NOT EXISTS public.market_scheduled_phase_deadlines (
  phase TEXT PRIMARY KEY CHECK (phase IN ('pre-market','intraday','post-market')),
  deadline_local TIME NOT NULL,
  effective_on DATE NOT NULL DEFAULT (timezone('America/Chicago', statement_timestamp())::date)
);
ALTER TABLE public.market_scheduled_phase_deadlines ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_scheduled_phase_deadlines FROM PUBLIC, anon, authenticated;
INSERT INTO public.market_scheduled_phase_deadlines(phase,deadline_local)
VALUES ('pre-market','09:30'),('intraday','13:00'),('post-market','17:15') ON CONFLICT (phase) DO NOTHING;

CREATE OR REPLACE FUNCTION public.start_market_analysis_run(
  p_request_id UUID, p_lease_token UUID, p_kind TEXT, p_market_date DATE
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_run public.analysis_runs%ROWTYPE;
BEGIN
  IF p_kind NOT IN ('pre-market','intraday','post-market','on-demand') OR p_market_date IS NULL THEN RAISE EXCEPTION 'invalid analysis run slot' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'start_run' OR v_request.lease_token<>p_lease_token OR v_request.status<>'claimed' THEN RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001'; END IF;
  SELECT * INTO v_run FROM public.analysis_runs WHERE gateway_request_id=p_request_id;
  IF FOUND THEN
    IF v_run.kind IS DISTINCT FROM p_kind OR COALESCE(v_run.scheduled_market_date,p_market_date) IS DISTINCT FROM p_market_date THEN RAISE EXCEPTION 'run identity mismatch' USING ERRCODE='22023'; END IF;
    RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
  END IF;
  IF p_kind <> 'on-demand' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('scheduled-analysis-run:'||p_market_date::text||':'||p_kind,0));
    SELECT * INTO v_run FROM public.analysis_runs WHERE scheduled_market_date=p_market_date AND scheduled_phase=p_kind FOR UPDATE;
    IF FOUND THEN RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true); END IF;
    INSERT INTO public.analysis_runs(kind,status,gateway_request_id,scheduled_market_date,scheduled_phase) VALUES(p_kind,'running',p_request_id,p_market_date,p_kind) RETURNING * INTO v_run;
  ELSE
    INSERT INTO public.analysis_runs(kind,status,gateway_request_id) VALUES(p_kind,'running',p_request_id) RETURNING * INTO v_run;
  END IF;
  RETURN jsonb_build_object('run_id',v_run.id,'duplicate',false);
END; $$;

CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_run public.analysis_runs%ROWTYPE; v_counts JSONB; v_statuses JSONB; v_ids JSONB; v_status TEXT;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM public.market_intelligence_runs i WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase AND i.market_date=v_run.scheduled_market_date)
       OR NOT EXISTS (SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id)
       OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish' AND q.status='completed') THEN RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_reports r WHERE r.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND p.status IN ('delivered','suppressed')) THEN RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023'; END IF;
  END IF;
  SELECT jsonb_build_object('evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id)) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb) INTO v_statuses FROM (SELECT status FROM public.market_publications WHERE run_id=p_run_id UNION ALL SELECT p.status FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb) INTO v_ids FROM (
    SELECT value AS message_id FROM public.market_publications p CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE p.run_id=p_run_id
    UNION ALL
    SELECT value AS message_id FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE r.run_id=p_run_id
  ) message_ids;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM public.market_gateway_requests WHERE run_id=p_run_id AND status='failed') OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain'] THEN 'partial' ELSE 'completed' END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object('run_id',p_run_id,'status',v_status,'write_counts',v_counts,'publication_statuses',v_statuses,'telegram_message_ids',v_ids);
END; $$;

CREATE OR REPLACE FUNCTION public.read_overdue_scheduled_market_phases(p_now TIMESTAMPTZ DEFAULT statement_timestamp())
RETURNS TABLE(market_date DATE, phase TEXT, deadline_at TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT d.market_date, deadline.phase, (d.market_date::timestamp + deadline.deadline_local) AT TIME ZONE 'America/Chicago' AS deadline_at
  FROM (SELECT timezone('America/Chicago',p_now)::date AS market_date) d
  JOIN public.market_scheduled_phase_deadlines deadline ON deadline.effective_on<=d.market_date
  WHERE extract(isodow FROM d.market_date) BETWEEN 1 AND 5
    AND ((d.market_date::timestamp + deadline.deadline_local) AT TIME ZONE 'America/Chicago') < p_now
    AND NOT EXISTS (SELECT 1 FROM public.market_policy_config policy WHERE policy.active AND policy.config->'nyse_holidays' ? d.market_date::text)
    AND NOT EXISTS (SELECT 1 FROM public.analysis_runs run WHERE run.scheduled_market_date=d.market_date AND run.scheduled_phase=deadline.phase AND run.status IN ('completed','suppressed'))
  ORDER BY d.market_date, deadline.phase;
$$;
REVOKE ALL ON FUNCTION public.start_market_analysis_run(UUID,UUID,TEXT,DATE) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_analysis_run(UUID,UUID,TEXT,DATE) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) TO stock_agent_dashboard;

-- Final fresh-schema mirror for 20260925: this must follow market_reports.
CREATE TABLE IF NOT EXISTS public.market_report_request_origins (
  request_id UUID PRIMARY KEY REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  scheduled_phase TEXT NOT NULL CHECK (scheduled_phase IN ('pre-market','intraday','post-market')),
  market_date DATE NOT NULL,
  requested_kind TEXT NOT NULL CHECK (requested_kind IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')),
  requested_report_id UUID NOT NULL,
  requested_packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  requested_idempotency_key TEXT NOT NULL CHECK (requested_idempotency_key ~ '^[0-9a-f]{64}$'),
  requested_report_hash TEXT NOT NULL CHECK (requested_report_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_market_report_request_origins_run ON public.market_report_request_origins(run_id,scheduled_phase,market_date);
ALTER TABLE public.market_report_request_origins ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_report_request_origins FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.record_market_report_origin(p_request_id UUID,p_lease_token UUID,p_run_id UUID,p_market_date DATE,p_requested_kind TEXT,p_requested_report_id UUID,p_requested_packet_id UUID,p_requested_idempotency_key TEXT,p_requested_report_hash TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_run public.analysis_runs%ROWTYPE; v_origin public.market_report_request_origins%ROWTYPE; v_packet_hash TEXT; v_expected_key TEXT; v_expected_id UUID;
BEGIN
  IF p_request_id IS NULL OR p_lease_token IS NULL OR p_run_id IS NULL OR p_market_date IS NULL OR p_requested_packet_id IS NULL OR p_requested_report_id IS NULL OR p_requested_kind NOT IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday') OR p_requested_idempotency_key !~ '^[0-9a-f]{64}$' OR p_requested_report_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid report origin' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'record_report' OR v_request.status<>'claimed' OR v_request.lease_token<>p_lease_token OR v_request.run_id IS NOT NULL THEN RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001'; END IF;
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NULL THEN RETURN jsonb_build_object('scheduled',false,'duplicate',false); END IF;
  IF v_run.scheduled_market_date IS DISTINCT FROM p_market_date OR NOT ((v_run.scheduled_phase='pre-market' AND p_requested_kind IN ('morning','monthly')) OR (v_run.scheduled_phase='intraday' AND p_requested_kind IN ('intraday','urgent')) OR (v_run.scheduled_phase='post-market' AND p_requested_kind IN ('weekly','monthly','theme','urgent'))) THEN RAISE EXCEPTION 'scheduled report origin mismatch' USING ERRCODE='22023'; END IF;
  SELECT packet_hash INTO v_packet_hash FROM public.market_evidence_packets WHERE id=p_requested_packet_id AND run_id=p_run_id AND status='completed';
  IF NOT FOUND THEN RAISE EXCEPTION 'completed evidence packet unavailable' USING ERRCODE='22023'; END IF;
  v_expected_key:=encode(extensions.digest(convert_to('v2:'||p_requested_kind||':'||p_market_date::text||':'||v_packet_hash||':'||p_requested_report_hash,'UTF8'),'sha256'),'hex');
  v_expected_id:=(substr(v_expected_key,1,8)||'-'||substr(v_expected_key,9,4)||'-5'||substr(v_expected_key,14,3)||'-8'||substr(v_expected_key,18,3)||'-'||substr(v_expected_key,21,12))::uuid;
  IF p_requested_idempotency_key<>v_expected_key OR p_requested_report_id<>v_expected_id THEN RAISE EXCEPTION 'scheduled report identity mismatch' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_origin FROM public.market_report_request_origins WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_origin.run_id IS DISTINCT FROM p_run_id OR v_origin.scheduled_phase IS DISTINCT FROM v_run.scheduled_phase OR v_origin.market_date IS DISTINCT FROM p_market_date OR v_origin.requested_kind IS DISTINCT FROM p_requested_kind OR v_origin.requested_report_id IS DISTINCT FROM p_requested_report_id OR v_origin.requested_packet_id IS DISTINCT FROM p_requested_packet_id OR v_origin.requested_idempotency_key IS DISTINCT FROM p_requested_idempotency_key OR v_origin.requested_report_hash IS DISTINCT FROM p_requested_report_hash THEN RAISE EXCEPTION 'scheduled report origin idempotency mismatch' USING ERRCODE='22023'; END IF;
    RETURN jsonb_build_object('scheduled',true,'duplicate',true);
  END IF;
  INSERT INTO public.market_report_request_origins(request_id,run_id,scheduled_phase,market_date,requested_kind,requested_report_id,requested_packet_id,requested_idempotency_key,requested_report_hash) VALUES(p_request_id,p_run_id,v_run.scheduled_phase,p_market_date,p_requested_kind,p_requested_report_id,p_requested_packet_id,p_requested_idempotency_key,p_requested_report_hash);
  RETURN jsonb_build_object('scheduled',true,'duplicate',false);
END; $$;

CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_run public.analysis_runs%ROWTYPE; v_counts JSONB; v_statuses JSONB; v_ids JSONB; v_status TEXT;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM public.market_intelligence_runs i WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase AND i.market_date=v_run.scheduled_market_date) OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_run_events e WHERE e.run_id=p_run_id AND e.status='completed') OR NOT EXISTS (SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id) OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish' AND q.status='completed') OR NOT EXISTS (SELECT 1 FROM public.market_publications p WHERE p.run_id=p_run_id AND p.market_date=v_run.scheduled_market_date AND p.phase=v_run.scheduled_phase AND p.status='suppressed') THEN RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q JOIN public.market_report_request_origins o ON o.request_id=q.request_id JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed' AND jsonb_typeof(q.response)='object' AND q.response->>'report_hash'=r.report_hash AND q.response->>'rendered_hash'=r.rendered_hash AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase AND o.market_date=v_run.scheduled_market_date AND o.requested_packet_id=r.packet_id AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly')) OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent')) OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent')))) THEN RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q JOIN public.market_report_request_origins o ON o.request_id=q.request_id JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END JOIN public.market_report_publications p ON p.report_id=r.id WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed' AND jsonb_typeof(q.response)='object' AND q.response->>'report_hash'=r.report_hash AND q.response->>'rendered_hash'=r.rendered_hash AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase AND o.market_date=v_run.scheduled_market_date AND o.requested_packet_id=r.packet_id AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly')) OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent')) OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent'))) AND p.status IN ('delivered','suppressed')) THEN RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023'; END IF;
  END IF;
  SELECT jsonb_build_object('evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id)) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb) INTO v_statuses FROM (SELECT status FROM public.market_publications WHERE run_id=p_run_id UNION ALL SELECT p.status FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb) INTO v_ids FROM (SELECT value AS message_id FROM public.market_publications p CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE p.run_id=p_run_id UNION ALL SELECT value AS message_id FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE r.run_id=p_run_id) message_ids;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM public.market_gateway_requests WHERE run_id=p_run_id AND status='failed') OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain'] THEN 'partial' WHEN NOT EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND p.status='delivered') AND EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id AND r.run_id=p_run_id WHERE p.status='suppressed') THEN 'suppressed' ELSE 'completed' END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object('run_id',p_run_id,'status',v_status,'write_counts',v_counts,'publication_statuses',v_statuses,'telegram_message_ids',v_ids);
END; $$;
REVOKE ALL ON FUNCTION public.record_market_report_origin(UUID,UUID,UUID,DATE,TEXT,UUID,UUID,TEXT,TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_report_origin(UUID,UUID,UUID,DATE,TEXT,UUID,UUID,TEXT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;

-- Executable fresh-schema mirror of 20260925_scheduled_report_origins.sql.
CREATE TABLE IF NOT EXISTS public.market_report_request_origins (
  request_id UUID PRIMARY KEY REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  scheduled_phase TEXT NOT NULL CHECK (scheduled_phase IN ('pre-market','intraday','post-market')),
  market_date DATE NOT NULL,
  requested_kind TEXT NOT NULL CHECK (requested_kind IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')),
  requested_report_id UUID NOT NULL,
  requested_packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  requested_idempotency_key TEXT NOT NULL CHECK (requested_idempotency_key ~ '^[0-9a-f]{64}$'),
  requested_report_hash TEXT NOT NULL CHECK (requested_report_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_market_report_request_origins_run ON public.market_report_request_origins(run_id,scheduled_phase,market_date);
ALTER TABLE public.market_report_request_origins ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_report_request_origins FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.record_market_report_origin(p_request_id UUID,p_lease_token UUID,p_run_id UUID,p_market_date DATE,p_requested_kind TEXT,p_requested_report_id UUID,p_requested_packet_id UUID,p_requested_idempotency_key TEXT,p_requested_report_hash TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_run public.analysis_runs%ROWTYPE; v_origin public.market_report_request_origins%ROWTYPE; v_packet_hash TEXT; v_expected_key TEXT; v_expected_id UUID;
BEGIN
  IF p_request_id IS NULL OR p_lease_token IS NULL OR p_run_id IS NULL OR p_market_date IS NULL OR p_requested_packet_id IS NULL OR p_requested_report_id IS NULL OR p_requested_kind NOT IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday') OR p_requested_idempotency_key !~ '^[0-9a-f]{64}$' OR p_requested_report_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid report origin' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'record_report' OR v_request.status<>'claimed' OR v_request.lease_token<>p_lease_token OR v_request.run_id IS NOT NULL THEN RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001'; END IF;
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NULL THEN RETURN jsonb_build_object('scheduled',false,'duplicate',false); END IF;
  IF v_run.scheduled_market_date IS DISTINCT FROM p_market_date OR NOT ((v_run.scheduled_phase='pre-market' AND p_requested_kind IN ('morning','monthly')) OR (v_run.scheduled_phase='intraday' AND p_requested_kind IN ('intraday','urgent')) OR (v_run.scheduled_phase='post-market' AND p_requested_kind IN ('weekly','monthly','theme','urgent'))) THEN RAISE EXCEPTION 'scheduled report origin mismatch' USING ERRCODE='22023'; END IF;
  SELECT packet_hash INTO v_packet_hash FROM public.market_evidence_packets WHERE id=p_requested_packet_id AND run_id=p_run_id AND status='completed';
  IF NOT FOUND THEN RAISE EXCEPTION 'completed evidence packet unavailable' USING ERRCODE='22023'; END IF;
  v_expected_key:=encode(extensions.digest(convert_to('v2:'||p_requested_kind||':'||p_market_date::text||':'||v_packet_hash||':'||p_requested_report_hash,'UTF8'),'sha256'),'hex');
  v_expected_id:=(substr(v_expected_key,1,8)||'-'||substr(v_expected_key,9,4)||'-5'||substr(v_expected_key,14,3)||'-8'||substr(v_expected_key,18,3)||'-'||substr(v_expected_key,21,12))::uuid;
  IF p_requested_idempotency_key<>v_expected_key OR p_requested_report_id<>v_expected_id THEN RAISE EXCEPTION 'scheduled report identity mismatch' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_origin FROM public.market_report_request_origins WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_origin.run_id IS DISTINCT FROM p_run_id OR v_origin.scheduled_phase IS DISTINCT FROM v_run.scheduled_phase OR v_origin.market_date IS DISTINCT FROM p_market_date OR v_origin.requested_kind IS DISTINCT FROM p_requested_kind OR v_origin.requested_report_id IS DISTINCT FROM p_requested_report_id OR v_origin.requested_packet_id IS DISTINCT FROM p_requested_packet_id OR v_origin.requested_idempotency_key IS DISTINCT FROM p_requested_idempotency_key OR v_origin.requested_report_hash IS DISTINCT FROM p_requested_report_hash THEN RAISE EXCEPTION 'scheduled report origin idempotency mismatch' USING ERRCODE='22023'; END IF;
    RETURN jsonb_build_object('scheduled',true,'duplicate',true);
  END IF;
  INSERT INTO public.market_report_request_origins(request_id,run_id,scheduled_phase,market_date,requested_kind,requested_report_id,requested_packet_id,requested_idempotency_key,requested_report_hash) VALUES(p_request_id,p_run_id,v_run.scheduled_phase,p_market_date,p_requested_kind,p_requested_report_id,p_requested_packet_id,p_requested_idempotency_key,p_requested_report_hash);
  RETURN jsonb_build_object('scheduled',true,'duplicate',false);
END; $$;

CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_run public.analysis_runs%ROWTYPE; v_counts JSONB; v_statuses JSONB; v_ids JSONB; v_status TEXT;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM public.market_intelligence_runs i WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase AND i.market_date=v_run.scheduled_market_date) OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_run_events e WHERE e.run_id=p_run_id AND e.status='completed') OR NOT EXISTS (SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id) OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish' AND q.status='completed') OR NOT EXISTS (SELECT 1 FROM public.market_publications p WHERE p.run_id=p_run_id AND p.market_date=v_run.scheduled_market_date AND p.phase=v_run.scheduled_phase AND p.status='suppressed') THEN RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q JOIN public.market_report_request_origins o ON o.request_id=q.request_id JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed' AND jsonb_typeof(q.response)='object' AND q.response->>'report_hash'=r.report_hash AND q.response->>'rendered_hash'=r.rendered_hash AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase AND o.market_date=v_run.scheduled_market_date AND o.requested_packet_id=r.packet_id AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly')) OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent')) OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent')))) THEN RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q JOIN public.market_report_request_origins o ON o.request_id=q.request_id JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END JOIN public.market_report_publications p ON p.report_id=r.id WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed' AND jsonb_typeof(q.response)='object' AND q.response->>'report_hash'=r.report_hash AND q.response->>'rendered_hash'=r.rendered_hash AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase AND o.market_date=v_run.scheduled_market_date AND o.requested_packet_id=r.packet_id AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly')) OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent')) OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent'))) AND p.status IN ('delivered','suppressed')) THEN RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023'; END IF;
  END IF;
  SELECT jsonb_build_object('evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id)) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb) INTO v_statuses FROM (SELECT status FROM public.market_publications WHERE run_id=p_run_id UNION ALL SELECT p.status FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb) INTO v_ids FROM (SELECT value AS message_id FROM public.market_publications p CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE p.run_id=p_run_id UNION ALL SELECT value AS message_id FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE r.run_id=p_run_id) message_ids;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM public.market_gateway_requests WHERE run_id=p_run_id AND status='failed') OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain'] THEN 'partial' WHEN NOT EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND p.status='delivered') AND EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id AND r.run_id=p_run_id WHERE p.status='suppressed') THEN 'suppressed' ELSE 'completed' END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object('run_id',p_run_id,'status',v_status,'write_counts',v_counts,'publication_statuses',v_statuses,'telegram_message_ids',v_ids);
END; $$;
REVOKE ALL ON FUNCTION public.record_market_report_origin(UUID,UUID,UUID,DATE,TEXT,UUID,UUID,TEXT,TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_report_origin(UUID,UUID,UUID,DATE,TEXT,UUID,UUID,TEXT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;

-- Final Task 9 lifecycle contract; retained after all fresh-schema dependencies.
ALTER TABLE public.market_scheduled_phase_deadlines ADD COLUMN IF NOT EXISTS grace_minutes INT NOT NULL DEFAULT 15 CHECK (grace_minutes BETWEEN 0 AND 60);
ALTER TABLE public.market_scheduled_phase_deadlines DROP CONSTRAINT IF EXISTS market_scheduled_phase_deadlines_pkey;
ALTER TABLE public.market_scheduled_phase_deadlines ADD CONSTRAINT market_scheduled_phase_deadlines_pkey PRIMARY KEY (phase,effective_on);
INSERT INTO public.market_scheduled_phase_deadlines(phase,deadline_local,grace_minutes)
VALUES ('pre-market','06:30',15),('intraday','12:00',15),('post-market','15:10',15)
ON CONFLICT (phase) DO UPDATE SET deadline_local=EXCLUDED.deadline_local,grace_minutes=EXCLUDED.grace_minutes;
CREATE OR REPLACE FUNCTION public.start_market_analysis_run(p_request_id UUID,p_lease_token UUID,p_kind TEXT,p_market_date DATE)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_run public.analysis_runs%ROWTYPE;
BEGIN
  IF p_kind NOT IN ('pre-market','intraday','post-market','on-demand') OR p_market_date IS NULL THEN RAISE EXCEPTION 'invalid analysis run slot' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'start_run' OR v_request.lease_token<>p_lease_token OR v_request.status<>'claimed' THEN RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001'; END IF;
  SELECT * INTO v_run FROM public.analysis_runs WHERE gateway_request_id=p_request_id;
  IF FOUND THEN
    IF v_run.kind IS DISTINCT FROM p_kind OR COALESCE(v_run.scheduled_market_date,p_market_date) IS DISTINCT FROM p_market_date THEN RAISE EXCEPTION 'run identity mismatch' USING ERRCODE='22023'; END IF;
    UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id;
    RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
  END IF;
  IF p_kind<>'on-demand' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('scheduled-analysis-run:'||p_market_date::text||':'||p_kind,0));
    SELECT * INTO v_run FROM public.analysis_runs WHERE scheduled_market_date=p_market_date AND scheduled_phase=p_kind FOR UPDATE;
    IF FOUND THEN UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id; RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true); END IF;
    INSERT INTO public.analysis_runs(kind,status,gateway_request_id,scheduled_market_date,scheduled_phase) VALUES(p_kind,'running',p_request_id,p_market_date,p_kind) RETURNING * INTO v_run;
  ELSE INSERT INTO public.analysis_runs(kind,status,gateway_request_id) VALUES(p_kind,'running',p_request_id) RETURNING * INTO v_run; END IF;
  UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id;
  RETURN jsonb_build_object('run_id',v_run.id,'duplicate',false);
END; $$;
CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_run public.analysis_runs%ROWTYPE; v_counts JSONB; v_statuses JSONB; v_ids JSONB; v_status TEXT;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs i WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase AND i.market_date=v_run.scheduled_market_date) OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_run_events e WHERE e.run_id=p_run_id AND e.status='completed') OR NOT EXISTS(SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id) OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS(SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS(SELECT 1 FROM public.market_gateway_requests q WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish' AND q.status='completed') OR NOT EXISTS(SELECT 1 FROM public.market_publications p WHERE p.run_id=p_run_id AND p.market_date=v_run.scheduled_market_date AND p.phase=v_run.scheduled_phase AND p.status='suppressed') THEN RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS(SELECT 1 FROM public.market_gateway_requests q WHERE q.run_id=p_run_id AND q.operation='record_report' AND q.status='completed') OR NOT EXISTS(SELECT 1 FROM public.market_reports r WHERE r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date) THEN RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date AND p.status IN ('delivered','suppressed')) THEN RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023'; END IF;
  END IF;
  SELECT jsonb_build_object('evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id)) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb) INTO v_statuses FROM (SELECT status FROM public.market_publications WHERE run_id=p_run_id UNION ALL SELECT p.status FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb) INTO v_ids FROM (SELECT value AS message_id FROM public.market_publications p CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE p.run_id=p_run_id UNION ALL SELECT value AS message_id FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE r.run_id=p_run_id) message_ids;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM public.market_gateway_requests WHERE run_id=p_run_id AND status='failed') OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain'] THEN 'partial' WHEN NOT EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND p.status='delivered') AND EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND p.status='suppressed') THEN 'suppressed' ELSE 'completed' END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object('run_id',p_run_id,'status',v_status,'write_counts',v_counts,'publication_statuses',v_statuses,'telegram_message_ids',v_ids);
END; $$;
CREATE OR REPLACE FUNCTION public.read_overdue_scheduled_market_phases(p_now TIMESTAMPTZ DEFAULT statement_timestamp()) RETURNS TABLE(market_date DATE,phase TEXT,deadline_at TIMESTAMPTZ) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_policy JSONB; v_today DATE := timezone('America/Chicago',p_now)::date; v_year TEXT := extract(year FROM v_today)::text;
BEGIN
  SELECT config INTO v_policy FROM public.market_policy_config WHERE active;
  IF NOT FOUND OR jsonb_typeof(v_policy->'nyse_holidays') IS DISTINCT FROM 'array' OR COALESCE(v_policy->>'market_calendar_year','') !~ '^[0-9]{4}$' OR v_policy->>'market_calendar_year' IS DISTINCT FROM v_year OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_policy->'nyse_holidays') AS holiday(value) WHERE jsonb_typeof(holiday.value)<>'string' OR holiday.value #>> '{}' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR left(holiday.value #>> '{}',4)<>v_year OR to_char(to_date(holiday.value #>> '{}','FXYYYY-MM-DD'),'YYYY-MM-DD')<>holiday.value #>> '{}') THEN RAISE EXCEPTION 'calendar coverage missing' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT slot_days.market_date,scheduled.phase,(slot_days.market_date::timestamp+deadline.deadline_local+make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago' FROM generate_series((SELECT min(effective_on) FROM public.market_scheduled_phase_deadlines),v_today,interval '1 day') AS days(value) CROSS JOIN LATERAL (SELECT days.value::date AS market_date) slot_days CROSS JOIN (VALUES ('pre-market'::text),('intraday'::text),('post-market'::text)) AS scheduled(phase) JOIN LATERAL (SELECT deadline_local,grace_minutes FROM public.market_scheduled_phase_deadlines deadline WHERE deadline.phase=scheduled.phase AND deadline.effective_on<=slot_days.market_date ORDER BY deadline.effective_on DESC LIMIT 1) deadline ON true WHERE extract(isodow FROM slot_days.market_date) BETWEEN 1 AND 5 AND ((slot_days.market_date::timestamp+deadline.deadline_local+make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago')<p_now AND NOT (v_policy->'nyse_holidays' ? slot_days.market_date::text) AND NOT EXISTS(SELECT 1 FROM public.analysis_runs run WHERE run.scheduled_market_date=slot_days.market_date AND run.scheduled_phase=scheduled.phase AND run.status IN ('completed','suppressed')) ORDER BY slot_days.market_date,scheduled.phase;
END; $$;
DROP FUNCTION IF EXISTS public.start_market_analysis_run(UUID, UUID, TEXT);
End superseded early mirror. */

UPDATE public.suggestions
SET action = CASE
  WHEN lower(trim(action)) IN ('add slowly','add/dca','dca','dca/add slowly') THEN 'add'
  WHEN lower(trim(action)) = 'hold/wait' THEN 'hold'
  WHEN lower(trim(action)) IN ('study','watch - alert at $285','watch - alert at $610','watch/add on pullback') THEN 'watch'
  ELSE CASE lower(trim(action))
    WHEN 'trim' THEN 'reduce'
    WHEN 'exit' THEN 'sell'
    ELSE lower(trim(action))
  END
END,
confidence = CASE
  WHEN confidence IS NULL THEN NULL
  WHEN lower(trim(confidence)) IN ('low-medium','medium-high') THEN 'medium'
  ELSE lower(trim(confidence))
END,
bucket = CASE WHEN bucket IS NULL THEN NULL ELSE lower(trim(bucket)) END;

UPDATE public.suggestions
SET evaluation_id = gen_random_uuid()
WHERE evaluation_id IS NULL;

INSERT INTO public.decision_evaluations (
  id, request_id, run_id, candidate_id, policy_version, input_digest,
  raw_action, final_action, policy_status, reason_codes, explanations,
  normalized, evidence, analyst, checker
)
SELECT
  s.evaluation_id, NULL, NULL, gen_random_uuid(), NULL,
  encode(extensions.digest(to_jsonb(s)::text, 'sha256'), 'hex'),
  s.action, s.action, 'legacy_unverified',
  '["LEGACY_UNVERIFIED"]'::jsonb,
  '["Historical suggestion imported without deterministic gateway review."]'::jsonb,
  jsonb_build_object('ticker', s.ticker, 'suggestion_id', s.id),
  '[]'::jsonb, '{}'::jsonb, '{}'::jsonb
FROM public.suggestions AS s
LEFT JOIN public.decision_evaluations AS e ON e.id = s.evaluation_id
WHERE e.id IS NULL;

ALTER TABLE public.suggestions
  DROP CONSTRAINT IF EXISTS suggestions_gateway_actionable_complete;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_evaluation_id_fkey') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_evaluation_id_fkey
      FOREIGN KEY (evaluation_id) REFERENCES public.decision_evaluations(id) ON DELETE RESTRICT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_action_canonical') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_action_canonical
      CHECK (action IN ('buy','add','hold','reduce','sell','watch','avoid'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_confidence_canonical') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_confidence_canonical
      CHECK (confidence IS NULL OR confidence IN ('low','medium','high'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_bucket_canonical') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_bucket_canonical
      CHECK (bucket IS NULL OR bucket IN ('core','growth','speculative'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_decision_source_canonical') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_decision_source_canonical
      CHECK (decision_source IN ('legacy','gateway'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_decision_mode_canonical') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_decision_mode_canonical
      CHECK (decision_mode IN ('discretionary','owner_plan'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_gateway_actionable_complete') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_gateway_actionable_complete CHECK (
      decision_source = 'legacy' OR action NOT IN ('buy','add')
      OR (decision_mode = 'owner_plan' AND bucket = 'core') OR (
        bucket IS NOT NULL AND confidence IS NOT NULL AND entry_zone_low > 0
        AND entry_zone_high >= entry_zone_low AND stop > 0 AND target > entry_zone_high
        AND valid_until IS NOT NULL AND price_at_suggestion > 0
        AND evidence_as_of IS NOT NULL AND invalidation_price > 0
      )
    );
  END IF;
END;
$$;
ALTER TABLE public.suggestions ALTER COLUMN evaluation_id SET NOT NULL;

CREATE OR REPLACE FUNCTION public.reject_decision_evaluation_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'decision evaluations are append-only' USING ERRCODE = '55000';
END;
$$;
DROP TRIGGER IF EXISTS decision_evaluations_append_only ON public.decision_evaluations;
CREATE TRIGGER decision_evaluations_append_only
BEFORE UPDATE OR DELETE ON public.decision_evaluations
FOR EACH ROW EXECUTE FUNCTION public.reject_decision_evaluation_mutation();

ALTER TABLE public.market_gateway_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_policy_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decision_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_publications ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.activate_market_policy_config(p_version INT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_config JSONB;
BEGIN
  SELECT config INTO v_config FROM public.market_policy_config
  WHERE version = p_version FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'policy version unavailable' USING ERRCODE = '22023'; END IF;
  UPDATE public.market_policy_config SET active = false, activated_at = NULL WHERE active;
  UPDATE public.market_policy_config SET active = true, activated_at = now() WHERE version = p_version;
  RETURN jsonb_build_object('version', p_version, 'active', true);
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_market_gateway_request(
  p_request_id UUID, p_operation TEXT, p_run_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_lease UUID;
BEGIN
  IF p_operation NOT IN ('start_run','read_context','record_artifacts','grade_due_decisions','evaluate_and_publish','finish_run')
     OR (p_operation = 'start_run' AND p_run_id IS NOT NULL)
     OR (p_operation <> 'start_run' AND p_run_id IS NULL) THEN
    RAISE EXCEPTION 'invalid request identity' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id = p_request_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('market_gateway_rate', 0));
    IF (SELECT count(*) FROM public.market_gateway_requests WHERE created_at >= now()-interval '1 hour') >= 100
       OR (p_run_id IS NOT NULL AND (SELECT count(*) FROM public.market_gateway_requests WHERE run_id=p_run_id) >= 20) THEN
      RAISE EXCEPTION 'gateway rate limit exceeded' USING ERRCODE = '54000';
    END IF;
    v_lease := gen_random_uuid();
    INSERT INTO public.market_gateway_requests(request_id, operation, run_id, status, lease_token)
    VALUES (p_request_id, p_operation, p_run_id, 'claimed', v_lease);
    RETURN jsonb_build_object('claimed', true, 'lease_token', v_lease, 'attempt_count', 1);
  END IF;
  IF v_request.operation <> p_operation
     OR (p_operation <> 'start_run' AND v_request.run_id IS DISTINCT FROM p_run_id) THEN
    RAISE EXCEPTION 'request identity mismatch' USING ERRCODE = '22023';
  END IF;
  IF v_request.status='failed' AND p_operation='record_report' THEN
    -- Only report delivery failures are reclaimable: their deterministic outbox
    -- key remains the authority and delivered/uncertain rows decline a send.
    v_lease := gen_random_uuid();
    UPDATE public.market_gateway_requests SET status='claimed',lease_token=v_lease,
      claimed_at=now(),attempt_count=attempt_count+1 WHERE request_id=p_request_id;
    RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,'attempt_count',v_request.attempt_count+1);
  END IF;
  IF v_request.status IN ('completed','failed') THEN
    RETURN jsonb_build_object('claimed', false, 'status', v_request.status,
      'response', v_request.response, 'response_digest', v_request.response_digest);
  END IF;
  IF v_request.claimed_at > now() - interval '5 minutes' THEN
    RETURN jsonb_build_object('claimed', false, 'status', 'REQUEST_IN_PROGRESS');
  END IF;
  v_lease := gen_random_uuid();
  UPDATE public.market_gateway_requests SET lease_token = v_lease, claimed_at = now(),
    attempt_count = attempt_count + 1 WHERE request_id = p_request_id;
  RETURN jsonb_build_object('claimed', true, 'lease_token', v_lease,
    'attempt_count', v_request.attempt_count + 1);
END;
$$;

CREATE OR REPLACE FUNCTION public.complete_market_gateway_request(
  p_request_id UUID, p_lease_token UUID, p_status TEXT, p_response JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_digest TEXT;
BEGIN
  IF p_status NOT IN ('completed','failed') OR p_response IS NULL
     OR octet_length(p_response::text) > 524288 THEN
    RAISE EXCEPTION 'invalid request completion' USING ERRCODE = '22023';
  END IF;
  v_digest := encode(extensions.digest(p_response::text, 'sha256'), 'hex');
  UPDATE public.market_gateway_requests SET status = p_status, response = p_response,
    response_digest = v_digest, finished_at = now()
  WHERE request_id = p_request_id AND lease_token = p_lease_token AND status = 'claimed';
  IF NOT FOUND THEN RAISE EXCEPTION 'request lease unavailable' USING ERRCODE = '40001'; END IF;
  RETURN jsonb_build_object('status', p_status, 'response_digest', v_digest);
END;
$$;

CREATE OR REPLACE FUNCTION public.start_market_analysis_run(
  p_request_id UUID, p_lease_token UUID, p_kind TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_run_id UUID; v_existing_kind TEXT;
BEGIN
  IF p_kind NOT IN ('pre-market','intraday','post-market','on-demand') THEN
    RAISE EXCEPTION 'invalid run kind' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id = p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation <> 'start_run' OR v_request.lease_token <> p_lease_token
     OR v_request.status <> 'claimed' THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE = '40001';
  END IF;
  SELECT id, kind INTO v_run_id, v_existing_kind FROM public.analysis_runs
  WHERE gateway_request_id = p_request_id;
  IF v_run_id IS NULL THEN
    INSERT INTO public.analysis_runs(kind, status, gateway_request_id)
    VALUES (p_kind, 'running', p_request_id) RETURNING id INTO v_run_id;
  ELSIF v_existing_kind IS DISTINCT FROM p_kind THEN
    RAISE EXCEPTION 'run kind mismatch' USING ERRCODE = '22023';
  END IF;
  UPDATE public.market_gateway_requests SET run_id = v_run_id WHERE request_id = p_request_id;
  RETURN jsonb_build_object('run_id', v_run_id, 'kind', p_kind);
END;
$$;

CREATE OR REPLACE FUNCTION public.apply_market_artifacts(
  p_request_id UUID, p_run_id UUID, p_lease_token UUID, p_mutations JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE; v_item JSONB; v_kind TEXT;
  v_obs INT := 0; v_snap INT := 0; v_lesson INT := 0; v_radar_up INT := 0;
  v_radar_del INT := 0; v_watch_open INT := 0; v_watch_close INT := 0;
  v_watch_ids JSONB := '[]'::jsonb; v_watch_id BIGINT; v_rows INT; v_receipt JSONB;
BEGIN
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id = p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation <> 'record_artifacts' OR v_request.run_id <> p_run_id
     OR v_request.lease_token <> p_lease_token OR v_request.status <> 'claimed' THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE = '40001';
  END IF;
  PERFORM 1 FROM public.analysis_runs WHERE id = p_run_id AND status = 'running' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE = '22023'; END IF;
  IF jsonb_typeof(p_mutations) <> 'array' OR jsonb_array_length(p_mutations) > 100 THEN
    RAISE EXCEPTION 'invalid artifact batch' USING ERRCODE = '22023';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_mutations) LOOP
    IF jsonb_typeof(v_item) <> 'object' OR NOT (v_item ? 'kind') THEN
      RAISE EXCEPTION 'invalid artifact mutation' USING ERRCODE = '22023';
    END IF;
    v_kind := v_item->>'kind';
    IF v_kind = 'observation' THEN
      IF NOT (v_item ?& ARRAY['kind','ticker','obs_date','event_type','summary','price_reaction','confidence','source'])
         OR (v_item - ARRAY['kind','ticker','obs_date','event_type','summary','price_reaction','confidence','source']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid observation mutation' USING ERRCODE = '22023';
      END IF;
      INSERT INTO public.stock_observations(ticker, obs_date, event_type, summary, price_reaction, confidence, source, run_id)
      VALUES (v_item->>'ticker', (v_item->>'obs_date')::date, v_item->>'event_type', v_item->>'summary',
        v_item->>'price_reaction', v_item->>'confidence', v_item->>'source', p_run_id);
      v_obs := v_obs + 1;
    ELSIF v_kind = 'snapshot' THEN
      IF NOT (v_item ?& ARRAY['kind','snap_date','ticker','close','day_move_pct','rsi14','sma50','sma200','macd_hist'])
         OR (v_item - ARRAY['kind','snap_date','ticker','close','day_move_pct','rsi14','sma50','sma200','macd_hist']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid snapshot mutation' USING ERRCODE = '22023';
      END IF;
      INSERT INTO public.daily_snapshots(snap_date,ticker,close,day_move_pct,rsi14,sma50,sma200,macd_hist,run_id)
      VALUES ((v_item->>'snap_date')::date,v_item->>'ticker',(v_item->>'close')::numeric,
        (v_item->>'day_move_pct')::numeric,(v_item->>'rsi14')::numeric,(v_item->>'sma50')::numeric,
        (v_item->>'sma200')::numeric,(v_item->>'macd_hist')::numeric,p_run_id)
      ON CONFLICT (snap_date,ticker) DO UPDATE SET close=EXCLUDED.close,day_move_pct=EXCLUDED.day_move_pct,
        rsi14=EXCLUDED.rsi14,sma50=EXCLUDED.sma50,sma200=EXCLUDED.sma200,
        macd_hist=EXCLUDED.macd_hist,run_id=EXCLUDED.run_id;
      v_snap := v_snap + 1;
    ELSIF v_kind = 'lesson' THEN
      IF NOT (v_item ?& ARRAY['kind','entry_date','category','content'])
         OR (v_item - ARRAY['kind','entry_date','category','content']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid lesson mutation' USING ERRCODE = '22023';
      END IF;
      INSERT INTO public.lessons(entry_date,category,content,run_id)
      VALUES ((v_item->>'entry_date')::date,v_item->>'category',v_item->>'content',p_run_id);
      v_lesson := v_lesson + 1;
    ELSIF v_kind = 'radar_upsert' THEN
      IF NOT (v_item ?& ARRAY['kind','ticker','added','last_seen','days_relevant','reason','bucket_guess','promoted','promoted_on'])
         OR (v_item - ARRAY['kind','ticker','added','last_seen','days_relevant','reason','bucket_guess','promoted','promoted_on']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid radar mutation' USING ERRCODE = '22023';
      END IF;
      INSERT INTO public.radar(ticker,added,last_seen,days_relevant,reason,bucket_guess,promoted,promoted_on,updated_run_id)
      VALUES (v_item->>'ticker',(v_item->>'added')::date,(v_item->>'last_seen')::date,
        (v_item->>'days_relevant')::int,v_item->>'reason',v_item->>'bucket_guess',
        (v_item->>'promoted')::boolean,(v_item->>'promoted_on')::date,p_run_id)
      ON CONFLICT (ticker) DO UPDATE SET last_seen=EXCLUDED.last_seen,days_relevant=EXCLUDED.days_relevant,
        reason=EXCLUDED.reason,bucket_guess=EXCLUDED.bucket_guess,promoted=EXCLUDED.promoted,
        promoted_on=EXCLUDED.promoted_on,updated_run_id=EXCLUDED.updated_run_id;
      v_radar_up := v_radar_up + 1;
    ELSIF v_kind = 'radar_delete' THEN
      IF NOT (v_item ?& ARRAY['kind','ticker']) OR (v_item - ARRAY['kind','ticker']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid radar delete mutation' USING ERRCODE = '22023';
      END IF;
      DELETE FROM public.radar WHERE ticker = v_item->>'ticker';
      v_radar_del := v_radar_del + 1;
    ELSIF v_kind = 'paper_watch_create' THEN
      IF NOT (v_item ?& ARRAY['kind','ticker','created','entry_ref_price','target_price','hypothetical_amount','thesis','horizon','agent_view_at_open','agent_score_at_open'])
         OR (v_item - ARRAY['kind','ticker','created','entry_ref_price','target_price','hypothetical_amount','thesis','horizon','agent_view_at_open','agent_score_at_open']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid paper watch create mutation' USING ERRCODE = '22023';
      END IF;
      INSERT INTO public.paper_watches(ticker,created,entry_ref_price,target_price,hypothetical_amount,
        thesis,horizon,status,agent_view_at_open,agent_score_at_open,opened_run_id)
      VALUES (v_item->>'ticker',(v_item->>'created')::date,(v_item->>'entry_ref_price')::numeric,
        (v_item->>'target_price')::numeric,(v_item->>'hypothetical_amount')::numeric,
        v_item->>'thesis',v_item->>'horizon','active',v_item->>'agent_view_at_open',
        (v_item->>'agent_score_at_open')::int,p_run_id) RETURNING id INTO v_watch_id;
      v_watch_ids := v_watch_ids || jsonb_build_array(v_watch_id); v_watch_open := v_watch_open + 1;
    ELSIF v_kind = 'paper_watch_close' THEN
      IF NOT (v_item ?& ARRAY['kind','watch_id','ticker','closed_date','close_price'])
         OR (v_item - ARRAY['kind','watch_id','ticker','closed_date','close_price']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid paper watch close mutation' USING ERRCODE = '22023';
      END IF;
      UPDATE public.paper_watches SET status='closed',closed_date=(v_item->>'closed_date')::date,
        close_price=(v_item->>'close_price')::numeric,closed_run_id=p_run_id
      WHERE id=(v_item->>'watch_id')::bigint AND ticker=v_item->>'ticker' AND status='active';
      GET DIAGNOSTICS v_rows = ROW_COUNT;
      IF v_rows <> 1 THEN RAISE EXCEPTION 'paper watch unavailable' USING ERRCODE = '22023'; END IF;
      v_watch_close := v_watch_close + 1;
    ELSE
      RAISE EXCEPTION 'unsupported artifact kind' USING ERRCODE = '22023';
    END IF;
  END LOOP;
  v_receipt := jsonb_build_object('counts',jsonb_build_object('observation',v_obs,'snapshot',v_snap,
    'lesson',v_lesson,'radar_upsert',v_radar_up,'radar_delete',v_radar_del,
    'paper_watch_create',v_watch_open,'paper_watch_close',v_watch_close),'paper_watch_ids',v_watch_ids);
  UPDATE public.market_gateway_requests SET status='completed',response=v_receipt,
    response_digest=encode(extensions.digest(v_receipt::text,'sha256'),'hex'),finished_at=now()
  WHERE request_id=p_request_id AND lease_token=p_lease_token AND status='claimed';
  IF NOT FOUND THEN RAISE EXCEPTION 'request lease unavailable' USING ERRCODE = '40001'; END IF;
  RETURN v_receipt;
END;
$$;

CREATE OR REPLACE FUNCTION public.apply_market_decision_bundle(
  p_request_id UUID, p_run_id UUID, p_lease_token UUID, p_policy_version INT,
  p_evaluations JSONB, p_suggestions JSONB, p_publication JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE; v_existing public.market_publications%ROWTYPE;
  v_item JSONB; v_eval public.decision_evaluations%ROWTYPE; v_pub_id UUID := gen_random_uuid();
  v_holding JSONB; v_rows INT; v_eval_count INT := 0; v_suggestion_count INT := 0;
  v_is_holiday BOOLEAN := false;
BEGIN
  IF jsonb_typeof(p_evaluations)<>'array' OR jsonb_typeof(p_suggestions)<>'array'
     OR jsonb_typeof(p_publication)<>'object' THEN
    RAISE EXCEPTION 'invalid decision transaction' USING ERRCODE = '22023';
  END IF;
  v_is_holiday := COALESCE(p_publication->>'kind'='holiday' AND p_publication->>'phase'='pre-market'
    AND p_run_id IS NULL AND jsonb_array_length(p_evaluations)=0
    AND jsonb_array_length(p_suggestions)=0, false);
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.lease_token <> p_lease_token OR v_request.status <> 'claimed'
     OR (v_is_holiday AND (v_request.operation <> 'start_run' OR v_request.run_id IS NOT NULL))
     OR (NOT v_is_holiday AND (v_request.operation <> 'evaluate_and_publish'
       OR v_request.run_id IS DISTINCT FROM p_run_id OR p_run_id IS NULL)) THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE = '40001';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_request_id::text, 0));
  SELECT * INTO v_existing FROM public.market_publications WHERE idempotency_key=p_request_id;
  IF FOUND THEN RETURN jsonb_build_object('publication_id',v_existing.id,'status',v_existing.status,'duplicate',true); END IF;
  IF v_is_holiday THEN
    SELECT * INTO v_existing FROM public.market_publications
    WHERE market_date=(p_publication->>'market_date')::date AND phase='pre-market' AND kind='holiday';
    IF FOUND THEN RETURN jsonb_build_object('publication_id',v_existing.id,'status',v_existing.status,'duplicate',true); END IF;
  ELSE
    PERFORM 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE = '22023'; END IF;
    SELECT * INTO v_existing FROM public.market_publications WHERE run_id=p_run_id;
    IF FOUND THEN RETURN jsonb_build_object('code','RUN_ALREADY_EVALUATED','publication_id',v_existing.id,'status',v_existing.status); END IF;
  END IF;
  PERFORM 1 FROM public.market_policy_config WHERE version=p_policy_version AND active;
  IF NOT FOUND THEN RAISE EXCEPTION 'active policy unavailable' USING ERRCODE = '22023'; END IF;
  IF jsonb_array_length(p_evaluations)>80 OR jsonb_array_length(p_suggestions)>80 THEN
    RAISE EXCEPTION 'invalid decision transaction' USING ERRCODE = '22023';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_evaluations) LOOP
    IF NOT (v_item ?& ARRAY['id','candidate_id','input_digest','raw_action','final_action','policy_status','reason_codes','explanations','normalized','evidence','analyst','checker'])
       OR (v_item - ARRAY['id','candidate_id','input_digest','raw_action','final_action','policy_status','reason_codes','explanations','normalized','evidence','analyst','checker']) <> '{}'::jsonb
       OR v_item->>'policy_status' NOT IN ('approved','downgraded','vetoed') THEN
      RAISE EXCEPTION 'invalid evaluation' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.decision_evaluations(id,request_id,run_id,candidate_id,policy_version,input_digest,
      raw_action,final_action,policy_status,reason_codes,explanations,normalized,evidence,analyst,checker)
    VALUES ((v_item->>'id')::uuid,p_request_id,p_run_id,(v_item->>'candidate_id')::uuid,p_policy_version,
      v_item->>'input_digest',v_item->>'raw_action',NULLIF(v_item->>'final_action',''),v_item->>'policy_status',
      v_item->'reason_codes',v_item->'explanations',v_item->'normalized',v_item->'evidence',
      v_item->'analyst',v_item->'checker');
    v_eval_count := v_eval_count + 1;
  END LOOP;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_suggestions) LOOP
    IF NOT (v_item ?& ARRAY['evaluation_id','candidate_id','date','ticker','action','decision_mode','bucket','depth',
      'entry_zone_low','entry_zone_high','valid_until','stop','target','confidence','bull','bear',
      'decisive_factor','risk_verdict','reason','score','price_at_suggestion','evidence_as_of','invalidation_price'])
       OR (v_item - ARRAY['evaluation_id','candidate_id','date','ticker','action','decision_mode','bucket','depth',
      'entry_zone_low','entry_zone_high','valid_until','stop','target','confidence','bull','bear',
      'decisive_factor','risk_verdict','reason','score','price_at_suggestion','evidence_as_of','invalidation_price']) <> '{}'::jsonb THEN
      RAISE EXCEPTION 'invalid suggestion' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_eval FROM public.decision_evaluations
    WHERE id=(v_item->>'evaluation_id')::uuid AND request_id=p_request_id AND run_id=p_run_id;
    IF NOT FOUND OR v_eval.candidate_id IS DISTINCT FROM (v_item->>'candidate_id')::uuid
       OR v_eval.final_action IS DISTINCT FROM v_item->>'action' OR v_eval.policy_version IS DISTINCT FROM p_policy_version
       OR v_eval.policy_status NOT IN ('approved','downgraded')
       OR v_eval.normalized->>'ticker' IS DISTINCT FROM v_item->>'ticker' THEN
      RAISE EXCEPTION 'suggestion evaluation mismatch' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.suggestions(date,ticker,action,decision_mode,bucket,depth,entry_zone_low,entry_zone_high,
      valid_until,stop,target,confidence,bull,bear,decisive_factor,risk_verdict,reason,score,
      price_at_suggestion,run_id,evidence_as_of,invalidation_price,evaluation_id,decision_source)
    VALUES ((v_item->>'date')::date,v_item->>'ticker',v_item->>'action',v_item->>'decision_mode',v_item->>'bucket',v_item->>'depth',
      (v_item->>'entry_zone_low')::numeric,(v_item->>'entry_zone_high')::numeric,(v_item->>'valid_until')::date,
      (v_item->>'stop')::numeric,(v_item->>'target')::numeric,v_item->>'confidence',v_item->>'bull',v_item->>'bear',
      v_item->>'decisive_factor',v_item->>'risk_verdict',v_item->>'reason',(v_item->>'score')::int,
      (v_item->>'price_at_suggestion')::numeric,p_run_id,(v_item->>'evidence_as_of')::timestamptz,
      (v_item->>'invalidation_price')::numeric,v_eval.id,'gateway');
    v_suggestion_count := v_suggestion_count + 1;
  END LOOP;
  IF p_publication ? 'holding_state' THEN
    IF jsonb_typeof(p_publication->'holding_state') <> 'array' THEN RAISE EXCEPTION 'invalid holding state'; END IF;
    FOR v_holding IN SELECT value FROM jsonb_array_elements(p_publication->'holding_state') LOOP
      IF NOT (v_holding ? 'ticker') OR (v_holding - ARRAY['ticker','high_water_price','stop_alert_active',
        'stop_near_alert_active','target_near_alert_active','target_alert_active']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid holding state keys' USING ERRCODE = '22023';
      END IF;
      UPDATE public.holdings SET
        high_water_price=CASE WHEN v_holding ? 'high_water_price' THEN
          GREATEST(COALESCE(high_water_price,0),(v_holding->>'high_water_price')::numeric) ELSE high_water_price END,
        stop_alert_active=CASE WHEN v_holding ? 'stop_alert_active' THEN (v_holding->>'stop_alert_active')::boolean ELSE stop_alert_active END,
        stop_near_alert_active=CASE WHEN v_holding ? 'stop_near_alert_active' THEN (v_holding->>'stop_near_alert_active')::boolean ELSE stop_near_alert_active END,
        target_near_alert_active=CASE WHEN v_holding ? 'target_near_alert_active' THEN (v_holding->>'target_near_alert_active')::boolean ELSE target_near_alert_active END,
        target_alert_active=CASE WHEN v_holding ? 'target_alert_active' THEN (v_holding->>'target_alert_active')::boolean ELSE target_alert_active END
      WHERE ticker=v_holding->>'ticker';
      GET DIAGNOSTICS v_rows = ROW_COUNT;
      IF v_rows <> 1 THEN RAISE EXCEPTION 'holding state target unavailable' USING ERRCODE = '22023'; END IF;
    END LOOP;
  END IF;
  IF NOT (p_publication ?& ARRAY['market_date','phase','kind','template_version','rendered_body','rendered_hash','status','holding_state'])
     OR (p_publication - ARRAY['market_date','phase','kind','template_version','rendered_body','rendered_hash','status','holding_state']) <> '{}'::jsonb
     OR p_publication->>'status' NOT IN ('ready','suppressed') THEN
    RAISE EXCEPTION 'invalid publication' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.market_publications(id,idempotency_key,run_id,market_date,phase,kind,
    template_version,rendered_body,rendered_hash,status)
  VALUES (v_pub_id,p_request_id,p_run_id,(p_publication->>'market_date')::date,p_publication->>'phase',
    p_publication->>'kind',(p_publication->>'template_version')::int,p_publication->>'rendered_body',
    p_publication->>'rendered_hash',p_publication->>'status');
  RETURN jsonb_build_object('publication_id',v_pub_id,'status',p_publication->>'status',
    'evaluation_count',v_eval_count,'suggestion_count',v_suggestion_count,'duplicate',false);
END;
$$;

CREATE OR REPLACE FUNCTION public.import_legacy_suggestion(p_row JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_action TEXT; v_eval_id UUID := gen_random_uuid(); v_candidate_id UUID := gen_random_uuid(); v_id BIGINT;
BEGIN
  IF jsonb_typeof(p_row)<>'object' OR NOT (p_row ?& ARRAY['date','ticker','action'])
     OR (p_row - ARRAY['date','ticker','action','bucket','depth','entry_zone_low','entry_zone_high','valid_until',
       'stop','target','confidence','bull','bear','decisive_factor','risk_verdict','invalidation_level','reason',
       'score','price_at_suggestion','evidence_as_of','invalidation_price']) <> '{}'::jsonb THEN
    RAISE EXCEPTION 'invalid legacy suggestion' USING ERRCODE = '22023';
  END IF;
  v_action := CASE lower(trim(p_row->>'action')) WHEN 'trim' THEN 'reduce' WHEN 'exit' THEN 'sell'
    ELSE lower(trim(p_row->>'action')) END;
  IF v_action NOT IN ('buy','add','hold','reduce','sell','watch','avoid') THEN
    RAISE EXCEPTION 'unknown legacy action' USING ERRCODE = '22023';
  END IF;
  IF p_row->>'ticker' !~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$' THEN
    RAISE EXCEPTION 'invalid legacy ticker' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.decision_evaluations(id,candidate_id,input_digest,raw_action,final_action,
    policy_status,reason_codes,explanations,normalized)
  VALUES (v_eval_id,v_candidate_id,encode(extensions.digest(p_row::text,'sha256'),'hex'),v_action,v_action,
    'legacy_unverified','["LEGACY_UNVERIFIED"]'::jsonb,
    '["Imported by local administrator without gateway review."]'::jsonb,
    jsonb_build_object('ticker',p_row->>'ticker'));
  INSERT INTO public.suggestions(date,ticker,action,bucket,depth,entry_zone_low,entry_zone_high,
    valid_until,stop,target,confidence,bull,bear,decisive_factor,risk_verdict,invalidation_level,
    reason,score,price_at_suggestion,evidence_as_of,invalidation_price,evaluation_id,decision_source)
  VALUES ((p_row->>'date')::date,p_row->>'ticker',v_action,lower(p_row->>'bucket'),p_row->>'depth',
    (p_row->>'entry_zone_low')::numeric,(p_row->>'entry_zone_high')::numeric,(p_row->>'valid_until')::date,
    (p_row->>'stop')::numeric,(p_row->>'target')::numeric,lower(p_row->>'confidence'),p_row->>'bull',p_row->>'bear',
    p_row->>'decisive_factor',p_row->>'risk_verdict',p_row->>'invalidation_level',p_row->>'reason',
    (p_row->>'score')::int,(p_row->>'price_at_suggestion')::numeric,(p_row->>'evidence_as_of')::timestamptz,
    (p_row->>'invalidation_price')::numeric,v_eval_id,'legacy') RETURNING id INTO v_id;
  RETURN jsonb_build_object('suggestion_id',v_id,'evaluation_id',v_eval_id);
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_market_publication(p_request_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_pub public.market_publications%ROWTYPE; v_lease UUID;
BEGIN
  SELECT * INTO v_pub FROM public.market_publications WHERE idempotency_key=p_request_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'publication unavailable' USING ERRCODE = '22023'; END IF;
  IF v_pub.status IN ('delivered','delivery_unknown','suppressed') THEN
    RETURN jsonb_build_object('claimed',false,'status',v_pub.status,'publication_id',v_pub.id);
  END IF;
  IF v_pub.status='sending' THEN
    IF v_pub.sending_started_at >= now()-interval '5 minutes' THEN
      RETURN jsonb_build_object('claimed',false,'status','sending','publication_id',v_pub.id);
    END IF;
    UPDATE public.market_publications SET status='delivery_unknown',lease_token=NULL,
      error='SEND_LEASE_EXPIRED',updated_at=now() WHERE id=v_pub.id;
    RETURN jsonb_build_object('claimed',false,'status','delivery_unknown','publication_id',v_pub.id);
  END IF;
  IF v_pub.status NOT IN ('ready','delivery_failed') THEN RAISE EXCEPTION 'invalid publication state'; END IF;
  v_lease := gen_random_uuid();
  UPDATE public.market_publications SET status='sending',lease_token=v_lease,sending_started_at=now(),
    attempt_count=attempt_count+1,updated_at=now(),error=NULL WHERE id=v_pub.id;
  RETURN jsonb_build_object('claimed',true,'status','sending','publication_id',v_pub.id,
    'lease_token',v_lease,'rendered_body',v_pub.rendered_body);
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_publication(
  p_request_id UUID, p_lease_token UUID, p_status TEXT, p_message_ids JSONB, p_error TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_valid_ids BOOLEAN;
BEGIN
  IF p_status NOT IN ('delivered','delivery_failed','delivery_unknown')
     OR jsonb_typeof(p_message_ids)<>'array' OR char_length(COALESCE(p_error,''))>1000 THEN
    RAISE EXCEPTION 'invalid publication completion' USING ERRCODE = '22023';
  END IF;
  SELECT COALESCE(bool_and(jsonb_typeof(value)='number' AND value::text ~ '^[0-9]+$'),true)
    INTO v_valid_ids FROM jsonb_array_elements(p_message_ids);
  IF NOT v_valid_ids OR (p_status='delivered' AND jsonb_array_length(p_message_ids)=0) THEN
    RAISE EXCEPTION 'invalid telegram message ids' USING ERRCODE = '22023';
  END IF;
  UPDATE public.market_publications SET status=p_status,telegram_message_ids=p_message_ids,
    delivered_at=CASE WHEN p_status='delivered' THEN now() ELSE NULL END,
    error=CASE WHEN p_error IS NULL THEN NULL ELSE regexp_replace(left(p_error,1000),'[^A-Za-z0-9_ .:-]','?','g') END,
    lease_token=NULL,updated_at=now()
  WHERE idempotency_key=p_request_id AND lease_token=p_lease_token AND status='sending';
  IF NOT FOUND THEN RAISE EXCEPTION 'publication lease unavailable' USING ERRCODE = '40001'; END IF;
  RETURN jsonb_build_object('status',p_status,'telegram_message_ids',p_message_ids);
END;
$$;

REVOKE ALL ON FUNCTION public.activate_market_policy_config(INT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.claim_market_gateway_request(UUID, TEXT, UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.complete_market_gateway_request(UUID, UUID, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.start_market_analysis_run(UUID, UUID, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_market_artifacts(UUID, UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_market_decision_bundle(UUID, UUID, UUID, INT, JSONB, JSONB, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.import_legacy_suggestion(JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.claim_market_publication(UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_publication(UUID, UUID, TEXT, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.activate_market_policy_config(INT) TO service_role;
GRANT EXECUTE ON FUNCTION public.claim_market_gateway_request(UUID, TEXT, UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.complete_market_gateway_request(UUID, UUID, TEXT, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.start_market_analysis_run(UUID, UUID, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_market_artifacts(UUID, UUID, UUID, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_market_decision_bundle(UUID, UUID, UUID, INT, JSONB, JSONB, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.import_legacy_suggestion(JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.claim_market_publication(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_publication(UUID, UUID, TEXT, JSONB, TEXT) TO service_role;

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

-- Deterministic grading of final policy-evaluated suggestions.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM public.suggestion_grades
    WHERE suggestion_id IS NOT NULL AND horizon_days IS NOT NULL
    GROUP BY suggestion_id, horizon_days HAVING count(*) > 1
  ) THEN
    RAISE EXCEPTION 'duplicate suggestion grades require review before outcome migration';
  END IF;
END;
$$;

ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS benchmark_ticker TEXT;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS stock_return_pct NUMERIC;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS benchmark_return_pct NUMERIC;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS excess_return_pct NUMERIC;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS mfe_pct NUMERIC;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS mae_pct NUMERIC;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS entry_hit_at DATE;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS stop_hit_at DATE;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS target_hit_at DATE;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS invalidation_hit_at DATE;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS coverage_status TEXT;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS horizon_sessions INT;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS policy_version INT;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS final_action TEXT;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS direction_success BOOLEAN;

CREATE UNIQUE INDEX IF NOT EXISTS idx_suggestion_grades_suggestion_horizon
  ON public.suggestion_grades(suggestion_id, horizon_days);
CREATE INDEX IF NOT EXISTS idx_suggestion_grades_coverage
  ON public.suggestion_grades(coverage_status, horizon_days, graded_at DESC);

CREATE OR REPLACE FUNCTION public.get_due_market_decisions(p_limit INT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE v_result JSONB;
BEGIN
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 50 THEN
    RAISE EXCEPTION 'invalid decision limit' USING ERRCODE = '22023';
  END IF;
  SELECT COALESCE(jsonb_agg(row_value ORDER BY decision_date, suggestion_id), '[]'::jsonb)
  INTO v_result
  FROM (
    SELECT
      s.date AS decision_date,
      s.id AS suggestion_id,
      jsonb_build_object(
        'suggestion_id', s.id,
        'decision_date', s.date,
        'ticker', s.ticker,
        'bucket', s.bucket,
        'final_action', e.final_action,
        'confidence', s.confidence,
        'policy_version', e.policy_version,
        'decision_price', s.price_at_suggestion,
        'entry_zone_low', s.entry_zone_low,
        'entry_zone_high', s.entry_zone_high,
        'stop', s.stop,
        'target', s.target,
        'invalidation_price', s.invalidation_price,
        'completed_horizons', COALESCE(
          to_jsonb(array_agg(DISTINCT g.horizon_days ORDER BY g.horizon_days)
            FILTER (WHERE g.coverage_status = 'complete')),
          '[]'::jsonb
        )
      ) AS row_value
    FROM public.suggestions AS s
    JOIN public.decision_evaluations AS e ON e.id = s.evaluation_id
    LEFT JOIN public.suggestion_grades AS g ON g.suggestion_id = s.id
    WHERE s.decision_source = 'gateway'
      AND e.policy_version IS NOT NULL
      AND s.bucket IS NOT NULL
      AND s.confidence IS NOT NULL
      AND s.price_at_suggestion IS NOT NULL
      AND s.date >= CURRENT_DATE - 370
    GROUP BY s.id, s.date, s.ticker, s.bucket, e.final_action, s.confidence,
      e.policy_version, s.price_at_suggestion, s.entry_zone_low, s.entry_zone_high,
      s.stop, s.target, s.invalidation_price
    HAVING count(DISTINCT g.horizon_days)
      FILTER (WHERE g.coverage_status = 'complete' AND g.horizon_days IN (5,21,63)) < 3
    ORDER BY s.date, s.id
    LIMIT p_limit
  ) AS due;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.upsert_market_outcome_grades(p_grades JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  v_item JSONB;
  v_existing public.suggestion_grades%ROWTYPE;
  v_ticker TEXT;
  v_decision_price NUMERIC;
  v_policy_version INT;
  v_final_action TEXT;
  v_horizon INT;
  v_status TEXT;
  v_expected_benchmark TEXT;
  v_inserted INT := 0;
  v_updated INT := 0;
  v_incomplete INT := 0;
  v_numeric_key TEXT;
  v_had_existing BOOLEAN;
BEGIN
  IF jsonb_typeof(p_grades) <> 'array' OR jsonb_array_length(p_grades) > 150 THEN
    RAISE EXCEPTION 'invalid outcome grade batch' USING ERRCODE = '22023';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_grades)
  LOOP
    IF jsonb_typeof(v_item) <> 'object'
      OR NOT (v_item ?& ARRAY[
        'suggestion_id','horizon_days','horizon_sessions','coverage_status','benchmark_ticker',
        'stock_return_pct','benchmark_return_pct','excess_return_pct','mfe_pct','mae_pct',
        'entry_hit_at','stop_hit_at','target_hit_at','invalidation_hit_at','policy_version',
        'final_action','direction_success'
      ])
      OR (v_item - ARRAY[
        'suggestion_id','horizon_days','horizon_sessions','coverage_status','benchmark_ticker',
        'stock_return_pct','benchmark_return_pct','excess_return_pct','mfe_pct','mae_pct',
        'entry_hit_at','stop_hit_at','target_hit_at','invalidation_hit_at','policy_version',
        'final_action','direction_success'
      ]) <> '{}'::jsonb THEN
      RAISE EXCEPTION 'invalid outcome grade' USING ERRCODE = '22023';
    END IF;

    v_horizon := (v_item->>'horizon_days')::int;
    v_status := v_item->>'coverage_status';
    IF v_horizon NOT IN (5,21,63)
      OR (v_item->>'horizon_sessions')::int NOT BETWEEN 0 AND v_horizon
      OR v_status NOT IN ('incomplete','complete','missing_history','missing_benchmark','corporate_action_review')
      OR (v_status = 'complete' AND (v_item->>'horizon_sessions')::int <> v_horizon)
      OR v_item->>'benchmark_ticker' NOT IN ('VOO','VXUS')
      OR jsonb_typeof(v_item->'direction_success') NOT IN ('boolean','null') THEN
      RAISE EXCEPTION 'invalid outcome grade status' USING ERRCODE = '22023';
    END IF;

    FOREACH v_numeric_key IN ARRAY ARRAY[
      'stock_return_pct','benchmark_return_pct','excess_return_pct','mfe_pct','mae_pct'
    ]
    LOOP
      IF v_item->>v_numeric_key IS NOT NULL
        AND (v_item->>v_numeric_key !~ '^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$'
          OR char_length(v_item->>v_numeric_key) > 40) THEN
        RAISE EXCEPTION 'invalid outcome decimal' USING ERRCODE = '22023';
      END IF;
    END LOOP;

    SELECT s.ticker, s.price_at_suggestion, e.policy_version, e.final_action
    INTO v_ticker, v_decision_price, v_policy_version, v_final_action
    FROM public.suggestions AS s
    JOIN public.decision_evaluations AS e ON e.id = s.evaluation_id
    WHERE s.id = (v_item->>'suggestion_id')::bigint
      AND s.decision_source = 'gateway';
    IF NOT FOUND OR v_policy_version IS DISTINCT FROM (v_item->>'policy_version')::int
      OR v_final_action IS DISTINCT FROM v_item->>'final_action' THEN
      RAISE EXCEPTION 'outcome provenance mismatch' USING ERRCODE = '22023';
    END IF;
    v_expected_benchmark := CASE WHEN v_ticker = 'VXUS' THEN 'VXUS' ELSE 'VOO' END;
    IF v_item->>'benchmark_ticker' <> v_expected_benchmark THEN
      RAISE EXCEPTION 'outcome benchmark mismatch' USING ERRCODE = '22023';
    END IF;
    IF v_status = 'corporate_action_review' AND (
      v_item->>'entry_hit_at' IS NOT NULL OR v_item->>'stop_hit_at' IS NOT NULL
      OR v_item->>'target_hit_at' IS NOT NULL OR v_item->>'invalidation_hit_at' IS NOT NULL
    ) THEN
      RAISE EXCEPTION 'split outcome contains raw threshold result' USING ERRCODE = '22023';
    END IF;
    IF v_item->'direction_success' <> 'null'::jsonb AND (
      v_status <> 'complete' OR v_final_action NOT IN ('buy','add','reduce','sell')
    ) THEN
      RAISE EXCEPTION 'invalid directional outcome' USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock((v_item->>'suggestion_id')::bigint);
    SELECT * INTO v_existing FROM public.suggestion_grades
    WHERE suggestion_id = (v_item->>'suggestion_id')::bigint AND horizon_days = v_horizon
    FOR UPDATE;
    v_had_existing := FOUND;
    IF v_had_existing AND v_existing.coverage_status = 'complete' THEN
      CONTINUE;
    END IF;

    INSERT INTO public.suggestion_grades(
      suggestion_id, graded_at, result, price_then, horizon_days, benchmark_ticker,
      stock_return_pct, benchmark_return_pct, excess_return_pct, mfe_pct, mae_pct,
      entry_hit_at, stop_hit_at, target_hit_at, invalidation_hit_at, coverage_status,
      horizon_sessions, policy_version, final_action, direction_success, note
    ) VALUES (
      (v_item->>'suggestion_id')::bigint, now(),
      CASE WHEN v_item->'direction_success' = 'true'::jsonb THEN 'right'
           WHEN v_item->'direction_success' = 'false'::jsonb THEN 'wrong' ELSE NULL END,
      v_decision_price, v_horizon, v_expected_benchmark,
      NULLIF(v_item->>'stock_return_pct','')::numeric,
      NULLIF(v_item->>'benchmark_return_pct','')::numeric,
      NULLIF(v_item->>'excess_return_pct','')::numeric,
      NULLIF(v_item->>'mfe_pct','')::numeric,
      NULLIF(v_item->>'mae_pct','')::numeric,
      NULLIF(v_item->>'entry_hit_at','')::date,
      NULLIF(v_item->>'stop_hit_at','')::date,
      NULLIF(v_item->>'target_hit_at','')::date,
      NULLIF(v_item->>'invalidation_hit_at','')::date,
      v_status, (v_item->>'horizon_sessions')::int,
      v_policy_version, v_final_action,
      CASE WHEN v_item->'direction_success' = 'null'::jsonb THEN NULL
           ELSE (v_item->>'direction_success')::boolean END,
      'deterministic gateway outcome'
    )
    ON CONFLICT (suggestion_id, horizon_days) DO UPDATE SET
      graded_at = EXCLUDED.graded_at,
      result = EXCLUDED.result,
      price_then = EXCLUDED.price_then,
      benchmark_ticker = EXCLUDED.benchmark_ticker,
      stock_return_pct = EXCLUDED.stock_return_pct,
      benchmark_return_pct = EXCLUDED.benchmark_return_pct,
      excess_return_pct = EXCLUDED.excess_return_pct,
      mfe_pct = EXCLUDED.mfe_pct,
      mae_pct = EXCLUDED.mae_pct,
      entry_hit_at = EXCLUDED.entry_hit_at,
      stop_hit_at = EXCLUDED.stop_hit_at,
      target_hit_at = EXCLUDED.target_hit_at,
      invalidation_hit_at = EXCLUDED.invalidation_hit_at,
      coverage_status = EXCLUDED.coverage_status,
      horizon_sessions = EXCLUDED.horizon_sessions,
      policy_version = EXCLUDED.policy_version,
      final_action = EXCLUDED.final_action,
      direction_success = EXCLUDED.direction_success,
      note = EXCLUDED.note;
    IF NOT v_had_existing THEN v_inserted := v_inserted + 1;
    ELSE v_updated := v_updated + 1;
    END IF;
    IF v_status <> 'complete' THEN v_incomplete := v_incomplete + 1; END IF;
  END LOOP;
  RETURN jsonb_build_object(
    'inserted', v_inserted, 'updated', v_updated, 'incomplete', v_incomplete
  );
END;
$$;

REVOKE ALL ON FUNCTION public.get_due_market_decisions(INT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.upsert_market_outcome_grades(JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_due_market_decisions(INT) TO service_role;
GRANT EXECUTE ON FUNCTION public.upsert_market_outcome_grades(JSONB) TO service_role;

-- Owner-only, receipt-backed alert lifecycle. Additive and idempotent.
-- Renderer v3 remains disabled by policy until the shadow rollout is approved.

ALTER TABLE public.market_gateway_requests
  DROP CONSTRAINT IF EXISTS market_gateway_requests_operation_check;
ALTER TABLE public.market_gateway_requests
  ADD CONSTRAINT market_gateway_requests_operation_check CHECK (operation IN (
    'start_run','read_context','record_artifacts','grade_due_decisions',
    'evaluate_and_publish','evaluate_alert_rules','finish_run'
  ));

ALTER TABLE public.market_publications
  ADD COLUMN IF NOT EXISTS telegram_accepted_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS public.market_alert_drafts (
  id UUID PRIMARY KEY,
  request_id UUID NOT NULL REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  source_evaluation_id UUID NOT NULL REFERENCES public.decision_evaluations(id) ON DELETE RESTRICT,
  rule_snapshot JSONB NOT NULL CHECK (
    jsonb_typeof(rule_snapshot) = 'object' AND octet_length(rule_snapshot::text) <= 32768
  ),
  fingerprint TEXT NOT NULL UNIQUE CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
  state TEXT NOT NULL DEFAULT 'draft' CHECK (state IN ('draft','armed','dismissed','expired')),
  owner_chat_id BIGINT,
  owner_user_id BIGINT,
  publication_id UUID REFERENCES public.market_publications(id) ON DELETE RESTRICT,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((owner_chat_id IS NULL) = (owner_user_id IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_market_alert_drafts_state_expiry
  ON public.market_alert_drafts(state, expires_at);
ALTER TABLE public.market_alert_drafts ADD COLUMN IF NOT EXISTS publication_id UUID
  REFERENCES public.market_publications(id) ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS public.market_alert_rules (
  id UUID PRIMARY KEY,
  source_draft_id UUID NOT NULL UNIQUE REFERENCES public.market_alert_drafts(id) ON DELETE RESTRICT,
  current_version INT NOT NULL CHECK (current_version > 0),
  state TEXT NOT NULL CHECK (state IN ('active','paused','snoozed','dismissed','expired')),
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
  profile TEXT NOT NULL CHECK (profile IN ('long_term','balanced','active')),
  severity TEXT NOT NULL CHECK (severity IN ('critical','review','update','watch','system')),
  session TEXT NOT NULL CHECK (session IN ('regular','pre_market','post_market','all')),
  confirmation TEXT NOT NULL CHECK (confirmation IN ('bar_close','two_quote')),
  conditions JSONB NOT NULL CHECK (
    jsonb_typeof(conditions) = 'array' AND jsonb_array_length(conditions) BETWEEN 1 AND 5
    AND octet_length(conditions::text) <= 16384
  ),
  cooldown_seconds INT NOT NULL CHECK (cooldown_seconds BETWEEN 60 AND 604800),
  fire_limit INT NOT NULL CHECK (fire_limit BETWEEN 1 AND 100),
  trigger_count INT NOT NULL DEFAULT 0 CHECK (trigger_count >= 0),
  valid_until TIMESTAMPTZ NOT NULL,
  snoozed_until TIMESTAMPTZ,
  owner_note TEXT NOT NULL DEFAULT '' CHECK (char_length(owner_note) <= 500),
  owner_chat_id BIGINT NOT NULL,
  owner_user_id BIGINT NOT NULL,
  armed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_triggered_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_market_alert_rules_state_ticker
  ON public.market_alert_rules(state, ticker);

CREATE TABLE IF NOT EXISTS public.market_alert_rule_versions (
  rule_id UUID NOT NULL REFERENCES public.market_alert_rules(id) ON DELETE RESTRICT,
  version INT NOT NULL CHECK (version > 0),
  snapshot JSONB NOT NULL CHECK (
    jsonb_typeof(snapshot) = 'object' AND octet_length(snapshot::text) <= 32768
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (rule_id, version)
);

CREATE TABLE IF NOT EXISTS public.market_alert_events (
  id UUID PRIMARY KEY,
  request_id UUID NOT NULL REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  rule_id UUID NOT NULL REFERENCES public.market_alert_rules(id) ON DELETE RESTRICT,
  rule_version INT NOT NULL CHECK (rule_version > 0),
  fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status IN ('triggered','unsafe_to_evaluate')),
  reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(reason_codes) = 'array'),
  observed_at TIMESTAMPTZ,
  evaluated_at TIMESTAMPTZ NOT NULL,
  persisted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  market_session TEXT NOT NULL CHECK (market_session IN ('regular','pre_market','post_market')),
  condition_results JSONB NOT NULL CHECK (
    jsonb_typeof(condition_results) = 'array' AND jsonb_array_length(condition_results) BETWEEN 1 AND 5
    AND octet_length(condition_results::text) <= 32768
  ),
  evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(evidence_ids) = 'array' AND jsonb_array_length(evidence_ids) <= 100
  ),
  publication_id UUID REFERENCES public.market_publications(id) ON DELETE RESTRICT,
  UNIQUE (rule_id, rule_version, fingerprint),
  FOREIGN KEY (rule_id, rule_version)
    REFERENCES public.market_alert_rule_versions(rule_id, version) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_market_alert_events_rule_evaluated
  ON public.market_alert_events(rule_id, evaluated_at DESC);

CREATE TABLE IF NOT EXISTS public.market_alert_actions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  draft_id UUID REFERENCES public.market_alert_drafts(id) ON DELETE RESTRICT,
  rule_id UUID REFERENCES public.market_alert_rules(id) ON DELETE RESTRICT,
  event_id UUID REFERENCES public.market_alert_events(id) ON DELETE RESTRICT,
  publication_id UUID REFERENCES public.market_publications(id) ON DELETE RESTRICT,
  telegram_update_id BIGINT NOT NULL,
  owner_chat_id BIGINT NOT NULL,
  owner_user_id BIGINT NOT NULL,
  action TEXT NOT NULL CHECK (action IN (
    'arm','pause','resume','snooze','acknowledge','dismiss'
  )),
  prior_state TEXT,
  new_state TEXT,
  expected_version INT NOT NULL CHECK (expected_version > 0),
  resulting_version INT NOT NULL CHECK (resulting_version > 0),
  snoozed_until TIMESTAMPTZ,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (telegram_update_id),
  CONSTRAINT market_alert_action_target CHECK (
    (action='acknowledge' AND draft_id IS NULL AND rule_id IS NOT NULL
      AND event_id IS NOT NULL AND publication_id IS NOT NULL)
    OR
    (action<>'acknowledge' AND event_id IS NULL AND publication_id IS NULL
      AND ((draft_id IS NOT NULL)::int + (rule_id IS NOT NULL)::int = 1))
  )
);
ALTER TABLE public.market_alert_actions
  ADD COLUMN IF NOT EXISTS event_id UUID REFERENCES public.market_alert_events(id) ON DELETE RESTRICT;
ALTER TABLE public.market_alert_actions
  ADD COLUMN IF NOT EXISTS publication_id UUID REFERENCES public.market_publications(id) ON DELETE RESTRICT;
ALTER TABLE public.market_alert_actions DROP CONSTRAINT IF EXISTS market_alert_actions_check;
ALTER TABLE public.market_alert_actions DROP CONSTRAINT IF EXISTS market_alert_action_target;
ALTER TABLE public.market_alert_actions ADD CONSTRAINT market_alert_action_target CHECK (
  (action='acknowledge' AND draft_id IS NULL AND rule_id IS NOT NULL
    AND event_id IS NOT NULL AND publication_id IS NOT NULL)
  OR
  (action<>'acknowledge' AND event_id IS NULL AND publication_id IS NULL
    AND ((draft_id IS NOT NULL)::int + (rule_id IS NOT NULL)::int = 1))
);

CREATE OR REPLACE FUNCTION public.reject_market_alert_ledger_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF TG_TABLE_NAME='market_alert_events' AND TG_OP='UPDATE'
     AND OLD.publication_id IS NULL AND NEW.publication_id IS NOT NULL
     AND (to_jsonb(OLD)-'publication_id')=(to_jsonb(NEW)-'publication_id') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'market alert ledgers are append-only' USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS market_alert_rule_versions_append_only ON public.market_alert_rule_versions;
CREATE TRIGGER market_alert_rule_versions_append_only
BEFORE UPDATE OR DELETE ON public.market_alert_rule_versions
FOR EACH ROW EXECUTE FUNCTION public.reject_market_alert_ledger_mutation();
DROP TRIGGER IF EXISTS market_alert_events_append_only ON public.market_alert_events;
CREATE TRIGGER market_alert_events_append_only
BEFORE UPDATE OR DELETE ON public.market_alert_events
FOR EACH ROW EXECUTE FUNCTION public.reject_market_alert_ledger_mutation();
DROP TRIGGER IF EXISTS market_alert_actions_append_only ON public.market_alert_actions;
CREATE TRIGGER market_alert_actions_append_only
BEFORE UPDATE OR DELETE ON public.market_alert_actions
FOR EACH ROW EXECUTE FUNCTION public.reject_market_alert_ledger_mutation();

ALTER TABLE public.market_alert_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_alert_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_alert_rule_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_alert_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_alert_actions ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.create_market_alert_drafts(
  p_request_id UUID, p_drafts JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_item JSONB; v_snapshot JSONB; v_condition JSONB;
  v_ids JSONB := '[]'::jsonb; v_id UUID; v_count INT := 0; v_existing INT;
BEGIN
  IF jsonb_typeof(p_drafts) <> 'array' OR jsonb_array_length(p_drafts) > 5 THEN
    RAISE EXCEPTION 'invalid alert drafts' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id;
  IF NOT FOUND OR v_request.operation <> 'evaluate_and_publish'
     OR v_request.status NOT IN ('claimed','completed') THEN
    RAISE EXCEPTION 'approved evaluation request unavailable' USING ERRCODE = '22023';
  END IF;
  UPDATE public.market_alert_drafts SET state='expired',updated_at=now()
  WHERE state='draft' AND expires_at<=now();
  PERFORM pg_advisory_xact_lock(hashtextextended('market_alert_draft_rate', 0));
  SELECT count(*) INTO v_existing FROM public.market_alert_drafts
  WHERE created_at>=now()-interval '1 hour';
  IF v_existing + jsonb_array_length(p_drafts) > 5 THEN
    RAISE EXCEPTION 'alert draft rate limit exceeded' USING ERRCODE = '54000';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_drafts) LOOP
    IF jsonb_typeof(v_item)<>'object'
       OR NOT (v_item ?& ARRAY['id','source_evaluation_id','rule_snapshot','fingerprint'])
       OR (v_item - ARRAY['id','source_evaluation_id','rule_snapshot','fingerprint']) <> '{}'::jsonb THEN
      RAISE EXCEPTION 'invalid alert draft item' USING ERRCODE = '22023';
    END IF;
    v_snapshot := v_item->'rule_snapshot';
    IF jsonb_typeof(v_snapshot)<>'object'
       OR NOT (v_snapshot ?& ARRAY['rule_id','version','state','ticker','profile','severity','session',
         'confirmation','conditions','cooldown_seconds','fire_limit','valid_until','owner_note'])
       OR (v_snapshot - ARRAY['rule_id','version','state','ticker','profile','severity','session',
         'confirmation','conditions','cooldown_seconds','fire_limit','valid_until','owner_note']) <> '{}'::jsonb
       OR v_snapshot->>'rule_id' IS DISTINCT FROM v_item->>'id'
       OR v_snapshot->>'state' <> 'draft'
       OR (v_snapshot->>'version')::int <= 0
       OR v_snapshot->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
       OR v_snapshot->>'profile' NOT IN ('long_term','balanced','active')
       OR v_snapshot->>'severity' NOT IN ('critical','review','update','watch','system')
       OR v_snapshot->>'session' NOT IN ('regular','pre_market','post_market','all')
       OR v_snapshot->>'confirmation' NOT IN ('bar_close','two_quote')
       OR jsonb_typeof(v_snapshot->'conditions')<>'array'
       OR jsonb_array_length(v_snapshot->'conditions') NOT BETWEEN 1 AND 5
       OR (v_snapshot->>'cooldown_seconds')::int NOT BETWEEN 60 AND 604800
       OR (v_snapshot->>'fire_limit')::int NOT BETWEEN 1 AND 100
       OR char_length(v_snapshot->>'owner_note') > 500
       OR (v_snapshot->>'valid_until')::timestamptz <= now()
       OR octet_length(v_snapshot::text) > 32768 THEN
      RAISE EXCEPTION 'invalid alert rule snapshot' USING ERRCODE = '22023';
    END IF;
    FOR v_condition IN SELECT value FROM jsonb_array_elements(v_snapshot->'conditions') LOOP
      IF jsonb_typeof(v_condition)<>'object'
         OR NOT (v_condition ?& ARRAY['kind','operator','left','right','timeframe'])
         OR (v_condition - ARRAY['kind','operator','left','right','timeframe']) <> '{}'::jsonb
         OR v_condition->>'kind' NOT IN ('price_cross','price_zone','sma_cross','rsi_range',
           'volume_multiple','recorded_stop','recorded_target','screen_entry','event_window')
         OR v_condition->>'operator' NOT IN ('above','below','inside','outside')
         OR v_condition->>'timeframe' NOT IN ('quote','15m','1h','1d') THEN
        RAISE EXCEPTION 'invalid alert condition' USING ERRCODE = '22023';
      END IF;
      IF v_condition->>'kind' NOT IN (
           'price_cross','price_zone','recorded_stop','recorded_target'
         ) OR v_condition->>'timeframe'<>'quote' THEN
        RAISE EXCEPTION 'unsupported alert condition adapter' USING ERRCODE = '22023';
      END IF;
    END LOOP;
    PERFORM 1 FROM public.decision_evaluations
    WHERE id=(v_item->>'source_evaluation_id')::uuid AND request_id=p_request_id
      AND policy_status='approved' AND final_action IS NOT NULL;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'approved source evaluation unavailable' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.market_alert_drafts(
      id,request_id,source_evaluation_id,rule_snapshot,fingerprint,expires_at
    ) VALUES (
      (v_item->>'id')::uuid,p_request_id,(v_item->>'source_evaluation_id')::uuid,
      v_snapshot,v_item->>'fingerprint',now()+interval '24 hours'
    ) ON CONFLICT (fingerprint) DO NOTHING RETURNING id INTO v_id;
    IF v_id IS NOT NULL THEN
      v_ids := v_ids || jsonb_build_array(v_id); v_count := v_count + 1;
    END IF;
    v_id := NULL;
  END LOOP;
  RETURN jsonb_build_object('created_count',v_count,'draft_ids',v_ids);
END;
$$;

CREATE OR REPLACE FUNCTION public.apply_market_alert_action(
  p_draft_or_rule_id UUID, p_action TEXT, p_update_id BIGINT, p_chat_id BIGINT,
  p_user_id BIGINT, p_expected_version INT, p_snooze_until TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_duplicate public.market_alert_actions%ROWTYPE;
  v_draft public.market_alert_drafts%ROWTYPE;
  v_rule public.market_alert_rules%ROWTYPE;
  v_event public.market_alert_events%ROWTYPE;
  v_snapshot JSONB; v_prior TEXT; v_new TEXT; v_version INT; v_action_id UUID;
BEGIN
  IF p_update_id<=0 OR p_chat_id<=0 OR p_user_id<=0 OR p_expected_version<=0 THEN
    RAISE EXCEPTION 'invalid alert action identity' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_duplicate FROM public.market_alert_actions
  WHERE telegram_update_id=p_update_id;
  IF FOUND THEN
    IF v_duplicate.action IS DISTINCT FROM p_action
       OR COALESCE(v_duplicate.event_id,v_duplicate.draft_id,v_duplicate.rule_id)
          IS DISTINCT FROM p_draft_or_rule_id
       OR v_duplicate.owner_chat_id IS DISTINCT FROM p_chat_id
       OR v_duplicate.owner_user_id IS DISTINCT FROM p_user_id
       OR v_duplicate.expected_version IS DISTINCT FROM p_expected_version
       OR v_duplicate.snoozed_until IS DISTINCT FROM p_snooze_until THEN
      RAISE EXCEPTION 'telegram update replay mismatch' USING ERRCODE = '22023';
    END IF;
    RETURN jsonb_build_object('ok',true,'duplicate',true,'state',v_duplicate.new_state,
      'version',v_duplicate.resulting_version,'action_id',v_duplicate.id);
  END IF;

  IF p_action='acknowledge' THEN
    SELECT * INTO v_event FROM public.market_alert_events
    WHERE id=p_draft_or_rule_id AND publication_id IS NOT NULL FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'alert event unavailable' USING ERRCODE = '42501'; END IF;
    SELECT * INTO v_rule FROM public.market_alert_rules WHERE id=v_event.rule_id FOR UPDATE;
    IF NOT FOUND OR v_rule.owner_chat_id IS DISTINCT FROM p_chat_id
       OR v_rule.owner_user_id IS DISTINCT FROM p_user_id THEN
      RAISE EXCEPTION 'alert owner mismatch' USING ERRCODE = '42501';
    END IF;
    IF v_event.rule_version IS DISTINCT FROM p_expected_version THEN
      RAISE EXCEPTION 'stale alert version' USING ERRCODE = '40001';
    END IF;
    INSERT INTO public.market_alert_actions(
      rule_id,event_id,publication_id,telegram_update_id,owner_chat_id,owner_user_id,
      action,prior_state,new_state,expected_version,resulting_version
    ) VALUES (
      v_rule.id,v_event.id,v_event.publication_id,p_update_id,p_chat_id,p_user_id,
      p_action,v_rule.state,v_rule.state,p_expected_version,v_event.rule_version
    ) RETURNING id INTO v_action_id;
    RETURN jsonb_build_object('ok',true,'duplicate',false,'state',v_rule.state,
      'version',v_event.rule_version,'rule_id',v_rule.id,'event_id',v_event.id,
      'publication_id',v_event.publication_id,'action_id',v_action_id);
  END IF;

  SELECT * INTO v_draft FROM public.market_alert_drafts
  WHERE id=p_draft_or_rule_id AND state='draft' FOR UPDATE;
  IF FOUND THEN
    v_snapshot := v_draft.rule_snapshot;
    IF v_draft.owner_chat_id IS NOT NULL AND
       (v_draft.owner_chat_id IS DISTINCT FROM p_chat_id OR v_draft.owner_user_id IS DISTINCT FROM p_user_id) THEN
      RAISE EXCEPTION 'alert owner mismatch' USING ERRCODE = '42501';
    END IF;
    IF v_draft.expires_at<=now() THEN RAISE EXCEPTION 'draft expired' USING ERRCODE = '22023'; END IF;
    IF (v_snapshot->>'version')::int IS DISTINCT FROM p_expected_version THEN
      RAISE EXCEPTION 'stale alert version' USING ERRCODE = '40001';
    END IF;
    IF NOT p_action IN ('arm','dismiss') OR v_draft.state<>'draft' THEN
      RAISE EXCEPTION 'invalid alert transition' USING ERRCODE = '22023';
    END IF;
    v_prior := v_draft.state; v_version := p_expected_version;
    IF p_action='dismiss' THEN
      v_new := 'dismissed';
      UPDATE public.market_alert_drafts SET state=v_new,owner_chat_id=p_chat_id,
        owner_user_id=p_user_id,updated_at=now() WHERE id=v_draft.id;
    ELSE
      v_new := 'active';
      v_snapshot := jsonb_set(v_snapshot,'{state}','"active"'::jsonb);
      INSERT INTO public.market_alert_rules(id,source_draft_id,current_version,state,ticker,profile,
        severity,session,confirmation,conditions,cooldown_seconds,fire_limit,valid_until,owner_note,
        owner_chat_id,owner_user_id)
      VALUES (v_draft.id,v_draft.id,v_version,v_new,v_snapshot->>'ticker',v_snapshot->>'profile',
        v_snapshot->>'severity',v_snapshot->>'session',v_snapshot->>'confirmation',
        v_snapshot->'conditions',(v_snapshot->>'cooldown_seconds')::int,
        (v_snapshot->>'fire_limit')::int,(v_snapshot->>'valid_until')::timestamptz,
        v_snapshot->>'owner_note',p_chat_id,p_user_id);
      INSERT INTO public.market_alert_rule_versions(rule_id,version,snapshot)
      VALUES (v_draft.id,v_version,v_snapshot);
      UPDATE public.market_alert_drafts SET state='armed',owner_chat_id=p_chat_id,
        owner_user_id=p_user_id,updated_at=now() WHERE id=v_draft.id;
    END IF;
    INSERT INTO public.market_alert_actions(draft_id,telegram_update_id,owner_chat_id,owner_user_id,
      action,prior_state,new_state,expected_version,resulting_version)
    VALUES (v_draft.id,p_update_id,p_chat_id,p_user_id,p_action,v_prior,v_new,
      p_expected_version,v_version) RETURNING id INTO v_action_id;
    RETURN jsonb_build_object('ok',true,'duplicate',false,'state',v_new,'version',v_version,
      'rule_id',CASE WHEN p_action='arm' THEN v_draft.id ELSE NULL END,'action_id',v_action_id);
  END IF;

  SELECT * INTO v_rule FROM public.market_alert_rules
  WHERE id=p_draft_or_rule_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'alert unavailable' USING ERRCODE = '42501'; END IF;
  IF v_rule.owner_chat_id IS DISTINCT FROM p_chat_id OR v_rule.owner_user_id IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'alert owner mismatch' USING ERRCODE = '42501';
  END IF;
  IF v_rule.valid_until<=now() AND v_rule.state IN ('active','paused','snoozed') THEN
    v_version := v_rule.current_version+1;
    SELECT snapshot INTO v_snapshot FROM public.market_alert_rule_versions
    WHERE rule_id=v_rule.id AND version=v_rule.current_version;
    v_snapshot := jsonb_set(jsonb_set(v_snapshot,'{state}','"expired"'::jsonb),
      '{version}',to_jsonb(v_version));
    UPDATE public.market_alert_rules SET state='expired',current_version=v_version,
      snoozed_until=NULL,updated_at=now() WHERE id=v_rule.id;
    INSERT INTO public.market_alert_rule_versions(rule_id,version,snapshot)
    VALUES (v_rule.id,v_version,v_snapshot);
    RETURN jsonb_build_object('ok',false,'duplicate',false,'state','expired',
      'version',v_version,'rule_id',v_rule.id,'reason','alert expired');
  END IF;
  IF v_rule.current_version IS DISTINCT FROM p_expected_version THEN
    RAISE EXCEPTION 'stale alert version' USING ERRCODE = '40001';
  END IF;
  IF NOT p_action IN ('pause','resume','snooze','dismiss') THEN
    RAISE EXCEPTION 'invalid alert transition' USING ERRCODE = '22023';
  END IF;
  v_prior := v_rule.state; v_new := v_prior; v_version := v_rule.current_version;
  IF p_action='pause' AND v_prior IN ('active','snoozed') THEN v_new := 'paused';
  ELSIF p_action='resume' AND v_prior IN ('paused','snoozed') THEN v_new := 'active';
  ELSIF p_action='snooze' AND v_prior='active' THEN
    IF p_snooze_until IS NULL OR p_snooze_until<=now()
       OR p_snooze_until>now()+interval '1 day 1 minute' THEN
      RAISE EXCEPTION 'invalid snooze window' USING ERRCODE = '22023';
    END IF;
    v_new := 'snoozed';
  ELSIF p_action='dismiss' AND v_prior IN ('active','paused','snoozed') THEN v_new := 'dismissed';
  ELSE RAISE EXCEPTION 'invalid alert transition' USING ERRCODE = '22023';
  END IF;
  v_version := v_version+1;
  SELECT snapshot INTO v_snapshot FROM public.market_alert_rule_versions
  WHERE rule_id=v_rule.id AND version=v_rule.current_version;
  v_snapshot := jsonb_set(jsonb_set(v_snapshot,'{state}',to_jsonb(v_new)),
    '{version}',to_jsonb(v_version));
  UPDATE public.market_alert_rules SET state=v_new,current_version=v_version,
    snoozed_until=CASE WHEN p_action='snooze' THEN p_snooze_until ELSE NULL END,
    updated_at=now() WHERE id=v_rule.id;
  INSERT INTO public.market_alert_rule_versions(rule_id,version,snapshot)
  VALUES (v_rule.id,v_version,v_snapshot);
  INSERT INTO public.market_alert_actions(rule_id,telegram_update_id,owner_chat_id,owner_user_id,
    action,prior_state,new_state,expected_version,resulting_version,snoozed_until)
  VALUES (v_rule.id,p_update_id,p_chat_id,p_user_id,p_action,v_prior,v_new,
    p_expected_version,v_version,CASE WHEN p_action='snooze' THEN p_snooze_until ELSE NULL END)
  RETURNING id INTO v_action_id;
  RETURN jsonb_build_object('ok',true,'duplicate',false,'state',v_new,'version',v_version,
    'rule_id',v_rule.id,'action_id',v_action_id);
END;
$$;

CREATE OR REPLACE FUNCTION public.expire_market_alert_rules()
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_rule public.market_alert_rules%ROWTYPE;
  v_snapshot JSONB;
  v_version INT;
  v_count INT := 0;
BEGIN
  FOR v_rule IN
    SELECT * FROM public.market_alert_rules
    WHERE state IN ('active','paused','snoozed') AND valid_until<=now()
    ORDER BY id FOR UPDATE
  LOOP
    v_version := v_rule.current_version+1;
    SELECT snapshot INTO v_snapshot FROM public.market_alert_rule_versions
    WHERE rule_id=v_rule.id AND version=v_rule.current_version;
    v_snapshot := jsonb_set(jsonb_set(v_snapshot,'{state}','"expired"'::jsonb),
      '{version}',to_jsonb(v_version));
    UPDATE public.market_alert_rules SET state='expired',current_version=v_version,
      snoozed_until=NULL,updated_at=now() WHERE id=v_rule.id;
    INSERT INTO public.market_alert_rule_versions(rule_id,version,snapshot)
    VALUES (v_rule.id,v_version,v_snapshot);
    v_count := v_count+1;
  END LOOP;
  RETURN jsonb_build_object('expired_count',v_count);
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_alert_evaluations(
  p_request_id UUID, p_evaluations JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE; v_item JSONB;
  v_rule public.market_alert_rules%ROWTYPE; v_id UUID;
  v_ids JSONB := '[]'::jsonb; v_count INT := 0;
BEGIN
  IF jsonb_typeof(p_evaluations)<>'array' OR jsonb_array_length(p_evaluations)>20 THEN
    RAISE EXCEPTION 'invalid alert evaluations' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'evaluate_alert_rules' OR v_request.status<>'claimed' THEN
    RAISE EXCEPTION 'alert evaluation request unavailable' USING ERRCODE = '40001';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_evaluations) LOOP
    IF jsonb_typeof(v_item)<>'object'
       OR NOT (v_item ?& ARRAY['id','rule_id','rule_version','fingerprint','status','reason_codes',
         'observed_at','evaluated_at','market_session','condition_results','evidence_ids'])
       OR (v_item - ARRAY['id','rule_id','rule_version','fingerprint','status','reason_codes',
         'observed_at','evaluated_at','market_session','condition_results','evidence_ids']) <> '{}'::jsonb
       OR v_item->>'status' NOT IN ('triggered','unsafe_to_evaluate')
       OR v_item->>'market_session' NOT IN ('regular','pre_market','post_market')
       OR jsonb_typeof(v_item->'condition_results')<>'array'
       OR jsonb_array_length(v_item->'condition_results') NOT BETWEEN 1 AND 5
       OR jsonb_typeof(v_item->'reason_codes')<>'array'
       OR jsonb_typeof(v_item->'evidence_ids')<>'array'
       OR (v_item->>'evaluated_at')::timestamptz > now()+interval '1 minute' THEN
      RAISE EXCEPTION 'invalid alert evaluation item' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_rule FROM public.market_alert_rules
    WHERE id=(v_item->>'rule_id')::uuid FOR UPDATE;
    IF NOT FOUND OR v_rule.current_version IS DISTINCT FROM (v_item->>'rule_version')::int THEN
      RAISE EXCEPTION 'stale alert version' USING ERRCODE = '40001';
    END IF;
    IF v_item->>'status'='triggered' THEN
      IF v_rule.state<>'active' OR v_rule.valid_until<=now() OR v_rule.trigger_count>=v_rule.fire_limit THEN
        RAISE EXCEPTION 'alert rule unavailable' USING ERRCODE = '22023';
      END IF;
      IF EXISTS (SELECT 1 FROM public.market_alert_events WHERE rule_id=v_rule.id
        AND status='triggered' AND evaluated_at>
          (v_item->>'evaluated_at')::timestamptz-make_interval(secs=>v_rule.cooldown_seconds)) THEN
        RAISE EXCEPTION 'alert cooldown active' USING ERRCODE = '22023';
      END IF;
    END IF;
    INSERT INTO public.market_alert_events(id,request_id,rule_id,rule_version,fingerprint,status,
      reason_codes,observed_at,evaluated_at,market_session,condition_results,evidence_ids)
    VALUES ((v_item->>'id')::uuid,p_request_id,v_rule.id,(v_item->>'rule_version')::int,
      v_item->>'fingerprint',v_item->>'status',v_item->'reason_codes',
      (v_item->>'observed_at')::timestamptz,(v_item->>'evaluated_at')::timestamptz,
      v_item->>'market_session',v_item->'condition_results',v_item->'evidence_ids')
    RETURNING id INTO v_id;
    IF v_item->>'status'='triggered' THEN
      UPDATE public.market_alert_rules SET trigger_count=trigger_count+1,
        last_triggered_at=(v_item->>'evaluated_at')::timestamptz,updated_at=now()
      WHERE id=v_rule.id;
    END IF;
    v_ids := v_ids || jsonb_build_array(v_id); v_count := v_count+1;
  END LOOP;
  RETURN jsonb_build_object('event_count',v_count,'event_ids',v_ids);
END;
$$;

CREATE OR REPLACE FUNCTION public.create_market_alert_publication(
  p_request_id UUID, p_publication_id UUID, p_market_date DATE, p_kind TEXT,
  p_rendered_body TEXT, p_rendered_hash TEXT, p_event_ids UUID[], p_draft_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_expected INT := COALESCE(array_length(p_event_ids,1),0);
  v_rows INT;
BEGIN
  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'evaluate_alert_rules' OR v_request.status<>'claimed' THEN
    RAISE EXCEPTION 'alert publication request unavailable' USING ERRCODE = '40001';
  END IF;
  IF p_publication_id IS NULL OR p_market_date IS NULL
     OR p_kind NOT IN ('new_idea','entry_trigger','stop_near','stop_breach',
       'target_near','target_hit','thesis_break','data_warning')
     OR p_rendered_body IS NULL OR char_length(p_rendered_body) NOT BETWEEN 1 AND 3500
     OR p_rendered_hash !~ '^[0-9a-f]{64}$'
     OR ((v_expected>0)::int + (p_draft_id IS NOT NULL)::int) <> 1
     OR v_expected>20 THEN
    RAISE EXCEPTION 'alert publication target mismatch' USING ERRCODE = '22023';
  END IF;
  IF v_expected>0 THEN
    SELECT count(*) INTO v_rows FROM public.market_alert_events
    WHERE id=ANY(p_event_ids) AND request_id=p_request_id
      AND status='triggered' AND publication_id IS NULL;
    IF v_rows<>v_expected THEN
      RAISE EXCEPTION 'alert event publication mismatch' USING ERRCODE = '22023';
    END IF;
  ELSE
    PERFORM 1 FROM public.market_alert_drafts
    WHERE id=p_draft_id AND state='draft' AND expires_at>now() AND publication_id IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'alert draft publication mismatch' USING ERRCODE = '22023';
    END IF;
  END IF;
  INSERT INTO public.market_publications(
    id,idempotency_key,run_id,market_date,phase,kind,
    template_version,rendered_body,rendered_hash,status
  ) VALUES (
    p_publication_id,p_request_id,NULL,p_market_date,'intraday',p_kind,
    3,p_rendered_body,p_rendered_hash,'ready'
  );
  IF v_expected>0 THEN
    UPDATE public.market_alert_events SET publication_id=p_publication_id
    WHERE id=ANY(p_event_ids);
  ELSE
    UPDATE public.market_alert_drafts SET publication_id=p_publication_id,updated_at=now()
    WHERE id=p_draft_id;
  END IF;
  RETURN jsonb_build_object('publication_id',p_publication_id,
    'linked_event_count',v_expected,'linked_draft_id',p_draft_id);
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_alert_publication(
  p_request_id UUID, p_lease_token UUID, p_status TEXT, p_message_ids JSONB,
  p_error TEXT, p_accepted_at TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_valid_ids BOOLEAN;
BEGIN
  IF p_status NOT IN ('delivered','delivery_failed','delivery_unknown')
     OR jsonb_typeof(p_message_ids)<>'array' OR jsonb_array_length(p_message_ids)>1
     OR (p_status='delivered' AND (jsonb_array_length(p_message_ids)<>1 OR p_accepted_at IS NULL))
     OR (p_status<>'delivered' AND p_accepted_at IS NOT NULL)
     OR (p_accepted_at IS NOT NULL AND p_accepted_at>now()+interval '1 minute') THEN
    RAISE EXCEPTION 'invalid alert publication completion' USING ERRCODE = '22023';
  END IF;
  SELECT COALESCE(bool_and(jsonb_typeof(value)='number' AND value::text ~ '^[0-9]+$'),true)
    INTO v_valid_ids FROM jsonb_array_elements(p_message_ids);
  IF NOT v_valid_ids THEN
    RAISE EXCEPTION 'invalid telegram message ids' USING ERRCODE = '22023';
  END IF;
  UPDATE public.market_publications SET status=p_status,telegram_message_ids=p_message_ids,
    telegram_accepted_at=CASE WHEN p_status='delivered' THEN p_accepted_at ELSE NULL END,
    delivered_at=CASE WHEN p_status='delivered' THEN now() ELSE NULL END,
    error=CASE WHEN p_error IS NULL THEN NULL ELSE regexp_replace(
      left(p_error,1000),'[^A-Za-z0-9_ .:-]','?','g') END,
    lease_token=NULL,updated_at=now()
  WHERE idempotency_key=p_request_id AND lease_token=p_lease_token
    AND status='sending' AND template_version=3;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'alert publication lease unavailable' USING ERRCODE = '40001';
  END IF;
  RETURN jsonb_build_object('status',p_status,'telegram_message_ids',p_message_ids,
    'telegram_accepted_at',p_accepted_at);
END;
$$;

DROP FUNCTION IF EXISTS public.link_market_alert_publication(UUID[], UUID);

REVOKE ALL ON FUNCTION public.create_market_alert_drafts(UUID, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_market_alert_action(UUID, TEXT, BIGINT, BIGINT, BIGINT, INT, TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_market_alert_evaluations(UUID, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.create_market_alert_publication(UUID, UUID, DATE, TEXT, TEXT, TEXT, UUID[], UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.expire_market_alert_rules() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_alert_publication(UUID, UUID, TEXT, JSONB, TEXT, TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_market_alert_drafts(UUID, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_market_alert_action(UUID, TEXT, BIGINT, BIGINT, BIGINT, INT, TIMESTAMPTZ) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_alert_evaluations(UUID, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.create_market_alert_publication(UUID, UUID, DATE, TEXT, TEXT, TEXT, UUID[], UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.expire_market_alert_rules() TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_alert_publication(UUID, UUID, TEXT, JSONB, TEXT, TIMESTAMPTZ) TO service_role;
-- Owner-only dashboard role. This role can read only the columns used by Web v1.
-- It has no write, DDL, application-function, Auth, Telegram-command, or secret access.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_agent_dashboard') THEN
    CREATE ROLE stock_agent_dashboard
      NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END
$$;

ALTER ROLE stock_agent_dashboard
  NOLOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOBYPASSRLS;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM stock_agent_dashboard;
GRANT USAGE ON SCHEMA public TO stock_agent_dashboard;

REVOKE ALL ON FUNCTION public.reject_decision_evaluation_mutation() FROM PUBLIC, stock_agent_dashboard;
REVOKE ALL ON FUNCTION public.reject_market_alert_ledger_mutation() FROM PUBLIC, stock_agent_dashboard;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE
  public.holdings,
  public.transactions,
  public.owner_investment_plans,
  public.analysis_runs,
  public.market_gateway_requests,
  public.decision_evaluations,
  public.suggestions,
  public.suggestion_grades,
  public.market_publications,
  public.market_policy_config,
  public.market_alert_drafts,
  public.market_alert_rules,
  public.market_alert_rule_versions,
  public.market_alert_events,
  public.market_alert_actions
FROM stock_agent_dashboard;

GRANT SELECT (ticker, shares, avg_cost, bucket, opened_at, stop, target)
  ON public.holdings TO stock_agent_dashboard;
GRANT SELECT (id, ts, ticker, side, qty, price, source, executed_on)
  ON public.transactions TO stock_agent_dashboard;
GRANT SELECT (id, ticker, bucket, amount, cadence, next_due_on, due_day, active, created_at, updated_at)
  ON public.owner_investment_plans TO stock_agent_dashboard;
GRANT SELECT (id, kind, started_at, finished_at, status, data_as_of, source_status, symbols,
              write_counts, telegram_message_ids, summary, gateway_request_id)
  ON public.analysis_runs TO stock_agent_dashboard;
GRANT SELECT (request_id, operation, run_id, status, attempt_count, response, response_digest,
              created_at, claimed_at, finished_at)
  ON public.market_gateway_requests TO stock_agent_dashboard;
GRANT SELECT (id, request_id, run_id, candidate_id, policy_version, raw_action, final_action,
              policy_status, reason_codes, explanations, normalized, evidence, analyst, checker,
              created_at)
  ON public.decision_evaluations TO stock_agent_dashboard;
GRANT SELECT (id, ts, date, ticker, action, bucket, depth, entry_zone_low, entry_zone_high,
              valid_until, stop, target, confidence, bull, bear, decisive_factor, risk_verdict,
              invalidation_level, reason, score, risk_band, price_at_suggestion, run_id,
              evidence_as_of, invalidation_price, evaluation_id, decision_source, decision_mode)
  ON public.suggestions TO stock_agent_dashboard;
GRANT SELECT (id, suggestion_id, graded_at, result, price_then, price_later, horizon_days, note)
  ON public.suggestion_grades TO stock_agent_dashboard;
GRANT SELECT (id, idempotency_key, run_id, market_date, phase, kind, template_version,
              rendered_body, rendered_hash, status, telegram_message_ids, attempt_count,
              sending_started_at, delivered_at, created_at, updated_at,
              telegram_accepted_at)
  ON public.market_publications TO stock_agent_dashboard;
GRANT SELECT (version, config, active, created_at, activated_at)
  ON public.market_policy_config TO stock_agent_dashboard;
GRANT SELECT (id, request_id, source_evaluation_id, rule_snapshot, fingerprint, state,
              publication_id, expires_at, created_at, updated_at)
  ON public.market_alert_drafts TO stock_agent_dashboard;
GRANT SELECT (id, source_draft_id, current_version, state, ticker, profile, severity, session,
              confirmation, conditions, cooldown_seconds, fire_limit, trigger_count, valid_until,
              snoozed_until, owner_note, armed_at, last_triggered_at, updated_at)
  ON public.market_alert_rules TO stock_agent_dashboard;
GRANT SELECT (rule_id, version, snapshot, created_at)
  ON public.market_alert_rule_versions TO stock_agent_dashboard;
GRANT SELECT (id, request_id, rule_id, rule_version, fingerprint, status, reason_codes, observed_at,
              evaluated_at, persisted_at, market_session, condition_results, evidence_ids,
              publication_id)
  ON public.market_alert_events TO stock_agent_dashboard;
GRANT SELECT (id, draft_id, rule_id, event_id, publication_id, telegram_update_id, action,
              prior_state, new_state, expected_version, resulting_version, snoozed_until,
              received_at)
  ON public.market_alert_actions TO stock_agent_dashboard;

DROP POLICY IF EXISTS owner_dashboard_select_holdings ON public.holdings;
CREATE POLICY owner_dashboard_select_holdings ON public.holdings
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_transactions ON public.transactions;
CREATE POLICY owner_dashboard_select_transactions ON public.transactions
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_plans ON public.owner_investment_plans;
CREATE POLICY owner_dashboard_select_plans ON public.owner_investment_plans
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_runs ON public.analysis_runs;
CREATE POLICY owner_dashboard_select_runs ON public.analysis_runs
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_gateway_requests ON public.market_gateway_requests;
CREATE POLICY owner_dashboard_select_gateway_requests ON public.market_gateway_requests
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_evaluations ON public.decision_evaluations;
CREATE POLICY owner_dashboard_select_evaluations ON public.decision_evaluations
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_suggestions ON public.suggestions;
CREATE POLICY owner_dashboard_select_suggestions ON public.suggestions
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_grades ON public.suggestion_grades;
CREATE POLICY owner_dashboard_select_grades ON public.suggestion_grades
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_publications ON public.market_publications;
CREATE POLICY owner_dashboard_select_publications ON public.market_publications
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_policy ON public.market_policy_config;
CREATE POLICY owner_dashboard_select_policy ON public.market_policy_config
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_alert_drafts ON public.market_alert_drafts;
CREATE POLICY owner_dashboard_select_alert_drafts ON public.market_alert_drafts
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_alert_rules ON public.market_alert_rules;
CREATE POLICY owner_dashboard_select_alert_rules ON public.market_alert_rules
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_alert_versions ON public.market_alert_rule_versions;
CREATE POLICY owner_dashboard_select_alert_versions ON public.market_alert_rule_versions
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_alert_events ON public.market_alert_events;
CREATE POLICY owner_dashboard_select_alert_events ON public.market_alert_events
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_alert_actions ON public.market_alert_actions;
CREATE POLICY owner_dashboard_select_alert_actions ON public.market_alert_actions
  FOR SELECT TO stock_agent_dashboard USING (true);

-- Immutable, receipt-backed market-intelligence and report ledgers.
-- Additive and idempotent. This migration is local-only until the V1-C6 gate.

ALTER TABLE public.market_gateway_requests
  DROP CONSTRAINT IF EXISTS market_gateway_requests_operation_check;
ALTER TABLE public.market_gateway_requests
  ADD CONSTRAINT market_gateway_requests_operation_check CHECK (operation IN (
    'start_run','read_context','record_artifacts','grade_due_decisions',
    'evaluate_and_publish','evaluate_alert_rules','finish_run','record_report'
  ));

CREATE OR REPLACE FUNCTION public.claim_market_gateway_request(
  p_request_id UUID, p_operation TEXT, p_run_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_lease UUID;
  v_stored_run UUID := CASE WHEN p_operation='record_report' THEN NULL ELSE p_run_id END;
BEGIN
  IF p_operation NOT IN (
       'start_run','read_context','record_artifacts','grade_due_decisions',
       'evaluate_and_publish','evaluate_alert_rules','finish_run','record_report'
     )
     OR (p_operation = 'start_run' AND p_run_id IS NOT NULL)
     OR (p_operation <> 'start_run' AND p_run_id IS NULL) THEN
    RAISE EXCEPTION 'invalid request identity' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('market_gateway_rate',0));
    IF (SELECT count(*) FROM public.market_gateway_requests
        WHERE created_at >= now()-interval '1 hour') >= 100
       OR (v_stored_run IS NOT NULL AND (
         SELECT count(*) FROM public.market_gateway_requests WHERE run_id=v_stored_run
       ) >= 20) THEN
      RAISE EXCEPTION 'gateway rate limit exceeded' USING ERRCODE = '54000';
    END IF;
    v_lease := gen_random_uuid();
    INSERT INTO public.market_gateway_requests(request_id,operation,run_id,status,lease_token)
    VALUES (p_request_id,p_operation,v_stored_run,'claimed',v_lease);
    RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,'attempt_count',1);
  END IF;
  IF v_request.operation<>p_operation
     OR v_request.run_id IS DISTINCT FROM v_stored_run THEN
    RAISE EXCEPTION 'request identity mismatch' USING ERRCODE = '22023';
  END IF;
  IF v_request.status='failed' AND p_operation='record_report' THEN
    v_lease := gen_random_uuid();
    UPDATE public.market_gateway_requests SET status='claimed',lease_token=v_lease,
      claimed_at=now(),attempt_count=attempt_count+1 WHERE request_id=p_request_id;
    RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,'attempt_count',v_request.attempt_count+1);
  END IF;
  IF v_request.status IN ('completed','failed') THEN
    RETURN jsonb_build_object('claimed',false,'status',v_request.status,
      'response',v_request.response,'response_digest',v_request.response_digest);
  END IF;
  IF v_request.claimed_at > now()-interval '5 minutes' THEN
    RETURN jsonb_build_object('claimed',false,'status','REQUEST_IN_PROGRESS');
  END IF;
  v_lease := gen_random_uuid();
  UPDATE public.market_gateway_requests SET lease_token=v_lease,claimed_at=now(),
    attempt_count=attempt_count+1 WHERE request_id=p_request_id;
  RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,
    'attempt_count',v_request.attempt_count+1);
END;
$$;

-- Consolidated from sql/migrations/20260907_market_intelligence.sql
-- Immutable, receipt-backed market-intelligence and report ledgers.
-- Additive and idempotent. This migration is local-only until the V1-C6 gate.

ALTER TABLE public.market_gateway_requests
  DROP CONSTRAINT IF EXISTS market_gateway_requests_operation_check;
ALTER TABLE public.market_gateway_requests
  ADD CONSTRAINT market_gateway_requests_operation_check CHECK (operation IN (
    'start_run','read_context','record_artifacts','grade_due_decisions',
    'evaluate_and_publish','evaluate_alert_rules','finish_run','record_report'
  ));

CREATE OR REPLACE FUNCTION public.claim_market_gateway_request(
  p_request_id UUID, p_operation TEXT, p_run_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_lease UUID;
  v_stored_run UUID := CASE WHEN p_operation='record_report' THEN NULL ELSE p_run_id END;
BEGIN
  IF p_operation NOT IN (
       'start_run','read_context','record_artifacts','grade_due_decisions',
       'evaluate_and_publish','evaluate_alert_rules','finish_run','record_report'
     )
     OR (p_operation = 'start_run' AND p_run_id IS NOT NULL)
     OR (p_operation <> 'start_run' AND p_run_id IS NULL) THEN
    RAISE EXCEPTION 'invalid request identity' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('market_gateway_rate',0));
    IF (SELECT count(*) FROM public.market_gateway_requests
        WHERE created_at >= now()-interval '1 hour') >= 100
       OR (v_stored_run IS NOT NULL AND (
         SELECT count(*) FROM public.market_gateway_requests WHERE run_id=v_stored_run
       ) >= 20) THEN
      RAISE EXCEPTION 'gateway rate limit exceeded' USING ERRCODE = '54000';
    END IF;
    v_lease := gen_random_uuid();
    INSERT INTO public.market_gateway_requests(request_id,operation,run_id,status,lease_token)
    VALUES (p_request_id,p_operation,v_stored_run,'claimed',v_lease);
    RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,'attempt_count',1);
  END IF;
  IF v_request.operation<>p_operation
     OR v_request.run_id IS DISTINCT FROM v_stored_run THEN
    RAISE EXCEPTION 'request identity mismatch' USING ERRCODE = '22023';
  END IF;
  IF v_request.status IN ('completed','failed') THEN
    RETURN jsonb_build_object('claimed',false,'status',v_request.status,
      'response',v_request.response,'response_digest',v_request.response_digest);
  END IF;
  IF v_request.claimed_at > now()-interval '5 minutes' THEN
    RETURN jsonb_build_object('claimed',false,'status','REQUEST_IN_PROGRESS');
  END IF;
  v_lease := gen_random_uuid();
  UPDATE public.market_gateway_requests SET lease_token=v_lease,claimed_at=now(),
    attempt_count=attempt_count+1 WHERE request_id=p_request_id;
  RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,
    'attempt_count',v_request.attempt_count+1);
END;
$$;


CREATE TABLE IF NOT EXISTS public.market_intelligence_runs (
  id UUID PRIMARY KEY,
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  market_date DATE NOT NULL,
  policy_version INT NOT NULL REFERENCES public.market_policy_config(version) ON DELETE RESTRICT,
  reservation_plan JSONB NOT NULL CHECK (
    jsonb_typeof(reservation_plan)='object'
    AND octet_length(reservation_plan::text) <= 32768
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_market_intelligence_runs_date_phase
  ON public.market_intelligence_runs(market_date, phase, created_at DESC);

CREATE TABLE IF NOT EXISTS public.market_intelligence_run_events (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  status TEXT NOT NULL CHECK (status IN ('started','completed','failed')),
  detail JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (
    jsonb_typeof(detail)='object' AND octet_length(detail::text) <= 131072
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, status)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_market_intelligence_terminal_event
  ON public.market_intelligence_run_events(run_id) WHERE status IN ('completed','failed');

CREATE TABLE IF NOT EXISTS public.market_source_quota_reservations (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL CHECK (provider IN (
    'gdelt','alpha_vantage','finnhub','yahoo','sec_edgar','federal_register',
    'white_house','doe','dod','eia','fred','bls','bea','social'
  )),
  market_date DATE NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  reserved_requests INT NOT NULL CHECK (reserved_requests BETWEEN 1 AND 100),
  cache_keys JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(cache_keys)='array' AND jsonb_array_length(cache_keys) <= 20
    AND octet_length(cache_keys::text) <= 12288
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_market_source_quota_provider_date
  ON public.market_source_quota_reservations(provider, market_date);

CREATE TABLE IF NOT EXISTS public.market_source_receipts (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  reservation_id UUID NOT NULL REFERENCES public.market_source_quota_reservations(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL CHECK (provider IN (
    'gdelt','alpha_vantage','finnhub','yahoo','sec_edgar','federal_register',
    'white_house','doe','dod','eia','fred','bls','bea','social'
  )),
  status TEXT NOT NULL CHECK (status IN (
    'succeeded','failed','cache_hit','quota_blocked','configuration_missing'
  )),
  cache_key TEXT NOT NULL CHECK (char_length(cache_key) BETWEEN 1 AND 512),
  requested_window JSONB NOT NULL CHECK (
    jsonb_typeof(requested_window)='object' AND octet_length(requested_window::text) <= 8192
  ),
  retrieved_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ,
  request_cost INT NOT NULL CHECK (request_cost BETWEEN 0 AND 100),
  upstream_remaining INT CHECK (upstream_remaining IS NULL OR upstream_remaining >= 0),
  returned_count INT NOT NULL CHECK (returned_count BETWEEN 0 AND 10000),
  accepted_count INT NOT NULL CHECK (accepted_count BETWEEN 0 AND 10000),
  duplicate_count INT NOT NULL CHECK (duplicate_count BETWEEN 0 AND 10000),
  dropped_count INT NOT NULL CHECK (dropped_count BETWEEN 0 AND 10000),
  error JSONB CHECK (error IS NULL OR (
    jsonb_typeof(error)='object' AND octet_length(error::text) <= 4096
  )),
  response_hash TEXT CHECK (response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, id),
  CHECK (accepted_count + duplicate_count + dropped_count <= returned_count),
  CHECK (
    (status IN ('succeeded','cache_hit') AND response_hash IS NOT NULL
      AND expires_at IS NOT NULL AND expires_at > retrieved_at AND error IS NULL)
    OR
    (status NOT IN ('succeeded','cache_hit') AND expires_at IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_market_source_receipts_cache
  ON public.market_source_receipts(provider, cache_key, expires_at DESC)
  WHERE status IN ('succeeded','cache_hit');

CREATE TABLE IF NOT EXISTS public.market_source_items (
  id UUID PRIMARY KEY,
  source_receipt_id UUID NOT NULL REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL CHECK (provider IN (
    'gdelt','alpha_vantage','finnhub','yahoo','sec_edgar','federal_register',
    'white_house','doe','dod','eia','fred','bls','bea','social'
  )),
  upstream_item_id TEXT CHECK (upstream_item_id IS NULL OR char_length(upstream_item_id) <= 512),
  canonical_url TEXT CHECK (
    canonical_url IS NULL OR (char_length(canonical_url) <= 2048 AND canonical_url ~ '^https://')
  ),
  published_at TIMESTAMPTZ,
  effective_at TIMESTAMPTZ,
  title TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 500),
  normalized_text TEXT NOT NULL CHECK (char_length(normalized_text) <= 2000),
  canonical_content TEXT NOT NULL CHECK (char_length(canonical_content) <= 4096),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (
    jsonb_typeof(metadata)='object' AND octet_length(metadata::text) <= 8192
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (source_receipt_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_market_source_items_hash
  ON public.market_source_items(provider, content_hash);

CREATE TABLE IF NOT EXISTS public.market_intelligence_run_items (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  source_item_id UUID NOT NULL REFERENCES public.market_source_items(id) ON DELETE RESTRICT,
  source_receipt_id UUID NOT NULL REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT,
  disposition TEXT NOT NULL CHECK (disposition IN (
    'accepted','duplicate','near_duplicate','dropped'
  )),
  drop_reason TEXT CHECK (drop_reason IS NULL OR char_length(drop_reason) <= 200),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, source_item_id, source_receipt_id),
  CHECK (
    (disposition='accepted' AND drop_reason IS NULL)
    OR (disposition<>'accepted' AND drop_reason IS NOT NULL)
  )
);

CREATE TABLE IF NOT EXISTS public.market_events (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL CHECK (char_length(event_type) BETWEEN 1 AND 80),
  title TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 500),
  summary TEXT NOT NULL CHECK (char_length(summary) <= 4000),
  occurred_at TIMESTAMPTZ,
  effective_at TIMESTAMPTZ,
  materiality NUMERIC(8,7) NOT NULL CHECK (materiality BETWEEN 0 AND 1),
  confidence NUMERIC(8,7) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  evidence_item_ids JSONB NOT NULL CHECK (
    jsonb_typeof(evidence_item_ids)='array'
    AND jsonb_array_length(evidence_item_ids) BETWEEN 1 AND 96
    AND octet_length(evidence_item_ids::text) <= 40960
  ),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_event_relationships (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  event_id UUID NOT NULL REFERENCES public.market_events(id) ON DELETE RESTRICT,
  source_kind TEXT NOT NULL CHECK (source_kind IN ('event','theme','value_chain','entity','security')),
  source_key TEXT NOT NULL CHECK (char_length(source_key) BETWEEN 1 AND 256),
  target_kind TEXT NOT NULL CHECK (target_kind IN ('theme','value_chain','entity','security','etf')),
  target_key TEXT NOT NULL CHECK (char_length(target_key) BETWEEN 1 AND 256),
  relationship_type TEXT NOT NULL CHECK (char_length(relationship_type) BETWEEN 1 AND 80),
  hypothesis BOOLEAN NOT NULL DEFAULT true,
  evidence_item_ids JSONB NOT NULL CHECK (
    jsonb_typeof(evidence_item_ids)='array'
    AND jsonb_array_length(evidence_item_ids) BETWEEN 1 AND 8
    AND octet_length(evidence_item_ids::text) <= 4096
  ),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_candidate_rankings (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  event_id UUID REFERENCES public.market_events(id) ON DELETE RESTRICT,
  candidate_key TEXT NOT NULL CHECK (char_length(candidate_key) BETWEEN 1 AND 256),
  ticker TEXT CHECK (ticker IS NULL OR ticker ~ '^[A-Z][A-Z0-9.-]{0,14}$'),
  rank INT NOT NULL CHECK (rank BETWEEN 1 AND 100),
  component_scores JSONB NOT NULL CHECK (
    jsonb_typeof(component_scores)='object' AND octet_length(component_scores::text) <= 8192
  ),
  total_score NUMERIC(12,6) NOT NULL CHECK (total_score BETWEEN -100000 AND 100000),
  qualified BOOLEAN NOT NULL,
  veto_reasons JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(veto_reasons)='array' AND jsonb_array_length(veto_reasons) <= 20
    AND octet_length(veto_reasons::text) <= 4096
  ),
  exposure_item_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(exposure_item_ids)='array' AND jsonb_array_length(exposure_item_ids) <= 8
    AND octet_length(exposure_item_ids::text) <= 4096
  ),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, candidate_key),
  UNIQUE (run_id, rank),
  CHECK (NOT qualified OR jsonb_array_length(exposure_item_ids) >= 1)
);

CREATE TABLE IF NOT EXISTS public.market_evidence_packets (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL UNIQUE REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  policy_version INT NOT NULL REFERENCES public.market_policy_config(version) ON DELETE RESTRICT,
  status TEXT NOT NULL CHECK (status='completed'),
  candidate_count INT NOT NULL CHECK (candidate_count BETWEEN 0 AND 12),
  evidence_count INT NOT NULL CHECK (evidence_count BETWEEN 0 AND 96),
  packet JSONB NOT NULL CHECK (
    jsonb_typeof(packet)='object' AND octet_length(packet::text) <= 98304
  ),
  packet_hash TEXT NOT NULL CHECK (packet_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE IF NOT EXISTS public.market_reports (
  id UUID PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  market_date DATE NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')),
  report JSONB NOT NULL CHECK (
    jsonb_typeof(report)='object' AND octet_length(report::text) <= 131072
  ),
  report_hash TEXT NOT NULL CHECK (report_hash ~ '^[0-9a-f]{64}$'),
  rendered_text TEXT NOT NULL CHECK (char_length(rendered_text) <= 14000),
  rendered_hash TEXT NOT NULL CHECK (rendered_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_market_reports_date_kind
  ON public.market_reports(market_date DESC, kind, created_at DESC);

ALTER TABLE public.market_reports
  ALTER COLUMN idempotency_key TYPE TEXT USING idempotency_key::text;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint
    WHERE conrelid='public.market_reports'::regclass
      AND conname='market_reports_idempotency_key_sha256'
  ) THEN
    ALTER TABLE public.market_reports ADD CONSTRAINT market_reports_idempotency_key_sha256
      CHECK (idempotency_key ~ '^[0-9a-f]{64}$');
  END IF;
END;
$$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_market_reports_packet_kind_date
  ON public.market_reports(run_id, packet_id, market_date, kind);

CREATE TABLE IF NOT EXISTS public.market_learning_observations (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  policy_version INT NOT NULL REFERENCES public.market_policy_config(version) ON DELETE RESTRICT,
  observation_type TEXT NOT NULL CHECK (observation_type IN (
    'outcome','missed-event','source-failure','noise'
  )),
  horizon_days INT NOT NULL CHECK (horizon_days BETWEEN 0 AND 3650),
  sample_size INT NOT NULL CHECK (sample_size BETWEEN 1 AND 1000000),
  benchmark TEXT CHECK (benchmark IS NULL OR char_length(benchmark) <= 100),
  observation JSONB NOT NULL CHECK (
    jsonb_typeof(observation)='object' AND octet_length(observation::text) <= 32768
  ),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, content_hash)
);

CREATE OR REPLACE FUNCTION public.reject_market_intelligence_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'market intelligence ledgers are append-only' USING ERRCODE = '55000';
END;
$$;

-- Canonical JSON is compact UTF-8 JSON: object keys are sorted lexicographically,
-- arrays retain order, and scalar values use PostgreSQL jsonb serialization.
CREATE OR REPLACE FUNCTION public.market_canonical_jsonb(p_value JSONB)
RETURNS TEXT LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog AS $$
DECLARE
  v_result TEXT;
BEGIN
  CASE jsonb_typeof(p_value)
    WHEN 'object' THEN
      SELECT '{' || COALESCE(string_agg(
        to_jsonb(entry.key)::text || ':' || public.market_canonical_jsonb(entry.value),
        ',' ORDER BY entry.key COLLATE "C"
      ), '') || '}' INTO v_result
      FROM jsonb_each(p_value) AS entry;
    WHEN 'array' THEN
      SELECT '[' || COALESCE(string_agg(
        public.market_canonical_jsonb(entry.value), ',' ORDER BY entry.ordinality
      ), '') || ']' INTO v_result
      FROM jsonb_array_elements(p_value) WITH ORDINALITY AS entry(value, ordinality);
    ELSE
      v_result := p_value::text;
  END CASE;
  RETURN v_result;
END;
$$;

DROP TRIGGER IF EXISTS market_intelligence_runs_append_only ON public.market_intelligence_runs;
CREATE TRIGGER market_intelligence_runs_append_only BEFORE UPDATE OR DELETE
ON public.market_intelligence_runs FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_intelligence_run_events_append_only ON public.market_intelligence_run_events;
CREATE TRIGGER market_intelligence_run_events_append_only BEFORE UPDATE OR DELETE
ON public.market_intelligence_run_events FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_source_quota_reservations_append_only ON public.market_source_quota_reservations;
CREATE TRIGGER market_source_quota_reservations_append_only BEFORE UPDATE OR DELETE
ON public.market_source_quota_reservations FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_source_receipts_append_only ON public.market_source_receipts;
CREATE TRIGGER market_source_receipts_append_only BEFORE UPDATE OR DELETE
ON public.market_source_receipts FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_source_items_append_only ON public.market_source_items;
CREATE TRIGGER market_source_items_append_only BEFORE UPDATE OR DELETE
ON public.market_source_items FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_intelligence_run_items_append_only ON public.market_intelligence_run_items;
CREATE TRIGGER market_intelligence_run_items_append_only BEFORE UPDATE OR DELETE
ON public.market_intelligence_run_items FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_events_append_only ON public.market_events;
CREATE TRIGGER market_events_append_only BEFORE UPDATE OR DELETE
ON public.market_events FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_event_relationships_append_only ON public.market_event_relationships;
CREATE TRIGGER market_event_relationships_append_only BEFORE UPDATE OR DELETE
ON public.market_event_relationships FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_candidate_rankings_append_only ON public.market_candidate_rankings;
CREATE TRIGGER market_candidate_rankings_append_only BEFORE UPDATE OR DELETE
ON public.market_candidate_rankings FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_evidence_packets_append_only ON public.market_evidence_packets;
CREATE TRIGGER market_evidence_packets_append_only BEFORE UPDATE OR DELETE
ON public.market_evidence_packets FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_reports_append_only ON public.market_reports;
CREATE TRIGGER market_reports_append_only BEFORE UPDATE OR DELETE
ON public.market_reports FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_learning_observations_append_only ON public.market_learning_observations;
CREATE TRIGGER market_learning_observations_append_only BEFORE UPDATE OR DELETE
ON public.market_learning_observations FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();

ALTER TABLE public.market_intelligence_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_intelligence_run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_source_quota_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_source_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_source_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_intelligence_run_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_event_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_candidate_rankings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_evidence_packets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_learning_observations ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE
  public.market_intelligence_runs,
  public.market_intelligence_run_events,
  public.market_source_quota_reservations,
  public.market_source_receipts,
  public.market_source_items,
  public.market_intelligence_run_items,
  public.market_events,
  public.market_event_relationships,
  public.market_candidate_rankings,
  public.market_evidence_packets,
  public.market_reports,
  public.market_learning_observations
FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,
  p_phase TEXT,
  p_market_date DATE,
  p_policy_version INT,
  p_reservation_plan JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_existing public.market_intelligence_runs%ROWTYPE;
  v_policy JSONB;
  v_reservation JSONB;
  v_provider TEXT;
  v_requested INT;
  v_phase_budget INT;
  v_daily_ceiling INT;
  v_existing_total INT;
  v_existing_phase INT;
  v_reservation_ids JSONB;
  v_cache_entries JSONB;
  v_was_existing BOOLEAN := false;
BEGIN
  IF p_run_id IS NULL OR p_market_date IS NULL OR p_policy_version <= 0
     OR p_phase NOT IN ('pre-market','intraday','post-market','on-demand')
     OR jsonb_typeof(p_reservation_plan)<>'object'
     OR NOT (p_reservation_plan ?& ARRAY['reservations'])
     OR (p_reservation_plan - ARRAY['reservations']) <> '{}'::jsonb
     OR jsonb_typeof(p_reservation_plan->'reservations')<>'array'
     OR jsonb_array_length(p_reservation_plan->'reservations') > 13
     OR octet_length(p_reservation_plan::text) > 32768 THEN
    RAISE EXCEPTION 'invalid intelligence reservation plan' USING ERRCODE = '22023';
  END IF;
  SELECT config INTO v_policy FROM public.market_policy_config
  WHERE version=p_policy_version;
  IF NOT FOUND OR jsonb_typeof(v_policy->'intelligence')<>'object' THEN
    RAISE EXCEPTION 'intelligence policy unavailable' USING ERRCODE = '22023';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-run:' || p_run_id::text, 0
  ));
  SELECT * INTO v_existing FROM public.market_intelligence_runs WHERE id=p_run_id;
  v_was_existing := FOUND;
  IF v_was_existing THEN
    IF v_existing.phase IS DISTINCT FROM p_phase
       OR v_existing.market_date IS DISTINCT FROM p_market_date
       OR v_existing.policy_version IS DISTINCT FROM p_policy_version
       OR v_existing.reservation_plan IS DISTINCT FROM p_reservation_plan THEN
      RAISE EXCEPTION 'intelligence run idempotency mismatch' USING ERRCODE = '22023';
    END IF;
  ELSE
    IF (SELECT count(*) FROM jsonb_array_elements(p_reservation_plan->'reservations')) <>
       (SELECT count(DISTINCT value->>'provider')
          FROM jsonb_array_elements(p_reservation_plan->'reservations')) THEN
      RAISE EXCEPTION 'duplicate provider reservation' USING ERRCODE = '22023';
    END IF;
    FOR v_reservation IN
      SELECT value FROM jsonb_array_elements(p_reservation_plan->'reservations')
      ORDER BY value->>'provider'
    LOOP
      IF jsonb_typeof(v_reservation)<>'object'
         OR NOT (v_reservation ?& ARRAY['id','provider','requests','cache_keys'])
         OR (v_reservation - ARRAY['id','provider','requests','cache_keys']) <> '{}'::jsonb
         OR v_reservation->>'provider' NOT IN (
           'gdelt','alpha_vantage','finnhub','yahoo','sec_edgar','federal_register',
           'white_house','doe','dod','eia','fred','bls','bea','social'
         )
         OR jsonb_typeof(v_reservation->'requests')<>'number'
         OR (v_reservation->>'requests') !~ '^[0-9]+$'
         OR (v_reservation->>'requests')::int NOT BETWEEN 1 AND 100
         OR jsonb_typeof(v_reservation->'cache_keys')<>'array'
         OR jsonb_array_length(v_reservation->'cache_keys') > 20
         OR EXISTS (
           SELECT 1 FROM jsonb_array_elements(v_reservation->'cache_keys') key
            WHERE jsonb_typeof(key)<>'string' OR char_length(key #>> '{}') NOT BETWEEN 1 AND 512
         ) THEN
        RAISE EXCEPTION 'invalid provider reservation' USING ERRCODE = '22023';
      END IF;
      BEGIN
        PERFORM (v_reservation->>'id')::uuid;
      EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'invalid provider reservation id' USING ERRCODE = '22023';
      END;
      v_provider := v_reservation->>'provider';
      v_requested := (v_reservation->>'requests')::int;
      IF v_provider='alpha_vantage' THEN
        v_phase_budget := COALESCE((v_policy #>> ARRAY[
          'intelligence','alpha_vantage_phase_budget',p_phase
        ])::int, 0);
        v_daily_ceiling := LEAST(COALESCE((v_policy #>> ARRAY[
          'intelligence','alpha_vantage_daily_ceiling'
        ])::int, 0), 20);
      ELSE
        v_phase_budget := COALESCE((v_policy #>> ARRAY[
          'intelligence','provider_phase_budgets',v_provider,p_phase
        ])::int, 0);
        v_daily_ceiling := 1000000;
      END IF;
      IF v_requested > v_phase_budget THEN
        RAISE EXCEPTION 'provider phase quota exceeded' USING ERRCODE = '54000';
      END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended(
        'market-intelligence-quota:' || v_provider || ':' || p_market_date::text, 0
      ));
      SELECT COALESCE(sum(reserved_requests),0) INTO v_existing_total
      FROM public.market_source_quota_reservations
      WHERE provider=v_provider AND market_date=p_market_date;
      SELECT COALESCE(sum(reserved_requests),0) INTO v_existing_phase
      FROM public.market_source_quota_reservations
      WHERE provider=v_provider AND market_date=p_market_date AND phase=p_phase;
      IF v_provider='alpha_vantage' AND v_existing_phase + v_requested > v_phase_budget THEN
        RAISE EXCEPTION 'alpha vantage phase quota exceeded' USING ERRCODE = '54000';
      END IF;
      IF v_provider='alpha_vantage' AND v_existing_total + v_requested > v_daily_ceiling THEN
        RAISE EXCEPTION 'alpha vantage daily quota exceeded' USING ERRCODE = '54000';
      END IF;
    END LOOP;

    INSERT INTO public.market_intelligence_runs(
      id,phase,market_date,policy_version,reservation_plan
    ) VALUES (p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan);
    FOR v_reservation IN SELECT value FROM jsonb_array_elements(p_reservation_plan->'reservations')
    LOOP
      INSERT INTO public.market_source_quota_reservations(
        id,run_id,provider,market_date,phase,reserved_requests,cache_keys
      ) VALUES (
        (v_reservation->>'id')::uuid,p_run_id,v_reservation->>'provider',p_market_date,p_phase,
        (v_reservation->>'requests')::int,v_reservation->'cache_keys'
      );
    END LOOP;
    INSERT INTO public.market_intelligence_run_events(id,run_id,status,detail)
    VALUES (gen_random_uuid(),p_run_id,'started',jsonb_build_object(
      'policy_version',p_policy_version,'phase',p_phase,'market_date',p_market_date
    ));
  END IF;

  SELECT COALESCE(jsonb_agg(id::text ORDER BY provider),'[]'::jsonb)
  INTO v_reservation_ids FROM public.market_source_quota_reservations WHERE run_id=p_run_id;
  SELECT COALESCE(jsonb_agg(cache_entry ORDER BY retrieved_at DESC),'[]'::jsonb)
  INTO v_cache_entries
  FROM (
    SELECT receipt.retrieved_at, jsonb_build_object(
      'receipt_id',receipt.id,
      'item_id',item.id,
      'provider',receipt.provider,
      'cache_key',receipt.cache_key,
      'retrieved_at',receipt.retrieved_at,
      'expires_at',receipt.expires_at,
      'content_hash',item.content_hash,
      'title',item.title,
      'normalized_text',item.normalized_text,
      'canonical_url',item.canonical_url,
      'published_at',item.published_at,
      'effective_at',item.effective_at,
      'metadata',item.metadata
    ) AS cache_entry
    FROM public.market_source_receipts receipt
    JOIN public.market_source_items item ON item.source_receipt_id=receipt.id
    WHERE receipt.status IN ('succeeded','cache_hit')
      AND receipt.expires_at > statement_timestamp()
      AND EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_reservation_plan->'reservations') requested
        WHERE requested->>'provider'=receipt.provider
          AND requested->'cache_keys' ? receipt.cache_key
      )
    ORDER BY receipt.retrieved_at DESC, item.id
    LIMIT 50
  ) bounded_cache;
  RETURN jsonb_build_object(
    'run_id',p_run_id,
    'reservation_ids',v_reservation_ids,
    'cache_entries',v_cache_entries,
    'duplicate',v_was_existing
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(
  p_run_id UUID,
  p_completion_id UUID,
  p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_run public.market_intelligence_runs%ROWTYPE;
  v_existing public.market_intelligence_run_events%ROWTYPE;
  v_row JSONB;
  v_receipt public.market_source_receipts%ROWTYPE;
  v_reservation public.market_source_quota_reservations%ROWTYPE;
  v_packet JSONB;
  v_candidate JSONB;
  v_evidence_id JSONB;
  v_source_host TEXT;
  v_payload_hash TEXT;
  v_receipt_json JSONB;
  v_item_count INT := 0;
  v_receipt_count INT := 0;
  v_event_count INT := 0;
  v_relationship_count INT := 0;
  v_ranking_count INT := 0;
BEGIN
  IF p_run_id IS NULL OR p_completion_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR octet_length(p_payload::text) > 1048576
     OR NOT (p_payload ?& ARRAY[
       'status','coverage','receipts','items','events','relationships','rankings','packet','error'
     ])
     OR (p_payload - ARRAY[
       'status','coverage','receipts','items','events','relationships','rankings','packet','error'
     ]) <> '{}'::jsonb
     OR p_payload->>'status' NOT IN ('completed','failed')
     OR jsonb_typeof(p_payload->'coverage')<>'object'
     OR octet_length((p_payload->'coverage')::text)>32768
     OR jsonb_typeof(p_payload->'receipts')<>'array'
     OR jsonb_array_length(p_payload->'receipts')>100
     OR jsonb_typeof(p_payload->'items')<>'array'
     OR jsonb_array_length(p_payload->'items')>500
     OR jsonb_typeof(p_payload->'events')<>'array'
     OR jsonb_array_length(p_payload->'events')>100
     OR jsonb_typeof(p_payload->'relationships')<>'array'
     OR jsonb_array_length(p_payload->'relationships')>500
     OR jsonb_typeof(p_payload->'rankings')<>'array'
     OR jsonb_array_length(p_payload->'rankings')>100
     OR (p_payload->>'status'='completed' AND jsonb_typeof(p_payload->'packet')<>'object')
     OR (p_payload->>'status'='completed' AND p_payload->'error'<>'null'::jsonb)
     OR (p_payload->>'status'='failed' AND p_payload->'packet'<>'null'::jsonb)
     OR (p_payload->>'status'='failed' AND jsonb_typeof(p_payload->'error')<>'object')
     OR (p_payload->>'status'='failed' AND octet_length((p_payload->'error')::text)>4096) THEN
    RAISE EXCEPTION 'invalid intelligence completion payload' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-completion:' || p_completion_id::text, 0
  ));
  v_payload_hash := encode(extensions.digest(p_payload::text,'sha256'),'hex');
  SELECT * INTO v_existing FROM public.market_intelligence_run_events WHERE id=p_completion_id;
  IF FOUND THEN
    IF v_existing.run_id IS DISTINCT FROM p_run_id
       OR v_existing.status IS DISTINCT FROM p_payload->>'status'
       OR v_existing.detail->>'payload_hash' IS DISTINCT FROM v_payload_hash THEN
      RAISE EXCEPTION 'intelligence completion idempotency mismatch' USING ERRCODE = '22023';
    END IF;
    RETURN (v_existing.detail->'receipt') || jsonb_build_object('duplicate',true);
  END IF;
  SELECT * INTO v_run FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'intelligence run unavailable' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.market_intelligence_run_events
    WHERE run_id=p_run_id AND status IN ('completed','failed')
  ) THEN
    RAISE EXCEPTION 'intelligence run already terminal' USING ERRCODE = '22023';
  END IF;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'receipts') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','reservation_id','status','cache_key','requested_window','retrieved_at','expires_at',
         'request_cost','upstream_remaining','returned_count','accepted_count','duplicate_count',
         'dropped_count','error','response_hash'
       ])
       OR (v_row - ARRAY[
         'id','reservation_id','status','cache_key','requested_window','retrieved_at','expires_at',
         'request_cost','upstream_remaining','returned_count','accepted_count','duplicate_count',
         'dropped_count','error','response_hash'
       ]) <> '{}'::jsonb
       OR v_row->>'status' NOT IN (
         'succeeded','failed','cache_hit','quota_blocked','configuration_missing'
       )
       OR char_length(v_row->>'cache_key') NOT BETWEEN 1 AND 512
       OR jsonb_typeof(v_row->'requested_window')<>'object'
       OR octet_length((v_row->'requested_window')::text)>8192
       OR (v_row->>'request_cost') !~ '^[0-9]+$'
       OR (v_row->>'request_cost')::int NOT BETWEEN 0 AND 100
       OR (v_row->>'returned_count') !~ '^[0-9]+$'
       OR (v_row->>'accepted_count') !~ '^[0-9]+$'
       OR (v_row->>'duplicate_count') !~ '^[0-9]+$'
       OR (v_row->>'dropped_count') !~ '^[0-9]+$'
       OR (v_row->>'status' IN ('succeeded','cache_hit')
         AND COALESCE(v_row->>'response_hash','') !~ '^[0-9a-f]{64}$')
       OR (v_row->>'status' NOT IN ('succeeded','cache_hit')
         AND v_row->'response_hash'<>'null'::jsonb
         AND v_row->>'response_hash' !~ '^[0-9a-f]{64}$')
       OR (v_row->>'status' NOT IN ('succeeded','cache_hit') AND v_row->'expires_at'<>'null'::jsonb)
       OR (v_row->>'status' IN ('succeeded','cache_hit') AND v_row->'expires_at'='null'::jsonb)
       OR (v_row->'error'<>'null'::jsonb AND (
         jsonb_typeof(v_row->'error')<>'object' OR octet_length((v_row->'error')::text)>4096
       )) THEN
      RAISE EXCEPTION 'invalid market source receipt' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_reservation FROM public.market_source_quota_reservations
    WHERE id=(v_row->>'reservation_id')::uuid AND run_id=p_run_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE = '22023';
    END IF;
    IF (v_row->>'request_cost')::int > v_reservation.reserved_requests THEN
      RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE = '54000';
    END IF;
    INSERT INTO public.market_source_receipts(
      id,run_id,reservation_id,provider,status,cache_key,requested_window,retrieved_at,expires_at,
      request_cost,upstream_remaining,returned_count,accepted_count,duplicate_count,dropped_count,
      error,response_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,v_reservation.id,v_reservation.provider,v_row->>'status',
      v_row->>'cache_key',v_row->'requested_window',(v_row->>'retrieved_at')::timestamptz,
      (v_row->>'expires_at')::timestamptz,(v_row->>'request_cost')::int,
      (v_row->>'upstream_remaining')::int,(v_row->>'returned_count')::int,
      (v_row->>'accepted_count')::int,(v_row->>'duplicate_count')::int,
      (v_row->>'dropped_count')::int,NULLIF(v_row->'error','null'::jsonb),v_row->>'response_hash'
    );
    v_receipt_count := v_receipt_count + 1;
  END LOOP;
  IF EXISTS (
    SELECT 1
    FROM public.market_source_quota_reservations reservation
    JOIN (
      SELECT (value->>'reservation_id')::uuid AS reservation_id,
             sum((value->>'request_cost')::int) AS used
      FROM jsonb_array_elements(p_payload->'receipts') GROUP BY 1
    ) usage ON usage.reservation_id=reservation.id
    WHERE reservation.run_id=p_run_id AND usage.used>reservation.reserved_requests
  ) THEN
    RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE = '54000';
  END IF;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'items') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','run_item_id','receipt_id','upstream_item_id','canonical_url','published_at',
         'effective_at','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ])
       OR (v_row - ARRAY[
         'id','run_item_id','receipt_id','upstream_item_id','canonical_url','published_at',
         'effective_at','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ]) <> '{}'::jsonb
       OR char_length(v_row->>'title') NOT BETWEEN 1 AND 500
       OR char_length(v_row->>'normalized_text') > 2000
       OR char_length(v_row->>'canonical_content') > 4096
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(
         extensions.digest(convert_to(v_row->>'canonical_content','UTF8'),'sha256'),'hex'
       )
       OR jsonb_typeof(v_row->'metadata')<>'object'
       OR octet_length((v_row->'metadata')::text)>8192
       OR v_row->>'disposition' NOT IN ('accepted','duplicate','near_duplicate','dropped')
       OR (v_row->>'disposition'='accepted' AND v_row->'drop_reason'<>'null'::jsonb)
       OR (v_row->>'disposition'<>'accepted' AND (
         v_row->'drop_reason'='null'::jsonb OR char_length(v_row->>'drop_reason')>200
       ))
       OR (v_row->'canonical_url'<>'null'::jsonb AND (
         char_length(v_row->>'canonical_url')>2048 OR v_row->>'canonical_url' !~ '^https://'
       )) THEN
      RAISE EXCEPTION 'invalid market source item or content hash' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_receipt FROM public.market_source_receipts
    WHERE id=(v_row->>'receipt_id')::uuid AND run_id=p_run_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'source item receipt unavailable' USING ERRCODE = '22023';
    END IF;
    IF v_row->>'disposition'='accepted'
       AND v_receipt.status NOT IN ('succeeded','cache_hit') THEN
      RAISE EXCEPTION 'accepted source item requires successful receipt' USING ERRCODE = '22023';
    END IF;
    IF v_row->'canonical_url'<>'null'::jsonb THEN
      v_source_host := lower(substring(v_row->>'canonical_url' FROM '^https://([^/:?#]+)'));
      IF NOT (CASE v_receipt.provider
        WHEN 'gdelt' THEN v_source_host='api.gdeltproject.org'
        WHEN 'alpha_vantage' THEN v_source_host='www.alphavantage.co'
        WHEN 'finnhub' THEN v_source_host='finnhub.io'
        WHEN 'yahoo' THEN v_source_host='query1.finance.yahoo.com'
        WHEN 'sec_edgar' THEN v_source_host IN ('www.sec.gov','data.sec.gov')
        WHEN 'federal_register' THEN v_source_host='www.federalregister.gov'
        WHEN 'white_house' THEN v_source_host='www.whitehouse.gov'
        WHEN 'doe' THEN v_source_host='www.energy.gov'
        WHEN 'dod' THEN v_source_host='www.defense.gov'
        WHEN 'eia' THEN v_source_host IN ('api.eia.gov','www.eia.gov')
        WHEN 'fred' THEN v_source_host IN ('api.stlouisfed.org','fred.stlouisfed.org')
        WHEN 'bls' THEN v_source_host IN ('api.bls.gov','www.bls.gov')
        WHEN 'bea' THEN v_source_host IN ('apps.bea.gov','www.bea.gov')
        WHEN 'social' THEN v_source_host IN ('www.reddit.com','oauth.reddit.com')
        ELSE false END) THEN
        RAISE EXCEPTION 'source URL host mismatch' USING ERRCODE = '22023';
      END IF;
    END IF;
    INSERT INTO public.market_source_items(
      id,source_receipt_id,provider,upstream_item_id,canonical_url,published_at,effective_at,title,
      normalized_text,canonical_content,content_hash,metadata
    ) VALUES (
      (v_row->>'id')::uuid,v_receipt.id,v_receipt.provider,v_row->>'upstream_item_id',
      v_row->>'canonical_url',(v_row->>'published_at')::timestamptz,
      (v_row->>'effective_at')::timestamptz,v_row->>'title',v_row->>'normalized_text',
      v_row->>'canonical_content',v_row->>'content_hash',v_row->'metadata'
    );
    INSERT INTO public.market_intelligence_run_items(
      id,run_id,source_item_id,source_receipt_id,disposition,drop_reason
    ) VALUES (
      (v_row->>'run_item_id')::uuid,p_run_id,(v_row->>'id')::uuid,v_receipt.id,
      v_row->>'disposition',v_row->>'drop_reason'
    );
    v_item_count := v_item_count + 1;
  END LOOP;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'events') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','event_type','title','summary','occurred_at','effective_at','materiality',
         'confidence','evidence_item_ids','content_hash'
       ])
       OR (v_row - ARRAY[
         'id','event_type','title','summary','occurred_at','effective_at','materiality',
         'confidence','evidence_item_ids','content_hash'
       ]) <> '{}'::jsonb
       OR char_length(v_row->>'event_type') NOT BETWEEN 1 AND 80
       OR char_length(v_row->>'title') NOT BETWEEN 1 AND 500
       OR char_length(v_row->>'summary')>4000
       OR (v_row->>'materiality')::numeric NOT BETWEEN 0 AND 1
       OR (v_row->>'confidence')::numeric NOT BETWEEN 0 AND 1
       OR jsonb_typeof(v_row->'evidence_item_ids')<>'array'
       OR jsonb_array_length(v_row->'evidence_item_ids') NOT BETWEEN 1 AND 96
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_row - ARRAY['id','content_hash']),'UTF8'
       ),'sha256'),'hex') THEN
      RAISE EXCEPTION 'invalid market event' USING ERRCODE = '22023';
    END IF;
    FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_row->'evidence_item_ids') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1
        FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND run_item.disposition='accepted' AND receipt.run_id=p_run_id
          AND receipt.status IN ('succeeded','cache_hit')
      ) THEN
        RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023';
      END IF;
    END LOOP;
    INSERT INTO public.market_events(
      id,run_id,event_type,title,summary,occurred_at,effective_at,materiality,confidence,
      evidence_item_ids,content_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,v_row->>'event_type',v_row->>'title',v_row->>'summary',
      (v_row->>'occurred_at')::timestamptz,(v_row->>'effective_at')::timestamptz,
      (v_row->>'materiality')::numeric,(v_row->>'confidence')::numeric,
      v_row->'evidence_item_ids',v_row->>'content_hash'
    );
    v_event_count := v_event_count + 1;
  END LOOP;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'relationships') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','event_id','source_kind','source_key','target_kind','target_key',
         'relationship_type','hypothesis','evidence_item_ids','content_hash'
       ])
       OR (v_row - ARRAY[
         'id','event_id','source_kind','source_key','target_kind','target_key',
         'relationship_type','hypothesis','evidence_item_ids','content_hash'
       ]) <> '{}'::jsonb
       OR v_row->>'source_kind' NOT IN ('event','theme','value_chain','entity','security')
       OR v_row->>'target_kind' NOT IN ('theme','value_chain','entity','security','etf')
       OR char_length(v_row->>'source_key') NOT BETWEEN 1 AND 256
       OR char_length(v_row->>'target_key') NOT BETWEEN 1 AND 256
       OR char_length(v_row->>'relationship_type') NOT BETWEEN 1 AND 80
       OR jsonb_typeof(v_row->'hypothesis')<>'boolean'
       OR jsonb_typeof(v_row->'evidence_item_ids')<>'array'
       OR jsonb_array_length(v_row->'evidence_item_ids') NOT BETWEEN 1 AND 8
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_row - ARRAY['id','content_hash']),'UTF8'
       ),'sha256'),'hex')
       OR NOT EXISTS (
         SELECT 1 FROM public.market_events
         WHERE id=(v_row->>'event_id')::uuid AND run_id=p_run_id
       ) THEN
      RAISE EXCEPTION 'invalid market event relationship' USING ERRCODE = '22023';
    END IF;
    FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_row->'evidence_item_ids') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1 FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
      ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
    END LOOP;
    INSERT INTO public.market_event_relationships(
      id,run_id,event_id,source_kind,source_key,target_kind,target_key,relationship_type,
      hypothesis,evidence_item_ids,content_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,(v_row->>'event_id')::uuid,v_row->>'source_kind',
      v_row->>'source_key',v_row->>'target_kind',v_row->>'target_key',
      v_row->>'relationship_type',(v_row->>'hypothesis')::boolean,
      v_row->'evidence_item_ids',v_row->>'content_hash'
    );
    v_relationship_count := v_relationship_count + 1;
  END LOOP;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'rankings') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','event_id','candidate_key','ticker','rank','component_scores','total_score',
         'qualified','veto_reasons','exposure_item_ids','content_hash'
       ])
       OR (v_row - ARRAY[
         'id','event_id','candidate_key','ticker','rank','component_scores','total_score',
         'qualified','veto_reasons','exposure_item_ids','content_hash'
       ]) <> '{}'::jsonb
       OR char_length(v_row->>'candidate_key') NOT BETWEEN 1 AND 256
       OR (v_row->'ticker'<>'null'::jsonb AND v_row->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$')
       OR (v_row->>'rank')::int NOT BETWEEN 1 AND 100
       OR jsonb_typeof(v_row->'component_scores')<>'object'
       OR octet_length((v_row->'component_scores')::text)>8192
       OR (v_row->>'total_score')::numeric NOT BETWEEN -100000 AND 100000
       OR jsonb_typeof(v_row->'qualified')<>'boolean'
       OR jsonb_typeof(v_row->'veto_reasons')<>'array'
       OR jsonb_array_length(v_row->'veto_reasons')>20
       OR jsonb_typeof(v_row->'exposure_item_ids')<>'array'
       OR jsonb_array_length(v_row->'exposure_item_ids')>8
       OR ((v_row->>'qualified')::boolean AND jsonb_array_length(v_row->'exposure_item_ids')=0)
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_row - ARRAY['id','content_hash']),'UTF8'
       ),'sha256'),'hex')
       OR (v_row->'event_id'<>'null'::jsonb AND NOT EXISTS (
         SELECT 1 FROM public.market_events
         WHERE id=(v_row->>'event_id')::uuid AND run_id=p_run_id
       )) THEN
      RAISE EXCEPTION 'invalid market candidate ranking' USING ERRCODE = '22023';
    END IF;
    FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_row->'exposure_item_ids') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1 FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
      ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
    END LOOP;
    INSERT INTO public.market_candidate_rankings(
      id,run_id,event_id,candidate_key,ticker,rank,component_scores,total_score,qualified,
      veto_reasons,exposure_item_ids,content_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,(v_row->>'event_id')::uuid,v_row->>'candidate_key',
      v_row->>'ticker',(v_row->>'rank')::int,v_row->'component_scores',
      (v_row->>'total_score')::numeric,(v_row->>'qualified')::boolean,
      v_row->'veto_reasons',v_row->'exposure_item_ids',v_row->>'content_hash'
    );
    v_ranking_count := v_ranking_count + 1;
  END LOOP;

  IF p_payload->>'status'='completed' THEN
    v_packet := p_payload->'packet';
    IF NOT (v_packet ?& ARRAY['id','candidate_count','evidence_count','packet','packet_hash'])
       OR (v_packet - ARRAY['id','candidate_count','evidence_count','packet','packet_hash']) <> '{}'::jsonb
       OR (v_packet->>'candidate_count')::int NOT BETWEEN 0 AND 12
       OR (v_packet->>'evidence_count')::int NOT BETWEEN 0 AND 96
       OR jsonb_typeof(v_packet->'packet')<>'object'
       OR octet_length((v_packet->'packet')::text)>98304
       OR v_packet->>'packet_hash' !~ '^[0-9a-f]{64}$'
       OR v_packet->>'packet_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_packet->'packet'),'UTF8'
       ),'sha256'),'hex')
       OR NOT (v_packet->'packet' ?& ARRAY[
         'candidates','evidence','coverage','limitations','policy_version'
       ])
       OR jsonb_typeof(v_packet->'packet'->'candidates')<>'array'
       OR jsonb_array_length(v_packet->'packet'->'candidates')<>(v_packet->>'candidate_count')::int
       OR jsonb_typeof(v_packet->'packet'->'evidence')<>'array'
       OR jsonb_array_length(v_packet->'packet'->'evidence')<>(v_packet->>'evidence_count')::int
       OR (v_packet->'packet'->>'policy_version')::int<>v_run.policy_version THEN
      RAISE EXCEPTION 'invalid or oversized evidence packet' USING ERRCODE = '22023';
    END IF;
    FOR v_candidate IN SELECT value FROM jsonb_array_elements(v_packet->'packet'->'candidates') LOOP
      IF jsonb_typeof(v_candidate)<>'object'
         OR jsonb_typeof(v_candidate->'evidence_ids')<>'array'
         OR jsonb_array_length(v_candidate->'evidence_ids')>8 THEN
        RAISE EXCEPTION 'evidence per candidate exceeds limit' USING ERRCODE = '22023';
      END IF;
    END LOOP;
    FOR v_evidence_id IN SELECT value->'item_id'
      FROM jsonb_array_elements(v_packet->'packet'->'evidence') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1 FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
      ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
    END LOOP;
    FOR v_candidate IN SELECT value FROM jsonb_array_elements(v_packet->'packet'->'candidates') LOOP
      FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_candidate->'evidence_ids') LOOP
        IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
          SELECT 1 FROM public.market_intelligence_run_items run_item
          JOIN public.market_source_items item ON item.id=run_item.source_item_id
          JOIN public.market_source_receipts receipt
            ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
          WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
            AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
            AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
        ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
      END LOOP;
    END LOOP;
    INSERT INTO public.market_evidence_packets(
      id,run_id,policy_version,status,candidate_count,evidence_count,packet,packet_hash
    ) VALUES (
      (v_packet->>'id')::uuid,p_run_id,v_run.policy_version,'completed',
      (v_packet->>'candidate_count')::int,(v_packet->>'evidence_count')::int,
      v_packet->'packet',v_packet->>'packet_hash'
    );
  END IF;

  v_receipt_json := jsonb_build_object(
    'run_id',p_run_id,
    'completion_id',p_completion_id,
    'status',p_payload->>'status',
    'counts',jsonb_build_object(
      'source_receipts',v_receipt_count,
      'source_items',v_item_count,
      'events',v_event_count,
      'relationships',v_relationship_count,
      'rankings',v_ranking_count,
      'packets',CASE WHEN p_payload->>'status'='completed' THEN 1 ELSE 0 END
    ),
    'packet_id',CASE WHEN p_payload->>'status'='completed' THEN v_packet->>'id' ELSE NULL END,
    'packet_hash',CASE WHEN p_payload->>'status'='completed' THEN v_packet->>'packet_hash' ELSE NULL END,
    'duplicate',false
  );
  INSERT INTO public.market_intelligence_run_events(id,run_id,status,detail)
  VALUES (
    p_completion_id,p_run_id,p_payload->>'status',jsonb_build_object(
      'payload_hash',v_payload_hash,
      'coverage',p_payload->'coverage',
      'error',p_payload->'error',
      'receipt',v_receipt_json
    )
  );
  RETURN v_receipt_json;
END;
$$;

CREATE OR REPLACE FUNCTION public.read_market_evidence_packet(
  p_packet_id UUID,
  p_run_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_result JSONB;
BEGIN
  IF p_packet_id IS NULL OR p_run_id IS NULL THEN
    RAISE EXCEPTION 'packet and run identifiers are required' USING ERRCODE = '22023';
  END IF;

  SELECT jsonb_build_object(
    'id',packet.id,
    'run_id',packet.run_id,
    'packet_hash',packet.packet_hash,
    'packet',packet.packet,
    'exposure_facts',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'candidate_key',fact.candidate_key,
        'evidence_id',fact.evidence_id,
        'exposure_kind',fact.exposure_kind,
        'status',fact.freshness,
        'observed_at',fact.observed_at,
        'retrieved_at',fact.retrieved_at
      ) ORDER BY fact.candidate_key,fact.evidence_id)
      FROM (
        SELECT DISTINCT ranking.candidate_key,
          item.id AS evidence_id,
          item.metadata->>'exposure_kind' AS exposure_kind,
          CASE WHEN receipt.expires_at>statement_timestamp()
                    AND COALESCE(item.effective_at,item.published_at) IS NOT NULL
            THEN 'fresh' ELSE 'stale' END AS freshness,
          COALESCE(item.effective_at,item.published_at) AS observed_at,
          receipt.retrieved_at
        FROM public.market_candidate_rankings ranking
        CROSS JOIN LATERAL jsonb_array_elements_text(ranking.exposure_item_ids)
          AS exposure_id(value)
        JOIN public.market_intelligence_run_items run_item
          ON run_item.run_id=ranking.run_id
          AND run_item.source_item_id=exposure_id.value::uuid
          AND run_item.disposition='accepted'
        JOIN public.market_source_items item
          ON item.id=run_item.source_item_id
          AND item.source_receipt_id=run_item.source_receipt_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id
          AND receipt.run_id=ranking.run_id
          AND receipt.status IN ('succeeded','cache_hit')
        WHERE ranking.run_id=packet.run_id AND ranking.qualified
          AND item.metadata->>'authority'='official'
          AND item.metadata->>'exposure_kind' IN (
            'filing','contract','backlog','revenue','capacity','official_fund'
          )
          AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(packet.packet->'candidates') candidate
            WHERE candidate->>'candidate_key'=ranking.candidate_key
              AND candidate->'evidence_ids' ? item.id::text
          )
      ) fact
    ),'[]'::jsonb)
  ) INTO v_result
  FROM public.market_evidence_packets packet
  JOIN public.market_intelligence_run_events event
    ON event.run_id=packet.run_id AND event.status='completed'
  WHERE packet.id=p_packet_id AND packet.run_id=p_run_id AND packet.status='completed';

  IF v_result IS NOT NULL AND octet_length(v_result::text)>131072 THEN
    RAISE EXCEPTION 'packet read exceeds bound' USING ERRCODE = '22023';
  END IF;
  RETURN v_result;
END;
$$;

DROP FUNCTION IF EXISTS public.record_market_report(UUID, UUID, JSONB);
CREATE OR REPLACE FUNCTION public.record_market_report(
  p_run_id UUID,
  p_idempotency_key TEXT,
  p_report JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_existing public.market_reports%ROWTYPE;
  v_packet public.market_evidence_packets%ROWTYPE;
  v_expected_key TEXT;
  v_expected_id UUID;
BEGIN
  IF p_run_id IS NULL OR p_idempotency_key IS NULL
     OR p_idempotency_key !~ '^[0-9a-f]{64}$' OR jsonb_typeof(p_report)<>'object'
     OR octet_length(p_report::text)>196608
     OR NOT (p_report ?& ARRAY[
       'id','packet_id','market_date','kind','report','report_hash','rendered_text','rendered_hash'
     ])
     OR (p_report - ARRAY[
       'id','packet_id','market_date','kind','report','report_hash','rendered_text','rendered_hash'
     ]) <> '{}'::jsonb
     OR p_report->>'kind' NOT IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')
     OR jsonb_typeof(p_report->'report')<>'object'
     OR octet_length((p_report->'report')::text)>131072
     OR p_report->>'report_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'report_hash' <> encode(extensions.digest(convert_to(
       public.market_canonical_jsonb(p_report->'report'),'UTF8'
     ),'sha256'),'hex')
     OR jsonb_typeof(p_report->'rendered_text')<>'string'
     OR char_length(p_report->>'rendered_text')>14000
     OR p_report->>'rendered_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'rendered_hash' <> encode(extensions.digest(
       convert_to(p_report->>'rendered_text','UTF8'),'sha256'
     ),'hex')
     OR jsonb_typeof(p_report->'report'->'source_ids') <> 'array'
     OR jsonb_typeof(p_report->'report'->'policy_decision_ids') <> 'array'
     OR jsonb_typeof(p_report->'report'->'comparison_ids') <> 'array'
     OR jsonb_array_length(p_report->'report'->'source_ids') = 0
     OR jsonb_array_length(p_report->'report'->'policy_decision_ids') = 0
     OR jsonb_array_length(p_report->'report'->'comparison_ids') <> 0
     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'source_ids') item
       WHERE item !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'policy_decision_ids') item
       WHERE item !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') THEN
    RAISE EXCEPTION 'invalid market report' USING ERRCODE = '22023';
  END IF;
  SELECT packet.* INTO v_packet
  FROM public.market_evidence_packets packet
  JOIN public.market_intelligence_run_events event
    ON event.run_id=packet.run_id AND event.status='completed'
  WHERE packet.id=(p_report->>'packet_id')::uuid
    AND packet.run_id=p_run_id AND packet.status='completed';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'completed evidence packet unavailable' USING ERRCODE = '22023';
  END IF;
  v_expected_key := encode(extensions.digest(convert_to(
    'v1:' || p_report->>'kind' || ':' || p_report->>'market_date' || ':' || v_packet.packet_hash,
    'UTF8'
  ), 'sha256'), 'hex');
  v_expected_id := (
    substr(v_expected_key,1,8) || '-' || substr(v_expected_key,9,4) || '-5' ||
    substr(v_expected_key,14,3) || '-8' || substr(v_expected_key,18,3) || '-' ||
    substr(v_expected_key,21,12)
  )::uuid;
  IF p_idempotency_key <> v_expected_key OR p_report->>'id' <> v_expected_id::text
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'source_ids') source_id
       WHERE NOT EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_packet.packet->'evidence') evidence
         WHERE evidence->>'item_id'=source_id
       )
     )
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'policy_decision_ids') decision_id
       WHERE NOT EXISTS (
         SELECT 1 FROM public.decision_evaluations evaluation
         WHERE evaluation.id=decision_id::uuid AND evaluation.run_id=p_run_id
       )
     ) THEN
    RAISE EXCEPTION 'market report chain mismatch' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-report:' || p_run_id::text || ':' || v_packet.id::text || ':' ||
      (p_report->>'market_date') || ':' || (p_report->>'kind'), 0
  ));
  SELECT * INTO v_existing FROM public.market_reports
  WHERE run_id=p_run_id AND packet_id=v_packet.id
    AND market_date=(p_report->>'market_date')::date AND kind=p_report->>'kind';
  IF FOUND THEN
    IF v_existing.run_id IS DISTINCT FROM p_run_id
       OR v_existing.id IS DISTINCT FROM v_expected_id
       OR v_existing.packet_id IS DISTINCT FROM (p_report->>'packet_id')::uuid
       OR v_existing.report_hash IS DISTINCT FROM p_report->>'report_hash'
       OR v_existing.rendered_text IS DISTINCT FROM p_report->>'rendered_text'
       OR v_existing.rendered_hash IS DISTINCT FROM p_report->>'rendered_hash' THEN
      RAISE EXCEPTION 'market report idempotency mismatch' USING ERRCODE = '22023';
    END IF;
    RETURN jsonb_build_object(
      'report_id',v_existing.id,
      'report_hash',v_existing.report_hash,
      'rendered_hash',v_existing.rendered_hash,
      'duplicate',true
    );
  END IF;
  INSERT INTO public.market_reports(
    id,idempotency_key,run_id,packet_id,market_date,kind,report,report_hash,rendered_text,rendered_hash
  ) VALUES (
    (p_report->>'id')::uuid,p_idempotency_key,p_run_id,v_packet.id,
    (p_report->>'market_date')::date,p_report->>'kind',p_report->'report',
    p_report->>'report_hash',p_report->>'rendered_text',p_report->>'rendered_hash'
  ) RETURNING * INTO v_existing;
  RETURN jsonb_build_object(
    'report_id',v_existing.id,
    'report_hash',v_existing.report_hash,
    'rendered_hash',v_existing.rendered_hash,
    'duplicate',false
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_learning(
  p_run_id UUID,
  p_observation JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_run public.market_intelligence_runs%ROWTYPE;
  v_existing public.market_learning_observations%ROWTYPE;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_observation)<>'object'
     OR octet_length(p_observation::text)>49152
     OR NOT (p_observation ?& ARRAY[
       'id','policy_version','observation_type','horizon_days','sample_size','benchmark',
       'observation','content_hash'
     ])
     OR (p_observation - ARRAY[
       'id','policy_version','observation_type','horizon_days','sample_size','benchmark',
       'observation','content_hash'
     ]) <> '{}'::jsonb
     OR p_observation->>'observation_type' NOT IN (
       'outcome','missed-event','source-failure','noise'
     )
     OR (p_observation->>'policy_version')::int <= 0
     OR (p_observation->>'horizon_days')::int NOT BETWEEN 0 AND 3650
     OR (p_observation->>'sample_size')::int NOT BETWEEN 1 AND 1000000
     OR (p_observation->'benchmark'<>'null'::jsonb
       AND char_length(p_observation->>'benchmark')>100)
     OR jsonb_typeof(p_observation->'observation')<>'object'
     OR octet_length((p_observation->'observation')::text)>32768
     OR (p_observation->'observation') ?| ARRAY[
       'apply','update','activate_policy','change_weights','add_provider',
       'mutate_holdings','mutate_plans','change_delivery'
     ]
     OR (
       jsonb_typeof(p_observation->'observation'->'proposed_change')='object'
       AND (p_observation->'observation'->'proposed_change') ?| ARRAY[
         'operation','rpc','apply','update','activate','provider_endpoint',
         'holding_mutation','plan_mutation','delivery_mutation'
       ]
     )
     OR p_observation->>'content_hash' !~ '^[0-9a-f]{64}$'
     OR p_observation->>'content_hash' <> encode(extensions.digest(convert_to(
       public.market_canonical_jsonb(p_observation->'observation'),'UTF8'
     ),'sha256'),'hex') THEN
    RAISE EXCEPTION 'invalid market learning observation' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-learning:' || (p_observation->>'id')::uuid::text, 0
  ));
  SELECT * INTO v_run FROM public.market_intelligence_runs WHERE id=p_run_id;
  IF NOT FOUND OR v_run.policy_version IS DISTINCT FROM (p_observation->>'policy_version')::int THEN
    RAISE EXCEPTION 'learning run or policy version mismatch' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_learning_observations
  WHERE id=(p_observation->>'id')::uuid;
  IF FOUND THEN
    IF v_existing.run_id IS DISTINCT FROM p_run_id
       OR v_existing.content_hash IS DISTINCT FROM p_observation->>'content_hash' THEN
      RAISE EXCEPTION 'market learning idempotency mismatch' USING ERRCODE = '22023';
    END IF;
    RETURN jsonb_build_object(
      'observation_id',v_existing.id,'content_hash',v_existing.content_hash,'duplicate',true
    );
  END IF;
  INSERT INTO public.market_learning_observations(
    id,run_id,policy_version,observation_type,horizon_days,sample_size,benchmark,
    observation,content_hash
  ) VALUES (
    (p_observation->>'id')::uuid,p_run_id,(p_observation->>'policy_version')::int,
    p_observation->>'observation_type',(p_observation->>'horizon_days')::int,
    (p_observation->>'sample_size')::int,p_observation->>'benchmark',
    p_observation->'observation',p_observation->>'content_hash'
  ) RETURNING * INTO v_existing;
  RETURN jsonb_build_object(
    'observation_id',v_existing.id,'content_hash',v_existing.content_hash,'duplicate',false
  );
END;
$$;

REVOKE ALL ON FUNCTION public.reject_market_intelligence_mutation()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.market_canonical_jsonb(JSONB)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID, TEXT, DATE, INT, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.read_market_evidence_packet(UUID, UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_market_report(UUID, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_market_learning(UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID, TEXT, DATE, INT, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_market_evidence_packet(UUID, UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_report(UUID, TEXT, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_learning(UUID, JSONB) TO service_role;

-- Consolidated from sql/migrations/20260908_owner_dashboard_intelligence_read_role.sql
-- Redacted owner-dashboard reads for immutable intelligence and report ledgers.
-- This migration grants direct column SELECT only; no application RPC is executable.

REVOKE ALL PRIVILEGES ON TABLE
  public.market_intelligence_runs,
  public.market_intelligence_run_events,
  public.market_source_quota_reservations,
  public.market_source_receipts,
  public.market_source_items,
  public.market_intelligence_run_items,
  public.market_events,
  public.market_event_relationships,
  public.market_candidate_rankings,
  public.market_evidence_packets,
  public.market_reports,
  public.market_learning_observations
FROM stock_agent_dashboard;

GRANT SELECT (id, phase, market_date, policy_version, created_at)
  ON public.market_intelligence_runs TO stock_agent_dashboard;
GRANT SELECT (run_id, status)
  ON public.market_intelligence_run_events TO stock_agent_dashboard;
GRANT SELECT (run_id, provider, status, retrieved_at, accepted_count, dropped_count)
  ON public.market_source_receipts TO stock_agent_dashboard;
GRANT SELECT (id, canonical_url, title)
  ON public.market_source_items TO stock_agent_dashboard;
GRANT SELECT (id, run_id, event_type, title, summary, occurred_at, effective_at, materiality,
              confidence, evidence_item_ids)
  ON public.market_events TO stock_agent_dashboard;
GRANT SELECT (id, run_id, source_key, target_kind, target_key, relationship_type, evidence_item_ids)
  ON public.market_event_relationships TO stock_agent_dashboard;
GRANT SELECT (id, run_id, event_id, candidate_key, ticker, rank, total_score, qualified,
              veto_reasons, exposure_item_ids)
  ON public.market_candidate_rankings TO stock_agent_dashboard;
GRANT SELECT (id, run_id, market_date, kind, report, report_hash, created_at)
  ON public.market_reports TO stock_agent_dashboard;

DROP POLICY IF EXISTS owner_dashboard_select_intelligence_runs ON public.market_intelligence_runs;
CREATE POLICY owner_dashboard_select_intelligence_runs ON public.market_intelligence_runs
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_intelligence_run_events ON public.market_intelligence_run_events;
CREATE POLICY owner_dashboard_select_intelligence_run_events ON public.market_intelligence_run_events
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_source_receipts ON public.market_source_receipts;
CREATE POLICY owner_dashboard_select_source_receipts ON public.market_source_receipts
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_source_items ON public.market_source_items;
CREATE POLICY owner_dashboard_select_source_items ON public.market_source_items
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_market_events ON public.market_events;
CREATE POLICY owner_dashboard_select_market_events ON public.market_events
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_event_relationships ON public.market_event_relationships;
CREATE POLICY owner_dashboard_select_event_relationships ON public.market_event_relationships
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_candidate_rankings ON public.market_candidate_rankings;
CREATE POLICY owner_dashboard_select_candidate_rankings ON public.market_candidate_rankings
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_reports ON public.market_reports;
CREATE POLICY owner_dashboard_select_reports ON public.market_reports
  FOR SELECT TO stock_agent_dashboard USING (true);

-- Consolidated from sql/migrations/20260909_transaction_chronology.sql
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

-- Consolidated from sql/migrations/20260910_delivery_outbox.sql
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

CREATE OR REPLACE FUNCTION public.suppress_market_report_publication(p_idempotency_key TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result public.market_report_publications%ROWTYPE;
BEGIN
  UPDATE public.market_report_publications SET status='suppressed',lease_token=NULL,lease_expires_at=NULL,
    telegram_message_ids='[]'::jsonb,telegram_accepted_at=NULL,updated_at=now()
  WHERE idempotency_key=p_idempotency_key AND status IN ('pending','failed')
  RETURNING * INTO v_result;
  IF NOT FOUND THEN SELECT * INTO v_result FROM public.market_report_publications WHERE idempotency_key=p_idempotency_key; END IF;
  IF NOT FOUND OR v_result.status<>'suppressed' THEN RAISE EXCEPTION 'report publication cannot be suppressed' USING ERRCODE='40001'; END IF;
  RETURN jsonb_build_object('report_id',v_result.report_id,'idempotency_key',v_result.idempotency_key,'status',v_result.status);
END;
$$;

REVOKE ALL ON TABLE public.market_report_publications FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.create_market_report_publication(UUID, UUID, TEXT, DATE, TEXT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.claim_market_report_publication(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_report_publication(TEXT, UUID, TEXT, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.suppress_market_report_publication(TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_market_report_publication(UUID, UUID, TEXT, DATE, TEXT, TEXT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.claim_market_report_publication(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_report_publication(TEXT, UUID, TEXT, JSONB, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.suppress_market_report_publication(TEXT) TO service_role;

-- Consolidated from sql/migrations/20260911_command_acknowledgements.sql
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

-- Consolidated from sql/migrations/202609120001_delivery_recovery_claims.sql
-- Existing deployments have already applied earlier request-claim migrations.
-- Reinstall the final claim routine so failed report deliveries can resume only
-- through their deterministic outbox key.
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
    IF (SELECT count(*) FROM public.market_gateway_requests WHERE created_at>=now()-interval '1 hour')>=100
       OR (v_stored_run IS NOT NULL AND (SELECT count(*) FROM public.market_gateway_requests WHERE run_id=v_stored_run)>=20) THEN
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

-- Consolidated from sql/migrations/202609120002_collection_cache_lineage.sql
-- Preserve cache-hit provenance without reusing a prior run reservation.
ALTER TABLE public.market_source_receipts
  ADD COLUMN IF NOT EXISTS cache_predecessor_receipt_id UUID
  REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT;

ALTER TABLE public.market_source_receipts
  DROP CONSTRAINT IF EXISTS market_source_receipts_cache_lineage_check;
ALTER TABLE public.market_source_receipts
  ADD CONSTRAINT market_source_receipts_cache_lineage_check CHECK (
    (status = 'cache_hit' AND request_cost = 0 AND cache_predecessor_receipt_id IS NOT NULL)
    OR (status <> 'cache_hit' AND cache_predecessor_receipt_id IS NULL)
  );

-- Consolidated from sql/migrations/20260913_command_acknowledgement_lease.sql
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

-- Consolidated from sql/migrations/20260914_provider_evidence_integrity.sql
-- Provider evidence v2: keep the canonical publisher item distinct from the
-- credential-free provider request. This is additive and leaves historical
-- source rows immutable.
CREATE TABLE IF NOT EXISTS public.market_source_item_provenance (
  source_item_id UUID PRIMARY KEY REFERENCES public.market_source_items(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL,
  canonical_item_url TEXT NOT NULL CHECK (char_length(canonical_item_url) <= 2048 AND canonical_item_url ~ '^https://'),
  request_url TEXT NOT NULL CHECK (char_length(request_url) <= 2048 AND request_url ~ '^https://'),
  retrieved_at TIMESTAMPTZ NOT NULL,
  reporting_at TIMESTAMPTZ,
  entity_ids JSONB NOT NULL CHECK (jsonb_typeof(entity_ids)='array' AND jsonb_array_length(entity_ids)<=32 AND octet_length(entity_ids::text)<=4096),
  security_ids JSONB NOT NULL CHECK (jsonb_typeof(security_ids)='array' AND jsonb_array_length(security_ids)<=32 AND octet_length(security_ids::text)<=1024),
  discovery_status TEXT NOT NULL CHECK (discovery_status IN ('qualified','no_event','insufficient_coverage')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.market_source_item_provenance ENABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS market_source_item_provenance_append_only ON public.market_source_item_provenance;
CREATE TRIGGER market_source_item_provenance_append_only BEFORE UPDATE OR DELETE
  ON public.market_source_item_provenance FOR EACH ROW EXECUTE FUNCTION public.reject_market_intelligence_mutation();
REVOKE ALL ON TABLE public.market_source_item_provenance FROM PUBLIC, anon, authenticated;

DO $$
BEGIN
  IF pg_catalog.to_regprocedure('public.record_market_intelligence(uuid,uuid,jsonb)') IS NOT NULL
     AND pg_catalog.to_regprocedure('public.record_market_intelligence_legacy(uuid,uuid,jsonb)') IS NULL THEN
    ALTER FUNCTION public.record_market_intelligence(UUID, UUID, JSONB)
      RENAME TO record_market_intelligence_legacy;
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(
  p_run_id UUID, p_completion_id UUID, p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_row JSONB;
  v_legacy_items JSONB;
  v_result JSONB;
  v_request_host TEXT;
  v_valid_request_host BOOLEAN;
BEGIN
  IF p_run_id IS NULL OR p_completion_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR jsonb_typeof(p_payload->'items')<>'array' THEN
    RAISE EXCEPTION 'invalid intelligence completion payload' USING ERRCODE='22023';
  END IF;
  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'items') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','run_item_id','receipt_id','provider','upstream_item_id','canonical_url','request_url',
         'published_at','retrieved_at','effective_at','reporting_at','entity_ids','security_ids',
         'discovery_status','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ])
       OR (v_row - ARRAY[
         'id','run_item_id','receipt_id','provider','upstream_item_id','canonical_url','request_url',
         'published_at','retrieved_at','effective_at','reporting_at','entity_ids','security_ids',
         'discovery_status','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ]) <> '{}'::jsonb
       OR COALESCE(char_length(v_row->>'upstream_item_id'),0) NOT BETWEEN 1 AND 512
       OR COALESCE(char_length(v_row->>'canonical_url'),0) NOT BETWEEN 1 AND 2048
       OR COALESCE(char_length(v_row->>'request_url'),0) NOT BETWEEN 1 AND 2048
       OR v_row->>'canonical_url' !~ '^https://' OR v_row->>'request_url' !~ '^https://'
       OR lower(v_row->>'request_url') ~ '(api[_-]?key|token|secret|password)='
       OR jsonb_typeof(v_row->'entity_ids')<>'array' OR jsonb_array_length(v_row->'entity_ids')>32
       OR jsonb_typeof(v_row->'security_ids')<>'array' OR jsonb_array_length(v_row->'security_ids')>32
       OR v_row->>'discovery_status' NOT IN ('qualified','no_event','insufficient_coverage')
       OR (v_row->>'discovery_status'='qualified' AND jsonb_array_length(v_row->'security_ids')=0) THEN
      RAISE EXCEPTION 'invalid provider evidence provenance' USING ERRCODE='22023';
    END IF;
    v_request_host := lower(substring(v_row->>'request_url' FROM '^https://([^/:?#]+)'));
    v_valid_request_host := CASE v_row->>'provider'
      WHEN 'gdelt' THEN v_request_host='api.gdeltproject.org'
      WHEN 'alpha_vantage' THEN v_request_host='www.alphavantage.co'
      WHEN 'finnhub' THEN v_request_host='finnhub.io'
      WHEN 'yahoo' THEN v_request_host='query1.finance.yahoo.com'
      WHEN 'sec_edgar' THEN v_request_host IN ('www.sec.gov','data.sec.gov')
      WHEN 'federal_register' THEN v_request_host='www.federalregister.gov'
      WHEN 'white_house' THEN v_request_host='www.whitehouse.gov'
      WHEN 'doe' THEN v_request_host='www.energy.gov'
      WHEN 'dod' THEN v_request_host='www.defense.gov'
      WHEN 'eia' THEN v_request_host IN ('api.eia.gov','www.eia.gov')
      WHEN 'fred' THEN v_request_host IN ('api.stlouisfed.org','fred.stlouisfed.org')
      WHEN 'bls' THEN v_request_host IN ('api.bls.gov','www.bls.gov')
      WHEN 'bea' THEN v_request_host IN ('apps.bea.gov','www.bea.gov')
      WHEN 'social' THEN v_request_host IN ('www.reddit.com','oauth.reddit.com')
      ELSE false END;
    IF NOT v_valid_request_host THEN
      RAISE EXCEPTION 'provider request URL host mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;

  SELECT jsonb_agg(
    ((value - ARRAY['provider','request_url','retrieved_at','reporting_at','entity_ids','security_ids','discovery_status'])
      || jsonb_build_object('canonical_url', value->'request_url'))
  ) INTO v_legacy_items FROM jsonb_array_elements(p_payload->'items');
  v_result := public.record_market_intelligence_legacy(
    p_run_id, p_completion_id, jsonb_set(p_payload, '{items}', COALESCE(v_legacy_items,'[]'::jsonb))
  );
  INSERT INTO public.market_source_item_provenance(
    source_item_id,provider,canonical_item_url,request_url,retrieved_at,reporting_at,
    entity_ids,security_ids,discovery_status
  )
  SELECT (value->>'id')::uuid,value->>'provider',value->>'canonical_url',value->>'request_url',
    (value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,
    value->'entity_ids',value->'security_ids',value->>'discovery_status'
  FROM jsonb_array_elements(p_payload->'items')
  ON CONFLICT (source_item_id) DO NOTHING;
  RETURN v_result;
END;
$$;
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) TO service_role;

-- Consolidated from sql/migrations/20260915_market_source_item_reuse.sql
-- Reuse immutable evidence identities across runs without weakening content checks.
DO $$
BEGIN
  IF pg_catalog.to_regprocedure('public.record_market_intelligence(uuid,uuid,jsonb)') IS NOT NULL
     AND pg_catalog.to_regprocedure('public.record_market_intelligence_provider_v2(uuid,uuid,jsonb)') IS NULL THEN
    ALTER FUNCTION public.record_market_intelligence(UUID, UUID, JSONB)
      RENAME TO record_market_intelligence_provider_v2;
  END IF;
END;
$$;

-- Upgrade the v2 wrapper additively: only a relationship-preserving
-- near_duplicate can enter Task 4 as accepted corroborating evidence.
DO $$
DECLARE definition_text TEXT;
BEGIN
  SELECT pg_get_functiondef('public.record_market_intelligence_provider_v2(uuid,uuid,jsonb)'::regprocedure) INTO definition_text;
  IF definition_text IS NULL THEN RAISE EXCEPTION 'provider evidence recorder unavailable' USING ERRCODE='22023'; END IF;
  definition_text := replace(definition_text, '|| jsonb_build_object(''canonical_url'', value->''request_url'')', '|| jsonb_build_object(''canonical_url'', value->''request_url'') || CASE WHEN value->>''disposition''=''near_duplicate'' THEN jsonb_build_object(''disposition'',''accepted'',''drop_reason'',NULL) ELSE ''{}''::jsonb END');
  EXECUTE definition_text;
END;
$$;

CREATE OR REPLACE FUNCTION public.reuse_market_source_item_if_immutable()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE existing_row public.market_source_items%ROWTYPE;
BEGIN
  SELECT * INTO existing_row FROM public.market_source_items WHERE id=NEW.id;
  IF NOT FOUND THEN RETURN NEW; END IF;
  IF existing_row.provider IS DISTINCT FROM NEW.provider
     OR existing_row.upstream_item_id IS DISTINCT FROM NEW.upstream_item_id
     OR existing_row.canonical_url IS DISTINCT FROM NEW.canonical_url
     OR existing_row.published_at IS DISTINCT FROM NEW.published_at
     OR existing_row.effective_at IS DISTINCT FROM NEW.effective_at
     OR existing_row.title IS DISTINCT FROM NEW.title
     OR existing_row.normalized_text IS DISTINCT FROM NEW.normalized_text
     OR existing_row.canonical_content IS DISTINCT FROM NEW.canonical_content
     OR existing_row.content_hash IS DISTINCT FROM NEW.content_hash
     OR existing_row.metadata IS DISTINCT FROM NEW.metadata THEN
    RAISE EXCEPTION 'conflicting immutable market source item identity' USING ERRCODE='22023';
  END IF;
  RETURN NULL;
END;
$$;
DROP TRIGGER IF EXISTS market_source_items_reuse_immutable ON public.market_source_items;
CREATE TRIGGER market_source_items_reuse_immutable BEFORE INSERT ON public.market_source_items
FOR EACH ROW EXECUTE FUNCTION public.reuse_market_source_item_if_immutable();

-- The legacy procedure owns Task 4 event/report completeness checks. Relax only
-- receipt identity equality: each run item is still required to reference its own
-- successful receipt, while its immutable source item may originate in an earlier run.
DO $$
DECLARE definition_text TEXT;
BEGIN
  SELECT pg_get_functiondef('public.record_market_intelligence_legacy(uuid,uuid,jsonb)'::regprocedure)
    INTO definition_text;
  IF definition_text IS NULL THEN
    RAISE EXCEPTION 'market intelligence legacy recorder unavailable' USING ERRCODE='22023';
  END IF;
  definition_text := replace(definition_text, ' AND receipt.id=item.source_receipt_id', '');
  EXECUTE definition_text;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(
  p_run_id UUID, p_completion_id UUID, p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
  -- Provider v2 validates provenance and converts only near_duplicate rows to
  -- accepted Task 4 evidence. Exact duplicates are never passed to the recorder.
  RETURN public.record_market_intelligence_provider_v2(p_run_id,p_completion_id,p_payload);
END;
$$;
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) TO service_role;

-- Consolidated from sql/migrations/20260916_run_scoped_request_provenance.sql
-- Keep request windows run-scoped; source identity remains content/publisher based.
CREATE TABLE IF NOT EXISTS public.market_run_source_item_provenance (
  run_item_id UUID PRIMARY KEY REFERENCES public.market_intelligence_run_items(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  source_item_id UUID NOT NULL REFERENCES public.market_source_items(id) ON DELETE RESTRICT,
  source_receipt_id UUID NOT NULL REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL,
  request_url TEXT NOT NULL CHECK (char_length(request_url)<=2048 AND request_url ~ '^https://' AND lower(request_url) !~ '(api[_-]?key|token|secret|password)='),
  retrieved_at TIMESTAMPTZ NOT NULL, reporting_at TIMESTAMPTZ,
  entity_ids JSONB NOT NULL CHECK (jsonb_typeof(entity_ids)='array' AND jsonb_array_length(entity_ids)<=32 AND octet_length(entity_ids::text)<=4096),
  security_ids JSONB NOT NULL CHECK (jsonb_typeof(security_ids)='array' AND jsonb_array_length(security_ids)<=32 AND octet_length(security_ids::text)<=1024),
  discovery_status TEXT NOT NULL CHECK (discovery_status IN ('qualified','no_event','insufficient_coverage')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE(run_id, source_item_id, source_receipt_id)
);
ALTER TABLE public.market_run_source_item_provenance ENABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS market_run_source_item_provenance_append_only ON public.market_run_source_item_provenance;
CREATE TRIGGER market_run_source_item_provenance_append_only BEFORE UPDATE OR DELETE ON public.market_run_source_item_provenance FOR EACH ROW EXECUTE FUNCTION public.reject_market_intelligence_mutation();
REVOKE ALL ON TABLE public.market_run_source_item_provenance FROM PUBLIC, anon, authenticated;

-- Request windows are not source identity. Content hash already commits provider,
-- stable upstream ID, publisher item URL, and claim polarity.
CREATE OR REPLACE FUNCTION public.reuse_market_source_item_if_immutable()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE existing_row public.market_source_items%ROWTYPE;
BEGIN
  SELECT * INTO existing_row FROM public.market_source_items WHERE id=NEW.id;
  IF NOT FOUND THEN RETURN NEW; END IF;
  IF existing_row.provider IS DISTINCT FROM NEW.provider OR existing_row.upstream_item_id IS DISTINCT FROM NEW.upstream_item_id OR existing_row.published_at IS DISTINCT FROM NEW.published_at OR existing_row.effective_at IS DISTINCT FROM NEW.effective_at OR existing_row.title IS DISTINCT FROM NEW.title OR existing_row.normalized_text IS DISTINCT FROM NEW.normalized_text OR existing_row.canonical_content IS DISTINCT FROM NEW.canonical_content OR existing_row.content_hash IS DISTINCT FROM NEW.content_hash OR existing_row.metadata IS DISTINCT FROM NEW.metadata THEN
    RAISE EXCEPTION 'conflicting immutable market source item identity' USING ERRCODE='22023';
  END IF;
  RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(p_run_id UUID,p_completion_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE result_row JSONB;
BEGIN
  result_row := public.record_market_intelligence_provider_v2(p_run_id,p_completion_id,p_payload);
  INSERT INTO public.market_run_source_item_provenance(run_item_id,run_id,source_item_id,source_receipt_id,provider,request_url,retrieved_at,reporting_at,entity_ids,security_ids,discovery_status)
  SELECT (value->>'run_item_id')::uuid,p_run_id,(value->>'id')::uuid,(value->>'receipt_id')::uuid,value->>'provider',value->>'request_url',(value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,value->'entity_ids',value->'security_ids',value->>'discovery_status'
  FROM jsonb_array_elements(p_payload->'items')
  ON CONFLICT (run_item_id) DO NOTHING;
  RETURN result_row;
END;
$$;
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) TO service_role;

-- Consolidated from sql/migrations/20260917_collection_checkpoint_hydration.sql
-- Additive deployed-contract boundary for restart-safe collection checkpoints.
CREATE TABLE IF NOT EXISTS public.market_collection_checkpoints (
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  cache_key TEXT NOT NULL CHECK (char_length(cache_key) BETWEEN 1 AND 512),
  request_window JSONB NOT NULL CHECK (jsonb_typeof(request_window) = 'object'),
  source_receipt_id UUID NOT NULL REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 65536),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (run_id, cache_key)
);
ALTER TABLE public.market_collection_checkpoints ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_collection_checkpoints FROM PUBLIC, anon, authenticated;

-- Consolidated from sql/migrations/20260918_durable_collection_controller.sql
-- Durable, restart-safe controller boundary.  This intentionally overrides the
-- deployed 20260916 wrappers instead of editing historical migrations.
ALTER TABLE public.market_intelligence_runs
  ADD COLUMN IF NOT EXISTS request_window JSONB;
ALTER TABLE public.market_intelligence_runs
  DROP CONSTRAINT IF EXISTS market_intelligence_runs_request_window_check;
ALTER TABLE public.market_intelligence_runs
  ADD CONSTRAINT market_intelligence_runs_request_window_check CHECK (
    request_window IS NULL OR (
      jsonb_typeof(request_window)='object'
      AND request_window ?& ARRAY['start','end','timezone','market_date','phase']
      AND (request_window - ARRAY['start','end','timezone','market_date','phase'])='{}'::jsonb
      AND request_window->>'timezone'='America/Chicago'
      AND request_window->>'phase'=phase
      AND request_window->>'market_date'=market_date::text
      AND (request_window->>'start')::timestamptz < (request_window->>'end')::timestamptz
    )
  );

ALTER TABLE public.market_collection_checkpoints
  DROP CONSTRAINT IF EXISTS market_collection_checkpoints_source_receipt_id_fkey;
ALTER TABLE public.market_collection_checkpoints
  DROP CONSTRAINT IF EXISTS market_collection_checkpoints_payload_check;
ALTER TABLE public.market_collection_checkpoints
  ADD CONSTRAINT market_collection_checkpoints_payload_check CHECK (
    jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536
    AND payload ?& ARRAY['receipt','items']
    AND (payload - ARRAY['receipt','items'])='{}'::jsonb
    AND jsonb_typeof(payload->'receipt')='object'
    AND jsonb_typeof(payload->'items')='array' AND jsonb_array_length(payload->'items')<=50
  );

CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,p_phase TEXT,p_market_date DATE,p_policy_version INT,
  p_reservation_plan JSONB,p_request_window JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_existing public.market_intelligence_runs%ROWTYPE; v_result JSONB; v_entries JSONB;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_request_window)<>'object'
     OR NOT (p_request_window ?& ARRAY['start','end','timezone','market_date','phase'])
     OR (p_request_window-ARRAY['start','end','timezone','market_date','phase'])<>'{}'::jsonb
     OR p_request_window->>'timezone'<>'America/Chicago' OR p_request_window->>'phase'<>p_phase
     OR p_request_window->>'market_date'<>p_market_date::text
     OR (p_request_window->>'start')::timestamptz >= (p_request_window->>'end')::timestamptz THEN
    RAISE EXCEPTION 'invalid intelligence request window' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('market-intelligence-run:'||p_run_id::text,0));
  SELECT * INTO v_existing FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF FOUND AND v_existing.request_window IS NOT NULL AND v_existing.request_window IS DISTINCT FROM p_request_window THEN
    RAISE EXCEPTION 'intelligence run window mismatch' USING ERRCODE='22023';
  END IF;
  v_result:=public.start_market_intelligence_run(p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan);
  UPDATE public.market_intelligence_runs SET request_window=COALESCE(request_window,p_request_window)
    WHERE id=p_run_id RETURNING request_window INTO p_request_window;
  SELECT COALESCE(jsonb_agg(payload||jsonb_build_object('cache_key',cache_key) ORDER BY created_at),'[]'::jsonb)
    INTO v_entries FROM public.market_collection_checkpoints
    WHERE run_id=p_run_id AND (payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp();
  RETURN jsonb_build_object('run_id',p_run_id,'reservation_ids',v_result->'reservation_ids',
    'cache_entries',v_entries,'request_window',p_request_window,'duplicate',(v_result->>'duplicate')::boolean);
END; $$;

CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(
  p_run_id UUID,p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_run public.market_intelligence_runs%ROWTYPE; v_receipt JSONB; v_key TEXT;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR NOT (p_payload ?& ARRAY['cache_key','receipt','items'])
     OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb
     OR char_length(p_payload->>'cache_key') NOT BETWEEN 1 AND 512
     OR jsonb_typeof(p_payload->'receipt')<>'object' OR jsonb_typeof(p_payload->'items')<>'array'
     OR jsonb_array_length(p_payload->'items')>50 OR octet_length(p_payload::text)>65536 THEN
    RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_run FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS (SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  v_receipt:=p_payload->'receipt'; v_key:=p_payload->>'cache_key';
  IF v_receipt->>'status'<>'succeeded' OR (v_receipt->>'request_cost')::int<1
     OR (v_receipt->>'source_receipt_id') !~ '^[0-9a-f-]{36}$'
     OR (v_receipt->>'expires_at')::timestamptz <= (v_receipt->>'retrieved_at')::timestamptz THEN
    RAISE EXCEPTION 'checkpoint must contain an actual successful request' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_collection_checkpoints(run_id,cache_key,request_window,source_receipt_id,payload)
  VALUES(p_run_id,v_key,v_run.request_window,(v_receipt->>'source_receipt_id')::uuid,p_payload- 'cache_key')
  ON CONFLICT(run_id,cache_key) DO UPDATE SET payload=EXCLUDED.payload
    WHERE public.market_collection_checkpoints.payload IS NOT DISTINCT FROM EXCLUDED.payload;
  IF NOT FOUND AND EXISTS(SELECT 1 FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND cache_key=v_key) THEN
    RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023';
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',v_key);
END; $$;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(p_run_id UUID,p_completion_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result_row JSONB;
BEGIN
  IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs run
      JOIN public.market_intelligence_run_events event ON event.run_id=run.id AND event.status='started'
      WHERE run.id=p_run_id) THEN RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.market_collection_checkpoints checkpoint
      WHERE checkpoint.run_id=p_run_id AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(p_payload->'receipts') receipt
        WHERE receipt->>'id'=checkpoint.source_receipt_id::text)) THEN
    RAISE EXCEPTION 'final packet omits collection checkpoint' USING ERRCODE='22023';
  END IF;
  result_row:=public.record_market_intelligence_provider_v2(p_run_id,p_completion_id,p_payload);
  INSERT INTO public.market_run_source_item_provenance(run_item_id,run_id,source_item_id,source_receipt_id,provider,request_url,retrieved_at,reporting_at,entity_ids,security_ids,discovery_status)
  SELECT (value->>'run_item_id')::uuid,p_run_id,(value->>'id')::uuid,(value->>'receipt_id')::uuid,value->>'provider',value->>'request_url',(value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,value->'entity_ids',value->'security_ids',value->>'discovery_status' FROM jsonb_array_elements(p_payload->'items') ON CONFLICT(run_item_id) DO NOTHING;
  RETURN result_row;
END; $$;

REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;

-- Consolidated from sql/migrations/20260919_controller_lineage_and_run_binding.sql
-- Final controller override: bind collection to an existing running analysis run,
-- retain replaced checkpoints, and make cache-hit predecessor provenance durable.
CREATE TABLE IF NOT EXISTS public.market_collection_checkpoint_history (
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  cache_key TEXT NOT NULL, source_receipt_id UUID NOT NULL,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536),
  replaced_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY(run_id,cache_key,source_receipt_id)
);
ALTER TABLE public.market_collection_checkpoint_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_collection_checkpoint_history FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,p_phase TEXT,p_market_date DATE,p_policy_version INT,p_reservation_plan JSONB,p_request_window JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_window JSONB; v_entries JSONB;
BEGIN
  PERFORM 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'analysis run is not running' USING ERRCODE='22023'; END IF;
  IF jsonb_typeof(p_request_window)<>'object' OR p_request_window->>'timezone'<>'America/Chicago' OR p_request_window->>'phase'<>p_phase OR p_request_window->>'market_date'<>p_market_date::text OR (p_request_window->>'start')::timestamptz >= (p_request_window->>'end')::timestamptz THEN RAISE EXCEPTION 'invalid intelligence request window' USING ERRCODE='22023'; END IF;
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NOT NULL AND v_window IS DISTINCT FROM p_request_window THEN RAISE EXCEPTION 'intelligence run window mismatch' USING ERRCODE='22023'; END IF;
  v_result:=public.start_market_intelligence_run(p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan);
  UPDATE public.market_intelligence_runs SET request_window=COALESCE(request_window,p_request_window) WHERE id=p_run_id RETURNING request_window INTO v_window;
  SELECT COALESCE(jsonb_agg(payload||jsonb_build_object('cache_key',cache_key) ORDER BY created_at),'[]'::jsonb) INTO v_entries FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND (payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp();
  RETURN jsonb_build_object('run_id',p_run_id,'reservation_ids',v_result->'reservation_ids','cache_entries',v_entries,'request_window',v_window,'duplicate',(v_result->>'duplicate')::boolean);
END; $$;

CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(p_run_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_window JSONB; v_existing public.market_collection_checkpoints%ROWTYPE; v_receipt JSONB;
BEGIN
  IF jsonb_typeof(p_payload)<>'object' OR NOT(p_payload ?& ARRAY['cache_key','receipt','items']) OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb OR jsonb_typeof(p_payload->'items')<>'array' OR jsonb_array_length(p_payload->'items')>50 THEN RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023'; END IF;
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running') THEN RAISE EXCEPTION 'analysis run is not running' USING ERRCODE='22023'; END IF;
  v_receipt:=p_payload->'receipt';
  IF (v_receipt->>'request_cost')::int<1 OR (v_receipt->>'source_receipt_id') !~ '^[0-9a-f-]{36}$' THEN RAISE EXCEPTION 'checkpoint must record an actual attempt' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_existing FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key' FOR UPDATE;
  IF FOUND AND v_existing.payload IS DISTINCT FROM p_payload-'cache_key' THEN
    IF (v_existing.payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp() THEN RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023'; END IF;
    INSERT INTO public.market_collection_checkpoint_history(run_id,cache_key,source_receipt_id,payload) VALUES(v_existing.run_id,v_existing.cache_key,v_existing.source_receipt_id,v_existing.payload) ON CONFLICT DO NOTHING;
    UPDATE public.market_collection_checkpoints SET source_receipt_id=(v_receipt->>'source_receipt_id')::uuid,payload=p_payload-'cache_key',created_at=statement_timestamp() WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
  ELSIF NOT FOUND THEN
    INSERT INTO public.market_collection_checkpoints(run_id,cache_key,request_window,source_receipt_id,payload) VALUES(p_run_id,p_payload->>'cache_key',v_window,(v_receipt->>'source_receipt_id')::uuid,p_payload-'cache_key');
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
END; $$;
REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;

-- Consolidated from sql/migrations/20260920_terminal_checkpoint_lineage.sql
-- Terminal cache-hit lineage resolves against durable checkpoints, not a source-receipt FK
-- that cannot exist before the resumed terminal transaction.
CREATE TABLE IF NOT EXISTS public.market_checkpoint_receipt_lineage (
  cache_receipt_id UUID PRIMARY KEY REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  cache_predecessor_receipt_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
    AND table_name='market_checkpoint_receipt_lineage' AND column_name='checkpoint_receipt_id') THEN
    ALTER TABLE public.market_checkpoint_receipt_lineage
      RENAME COLUMN checkpoint_receipt_id TO cache_predecessor_receipt_id;
  END IF;
END; $$;
ALTER TABLE public.market_checkpoint_receipt_lineage ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_checkpoint_receipt_lineage FROM PUBLIC,anon,authenticated;

-- The cache receipt is inserted by the terminal recorder before its durable
-- checkpoint lineage row can exist.  Keep the receipt table honest about cost
-- and let the controller below validate predecessor provenance atomically.
ALTER TABLE public.market_source_receipts
  DROP CONSTRAINT IF EXISTS market_source_receipts_cache_lineage_check;
ALTER TABLE public.market_source_receipts
  ADD CONSTRAINT market_source_receipts_cache_lineage_check CHECK (
    (status = 'cache_hit' AND request_cost = 0)
    OR (status <> 'cache_hit' AND cache_predecessor_receipt_id IS NULL)
  );

-- Final controller guard: checkpoint failures are durable actual attempts too.
-- A run can only be started for a currently-running analysis row; checkpointing
-- also requires the corresponding intelligence event stream to remain open.
CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,p_phase TEXT,p_market_date DATE,p_policy_version INT,p_reservation_plan JSONB,p_request_window JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_window JSONB; v_entries JSONB;
BEGIN
  PERFORM 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'analysis run is not running' USING ERRCODE='22023'; END IF;
  IF jsonb_typeof(p_request_window)<>'object' OR p_request_window->>'timezone'<>'America/Chicago'
     OR p_request_window->>'phase'<>p_phase OR p_request_window->>'market_date'<>p_market_date::text
     OR (p_request_window->>'start')::timestamptz >= (p_request_window->>'end')::timestamptz THEN
    RAISE EXCEPTION 'invalid intelligence request window' USING ERRCODE='22023'; END IF;
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NOT NULL AND v_window IS DISTINCT FROM p_request_window THEN
    RAISE EXCEPTION 'intelligence run window mismatch' USING ERRCODE='22023'; END IF;
  v_result:=public.start_market_intelligence_run(p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan);
  UPDATE public.market_intelligence_runs SET request_window=COALESCE(request_window,p_request_window)
    WHERE id=p_run_id RETURNING request_window INTO v_window;
  SELECT COALESCE(jsonb_agg(payload||jsonb_build_object('cache_key',cache_key) ORDER BY created_at),'[]'::jsonb)
    INTO v_entries FROM public.market_collection_checkpoints
    WHERE run_id=p_run_id AND ((payload->'receipt'->>'status')='failed'
      OR (payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp());
  RETURN jsonb_build_object('run_id',p_run_id,'reservation_ids',v_result->'reservation_ids',
    'cache_entries',v_entries,'request_window',v_window,'duplicate',(v_result->>'duplicate')::boolean);
END; $$;

CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(p_run_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_window JSONB; v_existing public.market_collection_checkpoints%ROWTYPE; v_receipt JSONB;
BEGIN
  IF jsonb_typeof(p_payload)<>'object' OR NOT(p_payload ?& ARRAY['cache_key','receipt','items'])
     OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'items')<>'array' OR jsonb_array_length(p_payload->'items')>50 THEN
    RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023'; END IF;
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  v_receipt:=p_payload->'receipt';
  IF v_receipt->>'status' NOT IN ('succeeded','failed') OR (v_receipt->>'request_cost')::int<1
     OR (v_receipt->>'source_receipt_id') !~ '^[0-9a-f-]{36}$'
     OR ((v_receipt->>'status')='succeeded' AND (v_receipt->>'expires_at')::timestamptz <= (v_receipt->>'retrieved_at')::timestamptz)
     OR ((v_receipt->>'status')='failed' AND COALESCE(v_receipt->>'error_code','')='') THEN
    RAISE EXCEPTION 'checkpoint must record an actual request outcome' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_existing FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key' FOR UPDATE;
  IF FOUND AND v_existing.payload IS DISTINCT FROM p_payload-'cache_key' THEN
    IF (v_existing.payload->'receipt'->>'status')='failed' OR (v_existing.payload->'receipt'->>'expires_at')::timestamptz<=statement_timestamp() THEN
      INSERT INTO public.market_collection_checkpoint_history(run_id,cache_key,source_receipt_id,payload)
        VALUES(v_existing.run_id,v_existing.cache_key,v_existing.source_receipt_id,v_existing.payload) ON CONFLICT DO NOTHING;
      UPDATE public.market_collection_checkpoints SET source_receipt_id=(v_receipt->>'source_receipt_id')::uuid,
        payload=p_payload-'cache_key',created_at=statement_timestamp() WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
    ELSE RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023'; END IF;
  ELSIF NOT FOUND THEN
    INSERT INTO public.market_collection_checkpoints(run_id,cache_key,request_window,source_receipt_id,payload)
      VALUES(p_run_id,p_payload->>'cache_key',v_window,(v_receipt->>'source_receipt_id')::uuid,p_payload-'cache_key');
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
END; $$;
REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(p_run_id UUID,p_completion_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_effective JSONB; result_row JSONB; v_receipt JSONB;
BEGIN
  -- The provider recorder owns exact completion-idempotency.  On a response
  -- loss it returns the stored packet receipt before the run-state guard.
  SELECT jsonb_set(p_payload,'{receipts}',COALESCE(jsonb_agg(CASE WHEN value->>'status'='cache_hit' THEN jsonb_set(value,'{cache_predecessor_receipt_id}','null'::jsonb) ELSE value END),'[]'::jsonb)) INTO v_effective FROM jsonb_array_elements(p_payload->'receipts');
  IF EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE id=p_completion_id) THEN
    RETURN public.record_market_intelligence_provider_v2(p_run_id,p_completion_id,v_effective);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.market_collection_checkpoints checkpoint WHERE checkpoint.run_id=p_run_id AND NOT EXISTS(
      SELECT 1 FROM jsonb_array_elements(p_payload->'receipts') receipt
      WHERE receipt->>'id'=checkpoint.source_receipt_id::text
         OR (receipt->>'status'='cache_hit' AND receipt->>'cache_predecessor_receipt_id'=checkpoint.source_receipt_id::text))) THEN
    RAISE EXCEPTION 'final packet omits collection checkpoint' USING ERRCODE='22023'; END IF;
  FOR v_receipt IN SELECT value FROM jsonb_array_elements(p_payload->'receipts') LOOP
    IF v_receipt->>'status'='cache_hit' AND NOT EXISTS(
      SELECT 1 FROM public.market_collection_checkpoints checkpoint WHERE checkpoint.run_id=p_run_id AND checkpoint.source_receipt_id::text=v_receipt->>'cache_predecessor_receipt_id'
        AND checkpoint.payload->'receipt'->>'provider'=v_receipt->>'provider'
        AND checkpoint.payload->'receipt'->>'cache_key'=v_receipt->>'cache_key'
        AND checkpoint.payload->'receipt'->'requested_window'=v_receipt->'requested_window'
        AND checkpoint.payload->'receipt'->>'response_hash'=v_receipt->>'response_hash'
      UNION ALL SELECT 1 FROM public.market_collection_checkpoint_history history WHERE history.run_id=p_run_id AND history.source_receipt_id::text=v_receipt->>'cache_predecessor_receipt_id'
        AND history.payload->'receipt'->>'provider'=v_receipt->>'provider'
        AND history.payload->'receipt'->>'cache_key'=v_receipt->>'cache_key'
        AND history.payload->'receipt'->'requested_window'=v_receipt->'requested_window'
        AND history.payload->'receipt'->>'response_hash'=v_receipt->>'response_hash') THEN
      RAISE EXCEPTION 'cache predecessor checkpoint unavailable' USING ERRCODE='22023'; END IF;
  END LOOP;
  result_row:=public.record_market_intelligence_provider_v2(p_run_id,p_completion_id,v_effective);
  INSERT INTO public.market_run_source_item_provenance(run_item_id,run_id,source_item_id,source_receipt_id,provider,request_url,retrieved_at,reporting_at,entity_ids,security_ids,discovery_status)
  SELECT (value->>'run_item_id')::uuid,p_run_id,(value->>'id')::uuid,(value->>'receipt_id')::uuid,value->>'provider',value->>'request_url',(value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,value->'entity_ids',value->'security_ids',value->>'discovery_status'
  FROM jsonb_array_elements(p_payload->'items') value ON CONFLICT(run_item_id) DO NOTHING;
  INSERT INTO public.market_checkpoint_receipt_lineage(cache_receipt_id,run_id,cache_predecessor_receipt_id)
  SELECT (value->>'id')::uuid,p_run_id,(value->>'cache_predecessor_receipt_id')::uuid FROM jsonb_array_elements(p_payload->'receipts') value WHERE value->>'status'='cache_hit' ON CONFLICT(cache_receipt_id) DO NOTHING;
  RETURN result_row;
END; $$;
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID,UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID,UUID,JSONB) TO service_role;

-- Consolidated from sql/migrations/20260921_intelligence_context_inputs.sql
-- Server-owned, bounded context used by the protected intelligence collector.
CREATE TABLE IF NOT EXISTS public.market_intelligence_context_inputs (
  run_id UUID PRIMARY KEY REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  holding_market_values JSONB NOT NULL CHECK (jsonb_typeof(holding_market_values)='object' AND octet_length(holding_market_values::text)<=16384),
  liquidity_by_ticker JSONB NOT NULL CHECK (jsonb_typeof(liquidity_by_ticker)='object' AND octet_length(liquidity_by_ticker::text)<=16384),
  overlap_by_ticker JSONB NOT NULL CHECK (jsonb_typeof(overlap_by_ticker)='object' AND octet_length(overlap_by_ticker::text)<=16384),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.market_intelligence_context_inputs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_intelligence_context_inputs FROM PUBLIC,anon,authenticated;

-- Consolidated from sql/migrations/20260922_collection_controller_contract.sql
-- Consolidated collection controller. Explicit definitions supersede historical
-- rename shims, so fresh and additive installs have the same nonrecursive chain.

-- The legacy fresh schema declares the outbox before the report ledger. Keep
-- its deferred FK and the remaining original gateway grants in both installs.
DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint
      WHERE conrelid='public.market_report_publications'::regclass AND conname='market_report_publications_report_id_fkey') THEN
    ALTER TABLE public.market_report_publications ADD CONSTRAINT market_report_publications_report_id_fkey
      FOREIGN KEY (report_id) REFERENCES public.market_reports(id) ON DELETE RESTRICT;
  END IF;
END; $$;
GRANT EXECUTE ON FUNCTION public.read_market_evidence_packet(UUID, UUID) TO service_role;
-- Report-decision reader and its grant are created together in 20261001.
GRANT EXECUTE ON FUNCTION public.record_market_report(UUID, TEXT, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_learning(UUID, JSONB) TO service_role;

CREATE OR REPLACE FUNCTION public.record_market_intelligence_legacy(
  p_run_id UUID,
  p_completion_id UUID,
  p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_run public.market_intelligence_runs%ROWTYPE;
  v_existing public.market_intelligence_run_events%ROWTYPE;
  v_row JSONB;
  v_receipt public.market_source_receipts%ROWTYPE;
  v_reservation public.market_source_quota_reservations%ROWTYPE;
  v_packet JSONB;
  v_candidate JSONB;
  v_evidence_id JSONB;
  v_source_host TEXT;
  v_payload_hash TEXT;
  v_receipt_json JSONB;
  v_item_count INT := 0;
  v_receipt_count INT := 0;
  v_event_count INT := 0;
  v_relationship_count INT := 0;
  v_ranking_count INT := 0;
BEGIN
  IF p_run_id IS NULL OR p_completion_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR octet_length(p_payload::text) > 1048576
     OR NOT (p_payload ?& ARRAY[
       'status','coverage','receipts','items','events','relationships','rankings','packet','error'
     ])
     OR (p_payload - ARRAY[
       'status','coverage','receipts','items','events','relationships','rankings','packet','error'
     ]) <> '{}'::jsonb
     OR p_payload->>'status' NOT IN ('completed','failed')
     OR jsonb_typeof(p_payload->'coverage')<>'object'
     OR octet_length((p_payload->'coverage')::text)>32768
     OR jsonb_typeof(p_payload->'receipts')<>'array'
     OR jsonb_array_length(p_payload->'receipts')>100
     OR jsonb_typeof(p_payload->'items')<>'array'
     OR jsonb_array_length(p_payload->'items')>500
     OR jsonb_typeof(p_payload->'events')<>'array'
     OR jsonb_array_length(p_payload->'events')>100
     OR jsonb_typeof(p_payload->'relationships')<>'array'
     OR jsonb_array_length(p_payload->'relationships')>500
     OR jsonb_typeof(p_payload->'rankings')<>'array'
     OR jsonb_array_length(p_payload->'rankings')>100
     OR (p_payload->>'status'='completed' AND jsonb_typeof(p_payload->'packet')<>'object')
     OR (p_payload->>'status'='completed' AND p_payload->'error'<>'null'::jsonb)
     OR (p_payload->>'status'='failed' AND p_payload->'packet'<>'null'::jsonb)
     OR (p_payload->>'status'='failed' AND jsonb_typeof(p_payload->'error')<>'object')
     OR (p_payload->>'status'='failed' AND octet_length((p_payload->'error')::text)>4096) THEN
    RAISE EXCEPTION 'invalid intelligence completion payload' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-completion:' || p_completion_id::text, 0
  ));
  v_payload_hash := encode(extensions.digest(p_payload::text,'sha256'),'hex');
  SELECT * INTO v_existing FROM public.market_intelligence_run_events WHERE id=p_completion_id;
  IF FOUND THEN
    IF v_existing.run_id IS DISTINCT FROM p_run_id
       OR v_existing.status IS DISTINCT FROM p_payload->>'status'
       OR v_existing.detail->>'payload_hash' IS DISTINCT FROM v_payload_hash THEN
      RAISE EXCEPTION 'intelligence completion idempotency mismatch' USING ERRCODE = '22023';
    END IF;
    RETURN (v_existing.detail->'receipt') || jsonb_build_object('duplicate',true);
  END IF;
  SELECT * INTO v_run FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'intelligence run unavailable' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.market_intelligence_run_events
    WHERE run_id=p_run_id AND status IN ('completed','failed')
  ) THEN
    RAISE EXCEPTION 'intelligence run already terminal' USING ERRCODE = '22023';
  END IF;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'receipts') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','reservation_id','status','cache_key','requested_window','retrieved_at','expires_at',
         'request_cost','upstream_remaining','returned_count','accepted_count','duplicate_count',
         'dropped_count','error','response_hash'
       ])
       OR (v_row - ARRAY[
         'id','reservation_id','status','cache_key','requested_window','retrieved_at','expires_at',
         'request_cost','upstream_remaining','returned_count','accepted_count','duplicate_count',
         'dropped_count','error','response_hash'
       ]) <> '{}'::jsonb
       OR v_row->>'status' NOT IN (
         'succeeded','failed','cache_hit','quota_blocked','configuration_missing'
       )
       OR char_length(v_row->>'cache_key') NOT BETWEEN 1 AND 512
       OR jsonb_typeof(v_row->'requested_window')<>'object'
       OR octet_length((v_row->'requested_window')::text)>8192
       OR (v_row->>'request_cost') !~ '^[0-9]+$'
       OR (v_row->>'request_cost')::int NOT BETWEEN 0 AND 100
       OR (v_row->>'returned_count') !~ '^[0-9]+$'
       OR (v_row->>'accepted_count') !~ '^[0-9]+$'
       OR (v_row->>'duplicate_count') !~ '^[0-9]+$'
       OR (v_row->>'dropped_count') !~ '^[0-9]+$'
       OR (v_row->>'status' IN ('succeeded','cache_hit')
         AND COALESCE(v_row->>'response_hash','') !~ '^[0-9a-f]{64}$')
       OR (v_row->>'status' NOT IN ('succeeded','cache_hit')
         AND v_row->'response_hash'<>'null'::jsonb
         AND v_row->>'response_hash' !~ '^[0-9a-f]{64}$')
       OR (v_row->>'status' NOT IN ('succeeded','cache_hit') AND v_row->'expires_at'<>'null'::jsonb)
       OR (v_row->>'status' IN ('succeeded','cache_hit') AND v_row->'expires_at'='null'::jsonb)
       OR (v_row->'error'<>'null'::jsonb AND (
         jsonb_typeof(v_row->'error')<>'object' OR octet_length((v_row->'error')::text)>4096
       )) THEN
      RAISE EXCEPTION 'invalid market source receipt' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_reservation FROM public.market_source_quota_reservations
    WHERE id=(v_row->>'reservation_id')::uuid AND run_id=p_run_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE = '22023';
    END IF;
    IF (v_row->>'request_cost')::int > v_reservation.reserved_requests THEN
      RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE = '54000';
    END IF;
    INSERT INTO public.market_source_receipts(
      id,run_id,reservation_id,provider,status,cache_key,requested_window,retrieved_at,expires_at,
      request_cost,upstream_remaining,returned_count,accepted_count,duplicate_count,dropped_count,
      error,response_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,v_reservation.id,v_reservation.provider,v_row->>'status',
      v_row->>'cache_key',v_row->'requested_window',(v_row->>'retrieved_at')::timestamptz,
      (v_row->>'expires_at')::timestamptz,(v_row->>'request_cost')::int,
      (v_row->>'upstream_remaining')::int,(v_row->>'returned_count')::int,
      (v_row->>'accepted_count')::int,(v_row->>'duplicate_count')::int,
      (v_row->>'dropped_count')::int,NULLIF(v_row->'error','null'::jsonb),v_row->>'response_hash'
    );
    v_receipt_count := v_receipt_count + 1;
  END LOOP;
  IF EXISTS (
    SELECT 1
    FROM public.market_source_quota_reservations reservation
    JOIN (
      SELECT (value->>'reservation_id')::uuid AS reservation_id,
             sum((value->>'request_cost')::int) AS used
      FROM jsonb_array_elements(p_payload->'receipts') GROUP BY 1
    ) usage ON usage.reservation_id=reservation.id
    WHERE reservation.run_id=p_run_id AND usage.used>reservation.reserved_requests
  ) THEN
    RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE = '54000';
  END IF;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'items') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','run_item_id','receipt_id','upstream_item_id','canonical_url','published_at',
         'effective_at','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ])
       OR (v_row - ARRAY[
         'id','run_item_id','receipt_id','upstream_item_id','canonical_url','published_at',
         'effective_at','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ]) <> '{}'::jsonb
       OR char_length(v_row->>'title') NOT BETWEEN 1 AND 500
       OR char_length(v_row->>'normalized_text') > 2000
       OR char_length(v_row->>'canonical_content') > 4096
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(
         extensions.digest(convert_to(v_row->>'canonical_content','UTF8'),'sha256'),'hex'
       )
       OR jsonb_typeof(v_row->'metadata')<>'object'
       OR octet_length((v_row->'metadata')::text)>8192
       OR v_row->>'disposition' NOT IN ('accepted','duplicate','near_duplicate','dropped')
       OR (v_row->>'disposition'='accepted' AND v_row->'drop_reason'<>'null'::jsonb)
       OR (v_row->>'disposition'<>'accepted' AND (
         v_row->'drop_reason'='null'::jsonb OR char_length(v_row->>'drop_reason')>200
       ))
       OR (v_row->'canonical_url'<>'null'::jsonb AND (
         char_length(v_row->>'canonical_url')>2048 OR v_row->>'canonical_url' !~ '^https://'
       )) THEN
      RAISE EXCEPTION 'invalid market source item or content hash' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_receipt FROM public.market_source_receipts
    WHERE id=(v_row->>'receipt_id')::uuid AND run_id=p_run_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'source item receipt unavailable' USING ERRCODE = '22023';
    END IF;
    IF v_row->>'disposition'='accepted'
       AND v_receipt.status NOT IN ('succeeded','cache_hit') THEN
      RAISE EXCEPTION 'accepted source item requires successful receipt' USING ERRCODE = '22023';
    END IF;
    IF v_row->'canonical_url'<>'null'::jsonb THEN
      v_source_host := lower(substring(v_row->>'canonical_url' FROM '^https://([^/:?#]+)'));
      IF NOT (CASE v_receipt.provider
        WHEN 'gdelt' THEN v_source_host='api.gdeltproject.org'
        WHEN 'alpha_vantage' THEN v_source_host='www.alphavantage.co'
        WHEN 'finnhub' THEN v_source_host='finnhub.io'
        WHEN 'yahoo' THEN v_source_host='query1.finance.yahoo.com'
        WHEN 'sec_edgar' THEN v_source_host IN ('www.sec.gov','data.sec.gov')
        WHEN 'federal_register' THEN v_source_host='www.federalregister.gov'
        WHEN 'white_house' THEN v_source_host='www.whitehouse.gov'
        WHEN 'doe' THEN v_source_host='www.energy.gov'
        WHEN 'dod' THEN v_source_host='www.defense.gov'
        WHEN 'eia' THEN v_source_host IN ('api.eia.gov','www.eia.gov')
        WHEN 'fred' THEN v_source_host IN ('api.stlouisfed.org','fred.stlouisfed.org')
        WHEN 'bls' THEN v_source_host IN ('api.bls.gov','www.bls.gov')
        WHEN 'bea' THEN v_source_host IN ('apps.bea.gov','www.bea.gov')
        WHEN 'social' THEN v_source_host IN ('www.reddit.com','oauth.reddit.com')
        ELSE false END) THEN
        RAISE EXCEPTION 'source URL host mismatch' USING ERRCODE = '22023';
      END IF;
    END IF;
    INSERT INTO public.market_source_items(
      id,source_receipt_id,provider,upstream_item_id,canonical_url,published_at,effective_at,title,
      normalized_text,canonical_content,content_hash,metadata
    ) VALUES (
      (v_row->>'id')::uuid,v_receipt.id,v_receipt.provider,v_row->>'upstream_item_id',
      v_row->>'canonical_url',(v_row->>'published_at')::timestamptz,
      (v_row->>'effective_at')::timestamptz,v_row->>'title',v_row->>'normalized_text',
      v_row->>'canonical_content',v_row->>'content_hash',v_row->'metadata'
    );
    INSERT INTO public.market_intelligence_run_items(
      id,run_id,source_item_id,source_receipt_id,disposition,drop_reason
    ) VALUES (
      (v_row->>'run_item_id')::uuid,p_run_id,(v_row->>'id')::uuid,v_receipt.id,
      v_row->>'disposition',v_row->>'drop_reason'
    );
    v_item_count := v_item_count + 1;
  END LOOP;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'events') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','event_type','title','summary','occurred_at','effective_at','materiality',
         'confidence','evidence_item_ids','content_hash'
       ])
       OR (v_row - ARRAY[
         'id','event_type','title','summary','occurred_at','effective_at','materiality',
         'confidence','evidence_item_ids','content_hash'
       ]) <> '{}'::jsonb
       OR char_length(v_row->>'event_type') NOT BETWEEN 1 AND 80
       OR char_length(v_row->>'title') NOT BETWEEN 1 AND 500
       OR char_length(v_row->>'summary')>4000
       OR (v_row->>'materiality')::numeric NOT BETWEEN 0 AND 1
       OR (v_row->>'confidence')::numeric NOT BETWEEN 0 AND 1
       OR jsonb_typeof(v_row->'evidence_item_ids')<>'array'
       OR jsonb_array_length(v_row->'evidence_item_ids') NOT BETWEEN 1 AND 96
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_row - ARRAY['id','content_hash']),'UTF8'
       ),'sha256'),'hex') THEN
      RAISE EXCEPTION 'invalid market event' USING ERRCODE = '22023';
    END IF;
    FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_row->'evidence_item_ids') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1
        FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND run_item.disposition='accepted' AND receipt.run_id=p_run_id
          AND receipt.status IN ('succeeded','cache_hit')
      ) THEN
        RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023';
      END IF;
    END LOOP;
    INSERT INTO public.market_events(
      id,run_id,event_type,title,summary,occurred_at,effective_at,materiality,confidence,
      evidence_item_ids,content_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,v_row->>'event_type',v_row->>'title',v_row->>'summary',
      (v_row->>'occurred_at')::timestamptz,(v_row->>'effective_at')::timestamptz,
      (v_row->>'materiality')::numeric,(v_row->>'confidence')::numeric,
      v_row->'evidence_item_ids',v_row->>'content_hash'
    );
    v_event_count := v_event_count + 1;
  END LOOP;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'relationships') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','event_id','source_kind','source_key','target_kind','target_key',
         'relationship_type','hypothesis','evidence_item_ids','content_hash'
       ])
       OR (v_row - ARRAY[
         'id','event_id','source_kind','source_key','target_kind','target_key',
         'relationship_type','hypothesis','evidence_item_ids','content_hash'
       ]) <> '{}'::jsonb
       OR v_row->>'source_kind' NOT IN ('event','theme','value_chain','entity','security')
       OR v_row->>'target_kind' NOT IN ('theme','value_chain','entity','security','etf')
       OR char_length(v_row->>'source_key') NOT BETWEEN 1 AND 256
       OR char_length(v_row->>'target_key') NOT BETWEEN 1 AND 256
       OR char_length(v_row->>'relationship_type') NOT BETWEEN 1 AND 80
       OR jsonb_typeof(v_row->'hypothesis')<>'boolean'
       OR jsonb_typeof(v_row->'evidence_item_ids')<>'array'
       OR jsonb_array_length(v_row->'evidence_item_ids') NOT BETWEEN 1 AND 8
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_row - ARRAY['id','content_hash']),'UTF8'
       ),'sha256'),'hex')
       OR NOT EXISTS (
         SELECT 1 FROM public.market_events
         WHERE id=(v_row->>'event_id')::uuid AND run_id=p_run_id
       ) THEN
      RAISE EXCEPTION 'invalid market event relationship' USING ERRCODE = '22023';
    END IF;
    FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_row->'evidence_item_ids') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1 FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
      ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
    END LOOP;
    INSERT INTO public.market_event_relationships(
      id,run_id,event_id,source_kind,source_key,target_kind,target_key,relationship_type,
      hypothesis,evidence_item_ids,content_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,(v_row->>'event_id')::uuid,v_row->>'source_kind',
      v_row->>'source_key',v_row->>'target_kind',v_row->>'target_key',
      v_row->>'relationship_type',(v_row->>'hypothesis')::boolean,
      v_row->'evidence_item_ids',v_row->>'content_hash'
    );
    v_relationship_count := v_relationship_count + 1;
  END LOOP;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'rankings') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','event_id','candidate_key','ticker','rank','component_scores','total_score',
         'qualified','veto_reasons','exposure_item_ids','content_hash'
       ])
       OR (v_row - ARRAY[
         'id','event_id','candidate_key','ticker','rank','component_scores','total_score',
         'qualified','veto_reasons','exposure_item_ids','content_hash'
       ]) <> '{}'::jsonb
       OR char_length(v_row->>'candidate_key') NOT BETWEEN 1 AND 256
       OR (v_row->'ticker'<>'null'::jsonb AND v_row->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$')
       OR (v_row->>'rank')::int NOT BETWEEN 1 AND 100
       OR jsonb_typeof(v_row->'component_scores')<>'object'
       OR octet_length((v_row->'component_scores')::text)>8192
       OR (v_row->>'total_score')::numeric NOT BETWEEN -100000 AND 100000
       OR jsonb_typeof(v_row->'qualified')<>'boolean'
       OR jsonb_typeof(v_row->'veto_reasons')<>'array'
       OR jsonb_array_length(v_row->'veto_reasons')>20
       OR jsonb_typeof(v_row->'exposure_item_ids')<>'array'
       OR jsonb_array_length(v_row->'exposure_item_ids')>8
       OR ((v_row->>'qualified')::boolean AND jsonb_array_length(v_row->'exposure_item_ids')=0)
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_row - ARRAY['id','content_hash']),'UTF8'
       ),'sha256'),'hex')
       OR (v_row->'event_id'<>'null'::jsonb AND NOT EXISTS (
         SELECT 1 FROM public.market_events
         WHERE id=(v_row->>'event_id')::uuid AND run_id=p_run_id
       )) THEN
      RAISE EXCEPTION 'invalid market candidate ranking' USING ERRCODE = '22023';
    END IF;
    FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_row->'exposure_item_ids') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1 FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
      ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
    END LOOP;
    INSERT INTO public.market_candidate_rankings(
      id,run_id,event_id,candidate_key,ticker,rank,component_scores,total_score,qualified,
      veto_reasons,exposure_item_ids,content_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,(v_row->>'event_id')::uuid,v_row->>'candidate_key',
      v_row->>'ticker',(v_row->>'rank')::int,v_row->'component_scores',
      (v_row->>'total_score')::numeric,(v_row->>'qualified')::boolean,
      v_row->'veto_reasons',v_row->'exposure_item_ids',v_row->>'content_hash'
    );
    v_ranking_count := v_ranking_count + 1;
  END LOOP;

  IF p_payload->>'status'='completed' THEN
    v_packet := p_payload->'packet';
    IF NOT (v_packet ?& ARRAY['id','candidate_count','evidence_count','packet','packet_hash'])
       OR (v_packet - ARRAY['id','candidate_count','evidence_count','packet','packet_hash']) <> '{}'::jsonb
       OR (v_packet->>'candidate_count')::int NOT BETWEEN 0 AND 12
       OR (v_packet->>'evidence_count')::int NOT BETWEEN 0 AND 96
       OR jsonb_typeof(v_packet->'packet')<>'object'
       OR octet_length((v_packet->'packet')::text)>98304
       OR v_packet->>'packet_hash' !~ '^[0-9a-f]{64}$'
       OR v_packet->>'packet_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_packet->'packet'),'UTF8'
       ),'sha256'),'hex')
       OR NOT (v_packet->'packet' ?& ARRAY[
         'candidates','evidence','coverage','limitations','policy_version'
       ])
       OR jsonb_typeof(v_packet->'packet'->'candidates')<>'array'
       OR jsonb_array_length(v_packet->'packet'->'candidates')<>(v_packet->>'candidate_count')::int
       OR jsonb_typeof(v_packet->'packet'->'evidence')<>'array'
       OR jsonb_array_length(v_packet->'packet'->'evidence')<>(v_packet->>'evidence_count')::int
       OR (v_packet->'packet'->>'policy_version')::int<>v_run.policy_version THEN
      RAISE EXCEPTION 'invalid or oversized evidence packet' USING ERRCODE = '22023';
    END IF;
    FOR v_candidate IN SELECT value FROM jsonb_array_elements(v_packet->'packet'->'candidates') LOOP
      IF jsonb_typeof(v_candidate)<>'object'
         OR jsonb_typeof(v_candidate->'evidence_ids')<>'array'
         OR jsonb_array_length(v_candidate->'evidence_ids')>8 THEN
        RAISE EXCEPTION 'evidence per candidate exceeds limit' USING ERRCODE = '22023';
      END IF;
    END LOOP;
    FOR v_evidence_id IN SELECT value->'item_id'
      FROM jsonb_array_elements(v_packet->'packet'->'evidence') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1 FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
      ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
    END LOOP;
    FOR v_candidate IN SELECT value FROM jsonb_array_elements(v_packet->'packet'->'candidates') LOOP
      FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_candidate->'evidence_ids') LOOP
        IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
          SELECT 1 FROM public.market_intelligence_run_items run_item
          JOIN public.market_source_items item ON item.id=run_item.source_item_id
          JOIN public.market_source_receipts receipt
            ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
          WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
            AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
            AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
        ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
      END LOOP;
    END LOOP;
    INSERT INTO public.market_evidence_packets(
      id,run_id,policy_version,status,candidate_count,evidence_count,packet,packet_hash
    ) VALUES (
      (v_packet->>'id')::uuid,p_run_id,v_run.policy_version,'completed',
      (v_packet->>'candidate_count')::int,(v_packet->>'evidence_count')::int,
      v_packet->'packet',v_packet->>'packet_hash'
    );
  END IF;

  v_receipt_json := jsonb_build_object(
    'run_id',p_run_id,
    'completion_id',p_completion_id,
    'status',p_payload->>'status',
    'counts',jsonb_build_object(
      'source_receipts',v_receipt_count,
      'source_items',v_item_count,
      'events',v_event_count,
      'relationships',v_relationship_count,
      'rankings',v_ranking_count,
      'packets',CASE WHEN p_payload->>'status'='completed' THEN 1 ELSE 0 END
    ),
    'packet_id',CASE WHEN p_payload->>'status'='completed' THEN v_packet->>'id' ELSE NULL END,
    'packet_hash',CASE WHEN p_payload->>'status'='completed' THEN v_packet->>'packet_hash' ELSE NULL END,
    'duplicate',false
  );
  INSERT INTO public.market_intelligence_run_events(id,run_id,status,detail)
  VALUES (
    p_completion_id,p_run_id,p_payload->>'status',jsonb_build_object(
      'payload_hash',v_payload_hash,
      'coverage',p_payload->'coverage',
      'error',p_payload->'error',
      'receipt',v_receipt_json
    )
  );
  RETURN v_receipt_json;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_intelligence_provider_v2(
  p_run_id UUID, p_completion_id UUID, p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_row JSONB;
  v_legacy_items JSONB;
  v_result JSONB;
  v_request_host TEXT;
  v_valid_request_host BOOLEAN;
BEGIN
  IF p_run_id IS NULL OR p_completion_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR jsonb_typeof(p_payload->'items')<>'array' THEN
    RAISE EXCEPTION 'invalid intelligence completion payload' USING ERRCODE='22023';
  END IF;
  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'items') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','run_item_id','receipt_id','provider','upstream_item_id','canonical_url','request_url',
         'published_at','retrieved_at','effective_at','reporting_at','entity_ids','security_ids',
         'discovery_status','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ])
       OR (v_row - ARRAY[
         'id','run_item_id','receipt_id','provider','upstream_item_id','canonical_url','request_url',
         'published_at','retrieved_at','effective_at','reporting_at','entity_ids','security_ids',
         'discovery_status','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ]) <> '{}'::jsonb
       OR COALESCE(char_length(v_row->>'upstream_item_id'),0) NOT BETWEEN 1 AND 512
       OR COALESCE(char_length(v_row->>'canonical_url'),0) NOT BETWEEN 1 AND 2048
       OR COALESCE(char_length(v_row->>'request_url'),0) NOT BETWEEN 1 AND 2048
       OR v_row->>'canonical_url' !~ '^https://' OR v_row->>'request_url' !~ '^https://'
       OR lower(v_row->>'request_url') ~ '(api[_-]?key|token|secret|password)='
       OR jsonb_typeof(v_row->'entity_ids')<>'array' OR jsonb_array_length(v_row->'entity_ids')>32
       OR jsonb_typeof(v_row->'security_ids')<>'array' OR jsonb_array_length(v_row->'security_ids')>32
       OR v_row->>'discovery_status' NOT IN ('qualified','no_event','insufficient_coverage')
       OR (v_row->>'discovery_status'='qualified' AND jsonb_array_length(v_row->'security_ids')=0) THEN
      RAISE EXCEPTION 'invalid provider evidence provenance' USING ERRCODE='22023';
    END IF;
    v_request_host := lower(substring(v_row->>'request_url' FROM '^https://([^/:?#]+)'));
    v_valid_request_host := CASE v_row->>'provider'
      WHEN 'gdelt' THEN v_request_host='api.gdeltproject.org'
      WHEN 'alpha_vantage' THEN v_request_host='www.alphavantage.co'
      WHEN 'finnhub' THEN v_request_host='finnhub.io'
      WHEN 'yahoo' THEN v_request_host='query1.finance.yahoo.com'
      WHEN 'sec_edgar' THEN v_request_host IN ('www.sec.gov','data.sec.gov')
      WHEN 'federal_register' THEN v_request_host='www.federalregister.gov'
      WHEN 'white_house' THEN v_request_host='www.whitehouse.gov'
      WHEN 'doe' THEN v_request_host='www.energy.gov'
      WHEN 'dod' THEN v_request_host='www.defense.gov'
      WHEN 'eia' THEN v_request_host IN ('api.eia.gov','www.eia.gov')
      WHEN 'fred' THEN v_request_host IN ('api.stlouisfed.org','fred.stlouisfed.org')
      WHEN 'bls' THEN v_request_host IN ('api.bls.gov','www.bls.gov')
      WHEN 'bea' THEN v_request_host IN ('apps.bea.gov','www.bea.gov')
      WHEN 'social' THEN v_request_host IN ('www.reddit.com','oauth.reddit.com')
      ELSE false END;
    IF NOT v_valid_request_host THEN
      RAISE EXCEPTION 'provider request URL host mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;

  SELECT jsonb_agg(
    ((value - ARRAY['provider','request_url','retrieved_at','reporting_at','entity_ids','security_ids','discovery_status'])
      || jsonb_build_object('canonical_url', value->'request_url') || CASE WHEN value->>'disposition'='near_duplicate' THEN jsonb_build_object('disposition','accepted','drop_reason',NULL) ELSE '{}'::jsonb END)
  ) INTO v_legacy_items FROM jsonb_array_elements(p_payload->'items');
  v_result := public.record_market_intelligence_legacy(
    p_run_id, p_completion_id, jsonb_set(p_payload, '{items}', COALESCE(v_legacy_items,'[]'::jsonb))
  );
  INSERT INTO public.market_source_item_provenance(
    source_item_id,provider,canonical_item_url,request_url,retrieved_at,reporting_at,
    entity_ids,security_ids,discovery_status
  )
  SELECT (value->>'id')::uuid,value->>'provider',value->>'canonical_url',value->>'request_url',
    (value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,
    value->'entity_ids',value->'security_ids',value->>'discovery_status'
  FROM jsonb_array_elements(p_payload->'items')
  ON CONFLICT (source_item_id) DO NOTHING;
  RETURN v_result;
END;
$$;

-- Only first initialization of the immutable session window is permitted.
CREATE OR REPLACE FUNCTION public.initialize_market_intelligence_window()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  IF TG_OP='UPDATE' AND OLD.request_window IS NULL AND NEW.request_window IS NOT NULL
     AND to_jsonb(OLD)-'request_window'=to_jsonb(NEW)-'request_window' THEN RETURN NEW; END IF;
  RAISE EXCEPTION 'market intelligence records are append-only' USING ERRCODE='55000';
END; $$;
DROP TRIGGER IF EXISTS market_intelligence_runs_append_only ON public.market_intelligence_runs;
CREATE TRIGGER market_intelligence_runs_append_only BEFORE UPDATE OR DELETE ON public.market_intelligence_runs
FOR EACH ROW EXECUTE FUNCTION public.initialize_market_intelligence_window();

CREATE TABLE IF NOT EXISTS public.market_intelligence_collection_completions (
  completion_id UUID PRIMARY KEY REFERENCES public.market_intelligence_run_events(id) ON DELETE RESTRICT,
  run_id UUID UNIQUE NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=1048576),
  receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.market_intelligence_collection_completions ENABLE ROW LEVEL SECURITY;
CREATE TRIGGER market_intelligence_collection_completions_append_only BEFORE UPDATE OR DELETE
ON public.market_intelligence_collection_completions FOR EACH ROW EXECUTE FUNCTION public.reject_market_intelligence_mutation();
REVOKE ALL ON public.market_intelligence_collection_completions FROM PUBLIC,anon,authenticated,service_role;
ALTER TABLE public.market_checkpoint_receipt_lineage ADD CONSTRAINT distinct_cache_predecessor CHECK(cache_receipt_id<>cache_predecessor_receipt_id);

CREATE OR REPLACE FUNCTION public.read_market_intelligence_completion(p_run_id UUID,p_completion_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_saved public.market_intelligence_collection_completions%ROWTYPE; v_providers JSONB;
BEGIN
  SELECT * INTO v_saved FROM public.market_intelligence_collection_completions
    WHERE run_id=p_run_id AND completion_id=p_completion_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT jsonb_object_agg(id::text,provider) INTO v_providers FROM public.market_source_quota_reservations WHERE run_id=p_run_id;
  RETURN jsonb_build_object('receipt',v_saved.receipt,'payload',v_saved.payload,'providers',v_providers);
END; $$;

CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,p_phase TEXT,p_market_date DATE,p_policy_version INT,p_reservation_plan JSONB,p_request_window JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_window JSONB; v_entries JSONB; v_usage JSONB;
BEGIN
  PERFORM 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'analysis run is not running' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.market_intelligence_quote_attempts WHERE run_id=p_run_id AND status='pending') THEN
    RAISE EXCEPTION 'quote collection outcome uncertain' USING ERRCODE='55000'; END IF;
  IF p_request_window IS NULL OR jsonb_typeof(p_request_window)<>'object'
     OR NOT(p_request_window ?& ARRAY['start','end','timezone','market_date','phase'])
     OR (p_request_window-ARRAY['start','end','timezone','market_date','phase'])<>'{}'::jsonb
     OR p_request_window->>'timezone'<>'America/Chicago' OR p_request_window->>'phase'<>p_phase
     OR p_request_window->>'market_date'<>p_market_date::text
     OR (p_request_window->>'start')::timestamptz >= (p_request_window->>'end')::timestamptz THEN
    RAISE EXCEPTION 'invalid intelligence request window' USING ERRCODE='22023'; END IF;
  v_result:=public.start_market_intelligence_run(p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan);
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL THEN
    UPDATE public.market_intelligence_runs SET request_window=p_request_window WHERE id=p_run_id RETURNING request_window INTO v_window;
  END IF;
  SELECT COALESCE(jsonb_agg(payload||jsonb_build_object('cache_key',cache_key) ORDER BY cache_key),'[]'::jsonb)
    INTO v_entries FROM public.market_collection_checkpoints WHERE run_id=p_run_id
      AND ((payload->'receipt'->>'status') IN ('failed','quota_blocked') OR (payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp());
  SELECT COALESCE(jsonb_object_agg(reservation_id,used),'{}'::jsonb) INTO v_usage FROM (
    SELECT payload->'receipt'->>'reservation_id' reservation_id,sum((payload->'receipt'->>'request_cost')::int) used
    FROM (SELECT payload FROM public.market_collection_checkpoints WHERE run_id=p_run_id
      UNION ALL SELECT payload FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id) paid GROUP BY 1
  ) usage;
  RETURN jsonb_build_object('run_id',p_run_id,'reservation_ids',v_result->'reservation_ids','cache_entries',v_entries,
    'request_window',v_window,'reservation_usage',v_usage,'duplicate',(v_result->>'duplicate')::boolean);
END; $$;

CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(p_run_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_window JSONB; v_existing public.market_collection_checkpoints%ROWTYPE; r JSONB;
  v_reserved public.market_source_quota_reservations%ROWTYPE; v_used INT;
BEGIN
  IF p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR NOT(p_payload ?& ARRAY['cache_key','receipt','items'])
     OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb OR octet_length(p_payload::text)>65536
     OR jsonb_typeof(p_payload->'items')<>'array' OR jsonb_array_length(p_payload->'items')>50 THEN
    RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023'; END IF;
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  r:=p_payload->'receipt';
  IF r IS NULL OR NOT(r ?& ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])
     OR (r-ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])<>'{}'::jsonb
     OR r->>'status' NOT IN ('succeeded','failed','quota_blocked') OR (r->>'request_cost')::int NOT BETWEEN 1 AND 100
     OR r->'cache_predecessor_receipt_id'<>'null'::jsonb OR r->>'cache_key'<>p_payload->>'cache_key'
     OR (r->'requested_window'->>'start')::timestamptz<>(v_window->>'start')::timestamptz
     OR (r->'requested_window'->>'end')::timestamptz<>(v_window->>'end')::timestamptz
     OR ((r->>'status')='succeeded' AND ((r->>'expires_at')::timestamptz<=(r->>'retrieved_at')::timestamptz OR r->>'response_hash' !~ '^[0-9a-f]{64}$'))
     OR ((r->>'status')<>'succeeded' AND (r->'expires_at'<>'null'::jsonb OR COALESCE(r->>'error_code','')='')) THEN
    RAISE EXCEPTION 'checkpoint must record an actual request outcome' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_reserved FROM public.market_source_quota_reservations WHERE run_id=p_run_id AND id=(r->>'reservation_id')::uuid AND provider=r->>'provider';
  IF NOT FOUND THEN RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_existing FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key' FOR UPDATE;
  IF FOUND AND v_existing.payload=p_payload-'cache_key' THEN RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key'); END IF;
  IF FOUND AND (v_existing.source_receipt_id=(r->>'source_receipt_id')::uuid
     OR (v_existing.payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp()) THEN
    RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(sum((payload->'receipt'->>'request_cost')::int),0) INTO v_used FROM (
    SELECT payload FROM public.market_collection_checkpoints WHERE run_id=p_run_id
    UNION ALL SELECT payload FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id
  ) paid WHERE payload->'receipt'->>'reservation_id'=r->>'reservation_id';
  IF v_used+(r->>'request_cost')::int>v_reserved.reserved_requests THEN RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000'; END IF;
  IF v_existing.run_id IS NOT NULL THEN
    INSERT INTO public.market_collection_checkpoint_history(run_id,cache_key,source_receipt_id,payload)
      VALUES(v_existing.run_id,v_existing.cache_key,v_existing.source_receipt_id,v_existing.payload);
    UPDATE public.market_collection_checkpoints SET source_receipt_id=(r->>'source_receipt_id')::uuid,
      payload=p_payload-'cache_key',created_at=statement_timestamp() WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
  ELSE
    INSERT INTO public.market_collection_checkpoints(run_id,cache_key,request_window,source_receipt_id,payload)
      VALUES(p_run_id,p_payload->>'cache_key',v_window,(r->>'source_receipt_id')::uuid,p_payload-'cache_key');
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
END; $$;

-- This outer contract owns lineage; the inner recorder accepts exactly its original keys.
CREATE OR REPLACE FUNCTION public.record_market_intelligence(p_run_id UUID,p_completion_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_effective JSONB; v_result JSONB; r JSONB; c JSONB; v_provider TEXT;
  v_saved public.market_intelligence_collection_completions%ROWTYPE; v_receipts JSONB;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('market-intelligence-completion:'||p_completion_id::text,0));
  SELECT * INTO v_saved FROM public.market_intelligence_collection_completions WHERE completion_id=p_completion_id;
  IF FOUND THEN
    IF v_saved.run_id<>p_run_id OR v_saved.payload IS DISTINCT FROM p_payload THEN RAISE EXCEPTION 'intelligence completion idempotency mismatch' USING ERRCODE='22023'; END IF;
    RETURN v_saved.receipt||jsonb_build_object('duplicate',true);
  END IF;
  PERFORM 1 FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.market_intelligence_quote_attempts WHERE run_id=p_run_id AND status='pending') THEN
    RAISE EXCEPTION 'quote collection outcome uncertain' USING ERRCODE='55000'; END IF;
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'receipts') LOOP
    IF NOT(r ? 'cache_predecessor_receipt_id') THEN RAISE EXCEPTION 'missing cache lineage' USING ERRCODE='22023'; END IF;
    SELECT provider INTO v_provider FROM public.market_source_quota_reservations WHERE run_id=p_run_id AND id=(r->>'reservation_id')::uuid;
    IF NOT FOUND THEN RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE='22023'; END IF;
    IF r->>'status'='cache_hit' THEN
      IF r->>'id'=r->>'cache_predecessor_receipt_id' OR (r->>'request_cost')::int<>0 THEN RAISE EXCEPTION 'invalid cache lineage' USING ERRCODE='22023'; END IF;
      SELECT payload->'receipt' INTO c FROM (
        SELECT source_receipt_id,payload FROM public.market_collection_checkpoints WHERE run_id=p_run_id
        UNION ALL SELECT source_receipt_id,payload FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id
      ) checkpoint WHERE source_receipt_id=(r->>'cache_predecessor_receipt_id')::uuid;
      IF c IS NULL OR c->>'status'<>'succeeded' OR c->>'provider' IS DISTINCT FROM v_provider
        OR c->>'reservation_id' IS DISTINCT FROM r->>'reservation_id'
        OR c->>'cache_key' IS DISTINCT FROM r->>'cache_key' OR c->'requested_window' IS DISTINCT FROM r->'requested_window'
        OR c->>'response_hash' IS DISTINCT FROM r->>'response_hash'
        OR c->'retrieved_at' IS DISTINCT FROM r->'retrieved_at' OR c->'expires_at' IS DISTINCT FROM r->'expires_at' THEN
        RAISE EXCEPTION 'cache predecessor checkpoint unavailable' USING ERRCODE='22023'; END IF;
    ELSIF r->'cache_predecessor_receipt_id'<>'null'::jsonb THEN RAISE EXCEPTION 'invalid cache lineage' USING ERRCODE='22023'; END IF;
  END LOOP;
  SELECT COALESCE(jsonb_agg(value-'cache_predecessor_receipt_id'),'[]'::jsonb) INTO v_receipts FROM jsonb_array_elements(p_payload->'receipts');
  -- Restore every actual checkpoint into the authoritative quota/source ledger.
  -- A cache-hit receipt never replaces or rewrites the original paid receipt.
  FOR c IN SELECT payload->'receipt' FROM public.market_collection_checkpoints WHERE run_id=p_run_id
    UNION ALL SELECT payload->'receipt' FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id LOOP
    r:=(c-ARRAY['provider','source_receipt_id','requested_limit','observed_at','error_code','cache_predecessor_receipt_id'])
      ||jsonb_build_object('id',c->'source_receipt_id','error',CASE WHEN c->'error_code'='null'::jsonb THEN 'null'::jsonb ELSE jsonb_build_object('code',c->'error_code') END);
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(v_receipts) value WHERE value->>'id'=r->>'id') THEN
      IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_receipts) value WHERE value=r) THEN RAISE EXCEPTION 'checkpoint receipt mismatch' USING ERRCODE='22023'; END IF;
    ELSE v_receipts:=v_receipts||jsonb_build_array(r); END IF;
  END LOOP;
  v_effective:=jsonb_set(p_payload,'{receipts}',v_receipts);
  v_result:=public.record_market_intelligence_provider_v2(p_run_id,p_completion_id,v_effective);
  INSERT INTO public.market_run_source_item_provenance(run_item_id,run_id,source_item_id,source_receipt_id,provider,request_url,retrieved_at,reporting_at,entity_ids,security_ids,discovery_status)
    SELECT (value->>'run_item_id')::uuid,p_run_id,(value->>'id')::uuid,(value->>'receipt_id')::uuid,value->>'provider',value->>'request_url',(value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,value->'entity_ids',value->'security_ids',value->>'discovery_status'
    FROM jsonb_array_elements(p_payload->'items') value;
  INSERT INTO public.market_checkpoint_receipt_lineage(cache_receipt_id,run_id,cache_predecessor_receipt_id)
    SELECT (value->>'id')::uuid,p_run_id,(value->>'cache_predecessor_receipt_id')::uuid FROM jsonb_array_elements(p_payload->'receipts') value WHERE value->>'status'='cache_hit';
  INSERT INTO public.market_intelligence_collection_completions(completion_id,run_id,payload,receipt) VALUES(p_completion_id,p_run_id,p_payload,v_result);
  RETURN v_result;
END; $$;

REVOKE ALL ON FUNCTION public.record_market_intelligence_legacy(UUID,UUID,JSONB),public.record_market_intelligence_provider_v2(UUID,UUID,JSONB) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_intelligence_completion(UUID,UUID),public.record_market_intelligence(UUID,UUID,JSONB),public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB),public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.read_market_intelligence_completion(UUID,UUID),public.record_market_intelligence(UUID,UUID,JSONB),public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB),public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;

ALTER TABLE public.market_intelligence_context_inputs ADD COLUMN IF NOT EXISTS current_quotes JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.market_intelligence_context_inputs ADD COLUMN IF NOT EXISTS quote_receipt_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
REVOKE ALL ON public.market_intelligence_context_inputs FROM PUBLIC,anon,authenticated,service_role;

CREATE TABLE IF NOT EXISTS public.market_intelligence_quote_attempts (
  source_receipt_id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id),
  reservation_id UUID NOT NULL REFERENCES public.market_source_quota_reservations(id), ticker TEXT NOT NULL,
  cache_key TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed')),
  quote JSONB, checkpoint JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.market_intelligence_quote_attempts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_intelligence_quote_attempts FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.claim_market_intelligence_quote(p_run_id UUID,p_input JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_run public.market_intelligence_runs%ROWTYPE; v_existing public.market_intelligence_quote_attempts%ROWTYPE;
  v_reserved INT; v_used INT;
BEGIN
  SELECT * INTO v_run FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND OR v_run.request_window IS NULL OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
    OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  IF p_input IS NULL OR NOT(p_input ?& ARRAY['ticker','cache_key','source_receipt_id','reservation_id'])
    OR (p_input-ARRAY['ticker','cache_key','source_receipt_id','reservation_id'])<>'{}'::jsonb
    OR p_input->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$' OR p_input->>'cache_key' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid quote collection identity' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_existing FROM public.market_intelligence_quote_attempts WHERE source_receipt_id=(p_input->>'source_receipt_id')::uuid;
  IF FOUND THEN
    IF v_existing.run_id<>p_run_id OR v_existing.ticker<>p_input->>'ticker' OR v_existing.cache_key<>p_input->>'cache_key'
      OR v_existing.reservation_id<>(p_input->>'reservation_id')::uuid THEN RAISE EXCEPTION 'quote identity mismatch' USING ERRCODE='22023'; END IF;
    RETURN jsonb_build_object('status',CASE WHEN v_existing.status='pending' THEN 'uncertain' ELSE 'completed' END,'checkpoint',v_existing.checkpoint);
  END IF;
  SELECT reserved_requests INTO v_reserved FROM public.market_source_quota_reservations
    WHERE id=(p_input->>'reservation_id')::uuid AND run_id=p_run_id AND provider='yahoo';
  IF NOT FOUND THEN RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(sum(cost),0) INTO v_used FROM (
    SELECT (payload->'receipt'->>'request_cost')::int cost FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND payload->'receipt'->>'provider'='yahoo'
    UNION ALL SELECT (payload->'receipt'->>'request_cost')::int FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id AND payload->'receipt'->>'provider'='yahoo'
    UNION ALL SELECT 1 FROM public.market_intelligence_quote_attempts WHERE run_id=p_run_id AND status='pending'
  ) used;
  IF v_used>=v_reserved THEN RETURN jsonb_build_object('status','quota_blocked','request_window',v_run.request_window); END IF;
  INSERT INTO public.market_intelligence_quote_attempts(source_receipt_id,run_id,reservation_id,ticker,cache_key,status)
    VALUES((p_input->>'source_receipt_id')::uuid,p_run_id,(p_input->>'reservation_id')::uuid,p_input->>'ticker',p_input->>'cache_key','pending');
  RETURN jsonb_build_object('status','claimed','request_window',v_run.request_window);
END; $$;

-- Called only by the server producer after its own bounded provider fetch.
-- There is deliberately no gateway operation accepting p_quote or p_checkpoint.
CREATE OR REPLACE FUNCTION public.record_market_intelligence_quote(p_run_id UUID,p_receipt_id UUID,p_quote JSONB,p_checkpoint JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_attempt public.market_intelligence_quote_attempts%ROWTYPE;
BEGIN
  SELECT * INTO v_attempt FROM public.market_intelligence_quote_attempts WHERE source_receipt_id=p_receipt_id AND run_id=p_run_id FOR UPDATE;
  IF NOT FOUND OR v_attempt.status<>'pending' THEN RAISE EXCEPTION 'quote attempt unavailable' USING ERRCODE='22023'; END IF;
  IF p_checkpoint->'receipt'->>'source_receipt_id' IS DISTINCT FROM p_receipt_id::text
    OR p_checkpoint->'receipt'->>'reservation_id' IS DISTINCT FROM v_attempt.reservation_id::text
    OR p_checkpoint->>'cache_key' IS DISTINCT FROM v_attempt.cache_key
    OR (p_checkpoint->'receipt'->>'request_cost')::int<>1
    OR p_checkpoint->'receipt'->>'provider' IS DISTINCT FROM 'yahoo'
    OR p_checkpoint->'receipt'->>'status' IS DISTINCT FROM (CASE WHEN p_quote IS NULL THEN 'failed' ELSE 'succeeded' END)
    OR (p_quote IS NOT NULL AND p_checkpoint->'receipt'->>'response_hash' IS DISTINCT FROM p_quote->>'response_hash')
    OR (p_quote IS NOT NULL AND (p_quote->>'ticker' IS DISTINCT FROM v_attempt.ticker OR p_quote->>'currency' IS DISTINCT FROM 'USD')) THEN
    RAISE EXCEPTION 'quote outcome identity mismatch' USING ERRCODE='22023'; END IF;
  PERFORM public.checkpoint_market_intelligence_collection(p_run_id,p_checkpoint);
  UPDATE public.market_intelligence_quote_attempts SET status=CASE WHEN p_quote IS NULL THEN 'failed' ELSE 'succeeded' END,
    quote=p_quote,checkpoint=p_checkpoint WHERE source_receipt_id=p_receipt_id;
  RETURN p_checkpoint;
END; $$;
REVOKE ALL ON FUNCTION public.claim_market_intelligence_quote(UUID,JSONB),public.record_market_intelligence_quote(UUID,UUID,JSONB,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.claim_market_intelligence_quote(UUID,JSONB),public.record_market_intelligence_quote(UUID,UUID,JSONB,JSONB) TO service_role;

-- No caller-supplied score, valuation, or overlap parameter exists. This producer
-- reads server-acquired Yahoo outcomes and the current reconciled holdings table.
CREATE OR REPLACE FUNCTION public.refresh_market_intelligence_context(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_quotes JSONB:='{}'; v_values JSONB:='{}'; v_liquidity JSONB:='{}'; v_overlap JSONB:='{}'; v_ids JSONB:='[]';
  row_data RECORD; q JSONB; v_total NUMERIC:=0; v_complete BOOLEAN:=true; v_equities BOOLEAN:=true; v_result JSONB;
BEGIN
  IF NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id) THEN RAISE EXCEPTION 'analysis run unavailable' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    SELECT jsonb_build_object('holding_market_values',holding_market_values,'liquidity_by_ticker',liquidity_by_ticker,
      'overlap_by_ticker',overlap_by_ticker,'current_quotes',current_quotes,'quote_receipt_ids',quote_receipt_ids)
      INTO v_result FROM public.market_intelligence_context_inputs WHERE run_id=p_run_id;
    RETURN v_result;
  END IF;
  FOR row_data IN SELECT source_receipt_id,quote FROM public.market_intelligence_quote_attempts
    WHERE run_id=p_run_id AND status='succeeded' ORDER BY created_at LOOP
    q:=row_data.quote;
    IF q IS NULL OR NOT(q ?& ARRAY['ticker','currency','price','as_of','instrument_type','average_daily_dollar_volume'])
      OR q->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$' OR q->>'currency'<>'USD'
      OR q->>'instrument_type' NOT IN ('EQUITY','ETF') OR q->>'price' !~ '^[0-9]+(\.[0-9]+)?$'
      OR (q->>'average_daily_dollar_volume' IS NOT NULL AND q->>'average_daily_dollar_volume' !~ '^[0-9]+(\.[0-9]+)?$')
      THEN CONTINUE; END IF;
    IF (q->>'price')::numeric<=0 OR (q->>'average_daily_dollar_volume')::numeric<=0
      OR (q->>'as_of')::timestamptz<statement_timestamp()-interval '20 minutes'
      OR (q->>'as_of')::timestamptz>statement_timestamp()+interval '5 minutes' THEN CONTINUE; END IF;
    v_quotes:=v_quotes||jsonb_build_object(q->>'ticker',q);
    IF q->>'average_daily_dollar_volume' IS NOT NULL THEN
      v_liquidity:=v_liquidity||jsonb_build_object(q->>'ticker',round(least(1,(q->>'average_daily_dollar_volume')::numeric/10000000),6)::text);
    END IF;
    v_ids:=v_ids||jsonb_build_array(row_data.source_receipt_id);
  END LOOP;
  FOR row_data IN SELECT ticker,shares FROM public.holdings WHERE shares>0 ORDER BY ticker LIMIT 101 LOOP
    q:=v_quotes->row_data.ticker;
    IF q IS NULL THEN v_complete:=false; v_equities:=false; CONTINUE; END IF;
    v_values:=v_values||jsonb_build_object(row_data.ticker,(row_data.shares*(q->>'price')::numeric)::text);
    v_total:=v_total+row_data.shares*(q->>'price')::numeric;
    IF q->>'instrument_type'<>'EQUITY' THEN v_equities:=false; END IF;
  END LOOP;
  IF (SELECT count(*) FROM public.holdings WHERE shares>0)>100 THEN RAISE EXCEPTION 'holdings exceed context bound' USING ERRCODE='54000'; END IF;
  -- Overlap is provable for an all-equity inventory. ETF underlying composition
  -- is unavailable from a quote, so it cannot silently contribute zero overlap.
  IF v_complete AND v_equities AND v_total>0 THEN
    FOR row_data IN SELECT key,value FROM jsonb_each(v_quotes) LOOP
      IF row_data.value->>'instrument_type'='EQUITY' THEN
        v_overlap:=v_overlap||jsonb_build_object(row_data.key,round(COALESCE((v_values->>row_data.key)::numeric,0)/v_total,6)::text);
      END IF;
    END LOOP;
  END IF;
  INSERT INTO public.market_intelligence_context_inputs(run_id,holding_market_values,liquidity_by_ticker,overlap_by_ticker,current_quotes,quote_receipt_ids)
    VALUES(p_run_id,v_values,v_liquidity,v_overlap,v_quotes,v_ids)
    ON CONFLICT(run_id) DO UPDATE SET holding_market_values=EXCLUDED.holding_market_values,
      liquidity_by_ticker=EXCLUDED.liquidity_by_ticker,overlap_by_ticker=EXCLUDED.overlap_by_ticker,
      current_quotes=EXCLUDED.current_quotes,quote_receipt_ids=EXCLUDED.quote_receipt_ids;
  RETURN jsonb_build_object('holding_market_values',v_values,'liquidity_by_ticker',v_liquidity,'overlap_by_ticker',v_overlap,
    'current_quotes',v_quotes,'quote_receipt_ids',v_ids);
END; $$;
REVOKE ALL ON FUNCTION public.refresh_market_intelligence_context(UUID) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.refresh_market_intelligence_context(UUID) TO service_role;

-- Consolidated from sql/migrations/20260923_scheduled_run_lifecycle.sql
-- Scheduled lifecycle closure. Additive after the existing 20260922 controller contract.
-- A scheduled run is identified independently from its gateway request so concurrent
-- request IDs for one market-date/phase converge on one durable run.
ALTER TABLE public.analysis_runs
  ADD COLUMN IF NOT EXISTS scheduled_market_date DATE,
  ADD COLUMN IF NOT EXISTS scheduled_phase TEXT;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                 WHERE conrelid='public.analysis_runs'::regclass
                   AND conname='analysis_runs_scheduled_phase_valid') THEN
    ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_scheduled_phase_valid
      CHECK (scheduled_phase IS NULL OR scheduled_phase IN ('pre-market','intraday','post-market'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                 WHERE conrelid='public.analysis_runs'::regclass
                   AND conname='analysis_runs_scheduled_slot_complete') THEN
    ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_scheduled_slot_complete
      CHECK ((scheduled_market_date IS NULL) = (scheduled_phase IS NULL));
  END IF;
END;
$$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_runs_scheduled_slot
  ON public.analysis_runs(scheduled_market_date, scheduled_phase)
  WHERE scheduled_market_date IS NOT NULL AND scheduled_phase IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.market_scheduled_phase_deadlines (
  phase TEXT PRIMARY KEY CHECK (phase IN ('pre-market','intraday','post-market')),
  deadline_local TIME NOT NULL,
  grace_minutes INT NOT NULL DEFAULT 15 CHECK (grace_minutes BETWEEN 0 AND 60),
  effective_on DATE NOT NULL DEFAULT (timezone('America/Chicago', statement_timestamp())::date)
);
ALTER TABLE public.market_scheduled_phase_deadlines
  ADD COLUMN IF NOT EXISTS grace_minutes INT NOT NULL DEFAULT 15 CHECK (grace_minutes BETWEEN 0 AND 60);
ALTER TABLE public.market_scheduled_phase_deadlines ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_scheduled_phase_deadlines FROM PUBLIC, anon, authenticated;
INSERT INTO public.market_scheduled_phase_deadlines(phase,deadline_local,grace_minutes)
VALUES ('pre-market','06:30',15),('intraday','12:00',15),('post-market','15:10',15)
ON CONFLICT (phase) DO UPDATE SET deadline_local=EXCLUDED.deadline_local,
  grace_minutes=EXCLUDED.grace_minutes;

CREATE OR REPLACE FUNCTION public.start_market_analysis_run(
  p_request_id UUID, p_lease_token UUID, p_kind TEXT, p_market_date DATE
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_run public.analysis_runs%ROWTYPE;
BEGIN
  IF p_kind NOT IN ('pre-market','intraday','post-market','on-demand') OR p_market_date IS NULL THEN
    RAISE EXCEPTION 'invalid analysis run slot' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'start_run' OR v_request.lease_token<>p_lease_token
     OR v_request.status<>'claimed' THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001';
  END IF;
  SELECT * INTO v_run FROM public.analysis_runs WHERE gateway_request_id=p_request_id;
  IF FOUND THEN
    IF v_run.kind IS DISTINCT FROM p_kind
       OR COALESCE(v_run.scheduled_market_date,p_market_date) IS DISTINCT FROM p_market_date THEN
      RAISE EXCEPTION 'run identity mismatch' USING ERRCODE='22023';
    END IF;
    UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id;
    RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
  END IF;
  IF p_kind <> 'on-demand' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('scheduled-analysis-run:'||p_market_date::text||':'||p_kind,0));
    SELECT * INTO v_run FROM public.analysis_runs
      WHERE scheduled_market_date=p_market_date AND scheduled_phase=p_kind FOR UPDATE;
    IF FOUND THEN
      UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id;
      RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
    END IF;
    INSERT INTO public.analysis_runs(kind,status,gateway_request_id,scheduled_market_date,scheduled_phase)
    VALUES (p_kind,'running',p_request_id,p_market_date,p_kind) RETURNING * INTO v_run;
  ELSE
    INSERT INTO public.analysis_runs(kind,status,gateway_request_id)
    VALUES (p_kind,'running',p_request_id) RETURNING * INTO v_run;
  END IF;
  UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id;
  RETURN jsonb_build_object('run_id',v_run.id,'duplicate',false);
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_run public.analysis_runs%ROWTYPE; v_counts JSONB; v_statuses JSONB; v_ids JSONB; v_status TEXT;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM public.market_intelligence_runs i
                   WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase
                     AND i.market_date=v_run.scheduled_market_date)
       OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_run_events e
                      WHERE e.run_id=p_run_id AND e.status='completed')
       OR NOT EXISTS (SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id)
       OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id) THEN
      RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id) THEN
      RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q
                   WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish' AND q.status='completed')
       OR NOT EXISTS (SELECT 1 FROM public.market_publications p
                      WHERE p.run_id=p_run_id AND p.market_date=v_run.scheduled_market_date
                        AND p.phase=v_run.scheduled_phase AND p.status='suppressed') THEN
      RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q
                   WHERE q.run_id=p_run_id AND q.operation='record_report' AND q.status='completed')
       OR NOT EXISTS (SELECT 1 FROM public.market_reports r
                      WHERE r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date) THEN
      RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_reports r
                   JOIN public.market_report_publications p ON p.report_id=r.id
                   WHERE r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
                     AND p.status IN ('delivered','suppressed')) THEN
      RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023';
    END IF;
  END IF;
  SELECT jsonb_build_object(
    'evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),
    'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),
    'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),
    'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id)
  ) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb) INTO v_statuses FROM (
    SELECT status FROM public.market_publications WHERE run_id=p_run_id
    UNION ALL
    SELECT p.status FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id
     WHERE r.run_id=p_run_id
  ) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb) INTO v_ids FROM (
    SELECT value AS message_id FROM public.market_publications p
      CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE p.run_id=p_run_id
    UNION ALL
    SELECT value AS message_id FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id
      CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE r.run_id=p_run_id
  ) message_ids;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM public.market_gateway_requests
                           WHERE run_id=p_run_id AND status='failed')
                   OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain']
              THEN 'partial'
              WHEN NOT EXISTS(SELECT 1 FROM public.market_reports r
                              JOIN public.market_report_publications p ON p.report_id=r.id
                              WHERE r.run_id=p_run_id AND p.status='delivered')
                   AND EXISTS(SELECT 1 FROM public.market_reports r
                              JOIN public.market_report_publications p ON p.report_id=r.id
                              WHERE r.run_id=p_run_id AND p.status='suppressed')
              THEN 'suppressed' ELSE 'completed' END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),
    write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object('run_id',p_run_id,'status',v_status,'write_counts',v_counts,
    'publication_statuses',v_statuses,'telegram_message_ids',v_ids);
END;
$$;

CREATE OR REPLACE FUNCTION public.read_overdue_scheduled_market_phases(
  p_now TIMESTAMPTZ DEFAULT statement_timestamp()
) RETURNS TABLE(market_date DATE, phase TEXT, deadline_at TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_policy JSONB; v_today DATE := timezone('America/Chicago',p_now)::date;
BEGIN
  SELECT config INTO v_policy FROM public.market_policy_config WHERE active;
  IF NOT FOUND OR COALESCE((v_policy->>'market_calendar_year')::int,0) <> extract(year FROM v_today) THEN
    RAISE EXCEPTION 'calendar coverage missing' USING ERRCODE='22023';
  END IF;
  RETURN QUERY
    SELECT slot_days.market_date, deadline.phase,
           (slot_days.market_date::timestamp + deadline.deadline_local + make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago'
    FROM generate_series((SELECT min(effective_on) FROM public.market_scheduled_phase_deadlines),v_today,interval '1 day') AS days(value)
    CROSS JOIN LATERAL (SELECT days.value::date AS market_date) slot_days
    JOIN public.market_scheduled_phase_deadlines deadline ON deadline.effective_on<=slot_days.market_date
    WHERE extract(isodow FROM slot_days.market_date) BETWEEN 1 AND 5
      AND ((slot_days.market_date::timestamp + deadline.deadline_local + make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago') < p_now
      AND NOT (v_policy->'nyse_holidays' ? slot_days.market_date::text)
      AND NOT EXISTS (SELECT 1 FROM public.analysis_runs run WHERE run.scheduled_market_date=slot_days.market_date
                      AND run.scheduled_phase=deadline.phase AND run.status IN ('completed','suppressed'))
    ORDER BY slot_days.market_date, deadline.phase;
END;
$$;

DROP FUNCTION IF EXISTS public.start_market_analysis_run(UUID, UUID, TEXT);
REVOKE ALL ON FUNCTION public.start_market_analysis_run(UUID,UUID,TEXT,DATE) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_analysis_run(UUID,UUID,TEXT,DATE) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) TO stock_agent_dashboard;

-- Consolidated from sql/migrations/20260924_scheduled_lifecycle_followup.sql
-- Follow-up closure for the deployed scheduled lifecycle. Schedule changes are
-- append-only by phase/effective date; report receipts are bound through the
-- immutable report identity returned by the intentionally runless request.
ALTER TABLE public.market_scheduled_phase_deadlines
  DROP CONSTRAINT IF EXISTS market_scheduled_phase_deadlines_pkey;
ALTER TABLE public.market_scheduled_phase_deadlines
  ADD CONSTRAINT market_scheduled_phase_deadlines_pkey PRIMARY KEY (phase, effective_on);

INSERT INTO public.market_scheduled_phase_deadlines(phase,deadline_local,grace_minutes,effective_on)
VALUES
  ('pre-market','06:30',15,timezone('America/Chicago', statement_timestamp())::date),
  ('intraday','12:00',15,timezone('America/Chicago', statement_timestamp())::date),
  ('post-market','15:10',15,timezone('America/Chicago', statement_timestamp())::date)
ON CONFLICT (phase,effective_on) DO NOTHING;

CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_run public.analysis_runs%ROWTYPE; v_counts JSONB; v_statuses JSONB; v_ids JSONB; v_status TEXT;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM public.market_intelligence_runs i
                   WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase
                     AND i.market_date=v_run.scheduled_market_date)
       OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_run_events e
                      WHERE e.run_id=p_run_id AND e.status='completed')
       OR NOT EXISTS (SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id)
       OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id) THEN
      RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id) THEN
      RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q
                   WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish' AND q.status='completed')
       OR NOT EXISTS (SELECT 1 FROM public.market_publications p
                      WHERE p.run_id=p_run_id AND p.market_date=v_run.scheduled_market_date
                        AND p.phase=v_run.scheduled_phase AND p.status='suppressed') THEN
      RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END
      WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed'
        AND jsonb_typeof(q.response)='object'
        AND q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND q.response->>'report_hash'=r.report_hash
        AND q.response->>'rendered_hash'=r.rendered_hash
        AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
        AND ((v_run.scheduled_phase='pre-market' AND r.kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND r.kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND r.kind IN ('weekly','monthly','theme','urgent')))
    ) THEN
      RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END
      JOIN public.market_report_publications p ON p.report_id=r.id
      WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed'
        AND jsonb_typeof(q.response)='object'
        AND q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND q.response->>'report_hash'=r.report_hash
        AND q.response->>'rendered_hash'=r.rendered_hash
        AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
        AND ((v_run.scheduled_phase='pre-market' AND r.kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND r.kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND r.kind IN ('weekly','monthly','theme','urgent')))
        AND p.status IN ('delivered','suppressed')
    ) THEN
      RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023';
    END IF;
  END IF;
  SELECT jsonb_build_object(
    'evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),
    'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),
    'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),
    'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id)
  ) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb) INTO v_statuses FROM (
    SELECT status FROM public.market_publications WHERE run_id=p_run_id
    UNION ALL SELECT p.status FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id
      WHERE r.run_id=p_run_id
  ) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb) INTO v_ids FROM (
    SELECT value AS message_id FROM public.market_publications p CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE p.run_id=p_run_id
    UNION ALL SELECT value AS message_id FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE r.run_id=p_run_id
  ) message_ids;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM public.market_gateway_requests WHERE run_id=p_run_id AND status='failed')
                   OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain'] THEN 'partial'
              WHEN NOT EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND p.status='delivered')
                   AND EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND p.status='suppressed') THEN 'suppressed'
              ELSE 'completed' END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object('run_id',p_run_id,'status',v_status,'write_counts',v_counts,'publication_statuses',v_statuses,'telegram_message_ids',v_ids);
END;
$$;

CREATE OR REPLACE FUNCTION public.read_overdue_scheduled_market_phases(
  p_now TIMESTAMPTZ DEFAULT statement_timestamp()
) RETURNS TABLE(market_date DATE, phase TEXT, deadline_at TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_policy JSONB; v_today DATE := timezone('America/Chicago',p_now)::date; v_year TEXT := extract(year FROM v_today)::text;
BEGIN
  SELECT config INTO v_policy FROM public.market_policy_config WHERE active;
  IF NOT FOUND OR jsonb_typeof(v_policy->'nyse_holidays') IS DISTINCT FROM 'array'
     OR COALESCE(v_policy->>'market_calendar_year','') !~ '^[0-9]{4}$'
     OR v_policy->>'market_calendar_year' IS DISTINCT FROM v_year
     OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_policy->'nyse_holidays') AS holiday(value)
                WHERE jsonb_typeof(holiday.value)<>'string'
                   OR holiday.value #>> '{}' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                   OR left(holiday.value #>> '{}',4)<>v_year
                   OR to_char(to_date(holiday.value #>> '{}','FXYYYY-MM-DD'),'YYYY-MM-DD')<>holiday.value #>> '{}') THEN
    RAISE EXCEPTION 'calendar coverage missing' USING ERRCODE='22023';
  END IF;
  RETURN QUERY
    SELECT slot_days.market_date, scheduled.phase,
           (slot_days.market_date::timestamp + deadline.deadline_local + make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago'
    FROM generate_series((SELECT min(effective_on) FROM public.market_scheduled_phase_deadlines),v_today,interval '1 day') AS days(value)
    CROSS JOIN LATERAL (SELECT days.value::date AS market_date) slot_days
    CROSS JOIN (VALUES ('pre-market'::text),('intraday'::text),('post-market'::text)) AS scheduled(phase)
    JOIN LATERAL (
      SELECT deadline_local,grace_minutes FROM public.market_scheduled_phase_deadlines deadline
      WHERE deadline.phase=scheduled.phase AND deadline.effective_on<=slot_days.market_date
      ORDER BY deadline.effective_on DESC LIMIT 1
    ) deadline ON true
    WHERE extract(isodow FROM slot_days.market_date) BETWEEN 1 AND 5
      AND ((slot_days.market_date::timestamp + deadline.deadline_local + make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago') < p_now
      AND NOT (v_policy->'nyse_holidays' ? slot_days.market_date::text)
      AND NOT EXISTS (SELECT 1 FROM public.analysis_runs run WHERE run.scheduled_market_date=slot_days.market_date
                      AND run.scheduled_phase=scheduled.phase AND run.status IN ('completed','suppressed'))
    ORDER BY slot_days.market_date, scheduled.phase;
END;
$$;

REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID),public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) TO stock_agent_dashboard;

-- Consolidated from sql/migrations/20260925_scheduled_report_origins.sql
-- A report delivery may promote its final kind to urgent or intraday. Keep the
-- scheduled request's original deterministic report identity separately so the
-- lifecycle can attest to its scheduled origin without trusting a rewritten kind.
CREATE TABLE IF NOT EXISTS public.market_report_request_origins (
  request_id UUID PRIMARY KEY REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  scheduled_phase TEXT NOT NULL CHECK (scheduled_phase IN ('pre-market','intraday','post-market')),
  market_date DATE NOT NULL,
  requested_kind TEXT NOT NULL CHECK (requested_kind IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')),
  requested_report_id UUID NOT NULL,
  requested_packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  requested_idempotency_key TEXT NOT NULL CHECK (requested_idempotency_key ~ '^[0-9a-f]{64}$'),
  requested_report_hash TEXT NOT NULL CHECK (requested_report_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_market_report_request_origins_run
  ON public.market_report_request_origins(run_id, scheduled_phase, market_date);
ALTER TABLE public.market_report_request_origins ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_report_request_origins FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.record_market_report_origin(
  p_request_id UUID,
  p_lease_token UUID,
  p_run_id UUID,
  p_market_date DATE,
  p_requested_kind TEXT,
  p_requested_report_id UUID,
  p_requested_packet_id UUID,
  p_requested_idempotency_key TEXT,
  p_requested_report_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_run public.analysis_runs%ROWTYPE;
  v_origin public.market_report_request_origins%ROWTYPE;
  v_packet_hash TEXT;
  v_expected_key TEXT;
  v_expected_id UUID;
BEGIN
  IF p_request_id IS NULL OR p_lease_token IS NULL OR p_run_id IS NULL
     OR p_market_date IS NULL OR p_requested_packet_id IS NULL
     OR p_requested_report_id IS NULL
     OR p_requested_kind NOT IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')
     OR p_requested_idempotency_key !~ '^[0-9a-f]{64}$'
     OR p_requested_report_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid report origin' USING ERRCODE='22023';
  END IF;

  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'record_report'
     OR v_request.status<>'claimed' OR v_request.lease_token<>p_lease_token
     OR v_request.run_id IS NOT NULL THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001';
  END IF;

  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NULL THEN
    RETURN jsonb_build_object('scheduled',false,'duplicate',false);
  END IF;
  IF v_run.scheduled_market_date IS DISTINCT FROM p_market_date
     OR NOT ((v_run.scheduled_phase='pre-market' AND p_requested_kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND p_requested_kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND p_requested_kind IN ('weekly','monthly','theme','urgent'))) THEN
    RAISE EXCEPTION 'scheduled report origin mismatch' USING ERRCODE='22023';
  END IF;

  SELECT packet_hash INTO v_packet_hash FROM public.market_evidence_packets
  WHERE id=p_requested_packet_id AND run_id=p_run_id AND status='completed';
  IF NOT FOUND THEN RAISE EXCEPTION 'completed evidence packet unavailable' USING ERRCODE='22023'; END IF;
  v_expected_key := encode(extensions.digest(convert_to(
    'v2:' || p_requested_kind || ':' || p_market_date::text || ':' ||
      v_packet_hash || ':' || p_requested_report_hash,
    'UTF8'
  ),'sha256'),'hex');
  v_expected_id := (
    substr(v_expected_key,1,8) || '-' || substr(v_expected_key,9,4) || '-5' ||
    substr(v_expected_key,14,3) || '-8' || substr(v_expected_key,18,3) || '-' ||
    substr(v_expected_key,21,12)
  )::uuid;
  IF p_requested_idempotency_key<>v_expected_key OR p_requested_report_id<>v_expected_id THEN
    RAISE EXCEPTION 'scheduled report identity mismatch' USING ERRCODE='22023';
  END IF;

  SELECT * INTO v_origin FROM public.market_report_request_origins
  WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_origin.run_id IS DISTINCT FROM p_run_id
       OR v_origin.scheduled_phase IS DISTINCT FROM v_run.scheduled_phase
       OR v_origin.market_date IS DISTINCT FROM p_market_date
       OR v_origin.requested_kind IS DISTINCT FROM p_requested_kind
       OR v_origin.requested_report_id IS DISTINCT FROM p_requested_report_id
       OR v_origin.requested_packet_id IS DISTINCT FROM p_requested_packet_id
       OR v_origin.requested_idempotency_key IS DISTINCT FROM p_requested_idempotency_key
       OR v_origin.requested_report_hash IS DISTINCT FROM p_requested_report_hash THEN
      RAISE EXCEPTION 'scheduled report origin idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('scheduled',true,'duplicate',true);
  END IF;

  INSERT INTO public.market_report_request_origins(
    request_id,run_id,scheduled_phase,market_date,requested_kind,
    requested_report_id,requested_packet_id,requested_idempotency_key,requested_report_hash
  ) VALUES (
    p_request_id,p_run_id,v_run.scheduled_phase,p_market_date,p_requested_kind,
    p_requested_report_id,p_requested_packet_id,p_requested_idempotency_key,p_requested_report_hash
  );
  RETURN jsonb_build_object('scheduled',true,'duplicate',false);
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_run public.analysis_runs%ROWTYPE; v_counts JSONB; v_statuses JSONB; v_ids JSONB; v_status TEXT;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM public.market_intelligence_runs i WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase AND i.market_date=v_run.scheduled_market_date) OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_run_events e WHERE e.run_id=p_run_id AND e.status='completed') OR NOT EXISTS (SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id) OR NOT EXISTS (SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id) THEN RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.market_gateway_requests q WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish' AND q.status='completed') OR NOT EXISTS (SELECT 1 FROM public.market_publications p WHERE p.run_id=p_run_id AND p.market_date=v_run.scheduled_market_date AND p.phase=v_run.scheduled_phase AND p.status='suppressed') THEN RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_report_request_origins o ON o.request_id=q.request_id
      JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END
      WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed' AND jsonb_typeof(q.response)='object' AND q.response->>'report_hash'=r.report_hash AND q.response->>'rendered_hash'=r.rendered_hash AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase AND o.market_date=v_run.scheduled_market_date AND o.requested_packet_id=r.packet_id AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly')) OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent')) OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent')))
    ) THEN RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_report_request_origins o ON o.request_id=q.request_id
      JOIN public.market_reports r ON r.id=CASE WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN (q.response->>'report_id')::uuid END
      JOIN public.market_report_publications p ON p.report_id=r.id
      WHERE q.operation='record_report' AND q.run_id IS NULL AND q.status='completed' AND jsonb_typeof(q.response)='object' AND q.response->>'report_hash'=r.report_hash AND q.response->>'rendered_hash'=r.rendered_hash AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase AND o.market_date=v_run.scheduled_market_date AND o.requested_packet_id=r.packet_id AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly')) OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent')) OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent'))) AND p.status IN ('delivered','suppressed')
    ) THEN RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023'; END IF;
  END IF;
  SELECT jsonb_build_object('evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id)) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb) INTO v_statuses FROM (SELECT status FROM public.market_publications WHERE run_id=p_run_id UNION ALL SELECT p.status FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb) INTO v_ids FROM (SELECT value AS message_id FROM public.market_publications p CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE p.run_id=p_run_id UNION ALL SELECT value AS message_id FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value WHERE r.run_id=p_run_id) message_ids;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM public.market_gateway_requests WHERE run_id=p_run_id AND status='failed') OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain'] THEN 'partial' WHEN NOT EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id WHERE r.run_id=p_run_id AND p.status='delivered') AND EXISTS(SELECT 1 FROM public.market_reports r JOIN public.market_report_publications p ON p.report_id=r.id AND r.run_id=p_run_id WHERE p.status='suppressed') THEN 'suppressed' ELSE 'completed' END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object('run_id',p_run_id,'status',v_status,'write_counts',v_counts,'publication_statuses',v_statuses,'telegram_message_ids',v_ids);
END; $$;

REVOKE ALL ON FUNCTION public.record_market_report_origin(UUID,UUID,UUID,DATE,TEXT,UUID,UUID,TEXT,TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_report_origin(UUID,UUID,UUID,DATE,TEXT,UUID,UUID,TEXT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;

-- Consolidated from sql/migrations/20260926_report_suppression_reasons.sql
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

-- Consolidated from sql/migrations/20260927_release_evidence_reader.sql
-- Dedicated SELECT-only evidence membership. The runtime starts NOLOGIN; the
-- protected operator may provision its password/login after reviewing the grants.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='stock_agent_release_reader') THEN
    CREATE ROLE stock_agent_release_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='stock_agent_release_reader_runtime') THEN
    CREATE ROLE stock_agent_release_reader_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
END; $$;
GRANT stock_agent_release_reader TO stock_agent_release_reader_runtime;
GRANT USAGE ON SCHEMA public,extensions TO stock_agent_release_reader;
DO $$
DECLARE name TEXT;
BEGIN
  FOREACH name IN ARRAY ARRAY[
    'holdings','transactions','portfolio_commands','analysis_runs','market_evidence_packets','market_reports',
    'market_report_publications','market_intelligence_runs','market_intelligence_collection_completions',
    'market_intelligence_run_events','market_collection_checkpoints','market_events','market_candidate_rankings',
    'market_gateway_requests','market_report_request_origins','market_publications',
    'market_source_quota_reservations','market_source_receipts','market_alert_drafts','market_alert_events','market_alert_actions'
  ] LOOP
    EXECUTE format('REVOKE ALL ON public.%I FROM stock_agent_release_reader,stock_agent_release_reader_runtime',name);
    EXECUTE format('GRANT SELECT ON public.%I TO stock_agent_release_reader',name);
    EXECUTE format('DROP POLICY IF EXISTS release_evidence_select ON public.%I',name);
    EXECUTE format('CREATE POLICY release_evidence_select ON public.%I FOR SELECT TO stock_agent_release_reader USING (true)',name);
  END LOOP;
  IF to_regclass('supabase_migrations.schema_migrations') IS NOT NULL THEN
    GRANT USAGE ON SCHEMA supabase_migrations TO stock_agent_release_reader;
    GRANT SELECT(version,statements) ON supabase_migrations.schema_migrations TO stock_agent_release_reader;
  END IF;
END; $$;

-- Consolidated from sql/migrations/20260928_weekly_audit_read_scope.sql
-- Extend the existing owner-only SELECT role for the bounded weekly audit.
-- The runtime login inherits only stock_agent_dashboard and cannot execute writes.

REVOKE ALL PRIVILEGES ON TABLE
  public.suggestion_grades,
  public.lessons,
  public.daily_snapshots
FROM stock_agent_dashboard;

GRANT SELECT (id, suggestion_id, graded_at, result, price_then, price_later,
              horizon_days, note, benchmark_ticker, stock_return_pct,
              benchmark_return_pct, excess_return_pct, mfe_pct, mae_pct,
              entry_hit_at, stop_hit_at, target_hit_at, invalidation_hit_at,
              coverage_status, horizon_sessions, policy_version, final_action,
              direction_success)
  ON public.suggestion_grades TO stock_agent_dashboard;
GRANT SELECT (id, entry_date, category, content, created_at)
  ON public.lessons TO stock_agent_dashboard;
GRANT SELECT (id, snap_date, ticker, close, day_move_pct, rsi14, sma50, sma200, macd_hist)
  ON public.daily_snapshots TO stock_agent_dashboard;

ALTER TABLE public.suggestion_grades ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lessons ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS owner_dashboard_select_grades ON public.suggestion_grades;
CREATE POLICY owner_dashboard_select_grades ON public.suggestion_grades
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_lessons ON public.lessons;
CREATE POLICY owner_dashboard_select_lessons ON public.lessons
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_snapshots ON public.daily_snapshots;
CREATE POLICY owner_dashboard_select_snapshots ON public.daily_snapshots
  FOR SELECT TO stock_agent_dashboard USING (true);

-- Consolidated from sql/migrations/20260929_policy_lifecycle_closure.sql
-- Final policy/lifecycle closure. Legacy dry_powder is deliberately not a cash
-- authority. Spendable cash must be an explicit, fresh reconciliation bound to
-- the transaction ledger watermark.
CREATE TABLE IF NOT EXISTS public.portfolio_cash_ledger_state (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
INSERT INTO public.portfolio_cash_ledger_state(singleton) VALUES(true)
ON CONFLICT(singleton) DO NOTHING;

CREATE OR REPLACE FUNCTION public.advance_portfolio_cash_ledger()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
  UPDATE public.portfolio_cash_ledger_state
  SET revision=revision+1,updated_at=statement_timestamp()
  WHERE singleton=true;
  RETURN NULL;
END;
$$;
DROP TRIGGER IF EXISTS transactions_advance_cash_ledger ON public.transactions;
CREATE TRIGGER transactions_advance_cash_ledger
AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.transactions
FOR EACH STATEMENT EXECUTE FUNCTION public.advance_portfolio_cash_ledger();

CREATE OR REPLACE FUNCTION public.read_portfolio_cash_ledger_watermark()
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
  SELECT jsonb_build_object(
    'ledger_watermark',revision::text,
    'ledger_updated_at',updated_at
  ) FROM public.portfolio_cash_ledger_state WHERE singleton=true
$$;

CREATE TABLE IF NOT EXISTS public.reconciled_cash_snapshots (
  id UUID PRIMARY KEY,
  as_of TIMESTAMPTZ NOT NULL,
  fresh_through TIMESTAMPTZ NOT NULL,
  ledger_watermark BIGINT NOT NULL CHECK (ledger_watermark >= 0),
  core_available NUMERIC NOT NULL CHECK (core_available >= 0),
  growth_available NUMERIC NOT NULL CHECK (growth_available >= 0),
  speculative_available NUMERIC NOT NULL CHECK (speculative_available >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CHECK (fresh_through > as_of AND fresh_through <= as_of + interval '30 minutes')
);
CREATE INDEX IF NOT EXISTS idx_reconciled_cash_snapshots_current
  ON public.reconciled_cash_snapshots(as_of DESC,created_at DESC);
DROP TRIGGER IF EXISTS reconciled_cash_snapshots_append_only
  ON public.reconciled_cash_snapshots;
CREATE TRIGGER reconciled_cash_snapshots_append_only BEFORE UPDATE OR DELETE
ON public.reconciled_cash_snapshots FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();

CREATE OR REPLACE FUNCTION public.record_reconciled_cash_snapshot(
  p_snapshot_id UUID,
  p_as_of TIMESTAMPTZ,
  p_fresh_through TIMESTAMPTZ,
  p_ledger_watermark BIGINT,
  p_cash JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_revision BIGINT;
  v_ledger_updated_at TIMESTAMPTZ;
  v_existing public.reconciled_cash_snapshots%ROWTYPE;
  v_duplicate BOOLEAN := false;
BEGIN
  IF p_snapshot_id IS NULL OR p_as_of IS NULL OR p_fresh_through IS NULL
     OR p_ledger_watermark IS NULL OR jsonb_typeof(p_cash) IS DISTINCT FROM 'object'
     OR NOT (p_cash ?& ARRAY['core','growth','speculative'])
     OR (p_cash - ARRAY['core','growth','speculative']) <> '{}'::jsonb
     OR EXISTS (
       SELECT 1 FROM jsonb_each(p_cash) item
       WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'string'
         OR trim(both '"' from item.value::text) !~ '^(0|[1-9][0-9]{0,14})(\.[0-9]{1,6})?$'
     )
     OR p_as_of > statement_timestamp() + interval '1 minute'
     OR p_fresh_through <= statement_timestamp()
     OR p_fresh_through > p_as_of + interval '30 minutes' THEN
    RAISE EXCEPTION 'invalid reconciled cash snapshot' USING ERRCODE='22023';
  END IF;

  SELECT revision,updated_at INTO v_revision,v_ledger_updated_at
  FROM public.portfolio_cash_ledger_state
  WHERE singleton=true FOR SHARE;
  IF v_revision IS DISTINCT FROM p_ledger_watermark
     OR p_as_of < v_ledger_updated_at THEN
    RAISE EXCEPTION 'cash ledger watermark changed' USING ERRCODE='40001';
  END IF;

  SELECT * INTO v_existing FROM public.reconciled_cash_snapshots
  WHERE id=p_snapshot_id;
  IF FOUND THEN
    v_duplicate := true;
    IF v_existing.as_of IS DISTINCT FROM p_as_of
       OR v_existing.fresh_through IS DISTINCT FROM p_fresh_through
       OR v_existing.ledger_watermark IS DISTINCT FROM p_ledger_watermark
       OR v_existing.core_available IS DISTINCT FROM (p_cash->>'core')::numeric
       OR v_existing.growth_available IS DISTINCT FROM (p_cash->>'growth')::numeric
       OR v_existing.speculative_available IS DISTINCT FROM (p_cash->>'speculative')::numeric THEN
      RAISE EXCEPTION 'cash snapshot idempotency mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    INSERT INTO public.reconciled_cash_snapshots(
      id,as_of,fresh_through,ledger_watermark,
      core_available,growth_available,speculative_available
    ) VALUES (
      p_snapshot_id,p_as_of,p_fresh_through,p_ledger_watermark,
      (p_cash->>'core')::numeric,(p_cash->>'growth')::numeric,
      (p_cash->>'speculative')::numeric
    ) RETURNING * INTO v_existing;
  END IF;

  RETURN jsonb_build_object(
    'snapshot_id',v_existing.id,
    'as_of',v_existing.as_of,
    'fresh_through',v_existing.fresh_through,
    'ledger_watermark',v_existing.ledger_watermark::text,
    'spendable_cash',jsonb_build_object(
      'core',v_existing.core_available::text,
      'growth',v_existing.growth_available::text,
      'speculative',v_existing.speculative_available::text
    ),
    'duplicate',v_duplicate
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.read_reconciled_cash_snapshot(
  p_now TIMESTAMPTZ DEFAULT statement_timestamp()
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_snapshot public.reconciled_cash_snapshots%ROWTYPE;
BEGIN
  IF p_now IS NULL THEN RETURN NULL; END IF;
  SELECT snapshot.* INTO v_snapshot
  FROM public.reconciled_cash_snapshots snapshot
  JOIN public.portfolio_cash_ledger_state ledger
    ON ledger.singleton=true AND ledger.revision=snapshot.ledger_watermark
  WHERE snapshot.as_of<=p_now AND snapshot.fresh_through>=p_now
  ORDER BY snapshot.as_of DESC,snapshot.created_at DESC,snapshot.id
  LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  RETURN jsonb_build_object(
    'snapshot_id',v_snapshot.id,
    'as_of',v_snapshot.as_of,
    'fresh_through',v_snapshot.fresh_through,
    'ledger_watermark',v_snapshot.ledger_watermark::text,
    'spendable_cash',jsonb_build_object(
      'core',v_snapshot.core_available::text,
      'growth',v_snapshot.growth_available::text,
      'speculative',v_snapshot.speculative_available::text
    )
  );
END;
$$;

-- Keep cash authority valid through the decision write itself. The shared
-- ledger lock prevents a confirmed transaction mutation from committing
-- between this check and apply_market_decision_bundle's durable writes.
CREATE OR REPLACE FUNCTION public.apply_market_decision_bundle_with_cash_snapshot(
  p_request_id UUID,
  p_run_id UUID,
  p_lease_token UUID,
  p_policy_version INT,
  p_evaluations JSONB,
  p_suggestions JSONB,
  p_publication JSONB,
  p_cash_snapshot_id UUID,
  p_cash_ledger_watermark BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_revision BIGINT;
  v_snapshot public.reconciled_cash_snapshots%ROWTYPE;
  v_requires_cash BOOLEAN;
BEGIN
  IF jsonb_typeof(p_evaluations) IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'invalid decision transaction' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_request_id::text,0));
  IF EXISTS (
    SELECT 1 FROM public.market_publications publication
    WHERE publication.idempotency_key=p_request_id
       OR (p_run_id IS NOT NULL AND publication.run_id=p_run_id)
  ) THEN
    -- The original RPC revalidates the active request lease, then returns the
    -- immutable same-request receipt or RUN_ALREADY_EVALUATED. Cash freshness
    -- must gate new money decisions, not make an already durable write orphaned.
    RETURN public.apply_market_decision_bundle(
      p_request_id,p_run_id,p_lease_token,p_policy_version,
      p_evaluations,p_suggestions,p_publication
    );
  END IF;
  SELECT EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_evaluations) evaluation
    WHERE evaluation->>'policy_status'='approved'
      AND evaluation->>'final_action' IN ('buy','add')
  ) INTO v_requires_cash;

  IF v_requires_cash THEN
    IF p_cash_snapshot_id IS NULL OR p_cash_ledger_watermark IS NULL THEN
      RAISE EXCEPTION 'CASH_UNAVAILABLE' USING ERRCODE='55000';
    END IF;
    SELECT revision INTO v_revision
    FROM public.portfolio_cash_ledger_state
    WHERE singleton=true FOR SHARE;
    SELECT * INTO v_snapshot
    FROM public.reconciled_cash_snapshots
    WHERE id=p_cash_snapshot_id FOR SHARE;
    IF NOT FOUND OR v_revision IS DISTINCT FROM p_cash_ledger_watermark
       OR v_snapshot.ledger_watermark IS DISTINCT FROM p_cash_ledger_watermark
       OR v_snapshot.as_of>statement_timestamp()
       OR v_snapshot.fresh_through<statement_timestamp() THEN
      RAISE EXCEPTION 'CASH_UNAVAILABLE' USING ERRCODE='55000';
    END IF;
  ELSIF p_cash_snapshot_id IS NOT NULL OR p_cash_ledger_watermark IS NOT NULL THEN
    RAISE EXCEPTION 'unexpected cash authority' USING ERRCODE='22023';
  END IF;

  RETURN public.apply_market_decision_bundle(
    p_request_id,p_run_id,p_lease_token,p_policy_version,
    p_evaluations,p_suggestions,p_publication
  );
END;
$$;

-- A quiet scheduled intraday run closes with a durable policy outcome, not a
-- fabricated immutable report or Telegram publication.
CREATE TABLE IF NOT EXISTS public.market_run_terminal_outcomes (
  run_id UUID PRIMARY KEY REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  evaluation_request_id UUID NOT NULL UNIQUE
    REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  outcome TEXT NOT NULL CHECK (outcome IN ('no_trigger','not_actionable')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
DROP TRIGGER IF EXISTS market_run_terminal_outcomes_append_only
  ON public.market_run_terminal_outcomes;
CREATE TRIGGER market_run_terminal_outcomes_append_only BEFORE UPDATE OR DELETE
ON public.market_run_terminal_outcomes FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();

CREATE OR REPLACE FUNCTION public.record_market_run_outcome(
  p_request_id UUID,
  p_lease_token UUID,
  p_run_id UUID,
  p_outcome TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_run public.analysis_runs%ROWTYPE;
  v_existing public.market_run_terminal_outcomes%ROWTYPE;
  v_evaluation_request_id UUID;
BEGIN
  IF p_request_id IS NULL OR p_lease_token IS NULL OR p_run_id IS NULL
     OR p_outcome NOT IN ('no_trigger','not_actionable') THEN
    RAISE EXCEPTION 'invalid market run outcome' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'evaluate_and_publish'
     OR v_request.run_id IS DISTINCT FROM p_run_id
     OR v_request.status<>'claimed' OR v_request.lease_token<>p_lease_token THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001';
  END IF;
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id;
  IF NOT FOUND OR v_run.scheduled_phase IS DISTINCT FROM 'intraday'
     OR v_run.scheduled_market_date IS NULL THEN
    RAISE EXCEPTION 'quiet outcome requires scheduled intraday run' USING ERRCODE='22023';
  END IF;
  SELECT publication.idempotency_key INTO v_evaluation_request_id
    FROM public.market_publications publication
    WHERE publication.run_id=p_run_id
      AND publication.idempotency_key=p_request_id
      AND publication.market_date=v_run.scheduled_market_date
      AND publication.phase='intraday' AND publication.status='suppressed'
      AND publication.telegram_message_ids='[]'::jsonb
    ORDER BY publication.created_at,publication.id
    LIMIT 1;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'suppressed evaluation receipt unavailable' USING ERRCODE='55000';
  END IF;
  SELECT * INTO v_existing FROM public.market_run_terminal_outcomes
  WHERE run_id=p_run_id;
  IF FOUND THEN
    IF v_existing.evaluation_request_id IS DISTINCT FROM v_evaluation_request_id
       OR v_existing.outcome IS DISTINCT FROM p_outcome THEN
      RAISE EXCEPTION 'market run outcome mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object(
      'run_id',v_existing.run_id,'outcome',v_existing.outcome,'duplicate',true
    );
  END IF;
  INSERT INTO public.market_run_terminal_outcomes(
    run_id,evaluation_request_id,outcome
  ) VALUES(p_run_id,v_evaluation_request_id,p_outcome) RETURNING * INTO v_existing;
  RETURN jsonb_build_object(
    'run_id',v_existing.run_id,'outcome',v_existing.outcome,'duplicate',false
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_run public.analysis_runs%ROWTYPE;
  v_counts JSONB;
  v_statuses JSONB;
  v_ids JSONB;
  v_status TEXT;
  v_quiet_intraday BOOLEAN := false;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS (
      SELECT 1 FROM public.market_intelligence_runs i
      WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase
        AND i.market_date=v_run.scheduled_market_date
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_intelligence_run_events e
      WHERE e.run_id=p_run_id AND e.status='completed'
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id
    ) THEN
      RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id
    ) THEN
      RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish'
        AND q.status='completed'
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_publications p
      WHERE p.run_id=p_run_id AND p.market_date=v_run.scheduled_market_date
        AND p.phase=v_run.scheduled_phase AND p.status='suppressed'
    ) THEN
      RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023';
    END IF;

    SELECT v_run.scheduled_phase='intraday' AND EXISTS (
      SELECT 1 FROM public.market_run_terminal_outcomes outcome
      JOIN public.market_gateway_requests request
        ON request.request_id=outcome.evaluation_request_id
      JOIN public.market_publications publication
        ON publication.run_id=outcome.run_id
       AND publication.idempotency_key=outcome.evaluation_request_id
      WHERE outcome.run_id=p_run_id
        AND outcome.outcome IN ('no_trigger','not_actionable')
        AND request.run_id=p_run_id AND request.operation='evaluate_and_publish'
        AND request.status='completed'
        AND publication.market_date=v_run.scheduled_market_date
        AND publication.phase='intraday' AND publication.status='suppressed'
        AND publication.telegram_message_ids='[]'::jsonb
    ) INTO v_quiet_intraday;

    IF NOT v_quiet_intraday AND NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_report_request_origins o ON o.request_id=q.request_id
      JOIN public.market_reports r ON r.id=CASE
        WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        THEN (q.response->>'report_id')::uuid END
      WHERE q.operation='record_report' AND q.run_id IS NULL
        AND q.status='completed' AND jsonb_typeof(q.response)='object'
        AND q.response->>'report_hash'=r.report_hash
        AND q.response->>'rendered_hash'=r.rendered_hash
        AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase
        AND o.market_date=v_run.scheduled_market_date
        AND o.requested_packet_id=r.packet_id
        AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
        AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent')))
    ) THEN
      RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT v_quiet_intraday AND NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_report_request_origins o ON o.request_id=q.request_id
      JOIN public.market_reports r ON r.id=CASE
        WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        THEN (q.response->>'report_id')::uuid END
      JOIN public.market_report_publications p ON p.report_id=r.id
      WHERE q.operation='record_report' AND q.run_id IS NULL
        AND q.status='completed' AND jsonb_typeof(q.response)='object'
        AND q.response->>'report_hash'=r.report_hash
        AND q.response->>'rendered_hash'=r.rendered_hash
        AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase
        AND o.market_date=v_run.scheduled_market_date
        AND o.requested_packet_id=r.packet_id
        AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
        AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent')))
        AND p.status IN ('delivered','suppressed')
    ) THEN
      RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023';
    END IF;
  END IF;

  SELECT jsonb_build_object(
    'evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),
    'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),
    'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),
    'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id),
    'run_outcomes',(SELECT count(*) FROM public.market_run_terminal_outcomes WHERE run_id=p_run_id)
  ) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb)
  INTO v_statuses FROM (
    SELECT status FROM public.market_publications WHERE run_id=p_run_id
    UNION ALL
    SELECT p.status FROM public.market_reports r
    JOIN public.market_report_publications p ON p.report_id=r.id
    WHERE r.run_id=p_run_id
  ) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb)
  INTO v_ids FROM (
    SELECT value AS message_id FROM public.market_publications p
    CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value
    WHERE p.run_id=p_run_id
    UNION ALL
    SELECT value AS message_id FROM public.market_reports r
    JOIN public.market_report_publications p ON p.report_id=r.id
    CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value
    WHERE r.run_id=p_run_id
  ) message_ids;
  SELECT CASE
    WHEN EXISTS(
      SELECT 1 FROM public.market_gateway_requests
      WHERE run_id=p_run_id AND status='failed'
    ) OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain']
      THEN 'partial'
    WHEN v_quiet_intraday OR (
      NOT EXISTS(
        SELECT 1 FROM public.market_reports r
        JOIN public.market_report_publications p ON p.report_id=r.id
        WHERE r.run_id=p_run_id AND p.status='delivered'
      ) AND EXISTS(
        SELECT 1 FROM public.market_reports r
        JOIN public.market_report_publications p ON p.report_id=r.id
        WHERE r.run_id=p_run_id AND p.status='suppressed'
      )
    ) THEN 'suppressed'
    ELSE 'completed'
  END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),
    write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object(
    'run_id',p_run_id,'status',v_status,'write_counts',v_counts,
    'publication_statuses',v_statuses,'telegram_message_ids',v_ids
  );
END;
$$;

ALTER TABLE public.portfolio_cash_ledger_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reconciled_cash_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_run_terminal_outcomes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.portfolio_cash_ledger_state,
  public.reconciled_cash_snapshots,public.market_run_terminal_outcomes
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.advance_portfolio_cash_ledger(),
  public.read_portfolio_cash_ledger_watermark(),
  public.record_reconciled_cash_snapshot(UUID,TIMESTAMPTZ,TIMESTAMPTZ,BIGINT,JSONB),
  public.read_reconciled_cash_snapshot(TIMESTAMPTZ),
  public.apply_market_decision_bundle_with_cash_snapshot(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT),
  public.record_market_run_outcome(UUID,UUID,UUID,TEXT),
  public.finish_market_analysis_run(UUID)
  FROM PUBLIC,anon,authenticated;
REVOKE EXECUTE ON FUNCTION public.apply_market_decision_bundle(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB)
  FROM service_role;
GRANT EXECUTE ON FUNCTION public.record_reconciled_cash_snapshot(UUID,TIMESTAMPTZ,TIMESTAMPTZ,BIGINT,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_portfolio_cash_ledger_watermark() TO service_role;
GRANT EXECUTE ON FUNCTION public.read_reconciled_cash_snapshot(TIMESTAMPTZ) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_market_decision_bundle_with_cash_snapshot(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_run_outcome(UUID,UUID,UUID,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;

-- Consolidated from sql/migrations/20260930_provider_attempt_and_recovery_closure.sql
-- Close paid secondary-provider crash ambiguity before any outbound transport.
-- The checkpoint row itself is the durable attempt ledger: a process restart
-- reuses an uncertain outcome instead of issuing a replacement request.

CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(
  p_run_id UUID,
  p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_window JSONB;
  v_existing public.market_collection_checkpoints%ROWTYPE;
  r JSONB;
  v_reserved public.market_source_quota_reservations%ROWTYPE;
  v_used INT;
  v_existing_receipt JSONB;
  v_existing_uncertain BOOLEAN;
  v_incoming_uncertain BOOLEAN;
  v_same_attempt_identity BOOLEAN;
  v_valid_attempt_transition BOOLEAN;
BEGIN
  IF p_payload IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR NOT(p_payload ?& ARRAY['cache_key','receipt','items'])
     OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb
     OR octet_length(p_payload::text)>65536
     OR jsonb_typeof(p_payload->'items')<>'array'
     OR jsonb_array_length(p_payload->'items')>50 THEN
    RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023';
  END IF;
  SELECT request_window INTO v_window
  FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL
     OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;

  r:=p_payload->'receipt';
  IF r IS NULL OR NOT(r ?& ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])
     OR (r-ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])<>'{}'::jsonb
     OR r->>'status' NOT IN ('succeeded','failed','quota_blocked')
     OR (r->>'request_cost')::int NOT BETWEEN 1 AND 100
     OR r->'cache_predecessor_receipt_id'<>'null'::jsonb
     OR r->>'cache_key'<>p_payload->>'cache_key'
     OR (r->'requested_window'->>'start')::timestamptz<>(v_window->>'start')::timestamptz
     OR (r->'requested_window'->>'end')::timestamptz<>(v_window->>'end')::timestamptz
     OR (r->>'source_receipt_id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
     OR ((r->>'status')='succeeded'
       AND ((r->>'expires_at')::timestamptz<=(r->>'retrieved_at')::timestamptz
         OR r->>'response_hash' !~ '^[0-9a-f]{64}$'))
     OR ((r->>'status')<>'succeeded'
       AND (r->'expires_at'<>'null'::jsonb OR COALESCE(r->>'error_code','')='')) THEN
    RAISE EXCEPTION 'checkpoint must record an actual request outcome' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_reserved
  FROM public.market_source_quota_reservations
  WHERE id=(r->>'reservation_id')::uuid AND run_id=p_run_id AND provider=r->>'provider';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE='22023';
  END IF;
  v_incoming_uncertain := r->>'status'='failed'
    AND r->>'error_code'='TRANSPORT_OUTCOME_UNCERTAIN';
  IF v_incoming_uncertain AND (
      v_reserved.provider NOT IN ('alpha_vantage','finnhub')
      OR jsonb_array_length(p_payload->'items')<>0
      OR r->'observed_at'<>'null'::jsonb
      OR r->'expires_at'<>'null'::jsonb
      OR r->'upstream_remaining'<>'null'::jsonb
      OR r->'response_hash'<>'null'::jsonb
      OR (r->>'returned_count')::int<>0
      OR (r->>'accepted_count')::int<>0
      OR (r->>'duplicate_count')::int<>0
      OR (r->>'dropped_count')::int<>0
  ) THEN
    RAISE EXCEPTION 'invalid provider attempt barrier' USING ERRCODE='22023';
  END IF;

  SELECT * INTO v_existing
  FROM public.market_collection_checkpoints
  WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key' FOR UPDATE;
  IF FOUND AND v_existing.payload=p_payload-'cache_key' THEN
    RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
  END IF;
  SELECT COALESCE(sum((payload->'receipt'->>'request_cost')::int),0) INTO v_used FROM (
    SELECT payload FROM public.market_collection_checkpoints WHERE run_id=p_run_id
    UNION ALL SELECT payload FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id
  ) paid WHERE payload->'receipt'->>'reservation_id'=r->>'reservation_id';
  IF v_existing.run_id IS NOT NULL THEN
    v_existing_receipt := v_existing.payload->'receipt';
    v_existing_uncertain := v_existing_receipt->>'status'='failed'
      AND v_existing_receipt->>'error_code'='TRANSPORT_OUTCOME_UNCERTAIN';
    v_same_attempt_identity := v_existing.source_receipt_id::text=r->>'source_receipt_id'
      AND v_existing_receipt->>'provider'=r->>'provider'
      AND v_existing_receipt->>'reservation_id'=r->>'reservation_id'
      AND v_existing_receipt->>'cache_key'=r->>'cache_key'
      AND v_existing_receipt->'requested_window'=r->'requested_window'
      AND v_existing_receipt->>'requested_limit'=r->>'requested_limit';
    v_valid_attempt_transition := v_existing_uncertain AND v_same_attempt_identity AND (
      (v_incoming_uncertain
       AND (r->>'request_cost')::int=(v_existing_receipt->>'request_cost')::int+1)
      OR (NOT v_incoming_uncertain
       AND (r->>'request_cost')::int=(v_existing_receipt->>'request_cost')::int)
    );
    IF v_valid_attempt_transition THEN
      IF v_used-(v_existing_receipt->>'request_cost')::int+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
        RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
      END IF;
      -- Never archive an attempt barrier: history plus its terminal row would
      -- count the same provider request twice during restart reconciliation.
      UPDATE public.market_collection_checkpoints
      SET payload=p_payload-'cache_key',created_at=statement_timestamp()
      WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
    ELSE
      IF v_existing.source_receipt_id=(r->>'source_receipt_id')::uuid
         OR (v_existing_receipt->>'expires_at')::timestamptz>statement_timestamp() THEN
        RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023';
      END IF;
      IF v_used+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
        RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
      END IF;
      INSERT INTO public.market_collection_checkpoint_history(
        run_id,cache_key,source_receipt_id,payload
      ) VALUES(
        v_existing.run_id,v_existing.cache_key,v_existing.source_receipt_id,v_existing.payload
      );
      UPDATE public.market_collection_checkpoints
      SET source_receipt_id=(r->>'source_receipt_id')::uuid,
          payload=p_payload-'cache_key',created_at=statement_timestamp()
      WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
    END IF;
  ELSE
    IF v_used+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
      RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
    END IF;
    INSERT INTO public.market_collection_checkpoints(
      run_id,cache_key,request_window,source_receipt_id,payload
    ) VALUES(
      p_run_id,p_payload->>'cache_key',v_window,
      (r->>'source_receipt_id')::uuid,p_payload-'cache_key'
    );
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
END;
$$;

REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB)
  TO service_role;

-- The recovery reader must cover the state added after its original grant
-- migration, plus acknowledgement and policy dependencies needed by a restore.
DO $$
DECLARE name TEXT;
BEGIN
  FOREACH name IN ARRAY ARRAY[
    'portfolio_command_acknowledgements','market_policy_config',
    'portfolio_cash_ledger_state','reconciled_cash_snapshots',
    'market_run_terminal_outcomes','market_collection_checkpoint_history'
  ] LOOP
    EXECUTE format(
      'REVOKE ALL ON public.%I FROM stock_agent_release_reader,stock_agent_release_reader_runtime',
      name
    );
    EXECUTE format('GRANT SELECT ON public.%I TO stock_agent_release_reader',name);
    EXECUTE format('DROP POLICY IF EXISTS release_evidence_select ON public.%I',name);
    EXECUTE format(
      'CREATE POLICY release_evidence_select ON public.%I FOR SELECT TO stock_agent_release_reader USING (true)',
      name
    );
  END LOOP;
END;
$$;

-- Consolidated from sql/migrations/20261001_immutable_history_closure.sql
-- Additive closure of changes previously embedded in immutable 20260907.
-- Cache lineage column already exists from 202609120002; the consolidated
-- controller owns receipt ingestion. Do not replace its final implementation.

CREATE TABLE IF NOT EXISTS public.market_policy_comparisons (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  evaluation_id UUID NOT NULL REFERENCES public.decision_evaluations(id) ON DELETE RESTRICT,
  comparison JSONB NOT NULL CHECK (jsonb_typeof(comparison)='object' AND octet_length(comparison::text)<=16384),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

DROP TRIGGER IF EXISTS market_policy_comparisons_append_only ON public.market_policy_comparisons;
CREATE TRIGGER market_policy_comparisons_append_only BEFORE UPDATE OR DELETE
ON public.market_policy_comparisons FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
ALTER TABLE public.market_policy_comparisons ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_policy_comparisons FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.read_market_evidence_packet(
  p_packet_id UUID,
  p_run_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_result JSONB;
BEGIN
  IF p_packet_id IS NULL OR p_run_id IS NULL THEN
    RAISE EXCEPTION 'packet and run identifiers are required' USING ERRCODE = '22023';
  END IF;

  SELECT jsonb_build_object(
    'id',packet.id,
    'run_id',packet.run_id,
    'packet_hash',packet.packet_hash,
    'packet',packet.packet,
    'evidence_facts',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'candidate_key',candidate->>'candidate_key',
        'evidence_id',item.id,
        'category',CASE
          WHEN item.metadata->>'evidence_category' IN ('quote','fundamentals','technicals','news','event','macro','sector')
            THEN item.metadata->>'evidence_category'
          WHEN item.provider='sec_edgar' THEN 'fundamentals'
          WHEN item.provider IN ('fred','eia','bls','bea') THEN 'macro'
          WHEN item.provider IN ('white_house','doe','dod','federal_register') THEN 'event'
          WHEN item.provider IN ('gdelt','finnhub') THEN 'news'
          WHEN item.provider='yahoo' THEN 'quote'
          ELSE 'unknown' END,
        'source',item.provider,
        'source_status',CASE WHEN receipt.status IN ('succeeded','cache_hit') THEN receipt.status ELSE 'failed' END,
        'authority',CASE
          WHEN item.metadata->>'authority'='official' AND item.provider IN ('sec_edgar','fred','eia','bls','bea','white_house','doe','dod','federal_register') THEN 'official'
          WHEN item.provider IN ('yahoo','alpha_vantage') THEN 'market_data'
          WHEN item.provider IN ('gdelt','finnhub') THEN 'reported'
          ELSE 'unverified' END,
        'published_at',item.published_at,
        'retrieved_at',receipt.retrieved_at,
        'expires_at',receipt.expires_at,
        'reference',item.canonical_url,
        'normalized_text',item.normalized_text,
        'exposure_kind',CASE WHEN item.metadata->>'exposure_kind' IN ('filing','contract','backlog','revenue','capacity','official_fund') THEN item.metadata->>'exposure_kind' ELSE NULL END,
        'relationship_eligible',EXISTS (
          SELECT 1 FROM public.market_candidate_rankings ranking
          WHERE ranking.run_id=packet.run_id AND ranking.candidate_key=candidate->>'candidate_key'
            AND ranking.qualified AND ranking.exposure_item_ids ? item.id::text
        ),
        'claim_key',item.metadata->>'claim_key',
        'claim_polarity',CASE WHEN item.metadata->>'claim_polarity' IN ('affirmed','denied') THEN item.metadata->>'claim_polarity' ELSE NULL END
      ) ORDER BY candidate->>'candidate_key',item.id)
      FROM jsonb_array_elements(packet.packet->'candidates') candidate
      CROSS JOIN LATERAL jsonb_array_elements_text(candidate->'evidence_ids') evidence_id
      JOIN public.market_intelligence_run_items run_item ON run_item.run_id=packet.run_id
        AND run_item.source_item_id=evidence_id::uuid AND run_item.disposition='accepted'
      JOIN public.market_source_items item ON item.id=run_item.source_item_id
      JOIN public.market_source_receipts receipt ON receipt.id=run_item.source_receipt_id
    ),'[]'::jsonb),
    'exposure_facts',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'candidate_key',fact.candidate_key,
        'evidence_id',fact.evidence_id,
        'exposure_kind',fact.exposure_kind,
        'status',fact.freshness,
        'observed_at',fact.observed_at,
        'retrieved_at',fact.retrieved_at
      ) ORDER BY fact.candidate_key,fact.evidence_id)
      FROM (
        SELECT DISTINCT ranking.candidate_key,
          item.id AS evidence_id,
          item.metadata->>'exposure_kind' AS exposure_kind,
          CASE WHEN receipt.expires_at>statement_timestamp()
                    AND COALESCE(item.effective_at,item.published_at) IS NOT NULL
            THEN 'fresh' ELSE 'stale' END AS freshness,
          COALESCE(item.effective_at,item.published_at) AS observed_at,
          receipt.retrieved_at
        FROM public.market_candidate_rankings ranking
        CROSS JOIN LATERAL jsonb_array_elements_text(ranking.exposure_item_ids)
          AS exposure_id(value)
        JOIN public.market_intelligence_run_items run_item
          ON run_item.run_id=ranking.run_id
          AND run_item.source_item_id=exposure_id.value::uuid
          AND run_item.disposition='accepted'
        JOIN public.market_source_items item
          ON item.id=run_item.source_item_id
          AND item.source_receipt_id=run_item.source_receipt_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id
          AND receipt.run_id=ranking.run_id
          AND receipt.status IN ('succeeded','cache_hit')
        WHERE ranking.run_id=packet.run_id AND ranking.qualified
          AND item.metadata->>'authority'='official'
          AND item.metadata->>'exposure_kind' IN (
            'filing','contract','backlog','revenue','capacity','official_fund'
          )
          AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(packet.packet->'candidates') candidate
            WHERE candidate->>'candidate_key'=ranking.candidate_key
              AND candidate->'evidence_ids' ? item.id::text
          )
      ) fact
    ),'[]'::jsonb)
  ) INTO v_result
  FROM public.market_evidence_packets packet
  JOIN public.market_intelligence_run_events event
    ON event.run_id=packet.run_id AND event.status='completed'
  WHERE packet.id=p_packet_id AND packet.run_id=p_run_id AND packet.status='completed';

  IF v_result IS NOT NULL AND octet_length(v_result::text)>131072 THEN
    RAISE EXCEPTION 'packet read exceeds bound' USING ERRCODE = '22023';
  END IF;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.read_market_report_decisions(
  p_run_id UUID, p_packet_id UUID, p_decision_ids JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_result JSONB;
BEGIN
  IF jsonb_typeof(p_decision_ids) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_decision_ids) NOT BETWEEN 1 AND 96 THEN
    RAISE EXCEPTION 'invalid report decision IDs' USING ERRCODE='22023';
  END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'evaluation_id',evaluation.id,'candidate_id',evaluation.candidate_id,
    'run_id',evaluation.run_id,'packet_id',packet.id,'packet_hash',packet.packet_hash,
    'ticker',evaluation.normalized->>'ticker','status',evaluation.policy_status,
    'final_action',evaluation.final_action,
    'final_alert_urgency',CASE WHEN evaluation.policy_status='approved' AND evaluation.final_action='hold'
      THEN evaluation.normalized->'final_alert_urgency' ELSE 'null'::jsonb END,
    'approved_terms',CASE WHEN evaluation.policy_status='approved' AND evaluation.final_action IN ('buy','add','reduce','sell')
      THEN evaluation.normalized->'approved_terms' ELSE 'null'::jsonb END
  ) ORDER BY evaluation.id),'[]'::jsonb) INTO v_result
  FROM public.market_evidence_packets packet
  JOIN public.decision_evaluations evaluation ON evaluation.run_id=packet.run_id
    AND evaluation.analyst->>'packet_id'=packet.id::text
    AND evaluation.policy_version=packet.policy_version
  WHERE packet.id=p_packet_id AND packet.run_id=p_run_id AND packet.status='completed'
    AND p_decision_ids ? evaluation.id::text;
  IF jsonb_array_length(v_result)<>jsonb_array_length(p_decision_ids) THEN
    RAISE EXCEPTION 'report decision provenance mismatch' USING ERRCODE='22023';
  END IF;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_report(
  p_run_id UUID,
  p_idempotency_key TEXT,
  p_report JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_existing public.market_reports%ROWTYPE;
  v_packet public.market_evidence_packets%ROWTYPE;
  v_expected_key TEXT;
  v_expected_id UUID;
BEGIN
  IF p_run_id IS NULL OR p_idempotency_key IS NULL
     OR p_idempotency_key !~ '^[0-9a-f]{64}$' OR jsonb_typeof(p_report)<>'object'
     OR octet_length(p_report::text)>196608
     OR NOT (p_report ?& ARRAY[
       'id','packet_id','market_date','kind','report','report_hash','rendered_text','rendered_hash'
     ])
     OR (p_report - ARRAY[
       'id','packet_id','market_date','kind','report','report_hash','rendered_text','rendered_hash'
     ]) <> '{}'::jsonb
     OR p_report->>'kind' NOT IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')
     OR jsonb_typeof(p_report->'report')<>'object'
     OR octet_length((p_report->'report')::text)>131072
     OR p_report->>'report_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'report_hash' <> encode(extensions.digest(convert_to(
       public.market_canonical_jsonb(p_report->'report'),'UTF8'
     ),'sha256'),'hex')
     OR jsonb_typeof(p_report->'rendered_text')<>'string'
     OR char_length(p_report->>'rendered_text')>14000
     OR p_report->>'rendered_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'rendered_hash' <> encode(extensions.digest(
       convert_to(p_report->>'rendered_text','UTF8'),'sha256'
     ),'hex')
     OR NOT (p_report->'report' ?& ARRAY[
       'source_ids','policy_decision_ids','comparison_ids'
     ])
     OR jsonb_typeof(p_report->'report'->'source_ids') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_report->'report'->'policy_decision_ids') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_report->'report'->'comparison_ids') IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_report->'report'->'source_ids') = 0
     OR jsonb_array_length(p_report->'report'->'policy_decision_ids') = 0
     OR jsonb_array_length(p_report->'report'->'comparison_ids') > 96
     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'source_ids') item
       WHERE item !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'policy_decision_ids') item
       WHERE item !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') THEN
    RAISE EXCEPTION 'invalid market report' USING ERRCODE = '22023';
  END IF;
  SELECT packet.* INTO v_packet
  FROM public.market_evidence_packets packet
  JOIN public.market_intelligence_run_events event
    ON event.run_id=packet.run_id AND event.status='completed'
  WHERE packet.id=(p_report->>'packet_id')::uuid
    AND packet.run_id=p_run_id AND packet.status='completed';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'completed evidence packet unavailable' USING ERRCODE = '22023';
  END IF;
  v_expected_key := encode(extensions.digest(convert_to(
    'v2:' || (p_report->>'kind') || ':' || (p_report->>'market_date') || ':' ||
      v_packet.packet_hash || ':' || (p_report->>'report_hash'),
    'UTF8'
  ), 'sha256'), 'hex');
  v_expected_id := (
    substr(v_expected_key,1,8) || '-' || substr(v_expected_key,9,4) || '-5' ||
    substr(v_expected_key,14,3) || '-8' || substr(v_expected_key,18,3) || '-' ||
    substr(v_expected_key,21,12)
  )::uuid;
  IF p_idempotency_key <> v_expected_key OR p_report->>'id' <> v_expected_id::text
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'source_ids') source_id
       WHERE NOT EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_packet.packet->'evidence') evidence
         JOIN public.market_source_items item ON item.id=(evidence->>'item_id')::uuid
         JOIN public.market_intelligence_run_items run_item ON run_item.source_item_id=item.id
           AND run_item.run_id=p_run_id AND run_item.disposition='accepted'
         JOIN public.market_source_receipts receipt ON receipt.id=run_item.source_receipt_id
           AND receipt.status IN ('succeeded','cache_hit')
         WHERE evidence->>'item_id'=source_id
       )
     )
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'policy_decision_ids') decision_id
       WHERE NOT EXISTS (
         SELECT 1 FROM public.decision_evaluations evaluation
         WHERE evaluation.id=decision_id::uuid AND evaluation.run_id=p_run_id
           AND evaluation.analyst->>'packet_id'=v_packet.id::text
           AND evaluation.policy_version=v_packet.policy_version
       )
     ) OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'comparison_ids') comparison_id
       WHERE NOT EXISTS (
         SELECT 1 FROM public.market_policy_comparisons comparison
         JOIN public.decision_evaluations evaluation ON evaluation.id=comparison.evaluation_id
           AND evaluation.run_id=p_run_id AND evaluation.analyst->>'packet_id'=v_packet.id::text
         WHERE comparison.id=comparison_id::uuid AND comparison.run_id=p_run_id
           AND comparison.packet_id=v_packet.id
           AND p_report->'report'->'policy_decision_ids' ? evaluation.id::text
       )
     ) THEN
    RAISE EXCEPTION 'market report chain mismatch' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-report:' || p_run_id::text || ':' || v_packet.id::text || ':' ||
      (p_report->>'market_date') || ':' || (p_report->>'kind'), 0
  ));
  SELECT * INTO v_existing FROM public.market_reports
  WHERE run_id=p_run_id AND packet_id=v_packet.id
    AND market_date=(p_report->>'market_date')::date AND kind=p_report->>'kind';
  IF FOUND THEN
    IF v_existing.run_id IS DISTINCT FROM p_run_id
       OR v_existing.id IS DISTINCT FROM v_expected_id
       OR v_existing.packet_id IS DISTINCT FROM (p_report->>'packet_id')::uuid
       OR v_existing.report_hash IS DISTINCT FROM p_report->>'report_hash'
       OR v_existing.rendered_text IS DISTINCT FROM p_report->>'rendered_text'
       OR v_existing.rendered_hash IS DISTINCT FROM p_report->>'rendered_hash' THEN
      RAISE EXCEPTION 'market report idempotency mismatch' USING ERRCODE = '22023';
    END IF;
    RETURN jsonb_build_object(
      'report_id',v_existing.id,
      'report_hash',v_existing.report_hash,
      'rendered_hash',v_existing.rendered_hash,
      'duplicate',true
    );
  END IF;
  INSERT INTO public.market_reports(
    id,idempotency_key,run_id,packet_id,market_date,kind,report,report_hash,rendered_text,rendered_hash
  ) VALUES (
    (p_report->>'id')::uuid,p_idempotency_key,p_run_id,v_packet.id,
    (p_report->>'market_date')::date,p_report->>'kind',p_report->'report',
    p_report->>'report_hash',p_report->>'rendered_text',p_report->>'rendered_hash'
  ) RETURNING * INTO v_existing;
  RETURN jsonb_build_object(
    'report_id',v_existing.id,
    'report_hash',v_existing.report_hash,
    'rendered_hash',v_existing.rendered_hash,
    'duplicate',false
  );
END;
$$;

REVOKE ALL ON FUNCTION public.read_market_evidence_packet(UUID, UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.read_market_evidence_packet(UUID, UUID) TO service_role;
REVOKE ALL ON FUNCTION public.read_market_report_decisions(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.read_market_report_decisions(UUID, UUID, JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.record_market_report(UUID, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_report(UUID, TEXT, JSONB) TO service_role;

-- Recovery must preserve policy comparison provenance and its evaluation FK.
REVOKE ALL ON public.decision_evaluations, public.market_policy_comparisons
  FROM stock_agent_release_reader, stock_agent_release_reader_runtime;
GRANT SELECT ON public.decision_evaluations, public.market_policy_comparisons TO stock_agent_release_reader;
DROP POLICY IF EXISTS release_evidence_select ON public.decision_evaluations;
CREATE POLICY release_evidence_select ON public.decision_evaluations
  FOR SELECT TO stock_agent_release_reader USING (true);
DROP POLICY IF EXISTS release_evidence_select ON public.market_policy_comparisons;
CREATE POLICY release_evidence_select ON public.market_policy_comparisons
  FOR SELECT TO stock_agent_release_reader USING (true);

-- Consolidated from sql/migrations/20261002_provider_registry_and_release_ledger_recovery.sql
-- All reviewed non-Yahoo outbound reservations checkpoint before transport.
-- Keep the exact list synchronized with the reviewed Python provider registry.
CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(
  p_run_id UUID,
  p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_window JSONB;
  v_existing public.market_collection_checkpoints%ROWTYPE;
  r JSONB;
  v_reserved public.market_source_quota_reservations%ROWTYPE;
  v_used INT;
  v_existing_receipt JSONB;
  v_existing_uncertain BOOLEAN;
  v_incoming_uncertain BOOLEAN;
  v_same_attempt_identity BOOLEAN;
  v_valid_attempt_transition BOOLEAN;
BEGIN
  IF p_payload IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR NOT(p_payload ?& ARRAY['cache_key','receipt','items'])
     OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb
     OR octet_length(p_payload::text)>65536
     OR jsonb_typeof(p_payload->'items')<>'array'
     OR jsonb_array_length(p_payload->'items')>50 THEN
    RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023';
  END IF;
  SELECT request_window INTO v_window
  FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL
     OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;

  r:=p_payload->'receipt';
  IF r IS NULL OR NOT(r ?& ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])
     OR (r-ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])<>'{}'::jsonb
     OR r->>'status' NOT IN ('succeeded','failed','quota_blocked')
     OR (r->>'request_cost')::int NOT BETWEEN 1 AND 100
     OR r->'cache_predecessor_receipt_id'<>'null'::jsonb
     OR r->>'cache_key'<>p_payload->>'cache_key'
     OR (r->'requested_window'->>'start')::timestamptz<>(v_window->>'start')::timestamptz
     OR (r->'requested_window'->>'end')::timestamptz<>(v_window->>'end')::timestamptz
     OR (r->>'source_receipt_id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
     OR ((r->>'status')='succeeded'
       AND ((r->>'expires_at')::timestamptz<=(r->>'retrieved_at')::timestamptz
         OR r->>'response_hash' !~ '^[0-9a-f]{64}$'))
     OR ((r->>'status')<>'succeeded'
       AND (r->'expires_at'<>'null'::jsonb OR COALESCE(r->>'error_code','')='')) THEN
    RAISE EXCEPTION 'checkpoint must record an actual request outcome' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_reserved
  FROM public.market_source_quota_reservations
  WHERE id=(r->>'reservation_id')::uuid AND run_id=p_run_id AND provider=r->>'provider';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE='22023';
  END IF;
  v_incoming_uncertain := r->>'status'='failed'
    AND r->>'error_code'='TRANSPORT_OUTCOME_UNCERTAIN';
  IF v_incoming_uncertain AND (
      v_reserved.provider NOT IN ('gdelt','alpha_vantage','finnhub','sec_edgar','federal_register','white_house','doe','dod','eia','fred','bls','bea')
      OR jsonb_array_length(p_payload->'items')<>0
      OR r->'observed_at'<>'null'::jsonb
      OR r->'expires_at'<>'null'::jsonb
      OR r->'upstream_remaining'<>'null'::jsonb
      OR r->'response_hash'<>'null'::jsonb
      OR (r->>'returned_count')::int<>0
      OR (r->>'accepted_count')::int<>0
      OR (r->>'duplicate_count')::int<>0
      OR (r->>'dropped_count')::int<>0
  ) THEN
    RAISE EXCEPTION 'invalid provider attempt barrier' USING ERRCODE='22023';
  END IF;

  SELECT * INTO v_existing
  FROM public.market_collection_checkpoints
  WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key' FOR UPDATE;
  IF FOUND AND v_existing.payload=p_payload-'cache_key' THEN
    RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
  END IF;
  SELECT COALESCE(sum((payload->'receipt'->>'request_cost')::int),0) INTO v_used FROM (
    SELECT payload FROM public.market_collection_checkpoints WHERE run_id=p_run_id
    UNION ALL SELECT payload FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id
  ) paid WHERE payload->'receipt'->>'reservation_id'=r->>'reservation_id';
  IF v_existing.run_id IS NOT NULL THEN
    v_existing_receipt := v_existing.payload->'receipt';
    v_existing_uncertain := v_existing_receipt->>'status'='failed'
      AND v_existing_receipt->>'error_code'='TRANSPORT_OUTCOME_UNCERTAIN';
    v_same_attempt_identity := v_existing.source_receipt_id::text=r->>'source_receipt_id'
      AND v_existing_receipt->>'provider'=r->>'provider'
      AND v_existing_receipt->>'reservation_id'=r->>'reservation_id'
      AND v_existing_receipt->>'cache_key'=r->>'cache_key'
      AND v_existing_receipt->'requested_window'=r->'requested_window'
      AND v_existing_receipt->>'requested_limit'=r->>'requested_limit';
    v_valid_attempt_transition := v_existing_uncertain AND v_same_attempt_identity AND (
      (v_incoming_uncertain
       AND (r->>'request_cost')::int=(v_existing_receipt->>'request_cost')::int+1)
      OR (NOT v_incoming_uncertain
       AND (r->>'request_cost')::int=(v_existing_receipt->>'request_cost')::int)
    );
    IF v_valid_attempt_transition THEN
      IF v_used-(v_existing_receipt->>'request_cost')::int+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
        RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
      END IF;
      -- Never archive an attempt barrier: history plus its terminal row would
      -- count the same provider request twice during restart reconciliation.
      UPDATE public.market_collection_checkpoints
      SET payload=p_payload-'cache_key',created_at=statement_timestamp()
      WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
    ELSE
      IF v_existing.source_receipt_id=(r->>'source_receipt_id')::uuid
         OR (v_existing_receipt->>'expires_at')::timestamptz>statement_timestamp() THEN
        RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023';
      END IF;
      IF v_used+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
        RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
      END IF;
      INSERT INTO public.market_collection_checkpoint_history(
        run_id,cache_key,source_receipt_id,payload
      ) VALUES(
        v_existing.run_id,v_existing.cache_key,v_existing.source_receipt_id,v_existing.payload
      );
      UPDATE public.market_collection_checkpoints
      SET source_receipt_id=(r->>'source_receipt_id')::uuid,
          payload=p_payload-'cache_key',created_at=statement_timestamp()
      WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
    END IF;
  ELSE
    IF v_used+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
      RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
    END IF;
    INSERT INTO public.market_collection_checkpoints(
      run_id,cache_key,request_window,source_receipt_id,payload
    ) VALUES(
      p_run_id,p_payload->>'cache_key',v_window,
      (r->>'source_receipt_id')::uuid,p_payload-'cache_key'
    );
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
END;
$$;

REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB)
  TO service_role;


-- Preserve the private release ledger separately from Supabase's native prefix.
-- Match the protected release reconciler's existing four-column table exactly.
CREATE TABLE IF NOT EXISTS public.stock_agent_release_migration_ledger (
  path TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
  UNIQUE (version, path)
);
ALTER TABLE public.stock_agent_release_migration_ledger ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.stock_agent_release_migration_ledger
  FROM PUBLIC,anon,authenticated,stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT SELECT ON public.stock_agent_release_migration_ledger TO stock_agent_release_reader;
DROP POLICY IF EXISTS release_evidence_select ON public.stock_agent_release_migration_ledger;
CREATE POLICY release_evidence_select ON public.stock_agent_release_migration_ledger
  FOR SELECT TO stock_agent_release_reader USING (true);

-- Consolidated from sql/migrations/20261003_release_ledger_acl_closure.sql
-- Supabase's public default privileges can grant service_role direct ledger
-- writes. Application credentials must never manufacture deployment receipts.
REVOKE ALL ON TABLE public.stock_agent_release_migration_ledger FROM service_role;

-- Consolidated from sql/migrations/20261005_market_wide_discovery.sql
-- Durable, bounded reference and discovery-stage ledgers.  These records are
-- research provenance only; none grants portfolio, alert, or execution authority.

CREATE TABLE IF NOT EXISTS public.market_reference_manifests (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  reference_version TEXT NOT NULL CHECK (reference_version ~ '^[a-z0-9][a-z0-9:._-]{0,127}$'),
  revision INT NOT NULL CHECK (revision BETWEEN 1 AND 10000),
  capability_version INT NOT NULL CHECK (capability_version BETWEEN 1 AND 10000),
  taxonomy_version INT NOT NULL CHECK (taxonomy_version BETWEEN 1 AND 10000),
  source_hash TEXT NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  manifest JSONB NOT NULL CHECK (jsonb_typeof(manifest)='object' AND octet_length(manifest::text)<=65536),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CHECK (valid_to IS NULL OR valid_to>valid_from),
  UNIQUE (reference_version, revision),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_security_reference_revisions (
  id UUID PRIMARY KEY,
  manifest_id UUID NOT NULL REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  revision INT NOT NULL CHECK (revision BETWEEN 1 AND 10000),
  security_id TEXT NOT NULL CHECK (security_id ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'),
  entity_id TEXT NOT NULL CHECK (entity_id ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'),
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9.-]{0,14}$'),
  exchange TEXT CHECK (exchange IS NULL OR exchange ~ '^[A-Z][A-Z0-9._-]{0,31}$'),
  instrument_type TEXT NOT NULL CHECK (instrument_type IN ('COMMON_STOCK','ADR','ETF','PREFERRED','WARRANT','OTC_COMMON','OTHER')),
  eligible BOOLEAN NOT NULL,
  exclusion_reasons JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(exclusion_reasons)='array' AND jsonb_array_length(exclusion_reasons)<=16 AND octet_length(exclusion_reasons::text)<=4096),
  aliases JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(aliases)='array' AND jsonb_array_length(aliases)<=32 AND octet_length(aliases::text)<=4096),
  source_ids JSONB NOT NULL CHECK (jsonb_typeof(source_ids)='array' AND jsonb_array_length(source_ids) BETWEEN 1 AND 16 AND octet_length(source_ids::text)<=4096),
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CHECK (valid_to IS NULL OR valid_to>valid_from),
  UNIQUE (manifest_id, security_id, revision),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_discovery_stage_tasks (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  stage TEXT NOT NULL CHECK (stage IN ('reference','signals','resolve','enrich','screen','quote')),
  capability_id TEXT NOT NULL CHECK (capability_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  provider TEXT NOT NULL CHECK (provider IN ('gdelt','alpha_vantage','finnhub','yahoo','sec_edgar','federal_register','white_house','doe','dod','eia','fred','bls','bea','social')),
  query_kind TEXT NOT NULL CHECK (query_kind IN ('feed','theme_search','issuer_submissions','filing_document','series','screener','quote','universe')),
  query_hash TEXT NOT NULL CHECK (query_hash ~ '^[0-9a-f]{64}$'),
  dependency_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(dependency_ids)='array' AND jsonb_array_length(dependency_ids)<=32 AND octet_length(dependency_ids::text)<=2048),
  requested_window JSONB NOT NULL CHECK (jsonb_typeof(requested_window)='object' AND octet_length(requested_window::text)<=2048),
  state TEXT NOT NULL CHECK (state IN ('planned','attempting','succeeded','failed','deferred','uncertain')),
  attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 10),
  request_budget INT NOT NULL CHECK (request_budget BETWEEN 0 AND 100),
  result JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(result)='object' AND octet_length(result::text)<=65536),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, stage, query_hash)
);

CREATE TABLE IF NOT EXISTS public.market_theme_episode_revisions (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  task_id UUID NOT NULL REFERENCES public.market_discovery_stage_tasks(id) ON DELETE RESTRICT,
  theme_id TEXT NOT NULL CHECK (theme_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  revision INT NOT NULL CHECK (revision BETWEEN 1 AND 10000),
  episode JSONB NOT NULL CHECK (jsonb_typeof(episode)='object' AND octet_length(episode::text)<=32768),
  source_ids JSONB NOT NULL CHECK (jsonb_typeof(source_ids)='array' AND jsonb_array_length(source_ids) BETWEEN 1 AND 64 AND octet_length(source_ids::text)<=8192),
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CHECK (valid_to IS NULL OR valid_to>valid_from),
  UNIQUE (run_id, theme_id, revision),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_exposure_facts (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  task_id UUID NOT NULL REFERENCES public.market_discovery_stage_tasks(id) ON DELETE RESTRICT,
  security_revision_id UUID NOT NULL REFERENCES public.market_security_reference_revisions(id) ON DELETE RESTRICT,
  theme_episode_revision_id UUID REFERENCES public.market_theme_episode_revisions(id) ON DELETE RESTRICT,
  exposure_kind TEXT NOT NULL CHECK (exposure_kind IN ('filing','contract','backlog','revenue','capacity','official_fund','supply_chain','customer','segment')),
  fact JSONB NOT NULL CHECK (jsonb_typeof(fact)='object' AND octet_length(fact::text)<=32768),
  source_ids JSONB NOT NULL CHECK (jsonb_typeof(source_ids)='array' AND jsonb_array_length(source_ids) BETWEEN 1 AND 64 AND octet_length(source_ids::text)<=8192),
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CHECK (valid_to IS NULL OR valid_to>valid_from),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_research_nominations (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  task_id UUID NOT NULL REFERENCES public.market_discovery_stage_tasks(id) ON DELETE RESTRICT,
  security_revision_id UUID NOT NULL REFERENCES public.market_security_reference_revisions(id) ON DELETE RESTRICT,
  theme_episode_revision_id UUID REFERENCES public.market_theme_episode_revisions(id) ON DELETE RESTRICT,
  exposure_fact_ids JSONB NOT NULL CHECK (jsonb_typeof(exposure_fact_ids)='array' AND jsonb_array_length(exposure_fact_ids) BETWEEN 1 AND 32 AND octet_length(exposure_fact_ids::text)<=2048),
  state TEXT NOT NULL CHECK (state IN ('nominated','researching','accepted','rejected','deferred')),
  rationale JSONB NOT NULL CHECK (jsonb_typeof(rationale)='object' AND octet_length(rationale::text)<=16384),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, security_revision_id, task_id)
);

CREATE OR REPLACE FUNCTION public.reject_market_discovery_mutation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'market discovery revision records are append-only' USING ERRCODE='55000';
END;
$$;

CREATE OR REPLACE FUNCTION public.enforce_market_discovery_stage_task_transition()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'market discovery task deletion is forbidden' USING ERRCODE='55000';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.stage IS DISTINCT FROM OLD.stage OR NEW.capability_id IS DISTINCT FROM OLD.capability_id
     OR NEW.provider IS DISTINCT FROM OLD.provider OR NEW.query_kind IS DISTINCT FROM OLD.query_kind
     OR NEW.query_hash IS DISTINCT FROM OLD.query_hash OR NEW.dependency_ids IS DISTINCT FROM OLD.dependency_ids
     OR NEW.requested_window IS DISTINCT FROM OLD.requested_window
     OR NEW.request_budget IS DISTINCT FROM OLD.request_budget OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'discovery task identity mismatch' USING ERRCODE='22023';
  END IF;
  IF NOT (
    (OLD.state='planned' AND NEW.state='attempting' AND NEW.attempt_count=OLD.attempt_count+1 AND NEW.result=OLD.result)
    OR (OLD.state='attempting' AND NEW.state IN ('succeeded','failed','deferred','uncertain') AND NEW.attempt_count=OLD.attempt_count)
  ) OR NEW.updated_at<=OLD.updated_at THEN
    RAISE EXCEPTION 'invalid discovery task state transition' USING ERRCODE='22023';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.enforce_market_research_nomination_transition()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'market research nomination deletion is forbidden' USING ERRCODE='55000';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.task_id IS DISTINCT FROM OLD.task_id
     OR NEW.security_revision_id IS DISTINCT FROM OLD.security_revision_id
     OR NEW.theme_episode_revision_id IS DISTINCT FROM OLD.theme_episode_revision_id
     OR NEW.exposure_fact_ids IS DISTINCT FROM OLD.exposure_fact_ids
     OR NEW.rationale IS DISTINCT FROM OLD.rationale OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'research nomination identity mismatch' USING ERRCODE='22023';
  END IF;
  IF NOT (
    (OLD.state='nominated' AND NEW.state IN ('researching','rejected','deferred'))
    OR (OLD.state='researching' AND NEW.state IN ('accepted','rejected','deferred'))
  ) OR NEW.updated_at<=OLD.updated_at THEN
    RAISE EXCEPTION 'invalid research nomination state transition' USING ERRCODE='22023';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS market_reference_manifests_append_only ON public.market_reference_manifests;
CREATE TRIGGER market_reference_manifests_append_only BEFORE UPDATE OR DELETE ON public.market_reference_manifests
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_security_reference_revisions_append_only ON public.market_security_reference_revisions;
CREATE TRIGGER market_security_reference_revisions_append_only BEFORE UPDATE OR DELETE ON public.market_security_reference_revisions
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_theme_episode_revisions_append_only ON public.market_theme_episode_revisions;
CREATE TRIGGER market_theme_episode_revisions_append_only BEFORE UPDATE OR DELETE ON public.market_theme_episode_revisions
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_exposure_facts_append_only ON public.market_exposure_facts;
CREATE TRIGGER market_exposure_facts_append_only BEFORE UPDATE OR DELETE ON public.market_exposure_facts
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_discovery_stage_tasks_transition_guard ON public.market_discovery_stage_tasks;
CREATE TRIGGER market_discovery_stage_tasks_transition_guard BEFORE UPDATE OR DELETE ON public.market_discovery_stage_tasks
FOR EACH ROW EXECUTE FUNCTION public.enforce_market_discovery_stage_task_transition();
DROP TRIGGER IF EXISTS market_research_nominations_transition_guard ON public.market_research_nominations;
CREATE TRIGGER market_research_nominations_transition_guard BEFORE UPDATE OR DELETE ON public.market_research_nominations
FOR EACH ROW EXECUTE FUNCTION public.enforce_market_research_nomination_transition();

CREATE OR REPLACE FUNCTION public.record_market_discovery_reference(p_run_id UUID, p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE m JSONB; r JSONB;
  v_existing_manifest public.market_reference_manifests%ROWTYPE;
  v_existing_security public.market_security_reference_revisions%ROWTYPE;
  v_count INT:=0; v_duplicate BOOLEAN:=true;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>1048576
     OR NOT(p_payload ?& ARRAY['manifest','security_revisions'])
     OR (p_payload-ARRAY['manifest','security_revisions'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'security_revisions')<>'array'
     OR jsonb_array_length(p_payload->'security_revisions')>15000 THEN
    RAISE EXCEPTION 'invalid discovery reference payload' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'security_revisions') child
    GROUP BY (child->>'id')::uuid HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate security revision id' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
                WHERE i.id=p_run_id AND a.status='running') THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  m:=p_payload->'manifest';
  IF jsonb_typeof(m)<>'object' OR NOT(m ?& ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])
     OR (m-ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])<>'{}'::jsonb
     OR m->>'source_hash' !~ '^[0-9a-f]{64}$' OR m->>'content_hash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(m->'manifest')<>'object' OR octet_length((m->'manifest')::text)>65536 THEN
    RAISE EXCEPTION 'invalid discovery reference manifest' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing_manifest FROM public.market_reference_manifests WHERE id=(m->>'id')::uuid;
  IF FOUND THEN
    IF v_existing_manifest.run_id<>p_run_id
       OR v_existing_manifest.reference_version<>m->>'reference_version'
       OR v_existing_manifest.revision<>(m->>'revision')::int
       OR v_existing_manifest.capability_version<>(m->>'capability_version')::int
       OR v_existing_manifest.taxonomy_version<>(m->>'taxonomy_version')::int
       OR v_existing_manifest.source_hash<>m->>'source_hash'
       OR v_existing_manifest.valid_from<>(m->>'valid_from')::timestamptz
       OR v_existing_manifest.valid_to IS DISTINCT FROM (m->>'valid_to')::timestamptz
       OR v_existing_manifest.manifest<>m->'manifest'
       OR v_existing_manifest.content_hash<>m->>'content_hash' THEN
      RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    INSERT INTO public.market_reference_manifests(id,run_id,reference_version,revision,capability_version,taxonomy_version,source_hash,valid_from,valid_to,manifest,content_hash)
    VALUES((m->>'id')::uuid,p_run_id,m->>'reference_version',(m->>'revision')::int,(m->>'capability_version')::int,(m->>'taxonomy_version')::int,m->>'source_hash',(m->>'valid_from')::timestamptz,(m->>'valid_to')::timestamptz,m->'manifest',m->>'content_hash');
    v_duplicate:=false;
  END IF;
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'security_revisions') LOOP
    IF jsonb_typeof(r)<>'object' OR NOT(r ?& ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])
       OR (r-ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])<>'{}'::jsonb
       OR r->>'manifest_id'<>m->>'id' OR r->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(r->'source_ids')<>'array' OR jsonb_array_length(r->'source_ids') NOT BETWEEN 1 AND 16 THEN
      RAISE EXCEPTION 'invalid security reference revision' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_existing_security FROM public.market_security_reference_revisions WHERE id=(r->>'id')::uuid;
    IF FOUND THEN
      IF v_existing_security.manifest_id<>(r->>'manifest_id')::uuid
         OR v_existing_security.run_id<>p_run_id
         OR v_existing_security.revision<>(r->>'revision')::int
         OR v_existing_security.security_id<>r->>'security_id'
         OR v_existing_security.entity_id<>r->>'entity_id'
         OR v_existing_security.ticker<>r->>'ticker'
         OR v_existing_security.exchange IS DISTINCT FROM r->>'exchange'
         OR v_existing_security.instrument_type<>r->>'instrument_type'
         OR v_existing_security.eligible<>(r->>'eligible')::boolean
         OR v_existing_security.exclusion_reasons<>r->'exclusion_reasons'
         OR v_existing_security.aliases<>r->'aliases'
         OR v_existing_security.source_ids<>r->'source_ids'
         OR v_existing_security.valid_from<>(r->>'valid_from')::timestamptz
         OR v_existing_security.valid_to IS DISTINCT FROM (r->>'valid_to')::timestamptz
         OR v_existing_security.content_hash<>r->>'content_hash' THEN
        RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
      END IF;
    ELSE
      IF v_duplicate THEN
        RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
      END IF;
      INSERT INTO public.market_security_reference_revisions(id,manifest_id,run_id,revision,security_id,entity_id,ticker,exchange,instrument_type,eligible,exclusion_reasons,aliases,source_ids,valid_from,valid_to,content_hash)
      VALUES((r->>'id')::uuid,(r->>'manifest_id')::uuid,p_run_id,(r->>'revision')::int,r->>'security_id',r->>'entity_id',r->>'ticker',r->>'exchange',r->>'instrument_type',(r->>'eligible')::boolean,r->'exclusion_reasons',r->'aliases',r->'source_ids',(r->>'valid_from')::timestamptz,(r->>'valid_to')::timestamptz,r->>'content_hash');
    END IF;
    v_count:=v_count+1;
  END LOOP;
  IF v_duplicate AND (
    SELECT count(*) FROM public.market_security_reference_revisions
    WHERE manifest_id=(m->>'id')::uuid
  )<>v_count THEN
    RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
  END IF;
  RETURN jsonb_build_object('manifest_id',m->>'id','security_revision_count',v_count,'duplicate',v_duplicate);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid discovery reference payload' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.checkpoint_market_discovery_stage(p_run_id UUID, p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t JSONB; r JSONB; v_existing public.market_discovery_stage_tasks%ROWTYPE; v_duplicate BOOLEAN:=false;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>262144
     OR NOT(p_payload ?& ARRAY['task','exposure_facts','theme_episode_revisions','research_nominations'])
     OR (p_payload-ARRAY['task','exposure_facts','theme_episode_revisions','research_nominations'])<>'{}'::jsonb
     OR EXISTS(SELECT 1 FROM jsonb_each(p_payload) item WHERE item.key<>'task' AND jsonb_typeof(item.value)<>'array')
     OR jsonb_array_length(p_payload->'exposure_facts')>100
     OR jsonb_array_length(p_payload->'theme_episode_revisions')>50
     OR jsonb_array_length(p_payload->'research_nominations')>50 THEN
    RAISE EXCEPTION 'invalid discovery stage checkpoint' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM (
      SELECT (child->>'id')::uuid AS id FROM jsonb_array_elements(p_payload->'exposure_facts') child
      GROUP BY (child->>'id')::uuid HAVING count(*)>1
      UNION ALL
      SELECT (child->>'id')::uuid AS id FROM jsonb_array_elements(p_payload->'theme_episode_revisions') child
      GROUP BY (child->>'id')::uuid HAVING count(*)>1
      UNION ALL
      SELECT (child->>'id')::uuid AS id FROM jsonb_array_elements(p_payload->'research_nominations') child
      GROUP BY (child->>'id')::uuid HAVING count(*)>1
    ) duplicated_child
  ) THEN
    RAISE EXCEPTION 'duplicate result child id' USING ERRCODE='22023';
  END IF;
  t:=p_payload->'task';
  IF jsonb_typeof(t)<>'object'
     OR NOT(t ?& ARRAY['id','stage','provider','capability_id','query_kind','query_hash','dependency_ids','requested_window','state','attempt_count','request_budget','result'])
     OR (t-ARRAY['id','stage','provider','capability_id','query_kind','query_hash','dependency_ids','requested_window','state','attempt_count','request_budget','result'])<>'{}'::jsonb
     OR t->>'query_hash' !~ '^[0-9a-f]{64}$' OR jsonb_typeof(t->'dependency_ids')<>'array'
     OR jsonb_array_length(t->'dependency_ids')>32 OR jsonb_typeof(t->'requested_window')<>'object'
     OR jsonb_typeof(t->'result')<>'object' OR octet_length((t->'result')::text)>65536 THEN
    RAISE EXCEPTION 'invalid discovery stage task' USING ERRCODE='22023';
  END IF;
  PERFORM 1 FROM public.analysis_runs a
  JOIN public.market_intelligence_runs i ON i.id=a.id
  WHERE i.id=p_run_id AND a.status='running'
  FOR UPDATE OF a;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(t->'dependency_ids') dependency
            WHERE dependency !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
               OR NOT EXISTS(SELECT 1 FROM public.market_discovery_stage_tasks d WHERE d.id=dependency::uuid AND d.run_id=p_run_id AND d.state='succeeded')) THEN
    RAISE EXCEPTION 'discovery task dependency mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_discovery_stage_tasks WHERE id=(t->>'id')::uuid FOR UPDATE;
  IF NOT FOUND THEN
    IF t->>'state'<>'planned' OR (t->>'attempt_count')::int<>0 OR t->'result'<>'{}'::jsonb
       OR jsonb_array_length(p_payload->'exposure_facts')<>0 OR jsonb_array_length(p_payload->'theme_episode_revisions')<>0 OR jsonb_array_length(p_payload->'research_nominations')<>0 THEN
      RAISE EXCEPTION 'new discovery task must be planned' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.market_discovery_stage_tasks(id,run_id,stage,capability_id,provider,query_kind,query_hash,dependency_ids,requested_window,state,attempt_count,request_budget,result)
    VALUES((t->>'id')::uuid,p_run_id,t->>'stage',t->>'capability_id',t->>'provider',t->>'query_kind',t->>'query_hash',t->'dependency_ids',t->'requested_window',t->>'state',(t->>'attempt_count')::int,(t->>'request_budget')::int,t->'result');
  ELSE
    IF v_existing.run_id<>p_run_id OR v_existing.stage<>t->>'stage' OR v_existing.capability_id<>t->>'capability_id'
       OR v_existing.provider<>t->>'provider' OR v_existing.query_kind<>t->>'query_kind'
       OR v_existing.query_hash<>t->>'query_hash' OR v_existing.dependency_ids<>t->'dependency_ids'
       OR v_existing.requested_window<>t->'requested_window' OR v_existing.request_budget<>(t->>'request_budget')::int THEN
      RAISE EXCEPTION 'discovery task identity mismatch' USING ERRCODE='22023';
    END IF;
    IF v_existing.state=t->>'state' AND v_existing.attempt_count=(t->>'attempt_count')::int AND v_existing.result=t->'result' THEN
      v_duplicate:=true;
    ELSE
      UPDATE public.market_discovery_stage_tasks SET state=t->>'state',attempt_count=(t->>'attempt_count')::int,result=t->'result',updated_at=statement_timestamp()
      WHERE id=v_existing.id;
    END IF;
  END IF;
  IF t->>'state'='succeeded' THEN
    IF (jsonb_array_length(p_payload->'theme_episode_revisions')>0 AND t->>'stage'<>'signals')
       OR (jsonb_array_length(p_payload->'exposure_facts')>0 AND t->>'stage'<>'enrich')
       OR (jsonb_array_length(p_payload->'research_nominations')>0 AND t->>'stage'<>'screen') THEN
      RAISE EXCEPTION 'discovery result does not match stage' USING ERRCODE='22023';
    END IF;
    FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'theme_episode_revisions') LOOP
      IF v_duplicate THEN
        IF NOT EXISTS(
          SELECT 1 FROM public.market_theme_episode_revisions e
          WHERE e.id=(r->>'id')::uuid AND e.run_id=p_run_id AND e.task_id=(t->>'id')::uuid
            AND e.theme_id=r->>'theme_id' AND e.revision=(r->>'revision')::int
            AND e.episode=r->'episode' AND e.source_ids=r->'source_ids'
            AND e.valid_from=(r->>'valid_from')::timestamptz
            AND e.valid_to IS NOT DISTINCT FROM (r->>'valid_to')::timestamptz
            AND e.content_hash=r->>'content_hash'
        ) THEN
          RAISE EXCEPTION 'discovery stage idempotency mismatch' USING ERRCODE='22023';
        END IF;
      ELSE
        INSERT INTO public.market_theme_episode_revisions(id,run_id,task_id,theme_id,revision,episode,source_ids,valid_from,valid_to,content_hash)
        VALUES((r->>'id')::uuid,p_run_id,(t->>'id')::uuid,r->>'theme_id',(r->>'revision')::int,r->'episode',r->'source_ids',(r->>'valid_from')::timestamptz,(r->>'valid_to')::timestamptz,r->>'content_hash');
      END IF;
    END LOOP;
    FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'exposure_facts') LOOP
      IF NOT EXISTS(
           SELECT 1 FROM public.market_security_reference_revisions s
           WHERE s.id=(r->>'security_revision_id')::uuid AND s.run_id=p_run_id
         ) OR (
           r->>'theme_episode_revision_id' IS NOT NULL AND NOT EXISTS(
             SELECT 1 FROM public.market_theme_episode_revisions e
             WHERE e.id=(r->>'theme_episode_revision_id')::uuid AND e.run_id=p_run_id
           )
         ) THEN
        RAISE EXCEPTION 'discovery result lineage mismatch' USING ERRCODE='22023';
      END IF;
      IF v_duplicate THEN
        IF NOT EXISTS(
          SELECT 1 FROM public.market_exposure_facts f
          WHERE f.id=(r->>'id')::uuid AND f.run_id=p_run_id AND f.task_id=(t->>'id')::uuid
            AND f.security_revision_id=(r->>'security_revision_id')::uuid
            AND f.theme_episode_revision_id IS NOT DISTINCT FROM (r->>'theme_episode_revision_id')::uuid
            AND f.exposure_kind=r->>'exposure_kind' AND f.fact=r->'fact' AND f.source_ids=r->'source_ids'
            AND f.valid_from=(r->>'valid_from')::timestamptz
            AND f.valid_to IS NOT DISTINCT FROM (r->>'valid_to')::timestamptz
            AND f.content_hash=r->>'content_hash'
        ) THEN
          RAISE EXCEPTION 'discovery stage idempotency mismatch' USING ERRCODE='22023';
        END IF;
      ELSE
        INSERT INTO public.market_exposure_facts(id,run_id,task_id,security_revision_id,theme_episode_revision_id,exposure_kind,fact,source_ids,valid_from,valid_to,content_hash)
        VALUES((r->>'id')::uuid,p_run_id,(t->>'id')::uuid,(r->>'security_revision_id')::uuid,(r->>'theme_episode_revision_id')::uuid,r->>'exposure_kind',r->'fact',r->'source_ids',(r->>'valid_from')::timestamptz,(r->>'valid_to')::timestamptz,r->>'content_hash');
      END IF;
    END LOOP;
    FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'research_nominations') LOOP
      IF jsonb_typeof(r->'exposure_fact_ids')<>'array'
         OR jsonb_array_length(r->'exposure_fact_ids') NOT BETWEEN 1 AND 32 THEN
        RAISE EXCEPTION 'discovery result lineage mismatch' USING ERRCODE='22023';
      END IF;
      IF EXISTS(
        SELECT 1 FROM jsonb_array_elements_text(r->'exposure_fact_ids') ids(fact_id)
        GROUP BY fact_id::uuid HAVING count(*)>1
      ) THEN
        RAISE EXCEPTION 'duplicate exposure fact id' USING ERRCODE='22023';
      END IF;
      IF NOT EXISTS(
           SELECT 1 FROM public.market_security_reference_revisions s
           WHERE s.id=(r->>'security_revision_id')::uuid AND s.run_id=p_run_id
         ) OR (
           r->>'theme_episode_revision_id' IS NOT NULL AND NOT EXISTS(
             SELECT 1 FROM public.market_theme_episode_revisions e
             WHERE e.id=(r->>'theme_episode_revision_id')::uuid AND e.run_id=p_run_id
           )
         ) OR EXISTS(
           SELECT 1 FROM jsonb_array_elements_text(r->'exposure_fact_ids') fact_id
           WHERE fact_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
              OR NOT EXISTS(
                SELECT 1 FROM public.market_exposure_facts f
                WHERE f.id=fact_id::uuid AND f.run_id=p_run_id
              )
         ) THEN
        RAISE EXCEPTION 'discovery result lineage mismatch' USING ERRCODE='22023';
      END IF;
      IF v_duplicate THEN
        IF NOT EXISTS(
          SELECT 1 FROM public.market_research_nominations n
          WHERE n.id=(r->>'id')::uuid AND n.run_id=p_run_id AND n.task_id=(t->>'id')::uuid
            AND n.security_revision_id=(r->>'security_revision_id')::uuid
            AND n.theme_episode_revision_id IS NOT DISTINCT FROM (r->>'theme_episode_revision_id')::uuid
            AND n.exposure_fact_ids=r->'exposure_fact_ids' AND n.state=r->>'state' AND n.rationale=r->'rationale'
        ) THEN
          RAISE EXCEPTION 'discovery stage idempotency mismatch' USING ERRCODE='22023';
        END IF;
      ELSE
        INSERT INTO public.market_research_nominations(id,run_id,task_id,security_revision_id,theme_episode_revision_id,exposure_fact_ids,state,rationale)
        VALUES((r->>'id')::uuid,p_run_id,(t->>'id')::uuid,(r->>'security_revision_id')::uuid,(r->>'theme_episode_revision_id')::uuid,r->'exposure_fact_ids',r->>'state',r->'rationale');
      END IF;
    END LOOP;
    IF v_duplicate AND (
      (SELECT count(*) FROM public.market_theme_episode_revisions WHERE task_id=(t->>'id')::uuid)<>jsonb_array_length(p_payload->'theme_episode_revisions')
      OR (SELECT count(*) FROM public.market_exposure_facts WHERE task_id=(t->>'id')::uuid)<>jsonb_array_length(p_payload->'exposure_facts')
      OR (SELECT count(*) FROM public.market_research_nominations WHERE task_id=(t->>'id')::uuid)<>jsonb_array_length(p_payload->'research_nominations')
    ) THEN
      RAISE EXCEPTION 'discovery stage idempotency mismatch' USING ERRCODE='22023';
    END IF;
  ELSIF jsonb_array_length(p_payload->'exposure_facts')<>0 OR jsonb_array_length(p_payload->'theme_episode_revisions')<>0 OR jsonb_array_length(p_payload->'research_nominations')<>0 THEN
    RAISE EXCEPTION 'non-success discovery checkpoint has result rows' USING ERRCODE='22023';
  END IF;
  RETURN jsonb_build_object('task',to_jsonb((SELECT d FROM public.market_discovery_stage_tasks d WHERE d.id=(t->>'id')::uuid))-'run_id'-'created_at'-'updated_at','duplicate',v_duplicate);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid discovery stage checkpoint' USING ERRCODE='22023';
END;
$$;

REVOKE ALL ON FUNCTION public.reject_market_discovery_mutation() FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.enforce_market_discovery_stage_task_transition() FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.enforce_market_research_nomination_transition() FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.read_market_discovery_context(p_run_id UUID, p_limit INT DEFAULT 100)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB;
BEGIN
  IF p_run_id IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid discovery context request' USING ERRCODE='22023';
  END IF;
  SELECT jsonb_build_object(
    'manifests',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC) FROM (SELECT id,reference_version,revision,capability_version,taxonomy_version,source_hash,valid_from,valid_to,manifest,content_hash,created_at FROM public.market_reference_manifests WHERE run_id=p_run_id ORDER BY created_at DESC LIMIT p_limit) x),'[]'::jsonb),
    'security_revisions',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC) FROM (SELECT id,manifest_id,revision,security_id,entity_id,ticker,exchange,instrument_type,eligible,exclusion_reasons,aliases,source_ids,valid_from,valid_to,content_hash,created_at FROM public.market_security_reference_revisions WHERE run_id=p_run_id ORDER BY created_at DESC LIMIT p_limit) x),'[]'::jsonb),
    'tasks',COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT id,stage,capability_id,provider,query_kind,query_hash,dependency_ids,requested_window,state,attempt_count,request_budget,result FROM public.market_discovery_stage_tasks WHERE run_id=p_run_id ORDER BY created_at LIMIT p_limit) x),'[]'::jsonb),
    'theme_episodes',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC) FROM (SELECT id,task_id,theme_id,revision,episode,source_ids,valid_from,valid_to,content_hash,created_at FROM public.market_theme_episode_revisions WHERE run_id=p_run_id ORDER BY created_at DESC LIMIT p_limit) x),'[]'::jsonb),
    'exposure_facts',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC) FROM (SELECT id,task_id,security_revision_id,theme_episode_revision_id,exposure_kind,fact,source_ids,valid_from,valid_to,content_hash,created_at FROM public.market_exposure_facts WHERE run_id=p_run_id ORDER BY created_at DESC LIMIT p_limit) x),'[]'::jsonb),
    'research_nominations',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC) FROM (SELECT id,task_id,security_revision_id,theme_episode_revision_id,exposure_fact_ids,state,rationale,created_at,updated_at FROM public.market_research_nominations WHERE run_id=p_run_id ORDER BY created_at DESC LIMIT p_limit) x),'[]'::jsonb)
  ) INTO v_result;
  IF octet_length(v_result::text)>1048576 THEN
    RAISE EXCEPTION 'discovery context exceeds bound' USING ERRCODE='54000';
  END IF;
  RETURN v_result;
END;
$$;

ALTER TABLE public.market_reference_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_security_reference_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_discovery_stage_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_exposure_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_theme_episode_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_research_nominations ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.market_reference_manifests,public.market_security_reference_revisions,
  public.market_discovery_stage_tasks,public.market_exposure_facts,
  public.market_theme_episode_revisions,public.market_research_nominations
FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;

REVOKE ALL ON FUNCTION public.record_market_discovery_reference(UUID, JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_discovery_reference(UUID, JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.checkpoint_market_discovery_stage(UUID, JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.checkpoint_market_discovery_stage(UUID, JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_context(UUID, INT) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.read_market_discovery_context(UUID, INT) TO service_role;

GRANT SELECT ON public.market_reference_manifests TO stock_agent_release_reader;
GRANT SELECT ON public.market_security_reference_revisions TO stock_agent_release_reader;
GRANT SELECT ON public.market_discovery_stage_tasks TO stock_agent_release_reader;
GRANT SELECT ON public.market_exposure_facts TO stock_agent_release_reader;
GRANT SELECT ON public.market_theme_episode_revisions TO stock_agent_release_reader;
GRANT SELECT ON public.market_research_nominations TO stock_agent_release_reader;

GRANT SELECT (id,reference_version,revision,capability_version,taxonomy_version,source_hash,valid_from,valid_to,manifest,content_hash,created_at) ON public.market_reference_manifests TO stock_agent_dashboard;
GRANT SELECT (id,security_id,entity_id,ticker,exchange,instrument_type,eligible,exclusion_reasons,aliases,valid_from,valid_to,content_hash,created_at) ON public.market_security_reference_revisions TO stock_agent_dashboard;
GRANT SELECT (id,stage,capability_id,provider,query_kind,query_hash,state,attempt_count,request_budget,created_at,updated_at) ON public.market_discovery_stage_tasks TO stock_agent_dashboard;
GRANT SELECT (id,security_revision_id,theme_episode_revision_id,exposure_kind,fact,source_ids,valid_from,valid_to,content_hash,created_at) ON public.market_exposure_facts TO stock_agent_dashboard;
GRANT SELECT (id,theme_id,revision,episode,source_ids,valid_from,valid_to,content_hash,created_at) ON public.market_theme_episode_revisions TO stock_agent_dashboard;
GRANT SELECT (id,security_revision_id,theme_episode_revision_id,exposure_fact_ids,state,rationale,created_at,updated_at) ON public.market_research_nominations TO stock_agent_dashboard;

DO $$ DECLARE name TEXT; BEGIN
  FOREACH name IN ARRAY ARRAY['market_reference_manifests','market_security_reference_revisions','market_discovery_stage_tasks','market_exposure_facts','market_theme_episode_revisions','market_research_nominations'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS release_evidence_select ON public.%I',name);
    EXECUTE format('CREATE POLICY release_evidence_select ON public.%I FOR SELECT TO stock_agent_release_reader USING (true)',name);
    EXECUTE format('DROP POLICY IF EXISTS owner_dashboard_select_discovery ON public.%I',name);
    EXECUTE format('CREATE POLICY owner_dashboard_select_discovery ON public.%I FOR SELECT TO stock_agent_dashboard USING (true)',name);
  END LOOP;
END; $$;

-- Consolidated from sql/migrations/20261006_reference_snapshot_transfer.sql
-- Bounded, resumable transfer of complete reference snapshots. Partial uploads
-- stay outside the finalized reference ledgers and are never readable as a
-- research reference. All records are provenance only and grant no action.

CREATE OR REPLACE FUNCTION public.market_reference_canonical_json_v1(p_value JSONB)
RETURNS TEXT LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE
  v_kind TEXT:=jsonb_typeof(p_value);
  v_result TEXT;
BEGIN
  IF v_kind='object' THEN
    SELECT '{'||COALESCE(string_agg(
      to_jsonb(e.key)::text||':'||public.market_reference_canonical_json_v1(e.value),
      ',' ORDER BY convert_to(e.key,'UTF8')
    ),'')||'}' INTO v_result FROM jsonb_each(p_value) e;
    RETURN v_result;
  ELSIF v_kind='array' THEN
    SELECT '['||COALESCE(string_agg(
      public.market_reference_canonical_json_v1(e.value),',' ORDER BY e.ordinality
    ),'')||']' INTO v_result
    FROM jsonb_array_elements(p_value) WITH ORDINALITY e(value,ordinality);
    RETURN v_result;
  END IF;
  RETURN p_value::text;
END;
$$;

CREATE OR REPLACE FUNCTION public.market_reference_manifest_semantic_v1(p_row JSONB)
RETURNS JSONB LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
  SELECT jsonb_build_object(
    'kind','reference_manifest','semantic_encoding_version',1,
    'value',jsonb_build_object(
      'id',p_row->'id','reference_version',p_row->'reference_version',
      'revision',p_row->'revision','capability_version',p_row->'capability_version',
      'taxonomy_version',p_row->'taxonomy_version','source_hash',p_row->'source_hash',
      'valid_from',to_char((p_row->>'valid_from')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'valid_to',CASE WHEN p_row->>'valid_to' IS NULL THEN NULL ELSE
        to_char((p_row->>'valid_to')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') END,
      'manifest',p_row->'manifest'
    )
  )
$$;

CREATE OR REPLACE FUNCTION public.market_reference_security_semantic_v1(p_row JSONB)
RETURNS JSONB LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
  SELECT jsonb_build_object(
    'kind','security_revision','semantic_encoding_version',1,
    'value',jsonb_build_object(
      'revision',p_row->'revision','security_id',p_row->'security_id',
      'entity_id',p_row->'entity_id','ticker',p_row->'ticker','exchange',p_row->'exchange',
      'instrument_type',p_row->'instrument_type','eligible',p_row->'eligible',
      'exclusion_reasons',p_row->'exclusion_reasons','aliases',p_row->'aliases',
      'source_ids',p_row->'source_ids',
      'valid_from',to_char((p_row->>'valid_from')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'valid_to',CASE WHEN p_row->>'valid_to' IS NULL THEN NULL ELSE
        to_char((p_row->>'valid_to')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') END
    )
  )
$$;

-- Re-harden the legacy service-only reference writer from 20261005 with the
-- canonical semantic encoding before any first insert or idempotent replay.
CREATE OR REPLACE FUNCTION public.record_market_discovery_reference(p_run_id UUID, p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE m JSONB; r JSONB;
  v_existing_manifest public.market_reference_manifests%ROWTYPE;
  v_existing_security public.market_security_reference_revisions%ROWTYPE;
  v_count INT:=0; v_duplicate BOOLEAN:=true;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>1048576
     OR NOT(p_payload ?& ARRAY['manifest','security_revisions'])
     OR (p_payload-ARRAY['manifest','security_revisions'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'security_revisions')<>'array'
     OR jsonb_array_length(p_payload->'security_revisions')>15000 THEN
    RAISE EXCEPTION 'invalid discovery reference payload' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'security_revisions') child
    GROUP BY (child->>'id')::uuid HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate security revision id' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
                WHERE i.id=p_run_id AND a.status='running') THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  m:=p_payload->'manifest';
  IF jsonb_typeof(m)<>'object' OR NOT(m ?& ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])
     OR (m-ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])<>'{}'::jsonb
     OR m->>'source_hash' !~ '^[0-9a-f]{64}$' OR m->>'content_hash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(m->'manifest')<>'object' OR octet_length((m->'manifest')::text)>65536 THEN
    RAISE EXCEPTION 'invalid discovery reference manifest' USING ERRCODE='22023';
  END IF;
  IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
       public.market_reference_manifest_semantic_v1(m)
     ),'UTF8'),'sha256'),'hex')<>m->>'content_hash' THEN
    RAISE EXCEPTION 'discovery reference manifest hash mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing_manifest FROM public.market_reference_manifests WHERE id=(m->>'id')::uuid;
  IF FOUND THEN
    IF v_existing_manifest.run_id<>p_run_id
       OR v_existing_manifest.reference_version<>m->>'reference_version'
       OR v_existing_manifest.revision<>(m->>'revision')::int
       OR v_existing_manifest.capability_version<>(m->>'capability_version')::int
       OR v_existing_manifest.taxonomy_version<>(m->>'taxonomy_version')::int
       OR v_existing_manifest.source_hash<>m->>'source_hash'
       OR v_existing_manifest.valid_from<>(m->>'valid_from')::timestamptz
       OR v_existing_manifest.valid_to IS DISTINCT FROM (m->>'valid_to')::timestamptz
       OR v_existing_manifest.manifest<>m->'manifest'
       OR v_existing_manifest.content_hash<>m->>'content_hash' THEN
      RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    INSERT INTO public.market_reference_manifests(id,run_id,reference_version,revision,capability_version,taxonomy_version,source_hash,valid_from,valid_to,manifest,content_hash)
    VALUES((m->>'id')::uuid,p_run_id,m->>'reference_version',(m->>'revision')::int,(m->>'capability_version')::int,(m->>'taxonomy_version')::int,m->>'source_hash',(m->>'valid_from')::timestamptz,(m->>'valid_to')::timestamptz,m->'manifest',m->>'content_hash');
    v_duplicate:=false;
  END IF;
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'security_revisions') LOOP
    IF jsonb_typeof(r)<>'object' OR NOT(r ?& ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])
       OR (r-ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])<>'{}'::jsonb
       OR r->>'manifest_id'<>m->>'id' OR r->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(r->'source_ids')<>'array' OR jsonb_array_length(r->'source_ids') NOT BETWEEN 1 AND 16 THEN
      RAISE EXCEPTION 'invalid security reference revision' USING ERRCODE='22023';
    END IF;
    IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
         public.market_reference_security_semantic_v1(r)
       ),'UTF8'),'sha256'),'hex')<>r->>'content_hash' THEN
      RAISE EXCEPTION 'discovery reference security hash mismatch' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_existing_security FROM public.market_security_reference_revisions WHERE id=(r->>'id')::uuid;
    IF FOUND THEN
      IF v_existing_security.manifest_id<>(r->>'manifest_id')::uuid
         OR v_existing_security.run_id<>p_run_id
         OR v_existing_security.revision<>(r->>'revision')::int
         OR v_existing_security.security_id<>r->>'security_id'
         OR v_existing_security.entity_id<>r->>'entity_id'
         OR v_existing_security.ticker<>r->>'ticker'
         OR v_existing_security.exchange IS DISTINCT FROM r->>'exchange'
         OR v_existing_security.instrument_type<>r->>'instrument_type'
         OR v_existing_security.eligible<>(r->>'eligible')::boolean
         OR v_existing_security.exclusion_reasons<>r->'exclusion_reasons'
         OR v_existing_security.aliases<>r->'aliases'
         OR v_existing_security.source_ids<>r->'source_ids'
         OR v_existing_security.valid_from<>(r->>'valid_from')::timestamptz
         OR v_existing_security.valid_to IS DISTINCT FROM (r->>'valid_to')::timestamptz
         OR v_existing_security.content_hash<>r->>'content_hash' THEN
        RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
      END IF;
    ELSE
      IF v_duplicate THEN
        RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
      END IF;
      INSERT INTO public.market_security_reference_revisions(id,manifest_id,run_id,revision,security_id,entity_id,ticker,exchange,instrument_type,eligible,exclusion_reasons,aliases,source_ids,valid_from,valid_to,content_hash)
      VALUES((r->>'id')::uuid,(r->>'manifest_id')::uuid,p_run_id,(r->>'revision')::int,r->>'security_id',r->>'entity_id',r->>'ticker',r->>'exchange',r->>'instrument_type',(r->>'eligible')::boolean,r->'exclusion_reasons',r->'aliases',r->'source_ids',(r->>'valid_from')::timestamptz,(r->>'valid_to')::timestamptz,r->>'content_hash');
    END IF;
    v_count:=v_count+1;
  END LOOP;
  IF v_duplicate AND (
    SELECT count(*) FROM public.market_security_reference_revisions
    WHERE manifest_id=(m->>'id')::uuid
  )<>v_count THEN
    RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
  END IF;
  RETURN jsonb_build_object('manifest_id',m->>'id','security_revision_count',v_count,'duplicate',v_duplicate);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid discovery reference payload' USING ERRCODE='22023';
END;
$$;

CREATE TABLE IF NOT EXISTS public.market_reference_transfer_requests (
  request_id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  operation TEXT NOT NULL CHECK (operation IN (
    'begin_discovery_reference','record_discovery_reference_chunk',
    'finalize_discovery_reference','pin_discovery_reference','read_discovery_reference'
  )),
  encoded_bytes INT NOT NULL CHECK (encoded_bytes BETWEEN 1 AND 262144),
  request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  request_payload JSONB NOT NULL CHECK (jsonb_typeof(request_payload)='object' AND octet_length(request_payload::text)<=196608),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS public.market_reference_chunk_receipts (
  manifest_id UUID NOT NULL,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  capability_id TEXT NOT NULL CHECK (capability_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  chunk_index INT NOT NULL CHECK (chunk_index BETWEEN -1 AND 511),
  chunk_count INT NOT NULL CHECK (chunk_count BETWEEN 1 AND 512),
  entry_count INT NOT NULL CHECK (entry_count BETWEEN 0 AND 200),
  chunk_hash TEXT NOT NULL CHECK (chunk_hash ~ '^[0-9a-f]{64}$'),
  predecessor_manifest_id UUID,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=196608),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (manifest_id,chunk_index)
);

CREATE TABLE IF NOT EXISTS public.market_reference_snapshot_memberships (
  manifest_id UUID NOT NULL REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  security_revision_id UUID NOT NULL REFERENCES public.market_security_reference_revisions(id) ON DELETE RESTRICT,
  security_id TEXT NOT NULL CHECK (security_id ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'),
  ordinal INT NOT NULL CHECK (ordinal BETWEEN 0 AND 14999),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (manifest_id,security_id),
  UNIQUE (manifest_id,security_revision_id),
  UNIQUE (manifest_id,ordinal)
);

CREATE TABLE IF NOT EXISTS public.market_reference_finalization_seals (
  manifest_id UUID PRIMARY KEY REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  capability_id TEXT NOT NULL CHECK (capability_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  predecessor_manifest_id UUID REFERENCES public.market_reference_finalization_seals(manifest_id) ON DELETE RESTRICT,
  chunk_count INT NOT NULL CHECK (chunk_count BETWEEN 1 AND 512),
  security_count INT NOT NULL CHECK (security_count BETWEEN 1 AND 15000),
  root_hash TEXT NOT NULL CHECK (root_hash ~ '^[0-9a-f]{64}$'),
  finalized_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id,capability_id)
);

CREATE TABLE IF NOT EXISTS public.market_reference_run_bindings (
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  capability_id TEXT NOT NULL CHECK (capability_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  manifest_id UUID REFERENCES public.market_reference_finalization_seals(manifest_id) ON DELETE RESTRICT,
  reference_status TEXT NOT NULL CHECK (reference_status IN ('healthy','reference_stale','reference_unavailable')),
  reference_as_of TIMESTAMPTZ NOT NULL,
  source_retrieved_at TIMESTAMPTZ,
  reference_age_seconds BIGINT CHECK (reference_age_seconds IS NULL OR reference_age_seconds>=0),
  request_payload JSONB NOT NULL CHECK (jsonb_typeof(request_payload)='object' AND octet_length(request_payload::text)<=196608),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (run_id,capability_id),
  CHECK (
    (reference_status='reference_unavailable' AND manifest_id IS NULL AND source_retrieved_at IS NULL AND reference_age_seconds IS NULL)
    OR (reference_status IN ('healthy','reference_stale') AND manifest_id IS NOT NULL AND source_retrieved_at IS NOT NULL AND reference_age_seconds IS NOT NULL)
  )
);

CREATE TABLE IF NOT EXISTS public.market_reference_predecessor_pins (
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  capability_id TEXT NOT NULL CHECK (capability_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  manifest_id UUID REFERENCES public.market_reference_finalization_seals(manifest_id) ON DELETE RESTRICT,
  reference_status TEXT NOT NULL CHECK (reference_status IN ('reference_stale','reference_unavailable')),
  reference_as_of TIMESTAMPTZ NOT NULL,
  source_retrieved_at TIMESTAMPTZ,
  reference_age_seconds BIGINT CHECK (reference_age_seconds IS NULL OR reference_age_seconds>=0),
  request_payload JSONB NOT NULL CHECK (jsonb_typeof(request_payload)='object' AND octet_length(request_payload::text)<=196608),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (run_id,capability_id),
  CHECK (
    (reference_status='reference_unavailable' AND manifest_id IS NULL AND source_retrieved_at IS NULL AND reference_age_seconds IS NULL)
    OR (reference_status='reference_stale' AND manifest_id IS NOT NULL AND source_retrieved_at IS NOT NULL AND reference_age_seconds IS NOT NULL)
  )
);

DROP TRIGGER IF EXISTS market_reference_chunk_receipts_append_only ON public.market_reference_chunk_receipts;
CREATE TRIGGER market_reference_chunk_receipts_append_only BEFORE UPDATE OR DELETE ON public.market_reference_chunk_receipts
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_reference_snapshot_memberships_append_only ON public.market_reference_snapshot_memberships;
CREATE TRIGGER market_reference_snapshot_memberships_append_only BEFORE UPDATE OR DELETE ON public.market_reference_snapshot_memberships
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_reference_finalization_seals_append_only ON public.market_reference_finalization_seals;
CREATE TRIGGER market_reference_finalization_seals_append_only BEFORE UPDATE OR DELETE ON public.market_reference_finalization_seals
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_reference_run_bindings_append_only ON public.market_reference_run_bindings;
CREATE TRIGGER market_reference_run_bindings_append_only BEFORE UPDATE OR DELETE ON public.market_reference_run_bindings
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_reference_predecessor_pins_append_only ON public.market_reference_predecessor_pins;
CREATE TRIGGER market_reference_predecessor_pins_append_only BEFORE UPDATE OR DELETE ON public.market_reference_predecessor_pins
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_reference_transfer_requests_append_only ON public.market_reference_transfer_requests;
CREATE TRIGGER market_reference_transfer_requests_append_only BEFORE UPDATE OR DELETE ON public.market_reference_transfer_requests
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();

CREATE OR REPLACE FUNCTION public.claim_market_reference_transfer_request(
  p_run_id UUID,p_operation TEXT,p_payload JSONB,p_request_id UUID,
  p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_existing public.market_reference_transfer_requests%ROWTYPE;
  v_canonical TEXT;
  v_count BIGINT;
  v_bytes BIGINT;
  v_started TIMESTAMPTZ;
BEGIN
  IF p_run_id IS NULL OR p_request_id IS NULL
     OR p_operation NOT IN ('begin_discovery_reference','record_discovery_reference_chunk',
       'finalize_discovery_reference','pin_discovery_reference','read_discovery_reference')
     OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR p_encoded_bytes NOT BETWEEN 1 AND 262144
     OR p_request_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference transfer claim' USING ERRCODE='22023';
  END IF;
  v_canonical:=public.market_reference_canonical_json_v1(jsonb_build_object(
    'dry_run',false,'operation',p_operation,'payload',p_payload,
    'request_id',p_request_id::text,'run_id',p_run_id::text,'schema_version',1
  ));
  IF octet_length(convert_to(v_canonical,'UTF8'))<>p_encoded_bytes
     OR encode(extensions.digest(convert_to(v_canonical,'UTF8'),'sha256'),'hex')<>p_request_hash THEN
    RAISE EXCEPTION 'reference transfer claim hash mismatch' USING ERRCODE='22023';
  END IF;
  PERFORM 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
  WHERE i.id=p_run_id AND a.status='running' FOR UPDATE OF i,a;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_reference_transfer_requests
  WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_existing.run_id<>p_run_id OR v_existing.operation<>p_operation
       OR v_existing.encoded_bytes<>p_encoded_bytes OR v_existing.request_hash<>p_request_hash
       OR v_existing.request_payload<>p_payload THEN
      RAISE EXCEPTION 'reference transfer request idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN true;
  END IF;
  SELECT count(*),COALESCE(sum(encoded_bytes),0),min(created_at)
  INTO v_count,v_bytes,v_started FROM public.market_reference_transfer_requests
  WHERE run_id=p_run_id;
  IF v_count>=160 OR v_bytes+p_encoded_bytes>33554432
     OR (v_started IS NOT NULL AND clock_timestamp()-v_started>interval '45 seconds') THEN
    RAISE EXCEPTION 'reference transfer aggregate bound exceeded' USING ERRCODE='54000';
  END IF;
  INSERT INTO public.market_reference_transfer_requests(
    request_id,run_id,operation,encoded_bytes,request_hash,request_payload
  ) VALUES(p_request_id,p_run_id,p_operation,p_encoded_bytes,p_request_hash,p_payload);
  RETURN false;
END;
$$;

CREATE OR REPLACE FUNCTION public.begin_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_manifest JSONB;
  v_manifest_id UUID;
  v_existing public.market_reference_chunk_receipts%ROWTYPE;
  v_predecessor UUID;
BEGIN
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'begin_discovery_reference',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest','capability_id','chunk_count','security_count','root_hash','predecessor_manifest_id'])
     OR (p_payload-ARRAY['manifest','capability_id','chunk_count','security_count','root_hash','predecessor_manifest_id'])<>'{}'::jsonb
     OR p_payload->>'capability_id' !~ '^[a-z][a-z0-9_]{2,79}$'
     OR (p_payload->>'chunk_count')::int NOT BETWEEN 1 AND 512
     OR (p_payload->>'security_count')::int NOT BETWEEN 1 AND 15000
     OR p_payload->>'root_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference begin payload' USING ERRCODE='22023';
  END IF;
  PERFORM 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
  WHERE i.id=p_run_id AND a.status='running' FOR UPDATE OF i,a;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  v_manifest:=p_payload->'manifest';
  IF jsonb_typeof(v_manifest)<>'object'
     OR NOT(v_manifest ?& ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])
     OR (v_manifest-ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])<>'{}'::jsonb
     OR v_manifest->>'source_hash' !~ '^[0-9a-f]{64}$'
     OR v_manifest->>'content_hash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(v_manifest->'manifest')<>'object'
     OR v_manifest->'manifest'->>'coverage_status'<>'scope_not_guaranteed'
     OR v_manifest->'manifest'->>'reference_status'<>'healthy'
     OR (v_manifest->'manifest'->>'security_count')::int<>(p_payload->>'security_count')::int THEN
    RAISE EXCEPTION 'invalid reference begin manifest' USING ERRCODE='22023';
  END IF;
  IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
       public.market_reference_manifest_semantic_v1(v_manifest)
     ),'UTF8'),'sha256'),'hex')<>v_manifest->>'content_hash' THEN
    RAISE EXCEPTION 'reference manifest hash mismatch' USING ERRCODE='22023';
  END IF;
  v_manifest_id:=(v_manifest->>'id')::uuid;
  IF p_payload->>'predecessor_manifest_id' IS NOT NULL THEN
    v_predecessor:=(p_payload->>'predecessor_manifest_id')::uuid;
    IF NOT EXISTS(
      SELECT 1 FROM public.market_reference_predecessor_pins b
      WHERE b.run_id=p_run_id AND b.capability_id=p_payload->>'capability_id'
        AND b.manifest_id=v_predecessor AND b.reference_status='reference_stale'
    ) THEN
      RAISE EXCEPTION 'reference predecessor membership mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    IF NOT EXISTS(
      SELECT 1 FROM public.market_reference_predecessor_pins b
      WHERE b.run_id=p_run_id AND b.capability_id=p_payload->>'capability_id'
        AND b.manifest_id IS NULL AND b.reference_status='reference_unavailable'
    ) THEN
      RAISE EXCEPTION 'reference predecessor membership mismatch' USING ERRCODE='22023';
    END IF;
  END IF;
  SELECT * INTO v_existing FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1;
  IF FOUND THEN
    IF v_existing.run_id<>p_run_id OR v_existing.capability_id<>p_payload->>'capability_id'
       OR v_existing.chunk_count<>(p_payload->>'chunk_count')::int
       OR v_existing.entry_count<>0 OR v_existing.chunk_hash<>p_payload->>'root_hash'
       OR v_existing.payload<>p_payload THEN
      RAISE EXCEPTION 'reference begin idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'predecessor_manifest_id',v_existing.predecessor_manifest_id::text,'duplicate',true);
  END IF;
  IF EXISTS(SELECT 1 FROM public.market_reference_manifests WHERE id=v_manifest_id)
     OR EXISTS(SELECT 1 FROM public.market_reference_chunk_receipts WHERE manifest_id=v_manifest_id) THEN
    RAISE EXCEPTION 'reference begin idempotency mismatch' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_reference_chunk_receipts(
    manifest_id,run_id,capability_id,chunk_index,chunk_count,entry_count,chunk_hash,
    predecessor_manifest_id,payload
  ) VALUES(
    v_manifest_id,p_run_id,p_payload->>'capability_id',-1,(p_payload->>'chunk_count')::int,
    0,p_payload->>'root_hash',v_predecessor,p_payload
  );
  RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'predecessor_manifest_id',v_predecessor::text,'duplicate',false);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid reference begin payload' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_discovery_reference_chunk(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_manifest_id UUID;
  v_begin public.market_reference_chunk_receipts%ROWTYPE;
  v_existing public.market_reference_chunk_receipts%ROWTYPE;
  v_entry JSONB;
  v_count INT;
  v_hash TEXT;
BEGIN
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'record_discovery_reference_chunk',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest_id','chunk_index','chunk_count','entries','chunk_hash'])
     OR (p_payload-ARRAY['manifest_id','chunk_index','chunk_count','entries','chunk_hash'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'entries')<>'array'
     OR jsonb_array_length(p_payload->'entries') NOT BETWEEN 1 AND 200
     OR (p_payload->>'chunk_index')::int NOT BETWEEN 0 AND 511
     OR (p_payload->>'chunk_count')::int NOT BETWEEN 1 AND 512
     OR p_payload->>'chunk_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference chunk payload' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(
    SELECT 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
    WHERE i.id=p_run_id AND a.status='running'
  ) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  v_manifest_id:=(p_payload->>'manifest_id')::uuid;
  SELECT * INTO v_begin FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1 FOR UPDATE;
  IF NOT FOUND OR v_begin.run_id<>p_run_id
     OR v_begin.chunk_count<>(p_payload->>'chunk_count')::int
     OR (p_payload->>'chunk_index')::int>=v_begin.chunk_count THEN
    RAISE EXCEPTION 'reference chunk has no matching begin' USING ERRCODE='22023';
  END IF;
  FOR v_entry IN SELECT value FROM jsonb_array_elements(p_payload->'entries') LOOP
    IF jsonb_typeof(v_entry)<>'object'
       OR NOT(v_entry ?& ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])
       OR (v_entry-ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])<>'{}'::jsonb
       OR v_entry->>'manifest_id'<>v_manifest_id::text
       OR v_entry->>'security_id' !~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'
       OR v_entry->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$'
       OR v_entry->>'content_hash' !~ '^[0-9a-f]{64}$' THEN
      RAISE EXCEPTION 'invalid reference chunk entry' USING ERRCODE='22023';
    END IF;
    IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
         public.market_reference_security_semantic_v1(v_entry)
       ),'UTF8'),'sha256'),'hex')<>v_entry->>'content_hash' THEN
      RAISE EXCEPTION 'reference security hash mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'entries') e
    GROUP BY e->>'security_id' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'entries') e
    GROUP BY (e->>'id')::uuid HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate reference security identity' USING ERRCODE='22023';
  END IF;
  SELECT count(*),encode(extensions.digest(convert_to(COALESCE(string_agg(
    (e.value->>'security_id')||chr(31)||(e.value->>'id')||chr(31)||(e.value->>'content_hash'),
    E'\n' ORDER BY e.ordinality),''),'UTF8'),'sha256'),'hex')
  INTO v_count,v_hash FROM jsonb_array_elements(p_payload->'entries') WITH ORDINALITY e(value,ordinality);
  IF v_hash<>p_payload->>'chunk_hash' THEN
    RAISE EXCEPTION 'reference chunk hash mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=(p_payload->>'chunk_index')::int;
  IF FOUND THEN
    IF v_existing.run_id<>p_run_id OR v_existing.chunk_count<>(p_payload->>'chunk_count')::int
       OR v_existing.entry_count<>v_count OR v_existing.chunk_hash<>v_hash
       OR v_existing.payload<>p_payload THEN
      RAISE EXCEPTION 'reference chunk idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'chunk_index',v_existing.chunk_index,'duplicate',true);
  END IF;
  IF EXISTS(SELECT 1 FROM public.market_reference_finalization_seals WHERE manifest_id=v_manifest_id) THEN
    RAISE EXCEPTION 'reference snapshot is already finalized' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_reference_chunk_receipts(
    manifest_id,run_id,capability_id,chunk_index,chunk_count,entry_count,chunk_hash,
    predecessor_manifest_id,payload
  ) VALUES(
    v_manifest_id,p_run_id,v_begin.capability_id,(p_payload->>'chunk_index')::int,
    v_begin.chunk_count,v_count,v_hash,v_begin.predecessor_manifest_id,p_payload
  );
  RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'chunk_index',(p_payload->>'chunk_index')::int,'duplicate',false);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid reference chunk payload' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.finalize_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_manifest_id UUID;
  v_begin public.market_reference_chunk_receipts%ROWTYPE;
  v_seal public.market_reference_finalization_seals%ROWTYPE;
  v_chunk_count INT;
  v_entry_count INT;
  v_min_chunk INT;
  v_max_chunk INT;
  v_root TEXT;
  v_entry JSONB;
  v_prior_revision UUID;
  v_revision_id UUID;
  v_ordinal INT:=0;
  v_manifest JSONB;
BEGIN
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'finalize_discovery_reference',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest_id','root_hash'])
     OR (p_payload-ARRAY['manifest_id','root_hash'])<>'{}'::jsonb
     OR p_payload->>'root_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference finalize payload' USING ERRCODE='22023';
  END IF;
  v_manifest_id:=(p_payload->>'manifest_id')::uuid;
  SELECT * INTO v_seal FROM public.market_reference_finalization_seals
  WHERE manifest_id=v_manifest_id;
  IF FOUND THEN
    IF v_seal.run_id<>p_run_id OR v_seal.root_hash<>p_payload->>'root_hash' THEN
      RAISE EXCEPTION 'reference finalization idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_seal.security_count,'duplicate',true);
  END IF;
  IF NOT EXISTS(
    SELECT 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
    WHERE i.id=p_run_id AND a.status='running'
  ) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_begin FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1 FOR UPDATE;
  IF NOT FOUND OR v_begin.run_id<>p_run_id OR v_begin.chunk_hash<>p_payload->>'root_hash' THEN
    RAISE EXCEPTION 'reference finalization has no matching begin' USING ERRCODE='22023';
  END IF;
  -- A concurrent exact retry can pass the optimistic seal lookup before the
  -- first caller commits. Recheck while holding the begin receipt lock so the
  -- loser observes the committed seal instead of surfacing a false conflict.
  SELECT * INTO v_seal FROM public.market_reference_finalization_seals
  WHERE manifest_id=v_manifest_id;
  IF FOUND THEN
    IF v_seal.run_id<>p_run_id OR v_seal.root_hash<>p_payload->>'root_hash' THEN
      RAISE EXCEPTION 'reference finalization idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_seal.security_count,'duplicate',true);
  END IF;
  SELECT count(*),COALESCE(sum(entry_count),0),min(chunk_index),max(chunk_index),
         encode(extensions.digest(convert_to(COALESCE(string_agg(chunk_hash,'' ORDER BY chunk_index),''),'UTF8'),'sha256'),'hex')
  INTO v_chunk_count,v_entry_count,v_min_chunk,v_max_chunk,v_root
  FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index>=0;
  IF v_chunk_count<>v_begin.chunk_count OR v_min_chunk<>0 OR v_max_chunk<>v_begin.chunk_count-1 THEN
    RAISE EXCEPTION 'reference chunk sequence mismatch' USING ERRCODE='22023';
  END IF;
  IF v_entry_count<>(v_begin.payload->>'security_count')::int THEN
    RAISE EXCEPTION 'reference snapshot count mismatch' USING ERRCODE='22023';
  END IF;
  IF v_root<>v_begin.chunk_hash THEN
    RAISE EXCEPTION 'reference snapshot root mismatch' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    GROUP BY e->>'security_id' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    GROUP BY (e->>'id')::uuid HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate reference security identity' USING ERRCODE='22023';
  END IF;
  IF v_begin.predecessor_manifest_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.market_reference_finalization_seals s
    WHERE s.manifest_id=v_begin.predecessor_manifest_id AND s.capability_id=v_begin.capability_id
  ) THEN
    RAISE EXCEPTION 'reference predecessor membership mismatch' USING ERRCODE='22023';
  END IF;
  v_manifest:=v_begin.payload->'manifest';
  IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
       public.market_reference_manifest_semantic_v1(v_manifest)
     ),'UTF8'),'sha256'),'hex')<>v_manifest->>'content_hash' THEN
    RAISE EXCEPTION 'reference manifest hash mismatch' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_reference_manifests(
    id,run_id,reference_version,revision,capability_version,taxonomy_version,source_hash,
    valid_from,valid_to,manifest,content_hash
  ) VALUES(
    v_manifest_id,p_run_id,v_manifest->>'reference_version',(v_manifest->>'revision')::int,
    (v_manifest->>'capability_version')::int,(v_manifest->>'taxonomy_version')::int,
    v_manifest->>'source_hash',(v_manifest->>'valid_from')::timestamptz,
    (v_manifest->>'valid_to')::timestamptz,v_manifest->'manifest',v_manifest->>'content_hash'
  );
  FOR v_entry IN
    SELECT e.value FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') WITH ORDINALITY e(value,item_ordinal)
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    ORDER BY c.chunk_index,e.item_ordinal
  LOOP
    IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
         public.market_reference_security_semantic_v1(v_entry)
       ),'UTF8'),'sha256'),'hex')<>v_entry->>'content_hash' THEN
      RAISE EXCEPTION 'reference security hash mismatch' USING ERRCODE='22023';
    END IF;
    v_prior_revision:=NULL;
    IF v_begin.predecessor_manifest_id IS NOT NULL THEN
      SELECT m.security_revision_id INTO v_prior_revision
      FROM public.market_reference_snapshot_memberships m
      JOIN public.market_security_reference_revisions s ON s.id=m.security_revision_id
      WHERE m.manifest_id=v_begin.predecessor_manifest_id
        AND m.security_id=v_entry->>'security_id'
        AND s.content_hash=v_entry->>'content_hash';
    END IF;
    IF v_prior_revision IS NULL THEN
      v_revision_id:=(v_entry->>'id')::uuid;
      INSERT INTO public.market_security_reference_revisions(
        id,manifest_id,run_id,revision,security_id,entity_id,ticker,exchange,instrument_type,
        eligible,exclusion_reasons,aliases,source_ids,valid_from,valid_to,content_hash
      ) VALUES(
        v_revision_id,v_manifest_id,p_run_id,(v_entry->>'revision')::int,
        v_entry->>'security_id',v_entry->>'entity_id',v_entry->>'ticker',v_entry->>'exchange',
        v_entry->>'instrument_type',(v_entry->>'eligible')::boolean,v_entry->'exclusion_reasons',
        v_entry->'aliases',v_entry->'source_ids',(v_entry->>'valid_from')::timestamptz,
        (v_entry->>'valid_to')::timestamptz,v_entry->>'content_hash'
      );
    ELSE
      v_revision_id:=v_prior_revision;
    END IF;
    INSERT INTO public.market_reference_snapshot_memberships(
      manifest_id,security_revision_id,security_id,ordinal
    ) VALUES(v_manifest_id,v_revision_id,v_entry->>'security_id',v_ordinal);
    v_ordinal:=v_ordinal+1;
  END LOOP;
  INSERT INTO public.market_reference_finalization_seals(
    manifest_id,run_id,capability_id,predecessor_manifest_id,chunk_count,security_count,root_hash
  ) VALUES(
    v_manifest_id,p_run_id,v_begin.capability_id,v_begin.predecessor_manifest_id,
    v_begin.chunk_count,v_entry_count,v_root
  );
  RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_entry_count,'duplicate',false);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range OR unique_violation OR foreign_key_violation THEN
  RAISE EXCEPTION 'reference finalization conflict' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.pin_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_existing public.market_reference_run_bindings%ROWTYPE;
  v_predecessor_existing public.market_reference_predecessor_pins%ROWTYPE;
  v_predecessor_pin public.market_reference_predecessor_pins%ROWTYPE;
  v_manifest_id UUID;
  v_status TEXT;
  v_as_of TIMESTAMPTZ;
  v_source TIMESTAMPTZ;
  v_age BIGINT;
  v_capability TEXT;
  v_role TEXT;
BEGIN
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'pin_discovery_reference',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['capability_id','binding_role','manifest_id','reference_status','reference_as_of'])
     OR (p_payload-ARRAY['capability_id','binding_role','manifest_id','reference_status','reference_as_of'])<>'{}'::jsonb
     OR p_payload->>'capability_id' !~ '^[a-z][a-z0-9_]{2,79}$'
     OR p_payload->>'binding_role' NOT IN ('predecessor','current')
     OR p_payload->>'reference_status' NOT IN ('healthy','reference_stale','reference_unavailable') THEN
    RAISE EXCEPTION 'invalid reference pin payload' USING ERRCODE='22023';
  END IF;
  v_capability:=p_payload->>'capability_id';
  v_role:=p_payload->>'binding_role';
  v_status:=p_payload->>'reference_status';
  v_as_of:=(p_payload->>'reference_as_of')::timestamptz;
  -- Serialize the one binding for this run before checking for an exact retry.
  -- This also prevents the run from finishing between validation and insert.
  PERFORM 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
  WHERE i.id=p_run_id AND a.status='running' FOR UPDATE OF i,a;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  IF v_role='predecessor' THEN
    IF v_status<>'reference_stale' OR p_payload->>'manifest_id' IS NOT NULL THEN
      RAISE EXCEPTION 'predecessor pin must request latest finalized reference' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_predecessor_existing FROM public.market_reference_predecessor_pins
    WHERE run_id=p_run_id AND capability_id=v_capability;
    IF FOUND THEN
      IF v_predecessor_existing.request_payload<>p_payload
         OR v_predecessor_existing.reference_as_of<>v_as_of THEN
        RAISE EXCEPTION 'reference pin idempotency mismatch' USING ERRCODE='22023';
      END IF;
      RETURN jsonb_build_object(
        'binding_role','predecessor','manifest_id',v_predecessor_existing.manifest_id::text,
        'reference_status',v_predecessor_existing.reference_status,
        'source_retrieved_at',v_predecessor_existing.source_retrieved_at,
        'reference_age_seconds',v_predecessor_existing.reference_age_seconds,'duplicate',true
      );
    END IF;
    SELECT s.manifest_id INTO v_manifest_id
    FROM public.market_reference_finalization_seals s
    WHERE s.capability_id=v_capability AND s.run_id<>p_run_id
    ORDER BY s.finalized_at DESC,s.manifest_id DESC LIMIT 1;
    IF v_manifest_id IS NULL THEN
      v_status:='reference_unavailable';
    ELSE
      SELECT (m.manifest->>'source_retrieved_at')::timestamptz INTO v_source
      FROM public.market_reference_manifests m WHERE m.id=v_manifest_id;
      IF v_source IS NULL OR v_source>v_as_of THEN
        RAISE EXCEPTION 'reference age is invalid' USING ERRCODE='22023';
      END IF;
      v_age:=floor(extract(epoch FROM (v_as_of-v_source)))::bigint;
    END IF;
    INSERT INTO public.market_reference_predecessor_pins(
      run_id,capability_id,manifest_id,reference_status,reference_as_of,
      source_retrieved_at,reference_age_seconds,request_payload
    ) VALUES(p_run_id,v_capability,v_manifest_id,v_status,v_as_of,v_source,v_age,p_payload);
    RETURN jsonb_build_object(
      'binding_role','predecessor','manifest_id',v_manifest_id::text,
      'reference_status',v_status,'source_retrieved_at',v_source,
      'reference_age_seconds',v_age,'duplicate',false
    );
  END IF;
  SELECT * INTO v_existing FROM public.market_reference_run_bindings
  WHERE run_id=p_run_id AND capability_id=v_capability;
  IF FOUND THEN
    IF v_existing.request_payload<>p_payload
       OR v_existing.reference_as_of<>v_as_of
       OR (v_status='healthy' AND (
         p_payload->>'manifest_id' IS NULL OR v_existing.reference_status<>'healthy'
         OR v_existing.manifest_id IS DISTINCT FROM (p_payload->>'manifest_id')::uuid
       ))
       OR (v_status='reference_stale' AND (
         v_existing.reference_status NOT IN ('reference_stale','reference_unavailable')
         OR (p_payload->>'manifest_id' IS NOT NULL
             AND v_existing.manifest_id IS DISTINCT FROM (p_payload->>'manifest_id')::uuid)
       ))
       OR (v_status='reference_unavailable' AND (
         p_payload->>'manifest_id' IS NOT NULL
         OR v_existing.reference_status<>'reference_unavailable'
       )) THEN
      RAISE EXCEPTION 'reference pin idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object(
      'binding_role','current','manifest_id',v_existing.manifest_id::text,
      'reference_status',v_existing.reference_status,
      'source_retrieved_at',v_existing.source_retrieved_at,
      'reference_age_seconds',v_existing.reference_age_seconds,'duplicate',true
    );
  END IF;
  IF v_status='healthy' THEN
    IF p_payload->>'manifest_id' IS NULL THEN
      RAISE EXCEPTION 'healthy reference pin requires a manifest' USING ERRCODE='22023';
    END IF;
    v_manifest_id:=(p_payload->>'manifest_id')::uuid;
    IF NOT EXISTS(
      SELECT 1 FROM public.market_reference_finalization_seals s
      WHERE s.manifest_id=v_manifest_id AND s.run_id=p_run_id AND s.capability_id=v_capability
    ) THEN
      RAISE EXCEPTION 'reference snapshot is not finalized' USING ERRCODE='22023';
    END IF;
  ELSIF v_status='reference_stale' THEN
    SELECT * INTO v_predecessor_pin
    FROM public.market_reference_predecessor_pins
    WHERE run_id=p_run_id AND capability_id=v_capability;
    IF NOT FOUND
       OR (p_payload->>'manifest_id' IS NOT NULL
           AND (p_payload->>'manifest_id')::uuid IS DISTINCT FROM v_predecessor_pin.manifest_id) THEN
      RAISE EXCEPTION 'reference predecessor pin mismatch' USING ERRCODE='22023';
    END IF;
    v_manifest_id:=v_predecessor_pin.manifest_id;
    IF v_predecessor_pin.reference_status='reference_unavailable' THEN
      v_status:='reference_unavailable';
    ELSIF v_predecessor_pin.reference_status<>'reference_stale'
       OR v_manifest_id IS NULL OR NOT EXISTS(
      SELECT 1 FROM public.market_reference_finalization_seals s
      WHERE s.manifest_id=v_manifest_id AND s.capability_id=v_capability
    ) THEN
      RAISE EXCEPTION 'reference predecessor pin mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    SELECT * INTO v_predecessor_pin
    FROM public.market_reference_predecessor_pins
    WHERE run_id=p_run_id AND capability_id=v_capability;
    IF NOT FOUND OR p_payload->>'manifest_id' IS NOT NULL
       OR v_predecessor_pin.reference_status<>'reference_unavailable'
       OR v_predecessor_pin.manifest_id IS NOT NULL THEN
      RAISE EXCEPTION 'reference predecessor pin mismatch' USING ERRCODE='22023';
    END IF;
    v_manifest_id:=NULL;
  END IF;
  IF v_manifest_id IS NOT NULL THEN
    SELECT (m.manifest->>'source_retrieved_at')::timestamptz INTO v_source
    FROM public.market_reference_manifests m
    JOIN public.market_reference_finalization_seals s ON s.manifest_id=m.id
    WHERE m.id=v_manifest_id;
    IF v_source IS NULL OR v_source>v_as_of THEN
      RAISE EXCEPTION 'reference age is invalid' USING ERRCODE='22023';
    END IF;
    v_age:=floor(extract(epoch FROM (v_as_of-v_source)))::bigint;
  END IF;
  INSERT INTO public.market_reference_run_bindings(
    run_id,capability_id,manifest_id,reference_status,reference_as_of,
    source_retrieved_at,reference_age_seconds,request_payload
  ) VALUES(p_run_id,v_capability,v_manifest_id,v_status,v_as_of,v_source,v_age,p_payload);
  RETURN jsonb_build_object(
    'binding_role','current','manifest_id',v_manifest_id::text,'reference_status',v_status,
    'source_retrieved_at',v_source,'reference_age_seconds',v_age,'duplicate',false
  );
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid reference pin payload' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.read_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_capability TEXT;
  v_role TEXT;
  v_manifest_id UUID;
  v_status TEXT;
  v_source TIMESTAMPTZ;
  v_age BIGINT;
  v_after TEXT;
  v_limit INT;
  v_row JSONB;
  v_rows JSONB:='[]'::jsonb;
  v_candidate JSONB;
  v_result JSONB;
  v_last TEXT;
  v_seen INT:=0;
  v_more BOOLEAN:=false;
BEGIN
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'read_discovery_reference',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['capability_id','binding_role','after_security_id','limit'])
     OR (p_payload-ARRAY['capability_id','binding_role','after_security_id','limit'])<>'{}'::jsonb
     OR p_payload->>'capability_id' !~ '^[a-z][a-z0-9_]{2,79}$'
     OR p_payload->>'binding_role' NOT IN ('predecessor','current')
     OR (p_payload->>'limit')::int NOT BETWEEN 1 AND 500 THEN
    RAISE EXCEPTION 'invalid reference read payload' USING ERRCODE='22023';
  END IF;
  v_capability:=p_payload->>'capability_id';
  v_role:=p_payload->>'binding_role';
  v_after:=p_payload->>'after_security_id';
  v_limit:=(p_payload->>'limit')::int;
  IF v_role='predecessor' THEN
    SELECT manifest_id,reference_status,source_retrieved_at,reference_age_seconds
    INTO v_manifest_id,v_status,v_source,v_age
    FROM public.market_reference_predecessor_pins
    WHERE run_id=p_run_id AND capability_id=v_capability;
  ELSE
    SELECT manifest_id,reference_status,source_retrieved_at,reference_age_seconds
    INTO v_manifest_id,v_status,v_source,v_age
    FROM public.market_reference_run_bindings
    WHERE run_id=p_run_id AND capability_id=v_capability;
  END IF;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'reference snapshot is not pinned' USING ERRCODE='22023';
  END IF;
  IF v_manifest_id IS NULL THEN
    RETURN jsonb_build_object(
      'binding',jsonb_build_object(
        'binding_role',v_role,'manifest_id',NULL,'reference_status','reference_unavailable',
        'source_retrieved_at',NULL,'reference_age_seconds',NULL
      ),
      'manifest',NULL,'securities','[]'::jsonb,'next_after_security_id',NULL,'complete',true
    );
  END IF;
  IF NOT EXISTS(
    SELECT 1 FROM public.market_reference_finalization_seals s
    WHERE s.manifest_id=v_manifest_id
  ) THEN
    RAISE EXCEPTION 'reference snapshot is not finalized' USING ERRCODE='22023';
  END IF;
  FOR v_row IN
    SELECT to_jsonb(x) FROM (
      SELECT s.id,m.manifest_id,s.revision,s.security_id,s.entity_id,s.ticker,s.exchange,
             s.instrument_type,s.eligible,s.exclusion_reasons,s.aliases,s.source_ids,
             s.valid_from,s.valid_to,s.content_hash
      FROM public.market_reference_snapshot_memberships m
      JOIN public.market_security_reference_revisions s ON s.id=m.security_revision_id
      WHERE m.manifest_id=v_manifest_id
        AND (v_after IS NULL OR m.security_id>v_after)
      ORDER BY m.security_id
      LIMIT v_limit+1
    ) x
  LOOP
    IF v_seen>=v_limit THEN
      v_more:=true;
      EXIT;
    END IF;
    v_candidate:=v_rows||jsonb_build_array(v_row);
    SELECT jsonb_build_object(
      'binding',jsonb_build_object(
        'binding_role',v_role,'manifest_id',v_manifest_id::text,
        'reference_status',v_status,
        'source_retrieved_at',v_source,
        'reference_age_seconds',v_age
      ),
      'manifest',(SELECT to_jsonb(y) FROM (
        SELECT id,reference_version,revision,capability_version,taxonomy_version,source_hash,
               valid_from,valid_to,manifest,content_hash
        FROM public.market_reference_manifests WHERE id=v_manifest_id
      ) y),
      'securities',v_candidate,'next_after_security_id',v_row->>'security_id','complete',false
    ) INTO v_result;
    IF octet_length(v_result::text)>196608 THEN
      v_more:=true;
      EXIT;
    END IF;
    v_rows:=v_candidate;
    v_last:=v_row->>'security_id';
    v_seen:=v_seen+1;
  END LOOP;
  IF v_seen=0 AND v_more THEN
    RAISE EXCEPTION 'one reference row exceeds page bound' USING ERRCODE='54000';
  END IF;
  SELECT jsonb_build_object(
    'binding',jsonb_build_object(
      'binding_role',v_role,'manifest_id',v_manifest_id::text,
      'reference_status',v_status,
      'source_retrieved_at',v_source,
      'reference_age_seconds',v_age
    ),
    'manifest',(SELECT to_jsonb(y) FROM (
      SELECT id,reference_version,revision,capability_version,taxonomy_version,source_hash,
             valid_from,valid_to,manifest,content_hash
      FROM public.market_reference_manifests WHERE id=v_manifest_id
    ) y),
    'securities',v_rows,'next_after_security_id',CASE WHEN v_more THEN v_last ELSE NULL END,
    'complete',NOT v_more
  ) INTO v_result;
  IF octet_length(v_result::text)>196608 THEN
    RAISE EXCEPTION 'reference page exceeds bound' USING ERRCODE='54000';
  END IF;
  RETURN v_result;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid reference read payload' USING ERRCODE='22023';
END;
$$;

ALTER TABLE public.market_reference_chunk_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reference_snapshot_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reference_finalization_seals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reference_run_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reference_predecessor_pins ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reference_transfer_requests ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.market_reference_chunk_receipts,public.market_reference_snapshot_memberships,
  public.market_reference_finalization_seals,public.market_reference_run_bindings,
  public.market_reference_predecessor_pins,public.market_reference_transfer_requests
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader;
GRANT SELECT ON public.market_reference_chunk_receipts,public.market_reference_snapshot_memberships,
  public.market_reference_finalization_seals,public.market_reference_run_bindings,
  public.market_reference_predecessor_pins,public.market_reference_transfer_requests
  TO stock_agent_release_reader;

REVOKE ALL ON FUNCTION public.market_reference_canonical_json_v1(JSONB) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.market_reference_manifest_semantic_v1(JSONB) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.market_reference_security_semantic_v1(JSONB) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.record_market_discovery_reference(UUID,JSONB) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.claim_market_reference_transfer_request(UUID,TEXT,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.begin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.finalize_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.pin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.record_market_discovery_reference(UUID,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.begin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finalize_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.pin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;

DO $$ DECLARE name TEXT; BEGIN
  FOREACH name IN ARRAY ARRAY[
    'market_reference_chunk_receipts','market_reference_snapshot_memberships',
    'market_reference_finalization_seals','market_reference_run_bindings',
    'market_reference_predecessor_pins','market_reference_transfer_requests'
  ] LOOP
    EXECUTE format('DROP POLICY IF EXISTS release_evidence_select ON public.%I',name);
    EXECUTE format('CREATE POLICY release_evidence_select ON public.%I FOR SELECT TO stock_agent_release_reader USING (true)',name);
  END LOOP;
END; $$;

-- Consolidated from sql/migrations/20261007_discovery_cursor_context.sql
-- Carry only validated terminal source cursors into a later protected collection run.
-- The source task/run provenance stays attached so a caller cannot invent a watermark.
CREATE OR REPLACE FUNCTION public.try_market_discovery_cursor_timestamp(p_value TEXT)
RETURNS TIMESTAMPTZ LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
  IF p_value IS NULL OR p_value !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
    RETURN NULL;
  END IF;
  RETURN p_value::timestamptz;
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
  RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION public.try_market_discovery_cursor_timestamp(TEXT)
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;

CREATE OR REPLACE FUNCTION public.read_market_discovery_cursor_context(
  p_run_id UUID,
  p_limit INT DEFAULT 100
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_now TIMESTAMPTZ:=statement_timestamp();
  v_result JSONB;
BEGIN
  IF p_run_id IS NULL OR p_limit NOT BETWEEN 1 AND 100 OR NOT EXISTS(
    SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running'
  ) THEN
    RAISE EXCEPTION 'invalid discovery cursor context request' USING ERRCODE='22023';
  END IF;

  WITH candidate AS (
    SELECT
      t.id AS source_task_id,
      t.run_id AS source_run_id,
      t.capability_id,
      t.provider,
      t.updated_at AS source_updated_at,
      t.result->'source_cursor' AS cursor,
      t.result->>'theme_id' AS theme_id,
      t.result->>'cursor_key' AS cursor_key
    FROM public.market_discovery_stage_tasks t
    JOIN public.analysis_runs a ON a.id=t.run_id AND a.status='completed'
    WHERE t.run_id<>p_run_id
      AND t.state='succeeded'
      AND t.updated_at<=v_now
      AND jsonb_typeof(t.result)='object'
      AND jsonb_typeof(t.result->'source_cursor')='object'
      AND (t.result->'source_cursor') ?& ARRAY[
        'provider','capability_id','completed_through','active_window_start',
        'active_window_end','backlog_token','page','accepted_item_ids','next_retry_phase'
      ]
      AND ((t.result->'source_cursor')-ARRAY[
        'provider','capability_id','completed_through','active_window_start',
        'active_window_end','backlog_token','page','accepted_item_ids','next_retry_phase'
      ])='{}'::jsonb
      AND t.result->'source_cursor'->>'provider'=t.provider
      AND t.result->'source_cursor'->>'capability_id'=t.capability_id
      AND (
        t.result->'theme_id'='null'::jsonb
        OR (
          jsonb_typeof(t.result->'theme_id')='string'
          AND t.result->>'theme_id' ~ '^[a-z][a-z0-9_]{2,79}$'
        )
      )
      AND t.result->>'cursor_key'=t.capability_id||':'||COALESCE(t.result->>'theme_id','default')
      AND octet_length(t.result->>'cursor_key')<=161
      AND (
        t.result->'source_cursor'->'completed_through'='null'::jsonb
        OR (
          jsonb_typeof(t.result->'source_cursor'->'completed_through')='string'
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'completed_through'
          ) IS NOT NULL
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'completed_through'
          )<=v_now
        )
      )
      AND (
        (
          t.result->'source_cursor'->'active_window_start'='null'::jsonb
          AND t.result->'source_cursor'->'active_window_end'='null'::jsonb
        ) OR (
          jsonb_typeof(t.result->'source_cursor'->'active_window_start')='string'
          AND jsonb_typeof(t.result->'source_cursor'->'active_window_end')='string'
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_start'
          ) IS NOT NULL
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_end'
          ) IS NOT NULL
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_start'
          )<=public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_end'
          )
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_end'
          )<=v_now
          AND (
            t.result->'source_cursor'->'completed_through'='null'::jsonb
            OR (
              public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'active_window_start'
              )<=public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'completed_through'
              )
              AND public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'completed_through'
              )<=public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'active_window_end'
              )
            )
          )
        )
      )
      AND (
        t.result->'source_cursor'->'backlog_token'='null'::jsonb
        OR (
          jsonb_typeof(t.result->'source_cursor'->'backlog_token')='string'
          AND octet_length(t.result->'source_cursor'->>'backlog_token') BETWEEN 1 AND 2048
          AND t.result->'source_cursor'->>'backlog_token' !~ '[[:cntrl:]]'
          AND t.result->'source_cursor'->'active_window_start'<>'null'::jsonb
        )
      )
      AND jsonb_typeof(t.result->'source_cursor'->'page')='number'
      AND CASE
        WHEN t.result->'source_cursor'->>'page' ~ '^(?:[1-9]|10)$'
        THEN (t.result->'source_cursor'->>'page')::int BETWEEN 1 AND 10
        ELSE false
      END
      AND (
        t.result->'source_cursor'->'backlog_token'='null'::jsonb
        OR (t.result->'source_cursor'->>'page')::int>=2
      )
      AND jsonb_typeof(t.result->'source_cursor'->'accepted_item_ids')='array'
      AND jsonb_array_length(t.result->'source_cursor'->'accepted_item_ids')<=500
      AND NOT EXISTS(
        SELECT 1
        FROM jsonb_array_elements(t.result->'source_cursor'->'accepted_item_ids') item
        WHERE jsonb_typeof(item)<>'string'
           OR octet_length(item#>>'{}') NOT BETWEEN 1 AND 512
           OR item#>>'{}' ~ '[[:cntrl:]]'
      )
      AND NOT EXISTS(
        SELECT 1
        FROM jsonb_array_elements_text(t.result->'source_cursor'->'accepted_item_ids') item(value)
        GROUP BY value HAVING count(*)>1
      )
      AND (
        t.result->'source_cursor'->'next_retry_phase'='null'::jsonb
        OR t.result->'source_cursor'->>'next_retry_phase' IN (
          'pre-market','intraday','post-market','on-demand'
        )
      )
      AND (
        t.result->'source_cursor'->'active_window_start'<>'null'::jsonb
        OR (
          t.result->'source_cursor'->'backlog_token'='null'::jsonb
          AND jsonb_array_length(t.result->'source_cursor'->'accepted_item_ids')=0
        )
      )
  ), ranked AS (
    SELECT candidate.*,
      row_number() OVER (
        PARTITION BY cursor_key ORDER BY source_updated_at DESC,source_task_id DESC
      ) AS cursor_rank
    FROM candidate
  ), selected AS (
    SELECT * FROM ranked WHERE cursor_rank=1
    ORDER BY cursor_key LIMIT p_limit
  )
  SELECT jsonb_build_object(
    'source_cursors',COALESCE((
      SELECT jsonb_agg(
        cursor||jsonb_build_object(
          'task_key',cursor_key,
          'source_run_id',source_run_id,
          'source_task_id',source_task_id,
          'source_updated_at',source_updated_at
        ) ORDER BY cursor_key
      ) FROM selected
    ),'[]'::jsonb),
    'last_completed_scans',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'capability_id',capability_id,
        'theme_id',COALESCE(theme_id,'default'),
        'completed_through',cursor->>'completed_through',
        'source_run_id',source_run_id,
        'source_task_id',source_task_id
      ) ORDER BY cursor_key)
      FROM selected WHERE cursor->'completed_through'<>'null'::jsonb
    ),'[]'::jsonb)
  ) INTO v_result;

  IF octet_length(v_result::text)>524288 THEN
    RAISE EXCEPTION 'discovery cursor context exceeds bound' USING ERRCODE='54000';
  END IF;
  RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.read_market_discovery_cursor_context(UUID,INT)
  FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.read_market_discovery_cursor_context(UUID,INT)
  TO service_role;

-- Consolidated from sql/migrations/20261008_official_source_completion_contract.sql
-- Tighten official-source completion and cross-run cursor provenance.
-- This migration is additive; reviewed migrations through 20261007 remain immutable.
CREATE OR REPLACE FUNCTION public.record_market_intelligence_provider_v2(
  p_run_id UUID, p_completion_id UUID, p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_row JSONB;
  v_legacy_items JSONB;
  v_result JSONB;
  v_request_host TEXT;
  v_valid_request_host BOOLEAN;
BEGIN
  IF p_run_id IS NULL OR p_completion_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR jsonb_typeof(p_payload->'items')<>'array' THEN
    RAISE EXCEPTION 'invalid intelligence completion payload' USING ERRCODE='22023';
  END IF;
  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'items') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','run_item_id','receipt_id','provider','upstream_item_id','canonical_url','request_url',
         'published_at','retrieved_at','effective_at','reporting_at','entity_ids','security_ids',
         'discovery_status','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ])
       OR (v_row - ARRAY[
         'id','run_item_id','receipt_id','provider','upstream_item_id','canonical_url','request_url',
         'published_at','retrieved_at','effective_at','reporting_at','entity_ids','security_ids',
         'discovery_status','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ]) <> '{}'::jsonb
       OR COALESCE(char_length(v_row->>'upstream_item_id'),0) NOT BETWEEN 1 AND 512
       OR COALESCE(char_length(v_row->>'canonical_url'),0) NOT BETWEEN 1 AND 2048
       OR COALESCE(char_length(v_row->>'request_url'),0) NOT BETWEEN 1 AND 2048
       OR v_row->>'canonical_url' !~ '^https://' OR v_row->>'request_url' !~ '^https://'
       OR lower(v_row->>'request_url') ~ '(api[_-]?key|token|secret|password)='
       OR jsonb_typeof(v_row->'entity_ids')<>'array' OR jsonb_array_length(v_row->'entity_ids')>32
       OR jsonb_typeof(v_row->'security_ids')<>'array' OR jsonb_array_length(v_row->'security_ids')>32
       OR v_row->>'discovery_status' NOT IN ('qualified','no_event','insufficient_coverage')
       OR (v_row->>'discovery_status'='qualified' AND jsonb_array_length(v_row->'security_ids')=0) THEN
      RAISE EXCEPTION 'invalid provider evidence provenance' USING ERRCODE='22023';
    END IF;
    v_request_host := lower(substring(v_row->>'request_url' FROM '^https://([^/:?#]+)'));
    v_valid_request_host := CASE v_row->>'provider'
      WHEN 'gdelt' THEN v_request_host='api.gdeltproject.org'
      WHEN 'alpha_vantage' THEN v_request_host='www.alphavantage.co'
      WHEN 'finnhub' THEN v_request_host='finnhub.io'
      WHEN 'yahoo' THEN v_request_host='query1.finance.yahoo.com'
      WHEN 'sec_edgar' THEN v_request_host IN ('www.sec.gov','data.sec.gov')
      WHEN 'federal_register' THEN v_request_host='www.federalregister.gov'
      WHEN 'white_house' THEN v_request_host='www.whitehouse.gov'
      WHEN 'doe' THEN v_request_host='www.energy.gov'
      WHEN 'dod' THEN v_row->>'request_url' ~ '^https://www\.(defense|war)\.gov/DesktopModules/ArticleCS/RSS\.ashx\?ContentType=(1|9)&Site=945&max=10$'
      WHEN 'eia' THEN v_request_host IN ('api.eia.gov','www.eia.gov')
      WHEN 'fred' THEN v_request_host IN ('api.stlouisfed.org','fred.stlouisfed.org')
      WHEN 'bls' THEN v_request_host IN ('api.bls.gov','www.bls.gov')
      WHEN 'bea' THEN v_request_host IN ('apps.bea.gov','www.bea.gov')
      WHEN 'social' THEN v_request_host IN ('www.reddit.com','oauth.reddit.com')
      ELSE false END;
    IF NOT v_valid_request_host THEN
      RAISE EXCEPTION 'provider request URL host mismatch' USING ERRCODE='22023';
    END IF;
    IF v_row->>'provider'='dod' AND NOT (
      (
        v_row->>'request_url' ~ 'ContentType=9&Site=945&max=10$'
        AND v_row->>'canonical_url' ~ '^https://www\.(defense|war)\.gov/News/(Releases|Contracts)/[^?#]+$'
      ) OR (
        v_row->>'request_url' ~ 'ContentType=1&Site=945&max=10$'
        AND v_row->>'canonical_url' ~ '^https://www\.(defense|war)\.gov/News/(News-Stories|Features|Releases)/[^?#]+$'
      )
    ) THEN
      RAISE EXCEPTION 'provider item URL path mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;

  SELECT jsonb_agg(
    ((value - ARRAY['provider','request_url','retrieved_at','reporting_at','entity_ids','security_ids','discovery_status'])
      || jsonb_build_object(
        'canonical_url',
        CASE WHEN value->>'provider'='dod'
          THEN regexp_replace(value->>'request_url','^https://www\.war\.gov','https://www.defense.gov')
          ELSE value->>'request_url'
        END
      ) || CASE WHEN value->>'disposition'='near_duplicate' THEN jsonb_build_object('disposition','accepted','drop_reason',NULL) ELSE '{}'::jsonb END)
  ) INTO v_legacy_items FROM jsonb_array_elements(p_payload->'items');
  v_result := public.record_market_intelligence_legacy(
    p_run_id, p_completion_id, jsonb_set(p_payload, '{items}', COALESCE(v_legacy_items,'[]'::jsonb))
  );
  INSERT INTO public.market_source_item_provenance(
    source_item_id,provider,canonical_item_url,request_url,retrieved_at,reporting_at,
    entity_ids,security_ids,discovery_status
  )
  SELECT (value->>'id')::uuid,value->>'provider',value->>'canonical_url',value->>'request_url',
    (value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,
    value->'entity_ids',value->'security_ids',value->>'discovery_status'
  FROM jsonb_array_elements(p_payload->'items')
  ON CONFLICT (source_item_id) DO NOTHING;
  RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.record_market_intelligence_provider_v2(UUID,UUID,JSONB)
  FROM PUBLIC,anon,authenticated,service_role;


-- Carry only validated terminal source cursors into a later protected collection run.
-- The source task/run provenance stays attached so a caller cannot invent a watermark.
CREATE OR REPLACE FUNCTION public.try_market_discovery_cursor_timestamp(p_value TEXT)
RETURNS TIMESTAMPTZ LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
  IF p_value IS NULL OR p_value !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
    RETURN NULL;
  END IF;
  RETURN p_value::timestamptz;
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
  RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION public.try_market_discovery_cursor_timestamp(TEXT)
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;

CREATE OR REPLACE FUNCTION public.read_market_discovery_cursor_context(
  p_run_id UUID,
  p_limit INT DEFAULT 100
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_now TIMESTAMPTZ:=statement_timestamp();
  v_consuming_started_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  SELECT started_at INTO v_consuming_started_at
  FROM public.analysis_runs WHERE id=p_run_id AND status='running';
  IF p_run_id IS NULL OR p_limit NOT BETWEEN 1 AND 100
     OR v_consuming_started_at IS NULL OR v_consuming_started_at>v_now THEN
    RAISE EXCEPTION 'invalid discovery cursor context request' USING ERRCODE='22023';
  END IF;

  WITH candidate AS (
    SELECT
      t.id AS source_task_id,
      t.run_id AS source_run_id,
      t.capability_id,
      t.provider,
      t.updated_at AS source_updated_at,
      t.result->'source_cursor' AS cursor,
      t.result->>'theme_id' AS theme_id,
      t.result->>'cursor_key' AS cursor_key
    FROM public.market_discovery_stage_tasks t
    JOIN public.analysis_runs a ON a.id=t.run_id AND a.status='completed'
      AND a.finished_at IS NOT NULL AND a.finished_at<v_consuming_started_at
    WHERE t.run_id<>p_run_id
      AND t.state='succeeded'
      AND t.updated_at<=a.finished_at
      AND jsonb_typeof(t.result)='object'
      AND jsonb_typeof(t.result->'source_cursor')='object'
      AND (t.result->'source_cursor') ?& ARRAY[
        'provider','capability_id','completed_through','active_window_start',
        'active_window_end','backlog_token','page','accepted_item_ids','next_retry_phase'
      ]
      AND ((t.result->'source_cursor')-ARRAY[
        'provider','capability_id','completed_through','active_window_start',
        'active_window_end','backlog_token','page','accepted_item_ids','next_retry_phase',
        'continuation_token_history'
      ])='{}'::jsonb
      AND t.result->'source_cursor'->>'provider'=t.provider
      AND t.result->'source_cursor'->>'capability_id'=t.capability_id
      AND (
        t.result->'theme_id'='null'::jsonb
        OR (
          jsonb_typeof(t.result->'theme_id')='string'
          AND t.result->>'theme_id' ~ '^[a-z][a-z0-9_]{2,79}$'
        )
      )
      AND t.result->>'cursor_key'=t.capability_id||':'||COALESCE(t.result->>'theme_id','default')
      AND octet_length(t.result->>'cursor_key')<=161
      AND (
        t.result->'source_cursor'->'completed_through'='null'::jsonb
        OR (
          jsonb_typeof(t.result->'source_cursor'->'completed_through')='string'
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'completed_through'
          ) IS NOT NULL
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'completed_through'
          )<=a.finished_at
        )
      )
      AND (
        (
          t.result->'source_cursor'->'active_window_start'='null'::jsonb
          AND t.result->'source_cursor'->'active_window_end'='null'::jsonb
        ) OR (
          jsonb_typeof(t.result->'source_cursor'->'active_window_start')='string'
          AND jsonb_typeof(t.result->'source_cursor'->'active_window_end')='string'
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_start'
          ) IS NOT NULL
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_end'
          ) IS NOT NULL
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_start'
          )<=public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_end'
          )
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_end'
          )<=a.finished_at
          AND (
            t.result->'source_cursor'->'completed_through'='null'::jsonb
            OR (
              public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'active_window_start'
              )<=public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'completed_through'
              )
              AND public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'completed_through'
              )<=public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'active_window_end'
              )
            )
          )
        )
      )
      AND (
        t.result->'source_cursor'->'backlog_token'='null'::jsonb
        OR (
          jsonb_typeof(t.result->'source_cursor'->'backlog_token')='string'
          AND octet_length(t.result->'source_cursor'->>'backlog_token') BETWEEN 1 AND 2048
          AND t.result->'source_cursor'->>'backlog_token' !~ '[[:cntrl:]]'
          AND t.result->'source_cursor'->'active_window_start'<>'null'::jsonb
        )
      )
      AND jsonb_typeof(t.result->'source_cursor'->'page')='number'
      AND CASE
        WHEN t.result->'source_cursor'->>'page' ~ '^[0-9]{1,4}$'
        THEN (t.result->'source_cursor'->>'page')::int BETWEEN 1 AND
          CASE WHEN t.capability_id='white_house_sitemap' THEN 2001 ELSE 10 END
        ELSE false
      END
      AND (
        t.result->'source_cursor'->'backlog_token'='null'::jsonb
        OR (t.result->'source_cursor'->>'page')::int>=2
      )
      AND jsonb_typeof(t.result->'source_cursor'->'accepted_item_ids')='array'
      AND jsonb_array_length(t.result->'source_cursor'->'accepted_item_ids')<=500
      AND NOT EXISTS(
        SELECT 1
        FROM jsonb_array_elements(t.result->'source_cursor'->'accepted_item_ids') item
        WHERE jsonb_typeof(item)<>'string'
           OR octet_length(item#>>'{}') NOT BETWEEN 1 AND 512
           OR item#>>'{}' ~ '[[:cntrl:]]'
      )
      AND NOT EXISTS(
        SELECT 1
        FROM jsonb_array_elements_text(t.result->'source_cursor'->'accepted_item_ids') item(value)
        GROUP BY value HAVING count(*)>1
      )
      AND (
        t.result->'source_cursor'->'next_retry_phase'='null'::jsonb
        OR t.result->'source_cursor'->>'next_retry_phase' IN (
          'pre-market','intraday','post-market','on-demand'
        )
      )
      AND (
        NOT (t.result->'source_cursor' ? 'continuation_token_history') OR (
          jsonb_typeof(t.result->'source_cursor'->'continuation_token_history')='array'
          AND jsonb_array_length(t.result->'source_cursor'->'continuation_token_history')<=64
          AND NOT EXISTS(
            SELECT 1 FROM jsonb_array_elements(
              t.result->'source_cursor'->'continuation_token_history'
            ) identity
            WHERE jsonb_typeof(identity)<>'string'
               OR identity#>>'{}' !~ '^[0-9a-f]{64}$'
          )
          AND NOT EXISTS(
            SELECT 1 FROM jsonb_array_elements_text(
              t.result->'source_cursor'->'continuation_token_history'
            ) identity(value) GROUP BY value HAVING count(*)>1
          )
          AND (
            t.result->'source_cursor'->'backlog_token'='null'::jsonb
            OR encode(extensions.digest(convert_to(
              t.result->'source_cursor'->>'backlog_token','UTF8'
            ),'sha256'),'hex') IN (
              SELECT value FROM jsonb_array_elements_text(
                t.result->'source_cursor'->'continuation_token_history'
              ) identity(value)
            )
          )
        )
      )
      AND (
        t.result->'source_cursor'->'active_window_start'<>'null'::jsonb
        OR (
          t.result->'source_cursor'->'backlog_token'='null'::jsonb
          AND jsonb_array_length(t.result->'source_cursor'->'accepted_item_ids')=0
        )
      )
  ), ranked AS (
    SELECT candidate.*,
      row_number() OVER (
        PARTITION BY cursor_key ORDER BY source_updated_at DESC,source_task_id DESC
      ) AS cursor_rank
    FROM candidate
  ), selected AS (
    SELECT * FROM ranked WHERE cursor_rank=1
    ORDER BY cursor_key LIMIT p_limit
  )
  SELECT jsonb_build_object(
    'source_cursors',COALESCE((
      SELECT jsonb_agg(
        cursor||jsonb_build_object(
          'task_key',cursor_key,
          'source_run_id',source_run_id,
          'source_task_id',source_task_id,
          'source_updated_at',source_updated_at
        ) ORDER BY cursor_key
      ) FROM selected
    ),'[]'::jsonb),
    'last_completed_scans',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'capability_id',capability_id,
        'theme_id',COALESCE(theme_id,'default'),
        'completed_through',cursor->>'completed_through',
        'source_run_id',source_run_id,
        'source_task_id',source_task_id
      ) ORDER BY cursor_key)
      FROM selected WHERE cursor->'completed_through'<>'null'::jsonb
    ),'[]'::jsonb)
  ) INTO v_result;

  IF octet_length(v_result::text)>524288 THEN
    RAISE EXCEPTION 'discovery cursor context exceeds bound' USING ERRCODE='54000';
  END IF;
  RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.read_market_discovery_cursor_context(UUID,INT)
  FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.read_market_discovery_cursor_context(UUID,INT)
  TO service_role;

-- Consolidated from sql/migrations/20261009_reference_issuer_names.sql
-- Add issuer names to the existing resumable reference protocol without
-- changing the semantic bytes or stored shape of any version-1 revision.

ALTER TABLE public.market_security_reference_revisions
  ADD COLUMN IF NOT EXISTS semantic_encoding_version INT NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS issuer_names JSONB;

ALTER TABLE public.market_security_reference_revisions
  DROP CONSTRAINT IF EXISTS market_security_reference_revisions_semantic_version_check,
  ADD CONSTRAINT market_security_reference_revisions_semantic_version_check CHECK (
    (semantic_encoding_version=1 AND issuer_names IS NULL)
    OR (semantic_encoding_version=2 AND jsonb_typeof(issuer_names)='object'
        AND octet_length(issuer_names::text)<=32768)
  );

CREATE TABLE IF NOT EXISTS public.market_reference_transfer_responses (
  request_id UUID PRIMARY KEY REFERENCES public.market_reference_transfer_requests(request_id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  encoded_bytes INT NOT NULL CHECK (encoded_bytes BETWEEN 1 AND 196608),
  response_hash TEXT NOT NULL CHECK (response_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

DROP TRIGGER IF EXISTS market_reference_transfer_responses_append_only
  ON public.market_reference_transfer_responses;
CREATE TRIGGER market_reference_transfer_responses_append_only
BEFORE UPDATE OR DELETE ON public.market_reference_transfer_responses
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();

ALTER TABLE public.market_reference_transfer_responses ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_reference_transfer_responses
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader;
GRANT SELECT ON public.market_reference_transfer_responses TO stock_agent_release_reader;
DROP POLICY IF EXISTS release_evidence_select ON public.market_reference_transfer_responses;
CREATE POLICY release_evidence_select ON public.market_reference_transfer_responses
  FOR SELECT TO stock_agent_release_reader USING (true);

CREATE OR REPLACE FUNCTION public.market_reference_issuer_names_canonical_v2(p_names JSONB)
RETURNS JSONB LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE
  v_canonical TEXT;
  v_observed JSONB;
  v_former JSONB;
BEGIN
  IF jsonb_typeof(p_names)<>'object'
     OR NOT(p_names ?& ARRAY['canonical_name','observed_names','former_names'])
     OR (p_names-ARRAY['canonical_name','observed_names','former_names'])<>'{}'::jsonb
     OR jsonb_typeof(p_names->'canonical_name')<>'string'
     OR length(btrim(p_names->>'canonical_name')) NOT BETWEEN 1 AND 300
     OR jsonb_typeof(p_names->'observed_names')<>'array'
     OR jsonb_array_length(p_names->'observed_names') NOT BETWEEN 1 AND 16
     OR jsonb_typeof(p_names->'former_names')<>'array'
     OR jsonb_array_length(p_names->'former_names')>32 THEN
    RAISE EXCEPTION 'invalid issuer names' USING ERRCODE='22023';
  END IF;
  v_canonical:=p_names->>'canonical_name';
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_names->'observed_names') n
    WHERE jsonb_typeof(n)<>'string' OR length(btrim(n#>>'{}')) NOT BETWEEN 1 AND 300
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements_text(p_names->'observed_names') n
    GROUP BY n HAVING count(*)>1
  ) OR NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements_text(p_names->'observed_names') n
    WHERE n=v_canonical
  ) THEN
    RAISE EXCEPTION 'invalid issuer observed names' USING ERRCODE='22023';
  END IF;
  SELECT jsonb_agg(to_jsonb(n) ORDER BY convert_to(n,'UTF8')) INTO v_observed
  FROM jsonb_array_elements_text(p_names->'observed_names') n;
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_names->'former_names') f
    WHERE jsonb_typeof(f)<>'object'
      OR NOT(f ?& ARRAY['name','valid_from','valid_to'])
      OR (f-ARRAY['name','valid_from','valid_to'])<>'{}'::jsonb
      OR jsonb_typeof(f->'name')<>'string'
      OR length(btrim(f->>'name')) NOT BETWEEN 1 AND 300
      OR f->>'valid_from' !~ '^\d{4}-\d{2}-\d{2}$'
      OR f->>'valid_to' !~ '^\d{4}-\d{2}-\d{2}$'
      OR (f->>'valid_from')::date>(f->>'valid_to')::date
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_names->'former_names') f
    JOIN jsonb_array_elements_text(p_names->'observed_names') n ON n=f->>'name'
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_names->'former_names') f
    GROUP BY f->>'name',f->>'valid_from',f->>'valid_to' HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'invalid issuer former names' USING ERRCODE='22023';
  END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'name',f->>'name','valid_from',f->>'valid_from','valid_to',f->>'valid_to'
  ) ORDER BY convert_to(f->>'name','UTF8'),f->>'valid_from',f->>'valid_to'),'[]'::jsonb)
  INTO v_former FROM jsonb_array_elements(p_names->'former_names') f;
  RETURN jsonb_build_object(
    'canonical_name',v_canonical,'observed_names',v_observed,'former_names',v_former
  );
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
  RAISE EXCEPTION 'invalid issuer names' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.market_reference_security_semantic_v2(p_row JSONB)
RETURNS JSONB LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
  SELECT jsonb_build_object(
    'kind','security_revision','semantic_encoding_version',2,
    'value',jsonb_build_object(
      'revision',p_row->'revision','security_id',p_row->'security_id',
      'entity_id',p_row->'entity_id','ticker',p_row->'ticker','exchange',p_row->'exchange',
      'instrument_type',p_row->'instrument_type','eligible',p_row->'eligible',
      'exclusion_reasons',p_row->'exclusion_reasons','aliases',p_row->'aliases',
      'source_ids',p_row->'source_ids',
      'valid_from',to_char((p_row->>'valid_from')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'valid_to',CASE WHEN p_row->>'valid_to' IS NULL THEN NULL ELSE
        to_char((p_row->>'valid_to')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') END,
      'issuer_names',public.market_reference_issuer_names_canonical_v2(p_row->'issuer_names')
    )
  )
$$;

CREATE OR REPLACE FUNCTION public.claim_market_reference_transfer_request(
  p_run_id UUID,p_operation TEXT,p_payload JSONB,p_request_id UUID,
  p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_existing public.market_reference_transfer_requests%ROWTYPE;
  v_canonical TEXT;
  v_count BIGINT;
  v_bytes BIGINT;
  v_started TIMESTAMPTZ;
BEGIN
  IF p_run_id IS NULL OR p_request_id IS NULL
     OR p_operation NOT IN ('begin_discovery_reference','record_discovery_reference_chunk',
       'finalize_discovery_reference','pin_discovery_reference','read_discovery_reference')
     OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR p_encoded_bytes NOT BETWEEN 1 AND 262144
     OR p_request_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference transfer claim' USING ERRCODE='22023';
  END IF;
  v_canonical:=public.market_reference_canonical_json_v1(jsonb_build_object(
    'dry_run',false,'operation',p_operation,'payload',p_payload,
    'request_id',p_request_id::text,'run_id',p_run_id::text,'schema_version',1
  ));
  IF octet_length(convert_to(v_canonical,'UTF8'))<>p_encoded_bytes
     OR encode(extensions.digest(convert_to(v_canonical,'UTF8'),'sha256'),'hex')<>p_request_hash THEN
    RAISE EXCEPTION 'reference transfer claim hash mismatch' USING ERRCODE='22023';
  END IF;
  PERFORM 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
  WHERE i.id=p_run_id AND a.status='running' FOR UPDATE OF i,a;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_reference_transfer_requests
  WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_existing.run_id<>p_run_id OR v_existing.operation<>p_operation
       OR v_existing.encoded_bytes<>p_encoded_bytes OR v_existing.request_hash<>p_request_hash
       OR v_existing.request_payload<>p_payload THEN
      RAISE EXCEPTION 'reference transfer request idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN true;
  END IF;
  SELECT count(*),COALESCE(sum(encoded_bytes),0),min(created_at)
  INTO v_count,v_bytes,v_started FROM public.market_reference_transfer_requests
  WHERE run_id=p_run_id;
  IF v_count>=384 OR v_bytes+p_encoded_bytes>50331648
     OR (v_started IS NOT NULL AND clock_timestamp()-v_started>interval '90 seconds') THEN
    RAISE EXCEPTION 'reference transfer aggregate bound exceeded' USING ERRCODE='54000';
  END IF;
  INSERT INTO public.market_reference_transfer_requests(
    request_id,run_id,operation,encoded_bytes,request_hash,request_payload
  ) VALUES(p_request_id,p_run_id,p_operation,p_encoded_bytes,p_request_hash,p_payload);
  RETURN false;
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_reference_transfer_request(
  p_request_id UUID,p_result JSONB
)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_request public.market_reference_transfer_requests%ROWTYPE;
  v_existing public.market_reference_transfer_responses%ROWTYPE;
  v_bytes INT;
  v_hash TEXT;
  v_total BIGINT;
BEGIN
  SELECT * INTO v_request FROM public.market_reference_transfer_requests
  WHERE request_id=p_request_id;
  IF NOT FOUND OR jsonb_typeof(p_result)<>'object' THEN
    RAISE EXCEPTION 'invalid reference transfer response' USING ERRCODE='22023';
  END IF;
  -- Exact retries intentionally flip only the advisory duplicate flag. Bind and
  -- budget the durable response content so the same request remains idempotent.
  p_result:=p_result-'duplicate';
  v_bytes:=octet_length(p_result::text);
  IF v_bytes NOT BETWEEN 1 AND 196608 THEN
    RAISE EXCEPTION 'reference response exceeds bound' USING ERRCODE='54000';
  END IF;
  v_hash:=encode(extensions.digest(convert_to(
    public.market_reference_canonical_json_v1(p_result),'UTF8'
  ),'sha256'),'hex');
  SELECT * INTO v_existing FROM public.market_reference_transfer_responses
  WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_existing.run_id<>v_request.run_id OR v_existing.encoded_bytes<>v_bytes
       OR v_existing.response_hash<>v_hash THEN
      RAISE EXCEPTION 'reference response idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN;
  END IF;
  SELECT COALESCE(sum(encoded_bytes),0) INTO v_total
  FROM public.market_reference_transfer_responses WHERE run_id=v_request.run_id;
  IF v_total+v_bytes>67108864 THEN
    RAISE EXCEPTION 'reference response aggregate bound exceeded' USING ERRCODE='54000';
  END IF;
  INSERT INTO public.market_reference_transfer_responses(
    request_id,run_id,encoded_bytes,response_hash
  ) VALUES(p_request_id,v_request.run_id,v_bytes,v_hash);
END;
$$;

ALTER FUNCTION public.begin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  RENAME TO begin_market_discovery_reference_v1_internal;
ALTER FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT)
  RENAME TO record_market_discovery_reference_chunk_v1_internal;
ALTER FUNCTION public.finalize_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  RENAME TO finalize_market_discovery_reference_v1_internal;
ALTER FUNCTION public.pin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  RENAME TO pin_market_discovery_reference_v1_internal;
ALTER FUNCTION public.read_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  RENAME TO read_market_discovery_reference_v1_internal;

CREATE OR REPLACE FUNCTION public.begin_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_format INT;
BEGIN
  v_format:=COALESCE((p_payload->'manifest'->'manifest'->>'format_version')::int,1);
  IF v_format NOT IN (1,2) THEN
    RAISE EXCEPTION 'invalid reference begin format_version' USING ERRCODE='22023';
  END IF;
  v_result:=public.begin_market_discovery_reference_v1_internal(
    p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_discovery_reference_chunk(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_manifest_id UUID;
  v_begin public.market_reference_chunk_receipts%ROWTYPE;
  v_existing public.market_reference_chunk_receipts%ROWTYPE;
  v_entry JSONB;
  v_count INT;
  v_hash TEXT;
  v_format INT;
  v_result JSONB;
BEGIN
  v_manifest_id:=(p_payload->>'manifest_id')::uuid;
  SELECT * INTO v_begin FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1;
  v_format:=COALESCE((v_begin.payload->'manifest'->'manifest'->>'format_version')::int,1);
  IF v_format=1 THEN
    v_result:=public.record_market_discovery_reference_chunk_v1_internal(
      p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
    );
    PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
    RETURN v_result;
  END IF;
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'record_discovery_reference_chunk',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF v_format<>2 OR v_begin.run_id<>p_run_id
     OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest_id','chunk_index','chunk_count','entries','chunk_hash'])
     OR (p_payload-ARRAY['manifest_id','chunk_index','chunk_count','entries','chunk_hash'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'entries')<>'array'
     OR jsonb_array_length(p_payload->'entries') NOT BETWEEN 1 AND 88
     OR (p_payload->>'chunk_index')::int NOT BETWEEN 0 AND 511
     OR (p_payload->>'chunk_count')::int NOT BETWEEN 1 AND 512
     OR p_payload->>'chunk_hash' !~ '^[0-9a-f]{64}$'
     OR v_begin.chunk_count<>(p_payload->>'chunk_count')::int
     OR (p_payload->>'chunk_index')::int>=v_begin.chunk_count THEN
    RAISE EXCEPTION 'invalid reference chunk payload' USING ERRCODE='22023';
  END IF;
  FOR v_entry IN SELECT value FROM jsonb_array_elements(p_payload->'entries') LOOP
    IF jsonb_typeof(v_entry)<>'object'
       OR NOT(v_entry ?& ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash','semantic_encoding_version','issuer_names'])
       OR (v_entry-ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash','semantic_encoding_version','issuer_names'])<>'{}'::jsonb
       OR (v_entry->>'semantic_encoding_version')::int<>2
       OR v_entry->>'manifest_id'<>v_manifest_id::text
       OR v_entry->>'security_id' !~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'
       OR v_entry->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$'
       OR v_entry->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR public.market_reference_issuer_names_canonical_v2(v_entry->'issuer_names')<>v_entry->'issuer_names'
       OR encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
            public.market_reference_security_semantic_v2(v_entry)
          ),'UTF8'),'sha256'),'hex')<>v_entry->>'content_hash' THEN
      RAISE EXCEPTION 'reference security hash mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'entries') e
    GROUP BY e->>'security_id' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'entries') e
    GROUP BY (e->>'id')::uuid HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate reference security identity' USING ERRCODE='22023';
  END IF;
  SELECT count(*),encode(extensions.digest(convert_to(COALESCE(string_agg(
    (e.value->>'security_id')||chr(31)||(e.value->>'id')||chr(31)||(e.value->>'content_hash'),
    E'\n' ORDER BY e.ordinality),''),'UTF8'),'sha256'),'hex')
  INTO v_count,v_hash FROM jsonb_array_elements(p_payload->'entries') WITH ORDINALITY e(value,ordinality);
  IF v_hash<>p_payload->>'chunk_hash' THEN
    RAISE EXCEPTION 'reference chunk hash mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=(p_payload->>'chunk_index')::int;
  IF FOUND THEN
    IF v_existing.run_id<>p_run_id OR v_existing.chunk_count<>(p_payload->>'chunk_count')::int
       OR v_existing.entry_count<>v_count OR v_existing.chunk_hash<>v_hash
       OR v_existing.payload<>p_payload THEN
      RAISE EXCEPTION 'reference chunk idempotency mismatch' USING ERRCODE='22023';
    END IF;
    v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'chunk_index',v_existing.chunk_index,'duplicate',true);
  ELSE
    IF EXISTS(SELECT 1 FROM public.market_reference_finalization_seals WHERE manifest_id=v_manifest_id) THEN
      RAISE EXCEPTION 'reference snapshot is already finalized' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.market_reference_chunk_receipts(
      manifest_id,run_id,capability_id,chunk_index,chunk_count,entry_count,chunk_hash,
      predecessor_manifest_id,payload
    ) VALUES(
      v_manifest_id,p_run_id,v_begin.capability_id,(p_payload->>'chunk_index')::int,
      v_begin.chunk_count,v_count,v_hash,v_begin.predecessor_manifest_id,p_payload
    );
    v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'chunk_index',(p_payload->>'chunk_index')::int,'duplicate',false);
  END IF;
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid reference chunk payload' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.finalize_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_manifest_id UUID;
  v_begin public.market_reference_chunk_receipts%ROWTYPE;
  v_seal public.market_reference_finalization_seals%ROWTYPE;
  v_chunk_count INT; v_entry_count INT; v_min_chunk INT; v_max_chunk INT;
  v_root TEXT; v_entry JSONB; v_prior_revision UUID; v_revision_id UUID;
  v_ordinal INT:=0; v_manifest JSONB; v_result JSONB; v_format INT;
BEGIN
  v_manifest_id:=(p_payload->>'manifest_id')::uuid;
  SELECT * INTO v_begin FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1;
  v_format:=COALESCE((v_begin.payload->'manifest'->'manifest'->>'format_version')::int,1);
  IF v_format=1 THEN
    v_result:=public.finalize_market_discovery_reference_v1_internal(
      p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
    );
    PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
    RETURN v_result;
  END IF;
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'finalize_discovery_reference',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF v_format<>2 OR v_begin.run_id<>p_run_id OR jsonb_typeof(p_payload)<>'object'
     OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest_id','root_hash'])
     OR (p_payload-ARRAY['manifest_id','root_hash'])<>'{}'::jsonb
     OR p_payload->>'root_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference finalize payload' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_seal FROM public.market_reference_finalization_seals
  WHERE manifest_id=v_manifest_id;
  IF FOUND THEN
    IF v_seal.run_id<>p_run_id OR v_seal.root_hash<>p_payload->>'root_hash' THEN
      RAISE EXCEPTION 'reference finalization idempotency mismatch' USING ERRCODE='22023';
    END IF;
    v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_seal.security_count,'duplicate',true);
    PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
    RETURN v_result;
  END IF;
  SELECT count(*),COALESCE(sum(entry_count),0),min(chunk_index),max(chunk_index),
         encode(extensions.digest(convert_to(COALESCE(string_agg(chunk_hash,'' ORDER BY chunk_index),''),'UTF8'),'sha256'),'hex')
  INTO v_chunk_count,v_entry_count,v_min_chunk,v_max_chunk,v_root
  FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index>=0;
  IF v_chunk_count<>v_begin.chunk_count OR v_min_chunk<>0 OR v_max_chunk<>v_begin.chunk_count-1 THEN
    RAISE EXCEPTION 'reference chunk sequence mismatch' USING ERRCODE='22023';
  END IF;
  IF v_entry_count<>(v_begin.payload->>'security_count')::int THEN
    RAISE EXCEPTION 'reference snapshot count mismatch' USING ERRCODE='22023';
  END IF;
  IF v_root<>v_begin.chunk_hash OR v_root<>p_payload->>'root_hash' THEN
    RAISE EXCEPTION 'reference snapshot root mismatch' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    GROUP BY e->>'security_id' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    GROUP BY (e->>'id')::uuid HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate reference security identity' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    GROUP BY e->>'entity_id' HAVING count(DISTINCT e->'issuer_names')>1
  ) THEN
    RAISE EXCEPTION 'same entity must have identical issuer names' USING ERRCODE='22023';
  END IF;
  IF v_begin.predecessor_manifest_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.market_reference_finalization_seals s
    WHERE s.manifest_id=v_begin.predecessor_manifest_id AND s.capability_id=v_begin.capability_id
  ) THEN
    RAISE EXCEPTION 'reference predecessor membership mismatch' USING ERRCODE='22023';
  END IF;
  v_manifest:=v_begin.payload->'manifest';
  IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
       public.market_reference_manifest_semantic_v1(v_manifest)
     ),'UTF8'),'sha256'),'hex')<>v_manifest->>'content_hash' THEN
    RAISE EXCEPTION 'reference manifest hash mismatch' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_reference_manifests(
    id,run_id,reference_version,revision,capability_version,taxonomy_version,source_hash,
    valid_from,valid_to,manifest,content_hash
  ) VALUES(
    v_manifest_id,p_run_id,v_manifest->>'reference_version',(v_manifest->>'revision')::int,
    (v_manifest->>'capability_version')::int,(v_manifest->>'taxonomy_version')::int,
    v_manifest->>'source_hash',(v_manifest->>'valid_from')::timestamptz,
    (v_manifest->>'valid_to')::timestamptz,v_manifest->'manifest',v_manifest->>'content_hash'
  );
  FOR v_entry IN
    SELECT e.value FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') WITH ORDINALITY e(value,item_ordinal)
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    ORDER BY c.chunk_index,e.item_ordinal
  LOOP
    IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
         public.market_reference_security_semantic_v2(v_entry)
       ),'UTF8'),'sha256'),'hex')<>v_entry->>'content_hash' THEN
      RAISE EXCEPTION 'reference security hash mismatch' USING ERRCODE='22023';
    END IF;
    v_prior_revision:=NULL;
    IF v_begin.predecessor_manifest_id IS NOT NULL THEN
      SELECT m.security_revision_id INTO v_prior_revision
      FROM public.market_reference_snapshot_memberships m
      JOIN public.market_security_reference_revisions s ON s.id=m.security_revision_id
      WHERE m.manifest_id=v_begin.predecessor_manifest_id
        AND m.security_id=v_entry->>'security_id'
        AND s.content_hash=v_entry->>'content_hash'
        AND s.semantic_encoding_version=2
        AND s.issuer_names=v_entry->'issuer_names';
    END IF;
    IF v_prior_revision IS NULL THEN
      v_revision_id:=(v_entry->>'id')::uuid;
      INSERT INTO public.market_security_reference_revisions(
        id,manifest_id,run_id,revision,security_id,entity_id,ticker,exchange,instrument_type,
        eligible,exclusion_reasons,aliases,source_ids,valid_from,valid_to,content_hash,
        semantic_encoding_version,issuer_names
      ) VALUES(
        v_revision_id,v_manifest_id,p_run_id,(v_entry->>'revision')::int,
        v_entry->>'security_id',v_entry->>'entity_id',v_entry->>'ticker',v_entry->>'exchange',
        v_entry->>'instrument_type',(v_entry->>'eligible')::boolean,v_entry->'exclusion_reasons',
        v_entry->'aliases',v_entry->'source_ids',(v_entry->>'valid_from')::timestamptz,
        (v_entry->>'valid_to')::timestamptz,v_entry->>'content_hash',2,v_entry->'issuer_names'
      );
    ELSE
      v_revision_id:=v_prior_revision;
    END IF;
    INSERT INTO public.market_reference_snapshot_memberships(
      manifest_id,security_revision_id,security_id,ordinal
    ) VALUES(v_manifest_id,v_revision_id,v_entry->>'security_id',v_ordinal);
    v_ordinal:=v_ordinal+1;
  END LOOP;
  INSERT INTO public.market_reference_finalization_seals(
    manifest_id,run_id,capability_id,predecessor_manifest_id,chunk_count,security_count,root_hash
  ) VALUES(
    v_manifest_id,p_run_id,v_begin.capability_id,v_begin.predecessor_manifest_id,
    v_begin.chunk_count,v_entry_count,v_root
  );
  v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_entry_count,'duplicate',false);
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range OR unique_violation OR foreign_key_violation THEN
  RAISE EXCEPTION 'reference finalization conflict' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.pin_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB;
BEGIN
  v_result:=public.pin_market_discovery_reference_v1_internal(
    p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.read_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_result JSONB; v_manifest UUID; v_format INT; v_rows JSONB; v_count INT;
BEGIN
  v_result:=public.read_market_discovery_reference_v1_internal(
    p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  v_manifest:=(v_result->'binding'->>'manifest_id')::uuid;
  IF v_manifest IS NULL THEN
    v_format:=1;
  ELSE
    SELECT COALESCE((manifest->>'format_version')::int,1) INTO v_format
    FROM public.market_reference_manifests WHERE id=v_manifest;
  END IF;
  IF v_format=2 THEN
    SELECT COALESCE(jsonb_agg(
      row.value||jsonb_build_object(
        'semantic_encoding_version',s.semantic_encoding_version,
        'issuer_names',s.issuer_names
      ) ORDER BY row.ordinality
    ),'[]'::jsonb) INTO v_rows
    FROM jsonb_array_elements(v_result->'securities') WITH ORDINALITY row(value,ordinality)
    JOIN public.market_security_reference_revisions s ON s.id=(row.value->>'id')::uuid;
    v_result:=jsonb_set(v_result,'{securities}',v_rows);
  END IF;
  v_result:=jsonb_set(
    v_result,'{binding,issuer_names_status}',
    to_jsonb(CASE WHEN v_format=2 THEN 'available' ELSE 'issuer_names_unavailable' END)
  );
  -- The v1 reader already packs pages to the limit. Make room for the explicit
  -- name availability state without changing its cursor or ordering contract.
  WHILE octet_length(v_result::text)>196608 LOOP
    v_count:=jsonb_array_length(v_result->'securities');
    IF v_count=0 THEN
      RAISE EXCEPTION 'one reference row exceeds page bound' USING ERRCODE='54000';
    END IF;
    v_result:=jsonb_set(v_result,'{securities}',
      (v_result->'securities')-(v_count-1));
    v_result:=jsonb_set(v_result,'{complete}','false'::jsonb);
    v_result:=jsonb_set(v_result,'{next_after_security_id}',to_jsonb(
      v_result->'securities'->(v_count-2)->>'security_id'
    ));
  END LOOP;
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.market_reference_issuer_names_canonical_v2(JSONB)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.market_reference_security_semantic_v2(JSONB)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.finish_market_reference_transfer_request(UUID,JSONB)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.begin_market_discovery_reference_v1_internal(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.record_market_discovery_reference_chunk_v1_internal(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.finalize_market_discovery_reference_v1_internal(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.pin_market_discovery_reference_v1_internal(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_reference_v1_internal(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.claim_market_reference_transfer_request(UUID,TEXT,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.begin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.finalize_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.pin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.begin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finalize_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.pin_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_market_discovery_reference(UUID,JSONB,UUID,INT,TEXT) TO service_role;

-- Consolidated from sql/migrations/20261010_bounded_adaptive_enrichment.sql
-- Immutable adaptive-enrichment selection, current-reference binding, and
-- primary-evidence validation. These records authorize bounded research
-- transport only; execution authority is structurally absent.

CREATE TABLE IF NOT EXISTS public.market_enrichment_selection_manifests (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  selection_stage TEXT NOT NULL CHECK (selection_stage IN ('holding_quotes','initial','filing_documents')),
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  request_count INT NOT NULL CHECK (request_count BETWEEN 0 AND 100),
  provider_reservations JSONB NOT NULL CHECK (jsonb_typeof(provider_reservations)='object' AND octet_length(provider_reservations::text)<=2048),
  deferred_reasons JSONB NOT NULL CHECK (jsonb_typeof(deferred_reasons)='object' AND octet_length(deferred_reasons::text)<=16384),
  manifest JSONB NOT NULL CHECK (jsonb_typeof(manifest)='object' AND octet_length(manifest::text)<=65536),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id,selection_stage)
);

CREATE TABLE IF NOT EXISTS public.market_enrichment_request_descriptors (
  id UUID PRIMARY KEY,
  manifest_id UUID NOT NULL REFERENCES public.market_enrichment_selection_manifests(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  task_id UUID NOT NULL UNIQUE REFERENCES public.market_discovery_stage_tasks(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL CHECK (provider IN ('sec_edgar','yahoo','gdelt')),
  capability_id TEXT NOT NULL CHECK (capability_id IN ('sec_issuer_submissions','sec_filing_document','yahoo_security_quote','gdelt_theme_search')),
  query_kind TEXT NOT NULL CHECK (query_kind IN ('issuer_submissions','filing_document','quote','theme_search')),
  descriptor JSONB NOT NULL CHECK (jsonb_typeof(descriptor)='object' AND octet_length(descriptor::text)<=32768),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (manifest_id,content_hash)
);

DROP TRIGGER IF EXISTS market_enrichment_selection_manifests_append_only ON public.market_enrichment_selection_manifests;
CREATE TRIGGER market_enrichment_selection_manifests_append_only BEFORE UPDATE OR DELETE ON public.market_enrichment_selection_manifests
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_enrichment_request_descriptors_append_only ON public.market_enrichment_request_descriptors;
CREATE TRIGGER market_enrichment_request_descriptors_append_only BEFORE UPDATE OR DELETE ON public.market_enrichment_request_descriptors
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();

CREATE OR REPLACE FUNCTION public.market_enrichment_manifest_semantic_hash_v1(p_manifest JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
  SELECT encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
    p_manifest-ARRAY['manifest_id','semantic_hash']
  ),'UTF8'),'sha256'),'hex')
$$;

CREATE OR REPLACE FUNCTION public.market_enrichment_request_semantic_hash_v1(p_request JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
  SELECT encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
    p_request-'descriptor_hash'
  ),'UTF8'),'sha256'),'hex')
$$;

CREATE OR REPLACE FUNCTION public.market_exposure_fact_semantic_hash_v1(p_fact JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
  SELECT encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
    p_fact
  ),'UTF8'),'sha256'),'hex')
$$;

CREATE OR REPLACE FUNCTION public.seal_market_enrichment_selection(p_run_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  m JSONB; r JSONB; v_hash TEXT; v_manifest_id UUID; v_existing public.market_enrichment_selection_manifests%ROWTYPE;
  v_request_count INT:=0; v_expected JSONB; v_sec_submissions INT:=0;
  v_sec_documents INT:=0; v_yahoo_quotes INT:=0;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>262144
     OR NOT (p_payload ?& ARRAY['manifest','requests'])
     OR (p_payload-ARRAY['manifest','requests'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'requests')<>'array'
     OR jsonb_array_length(p_payload->'requests')>100 THEN
    RAISE EXCEPTION 'invalid enrichment selection' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.analysis_runs a JOIN public.market_intelligence_runs i ON i.id=a.id
                WHERE a.id=p_run_id AND a.status='running') THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  m:=p_payload->'manifest';
  IF jsonb_typeof(m)<>'object'
     OR NOT (m ?& ARRAY['manifest_id','run_id','phase','selection_stage','schema_version','execution_allowed','provider_reservations','deferred_reasons','request_descriptors','semantic_hash'])
     OR (m-ARRAY['manifest_id','run_id','phase','selection_stage','schema_version','execution_allowed','provider_reservations','deferred_reasons','request_descriptors','semantic_hash'])<>'{}'::jsonb
     OR m->>'run_id' IS DISTINCT FROM p_run_id::text OR m->'schema_version'<>'1'::jsonb
     OR m->>'selection_stage' NOT IN ('holding_quotes','initial','filing_documents')
     OR m->'execution_allowed'<>'false'::jsonb OR jsonb_typeof(m->'provider_reservations')<>'object'
     OR jsonb_typeof(m->'deferred_reasons')<>'object' OR jsonb_typeof(m->'request_descriptors')<>'array'
     OR jsonb_array_length(m->'request_descriptors')<>jsonb_array_length(p_payload->'requests')
     OR m->>'semantic_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid enrichment selection manifest' USING ERRCODE='22023';
  END IF;
  v_expected:=CASE m->>'phase'
    WHEN 'pre-market' THEN '{"gdelt_reverse":2,"sec_filing_document":3,"sec_issuer_submissions":3,"yahoo_security_quote":4}'::jsonb
    WHEN 'intraday' THEN '{"gdelt_reverse":1,"sec_filing_document":1,"sec_issuer_submissions":1,"yahoo_security_quote":1}'::jsonb
    WHEN 'post-market' THEN '{"gdelt_reverse":2,"sec_filing_document":2,"sec_issuer_submissions":2,"yahoo_security_quote":2}'::jsonb
    WHEN 'on-demand' THEN '{"gdelt_reverse":1,"sec_filing_document":1,"sec_issuer_submissions":1,"yahoo_security_quote":1}'::jsonb
    ELSE NULL END;
  IF v_expected IS NULL OR m->'provider_reservations'<>v_expected THEN
    RAISE EXCEPTION 'enrichment provider reservation mismatch' USING ERRCODE='22023';
  END IF;
  v_hash:=public.market_enrichment_manifest_semantic_hash_v1(m);
  IF v_hash<>m->>'semantic_hash' THEN
    RAISE EXCEPTION 'enrichment selection hash mismatch' USING ERRCODE='22023';
  END IF;
  v_manifest_id:=(m->>'manifest_id')::uuid;
  SELECT * INTO v_existing FROM public.market_enrichment_selection_manifests
  WHERE run_id=p_run_id AND selection_stage=m->>'selection_stage';
  IF FOUND THEN
    IF v_existing.id<>v_manifest_id OR v_existing.manifest<>m OR v_existing.content_hash<>v_hash
       OR (SELECT count(*) FROM public.market_enrichment_request_descriptors d WHERE d.manifest_id=v_manifest_id)
          <>jsonb_array_length(p_payload->'requests')
       OR EXISTS(
         SELECT 1 FROM jsonb_array_elements(p_payload->'requests') x
         LEFT JOIN public.market_enrichment_request_descriptors d
           ON d.id=(x->>'request_id')::uuid AND d.manifest_id=v_manifest_id
         LEFT JOIN public.market_discovery_stage_tasks t
           ON t.id=d.task_id AND t.run_id=p_run_id
         WHERE jsonb_typeof(x)<>'object'
           OR NOT (x ?& ARRAY['request_id','task_id','stage','provider','capability_id','query_kind','descriptor','dependency_ids','requested_window','request_budget','execution_allowed','descriptor_hash'])
           OR (x-ARRAY['request_id','task_id','stage','provider','capability_id','query_kind','descriptor','dependency_ids','requested_window','request_budget','execution_allowed','descriptor_hash'])<>'{}'::jsonb
           OR d.id IS NULL OR d.task_id<>(x->>'task_id')::uuid
           OR d.provider<>x->>'provider' OR d.capability_id<>x->>'capability_id'
           OR d.query_kind<>x->>'query_kind' OR d.descriptor<>x->'descriptor'
           OR d.content_hash<>x->>'descriptor_hash' OR t.stage<>x->>'stage'
           OR t.dependency_ids<>x->'dependency_ids' OR t.requested_window<>x->'requested_window'
           OR t.request_budget<>(x->>'request_budget')::int OR x->'execution_allowed'<>'false'::jsonb
       ) THEN
      RAISE EXCEPTION 'enrichment selection replay mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'request_count',v_existing.request_count,'duplicate',true);
  END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'requests') x
            GROUP BY (x->>'request_id')::uuid HAVING count(*)>1) THEN
    RAISE EXCEPTION 'duplicate enrichment request identity' USING ERRCODE='22023';
  END IF;
  IF (SELECT count(*) FROM public.market_discovery_stage_tasks WHERE run_id=p_run_id)
       + jsonb_array_length(p_payload->'requests') > 100
     OR (m->>'selection_stage'='initial' AND (
       SELECT count(DISTINCT x->'descriptor'->>'entity_id')
       FROM jsonb_array_elements(p_payload->'requests') x
     ) > 4) THEN
    RAISE EXCEPTION 'enrichment task capacity exceeded' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_enrichment_selection_manifests(
    id,run_id,selection_stage,phase,request_count,provider_reservations,deferred_reasons,manifest,content_hash
  ) VALUES(
    v_manifest_id,p_run_id,m->>'selection_stage',m->>'phase',jsonb_array_length(p_payload->'requests'),
    m->'provider_reservations',m->'deferred_reasons',m,v_hash
  );
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'requests') LOOP
    IF jsonb_typeof(r)<>'object'
       OR NOT (r ?& ARRAY['request_id','task_id','stage','provider','capability_id','query_kind','descriptor','dependency_ids','requested_window','request_budget','execution_allowed','descriptor_hash'])
       OR (r-ARRAY['request_id','task_id','stage','provider','capability_id','query_kind','descriptor','dependency_ids','requested_window','request_budget','execution_allowed','descriptor_hash'])<>'{}'::jsonb
       OR r->>'request_id' IS DISTINCT FROM r->>'task_id'
       OR r->'execution_allowed'<>'false'::jsonb OR r->>'stage' NOT IN ('enrich','quote','resolve')
       OR jsonb_typeof(r->'descriptor')<>'object' OR jsonb_typeof(r->'dependency_ids')<>'array'
       OR jsonb_array_length(r->'dependency_ids')>32 OR jsonb_typeof(r->'requested_window')<>'object'
       OR (r->>'request_budget')::int<>1 OR r->>'descriptor_hash' !~ '^[0-9a-f]{64}$'
       OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(m->'request_descriptors') x
                     WHERE x->>'request_id'=r->>'request_id' AND x->>'descriptor_hash'=r->>'descriptor_hash') THEN
      RAISE EXCEPTION 'invalid enrichment request descriptor' USING ERRCODE='22023';
    END IF;
    IF NOT (r->'descriptor' ?& ARRAY['adverse_path','cache_key','cik','dependency_task_ids','entity_id','event_ids','hypothesis_ids','instrument_type','priority','reference_manifest_id','reservation_id','role','security_id','security_revision_id','source_item_ids','source_receipt_id','theme_id','ticker'])
       OR r->'descriptor'->'adverse_path' NOT IN ('true'::jsonb,'false'::jsonb)
       OR r->'descriptor'->>'cache_key' !~ '^[0-9a-f]{64}$'
       OR r->'descriptor'->>'cik' !~ '^[0-9]{10}$' OR r->'descriptor'->>'cik'='0000000000'
       OR r->'descriptor'->'dependency_task_ids'<>r->'dependency_ids'
       OR jsonb_typeof(r->'descriptor'->'event_ids')<>'array'
       OR jsonb_array_length(r->'descriptor'->'event_ids') NOT BETWEEN 1 AND 64
       OR jsonb_typeof(r->'descriptor'->'hypothesis_ids')<>'array'
       OR jsonb_array_length(r->'descriptor'->'hypothesis_ids') NOT BETWEEN 1 AND 64
       OR jsonb_typeof(r->'descriptor'->'source_item_ids')<>'array'
       OR jsonb_array_length(r->'descriptor'->'source_item_ids')>64
       OR r->'descriptor'->>'priority' !~ '^(0|[1-9][0-9]?|100)$'
       OR r->'descriptor'->>'source_receipt_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR NOT EXISTS(SELECT 1 FROM public.market_source_quota_reservations q
                     WHERE q.id=(r->'descriptor'->>'reservation_id')::uuid
                       AND q.run_id=p_run_id AND q.provider=r->>'provider'
                       AND q.reserved_requests>(
                         SELECT count(*) FROM public.market_enrichment_request_descriptors used
                         WHERE used.run_id=p_run_id AND used.provider=r->>'provider'
                       )) THEN
      RAISE EXCEPTION 'invalid enrichment request binding' USING ERRCODE='22023';
    END IF;
    IF r->>'query_kind'='issuer_submissions' THEN
      IF m->>'selection_stage'<>'initial' OR r->>'stage'<>'enrich'
         OR r->>'provider'<>'sec_edgar' OR r->>'capability_id'<>'sec_issuer_submissions'
         OR ((r->'descriptor')-ARRAY['adverse_path','cache_key','cik','dependency_task_ids','entity_id','event_ids','hypothesis_ids','instrument_type','issuer_entity_id','priority','reference_manifest_id','reservation_id','role','security_id','security_revision_id','source_item_ids','source_receipt_id','theme_id','ticker'])<>'{}'::jsonb
         OR r->'descriptor'->>'issuer_entity_id'<>r->'descriptor'->>'entity_id' THEN
        RAISE EXCEPTION 'invalid issuer submissions descriptor' USING ERRCODE='22023';
      END IF;
      v_sec_submissions:=v_sec_submissions+1;
    ELSIF r->>'query_kind'='filing_document' THEN
      IF m->>'selection_stage'<>'filing_documents' OR r->>'stage'<>'enrich'
         OR r->>'provider'<>'sec_edgar' OR r->>'capability_id'<>'sec_filing_document'
         OR ((r->'descriptor')-ARRAY['accepted_at','accession_number','adverse_path','cache_key','cik','dependency_task_ids','entity_id','event_ids','filing_date','form','hypothesis_ids','instrument_type','primary_document','priority','reference_manifest_id','reporting_period_end','reservation_id','role','security_id','security_revision_id','source_item_ids','source_receipt_id','submissions_response_hash','theme_id','ticker'])<>'{}'::jsonb
         OR r->'descriptor'->>'accession_number' !~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'
         OR r->'descriptor'->>'primary_document' !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$'
         OR r->'descriptor'->>'form' !~ '^(10-K|10-Q|8-K|20-F|40-F)(/A)?$'
         OR r->'descriptor'->>'submissions_response_hash' !~ '^[0-9a-f]{64}$'
         OR r->'descriptor'->>'filing_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
        RAISE EXCEPTION 'invalid filing document descriptor' USING ERRCODE='22023';
      END IF;
      v_sec_documents:=v_sec_documents+1;
    ELSIF r->>'query_kind'='quote' THEN
      IF m->>'selection_stage' NOT IN ('holding_quotes','initial') OR r->>'stage'<>'quote'
         OR r->>'provider'<>'yahoo' OR r->>'capability_id'<>'yahoo_security_quote'
         OR ((r->'descriptor')-ARRAY['adverse_path','cache_key','cik','dependency_task_ids','entity_id','event_ids','hypothesis_ids','instrument_type','priority','reference_manifest_id','reservation_id','role','security_id','security_revision_id','source_item_ids','source_receipt_id','theme_id','ticker'])<>'{}'::jsonb THEN
        RAISE EXCEPTION 'invalid selected quote descriptor' USING ERRCODE='22023';
      END IF;
      v_yahoo_quotes:=v_yahoo_quotes+1;
    ELSE
      RAISE EXCEPTION 'unsupported enrichment query kind' USING ERRCODE='22023';
    END IF;
    v_hash:=public.market_enrichment_request_semantic_hash_v1(r);
    IF v_hash<>r->>'descriptor_hash' THEN
      RAISE EXCEPTION 'enrichment request hash mismatch' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS(
      SELECT 1 FROM public.market_reference_run_bindings b
      JOIN public.market_reference_snapshot_memberships member ON member.manifest_id=b.manifest_id
      JOIN public.market_security_reference_revisions s ON s.id=member.security_revision_id
      WHERE b.run_id=p_run_id AND b.capability_id='sec_company_tickers_universe'
        AND b.reference_status IN ('healthy','reference_stale')
        AND b.manifest_id=(r->'descriptor'->>'reference_manifest_id')::uuid
        AND s.id=(r->'descriptor'->>'security_revision_id')::uuid AND s.eligible
        AND s.entity_id=r->'descriptor'->>'entity_id'
        AND s.security_id=r->'descriptor'->>'security_id'
        AND s.ticker=r->'descriptor'->>'ticker'
        AND s.instrument_type=r->'descriptor'->>'instrument_type'
    ) THEN
      RAISE EXCEPTION 'enrichment current reference membership mismatch' USING ERRCODE='22023';
    END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(r->'dependency_ids') dependency
              WHERE NOT EXISTS(SELECT 1 FROM public.market_discovery_stage_tasks d
                               WHERE d.id=dependency::uuid AND d.run_id=p_run_id AND d.state='succeeded')) THEN
      RAISE EXCEPTION 'enrichment dependency mismatch' USING ERRCODE='22023';
    END IF;
    IF r->>'query_kind'='filing_document' AND (
      jsonb_array_length(r->'dependency_ids')<>1 OR NOT EXISTS(
        SELECT 1
        FROM public.market_discovery_stage_tasks parent
        JOIN public.market_enrichment_request_descriptors parent_request
          ON parent_request.task_id=parent.id AND parent_request.run_id=parent.run_id
        JOIN (
          SELECT checkpoint.run_id,checkpoint.cache_key,checkpoint.source_receipt_id,checkpoint.payload
          FROM public.market_collection_checkpoints checkpoint
          UNION ALL
          SELECT history.run_id,history.cache_key,history.source_receipt_id,history.payload
          FROM public.market_collection_checkpoint_history history
        ) checkpoint
          ON checkpoint.run_id=parent.run_id
         AND checkpoint.cache_key=parent.result->'checkpoint'->>'cache_key'
         AND checkpoint.cache_key=parent_request.descriptor->>'cache_key'
         AND checkpoint.source_receipt_id=(parent_request.descriptor->>'source_receipt_id')::uuid
        CROSS JOIN LATERAL jsonb_array_elements(checkpoint.payload->'items') item
        WHERE parent.id=(r->'dependency_ids'->>0)::uuid
          AND parent.run_id=p_run_id AND parent.state='succeeded'
          AND parent.provider='sec_edgar' AND parent.capability_id='sec_issuer_submissions'
          AND parent.query_kind='issuer_submissions'
          AND parent.query_hash=parent_request.content_hash
          AND parent_request.provider='sec_edgar'
          AND parent_request.capability_id='sec_issuer_submissions'
          AND parent_request.query_kind='issuer_submissions'
          AND parent_request.descriptor->>'entity_id'=r->'descriptor'->>'entity_id'
          AND parent_request.descriptor->>'security_id'=r->'descriptor'->>'security_id'
          AND parent_request.descriptor->>'security_revision_id'=r->'descriptor'->>'security_revision_id'
          AND parent_request.descriptor->>'reference_manifest_id'=r->'descriptor'->>'reference_manifest_id'
          AND parent_request.descriptor->>'cik'=r->'descriptor'->>'cik'
          AND checkpoint.payload->'receipt'->>'provider'='sec_edgar'
          AND checkpoint.payload->'receipt'->>'status' IN ('succeeded','cache_hit')
          AND checkpoint.payload->'receipt'->>'cache_key'=checkpoint.cache_key
          AND checkpoint.payload->'receipt'->>'source_receipt_id'=checkpoint.source_receipt_id::text
          AND checkpoint.payload->'receipt'->>'response_hash'=r->'descriptor'->>'submissions_response_hash'
          AND item->>'provider'='sec_edgar' AND item->>'authority'='official'
          AND (item->>'request_url')=(
              'https://data.sec.gov/submissions/CIK'||(r->'descriptor'->>'cik')||'.json'
          )
          AND (item->>'source_url')=(
              'https://www.sec.gov/Archives/edgar/data/'
              ||((r->'descriptor'->>'cik')::bigint)::text||'/'
              ||replace(r->'descriptor'->>'accession_number','-','')||'/'
              ||(r->'descriptor'->>'primary_document')
          )
          AND item->'metadata'->>'issuer_cik'=r->'descriptor'->>'cik'
          AND item->'metadata'->>'accession_number'=r->'descriptor'->>'accession_number'
          AND item->'metadata'->>'form'=r->'descriptor'->>'form'
          AND item->'metadata'->>'primary_document'=r->'descriptor'->>'primary_document'
          AND item->'metadata'->>'filing_date'=r->'descriptor'->>'filing_date'
          AND item->'metadata'->>'reporting_period_end' IS NOT DISTINCT FROM
              r->'descriptor'->>'reporting_period_end'
          AND (item->'metadata'->>'accepted_at')::timestamptz IS NOT DISTINCT FROM
              (r->'descriptor'->>'accepted_at')::timestamptz
          AND item->'metadata'->>'submissions_response_hash'=
              r->'descriptor'->>'submissions_response_hash'
          AND (SELECT count(*) FROM jsonb_array_elements(checkpoint.payload->'items') candidate
               WHERE candidate->'metadata'->>'accession_number'=
                     r->'descriptor'->>'accession_number')=1
      )
    ) THEN
      RAISE EXCEPTION 'filing document lacks exact issuer submissions membership' USING ERRCODE='22023';
    END IF;
    IF r->>'query_kind'='quote' AND (
      r->>'provider'<>'yahoo' OR r->>'capability_id'<>'yahoo_security_quote'
      OR r->'descriptor'->>'source_receipt_id' !~ '^[0-9a-f-]{36}$'
      OR r->'descriptor'->>'cache_key' !~ '^[0-9a-f]{64}$'
      OR NOT EXISTS(SELECT 1 FROM public.market_source_quota_reservations q
                    WHERE q.id=(r->'descriptor'->>'reservation_id')::uuid AND q.run_id=p_run_id AND q.provider='yahoo')
    ) THEN
      RAISE EXCEPTION 'invalid selected quote descriptor' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.market_discovery_stage_tasks(
      id,run_id,stage,capability_id,provider,query_kind,query_hash,dependency_ids,requested_window,state,attempt_count,request_budget,result
    ) VALUES(
      (r->>'task_id')::uuid,p_run_id,r->>'stage',r->>'capability_id',r->>'provider',r->>'query_kind',
      r->>'descriptor_hash',r->'dependency_ids',r->'requested_window','planned',0,1,'{}'::jsonb
    );
    INSERT INTO public.market_enrichment_request_descriptors(
      id,manifest_id,run_id,task_id,provider,capability_id,query_kind,descriptor,content_hash
    ) VALUES(
      (r->>'request_id')::uuid,v_manifest_id,p_run_id,(r->>'task_id')::uuid,r->>'provider',
      r->>'capability_id',r->>'query_kind',r->'descriptor',r->>'descriptor_hash'
    );
    v_request_count:=v_request_count+1;
  END LOOP;
  IF (m->>'selection_stage'='initial' AND (
        v_sec_submissions>(v_expected->>'sec_issuer_submissions')::int
        OR v_yahoo_quotes>(v_expected->>'yahoo_security_quote')::int
      )) OR (m->>'selection_stage'='filing_documents'
        AND v_sec_documents>(v_expected->>'sec_filing_document')::int) THEN
    RAISE EXCEPTION 'enrichment provider capacity exceeded' USING ERRCODE='22023';
  END IF;
  RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'request_count',v_request_count,'duplicate',false);
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR foreign_key_violation OR unique_violation THEN
  RAISE EXCEPTION 'invalid enrichment selection' USING ERRCODE='22023';
END;
$$;

ALTER FUNCTION public.claim_market_intelligence_quote(UUID,JSONB)
  RENAME TO claim_market_intelligence_quote_v1_internal;

CREATE OR REPLACE FUNCTION public.claim_market_intelligence_quote(p_run_id UUID,p_input JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_descriptor public.market_enrichment_request_descriptors%ROWTYPE; v_task public.market_discovery_stage_tasks%ROWTYPE;
BEGIN
  IF p_input IS NULL OR jsonb_typeof(p_input)<>'object'
     OR NOT (p_input ?& ARRAY['ticker','instrument_type','security_revision_id','reference_manifest_id','selection_manifest_id','selected_task_id','cache_key','source_receipt_id','reservation_id'])
     OR (p_input-ARRAY['ticker','instrument_type','security_revision_id','reference_manifest_id','selection_manifest_id','selected_task_id','cache_key','source_receipt_id','reservation_id'])<>'{}'::jsonb THEN
    RAISE EXCEPTION 'invalid quote collection identity' USING ERRCODE='22023';
  END IF;
  SELECT d.* INTO v_descriptor FROM public.market_enrichment_request_descriptors d
  WHERE d.run_id=p_run_id AND d.manifest_id=(p_input->>'selection_manifest_id')::uuid
    AND d.task_id=(p_input->>'selected_task_id')::uuid AND d.provider='yahoo'
    AND d.capability_id='yahoo_security_quote' AND d.query_kind='quote';
  IF NOT FOUND OR v_descriptor.descriptor->>'ticker' IS DISTINCT FROM p_input->>'ticker'
     OR v_descriptor.descriptor->>'instrument_type' IS DISTINCT FROM p_input->>'instrument_type'
     OR v_descriptor.descriptor->>'security_revision_id' IS DISTINCT FROM p_input->>'security_revision_id'
     OR v_descriptor.descriptor->>'reference_manifest_id' IS DISTINCT FROM p_input->>'reference_manifest_id'
     OR v_descriptor.descriptor->>'cache_key' IS DISTINCT FROM p_input->>'cache_key'
     OR v_descriptor.descriptor->>'source_receipt_id' IS DISTINCT FROM p_input->>'source_receipt_id'
     OR v_descriptor.descriptor->>'reservation_id' IS DISTINCT FROM p_input->>'reservation_id' THEN
    RAISE EXCEPTION 'quote is not a frozen selected descriptor' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_task FROM public.market_discovery_stage_tasks WHERE id=v_descriptor.task_id AND run_id=p_run_id;
  IF NOT FOUND OR v_task.state<>'attempting' OR v_task.attempt_count<>1 OR v_task.query_hash<>v_descriptor.content_hash THEN
    RAISE EXCEPTION 'quote attempt barrier is unavailable' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(
    SELECT 1 FROM public.market_reference_run_bindings b
    JOIN public.market_reference_snapshot_memberships member ON member.manifest_id=b.manifest_id
    JOIN public.market_security_reference_revisions s ON s.id=member.security_revision_id
    WHERE b.run_id=p_run_id AND b.capability_id='sec_company_tickers_universe'
      AND b.manifest_id=(p_input->>'reference_manifest_id')::uuid
      AND s.id=(p_input->>'security_revision_id')::uuid AND s.ticker=p_input->>'ticker'
      AND s.instrument_type=p_input->>'instrument_type' AND s.eligible
  ) THEN
    RAISE EXCEPTION 'quote current reference membership mismatch' USING ERRCODE='22023';
  END IF;
  RETURN public.claim_market_intelligence_quote_v1_internal(p_run_id,jsonb_build_object(
    'ticker',p_input->'ticker','cache_key',p_input->'cache_key',
    'source_receipt_id',p_input->'source_receipt_id','reservation_id',p_input->'reservation_id'
  ));
EXCEPTION WHEN invalid_text_representation OR foreign_key_violation THEN
  RAISE EXCEPTION 'invalid quote collection identity' USING ERRCODE='22023';
END;
$$;

ALTER FUNCTION public.checkpoint_market_discovery_stage(UUID,JSONB)
  RENAME TO checkpoint_market_discovery_stage_v1_internal;

CREATE OR REPLACE FUNCTION public.checkpoint_market_discovery_stage(p_run_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r JSONB; v_value JSONB; v_task public.market_discovery_stage_tasks%ROWTYPE; v_result JSONB;
BEGIN
  IF jsonb_typeof(p_payload->'exposure_facts')<>'array' OR NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'exposure_facts') x
    WHERE x->'fact'->>'kind'='exposure_fact' AND x->'fact'->'value'->>'schema_version'='1'
  ) THEN
    RETURN public.checkpoint_market_discovery_stage_v1_internal(p_run_id,p_payload);
  END IF;
  IF p_payload->'task'->>'state'<>'succeeded' OR p_payload->'task'->>'stage'<>'enrich'
     OR jsonb_array_length(p_payload->'theme_episode_revisions')<>0
     OR jsonb_array_length(p_payload->'research_nominations')<>0 THEN
    RAISE EXCEPTION 'invalid typed exposure checkpoint' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_task FROM public.market_discovery_stage_tasks
  WHERE id=(p_payload->'task'->>'id')::uuid AND run_id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'typed exposure task is not planned' USING ERRCODE='22023'; END IF;
  IF v_task.state='succeeded' THEN
    IF v_task.capability_id<>p_payload->'task'->>'capability_id' OR v_task.query_hash<>p_payload->'task'->>'query_hash'
       OR v_task.result<>p_payload->'task'->'result'
       OR (SELECT count(*) FROM public.market_exposure_facts f WHERE f.task_id=v_task.id)
          <>jsonb_array_length(p_payload->'exposure_facts') THEN
      RAISE EXCEPTION 'typed exposure replay mismatch' USING ERRCODE='22023';
    END IF;
    FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'exposure_facts') LOOP
      IF NOT EXISTS(SELECT 1 FROM public.market_exposure_facts f WHERE f.id=(r->>'id')::uuid
                    AND f.run_id=p_run_id AND f.task_id=v_task.id AND f.fact=r->'fact'
                    AND f.source_ids=r->'source_ids' AND f.content_hash=r->>'content_hash') THEN
        RAISE EXCEPTION 'typed exposure replay mismatch' USING ERRCODE='22023';
      END IF;
    END LOOP;
    RETURN jsonb_build_object('task',to_jsonb(v_task)-'run_id'-'created_at'-'updated_at','duplicate',true);
  END IF;
  IF v_task.state<>'attempting' OR v_task.attempt_count<>1 THEN
    RAISE EXCEPTION 'typed exposure attempt barrier is unavailable' USING ERRCODE='22023';
  END IF;
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'exposure_facts') LOOP
    v_value:=r->'fact'->'value';
    IF jsonb_typeof(r)<>'object' OR r->>'exposure_kind'<>'filing'
       OR NOT (r ?& ARRAY['id','security_revision_id','theme_episode_revision_id','exposure_kind','fact','source_ids','valid_from','valid_to','content_hash'])
       OR (r-ARRAY['id','security_revision_id','theme_episode_revision_id','exposure_kind','fact','source_ids','valid_from','valid_to','content_hash'])<>'{}'::jsonb
       OR r->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR public.market_exposure_fact_semantic_hash_v1(r->'fact')<>r->>'content_hash'
       OR jsonb_typeof(r->'fact')<>'object'
       OR NOT (r->'fact' ?& ARRAY['kind','semantic_encoding_version','value'])
       OR ((r->'fact')-ARRAY['kind','semantic_encoding_version','value'])<>'{}'::jsonb
       OR r->'fact'->>'kind'<>'exposure_fact' OR r->'fact'->'semantic_encoding_version'<>'1'::jsonb
       OR jsonb_typeof(v_value)<>'object'
       OR NOT (v_value ?& ARRAY['accepted_at','accession_number','business_exposure','claim_state','effective_at','entity_id','event_ids','execution_allowed','filing_date','filing_rule_version','financial_materiality','form','is_amendment','hypothesis_ids','issuer_cik','limitations','metric','normalized_passage_hash','parser_version','passage','period_end','period_start','primary_document','reference_manifest_id','reporting_period_end','retrieved_at','role','schema_version','security_id','security_revision_id','source_cache_key','source_item_content_hash','source_item_id','source_locator','source_receipt_id','source_response_hash','source_url','status','submissions_response_hash','ticker','unit','value'])
       OR (v_value-ARRAY['accepted_at','accession_number','business_exposure','claim_state','effective_at','entity_id','event_ids','execution_allowed','filing_date','filing_rule_version','financial_materiality','form','is_amendment','hypothesis_ids','issuer_cik','limitations','metric','normalized_passage_hash','parser_version','passage','period_end','period_start','primary_document','reference_manifest_id','reporting_period_end','retrieved_at','role','schema_version','security_id','security_revision_id','source_cache_key','source_item_content_hash','source_item_id','source_locator','source_receipt_id','source_response_hash','source_url','status','submissions_response_hash','ticker','unit','value'])<>'{}'::jsonb
       OR v_value->'execution_allowed'<>'false'::jsonb OR v_value->>'status' NOT IN ('supported','contradicted','superseded','insufficient')
       OR v_value->>'claim_state' NOT IN ('operational','planned','forecast','customer','contradicted','insufficient')
       OR v_value->>'business_exposure' NOT IN ('supported','contradicted','unresolved')
       OR v_value->>'financial_materiality' NOT IN ('supported','contradicted','unknown')
       OR v_value->>'metric' NOT IN ('business_exposure','revenue_share')
       OR v_value->>'issuer_cik' !~ '^[0-9]{10}$' OR v_value->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$'
       OR v_value->>'entity_id'<>('sec-cik:'||(v_value->>'issuer_cik'))
       OR v_value->>'security_revision_id'<>r->>'security_revision_id'
       OR v_value->>'accession_number' !~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'
       OR v_value->>'primary_document' !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$'
       OR v_value->>'form' !~ '^(10-K|10-Q|8-K|20-F|40-F)(/A)?$'
       OR v_value->>'submissions_response_hash' !~ '^[0-9a-f]{64}$'
       OR v_value->>'source_cache_key' !~ '^[0-9a-f]{64}$'
       OR v_value->>'source_item_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR v_value->>'source_receipt_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR length(v_value->>'passage') NOT BETWEEN 1 AND 2000 OR length(v_value->>'source_locator') NOT BETWEEN 1 AND 256
       OR v_value->>'source_response_hash' !~ '^[0-9a-f]{64}$'
       OR v_value->>'source_url'<>(
         'https://www.sec.gov/Archives/edgar/data/'
         ||((v_value->>'issuer_cik')::bigint)::text||'/'
         ||replace((v_value->>'accession_number'),'-','')||'/'
         ||(v_value->>'primary_document')
       )
       OR v_value->>'normalized_passage_hash' !~ '^[0-9a-f]{64}$'
       OR v_value->>'source_item_content_hash' !~ '^[0-9a-f]{64}$'
       OR encode(extensions.digest(convert_to(v_value->>'passage','UTF8'),'sha256'),'hex')<>v_value->>'normalized_passage_hash'
       OR jsonb_typeof(v_value->'event_ids')<>'array' OR jsonb_array_length(v_value->'event_ids') NOT BETWEEN 1 AND 64
       OR jsonb_typeof(v_value->'hypothesis_ids')<>'array' OR jsonb_array_length(v_value->'hypothesis_ids') NOT BETWEEN 1 AND 64
       OR jsonb_typeof(v_value->'limitations')<>'array' OR jsonb_array_length(v_value->'limitations')>16
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_value->'event_ids') x WHERE jsonb_typeof(x)<>'string')
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(v_value->'event_ids') x WHERE length(x) NOT BETWEEN 1 AND 160)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_value->'hypothesis_ids') x WHERE jsonb_typeof(x)<>'string')
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(v_value->'hypothesis_ids') x WHERE length(x) NOT BETWEEN 1 AND 160)
       OR v_value->>'filing_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
       OR (v_value->'accepted_at'<>'null'::jsonb AND v_value->>'accepted_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T')
       OR (v_value->'reporting_period_end'<>'null'::jsonb AND v_value->>'reporting_period_end' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
       OR (v_value->'period_start'<>'null'::jsonb AND v_value->>'period_start' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
       OR (v_value->'period_end'<>'null'::jsonb AND v_value->>'period_end' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
       OR v_value->>'retrieved_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
       OR r->>'valid_from'<>v_value->>'filing_date' OR r->'valid_to'<>'null'::jsonb
       OR ((v_value->>'metric'='business_exposure')<>(v_value->'value'='null'::jsonb AND v_value->'unit'='null'::jsonb))
       OR (v_value->>'metric'='business_exposure' AND v_value->>'financial_materiality' IS DISTINCT FROM 'unknown')
       OR (v_value->>'metric'='revenue_share' AND (
         length(v_value->>'value') NOT BETWEEN 1 AND 32
         OR jsonb_typeof(v_value->'value') IS DISTINCT FROM 'string'
         OR v_value->>'value' !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
         OR (v_value->>'value')::numeric NOT BETWEEN 0 AND 100
         OR v_value->>'unit' IS DISTINCT FROM 'percent_of_revenue'
       ))
       OR (v_value->>'financial_materiality'='supported' AND (
         v_value->>'metric' IS DISTINCT FROM 'revenue_share'
         OR jsonb_typeof(v_value->'value') IS DISTINCT FROM 'string'
         OR v_value->>'value' !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
         OR (v_value->>'value')::numeric NOT BETWEEN 0 AND 100
         OR v_value->>'unit' IS DISTINCT FROM 'percent_of_revenue'
         OR v_value->'period_end'='null'::jsonb
         OR v_value->>'period_end' IS DISTINCT FROM v_value->>'reporting_period_end'
         OR v_value->>'claim_state' IS DISTINCT FROM 'operational'
         OR v_value->>'business_exposure' IS DISTINCT FROM 'supported'
         OR v_value->>'status' IS DISTINCT FROM 'supported'
       ))
       OR (v_value->>'status'='supported' AND (v_value->>'claim_state'<>'operational' OR v_value->>'business_exposure'<>'supported'))
       OR (v_value->>'status'='contradicted' AND (v_value->>'claim_state'<>'contradicted' OR v_value->>'business_exposure'<>'contradicted'))
       OR (v_value->>'status'='insufficient' AND (v_value->>'claim_state' NOT IN ('planned','forecast','customer','insufficient') OR v_value->>'business_exposure'<>'unresolved'))
       OR r->'source_ids'<>jsonb_build_array(v_value->'source_item_id')
       OR NOT EXISTS(
         SELECT 1 FROM public.market_reference_run_bindings b
         JOIN public.market_reference_snapshot_memberships member ON member.manifest_id=b.manifest_id
         JOIN public.market_security_reference_revisions s ON s.id=member.security_revision_id
         WHERE b.run_id=p_run_id AND b.capability_id='sec_company_tickers_universe'
           AND b.reference_status IN ('healthy','reference_stale')
           AND b.manifest_id=(v_value->>'reference_manifest_id')::uuid
           AND member.security_revision_id=(r->>'security_revision_id')::uuid
           AND s.eligible AND s.entity_id=v_value->>'entity_id'
           AND s.security_id=v_value->>'security_id' AND s.ticker=v_value->>'ticker'
       )
       OR NOT EXISTS(
         SELECT 1 FROM public.market_enrichment_request_descriptors d
         WHERE d.run_id=p_run_id AND d.task_id=v_task.id
           AND d.provider='sec_edgar' AND d.capability_id='sec_filing_document'
           AND d.query_kind='filing_document'
           AND d.descriptor->>'reference_manifest_id'=v_value->>'reference_manifest_id'
           AND d.descriptor->>'security_revision_id'=v_value->>'security_revision_id'
           AND d.descriptor->>'security_id'=v_value->>'security_id'
           AND d.descriptor->>'entity_id'=v_value->>'entity_id'
           AND d.descriptor->>'ticker'=v_value->>'ticker'
           AND d.descriptor->>'cik'=v_value->>'issuer_cik'
           AND d.descriptor->>'accession_number'=v_value->>'accession_number'
           AND d.descriptor->>'form'=v_value->>'form'
           AND d.descriptor->>'primary_document'=v_value->>'primary_document'
           AND d.descriptor->>'submissions_response_hash'=v_value->>'submissions_response_hash'
           AND d.descriptor->>'source_receipt_id'=v_value->>'source_receipt_id'
           AND d.descriptor->>'cache_key'=v_value->>'source_cache_key'
           AND d.descriptor->>'filing_date'=v_value->>'filing_date'
           AND (d.descriptor->>'accepted_at')::timestamptz IS NOT DISTINCT FROM (v_value->>'accepted_at')::timestamptz
           AND (d.descriptor->>'reporting_period_end')::date IS NOT DISTINCT FROM (v_value->>'reporting_period_end')::date
       )
       OR NOT EXISTS(
         SELECT 1 FROM (
           SELECT c.cache_key,c.payload FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id
           UNION ALL SELECT h.cache_key,h.payload FROM public.market_collection_checkpoint_history h WHERE h.run_id=p_run_id
         ) checkpoint
         WHERE checkpoint.cache_key=v_value->>'source_cache_key'
           AND checkpoint.payload->'receipt'->>'source_receipt_id'=v_value->>'source_receipt_id'
           AND checkpoint.payload->'receipt'->>'response_hash'=v_value->>'source_response_hash'
           AND EXISTS(SELECT 1 FROM jsonb_array_elements(checkpoint.payload->'items') item
                      WHERE item->>'content_hash'=v_value->>'source_item_content_hash'
                        AND item->>'authority'='official'
                        AND item->>'source_url'=v_value->>'source_url'
                        AND item->>'request_url'=v_value->>'source_url'
                        AND item->>'normalized_text'=v_value->>'passage'
                        AND octet_length(item->>'canonical_content')<=8192
                        AND item->'metadata'->>'raw_response_hash'=v_value->>'source_response_hash'
                        AND item->'metadata'->>'normalized_passage_hash'=v_value->>'normalized_passage_hash'
                        AND item->'metadata'->>'source_locator'=v_value->>'source_locator'
                        AND item->'metadata'->>'parser_version'=v_value->>'parser_version'
                        AND item->'metadata'->>'filing_rule_version'=v_value->>'filing_rule_version')
       ) THEN
      RAISE EXCEPTION 'invalid typed exposure fact lineage' USING ERRCODE='22023';
    END IF;
  END LOOP;
  v_result:=public.checkpoint_market_discovery_stage_v1_internal(
    p_run_id,jsonb_set(p_payload,'{exposure_facts}','[]'::jsonb)
  );
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'exposure_facts') LOOP
    INSERT INTO public.market_exposure_facts(
      id,run_id,task_id,security_revision_id,theme_episode_revision_id,exposure_kind,fact,source_ids,valid_from,valid_to,content_hash
    ) VALUES(
      (r->>'id')::uuid,p_run_id,(p_payload->'task'->>'id')::uuid,(r->>'security_revision_id')::uuid,
      (r->>'theme_episode_revision_id')::uuid,r->>'exposure_kind',r->'fact',r->'source_ids',
      (r->>'valid_from')::timestamptz,(r->>'valid_to')::timestamptz,r->>'content_hash'
    );
  END LOOP;
  RETURN v_result;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range OR foreign_key_violation OR unique_violation THEN
  RAISE EXCEPTION 'invalid typed exposure checkpoint' USING ERRCODE='22023';
END;
$$;

ALTER FUNCTION public.record_market_intelligence(UUID,UUID,JSONB)
  RENAME TO record_market_intelligence_v3_internal;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(p_run_id UUID,p_completion_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB;
BEGIN
  v_result:=public.record_market_intelligence_v3_internal(p_run_id,p_completion_id,p_payload);
  IF EXISTS(
    SELECT 1 FROM public.market_exposure_facts f
    CROSS JOIN LATERAL jsonb_array_elements_text(f.source_ids) source_id
    WHERE f.run_id=p_run_id AND f.fact->>'kind'='exposure_fact'
      AND NOT EXISTS(
        SELECT 1 FROM public.market_source_items i
        JOIN public.market_run_source_item_provenance provenance
          ON provenance.source_item_id=i.id AND provenance.run_id=p_run_id
        WHERE i.id=source_id::uuid
          AND i.content_hash=f.fact->'value'->>'source_item_content_hash'
          AND provenance.request_url=f.fact->'value'->>'source_url'
          AND provenance.source_receipt_id=(f.fact->'value'->>'source_receipt_id')::uuid
      )
  ) THEN
    RAISE EXCEPTION 'exposure final source reconciliation mismatch' USING ERRCODE='22023';
  END IF;
  RETURN v_result;
END;
$$;

ALTER FUNCTION public.read_market_discovery_context(UUID,INT)
  RENAME TO read_market_discovery_context_v1_internal;

CREATE OR REPLACE FUNCTION public.read_market_discovery_context(p_run_id UUID,p_limit INT DEFAULT 100)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB;
BEGIN
  v_result:=public.read_market_discovery_context_v1_internal(p_run_id,p_limit);
  v_result:=v_result||jsonb_build_object(
    'enrichment_selections',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'manifest',m.manifest,
        'requests',COALESCE((
          SELECT jsonb_agg(jsonb_build_object(
            'request_id',d.id::text,'task_id',d.task_id::text,'stage',t.stage,
            'provider',d.provider,'capability_id',d.capability_id,
            'query_kind',d.query_kind,'descriptor',d.descriptor,
            'dependency_ids',t.dependency_ids,'requested_window',t.requested_window,
            'request_budget',t.request_budget,'execution_allowed',false,
            'descriptor_hash',d.content_hash
          ) ORDER BY d.created_at,d.id)
          FROM public.market_enrichment_request_descriptors d
          JOIN public.market_discovery_stage_tasks t ON t.id=d.task_id AND t.run_id=d.run_id
          WHERE d.manifest_id=m.id
        ),'[]'::jsonb)
      ) ORDER BY m.created_at,m.id)
      FROM (
        SELECT * FROM public.market_enrichment_selection_manifests
        WHERE run_id=p_run_id ORDER BY created_at,id LIMIT 3
      ) m
    ),'[]'::jsonb)
  );
  IF octet_length(v_result::text)>1048576 THEN
    RAISE EXCEPTION 'discovery context exceeds bound' USING ERRCODE='54000';
  END IF;
  RETURN v_result;
END;
$$;

ALTER TABLE public.market_enrichment_selection_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_enrichment_request_descriptors ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_enrichment_selection_manifests,public.market_enrichment_request_descriptors FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.market_enrichment_selection_manifests,public.market_enrichment_request_descriptors TO stock_agent_release_reader;

REVOKE ALL ON FUNCTION public.seal_market_enrichment_selection(UUID,JSONB),public.claim_market_intelligence_quote(UUID,JSONB),
  public.checkpoint_market_discovery_stage(UUID,JSONB),public.record_market_intelligence(UUID,UUID,JSONB)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.seal_market_enrichment_selection(UUID,JSONB),public.claim_market_intelligence_quote(UUID,JSONB),
  public.checkpoint_market_discovery_stage(UUID,JSONB),public.record_market_intelligence(UUID,UUID,JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.claim_market_intelligence_quote_v1_internal(UUID,JSONB),
  public.checkpoint_market_discovery_stage_v1_internal(UUID,JSONB),public.record_market_intelligence_v3_internal(UUID,UUID,JSONB)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_context_v1_internal(UUID,INT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_context(UUID,INT)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.read_market_discovery_context(UUID,INT) TO service_role;
REVOKE ALL ON FUNCTION public.market_enrichment_manifest_semantic_hash_v1(JSONB),
  public.market_enrichment_request_semantic_hash_v1(JSONB),public.market_exposure_fact_semantic_hash_v1(JSONB)
  FROM PUBLIC,anon,authenticated,service_role;

-- Versioned research/suitability packet contract. V1 packet rows remain byte-for-byte
-- readable; only explicit contract_version=2 rows enter these promotion gates.

CREATE OR REPLACE FUNCTION public.market_portfolio_revision_v1()
RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
  SELECT encode(extensions.digest(convert_to(public.market_canonical_jsonb(
    COALESCE((SELECT jsonb_agg(to_jsonb(h) ORDER BY h.ticker) FROM (
      SELECT ticker,shares::text AS shares,avg_cost::text AS avg_cost,
        bucket,stop::text AS stop,target::text AS target
      FROM public.holdings WHERE shares>0 ORDER BY ticker
    ) h),'[]'::jsonb)
  ),'UTF8'),'sha256'),'hex')
$$;

ALTER TABLE public.market_intelligence_context_inputs
  ADD COLUMN IF NOT EXISTS portfolio_revision TEXT,
  ADD COLUMN IF NOT EXISTS portfolio_valuation_complete BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS cash_revision BIGINT;

ALTER FUNCTION public.refresh_market_intelligence_context(UUID)
  RENAME TO refresh_market_intelligence_context_v1_internal;

CREATE OR REPLACE FUNCTION public.refresh_market_intelligence_context(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_result JSONB;
  v_quotes JSONB;
  v_liquidity_states JSONB;
  v_liquidity_provenance JSONB;
  v_overlap_states JSONB;
  v_overlap_provenance JSONB;
  v_reference_state TEXT;
  v_reference_provenance JSONB;
  v_revision TEXT;
  v_cash BIGINT;
  v_complete BOOLEAN;
BEGIN
  v_result:=public.refresh_market_intelligence_context_v1_internal(p_run_id);
  SELECT COALESCE(jsonb_object_agg(a.ticker,a.quote||jsonb_build_object(
    'receipt_id',a.source_receipt_id,
    'expires_at',a.checkpoint->'receipt'->'expires_at'
  ) ORDER BY a.ticker),'{}'::jsonb)
  INTO v_quotes FROM public.market_intelligence_quote_attempts a
  WHERE a.run_id=p_run_id AND a.status='succeeded';
  v_revision:=public.market_portfolio_revision_v1();
  SELECT revision INTO v_cash FROM public.portfolio_cash_ledger_state
  WHERE singleton=true;
  v_complete:=(SELECT count(*)>0 FROM public.holdings WHERE shares>0)
    AND NOT EXISTS(
      SELECT 1 FROM public.holdings h WHERE h.shares>0
      AND NOT (v_result->'holding_market_values' ? h.ticker)
    )
    AND COALESCE(v_result->'overlap_by_ticker','{}'::jsonb)<>'{}'::jsonb;
  SELECT COALESCE(jsonb_object_agg(key,'passed' ORDER BY key),'{}'::jsonb)
  INTO v_liquidity_states FROM jsonb_object_keys(
    COALESCE(v_result->'liquidity_by_ticker','{}'::jsonb)
  ) key;
  SELECT COALESCE(jsonb_object_agg(a.ticker,jsonb_build_object(
    'source','market_intelligence_quote_attempts',
    'quote_receipt_id',a.source_receipt_id,
    'retrieved_at',a.checkpoint->'receipt'->'retrieved_at',
    'expires_at',a.checkpoint->'receipt'->'expires_at'
  ) ORDER BY a.ticker),'{}'::jsonb)
  INTO v_liquidity_provenance FROM public.market_intelligence_quote_attempts a
  WHERE a.run_id=p_run_id AND a.status='succeeded'
    AND COALESCE(v_result->'liquidity_by_ticker','{}'::jsonb) ? a.ticker;
  SELECT CASE WHEN v_complete THEN
    COALESCE(jsonb_object_agg(key,'passed' ORDER BY key),'{}'::jsonb)
  ELSE '{}'::jsonb END,
  CASE WHEN v_complete THEN
    COALESCE(jsonb_object_agg(key,jsonb_build_object(
      'source','holdings_and_verified_quotes','portfolio_revision',v_revision
    ) ORDER BY key),'{}'::jsonb)
  ELSE '{}'::jsonb END
  INTO v_overlap_states,v_overlap_provenance
  FROM jsonb_object_keys(COALESCE(v_result->'overlap_by_ticker','{}'::jsonb)) key;
  SELECT CASE binding.reference_status WHEN 'healthy' THEN 'current'
      WHEN 'reference_stale' THEN 'stale' ELSE 'unavailable' END,
    jsonb_build_object(
      'source','market_reference_run_bindings','manifest_id',binding.manifest_id,
      'reference_as_of',binding.reference_as_of,
      'source_retrieved_at',binding.source_retrieved_at,
      'reference_age_seconds',binding.reference_age_seconds
    )
  INTO v_reference_state,v_reference_provenance
  FROM public.market_reference_run_bindings binding
  WHERE binding.run_id=p_run_id AND binding.capability_id='sec_company_tickers_universe';
  v_result:=jsonb_set(v_result,'{current_quotes}',v_quotes,true)||jsonb_build_object(
    'portfolio_revision',v_revision,
    'portfolio_valuation_complete',v_complete,
    'cash_revision',v_cash::text,
    'valuation_status','unavailable',
    'valuation_state_by_ticker','{}'::jsonb,
    'valuation_provenance_by_ticker','{}'::jsonb,
    'liquidity_state_by_ticker',v_liquidity_states,
    'liquidity_provenance_by_ticker',v_liquidity_provenance,
    'overlap_state_by_ticker',v_overlap_states,
    'overlap_provenance_by_ticker',v_overlap_provenance,
    'current_reference_state',COALESCE(v_reference_state,'unavailable'),
    'current_reference_provenance',COALESCE(v_reference_provenance,'{}'::jsonb)
  );
  UPDATE public.market_intelligence_context_inputs SET
    current_quotes=v_quotes,portfolio_revision=v_revision,
    portfolio_valuation_complete=v_complete,cash_revision=v_cash
  WHERE run_id=p_run_id;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.market_v2_sorted_unique_text_array(
  p_value JSONB,p_maximum INT,p_nonempty BOOLEAN DEFAULT false
) RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_count INT;
BEGIN
  IF jsonb_typeof(p_value)<>'array' OR jsonb_array_length(p_value)>p_maximum
     OR (p_nonempty AND jsonb_array_length(p_value)=0)
     OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_value) item WHERE jsonb_typeof(item)<>'string') THEN
    RETURN false;
  END IF;
  SELECT count(DISTINCT value) INTO v_count FROM jsonb_array_elements_text(p_value);
  RETURN v_count=jsonb_array_length(p_value)
    AND p_value=COALESCE((SELECT jsonb_agg(value ORDER BY value COLLATE "C")
                         FROM jsonb_array_elements_text(p_value) value),'[]'::jsonb);
END;
$$;

CREATE OR REPLACE FUNCTION public.validate_market_evidence_packet_v2(
  p_run_id UUID,p_policy_version INT,p_packet JSONB
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  c JSONB;
  e JSONB;
  r JSONB;
  a JSONB;
  s JSONB;
  l JSONB;
  v_candidate_hash TEXT;
  v_suitability_hash TEXT;
  v_observed TIMESTAMPTZ;
BEGIN
  IF jsonb_typeof(p_packet)<>'object' OR p_packet->>'contract_version'<>'2'
     OR p_packet->'execution_allowed'<>'false'::jsonb
     OR NOT (p_packet ?& ARRAY[
       'action_candidates','contract_version','coverage','evidence','execution_allowed',
       'limitations','observed_at','omissions','policy_version','research_candidates','run_id'
     ])
     OR (p_packet-ARRAY[
       'action_candidates','contract_version','coverage','evidence','execution_allowed',
       'limitations','observed_at','omissions','policy_version','research_candidates','run_id'
     ])<>'{}'::jsonb
     OR p_packet->>'run_id'<>p_run_id::text
     OR (p_packet->>'policy_version')::int<>p_policy_version
     OR p_packet->>'observed_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$'
     OR jsonb_typeof(p_packet->'coverage')<>'object'
     OR jsonb_typeof(p_packet->'limitations')<>'array'
     OR jsonb_typeof(p_packet->'omissions')<>'array'
     OR jsonb_array_length(p_packet->'omissions')>1000
     OR jsonb_typeof(p_packet->'research_candidates')<>'array'
     OR jsonb_array_length(p_packet->'research_candidates')>12
     OR jsonb_typeof(p_packet->'action_candidates')<>'array'
     -- No protected issuer-valuation ledger exists in this schema version.
     -- A caller cannot seal that gate by rehashing a suitability object.
     OR jsonb_array_length(p_packet->'action_candidates')<>0
     OR jsonb_typeof(p_packet->'evidence')<>'array'
     OR jsonb_array_length(p_packet->'evidence')>96
     OR octet_length(p_packet::text)>98304 THEN
    RAISE EXCEPTION 'invalid evidence packet v2 envelope' USING ERRCODE='22023';
  END IF;
  v_observed:=(p_packet->>'observed_at')::timestamptz;
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_packet->'research_candidates') x
    GROUP BY x->>'candidate_key' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_packet->'action_candidates') x
    GROUP BY x->>'candidate_key' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_packet->'evidence') x
    GROUP BY x->>'item_id' HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate evidence packet v2 identity' USING ERRCODE='22023';
  END IF;
  IF NOT public.market_v2_sorted_unique_text_array(p_packet->'limitations',100,false)
     OR p_packet->'evidence'<>COALESCE((
       SELECT jsonb_agg(value ORDER BY value->>'item_id' COLLATE "C")
       FROM jsonb_array_elements(p_packet->'evidence')
     ),'[]'::jsonb)
     OR EXISTS(
       SELECT 1 FROM jsonb_array_elements(p_packet->'omissions') omission
       WHERE jsonb_typeof(omission)<>'object'
          OR NOT(omission ?& ARRAY['candidate_key','item_id','kind','reason','stage'])
          OR (omission-ARRAY['candidate_key','item_id','kind','reason','stage'])<>'{}'::jsonb
          OR COALESCE(omission->>'kind','')='' OR COALESCE(omission->>'reason','')=''
          OR COALESCE(omission->>'stage','')=''
          OR (omission->'item_id'<>'null'::jsonb AND omission->>'item_id'
              !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
     )
     OR EXISTS(
       SELECT 1 FROM jsonb_array_elements(p_packet->'research_candidates') candidate
       WHERE candidate->'ticker'<>'null'::jsonb
       GROUP BY candidate->>'ticker' HAVING count(*)>1
     ) THEN
    RAISE EXCEPTION 'invalid evidence packet v2 ordering or omission identity' USING ERRCODE='22023';
  END IF;

  FOR e IN SELECT value FROM jsonb_array_elements(p_packet->'evidence') LOOP
    IF jsonb_typeof(e)<>'object' OR NOT (e ?& ARRAY[
      'authority','canonical_url','claim_type','content_hash','effective_at','item_id',
      'normalized_text','published_at','reporting_at','retrieved_at','source_identity'
    ]) OR (e-ARRAY[
      'authority','canonical_url','claim_type','content_hash','effective_at','item_id',
      'normalized_text','published_at','reporting_at','retrieved_at','source_identity'
    ])<>'{}'::jsonb OR e->>'item_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR e->>'content_hash' !~ '^[0-9a-f]{64}$' OR e->>'canonical_url' !~ '^https://'
       OR e->>'retrieved_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$'
       OR (e->'published_at'<>'null'::jsonb AND e->>'published_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$')
       OR (e->'reporting_at'<>'null'::jsonb AND e->>'reporting_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$')
       OR (e->'effective_at'<>'null'::jsonb AND e->>'effective_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$')
       OR jsonb_typeof(e->'source_identity')<>'object'
       OR NOT (e->'source_identity' ?& ARRAY['provider','receipt_id','upstream_item_id'])
       OR ((e->'source_identity')-ARRAY['provider','receipt_id','upstream_item_id'])<>'{}'::jsonb
       OR e->'source_identity'->>'receipt_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(e->'source_identity'->>'provider','')=''
       OR COALESCE(e->'source_identity'->>'upstream_item_id','')=''
       OR NOT EXISTS(
         SELECT 1 FROM public.market_source_items i
         JOIN public.market_intelligence_run_items ri
           ON ri.source_item_id=i.id AND ri.run_id=p_run_id AND ri.disposition='accepted'
         JOIN public.market_run_source_item_provenance provenance
           ON provenance.run_item_id=ri.id AND provenance.run_id=p_run_id
         JOIN public.market_source_item_provenance source
           ON source.source_item_id=i.id
         JOIN public.market_source_receipts receipt
           ON receipt.id=ri.source_receipt_id AND receipt.run_id=p_run_id
              AND receipt.status IN ('succeeded','cache_hit')
         WHERE i.id=(e->>'item_id')::uuid
           AND i.content_hash=e->>'content_hash'
           AND source.canonical_item_url=e->>'canonical_url'
           AND i.provider=e->'source_identity'->>'provider'
           AND i.upstream_item_id=e->'source_identity'->>'upstream_item_id'
           AND ri.source_receipt_id=(e->'source_identity'->>'receipt_id')::uuid
           AND i.normalized_text=e->>'normalized_text'
           AND i.published_at IS NOT DISTINCT FROM (e->>'published_at')::timestamptz
           AND i.effective_at IS NOT DISTINCT FROM (e->>'effective_at')::timestamptz
           AND provenance.reporting_at IS NOT DISTINCT FROM (e->>'reporting_at')::timestamptz
           AND provenance.retrieved_at=(e->>'retrieved_at')::timestamptz
       ) THEN
      RAISE EXCEPTION 'evidence packet v2 source lineage mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;

  FOR c IN SELECT value FROM jsonb_array_elements(p_packet->'research_candidates') LOOP
    IF jsonb_typeof(c)<>'object' OR NOT (c ?& ARRAY[
      'adverse_paths','candidate_hash','candidate_key','entity_id','event_ids','evidence',
      'exposure_fact_ids','limitations','priority_components','priority_score','research_state',
      'roles','security_id','suitability','theme_ids','ticker'
    ]) OR (c-ARRAY[
      'adverse_paths','candidate_hash','candidate_key','entity_id','event_ids','evidence',
      'exposure_fact_ids','limitations','priority_components','priority_score','research_state',
      'roles','security_id','suitability','theme_ids','ticker'
    ])<>'{}'::jsonb OR c->>'candidate_hash' !~ '^[0-9a-f]{64}$'
       OR c->>'research_state' NOT IN ('unresolved','resolved','exposure_supported','analysis_ready')
       OR jsonb_typeof(c->'event_ids')<>'array' OR jsonb_array_length(c->'event_ids')<1
       OR jsonb_typeof(c->'evidence')<>'array' OR jsonb_array_length(c->'evidence') NOT BETWEEN 1 AND 8
       OR jsonb_typeof(c->'exposure_fact_ids')<>'array'
       OR jsonb_typeof(c->'roles')<>'array' OR jsonb_typeof(c->'theme_ids')<>'array'
       OR jsonb_typeof(c->'limitations')<>'array' OR jsonb_typeof(c->'adverse_paths')<>'array'
       OR NOT public.market_v2_sorted_unique_text_array(c->'event_ids',50,true)
       OR NOT public.market_v2_sorted_unique_text_array(c->'exposure_fact_ids',50,false)
       OR NOT public.market_v2_sorted_unique_text_array(c->'roles',50,false)
       OR NOT public.market_v2_sorted_unique_text_array(c->'theme_ids',50,false)
       OR NOT public.market_v2_sorted_unique_text_array(c->'limitations',100,false)
       OR NOT public.market_v2_sorted_unique_text_array(c->'adverse_paths',50,false)
       OR jsonb_typeof(c->'priority_components')<>'object'
       OR NOT(c->'priority_components' ?& ARRAY['authority_corroboration','exposure','materiality','recency'])
       OR ((c->'priority_components')-ARRAY['authority_corroboration','exposure','materiality','recency'])<>'{}'::jsonb
       OR c->>'priority_score' !~ '^-?(0|[1-9][0-9]*)\.[0-9]{6}$'
       OR EXISTS(SELECT 1 FROM jsonb_each_text(c->'priority_components') x WHERE x.value !~ '^-?(0|[1-9][0-9]*)\.[0-9]{6}$' OR x.value='-0.000000')
       OR c->>'priority_score'='-0.000000' THEN
      RAISE EXCEPTION 'invalid research candidate v2' USING ERRCODE='22023';
    END IF;
    v_candidate_hash:=encode(extensions.digest(convert_to(
      public.market_canonical_jsonb(c-'candidate_hash'),'UTF8'
    ),'sha256'),'hex');
    IF v_candidate_hash<>c->>'candidate_hash' THEN
      RAISE EXCEPTION 'research candidate hash mismatch' USING ERRCODE='22023';
    END IF;
    IF EXISTS(
      SELECT 1 FROM jsonb_array_elements(c->'evidence') ref
      WHERE jsonb_typeof(ref)<>'object'
         OR NOT(ref ?& ARRAY['claim_type','item_id','relationship_eligible','role'])
         OR (ref-ARRAY['claim_type','item_id','relationship_eligible','role'])<>'{}'::jsonb
         OR ref->>'role' NOT IN ('supporting','opposing')
         OR jsonb_typeof(ref->'relationship_eligible')<>'boolean'
         OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_packet->'evidence') shared WHERE shared->>'item_id'=ref->>'item_id')
    ) OR EXISTS(
      SELECT 1 FROM jsonb_array_elements(c->'evidence') ref
      GROUP BY ref->>'item_id' HAVING count(*)>1
    ) THEN
      RAISE EXCEPTION 'candidate evidence relationship mismatch' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS(
      SELECT 1 FROM public.market_candidate_rankings ranking
      JOIN public.market_events event ON event.id=ranking.event_id AND event.run_id=ranking.run_id
      WHERE ranking.run_id=p_run_id AND ranking.candidate_key=c->>'candidate_key'
        AND EXISTS(SELECT 1 FROM jsonb_array_elements(c->'evidence') ref WHERE event.evidence_item_ids ? (ref->>'item_id'))
    ) THEN
      RAISE EXCEPTION 'research candidate lacks source-backed event' USING ERRCODE='22023';
    END IF;
    IF c->>'research_state'='unresolved' THEN
      IF c->'security_id'<>'null'::jsonb OR c->'ticker'<>'null'::jsonb
         OR COALESCE(c->>'entity_id','') !~ '^unresolved:' THEN
        RAISE EXCEPTION 'invalid unresolved research identity' USING ERRCODE='22023';
      END IF;
    ELSIF (
      c->'security_id'='null'::jsonb OR c->'ticker'='null'::jsonb OR c->'entity_id'='null'::jsonb
    ) THEN
      RAISE EXCEPTION 'resolved research identity is missing' USING ERRCODE='22023';
    ELSIF c->'security_id'<>'null'::jsonb AND c->>'candidate_key'<>c->>'security_id' THEN
      RAISE EXCEPTION 'resolved candidate key does not match security identity' USING ERRCODE='22023';
    END IF;
    s:=c->'suitability';
    IF jsonb_typeof(s)<>'object' OR NOT(s ?& ARRAY[
      'component_scores','evaluation_hash','lineage','missing_reasons','state','veto_reasons'
    ]) OR (s-ARRAY[
      'component_scores','evaluation_hash','lineage','missing_reasons','state','veto_reasons'
    ])<>'{}'::jsonb OR s->>'state' NOT IN ('unknown','eligible','vetoed')
       OR s->>'evaluation_hash' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(s->'missing_reasons')<>'array' OR jsonb_typeof(s->'veto_reasons')<>'array'
       OR NOT public.market_v2_sorted_unique_text_array(s->'missing_reasons',50,false)
       OR NOT public.market_v2_sorted_unique_text_array(s->'veto_reasons',50,false)
       OR jsonb_typeof(s->'component_scores')<>'object'
       OR NOT(s->'component_scores' ?& ARRAY['concentration_penalty','duplication_penalty','liquidity','portfolio_relevance'])
       OR ((s->'component_scores')-ARRAY['concentration_penalty','duplication_penalty','liquidity','portfolio_relevance'])<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each_text(s->'component_scores') x WHERE x.value !~ '^-?(0|[1-9][0-9]*)\.[0-9]{6}$' OR x.value='-0.000000') THEN
      RAISE EXCEPTION 'invalid suitability v2' USING ERRCODE='22023';
    END IF;
    v_suitability_hash:=encode(extensions.digest(convert_to(
      public.market_canonical_jsonb(s-'evaluation_hash'),'UTF8'
    ),'sha256'),'hex');
    IF v_suitability_hash<>s->>'evaluation_hash' THEN
      RAISE EXCEPTION 'suitability hash mismatch' USING ERRCODE='22023';
    END IF;
    l:=s->'lineage';
    IF l<>'null'::jsonb AND (
      jsonb_typeof(l)<>'object' OR NOT(l ?& ARRAY[
        'cash_revision','evidence_receipt_ids','observed_at','policy_version',
        'portfolio_revision','quote_as_of','quote_expires_at','quote_receipt_id',
        'reference_expires_at','reference_manifest_id','reference_revision','run_id',
        'security_revision_id'
      ]) OR (l-ARRAY[
        'cash_revision','evidence_receipt_ids','observed_at','policy_version',
        'portfolio_revision','quote_as_of','quote_expires_at','quote_receipt_id',
        'reference_expires_at','reference_manifest_id','reference_revision','run_id',
        'security_revision_id'
      ])<>'{}'::jsonb OR jsonb_typeof(l->'evidence_receipt_ids')<>'object'
      OR l->>'run_id'<>p_run_id::text OR (l->>'policy_version')::int<>p_policy_version
      OR l->>'observed_at'<>p_packet->>'observed_at'
      OR (SELECT count(*) FROM jsonb_object_keys(l->'evidence_receipt_ids'))
         <>jsonb_array_length(c->'evidence')
      OR EXISTS(SELECT 1 FROM jsonb_each_text(l->'evidence_receipt_ids') receipt
                WHERE receipt.key !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                   OR receipt.value !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
      OR EXISTS(
        SELECT 1 FROM jsonb_array_elements(c->'evidence') ref
        JOIN LATERAL (
          SELECT shared->'source_identity'->>'receipt_id' AS receipt_id
          FROM jsonb_array_elements(p_packet->'evidence') shared
          WHERE shared->>'item_id'=ref->>'item_id'
        ) source ON true
        WHERE l->'evidence_receipt_ids'->>(ref->>'item_id')
              IS DISTINCT FROM source.receipt_id
      )
    ) THEN
      RAISE EXCEPTION 'candidate protected lineage mismatch' USING ERRCODE='22023';
    END IF;
    IF c->>'research_state'='analysis_ready' THEN
      IF c->'security_id'='null'::jsonb OR c->'ticker'='null'::jsonb
         OR jsonb_array_length(c->'exposure_fact_ids')=0
         OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(c->'evidence') ref WHERE ref->>'role'='supporting')
         OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(c->'evidence') ref WHERE ref->>'role'='opposing')
         OR jsonb_typeof(l)<>'object'
         OR l->>'run_id'<>p_run_id::text OR (l->>'policy_version')::int<>p_policy_version
         OR (l->>'observed_at')::timestamptz<>v_observed
         OR NOT EXISTS(
           SELECT 1 FROM public.market_reference_run_bindings b
           JOIN public.market_reference_manifests manifest ON manifest.id=b.manifest_id
           JOIN public.market_reference_snapshot_memberships member ON member.manifest_id=b.manifest_id
           JOIN public.market_security_reference_revisions security ON security.id=member.security_revision_id
           WHERE b.run_id=p_run_id AND b.reference_status='healthy'
             AND b.manifest_id=(l->>'reference_manifest_id')::uuid
             AND manifest.revision=(l->>'reference_revision')::int
             AND member.security_revision_id=(l->>'security_revision_id')::uuid
             AND security.eligible AND security.security_id=c->>'security_id'
             AND security.entity_id=c->>'entity_id' AND security.ticker=c->>'ticker'
             AND (l->>'reference_expires_at')::timestamptz>v_observed
         ) OR EXISTS(
           SELECT 1 FROM jsonb_array_elements_text(c->'exposure_fact_ids') claimed
           WHERE NOT EXISTS(
             SELECT 1 FROM public.market_exposure_facts fact
             WHERE fact.id::text=claimed.value
               AND fact.run_id=p_run_id
               AND fact.security_revision_id=(l->>'security_revision_id')::uuid
               AND fact.fact->'value'->>'security_id'=c->>'security_id'
               AND fact.fact->'value'->>'entity_id'=c->>'entity_id'
               AND fact.fact->'value'->>'ticker'=c->>'ticker'
               AND fact.fact->'value'->>'status'='supported'
               AND fact.fact->'value'->>'claim_state'='operational'
               AND fact.fact->'value'->>'business_exposure'='supported'
               AND NOT EXISTS(
                 SELECT 1 FROM jsonb_array_elements_text(fact.fact->'value'->'event_ids') event_id
                 WHERE NOT(c->'event_ids' ? event_id.value)
               )
               AND c->'roles' ? (fact.fact->'value'->>'role')
               AND EXISTS(SELECT 1 FROM jsonb_array_elements(c->'evidence') ref
                 WHERE ref->>'item_id'=fact.fact->'value'->>'source_item_id'
                   AND ref->>'claim_type'='issuer_exposure'
                   AND ref->'relationship_eligible'='true'::jsonb)
           )
         ) THEN
        RAISE EXCEPTION 'analysis-ready lineage mismatch' USING ERRCODE='22023';
      END IF;
    END IF;
    IF s->>'state'='eligible' OR NOT(s->'missing_reasons' ? 'valuation_missing') THEN
      RAISE EXCEPTION 'protected issuer valuation unavailable' USING ERRCODE='22023';
    ELSIF s->>'state'='unknown' AND jsonb_array_length(s->'missing_reasons')=0 THEN
      RAISE EXCEPTION 'unknown suitability requires missing reasons' USING ERRCODE='22023';
    ELSIF s->>'state'='vetoed' AND jsonb_array_length(s->'veto_reasons')=0 THEN
      RAISE EXCEPTION 'vetoed suitability requires veto reasons' USING ERRCODE='22023';
    END IF;
  END LOOP;

  FOR a IN SELECT value FROM jsonb_array_elements(p_packet->'action_candidates') LOOP
    IF jsonb_typeof(a)<>'object' OR NOT(a ?& ARRAY['candidate_hash','candidate_key','suitability_hash'])
       OR (a-ARRAY['candidate_hash','candidate_key','suitability_hash'])<>'{}'::jsonb
       OR NOT EXISTS(
         SELECT 1 FROM jsonb_array_elements(p_packet->'research_candidates') candidate
         WHERE candidate->>'candidate_key'=a->>'candidate_key'
           AND candidate->>'candidate_hash'=a->>'candidate_hash'
           AND candidate->'suitability'->>'evaluation_hash'=a->>'suitability_hash'
           AND candidate->>'research_state'='analysis_ready'
           AND candidate->'suitability'->>'state'='eligible'
       ) THEN
      RAISE EXCEPTION 'action lane is not an exact eligible subset' USING ERRCODE='22023';
    END IF;
  END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION public.enforce_market_evidence_packet_v2()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
  IF NEW.packet->>'contract_version'='2' THEN
    PERFORM public.validate_market_evidence_packet_v2(NEW.run_id,NEW.policy_version,NEW.packet);
    IF NEW.candidate_count<>jsonb_array_length(NEW.packet->'research_candidates')
       OR NEW.evidence_count<>jsonb_array_length(NEW.packet->'evidence')
       OR NEW.packet_hash<>encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(NEW.packet),'UTF8'
       ),'sha256'),'hex') THEN
      RAISE EXCEPTION 'packet v2 count or hash mismatch' USING ERRCODE='22023';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS market_evidence_packet_v2_guard ON public.market_evidence_packets;
CREATE TRIGGER market_evidence_packet_v2_guard BEFORE INSERT ON public.market_evidence_packets
FOR EACH ROW EXECUTE FUNCTION public.enforce_market_evidence_packet_v2();

ALTER FUNCTION public.read_market_evidence_packet(UUID,UUID)
  RENAME TO read_market_evidence_packet_v1_internal;

CREATE OR REPLACE FUNCTION public.read_market_evidence_packet(p_packet_id UUID,p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_packet public.market_evidence_packets%ROWTYPE; v_result JSONB;
BEGIN
  SELECT * INTO v_packet FROM public.market_evidence_packets
  WHERE id=p_packet_id AND run_id=p_run_id AND status='completed';
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF v_packet.packet->>'contract_version' IS DISTINCT FROM '2' THEN
    RETURN public.read_market_evidence_packet_v1_internal(p_packet_id,p_run_id);
  END IF;
  PERFORM public.validate_market_evidence_packet_v2(v_packet.run_id,v_packet.policy_version,v_packet.packet);
  SELECT jsonb_build_object(
    'id',v_packet.id,'run_id',v_packet.run_id,'packet_hash',v_packet.packet_hash,
    'packet',v_packet.packet,
    'evidence_facts',COALESCE(jsonb_agg(jsonb_build_object(
      'candidate_key',candidate->>'ticker','evidence_id',item.id,
      'category',CASE WHEN item.provider='sec_edgar' THEN 'fundamentals'
        WHEN item.provider IN ('fred','eia','bls','bea') THEN 'macro'
        WHEN item.provider IN ('white_house','doe','dod','federal_register') THEN 'event'
        WHEN item.provider IN ('gdelt','finnhub') THEN 'news'
        WHEN item.provider='yahoo' THEN 'quote' ELSE 'unknown' END,
      'source',item.provider,'source_status',receipt.status,
      'authority',CASE WHEN item.metadata->>'authority'='official' THEN 'official'
        WHEN item.provider IN ('yahoo','alpha_vantage') THEN 'market_data'
        WHEN item.provider IN ('gdelt','finnhub') THEN 'reported' ELSE 'unverified' END,
      'published_at',item.published_at,'retrieved_at',receipt.retrieved_at,
      'expires_at',receipt.expires_at,'reference',source.canonical_item_url,
      'normalized_text',item.normalized_text,
      'exposure_kind',CASE WHEN ref->>'claim_type'='issuer_exposure'
        THEN COALESCE(fact.exposure_kind,'filing') ELSE NULL END,
      'relationship_eligible',ref->'relationship_eligible',
      'claim_key',item.metadata->>'claim_key',
      'claim_polarity',CASE WHEN item.metadata->>'claim_polarity' IN ('affirmed','denied')
        THEN item.metadata->>'claim_polarity' ELSE NULL END
    ) ORDER BY candidate->>'ticker',item.id),'[]'::jsonb),
    'exposure_facts',COALESCE(jsonb_agg(jsonb_build_object(
      'candidate_key',candidate->>'ticker','evidence_id',item.id,
      'exposure_kind',fact.exposure_kind,
      'status',CASE WHEN receipt.expires_at>statement_timestamp()
        AND fact.fact->'value'->>'status'='supported' THEN 'fresh' ELSE 'stale' END,
      'observed_at',COALESCE(item.effective_at,item.published_at),
      'retrieved_at',receipt.retrieved_at
    ) ORDER BY candidate->>'ticker',item.id) FILTER (WHERE fact.id IS NOT NULL),'[]'::jsonb)
  ) INTO v_result
  FROM jsonb_array_elements(v_packet.packet->'research_candidates') candidate
  CROSS JOIN LATERAL jsonb_array_elements(candidate->'evidence') ref
  JOIN public.market_source_items item ON item.id=(ref->>'item_id')::uuid
  JOIN public.market_source_item_provenance source ON source.source_item_id=item.id
  JOIN public.market_source_receipts receipt ON receipt.id=item.source_receipt_id
  LEFT JOIN public.market_exposure_facts fact ON fact.run_id=v_packet.run_id
    AND fact.id::text IN (SELECT value FROM jsonb_array_elements_text(candidate->'exposure_fact_ids'))
    AND fact.fact->'value'->>'source_item_id'=item.id::text;
  IF octet_length(v_result::text)>131072 THEN
    RAISE EXCEPTION 'packet read exceeds bound' USING ERRCODE='22023';
  END IF;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_report(
  p_run_id UUID,
  p_idempotency_key TEXT,
  p_report JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_existing public.market_reports%ROWTYPE;
  v_packet public.market_evidence_packets%ROWTYPE;
  v_expected_key TEXT;
  v_expected_id UUID;
  v_research_only BOOLEAN;
BEGIN
  IF p_run_id IS NULL OR p_idempotency_key IS NULL
     OR p_idempotency_key !~ '^[0-9a-f]{64}$' OR jsonb_typeof(p_report)<>'object'
     OR octet_length(p_report::text)>196608
     OR NOT (p_report ?& ARRAY[
       'id','packet_id','market_date','kind','report','report_hash','rendered_text','rendered_hash'
     ])
     OR (p_report - ARRAY[
       'id','packet_id','market_date','kind','report','report_hash','rendered_text','rendered_hash'
     ]) <> '{}'::jsonb
     OR p_report->>'kind' NOT IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')
     OR jsonb_typeof(p_report->'report')<>'object'
     OR octet_length((p_report->'report')::text)>131072
     OR p_report->>'report_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'report_hash' <> encode(extensions.digest(convert_to(
       public.market_canonical_jsonb(p_report->'report'),'UTF8'
     ),'sha256'),'hex')
     OR jsonb_typeof(p_report->'rendered_text')<>'string'
     OR octet_length(convert_to(p_report->>'rendered_text','UTF8'))>14000
     OR p_report->>'rendered_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'rendered_hash' <> encode(extensions.digest(
       convert_to(p_report->>'rendered_text','UTF8'),'sha256'
     ),'hex')
     OR NOT (p_report->'report' ?& ARRAY[
       'actionable_risk','comparison_ids','full_markdown','intraday_triggered',
       'material_thesis_change','policy_decision_ids','source_ids','suggestion_only',
       'summary','title'
     ])
     OR ((p_report->'report')-ARRAY[
       'actionable_risk','comparison_ids','full_markdown','intraday_triggered',
       'material_thesis_change','policy_decision_ids','source_ids','suggestion_only',
       'summary','title'
     ])<>'{}'::jsonb
     OR jsonb_typeof(p_report->'report'->'title')<>'string'
     OR octet_length(convert_to(p_report->'report'->>'title','UTF8')) NOT BETWEEN 1 AND 200
     OR jsonb_typeof(p_report->'report'->'summary')<>'string'
     OR octet_length(convert_to(p_report->'report'->>'summary','UTF8'))>1000
     OR jsonb_typeof(p_report->'report'->'full_markdown')<>'string'
     OR octet_length(convert_to(p_report->'report'->>'full_markdown','UTF8')) NOT BETWEEN 1 AND 14000
     OR jsonb_typeof(p_report->'report'->'actionable_risk')<>'boolean'
     OR jsonb_typeof(p_report->'report'->'material_thesis_change')<>'boolean'
     OR jsonb_typeof(p_report->'report'->'intraday_triggered')<>'boolean'
     OR p_report->'report'->'suggestion_only'<>'true'::jsonb
     OR jsonb_typeof(p_report->'report'->'source_ids') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_report->'report'->'policy_decision_ids') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_report->'report'->'comparison_ids') IS DISTINCT FROM 'array'
     OR NOT public.market_v2_sorted_unique_text_array(p_report->'report'->'source_ids',96,true)
     OR NOT public.market_v2_sorted_unique_text_array(p_report->'report'->'policy_decision_ids',96,false)
     OR NOT public.market_v2_sorted_unique_text_array(p_report->'report'->'comparison_ids',96,false)
     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'source_ids') item
       WHERE item !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'policy_decision_ids') item
       WHERE item !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') THEN
    RAISE EXCEPTION 'invalid market report' USING ERRCODE = '22023';
  END IF;
  SELECT packet.* INTO v_packet
  FROM public.market_evidence_packets packet
  JOIN public.market_intelligence_run_events event
    ON event.run_id=packet.run_id AND event.status='completed'
  WHERE packet.id=(p_report->>'packet_id')::uuid
    AND packet.run_id=p_run_id AND packet.status='completed';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'completed evidence packet unavailable' USING ERRCODE = '22023';
  END IF;
  v_research_only:=v_packet.packet->>'contract_version'='2'
    AND jsonb_array_length(v_packet.packet->'research_candidates')>0
    AND jsonb_array_length(v_packet.packet->'action_candidates')=0
    AND jsonb_array_length(p_report->'report'->'policy_decision_ids')=0;
  IF jsonb_array_length(p_report->'report'->'policy_decision_ids')=0
     AND NOT v_research_only THEN
    RAISE EXCEPTION 'report policy decision provenance missing' USING ERRCODE='22023';
  END IF;
  v_expected_key := encode(extensions.digest(convert_to(
    'v2:' || (p_report->>'kind') || ':' || (p_report->>'market_date') || ':' ||
      v_packet.packet_hash || ':' || (p_report->>'report_hash'),
    'UTF8'
  ), 'sha256'), 'hex');
  v_expected_id := (
    substr(v_expected_key,1,8) || '-' || substr(v_expected_key,9,4) || '-5' ||
    substr(v_expected_key,14,3) || '-8' || substr(v_expected_key,18,3) || '-' ||
    substr(v_expected_key,21,12)
  )::uuid;
  IF p_idempotency_key <> v_expected_key OR p_report->>'id' <> v_expected_id::text
     OR (v_research_only AND (
       jsonb_array_length(p_report->'report'->'comparison_ids')<>0
       OR p_report->'report'->'actionable_risk'<>'false'::jsonb
       OR p_report->'report'->'material_thesis_change'<>'false'::jsonb
       OR p_report->'report'->'intraday_triggered'<>'false'::jsonb
       OR jsonb_array_length(p_report->'report'->'source_ids')<>(
         SELECT count(DISTINCT ref->>'item_id')
         FROM jsonb_array_elements(v_packet.packet->'research_candidates') candidate
         CROSS JOIN LATERAL jsonb_array_elements(candidate->'evidence') ref
       )
       OR EXISTS(
         SELECT 1 FROM jsonb_array_elements(v_packet.packet->'research_candidates') candidate
         CROSS JOIN LATERAL jsonb_array_elements(candidate->'evidence') ref
         WHERE NOT (p_report->'report'->'source_ids' ? (ref->>'item_id'))
       )
     ))
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'source_ids') source_id
       WHERE NOT EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_packet.packet->'evidence') evidence
         JOIN public.market_source_items item ON item.id=(evidence->>'item_id')::uuid
         JOIN public.market_intelligence_run_items run_item ON run_item.source_item_id=item.id
           AND run_item.run_id=p_run_id AND run_item.disposition='accepted'
         JOIN public.market_source_receipts receipt ON receipt.id=run_item.source_receipt_id
           AND receipt.status IN ('succeeded','cache_hit')
         WHERE evidence->>'item_id'=source_id
       )
     )
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'policy_decision_ids') decision_id
       WHERE NOT EXISTS (
         SELECT 1 FROM public.decision_evaluations evaluation
         WHERE evaluation.id=decision_id::uuid AND evaluation.run_id=p_run_id
           AND evaluation.analyst->>'packet_id'=v_packet.id::text
           AND evaluation.policy_version=v_packet.policy_version
       )
     ) OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'comparison_ids') comparison_id
       WHERE NOT EXISTS (
         SELECT 1 FROM public.market_policy_comparisons comparison
         JOIN public.decision_evaluations evaluation ON evaluation.id=comparison.evaluation_id
           AND evaluation.run_id=p_run_id AND evaluation.analyst->>'packet_id'=v_packet.id::text
         WHERE comparison.id=comparison_id::uuid AND comparison.run_id=p_run_id
           AND comparison.packet_id=v_packet.id
           AND p_report->'report'->'policy_decision_ids' ? evaluation.id::text
       )
     ) THEN
    RAISE EXCEPTION 'market report chain mismatch' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-report:' || p_run_id::text || ':' || v_packet.id::text || ':' ||
      (p_report->>'market_date') || ':' || (p_report->>'kind'), 0
  ));
  SELECT * INTO v_existing FROM public.market_reports
  WHERE run_id=p_run_id AND packet_id=v_packet.id
    AND market_date=(p_report->>'market_date')::date AND kind=p_report->>'kind';
  IF FOUND THEN
    IF v_existing.run_id IS DISTINCT FROM p_run_id
       OR v_existing.id IS DISTINCT FROM v_expected_id
       OR v_existing.packet_id IS DISTINCT FROM (p_report->>'packet_id')::uuid
       OR v_existing.report_hash IS DISTINCT FROM p_report->>'report_hash'
       OR v_existing.rendered_text IS DISTINCT FROM p_report->>'rendered_text'
       OR v_existing.rendered_hash IS DISTINCT FROM p_report->>'rendered_hash' THEN
      RAISE EXCEPTION 'market report idempotency mismatch' USING ERRCODE = '22023';
    END IF;
    RETURN jsonb_build_object(
      'report_id',v_existing.id,
      'report_hash',v_existing.report_hash,
      'rendered_hash',v_existing.rendered_hash,
      'duplicate',true
    );
  END IF;
  INSERT INTO public.market_reports(
    id,idempotency_key,run_id,packet_id,market_date,kind,report,report_hash,rendered_text,rendered_hash
  ) VALUES (
    (p_report->>'id')::uuid,p_idempotency_key,p_run_id,v_packet.id,
    (p_report->>'market_date')::date,p_report->>'kind',p_report->'report',
    p_report->>'report_hash',p_report->>'rendered_text',p_report->>'rendered_hash'
  ) RETURNING * INTO v_existing;
  RETURN jsonb_build_object(
    'report_id',v_existing.id,
    'report_hash',v_existing.report_hash,
    'rendered_hash',v_existing.rendered_hash,
    'duplicate',false
  );
END;
$$;


ALTER FUNCTION public.apply_market_decision_bundle_with_cash_snapshot(
  UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT
) RENAME TO apply_market_decision_bundle_with_cash_snapshot_v1_internal;

CREATE OR REPLACE FUNCTION public.apply_market_decision_bundle_with_cash_snapshot(
  p_request_id UUID,p_run_id UUID,p_lease_token UUID,p_policy_version INT,
  p_evaluations JSONB,p_suggestions JSONB,p_publication JSONB,
  p_cash_snapshot_id UUID,p_cash_ledger_watermark BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE evaluation JSONB; packet public.market_evidence_packets%ROWTYPE; candidate JSONB;
BEGIN
  IF jsonb_typeof(p_evaluations)<>'array' THEN
    RAISE EXCEPTION 'invalid decision transaction' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_request_id::text,0));
  IF EXISTS(
    SELECT 1 FROM public.market_publications publication
    WHERE publication.idempotency_key=p_request_id
       OR (p_run_id IS NOT NULL AND publication.run_id=p_run_id)
  ) THEN
    RETURN public.apply_market_decision_bundle_with_cash_snapshot_v1_internal(
      p_request_id,p_run_id,p_lease_token,p_policy_version,p_evaluations,
      p_suggestions,p_publication,p_cash_snapshot_id,p_cash_ledger_watermark
    );
  END IF;
  FOR evaluation IN SELECT value FROM jsonb_array_elements(p_evaluations) LOOP
    IF evaluation->>'policy_status'='approved'
       AND evaluation->>'final_action' IN ('buy','add','reduce','sell') THEN
      SELECT * INTO packet FROM public.market_evidence_packets
      WHERE id=(evaluation->'analyst'->>'packet_id')::uuid
        AND run_id=p_run_id AND policy_version=p_policy_version AND status='completed';
      IF NOT FOUND OR packet.packet->>'contract_version'<>'2' THEN
        RAISE EXCEPTION 'ACTION_LANE_REQUIRED' USING ERRCODE='55000';
      END IF;
      PERFORM public.validate_market_evidence_packet_v2(packet.run_id,packet.policy_version,packet.packet);
      SELECT value INTO candidate FROM jsonb_array_elements(packet.packet->'research_candidates')
      WHERE value->>'ticker'=evaluation->'normalized'->>'ticker';
      IF candidate IS NULL OR NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(packet.packet->'action_candidates') action
        WHERE action->>'candidate_key'=candidate->>'candidate_key'
          AND action->>'candidate_hash'=candidate->>'candidate_hash'
          AND action->>'suitability_hash'=candidate->'suitability'->>'evaluation_hash'
      ) OR candidate->'suitability'->'lineage'->>'portfolio_revision'
            <>public.market_portfolio_revision_v1()
         OR (candidate->'suitability'->'lineage'->>'cash_revision')::bigint
            IS DISTINCT FROM (SELECT revision FROM public.portfolio_cash_ledger_state WHERE singleton=true)
         OR candidate->'suitability'->'lineage'->>'reference_expires_at' IS NULL
         OR (candidate->'suitability'->'lineage'->>'reference_expires_at')::timestamptz
            <=statement_timestamp()
         OR candidate->'suitability'->'lineage'->>'quote_expires_at' IS NULL
         OR (candidate->'suitability'->'lineage'->>'quote_expires_at')::timestamptz
            <=statement_timestamp() THEN
        RAISE EXCEPTION 'ACTION_LANE_CONTEXT_CHANGED' USING ERRCODE='55000';
      END IF;
    END IF;
  END LOOP;
  RETURN public.apply_market_decision_bundle_with_cash_snapshot_v1_internal(
    p_request_id,p_run_id,p_lease_token,p_policy_version,p_evaluations,
    p_suggestions,p_publication,p_cash_snapshot_id,p_cash_ledger_watermark
  );
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid action lane identity' USING ERRCODE='22023';
END;
$$;

-- V2 recovery needs the protected source/event lineage that the packet guard
-- authenticates.  Keep this read-only and confined to the existing release
-- evidence role; the runtime role receives it only through membership.
DO $$
DECLARE name TEXT;
BEGIN
  FOREACH name IN ARRAY ARRAY[
    'market_source_receipts','market_source_items','market_intelligence_run_items',
    'market_source_item_provenance','market_run_source_item_provenance',
    'market_events','market_candidate_rankings'
  ] LOOP
    EXECUTE format(
      'REVOKE ALL ON public.%I FROM stock_agent_release_reader,stock_agent_release_reader_runtime',
      name
    );
    EXECUTE format('GRANT SELECT ON public.%I TO stock_agent_release_reader',name);
    EXECUTE format('DROP POLICY IF EXISTS release_evidence_select ON public.%I',name);
    EXECUTE format(
      'CREATE POLICY release_evidence_select ON public.%I FOR SELECT TO stock_agent_release_reader USING (true)',
      name
    );
  END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION public.market_portfolio_revision_v1(),
  public.market_v2_sorted_unique_text_array(JSONB,INT,BOOLEAN),
  public.validate_market_evidence_packet_v2(UUID,INT,JSONB),
  public.enforce_market_evidence_packet_v2(),
  public.refresh_market_intelligence_context_v1_internal(UUID),
  public.read_market_evidence_packet_v1_internal(UUID,UUID),
  public.apply_market_decision_bundle_with_cash_snapshot_v1_internal(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT)
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON FUNCTION public.refresh_market_intelligence_context(UUID),
  public.read_market_evidence_packet(UUID,UUID),
  public.apply_market_decision_bundle_with_cash_snapshot(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT)
  FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.refresh_market_intelligence_context(UUID),
  public.read_market_evidence_packet(UUID,UUID),
  public.apply_market_decision_bundle_with_cash_snapshot(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT)
  TO service_role;
-- V2 cross-run theme memory and research-nomination companion ledgers.
-- The 20261005 discovery rows remain readable historical v1 records and do
-- not authorize v2 memory, nominations, qualification, or action.

CREATE TABLE IF NOT EXISTS public.market_theme_episode_revisions_v2 (
  revision_id UUID PRIMARY KEY,
  theme_id TEXT NOT NULL CHECK (
    theme_id ~ '^[a-z][a-z0-9_]{2,79}$'
    OR theme_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
  ),
  episode_id UUID NOT NULL,
  revision INT NOT NULL CHECK (revision BETWEEN 1 AND 10000),
  identity_version INT NOT NULL DEFAULT 2 CHECK (identity_version=2),
  anchor_hash TEXT NOT NULL CHECK (anchor_hash ~ '^[0-9a-f]{64}$'),
  origin_run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  predecessor_revision_id UUID REFERENCES public.market_theme_episode_revisions_v2(revision_id) ON DELETE RESTRICT,
  predecessor_content_hash TEXT CHECK (predecessor_content_hash IS NULL OR predecessor_content_hash ~ '^[0-9a-f]{64}$'),
  theme_mechanism TEXT NOT NULL CHECK (char_length(theme_mechanism) BETWEEN 3 AND 240),
  subject_identity TEXT NOT NULL CHECK (char_length(subject_identity) BETWEEN 1 AND 240),
  jurisdiction TEXT NOT NULL CHECK (char_length(jurisdiction) BETWEEN 2 AND 80),
  effective_period_start DATE NOT NULL,
  effective_period_end DATE,
  authoritative_id TEXT CHECK (authoritative_id IS NULL OR char_length(authoritative_id) BETWEEN 1 AND 256),
  source_membership JSONB NOT NULL CHECK (jsonb_typeof(source_membership)='array' AND jsonb_array_length(source_membership) BETWEEN 1 AND 64 AND octet_length(source_membership::text)<=16384),
  source_ids JSONB NOT NULL CHECK (jsonb_typeof(source_ids)='array' AND jsonb_array_length(source_ids) BETWEEN 1 AND 64 AND octet_length(source_ids::text)<=8192),
  supporting_source_ids JSONB NOT NULL CHECK (jsonb_typeof(supporting_source_ids)='array' AND jsonb_array_length(supporting_source_ids)<=64 AND octet_length(supporting_source_ids::text)<=8192),
  opposing_source_ids JSONB NOT NULL CHECK (jsonb_typeof(opposing_source_ids)='array' AND jsonb_array_length(opposing_source_ids)<=64 AND octet_length(opposing_source_ids::text)<=8192),
  added_source_ids JSONB NOT NULL CHECK (jsonb_typeof(added_source_ids)='array' AND jsonb_array_length(added_source_ids)<=64 AND octet_length(added_source_ids::text)<=8192),
  investigated_entity_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(investigated_entity_ids)='array' AND jsonb_array_length(investigated_entity_ids)<=64 AND octet_length(investigated_entity_ids::text)<=8192),
  missing_questions JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(missing_questions)='array' AND jsonb_array_length(missing_questions)<=24 AND octet_length(missing_questions::text)<=8192),
  invalidation_conditions JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(invalidation_conditions)='array' AND jsonb_array_length(invalidation_conditions)<=24 AND octet_length(invalidation_conditions::text)<=8192),
  first_seen TIMESTAMPTZ NOT NULL,
  last_seen TIMESTAMPTZ NOT NULL,
  next_review_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('open','closed')),
  closure_reason TEXT CHECK (closure_reason IS NULL OR char_length(closure_reason) BETWEEN 3 AND 500),
  reopen_reason TEXT CHECK (reopen_reason IS NULL OR char_length(reopen_reason) BETWEEN 3 AND 500),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  execution_allowed BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_allowed),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (episode_id, revision),
  UNIQUE (predecessor_revision_id),
  UNIQUE (origin_run_id, content_hash),
  CHECK (effective_period_end IS NULL OR effective_period_end>=effective_period_start),
  CHECK (last_seen>=first_seen AND next_review_at>=first_seen),
  CHECK (expires_at>first_seen AND expires_at<=first_seen+INTERVAL '30 days'),
  CHECK ((revision=1 AND predecessor_revision_id IS NULL AND predecessor_content_hash IS NULL)
      OR (revision>1 AND predecessor_revision_id IS NOT NULL AND predecessor_content_hash IS NOT NULL)),
  CHECK ((state='closed' AND closure_reason IS NOT NULL) OR (state='open' AND closure_reason IS NULL)),
  CHECK (reopen_reason IS NULL OR revision>1)
);
CREATE INDEX IF NOT EXISTS idx_market_theme_episode_v2_head
  ON public.market_theme_episode_revisions_v2(episode_id,revision DESC);
CREATE INDEX IF NOT EXISTS idx_market_theme_episode_v2_anchor
  ON public.market_theme_episode_revisions_v2(anchor_hash,created_at DESC);

CREATE TABLE IF NOT EXISTS public.market_reviewer_identity_receipts_v2 (
  receipt_id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  packet_hash TEXT NOT NULL CHECK (packet_hash ~ '^[0-9a-f]{64}$'),
  reference_manifest_id UUID NOT NULL REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  actor_identity TEXT NOT NULL CHECK (actor_identity ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{2,127}$'),
  reviewed_role TEXT NOT NULL CHECK (reviewed_role IN ('analyst','checker')),
  predecessor_receipt_id UUID REFERENCES public.market_reviewer_identity_receipts_v2(receipt_id) ON DELETE RESTRICT,
  review_hash TEXT NOT NULL CHECK (review_hash ~ '^[0-9a-f]{64}$'),
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  execution_allowed BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_allowed),
  UNIQUE (packet_id,reviewed_role),
  UNIQUE (packet_id,actor_identity),
  CHECK ((reviewed_role='analyst' AND predecessor_receipt_id IS NULL)
      OR (reviewed_role='checker' AND predecessor_receipt_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS public.market_research_nomination_requests_v2 (
  request_id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  reviewer_receipt_id UUID NOT NULL REFERENCES public.market_reviewer_identity_receipts_v2(receipt_id) ON DELETE RESTRICT,
  request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  accepted_count INT NOT NULL CHECK (accepted_count BETWEEN 0 AND 3),
  response JSONB NOT NULL CHECK (jsonb_typeof(response)='object' AND octet_length(response::text)<=16384),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id,request_hash)
);

CREATE TABLE IF NOT EXISTS public.market_research_nominations_v2 (
  nomination_id UUID PRIMARY KEY,
  request_id UUID NOT NULL REFERENCES public.market_research_nomination_requests_v2(request_id) ON DELETE RESTRICT,
  origin_run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  packet_hash TEXT NOT NULL CHECK (packet_hash ~ '^[0-9a-f]{64}$'),
  reviewer_receipt_id UUID NOT NULL REFERENCES public.market_reviewer_identity_receipts_v2(receipt_id) ON DELETE RESTRICT,
  actor_identity TEXT NOT NULL CHECK (char_length(actor_identity) BETWEEN 3 AND 128),
  reviewed_role TEXT NOT NULL CHECK (reviewed_role IN ('analyst','checker')),
  reference_manifest_id UUID NOT NULL REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  theme_id TEXT NOT NULL CHECK (theme_id ~ '^[a-z][a-z0-9_]{2,79}$' OR theme_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'),
  entity_id TEXT CHECK (entity_id IS NULL OR char_length(entity_id) BETWEEN 1 AND 128),
  security_id TEXT CHECK (security_id IS NULL OR char_length(security_id) BETWEEN 1 AND 128),
  relationship_role TEXT NOT NULL CHECK (relationship_role ~ '^[a-z][a-z0-9_]{2,79}$'),
  reason TEXT NOT NULL CHECK (char_length(reason) BETWEEN 20 AND 500),
  evidence_ids JSONB NOT NULL CHECK (jsonb_typeof(evidence_ids)='array' AND jsonb_array_length(evidence_ids) BETWEEN 1 AND 8 AND octet_length(evidence_ids::text)<=4096),
  required_evidence_kind TEXT NOT NULL CHECK (required_evidence_kind IN ('primary_exposure','contradictory_primary','current_filing','official_program','entity_identity','relationship','current_reference')),
  priority INT NOT NULL CHECK (priority BETWEEN 1 AND 5),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  expires_at TIMESTAMPTZ NOT NULL,
  execution_allowed BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_allowed),
  UNIQUE (origin_run_id,theme_id,entity_id,security_id,relationship_role,required_evidence_kind),
  CHECK (expires_at>created_at AND expires_at<=created_at+INTERVAL '7 days')
);

CREATE TABLE IF NOT EXISTS public.market_research_nomination_lifecycle_v2 (
  receipt_id UUID PRIMARY KEY,
  nomination_id UUID NOT NULL REFERENCES public.market_research_nominations_v2(nomination_id) ON DELETE RESTRICT,
  transition_run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  predecessor_receipt_id UUID REFERENCES public.market_research_nomination_lifecycle_v2(receipt_id) ON DELETE RESTRICT,
  state TEXT NOT NULL CHECK (state IN ('pending','selected','resolved','rejected','expired')),
  reason TEXT CHECK (reason IS NULL OR char_length(reason) BETWEEN 3 AND 500),
  selection_descriptor JSONB CHECK (selection_descriptor IS NULL OR (jsonb_typeof(selection_descriptor)='object' AND octet_length(selection_descriptor::text)<=8192)),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  receipt_hash TEXT NOT NULL CHECK (receipt_hash ~ '^[0-9a-f]{64}$'),
  execution_allowed BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_allowed),
  UNIQUE (predecessor_receipt_id)
);

CREATE TABLE IF NOT EXISTS public.market_intelligence_memory_context_bindings_v2 (
  run_id UUID PRIMARY KEY REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  as_of TIMESTAMPTZ NOT NULL,
  reference_manifest_id UUID REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  reference_hash TEXT CHECK (reference_hash IS NULL OR reference_hash ~ '^[0-9a-f]{64}$'),
  selected_revision_ids JSONB NOT NULL CHECK (jsonb_typeof(selected_revision_ids)='array' AND jsonb_array_length(selected_revision_ids)<=25),
  selected_nomination_ids JSONB NOT NULL CHECK (jsonb_typeof(selected_nomination_ids)='array' AND jsonb_array_length(selected_nomination_ids)<=12),
  context JSONB NOT NULL CHECK (jsonb_typeof(context)='object' AND octet_length(context::text)<=65536),
  snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE OR REPLACE FUNCTION public.reject_theme_memory_v2_mutation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'theme memory v2 records are append-only' USING ERRCODE='55000';
END;
$$;

DROP TRIGGER IF EXISTS market_theme_episode_revisions_v2_append_only ON public.market_theme_episode_revisions_v2;
CREATE TRIGGER market_theme_episode_revisions_v2_append_only BEFORE UPDATE OR DELETE ON public.market_theme_episode_revisions_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();
DROP TRIGGER IF EXISTS market_reviewer_identity_receipts_v2_append_only ON public.market_reviewer_identity_receipts_v2;
CREATE TRIGGER market_reviewer_identity_receipts_v2_append_only BEFORE UPDATE OR DELETE ON public.market_reviewer_identity_receipts_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();
DROP TRIGGER IF EXISTS market_research_nomination_requests_v2_append_only ON public.market_research_nomination_requests_v2;
CREATE TRIGGER market_research_nomination_requests_v2_append_only BEFORE UPDATE OR DELETE ON public.market_research_nomination_requests_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();
DROP TRIGGER IF EXISTS market_research_nominations_v2_append_only ON public.market_research_nominations_v2;
CREATE TRIGGER market_research_nominations_v2_append_only BEFORE UPDATE OR DELETE ON public.market_research_nominations_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();
DROP TRIGGER IF EXISTS market_research_nomination_lifecycle_v2_append_only ON public.market_research_nomination_lifecycle_v2;
CREATE TRIGGER market_research_nomination_lifecycle_v2_append_only BEFORE UPDATE OR DELETE ON public.market_research_nomination_lifecycle_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();
DROP TRIGGER IF EXISTS market_intelligence_memory_context_bindings_v2_append_only ON public.market_intelligence_memory_context_bindings_v2;
CREATE TRIGGER market_intelligence_memory_context_bindings_v2_append_only BEFORE UPDATE OR DELETE ON public.market_intelligence_memory_context_bindings_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();

CREATE OR REPLACE FUNCTION public.record_theme_episode_revision_v2(p_run_id UUID,p_revision JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_existing public.market_theme_episode_revisions_v2%ROWTYPE;
  v_predecessor public.market_theme_episode_revisions_v2%ROWTYPE;
  v_anchor TEXT;
  v_hash TEXT;
  v_source JSONB;
  v_first_seen TIMESTAMPTZ;
  v_last_seen TIMESTAMPTZ;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_revision)<>'object'
     OR NOT(p_revision ?& ARRAY['revision_id','theme_id','episode_id','revision','identity_version','anchor_hash','predecessor_revision_id','predecessor_content_hash','theme_mechanism','subject_identity','jurisdiction','effective_period_start','effective_period_end','authoritative_id','source_membership','source_ids','supporting_source_ids','opposing_source_ids','added_source_ids','investigated_entity_ids','missing_questions','invalidation_conditions','first_seen','last_seen','next_review_at','expires_at','state','closure_reason','reopen_reason','content_hash','execution_allowed'])
     OR (p_revision-ARRAY['revision_id','theme_id','episode_id','revision','identity_version','anchor_hash','predecessor_revision_id','predecessor_content_hash','theme_mechanism','subject_identity','jurisdiction','effective_period_start','effective_period_end','authoritative_id','source_membership','source_ids','supporting_source_ids','opposing_source_ids','added_source_ids','investigated_entity_ids','missing_questions','invalidation_conditions','first_seen','last_seen','next_review_at','expires_at','state','closure_reason','reopen_reason','content_hash','execution_allowed'])<>'{}'::jsonb
     OR (p_revision->>'identity_version')::int<>2 OR (p_revision->>'execution_allowed')::boolean
     OR jsonb_typeof(p_revision->'source_ids')<>'array'
     OR jsonb_array_length(p_revision->'source_ids') NOT BETWEEN 1 AND 64 THEN
    RAISE EXCEPTION 'invalid theme episode revision v2' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs r JOIN public.market_intelligence_run_events e ON e.run_id=r.id AND e.status IN ('started','completed') WHERE r.id=p_run_id) THEN
    RAISE EXCEPTION 'theme origin run is not valid' USING ERRCODE='22023';
  END IF;
  v_anchor:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(jsonb_build_object(
    'authoritative_id',p_revision->'authoritative_id',
    'effective_period',jsonb_build_object('end',p_revision->'effective_period_end','start',p_revision->>'effective_period_start'),
    'identity_version',2,'jurisdiction',upper(trim(p_revision->>'jurisdiction')),
    'subject_identity',lower(trim(p_revision->>'subject_identity')),
    'theme_id',p_revision->>'theme_id','theme_mechanism',lower(trim(p_revision->>'theme_mechanism'))
  )),'UTF8'),'sha256'),'hex');
  IF v_anchor<>p_revision->>'anchor_hash' THEN
    RAISE EXCEPTION 'theme episode anchor hash mismatch' USING ERRCODE='22023';
  END IF;
  FOR v_source IN SELECT value FROM jsonb_array_elements(p_revision->'source_membership') LOOP
    IF jsonb_typeof(v_source)<>'object' OR NOT(v_source ?& ARRAY['evidence_id','story_identity','polarity'])
       OR (v_source-ARRAY['evidence_id','story_identity','polarity'])<>'{}'::jsonb
       OR jsonb_typeof(v_source->'story_identity')<>'string'
       OR octet_length(v_source->>'story_identity') NOT BETWEEN 1 AND 512
       OR v_source->>'polarity' NOT IN ('supporting','opposing')
       OR NOT EXISTS(
         SELECT 1 FROM public.market_source_items item
         JOIN public.market_source_receipts receipt ON receipt.id=item.source_receipt_id
         JOIN public.market_intelligence_run_items used ON used.source_item_id=item.id
         WHERE item.id=(v_source->>'evidence_id')::uuid
           AND receipt.status IN ('succeeded','cache_hit')
           AND used.disposition IN ('accepted','duplicate','near_duplicate')
       ) THEN
      RAISE EXCEPTION 'theme source membership mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_revision->'source_membership') member GROUP BY member->>'story_identity' HAVING count(*)>1)
     OR (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'source_ids'))
        IS DISTINCT FROM (SELECT array_agg(member->>'evidence_id' ORDER BY member->>'evidence_id') FROM jsonb_array_elements(p_revision->'source_membership') member)
     OR (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'supporting_source_ids'))
        IS DISTINCT FROM (SELECT array_agg(member->>'evidence_id' ORDER BY member->>'evidence_id') FROM jsonb_array_elements(p_revision->'source_membership') member WHERE member->>'polarity'='supporting')
     OR (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'opposing_source_ids'))
        IS DISTINCT FROM (SELECT array_agg(member->>'evidence_id' ORDER BY member->>'evidence_id') FROM jsonb_array_elements(p_revision->'source_membership') member WHERE member->>'polarity'='opposing') THEN
    RAISE EXCEPTION 'theme source partition mismatch' USING ERRCODE='22023';
  END IF;
  SELECT min(receipt.retrieved_at),max(receipt.retrieved_at) INTO v_first_seen,v_last_seen
  FROM public.market_source_items item JOIN public.market_source_receipts receipt ON receipt.id=item.source_receipt_id
  WHERE item.id IN (SELECT (member->>'evidence_id')::uuid FROM jsonb_array_elements(p_revision->'source_membership') member);
  IF v_first_seen IS NULL OR v_last_seen IS NULL
     OR (p_revision->>'first_seen')::timestamptz<>v_first_seen
     OR (p_revision->>'last_seen')::timestamptz<>v_last_seen THEN
    RAISE EXCEPTION 'theme source-backed observation mismatch' USING ERRCODE='22023';
  END IF;
  v_hash:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(p_revision-'content_hash'),'UTF8'),'sha256'),'hex');
  IF v_hash<>p_revision->>'content_hash' THEN
    RAISE EXCEPTION 'theme revision content hash mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_theme_episode_revisions_v2 WHERE revision_id=(p_revision->>'revision_id')::uuid;
  IF FOUND THEN
    IF v_existing.origin_run_id<>p_run_id OR v_existing.content_hash<>v_hash THEN
      RAISE EXCEPTION 'theme revision replay mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('revision_id',v_existing.revision_id,'episode_id',v_existing.episode_id,'revision',v_existing.revision,'duplicate',true);
  END IF;
  IF (p_revision->>'revision')::int>1 THEN
    SELECT * INTO v_predecessor FROM public.market_theme_episode_revisions_v2 WHERE revision_id=(p_revision->>'predecessor_revision_id')::uuid FOR UPDATE;
    IF NOT FOUND OR v_predecessor.episode_id<>(p_revision->>'episode_id')::uuid
       OR v_predecessor.revision+1<>(p_revision->>'revision')::int
       OR v_predecessor.content_hash<>p_revision->>'predecessor_content_hash'
       OR v_predecessor.theme_id<>p_revision->>'theme_id'
       OR v_predecessor.anchor_hash<>v_anchor
       OR v_predecessor.first_seen<>(p_revision->>'first_seen')::timestamptz
       OR v_predecessor.last_seen>(p_revision->>'last_seen')::timestamptz
       OR (p_revision->>'expires_at')::timestamptz>v_predecessor.expires_at
       OR jsonb_array_length(p_revision->'added_source_ids')=0
       OR (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'added_source_ids'))
          IS DISTINCT FROM (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'source_ids') current_source(value) WHERE NOT (v_predecessor.source_ids ? current_source.value))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_revision->'added_source_ids') added(value) WHERE NOT EXISTS(
         SELECT 1 FROM public.market_intelligence_run_items used
         JOIN public.market_source_items item ON item.id=used.source_item_id
         JOIN public.market_source_receipts receipt ON receipt.id=used.source_receipt_id
         WHERE used.run_id=p_run_id AND item.id=added.value::uuid
           AND used.disposition IN ('accepted','duplicate','near_duplicate')
           AND receipt.status IN ('succeeded','cache_hit')
       ))
       OR (v_predecessor.state='closed' AND NULLIF(p_revision->>'reopen_reason','') IS NULL) THEN
      RAISE EXCEPTION 'theme predecessor lineage mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    IF (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'added_source_ids'))
       IS DISTINCT FROM (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'source_ids'))
       OR EXISTS(SELECT 1 FROM public.market_theme_episode_revisions_v2 WHERE anchor_hash=v_anchor) THEN
      RAISE EXCEPTION 'theme episode identity or initial evidence mismatch' USING ERRCODE='22023';
    END IF;
  END IF;
  INSERT INTO public.market_theme_episode_revisions_v2(
    revision_id,theme_id,episode_id,revision,identity_version,anchor_hash,origin_run_id,
    predecessor_revision_id,predecessor_content_hash,theme_mechanism,subject_identity,jurisdiction,
    effective_period_start,effective_period_end,authoritative_id,source_membership,source_ids,
    supporting_source_ids,opposing_source_ids,added_source_ids,investigated_entity_ids,missing_questions,
    invalidation_conditions,first_seen,last_seen,next_review_at,expires_at,state,closure_reason,reopen_reason,
    content_hash,execution_allowed
  ) VALUES (
    (p_revision->>'revision_id')::uuid,p_revision->>'theme_id',(p_revision->>'episode_id')::uuid,
    (p_revision->>'revision')::int,2,v_anchor,p_run_id,(p_revision->>'predecessor_revision_id')::uuid,
    p_revision->>'predecessor_content_hash',p_revision->>'theme_mechanism',p_revision->>'subject_identity',
    p_revision->>'jurisdiction',(p_revision->>'effective_period_start')::date,(p_revision->>'effective_period_end')::date,
    p_revision->>'authoritative_id',p_revision->'source_membership',p_revision->'source_ids',
    p_revision->'supporting_source_ids',p_revision->'opposing_source_ids',p_revision->'added_source_ids',
    p_revision->'investigated_entity_ids',p_revision->'missing_questions',p_revision->'invalidation_conditions',
    (p_revision->>'first_seen')::timestamptz,(p_revision->>'last_seen')::timestamptz,
    (p_revision->>'next_review_at')::timestamptz,(p_revision->>'expires_at')::timestamptz,
    p_revision->>'state',p_revision->>'closure_reason',p_revision->>'reopen_reason',v_hash,false
  );
  RETURN jsonb_build_object('revision_id',p_revision->>'revision_id','episode_id',p_revision->>'episode_id','revision',(p_revision->>'revision')::int,'duplicate',false);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range OR unique_violation THEN
  RAISE EXCEPTION 'invalid or conflicting theme episode revision v2' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.record_research_review_identity_v2(p_run_id UUID,p_receipt_id UUID,p_review JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_packet public.market_evidence_packets%ROWTYPE; v_binding public.market_reference_run_bindings%ROWTYPE; v_prior public.market_reviewer_identity_receipts_v2%ROWTYPE; v_hash TEXT;
BEGIN
  IF jsonb_typeof(p_review)<>'object' OR NOT(p_review ?& ARRAY['actor_identity','reviewed_role','predecessor_receipt_id'])
     OR (p_review-ARRAY['actor_identity','reviewed_role','predecessor_receipt_id'])<>'{}'::jsonb
     OR p_review->>'reviewed_role' NOT IN ('analyst','checker') THEN
    RAISE EXCEPTION 'invalid reviewer identity receipt' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_packet FROM public.market_evidence_packets WHERE run_id=p_run_id AND status='completed' AND packet->>'contract_version'='2' ORDER BY created_at DESC,id DESC LIMIT 1;
  SELECT * INTO v_binding FROM public.market_reference_run_bindings WHERE run_id=p_run_id AND reference_status='healthy' ORDER BY created_at DESC LIMIT 1;
  IF NOT FOUND OR v_packet.id IS NULL OR v_packet.packet_hash<>encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_packet.packet),'UTF8'),'sha256'),'hex') THEN
    RAISE EXCEPTION 'review packet or reference binding unavailable' USING ERRCODE='22023';
  END IF;
  IF p_review->>'reviewed_role'='checker' THEN
    SELECT * INTO v_prior FROM public.market_reviewer_identity_receipts_v2 WHERE receipt_id=(p_review->>'predecessor_receipt_id')::uuid;
    IF NOT FOUND OR v_prior.run_id<>p_run_id OR v_prior.packet_id<>v_packet.id OR v_prior.reviewed_role<>'analyst' OR v_prior.actor_identity=p_review->>'actor_identity' THEN
      RAISE EXCEPTION 'checker identity lineage mismatch' USING ERRCODE='22023';
    END IF;
  ELSIF p_review->'predecessor_receipt_id'<>'null'::jsonb THEN
    RAISE EXCEPTION 'analyst predecessor is forbidden' USING ERRCODE='22023';
  END IF;
  v_hash:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(jsonb_build_object(
    'receipt_id',p_receipt_id,'run_id',p_run_id,'packet_id',v_packet.id,'packet_hash',v_packet.packet_hash,
    'reference_manifest_id',v_binding.manifest_id,'actor_identity',p_review->>'actor_identity',
    'reviewed_role',p_review->>'reviewed_role','predecessor_receipt_id',p_review->'predecessor_receipt_id'
  )),'UTF8'),'sha256'),'hex');
  SELECT * INTO v_prior FROM public.market_reviewer_identity_receipts_v2 WHERE receipt_id=p_receipt_id;
  IF FOUND THEN
    IF v_prior.review_hash<>v_hash THEN RAISE EXCEPTION 'review identity replay mismatch' USING ERRCODE='22023'; END IF;
    RETURN jsonb_build_object('receipt_id',v_prior.receipt_id,'review_hash',v_prior.review_hash,'duplicate',true);
  END IF;
  INSERT INTO public.market_reviewer_identity_receipts_v2(receipt_id,run_id,packet_id,packet_hash,reference_manifest_id,actor_identity,reviewed_role,predecessor_receipt_id,review_hash)
  VALUES(p_receipt_id,p_run_id,v_packet.id,v_packet.packet_hash,v_binding.manifest_id,p_review->>'actor_identity',p_review->>'reviewed_role',(p_review->>'predecessor_receipt_id')::uuid,v_hash);
  RETURN jsonb_build_object('receipt_id',p_receipt_id,'review_hash',v_hash,'duplicate',false);
EXCEPTION WHEN invalid_text_representation OR unique_violation THEN
  RAISE EXCEPTION 'invalid or conflicting reviewer identity receipt' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.record_research_nominations(p_run_id UUID,p_request_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_receipt public.market_reviewer_identity_receipts_v2%ROWTYPE; v_request public.market_research_nomination_requests_v2%ROWTYPE; v_packet public.market_evidence_packets%ROWTYPE; v_nom JSONB; v_entry JSONB; v_entries JSONB:='[]'::jsonb; v_hash TEXT; v_response JSONB; v_count INT; v_matches INT; v_id UUID; v_created TIMESTAMPTZ:=statement_timestamp();
BEGIN
  IF p_run_id IS NULL OR p_request_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR NOT(p_payload ?& ARRAY['reviewer_receipt_id','nominations'])
     OR (p_payload-ARRAY['reviewer_receipt_id','nominations'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'nominations')<>'array'
     OR jsonb_array_length(p_payload->'nominations') NOT BETWEEN 1 AND 3 THEN
    RAISE EXCEPTION 'invalid research nomination request' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_run_id::text,90210));
  v_hash:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(p_payload),'UTF8'),'sha256'),'hex');
  SELECT * INTO v_request FROM public.market_research_nomination_requests_v2 WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_request.run_id<>p_run_id OR v_request.request_hash<>v_hash THEN
      RAISE EXCEPTION 'research nomination request replay mismatch' USING ERRCODE='22023';
    END IF;
    RETURN v_request.response;
  END IF;
  SELECT * INTO v_receipt FROM public.market_reviewer_identity_receipts_v2 WHERE receipt_id=(p_payload->>'reviewer_receipt_id')::uuid AND run_id=p_run_id;
  SELECT * INTO v_packet FROM public.market_evidence_packets WHERE id=v_receipt.packet_id AND run_id=p_run_id AND status='completed' AND packet_hash=v_receipt.packet_hash AND packet->>'contract_version'='2';
  IF v_receipt.receipt_id IS NULL OR v_packet.id IS NULL OR v_packet.packet_hash<>encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_packet.packet),'UTF8'),'sha256'),'hex') THEN
    RAISE EXCEPTION 'nomination reviewer packet identity mismatch' USING ERRCODE='22023';
  END IF;
  v_count:=jsonb_array_length(p_payload->'nominations');
  IF (SELECT count(*) FROM public.market_research_nominations_v2 WHERE origin_run_id=p_run_id)+v_count>3 THEN
    RAISE EXCEPTION 'accepted nomination limit exceeded' USING ERRCODE='22023';
  END IF;
  FOR v_nom IN SELECT value FROM jsonb_array_elements(p_payload->'nominations') LOOP
    IF jsonb_typeof(v_nom)<>'object'
       OR NOT(v_nom ?& ARRAY['theme_id','entity_id','security_id','role','reason','evidence_ids','required_evidence_kind','priority'])
       OR (v_nom-ARRAY['theme_id','entity_id','security_id','role','reason','evidence_ids','required_evidence_kind','priority'])<>'{}'::jsonb
       OR NOT(v_nom->>'theme_id' ~ '^[a-z][a-z0-9_]{2,79}$' OR v_nom->>'theme_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
       OR v_nom->>'role' !~ '^[a-z][a-z0-9_]{2,79}$'
       OR v_nom->>'required_evidence_kind' NOT IN ('primary_exposure','contradictory_primary','current_filing','official_program','entity_identity','relationship','current_reference')
       OR (v_nom->>'priority')::int NOT BETWEEN 1 AND 5
       OR char_length(v_nom->>'reason') NOT BETWEEN 20 AND 500
       OR v_nom->>'reason' ~* '(https?://|www\.|(^|[^a-z])(buy|sell|trade|order|price|score|watchlist|holding|portfolio|cash|alert|policy|allocation|browse|search|query)([^a-z]|$))'
       OR jsonb_typeof(v_nom->'evidence_ids')<>'array' OR jsonb_array_length(v_nom->'evidence_ids') NOT BETWEEN 1 AND 8
       OR (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(v_nom->'evidence_ids'))<>jsonb_array_length(v_nom->'evidence_ids') THEN
      RAISE EXCEPTION 'invalid research nomination' USING ERRCODE='22023';
    END IF;
    SELECT count(*) INTO v_matches FROM jsonb_array_elements(v_packet.packet->'research_candidates') candidate
    WHERE candidate->'theme_ids' ? (v_nom->>'theme_id')
      AND candidate->'roles' ? (v_nom->>'role')
      AND candidate->'entity_id' IS NOT DISTINCT FROM v_nom->'entity_id'
      AND candidate->'security_id' IS NOT DISTINCT FROM v_nom->'security_id'
      AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements_text(v_nom->'evidence_ids') wanted
        WHERE NOT EXISTS(
          SELECT 1 FROM jsonb_array_elements(candidate->'evidence') evidence
          WHERE evidence->>'item_id'=wanted.value
            AND evidence->'relationship_eligible'='true'::jsonb
            AND (v_nom->>'required_evidence_kind'<>'contradictory_primary' OR evidence->>'role'='opposing')
        )
      );
    IF v_matches<>1 THEN
      RAISE EXCEPTION 'candidate evidence relationship mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;
  v_response:=jsonb_build_object('request_id',p_request_id,'run_id',p_run_id,'packet_id',v_packet.id,'accepted_count',v_count,'duplicate',false,'nominations','[]'::jsonb);
  FOR v_nom IN SELECT value FROM jsonb_array_elements(p_payload->'nominations') LOOP
    v_id:=extensions.gen_random_uuid();
    v_entries:=v_entries||jsonb_build_array(jsonb_build_object('nomination_id',v_id,'nomination',v_nom));
    v_response:=jsonb_set(v_response,'{nominations}',(v_response->'nominations')||jsonb_build_array(jsonb_build_object('nomination_id',v_id,'state','pending','expires_at',v_created+INTERVAL '7 days')));
  END LOOP;
  INSERT INTO public.market_research_nomination_requests_v2(request_id,run_id,packet_id,reviewer_receipt_id,request_hash,accepted_count,response)
  VALUES(p_request_id,p_run_id,v_packet.id,v_receipt.receipt_id,v_hash,v_count,v_response);
  FOR v_entry IN SELECT value FROM jsonb_array_elements(v_entries) LOOP
    v_id:=(v_entry->>'nomination_id')::uuid;
    v_nom:=v_entry->'nomination';
    INSERT INTO public.market_research_nominations_v2(nomination_id,request_id,origin_run_id,packet_id,packet_hash,reviewer_receipt_id,actor_identity,reviewed_role,reference_manifest_id,theme_id,entity_id,security_id,relationship_role,reason,evidence_ids,required_evidence_kind,priority,created_at,expires_at)
    VALUES(v_id,p_request_id,p_run_id,v_packet.id,v_packet.packet_hash,v_receipt.receipt_id,v_receipt.actor_identity,v_receipt.reviewed_role,v_receipt.reference_manifest_id,v_nom->>'theme_id',v_nom->>'entity_id',v_nom->>'security_id',v_nom->>'role',v_nom->>'reason',v_nom->'evidence_ids',v_nom->>'required_evidence_kind',(v_nom->>'priority')::int,v_created,v_created+INTERVAL '7 days');
    INSERT INTO public.market_research_nomination_lifecycle_v2(receipt_id,nomination_id,transition_run_id,state,receipt_hash)
    VALUES(extensions.gen_random_uuid(),v_id,p_run_id,'pending',encode(extensions.digest(convert_to(public.market_canonical_jsonb(jsonb_build_object('nomination_id',v_id,'state','pending','created_at',v_created)),'UTF8'),'sha256'),'hex'));
  END LOOP;
  RETURN v_response;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR unique_violation THEN
  RAISE EXCEPTION 'invalid or conflicting research nomination request' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.transition_research_nomination_v2(p_run_id UUID,p_nomination_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_nom public.market_research_nominations_v2%ROWTYPE; v_head public.market_research_nomination_lifecycle_v2%ROWTYPE; v_state TEXT; v_receipt UUID; v_hash TEXT;
BEGIN
  IF jsonb_typeof(p_payload)<>'object' OR NOT(p_payload ?& ARRAY['state','reason','selection_descriptor'])
     OR (p_payload-ARRAY['state','reason','selection_descriptor'])<>'{}'::jsonb THEN RAISE EXCEPTION 'invalid nomination lifecycle request' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_nom FROM public.market_research_nominations_v2 WHERE nomination_id=p_nomination_id;
  SELECT * INTO v_head FROM public.market_research_nomination_lifecycle_v2 WHERE nomination_id=p_nomination_id ORDER BY created_at DESC,receipt_id DESC LIMIT 1 FOR UPDATE;
  v_state:=p_payload->>'state';
  IF NOT FOUND OR (v_head.state='pending' AND v_state NOT IN ('pending','selected','rejected','expired'))
     OR (v_head.state='selected' AND v_state NOT IN ('resolved','rejected','expired'))
     OR v_head.state IN ('resolved','rejected','expired')
     OR (v_state='pending' AND NULLIF(trim(p_payload->>'reason'),'') IS NULL)
     OR (v_state='selected' AND p_payload->'selection_descriptor'='null'::jsonb)
     OR (v_state<>'selected' AND p_payload->'selection_descriptor'<>'null'::jsonb)
     OR (v_state='selected' AND p_run_id=v_nom.origin_run_id)
     OR (v_state='expired' AND statement_timestamp()<v_nom.expires_at)
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs run WHERE run.id=p_run_id)
     OR (v_state='selected' AND (
       jsonb_typeof(p_payload->'selection_descriptor')<>'object'
       OR NOT(p_payload->'selection_descriptor' ?& ARRAY['request_id','descriptor_hash','uncertain_outcome_barrier','execution_allowed'])
       OR ((p_payload->'selection_descriptor')-ARRAY['request_id','descriptor_hash','uncertain_outcome_barrier','execution_allowed'])<>'{}'::jsonb
       OR p_payload->'selection_descriptor'->'uncertain_outcome_barrier'<>'true'::jsonb
       OR p_payload->'selection_descriptor'->'execution_allowed'<>'false'::jsonb
       OR p_payload->'selection_descriptor'->>'descriptor_hash' !~ '^[0-9a-f]{64}$'
       OR NOT EXISTS(
         SELECT 1 FROM public.market_enrichment_request_descriptors descriptor
         JOIN public.market_enrichment_selection_manifests manifest ON manifest.id=descriptor.manifest_id
         JOIN public.market_discovery_stage_tasks task ON task.id=descriptor.task_id
         JOIN public.analysis_runs analysis ON analysis.id=descriptor.run_id
         WHERE descriptor.id=(p_payload->'selection_descriptor'->>'request_id')::uuid
           AND descriptor.run_id=p_run_id
           AND descriptor.content_hash=p_payload->'selection_descriptor'->>'descriptor_hash'
           AND task.state='planned' AND task.query_hash=descriptor.content_hash
           AND manifest.run_id=p_run_id
           AND manifest.created_at>v_nom.created_at
           AND ((analysis.scheduled_phase IS NOT NULL AND analysis.scheduled_market_date IS NOT NULL)
             OR (analysis.kind='on-demand' AND analysis.scheduled_phase IS NULL))
       )
     )) THEN
    RAISE EXCEPTION 'invalid nomination lifecycle transition' USING ERRCODE='22023';
  END IF;
  v_receipt:=extensions.gen_random_uuid();
  v_hash:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(jsonb_build_object('nomination_id',p_nomination_id,'run_id',p_run_id,'predecessor',v_head.receipt_id,'state',v_state,'reason',p_payload->'reason','selection_descriptor',p_payload->'selection_descriptor')),'UTF8'),'sha256'),'hex');
  INSERT INTO public.market_research_nomination_lifecycle_v2(receipt_id,nomination_id,transition_run_id,predecessor_receipt_id,state,reason,selection_descriptor,receipt_hash)
  VALUES(v_receipt,p_nomination_id,p_run_id,v_head.receipt_id,v_state,p_payload->>'reason',NULLIF(p_payload->'selection_descriptor','null'::jsonb),v_hash);
  RETURN jsonb_build_object('receipt_id',v_receipt,'nomination_id',p_nomination_id,'state',v_state,'receipt_hash',v_hash);
END;
$$;

CREATE OR REPLACE FUNCTION public.read_theme_memory_context(p_run_id UUID,p_as_of TIMESTAMPTZ DEFAULT statement_timestamp())
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_existing public.market_intelligence_memory_context_bindings_v2%ROWTYPE; v_context JSONB; v_revisions JSONB; v_nominations JSONB; v_urgent JSONB; v_material JSONB; v_radar JSONB; v_cursors JSONB:='[]'::jsonb; v_ref UUID; v_ref_hash TEXT; v_hash TEXT; v_active_available INT; v_due_available INT;
BEGIN
  SELECT * INTO v_existing FROM public.market_intelligence_memory_context_bindings_v2 WHERE run_id=p_run_id;
  IF FOUND THEN
    IF v_existing.snapshot_hash<>encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_existing.context),'UTF8'),'sha256'),'hex') THEN RAISE EXCEPTION 'memory context hash mismatch' USING ERRCODE='55000'; END IF;
    RETURN v_existing.context;
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs WHERE id=p_run_id) THEN RAISE EXCEPTION 'unknown memory context run' USING ERRCODE='22023'; END IF;
  SELECT b.manifest_id,m.content_hash INTO v_ref,v_ref_hash FROM public.market_reference_run_bindings b JOIN public.market_reference_manifests m ON m.id=b.manifest_id WHERE b.run_id=p_run_id AND b.reference_status='healthy' ORDER BY b.created_at DESC LIMIT 1;
  SELECT COALESCE(jsonb_agg(to_jsonb(head)-ARRAY['created_at','priority_adverse','priority_overdue','priority_unresolved'] ORDER BY head.priority_adverse DESC,head.priority_overdue DESC,head.priority_unresolved DESC,head.next_review_at,head.episode_id),'[]'::jsonb)
  INTO v_revisions FROM (
    SELECT latest.*,(latest.opposing_source_ids<>'[]'::jsonb) AS priority_adverse,
      (latest.next_review_at<=p_as_of) AS priority_overdue,
      (latest.missing_questions<>'[]'::jsonb) AS priority_unresolved
    FROM (
      SELECT DISTINCT ON (revision.episode_id) revision.*
      FROM public.market_theme_episode_revisions_v2 revision
      WHERE revision.created_at<=p_as_of ORDER BY revision.episode_id,revision.revision DESC
    ) latest
    JOIN public.market_intelligence_run_events terminal
      ON terminal.run_id=latest.origin_run_id AND terminal.status='completed'
    WHERE latest.state='open' AND latest.expires_at>p_as_of
    ORDER BY priority_adverse DESC,priority_overdue DESC,priority_unresolved DESC,
      latest.next_review_at,latest.episode_id LIMIT 25
  ) head;
  SELECT COALESCE(jsonb_agg(to_jsonb(due)-'created_at' ORDER BY due.priority DESC,due.expires_at,due.nomination_id),'[]'::jsonb) INTO v_nominations FROM (
    SELECT nomination.* FROM public.market_research_nominations_v2 nomination
    JOIN LATERAL (SELECT state FROM public.market_research_nomination_lifecycle_v2 life WHERE life.nomination_id=nomination.nomination_id ORDER BY life.created_at DESC,life.receipt_id DESC LIMIT 1) current ON current.state='pending'
    WHERE nomination.expires_at>p_as_of AND nomination.created_at<=p_as_of ORDER BY nomination.priority DESC,nomination.expires_at,nomination.nomination_id LIMIT 12
  ) due;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('event_id',event.id,'event_type',event.event_type,'title',left(event.title,240),'summary',left(event.summary,500),'occurred_at',event.occurred_at,'effective_at',event.effective_at,'materiality',event.materiality,'confidence',event.confidence) ORDER BY event.materiality DESC,event.id),'[]'::jsonb) INTO v_urgent FROM (
    SELECT event.* FROM public.market_events event JOIN public.market_intelligence_run_events done ON done.run_id=event.run_id AND done.status='completed' WHERE event.created_at<=p_as_of ORDER BY event.materiality DESC,event.created_at DESC,event.id LIMIT 10
  ) event;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('theme_id',head.theme_id,'episode_id',head.episode_id,'revision_id',head.revision_id,'mechanism',head.theme_mechanism,'adverse',head.opposing_source_ids<>'[]'::jsonb,'next_review_at',head.next_review_at) ORDER BY jsonb_array_length(head.opposing_source_ids) DESC,head.next_review_at,head.episode_id),'[]'::jsonb) INTO v_material FROM (
    SELECT latest.* FROM (
      SELECT DISTINCT ON (revision.episode_id) revision.* FROM public.market_theme_episode_revisions_v2 revision
      WHERE revision.created_at<=p_as_of ORDER BY revision.episode_id,revision.revision DESC
    ) latest JOIN public.market_intelligence_run_events done ON done.run_id=latest.origin_run_id AND done.status='completed'
    WHERE latest.state='open' AND latest.expires_at>p_as_of
    ORDER BY (latest.opposing_source_ids<>'[]'::jsonb) DESC,(latest.next_review_at<=p_as_of) DESC,
      (latest.missing_questions<>'[]'::jsonb) DESC,latest.next_review_at,latest.episode_id LIMIT 10
  ) head;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('ticker',ticker,'reason',left(COALESCE(reason,''),500),'days_relevant',days_relevant) ORDER BY last_seen DESC NULLS LAST,ticker),'[]'::jsonb) INTO v_radar FROM (SELECT * FROM public.radar ORDER BY last_seen DESC NULLS LAST,ticker LIMIT 20) bounded;
  SELECT COALESCE(jsonb_agg(cursor.value ORDER BY cursor.ordinality),'[]'::jsonb)
  INTO v_cursors FROM (
    SELECT value,ordinality FROM jsonb_array_elements(
      public.read_market_discovery_cursor_context(p_run_id,100)->'source_cursors'
    ) WITH ORDINALITY LIMIT 100
  ) cursor;
  SELECT count(*) INTO v_active_available FROM (
    SELECT DISTINCT ON (revision.episode_id) revision.* FROM public.market_theme_episode_revisions_v2 revision
    WHERE revision.created_at<=p_as_of ORDER BY revision.episode_id,revision.revision DESC
  ) latest JOIN public.market_intelligence_run_events done ON done.run_id=latest.origin_run_id AND done.status='completed'
  WHERE latest.state='open' AND latest.expires_at>p_as_of;
  SELECT count(*) INTO v_due_available FROM public.market_research_nominations_v2 nomination
  JOIN LATERAL (SELECT state FROM public.market_research_nomination_lifecycle_v2 life WHERE life.nomination_id=nomination.nomination_id ORDER BY life.created_at DESC,life.receipt_id DESC LIMIT 1) current ON current.state='pending'
  WHERE nomination.expires_at>p_as_of AND nomination.created_at<=p_as_of;
  v_context:=jsonb_build_object('memory_version',2,'as_of',p_as_of,'reference_manifest_id',v_ref,'reference_hash',v_ref_hash,'active_theme_heads',v_revisions,'due_nominations',v_nominations,'urgent_events',v_urgent,'high_materiality_themes',v_material,'radar',v_radar,'source_cursors',v_cursors,'available_counts',jsonb_build_object('theme_heads',v_active_available,'due_nominations',v_due_available),'returned_counts',jsonb_build_object('theme_heads',jsonb_array_length(v_revisions),'due_nominations',jsonb_array_length(v_nominations),'urgent_events',jsonb_array_length(v_urgent),'high_materiality_themes',jsonb_array_length(v_material),'radar',jsonb_array_length(v_radar),'source_cursors',jsonb_array_length(v_cursors)),'deferred_counts',jsonb_build_object('theme_heads',GREATEST(v_active_available-jsonb_array_length(v_revisions),0),'due_nominations',GREATEST(v_due_available-jsonb_array_length(v_nominations),0)),'byte_truncated',false,'research_only',true,'execution_allowed',false);
  WHILE octet_length(v_context::text)>65536 AND jsonb_array_length(v_context->'active_theme_heads')>0 LOOP
    v_context:=jsonb_set(v_context,'{active_theme_heads}',(SELECT COALESCE(jsonb_agg(value ORDER BY ordinality),'[]'::jsonb) FROM jsonb_array_elements(v_context->'active_theme_heads') WITH ORDINALITY WHERE ordinality<jsonb_array_length(v_context->'active_theme_heads')));
    v_context:=jsonb_set(v_context,'{byte_truncated}','true'::jsonb);
  END LOOP;
  v_context:=jsonb_set(v_context,'{returned_counts,theme_heads}',to_jsonb(jsonb_array_length(v_context->'active_theme_heads')));
  v_context:=jsonb_set(v_context,'{deferred_counts,theme_heads}',to_jsonb(GREATEST((v_context->'available_counts'->>'theme_heads')::int-jsonb_array_length(v_context->'active_theme_heads'),0)));
  IF octet_length(v_context::text)>65536 THEN RAISE EXCEPTION 'protected memory context exceeds bound' USING ERRCODE='22023'; END IF;
  v_hash:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_context),'UTF8'),'sha256'),'hex');
  INSERT INTO public.market_intelligence_memory_context_bindings_v2(run_id,as_of,reference_manifest_id,reference_hash,selected_revision_ids,selected_nomination_ids,context,snapshot_hash)
  VALUES(p_run_id,p_as_of,v_ref,v_ref_hash,(SELECT COALESCE(jsonb_agg(value->'revision_id'),'[]'::jsonb) FROM jsonb_array_elements(v_context->'active_theme_heads') value),(SELECT COALESCE(jsonb_agg(value->'nomination_id'),'[]'::jsonb) FROM jsonb_array_elements(v_context->'due_nominations') value),v_context,v_hash)
  ON CONFLICT (run_id) DO NOTHING;
  SELECT * INTO v_existing FROM public.market_intelligence_memory_context_bindings_v2 WHERE run_id=p_run_id;
  IF v_existing.snapshot_hash<>encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_existing.context),'UTF8'),'sha256'),'hex') THEN RAISE EXCEPTION 'memory context hash mismatch' USING ERRCODE='55000'; END IF;
  RETURN v_existing.context;
END;
$$;

ALTER FUNCTION public.refresh_market_intelligence_context(UUID) RENAME TO refresh_market_intelligence_context_v2_internal;
CREATE OR REPLACE FUNCTION public.refresh_market_intelligence_context(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_base JSONB; v_memory JSONB; v_snapshot_hash TEXT;
BEGIN
  v_base:=public.refresh_market_intelligence_context_v2_internal(p_run_id);
  v_memory:=public.read_theme_memory_context(p_run_id,statement_timestamp());
  SELECT snapshot_hash INTO STRICT v_snapshot_hash FROM public.market_intelligence_memory_context_bindings_v2 WHERE run_id=p_run_id;
  RETURN v_base||jsonb_build_object('theme_memory',v_memory,'theme_memory_snapshot_hash',v_snapshot_hash,'radar',v_memory->'radar','urgent_events',v_memory->'urgent_events','high_materiality_themes',v_memory->'high_materiality_themes');
END;
$$;

CREATE OR REPLACE FUNCTION public.read_owner_intelligence_v2(p_limit INT DEFAULT 25)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_themes JSONB; v_run UUID; v_as_of TIMESTAMPTZ; v_available INT;
BEGIN
  IF p_limit NOT BETWEEN 1 AND 25 THEN RAISE EXCEPTION 'invalid intelligence projection limit' USING ERRCODE='22023'; END IF;
  SELECT packet.run_id,packet.created_at INTO v_run,v_as_of FROM public.market_evidence_packets packet JOIN public.market_intelligence_run_events done ON done.run_id=packet.run_id AND done.status='completed' WHERE packet.packet->>'contract_version'='2' AND packet.packet->>'execution_allowed'='false' ORDER BY packet.created_at DESC,packet.id DESC LIMIT 1;
  IF v_run IS NULL THEN RETURN jsonb_build_object('intelligence_version',2,'run_id',NULL,'data_as_of',NULL,'themes','[]'::jsonb,'companies','[]'::jsonb,'evidence','[]'::jsonb,'source_health','[]'::jsonb,'coverage',jsonb_build_object('mode','bounded','complete_market_coverage',false),'reference',jsonb_build_object('state','unavailable'),'scope',jsonb_build_object('research_only',true,'market_wide',true),'backlog',jsonb_build_object('available',0,'returned',0,'deferred',0,'byte_truncated',false),'omissions',jsonb_build_array('No completed validated v2 research packet is available.'),'boundaries',jsonb_build_object('research_only',true,'execution_disabled',true,'valuation_unavailable',true)); END IF;
  SELECT count(DISTINCT revision.episode_id) INTO v_available
  FROM public.market_theme_episode_revisions_v2 revision
  JOIN public.market_intelligence_run_events done ON done.run_id=revision.origin_run_id AND done.status='completed'
  WHERE revision.created_at<=v_as_of;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('theme_id',head.theme_id,'episode_id',head.episode_id,'revision_id',head.revision_id,'revision',head.revision,'mechanism',head.theme_mechanism,'subject',head.subject_identity,'jurisdiction',head.jurisdiction,'state',CASE WHEN head.state='open' AND head.expires_at>v_as_of THEN 'active' WHEN head.expires_at<=v_as_of THEN 'expired' ELSE 'closed' END,'first_seen',head.first_seen,'last_seen',head.last_seen,'next_review_at',head.next_review_at,'expires_at',head.expires_at,'adverse_evidence_count',jsonb_array_length(head.opposing_source_ids),'missing_questions',COALESCE((SELECT jsonb_agg(to_jsonb(left(question.value,500)) ORDER BY question.ordinality) FROM (SELECT value,ordinality FROM jsonb_array_elements_text(head.missing_questions) WITH ORDINALITY LIMIT 24) question),'[]'::jsonb),'invalidation_conditions',COALESCE((SELECT jsonb_agg(to_jsonb(left(condition.value,500)) ORDER BY condition.ordinality) FROM (SELECT value,ordinality FROM jsonb_array_elements_text(head.invalidation_conditions) WITH ORDINALITY LIMIT 24) condition),'[]'::jsonb)) ORDER BY (head.revision>1) DESC,(head.opposing_source_ids<>'[]'::jsonb) DESC,head.last_seen DESC,head.episode_id),'[]'::jsonb) INTO v_themes FROM (
    SELECT latest.* FROM (
      SELECT DISTINCT ON (episode_id) * FROM public.market_theme_episode_revisions_v2
      WHERE created_at<=v_as_of ORDER BY episode_id,revision DESC
    ) latest
    JOIN public.market_intelligence_run_events done
      ON done.run_id=latest.origin_run_id AND done.status='completed'
    ORDER BY (latest.revision>1) DESC,(latest.opposing_source_ids<>'[]'::jsonb) DESC,
      latest.last_seen DESC,latest.episode_id LIMIT p_limit
  ) head;
  v_result:=jsonb_build_object('intelligence_version',2,'data_as_of',v_as_of,'run_id',v_run,'themes',v_themes,
    'companies',(SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'company_id',COALESCE(candidate->>'entity_id',candidate->>'candidate_key'),
      'name',left(candidate->>'candidate_key',240),'ticker',candidate->'ticker',
      'outside_watchlist',CASE WHEN candidate->'ticker'='null'::jsonb THEN 'null'::jsonb ELSE to_jsonb(
        NOT COALESCE((SELECT input.holding_market_values ? (candidate->>'ticker') FROM public.market_intelligence_context_inputs input WHERE input.run_id=v_run),false)
        AND NOT COALESCE((SELECT EXISTS(SELECT 1 FROM jsonb_array_elements(binding.context->'radar') watch WHERE watch->>'ticker'=candidate->>'ticker') FROM public.market_intelligence_memory_context_bindings_v2 binding WHERE binding.run_id=v_run),false)
      ) END,
      'relationship_paths',COALESCE((SELECT jsonb_agg(to_jsonb(left(path.value,500)) ORDER BY path.ordinality) FROM (SELECT value,ordinality FROM jsonb_array_elements_text(candidate->'roles') WITH ORDINALITY LIMIT 4) path),'[]'::jsonb),
      'evidence_ids',COALESCE((SELECT jsonb_agg(ref->'item_id' ORDER BY ref->>'item_id') FROM (SELECT value AS ref FROM jsonb_array_elements(candidate->'evidence') LIMIT 8) bounded_ref),'[]'::jsonb),
      'missing_inputs',COALESCE((SELECT jsonb_agg(to_jsonb(left(missing.value,500)) ORDER BY missing.ordinality) FROM (SELECT value,ordinality FROM jsonb_array_elements_text(COALESCE(candidate->'suitability'->'missing_reasons','[]'::jsonb)) WITH ORDINALITY LIMIT 16) missing),'[]'::jsonb)
    ) ORDER BY candidate->>'candidate_key'),'[]'::jsonb) FROM (SELECT value AS candidate FROM public.market_evidence_packets packet CROSS JOIN LATERAL jsonb_array_elements(packet.packet->'research_candidates') WHERE packet.run_id=v_run ORDER BY value->>'candidate_key' LIMIT 12) candidates),
    'evidence',(SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'evidence_id',item->'item_id','label',left(item->'source_identity'->>'provider'||' evidence',256),
      'url',CASE WHEN item->>'canonical_url' ~ '^https://(www\.sec\.gov/(Archives|files|ixviewer)/|data\.sec\.gov/(submissions|api/xbrl)/|www\.federalregister\.gov/(documents|api/v1/documents)/|www\.whitehouse\.gov/briefing-room/|www\.energy\.gov/(articles|gdo|ceser|oe)/|www\.defense\.gov/(News|Contracts)/|api\.eia\.gov/v2/|www\.eia\.gov/(todayinenergy|electricity)/|api\.stlouisfed\.org/fred/|fred\.stlouisfed\.org/series/|api\.bls\.gov/publicAPI/|www\.bls\.gov/(news\.release|regions)/|apps\.bea\.gov/api/|www\.bea\.gov/(news|data)/|api\.gdeltproject\.org/api/v2/|www\.alphavantage\.co/query|finnhub\.io/api/v1/|query1\.finance\.yahoo\.com/(v8/finance/chart|v10/finance/quoteSummary)/)' AND item->>'canonical_url' !~ 'https://[^/]*@' AND item->>'canonical_url' !~ '^https://[^/]+:[0-9]+' THEN item->'canonical_url' ELSE 'null'::jsonb END,
      'passage',left(item->>'normalized_text',2000),
      'role',CASE WHEN EXISTS(SELECT 1 FROM public.market_evidence_packets packet2 CROSS JOIN LATERAL jsonb_array_elements(packet2.packet->'research_candidates') candidate2 CROSS JOIN LATERAL jsonb_array_elements(candidate2->'evidence') ref WHERE packet2.run_id=v_run AND ref->>'item_id'=item->>'item_id' AND ref->>'role'='opposing') THEN 'opposing' ELSE 'supporting' END,
      'retrieved_at',item->'retrieved_at'
    ) ORDER BY item->>'item_id'),'[]'::jsonb) FROM (SELECT value AS item FROM public.market_evidence_packets packet CROSS JOIN LATERAL jsonb_array_elements(packet.packet->'evidence') WHERE packet.run_id=v_run ORDER BY value->>'item_id' LIMIT 96) evidence_items),
    'source_health',(SELECT COALESCE(jsonb_agg(jsonb_build_object('provider',provider,'status',status,'retrieved_at',retrieved_at,'accepted_count',accepted_count,'dropped_count',dropped_count) ORDER BY provider,retrieved_at DESC),'[]'::jsonb) FROM (SELECT DISTINCT ON(provider) provider,status,retrieved_at,accepted_count,dropped_count FROM public.market_source_receipts WHERE run_id=v_run ORDER BY provider,retrieved_at DESC) source),
    'reference',COALESCE((SELECT jsonb_strip_nulls(jsonb_build_object('state',reference_status,'manifest_id',manifest_id,'as_of',reference_as_of,'age_seconds',reference_age_seconds)) FROM public.market_reference_run_bindings WHERE run_id=v_run ORDER BY created_at DESC LIMIT 1),jsonb_build_object('state','unavailable')),'coverage',jsonb_build_object('mode','bounded','complete_market_coverage',false),'scope',jsonb_build_object('research_only',true,'market_wide',true),'omissions',jsonb_build_array('Historical memory cannot become current evidence or suitability without current validation.'),'backlog',jsonb_build_object('available',v_available,'returned',jsonb_array_length(v_themes),'deferred',GREATEST(v_available-jsonb_array_length(v_themes),0),'byte_truncated',false),'boundaries',jsonb_build_object('research_only',true,'execution_disabled',true,'valuation_unavailable',true));
  WHILE octet_length(v_result::text)>98304 LOOP
    IF jsonb_array_length(v_result->'evidence')>0 THEN
      v_result:=jsonb_set(v_result,'{evidence}',(v_result->'evidence')-(jsonb_array_length(v_result->'evidence')-1));
      v_result:=jsonb_set(v_result,'{companies}',COALESCE((
        SELECT jsonb_agg(jsonb_set(company.value,'{evidence_ids}',COALESCE((
          SELECT jsonb_agg(to_jsonb(ref.value) ORDER BY ref.ordinality)
          FROM jsonb_array_elements_text(company.value->'evidence_ids') WITH ORDINALITY ref(value,ordinality)
          WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(v_result->'evidence') kept WHERE kept->>'evidence_id'=ref.value)
        ),'[]'::jsonb)) ORDER BY company.ordinality)
        FROM jsonb_array_elements(v_result->'companies') WITH ORDINALITY company(value,ordinality)
      ),'[]'::jsonb));
    ELSIF jsonb_array_length(v_result->'companies')>0 THEN
      v_result:=jsonb_set(v_result,'{companies}',(v_result->'companies')-(jsonb_array_length(v_result->'companies')-1));
    ELSIF jsonb_array_length(v_result->'themes')>0 THEN
      v_result:=jsonb_set(v_result,'{themes}',(v_result->'themes')-(jsonb_array_length(v_result->'themes')-1));
      v_result:=jsonb_set(v_result,'{backlog,returned}',to_jsonb(jsonb_array_length(v_result->'themes')));
      v_result:=jsonb_set(v_result,'{backlog,deferred}',to_jsonb(GREATEST(v_available-jsonb_array_length(v_result->'themes'),0)));
    ELSE
      RAISE EXCEPTION 'owner intelligence projection cannot satisfy byte bound' USING ERRCODE='22023';
    END IF;
    v_result:=jsonb_set(v_result,'{backlog,byte_truncated}','true'::jsonb);
  END LOOP;
  RETURN v_result;
END;
$$;

ALTER TABLE public.market_theme_episode_revisions_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reviewer_identity_receipts_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_research_nomination_requests_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_research_nominations_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_research_nomination_lifecycle_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_intelligence_memory_context_bindings_v2 ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.market_theme_episode_revisions_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON public.market_reviewer_identity_receipts_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON public.market_research_nomination_requests_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON public.market_research_nominations_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON public.market_research_nomination_lifecycle_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON public.market_intelligence_memory_context_bindings_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT SELECT ON public.market_theme_episode_revisions_v2,public.market_reviewer_identity_receipts_v2,public.market_research_nomination_requests_v2,public.market_research_nominations_v2,public.market_research_nomination_lifecycle_v2,public.market_intelligence_memory_context_bindings_v2 TO stock_agent_release_reader;
CREATE POLICY release_theme_memory_v2_select ON public.market_theme_episode_revisions_v2 FOR SELECT TO stock_agent_release_reader USING(true);
CREATE POLICY release_reviewer_identity_v2_select ON public.market_reviewer_identity_receipts_v2 FOR SELECT TO stock_agent_release_reader USING(true);
CREATE POLICY release_nomination_request_v2_select ON public.market_research_nomination_requests_v2 FOR SELECT TO stock_agent_release_reader USING(true);
CREATE POLICY release_nomination_v2_select ON public.market_research_nominations_v2 FOR SELECT TO stock_agent_release_reader USING(true);
CREATE POLICY release_nomination_lifecycle_v2_select ON public.market_research_nomination_lifecycle_v2 FOR SELECT TO stock_agent_release_reader USING(true);
CREATE POLICY release_memory_context_v2_select ON public.market_intelligence_memory_context_bindings_v2 FOR SELECT TO stock_agent_release_reader USING(true);

REVOKE ALL ON FUNCTION public.reject_theme_memory_v2_mutation(),public.refresh_market_intelligence_context_v2_internal(UUID) FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON FUNCTION public.record_theme_episode_revision_v2(UUID,JSONB),public.record_research_review_identity_v2(UUID,UUID,JSONB),public.record_research_nominations(UUID,UUID,JSONB),public.transition_research_nomination_v2(UUID,UUID,JSONB),public.read_theme_memory_context(UUID,TIMESTAMPTZ),public.refresh_market_intelligence_context(UUID) FROM PUBLIC,anon,authenticated,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.record_theme_episode_revision_v2(UUID,JSONB),public.record_research_review_identity_v2(UUID,UUID,JSONB),public.transition_research_nomination_v2(UUID,UUID,JSONB),public.read_theme_memory_context(UUID,TIMESTAMPTZ),public.refresh_market_intelligence_context(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_research_nominations(UUID,UUID,JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.read_owner_intelligence_v2(INT) FROM PUBLIC,anon,authenticated,service_role,stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.read_owner_intelligence_v2(INT) TO stock_agent_dashboard;
