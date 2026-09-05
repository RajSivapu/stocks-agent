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
