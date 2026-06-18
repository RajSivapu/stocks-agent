CREATE TABLE IF NOT EXISTS holdings (
  ticker TEXT PRIMARY KEY, shares NUMERIC NOT NULL, avg_cost NUMERIC NOT NULL,
  bucket TEXT, opened_at DATE, notes TEXT);

-- v2.1: trailing-stop fields on holdings
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS stop NUMERIC;
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS target NUMERIC;
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS high_water_price NUMERIC;

CREATE TABLE IF NOT EXISTS transactions (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  ticker TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  qty NUMERIC NOT NULL, price NUMERIC NOT NULL, source TEXT DEFAULT 'owner');

CREATE TABLE IF NOT EXISTS suggestions (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  date DATE NOT NULL, ticker TEXT NOT NULL, action TEXT NOT NULL, bucket TEXT,
  depth TEXT, entry_zone_low NUMERIC, entry_zone_high NUMERIC, valid_until DATE,
  stop NUMERIC, target NUMERIC, confidence TEXT, bull TEXT, bear TEXT,
  decisive_factor TEXT, risk_verdict TEXT, invalidation_level TEXT, reason TEXT,
  score INT, score_growth INT, score_health INT, score_valuation INT,
  risk_band TEXT, score_inputs TEXT, score_partial BOOLEAN DEFAULT false,
  price_at_suggestion NUMERIC);

CREATE TABLE IF NOT EXISTS suggestion_grades (
  id BIGSERIAL PRIMARY KEY, suggestion_id BIGINT REFERENCES suggestions(id),
  graded_at TIMESTAMPTZ DEFAULT now(), result TEXT, price_then NUMERIC,
  price_later NUMERIC, horizon_days INT, note TEXT);

CREATE TABLE IF NOT EXISTS stock_observations (
  id BIGSERIAL PRIMARY KEY, ticker TEXT NOT NULL, obs_date DATE NOT NULL,
  event_type TEXT, summary TEXT, price_reaction TEXT, confidence TEXT,
  source TEXT, created_at TIMESTAMPTZ DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_obs_ticker ON stock_observations(ticker);

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

-- v2.1: owner's personal paper-watch hypotheses (separate from radar + holdings)
CREATE TABLE IF NOT EXISTS paper_watches (
  id BIGSERIAL PRIMARY KEY, ticker TEXT NOT NULL, created DATE NOT NULL,
  entry_ref_price NUMERIC, target_price NUMERIC, hypothetical_amount NUMERIC,
  thesis TEXT, horizon TEXT, status TEXT NOT NULL DEFAULT 'active',
  closed_date DATE, close_price NUMERIC,
  agent_view_at_open TEXT, agent_score_at_open INT,
  created_at TIMESTAMPTZ DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_watches(status);
