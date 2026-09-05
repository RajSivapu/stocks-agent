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
