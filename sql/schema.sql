-- GENERATED CANONICAL FRESH-INSTALL SCHEMA.
-- Source of truth: sql/legacy_schema.sql followed by every reviewed migration after 20260904.
-- Regenerate with: python scripts/verify_schema_parity.py --write
-- Do not add credentials, data rows, or platform-owned Auth definitions here.

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

-- BEGIN REVIEWED MIGRATION: sql/migrations/20260905000000_multitenancy_foundation.sql

-- Multi-tenant identity and schema foundation.
--
-- This migration is deliberately additive. Legacy owner rows remain nullable until the
-- operator-confirmed, fail-closed backfill in the next release gate has proved parity.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_agent_migration_owner') THEN
    CREATE ROLE stock_agent_migration_owner NOLOGIN NOINHERIT;
  END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION stock_agent_migration_owner;
CREATE SCHEMA IF NOT EXISTS api AUTHORIZATION stock_agent_migration_owner;
CREATE SCHEMA IF NOT EXISTS machine AUTHORIZATION stock_agent_migration_owner;

ALTER SCHEMA app OWNER TO stock_agent_migration_owner;
ALTER SCHEMA api OWNER TO stock_agent_migration_owner;
ALTER SCHEMA machine OWNER TO stock_agent_migration_owner;

REVOKE CREATE ON SCHEMA public FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SCHEMA app, machine FROM PUBLIC, anon, authenticated;
REVOKE CREATE ON SCHEMA api FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS app.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  display_name TEXT CHECK (display_name IS NULL OR char_length(display_name) <= 120),
  timezone TEXT NOT NULL DEFAULT 'America/Chicago'
    CHECK (char_length(timezone) BETWEEN 1 AND 100),
  status TEXT NOT NULL DEFAULT 'invited'
    CHECK (status IN ('invited', 'active', 'deactivated', 'deletion_pending')),
  onboarding_completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.app_admins (
  user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'operator' CHECK (role IN ('operator', 'admin')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.user_consents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  document_version TEXT NOT NULL CHECK (char_length(document_version) BETWEEN 1 AND 100),
  source TEXT NOT NULL CHECK (source IN ('web', 'operator')),
  accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, document_version)
);

CREATE TABLE IF NOT EXISTS app.notification_preferences (
  owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  pre_market_enabled BOOLEAN NOT NULL DEFAULT true,
  intraday_enabled BOOLEAN NOT NULL DEFAULT true,
  post_market_enabled BOOLEAN NOT NULL DEFAULT true,
  operational_enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.agent_connections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  provider TEXT NOT NULL CHECK (provider = 'claude'),
  credential_type TEXT NOT NULL CHECK (credential_type = 'claude_routine_v1'),
  inbound_token_digest BYTEA,
  outbound_trigger_secret_id UUID,
  capabilities JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(capabilities) = 'object'),
  contract_version INT NOT NULL DEFAULT 2 CHECK (contract_version > 0),
  status TEXT NOT NULL DEFAULT 'disabled'
    CHECK (status IN ('disabled', 'testing', 'ready', 'active', 'revoked')),
  last_handshake_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id)
);

CREATE TABLE IF NOT EXISTS app.analysis_schedules (
  owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  primary_connection_id UUID,
  timezone TEXT NOT NULL DEFAULT 'America/Chicago'
    CHECK (char_length(timezone) BETWEEN 1 AND 100),
  pre_market_enabled BOOLEAN NOT NULL DEFAULT true,
  intraday_enabled BOOLEAN NOT NULL DEFAULT true,
  post_market_enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT analysis_schedules_owner_connection_fkey
    FOREIGN KEY (owner_id, primary_connection_id)
    REFERENCES app.agent_connections(owner_id, id)
    ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS app.telegram_links (
  owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  telegram_chat_id BIGINT NOT NULL UNIQUE,
  telegram_user_id BIGINT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ,
  CHECK (
    (status = 'active' AND revoked_at IS NULL)
    OR (status = 'revoked' AND revoked_at IS NOT NULL)
  )
);

ALTER TABLE app.profiles OWNER TO stock_agent_migration_owner;
ALTER TABLE app.app_admins OWNER TO stock_agent_migration_owner;
ALTER TABLE app.user_consents OWNER TO stock_agent_migration_owner;
ALTER TABLE app.notification_preferences OWNER TO stock_agent_migration_owner;
ALTER TABLE app.agent_connections OWNER TO stock_agent_migration_owner;
ALTER TABLE app.analysis_schedules OWNER TO stock_agent_migration_owner;
ALTER TABLE app.telegram_links OWNER TO stock_agent_migration_owner;

DO $$
DECLARE
  target_table TEXT;
  key_column TEXT;
  constraint_name TEXT;
BEGIN
  FOR target_table, key_column IN
    SELECT * FROM (VALUES
      ('holdings', 'ticker'),
      ('analysis_runs', 'id'),
      ('transactions', 'id'),
      ('portfolio_commands', 'id'),
      ('telegram_updates', 'telegram_update_id'),
      ('suggestions', 'id'),
      ('suggestion_grades', 'id'),
      ('stock_observations', 'id'),
      ('daily_snapshots', 'id'),
      ('dry_powder', 'month'),
      ('radar', 'ticker'),
      ('lessons', 'id'),
      ('paper_watches', 'id'),
      ('market_gateway_requests', 'request_id'),
      ('decision_evaluations', 'id'),
      ('market_publications', 'id'),
      ('owner_investment_plans', 'id')
    ) AS owner_tables(table_name, primary_key_column)
  LOOP
    EXECUTE format(
      'ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS owner_id UUID',
      target_table
    );

    constraint_name := target_table || '_owner_id_fkey';
    IF NOT EXISTS (
      SELECT 1
      FROM pg_constraint
      WHERE conrelid = format('public.%I', target_table)::regclass
        AND conname = constraint_name
    ) THEN
      EXECUTE format(
        'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (owner_id) REFERENCES auth.users(id) ON DELETE RESTRICT NOT VALID',
        target_table,
        constraint_name
      );
    END IF;

    constraint_name := target_table || '_owner_' || key_column || '_key';
    IF NOT EXISTS (
      SELECT 1
      FROM pg_constraint
      WHERE conrelid = format('public.%I', target_table)::regclass
        AND conname = constraint_name
    ) THEN
      EXECUTE format(
        'ALTER TABLE public.%I ADD CONSTRAINT %I UNIQUE (owner_id, %I)',
        target_table,
        constraint_name,
        key_column
      );
    END IF;
  END LOOP;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA app FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA app FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS app.single_owner_migration_receipts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE RESTRICT,
  owner_id_hash TEXT NOT NULL UNIQUE CHECK (owner_id_hash ~ '^[0-9a-f]{64}$'),
  before_counts JSONB NOT NULL CHECK (jsonb_typeof(before_counts) = 'object'),
  after_counts JSONB NOT NULL CHECK (jsonb_typeof(after_counts) = 'object'),
  row_digest TEXT NOT NULL CHECK (row_digest ~ '^[0-9a-f]{64}$'),
  relationship_digest TEXT NOT NULL CHECK (relationship_digest ~ '^[0-9a-f]{64}$'),
  passed BOOLEAN NOT NULL CHECK (passed),
  completed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE app.single_owner_migration_receipts OWNER TO stock_agent_migration_owner;
REVOKE ALL ON app.single_owner_migration_receipts FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION app.reject_owner_id_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
    RAISE EXCEPTION 'owner_id is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$$;
ALTER FUNCTION app.reject_owner_id_mutation() OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.reject_owner_id_mutation() FROM PUBLIC, anon, authenticated, service_role;
DROP TRIGGER IF EXISTS reject_owner_id_mutation ON app.single_owner_migration_receipts;
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.single_owner_migration_receipts
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

CREATE OR REPLACE FUNCTION machine.single_owner_table_counts(p_schema NAME)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  target_table TEXT;
  row_count BIGINT;
  result JSONB := '{}'::jsonb;
BEGIN
  IF p_schema::text NOT IN ('public', 'app') THEN
    RAISE EXCEPTION 'unsupported migration schema';
  END IF;
  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format('SELECT count(*) FROM %I.%I', p_schema, target_table) INTO row_count;
    result := result || jsonb_build_object(target_table, row_count);
  END LOOP;
  RETURN result;
END
$$;

CREATE OR REPLACE FUNCTION machine.single_owner_row_digest(p_schema NAME)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  target_table TEXT;
  table_digest TEXT;
  digest_material TEXT := '';
BEGIN
  IF p_schema::text NOT IN ('public', 'app') THEN
    RAISE EXCEPTION 'unsupported migration schema';
  END IF;
  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format(
      'SELECT encode(extensions.digest(coalesce(jsonb_agg(to_jsonb(r) - ''owner_id'' ORDER BY (to_jsonb(r) - ''owner_id'')::text)::text, ''[]''), ''sha256''), ''hex'') FROM %I.%I AS r',
      p_schema,
      target_table
    ) INTO table_digest;
    digest_material := digest_material || target_table || ':' || table_digest || E'\n';
  END LOOP;
  RETURN encode(extensions.digest(digest_material, 'sha256'), 'hex');
END
$$;

CREATE OR REPLACE FUNCTION machine.single_owner_relationship_digest(p_schema NAME)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  target_table TEXT;
  relationship_columns TEXT;
  table_digest TEXT;
  digest_material TEXT := '';
BEGIN
  IF p_schema::text NOT IN ('public', 'app') THEN
    RAISE EXCEPTION 'unsupported migration schema';
  END IF;
  FOR target_table, relationship_columns IN
    SELECT * FROM (VALUES
      ('analysis_runs', 'id, gateway_request_id'),
      ('suggestions', 'id, run_id, evaluation_id'),
      ('suggestion_grades', 'id, suggestion_id'),
      ('stock_observations', 'id, run_id'),
      ('daily_snapshots', 'id, run_id'),
      ('radar', 'ticker, updated_run_id'),
      ('lessons', 'id, run_id'),
      ('paper_watches', 'id, opened_run_id, closed_run_id'),
      ('market_gateway_requests', 'request_id, run_id'),
      ('decision_evaluations', 'id, request_id, run_id'),
      ('market_publications', 'id, idempotency_key, run_id')
    ) AS relationships(table_name, column_list)
  LOOP
    EXECUTE format(
      'SELECT encode(extensions.digest(coalesce(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text)::text, ''[]''), ''sha256''), ''hex'') FROM (SELECT %s FROM %I.%I) AS r',
      relationship_columns,
      p_schema,
      target_table
    ) INTO table_digest;
    digest_material := digest_material || target_table || ':' || table_digest || E'\n';
  END LOOP;
  RETURN encode(extensions.digest(digest_material, 'sha256'), 'hex');
END
$$;

CREATE OR REPLACE FUNCTION machine.backfill_single_owner_to_tenant(p_owner_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  target_table TEXT;
  unknown_rows BIGINT;
  duplicate_rows BIGINT;
  before_counts JSONB;
  after_counts JSONB;
  before_row_digest TEXT;
  after_row_digest TEXT;
  before_relationship_digest TEXT;
  after_relationship_digest TEXT;
  receipt JSONB;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('stock-agent-single-owner-backfill', 0));

  IF p_owner_id IS NULL OR NOT EXISTS (SELECT 1 FROM auth.users WHERE id = p_owner_id) THEN
    RAISE EXCEPTION 'owner must be exactly one existing Auth user';
  END IF;

  SELECT to_jsonb(r) - 'id' - 'owner_id'
  INTO receipt
  FROM app.single_owner_migration_receipts AS r
  WHERE r.owner_id = p_owner_id AND r.passed;
  IF receipt IS NOT NULL THEN
    RETURN receipt;
  END IF;

  IF to_regclass('public.holdings') IS NULL OR to_regclass('app.holdings') IS NOT NULL THEN
    RAISE EXCEPTION 'legacy schema is not in a clean pre-migration state';
  END IF;

  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format(
      'SELECT count(*) FROM public.%I WHERE owner_id IS NOT NULL AND owner_id <> $1',
      target_table
    ) INTO unknown_rows USING p_owner_id;
    IF unknown_rows > 0 THEN
      RAISE EXCEPTION 'unknown owner rows in %', target_table;
    END IF;
  END LOOP;

  IF EXISTS (
    SELECT 1 FROM public.holdings
    WHERE bucket IS NOT NULL AND bucket NOT IN ('core', 'growth', 'speculative', 'unclassified')
  ) OR EXISTS (
    SELECT 1 FROM public.radar
    WHERE bucket_guess IS NOT NULL AND bucket_guess NOT IN ('core', 'growth', 'speculative', 'unclassified')
  ) OR EXISTS (
    SELECT 1 FROM public.suggestions
    WHERE action NOT IN ('buy', 'add', 'hold', 'reduce', 'sell', 'watch', 'avoid')
       OR (bucket IS NOT NULL AND bucket NOT IN ('core', 'growth', 'speculative', 'unclassified'))
       OR (confidence IS NOT NULL AND confidence NOT IN ('low', 'medium', 'high'))
  ) OR EXISTS (
    SELECT 1 FROM public.portfolio_commands
    WHERE bucket IS NOT NULL AND bucket NOT IN ('core', 'growth', 'speculative')
  ) OR EXISTS (
    SELECT 1 FROM public.owner_investment_plans WHERE bucket <> 'core'
  ) THEN
    RAISE EXCEPTION 'unsupported legacy label';
  END IF;

  IF EXISTS (
    SELECT 1 FROM public.suggestion_grades AS c
    LEFT JOIN public.suggestions AS p ON p.id = c.suggestion_id
    WHERE c.suggestion_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.suggestions AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.run_id
    WHERE c.run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.suggestions AS c
    LEFT JOIN public.decision_evaluations AS p ON p.id = c.evaluation_id
    WHERE c.evaluation_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.stock_observations AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.run_id
    WHERE c.run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.daily_snapshots AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.run_id
    WHERE c.run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.lessons AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.run_id
    WHERE c.run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.radar AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.updated_run_id
    WHERE c.updated_run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.paper_watches AS c
    LEFT JOIN public.analysis_runs AS opened ON opened.id = c.opened_run_id
    LEFT JOIN public.analysis_runs AS closed ON closed.id = c.closed_run_id
    WHERE (c.opened_run_id IS NOT NULL AND opened.id IS NULL)
       OR (c.closed_run_id IS NOT NULL AND closed.id IS NULL)
  ) OR EXISTS (
    SELECT 1 FROM public.market_gateway_requests AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.run_id
    WHERE c.run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.decision_evaluations AS c
    LEFT JOIN public.market_gateway_requests AS request ON request.request_id = c.request_id
    LEFT JOIN public.analysis_runs AS run ON run.id = c.run_id
    WHERE (c.request_id IS NOT NULL AND request.request_id IS NULL)
       OR (c.run_id IS NOT NULL AND run.id IS NULL)
  ) OR EXISTS (
    SELECT 1 FROM public.market_publications AS c
    LEFT JOIN public.market_gateway_requests AS request ON request.request_id = c.idempotency_key
    LEFT JOIN public.analysis_runs AS run ON run.id = c.run_id
    WHERE request.request_id IS NULL OR (c.run_id IS NOT NULL AND run.id IS NULL)
  ) OR EXISTS (
    SELECT 1 FROM public.analysis_runs AS c
    LEFT JOIN public.market_gateway_requests AS p ON p.request_id = c.gateway_request_id
    WHERE c.gateway_request_id IS NOT NULL AND p.request_id IS NULL
  ) THEN
    RAISE EXCEPTION 'orphaned relationship';
  END IF;

  FOR target_table, duplicate_rows IN
    SELECT 'holdings', count(*) FROM (
      SELECT ticker FROM public.holdings GROUP BY ticker HAVING count(*) > 1
    ) AS duplicates
    UNION ALL
    SELECT 'dry_powder', count(*) FROM (
      SELECT month FROM public.dry_powder GROUP BY month HAVING count(*) > 1
    ) AS duplicates
    UNION ALL
    SELECT 'radar', count(*) FROM (
      SELECT ticker FROM public.radar GROUP BY ticker HAVING count(*) > 1
    ) AS duplicates
    UNION ALL
    SELECT 'daily_snapshots', count(*) FROM (
      SELECT snap_date, ticker FROM public.daily_snapshots
      GROUP BY snap_date, ticker HAVING count(*) > 1
    ) AS duplicates
    UNION ALL
    SELECT 'owner_investment_plans', count(*) FROM (
      SELECT ticker FROM public.owner_investment_plans GROUP BY ticker HAVING count(*) > 1
    ) AS duplicates
  LOOP
    IF duplicate_rows > 0 THEN
      RAISE EXCEPTION 'duplicate owner key in %', target_table;
    END IF;
  END LOOP;

  before_counts := machine.single_owner_table_counts('public');
  before_row_digest := machine.single_owner_row_digest('public');
  before_relationship_digest := machine.single_owner_relationship_digest('public');

  INSERT INTO app.profiles (id, status)
  VALUES (p_owner_id, 'active')
  ON CONFLICT (id) DO NOTHING;
  INSERT INTO app.notification_preferences (owner_id)
  VALUES (p_owner_id)
  ON CONFLICT (owner_id) DO NOTHING;

  -- The legacy append-only trigger intentionally rejects every update. Disable only this
  -- reviewed maintenance trigger inside the migration transaction, then restore it below.
  DROP TRIGGER decision_evaluations_append_only ON public.decision_evaluations;

  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format('UPDATE public.%I SET owner_id = $1 WHERE owner_id IS NULL', target_table)
      USING p_owner_id;
    EXECUTE format('ALTER TABLE public.%I ALTER COLUMN owner_id SET NOT NULL', target_table);
  END LOOP;

  ALTER TABLE public.holdings DROP CONSTRAINT holdings_owner_ticker_key;
  ALTER TABLE public.holdings DROP CONSTRAINT holdings_pkey;
  ALTER TABLE public.holdings ADD CONSTRAINT holdings_pkey PRIMARY KEY (owner_id, ticker);

  ALTER TABLE public.dry_powder DROP CONSTRAINT dry_powder_owner_month_key;
  ALTER TABLE public.dry_powder DROP CONSTRAINT dry_powder_pkey;
  ALTER TABLE public.dry_powder ADD CONSTRAINT dry_powder_pkey PRIMARY KEY (owner_id, month);

  ALTER TABLE public.radar DROP CONSTRAINT radar_owner_ticker_key;
  ALTER TABLE public.radar DROP CONSTRAINT radar_pkey;
  ALTER TABLE public.radar ADD CONSTRAINT radar_pkey PRIMARY KEY (owner_id, ticker);

  ALTER TABLE public.daily_snapshots DROP CONSTRAINT daily_snapshots_snap_date_ticker_key;
  ALTER TABLE public.daily_snapshots ADD CONSTRAINT daily_snapshots_owner_date_ticker_key
    UNIQUE (owner_id, snap_date, ticker);

  ALTER TABLE public.owner_investment_plans DROP CONSTRAINT owner_investment_plans_ticker_key;
  ALTER TABLE public.owner_investment_plans ADD CONSTRAINT owner_investment_plans_owner_ticker_key
    UNIQUE (owner_id, ticker);

  DROP INDEX public.one_market_publication_per_run;
  CREATE UNIQUE INDEX one_market_publication_per_run
    ON public.market_publications (owner_id, run_id) WHERE run_id IS NOT NULL;
  DROP INDEX public.one_holiday_publication_per_market_date;
  CREATE UNIQUE INDEX one_holiday_publication_per_market_date
    ON public.market_publications (owner_id, market_date, phase, kind) WHERE kind = 'holiday';

  ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (run_id);
  ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_owner_evaluation_fkey
    FOREIGN KEY (owner_id, evaluation_id) REFERENCES public.decision_evaluations(owner_id, id) ON DELETE RESTRICT;
  ALTER TABLE public.suggestion_grades ADD CONSTRAINT suggestion_grades_owner_suggestion_fkey
    FOREIGN KEY (owner_id, suggestion_id) REFERENCES public.suggestions(owner_id, id) ON DELETE RESTRICT;
  ALTER TABLE public.stock_observations ADD CONSTRAINT stock_observations_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (run_id);
  ALTER TABLE public.daily_snapshots ADD CONSTRAINT daily_snapshots_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (run_id);
  ALTER TABLE public.lessons ADD CONSTRAINT lessons_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (run_id);
  ALTER TABLE public.radar ADD CONSTRAINT radar_owner_run_fkey
    FOREIGN KEY (owner_id, updated_run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (updated_run_id);
  ALTER TABLE public.paper_watches ADD CONSTRAINT paper_watches_owner_opened_run_fkey
    FOREIGN KEY (owner_id, opened_run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (opened_run_id);
  ALTER TABLE public.paper_watches ADD CONSTRAINT paper_watches_owner_closed_run_fkey
    FOREIGN KEY (owner_id, closed_run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (closed_run_id);
  ALTER TABLE public.market_gateway_requests ADD CONSTRAINT market_gateway_requests_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (run_id);
  ALTER TABLE public.decision_evaluations ADD CONSTRAINT decision_evaluations_owner_request_fkey
    FOREIGN KEY (owner_id, request_id) REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;
  ALTER TABLE public.decision_evaluations ADD CONSTRAINT decision_evaluations_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id) ON DELETE RESTRICT;
  ALTER TABLE public.market_publications ADD CONSTRAINT market_publications_owner_request_fkey
    FOREIGN KEY (owner_id, idempotency_key) REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;
  ALTER TABLE public.market_publications ADD CONSTRAINT market_publications_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id) ON DELETE RESTRICT;
  ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_owner_gateway_request_fkey
    FOREIGN KEY (owner_id, gateway_request_id) REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;

  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format('ALTER TABLE public.%I SET SCHEMA app', target_table);
    EXECUTE format('ALTER TABLE app.%I OWNER TO stock_agent_migration_owner', target_table);
    EXECUTE format(
      'CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.%I FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation()',
      target_table
    );
  END LOOP;

  CREATE TRIGGER decision_evaluations_append_only
    BEFORE UPDATE OR DELETE ON app.decision_evaluations
    FOR EACH ROW EXECUTE FUNCTION public.reject_decision_evaluation_mutation();

  after_counts := machine.single_owner_table_counts('app');
  after_row_digest := machine.single_owner_row_digest('app');
  after_relationship_digest := machine.single_owner_relationship_digest('app');

  IF before_counts IS DISTINCT FROM after_counts THEN
    RAISE EXCEPTION 'row count changed during owner migration';
  END IF;
  IF before_row_digest IS DISTINCT FROM after_row_digest THEN
    RAISE EXCEPTION 'row digest changed during owner migration';
  END IF;
  IF before_relationship_digest IS DISTINCT FROM after_relationship_digest THEN
    RAISE EXCEPTION 'relationship digest changed during owner migration';
  END IF;

  INSERT INTO app.single_owner_migration_receipts (
    owner_id, owner_id_hash, before_counts, after_counts, row_digest,
    relationship_digest, passed
  ) VALUES (
    p_owner_id,
    encode(extensions.digest(p_owner_id::text, 'sha256'), 'hex'),
    before_counts,
    after_counts,
    after_row_digest,
    after_relationship_digest,
    true
  )
  RETURNING to_jsonb(single_owner_migration_receipts) - 'id' - 'owner_id' INTO receipt;

  RETURN receipt;
END
$$;

REVOKE ALL ON FUNCTION machine.single_owner_table_counts(NAME) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION machine.single_owner_row_digest(NAME) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION machine.single_owner_relationship_digest(NAME) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION machine.backfill_single_owner_to_tenant(UUID) FROM PUBLIC, anon, authenticated, service_role;

-- END REVIEWED MIGRATION: sql/migrations/20260905000000_multitenancy_foundation.sql

-- BEGIN FRESH-INSTALL BOOTSTRAP: sql/bootstrap/fresh_multitenancy.sql

-- Fresh-install-only structural transition after 20260905.
-- Every legacy table must be empty. Existing installations use the reviewed owner backfill instead.

DO $$
DECLARE
  target_table TEXT;
  row_count BIGINT;
BEGIN
  IF to_regclass('public.holdings') IS NULL OR to_regclass('app.holdings') IS NOT NULL THEN
    RAISE EXCEPTION 'fresh schema is not at the multitenancy bootstrap boundary';
  END IF;
  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format('SELECT count(*) FROM public.%I', target_table) INTO row_count;
    IF row_count <> 0 THEN
      RAISE EXCEPTION 'fresh bootstrap refuses non-empty table %', target_table;
    END IF;
    EXECUTE format('ALTER TABLE public.%I ALTER COLUMN owner_id SET NOT NULL', target_table);
  END LOOP;
END
$$;

DROP TRIGGER decision_evaluations_append_only ON public.decision_evaluations;

ALTER TABLE public.holdings DROP CONSTRAINT holdings_owner_ticker_key;
ALTER TABLE public.holdings DROP CONSTRAINT holdings_pkey;
ALTER TABLE public.holdings ADD CONSTRAINT holdings_pkey PRIMARY KEY (owner_id, ticker);

ALTER TABLE public.dry_powder DROP CONSTRAINT dry_powder_owner_month_key;
ALTER TABLE public.dry_powder DROP CONSTRAINT dry_powder_pkey;
ALTER TABLE public.dry_powder ADD CONSTRAINT dry_powder_pkey PRIMARY KEY (owner_id, month);

ALTER TABLE public.radar DROP CONSTRAINT radar_owner_ticker_key;
ALTER TABLE public.radar DROP CONSTRAINT radar_pkey;
ALTER TABLE public.radar ADD CONSTRAINT radar_pkey PRIMARY KEY (owner_id, ticker);

ALTER TABLE public.daily_snapshots DROP CONSTRAINT daily_snapshots_snap_date_ticker_key;
ALTER TABLE public.daily_snapshots ADD CONSTRAINT daily_snapshots_owner_date_ticker_key
  UNIQUE (owner_id, snap_date, ticker);

ALTER TABLE public.owner_investment_plans DROP CONSTRAINT owner_investment_plans_ticker_key;
ALTER TABLE public.owner_investment_plans ADD CONSTRAINT owner_investment_plans_owner_ticker_key
  UNIQUE (owner_id, ticker);

DROP INDEX public.one_market_publication_per_run;
CREATE UNIQUE INDEX one_market_publication_per_run
  ON public.market_publications (owner_id, run_id) WHERE run_id IS NOT NULL;
DROP INDEX public.one_holiday_publication_per_market_date;
CREATE UNIQUE INDEX one_holiday_publication_per_market_date
  ON public.market_publications (owner_id, market_date, phase, kind) WHERE kind = 'holiday';

ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_owner_run_fkey
  FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
  ON DELETE SET NULL (run_id);
ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_owner_evaluation_fkey
  FOREIGN KEY (owner_id, evaluation_id)
  REFERENCES public.decision_evaluations(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE public.suggestion_grades ADD CONSTRAINT suggestion_grades_owner_suggestion_fkey
  FOREIGN KEY (owner_id, suggestion_id)
  REFERENCES public.suggestions(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE public.stock_observations ADD CONSTRAINT stock_observations_owner_run_fkey
  FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
  ON DELETE SET NULL (run_id);
ALTER TABLE public.daily_snapshots ADD CONSTRAINT daily_snapshots_owner_run_fkey
  FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
  ON DELETE SET NULL (run_id);
ALTER TABLE public.lessons ADD CONSTRAINT lessons_owner_run_fkey
  FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
  ON DELETE SET NULL (run_id);
ALTER TABLE public.radar ADD CONSTRAINT radar_owner_run_fkey
  FOREIGN KEY (owner_id, updated_run_id) REFERENCES public.analysis_runs(owner_id, id)
  ON DELETE SET NULL (updated_run_id);
ALTER TABLE public.paper_watches ADD CONSTRAINT paper_watches_owner_opened_run_fkey
  FOREIGN KEY (owner_id, opened_run_id) REFERENCES public.analysis_runs(owner_id, id)
  ON DELETE SET NULL (opened_run_id);
ALTER TABLE public.paper_watches ADD CONSTRAINT paper_watches_owner_closed_run_fkey
  FOREIGN KEY (owner_id, closed_run_id) REFERENCES public.analysis_runs(owner_id, id)
  ON DELETE SET NULL (closed_run_id);
ALTER TABLE public.market_gateway_requests ADD CONSTRAINT market_gateway_requests_owner_run_fkey
  FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
  ON DELETE SET NULL (run_id);
ALTER TABLE public.decision_evaluations ADD CONSTRAINT decision_evaluations_owner_request_fkey
  FOREIGN KEY (owner_id, request_id)
  REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;
ALTER TABLE public.decision_evaluations ADD CONSTRAINT decision_evaluations_owner_run_fkey
  FOREIGN KEY (owner_id, run_id)
  REFERENCES public.analysis_runs(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE public.market_publications ADD CONSTRAINT market_publications_owner_request_fkey
  FOREIGN KEY (owner_id, idempotency_key)
  REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;
ALTER TABLE public.market_publications ADD CONSTRAINT market_publications_owner_run_fkey
  FOREIGN KEY (owner_id, run_id)
  REFERENCES public.analysis_runs(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_owner_gateway_request_fkey
  FOREIGN KEY (owner_id, gateway_request_id)
  REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;

DO $$
DECLARE
  target_table TEXT;
BEGIN
  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format('ALTER TABLE public.%I SET SCHEMA app', target_table);
    EXECUTE format('ALTER TABLE app.%I OWNER TO stock_agent_migration_owner', target_table);
    EXECUTE format(
      'CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.%I FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation()',
      target_table
    );
  END LOOP;
END
$$;

CREATE TRIGGER decision_evaluations_append_only
  BEFORE UPDATE OR DELETE ON app.decision_evaluations
  FOR EACH ROW EXECUTE FUNCTION public.reject_decision_evaluation_mutation();

DROP FUNCTION machine.backfill_single_owner_to_tenant(UUID);
DROP FUNCTION machine.single_owner_relationship_digest(NAME);
DROP FUNCTION machine.single_owner_row_digest(NAME);
DROP FUNCTION machine.single_owner_table_counts(NAME);

-- END FRESH-INSTALL BOOTSTRAP: sql/bootstrap/fresh_multitenancy.sql

-- BEGIN REVIEWED MIGRATION: sql/migrations/20260906000000_owner_api_and_machine_roles.sql

-- Forced row isolation and the release-one browser API allow-list.
-- Apply only after the single-owner backfill has moved owner tables into app.

REVOKE CREATE ON SCHEMA public FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SCHEMA app, machine FROM PUBLIC, anon, authenticated, service_role;
REVOKE CREATE ON SCHEMA api FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA api, app TO authenticated;
GRANT USAGE ON SCHEMA api TO anon;

CREATE TABLE IF NOT EXISTS app.owner_policy_overrides (
  owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  policy_version INT NOT NULL DEFAULT 1 CHECK (policy_version > 0),
  max_single_position_pct NUMERIC(5, 2) NOT NULL DEFAULT 20
    CHECK (max_single_position_pct > 0 AND max_single_position_pct <= 20),
  max_speculative_position_pct NUMERIC(5, 2) NOT NULL DEFAULT 5
    CHECK (max_speculative_position_pct > 0 AND max_speculative_position_pct <= 5),
  max_portfolio_drawdown_pct NUMERIC(5, 2) NOT NULL DEFAULT 15
    CHECK (max_portfolio_drawdown_pct > 0 AND max_portfolio_drawdown_pct <= 15),
  min_reward_risk NUMERIC(5, 2) NOT NULL DEFAULT 2
    CHECK (min_reward_risk >= 2 AND min_reward_risk <= 100),
  max_live_quote_age_seconds INT NOT NULL DEFAULT 900
    CHECK (max_live_quote_age_seconds > 0 AND max_live_quote_age_seconds <= 900),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE app.owner_policy_overrides OWNER TO stock_agent_migration_owner;
DROP TRIGGER IF EXISTS reject_owner_id_mutation ON app.owner_policy_overrides;
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.owner_policy_overrides
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

DO $$
DECLARE
  target_table TEXT;
  existing_policy RECORD;
BEGIN
  FOREACH target_table IN ARRAY ARRAY[
    'profiles', 'app_admins', 'user_consents', 'notification_preferences',
    'analysis_schedules', 'agent_connections', 'telegram_links',
    'single_owner_migration_receipts', 'owner_policy_overrides',
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', target_table);
    EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY', target_table);
    FOR existing_policy IN
      SELECT policyname
      FROM pg_policies
      WHERE schemaname = 'app' AND tablename = target_table
    LOOP
      EXECUTE format(
        'DROP POLICY %I ON app.%I', existing_policy.policyname, target_table
      );
    END LOOP;
  END LOOP;
END
$$;

CREATE POLICY profiles_owner_select ON app.profiles
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND id = (SELECT auth.uid()));
CREATE POLICY profiles_owner_update ON app.profiles
  FOR UPDATE TO authenticated
  USING (auth.uid() IS NOT NULL AND id = (SELECT auth.uid()))
  WITH CHECK (auth.uid() IS NOT NULL AND id = (SELECT auth.uid()));

CREATE POLICY user_consents_owner_select ON app.user_consents
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY user_consents_owner_insert ON app.user_consents
  FOR INSERT TO authenticated
  WITH CHECK (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

CREATE POLICY notification_preferences_owner_select ON app.notification_preferences
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY notification_preferences_owner_update ON app.notification_preferences
  FOR UPDATE TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()))
  WITH CHECK (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

CREATE POLICY analysis_schedules_owner_select ON app.analysis_schedules
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY agent_connections_owner_select ON app.agent_connections
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY telegram_links_owner_select ON app.telegram_links
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

CREATE POLICY holdings_owner_select ON app.holdings
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY analysis_runs_owner_select ON app.analysis_runs
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY transactions_owner_select ON app.transactions
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY suggestions_owner_select ON app.suggestions
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY owner_investment_plans_owner_select ON app.owner_investment_plans
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

REVOKE ALL ON ALL TABLES IN SCHEMA app FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA app FROM PUBLIC, anon, authenticated, service_role;

GRANT SELECT (id, display_name, timezone, status, onboarding_completed_at, created_at, updated_at)
  ON app.profiles TO authenticated;
GRANT UPDATE (display_name, timezone)
  ON app.profiles TO authenticated;
GRANT SELECT (owner_id, document_version, accepted_at)
  ON app.user_consents TO authenticated;
GRANT SELECT (owner_id, pre_market_enabled, intraday_enabled, post_market_enabled,
              operational_enabled, created_at, updated_at)
  ON app.notification_preferences TO authenticated;
GRANT UPDATE (pre_market_enabled, intraday_enabled, post_market_enabled, operational_enabled)
  ON app.notification_preferences TO authenticated;
GRANT SELECT (owner_id, primary_connection_id, timezone, pre_market_enabled,
              intraday_enabled, post_market_enabled, created_at, updated_at)
  ON app.analysis_schedules TO authenticated;
GRANT SELECT (owner_id, id, public_id, provider, credential_type, capabilities,
              contract_version, status, last_handshake_at, created_at, updated_at)
  ON app.agent_connections TO authenticated;
GRANT SELECT (owner_id, status, linked_at, revoked_at)
  ON app.telegram_links TO authenticated;
GRANT SELECT (owner_id, ticker, shares, avg_cost, bucket, opened_at, stop, target,
              high_water_price, hold_override_until)
  ON app.holdings TO authenticated;
GRANT SELECT (owner_id, id, ts, ticker, side, qty, price, source, executed_on)
  ON app.transactions TO authenticated;
GRANT SELECT (owner_id, id, ticker, bucket, amount, cadence, next_due_on, due_day,
              active, created_at, updated_at)
  ON app.owner_investment_plans TO authenticated;
GRANT SELECT (owner_id, id, run_id, ts, date, ticker, action, bucket, depth, entry_zone_low,
              entry_zone_high, valid_until, stop, target, confidence, decisive_factor,
              risk_verdict, invalidation_level, reason, score, risk_band,
              price_at_suggestion, evidence_as_of, decision_mode)
  ON app.suggestions TO authenticated;
GRANT SELECT (owner_id, id, kind, started_at, finished_at, status, data_as_of,
              source_status, symbols, write_counts, summary)
  ON app.analysis_runs TO authenticated;

DROP VIEW IF EXISTS api.profile;
CREATE VIEW api.profile WITH (security_invoker = true) AS
SELECT id, display_name, timezone, status, onboarding_completed_at, created_at, updated_at
FROM app.profiles;

DROP VIEW IF EXISTS api.consents;
CREATE VIEW api.consents WITH (security_invoker = true) AS
SELECT document_version, accepted_at
FROM app.user_consents;

DROP VIEW IF EXISTS api.today;
CREATE VIEW api.today WITH (security_invoker = true) AS
SELECT id AS run_id,
       kind,
       started_at,
       finished_at,
       status,
       data_as_of,
       source_status,
       symbols,
       write_counts,
       summary
FROM app.analysis_runs
WHERE (started_at AT TIME ZONE 'America/Chicago')::date =
      (now() AT TIME ZONE 'America/Chicago')::date;

DROP VIEW IF EXISTS api.holdings;
CREATE VIEW api.holdings WITH (security_invoker = true) AS
SELECT ticker, shares, avg_cost, bucket, opened_at, stop, target,
       high_water_price, hold_override_until
FROM app.holdings;

DROP VIEW IF EXISTS api.transactions;
CREATE VIEW api.transactions WITH (security_invoker = true) AS
SELECT id, ts, ticker, side, qty, price, source, executed_on
FROM app.transactions;

DROP VIEW IF EXISTS api.plans;
CREATE VIEW api.plans WITH (security_invoker = true) AS
SELECT id, ticker, bucket, amount::text AS amount, cadence, next_due_on, due_day,
       active, created_at, updated_at
FROM app.owner_investment_plans;

DROP VIEW IF EXISTS api.recommendations;
CREATE VIEW api.recommendations WITH (security_invoker = true) AS
SELECT id, run_id, ts, date, ticker, action, bucket, depth, entry_zone_low,
       entry_zone_high, valid_until, stop, target, confidence, decisive_factor,
       risk_verdict, invalidation_level, reason, score, risk_band,
       price_at_suggestion, evidence_as_of, decision_mode
FROM app.suggestions;

DROP VIEW IF EXISTS api.runs;
CREATE VIEW api.runs WITH (security_invoker = true) AS
SELECT id, kind, started_at, finished_at, status, data_as_of,
       source_status, symbols, write_counts, summary
FROM app.analysis_runs;

DROP VIEW IF EXISTS api.connections;
CREATE VIEW api.connections WITH (security_invoker = true) AS
SELECT id, public_id, provider, credential_type, capabilities,
       contract_version, status, last_handshake_at, created_at, updated_at
FROM app.agent_connections;

DROP VIEW IF EXISTS api.telegram_status;
CREATE VIEW api.telegram_status WITH (security_invoker = true) AS
SELECT status, linked_at, revoked_at
FROM app.telegram_links;

DROP VIEW IF EXISTS api.settings;
CREATE VIEW api.settings WITH (security_invoker = true) AS
SELECT p.display_name,
       p.timezone,
       n.pre_market_enabled AS notify_pre_market,
       n.intraday_enabled AS notify_intraday,
       n.post_market_enabled AS notify_post_market,
       n.operational_enabled AS notify_operational,
       s.primary_connection_id,
       s.timezone AS schedule_timezone,
       s.pre_market_enabled AS schedule_pre_market,
       s.intraday_enabled AS schedule_intraday,
       s.post_market_enabled AS schedule_post_market
FROM app.profiles AS p
LEFT JOIN app.notification_preferences AS n ON n.owner_id = p.id
LEFT JOIN app.analysis_schedules AS s ON s.owner_id = p.id;

ALTER VIEW api.profile OWNER TO stock_agent_migration_owner;
ALTER VIEW api.consents OWNER TO stock_agent_migration_owner;
ALTER VIEW api.today OWNER TO stock_agent_migration_owner;
ALTER VIEW api.holdings OWNER TO stock_agent_migration_owner;
ALTER VIEW api.transactions OWNER TO stock_agent_migration_owner;
ALTER VIEW api.plans OWNER TO stock_agent_migration_owner;
ALTER VIEW api.recommendations OWNER TO stock_agent_migration_owner;
ALTER VIEW api.runs OWNER TO stock_agent_migration_owner;
ALTER VIEW api.connections OWNER TO stock_agent_migration_owner;
ALTER VIEW api.telegram_status OWNER TO stock_agent_migration_owner;
ALTER VIEW api.settings OWNER TO stock_agent_migration_owner;

REVOKE ALL ON ALL TABLES IN SCHEMA api FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.profile, api.consents, api.today, api.holdings, api.transactions, api.plans,
  api.recommendations, api.runs, api.connections, api.telegram_status, api.settings
  TO authenticated;

DO $$
DECLARE
  exposed_function REGPROCEDURE;
BEGIN
  FOR exposed_function IN
    SELECT p.oid::regprocedure
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
  LOOP
    EXECUTE format(
      'REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated',
      exposed_function
    );
  END LOOP;
END
$$;

ALTER DEFAULT PRIVILEGES IN SCHEMA app
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA api
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE stock_agent_migration_owner IN SCHEMA app
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE stock_agent_migration_owner IN SCHEMA api
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;

DO $$
DECLARE
  role_name TEXT;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'stock_agent_gateway',
    'stock_agent_scheduler',
    'stock_agent_telegram',
    'stock_agent_backup'
  ]
  LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS', role_name);
    END IF;
    EXECUTE format('REVOKE ALL ON SCHEMA public, app, api, auth FROM %I', role_name);
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'vault') THEN
      EXECUTE format('REVOKE ALL ON SCHEMA vault FROM %I', role_name);
    END IF;
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA app FROM %I', role_name);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA app FROM %I', role_name);
    EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA machine FROM %I', role_name);
    EXECUTE format('GRANT USAGE ON SCHEMA machine TO %I', role_name);
  END LOOP;
END
$$;

-- END REVIEWED MIGRATION: sql/migrations/20260906000000_owner_api_and_machine_roles.sql

-- BEGIN REVIEWED MIGRATION: sql/migrations/20260907000000_ledger_projection_commands.sql

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
SELECT ticker, shares::text AS shares, avg_cost::text AS avg_cost, bucket, opened_at,
       stop::text AS stop, target::text AS target,
       high_water_price::text AS high_water_price, hold_override_until,
       projection_sequence::text AS projection_sequence
FROM app.holdings;
ALTER VIEW api.holdings OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.holdings FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.holdings TO authenticated;

CREATE VIEW api.transactions WITH (security_invoker = true) AS
SELECT id, ts AS created_at, ticker, event_type, side, qty::text AS qty,
       price::text AS price, fees::text AS fees,
       executed_on, ledger_sequence::text AS ledger_sequence, bucket, source_channel,
       corrects_transaction_id
FROM app.transactions;
ALTER VIEW api.transactions OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.transactions FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.transactions TO authenticated;

-- END REVIEWED MIGRATION: sql/migrations/20260907000000_ledger_projection_commands.sql

-- BEGIN REVIEWED MIGRATION: sql/migrations/20260908000000_portfolio_command_state_machine.sql

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

-- END REVIEWED MIGRATION: sql/migrations/20260908000000_portfolio_command_state_machine.sql

-- BEGIN REVIEWED MIGRATION: sql/migrations/20260908010000_app_api_limits.sql

-- One browser RPC boundary: authenticated dispatch, database-backed limits, and body-free audit.

CREATE TABLE app.app_api_rate_limits (
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  scope TEXT NOT NULL CHECK (char_length(scope) BETWEEN 1 AND 80),
  client_key TEXT NOT NULL CHECK (
    client_key = 'owner' OR client_key ~ '^[0-9a-f]{64}$'
  ),
  window_started_at TIMESTAMPTZ NOT NULL,
  hit_count INT NOT NULL CHECK (hit_count > 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id, scope, client_key)
);
ALTER TABLE app.app_api_rate_limits OWNER TO stock_agent_migration_owner;
ALTER TABLE app.app_api_rate_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.app_api_rate_limits FORCE ROW LEVEL SECURITY;
CREATE POLICY app_api_rate_limits_executor_all ON app.app_api_rate_limits
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.app_api_rate_limits
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

CREATE TABLE app.app_api_audit_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  request_id UUID NOT NULL,
  route TEXT NOT NULL CHECK (char_length(route) BETWEEN 1 AND 100),
  result_code TEXT NOT NULL CHECK (result_code ~ '^[A-Z][A-Z0-9_]{1,99}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, request_id)
);
ALTER TABLE app.app_api_audit_events OWNER TO stock_agent_migration_owner;
ALTER TABLE app.app_api_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.app_api_audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY app_api_audit_events_executor_all ON app.app_api_audit_events
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.app_api_audit_events
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

REVOKE ALL ON app.app_api_rate_limits, app.app_api_audit_events
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON SEQUENCE app.app_api_audit_events_id_seq
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION app.consume_rate_limit(
  p_owner_id UUID,
  p_scope TEXT,
  p_client_key TEXT,
  p_limit INT,
  p_window_seconds INT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  observed_at TIMESTAMPTZ := clock_timestamp();
  window_value TIMESTAMPTZ;
  count_value INT;
  retry_value INT;
BEGIN
  IF p_owner_id IS NULL OR p_scope !~ '^[a-z][a-z0-9_]{0,79}$'
     OR (p_client_key <> 'owner' AND p_client_key !~ '^[0-9a-f]{64}$')
     OR p_limit NOT BETWEEN 1 AND 1000
     OR p_window_seconds NOT BETWEEN 1 AND 86400 THEN
    RAISE EXCEPTION 'invalid rate limit parameters';
  END IF;
  window_value := to_timestamp(
    floor(extract(epoch FROM observed_at) / p_window_seconds) * p_window_seconds
  );
  INSERT INTO app.app_api_rate_limits (
    owner_id, scope, client_key, window_started_at, hit_count, updated_at
  ) VALUES (
    p_owner_id, p_scope, p_client_key, window_value, 1, observed_at
  )
  ON CONFLICT (owner_id, scope, client_key) DO UPDATE
  SET window_started_at = CASE
        WHEN app.app_api_rate_limits.window_started_at = window_value
          THEN app.app_api_rate_limits.window_started_at
        ELSE window_value END,
      hit_count = CASE
        WHEN app.app_api_rate_limits.window_started_at = window_value
          THEN app.app_api_rate_limits.hit_count + 1
        ELSE 1 END,
      updated_at = observed_at
  RETURNING hit_count INTO count_value;
  retry_value := greatest(
    1,
    ceil(extract(epoch FROM window_value + make_interval(secs => p_window_seconds) - observed_at))::int
  );
  RETURN jsonb_build_object(
    'allowed', count_value <= p_limit,
    'retry_after_seconds', CASE WHEN count_value <= p_limit THEN 0 ELSE retry_value END
  );
END
$$;

CREATE OR REPLACE FUNCTION api.app_dispatch(
  p_route TEXT,
  p_request_id UUID,
  p_ip_digest TEXT,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID := auth.uid();
  scope_value TEXT;
  limit_value INT;
  window_value INT;
  owner_limit JSONB;
  client_limit JSONB;
  data_value JSONB;
  result_code TEXT;
BEGIN
  IF owner_value IS NULL OR p_request_id IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'UNAUTHORIZED'));
  END IF;
  IF jsonb_typeof(p_request) <> 'object' OR p_ip_digest !~ '^[0-9a-f]{64}$' THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'INVALID_REQUEST'));
  END IF;

  CASE p_route
    WHEN 'POST /portfolio/preview',
         'POST /portfolio/correction/preview',
         'POST /plans/preview' THEN
      scope_value := 'command_preview'; limit_value := 30; window_value := 60;
    WHEN 'POST /portfolio/confirm',
         'POST /portfolio/correction/confirm',
         'POST /plans/confirm' THEN
      scope_value := 'command_confirm'; limit_value := 20; window_value := 60;
    WHEN 'POST /telegram/pairing-code' THEN
      scope_value := 'telegram_pairing'; limit_value := 5; window_value := 600;
    WHEN 'POST /connections/create',
         'POST /connections/handshake',
         'POST /connections/activate',
         'POST /connections/revoke' THEN
      scope_value := 'connection_change'; limit_value := 10; window_value := 3600;
    WHEN 'GET /settings', 'PATCH /settings' THEN
      scope_value := 'settings'; limit_value := 30; window_value := 60;
    WHEN 'GET /export' THEN
      scope_value := 'export'; limit_value := 2; window_value := 86400;
    WHEN 'POST /account/delete/request', 'POST /account/delete/confirm' THEN
      scope_value := 'account_delete'; limit_value := 3; window_value := 86400;
    ELSE
      RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'ROUTE_NOT_ALLOWED'));
  END CASE;

  owner_limit := app.consume_rate_limit(
    owner_value, scope_value, 'owner', limit_value, window_value
  );
  client_limit := app.consume_rate_limit(
    owner_value, scope_value, p_ip_digest, limit_value, window_value
  );
  IF NOT (owner_limit->>'allowed')::boolean OR NOT (client_limit->>'allowed')::boolean THEN
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, 'RATE_LIMITED');
    RETURN jsonb_build_object(
      'ok', false,
      'error', jsonb_build_object(
        'code', 'RATE_LIMITED',
        'retry_after_seconds', greatest(
          (owner_limit->>'retry_after_seconds')::int,
          (client_limit->>'retry_after_seconds')::int
        )
      )
    );
  END IF;

  BEGIN
    CASE p_route
      WHEN 'POST /portfolio/preview',
           'POST /portfolio/correction/preview',
           'POST /plans/preview' THEN
        data_value := api.preview_portfolio_command(p_request);
      WHEN 'POST /portfolio/confirm',
           'POST /portfolio/correction/confirm',
           'POST /plans/confirm' THEN
        IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['command_id','preview_digest']) THEN
          RAISE EXCEPTION 'invalid confirmation request';
        END IF;
        data_value := api.confirm_portfolio_command(
          (p_request->>'command_id')::uuid,
          p_request->>'preview_digest'
        );
      WHEN 'POST /telegram/pairing-code' THEN
        IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['code_digest'])
           OR p_request->>'code_digest' !~ '^[0-9a-f]{64}$' THEN
          RAISE EXCEPTION 'invalid pairing code request';
        END IF;
        data_value := app.issue_telegram_pairing_code(
          owner_value, p_request->>'code_digest'
        );
      WHEN 'POST /connections/create' THEN
        data_value := app.create_agent_connection(owner_value, p_request);
      WHEN 'POST /connections/handshake' THEN
        data_value := app.begin_agent_connection_handshake(owner_value, p_request);
      WHEN 'POST /connections/activate' THEN
        data_value := app.activate_agent_connection(owner_value, p_request);
      WHEN 'POST /connections/revoke' THEN
        data_value := app.revoke_agent_connection(owner_value, p_request);
      ELSE
        INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
        VALUES (owner_value, p_request_id, p_route, 'NOT_READY');
        RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'NOT_READY'));
    END CASE;
    result_code := upper(coalesce(data_value->>'status', 'OK'));
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, result_code);
    RETURN jsonb_build_object('ok', true, 'data', data_value, 'request_id', p_request_id);
  EXCEPTION
    WHEN invalid_text_representation OR invalid_parameter_value OR check_violation
      OR numeric_value_out_of_range OR datetime_field_overflow THEN
      result_code := 'INVALID_REQUEST';
    WHEN unique_violation OR serialization_failure THEN
      result_code := 'CONFLICT';
    WHEN insufficient_privilege THEN
      result_code := 'NOT_FOUND';
    WHEN OTHERS THEN
      result_code := CASE
        WHEN SQLERRM ~* '(invalid|cash total|bucket|required|holding not found|sell exceeds|expired)'
          THEN 'INVALID_REQUEST'
        WHEN SQLERRM ~* '(stale|changed|digest|idempotency|cancelled|already)'
          THEN 'CONFLICT'
        ELSE 'INTERNAL_ERROR'
      END;
  END;
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, result_code);
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', result_code));
END
$$;

ALTER FUNCTION app.consume_rate_limit(UUID, TEXT, TEXT, INT, INT)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB)
  OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.consume_rate_limit(UUID, TEXT, TEXT, INT, INT)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION api.preview_portfolio_command(JSONB) FROM authenticated;
REVOKE ALL ON FUNCTION api.confirm_portfolio_command(UUID, TEXT) FROM authenticated;
REVOKE ALL ON FUNCTION api.cancel_portfolio_command(UUID, TEXT) FROM authenticated;
REVOKE ALL ON FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB)
  FROM PUBLIC, anon, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB) TO authenticated;

-- END REVIEWED MIGRATION: sql/migrations/20260908010000_app_api_limits.sql

-- BEGIN REVIEWED MIGRATION: sql/migrations/20260908020000_telegram_multitenancy.sql

-- Private-chat pairing, opaque callbacks, and update replay membership.

CREATE TABLE app.telegram_pairing_codes (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  code_digest BYTEA NOT NULL UNIQUE CHECK (octet_length(code_digest) = 32),
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id),
  CHECK (expires_at > created_at)
);
CREATE UNIQUE INDEX telegram_pairing_codes_one_active_owner_idx
  ON app.telegram_pairing_codes(owner_id) WHERE consumed_at IS NULL;
CREATE INDEX telegram_pairing_codes_expiry_idx
  ON app.telegram_pairing_codes(expires_at) WHERE consumed_at IS NULL;
ALTER TABLE app.telegram_pairing_codes OWNER TO stock_agent_migration_owner;
ALTER TABLE app.telegram_pairing_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.telegram_pairing_codes FORCE ROW LEVEL SECURITY;
CREATE POLICY telegram_pairing_codes_executor_all ON app.telegram_pairing_codes
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.telegram_pairing_codes
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

CREATE TABLE app.telegram_callback_tokens (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  command_id UUID NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('confirm','cancel')),
  token_digest BYTEA NOT NULL UNIQUE CHECK (octet_length(token_digest) = 32),
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  invalidated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (owner_id, command_id)
    REFERENCES app.portfolio_commands(owner_id, id) ON DELETE CASCADE,
  UNIQUE (owner_id, command_id, action),
  CHECK (expires_at > created_at),
  CHECK (consumed_at IS NULL OR invalidated_at IS NULL)
);
CREATE INDEX telegram_callback_tokens_expiry_idx
  ON app.telegram_callback_tokens(expires_at)
  WHERE consumed_at IS NULL AND invalidated_at IS NULL;
CREATE INDEX telegram_updates_retention_idx
  ON app.telegram_updates(received_at);
ALTER TABLE app.telegram_callback_tokens OWNER TO stock_agent_migration_owner;
ALTER TABLE app.telegram_callback_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.telegram_callback_tokens FORCE ROW LEVEL SECURITY;
CREATE POLICY telegram_callback_tokens_executor_all ON app.telegram_callback_tokens
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.telegram_callback_tokens
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

CREATE TABLE machine.telegram_pairing_attempts (
  identity_digest BYTEA PRIMARY KEY CHECK (octet_length(identity_digest) = 32),
  window_started_at TIMESTAMPTZ NOT NULL,
  failed_attempts SMALLINT NOT NULL DEFAULT 0 CHECK (failed_attempts BETWEEN 0 AND 5),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE machine.telegram_pairing_attempts OWNER TO stock_agent_migration_owner;

DROP POLICY telegram_links_executor_select ON app.telegram_links;
CREATE POLICY telegram_links_executor_all ON app.telegram_links
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY telegram_updates_executor_all ON app.telegram_updates
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

REVOKE ALL ON app.telegram_pairing_codes, app.telegram_callback_tokens
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON machine.telegram_pairing_attempts
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION app.issue_telegram_pairing_code(
  p_owner_id UUID,
  p_code_digest TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  code_value app.telegram_pairing_codes%ROWTYPE;
BEGIN
  IF p_owner_id IS NULL OR p_code_digest !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (SELECT 1 FROM app.profiles WHERE id = p_owner_id AND status = 'active') THEN
    RAISE EXCEPTION 'invalid pairing code request';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('telegram-pair:' || p_owner_id::text, 0));
  UPDATE app.telegram_pairing_codes
  SET consumed_at = now()
  WHERE owner_id = p_owner_id AND consumed_at IS NULL;
  INSERT INTO app.telegram_pairing_codes(owner_id, code_digest, expires_at)
  VALUES (p_owner_id, decode(p_code_digest, 'hex'), now() + interval '10 minutes')
  RETURNING * INTO code_value;
  RETURN jsonb_build_object(
    'pairing_id', code_value.id,
    'status', 'issued',
    'expires_at', code_value.expires_at
  );
END
$$;

CREATE OR REPLACE FUNCTION app.create_telegram_callback_tokens(
  p_owner_id UUID,
  p_command_id UUID,
  p_confirm_digest TEXT,
  p_cancel_digest TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  command_value app.portfolio_commands%ROWTYPE;
BEGIN
  IF p_confirm_digest !~ '^[0-9a-f]{64}$' OR p_cancel_digest !~ '^[0-9a-f]{64}$'
     OR p_confirm_digest = p_cancel_digest THEN
    RAISE EXCEPTION 'invalid callback token digests';
  END IF;
  SELECT * INTO command_value
  FROM app.portfolio_commands
  WHERE owner_id = p_owner_id AND id = p_command_id
    AND channel = 'telegram' AND status = 'previewed'
  FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'previewed telegram command not found'; END IF;
  INSERT INTO app.telegram_callback_tokens(
    owner_id, command_id, action, token_digest, expires_at
  ) VALUES
    (p_owner_id, p_command_id, 'confirm', decode(p_confirm_digest, 'hex'), command_value.expires_at),
    (p_owner_id, p_command_id, 'cancel', decode(p_cancel_digest, 'hex'), command_value.expires_at);
  RETURN jsonb_build_object('status', 'created', 'expires_at', command_value.expires_at);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_consume_pairing(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  identity_value BYTEA;
  code_value app.telegram_pairing_codes%ROWTYPE;
  attempt_value machine.telegram_pairing_attempts%ROWTYPE;
  existing_owner_link app.telegram_links%ROWTYPE;
  claimed_update BIGINT;
  chat_value BIGINT;
  update_value BIGINT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request,
    ARRAY['chat_id','user_id','update_id','code_digest','identity_digest','confirm_relink']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'code_digest' !~ '^[0-9a-f]{64}$'
    OR p_request->>'identity_digest' !~ '^[0-9a-f]{64}$'
    OR jsonb_typeof(p_request->'confirm_relink') <> 'boolean' THEN
    RETURN jsonb_build_object('status', 'invalid_code');
  END IF;
  chat_value := (p_request->>'chat_id')::bigint;
  update_value := (p_request->>'update_id')::bigint;
  IF chat_value <> (p_request->>'user_id')::bigint THEN
    RETURN jsonb_build_object('status', 'private_chat_required');
  END IF;
  identity_value := decode(p_request->>'identity_digest', 'hex');
  INSERT INTO machine.telegram_pairing_attempts(
    identity_digest, window_started_at, failed_attempts
  ) VALUES (identity_value, now(), 0)
  ON CONFLICT (identity_digest) DO UPDATE
  SET window_started_at = CASE
        WHEN machine.telegram_pairing_attempts.window_started_at <= now() - interval '10 minutes'
          THEN now() ELSE machine.telegram_pairing_attempts.window_started_at END,
      failed_attempts = CASE
        WHEN machine.telegram_pairing_attempts.window_started_at <= now() - interval '10 minutes'
          THEN 0 ELSE machine.telegram_pairing_attempts.failed_attempts END,
      updated_at = now()
  RETURNING * INTO attempt_value;
  IF attempt_value.failed_attempts >= 5 THEN
    RETURN jsonb_build_object('status', 'rate_limited');
  END IF;

  SELECT * INTO code_value
  FROM app.telegram_pairing_codes
  WHERE code_digest = decode(p_request->>'code_digest', 'hex')
    AND consumed_at IS NULL AND expires_at > now()
  FOR UPDATE;
  IF NOT FOUND THEN
    UPDATE machine.telegram_pairing_attempts
    SET failed_attempts = least(5, failed_attempts + 1), updated_at = now()
    WHERE identity_digest = identity_value;
    RETURN jsonb_build_object('status', 'invalid_code');
  END IF;

  IF EXISTS (
    SELECT 1 FROM app.telegram_links
    WHERE owner_id <> code_value.owner_id
      AND (telegram_chat_id = chat_value OR telegram_user_id = chat_value)
  ) THEN
    UPDATE machine.telegram_pairing_attempts
    SET failed_attempts = least(5, failed_attempts + 1), updated_at = now()
    WHERE identity_digest = identity_value;
    RETURN jsonb_build_object('status', 'invalid_code');
  END IF;
  SELECT * INTO existing_owner_link
  FROM app.telegram_links
  WHERE owner_id = code_value.owner_id
  FOR UPDATE;
  IF FOUND AND existing_owner_link.status = 'active'
     AND (existing_owner_link.telegram_chat_id <> chat_value
          OR existing_owner_link.telegram_user_id <> chat_value)
     AND NOT (p_request->>'confirm_relink')::boolean THEN
    RETURN jsonb_build_object('status', 'relink_required');
  END IF;

  INSERT INTO app.telegram_updates(owner_id, telegram_update_id, kind)
  VALUES (code_value.owner_id, update_value, 'message')
  ON CONFLICT (telegram_update_id) DO NOTHING
  RETURNING telegram_update_id INTO claimed_update;
  IF claimed_update IS NULL THEN
    RETURN jsonb_build_object('status', 'duplicate');
  END IF;

  INSERT INTO app.telegram_links(
    owner_id, telegram_chat_id, telegram_user_id, status, linked_at, revoked_at
  ) VALUES (code_value.owner_id, chat_value, chat_value, 'active', now(), NULL)
  ON CONFLICT (owner_id) DO UPDATE
  SET telegram_chat_id = EXCLUDED.telegram_chat_id,
      telegram_user_id = EXCLUDED.telegram_user_id,
      status = 'active', linked_at = now(), revoked_at = NULL;
  UPDATE app.telegram_pairing_codes SET consumed_at = now() WHERE id = code_value.id;
  DELETE FROM machine.telegram_pairing_attempts WHERE identity_digest = identity_value;
  RETURN jsonb_build_object('status', 'linked');
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_claim_update(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  claimed_value BIGINT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id','update_id','kind'])
     OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
     OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
     OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
     OR p_request->>'kind' NOT IN ('message','callback_query') THEN
    RAISE EXCEPTION 'invalid telegram update';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501'; END IF;
  INSERT INTO app.telegram_updates(owner_id, telegram_update_id, kind)
  VALUES (owner_value, (p_request->>'update_id')::bigint, p_request->>'kind')
  ON CONFLICT (telegram_update_id) DO NOTHING
  RETURNING telegram_update_id INTO claimed_value;
  RETURN jsonb_build_object('claimed', claimed_value IS NOT NULL);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_resolve_link(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  linked_value BOOLEAN;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id']) THEN
    RETURN jsonb_build_object('linked', false);
  END IF;
  SELECT EXISTS (
    SELECT 1 FROM app.telegram_links
    WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
      AND telegram_user_id = (p_request->>'user_id')::bigint
      AND status = 'active'
  ) INTO linked_value;
  RETURN jsonb_build_object('linked', linked_value);
EXCEPTION WHEN OTHERS THEN
  RETURN jsonb_build_object('linked', false);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_unlink(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id']) THEN
    RAISE EXCEPTION 'invalid unlink request';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active'
  FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('status', 'unlinked'); END IF;
  UPDATE app.telegram_links
  SET status = 'revoked', revoked_at = now()
  WHERE owner_id = owner_value;
  UPDATE app.portfolio_commands
  SET status = 'cancelled', updated_at = now()
  WHERE owner_id = owner_value AND channel = 'telegram' AND status = 'previewed';
  UPDATE app.telegram_callback_tokens
  SET invalidated_at = now()
  WHERE owner_id = owner_value AND consumed_at IS NULL AND invalidated_at IS NULL;
  RETURN jsonb_build_object('status', 'unlinked');
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_resolve_callback(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  callback_value RECORD;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id','token_digest'])
     OR p_request->>'token_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid callback request';
  END IF;
  SELECT token.action, token.command_id, command.preview_digest
  INTO callback_value
  FROM app.telegram_callback_tokens AS token
  JOIN app.portfolio_commands AS command
    ON command.owner_id = token.owner_id AND command.id = token.command_id
  JOIN app.telegram_links AS link ON link.owner_id = token.owner_id
  WHERE token.token_digest = decode(p_request->>'token_digest', 'hex')
    AND token.expires_at > now() AND token.consumed_at IS NULL
    AND token.invalidated_at IS NULL
    AND link.status = 'active'
    AND link.telegram_chat_id = (p_request->>'chat_id')::bigint
    AND link.telegram_user_id = (p_request->>'user_id')::bigint;
  IF NOT FOUND THEN RAISE EXCEPTION 'callback unavailable' USING ERRCODE = '42501'; END IF;
  RETURN jsonb_build_object(
    'action', callback_value.action,
    'command_id', callback_value.command_id,
    'preview_digest', callback_value.preview_digest
  );
END
$$;

ALTER FUNCTION app.issue_telegram_pairing_code(UUID, TEXT) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.create_telegram_callback_tokens(UUID, UUID, TEXT, TEXT) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_consume_pairing(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_claim_update(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_resolve_link(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_unlink(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_resolve_callback(JSONB) OWNER TO stock_agent_migration_owner;

REVOKE ALL ON FUNCTION app.issue_telegram_pairing_code(UUID, TEXT),
  app.create_telegram_callback_tokens(UUID, UUID, TEXT, TEXT)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION machine.telegram_consume_pairing(JSONB),
  machine.telegram_claim_update(JSONB), machine.telegram_resolve_link(JSONB),
  machine.telegram_unlink(JSONB), machine.telegram_resolve_callback(JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_backup;
GRANT EXECUTE ON FUNCTION machine.telegram_consume_pairing(JSONB),
  machine.telegram_claim_update(JSONB), machine.telegram_resolve_link(JSONB),
  machine.telegram_unlink(JSONB), machine.telegram_resolve_callback(JSONB)
  TO stock_agent_telegram;

CREATE INDEX telegram_updates_received_idx ON app.telegram_updates(received_at);

-- END REVIEWED MIGRATION: sql/migrations/20260908020000_telegram_multitenancy.sql

-- BEGIN REVIEWED MIGRATION: sql/migrations/20260908030000_telegram_webhook_runtime.sql

-- Multi-user Telegram webhook runtime: atomic callbacks, bounded reads, and body-free receipts.

CREATE TABLE app.telegram_deliveries (
  id BIGSERIAL PRIMARY KEY,
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  telegram_update_id BIGINT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('message', 'callback_query')),
  status TEXT NOT NULL CHECK (status IN ('delivered', 'delivery_failed', 'delivery_unknown')),
  telegram_message_id BIGINT,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (owner_id, telegram_update_id)
    REFERENCES app.telegram_updates(owner_id, telegram_update_id) ON DELETE CASCADE,
  UNIQUE (owner_id, telegram_update_id),
  CHECK (telegram_message_id IS NULL OR telegram_message_id > 0)
);
ALTER TABLE app.telegram_deliveries OWNER TO stock_agent_migration_owner;
ALTER SEQUENCE app.telegram_deliveries_id_seq OWNER TO stock_agent_migration_owner;
ALTER TABLE app.telegram_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.telegram_deliveries FORCE ROW LEVEL SECURITY;
CREATE POLICY telegram_deliveries_executor_all ON app.telegram_deliveries
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.telegram_deliveries
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

REVOKE ALL ON app.telegram_deliveries FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON SEQUENCE app.telegram_deliveries_id_seq FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram,
  stock_agent_backup;

CREATE TABLE app.telegram_pairing_deliveries (
  telegram_update_id BIGINT PRIMARY KEY,
  pairing_status TEXT NOT NULL CHECK (
    pairing_status IN ('linked', 'relink_required', 'rate_limited', 'invalid_code', 'private_chat_required', 'duplicate')
  ),
  status TEXT NOT NULL CHECK (status IN ('delivered', 'delivery_failed', 'delivery_unknown')),
  telegram_message_id BIGINT,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (telegram_message_id IS NULL OR telegram_message_id > 0)
);
ALTER TABLE app.telegram_pairing_deliveries OWNER TO stock_agent_migration_owner;
ALTER TABLE app.telegram_pairing_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.telegram_pairing_deliveries FORCE ROW LEVEL SECURITY;
CREATE POLICY telegram_pairing_deliveries_executor_all ON app.telegram_pairing_deliveries
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
REVOKE ALL ON app.telegram_pairing_deliveries FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION machine.telegram_prepare_command(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  claim_value JSONB;
  preview_value JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request,
    ARRAY['chat_id','user_id','update_id','idempotency_key','command','confirm_digest','cancel_digest']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'idempotency_key' !~
      '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
    OR p_request->>'confirm_digest' !~ '^[0-9a-f]{64}$'
    OR p_request->>'cancel_digest' !~ '^[0-9a-f]{64}$'
    OR p_request->>'confirm_digest' = p_request->>'cancel_digest' THEN
    RAISE EXCEPTION 'invalid telegram command request';
  END IF;
  claim_value := machine.telegram_claim_update(jsonb_build_object(
    'chat_id', p_request->>'chat_id',
    'user_id', p_request->>'user_id',
    'update_id', p_request->>'update_id',
    'kind', 'message'
  ));
  IF claim_value->>'claimed' <> 'true' THEN
    RETURN jsonb_build_object('claimed', false);
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  preview_value := app.preview_portfolio_command(
    owner_value,
    'telegram',
    'telegram:' || owner_value::text,
    (p_request->>'idempotency_key')::uuid,
    p_request->'command'
  );
  PERFORM app.create_telegram_callback_tokens(
    owner_value,
    (preview_value->>'command_id')::uuid,
    p_request->>'confirm_digest',
    p_request->>'cancel_digest'
  );
  RETURN preview_value || jsonb_build_object('claimed', true);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_apply_callback(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  callback_value RECORD;
  command_result JSONB;
  claim_value JSONB;
  rejection_reason TEXT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['chat_id','user_id','update_id','action','token_digest']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'action' NOT IN ('confirm','cancel')
    OR p_request->>'token_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid callback request';
  END IF;

  claim_value := machine.telegram_claim_update(jsonb_build_object(
    'chat_id', p_request->>'chat_id',
    'user_id', p_request->>'user_id',
    'update_id', p_request->>'update_id',
    'kind', 'callback_query'
  ));
  IF claim_value->>'claimed' <> 'true' THEN
    RETURN jsonb_build_object('claimed', false);
  END IF;

  SELECT token.id, token.owner_id, token.command_id, token.action,
         command.preview_digest
  INTO callback_value
  FROM app.telegram_callback_tokens AS token
  JOIN app.portfolio_commands AS command
    ON command.owner_id = token.owner_id AND command.id = token.command_id
  JOIN app.telegram_links AS link ON link.owner_id = token.owner_id
  WHERE token.token_digest = decode(p_request->>'token_digest', 'hex')
    AND token.action = p_request->>'action'
    AND token.expires_at > now()
    AND token.consumed_at IS NULL
    AND token.invalidated_at IS NULL
    AND link.status = 'active'
    AND link.telegram_chat_id = (p_request->>'chat_id')::bigint
    AND link.telegram_user_id = (p_request->>'user_id')::bigint
  FOR UPDATE OF token;
  IF NOT FOUND THEN
    RETURN jsonb_build_object(
      'claimed', true, 'action', p_request->>'action',
      'status', 'unavailable', 'reason', 'callback_unavailable'
    );
  END IF;

  BEGIN
    IF callback_value.action = 'confirm' THEN
      command_result := app.confirm_portfolio_command(
        callback_value.owner_id,
        'telegram',
        'telegram:' || callback_value.owner_id::text,
        callback_value.command_id,
        callback_value.preview_digest
      );
    ELSE
      command_result := app.cancel_portfolio_command(
        callback_value.owner_id,
        'telegram',
        'telegram:' || callback_value.owner_id::text,
        callback_value.command_id,
        callback_value.preview_digest
      );
    END IF;
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    rejection_reason := 'command_no_longer_applicable';
    UPDATE app.portfolio_commands
    SET status = CASE WHEN expires_at <= now() THEN 'expired' ELSE 'error' END,
        error_code = 'TELEGRAM_CALLBACK_REJECTED', updated_at = now()
    WHERE owner_id = callback_value.owner_id
      AND id = callback_value.command_id
      AND status = 'previewed';
  END;

  UPDATE app.telegram_callback_tokens
  SET consumed_at = now()
  WHERE id = callback_value.id;
  UPDATE app.telegram_callback_tokens
  SET invalidated_at = now()
  WHERE owner_id = callback_value.owner_id
    AND command_id = callback_value.command_id
    AND id <> callback_value.id
    AND consumed_at IS NULL
    AND invalidated_at IS NULL;

  IF rejection_reason IS NOT NULL THEN
    RETURN jsonb_build_object(
      'claimed', true,
      'action', callback_value.action,
      'command_id', callback_value.command_id,
      'status', 'rejected',
      'reason', rejection_reason
    );
  END IF;

  RETURN jsonb_build_object(
    'claimed', true,
    'action', callback_value.action,
    'command_id', callback_value.command_id,
    'status', command_result->>'status',
    'result', command_result->'result'
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_portfolio(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  holdings_value JSONB;
  claim_value JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id','update_id'])
    OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$' THEN
    RAISE EXCEPTION 'invalid telegram portfolio request';
  END IF;
  claim_value := machine.telegram_claim_update(jsonb_build_object(
    'chat_id', p_request->>'chat_id', 'user_id', p_request->>'user_id',
    'update_id', p_request->>'update_id', 'kind', 'message'
  ));
  IF claim_value->>'claimed' <> 'true' THEN
    RETURN jsonb_build_object('claimed', false);
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT coalesce(jsonb_agg(to_jsonb(holding) ORDER BY holding.ticker), '[]'::jsonb)
  INTO holdings_value
  FROM (
    SELECT ticker, shares, avg_cost, bucket, stop, target, projection_sequence
    FROM app.holdings
    WHERE owner_id = owner_value
    ORDER BY ticker
    LIMIT 40
  ) AS holding;
  RETURN jsonb_build_object('claimed', true, 'holdings', holdings_value);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_plans(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  plans_value JSONB;
  claim_value JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id','update_id'])
    OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$' THEN
    RAISE EXCEPTION 'invalid telegram plans request';
  END IF;
  claim_value := machine.telegram_claim_update(jsonb_build_object(
    'chat_id', p_request->>'chat_id', 'user_id', p_request->>'user_id',
    'update_id', p_request->>'update_id', 'kind', 'message'
  ));
  IF claim_value->>'claimed' <> 'true' THEN
    RETURN jsonb_build_object('claimed', false);
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT coalesce(jsonb_agg(to_jsonb(plan) ORDER BY plan.next_due_on, plan.ticker), '[]'::jsonb)
  INTO plans_value
  FROM (
    SELECT ticker, amount, cadence, next_due_on, bucket
    FROM app.owner_investment_plans
    WHERE owner_id = owner_value AND active
    ORDER BY next_due_on, ticker
    LIMIT 20
  ) AS plan;
  RETURN jsonb_build_object('claimed', true, 'plans', plans_value);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_record_delivery(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  inserted_value BIGINT;
  message_value BIGINT;
  existing_value app.telegram_deliveries%ROWTYPE;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['chat_id','user_id','update_id','kind','status','message_id']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'kind' NOT IN ('message','callback_query')
    OR p_request->>'status' NOT IN ('delivered','delivery_failed','delivery_unknown')
    OR (p_request->'message_id' <> 'null'::jsonb
        AND p_request->>'message_id' !~ '^[1-9][0-9]{0,15}$') THEN
    RAISE EXCEPTION 'invalid telegram delivery receipt';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM app.telegram_updates
    WHERE owner_id = owner_value
      AND telegram_update_id = (p_request->>'update_id')::bigint
      AND kind = p_request->>'kind'
  ) THEN
    RAISE EXCEPTION 'telegram update was not claimed';
  END IF;
  message_value := CASE WHEN p_request->'message_id' = 'null'::jsonb
                        THEN NULL ELSE (p_request->>'message_id')::bigint END;

  INSERT INTO app.telegram_deliveries(
    owner_id, telegram_update_id, kind, status, telegram_message_id
  ) VALUES (
    owner_value,
    (p_request->>'update_id')::bigint,
    p_request->>'kind',
    p_request->>'status',
    message_value
  )
  ON CONFLICT (owner_id, telegram_update_id) DO NOTHING
  RETURNING id INTO inserted_value;

  IF inserted_value IS NULL THEN
    SELECT * INTO existing_value
    FROM app.telegram_deliveries
    WHERE owner_id = owner_value
      AND telegram_update_id = (p_request->>'update_id')::bigint;
    IF existing_value.kind <> p_request->>'kind'
       OR existing_value.status <> p_request->>'status'
       OR existing_value.telegram_message_id IS DISTINCT FROM message_value THEN
      RAISE EXCEPTION 'delivery receipt idempotency mismatch';
    END IF;
  END IF;
  RETURN jsonb_build_object('recorded', true, 'status', p_request->>'status');
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_record_pairing_delivery(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  message_value BIGINT;
  existing_value app.telegram_pairing_deliveries%ROWTYPE;
  inserted_value BIGINT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['update_id','pairing_status','status','message_id']
  ) OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'pairing_status' NOT IN (
      'linked', 'relink_required', 'rate_limited', 'invalid_code', 'private_chat_required', 'duplicate'
    )
    OR p_request->>'status' NOT IN ('delivered','delivery_failed','delivery_unknown')
    OR (p_request->'message_id' <> 'null'::jsonb
        AND p_request->>'message_id' !~ '^[1-9][0-9]{0,15}$') THEN
    RAISE EXCEPTION 'invalid telegram pairing delivery receipt';
  END IF;
  message_value := CASE WHEN p_request->'message_id' = 'null'::jsonb
                        THEN NULL ELSE (p_request->>'message_id')::bigint END;
  INSERT INTO app.telegram_pairing_deliveries(
    telegram_update_id, pairing_status, status, telegram_message_id
  ) VALUES (
    (p_request->>'update_id')::bigint,
    p_request->>'pairing_status',
    p_request->>'status',
    message_value
  )
  ON CONFLICT (telegram_update_id) DO NOTHING
  RETURNING telegram_update_id INTO inserted_value;
  IF inserted_value IS NULL THEN
    SELECT * INTO existing_value
    FROM app.telegram_pairing_deliveries
    WHERE telegram_update_id = (p_request->>'update_id')::bigint;
    IF existing_value.pairing_status <> p_request->>'pairing_status'
       OR existing_value.status <> p_request->>'status'
       OR existing_value.telegram_message_id IS DISTINCT FROM message_value THEN
      RAISE EXCEPTION 'pairing delivery receipt idempotency mismatch';
    END IF;
  END IF;
  RETURN jsonb_build_object('recorded', true, 'status', p_request->>'status');
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_resolve_link(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  linked_value BOOLEAN;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id'])
    OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id' THEN
    RETURN jsonb_build_object('linked', false);
  END IF;
  SELECT EXISTS (
    SELECT 1 FROM app.telegram_links
    WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
      AND telegram_user_id = (p_request->>'user_id')::bigint
      AND status = 'active'
  ) INTO linked_value;
  RETURN jsonb_build_object('linked', linked_value);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_claim_update(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  claimed_value BIGINT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id','update_id','kind'])
    OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'kind' NOT IN ('message','callback_query') THEN
    RAISE EXCEPTION 'invalid telegram update';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  INSERT INTO app.telegram_updates(owner_id, telegram_update_id, kind)
  VALUES (owner_value, (p_request->>'update_id')::bigint, p_request->>'kind')
  ON CONFLICT (telegram_update_id) DO NOTHING
  RETURNING telegram_update_id INTO claimed_value;
  RETURN jsonb_build_object('claimed', claimed_value IS NOT NULL);
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
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
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

CREATE OR REPLACE FUNCTION machine.telegram_unlink(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  claim_value JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id','update_id'])
    OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$' THEN
    RAISE EXCEPTION 'invalid unlink request';
  END IF;
  claim_value := machine.telegram_claim_update(jsonb_build_object(
    'chat_id', p_request->>'chat_id', 'user_id', p_request->>'user_id',
    'update_id', p_request->>'update_id', 'kind', 'message'
  ));
  IF claim_value->>'claimed' <> 'true' THEN
    RETURN jsonb_build_object('claimed', false);
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_updates
  WHERE telegram_update_id = (p_request->>'update_id')::bigint;
  UPDATE app.telegram_links
  SET status = 'revoked', revoked_at = now()
  WHERE owner_id = owner_value;
  UPDATE app.portfolio_commands
  SET status = 'cancelled', updated_at = now()
  WHERE owner_id = owner_value AND channel = 'telegram' AND status = 'previewed';
  UPDATE app.telegram_callback_tokens
  SET invalidated_at = now()
  WHERE owner_id = owner_value AND consumed_at IS NULL AND invalidated_at IS NULL;
  RETURN jsonb_build_object('claimed', true, 'status', 'unlinked');
END
$$;

ALTER FUNCTION machine.telegram_prepare_command(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_apply_callback(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_portfolio(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_plans(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_record_delivery(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_record_pairing_delivery(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_resolve_link(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_claim_update(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_preview_command(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_unlink(JSONB)
  OWNER TO stock_agent_migration_owner;

REVOKE ALL ON FUNCTION machine.telegram_confirm_command(JSONB),
  machine.telegram_cancel_command(JSONB), machine.telegram_resolve_callback(JSONB),
  machine.telegram_preview_command(JSONB)
  FROM stock_agent_telegram;
REVOKE ALL ON FUNCTION machine.telegram_prepare_command(JSONB),
  machine.telegram_apply_callback(JSONB), machine.telegram_portfolio(JSONB),
  machine.telegram_plans(JSONB), machine.telegram_record_delivery(JSONB),
  machine.telegram_record_pairing_delivery(JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION machine.telegram_prepare_command(JSONB),
  machine.telegram_apply_callback(JSONB), machine.telegram_portfolio(JSONB),
  machine.telegram_plans(JSONB), machine.telegram_record_delivery(JSONB),
  machine.telegram_record_pairing_delivery(JSONB)
  TO stock_agent_telegram;

CREATE INDEX telegram_deliveries_recorded_idx ON app.telegram_deliveries(recorded_at);
CREATE INDEX telegram_pairing_deliveries_recorded_idx
  ON app.telegram_pairing_deliveries(recorded_at);

-- END REVIEWED MIGRATION: sql/migrations/20260908030000_telegram_webhook_runtime.sql

-- BEGIN REVIEWED MIGRATION: sql/migrations/20260908040000_provider_runs_and_evidence.sql

-- Provider-neutral run evidence. Model submissions reference these server-owned facts by digest.

CREATE TABLE app.run_evidence (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  run_id UUID NOT NULL,
  evidence_id TEXT NOT NULL CHECK (char_length(evidence_id) BETWEEN 1 AND 100),
  source_run_id UUID,
  category TEXT NOT NULL CHECK (category IN (
    'market_snapshot', 'source_search', 'filing', 'fundamentals', 'news',
    'technicals', 'corporate_action', 'issuer', 'exchange'
  )),
  source_identifier TEXT NOT NULL CHECK (char_length(source_identifier) BETWEEN 1 AND 200),
  reference_identifier TEXT CHECK (
    reference_identifier IS NULL OR char_length(reference_identifier) BETWEEN 1 AND 500
  ),
  observed_at TIMESTAMPTZ,
  retrieved_at TIMESTAMPTZ NOT NULL,
  revalidated_at TIMESTAMPTZ,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  claims JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(claims) = 'array' AND jsonb_array_length(claims) <= 10
    AND octet_length(claims::text) <= 6000
  ),
  status TEXT NOT NULL CHECK (status IN (
    'fresh', 'stale', 'conflicting', 'missing', 'no_new_material_evidence',
    'suspected', 'needs_review', 'clear'
  )),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, run_id, evidence_id),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE CASCADE,
  FOREIGN KEY (owner_id, source_run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE RESTRICT,
  CHECK (revalidated_at IS NULL OR revalidated_at >= retrieved_at)
);

CREATE TABLE app.source_search_receipts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  run_id UUID NOT NULL,
  searched_at TIMESTAMPTZ NOT NULL,
  categories TEXT[] NOT NULL CHECK (
    cardinality(categories) BETWEEN 1 AND 8
    AND categories <@ ARRAY['filing','fundamentals','news','issuer','exchange','sector','macro']::text[]
  ),
  sources JSONB NOT NULL CHECK (
    jsonb_typeof(sources) = 'array' AND jsonb_array_length(sources) BETWEEN 1 AND 20
    AND octet_length(sources::text) <= 4000
  ),
  result_status TEXT NOT NULL CHECK (
    result_status IN ('material_evidence_found', 'no_new_material_evidence', 'source_unavailable')
  ),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE CASCADE
);

ALTER TABLE app.run_evidence OWNER TO stock_agent_migration_owner;
ALTER TABLE app.source_search_receipts OWNER TO stock_agent_migration_owner;

CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.run_evidence
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.source_search_receipts
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

ALTER TABLE app.run_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.run_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE app.source_search_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.source_search_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY run_evidence_executor_all ON app.run_evidence
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY source_search_receipts_executor_all ON app.source_search_receipts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

REVOKE ALL ON app.run_evidence, app.source_search_receipts
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE INDEX run_evidence_run_category_idx
  ON app.run_evidence(owner_id, run_id, category, retrieved_at DESC);
CREATE INDEX source_search_receipts_run_idx
  ON app.source_search_receipts(owner_id, run_id, searched_at DESC);

CREATE TABLE app.market_quote_cache (
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'),
  price NUMERIC(24, 8) NOT NULL CHECK (price > 0),
  previous_close NUMERIC(24, 8) CHECK (previous_close IS NULL OR previous_close > 0),
  provider TEXT NOT NULL CHECK (char_length(provider) BETWEEN 1 AND 80),
  source_timestamp TIMESTAMPTZ NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL,
  session TEXT NOT NULL CHECK (session IN ('PRE','REGULAR','POST','CLOSED')),
  adjustment_status TEXT NOT NULL CHECK (
    adjustment_status IN ('raw','adjusted','corporate_action_pending')
  ),
  status TEXT NOT NULL CHECK (
    status IN ('fresh','delayed','stale','conflicting','unavailable')
  ),
  content_digest TEXT NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
  conflict_basis_points INT CHECK (conflict_basis_points IS NULL OR conflict_basis_points >= 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id, ticker),
  CHECK (source_timestamp <= retrieved_at + interval '5 minutes')
);

CREATE TABLE app.corporate_action_states (
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'),
  state TEXT NOT NULL CHECK (state IN ('clear','suspected','needs_review')),
  event_type TEXT CHECK (
    event_type IS NULL OR event_type IN ('split','reverse_split','symbol_change','merger','delisting')
  ),
  ratio NUMERIC(24, 8) CHECK (ratio IS NULL OR ratio > 0),
  source_identifier TEXT CHECK (
    source_identifier IS NULL OR char_length(source_identifier) BETWEEN 1 AND 200
  ),
  source_reference TEXT CHECK (
    source_reference IS NULL OR char_length(source_reference) BETWEEN 1 AND 500
  ),
  reason_code TEXT NOT NULL CHECK (
    reason_code IN ('confirmed_and_normalized','unverified_corporate_action',
                    'corporate_action_conflict','no_corporate_action_signal')
  ),
  detected_at TIMESTAMPTZ NOT NULL,
  reviewed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id, ticker),
  CHECK (state <> 'clear' OR reason_code IN ('confirmed_and_normalized','no_corporate_action_signal'))
);

ALTER TABLE app.market_quote_cache OWNER TO stock_agent_migration_owner;
ALTER TABLE app.corporate_action_states OWNER TO stock_agent_migration_owner;
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.market_quote_cache
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.corporate_action_states
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

ALTER TABLE app.market_quote_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.market_quote_cache FORCE ROW LEVEL SECURITY;
ALTER TABLE app.corporate_action_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.corporate_action_states FORCE ROW LEVEL SECURITY;
CREATE POLICY market_quote_cache_owner_select ON app.market_quote_cache
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY market_quote_cache_executor_all ON app.market_quote_cache
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY corporate_action_states_owner_select ON app.corporate_action_states
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY corporate_action_states_executor_all ON app.corporate_action_states
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY radar_owner_quote_select ON app.radar
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

REVOKE ALL ON app.market_quote_cache, app.corporate_action_states
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT SELECT (owner_id, ticker, price, previous_close, provider, source_timestamp,
              retrieved_at, session, adjustment_status, status, conflict_basis_points)
  ON app.market_quote_cache TO authenticated;
GRANT SELECT (owner_id, ticker, state, event_type, ratio, reason_code, detected_at, reviewed_at)
  ON app.corporate_action_states TO authenticated;
GRANT SELECT (owner_id, ticker) ON app.radar TO authenticated;

CREATE VIEW api.market_quotes WITH (security_invoker = true) AS
SELECT quote.ticker,
       quote.price::text AS price,
       quote.previous_close::text AS previous_close,
       quote.provider,
       quote.source_timestamp AS as_of,
       quote.retrieved_at,
       quote.session,
       quote.adjustment_status,
       quote.status,
       quote.conflict_basis_points,
       coalesce(action.state, 'clear') AS corporate_action_state,
       coalesce(action.state IN ('suspected','needs_review'), false) AS alerts_suppressed
FROM app.market_quote_cache AS quote
LEFT JOIN app.corporate_action_states AS action
  ON action.owner_id = quote.owner_id AND action.ticker = quote.ticker
WHERE EXISTS (
  SELECT 1 FROM app.holdings AS holding
  WHERE holding.owner_id = quote.owner_id AND holding.ticker = quote.ticker
) OR EXISTS (
  SELECT 1 FROM app.radar AS watched
  WHERE watched.owner_id = quote.owner_id AND watched.ticker = quote.ticker
);
ALTER VIEW api.market_quotes OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.market_quotes FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.market_quotes TO authenticated;

CREATE INDEX market_quote_cache_freshness_idx
  ON app.market_quote_cache(owner_id, status, retrieved_at DESC);
CREATE INDEX corporate_action_states_review_idx
  ON app.corporate_action_states(owner_id, state, updated_at DESC);

-- Provider V2 gateway. The Edge function has no table privileges: every operation enters
-- through one owner-resolving, transaction-scoped SECURITY DEFINER function below.

ALTER TABLE app.market_gateway_requests
  ADD COLUMN connection_id UUID,
  ADD COLUMN input_digest TEXT CHECK (
    input_digest IS NULL OR input_digest ~ '^[0-9a-f]{64}$'
  );
ALTER TABLE app.market_gateway_requests
  ADD CONSTRAINT market_gateway_requests_owner_connection_fkey
  FOREIGN KEY (owner_id, connection_id)
  REFERENCES app.agent_connections(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE app.market_gateway_requests
  DROP CONSTRAINT market_gateway_requests_operation_check;
ALTER TABLE app.market_gateway_requests
  ADD CONSTRAINT market_gateway_requests_operation_check CHECK (operation IN (
    'start_run','read_context','record_artifacts','grade_due_decisions',
    'evaluate_and_publish','finish_run','read_bounded_context','submit_analysis',
    'record_permitted_artifacts'
  ));

ALTER TABLE app.analysis_runs
  ADD COLUMN connection_id UUID,
  ADD COLUMN market_date DATE,
  ADD COLUMN provider TEXT CHECK (provider IS NULL OR char_length(provider) BETWEEN 1 AND 50),
  ADD COLUMN model TEXT CHECK (model IS NULL OR char_length(model) BETWEEN 1 AND 100);
ALTER TABLE app.analysis_runs
  ADD CONSTRAINT analysis_runs_owner_connection_fkey
  FOREIGN KEY (owner_id, connection_id)
  REFERENCES app.agent_connections(owner_id, id) ON DELETE RESTRICT;

-- A run belongs to its New York market date, not the calendar date implied by
-- when its provider happened to start. This keeps delayed/retried routines on
-- the correct Today screen and avoids a Chicago-specific owner assumption.
GRANT SELECT (owner_id, id, kind, started_at, finished_at, status, data_as_of,
              source_status, symbols, write_counts, summary, market_date, provider, model)
  ON app.analysis_runs TO authenticated;
CREATE OR REPLACE VIEW api.today WITH (security_invoker = true) AS
SELECT id AS run_id,
       kind,
       started_at,
       finished_at,
       status,
       data_as_of,
       source_status,
       symbols,
       write_counts,
       summary,
       market_date,
       provider,
       model
FROM app.analysis_runs
WHERE market_date = (now() AT TIME ZONE 'America/New_York')::date;
ALTER VIEW api.today OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.today FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.today TO authenticated;

CREATE TABLE app.agent_analysis_submissions (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  run_id UUID NOT NULL,
  request_id UUID NOT NULL,
  provider TEXT NOT NULL CHECK (char_length(provider) BETWEEN 1 AND 50),
  model TEXT NOT NULL CHECK (char_length(model) BETWEEN 1 AND 100),
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  market_date DATE NOT NULL,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 65536),
  payload_digest TEXT NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status IN ('accepted','quarantined')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, request_id),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE CASCADE
);
ALTER TABLE app.agent_analysis_submissions OWNER TO stock_agent_migration_owner;
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.agent_analysis_submissions
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
ALTER TABLE app.agent_analysis_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.agent_analysis_submissions FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_analysis_submissions_executor_all ON app.agent_analysis_submissions
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
REVOKE ALL ON app.agent_analysis_submissions
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE INDEX agent_analysis_submissions_run_idx
  ON app.agent_analysis_submissions(owner_id, run_id, created_at DESC);

ALTER TABLE app.market_publications
  ADD COLUMN rendered_parts JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(rendered_parts) = 'array'
    AND jsonb_array_length(rendered_parts) <= 4
    AND octet_length(rendered_parts::text) <= 15000
  );
-- Preserve deliverable legacy rows without guessing prior multipart boundaries. An in-flight
-- legacy send is irrecoverably ambiguous and must never be retried after the migration.
UPDATE app.market_publications
SET rendered_parts = jsonb_build_array(rendered_body)
WHERE rendered_parts = '[]'::jsonb
  AND char_length(rendered_body) BETWEEN 1 AND 3500;
UPDATE app.market_publications
SET status = 'delivery_unknown', lease_token = NULL,
    error = 'MIGRATION_IN_FLIGHT_DELIVERY_UNKNOWN', updated_at = clock_timestamp()
WHERE status = 'sending';
UPDATE app.market_publications
SET status = 'suppressed', lease_token = NULL,
    error = 'MIGRATION_PUBLICATION_NOT_DELIVERABLE', updated_at = clock_timestamp()
WHERE status = 'ready' AND jsonb_array_length(rendered_parts) = 0;

-- FORCE RLS also applies to the table owner. These policies are intentionally granted only
-- to the non-login migration owner used by reviewed SECURITY DEFINER functions.
ALTER TABLE public.market_policy_config OWNER TO stock_agent_migration_owner;
ALTER TABLE public.market_policy_config FORCE ROW LEVEL SECURITY;
CREATE POLICY market_policy_config_executor_all ON public.market_policy_config
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
REVOKE ALL ON public.market_policy_config
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE POLICY agent_connections_executor_all ON app.agent_connections
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY user_consents_executor_all ON app.user_consents
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY analysis_schedules_executor_all ON app.analysis_schedules
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY analysis_runs_executor_all ON app.analysis_runs
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY market_gateway_requests_executor_all ON app.market_gateway_requests
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY decision_evaluations_executor_all ON app.decision_evaluations
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY notification_preferences_executor_all ON app.notification_preferences
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY dry_powder_executor_all ON app.dry_powder
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY suggestions_executor_all ON app.suggestions
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY suggestion_grades_executor_all ON app.suggestion_grades
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY stock_observations_executor_all ON app.stock_observations
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY daily_snapshots_executor_all ON app.daily_snapshots
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY radar_executor_all ON app.radar
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY lessons_executor_all ON app.lessons
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY paper_watches_executor_all ON app.paper_watches
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

CREATE OR REPLACE FUNCTION machine.agent_constant_time_equal(p_left BYTEA, p_right BYTEA)
RETURNS BOOLEAN
LANGUAGE plpgsql
IMMUTABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  left_padded BYTEA := substring(coalesce(p_left, ''::bytea) || decode(repeat('00', 32), 'hex') FROM 1 FOR 32);
  right_padded BYTEA := substring(coalesce(p_right, ''::bytea) || decode(repeat('00', 32), 'hex') FROM 1 FOR 32);
  difference INT := abs(octet_length(coalesce(p_left, ''::bytea)) - octet_length(coalesce(p_right, ''::bytea)));
  byte_index INT;
BEGIN
  FOR byte_index IN 0..31 LOOP
    difference := difference | (get_byte(left_padded, byte_index) # get_byte(right_padded, byte_index));
  END LOOP;
  RETURN difference = 0;
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_claim_operation(p_request JSONB, p_expected TEXT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_row app.agent_connections%ROWTYPE;
  run_row app.analysis_runs%ROWTYPE;
  existing_request app.market_gateway_requests%ROWTYPE;
  presented_digest BYTEA;
  request_digest TEXT;
  request_uuid UUID;
  run_uuid UUID;
  digest_matches BOOLEAN;
  connection_found BOOLEAN;
  operation_lease UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request,
    ARRAY['connection_id','secret_digest','contract_version','operation','request_id','run_id','dry_run','payload']
  ) OR jsonb_typeof(p_request->'payload') <> 'object'
     OR jsonb_typeof(p_request->'dry_run') <> 'boolean'
     OR p_request->>'operation' <> p_expected
     OR p_request->>'contract_version' <> '2'
     OR p_request->>'connection_id' !~ '^[0-9a-fA-F-]{36}$'
     OR p_request->>'request_id' !~ '^[0-9a-fA-F-]{36}$'
     OR p_request->>'secret_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;

  BEGIN
    request_uuid := (p_request->>'request_id')::uuid;
    presented_digest := decode(p_request->>'secret_digest', 'hex');
    IF p_expected = 'start_run' THEN
      IF p_request->'run_id' <> 'null'::jsonb THEN
        RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
      END IF;
    ELSE
      IF jsonb_typeof(p_request->'run_id') <> 'string' THEN
        RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
      END IF;
      run_uuid := (p_request->>'run_id')::uuid;
    END IF;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END;

  SELECT * INTO connection_row
  FROM app.agent_connections
  WHERE public_id = (p_request->>'connection_id')::uuid;
  connection_found := FOUND;

  digest_matches := machine.agent_constant_time_equal(
    coalesce(connection_row.inbound_token_digest, decode(repeat('00', 32), 'hex')),
    presented_digest
  );
  IF NOT connection_found OR NOT digest_matches OR connection_row.status NOT IN ('testing','active')
     OR connection_row.contract_version <> 2 THEN
    RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
  END IF;

  request_digest := encode(
    extensions.digest(convert_to((p_request - 'secret_digest')::text, 'UTF8'), 'sha256'),
    'hex'
  );
  SELECT * INTO existing_request
  FROM app.market_gateway_requests
  WHERE request_id = request_uuid;
  IF FOUND THEN
    IF existing_request.owner_id <> connection_row.owner_id
       OR existing_request.connection_id IS DISTINCT FROM connection_row.id
       OR existing_request.operation <> p_expected
       OR existing_request.input_digest IS DISTINCT FROM request_digest THEN
      RAISE EXCEPTION 'request replay conflict' USING ERRCODE = '23505';
    END IF;
    IF existing_request.status = 'completed' THEN
      RETURN jsonb_build_object(
        'duplicate', true, 'response', existing_request.response,
        'owner_id', connection_row.owner_id, 'connection_id', connection_row.id,
        'request_id', request_uuid, 'run_id', existing_request.run_id,
        'dry_run', false, 'connection_status', connection_row.status
      );
    END IF;
    RAISE EXCEPTION 'request replay conflict' USING ERRCODE = '23505';
  END IF;

  IF p_expected <> 'start_run' AND NOT (p_request->>'dry_run')::boolean THEN
    SELECT * INTO run_row
    FROM app.analysis_runs
    WHERE owner_id = connection_row.owner_id
      AND id = run_uuid
      AND connection_id = connection_row.id
      AND status = 'running';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
    END IF;
  END IF;

  IF (p_request->>'dry_run')::boolean THEN
    RETURN jsonb_build_object(
      'duplicate', false, 'owner_id', connection_row.owner_id,
      'connection_id', connection_row.id, 'request_id', request_uuid,
      'run_id', run_uuid, 'dry_run', true, 'provider', connection_row.provider,
      'connection_status', connection_row.status, 'lease_token', extensions.gen_random_uuid()
    );
  END IF;

  IF run_uuid IS NOT NULL AND (
    SELECT count(*) FROM app.market_gateway_requests
    WHERE owner_id = connection_row.owner_id AND run_id = run_uuid
      AND operation <> 'start_run'
  ) >= 12 THEN
    RAISE EXCEPTION 'run operation limit reached' USING ERRCODE = '54000';
  END IF;

  operation_lease := extensions.gen_random_uuid();
  INSERT INTO app.market_gateway_requests(
    owner_id, request_id, operation, run_id, connection_id, input_digest,
    status, lease_token
  ) VALUES (
    connection_row.owner_id, request_uuid, p_expected, run_uuid,
    connection_row.id, request_digest, 'claimed', operation_lease
  );
  RETURN jsonb_build_object(
    'duplicate', false, 'owner_id', connection_row.owner_id,
    'connection_id', connection_row.id, 'request_id', request_uuid,
    'run_id', run_uuid, 'dry_run', false, 'provider', connection_row.provider,
    'connection_status', connection_row.status, 'lease_token', operation_lease
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_complete_operation(
  p_owner_id UUID, p_request_id UUID, p_response JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
  IF octet_length(p_response::text) > 524288 THEN
    RAISE EXCEPTION 'agent response exceeds limit' USING ERRCODE = '54000';
  END IF;
  UPDATE app.market_gateway_requests
  SET status = 'completed', response = p_response,
      response_digest = encode(extensions.digest(convert_to(p_response::text, 'UTF8'), 'sha256'), 'hex'),
      finished_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND request_id = p_request_id AND status = 'claimed';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'agent request unavailable' USING ERRCODE = '42501';
  END IF;
  RETURN p_response;
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_start_run(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  payload JSONB := p_request->'payload';
  owner_uuid UUID;
  connection_uuid UUID;
  request_uuid UUID;
  run_uuid UUID := extensions.gen_random_uuid();
  phase_value TEXT;
  market_day DATE;
  trigger_uuid UUID;
  slot_row RECORD;
  slot_found BOOLEAN;
  response JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(payload, ARRAY['trigger_request_id'])
     OR (payload->'trigger_request_id' <> 'null'::jsonb
         AND jsonb_typeof(payload->'trigger_request_id') <> 'string') THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    IF payload->'trigger_request_id' <> 'null'::jsonb THEN
      trigger_uuid := (payload->>'trigger_request_id')::uuid;
    END IF;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END;

  claim := machine.agent_claim_operation(p_request, 'start_run');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  IF (claim->>'dry_run')::boolean THEN
    RETURN jsonb_build_object(
      'status', 'dry_run', 'run_id', run_uuid, 'phase', 'on-demand',
      'market_date', (clock_timestamp() AT TIME ZONE 'America/New_York')::date,
      'writes', 0
    );
  END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  connection_uuid := (claim->>'connection_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;

  IF trigger_uuid IS NULL THEN
    phase_value := 'on-demand';
    market_day := (clock_timestamp() AT TIME ZONE 'America/New_York')::date;
  ELSE
    SELECT * INTO slot_row FROM app.scheduled_run_slots
    WHERE owner_id = owner_uuid AND connection_id = connection_uuid
      AND trigger_request_id = trigger_uuid FOR UPDATE;
    slot_found := FOUND;
    IF NOT slot_found OR slot_row.holiday
       OR slot_row.status NOT IN ('claimed','triggered','trigger_unknown','provider_started') THEN
      RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
    END IF;
    phase_value := slot_row.phase;
    market_day := slot_row.market_date;
    IF slot_row.canonical_run_id IS NOT NULL THEN
      run_uuid := slot_row.canonical_run_id;
      UPDATE app.market_gateway_requests SET run_id = run_uuid
      WHERE owner_id = owner_uuid AND request_id = request_uuid;
      response := jsonb_build_object(
        'status', 'running', 'run_id', run_uuid, 'phase', phase_value,
        'market_date', market_day, 'canonical', true
      );
      RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
    END IF;
  END IF;
  IF claim->>'connection_status' = 'testing'
     AND (trigger_uuid IS NULL OR slot_row.purpose <> 'handshake') THEN
    RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(owner_uuid::text || ':' || connection_uuid::text || ':' || market_day::text, 0));
  IF (SELECT count(*) FROM app.analysis_runs
      WHERE owner_id = owner_uuid AND connection_id = connection_uuid
        AND market_date = market_day) >= 6 THEN
    RAISE EXCEPTION 'daily run limit reached' USING ERRCODE = '54000';
  END IF;
  IF phase_value = 'on-demand' AND EXISTS (
    SELECT 1 FROM app.analysis_runs
    WHERE owner_id = owner_uuid AND connection_id = connection_uuid
      AND kind = 'on-demand' AND started_at >= now() - interval '1 hour'
  ) THEN
    RAISE EXCEPTION 'on-demand run limit reached' USING ERRCODE = '54000';
  END IF;

  INSERT INTO app.analysis_runs(
    owner_id, id, kind, status, gateway_request_id, connection_id,
    market_date, provider
  ) VALUES (
    owner_uuid, run_uuid, phase_value, 'running', request_uuid, connection_uuid,
    market_day, claim->>'provider'
  );
  UPDATE app.market_gateway_requests
  SET run_id = run_uuid
  WHERE owner_id = owner_uuid AND request_id = request_uuid;
  IF trigger_uuid IS NOT NULL THEN
    UPDATE app.scheduled_run_slots SET canonical_run_id = run_uuid,
      status = 'provider_started', updated_at = clock_timestamp()
    WHERE owner_id = owner_uuid AND id = slot_row.id;
    UPDATE app.routine_trigger_attempts SET status = 'provider_started'
    WHERE owner_id = owner_uuid AND slot_id = slot_row.id
      AND status IN ('claimed','triggered','trigger_unknown');
  END IF;
  response := jsonb_build_object(
    'status', 'running', 'run_id', run_uuid, 'phase', phase_value,
    'market_date', market_day
  );
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_build_owner_context(
  p_owner_id UUID, p_run_id UUID, p_phase TEXT, p_market_date DATE, p_started_at TIMESTAMPTZ
) RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  SELECT jsonb_build_object(
    'run_id', p_run_id,
    'contract_version', 2,
    'phase', p_phase,
    'market_date', p_market_date,
    'started_at', p_started_at,
    'holdings', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT ticker, shares::text AS shares, avg_cost::text AS avg_cost, bucket,
             stop::text AS stop, target::text AS target,
             high_water_price::text AS high_water_price, hold_override_until,
             stop_alert_active, stop_near_alert_active,
             target_near_alert_active, target_alert_active
      FROM app.holdings WHERE owner_id = p_owner_id ORDER BY ticker LIMIT 100
    ) row_value),
    'holding_quotes', '{}'::jsonb,
    'realized_pnl_today', (
      SELECT coalesce(sum((result->>'realized_pnl')::numeric), 0)::text
      FROM app.portfolio_commands
      WHERE owner_id = p_owner_id AND status = 'applied'
        AND operation IN ('sell','sell_all')
        AND (applied_at AT TIME ZONE 'America/New_York')::date = p_market_date
        AND result->>'realized_pnl' ~ '^-?[0-9]+(?:\.[0-9]+)?$'
    ),
    'portfolio_command_coverage_complete', NOT EXISTS (
      SELECT 1 FROM app.portfolio_commands
      WHERE owner_id = p_owner_id AND status = 'applied'
        AND operation IN ('sell','sell_all')
        AND (applied_at AT TIME ZONE 'America/New_York')::date = p_market_date
        AND coalesce(result->>'realized_pnl', '') !~ '^-?[0-9]+(?:\.[0-9]+)?$'
    ),
    'consecutive_completed_losses', (
      SELECT count(*) FROM (
        SELECT direction_success,
               sum(CASE WHEN direction_success THEN 1 ELSE 0 END)
                 OVER (ORDER BY graded_at DESC, id DESC ROWS UNBOUNDED PRECEDING) AS prior_successes
        FROM app.suggestion_grades
        WHERE owner_id = p_owner_id AND coverage_status = 'complete'
          AND direction_success IS NOT NULL
        ORDER BY graded_at DESC, id DESC LIMIT 50
      ) recent WHERE NOT direction_success AND prior_successes = 0
    ),
    'owner_plans', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, ticker, bucket, amount::text AS amount, cadence, next_due_on, active,
             updated_at
      FROM app.owner_investment_plans WHERE owner_id = p_owner_id AND active
      ORDER BY next_due_on, ticker LIMIT 20
    ) row_value),
    'plans', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, ticker, bucket, amount::text AS amount, cadence, next_due_on, active,
             updated_at
      FROM app.owner_investment_plans WHERE owner_id = p_owner_id AND active
      ORDER BY next_due_on, ticker LIMIT 20
    ) row_value),
    'recent_suggestions', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, date, ticker, action, bucket, confidence, score,
             stop::text AS stop, target::text AS target,
             invalidation_price::text AS invalidation_price,
             valid_until, evidence_as_of
      FROM app.suggestions WHERE owner_id = p_owner_id
      ORDER BY ts DESC, id DESC LIMIT 100
    ) row_value),
    'observations', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, ticker, obs_date, event_type, summary, price_reaction, confidence, source
      FROM app.stock_observations WHERE owner_id = p_owner_id
      ORDER BY obs_date DESC, id DESC LIMIT 100
    ) row_value),
    'lessons', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, entry_date, category, content FROM app.lessons
      WHERE owner_id = p_owner_id ORDER BY entry_date DESC, id DESC LIMIT 40
    ) row_value),
    'radar', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT ticker, added, last_seen, days_relevant, reason, bucket_guess, promoted, promoted_on
      FROM app.radar WHERE owner_id = p_owner_id ORDER BY last_seen DESC NULLS LAST, ticker LIMIT 20
    ) row_value),
    'recent_grades', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT suggestion_id, horizon_days, coverage_status, excess_return_pct::text AS excess_return_pct,
             direction_success
      FROM app.suggestion_grades WHERE owner_id = p_owner_id
      ORDER BY graded_at DESC, id DESC LIMIT 150
    ) row_value),
    'dry_powder', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT month, growth_available::text AS growth_available,
             spec_available::text AS spec_available, rolled_months
      FROM app.dry_powder WHERE owner_id = p_owner_id ORDER BY month DESC LIMIT 12
    ) row_value),
    'paper_watches', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, ticker, created, entry_ref_price::text AS entry_ref_price,
             target_price::text AS target_price, hypothetical_amount::text AS hypothetical_amount,
             thesis, horizon, agent_view_at_open, agent_score_at_open
      FROM app.paper_watches WHERE owner_id = p_owner_id AND status = 'active'
      ORDER BY created DESC, id DESC LIMIT 50
    ) row_value),
    'evidence', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT evidence_id, category, source_identifier, reference_identifier, observed_at,
             retrieved_at, revalidated_at, content_hash, claims, status
      FROM app.run_evidence WHERE owner_id = p_owner_id AND run_id = p_run_id
      ORDER BY retrieved_at DESC, evidence_id LIMIT 100
    ) row_value),
    'quotes', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT ticker, price::text AS price, previous_close::text AS previous_close,
             provider, source_timestamp, retrieved_at, session, adjustment_status,
             status, conflict_basis_points
      FROM app.market_quote_cache WHERE owner_id = p_owner_id ORDER BY ticker LIMIT 60
    ) row_value)
  )
$$;

CREATE OR REPLACE FUNCTION machine.agent_read_bounded_context(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  owner_uuid UUID;
  run_uuid UUID;
  request_uuid UUID;
  response JSONB;
  handshake_run BOOLEAN;
  challenge_value TEXT;
  phase_value TEXT;
  market_day DATE;
  started_value TIMESTAMPTZ;
BEGIN
  claim := machine.agent_claim_operation(p_request, 'read_bounded_context');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  IF NOT (
    app.jsonb_has_exact_keys(p_request->'payload', ARRAY[]::text[])
    OR (
      app.jsonb_has_exact_keys(p_request->'payload', ARRAY['research'])
      AND jsonb_typeof(p_request->'payload'->'research') = 'object'
      AND octet_length((p_request->'payload'->'research')::text) <= 12000
    )
  ) THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;
  SELECT kind, market_date, started_at INTO phase_value, market_day, started_value
  FROM app.analysis_runs WHERE owner_id = owner_uuid AND id = run_uuid;
  IF (claim->>'dry_run')::boolean THEN
    phase_value := 'on-demand';
    market_day := (clock_timestamp() AT TIME ZONE 'America/New_York')::date;
    started_value := clock_timestamp();
  END IF;

  SELECT encode(handshake_challenge, 'hex') INTO challenge_value
  FROM app.scheduled_run_slots
  WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake';
  handshake_run := FOUND;
  IF handshake_run THEN
    response := jsonb_build_object(
      'run_id', run_uuid, 'handshake', true, 'contract_version', 2,
      'challenge', challenge_value, 'phase', phase_value, 'market_date', market_day,
      'holdings', '[]'::jsonb, 'plans', '[]'::jsonb,
      'recent_suggestions', '[]'::jsonb, 'radar', '[]'::jsonb,
      'evidence', '[]'::jsonb, 'quotes', '[]'::jsonb,
      'allowed_source_hosts', jsonb_build_array(
        'query1.finance.yahoo.com', 'www.sec.gov', 'finnhub.io'
      )
    );
    IF (claim->>'dry_run')::boolean THEN RETURN response || jsonb_build_object('dry_run', true); END IF;
    RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
  END IF;

  response := machine.agent_build_owner_context(
    owner_uuid, run_uuid, phase_value, market_day, started_value
  );
  IF (claim->>'dry_run')::boolean THEN RETURN response || jsonb_build_object('dry_run', true); END IF;
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_submit_analysis(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  payload JSONB := p_request->'payload';
  owner_uuid UUID;
  run_uuid UUID;
  run_started TIMESTAMPTZ;
  phase_value TEXT;
  market_day DATE;
  policy_value JSONB;
  context_value JSONB;
  corporate_value JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(payload, ARRAY[
      'phase','market_date','title','suggestion_only','provider','model','analyst',
      'checker','dimensions','evidence_packets','evidence_refs','prior_suggestion_ids','candidates'
    ]) OR payload->>'suggestion_only' <> 'true'
       OR octet_length(payload::text) > 65536
       OR jsonb_typeof(payload->'evidence_packets') <> 'array'
       OR jsonb_array_length(payload->'evidence_packets') NOT BETWEEN 1 AND 4
       OR jsonb_typeof(payload->'evidence_refs') <> 'array'
       OR jsonb_array_length(payload->'evidence_refs') > 100
       OR jsonb_typeof(payload->'candidates') <> 'array'
       OR jsonb_array_length(payload->'candidates') > 20 THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  claim := machine.agent_claim_operation(p_request, 'submit_analysis');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  IF claim->>'connection_status' <> 'active' THEN
    RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
  END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  IF NOT (claim->>'dry_run')::boolean AND EXISTS (
    SELECT 1 FROM app.scheduled_run_slots
    WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake'
  ) THEN
    RAISE EXCEPTION 'handshake operation is not permitted' USING ERRCODE = '42501';
  END IF;
  IF (claim->>'dry_run')::boolean THEN
    phase_value := 'on-demand';
    market_day := (clock_timestamp() AT TIME ZONE 'America/New_York')::date;
    run_started := clock_timestamp();
  ELSE
    SELECT kind, market_date, started_at INTO phase_value, market_day, run_started
    FROM app.analysis_runs
    WHERE owner_id = owner_uuid AND id = run_uuid AND status = 'running';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
    END IF;
  END IF;
  SELECT config || jsonb_build_object('version', version) INTO policy_value
  FROM public.market_policy_config WHERE active ORDER BY version DESC LIMIT 1;
  IF policy_value IS NULL THEN
    RAISE EXCEPTION 'active policy unavailable' USING ERRCODE = '22023';
  END IF;
  context_value := machine.agent_build_owner_context(
    owner_uuid, run_uuid, phase_value, market_day, run_started
  );
  SELECT coalesce(jsonb_agg(jsonb_build_object('ticker', ticker, 'state', state) ORDER BY ticker), '[]'::jsonb)
  INTO corporate_value FROM app.corporate_action_states WHERE owner_id = owner_uuid;
  RETURN jsonb_build_object(
    'status', CASE WHEN (claim->>'dry_run')::boolean THEN 'dry_run' ELSE 'claimed' END,
    'lease_token', claim->>'lease_token',
    'run_id', run_uuid,
    'phase', phase_value,
    'market_date', market_day,
    'started_at', run_started,
    'policy', policy_value,
    'context', context_value,
    'corporate_actions', corporate_value
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_apply_analysis(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_row app.agent_connections%ROWTYPE;
  request_row app.market_gateway_requests%ROWTYPE;
  run_row app.analysis_runs%ROWTYPE;
  decision JSONB := p_request->'decision';
  submission JSONB;
  publication JSONB;
  item JSONB;
  evaluation_row app.decision_evaluations%ROWTYPE;
  owner_uuid UUID;
  request_uuid UUID;
  run_uuid UUID;
  lease_uuid UUID;
  publication_uuid UUID := extensions.gen_random_uuid();
  delivery_lease UUID;
  chat_value BIGINT;
  notify_enabled BOOLEAN := false;
  final_publication_status TEXT;
  evaluation_count INT := 0;
  suggestion_count INT := 0;
  updated_rows INT;
  run_started TIMESTAMPTZ;
  response JSONB;
  presented_digest BYTEA;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY[
      'connection_id','secret_digest','request_id','run_id','lease_token','decision'
    ]) OR jsonb_typeof(decision) <> 'object'
       OR NOT app.jsonb_has_exact_keys(decision, ARRAY[
         'provider_submission','evidence','search_receipts','policy_quotes','policy_version',
         'evaluations','suggestions','holding_state','publication'
       ]) OR octet_length(decision::text) > 262144 THEN
    RAISE EXCEPTION 'invalid analysis transaction' USING ERRCODE = '22023';
  END IF;
  BEGIN
    request_uuid := (p_request->>'request_id')::uuid;
    run_uuid := (p_request->>'run_id')::uuid;
    lease_uuid := (p_request->>'lease_token')::uuid;
    presented_digest := decode(p_request->>'secret_digest', 'hex');
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid analysis transaction' USING ERRCODE = '22023';
  END;
  SELECT * INTO connection_row FROM app.agent_connections
  WHERE public_id = (p_request->>'connection_id')::uuid;
  IF NOT FOUND OR connection_row.status <> 'active'
     OR NOT machine.agent_constant_time_equal(connection_row.inbound_token_digest, presented_digest) THEN
    RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
  END IF;
  owner_uuid := connection_row.owner_id;
  SELECT * INTO request_row FROM app.market_gateway_requests
  WHERE owner_id = owner_uuid AND request_id = request_uuid FOR UPDATE;
  IF NOT FOUND OR request_row.connection_id <> connection_row.id
     OR request_row.run_id <> run_uuid OR request_row.operation <> 'submit_analysis'
     OR request_row.status <> 'claimed' OR request_row.lease_token <> lease_uuid THEN
    RAISE EXCEPTION 'analysis request unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO run_row FROM app.analysis_runs
  WHERE owner_id = owner_uuid AND id = run_uuid AND connection_id = connection_row.id
    AND status = 'running' FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'analysis run unavailable' USING ERRCODE = '42501';
  END IF;
  run_started := run_row.started_at;
  submission := decision->'provider_submission';
  publication := decision->'publication';
  IF jsonb_typeof(submission) <> 'object'
     OR submission->>'phase' IS DISTINCT FROM run_row.kind
     OR submission->>'market_date' IS DISTINCT FROM run_row.market_date::text
     OR submission->>'suggestion_only' <> 'true'
     OR jsonb_typeof(decision->'evidence') <> 'array'
     OR jsonb_array_length(decision->'evidence') > 100
     OR jsonb_typeof(decision->'search_receipts') <> 'array'
     OR jsonb_array_length(decision->'search_receipts') > 4
     OR jsonb_typeof(decision->'policy_quotes') <> 'array'
     OR jsonb_array_length(decision->'policy_quotes') > 60
     OR jsonb_typeof(decision->'evaluations') <> 'array'
     OR jsonb_array_length(decision->'evaluations') > 20
     OR jsonb_typeof(decision->'suggestions') <> 'array'
     OR jsonb_array_length(decision->'suggestions') > 20
     OR jsonb_typeof(decision->'holding_state') <> 'array'
     OR jsonb_array_length(decision->'holding_state') > 100
     OR jsonb_typeof(publication) <> 'object' THEN
    RAISE EXCEPTION 'invalid analysis transaction' USING ERRCODE = '22023';
  END IF;
  PERFORM 1 FROM public.market_policy_config
  WHERE active AND version = (decision->>'policy_version')::int;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'active policy unavailable' USING ERRCODE = '22023';
  END IF;

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'evidence') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'evidence_id','source_run_id','category','source_identifier','reference_identifier',
        'observed_at','retrieved_at','revalidated_at','content_hash','claims','status'
      ]) OR item->>'evidence_id' !~ '^[a-z0-9][a-z0-9._:-]{0,99}$'
         OR item->>'category' NOT IN (
           'market_snapshot','source_search','filing','fundamentals','news','technicals',
           'corporate_action','issuer','exchange'
         ) OR item->>'content_hash' !~ '^[0-9a-f]{64}$'
         OR jsonb_typeof(item->'claims') <> 'array'
         OR jsonb_array_length(item->'claims') > 10
         OR item->>'status' NOT IN (
           'fresh','stale','conflicting','missing','no_new_material_evidence',
           'suspected','needs_review','clear'
         ) THEN
      RAISE EXCEPTION 'invalid evidence' USING ERRCODE = '22023';
    END IF;
    IF (item->>'retrieved_at')::timestamptz < run_started
       OR (item->>'retrieved_at')::timestamptz > clock_timestamp() + interval '5 minutes'
       OR (item->>'revalidated_at') IS NOT NULL
          AND (item->>'revalidated_at')::timestamptz < run_started THEN
      RAISE EXCEPTION 'evidence_stale' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.run_evidence(
      owner_id, run_id, evidence_id, source_run_id, category, source_identifier,
      reference_identifier, observed_at, retrieved_at, revalidated_at,
      content_hash, claims, status
    ) VALUES (
      owner_uuid, run_uuid, item->>'evidence_id',
      CASE WHEN item->'source_run_id' = 'null'::jsonb THEN NULL ELSE (item->>'source_run_id')::uuid END,
      item->>'category', item->>'source_identifier', item->>'reference_identifier',
      (item->>'observed_at')::timestamptz, (item->>'retrieved_at')::timestamptz,
      (item->>'revalidated_at')::timestamptz, item->>'content_hash', item->'claims', item->>'status'
    );
  END LOOP;

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'search_receipts') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'searched_at','categories','sources','result_status','content_hash'
      ]) OR jsonb_typeof(item->'categories') <> 'array'
         OR jsonb_typeof(item->'sources') <> 'array'
         OR item->>'result_status' NOT IN (
           'material_evidence_found','no_new_material_evidence','source_unavailable'
         ) OR item->>'content_hash' !~ '^[0-9a-f]{64}$'
         OR (item->>'searched_at')::timestamptz < run_started
         OR (item->>'searched_at')::timestamptz > clock_timestamp() + interval '5 minutes' THEN
      RAISE EXCEPTION 'invalid source receipt' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.source_search_receipts(
      owner_id, run_id, searched_at, categories, sources, result_status, content_hash
    ) VALUES (
      owner_uuid, run_uuid, (item->>'searched_at')::timestamptz,
      ARRAY(SELECT jsonb_array_elements_text(item->'categories')),
      item->'sources', item->>'result_status', item->>'content_hash'
    );
  END LOOP;

  IF NOT EXISTS (
    SELECT 1 FROM app.run_evidence WHERE owner_id = owner_uuid AND run_id = run_uuid
      AND category = 'market_snapshot' AND status = 'fresh' AND retrieved_at >= run_started
  ) OR NOT EXISTS (
    SELECT 1 FROM app.source_search_receipts WHERE owner_id = owner_uuid AND run_id = run_uuid
      AND searched_at >= run_started AND result_status <> 'source_unavailable'
  ) OR NOT EXISTS (
    SELECT 1 FROM app.run_evidence WHERE owner_id = owner_uuid AND run_id = run_uuid
      AND category = 'source_search' AND status IN ('fresh','no_new_material_evidence')
      AND retrieved_at >= run_started
  ) THEN
    RAISE EXCEPTION 'evidence_missing' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(submission->'evidence_refs') reference
    LEFT JOIN app.run_evidence evidence
      ON evidence.owner_id = owner_uuid AND evidence.run_id = run_uuid
     AND evidence.evidence_id = reference->>'evidence_id'
     AND evidence.content_hash = reference->>'content_hash'
    WHERE reference->>'run_id' IS DISTINCT FROM run_uuid::text OR evidence.id IS NULL
  ) THEN
    RAISE EXCEPTION 'evidence_conflicting' USING ERRCODE = '22023';
  END IF;

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'policy_quotes') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'ticker','price','previous_close','as_of','market_state','source','retrieved_at'
      ]) OR item->>'ticker' !~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'
         OR item->>'price' !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
         OR item->>'market_state' NOT IN ('PRE','REGULAR','POST','CLOSED')
         OR item->>'source' <> 'yahoo-chart'
         OR (item->>'as_of')::timestamptz > (item->>'retrieved_at')::timestamptz + interval '5 minutes'
         OR (item->>'retrieved_at')::timestamptz < run_started THEN
      RAISE EXCEPTION 'invalid server quote' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.market_quote_cache(
      owner_id, ticker, price, previous_close, provider, source_timestamp,
      retrieved_at, session, adjustment_status, status, content_digest, conflict_basis_points
    ) VALUES (
      owner_uuid, item->>'ticker', (item->>'price')::numeric,
      CASE WHEN item->'previous_close' = 'null'::jsonb THEN NULL ELSE (item->>'previous_close')::numeric END,
      item->>'source', (item->>'as_of')::timestamptz, (item->>'retrieved_at')::timestamptz,
      item->>'market_state', 'raw',
      CASE
        WHEN run_row.kind IN ('intraday','on-demand')
             AND item->>'market_state' = 'REGULAR'
             AND (item->>'as_of')::timestamptz >= (item->>'retrieved_at')::timestamptz - interval '20 minutes'
          THEN 'fresh'
        WHEN run_row.kind IN ('pre-market','post-market')
             AND (item->>'as_of')::timestamptz >= (item->>'retrieved_at')::timestamptz - interval '18 hours'
          THEN 'fresh'
        ELSE 'stale'
      END,
      encode(extensions.digest(convert_to(item::text, 'UTF8'), 'sha256'), 'hex'), NULL
    ) ON CONFLICT (owner_id, ticker) DO UPDATE SET
      price = excluded.price, previous_close = excluded.previous_close,
      provider = excluded.provider, source_timestamp = excluded.source_timestamp,
      retrieved_at = excluded.retrieved_at, session = excluded.session,
      adjustment_status = excluded.adjustment_status, status = excluded.status,
      content_digest = excluded.content_digest, conflict_basis_points = NULL,
      updated_at = clock_timestamp();
  END LOOP;

  INSERT INTO app.agent_analysis_submissions(
    owner_id, run_id, request_id, provider, model, phase, market_date,
    payload, payload_digest, status
  ) VALUES (
    owner_uuid, run_uuid, request_uuid, submission->>'provider', submission->>'model',
    submission->>'phase', (submission->>'market_date')::date, submission,
    encode(extensions.digest(convert_to(submission::text, 'UTF8'), 'sha256'), 'hex'), 'accepted'
  );

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'evaluations') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'id','candidate_id','input_digest','raw_action','final_action','policy_status',
        'reason_codes','explanations','normalized','evidence','analyst','checker'
      ]) OR item->>'policy_status' NOT IN ('approved','downgraded','vetoed')
         OR item->>'input_digest' !~ '^[0-9a-f]{64}$' THEN
      RAISE EXCEPTION 'invalid evaluation' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.decision_evaluations(
      owner_id, id, request_id, run_id, candidate_id, policy_version, input_digest,
      raw_action, final_action, policy_status, reason_codes, explanations,
      normalized, evidence, analyst, checker
    ) VALUES (
      owner_uuid, (item->>'id')::uuid, request_uuid, run_uuid,
      (item->>'candidate_id')::uuid, (decision->>'policy_version')::int,
      item->>'input_digest', item->>'raw_action', NULLIF(item->>'final_action',''),
      item->>'policy_status', item->'reason_codes', item->'explanations',
      item->'normalized', item->'evidence', item->'analyst', item->'checker'
    );
    evaluation_count := evaluation_count + 1;
  END LOOP;

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'suggestions') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'evaluation_id','candidate_id','date','ticker','action','decision_mode','bucket','depth',
        'entry_zone_low','entry_zone_high','valid_until','stop','target','confidence','bull','bear',
        'decisive_factor','risk_verdict','reason','score','price_at_suggestion','evidence_as_of',
        'invalidation_price'
      ]) THEN
      RAISE EXCEPTION 'invalid suggestion' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO evaluation_row FROM app.decision_evaluations
    WHERE owner_id = owner_uuid AND id = (item->>'evaluation_id')::uuid
      AND request_id = request_uuid AND run_id = run_uuid;
    IF NOT FOUND OR evaluation_row.candidate_id <> (item->>'candidate_id')::uuid
       OR evaluation_row.final_action IS DISTINCT FROM item->>'action'
       OR evaluation_row.policy_status NOT IN ('approved','downgraded')
       OR evaluation_row.normalized->>'ticker' IS DISTINCT FROM item->>'ticker' THEN
      RAISE EXCEPTION 'suggestion evaluation mismatch' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.suggestions(
      owner_id, date, ticker, action, decision_mode, bucket, depth,
      entry_zone_low, entry_zone_high, valid_until, stop, target, confidence,
      bull, bear, decisive_factor, risk_verdict, reason, score, price_at_suggestion,
      run_id, evidence_as_of, invalidation_price, evaluation_id, decision_source
    ) VALUES (
      owner_uuid, (item->>'date')::date, item->>'ticker', item->>'action',
      item->>'decision_mode', item->>'bucket', item->>'depth',
      (item->>'entry_zone_low')::numeric, (item->>'entry_zone_high')::numeric,
      (item->>'valid_until')::date, (item->>'stop')::numeric, (item->>'target')::numeric,
      item->>'confidence', item->>'bull', item->>'bear', item->>'decisive_factor',
      item->>'risk_verdict', item->>'reason', (item->>'score')::int,
      (item->>'price_at_suggestion')::numeric, run_uuid,
      (item->>'evidence_as_of')::timestamptz, (item->>'invalidation_price')::numeric,
      evaluation_row.id, 'gateway'
    );
    suggestion_count := suggestion_count + 1;
  END LOOP;

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'holding_state') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'ticker','high_water_price','stop_alert_active','stop_near_alert_active',
        'target_near_alert_active','target_alert_active'
      ]) THEN
      RAISE EXCEPTION 'invalid holding state' USING ERRCODE = '22023';
    END IF;
    UPDATE app.holdings SET
      high_water_price = greatest(coalesce(high_water_price, 0), (item->>'high_water_price')::numeric),
      stop_alert_active = (item->>'stop_alert_active')::boolean,
      stop_near_alert_active = (item->>'stop_near_alert_active')::boolean,
      target_near_alert_active = (item->>'target_near_alert_active')::boolean,
      target_alert_active = (item->>'target_alert_active')::boolean
    WHERE owner_id = owner_uuid AND ticker = item->>'ticker';
    GET DIAGNOSTICS updated_rows = ROW_COUNT;
    IF updated_rows <> 1 THEN
      RAISE EXCEPTION 'holding state target unavailable' USING ERRCODE = '22023';
    END IF;
  END LOOP;

  IF NOT app.jsonb_has_exact_keys(publication, ARRAY[
      'market_date','phase','kind','template_version','rendered_body','rendered_parts','rendered_hash','status'
    ]) OR publication->>'market_date' IS DISTINCT FROM run_row.market_date::text
       OR publication->>'phase' IS DISTINCT FROM run_row.kind
       OR publication->>'status' NOT IN ('ready','suppressed')
       OR publication->>'rendered_hash' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(publication->'rendered_parts') <> 'array'
       OR jsonb_array_length(publication->'rendered_parts') > 4
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(publication->'rendered_parts') part
         WHERE jsonb_typeof(part) <> 'string' OR char_length(part #>> '{}') NOT BETWEEN 1 AND 3500
       )
       OR (publication->>'status' = 'ready' AND jsonb_array_length(publication->'rendered_parts') = 0)
       OR (publication->>'status' = 'suppressed' AND jsonb_array_length(publication->'rendered_parts') <> 0)
       OR publication->>'rendered_hash' IS DISTINCT FROM encode(
         extensions.digest(convert_to(publication->>'rendered_body', 'UTF8'), 'sha256'), 'hex'
       )
       OR publication->>'rendered_body' IS DISTINCT FROM coalesce((
         SELECT string_agg(part.value, E'\n\n' ORDER BY part.ordinality)
         FROM jsonb_array_elements_text(publication->'rendered_parts')
           WITH ORDINALITY AS part(value, ordinality)
       ), '')
       OR char_length(publication->>'rendered_body') > 14000 THEN
    RAISE EXCEPTION 'invalid publication' USING ERRCODE = '22023';
  END IF;
  SELECT CASE run_row.kind
      WHEN 'pre-market' THEN pre_market_enabled
      WHEN 'intraday' THEN intraday_enabled
      WHEN 'post-market' THEN post_market_enabled
      ELSE false
    END INTO notify_enabled
  FROM app.notification_preferences WHERE owner_id = owner_uuid;
  SELECT telegram_chat_id INTO chat_value FROM app.telegram_links
  WHERE owner_id = owner_uuid AND status = 'active';
  final_publication_status := CASE
    WHEN publication->>'status' = 'ready' AND coalesce(notify_enabled, false) AND chat_value IS NOT NULL
      THEN 'sending'
    ELSE 'suppressed'
  END;
  IF final_publication_status = 'sending' THEN delivery_lease := extensions.gen_random_uuid(); END IF;
  INSERT INTO app.market_publications(
    owner_id, id, idempotency_key, run_id, market_date, phase, kind,
    template_version, rendered_body, rendered_parts, rendered_hash, status, lease_token,
    sending_started_at, attempt_count
  ) VALUES (
    owner_uuid, publication_uuid, request_uuid, run_uuid, run_row.market_date,
    run_row.kind, publication->>'kind', (publication->>'template_version')::int,
    publication->>'rendered_body', publication->'rendered_parts', publication->>'rendered_hash', final_publication_status,
    delivery_lease, CASE WHEN delivery_lease IS NULL THEN NULL ELSE clock_timestamp() END,
    CASE WHEN delivery_lease IS NULL THEN 0 ELSE 1 END
  );
  UPDATE app.analysis_runs SET
    provider = submission->>'provider', model = submission->>'model',
    data_as_of = (SELECT max(retrieved_at) FROM app.run_evidence
                  WHERE owner_id = owner_uuid AND run_id = run_uuid),
    source_status = jsonb_build_object(
      'analysis', 'accepted', 'evidence', 'server_verified',
      'publication', final_publication_status
    )
  WHERE owner_id = owner_uuid AND id = run_uuid;
  response := jsonb_build_object(
    'status', 'accepted', 'run_id', run_uuid, 'publication_id', publication_uuid,
    'publication_status', final_publication_status,
    'evaluation_count', evaluation_count, 'suggestion_count', suggestion_count,
    'telegram_message_ids', '[]'::jsonb
  );
  IF run_row.kind = 'on-demand' THEN
    response := response || jsonb_build_object('preview', publication->>'rendered_body');
  END IF;
  IF final_publication_status = 'suppressed' THEN
    RETURN jsonb_build_object(
      'delivery_required', false,
      'response', machine.agent_complete_operation(owner_uuid, request_uuid, response)
    );
  END IF;
  RETURN jsonb_build_object(
    'delivery_required', true,
    'chat_id', chat_value::text,
    'delivery_lease', delivery_lease,
    'response', response
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_finish_analysis_delivery(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_row app.agent_connections%ROWTYPE;
  request_row app.market_gateway_requests%ROWTYPE;
  publication_row app.market_publications%ROWTYPE;
  owner_uuid UUID;
  request_uuid UUID;
  run_uuid UUID;
  delivery_lease UUID;
  status_value TEXT;
  message_ids JSONB;
  response JSONB;
  presented_digest BYTEA;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY[
      'connection_id','secret_digest','request_id','run_id','delivery_lease','status','message_ids'
    ]) OR p_request->>'status' NOT IN ('delivered','delivery_failed','delivery_unknown')
       OR jsonb_typeof(p_request->'message_ids') <> 'array'
       OR jsonb_array_length(p_request->'message_ids') > 4 THEN
    RAISE EXCEPTION 'invalid delivery completion' USING ERRCODE = '22023';
  END IF;
  BEGIN
    request_uuid := (p_request->>'request_id')::uuid;
    run_uuid := (p_request->>'run_id')::uuid;
    delivery_lease := (p_request->>'delivery_lease')::uuid;
    presented_digest := decode(p_request->>'secret_digest', 'hex');
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid delivery completion' USING ERRCODE = '22023';
  END;
  status_value := p_request->>'status';
  message_ids := p_request->'message_ids';
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(message_ids) item
    WHERE jsonb_typeof(item) <> 'number' OR (item::text)::bigint <= 0
  ) OR (status_value = 'delivered' AND jsonb_array_length(message_ids) = 0)
     OR (status_value = 'delivery_failed' AND jsonb_array_length(message_ids) <> 0) THEN
    RAISE EXCEPTION 'invalid delivery completion' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO connection_row FROM app.agent_connections
  WHERE public_id = (p_request->>'connection_id')::uuid;
  IF NOT FOUND OR NOT machine.agent_constant_time_equal(connection_row.inbound_token_digest, presented_digest) THEN
    RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
  END IF;
  owner_uuid := connection_row.owner_id;
  SELECT * INTO request_row FROM app.market_gateway_requests
  WHERE owner_id = owner_uuid AND request_id = request_uuid FOR UPDATE;
  IF NOT FOUND OR request_row.connection_id <> connection_row.id
     OR request_row.run_id <> run_uuid OR request_row.operation <> 'submit_analysis'
     OR request_row.status <> 'claimed' THEN
    RAISE EXCEPTION 'analysis request unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO publication_row FROM app.market_publications
  WHERE owner_id = owner_uuid AND idempotency_key = request_uuid AND run_id = run_uuid FOR UPDATE;
  IF NOT FOUND OR publication_row.status <> 'sending' OR publication_row.lease_token <> delivery_lease THEN
    RAISE EXCEPTION 'publication lease unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.market_publications SET
    status = status_value,
    telegram_message_ids = message_ids,
    lease_token = NULL,
    delivered_at = CASE WHEN status_value = 'delivered' THEN clock_timestamp() ELSE NULL END,
    error = CASE status_value
      WHEN 'delivery_failed' THEN 'TELEGRAM_REJECTED'
      WHEN 'delivery_unknown' THEN 'TELEGRAM_OUTCOME_UNKNOWN'
      ELSE NULL
    END,
    updated_at = clock_timestamp()
  WHERE owner_id = owner_uuid AND id = publication_row.id;
  UPDATE app.analysis_runs SET
    telegram_message_ids = message_ids,
    source_status = source_status || jsonb_build_object('publication', status_value)
  WHERE owner_id = owner_uuid AND id = run_uuid;
  response := jsonb_build_object(
    'status', 'accepted', 'run_id', run_uuid,
    'publication_id', publication_row.id, 'publication_status', status_value,
    'telegram_message_ids', message_ids
  );
  RETURN jsonb_build_object(
    'response', machine.agent_complete_operation(owner_uuid, request_uuid, response)
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_record_permitted_artifacts(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  payload JSONB := p_request->'payload';
  owner_uuid UUID;
  run_uuid UUID;
  request_uuid UUID;
  item JSONB;
  item_kind TEXT;
  counts JSONB := '{}'::jsonb;
  response JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(payload, ARRAY['mutations'])
     OR jsonb_typeof(payload->'mutations') <> 'array'
     OR jsonb_array_length(payload->'mutations') NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  claim := machine.agent_claim_operation(p_request, 'record_permitted_artifacts');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;
  IF EXISTS (
    SELECT 1 FROM app.scheduled_run_slots
    WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake'
  ) THEN
    RAISE EXCEPTION 'handshake operation is not permitted' USING ERRCODE = '42501';
  END IF;

  FOR item IN SELECT value FROM jsonb_array_elements(payload->'mutations') LOOP
    item_kind := item->>'kind';
    IF item_kind NOT IN (
      'observation','snapshot','lesson','radar_upsert','radar_delete',
      'paper_watch_create','paper_watch_close'
    ) THEN
      RAISE EXCEPTION 'artifact kind is not permitted' USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(item) <> 'object' THEN
      RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
    END IF;
    counts := jsonb_set(
      counts, ARRAY[item_kind],
      to_jsonb(coalesce((counts->>item_kind)::int, 0) + 1), true
    );
    IF (claim->>'dry_run')::boolean THEN CONTINUE; END IF;

    CASE item_kind
      WHEN 'observation' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','ticker','obs_date','event_type','summary','price_reaction','confidence','source']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        INSERT INTO app.stock_observations(
          owner_id, ticker, obs_date, event_type, summary, price_reaction,
          confidence, source, run_id
        ) VALUES (
          owner_uuid, upper(item->>'ticker'), (item->>'obs_date')::date,
          item->>'event_type', item->>'summary', item->>'price_reaction',
          item->>'confidence', item->>'source', run_uuid
        );
      WHEN 'snapshot' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','snap_date','ticker','close','day_move_pct','rsi14','sma50','sma200','macd_hist']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        INSERT INTO app.daily_snapshots(
          owner_id, snap_date, ticker, close, day_move_pct, rsi14, sma50,
          sma200, macd_hist, run_id
        ) VALUES (
          owner_uuid, (item->>'snap_date')::date, upper(item->>'ticker'),
          (item->>'close')::numeric, (item->>'day_move_pct')::numeric,
          (item->>'rsi14')::numeric, (item->>'sma50')::numeric,
          (item->>'sma200')::numeric, (item->>'macd_hist')::numeric, run_uuid
        ) ON CONFLICT (owner_id, snap_date, ticker) DO UPDATE
          SET close = excluded.close, day_move_pct = excluded.day_move_pct,
              rsi14 = excluded.rsi14, sma50 = excluded.sma50,
              sma200 = excluded.sma200, macd_hist = excluded.macd_hist,
              run_id = excluded.run_id;
      WHEN 'lesson' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','entry_date','category','content']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        INSERT INTO app.lessons(owner_id, entry_date, category, content, run_id)
        VALUES (owner_uuid, (item->>'entry_date')::date, item->>'category', item->>'content', run_uuid);
      WHEN 'radar_upsert' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','ticker','added','last_seen','days_relevant','reason','bucket_guess','promoted','promoted_on']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        INSERT INTO app.radar(
          owner_id, ticker, added, last_seen, days_relevant, reason,
          bucket_guess, promoted, promoted_on, updated_run_id
        ) VALUES (
          owner_uuid, upper(item->>'ticker'), (item->>'added')::date,
          (item->>'last_seen')::date, (item->>'days_relevant')::int,
          item->>'reason', item->>'bucket_guess', (item->>'promoted')::boolean,
          (item->>'promoted_on')::date, run_uuid
        ) ON CONFLICT (owner_id, ticker) DO UPDATE SET
          last_seen = excluded.last_seen, days_relevant = excluded.days_relevant,
          reason = excluded.reason, bucket_guess = excluded.bucket_guess,
          promoted = excluded.promoted, promoted_on = excluded.promoted_on,
          updated_run_id = excluded.updated_run_id;
      WHEN 'radar_delete' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','ticker']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        DELETE FROM app.radar WHERE owner_id = owner_uuid AND ticker = upper(item->>'ticker');
      WHEN 'paper_watch_create' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','ticker','entry_ref_price','target_price','hypothetical_amount','thesis','horizon']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        INSERT INTO app.paper_watches(
          owner_id, ticker, created, entry_ref_price, target_price,
          hypothetical_amount, thesis, horizon, opened_run_id
        ) VALUES (
          owner_uuid, upper(item->>'ticker'), current_date,
          (item->>'entry_ref_price')::numeric, (item->>'target_price')::numeric,
          (item->>'hypothetical_amount')::numeric, item->>'thesis', item->>'horizon', run_uuid
        );
      WHEN 'paper_watch_close' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','watch_id','ticker']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        UPDATE app.paper_watches SET status = 'closed', closed_date = current_date,
          closed_run_id = run_uuid
        WHERE owner_id = owner_uuid AND id = (item->>'watch_id')::bigint
          AND ticker = upper(item->>'ticker') AND status = 'active';
        IF NOT FOUND THEN RAISE EXCEPTION 'paper watch unavailable' USING ERRCODE = '22023'; END IF;
    END CASE;
  END LOOP;
  response := jsonb_build_object('status', CASE WHEN (claim->>'dry_run')::boolean THEN 'dry_run' ELSE 'recorded' END, 'counts', counts);
  IF (claim->>'dry_run')::boolean THEN RETURN response || jsonb_build_object('writes', 0); END IF;
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_grade_due_decisions(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  owner_uuid UUID;
  run_uuid UUID;
  request_uuid UUID;
  result_limit INT;
  response JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request->'payload', ARRAY['limit'])
     OR p_request->'payload'->>'limit' !~ '^\d{1,2}$' THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  result_limit := (p_request->'payload'->>'limit')::int;
  IF result_limit NOT BETWEEN 1 AND 50 THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  claim := machine.agent_claim_operation(p_request, 'grade_due_decisions');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;
  IF EXISTS (
    SELECT 1 FROM app.scheduled_run_slots
    WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake'
  ) THEN
    RAISE EXCEPTION 'handshake operation is not permitted' USING ERRCODE = '42501';
  END IF;
  SELECT jsonb_build_object(
    'status', CASE WHEN (claim->>'dry_run')::boolean THEN 'dry_run' ELSE 'due_returned' END,
    'run_id', run_uuid,
    'due', coalesce(jsonb_agg(to_jsonb(due_row)), '[]'::jsonb)
  ) INTO response
  FROM (
    SELECT suggestion.id AS suggestion_id, suggestion.ticker, suggestion.action,
           suggestion.date, suggestion.valid_until
    FROM app.suggestions AS suggestion
    WHERE suggestion.owner_id = owner_uuid
      AND suggestion.date < current_date
      AND NOT EXISTS (
        SELECT 1 FROM app.suggestion_grades AS grade
        WHERE grade.owner_id = owner_uuid AND grade.suggestion_id = suggestion.id
      )
    ORDER BY suggestion.date, suggestion.id LIMIT result_limit
  ) due_row;
  IF (claim->>'dry_run')::boolean THEN RETURN response; END IF;
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_finish_run(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  owner_uuid UUID;
  run_uuid UUID;
  request_uuid UUID;
  completed_count INT;
  failed_count INT;
  claimed_count INT;
  final_status TEXT;
  response JSONB;
  handshake_run BOOLEAN;
  handshake_challenge_value BYTEA;
  check_row JSONB;
  check_observed_at TIMESTAMPTZ;
  required_host TEXT;
  telegram_status TEXT;
  telegram_ids JSONB;
  publication_incomplete_count INT;
BEGIN
  claim := machine.agent_claim_operation(p_request, 'finish_run');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;
  SELECT count(*) FILTER (WHERE status = 'completed'),
         count(*) FILTER (WHERE status = 'failed'),
         count(*) FILTER (WHERE status = 'claimed' AND request_id <> request_uuid)
  INTO completed_count, failed_count, claimed_count
  FROM app.market_gateway_requests
  WHERE owner_id = owner_uuid AND run_id = run_uuid;
  SELECT CASE
      WHEN bool_or(status = 'delivery_unknown') THEN 'delivery_unknown'
      WHEN bool_or(status = 'delivery_failed') THEN 'delivery_failed'
      WHEN bool_or(status = 'delivered') THEN 'delivered'
      ELSE 'not_sent'
    END,
    coalesce((
      SELECT jsonb_agg(message_id ORDER BY publication.created_at, message_id::text)
      FROM app.market_publications AS publication
      CROSS JOIN LATERAL jsonb_array_elements(publication.telegram_message_ids) AS message_id
      WHERE publication.owner_id = owner_uuid AND publication.run_id = run_uuid
    ), '[]'::jsonb),
    count(*) FILTER (WHERE status IN ('ready','sending','delivery_failed','delivery_unknown'))
  INTO telegram_status, telegram_ids, publication_incomplete_count
  FROM app.market_publications
  WHERE owner_id = owner_uuid AND run_id = run_uuid;
  final_status := CASE
    WHEN failed_count > 0 OR claimed_count > 0 OR publication_incomplete_count > 0
      THEN 'partial'
    ELSE 'completed'
  END;
  SELECT handshake_challenge INTO handshake_challenge_value
  FROM app.scheduled_run_slots
  WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake'
  FOR UPDATE;
  handshake_run := FOUND;
  IF handshake_run THEN
    IF NOT app.jsonb_has_exact_keys(
         p_request->'payload', ARRAY['contract_version','challenge','source_checks']
       ) OR p_request->'payload'->>'contract_version' <> '2'
       OR p_request->'payload'->>'challenge' <> encode(handshake_challenge_value, 'hex')
       OR jsonb_typeof(p_request->'payload'->'source_checks') <> 'array'
       OR jsonb_array_length(p_request->'payload'->'source_checks') <> 3 THEN
      RAISE EXCEPTION 'handshake verification failed' USING ERRCODE = '42501';
    END IF;
    FOREACH required_host IN ARRAY ARRAY[
      'query1.finance.yahoo.com', 'www.sec.gov', 'finnhub.io'
    ] LOOP
      IF (
        SELECT count(*) FROM jsonb_array_elements(p_request->'payload'->'source_checks') item
        WHERE item->>'host' = required_host
      ) <> 1 THEN
        RAISE EXCEPTION 'handshake verification failed' USING ERRCODE = '42501';
      END IF;
    END LOOP;
    FOR check_row IN
      SELECT value FROM jsonb_array_elements(p_request->'payload'->'source_checks')
    LOOP
      IF NOT app.jsonb_has_exact_keys(
           check_row, ARRAY['host','status','content_hash','observed_at']
         ) OR check_row->>'host' NOT IN (
           'query1.finance.yahoo.com', 'www.sec.gov', 'finnhub.io'
         ) OR check_row->>'status' <> 'reachable'
         OR check_row->>'content_hash' !~ '^[0-9a-f]{64}$'
         OR jsonb_typeof(check_row->'observed_at') <> 'string' THEN
        RAISE EXCEPTION 'handshake verification failed' USING ERRCODE = '42501';
      END IF;
      BEGIN
        check_observed_at := (check_row->>'observed_at')::timestamptz;
      EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
        RAISE EXCEPTION 'handshake verification failed' USING ERRCODE = '42501';
      END;
      IF check_observed_at < clock_timestamp() - interval '15 minutes'
         OR check_observed_at > clock_timestamp() + interval '5 minutes' THEN
        RAISE EXCEPTION 'handshake verification failed' USING ERRCODE = '42501';
      END IF;
    END LOOP;
  ELSIF NOT app.jsonb_has_exact_keys(p_request->'payload', ARRAY[]::text[]) THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  IF handshake_run AND NOT EXISTS (
    SELECT 1 FROM app.market_gateway_requests
    WHERE owner_id = owner_uuid AND run_id = run_uuid
      AND operation = 'read_bounded_context' AND status = 'completed'
  ) THEN
    final_status := 'partial';
  END IF;
  response := jsonb_build_object(
    'status', CASE WHEN (claim->>'dry_run')::boolean THEN 'dry_run' ELSE final_status END,
    'run_id', run_uuid,
    'operations', jsonb_build_object(
      'completed', completed_count, 'failed', failed_count, 'in_progress', claimed_count
    ),
    'telegram', jsonb_build_object('status', telegram_status, 'message_ids', telegram_ids)
  );
  IF (claim->>'dry_run')::boolean THEN RETURN response; END IF;
  UPDATE app.analysis_runs SET status = final_status, finished_at = clock_timestamp(),
    write_counts = jsonb_build_object('gateway_operations', completed_count),
    telegram_message_ids = telegram_ids,
    summary = CASE
      WHEN telegram_status = 'delivered' THEN 'Run completed with a recorded Telegram delivery.'
      WHEN telegram_status = 'delivery_failed' THEN 'Run finished partially; Telegram rejected the message.'
      WHEN telegram_status = 'delivery_unknown' THEN 'Run finished partially; Telegram delivery is unknown.'
      WHEN final_status = 'completed' THEN 'Run completed; no Telegram send was recorded.'
      ELSE 'Run finished partially; no Telegram send was recorded.'
    END
  WHERE owner_id = owner_uuid AND id = run_uuid;
  IF handshake_run AND final_status = 'completed' THEN
    UPDATE app.scheduled_run_slots SET status = 'completed',
      handshake_receipt = p_request->'payload', updated_at = clock_timestamp()
    WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake';
    UPDATE app.agent_connections SET status = 'ready', last_handshake_at = clock_timestamp(),
      updated_at = clock_timestamp()
    WHERE owner_id = owner_uuid AND id = (claim->>'connection_id')::uuid AND status = 'testing';
  END IF;
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
$$;

ALTER FUNCTION machine.agent_constant_time_equal(BYTEA, BYTEA) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_claim_operation(JSONB, TEXT) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_complete_operation(UUID, UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_start_run(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_build_owner_context(UUID, UUID, TEXT, DATE, TIMESTAMPTZ) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_read_bounded_context(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_submit_analysis(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_apply_analysis(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_finish_analysis_delivery(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_record_permitted_artifacts(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_grade_due_decisions(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_finish_run(JSONB) OWNER TO stock_agent_migration_owner;

REVOKE ALL ON FUNCTION machine.agent_constant_time_equal(BYTEA, BYTEA) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_claim_operation(JSONB, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_complete_operation(UUID, UUID, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_start_run(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_build_owner_context(UUID, UUID, TEXT, DATE, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_read_bounded_context(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_submit_analysis(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_apply_analysis(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_finish_analysis_delivery(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_record_permitted_artifacts(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_grade_due_decisions(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_finish_run(JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION machine.agent_start_run(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_read_bounded_context(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_submit_analysis(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_apply_analysis(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_finish_analysis_delivery(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_record_permitted_artifacts(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_grade_due_decisions(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_finish_run(JSONB) TO stock_agent_gateway;

-- Canonical market-session slots and the release-one Claude Routine trigger adapter.

ALTER TABLE app.agent_connections ADD COLUMN trigger_url TEXT CHECK (
  trigger_url IS NULL OR trigger_url ~
    '^https://api\.anthropic\.com/v1/claude_code/routines/trig_[A-Za-z0-9]{6,128}/fire$'
);

CREATE TABLE app.scheduled_run_slots (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  connection_id UUID NOT NULL,
  market_date DATE NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  purpose TEXT NOT NULL DEFAULT 'scheduled' CHECK (purpose IN ('scheduled','handshake')),
  handshake_challenge BYTEA,
  handshake_receipt JSONB,
  due_at TIMESTAMPTZ NOT NULL,
  window_ends_at TIMESTAMPTZ NOT NULL,
  holiday BOOLEAN NOT NULL DEFAULT false,
  trigger_request_id UUID NOT NULL DEFAULT extensions.gen_random_uuid(),
  canonical_run_id UUID,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
    'pending','claimed','triggered','trigger_failed','trigger_unknown',
    'provider_started','completed','missed','holiday_ready','budget_suppressed'
  )),
  lease_token UUID,
  lease_expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, trigger_request_id),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, connection_id)
    REFERENCES app.agent_connections(owner_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (owner_id, canonical_run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE RESTRICT,
  CHECK (window_ends_at > due_at),
  CHECK ((status = 'pending' AND lease_token IS NULL) OR status <> 'pending'),
  CHECK ((purpose = 'handshake' AND phase = 'on-demand' AND NOT holiday)
      OR (purpose = 'scheduled' AND phase <> 'on-demand')),
  CHECK ((purpose = 'handshake' AND octet_length(handshake_challenge) = 32)
      OR (purpose = 'scheduled' AND handshake_challenge IS NULL)),
  CHECK (handshake_receipt IS NULL OR purpose = 'handshake')
);

CREATE TABLE app.routine_trigger_attempts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  slot_id UUID NOT NULL,
  connection_id UUID NOT NULL,
  trigger_request_id UUID NOT NULL,
  status TEXT NOT NULL DEFAULT 'claimed' CHECK (status IN (
    'claimed','triggered','trigger_failed','trigger_unknown','provider_started'
  )),
  response_status INT CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
  provider_session_url TEXT CHECK (
    provider_session_url IS NULL OR provider_session_url ~
      '^https://claude\.ai/code/session_[A-Za-z0-9_-]{6,200}$'
  ),
  response_digest TEXT CHECK (response_digest IS NULL OR response_digest ~ '^[0-9a-f]{64}$'),
  claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  UNIQUE (owner_id, slot_id),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, slot_id)
    REFERENCES app.scheduled_run_slots(owner_id, id) ON DELETE CASCADE,
  FOREIGN KEY (owner_id, connection_id)
    REFERENCES app.agent_connections(owner_id, id) ON DELETE RESTRICT
);

CREATE TABLE app.owner_run_allowances (
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  allowance_date DATE NOT NULL,
  invocation_count INT NOT NULL DEFAULT 0 CHECK (invocation_count BETWEEN 0 AND 6),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id, allowance_date)
);

CREATE TABLE app.operational_events (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  code TEXT NOT NULL CHECK (code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
  period_key TEXT NOT NULL CHECK (char_length(period_key) BETWEEN 1 AND 32),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','notified','resolved')),
  detail JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (
    jsonb_typeof(detail) = 'object' AND octet_length(detail::text) <= 2000
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, code, period_key),
  UNIQUE (owner_id, id)
);

CREATE TABLE app.owner_operational_state (
  owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  mutations_paused BOOLEAN NOT NULL DEFAULT false,
  reason_code TEXT CHECK (
    reason_code IS NULL OR reason_code IN ('LEDGER_PROJECTION_MISMATCH')
  ),
  last_projection_check_at TIMESTAMPTZ,
  last_projection_ok BOOLEAN,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (mutations_paused = (reason_code IS NOT NULL))
);

CREATE TABLE app.operational_alerts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  event_id UUID NOT NULL,
  code TEXT NOT NULL CHECK (code IN (
    'EXPECTED_RUN_MISSED','PROVIDER_DISCONNECTED','LEDGER_PROJECTION_MISMATCH',
    'RUN_PARTIAL','BACKUP_STALE'
  )),
  status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN (
    'ready','sending','delivered','suppressed','delivery_failed','delivery_unknown'
  )),
  telegram_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(telegram_message_ids) = 'array'
    AND jsonb_array_length(telegram_message_ids) <= 4
  ),
  attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
  lease_token UUID,
  sending_started_at TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ,
  error_code TEXT CHECK (error_code IS NULL OR error_code IN (
    'TELEGRAM_REJECTED','TELEGRAM_OUTCOME_UNKNOWN','DELIVERY_LEASE_EXPIRED'
  )),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, event_id),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, event_id)
    REFERENCES app.operational_events(owner_id, id) ON DELETE CASCADE,
  CHECK ((status = 'sending') = (lease_token IS NOT NULL)),
  CHECK (status <> 'sending' OR sending_started_at IS NOT NULL),
  CHECK (status <> 'delivered' OR (
    delivered_at IS NOT NULL AND jsonb_array_length(telegram_message_ids) > 0
  ))
);

ALTER TABLE app.scheduled_run_slots OWNER TO stock_agent_migration_owner;
ALTER TABLE app.routine_trigger_attempts OWNER TO stock_agent_migration_owner;
ALTER TABLE app.owner_run_allowances OWNER TO stock_agent_migration_owner;
ALTER TABLE app.operational_events OWNER TO stock_agent_migration_owner;
ALTER TABLE app.owner_operational_state OWNER TO stock_agent_migration_owner;
ALTER TABLE app.operational_alerts OWNER TO stock_agent_migration_owner;
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.scheduled_run_slots
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.routine_trigger_attempts
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.owner_run_allowances
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.operational_events
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.owner_operational_state
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.operational_alerts
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
ALTER TABLE app.scheduled_run_slots ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.scheduled_run_slots FORCE ROW LEVEL SECURITY;
ALTER TABLE app.routine_trigger_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.routine_trigger_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE app.owner_run_allowances ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.owner_run_allowances FORCE ROW LEVEL SECURITY;
ALTER TABLE app.operational_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.operational_events FORCE ROW LEVEL SECURITY;
ALTER TABLE app.owner_operational_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.owner_operational_state FORCE ROW LEVEL SECURITY;
ALTER TABLE app.operational_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.operational_alerts FORCE ROW LEVEL SECURITY;
CREATE POLICY scheduled_run_slots_executor_all ON app.scheduled_run_slots
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY routine_trigger_attempts_executor_all ON app.routine_trigger_attempts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY owner_run_allowances_executor_all ON app.owner_run_allowances
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY operational_events_executor_all ON app.operational_events
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY owner_operational_state_executor_all ON app.owner_operational_state
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY operational_alerts_executor_all ON app.operational_alerts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
REVOKE ALL ON app.scheduled_run_slots, app.routine_trigger_attempts,
  app.owner_run_allowances, app.operational_events, app.owner_operational_state,
  app.operational_alerts
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE INDEX scheduled_run_slots_due_idx ON app.scheduled_run_slots(status, due_at, window_ends_at);
CREATE UNIQUE INDEX scheduled_run_slots_canonical_idx
  ON app.scheduled_run_slots(owner_id, market_date, phase) WHERE purpose = 'scheduled';
CREATE INDEX routine_trigger_attempts_status_idx ON app.routine_trigger_attempts(status, claimed_at);
CREATE INDEX operational_events_status_idx ON app.operational_events(status, created_at);
CREATE INDEX operational_alerts_status_idx ON app.operational_alerts(status, created_at);

CREATE OR REPLACE FUNCTION app.enforce_owner_mutation_safety()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM app.owner_operational_state
    WHERE owner_id = NEW.owner_id AND mutations_paused
  ) THEN
    RAISE EXCEPTION 'owner mutations are paused' USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END
$$;
ALTER FUNCTION app.enforce_owner_mutation_safety() OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.enforce_owner_mutation_safety()
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE TRIGGER enforce_owner_mutation_safety
  BEFORE UPDATE OF status ON app.portfolio_commands
  FOR EACH ROW
  WHEN (NEW.status IN ('confirmed','applied'))
  EXECUTE FUNCTION app.enforce_owner_mutation_safety();
CREATE TRIGGER enforce_owner_ledger_safety
  BEFORE INSERT ON app.transactions
  FOR EACH ROW
  WHEN (NEW.event_type IN ('trade','void') AND NEW.source_channel <> 'migration')
  EXECUTE FUNCTION app.enforce_owner_mutation_safety();

CREATE TABLE machine.market_calendar_days (
  market_date DATE PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('open','holiday')),
  early_close BOOLEAN NOT NULL DEFAULT false,
  reviewed BOOLEAN NOT NULL DEFAULT false,
  CHECK (status = 'open' OR NOT early_close)
);
CREATE TABLE machine.routine_budget_config (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  monthly_limit INT NOT NULL CHECK (monthly_limit BETWEEN 1 AND 100000),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE machine.routine_monthly_usage (
  usage_month DATE PRIMARY KEY CHECK (usage_month = date_trunc('month', usage_month)::date),
  invocation_count INT NOT NULL DEFAULT 0 CHECK (invocation_count >= 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE machine.market_calendar_days OWNER TO stock_agent_migration_owner;
ALTER TABLE machine.routine_budget_config OWNER TO stock_agent_migration_owner;
ALTER TABLE machine.routine_monthly_usage OWNER TO stock_agent_migration_owner;
REVOKE ALL ON machine.market_calendar_days, machine.routine_budget_config,
  machine.routine_monthly_usage FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
INSERT INTO machine.routine_budget_config(singleton, monthly_limit) VALUES (true, 1000);
INSERT INTO machine.market_calendar_days(market_date, status, early_close, reviewed)
SELECT day_value::date,
       CASE WHEN day_value::date = ANY(ARRAY[
         DATE '2026-01-01', DATE '2026-01-19', DATE '2026-02-16', DATE '2026-04-03',
         DATE '2026-05-25', DATE '2026-06-19', DATE '2026-07-03', DATE '2026-09-07',
         DATE '2026-11-26', DATE '2026-12-25'
       ]) THEN 'holiday' ELSE 'open' END,
       day_value::date = ANY(ARRAY[DATE '2026-07-02', DATE '2026-11-27', DATE '2026-12-24']),
       true
FROM generate_series(DATE '2026-01-01', DATE '2026-12-31', interval '1 day') AS day_value
WHERE extract(isodow FROM day_value) BETWEEN 1 AND 5;

CREATE OR REPLACE FUNCTION machine.scheduler_claim_due_slots(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  effective_now TIMESTAMPTZ;
  claim_limit INT;
  market_day DATE;
  calendar_row machine.market_calendar_days%ROWTYPE;
  calendar_available BOOLEAN;
  candidate app.scheduled_run_slots%ROWTYPE;
  budget_limit INT;
  usage_month_value DATE;
  owner_consumed BOOLEAN;
  product_consumed BOOLEAN;
  attempt_uuid UUID;
  slots JSONB := '[]'::jsonb;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['now','limit'])
     OR jsonb_typeof(p_request->'now') <> 'string'
     OR p_request->>'limit' !~ '^\d{1,2}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN effective_now := (p_request->>'now')::timestamptz;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  claim_limit := (p_request->>'limit')::int;
  IF claim_limit NOT BETWEEN 1 AND 20 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  market_day := (effective_now AT TIME ZONE 'America/New_York')::date;
  SELECT * INTO calendar_row FROM machine.market_calendar_days
  WHERE market_date = market_day AND reviewed;
  calendar_available := FOUND;

  IF calendar_available AND calendar_row.status = 'holiday' THEN
    INSERT INTO app.scheduled_run_slots(
      owner_id, connection_id, market_date, phase, due_at, window_ends_at, holiday
    )
    SELECT schedule.owner_id, connection.id, market_day, 'pre-market',
      (market_day + TIME '09:30') AT TIME ZONE 'America/New_York' - interval '120 minutes',
      (market_day + TIME '09:30') AT TIME ZONE 'America/New_York' - interval '60 minutes', true
    FROM app.analysis_schedules AS schedule
    JOIN app.agent_connections AS connection
      ON connection.owner_id = schedule.owner_id AND connection.id = schedule.primary_connection_id
    WHERE schedule.pre_market_enabled AND connection.status = 'active'
      AND connection.provider = 'claude' AND connection.trigger_url IS NOT NULL
    ON CONFLICT (owner_id, market_date, phase) WHERE purpose = 'scheduled' DO NOTHING;
  ELSIF calendar_available THEN
    INSERT INTO app.scheduled_run_slots(
      owner_id, connection_id, market_date, phase, due_at, window_ends_at, holiday
    )
    SELECT schedule.owner_id, connection.id, market_day, phase_value,
      CASE phase_value
        WHEN 'pre-market' THEN (market_day + TIME '09:30') AT TIME ZONE 'America/New_York' - interval '120 minutes'
        WHEN 'intraday' THEN (market_day + TIME '09:30') AT TIME ZONE 'America/New_York'
          + CASE WHEN calendar_row.early_close THEN interval '120 minutes' ELSE interval '210 minutes' END
        ELSE (market_day + CASE WHEN calendar_row.early_close THEN TIME '13:00' ELSE TIME '16:00' END)
          AT TIME ZONE 'America/New_York' + interval '10 minutes'
      END,
      CASE phase_value
        WHEN 'pre-market' THEN (market_day + TIME '09:30') AT TIME ZONE 'America/New_York' - interval '60 minutes'
        WHEN 'intraday' THEN (market_day + TIME '09:30') AT TIME ZONE 'America/New_York'
          + CASE WHEN calendar_row.early_close THEN interval '180 minutes' ELSE interval '270 minutes' END
        ELSE (market_day + CASE WHEN calendar_row.early_close THEN TIME '13:00' ELSE TIME '16:00' END)
          AT TIME ZONE 'America/New_York' + interval '70 minutes'
      END,
      false
    FROM app.analysis_schedules AS schedule
    JOIN app.agent_connections AS connection
      ON connection.owner_id = schedule.owner_id AND connection.id = schedule.primary_connection_id
    CROSS JOIN unnest(ARRAY['pre-market','intraday','post-market']) AS phase_value
    WHERE connection.status = 'active' AND connection.provider = 'claude'
      AND connection.trigger_url IS NOT NULL
      AND CASE phase_value WHEN 'pre-market' THEN schedule.pre_market_enabled
          WHEN 'intraday' THEN schedule.intraday_enabled ELSE schedule.post_market_enabled END
    ON CONFLICT (owner_id, market_date, phase) WHERE purpose = 'scheduled' DO NOTHING;
  END IF;

  SELECT monthly_limit INTO budget_limit FROM machine.routine_budget_config WHERE singleton;
  usage_month_value := date_trunc('month', market_day)::date;
  FOR candidate IN
    SELECT * FROM app.scheduled_run_slots
    WHERE market_date = market_day AND status = 'pending'
      AND (purpose = 'handshake' OR calendar_available)
      AND due_at <= effective_now AND window_ends_at >= effective_now
    ORDER BY due_at, owner_id
    LIMIT claim_limit FOR UPDATE SKIP LOCKED
  LOOP
    IF candidate.holiday THEN
      UPDATE app.scheduled_run_slots SET status = 'claimed', lease_token = extensions.gen_random_uuid(),
        lease_expires_at = candidate.window_ends_at, updated_at = clock_timestamp()
      WHERE owner_id = candidate.owner_id AND id = candidate.id;
      slots := slots || jsonb_build_array(jsonb_build_object(
        'slot_id', candidate.id, 'trigger_request_id', candidate.trigger_request_id,
        'phase', candidate.phase, 'market_date', candidate.market_date,
        'holiday', true, 'attempt_id', null
      ));
      CONTINUE;
    END IF;

    INSERT INTO machine.routine_monthly_usage(usage_month) VALUES (usage_month_value)
      ON CONFLICT (usage_month) DO NOTHING;
    UPDATE machine.routine_monthly_usage SET invocation_count = invocation_count + 1,
      updated_at = clock_timestamp()
    WHERE routine_monthly_usage.usage_month = usage_month_value
      AND invocation_count < budget_limit;
    product_consumed := FOUND;
    IF NOT product_consumed THEN
      UPDATE app.scheduled_run_slots SET status = 'budget_suppressed', updated_at = clock_timestamp()
      WHERE owner_id = candidate.owner_id AND id = candidate.id;
      INSERT INTO app.operational_events(owner_id, code, period_key)
      VALUES (candidate.owner_id, 'ROUTINE_MONTHLY_BUDGET_REACHED', to_char(usage_month_value, 'YYYY-MM'))
      ON CONFLICT (owner_id, code, period_key) DO NOTHING;
      CONTINUE;
    END IF;

    INSERT INTO app.owner_run_allowances(owner_id, allowance_date)
    VALUES (candidate.owner_id, market_day)
    ON CONFLICT (owner_id, allowance_date) DO NOTHING;
    UPDATE app.owner_run_allowances SET invocation_count = invocation_count + 1,
      updated_at = clock_timestamp()
    WHERE owner_id = candidate.owner_id AND allowance_date = market_day
      AND invocation_count < 6;
    owner_consumed := FOUND;
    IF NOT owner_consumed THEN
      UPDATE machine.routine_monthly_usage SET invocation_count = invocation_count - 1,
        updated_at = clock_timestamp() WHERE routine_monthly_usage.usage_month = usage_month_value;
      UPDATE app.scheduled_run_slots SET status = 'budget_suppressed', updated_at = clock_timestamp()
      WHERE owner_id = candidate.owner_id AND id = candidate.id;
      INSERT INTO app.operational_events(owner_id, code, period_key)
      VALUES (candidate.owner_id, 'OWNER_DAILY_RUN_LIMIT_REACHED', market_day::text)
      ON CONFLICT (owner_id, code, period_key) DO NOTHING;
      CONTINUE;
    END IF;

    attempt_uuid := extensions.gen_random_uuid();
    UPDATE app.scheduled_run_slots SET status = 'claimed', lease_token = extensions.gen_random_uuid(),
      lease_expires_at = candidate.window_ends_at, updated_at = clock_timestamp()
    WHERE owner_id = candidate.owner_id AND id = candidate.id;
    INSERT INTO app.routine_trigger_attempts(
      id, owner_id, slot_id, connection_id, trigger_request_id
    ) VALUES (
      attempt_uuid, candidate.owner_id, candidate.id, candidate.connection_id,
      candidate.trigger_request_id
    );
    slots := slots || jsonb_build_array(jsonb_build_object(
      'slot_id', candidate.id, 'trigger_request_id', candidate.trigger_request_id,
      'phase', candidate.phase, 'market_date', candidate.market_date,
      'holiday', false, 'attempt_id', attempt_uuid
    ));
  END LOOP;
  RETURN jsonb_build_object(
    'slots', slots,
    'calendar_status', CASE WHEN calendar_available THEN calendar_row.status ELSE 'unavailable' END
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_read_trigger_secret(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  attempt_uuid UUID;
  endpoint_value TEXT;
  token_value TEXT;
  request_uuid UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['attempt_id']) THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN attempt_uuid := (p_request->>'attempt_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  SELECT connection.trigger_url, secret.secret, attempt.trigger_request_id
  INTO endpoint_value, token_value, request_uuid
  FROM app.routine_trigger_attempts AS attempt
  JOIN app.scheduled_run_slots AS slot
    ON slot.owner_id = attempt.owner_id AND slot.id = attempt.slot_id
  JOIN app.agent_connections AS connection
    ON connection.owner_id = attempt.owner_id AND connection.id = attempt.connection_id
  JOIN vault.decrypted_secrets AS secret ON secret.id = connection.outbound_trigger_secret_id
  WHERE attempt.id = attempt_uuid AND attempt.status = 'claimed'
    AND slot.status = 'claimed'
    AND (
      connection.status = 'active'
      OR (connection.status = 'testing' AND slot.purpose = 'handshake')
    );
  IF NOT FOUND OR endpoint_value !~
    '^https://api\.anthropic\.com/v1/claude_code/routines/trig_[A-Za-z0-9]{6,128}/fire$'
    OR token_value IS NULL OR char_length(token_value) NOT BETWEEN 24 AND 500
    OR token_value ~ '\s' THEN
    RAISE EXCEPTION 'scheduler trigger unavailable' USING ERRCODE = '42501';
  END IF;
  RETURN jsonb_build_object('endpoint', endpoint_value, 'token', token_value,
    'trigger_request_id', request_uuid);
END
$$;

GRANT USAGE ON SCHEMA vault TO stock_agent_migration_owner;
GRANT SELECT ON vault.decrypted_secrets TO stock_agent_migration_owner;

CREATE OR REPLACE FUNCTION machine.scheduler_record_trigger_result(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  attempt_uuid UUID;
  attempt_row app.routine_trigger_attempts%ROWTYPE;
  status_value TEXT;
  response_code INT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['attempt_id','status','response_status','session_url','response_digest']
  ) OR p_request->>'status' NOT IN ('triggered','trigger_failed','trigger_unknown')
     OR p_request->>'response_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    attempt_uuid := (p_request->>'attempt_id')::uuid;
    response_code := CASE WHEN p_request->'response_status' = 'null'::jsonb THEN NULL
      ELSE (p_request->>'response_status')::int END;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  IF response_code IS NOT NULL AND response_code NOT BETWEEN 100 AND 599 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  IF p_request->'session_url' <> 'null'::jsonb AND p_request->>'session_url' !~
    '^https://claude\.ai/code/session_[A-Za-z0-9_-]{6,200}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO attempt_row FROM app.routine_trigger_attempts
  WHERE id = attempt_uuid FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'scheduler trigger unavailable' USING ERRCODE = '42501'; END IF;
  status_value := p_request->>'status';
  IF attempt_row.status <> 'claimed' THEN
    IF attempt_row.status = 'provider_started' AND attempt_row.response_digest IS NULL THEN
      UPDATE app.routine_trigger_attempts SET
        response_status = response_code,
        provider_session_url = CASE WHEN p_request->'session_url' = 'null'::jsonb THEN NULL ELSE p_request->>'session_url' END,
        response_digest = p_request->>'response_digest', finished_at = clock_timestamp()
      WHERE id = attempt_uuid;
      RETURN jsonb_build_object('status', 'provider_started', 'duplicate', false);
    END IF;
    IF attempt_row.status = status_value
       AND attempt_row.response_status IS NOT DISTINCT FROM response_code
       AND attempt_row.provider_session_url IS NOT DISTINCT FROM nullif(p_request->>'session_url', '')
       AND attempt_row.response_digest = p_request->>'response_digest' THEN
      RETURN jsonb_build_object('status', attempt_row.status, 'duplicate', true);
    END IF;
    RAISE EXCEPTION 'scheduler result conflict' USING ERRCODE = '23505';
  END IF;
  UPDATE app.routine_trigger_attempts SET status = status_value,
    response_status = response_code,
    provider_session_url = CASE WHEN p_request->'session_url' = 'null'::jsonb THEN NULL ELSE p_request->>'session_url' END,
    response_digest = p_request->>'response_digest', finished_at = clock_timestamp()
  WHERE id = attempt_uuid;
  UPDATE app.scheduled_run_slots SET status = status_value, updated_at = clock_timestamp()
  WHERE owner_id = attempt_row.owner_id AND id = attempt_row.slot_id AND status = 'claimed';
  RETURN jsonb_build_object('status', status_value, 'duplicate', false);
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_publish_holiday(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  slot_uuid UUID;
  slot_row app.scheduled_run_slots%ROWTYPE;
  publication_uuid UUID;
  body_value TEXT := '🏛 Market closed today — US public holiday. No brief.';
  body_digest TEXT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['slot_id']) THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN slot_uuid := (p_request->>'slot_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  SELECT * INTO slot_row FROM app.scheduled_run_slots
  WHERE id = slot_uuid AND holiday AND phase = 'pre-market' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'scheduler slot unavailable' USING ERRCODE = '42501'; END IF;
  SELECT id INTO publication_uuid FROM app.market_publications
  WHERE owner_id = slot_row.owner_id AND market_date = slot_row.market_date
    AND phase = 'pre-market' AND kind = 'holiday';
  IF FOUND THEN RETURN jsonb_build_object('status', 'ready', 'publication_id', publication_uuid, 'duplicate', true); END IF;
  IF slot_row.status <> 'claimed' THEN
    RAISE EXCEPTION 'scheduler slot unavailable' USING ERRCODE = '42501';
  END IF;
  body_digest := encode(extensions.digest(convert_to(body_value, 'UTF8'), 'sha256'), 'hex');
  INSERT INTO app.market_gateway_requests(
    owner_id, request_id, operation, connection_id, input_digest, status,
    lease_token, response, response_digest, finished_at
  ) VALUES (
    slot_row.owner_id, slot_row.trigger_request_id, 'start_run', slot_row.connection_id,
    encode(extensions.digest(convert_to(slot_row.id::text, 'UTF8'), 'sha256'), 'hex'),
    'completed', extensions.gen_random_uuid(), jsonb_build_object('holiday', true),
    encode(extensions.digest(convert_to('{"holiday": true}', 'UTF8'), 'sha256'), 'hex'), clock_timestamp()
  );
  publication_uuid := extensions.gen_random_uuid();
  INSERT INTO app.market_publications(
    owner_id, id, idempotency_key, run_id, market_date, phase, kind,
    template_version, rendered_body, rendered_parts, rendered_hash, status
  ) VALUES (
    slot_row.owner_id, publication_uuid, slot_row.trigger_request_id, NULL,
    slot_row.market_date, 'pre-market', 'holiday', 1, body_value,
    jsonb_build_array(body_value), body_digest, 'ready'
  );
  UPDATE app.scheduled_run_slots SET status = 'holiday_ready', updated_at = clock_timestamp()
  WHERE owner_id = slot_row.owner_id AND id = slot_uuid;
  RETURN jsonb_build_object('status', 'ready', 'publication_id', publication_uuid, 'duplicate', false);
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_run_maintenance(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  effective_now TIMESTAMPTZ;
  expired_commands INT := 0;
  expired_pairing_codes INT := 0;
  expired_callback_tokens INT := 0;
  deleted_updates INT := 0;
  missed_slots INT := 0;
  stale_publications INT := 0;
  stale_operational_alerts INT := 0;
  projection_failures INT := 0;
  alerts_created INT := 0;
  publication_row app.market_publications%ROWTYPE;
  slot_row app.scheduled_run_slots%ROWTYPE;
  owner_row RECORD;
  identity_row RECORD;
  holding_row app.holdings%ROWTYPE;
  folded JSONB;
  mismatch BOOLEAN;
  event_uuid UUID;
  final_response JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['now'])
     OR jsonb_typeof(p_request->'now') <> 'string' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN effective_now := (p_request->>'now')::timestamptz;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  IF effective_now < clock_timestamp() - interval '1 day'
     OR effective_now > clock_timestamp() + interval '5 minutes' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;

  WITH expired AS (
    SELECT owner_id, id FROM app.portfolio_commands
    WHERE status IN ('submitted','previewed') AND expires_at <= effective_now
    ORDER BY expires_at, id LIMIT 200 FOR UPDATE SKIP LOCKED
  )
  UPDATE app.portfolio_commands AS command SET
    status = 'expired',
    preview_digest = coalesce(
      command.preview_digest,
      encode(extensions.digest(convert_to('expired-unpreviewed:' || command.id::text, 'UTF8'), 'sha256'), 'hex')
    ),
    updated_at = effective_now
  FROM expired
  WHERE command.owner_id = expired.owner_id AND command.id = expired.id;
  GET DIAGNOSTICS expired_commands = ROW_COUNT;

  WITH expired AS (
    SELECT owner_id, id FROM app.telegram_pairing_codes
    WHERE consumed_at IS NULL AND expires_at <= effective_now
    ORDER BY expires_at, id LIMIT 200 FOR UPDATE SKIP LOCKED
  )
  UPDATE app.telegram_pairing_codes AS code SET consumed_at = effective_now
  FROM expired WHERE code.owner_id = expired.owner_id AND code.id = expired.id;
  GET DIAGNOSTICS expired_pairing_codes = ROW_COUNT;
  WITH expired AS (
    SELECT owner_id, id FROM app.telegram_callback_tokens
    WHERE consumed_at IS NULL AND invalidated_at IS NULL AND expires_at <= effective_now
    ORDER BY expires_at, id LIMIT 400 FOR UPDATE SKIP LOCKED
  )
  UPDATE app.telegram_callback_tokens AS token SET invalidated_at = effective_now
  FROM expired WHERE token.owner_id = expired.owner_id AND token.id = expired.id;
  GET DIAGNOSTICS expired_callback_tokens = ROW_COUNT;
  WITH expired AS (
    SELECT owner_id, telegram_update_id FROM app.telegram_updates
    WHERE received_at < effective_now - interval '30 days'
    ORDER BY received_at, owner_id, telegram_update_id LIMIT 500
  )
  DELETE FROM app.telegram_updates AS update_row USING expired
  WHERE update_row.owner_id = expired.owner_id
    AND update_row.telegram_update_id = expired.telegram_update_id;
  GET DIAGNOSTICS deleted_updates = ROW_COUNT;

  FOR publication_row IN
    SELECT * FROM app.market_publications
    WHERE status = 'sending'
      AND sending_started_at < effective_now - interval '5 minutes'
    ORDER BY sending_started_at, id LIMIT 100
    FOR UPDATE
  LOOP
    UPDATE app.market_publications SET
      status = 'delivery_unknown', lease_token = NULL,
      error = 'DELIVERY_LEASE_EXPIRED', updated_at = effective_now
    WHERE owner_id = publication_row.owner_id AND id = publication_row.id;
    IF publication_row.run_id IS NOT NULL THEN
      final_response := jsonb_build_object(
        'status', 'accepted', 'run_id', publication_row.run_id,
        'publication_id', publication_row.id,
        'publication_status', 'delivery_unknown',
        'telegram_message_ids', publication_row.telegram_message_ids
      );
      UPDATE app.market_gateway_requests SET
        status = 'completed', response = final_response,
        response_digest = encode(extensions.digest(convert_to(final_response::text, 'UTF8'), 'sha256'), 'hex'),
        finished_at = effective_now
      WHERE owner_id = publication_row.owner_id
        AND request_id = publication_row.idempotency_key AND status = 'claimed';
      UPDATE app.analysis_runs SET
        telegram_message_ids = publication_row.telegram_message_ids,
        source_status = source_status || jsonb_build_object('publication', 'delivery_unknown')
      WHERE owner_id = publication_row.owner_id AND id = publication_row.run_id;
    END IF;
    stale_publications := stale_publications + 1;
  END LOOP;

  WITH stale AS (
    SELECT owner_id, id FROM app.operational_alerts
    WHERE status = 'sending'
      AND sending_started_at < effective_now - interval '5 minutes'
    ORDER BY sending_started_at, id LIMIT 100 FOR UPDATE SKIP LOCKED
  )
  UPDATE app.operational_alerts AS alert SET
    status = 'delivery_unknown', lease_token = NULL,
    error_code = 'DELIVERY_LEASE_EXPIRED', updated_at = effective_now
  FROM stale WHERE alert.owner_id = stale.owner_id AND alert.id = stale.id;
  GET DIAGNOSTICS stale_operational_alerts = ROW_COUNT;

  FOR slot_row IN
    SELECT * FROM app.scheduled_run_slots
    WHERE status IN (
      'pending','claimed','triggered','trigger_failed','trigger_unknown','provider_started'
    ) AND window_ends_at < effective_now
    ORDER BY window_ends_at, id LIMIT 100
    FOR UPDATE
  LOOP
    UPDATE app.scheduled_run_slots SET
      status = 'missed', lease_token = NULL, lease_expires_at = NULL,
      updated_at = effective_now
    WHERE owner_id = slot_row.owner_id AND id = slot_row.id;
    missed_slots := missed_slots + 1;
    IF slot_row.purpose = 'scheduled' THEN
      INSERT INTO app.operational_events(owner_id, code, period_key)
      VALUES (
        slot_row.owner_id, 'EXPECTED_RUN_MISSED',
        slot_row.market_date::text || ':' || slot_row.phase
      ) ON CONFLICT (owner_id, code, period_key) DO NOTHING;
    END IF;
  END LOOP;

  INSERT INTO app.operational_events(owner_id, code, period_key)
  SELECT schedule.owner_id, 'PROVIDER_DISCONNECTED',
         (effective_now AT TIME ZONE 'America/New_York')::date::text
  FROM app.analysis_schedules AS schedule
  JOIN app.agent_connections AS connection
    ON connection.owner_id = schedule.owner_id AND connection.id = schedule.primary_connection_id
  JOIN app.profiles AS profile ON profile.id = schedule.owner_id AND profile.status = 'active'
  WHERE connection.status <> 'active'
    AND NOT EXISTS (
      SELECT 1 FROM app.operational_events AS existing
      WHERE existing.owner_id = schedule.owner_id
        AND existing.code = 'PROVIDER_DISCONNECTED'
        AND existing.period_key = (effective_now AT TIME ZONE 'America/New_York')::date::text
    )
  ORDER BY schedule.owner_id LIMIT 100
  ON CONFLICT (owner_id, code, period_key) DO NOTHING;

  INSERT INTO app.operational_events(owner_id, code, period_key)
  SELECT run.owner_id, 'RUN_PARTIAL', substr(md5(run.id::text), 1, 32)
  FROM app.analysis_runs AS run
  WHERE run.status = 'partial'
    AND NOT EXISTS (
      SELECT 1 FROM app.operational_events AS existing
      WHERE existing.owner_id = run.owner_id AND existing.code = 'RUN_PARTIAL'
        AND existing.period_key = substr(md5(run.id::text), 1, 32)
    )
  ORDER BY run.finished_at NULLS FIRST, run.id LIMIT 100
  ON CONFLICT (owner_id, code, period_key) DO NOTHING;

  FOR owner_row IN
    SELECT profile.id
    FROM app.profiles AS profile
    LEFT JOIN app.owner_operational_state AS state ON state.owner_id = profile.id
    WHERE profile.status = 'active'
      AND (
        state.last_projection_check_at IS NULL
        OR (state.last_projection_check_at AT TIME ZONE 'America/New_York')::date
           < (effective_now AT TIME ZONE 'America/New_York')::date
    )
    ORDER BY profile.id LIMIT 20
  LOOP
    mismatch := false;
    BEGIN
      FOR identity_row IN
        SELECT ticker FROM app.holdings WHERE owner_id = owner_row.id
        UNION
        SELECT ticker FROM app.transactions
        WHERE owner_id = owner_row.id AND event_type IN ('opening','trade','void')
        ORDER BY ticker
      LOOP
        folded := app.fold_holding(owner_row.id, identity_row.ticker);
        SELECT * INTO holding_row FROM app.holdings
        WHERE owner_id = owner_row.id AND ticker = identity_row.ticker;
        IF (folded->>'shares')::numeric = 0 THEN
          IF FOUND THEN mismatch := true; EXIT; END IF;
        ELSIF NOT FOUND
           OR holding_row.shares IS DISTINCT FROM (folded->>'shares')::numeric
           OR holding_row.avg_cost IS DISTINCT FROM (folded->>'avg_cost')::numeric
           OR holding_row.projection_sequence IS DISTINCT FROM (folded->>'projection_sequence')::bigint THEN
          mismatch := true;
          EXIT;
        END IF;
      END LOOP;
    EXCEPTION WHEN OTHERS THEN
      mismatch := true;
    END;
    INSERT INTO app.owner_operational_state(
      owner_id, mutations_paused, reason_code, last_projection_check_at,
      last_projection_ok, updated_at
    ) VALUES (
      owner_row.id, mismatch,
      CASE WHEN mismatch THEN 'LEDGER_PROJECTION_MISMATCH' ELSE NULL END,
      effective_now, NOT mismatch, effective_now
    ) ON CONFLICT (owner_id) DO UPDATE SET
      mutations_paused = excluded.mutations_paused,
      reason_code = excluded.reason_code,
      last_projection_check_at = excluded.last_projection_check_at,
      last_projection_ok = excluded.last_projection_ok,
      updated_at = excluded.updated_at;
    IF mismatch THEN
      projection_failures := projection_failures + 1;
      INSERT INTO app.operational_events(owner_id, code, period_key)
      VALUES (
        owner_row.id, 'LEDGER_PROJECTION_MISMATCH',
        (effective_now AT TIME ZONE 'America/New_York')::date::text
      ) ON CONFLICT (owner_id, code, period_key) DO NOTHING;
    ELSE
      UPDATE app.operational_events SET status = 'resolved', updated_at = effective_now
      WHERE owner_id = owner_row.id AND code = 'LEDGER_PROJECTION_MISMATCH' AND status = 'open';
      UPDATE app.operational_alerts SET status = 'suppressed', updated_at = effective_now
      WHERE owner_id = owner_row.id AND code = 'LEDGER_PROJECTION_MISMATCH' AND status = 'ready';
    END IF;
  END LOOP;

  INSERT INTO app.operational_alerts(owner_id, event_id, code)
  SELECT event.owner_id, event.id, event.code
  FROM app.operational_events AS event
  WHERE event.status = 'open' AND event.code IN (
    'EXPECTED_RUN_MISSED','PROVIDER_DISCONNECTED','LEDGER_PROJECTION_MISMATCH',
    'RUN_PARTIAL','BACKUP_STALE'
  )
    AND NOT EXISTS (
      SELECT 1 FROM app.operational_alerts AS existing
      WHERE existing.owner_id = event.owner_id AND existing.event_id = event.id
    )
  ORDER BY event.created_at, event.id LIMIT 100
  ON CONFLICT (owner_id, event_id) DO NOTHING;
  GET DIAGNOSTICS alerts_created = ROW_COUNT;

  RETURN jsonb_build_object(
    'expired_commands', expired_commands,
    'expired_pairing_codes', expired_pairing_codes,
    'expired_callback_tokens', expired_callback_tokens,
    'deleted_telegram_updates', deleted_updates,
    'missed_slots', missed_slots,
    'stale_publications', stale_publications,
    'stale_operational_alerts', stale_operational_alerts,
    'projection_failures', projection_failures,
    'alerts_created', alerts_created
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_claim_publications(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  effective_now TIMESTAMPTZ;
  claim_limit INT;
  publication_row app.market_publications%ROWTYPE;
  lease_uuid UUID;
  chat_value BIGINT;
  enabled_value BOOLEAN;
  publications JSONB := '[]'::jsonb;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['now','limit'])
     OR p_request->>'limit' !~ '^\d{1,2}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    effective_now := (p_request->>'now')::timestamptz;
    claim_limit := (p_request->>'limit')::int;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  IF claim_limit NOT BETWEEN 1 AND 20 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  FOR publication_row IN
    SELECT * FROM app.market_publications
    WHERE status = 'ready'
    ORDER BY created_at, id LIMIT claim_limit FOR UPDATE SKIP LOCKED
  LOOP
    SELECT telegram_chat_id INTO chat_value FROM app.telegram_links
    WHERE owner_id = publication_row.owner_id AND status = 'active';
    SELECT CASE publication_row.phase
      WHEN 'pre-market' THEN pre_market_enabled
      WHEN 'intraday' THEN intraday_enabled
      WHEN 'post-market' THEN post_market_enabled
      ELSE false END
    INTO enabled_value FROM app.notification_preferences
    WHERE owner_id = publication_row.owner_id;
    IF chat_value IS NULL OR NOT coalesce(enabled_value, false) THEN
      UPDATE app.market_publications SET status = 'suppressed', updated_at = effective_now
      WHERE owner_id = publication_row.owner_id AND id = publication_row.id;
      CONTINUE;
    END IF;
    lease_uuid := extensions.gen_random_uuid();
    UPDATE app.market_publications SET
      status = 'sending', lease_token = lease_uuid,
      sending_started_at = effective_now, attempt_count = attempt_count + 1,
      error = NULL, updated_at = effective_now
    WHERE owner_id = publication_row.owner_id AND id = publication_row.id;
    publications := publications || jsonb_build_array(jsonb_build_object(
      'publication_id', publication_row.id, 'lease_token', lease_uuid,
      'chat_id', chat_value::text, 'parts', publication_row.rendered_parts
    ));
  END LOOP;
  RETURN jsonb_build_object('publications', publications);
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_finish_publication(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  publication_uuid UUID;
  lease_uuid UUID;
  status_value TEXT;
  message_ids JSONB;
  publication_row app.market_publications%ROWTYPE;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
       p_request, ARRAY['publication_id','lease_token','status','message_ids']
     ) OR p_request->>'status' NOT IN ('delivered','delivery_failed','delivery_unknown')
       OR jsonb_typeof(p_request->'message_ids') <> 'array'
       OR jsonb_array_length(p_request->'message_ids') > 4 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    publication_uuid := (p_request->>'publication_id')::uuid;
    lease_uuid := (p_request->>'lease_token')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  status_value := p_request->>'status';
  message_ids := p_request->'message_ids';
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(message_ids) item
    WHERE jsonb_typeof(item) <> 'number' OR (item::text)::bigint <= 0
  ) OR (status_value = 'delivered' AND jsonb_array_length(message_ids) = 0)
     OR (status_value = 'delivery_failed' AND jsonb_array_length(message_ids) <> 0) THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO publication_row FROM app.market_publications
  WHERE id = publication_uuid FOR UPDATE;
  IF NOT FOUND OR publication_row.status <> 'sending'
     OR publication_row.lease_token <> lease_uuid THEN
    RAISE EXCEPTION 'publication lease unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.market_publications SET
    status = status_value, telegram_message_ids = message_ids, lease_token = NULL,
    delivered_at = CASE WHEN status_value = 'delivered' THEN clock_timestamp() ELSE NULL END,
    error = CASE status_value
      WHEN 'delivery_failed' THEN 'TELEGRAM_REJECTED'
      WHEN 'delivery_unknown' THEN 'TELEGRAM_OUTCOME_UNKNOWN'
      ELSE NULL END,
    updated_at = clock_timestamp()
  WHERE owner_id = publication_row.owner_id AND id = publication_uuid;
  RETURN jsonb_build_object(
    'status', status_value, 'publication_id', publication_uuid,
    'telegram_message_ids', message_ids
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_claim_operational_alerts(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  effective_now TIMESTAMPTZ;
  claim_limit INT;
  alert_row app.operational_alerts%ROWTYPE;
  lease_uuid UUID;
  chat_value BIGINT;
  enabled_value BOOLEAN;
  alerts JSONB := '[]'::jsonb;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['now','limit'])
     OR p_request->>'limit' !~ '^\d{1,2}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    effective_now := (p_request->>'now')::timestamptz;
    claim_limit := (p_request->>'limit')::int;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  IF claim_limit NOT BETWEEN 1 AND 20 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  FOR alert_row IN
    SELECT * FROM app.operational_alerts
    WHERE status = 'ready'
    ORDER BY created_at, id LIMIT claim_limit FOR UPDATE SKIP LOCKED
  LOOP
    SELECT operational_enabled INTO enabled_value FROM app.notification_preferences
    WHERE owner_id = alert_row.owner_id;
    SELECT telegram_chat_id INTO chat_value FROM app.telegram_links
    WHERE owner_id = alert_row.owner_id AND status = 'active';
    IF chat_value IS NULL OR NOT coalesce(enabled_value, false) THEN
      UPDATE app.operational_alerts SET status = 'suppressed', updated_at = effective_now
      WHERE owner_id = alert_row.owner_id AND id = alert_row.id;
      UPDATE app.operational_events SET status = 'resolved', updated_at = effective_now
      WHERE owner_id = alert_row.owner_id AND id = alert_row.event_id;
      CONTINUE;
    END IF;
    lease_uuid := extensions.gen_random_uuid();
    UPDATE app.operational_alerts SET
      status = 'sending', lease_token = lease_uuid,
      sending_started_at = effective_now, attempt_count = attempt_count + 1,
      error_code = NULL, updated_at = effective_now
    WHERE owner_id = alert_row.owner_id AND id = alert_row.id;
    alerts := alerts || jsonb_build_array(jsonb_build_object(
      'alert_id', alert_row.id, 'lease_token', lease_uuid,
      'chat_id', chat_value::text, 'code', alert_row.code
    ));
  END LOOP;
  RETURN jsonb_build_object('alerts', alerts);
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_finish_operational_alert(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  alert_uuid UUID;
  lease_uuid UUID;
  status_value TEXT;
  message_ids JSONB;
  alert_row app.operational_alerts%ROWTYPE;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
       p_request, ARRAY['alert_id','lease_token','status','message_ids']
     ) OR p_request->>'status' NOT IN ('delivered','delivery_failed','delivery_unknown')
       OR jsonb_typeof(p_request->'message_ids') <> 'array'
       OR jsonb_array_length(p_request->'message_ids') > 1 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    alert_uuid := (p_request->>'alert_id')::uuid;
    lease_uuid := (p_request->>'lease_token')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  status_value := p_request->>'status';
  message_ids := p_request->'message_ids';
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(message_ids) item
    WHERE jsonb_typeof(item) <> 'number' OR (item::text)::bigint <= 0
  ) OR (status_value = 'delivered' AND jsonb_array_length(message_ids) <> 1)
     OR (status_value = 'delivery_failed' AND jsonb_array_length(message_ids) <> 0) THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO alert_row FROM app.operational_alerts
  WHERE id = alert_uuid FOR UPDATE;
  IF NOT FOUND OR alert_row.status <> 'sending' OR alert_row.lease_token <> lease_uuid THEN
    RAISE EXCEPTION 'operational alert lease unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.operational_alerts SET
    status = status_value, telegram_message_ids = message_ids, lease_token = NULL,
    delivered_at = CASE WHEN status_value = 'delivered' THEN clock_timestamp() ELSE NULL END,
    error_code = CASE status_value
      WHEN 'delivery_failed' THEN 'TELEGRAM_REJECTED'
      WHEN 'delivery_unknown' THEN 'TELEGRAM_OUTCOME_UNKNOWN'
      ELSE NULL END,
    updated_at = clock_timestamp()
  WHERE owner_id = alert_row.owner_id AND id = alert_uuid;
  UPDATE app.operational_events SET
    status = CASE WHEN status_value = 'delivered' THEN 'notified' ELSE 'open' END,
    updated_at = clock_timestamp()
  WHERE owner_id = alert_row.owner_id AND id = alert_row.event_id;
  RETURN jsonb_build_object(
    'status', status_value, 'alert_id', alert_uuid,
    'telegram_message_ids', message_ids
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_read_due_decisions(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  effective_now TIMESTAMPTZ;
  claim_limit INT;
  due_rows JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['now','limit'])
     OR p_request->>'limit' !~ '^\d{1,2}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    effective_now := (p_request->>'now')::timestamptz;
    claim_limit := (p_request->>'limit')::int;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  IF claim_limit NOT BETWEEN 1 AND 20
     OR effective_now < clock_timestamp() - interval '1 day'
     OR effective_now > clock_timestamp() + interval '5 minutes' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  SELECT coalesce(jsonb_agg(row_value ORDER BY decision_date, suggestion_id), '[]'::jsonb)
  INTO due_rows
  FROM (
    SELECT suggestion.date AS decision_date, suggestion.id AS suggestion_id,
      jsonb_build_object(
        'owner_id', suggestion.owner_id,
        'suggestion_id', suggestion.id,
        'decision_date', suggestion.date,
        'ticker', suggestion.ticker,
        'bucket', suggestion.bucket,
        'final_action', evaluation.final_action,
        'confidence', suggestion.confidence,
        'policy_version', evaluation.policy_version,
        'decision_price', suggestion.price_at_suggestion::text,
        'entry_zone_low', suggestion.entry_zone_low::text,
        'entry_zone_high', suggestion.entry_zone_high::text,
        'stop', suggestion.stop::text,
        'target', suggestion.target::text,
        'invalidation_price', suggestion.invalidation_price::text,
        'completed_horizons', coalesce(
          to_jsonb(array_agg(DISTINCT grade.horizon_days ORDER BY grade.horizon_days)
            FILTER (WHERE grade.coverage_status = 'complete'
              OR (grade.graded_at AT TIME ZONE 'America/New_York')::date
                 = (effective_now AT TIME ZONE 'America/New_York')::date)),
          '[]'::jsonb
        )
      ) AS row_value
    FROM app.suggestions AS suggestion
    JOIN app.decision_evaluations AS evaluation
      ON evaluation.owner_id = suggestion.owner_id AND evaluation.id = suggestion.evaluation_id
    LEFT JOIN app.suggestion_grades AS grade
      ON grade.owner_id = suggestion.owner_id AND grade.suggestion_id = suggestion.id
    JOIN app.profiles AS profile
      ON profile.id = suggestion.owner_id AND profile.status = 'active'
    WHERE suggestion.decision_source = 'gateway'
      AND evaluation.policy_version IS NOT NULL
      AND suggestion.bucket IN ('core','growth','speculative')
      AND suggestion.confidence IN ('low','medium','high')
      AND suggestion.price_at_suggestion IS NOT NULL
      AND evaluation.final_action IN ('buy','add','hold','reduce','sell','watch','avoid')
      AND suggestion.date >= (effective_now AT TIME ZONE 'America/New_York')::date - 370
    GROUP BY suggestion.owner_id, suggestion.id, suggestion.date, suggestion.ticker,
      suggestion.bucket, evaluation.final_action, suggestion.confidence,
      evaluation.policy_version, suggestion.price_at_suggestion,
      suggestion.entry_zone_low, suggestion.entry_zone_high, suggestion.stop,
      suggestion.target, suggestion.invalidation_price
    HAVING count(DISTINCT grade.horizon_days)
      FILTER (WHERE grade.coverage_status = 'complete' AND grade.horizon_days IN (5,21,63)) < 3
      AND count(DISTINCT grade.horizon_days)
      FILTER (WHERE grade.coverage_status <> 'complete'
        AND (grade.graded_at AT TIME ZONE 'America/New_York')::date
          = (effective_now AT TIME ZONE 'America/New_York')::date) < 3
    ORDER BY suggestion.date, suggestion.id
    LIMIT claim_limit
  ) due;
  RETURN jsonb_build_object('due', due_rows);
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_apply_outcome_grades(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  grade_item JSONB;
  owner_uuid UUID;
  suggestion_value BIGINT;
  existing_grade app.suggestion_grades%ROWTYPE;
  ticker_value TEXT;
  decision_price_value NUMERIC;
  policy_version_value INT;
  final_action_value TEXT;
  horizon_value INT;
  sessions_value INT;
  status_value TEXT;
  expected_benchmark TEXT;
  numeric_key TEXT;
  had_existing BOOLEAN;
  inserted_count INT := 0;
  updated_count INT := 0;
  incomplete_count INT := 0;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['grades'])
     OR jsonb_typeof(p_request->'grades') <> 'array'
     OR jsonb_array_length(p_request->'grades') > 60 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  FOR grade_item IN SELECT value FROM jsonb_array_elements(p_request->'grades')
  LOOP
    IF jsonb_typeof(grade_item) <> 'object'
       OR NOT app.jsonb_has_exact_keys(grade_item, ARRAY[
         'owner_id','suggestion_id','horizon_days','horizon_sessions','coverage_status',
         'benchmark_ticker','stock_return_pct','benchmark_return_pct','excess_return_pct',
         'mfe_pct','mae_pct','entry_hit_at','stop_hit_at','target_hit_at',
         'invalidation_hit_at','policy_version','final_action','direction_success'
       ])
       OR grade_item->>'suggestion_id' !~ '^[1-9][0-9]*$' THEN
      RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
    END IF;
    BEGIN
      owner_uuid := (grade_item->>'owner_id')::uuid;
      suggestion_value := (grade_item->>'suggestion_id')::bigint;
      horizon_value := (grade_item->>'horizon_days')::int;
      sessions_value := (grade_item->>'horizon_sessions')::int;
      policy_version_value := (grade_item->>'policy_version')::int;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR null_value_not_allowed THEN
      RAISE EXCEPTION 'invalid outcome grade' USING ERRCODE = '22023';
    END;
    status_value := grade_item->>'coverage_status';
    IF horizon_value NOT IN (5, 21, 63)
       OR sessions_value NOT BETWEEN 0 AND horizon_value
       OR status_value NOT IN (
         'incomplete','complete','missing_history','missing_benchmark','corporate_action_review'
       )
       OR (status_value = 'complete' AND sessions_value <> horizon_value)
       OR grade_item->>'benchmark_ticker' NOT IN ('VOO','VXUS')
       OR jsonb_typeof(grade_item->'direction_success') NOT IN ('boolean','null')
       OR policy_version_value < 1
       OR grade_item->>'final_action' NOT IN ('buy','add','hold','reduce','sell','watch','avoid')
       OR EXISTS (
         SELECT 1 FROM unnest(ARRAY[
           grade_item->>'entry_hit_at', grade_item->>'stop_hit_at',
           grade_item->>'target_hit_at', grade_item->>'invalidation_hit_at'
         ]) date_text
         WHERE date_text IS NOT NULL AND date_text !~ '^\d{4}-\d{2}-\d{2}$'
       ) THEN
      RAISE EXCEPTION 'invalid outcome grade' USING ERRCODE = '22023';
    END IF;
    FOREACH numeric_key IN ARRAY ARRAY[
      'stock_return_pct','benchmark_return_pct','excess_return_pct','mfe_pct','mae_pct'
    ]
    LOOP
      IF grade_item->>numeric_key IS NOT NULL
         AND (grade_item->>numeric_key !~ '^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$'
           OR char_length(grade_item->>numeric_key) > 40) THEN
        RAISE EXCEPTION 'invalid outcome decimal' USING ERRCODE = '22023';
      END IF;
    END LOOP;

    SELECT suggestion.ticker, suggestion.price_at_suggestion,
           evaluation.policy_version, evaluation.final_action
    INTO ticker_value, decision_price_value, policy_version_value, final_action_value
    FROM app.suggestions AS suggestion
    JOIN app.decision_evaluations AS evaluation
      ON evaluation.owner_id = suggestion.owner_id
     AND evaluation.id = suggestion.evaluation_id
    WHERE suggestion.owner_id = owner_uuid
      AND suggestion.id = suggestion_value
      AND suggestion.decision_source = 'gateway';
    IF NOT FOUND
       OR policy_version_value IS DISTINCT FROM (grade_item->>'policy_version')::int
       OR final_action_value IS DISTINCT FROM grade_item->>'final_action' THEN
      RAISE EXCEPTION 'outcome provenance mismatch' USING ERRCODE = '22023';
    END IF;
    expected_benchmark := CASE WHEN ticker_value = 'VXUS' THEN 'VXUS' ELSE 'VOO' END;
    IF grade_item->>'benchmark_ticker' <> expected_benchmark THEN
      RAISE EXCEPTION 'outcome benchmark mismatch' USING ERRCODE = '22023';
    END IF;
    IF status_value = 'corporate_action_review' AND (
      grade_item->>'entry_hit_at' IS NOT NULL OR grade_item->>'stop_hit_at' IS NOT NULL
      OR grade_item->>'target_hit_at' IS NOT NULL
      OR grade_item->>'invalidation_hit_at' IS NOT NULL
    ) THEN
      RAISE EXCEPTION 'split outcome contains raw threshold result' USING ERRCODE = '22023';
    END IF;
    IF status_value IN ('missing_history','missing_benchmark') AND (
      grade_item->>'stock_return_pct' IS NOT NULL
      OR grade_item->>'benchmark_return_pct' IS NOT NULL
      OR grade_item->>'excess_return_pct' IS NOT NULL
      OR grade_item->>'mfe_pct' IS NOT NULL OR grade_item->>'mae_pct' IS NOT NULL
      OR grade_item->>'entry_hit_at' IS NOT NULL OR grade_item->>'stop_hit_at' IS NOT NULL
      OR grade_item->>'target_hit_at' IS NOT NULL
      OR grade_item->>'invalidation_hit_at' IS NOT NULL
      OR grade_item->'direction_success' <> 'null'::jsonb
    ) THEN
      RAISE EXCEPTION 'missing outcome contains result' USING ERRCODE = '22023';
    END IF;
    IF status_value = 'complete' AND (
      grade_item->>'stock_return_pct' IS NULL
      OR grade_item->>'benchmark_return_pct' IS NULL
      OR grade_item->>'excess_return_pct' IS NULL
      OR grade_item->>'mfe_pct' IS NULL OR grade_item->>'mae_pct' IS NULL
    ) THEN
      RAISE EXCEPTION 'complete outcome missing result' USING ERRCODE = '22023';
    END IF;
    IF grade_item->'direction_success' <> 'null'::jsonb AND (
      status_value <> 'complete' OR final_action_value NOT IN ('buy','add','reduce','sell')
    ) THEN
      RAISE EXCEPTION 'invalid directional outcome' USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(suggestion_value);
    SELECT * INTO existing_grade
    FROM app.suggestion_grades
    WHERE owner_id = owner_uuid AND suggestion_id = suggestion_value
      AND horizon_days = horizon_value
    FOR UPDATE;
    had_existing := FOUND;
    IF had_existing AND existing_grade.coverage_status = 'complete' THEN
      CONTINUE;
    END IF;

    INSERT INTO app.suggestion_grades(
      owner_id, suggestion_id, graded_at, result, price_then, horizon_days,
      benchmark_ticker, stock_return_pct, benchmark_return_pct, excess_return_pct,
      mfe_pct, mae_pct, entry_hit_at, stop_hit_at, target_hit_at,
      invalidation_hit_at, coverage_status, horizon_sessions, policy_version,
      final_action, direction_success, note
    ) VALUES (
      owner_uuid, suggestion_value, clock_timestamp(),
      CASE WHEN grade_item->'direction_success' = 'true'::jsonb THEN 'right'
           WHEN grade_item->'direction_success' = 'false'::jsonb THEN 'wrong'
           ELSE NULL END,
      decision_price_value, horizon_value, expected_benchmark,
      NULLIF(grade_item->>'stock_return_pct','')::numeric,
      NULLIF(grade_item->>'benchmark_return_pct','')::numeric,
      NULLIF(grade_item->>'excess_return_pct','')::numeric,
      NULLIF(grade_item->>'mfe_pct','')::numeric,
      NULLIF(grade_item->>'mae_pct','')::numeric,
      NULLIF(grade_item->>'entry_hit_at','')::date,
      NULLIF(grade_item->>'stop_hit_at','')::date,
      NULLIF(grade_item->>'target_hit_at','')::date,
      NULLIF(grade_item->>'invalidation_hit_at','')::date,
      status_value, sessions_value, policy_version_value, final_action_value,
      CASE WHEN grade_item->'direction_success' = 'null'::jsonb THEN NULL
           ELSE (grade_item->>'direction_success')::boolean END,
      'deterministic scheduler outcome'
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
    IF had_existing THEN
      updated_count := updated_count + 1;
    ELSE
      inserted_count := inserted_count + 1;
    END IF;
    IF status_value <> 'complete' THEN
      incomplete_count := incomplete_count + 1;
    END IF;
  END LOOP;
  RETURN jsonb_build_object(
    'inserted', inserted_count, 'updated', updated_count, 'incomplete', incomplete_count
  );
END
$$;

CREATE POLICY market_publications_executor_all ON app.market_publications
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

ALTER FUNCTION machine.scheduler_claim_due_slots(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_read_trigger_secret(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_record_trigger_result(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_publish_holiday(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_run_maintenance(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_claim_publications(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_finish_publication(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_claim_operational_alerts(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_finish_operational_alert(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_read_due_decisions(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_apply_outcome_grades(JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION machine.scheduler_claim_due_slots(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_read_trigger_secret(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_record_trigger_result(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_publish_holiday(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_run_maintenance(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_claim_publications(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_finish_publication(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_claim_operational_alerts(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_finish_operational_alert(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_read_due_decisions(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_apply_outcome_grades(JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION machine.scheduler_claim_due_slots(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_read_trigger_secret(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_record_trigger_result(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_publish_holiday(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_run_maintenance(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_claim_publications(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_finish_publication(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_claim_operational_alerts(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_finish_operational_alert(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_read_due_decisions(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_apply_outcome_grades(JSONB) TO stock_agent_scheduler;

-- User-owned provider connection lifecycle. Plain inbound secrets never enter SQL; the
-- outbound Routine token is accepted once and immediately moved into Vault.

CREATE TABLE machine.connection_policy (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  current_consent_version TEXT NOT NULL CHECK (char_length(current_consent_version) BETWEEN 3 AND 100),
  contract_version INT NOT NULL CHECK (contract_version = 2)
);
ALTER TABLE machine.connection_policy OWNER TO stock_agent_migration_owner;
REVOKE ALL ON machine.connection_policy FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
INSERT INTO machine.connection_policy(singleton, current_consent_version, contract_version)
VALUES (true, 'provider-data-v1', 2);

GRANT EXECUTE ON FUNCTION vault.create_secret(TEXT, TEXT, TEXT) TO stock_agent_migration_owner;
GRANT EXECUTE ON FUNCTION vault.delete_secret(UUID) TO stock_agent_migration_owner;

CREATE OR REPLACE FUNCTION app.create_agent_connection(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  consent_version_value TEXT;
  connection_uuid UUID := extensions.gen_random_uuid();
  public_uuid UUID := extensions.gen_random_uuid();
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['provider','consent_version','inbound_token_digest']
  ) OR p_request->>'provider' <> 'claude'
     OR p_request->>'inbound_token_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END IF;
  SELECT current_consent_version INTO consent_version_value
  FROM machine.connection_policy WHERE singleton;
  IF p_request->>'consent_version' <> consent_version_value
     OR NOT EXISTS (
       SELECT 1 FROM app.user_consents
       WHERE owner_id = p_owner_id AND document_version = consent_version_value
     ) OR NOT EXISTS (
       SELECT 1 FROM app.profiles WHERE id = p_owner_id AND status = 'active'
     ) THEN
    RAISE EXCEPTION 'current provider consent is required' USING ERRCODE = '42501';
  END IF;
  INSERT INTO app.agent_connections(
    id, owner_id, public_id, provider, credential_type, inbound_token_digest,
    capabilities, contract_version, status
  ) VALUES (
    connection_uuid, p_owner_id, public_uuid, 'claude', 'claude_routine_v1',
    decode(p_request->>'inbound_token_digest', 'hex'),
    jsonb_build_object(
      'suggestion_only', true, 'bounded_context', true,
      'analyst_checker', true, 'contract_version', 2
    ), 2, 'disabled'
  );
  RETURN jsonb_build_object(
    'connection_id', connection_uuid, 'public_id', public_uuid,
    'provider', 'claude', 'status', 'disabled', 'contract_version', 2
  );
END
$$;

CREATE OR REPLACE FUNCTION app.begin_agent_connection_handshake(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_uuid UUID;
  connection_row app.agent_connections%ROWTYPE;
  vault_uuid UUID;
  previous_vault_uuid UUID;
  slot_uuid UUID;
  trigger_uuid UUID;
  consent_version_value TEXT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['connection_id','trigger_url','trigger_token']
  ) OR p_request->>'trigger_url' !~
       '^https://api\.anthropic\.com/v1/claude_code/routines/trig_[A-Za-z0-9]{6,128}/fire$'
     OR char_length(p_request->>'trigger_token') NOT BETWEEN 24 AND 500
     OR p_request->>'trigger_token' ~ '\s' THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END IF;
  BEGIN connection_uuid := (p_request->>'connection_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END;
  SELECT * INTO connection_row FROM app.agent_connections
  WHERE owner_id = p_owner_id AND id = connection_uuid FOR UPDATE;
  IF NOT FOUND OR connection_row.status NOT IN ('disabled','testing') THEN
    RAISE EXCEPTION 'connection unavailable' USING ERRCODE = '42501';
  END IF;
  IF connection_row.status = 'testing' THEN
    SELECT id, trigger_request_id INTO slot_uuid, trigger_uuid
    FROM app.scheduled_run_slots
    WHERE owner_id = p_owner_id AND connection_id = connection_uuid
      AND purpose = 'handshake'
      AND status IN ('pending','claimed','triggered','trigger_unknown','provider_started')
    ORDER BY created_at DESC LIMIT 1;
    IF FOUND THEN
      RETURN jsonb_build_object('connection_id', connection_uuid, 'status', 'testing',
        'handshake_id', slot_uuid, 'trigger_request_id', trigger_uuid, 'duplicate', true);
    END IF;
  END IF;
  SELECT current_consent_version INTO consent_version_value
  FROM machine.connection_policy WHERE singleton;
  IF NOT EXISTS (
    SELECT 1 FROM app.user_consents
    WHERE owner_id = p_owner_id AND document_version = consent_version_value
  ) THEN
    RAISE EXCEPTION 'current provider consent is required' USING ERRCODE = '42501';
  END IF;
  vault_uuid := vault.create_secret(
    p_request->>'trigger_token', 'routine-trigger-' || connection_uuid::text,
    'User-owned Claude Routine trigger token'
  );
  previous_vault_uuid := connection_row.outbound_trigger_secret_id;
  UPDATE app.agent_connections SET
    outbound_trigger_secret_id = vault_uuid,
    trigger_url = p_request->>'trigger_url', status = 'testing',
    updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND id = connection_uuid;
  IF previous_vault_uuid IS NOT NULL THEN
    PERFORM vault.delete_secret(previous_vault_uuid);
  END IF;
  slot_uuid := extensions.gen_random_uuid();
  trigger_uuid := extensions.gen_random_uuid();
  INSERT INTO app.scheduled_run_slots(
    id, owner_id, connection_id, market_date, phase, purpose, handshake_challenge, due_at,
    window_ends_at, trigger_request_id, status
  ) VALUES (
    slot_uuid, p_owner_id, connection_uuid,
    (clock_timestamp() AT TIME ZONE 'America/New_York')::date,
    'on-demand', 'handshake', extensions.gen_random_bytes(32), clock_timestamp(),
    clock_timestamp() + interval '1 hour', trigger_uuid, 'pending'
  );
  RETURN jsonb_build_object('connection_id', connection_uuid, 'status', 'testing',
    'handshake_id', slot_uuid, 'trigger_request_id', trigger_uuid, 'duplicate', false);
END
$$;

CREATE OR REPLACE FUNCTION app.activate_agent_connection(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_uuid UUID;
  consent_version_value TEXT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['connection_id']) THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END IF;
  BEGIN connection_uuid := (p_request->>'connection_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END;
  SELECT current_consent_version INTO consent_version_value
  FROM machine.connection_policy WHERE singleton;
  IF NOT EXISTS (
    SELECT 1 FROM app.user_consents
    WHERE owner_id = p_owner_id AND document_version = consent_version_value
  ) OR NOT EXISTS (
    SELECT 1 FROM app.agent_connections
    WHERE owner_id = p_owner_id AND id = connection_uuid AND status = 'ready'
      AND last_handshake_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'connection unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.agent_connections SET status = 'ready', updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND id <> connection_uuid AND status = 'active';
  UPDATE app.agent_connections SET status = 'active', updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND id = connection_uuid;
  INSERT INTO app.analysis_schedules(owner_id, primary_connection_id)
  VALUES (p_owner_id, connection_uuid)
  ON CONFLICT (owner_id) DO UPDATE SET primary_connection_id = excluded.primary_connection_id,
    updated_at = clock_timestamp();
  RETURN jsonb_build_object('connection_id', connection_uuid, 'status', 'active');
END
$$;

CREATE OR REPLACE FUNCTION app.revoke_agent_connection(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_uuid UUID;
  vault_uuid UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['connection_id']) THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END IF;
  BEGIN connection_uuid := (p_request->>'connection_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END;
  SELECT outbound_trigger_secret_id INTO vault_uuid
  FROM app.agent_connections WHERE owner_id = p_owner_id AND id = connection_uuid FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'connection unavailable' USING ERRCODE = '42501'; END IF;
  UPDATE app.analysis_schedules SET primary_connection_id = NULL, updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND primary_connection_id = connection_uuid;
  UPDATE app.scheduled_run_slots SET status = 'missed', updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND connection_id = connection_uuid
    AND status IN ('pending','claimed','triggered','trigger_unknown');
  UPDATE app.routine_trigger_attempts SET status = 'trigger_failed', finished_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND connection_id = connection_uuid AND status = 'claimed';
  UPDATE app.agent_connections SET status = 'revoked', inbound_token_digest = NULL,
    outbound_trigger_secret_id = NULL, trigger_url = NULL, updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND id = connection_uuid;
  IF vault_uuid IS NOT NULL THEN PERFORM vault.delete_secret(vault_uuid); END IF;
  RETURN jsonb_build_object('connection_id', connection_uuid, 'status', 'revoked');
END
$$;

ALTER FUNCTION app.create_agent_connection(UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.begin_agent_connection_handshake(UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.activate_agent_connection(UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.revoke_agent_connection(UUID, JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.create_agent_connection(UUID, JSONB) FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION app.begin_agent_connection_handshake(UUID, JSONB) FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION app.activate_agent_connection(UUID, JSONB) FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION app.revoke_agent_connection(UUID, JSONB) FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

-- END REVIEWED MIGRATION: sql/migrations/20260908040000_provider_runs_and_evidence.sql

-- BEGIN REVIEWED MIGRATION: sql/migrations/20260909000000_research_run_api.sql

-- Owner-visible research/run transparency and a rate-limited on-demand trigger request.

-- Web-requested runs are distinct from canonical scheduled slots and connection handshakes.
ALTER TABLE app.scheduled_run_slots
  DROP CONSTRAINT scheduled_run_slots_purpose_check;
ALTER TABLE app.scheduled_run_slots
  ADD CONSTRAINT scheduled_run_slots_purpose_check
  CHECK (purpose IN ('scheduled','handshake','on_demand'));
ALTER TABLE app.scheduled_run_slots
  DROP CONSTRAINT scheduled_run_slots_check2;
ALTER TABLE app.scheduled_run_slots
  ADD CONSTRAINT scheduled_run_slots_check2 CHECK (
    (purpose = 'handshake' AND phase = 'on-demand' AND NOT holiday)
    OR (purpose = 'on_demand' AND phase = 'on-demand' AND NOT holiday)
    OR (purpose = 'scheduled' AND phase <> 'on-demand')
  );
ALTER TABLE app.scheduled_run_slots
  DROP CONSTRAINT scheduled_run_slots_check3;
ALTER TABLE app.scheduled_run_slots
  ADD CONSTRAINT scheduled_run_slots_check3 CHECK (
    (purpose = 'handshake' AND octet_length(handshake_challenge) = 32)
    OR (purpose IN ('scheduled','on_demand') AND handshake_challenge IS NULL)
  );

CREATE POLICY decision_evaluations_owner_select ON app.decision_evaluations
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY market_publications_owner_select ON app.market_publications
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY suggestion_grades_owner_select ON app.suggestion_grades
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY run_evidence_owner_select ON app.run_evidence
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY agent_analysis_submissions_owner_select ON app.agent_analysis_submissions
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY scheduled_run_slots_owner_select ON app.scheduled_run_slots
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY routine_trigger_attempts_owner_select ON app.routine_trigger_attempts
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY profiles_executor_all ON app.profiles
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

CREATE OR REPLACE VIEW api.settings WITH (security_invoker = true) AS
SELECT profile.display_name,
       profile.timezone,
       coalesce(notification.pre_market_enabled, true) AS notify_pre_market,
       coalesce(notification.intraday_enabled, true) AS notify_intraday,
       coalesce(notification.post_market_enabled, true) AS notify_post_market,
       coalesce(notification.operational_enabled, true) AS notify_operational,
       schedule.primary_connection_id,
       coalesce(schedule.timezone, profile.timezone) AS schedule_timezone,
       coalesce(schedule.pre_market_enabled, true) AS schedule_pre_market,
       coalesce(schedule.intraday_enabled, true) AS schedule_intraday,
       coalesce(schedule.post_market_enabled, true) AS schedule_post_market
FROM app.profiles AS profile
LEFT JOIN app.notification_preferences AS notification ON notification.owner_id = profile.id
LEFT JOIN app.analysis_schedules AS schedule ON schedule.owner_id = profile.id;
ALTER VIEW api.settings OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.settings FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.settings TO authenticated;

GRANT SELECT (owner_id, id, run_id, ts, date, ticker, action, bucket, depth,
              entry_zone_low, entry_zone_high, valid_until, stop, target,
              confidence, bull, bear, decisive_factor, risk_verdict,
              invalidation_level, reason, risk_band, price_at_suggestion,
              evidence_as_of, evaluation_id, invalidation_price, decision_mode)
  ON app.suggestions TO authenticated;
GRANT SELECT (owner_id, id, run_id, raw_action, final_action, policy_status,
              reason_codes, explanations, analyst, checker, policy_version, created_at)
  ON app.decision_evaluations TO authenticated;
GRANT SELECT (owner_id, id, run_id, market_date, phase, kind, status,
              telegram_message_ids, delivered_at, created_at, updated_at)
  ON app.market_publications TO authenticated;
GRANT SELECT (owner_id, suggestion_id, graded_at, result, horizon_days,
              benchmark_ticker, stock_return_pct, benchmark_return_pct,
              excess_return_pct, mfe_pct, mae_pct, entry_hit_at, stop_hit_at,
              target_hit_at, invalidation_hit_at, coverage_status,
              horizon_sessions, final_action, direction_success)
  ON app.suggestion_grades TO authenticated;
GRANT SELECT (owner_id, run_id, evidence_id, category, source_identifier,
              reference_identifier, observed_at, retrieved_at, revalidated_at,
              claims, status)
  ON app.run_evidence TO authenticated;
GRANT SELECT (owner_id, run_id, provider, model, phase, market_date, status, created_at)
  ON app.agent_analysis_submissions TO authenticated;
GRANT SELECT (owner_id, id, connection_id, market_date, phase, purpose, due_at,
              window_ends_at, holiday, canonical_run_id, status, created_at, updated_at)
  ON app.scheduled_run_slots TO authenticated;
GRANT SELECT (owner_id, id, slot_id, connection_id, status, response_status,
              provider_session_url, claimed_at, finished_at)
  ON app.routine_trigger_attempts TO authenticated;

CREATE VIEW api.research WITH (security_invoker = true) AS
SELECT suggestion.id::text AS id,
       suggestion.run_id,
       run.market_date,
       run.kind AS phase,
       run.status AS run_status,
       run.provider,
       run.model,
       suggestion.ts AS created_at,
       suggestion.ticker,
       coalesce(evaluation.final_action, suggestion.action) AS action,
       evaluation.raw_action,
       evaluation.policy_status,
       coalesce(evaluation.reason_codes, '[]'::jsonb) AS policy_reason_codes,
       coalesce(evaluation.explanations, '[]'::jsonb) AS policy_explanations,
       CASE WHEN octet_length(evaluation.analyst::text) <= 16000
            THEN evaluation.analyst ELSE '{"completed":false}'::jsonb END AS analyst,
       CASE WHEN octet_length(evaluation.checker::text) <= 16000
            THEN evaluation.checker ELSE '{"completed":false}'::jsonb END AS checker,
       suggestion.confidence,
       suggestion.price_at_suggestion::text AS verified_price,
       suggestion.evidence_as_of,
       suggestion.entry_zone_low::text AS entry_zone_low,
       suggestion.entry_zone_high::text AS entry_zone_high,
       suggestion.stop::text AS stop,
       suggestion.target::text AS target,
       suggestion.invalidation_price::text AS invalidation_price,
       suggestion.valid_until,
       suggestion.depth AS horizon,
       suggestion.bucket,
       suggestion.risk_verdict,
       left(suggestion.decisive_factor, 4000) AS decisive_factor,
       left(suggestion.reason, 4000) AS reason,
       left(suggestion.bull, 4000) AS bull_case,
       left(suggestion.bear, 4000) AS bear_case,
       CASE
         WHEN action.state IN ('suspected','needs_review') THEN 'corporate_action_pending'
         WHEN EXISTS (
           SELECT 1 FROM app.run_evidence conflict
           WHERE conflict.owner_id = suggestion.owner_id
             AND conflict.run_id = suggestion.run_id AND conflict.status = 'conflicting'
         ) THEN 'source_conflict'
         WHEN EXISTS (
           SELECT 1 FROM app.run_evidence stale
           WHERE stale.owner_id = suggestion.owner_id
             AND stale.run_id = suggestion.run_id AND stale.status = 'stale'
         ) THEN 'stale'
         WHEN suggestion.run_id IS NULL OR NOT EXISTS (
           SELECT 1 FROM app.run_evidence present
           WHERE present.owner_id = suggestion.owner_id AND present.run_id = suggestion.run_id
         ) THEN 'missing'
         ELSE 'fresh'
       END AS evidence_status,
       coalesce(evidence.items, '[]'::jsonb) AS evidence,
       publication.kind AS publication_kind,
       publication.status AS notification_status,
       publication.delivered_at,
       CASE WHEN publication.status = 'delivered'
            THEN coalesce((
              SELECT jsonb_agg(message_id #>> '{}')
              FROM jsonb_array_elements(publication.telegram_message_ids) AS message_id
            ), '[]'::jsonb)
            ELSE '[]'::jsonb END AS telegram_message_ids,
       CASE publication.status
         WHEN 'delivery_failed' THEN 'TELEGRAM_REJECTED'
         WHEN 'delivery_unknown' THEN 'TELEGRAM_OUTCOME_UNKNOWN'
         ELSE NULL
       END AS delivery_error_code,
       coalesce(outcome.items, '[]'::jsonb) AS outcomes
FROM app.suggestions AS suggestion
LEFT JOIN app.analysis_runs AS run
  ON run.owner_id = suggestion.owner_id AND run.id = suggestion.run_id
LEFT JOIN app.decision_evaluations AS evaluation
  ON evaluation.owner_id = suggestion.owner_id AND evaluation.id = suggestion.evaluation_id
LEFT JOIN app.market_publications AS publication
  ON publication.owner_id = suggestion.owner_id AND publication.run_id = suggestion.run_id
LEFT JOIN app.corporate_action_states AS action
  ON action.owner_id = suggestion.owner_id AND action.ticker = suggestion.ticker
LEFT JOIN LATERAL (
  SELECT jsonb_agg(jsonb_build_object(
    'evidence_id', bounded.evidence_id,
    'category', bounded.category,
    'source', bounded.source_identifier,
    'reference', bounded.reference_identifier,
    'observed_at', bounded.observed_at,
    'retrieved_at', bounded.retrieved_at,
    'revalidated_at', bounded.revalidated_at,
    'claims', bounded.claims,
    'status', bounded.status
  ) ORDER BY bounded.retrieved_at DESC, bounded.evidence_id) AS items
  FROM (
    SELECT evidence.evidence_id, evidence.category, evidence.source_identifier,
           evidence.reference_identifier, evidence.observed_at, evidence.retrieved_at,
           evidence.revalidated_at, evidence.claims, evidence.status
    FROM app.run_evidence AS evidence
    WHERE evidence.owner_id = suggestion.owner_id AND evidence.run_id = suggestion.run_id
    ORDER BY evidence.retrieved_at DESC, evidence.evidence_id
    LIMIT 25
  ) AS bounded
) AS evidence ON true
LEFT JOIN LATERAL (
  SELECT jsonb_agg(jsonb_build_object(
    'horizon_sessions', grade.horizon_days,
    'coverage_status', grade.coverage_status,
    'stock_return_pct', grade.stock_return_pct::text,
    'benchmark', grade.benchmark_ticker,
    'benchmark_return_pct', grade.benchmark_return_pct::text,
    'excess_return_pct', grade.excess_return_pct::text,
    'mfe_pct', grade.mfe_pct::text,
    'mae_pct', grade.mae_pct::text,
    'entry_hit_at', grade.entry_hit_at,
    'stop_hit_at', grade.stop_hit_at,
    'target_hit_at', grade.target_hit_at,
    'invalidation_hit_at', grade.invalidation_hit_at,
    'direction_success', grade.direction_success,
    'graded_at', grade.graded_at
  ) ORDER BY grade.horizon_days) AS items
  FROM app.suggestion_grades AS grade
  WHERE grade.owner_id = suggestion.owner_id
    AND grade.suggestion_id = suggestion.id AND grade.horizon_days IN (5,21,63)
) AS outcome ON true;

CREATE VIEW api.run_timeline WITH (security_invoker = true) AS
WITH timeline AS (
  SELECT slot.owner_id,
         slot.id AS slot_id,
         slot.market_date,
         slot.phase,
         slot.purpose,
         slot.due_at,
         slot.window_ends_at,
         slot.holiday,
         slot.status AS slot_status,
         slot.canonical_run_id AS run_id
  FROM app.scheduled_run_slots AS slot
  UNION ALL
  SELECT run.owner_id,
         NULL::uuid,
         run.market_date,
         run.kind,
         'provider_direct'::text,
         NULL::timestamptz,
         NULL::timestamptz,
         false,
         'provider_started'::text,
         run.id
  FROM app.analysis_runs AS run
  WHERE NOT EXISTS (
    SELECT 1 FROM app.scheduled_run_slots AS slot
    WHERE slot.owner_id = run.owner_id AND slot.canonical_run_id = run.id
  )
)
SELECT timeline.slot_id,
       timeline.market_date,
       timeline.phase,
       timeline.purpose,
       timeline.due_at AS expected_at,
       timeline.window_ends_at,
       timeline.holiday,
       timeline.slot_status,
       trigger.status AS trigger_status,
       trigger.response_status AS trigger_response_status,
       trigger.provider_session_url,
       trigger.claimed_at AS trigger_started_at,
       trigger.finished_at AS trigger_finished_at,
       run.id AS run_id,
       run.started_at,
       run.finished_at,
       run.status AS run_status,
       run.data_as_of,
       run.source_status,
       run.symbols,
       run.write_counts,
       run.summary,
       run.provider,
       run.model,
       submission.status AS submission_status,
       coalesce(policy.states, '[]'::jsonb) AS policy_states,
       CASE
         WHEN EXISTS (
           SELECT 1 FROM app.run_evidence evidence
           WHERE evidence.owner_id = timeline.owner_id
             AND evidence.run_id = timeline.run_id AND evidence.status = 'conflicting'
         ) THEN 'source_conflict'
         WHEN EXISTS (
           SELECT 1 FROM app.run_evidence evidence
           WHERE evidence.owner_id = timeline.owner_id
             AND evidence.run_id = timeline.run_id AND evidence.status = 'stale'
         ) THEN 'stale'
         WHEN timeline.run_id IS NULL THEN 'not_started'
         WHEN NOT EXISTS (
           SELECT 1 FROM app.run_evidence evidence
           WHERE evidence.owner_id = timeline.owner_id AND evidence.run_id = timeline.run_id
         ) THEN 'missing'
         ELSE 'fresh'
       END AS evidence_status,
       publication.kind AS publication_kind,
       publication.status AS publication_status,
       publication.delivered_at,
       CASE WHEN publication.status = 'delivered'
            THEN coalesce((
              SELECT jsonb_agg(message_id #>> '{}')
              FROM jsonb_array_elements(publication.telegram_message_ids) AS message_id
            ), '[]'::jsonb)
            ELSE '[]'::jsonb END AS telegram_message_ids,
       CASE
         WHEN timeline.slot_status = 'trigger_failed' THEN 'PROVIDER_TRIGGER_FAILED'
         WHEN timeline.slot_status = 'trigger_unknown' THEN 'TRIGGER_OUTCOME_UNKNOWN'
         WHEN timeline.slot_status = 'missed' THEN 'EXPECTED_RUN_MISSED'
         WHEN timeline.slot_status = 'budget_suppressed' THEN 'RUN_BUDGET_SUPPRESSED'
         WHEN run.status = 'partial' THEN 'RUN_PARTIAL'
         WHEN publication.status = 'delivery_failed' THEN 'TELEGRAM_REJECTED'
         WHEN publication.status = 'delivery_unknown' THEN 'TELEGRAM_OUTCOME_UNKNOWN'
         ELSE NULL
       END AS error_code
FROM timeline
LEFT JOIN app.routine_trigger_attempts AS trigger
  ON trigger.owner_id = timeline.owner_id AND trigger.slot_id = timeline.slot_id
LEFT JOIN app.analysis_runs AS run
  ON run.owner_id = timeline.owner_id AND run.id = timeline.run_id
LEFT JOIN LATERAL (
  SELECT submitted.status, submitted.created_at
  FROM app.agent_analysis_submissions AS submitted
  WHERE submitted.owner_id = timeline.owner_id AND submitted.run_id = timeline.run_id
  ORDER BY submitted.created_at DESC LIMIT 1
) AS submission ON true
LEFT JOIN LATERAL (
  SELECT jsonb_agg(DISTINCT evaluation.policy_status) AS states
  FROM app.decision_evaluations AS evaluation
  WHERE evaluation.owner_id = timeline.owner_id AND evaluation.run_id = timeline.run_id
) AS policy ON true
LEFT JOIN app.market_publications AS publication
  ON publication.owner_id = timeline.owner_id AND publication.run_id = timeline.run_id;

ALTER VIEW api.research OWNER TO stock_agent_migration_owner;
ALTER VIEW api.run_timeline OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.research, api.run_timeline FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.research, api.run_timeline TO authenticated;

CREATE OR REPLACE FUNCTION app.create_on_demand_run_slot(
  p_owner_id UUID,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_row app.agent_connections%ROWTYPE;
  observed_at TIMESTAMPTZ := clock_timestamp();
  market_day DATE := (observed_at AT TIME ZONE 'America/New_York')::date;
  slot_uuid UUID := extensions.gen_random_uuid();
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY[]::text[]) THEN
    RAISE EXCEPTION 'invalid run request' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_owner_id::text || ':on-demand', 0));
  SELECT connection.* INTO connection_row
  FROM app.analysis_schedules AS schedule
  JOIN app.agent_connections AS connection
    ON connection.owner_id = schedule.owner_id
   AND connection.id = schedule.primary_connection_id
  WHERE schedule.owner_id = p_owner_id
    AND connection.status = 'active'
    AND connection.provider = 'claude'
    AND connection.trigger_url IS NOT NULL
    AND connection.outbound_trigger_secret_id IS NOT NULL
  FOR UPDATE OF connection;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'connection unavailable' USING ERRCODE = '42501';
  END IF;
  IF EXISTS (
    SELECT 1 FROM app.scheduled_run_slots
    WHERE owner_id = p_owner_id AND purpose = 'on_demand'
      AND created_at >= observed_at - interval '1 hour'
  ) THEN
    RAISE EXCEPTION 'on-demand run already requested' USING ERRCODE = '40001';
  END IF;
  INSERT INTO app.scheduled_run_slots(
    id, owner_id, connection_id, market_date, phase, purpose,
    due_at, window_ends_at, holiday, status
  ) VALUES (
    slot_uuid, p_owner_id, connection_row.id, market_day, 'on-demand', 'on_demand',
    observed_at, observed_at + interval '20 minutes', false, 'pending'
  );
  RETURN jsonb_build_object(
    'status', 'queued',
    'slot_id', slot_uuid,
    'phase', 'on-demand',
    'market_date', market_day,
    'expected_by', observed_at + interval '20 minutes',
    'telegram', 'suppressed'
  );
END
$$;
ALTER FUNCTION app.create_on_demand_run_slot(UUID, JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.create_on_demand_run_slot(UUID, JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION app.read_owner_settings(
  p_owner_id UUID,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  result_value JSONB;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY[]::text[]) THEN
    RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
  END IF;
  SELECT jsonb_build_object(
    'display_name', coalesce(profile.display_name, 'Stock Agent owner'),
    'timezone', profile.timezone,
    'notify_pre_market', coalesce(notification.pre_market_enabled, true),
    'notify_intraday', coalesce(notification.intraday_enabled, true),
    'notify_post_market', coalesce(notification.post_market_enabled, true),
    'notify_operational', coalesce(notification.operational_enabled, true),
    'primary_connection_id', schedule.primary_connection_id,
    'schedule_timezone', coalesce(schedule.timezone, profile.timezone),
    'schedule_pre_market', coalesce(schedule.pre_market_enabled, true),
    'schedule_intraday', coalesce(schedule.intraday_enabled, true),
    'schedule_post_market', coalesce(schedule.post_market_enabled, true)
  ) INTO result_value
  FROM app.profiles AS profile
  LEFT JOIN app.notification_preferences AS notification ON notification.owner_id = profile.id
  LEFT JOIN app.analysis_schedules AS schedule ON schedule.owner_id = profile.id
  WHERE profile.id = p_owner_id AND profile.status = 'active';
  IF result_value IS NULL THEN
    RAISE EXCEPTION 'settings unavailable' USING ERRCODE = '42501';
  END IF;
  RETURN result_value;
END
$$;

CREATE OR REPLACE FUNCTION app.update_owner_settings(
  p_owner_id UUID,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  allowed_keys CONSTANT TEXT[] := ARRAY[
    'display_name','timezone','notify_pre_market','notify_intraday','notify_post_market',
    'notify_operational','schedule_pre_market','schedule_intraday','schedule_post_market'
  ];
  request_key TEXT;
  timezone_value TEXT;
BEGIN
  IF p_owner_id IS NULL OR jsonb_typeof(p_request) <> 'object'
     OR p_request = '{}'::jsonb THEN
    RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
  END IF;
  FOR request_key IN SELECT jsonb_object_keys(p_request) LOOP
    IF NOT request_key = ANY(allowed_keys) THEN
      RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
    END IF;
    IF request_key = 'display_name' THEN
      IF jsonb_typeof(p_request->request_key) <> 'string'
         OR char_length(btrim(p_request->>request_key)) NOT BETWEEN 1 AND 120 THEN
        RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
      END IF;
    ELSIF request_key = 'timezone' THEN
      IF jsonb_typeof(p_request->request_key) <> 'string'
         OR char_length(p_request->>request_key) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
      END IF;
    ELSIF jsonb_typeof(p_request->request_key) <> 'boolean' THEN
      RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
    END IF;
  END LOOP;

  SELECT timezone INTO timezone_value
  FROM app.profiles WHERE id = p_owner_id AND status = 'active' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'settings unavailable' USING ERRCODE = '42501'; END IF;
  IF p_request ? 'timezone' AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name = p_request->>'timezone'
  ) THEN
    RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
  END IF;
  IF p_request ? 'timezone' THEN timezone_value := p_request->>'timezone'; END IF;

  UPDATE app.profiles SET
    display_name = CASE WHEN p_request ? 'display_name' THEN btrim(p_request->>'display_name') ELSE display_name END,
    timezone = timezone_value,
    updated_at = clock_timestamp()
  WHERE id = p_owner_id;

  INSERT INTO app.notification_preferences(
    owner_id, pre_market_enabled, intraday_enabled, post_market_enabled, operational_enabled
  ) VALUES (
    p_owner_id,
    coalesce((p_request->>'notify_pre_market')::boolean, true),
    coalesce((p_request->>'notify_intraday')::boolean, true),
    coalesce((p_request->>'notify_post_market')::boolean, true),
    coalesce((p_request->>'notify_operational')::boolean, true)
  ) ON CONFLICT (owner_id) DO UPDATE SET
    pre_market_enabled = CASE WHEN p_request ? 'notify_pre_market' THEN (p_request->>'notify_pre_market')::boolean ELSE app.notification_preferences.pre_market_enabled END,
    intraday_enabled = CASE WHEN p_request ? 'notify_intraday' THEN (p_request->>'notify_intraday')::boolean ELSE app.notification_preferences.intraday_enabled END,
    post_market_enabled = CASE WHEN p_request ? 'notify_post_market' THEN (p_request->>'notify_post_market')::boolean ELSE app.notification_preferences.post_market_enabled END,
    operational_enabled = CASE WHEN p_request ? 'notify_operational' THEN (p_request->>'notify_operational')::boolean ELSE app.notification_preferences.operational_enabled END,
    updated_at = clock_timestamp();

  INSERT INTO app.analysis_schedules(
    owner_id, timezone, pre_market_enabled, intraday_enabled, post_market_enabled
  ) VALUES (
    p_owner_id,
    timezone_value,
    coalesce((p_request->>'schedule_pre_market')::boolean, true),
    coalesce((p_request->>'schedule_intraday')::boolean, true),
    coalesce((p_request->>'schedule_post_market')::boolean, true)
  ) ON CONFLICT (owner_id) DO UPDATE SET
    timezone = CASE WHEN p_request ? 'timezone' THEN timezone_value ELSE app.analysis_schedules.timezone END,
    pre_market_enabled = CASE WHEN p_request ? 'schedule_pre_market' THEN (p_request->>'schedule_pre_market')::boolean ELSE app.analysis_schedules.pre_market_enabled END,
    intraday_enabled = CASE WHEN p_request ? 'schedule_intraday' THEN (p_request->>'schedule_intraday')::boolean ELSE app.analysis_schedules.intraday_enabled END,
    post_market_enabled = CASE WHEN p_request ? 'schedule_post_market' THEN (p_request->>'schedule_post_market')::boolean ELSE app.analysis_schedules.post_market_enabled END,
    updated_at = clock_timestamp();
  RETURN jsonb_build_object('status', 'updated');
END
$$;

CREATE OR REPLACE FUNCTION app.unlink_owner_telegram(
  p_owner_id UUID,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY[]::text[]) THEN
    RAISE EXCEPTION 'invalid unlink request' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('telegram-link:' || p_owner_id::text, 0));
  UPDATE app.telegram_links SET status = 'revoked', revoked_at = coalesce(revoked_at, clock_timestamp())
  WHERE owner_id = p_owner_id AND status = 'active';
  UPDATE app.telegram_pairing_codes SET consumed_at = coalesce(consumed_at, clock_timestamp())
  WHERE owner_id = p_owner_id AND consumed_at IS NULL;
  UPDATE app.portfolio_commands SET status = 'cancelled', updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND channel = 'telegram' AND status = 'previewed';
  UPDATE app.telegram_callback_tokens SET invalidated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND consumed_at IS NULL AND invalidated_at IS NULL;
  RETURN jsonb_build_object('status', 'unlinked');
END
$$;

ALTER FUNCTION app.read_owner_settings(UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.update_owner_settings(UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.unlink_owner_telegram(UUID, JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.read_owner_settings(UUID, JSONB),
  app.update_owner_settings(UUID, JSONB), app.unlink_owner_telegram(UUID, JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

-- Replace the browser dispatcher to include the reviewed run-now route. The Edge layer
-- authenticates and validates an exact empty body before entering this function.
CREATE OR REPLACE FUNCTION api.app_dispatch(
  p_route TEXT,
  p_request_id UUID,
  p_ip_digest TEXT,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID := auth.uid();
  scope_value TEXT;
  limit_value INT;
  window_value INT;
  owner_limit JSONB;
  client_limit JSONB;
  data_value JSONB;
  result_code TEXT;
BEGIN
  IF owner_value IS NULL OR p_request_id IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'UNAUTHORIZED'));
  END IF;
  IF jsonb_typeof(p_request) <> 'object' OR p_ip_digest !~ '^[0-9a-f]{64}$' THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'INVALID_REQUEST'));
  END IF;

  CASE p_route
    WHEN 'POST /portfolio/preview',
         'POST /portfolio/correction/preview',
         'POST /plans/preview' THEN
      scope_value := 'command_preview'; limit_value := 30; window_value := 60;
    WHEN 'POST /portfolio/confirm',
         'POST /portfolio/correction/confirm',
         'POST /plans/confirm' THEN
      scope_value := 'command_confirm'; limit_value := 20; window_value := 60;
    WHEN 'POST /telegram/pairing-code' THEN
      scope_value := 'telegram_pairing'; limit_value := 5; window_value := 600;
    WHEN 'POST /telegram/unlink' THEN
      scope_value := 'telegram_change'; limit_value := 10; window_value := 3600;
    WHEN 'POST /connections/create',
         'POST /connections/handshake',
         'POST /connections/activate',
         'POST /connections/revoke' THEN
      scope_value := 'connection_change'; limit_value := 10; window_value := 3600;
    WHEN 'POST /runs/on-demand' THEN
      scope_value := 'run_on_demand'; limit_value := 1; window_value := 3600;
    WHEN 'GET /settings', 'PATCH /settings' THEN
      scope_value := 'settings'; limit_value := 30; window_value := 60;
    WHEN 'GET /export' THEN
      scope_value := 'export'; limit_value := 2; window_value := 86400;
    WHEN 'POST /account/delete/request', 'POST /account/delete/confirm' THEN
      scope_value := 'account_delete'; limit_value := 3; window_value := 86400;
    ELSE
      RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'ROUTE_NOT_ALLOWED'));
  END CASE;

  owner_limit := app.consume_rate_limit(
    owner_value, scope_value, 'owner', limit_value, window_value
  );
  client_limit := app.consume_rate_limit(
    owner_value, scope_value, p_ip_digest, limit_value, window_value
  );
  IF NOT (owner_limit->>'allowed')::boolean OR NOT (client_limit->>'allowed')::boolean THEN
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, 'RATE_LIMITED');
    RETURN jsonb_build_object(
      'ok', false,
      'error', jsonb_build_object(
        'code', 'RATE_LIMITED',
        'retry_after_seconds', greatest(
          (owner_limit->>'retry_after_seconds')::int,
          (client_limit->>'retry_after_seconds')::int
        )
      )
    );
  END IF;

  BEGIN
    CASE p_route
      WHEN 'POST /portfolio/preview',
           'POST /portfolio/correction/preview',
           'POST /plans/preview' THEN
        data_value := api.preview_portfolio_command(p_request);
      WHEN 'POST /portfolio/confirm',
           'POST /portfolio/correction/confirm',
           'POST /plans/confirm' THEN
        IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['command_id','preview_digest']) THEN
          RAISE EXCEPTION 'invalid confirmation request';
        END IF;
        data_value := api.confirm_portfolio_command(
          (p_request->>'command_id')::uuid,
          p_request->>'preview_digest'
        );
      WHEN 'POST /telegram/pairing-code' THEN
        IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['code_digest'])
           OR p_request->>'code_digest' !~ '^[0-9a-f]{64}$' THEN
          RAISE EXCEPTION 'invalid pairing code request';
        END IF;
        data_value := app.issue_telegram_pairing_code(
          owner_value, p_request->>'code_digest'
        );
      WHEN 'POST /telegram/unlink' THEN
        data_value := app.unlink_owner_telegram(owner_value, p_request);
      WHEN 'POST /connections/create' THEN
        data_value := app.create_agent_connection(owner_value, p_request);
      WHEN 'POST /connections/handshake' THEN
        data_value := app.begin_agent_connection_handshake(owner_value, p_request);
      WHEN 'POST /connections/activate' THEN
        data_value := app.activate_agent_connection(owner_value, p_request);
      WHEN 'POST /connections/revoke' THEN
        data_value := app.revoke_agent_connection(owner_value, p_request);
      WHEN 'POST /runs/on-demand' THEN
        data_value := app.create_on_demand_run_slot(owner_value, p_request);
      WHEN 'GET /settings' THEN
        data_value := app.read_owner_settings(owner_value, p_request);
      WHEN 'PATCH /settings' THEN
        data_value := app.update_owner_settings(owner_value, p_request);
      ELSE
        INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
        VALUES (owner_value, p_request_id, p_route, 'NOT_READY');
        RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'NOT_READY'));
    END CASE;
    result_code := upper(coalesce(data_value->>'status', 'OK'));
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, result_code);
    RETURN jsonb_build_object('ok', true, 'data', data_value, 'request_id', p_request_id);
  EXCEPTION
    WHEN invalid_text_representation OR invalid_parameter_value OR check_violation
      OR numeric_value_out_of_range OR datetime_field_overflow THEN
      result_code := 'INVALID_REQUEST';
    WHEN unique_violation OR serialization_failure THEN
      result_code := 'CONFLICT';
    WHEN insufficient_privilege THEN
      result_code := 'NOT_FOUND';
    WHEN OTHERS THEN
      result_code := CASE
        WHEN SQLERRM ~* '(invalid|cash total|bucket|required|holding not found|sell exceeds|expired)'
          THEN 'INVALID_REQUEST'
        WHEN SQLERRM ~* '(stale|changed|digest|idempotency|cancelled|already)'
          THEN 'CONFLICT'
        ELSE 'INTERNAL_ERROR'
      END;
  END;
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, result_code);
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', result_code));
END
$$;
ALTER FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB)
  FROM PUBLIC, anon, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB) TO authenticated;

-- END REVIEWED MIGRATION: sql/migrations/20260909000000_research_run_api.sql

-- BEGIN REVIEWED MIGRATION: sql/migrations/20260910000000_retention_recovery.sql

-- Private account lifecycle, step-up receipts, secret-free exports, and operator reset controls.

CREATE TABLE app.account_step_up_challenges (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  initial_session_digest BYTEA NOT NULL CHECK (octet_length(initial_session_digest) = 32),
  expires_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id),
  CHECK (expires_at > created_at),
  CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE TABLE app.account_step_up_receipts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  challenge_id UUID NOT NULL,
  session_digest BYTEA NOT NULL CHECK (octet_length(session_digest) = 32),
  authenticated_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id),
  UNIQUE (owner_id, challenge_id),
  FOREIGN KEY (owner_id, challenge_id)
    REFERENCES app.account_step_up_challenges(owner_id, id) ON DELETE RESTRICT,
  CHECK (expires_at > created_at),
  CHECK (authenticated_at <= created_at + interval '30 seconds'),
  CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);

CREATE TABLE app.account_deletion_requests (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  step_up_receipt_id UUID NOT NULL,
  status TEXT NOT NULL DEFAULT 'confirmation_pending' CHECK (
    status IN ('confirmation_pending','pending','cancelled','processing','completed')
  ),
  confirmation_expires_at TIMESTAMPTZ NOT NULL,
  requested_at TIMESTAMPTZ,
  cancel_until TIMESTAMPTZ,
  delete_by TIMESTAMPTZ,
  telegram_cleanup_token_digest BYTEA CHECK (
    telegram_cleanup_token_digest IS NULL OR octet_length(telegram_cleanup_token_digest) = 32
  ),
  telegram_cleanup_status JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (
    jsonb_typeof(telegram_cleanup_status) = 'object'
    AND octet_length(telegram_cleanup_status::text) <= 2000
  ),
  cancelled_at TIMESTAMPTZ,
  processing_started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, step_up_receipt_id)
    REFERENCES app.account_step_up_receipts(owner_id, id) ON DELETE RESTRICT,
  CHECK (confirmation_expires_at > created_at),
  CHECK (
    status = 'confirmation_pending'
    OR (requested_at IS NOT NULL AND cancel_until = requested_at + interval '72 hours'
      AND delete_by = requested_at + interval '7 days')
  ),
  CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL)),
  CHECK (status NOT IN ('processing','completed') OR processing_started_at IS NOT NULL),
  CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);
CREATE UNIQUE INDEX account_deletion_one_active_owner_idx
  ON app.account_deletion_requests(owner_id)
  WHERE status IN ('confirmation_pending','pending','processing');

-- This table intentionally has no Auth foreign key. It survives identity deletion and prevents
-- a 35-day recovery archive from resurrecting an account the owner deleted.
CREATE TABLE app.deletion_tombstones (
  owner_id UUID PRIMARY KEY,
  deletion_request_id UUID NOT NULL UNIQUE,
  deleted_at TIMESTAMPTZ NOT NULL,
  archives_expire_after TIMESTAMPTZ NOT NULL,
  CHECK (archives_expire_after = deleted_at + interval '35 days')
);

-- Non-financial, immutable evidence that an offline ledger reset occurred.
CREATE TABLE app.owner_ledger_reset_receipts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL,
  step_up_receipt_id UUID NOT NULL UNIQUE,
  export_digest TEXT NOT NULL CHECK (export_digest ~ '^[0-9a-f]{64}$'),
  row_counts JSONB NOT NULL CHECK (
    jsonb_typeof(row_counts) = 'object' AND octet_length(row_counts::text) <= 2000
  ),
  reset_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE app.account_step_up_challenges OWNER TO stock_agent_migration_owner;
ALTER TABLE app.account_step_up_receipts OWNER TO stock_agent_migration_owner;
ALTER TABLE app.account_deletion_requests OWNER TO stock_agent_migration_owner;
ALTER TABLE app.deletion_tombstones OWNER TO stock_agent_migration_owner;
ALTER TABLE app.owner_ledger_reset_receipts OWNER TO stock_agent_migration_owner;

DO $$
DECLARE target_table TEXT;
BEGIN
  FOREACH target_table IN ARRAY ARRAY[
    'account_step_up_challenges','account_step_up_receipts','account_deletion_requests',
    'deletion_tombstones','owner_ledger_reset_receipts'
  ] LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', target_table);
    EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY', target_table);
    EXECUTE format(
      'CREATE POLICY %I ON app.%I FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true)',
      target_table || '_executor_all', target_table
    );
    EXECUTE format(
      'CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.%I '
      'FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation()', target_table
    );
  END LOOP;
END
$$;

CREATE POLICY account_deletion_requests_owner_select ON app.account_deletion_requests
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

-- These pre-existing forced-RLS tables need an owner-only executor policy so ungranted
-- lifecycle functions can remove their rows during a verified account purge.
CREATE POLICY app_admins_lifecycle_executor_all ON app.app_admins
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY single_owner_receipts_lifecycle_executor_all
  ON app.single_owner_migration_receipts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY owner_policy_lifecycle_executor_all ON app.owner_policy_overrides
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

REVOKE ALL ON app.account_step_up_challenges, app.account_step_up_receipts,
  app.account_deletion_requests, app.deletion_tombstones, app.owner_ledger_reset_receipts
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT SELECT (owner_id, id, status, requested_at, cancel_until, delete_by,
              telegram_cleanup_status, cancelled_at, completed_at, created_at, updated_at)
  ON app.account_deletion_requests TO authenticated;

CREATE OR REPLACE FUNCTION app.prevent_immutable_receipt_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF TG_OP = 'DELETE' AND TG_TABLE_NAME = 'deletion_tombstones'
     AND current_setting('stock_agent.retention_tombstones', true) = 'on' THEN
    RETURN OLD;
  END IF;
  IF TG_OP = 'DELETE' AND TG_TABLE_NAME = 'owner_ledger_reset_receipts'
     AND current_setting('stock_agent.account_purge_owner', true) = OLD.owner_id::text THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'immutable lifecycle receipt';
END
$$;
ALTER FUNCTION app.prevent_immutable_receipt_mutation() OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.prevent_immutable_receipt_mutation() FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE TRIGGER prevent_deletion_tombstone_mutation
  BEFORE UPDATE OR DELETE ON app.deletion_tombstones
  FOR EACH ROW EXECUTE FUNCTION app.prevent_immutable_receipt_mutation();
CREATE TRIGGER prevent_ledger_reset_receipt_mutation
  BEFORE UPDATE OR DELETE ON app.owner_ledger_reset_receipts
  FOR EACH ROW EXECUTE FUNCTION app.prevent_immutable_receipt_mutation();

CREATE OR REPLACE FUNCTION app.accept_owner_consent(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE current_version TEXT;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY['document_version'])
     OR jsonb_typeof(p_request->'document_version') <> 'string' THEN
    RAISE EXCEPTION 'invalid consent request' USING ERRCODE = '22023';
  END IF;
  SELECT current_consent_version INTO current_version
  FROM machine.connection_policy WHERE singleton;
  IF current_version IS NULL OR p_request->>'document_version' <> current_version THEN
    RAISE EXCEPTION 'invalid consent version' USING ERRCODE = '22023';
  END IF;
  PERFORM 1 FROM app.profiles
  WHERE id = p_owner_id AND status IN ('invited','active') FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'profile unavailable' USING ERRCODE = '42501'; END IF;
  INSERT INTO app.user_consents(owner_id, document_version, source)
  VALUES (p_owner_id, current_version, 'web')
  ON CONFLICT (owner_id, document_version) DO NOTHING;
  UPDATE app.profiles SET status = 'active',
    onboarding_completed_at = coalesce(onboarding_completed_at, clock_timestamp()),
    updated_at = clock_timestamp()
  WHERE id = p_owner_id;
  RETURN jsonb_build_object('status', 'accepted', 'document_version', current_version);
END
$$;

CREATE OR REPLACE FUNCTION app.create_account_step_up_challenge(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE challenge_id UUID := extensions.gen_random_uuid();
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY['session_digest'])
     OR p_request->>'session_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid step up request' USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM app.profiles WHERE id = p_owner_id
      AND status IN ('invited','active','deletion_pending')
  ) THEN RAISE EXCEPTION 'profile unavailable' USING ERRCODE = '42501'; END IF;
  UPDATE app.account_step_up_challenges SET completed_at = observed_at
  WHERE owner_id = p_owner_id AND completed_at IS NULL;
  INSERT INTO app.account_step_up_challenges(
    id, owner_id, initial_session_digest, expires_at, created_at
  ) VALUES (
    challenge_id, p_owner_id, decode(p_request->>'session_digest', 'hex'),
    observed_at + interval '10 minutes', observed_at
  );
  RETURN jsonb_build_object(
    'status', 'challenge_created', 'challenge_id', challenge_id,
    'expires_at', observed_at + interval '10 minutes'
  );
END
$$;

CREATE OR REPLACE FUNCTION app.complete_account_step_up(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE challenge_row app.account_step_up_challenges%ROWTYPE;
DECLARE receipt_id UUID := extensions.gen_random_uuid();
DECLARE session_value BYTEA;
DECLARE authenticated_value TIMESTAMPTZ;
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(
       p_request, ARRAY['challenge_id','session_digest','auth_method','authenticated_at']
     ) OR p_request->>'challenge_id' !~
       '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
     OR p_request->>'session_digest' !~ '^[0-9a-f]{64}$'
     OR p_request->>'auth_method' <> 'otp' THEN
    RAISE EXCEPTION 'invalid step up request' USING ERRCODE = '22023';
  END IF;
  BEGIN authenticated_value := (p_request->>'authenticated_at')::timestamptz;
  EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'invalid step up request' USING ERRCODE = '22023'; END;
  SELECT * INTO challenge_row FROM app.account_step_up_challenges
  WHERE owner_id = p_owner_id AND id = (p_request->>'challenge_id')::uuid FOR UPDATE;
  session_value := decode(p_request->>'session_digest', 'hex');
  IF NOT FOUND OR challenge_row.completed_at IS NOT NULL OR challenge_row.expires_at <= observed_at
     OR challenge_row.initial_session_digest = session_value
     OR authenticated_value < challenge_row.created_at - interval '5 seconds'
     OR authenticated_value < observed_at - interval '5 minutes'
     OR authenticated_value > observed_at + interval '30 seconds' THEN
    RAISE EXCEPTION 'fresh OTP step up unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.account_step_up_challenges SET completed_at = observed_at WHERE id = challenge_row.id;
  INSERT INTO app.account_step_up_receipts(
    id, owner_id, challenge_id, session_digest, authenticated_at, expires_at, created_at
  ) VALUES (
    receipt_id, p_owner_id, challenge_row.id, session_value, authenticated_value,
    observed_at + interval '5 minutes', observed_at
  );
  RETURN jsonb_build_object(
    'status', 'verified', 'step_up_receipt_id', receipt_id,
    'expires_at', observed_at + interval '5 minutes'
  );
END
$$;

CREATE OR REPLACE FUNCTION app.assert_fresh_step_up(
  p_owner_id UUID,
  p_receipt_id UUID,
  p_session_digest TEXT,
  p_consume BOOLEAN DEFAULT false
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
BEGIN
  IF p_owner_id IS NULL OR p_receipt_id IS NULL
     OR p_session_digest !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'fresh step up required' USING ERRCODE = '42501';
  END IF;
  PERFORM 1 FROM app.account_step_up_receipts
  WHERE owner_id = p_owner_id AND id = p_receipt_id
    AND session_digest = decode(p_session_digest, 'hex')
    AND consumed_at IS NULL AND expires_at > observed_at
  FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'fresh step up required' USING ERRCODE = '42501'; END IF;
  IF p_consume THEN
    UPDATE app.account_step_up_receipts SET consumed_at = observed_at
    WHERE owner_id = p_owner_id AND id = p_receipt_id;
  END IF;
END
$$;

CREATE OR REPLACE FUNCTION app.export_owner_account(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE document JSONB;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY[]::text[])
     OR NOT EXISTS (SELECT 1 FROM app.profiles WHERE id = p_owner_id) THEN
    RAISE EXCEPTION 'export unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT jsonb_build_object(
    'schema_version', 1,
    'exported_at', clock_timestamp(),
    'profile', jsonb_build_object(
      'display_name', profile.display_name,
      'timezone', profile.timezone,
      'status', profile.status,
      'onboarding_completed_at', profile.onboarding_completed_at,
      'created_at', profile.created_at,
      'updated_at', profile.updated_at
    ),
    'consents', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'document_version', consent.document_version,
      'source', consent.source,
      'accepted_at', consent.accepted_at
    ) ORDER BY consent.accepted_at) FROM app.user_consents consent
      WHERE consent.owner_id = p_owner_id), '[]'::jsonb),
    'preferences', coalesce((SELECT jsonb_build_object(
      'pre_market_enabled', preference.pre_market_enabled,
      'intraday_enabled', preference.intraday_enabled,
      'post_market_enabled', preference.post_market_enabled,
      'operational_enabled', preference.operational_enabled,
      'updated_at', preference.updated_at
    ) FROM app.notification_preferences preference WHERE preference.owner_id = p_owner_id), '{}'::jsonb),
    'portfolio', jsonb_build_object(
      'holdings', coalesce((SELECT jsonb_agg(jsonb_build_object(
        'ticker', holding.ticker, 'shares', holding.shares::text,
        'avg_cost', holding.avg_cost::text, 'bucket', holding.bucket,
        'opened_at', holding.opened_at, 'stop', holding.stop::text,
        'target', holding.target::text, 'projection_sequence', holding.projection_sequence::text
      ) ORDER BY holding.ticker) FROM app.holdings holding
        WHERE holding.owner_id = p_owner_id), '[]'::jsonb),
      'transactions', coalesce((SELECT jsonb_agg(jsonb_build_object(
        'transaction_id', transaction_row.public_id, 'created_at', transaction_row.ts,
        'ticker', transaction_row.ticker, 'event_type', transaction_row.event_type,
        'side', transaction_row.side, 'quantity', transaction_row.qty::text,
        'price', transaction_row.price::text, 'fees', transaction_row.fees::text,
        'executed_on', transaction_row.executed_on,
        'ledger_sequence', transaction_row.ledger_sequence::text,
        'bucket', transaction_row.bucket, 'source_channel', transaction_row.source_channel,
        'corrects_transaction_id', corrected.public_id
      ) ORDER BY transaction_row.ledger_sequence) FROM app.transactions transaction_row
        LEFT JOIN app.transactions corrected
          ON corrected.owner_id = transaction_row.owner_id
         AND corrected.id = transaction_row.corrects_transaction_id
        WHERE transaction_row.owner_id = p_owner_id), '[]'::jsonb),
      'plans', coalesce((SELECT jsonb_agg(jsonb_build_object(
        'id', plan.id, 'ticker', plan.ticker, 'bucket', plan.bucket,
        'amount', plan.amount::text, 'cadence', plan.cadence,
        'next_due_on', plan.next_due_on, 'due_day', plan.due_day, 'active', plan.active,
        'created_at', plan.created_at, 'updated_at', plan.updated_at
      ) ORDER BY plan.created_at, plan.id) FROM app.owner_investment_plans plan
        WHERE plan.owner_id = p_owner_id), '[]'::jsonb)
    ),
    'commands', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'id', command.id, 'channel', command.channel, 'operation', command.operation,
      'before', command.before_projection, 'after', command.after_projection,
      'warnings', command.warnings, 'status', command.status,
      'expires_at', command.expires_at, 'confirmed_at', command.confirmed_at,
      'applied_at', command.applied_at, 'result', command.result,
      'error_code', command.error_code, 'created_at', command.created_at
    ) ORDER BY command.created_at, command.id) FROM app.portfolio_commands command
      WHERE command.owner_id = p_owner_id), '[]'::jsonb),
    'recommendations', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'id', suggestion.id::text, 'run_id', suggestion.run_id,
      'created_at', suggestion.ts, 'date', suggestion.date,
      'ticker', suggestion.ticker, 'action', suggestion.action,
      'bucket', suggestion.bucket, 'confidence', suggestion.confidence,
      'valid_until', suggestion.valid_until, 'risk_verdict', suggestion.risk_verdict,
      'price_at_suggestion', suggestion.price_at_suggestion::text,
      'evidence_as_of', suggestion.evidence_as_of
    ) ORDER BY suggestion.ts, suggestion.id) FROM app.suggestions suggestion
      WHERE suggestion.owner_id = p_owner_id), '[]'::jsonb),
    'evidence_metadata', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'evidence_id', evidence.evidence_id, 'run_id', evidence.run_id,
      'category', evidence.category, 'source', evidence.source_identifier,
      'reference', evidence.reference_identifier, 'observed_at', evidence.observed_at,
      'retrieved_at', evidence.retrieved_at, 'revalidated_at', evidence.revalidated_at,
      'content_hash', evidence.content_hash, 'status', evidence.status
    ) ORDER BY evidence.retrieved_at, evidence.id) FROM app.run_evidence evidence
      WHERE evidence.owner_id = p_owner_id), '[]'::jsonb),
    'runs', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'id', run.id, 'kind', run.kind, 'market_date', run.market_date,
      'provider', run.provider, 'model', run.model, 'started_at', run.started_at,
      'finished_at', run.finished_at, 'status', run.status,
      'data_as_of', run.data_as_of, 'source_status', run.source_status,
      'symbols', run.symbols, 'write_counts', run.write_counts, 'summary', run.summary
    ) ORDER BY run.started_at, run.id) FROM app.analysis_runs run
      WHERE run.owner_id = p_owner_id), '[]'::jsonb),
    'connections', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'id', connection.id, 'provider', connection.provider,
      'credential_type', connection.credential_type,
      'capabilities', connection.capabilities,
      'contract_version', connection.contract_version, 'status', connection.status,
      'last_handshake_at', connection.last_handshake_at,
      'created_at', connection.created_at, 'updated_at', connection.updated_at
    ) ORDER BY connection.created_at, connection.id) FROM app.agent_connections connection
      WHERE connection.owner_id = p_owner_id), '[]'::jsonb),
    'telegram', coalesce((SELECT jsonb_build_object(
      'status', link.status, 'linked_at', link.linked_at, 'revoked_at', link.revoked_at
    ) FROM app.telegram_links link WHERE link.owner_id = p_owner_id),
      jsonb_build_object('status', 'unlinked'))
  ) INTO document
  FROM app.profiles profile WHERE profile.id = p_owner_id;
  RETURN jsonb_build_object(
    'status', 'ready', 'format', 'json',
    'filename', 'stock-agent-account.json',
    'media_type', 'application/json; charset=utf-8',
    'body', jsonb_pretty(document) || E'\n'
  );
END
$$;

CREATE OR REPLACE FUNCTION app.csv_safe_cell(p_value TEXT)
RETURNS TEXT
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog
AS $$
DECLARE value TEXT := coalesce(p_value, '');
BEGIN
  IF value ~ '^[=+@-]' THEN value := '''' || value; END IF;
  RETURN '"' || replace(value, '"', '""') || '"';
END
$$;

CREATE OR REPLACE FUNCTION app.export_owner_ledger(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE csv_body TEXT;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY[]::text[])
     OR NOT EXISTS (SELECT 1 FROM app.profiles WHERE id = p_owner_id) THEN
    RAISE EXCEPTION 'export unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT 'transaction_id,created_at,ticker,event_type,side,quantity,price,fees,executed_on,ledger_sequence,bucket,source_channel,corrects_transaction_id' || E'\n'
    || coalesce(string_agg(
      app.csv_safe_cell(transaction_row.public_id::text) || ',' ||
      app.csv_safe_cell(transaction_row.ts::text) || ',' ||
      app.csv_safe_cell(transaction_row.ticker) || ',' ||
      app.csv_safe_cell(transaction_row.event_type) || ',' ||
      app.csv_safe_cell(transaction_row.side) || ',' ||
      app.csv_safe_cell(transaction_row.qty::text) || ',' ||
      app.csv_safe_cell(transaction_row.price::text) || ',' ||
      app.csv_safe_cell(transaction_row.fees::text) || ',' ||
      app.csv_safe_cell(transaction_row.executed_on::text) || ',' ||
      app.csv_safe_cell(transaction_row.ledger_sequence::text) || ',' ||
      app.csv_safe_cell(transaction_row.bucket) || ',' ||
      app.csv_safe_cell(transaction_row.source_channel) || ',' ||
      app.csv_safe_cell(corrected.public_id::text), E'\n'
      ORDER BY transaction_row.ledger_sequence
    ), '') || CASE WHEN EXISTS (
      SELECT 1 FROM app.transactions existing WHERE existing.owner_id = p_owner_id
    ) THEN E'\n' ELSE '' END
  INTO csv_body
  FROM app.transactions transaction_row
  LEFT JOIN app.transactions corrected
    ON corrected.owner_id = transaction_row.owner_id
   AND corrected.id = transaction_row.corrects_transaction_id
  WHERE transaction_row.owner_id = p_owner_id;
  RETURN jsonb_build_object(
    'status', 'ready', 'format', 'csv',
    'filename', 'stock-agent-ledger.csv',
    'media_type', 'text/csv; charset=utf-8', 'body', csv_body
  );
END
$$;

CREATE OR REPLACE FUNCTION app.request_account_deletion(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE receipt_id UUID;
DECLARE deletion_id UUID := extensions.gen_random_uuid();
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
DECLARE existing_row app.account_deletion_requests%ROWTYPE;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['step_up_receipt_id','session_digest']
  ) THEN RAISE EXCEPTION 'invalid deletion request' USING ERRCODE = '22023'; END IF;
  BEGIN receipt_id := (p_request->>'step_up_receipt_id')::uuid;
  EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'invalid deletion request' USING ERRCODE = '22023'; END;
  PERFORM app.assert_fresh_step_up(p_owner_id, receipt_id, p_request->>'session_digest', false);
  SELECT * INTO existing_row FROM app.account_deletion_requests
  WHERE owner_id = p_owner_id AND status IN ('confirmation_pending','pending','processing')
  FOR UPDATE;
  IF FOUND THEN
    RETURN jsonb_build_object(
      'status', existing_row.status,
      'deletion_request_id', existing_row.id,
      'confirmation_phrase', 'DELETE MY ACCOUNT',
      'confirmation_expires_at', existing_row.confirmation_expires_at
    );
  END IF;
  INSERT INTO app.account_deletion_requests(
    id, owner_id, step_up_receipt_id, confirmation_expires_at, created_at, updated_at
  ) VALUES (
    deletion_id, p_owner_id, receipt_id, observed_at + interval '5 minutes', observed_at, observed_at
  );
  RETURN jsonb_build_object(
    'status', 'confirmation_pending', 'deletion_request_id', deletion_id,
    'confirmation_phrase', 'DELETE MY ACCOUNT',
    'confirmation_expires_at', observed_at + interval '5 minutes'
  );
END
$$;

CREATE OR REPLACE FUNCTION app.confirm_account_deletion(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE deletion_row app.account_deletion_requests%ROWTYPE;
DECLARE receipt_id UUID;
DECLARE deletion_id UUID;
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
DECLARE connection_row RECORD;
DECLARE recent_messages INT;
DECLARE cleanup_messages JSONB := '[]'::jsonb;
DECLARE cleanup_chat_id BIGINT;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['deletion_request_id','step_up_receipt_id','session_digest',
      'confirmation_phrase','cleanup_token_digest']
  ) OR p_request->>'confirmation_phrase' <> 'DELETE MY ACCOUNT'
     OR p_request->>'cleanup_token_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid deletion confirmation' USING ERRCODE = '22023';
  END IF;
  BEGIN
    deletion_id := (p_request->>'deletion_request_id')::uuid;
    receipt_id := (p_request->>'step_up_receipt_id')::uuid;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'invalid deletion confirmation' USING ERRCODE = '22023';
  END;
  SELECT * INTO deletion_row FROM app.account_deletion_requests
  WHERE owner_id = p_owner_id AND id = deletion_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'deletion unavailable' USING ERRCODE = '42501'; END IF;
  IF deletion_row.status = 'pending' THEN
    IF coalesce(deletion_row.telegram_cleanup_status->>'status', '')
       IN ('queued','partial','failed','unavailable') THEN
      UPDATE app.account_deletion_requests
      SET telegram_cleanup_token_digest = decode(p_request->>'cleanup_token_digest', 'hex'),
          updated_at = observed_at
      WHERE id = deletion_row.id;
      SELECT telegram_chat_id INTO cleanup_chat_id FROM app.telegram_links
        WHERE owner_id = p_owner_id;
      SELECT coalesce(jsonb_agg(message_id ORDER BY message_id), '[]'::jsonb)
      INTO cleanup_messages FROM (
        SELECT DISTINCT candidate.message_id
        FROM (
          SELECT delivery.telegram_message_id::text AS message_id
          FROM app.telegram_deliveries delivery
          WHERE delivery.owner_id = p_owner_id AND delivery.telegram_message_id IS NOT NULL
            AND delivery.recorded_at >= observed_at - interval '48 hours'
          UNION ALL
          SELECT message_id
          FROM app.market_publications publication
          CROSS JOIN LATERAL jsonb_array_elements_text(publication.telegram_message_ids)
            AS messages(message_id)
          WHERE publication.owner_id = p_owner_id AND publication.delivered_at IS NOT NULL
            AND publication.delivered_at >= observed_at - interval '48 hours'
          UNION ALL
          SELECT message_id
          FROM app.operational_alerts alert
          CROSS JOIN LATERAL jsonb_array_elements_text(alert.telegram_message_ids)
            AS messages(message_id)
          WHERE alert.owner_id = p_owner_id AND alert.delivered_at IS NOT NULL
            AND alert.delivered_at >= observed_at - interval '48 hours'
        ) candidate
        WHERE candidate.message_id ~ '^[1-9][0-9]{0,18}$'
        ORDER BY candidate.message_id
        LIMIT 100
      ) recent;
    END IF;
    RETURN jsonb_build_object(
      'status', 'pending', 'deletion_request_id', deletion_row.id,
      'cancel_until', deletion_row.cancel_until, 'delete_by', deletion_row.delete_by,
      'older_telegram_history_requires_manual_removal', true, 'duplicate', true,
      '_telegram_cleanup', jsonb_build_object(
        'record_required', coalesce(deletion_row.telegram_cleanup_status->>'status', '')
          IN ('queued','partial','failed','unavailable'),
        'previous_status', CASE
          WHEN deletion_row.telegram_cleanup_status->>'status' IN ('completed','nothing_recent')
            THEN deletion_row.telegram_cleanup_status->>'status'
          ELSE NULL
        END,
        'attempted', coalesce((deletion_row.telegram_cleanup_status->>'attempted')::int, 0),
        'deleted', coalesce((deletion_row.telegram_cleanup_status->>'deleted')::int, 0),
        'failed', coalesce((deletion_row.telegram_cleanup_status->>'failed')::int, 0),
        'chat_id', cleanup_chat_id::text, 'message_ids', cleanup_messages
      )
    );
  END IF;
  IF deletion_row.status <> 'confirmation_pending'
     OR deletion_row.step_up_receipt_id <> receipt_id
     OR deletion_row.confirmation_expires_at <= observed_at THEN
    RAISE EXCEPTION 'deletion confirmation expired' USING ERRCODE = '42501';
  END IF;
  PERFORM app.assert_fresh_step_up(p_owner_id, receipt_id, p_request->>'session_digest', true);

  FOR connection_row IN
    SELECT id FROM app.agent_connections WHERE owner_id = p_owner_id AND status <> 'revoked'
  LOOP
    PERFORM app.revoke_agent_connection(
      p_owner_id, jsonb_build_object('connection_id', connection_row.id)
    );
  END LOOP;
  UPDATE app.analysis_schedules SET primary_connection_id = NULL,
    pre_market_enabled = false, intraday_enabled = false, post_market_enabled = false,
    updated_at = observed_at WHERE owner_id = p_owner_id;
  UPDATE app.notification_preferences SET pre_market_enabled = false,
    intraday_enabled = false, post_market_enabled = false, operational_enabled = false,
    updated_at = observed_at WHERE owner_id = p_owner_id;
  UPDATE app.telegram_links SET status = 'revoked', revoked_at = coalesce(revoked_at, observed_at)
    WHERE owner_id = p_owner_id AND status = 'active';
  UPDATE app.telegram_pairing_codes SET consumed_at = coalesce(consumed_at, observed_at)
    WHERE owner_id = p_owner_id AND consumed_at IS NULL;
  UPDATE app.telegram_callback_tokens SET invalidated_at = observed_at
    WHERE owner_id = p_owner_id AND consumed_at IS NULL AND invalidated_at IS NULL;
  UPDATE app.portfolio_commands SET status = 'cancelled', updated_at = observed_at
    WHERE owner_id = p_owner_id AND status IN ('submitted','previewed');
  SELECT telegram_chat_id INTO cleanup_chat_id FROM app.telegram_links
    WHERE owner_id = p_owner_id;
  SELECT count(DISTINCT candidate.message_id)::int INTO recent_messages
  FROM (
    SELECT delivery.telegram_message_id::text AS message_id
    FROM app.telegram_deliveries delivery
    WHERE delivery.owner_id = p_owner_id AND delivery.telegram_message_id IS NOT NULL
      AND delivery.recorded_at >= observed_at - interval '48 hours'
    UNION ALL
    SELECT message_id
    FROM app.market_publications publication
    CROSS JOIN LATERAL jsonb_array_elements_text(publication.telegram_message_ids)
      AS messages(message_id)
    WHERE publication.owner_id = p_owner_id AND publication.delivered_at IS NOT NULL
      AND publication.delivered_at >= observed_at - interval '48 hours'
    UNION ALL
    SELECT message_id
    FROM app.operational_alerts alert
    CROSS JOIN LATERAL jsonb_array_elements_text(alert.telegram_message_ids)
      AS messages(message_id)
    WHERE alert.owner_id = p_owner_id AND alert.delivered_at IS NOT NULL
      AND alert.delivered_at >= observed_at - interval '48 hours'
  ) candidate
  WHERE candidate.message_id ~ '^[1-9][0-9]{0,18}$';
  SELECT coalesce(jsonb_agg(message_id ORDER BY message_id), '[]'::jsonb)
  INTO cleanup_messages FROM (
    SELECT DISTINCT candidate.message_id
    FROM (
      SELECT delivery.telegram_message_id::text AS message_id
      FROM app.telegram_deliveries delivery
      WHERE delivery.owner_id = p_owner_id AND delivery.telegram_message_id IS NOT NULL
        AND delivery.recorded_at >= observed_at - interval '48 hours'
      UNION ALL
      SELECT message_id
      FROM app.market_publications publication
      CROSS JOIN LATERAL jsonb_array_elements_text(publication.telegram_message_ids)
        AS messages(message_id)
      WHERE publication.owner_id = p_owner_id AND publication.delivered_at IS NOT NULL
        AND publication.delivered_at >= observed_at - interval '48 hours'
      UNION ALL
      SELECT message_id
      FROM app.operational_alerts alert
      CROSS JOIN LATERAL jsonb_array_elements_text(alert.telegram_message_ids)
        AS messages(message_id)
      WHERE alert.owner_id = p_owner_id AND alert.delivered_at IS NOT NULL
        AND alert.delivered_at >= observed_at - interval '48 hours'
    ) candidate
    WHERE candidate.message_id ~ '^[1-9][0-9]{0,18}$'
    ORDER BY candidate.message_id
    LIMIT 100
  ) recent;
  UPDATE app.account_deletion_requests SET
    status = 'pending', requested_at = observed_at,
    cancel_until = observed_at + interval '72 hours',
    delete_by = observed_at + interval '7 days',
    telegram_cleanup_status = jsonb_build_object(
      'status', CASE WHEN recent_messages > 0 THEN 'queued' ELSE 'nothing_recent' END,
      'recent_eligible_count', least(recent_messages, 100),
      'older_history_requires_manual_removal', true
    ),
    telegram_cleanup_token_digest = decode(p_request->>'cleanup_token_digest', 'hex'),
    updated_at = observed_at
  WHERE id = deletion_id;
  UPDATE app.profiles SET status = 'deletion_pending', updated_at = observed_at
    WHERE id = p_owner_id;
  RETURN jsonb_build_object(
    'status', 'pending', 'deletion_request_id', deletion_id,
    'cancel_until', observed_at + interval '72 hours',
    'delete_by', observed_at + interval '7 days',
    'older_telegram_history_requires_manual_removal', true, 'duplicate', false,
    '_telegram_cleanup', jsonb_build_object(
      'record_required', true, 'previous_status', NULL,
      'attempted', 0, 'deleted', 0, 'failed', 0,
      'chat_id', cleanup_chat_id::text, 'message_ids', cleanup_messages
    )
  );
END
$$;

CREATE OR REPLACE FUNCTION app.record_account_telegram_cleanup(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE deletion_id UUID;
DECLARE attempted_count INT;
DECLARE deleted_count INT;
DECLARE failed_count INT;
DECLARE status_value TEXT;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['deletion_request_id','cleanup_token','attempted','deleted','failed','status']
  ) OR p_request->>'deletion_request_id' !~
       '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
     OR p_request->>'cleanup_token' !~ '^[A-Za-z0-9_-]{43}$'
     OR p_request->>'attempted' !~ '^[0-9]{1,3}$'
     OR p_request->>'deleted' !~ '^[0-9]{1,3}$'
     OR p_request->>'failed' !~ '^[0-9]{1,3}$'
     OR p_request->>'status' NOT IN ('nothing_recent','completed','partial','failed','unavailable') THEN
    RAISE EXCEPTION 'invalid Telegram cleanup result' USING ERRCODE = '22023';
  END IF;
  deletion_id := (p_request->>'deletion_request_id')::uuid;
  attempted_count := (p_request->>'attempted')::int;
  deleted_count := (p_request->>'deleted')::int;
  failed_count := (p_request->>'failed')::int;
  status_value := p_request->>'status';
  IF attempted_count > 100 OR deleted_count + failed_count <> attempted_count
     OR NOT EXISTS (
       SELECT 1 FROM app.account_deletion_requests
       WHERE owner_id = p_owner_id AND id = deletion_id AND status = 'pending'
         AND telegram_cleanup_token_digest = extensions.digest(
           p_request->>'cleanup_token', 'sha256'
         )
     ) THEN
    RAISE EXCEPTION 'Telegram cleanup unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.account_deletion_requests SET
    telegram_cleanup_status = jsonb_build_object(
      'status', status_value, 'attempted', attempted_count,
      'deleted', deleted_count, 'failed', failed_count,
      'older_history_requires_manual_removal', true,
      'recorded_at', clock_timestamp()
    ),
    telegram_cleanup_token_digest = NULL,
    updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND id = deletion_id;
  RETURN jsonb_build_object('status', 'recorded');
END
$$;

CREATE OR REPLACE FUNCTION app.cancel_account_deletion(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE deletion_row app.account_deletion_requests%ROWTYPE;
DECLARE receipt_id UUID;
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['step_up_receipt_id','session_digest']
  ) THEN RAISE EXCEPTION 'invalid cancellation request' USING ERRCODE = '22023'; END IF;
  BEGIN receipt_id := (p_request->>'step_up_receipt_id')::uuid;
  EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'invalid cancellation request' USING ERRCODE = '22023'; END;
  SELECT * INTO deletion_row FROM app.account_deletion_requests
  WHERE owner_id = p_owner_id AND status = 'pending'
  ORDER BY requested_at DESC LIMIT 1 FOR UPDATE;
  IF NOT FOUND OR deletion_row.cancel_until <= observed_at THEN
    RAISE EXCEPTION 'cancellation window expired' USING ERRCODE = '42501';
  END IF;
  PERFORM app.assert_fresh_step_up(p_owner_id, receipt_id, p_request->>'session_digest', true);
  UPDATE app.account_deletion_requests SET status = 'cancelled', cancelled_at = observed_at,
    updated_at = observed_at WHERE id = deletion_row.id;
  UPDATE app.profiles SET status = 'active', updated_at = observed_at WHERE id = p_owner_id;
  RETURN jsonb_build_object(
    'status', 'cancelled', 'deletion_request_id', deletion_row.id,
    'credentials_require_reconnection', true
  );
END
$$;

-- Prepare the active-data purge in one transaction. The Auth identity remains until the
-- offline operator has committed this function and can delete Auth through the Admin API.
CREATE OR REPLACE FUNCTION app.operator_prepare_account_deletion(
  p_owner_id UUID,
  p_deletion_request_id UUID,
  p_confirmation TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE deletion_row app.account_deletion_requests%ROWTYPE;
DECLARE tombstone_row app.deletion_tombstones%ROWTYPE;
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
DECLARE connection_row RECORD;
DECLARE target_table TEXT;
DECLARE residue_count BIGINT;
BEGIN
  IF p_owner_id IS NULL OR p_deletion_request_id IS NULL
     OR p_confirmation <> 'DELETE AUTH ' || p_owner_id::text THEN
    RAISE EXCEPTION 'account deletion confirmation mismatch' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('account-delete:' || p_owner_id::text, 0));
  SELECT * INTO tombstone_row
  FROM app.deletion_tombstones
  WHERE owner_id = p_owner_id;
  IF FOUND THEN
    IF tombstone_row.deletion_request_id <> p_deletion_request_id THEN
      RAISE EXCEPTION 'account deletion unavailable' USING ERRCODE = '42501';
    END IF;
    RETURN jsonb_build_object(
      'status', 'ready_for_auth_deletion',
      'deletion_request_id', tombstone_row.deletion_request_id,
      'deleted_at', tombstone_row.deleted_at,
      'archives_expire_after', tombstone_row.archives_expire_after,
      'duplicate', true
    );
  END IF;
  SELECT * INTO deletion_row
  FROM app.account_deletion_requests
  WHERE owner_id = p_owner_id AND id = p_deletion_request_id
  FOR UPDATE;
  IF NOT FOUND OR deletion_row.status <> 'pending'
     OR deletion_row.cancel_until > observed_at
     OR NOT EXISTS (
       SELECT 1 FROM app.profiles
       WHERE id = p_owner_id AND status = 'deletion_pending'
     ) THEN
    RAISE EXCEPTION 'account deletion unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.account_deletion_requests
  SET status = 'processing', processing_started_at = observed_at, updated_at = observed_at
  WHERE id = deletion_row.id;

  -- Revoke any authority that appeared through an operator error after the owner confirmed.
  FOR connection_row IN
    SELECT id FROM app.agent_connections
    WHERE owner_id = p_owner_id AND (
      status <> 'revoked' OR inbound_token_digest IS NOT NULL
      OR outbound_trigger_secret_id IS NOT NULL OR trigger_url IS NOT NULL
    )
  LOOP
    PERFORM app.revoke_agent_connection(
      p_owner_id, jsonb_build_object('connection_id', connection_row.id)
    );
  END LOOP;
  IF EXISTS (
    SELECT 1 FROM app.agent_connections
    WHERE owner_id = p_owner_id AND (
      status <> 'revoked' OR inbound_token_digest IS NOT NULL
      OR outbound_trigger_secret_id IS NOT NULL OR trigger_url IS NOT NULL
    )
  ) THEN
    RAISE EXCEPTION 'account authority revocation incomplete';
  END IF;

  INSERT INTO app.deletion_tombstones(
    owner_id, deletion_request_id, deleted_at, archives_expire_after
  ) VALUES (
    p_owner_id, p_deletion_request_id, observed_at, observed_at + interval '35 days'
  );
  PERFORM set_config('stock_agent.account_purge_owner', p_owner_id::text, true);

  -- Children and cross-references precede their parents. Failed statements roll back the
  -- tombstone and every deletion, so the Auth identity is never removed after a partial purge.
  DELETE FROM app.telegram_pairing_deliveries delivery
  USING app.telegram_updates update_row
  WHERE delivery.telegram_update_id = update_row.telegram_update_id
    AND update_row.owner_id = p_owner_id;
  DELETE FROM app.telegram_callback_tokens WHERE owner_id = p_owner_id;
  DELETE FROM app.telegram_deliveries WHERE owner_id = p_owner_id;
  DELETE FROM app.telegram_pairing_codes WHERE owner_id = p_owner_id;
  DELETE FROM app.telegram_updates WHERE owner_id = p_owner_id;

  DELETE FROM app.routine_trigger_attempts WHERE owner_id = p_owner_id;
  DELETE FROM app.operational_alerts WHERE owner_id = p_owner_id;
  DELETE FROM app.operational_events WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_operational_state WHERE owner_id = p_owner_id;
  DELETE FROM app.scheduled_run_slots WHERE owner_id = p_owner_id;

  DELETE FROM app.run_evidence WHERE owner_id = p_owner_id;
  DELETE FROM app.source_search_receipts WHERE owner_id = p_owner_id;
  DELETE FROM app.agent_analysis_submissions WHERE owner_id = p_owner_id;
  DELETE FROM app.suggestion_grades WHERE owner_id = p_owner_id;
  DELETE FROM app.suggestions WHERE owner_id = p_owner_id;
  DELETE FROM app.market_publications WHERE owner_id = p_owner_id;
  DELETE FROM app.decision_evaluations WHERE owner_id = p_owner_id;
  DELETE FROM app.stock_observations WHERE owner_id = p_owner_id;
  DELETE FROM app.daily_snapshots WHERE owner_id = p_owner_id;
  DELETE FROM app.lessons WHERE owner_id = p_owner_id;
  DELETE FROM app.radar WHERE owner_id = p_owner_id;
  DELETE FROM app.paper_watches WHERE owner_id = p_owner_id;
  DELETE FROM app.market_quote_cache WHERE owner_id = p_owner_id;
  DELETE FROM app.corporate_action_states WHERE owner_id = p_owner_id;

  DELETE FROM app.transactions WHERE owner_id = p_owner_id AND event_type = 'void';
  DELETE FROM app.transactions WHERE owner_id = p_owner_id;
  DELETE FROM app.portfolio_commands WHERE owner_id = p_owner_id;
  DELETE FROM app.holdings WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_investment_plans WHERE owner_id = p_owner_id;
  DELETE FROM app.dry_powder WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_ledger_counters WHERE owner_id = p_owner_id;

  DELETE FROM app.analysis_runs WHERE owner_id = p_owner_id;
  DELETE FROM app.market_gateway_requests WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_run_allowances WHERE owner_id = p_owner_id;
  DELETE FROM app.app_api_audit_events WHERE owner_id = p_owner_id;
  DELETE FROM app.app_api_rate_limits WHERE owner_id = p_owner_id;
  DELETE FROM app.analysis_schedules WHERE owner_id = p_owner_id;
  DELETE FROM app.agent_connections WHERE owner_id = p_owner_id;
  DELETE FROM app.notification_preferences WHERE owner_id = p_owner_id;
  DELETE FROM app.telegram_links WHERE owner_id = p_owner_id;
  DELETE FROM app.user_consents WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_policy_overrides WHERE owner_id = p_owner_id;
  DELETE FROM app.single_owner_migration_receipts WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_ledger_reset_receipts WHERE owner_id = p_owner_id;
  DELETE FROM app.app_admins WHERE user_id = p_owner_id;
  DELETE FROM app.account_deletion_requests WHERE owner_id = p_owner_id;
  DELETE FROM app.account_step_up_receipts WHERE owner_id = p_owner_id;
  DELETE FROM app.account_step_up_challenges WHERE owner_id = p_owner_id;
  PERFORM set_config('stock_agent.account_purge_owner', '', true);

  FOR target_table IN
    SELECT column_row.table_name
    FROM information_schema.columns column_row
    JOIN pg_catalog.pg_class table_row ON table_row.relname = column_row.table_name
    JOIN pg_catalog.pg_namespace schema_row
      ON schema_row.oid = table_row.relnamespace AND schema_row.nspname = column_row.table_schema
    WHERE column_row.table_schema = 'app' AND column_row.column_name = 'owner_id'
      AND column_row.table_name <> 'deletion_tombstones' AND table_row.relkind = 'r'
    ORDER BY column_row.table_name
  LOOP
    EXECUTE format('SELECT count(*) FROM app.%I WHERE owner_id = $1', target_table)
      INTO residue_count USING p_owner_id;
    IF residue_count <> 0 THEN
      RAISE EXCEPTION 'account purge verification failed for %', target_table;
    END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM app.app_admins WHERE user_id = p_owner_id) THEN
    RAISE EXCEPTION 'account purge verification failed for app_admins';
  END IF;
  RETURN jsonb_build_object(
    'status', 'ready_for_auth_deletion',
    'deletion_request_id', p_deletion_request_id,
    'deleted_at', observed_at,
    'archives_expire_after', observed_at + interval '35 days',
    'duplicate', false
  );
END
$$;

CREATE OR REPLACE FUNCTION app.operator_preview_ledger_reset(
  p_owner_id UUID,
  p_step_up_receipt_id UUID
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE result JSONB;
BEGIN
  IF p_owner_id IS NULL OR p_step_up_receipt_id IS NULL OR NOT EXISTS (
    SELECT 1 FROM app.account_step_up_receipts
    WHERE owner_id = p_owner_id AND id = p_step_up_receipt_id
      AND consumed_at IS NULL AND expires_at > clock_timestamp()
  ) THEN RAISE EXCEPTION 'fresh step up required' USING ERRCODE = '42501'; END IF;
  SELECT jsonb_build_object(
    'holdings', (SELECT count(*) FROM app.holdings WHERE owner_id = p_owner_id),
    'transactions', (SELECT count(*) FROM app.transactions WHERE owner_id = p_owner_id),
    'commands', (SELECT count(*) FROM app.portfolio_commands WHERE owner_id = p_owner_id),
    'plans', (SELECT count(*) FROM app.owner_investment_plans WHERE owner_id = p_owner_id),
    'dry_powder', (SELECT count(*) FROM app.dry_powder WHERE owner_id = p_owner_id)
  ) INTO result;
  RETURN result;
END
$$;

CREATE OR REPLACE FUNCTION app.operator_apply_ledger_reset(
  p_owner_id UUID,
  p_step_up_receipt_id UUID,
  p_export_digest TEXT,
  p_confirmation TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE counts JSONB;
DECLARE reset_id UUID := extensions.gen_random_uuid();
BEGIN
  IF p_export_digest !~ '^[0-9a-f]{64}$'
     OR p_confirmation <> 'RESET ' || p_owner_id::text THEN
    RAISE EXCEPTION 'reset confirmation mismatch' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('ledger-reset:' || p_owner_id::text, 0));
  counts := app.operator_preview_ledger_reset(p_owner_id, p_step_up_receipt_id);
  UPDATE app.account_step_up_receipts SET consumed_at = clock_timestamp()
    WHERE owner_id = p_owner_id AND id = p_step_up_receipt_id
      AND consumed_at IS NULL AND expires_at > clock_timestamp();
  IF NOT FOUND THEN RAISE EXCEPTION 'fresh step up required' USING ERRCODE = '42501'; END IF;
  PERFORM set_config('stock_agent.ledger_reset_owner', p_owner_id::text, true);
  DELETE FROM app.telegram_callback_tokens WHERE owner_id = p_owner_id;
  DELETE FROM app.transactions WHERE owner_id = p_owner_id AND event_type = 'void';
  DELETE FROM app.transactions WHERE owner_id = p_owner_id;
  DELETE FROM app.portfolio_commands WHERE owner_id = p_owner_id;
  DELETE FROM app.holdings WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_investment_plans WHERE owner_id = p_owner_id;
  DELETE FROM app.dry_powder WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_ledger_counters WHERE owner_id = p_owner_id;
  PERFORM set_config('stock_agent.ledger_reset_owner', '', true);
  IF EXISTS (SELECT 1 FROM app.holdings WHERE owner_id = p_owner_id)
     OR EXISTS (SELECT 1 FROM app.transactions WHERE owner_id = p_owner_id) THEN
    RAISE EXCEPTION 'ledger reset verification failed';
  END IF;
  INSERT INTO app.owner_ledger_reset_receipts(
    id, owner_id, step_up_receipt_id, export_digest, row_counts
  ) VALUES (reset_id, p_owner_id, p_step_up_receipt_id, p_export_digest, counts);
  RETURN jsonb_build_object('status', 'reset', 'reset_receipt_id', reset_id, 'row_counts', counts);
END
$$;

-- Preserve append-only behavior for every normal path. Only the ungranted operator reset function
-- can set this transaction-local owner marker before deleting that owner's ledger rows.
CREATE OR REPLACE FUNCTION app.reject_ledger_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF TG_OP = 'DELETE'
     AND (
       current_setting('stock_agent.ledger_reset_owner', true) = OLD.owner_id::text
       OR current_setting('stock_agent.account_purge_owner', true) = OLD.owner_id::text
     ) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'ledger is append-only' USING ERRCODE = '55000';
END
$$;
ALTER FUNCTION app.reject_ledger_mutation() OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.reject_ledger_mutation()
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION public.reject_decision_evaluation_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF TG_OP = 'DELETE'
     AND current_setting('stock_agent.account_purge_owner', true) = OLD.owner_id::text THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'decision evaluations are append-only' USING ERRCODE = '55000';
END
$$;
ALTER FUNCTION public.reject_decision_evaluation_mutation()
  OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION public.reject_decision_evaluation_mutation()
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

DO $$
DECLARE signature REGPROCEDURE;
BEGIN
  FOREACH signature IN ARRAY ARRAY[
    'app.accept_owner_consent(uuid,jsonb)'::regprocedure,
    'app.create_account_step_up_challenge(uuid,jsonb)'::regprocedure,
    'app.complete_account_step_up(uuid,jsonb)'::regprocedure,
    'app.assert_fresh_step_up(uuid,uuid,text,boolean)'::regprocedure,
    'app.export_owner_account(uuid,jsonb)'::regprocedure,
    'app.csv_safe_cell(text)'::regprocedure,
    'app.export_owner_ledger(uuid,jsonb)'::regprocedure,
    'app.request_account_deletion(uuid,jsonb)'::regprocedure,
    'app.confirm_account_deletion(uuid,jsonb)'::regprocedure,
    'app.record_account_telegram_cleanup(uuid,jsonb)'::regprocedure,
    'app.cancel_account_deletion(uuid,jsonb)'::regprocedure,
    'app.operator_prepare_account_deletion(uuid,uuid,text)'::regprocedure,
    'app.operator_preview_ledger_reset(uuid,uuid)'::regprocedure,
    'app.operator_apply_ledger_reset(uuid,uuid,text,text)'::regprocedure
  ] LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO stock_agent_migration_owner', signature);
    EXECUTE format(
      'REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated, service_role, '
      'stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup',
      signature
    );
  END LOOP;
END
$$;

-- Offline invitation bootstrap. The service-role credential is allowed only in the operator CLI;
-- no runtime function contains it, and ordinary authenticated users cannot execute this RPC.
GRANT SELECT (id) ON auth.users TO stock_agent_migration_owner;
CREATE OR REPLACE FUNCTION api.operator_initialize_invited_user(p_owner_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
  IF p_owner_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM auth.users WHERE id = p_owner_id)
     OR EXISTS (SELECT 1 FROM app.deletion_tombstones WHERE owner_id = p_owner_id)
     OR EXISTS (SELECT 1 FROM app.profiles WHERE id = p_owner_id) THEN
    RAISE EXCEPTION 'invitation identity unavailable' USING ERRCODE = '42501';
  END IF;
  INSERT INTO app.profiles(id, status) VALUES (p_owner_id, 'invited');
  INSERT INTO app.notification_preferences(owner_id) VALUES (p_owner_id);
  RETURN jsonb_build_object('status', 'invited');
END
$$;
ALTER FUNCTION api.operator_initialize_invited_user(UUID) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION api.operator_initialize_invited_user(UUID)
  FROM PUBLIC, anon, authenticated,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION api.operator_initialize_invited_user(UUID) TO service_role;
GRANT USAGE ON SCHEMA api TO service_role;

DROP VIEW IF EXISTS api.account_status;
CREATE VIEW api.account_status WITH (security_invoker = true) AS
SELECT profile.status AS account_status,
       deletion.status AS deletion_status,
       deletion.requested_at,
       deletion.cancel_until,
       deletion.delete_by,
       deletion.telegram_cleanup_status,
       deletion.cancelled_at,
       deletion.completed_at
FROM app.profiles profile
LEFT JOIN LATERAL (
  SELECT request.status, request.requested_at, request.cancel_until, request.delete_by,
         request.telegram_cleanup_status, request.cancelled_at, request.completed_at
  FROM app.account_deletion_requests request
  WHERE request.owner_id = profile.id
  ORDER BY request.created_at DESC LIMIT 1
) deletion ON true;
ALTER VIEW api.account_status OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.account_status FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.account_status TO authenticated;

-- Once deletion starts, user JWTs may reach only the lifecycle view. Machine roles retain access
-- long enough to finish revocation/cleanup, while every normal invoker view becomes empty.
DO $$
DECLARE target_table TEXT;
BEGIN
  FOREACH target_table IN ARRAY ARRAY[
    'notification_preferences','analysis_schedules','agent_connections','telegram_links',
    'telegram_pairing_codes','telegram_callback_tokens','telegram_deliveries',
    'run_evidence','source_search_receipts','market_quote_cache','corporate_action_states',
    'agent_analysis_submissions','scheduled_run_slots','routine_trigger_attempts',
    'owner_run_allowances','operational_events','owner_operational_state','operational_alerts',
    'owner_ledger_counters','app_api_rate_limits','app_api_audit_events','holdings',
    'analysis_runs','transactions','portfolio_commands','telegram_updates','suggestions',
    'suggestion_grades','stock_observations','daily_snapshots','dry_powder','radar','lessons',
    'paper_watches','market_gateway_requests','decision_evaluations','market_publications',
    'owner_investment_plans'
  ] LOOP
    EXECUTE format(
      'CREATE POLICY %I ON app.%I AS RESTRICTIVE FOR ALL TO authenticated '
      'USING (auth.uid() IS NOT NULL AND EXISTS ('
      'SELECT 1 FROM app.profiles lifecycle_profile '
      'WHERE lifecycle_profile.id = (SELECT auth.uid()) AND lifecycle_profile.status = ''active'')) '
      'WITH CHECK (auth.uid() IS NOT NULL AND EXISTS ('
      'SELECT 1 FROM app.profiles lifecycle_profile '
      'WHERE lifecycle_profile.id = (SELECT auth.uid()) AND lifecycle_profile.status = ''active''))',
      target_table || '_active_account_guard', target_table
    );
  END LOOP;
END
$$;

-- Keep the previous reviewed dispatcher private, then add a lifecycle-aware public wrapper.
ALTER FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB) SET SCHEMA app;
ALTER FUNCTION app.app_dispatch(TEXT, UUID, TEXT, JSONB) RENAME TO dispatch_active_request;
REVOKE ALL ON FUNCTION app.dispatch_active_request(TEXT, UUID, TEXT, JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION api.app_dispatch(
  p_route TEXT,
  p_request_id UUID,
  p_ip_digest TEXT,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE owner_value UUID := auth.uid();
DECLARE profile_status TEXT;
DECLARE scope_value TEXT;
DECLARE owner_limit JSONB;
DECLARE client_limit JSONB;
DECLARE data_value JSONB;
DECLARE result_code TEXT;
BEGIN
  IF owner_value IS NULL OR p_request_id IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'UNAUTHORIZED'));
  END IF;
  IF jsonb_typeof(p_request) <> 'object' OR p_ip_digest !~ '^[0-9a-f]{64}$' THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'INVALID_REQUEST'));
  END IF;
  SELECT status INTO profile_status FROM app.profiles WHERE id = owner_value;
  IF profile_status IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'NOT_FOUND'));
  END IF;
  IF profile_status = 'deletion_pending' AND p_route NOT IN (
    'GET /account/status','GET /export/account.json','GET /export/ledger.csv',
    'POST /account/step-up/challenge','POST /account/step-up/complete',
    'POST /account/delete/confirm','POST /account/delete/cancel',
    'POST /account/delete/cleanup-result'
  ) THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'ACCOUNT_DELETION_PENDING'));
  END IF;
  IF profile_status = 'invited' AND p_route NOT IN (
    'POST /consents/accept','GET /account/status','GET /export/account.json',
    'GET /export/ledger.csv','POST /account/step-up/challenge','POST /account/step-up/complete'
  ) THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'CONSENT_REQUIRED'));
  END IF;
  IF p_route = 'POST /telegram/pairing-code' THEN
    IF NOT app.jsonb_has_exact_keys(
      p_request, ARRAY['code_digest','step_up_receipt_id','session_digest']
    ) THEN
      RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'INVALID_REQUEST'));
    END IF;
    PERFORM app.assert_fresh_step_up(
      owner_value, (p_request->>'step_up_receipt_id')::uuid,
      p_request->>'session_digest', false
    );
    RETURN app.dispatch_active_request(
      p_route, p_request_id, p_ip_digest,
      p_request - 'step_up_receipt_id' - 'session_digest'
    );
  END IF;
  IF p_route = 'POST /connections/handshake' THEN
    IF NOT app.jsonb_has_exact_keys(
      p_request, ARRAY['connection_id','trigger_url','trigger_token',
        'step_up_receipt_id','session_digest']
    ) THEN
      RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'INVALID_REQUEST'));
    END IF;
    PERFORM app.assert_fresh_step_up(
      owner_value, (p_request->>'step_up_receipt_id')::uuid,
      p_request->>'session_digest', false
    );
    RETURN app.dispatch_active_request(
      p_route, p_request_id, p_ip_digest,
      p_request - 'step_up_receipt_id' - 'session_digest'
    );
  END IF;
  IF p_route NOT IN (
    'POST /consents/accept','GET /account/status','GET /export/account.json',
    'GET /health/operator',
    'GET /export/ledger.csv','POST /account/step-up/challenge','POST /account/step-up/complete',
    'POST /account/delete/request','POST /account/delete/confirm','POST /account/delete/cancel',
    'POST /account/delete/cleanup-result'
  ) THEN
    RETURN app.dispatch_active_request(p_route, p_request_id, p_ip_digest, p_request);
  END IF;

  scope_value := CASE
    WHEN p_route = 'POST /consents/accept' THEN 'consent'
    WHEN p_route = 'GET /account/status' THEN 'account_status'
    WHEN p_route = 'GET /health/operator' THEN 'operator_health'
    WHEN p_route LIKE 'GET /export/%' THEN 'export'
    WHEN p_route LIKE 'POST /account/step-up/%' THEN 'account_step_up'
    ELSE 'account_delete'
  END;
  owner_limit := app.consume_rate_limit(owner_value, scope_value, 'owner',
    CASE scope_value WHEN 'account_status' THEN 60 WHEN 'operator_health' THEN 30 WHEN 'account_step_up' THEN 10 WHEN 'export' THEN 4 ELSE 5 END,
    CASE scope_value WHEN 'account_status' THEN 60 WHEN 'operator_health' THEN 60 WHEN 'account_step_up' THEN 600 ELSE 86400 END);
  client_limit := app.consume_rate_limit(owner_value, scope_value, p_ip_digest,
    CASE scope_value WHEN 'account_status' THEN 60 WHEN 'operator_health' THEN 30 WHEN 'account_step_up' THEN 10 WHEN 'export' THEN 4 ELSE 5 END,
    CASE scope_value WHEN 'account_status' THEN 60 WHEN 'operator_health' THEN 60 WHEN 'account_step_up' THEN 600 ELSE 86400 END);
  IF NOT (owner_limit->>'allowed')::boolean OR NOT (client_limit->>'allowed')::boolean THEN
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, 'RATE_LIMITED');
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object(
      'code', 'RATE_LIMITED', 'retry_after_seconds', greatest(
        (owner_limit->>'retry_after_seconds')::int,
        (client_limit->>'retry_after_seconds')::int
      )
    ));
  END IF;
  BEGIN
    CASE p_route
      WHEN 'POST /consents/accept' THEN data_value := app.accept_owner_consent(owner_value, p_request);
      WHEN 'GET /account/status' THEN
        SELECT jsonb_build_object(
          'account_status', account_status, 'deletion_status', deletion_status,
          'requested_at', requested_at, 'cancel_until', cancel_until, 'delete_by', delete_by,
          'telegram_cleanup_status', coalesce(telegram_cleanup_status, '{}'::jsonb),
          'cancelled_at', cancelled_at, 'completed_at', completed_at
        ) INTO data_value FROM api.account_status;
      WHEN 'GET /health/operator' THEN data_value := app.read_operator_health(owner_value);
      WHEN 'GET /export/account.json' THEN data_value := app.export_owner_account(owner_value, p_request);
      WHEN 'GET /export/ledger.csv' THEN data_value := app.export_owner_ledger(owner_value, p_request);
      WHEN 'POST /account/step-up/challenge' THEN
        data_value := app.create_account_step_up_challenge(owner_value, p_request);
      WHEN 'POST /account/step-up/complete' THEN
        data_value := app.complete_account_step_up(owner_value, p_request);
      WHEN 'POST /account/delete/request' THEN
        data_value := app.request_account_deletion(owner_value, p_request);
      WHEN 'POST /account/delete/confirm' THEN
        data_value := app.confirm_account_deletion(owner_value, p_request);
      WHEN 'POST /account/delete/cleanup-result' THEN
        data_value := app.record_account_telegram_cleanup(owner_value, p_request);
      WHEN 'POST /account/delete/cancel' THEN
        data_value := app.cancel_account_deletion(owner_value, p_request);
      ELSE RAISE EXCEPTION 'route unavailable' USING ERRCODE = '42501';
    END CASE;
    result_code := upper(coalesce(data_value->>'status', 'OK'));
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, result_code);
    RETURN jsonb_build_object('ok', true, 'data', data_value, 'request_id', p_request_id);
  EXCEPTION
    WHEN invalid_text_representation OR invalid_parameter_value OR check_violation
      OR numeric_value_out_of_range OR datetime_field_overflow THEN result_code := 'INVALID_REQUEST';
    WHEN unique_violation OR serialization_failure THEN result_code := 'CONFLICT';
    WHEN insufficient_privilege THEN result_code := 'NOT_FOUND';
    WHEN OTHERS THEN
      result_code := CASE
        WHEN SQLERRM ~* '(invalid|confirmation mismatch)' THEN 'INVALID_REQUEST'
        WHEN SQLERRM ~* '(fresh step up|unavailable|expired|cancellation window)' THEN 'NOT_FOUND'
        ELSE 'INTERNAL_ERROR'
      END;
  END;
  INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
  VALUES (owner_value, p_request_id, p_route, result_code);
  RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', result_code));
END
$$;
ALTER FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB)
  FROM PUBLIC, anon, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB) TO authenticated;

ALTER DEFAULT PRIVILEGES FOR ROLE stock_agent_migration_owner IN SCHEMA app
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;

-- Execute-only disaster-recovery export contract. The contract snapshots every app table's
-- columns at migration time, so a later unreviewed table or column makes export fail closed.
CREATE TABLE app.backup_restore_receipts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  archive_digest TEXT NOT NULL CHECK (archive_digest ~ '^[0-9a-f]{64}$'),
  source_exported_at TIMESTAMPTZ NOT NULL,
  row_counts JSONB NOT NULL CHECK (
    jsonb_typeof(row_counts) = 'object' AND octet_length(row_counts::text) <= 10000
  ),
  relationship_digest TEXT NOT NULL CHECK (relationship_digest ~ '^[0-9a-f]{64}$'),
  projection_digest TEXT NOT NULL CHECK (projection_digest ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status = 'verified'),
  restored_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE app.backup_restore_receipts OWNER TO stock_agent_migration_owner;
ALTER TABLE app.backup_restore_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.backup_restore_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY backup_restore_receipts_executor_all ON app.backup_restore_receipts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER prevent_backup_restore_receipt_mutation
  BEFORE UPDATE OR DELETE ON app.backup_restore_receipts
  FOR EACH ROW EXECUTE FUNCTION app.prevent_immutable_receipt_mutation();
REVOKE ALL ON app.backup_restore_receipts
  FROM PUBLIC, anon, authenticated, service_role, stock_agent_gateway,
       stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE TABLE machine.backup_dataset_contract (
  name TEXT PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]{1,62}$'),
  disposition TEXT NOT NULL CHECK (
    disposition IN ('include','exclude_transient','exclude_rebuildable')
  ),
  excluded_columns TEXT[] NOT NULL DEFAULT '{}'::text[],
  source_columns TEXT[] NOT NULL DEFAULT '{}'::text[],
  reason_code TEXT NOT NULL CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
  CHECK (disposition = 'include' OR cardinality(excluded_columns) = 0)
);
ALTER TABLE machine.backup_dataset_contract OWNER TO stock_agent_migration_owner;
REVOKE ALL ON machine.backup_dataset_contract
  FROM PUBLIC, anon, authenticated, service_role, stock_agent_gateway,
       stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

INSERT INTO machine.backup_dataset_contract(name, disposition, excluded_columns, reason_code)
VALUES
  ('profiles', 'include', '{}', 'DURABLE_IDENTITY_PROFILE'),
  ('app_admins', 'include', '{}', 'DURABLE_OPERATOR_ROLE'),
  ('user_consents', 'include', '{}', 'DURABLE_CONSENT'),
  ('notification_preferences', 'include', '{}', 'DURABLE_OWNER_SETTING'),
  ('agent_connections', 'include', ARRAY['inbound_token_digest','outbound_trigger_secret_id','trigger_url'], 'SANITIZED_PROVIDER_CONNECTION'),
  ('analysis_schedules', 'include', '{}', 'DURABLE_OWNER_SETTING'),
  ('telegram_links', 'include', '{}', 'ENCRYPTED_OWNER_LINK'),
  ('single_owner_migration_receipts', 'exclude_rebuildable', '{}', 'LEGACY_MIGRATION_RECEIPT'),
  ('owner_policy_overrides', 'include', '{}', 'DURABLE_POLICY'),
  ('holdings', 'include', '{}', 'DURABLE_PORTFOLIO_PROJECTION'),
  ('analysis_runs', 'include', '{}', 'DURABLE_RUN_AUDIT'),
  ('transactions', 'include', '{}', 'DURABLE_LEDGER'),
  ('portfolio_commands', 'include', '{}', 'DURABLE_COMMAND_AUDIT'),
  ('telegram_updates', 'include', '{}', 'BOUNDED_REPLAY_AUDIT'),
  ('suggestions', 'include', '{}', 'DURABLE_RECOMMENDATION'),
  ('suggestion_grades', 'include', '{}', 'DURABLE_OUTCOME_GRADE'),
  ('stock_observations', 'include', '{}', 'DURABLE_RESEARCH'),
  ('daily_snapshots', 'include', '{}', 'DURABLE_MARKET_SNAPSHOT'),
  ('dry_powder', 'include', '{}', 'DURABLE_OWNER_SETTING'),
  ('radar', 'include', '{}', 'DURABLE_WATCHLIST'),
  ('lessons', 'include', '{}', 'DURABLE_RESEARCH'),
  ('paper_watches', 'include', '{}', 'DURABLE_OWNER_RESEARCH'),
  ('market_gateway_requests', 'include', '{}', 'DURABLE_GATEWAY_AUDIT'),
  ('decision_evaluations', 'include', '{}', 'DURABLE_POLICY_AUDIT'),
  ('market_publications', 'include', ARRAY['lease_token'], 'SANITIZED_PUBLICATION_AUDIT'),
  ('owner_investment_plans', 'include', '{}', 'DURABLE_INVESTMENT_PLAN'),
  ('owner_ledger_counters', 'include', '{}', 'DURABLE_LEDGER_SEQUENCE'),
  ('app_api_rate_limits', 'exclude_transient', '{}', 'TRANSIENT_RATE_LIMIT'),
  ('app_api_audit_events', 'include', '{}', 'BOUNDED_REQUEST_AUDIT'),
  ('run_evidence', 'include', '{}', 'DURABLE_EVIDENCE'),
  ('source_search_receipts', 'include', '{}', 'DURABLE_SOURCE_AUDIT'),
  ('market_quote_cache', 'exclude_rebuildable', '{}', 'REFETCH_MARKET_QUOTE'),
  ('corporate_action_states', 'include', '{}', 'DURABLE_SAFETY_STATE'),
  ('agent_analysis_submissions', 'include', '{}', 'DURABLE_PROVIDER_AUDIT'),
  ('scheduled_run_slots', 'exclude_transient', '{}', 'REBUILD_RUN_SLOTS'),
  ('routine_trigger_attempts', 'exclude_transient', '{}', 'TRANSIENT_PROVIDER_TRIGGER'),
  ('owner_run_allowances', 'exclude_transient', '{}', 'TRANSIENT_DAILY_ALLOWANCE'),
  ('operational_events', 'include', '{}', 'DURABLE_OPERATIONAL_AUDIT'),
  ('owner_operational_state', 'include', '{}', 'DURABLE_SAFETY_STATE'),
  ('operational_alerts', 'include', ARRAY['lease_token'], 'SANITIZED_ALERT_AUDIT'),
  ('telegram_pairing_codes', 'exclude_transient', '{}', 'SECRET_PAIRING_CLAIM'),
  ('telegram_callback_tokens', 'exclude_transient', '{}', 'SECRET_CALLBACK_CLAIM'),
  ('telegram_deliveries', 'include', '{}', 'DURABLE_DELIVERY_AUDIT'),
  ('telegram_pairing_deliveries', 'exclude_transient', '{}', 'TRANSIENT_PAIRING_DELIVERY'),
  ('account_step_up_challenges', 'exclude_transient', '{}', 'SECRET_SESSION_CHALLENGE'),
  ('account_step_up_receipts', 'exclude_transient', '{}', 'SECRET_SESSION_RECEIPT'),
  ('account_deletion_requests', 'exclude_transient', '{}', 'REBUILD_DELETION_WORKFLOW'),
  ('deletion_tombstones', 'include', '{}', 'DURABLE_DELETION_TOMBSTONE'),
  ('owner_ledger_reset_receipts', 'include', '{}', 'DURABLE_RESET_AUDIT'),
  ('backup_restore_receipts', 'exclude_rebuildable', '{}', 'TARGET_RESTORE_RECEIPT');

UPDATE machine.backup_dataset_contract AS contract
SET source_columns = catalog.columns
FROM (
  SELECT c.relname AS name, array_agg(a.attname::text ORDER BY a.attnum) AS columns
  FROM pg_catalog.pg_class AS c
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
  WHERE n.nspname = 'app' AND c.relkind = 'r'
    AND a.attnum > 0 AND NOT a.attisdropped
  GROUP BY c.relname
) AS catalog
WHERE contract.name = catalog.name;

DO $$
DECLARE target_table TEXT;
BEGIN
  FOR target_table IN SELECT name FROM machine.backup_dataset_contract LOOP
    EXECUTE format('DROP POLICY IF EXISTS backup_export_executor_all ON app.%I', target_table);
    EXECUTE format(
      'CREATE POLICY backup_export_executor_all ON app.%I FOR SELECT '
      'TO stock_agent_migration_owner USING (true)', target_table
    );
  END LOOP;
END
$$;

GRANT SELECT (id, email) ON auth.users TO stock_agent_migration_owner;

CREATE OR REPLACE FUNCTION machine.backup_export_catalog(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE expected_tables TEXT[];
DECLARE actual_tables TEXT[];
DECLARE drifted_table TEXT;
DECLARE table_manifest JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['schema_version'])
     OR jsonb_typeof(p_request->'schema_version') <> 'number'
     OR (p_request->>'schema_version')::int <> 1 THEN
    RAISE EXCEPTION 'backup contract unavailable' USING ERRCODE = '22023';
  END IF;
  SELECT array_agg(name ORDER BY name) INTO expected_tables
  FROM machine.backup_dataset_contract;
  SELECT array_agg(c.relname ORDER BY c.relname) INTO actual_tables
  FROM pg_catalog.pg_class AS c
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  WHERE n.nspname = 'app' AND c.relkind = 'r';
  IF expected_tables IS DISTINCT FROM actual_tables THEN
    RAISE EXCEPTION 'backup contract unavailable: app table drift' USING ERRCODE = '55000';
  END IF;
  SELECT contract.name INTO drifted_table
  FROM machine.backup_dataset_contract AS contract
  WHERE contract.source_columns IS DISTINCT FROM (
    SELECT array_agg(a.attname::text ORDER BY a.attnum)
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
    WHERE n.nspname = 'app' AND c.relname = contract.name
      AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
  )
  LIMIT 1;
  IF drifted_table IS NOT NULL THEN
    RAISE EXCEPTION 'backup contract unavailable: app column drift' USING ERRCODE = '55000';
  END IF;
  SELECT jsonb_agg(jsonb_build_object(
    'name', name,
    'disposition', disposition,
    'columns', source_columns,
    'excluded_columns', excluded_columns,
    'reason_code', reason_code
  ) ORDER BY name) INTO table_manifest
  FROM machine.backup_dataset_contract;
  RETURN jsonb_build_object('schema_version', 1, 'tables', table_manifest);
END
$$;
ALTER FUNCTION machine.backup_export_catalog(JSONB) OWNER TO stock_agent_migration_owner;

CREATE OR REPLACE FUNCTION machine.backup_export_dataset(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE dataset_name TEXT;
DECLARE contract machine.backup_dataset_contract%ROWTYPE;
DECLARE exported_rows JSONB;
DECLARE exported_columns TEXT[];
DECLARE row_patch JSONB := '{}'::jsonb;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['schema_version','dataset'])
     OR jsonb_typeof(p_request->'schema_version') <> 'number'
     OR jsonb_typeof(p_request->'dataset') <> 'string'
     OR (p_request->>'schema_version')::int <> 1 THEN
    RAISE EXCEPTION 'backup contract unavailable' USING ERRCODE = '22023';
  END IF;
  PERFORM machine.backup_export_catalog(jsonb_build_object('schema_version', 1));
  dataset_name := p_request->>'dataset';
  IF dataset_name = 'identity_recovery' THEN
    SELECT coalesce(jsonb_agg(jsonb_build_object(
      'email', lower(auth_user.email), 'owner_id', profile.id
    ) ORDER BY profile.id), '[]'::jsonb)
    INTO exported_rows
    FROM app.profiles AS profile
    JOIN auth.users AS auth_user ON auth_user.id = profile.id
    WHERE auth_user.email IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM app.deletion_tombstones AS tombstone
        WHERE tombstone.owner_id = profile.id
      );
    RETURN jsonb_build_object(
      'dataset', dataset_name,
      'columns', jsonb_build_array('email','owner_id'),
      'rows', exported_rows
    );
  END IF;
  SELECT * INTO contract
  FROM machine.backup_dataset_contract
  WHERE name = dataset_name AND disposition = 'include';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'backup dataset unavailable' USING ERRCODE = '42501';
  END IF;
  IF dataset_name = 'agent_connections' THEN
    row_patch := jsonb_build_object('status', 'disabled', 'last_handshake_at', NULL);
  END IF;
  EXECUTE format(
    'SELECT coalesce(jsonb_agg(row_value ORDER BY row_value::text), ''[]''::jsonb) '
    'FROM (SELECT (to_jsonb(source_row) - $1::text[]) || $2::jsonb AS row_value '
    'FROM app.%I AS source_row) AS exported',
    contract.name
  ) INTO exported_rows USING contract.excluded_columns, row_patch;
  SELECT array_agg(column_name ORDER BY column_name) INTO exported_columns
  FROM unnest(contract.source_columns) AS column_name
  WHERE NOT (column_name = ANY(contract.excluded_columns));
  RETURN jsonb_build_object(
    'dataset', dataset_name,
    'columns', to_jsonb(exported_columns),
    'rows', exported_rows
  );
END
$$;
ALTER FUNCTION machine.backup_export_dataset(JSONB) OWNER TO stock_agent_migration_owner;

REVOKE ALL ON FUNCTION machine.backup_export_catalog(JSONB),
  machine.backup_export_dataset(JSONB)
  FROM PUBLIC, anon, authenticated, service_role, stock_agent_gateway,
       stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT USAGE ON SCHEMA machine TO stock_agent_backup;
GRANT EXECUTE ON FUNCTION machine.backup_export_catalog(JSONB),
  machine.backup_export_dataset(JSONB) TO stock_agent_backup;

-- Bounded operations and retention. Health never returns an owner identifier or portfolio fact.
-- Backup status is rebuilt by the successful off-site exporter and is not itself recovery data.
CREATE TABLE app.backup_status (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  last_success_at TIMESTAMPTZ NOT NULL,
  schema_version INT NOT NULL CHECK (schema_version = 1),
  ciphertext_bytes BIGINT NOT NULL CHECK (ciphertext_bytes BETWEEN 1 AND 537919488),
  ciphertext_digest TEXT NOT NULL CHECK (ciphertext_digest ~ '^[0-9a-f]{64}$'),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- This is intentionally hash-only and carries no owner UUID or command payload. It proves that a
-- terminal command existed after its replay-bearing row reaches the 90-day retention boundary.
CREATE TABLE app.command_retention_receipts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_digest TEXT NOT NULL CHECK (owner_digest ~ '^[0-9a-f]{64}$'),
  command_digest TEXT NOT NULL UNIQUE CHECK (command_digest ~ '^[0-9a-f]{64}$'),
  operation TEXT NOT NULL CHECK (
    operation IN ('buy','sell','sell_all','stop','plan','cancel_plan','correct_transaction')
  ),
  terminal_status TEXT NOT NULL CHECK (terminal_status IN ('cancelled','expired')),
  command_created_at TIMESTAMPTZ NOT NULL,
  compacted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE app.run_evidence
  ADD COLUMN claims_compacted_at TIMESTAMPTZ;
ALTER TABLE app.agent_analysis_submissions
  ADD COLUMN payload_compacted_at TIMESTAMPTZ;

ALTER TABLE app.backup_status OWNER TO stock_agent_migration_owner;
ALTER TABLE app.command_retention_receipts OWNER TO stock_agent_migration_owner;
ALTER TABLE app.backup_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.backup_status FORCE ROW LEVEL SECURITY;
ALTER TABLE app.command_retention_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.command_retention_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY backup_status_executor_all ON app.backup_status
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY command_retention_receipts_executor_all ON app.command_retention_receipts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER prevent_command_retention_receipt_mutation
  BEFORE UPDATE OR DELETE ON app.command_retention_receipts
  FOR EACH ROW EXECUTE FUNCTION app.prevent_immutable_receipt_mutation();
REVOKE ALL ON app.backup_status, app.command_retention_receipts
  FROM PUBLIC, anon, authenticated, service_role, stock_agent_gateway,
       stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

INSERT INTO machine.backup_dataset_contract(
  name, disposition, excluded_columns, reason_code
) VALUES
  ('backup_status', 'exclude_rebuildable', '{}', 'REBUILD_BACKUP_STATUS'),
  ('command_retention_receipts', 'include', '{}', 'DURABLE_BOUNDED_COMMAND_AUDIT');

-- Refresh every reviewed source-column snapshot after the two compaction-marker additions and the
-- new operations tables. Any later schema change will again make backup export fail closed.
UPDATE machine.backup_dataset_contract AS contract
SET source_columns = catalog.columns
FROM (
  SELECT c.relname AS name, array_agg(a.attname::text ORDER BY a.attnum) AS columns
  FROM pg_catalog.pg_class AS c
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
  WHERE n.nspname = 'app' AND c.relkind = 'r'
    AND a.attnum > 0 AND NOT a.attisdropped
  GROUP BY c.relname
) AS catalog
WHERE contract.name = catalog.name;

CREATE OR REPLACE FUNCTION api.public_health()
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  SELECT jsonb_build_object('status', 'ok', 'schema_version', 1)
$$;
ALTER FUNCTION api.public_health() OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION api.public_health()
  FROM PUBLIC, service_role, stock_agent_gateway, stock_agent_scheduler,
       stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION api.public_health() TO anon, authenticated;

CREATE OR REPLACE FUNCTION app.read_operator_health(p_owner_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE provider_active INT;
DECLARE provider_unavailable INT;
DECLARE missed_24 INT;
DECLARE missed_7 INT;
DECLARE usage_count INT;
DECLARE usage_limit INT;
DECLARE projection_checked INT;
DECLARE projection_failed INT;
DECLARE projection_paused INT;
DECLARE backup_at TIMESTAMPTZ;
DECLARE restore_at TIMESTAMPTZ;
DECLARE backup_age INT;
DECLARE restore_age INT;
DECLARE scheduler_status TEXT;
DECLARE provider_status TEXT;
DECLARE backup_status_value TEXT;
DECLARE restore_status_value TEXT;
DECLARE projection_status TEXT;
DECLARE overall_status TEXT;
BEGIN
  IF p_owner_id IS NULL OR NOT EXISTS (
    SELECT 1 FROM app.app_admins WHERE user_id = p_owner_id AND role IN ('operator','admin')
  ) THEN
    RAISE EXCEPTION 'operator unavailable' USING ERRCODE = '42501';
  END IF;

  SELECT count(*) FILTER (WHERE connection.status = 'active'),
         count(*) FILTER (WHERE connection.status <> 'active')
    INTO provider_active, provider_unavailable
  FROM app.agent_connections AS connection
  JOIN app.profiles AS profile ON profile.id = connection.owner_id
  WHERE profile.status = 'active' AND connection.provider = 'claude'
    AND connection.status <> 'revoked';

  SELECT count(*) FILTER (WHERE slot.updated_at >= clock_timestamp() - interval '24 hours'),
         count(*) FILTER (WHERE slot.updated_at >= clock_timestamp() - interval '7 days')
    INTO missed_24, missed_7
  FROM app.scheduled_run_slots AS slot
  WHERE slot.status = 'missed';

  SELECT coalesce(usage.invocation_count, 0), config.monthly_limit
    INTO usage_count, usage_limit
  FROM machine.routine_budget_config AS config
  LEFT JOIN machine.routine_monthly_usage AS usage
    ON usage.usage_month = date_trunc('month', clock_timestamp())::date
  WHERE config.singleton;

  SELECT count(*) FILTER (WHERE state.last_projection_check_at IS NOT NULL),
         count(*) FILTER (WHERE state.last_projection_ok = false),
         count(*) FILTER (WHERE state.mutations_paused)
    INTO projection_checked, projection_failed, projection_paused
  FROM app.owner_operational_state AS state;

  SELECT last_success_at INTO backup_at FROM app.backup_status WHERE singleton;
  SELECT max(restored_at) INTO restore_at FROM app.backup_restore_receipts WHERE status = 'verified';
  backup_age := CASE WHEN backup_at IS NULL THEN NULL ELSE
    greatest(0, floor(extract(epoch FROM (clock_timestamp() - backup_at)) / 3600)::int) END;
  restore_age := CASE WHEN restore_at IS NULL THEN NULL ELSE
    greatest(0, floor(extract(epoch FROM (clock_timestamp() - restore_at)) / 86400)::int) END;

  scheduler_status := CASE WHEN missed_24 > 0 THEN 'degraded' ELSE 'ok' END;
  provider_status := CASE WHEN provider_unavailable > 0 THEN 'degraded' ELSE 'ok' END;
  backup_status_value := CASE
    WHEN backup_at IS NULL THEN 'missing'
    WHEN backup_at < clock_timestamp() - interval '36 hours' THEN 'stale'
    ELSE 'ok'
  END;
  restore_status_value := CASE
    WHEN restore_at IS NULL THEN 'missing'
    WHEN restore_at < clock_timestamp() - interval '30 days' THEN 'stale'
    ELSE 'ok'
  END;
  projection_status := CASE
    WHEN projection_failed > 0 OR projection_paused > 0 THEN 'degraded' ELSE 'ok'
  END;
  overall_status := CASE WHEN scheduler_status = 'ok' AND provider_status = 'ok'
      AND backup_status_value = 'ok' AND restore_status_value = 'ok'
      AND projection_status = 'ok'
    THEN 'ok' ELSE 'degraded' END;

  RETURN jsonb_build_object(
    'status', overall_status,
    'component_status', jsonb_build_object(
      'database', 'ok',
      'scheduler', scheduler_status,
      'provider_adapter', provider_status,
      'backup', backup_status_value,
      'restore', restore_status_value,
      'projections', projection_status
    ),
    'deployed_versions', jsonb_build_object(
      'database_schema', 20260910,
      'provider_contract', 2
    ),
    'provider_adapter', jsonb_build_object(
      'provider', 'claude',
      'active', provider_active,
      'unavailable', provider_unavailable
    ),
    'missed_runs', jsonb_build_object(
      'last_24_hours', missed_24,
      'last_7_days', missed_7
    ),
    'quota_pressure', jsonb_build_object(
      'month_invocations', usage_count,
      'configured_limit', usage_limit
    ),
    'backup', jsonb_build_object(
      'age_hours', backup_age,
      'last_success_at', backup_at
    ),
    'restore', jsonb_build_object(
      'age_days', restore_age,
      'last_verified_at', restore_at
    ),
    'projection', jsonb_build_object(
      'checked', projection_checked,
      'failed', projection_failed,
      'paused', projection_paused
    )
  );
END
$$;
ALTER FUNCTION app.read_operator_health(UUID) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.read_operator_health(UUID)
  FROM PUBLIC, anon, authenticated, service_role, stock_agent_gateway,
       stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION machine.backup_record_success(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE exported_at_value TIMESTAMPTZ;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
       p_request, ARRAY['schema_version','exported_at','ciphertext_bytes','ciphertext_digest']
     ) OR jsonb_typeof(p_request->'schema_version') <> 'number'
     OR (p_request->>'schema_version')::int <> 1
     OR jsonb_typeof(p_request->'ciphertext_bytes') <> 'number'
     OR (p_request->>'ciphertext_bytes')::bigint NOT BETWEEN 1 AND 537919488
     OR p_request->>'ciphertext_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid backup success receipt' USING ERRCODE = '22023';
  END IF;
  BEGIN
    exported_at_value := (p_request->>'exported_at')::timestamptz;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'invalid backup success receipt' USING ERRCODE = '22023';
  END;
  IF exported_at_value > clock_timestamp() + interval '5 minutes'
     OR exported_at_value < clock_timestamp() - interval '7 days' THEN
    RAISE EXCEPTION 'invalid backup success receipt' USING ERRCODE = '22023';
  END IF;
  INSERT INTO app.backup_status(
    singleton, last_success_at, schema_version, ciphertext_bytes, ciphertext_digest, updated_at
  ) VALUES (
    true, exported_at_value, 1, (p_request->>'ciphertext_bytes')::bigint,
    p_request->>'ciphertext_digest', clock_timestamp()
  )
  ON CONFLICT (singleton) DO UPDATE
  SET last_success_at = EXCLUDED.last_success_at,
      schema_version = EXCLUDED.schema_version,
      ciphertext_bytes = EXCLUDED.ciphertext_bytes,
      ciphertext_digest = EXCLUDED.ciphertext_digest,
      updated_at = clock_timestamp()
  WHERE app.backup_status.last_success_at <= EXCLUDED.last_success_at;
  RETURN jsonb_build_object('status', 'recorded', 'last_success_at', exported_at_value);
END
$$;
ALTER FUNCTION machine.backup_record_success(JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION machine.backup_record_success(JSONB)
  FROM PUBLIC, anon, authenticated, service_role, stock_agent_gateway,
       stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION machine.backup_record_success(JSONB) TO stock_agent_backup;

CREATE OR REPLACE FUNCTION machine.scheduler_apply_retention(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE retention_now TIMESTAMPTZ := clock_timestamp();
DECLARE pairing_count INT := 0;
DECLARE callback_count INT := 0;
DECLARE update_count INT := 0;
DECLARE pairing_delivery_count INT := 0;
DECLARE command_count INT := 0;
DECLARE evidence_count INT := 0;
DECLARE submission_count INT := 0;
DECLARE tombstone_count INT := 0;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['maintenance_id'])
     OR p_request->>'maintenance_id' !~
       '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$' THEN
    RAISE EXCEPTION 'invalid retention request' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(86190260910);

  WITH candidates AS MATERIALIZED (
    SELECT code_digest FROM app.telegram_pairing_codes
    WHERE expires_at < retention_now - interval '24 hours'
    ORDER BY expires_at LIMIT 500 FOR UPDATE SKIP LOCKED
  )
  DELETE FROM app.telegram_pairing_codes AS target
  USING candidates WHERE target.code_digest = candidates.code_digest;
  GET DIAGNOSTICS pairing_count = ROW_COUNT;

  WITH candidates AS MATERIALIZED (
    SELECT id FROM app.telegram_callback_tokens
    WHERE expires_at < retention_now - interval '24 hours'
    ORDER BY expires_at LIMIT 500 FOR UPDATE SKIP LOCKED
  )
  DELETE FROM app.telegram_callback_tokens AS target
  USING candidates WHERE target.id = candidates.id;
  GET DIAGNOSTICS callback_count = ROW_COUNT;

  WITH candidates AS MATERIALIZED (
    SELECT telegram_update_id FROM app.telegram_pairing_deliveries
    WHERE recorded_at < retention_now - interval '30 days'
    ORDER BY recorded_at LIMIT 500 FOR UPDATE SKIP LOCKED
  )
  DELETE FROM app.telegram_pairing_deliveries AS target
  USING candidates WHERE target.telegram_update_id = candidates.telegram_update_id;
  GET DIAGNOSTICS pairing_delivery_count = ROW_COUNT;

  WITH candidates AS MATERIALIZED (
    SELECT owner_id, telegram_update_id FROM app.telegram_updates
    WHERE received_at < retention_now - interval '30 days'
    ORDER BY received_at LIMIT 500 FOR UPDATE SKIP LOCKED
  )
  DELETE FROM app.telegram_updates AS target
  USING candidates
  WHERE target.owner_id = candidates.owner_id
    AND target.telegram_update_id = candidates.telegram_update_id;
  GET DIAGNOSTICS update_count = ROW_COUNT;

  WITH candidates AS MATERIALIZED (
    SELECT id, owner_id, operation, status, created_at
    FROM app.portfolio_commands
    WHERE status IN ('cancelled','expired')
      AND created_at < retention_now - interval '90 days'
    ORDER BY created_at LIMIT 500 FOR UPDATE SKIP LOCKED
  ), deleted AS (
    DELETE FROM app.portfolio_commands AS target
    USING candidates WHERE target.id = candidates.id
    RETURNING target.id, target.owner_id, target.operation, target.status, target.created_at
  )
  INSERT INTO app.command_retention_receipts(
    owner_digest, command_digest, operation, terminal_status, command_created_at, compacted_at
  )
  SELECT encode(extensions.digest(owner_id::text, 'sha256'), 'hex'),
         encode(extensions.digest(id::text, 'sha256'), 'hex'),
         operation, status, created_at, retention_now
  FROM deleted
  ON CONFLICT (command_digest) DO NOTHING;
  GET DIAGNOSTICS command_count = ROW_COUNT;

  WITH candidates AS MATERIALIZED (
    SELECT id FROM app.run_evidence
    WHERE claims_compacted_at IS NULL
      AND created_at < retention_now - interval '11 months'
    ORDER BY created_at LIMIT 500 FOR UPDATE SKIP LOCKED
  )
  UPDATE app.run_evidence AS target
  SET claims = '[]'::jsonb, claims_compacted_at = retention_now
  FROM candidates WHERE target.id = candidates.id;
  GET DIAGNOSTICS evidence_count = ROW_COUNT;

  WITH candidates AS MATERIALIZED (
    SELECT id FROM app.agent_analysis_submissions
    WHERE payload_compacted_at IS NULL
      AND created_at < retention_now - interval '11 months'
    ORDER BY created_at LIMIT 500 FOR UPDATE SKIP LOCKED
  )
  UPDATE app.agent_analysis_submissions AS target
  SET payload = '{"compacted":true}'::jsonb, payload_compacted_at = retention_now
  FROM candidates WHERE target.id = candidates.id;
  GET DIAGNOSTICS submission_count = ROW_COUNT;

  PERFORM pg_catalog.set_config('stock_agent.retention_tombstones', 'on', true);
  WITH candidates AS MATERIALIZED (
    SELECT owner_id FROM app.deletion_tombstones
    WHERE archives_expire_after < retention_now
    ORDER BY archives_expire_after LIMIT 500 FOR UPDATE SKIP LOCKED
  )
  DELETE FROM app.deletion_tombstones AS target
  USING candidates WHERE target.owner_id = candidates.owner_id;
  GET DIAGNOSTICS tombstone_count = ROW_COUNT;

  RETURN jsonb_build_object(
    'status', 'completed',
    'pairing_codes', pairing_count,
    'callback_tokens', callback_count,
    'telegram_updates', update_count,
    'pairing_deliveries', pairing_delivery_count,
    'commands_compacted', command_count,
    'evidence_compacted', evidence_count,
    'submissions_compacted', submission_count,
    'tombstones_expired', tombstone_count
  );
END
$$;
ALTER FUNCTION machine.scheduler_apply_retention(JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION machine.scheduler_apply_retention(JSONB)
  FROM PUBLIC, anon, authenticated, service_role, stock_agent_gateway,
       stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION machine.scheduler_apply_retention(JSONB) TO stock_agent_scheduler;

-- END REVIEWED MIGRATION: sql/migrations/20260910000000_retention_recovery.sql
