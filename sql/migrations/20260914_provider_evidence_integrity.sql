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
