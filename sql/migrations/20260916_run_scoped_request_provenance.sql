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
