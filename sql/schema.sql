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
