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
