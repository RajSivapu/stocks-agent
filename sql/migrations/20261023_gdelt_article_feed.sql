-- Permit the reviewed GDELT Article List rolling RSS feed after the DOC API
-- began returning persistent rate-limit responses. Keep the old reviewed DOC
-- endpoint valid for historical evidence and reject every other data host path.
DO $$
DECLARE
  definition_text TEXT;
  old_clause CONSTANT TEXT := 'WHEN ''gdelt'' THEN v_request_host=''api.gdeltproject.org''';
  new_clause CONSTANT TEXT := 'WHEN ''gdelt'' THEN (v_request_host=''api.gdeltproject.org'' OR v_row->>''request_url''=''https://data.gdeltproject.org/gdeltv3/gal/feed.rss'')';
BEGIN
  SELECT pg_get_functiondef(
    'public.record_market_intelligence_provider_v2(uuid,uuid,jsonb)'::regprocedure
  ) INTO definition_text;
  IF definition_text IS NULL OR position(old_clause IN definition_text)=0 THEN
    RAISE EXCEPTION 'GDELT provider evidence host clause unavailable' USING ERRCODE='22023';
  END IF;
  EXECUTE replace(definition_text,old_clause,new_clause);
END;
$$;

DO $$
DECLARE
  definition_text TEXT;
  old_clause CONSTANT TEXT := 'WHEN ''gdelt'' THEN v_source_host=''api.gdeltproject.org''';
  new_clause CONSTANT TEXT := 'WHEN ''gdelt'' THEN (v_source_host=''api.gdeltproject.org'' OR v_row->>''canonical_url''=''https://data.gdeltproject.org/gdeltv3/gal/feed.rss'')';
BEGIN
  SELECT pg_get_functiondef(
    'public.record_market_intelligence_legacy(uuid,uuid,jsonb)'::regprocedure
  ) INTO definition_text;
  IF definition_text IS NULL OR position(old_clause IN definition_text)=0 THEN
    RAISE EXCEPTION 'GDELT legacy evidence host clause unavailable' USING ERRCODE='22023';
  END IF;
  EXECUTE replace(definition_text,old_clause,new_clause);
END;
$$;

REVOKE ALL ON FUNCTION public.record_market_intelligence_provider_v2(UUID,UUID,JSONB),
  public.record_market_intelligence_legacy(UUID,UUID,JSONB)
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;

