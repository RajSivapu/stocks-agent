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
