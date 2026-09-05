-- Reuse immutable evidence identities across runs without weakening content checks.
DO $$
BEGIN
  IF pg_catalog.to_regprocedure('public.record_market_intelligence(uuid,uuid,jsonb)') IS NOT NULL
     AND pg_catalog.to_regprocedure('public.record_market_intelligence_provider_v2(uuid,uuid,jsonb)') IS NULL THEN
    ALTER FUNCTION public.record_market_intelligence(UUID, UUID, JSONB)
      RENAME TO record_market_intelligence_provider_v2;
  END IF;
END;
$$;

-- Upgrade the v2 wrapper additively: only a relationship-preserving
-- near_duplicate can enter Task 4 as accepted corroborating evidence.
DO $$
DECLARE definition_text TEXT;
BEGIN
  SELECT pg_get_functiondef('public.record_market_intelligence_provider_v2(uuid,uuid,jsonb)'::regprocedure) INTO definition_text;
  IF definition_text IS NULL THEN RAISE EXCEPTION 'provider evidence recorder unavailable' USING ERRCODE='22023'; END IF;
  definition_text := replace(definition_text, '|| jsonb_build_object(''canonical_url'', value->''request_url'')', '|| jsonb_build_object(''canonical_url'', value->''request_url'') || CASE WHEN value->>''disposition''=''near_duplicate'' THEN jsonb_build_object(''disposition'',''accepted'',''drop_reason'',NULL) ELSE ''{}''::jsonb END');
  EXECUTE definition_text;
END;
$$;

CREATE OR REPLACE FUNCTION public.reuse_market_source_item_if_immutable()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE existing_row public.market_source_items%ROWTYPE;
BEGIN
  SELECT * INTO existing_row FROM public.market_source_items WHERE id=NEW.id;
  IF NOT FOUND THEN RETURN NEW; END IF;
  IF existing_row.provider IS DISTINCT FROM NEW.provider
     OR existing_row.upstream_item_id IS DISTINCT FROM NEW.upstream_item_id
     OR existing_row.canonical_url IS DISTINCT FROM NEW.canonical_url
     OR existing_row.published_at IS DISTINCT FROM NEW.published_at
     OR existing_row.effective_at IS DISTINCT FROM NEW.effective_at
     OR existing_row.title IS DISTINCT FROM NEW.title
     OR existing_row.normalized_text IS DISTINCT FROM NEW.normalized_text
     OR existing_row.canonical_content IS DISTINCT FROM NEW.canonical_content
     OR existing_row.content_hash IS DISTINCT FROM NEW.content_hash
     OR existing_row.metadata IS DISTINCT FROM NEW.metadata THEN
    RAISE EXCEPTION 'conflicting immutable market source item identity' USING ERRCODE='22023';
  END IF;
  RETURN NULL;
END;
$$;
DROP TRIGGER IF EXISTS market_source_items_reuse_immutable ON public.market_source_items;
CREATE TRIGGER market_source_items_reuse_immutable BEFORE INSERT ON public.market_source_items
FOR EACH ROW EXECUTE FUNCTION public.reuse_market_source_item_if_immutable();

-- The legacy procedure owns Task 4 event/report completeness checks. Relax only
-- receipt identity equality: each run item is still required to reference its own
-- successful receipt, while its immutable source item may originate in an earlier run.
DO $$
DECLARE definition_text TEXT;
BEGIN
  SELECT pg_get_functiondef('public.record_market_intelligence_legacy(uuid,uuid,jsonb)'::regprocedure)
    INTO definition_text;
  IF definition_text IS NULL THEN
    RAISE EXCEPTION 'market intelligence legacy recorder unavailable' USING ERRCODE='22023';
  END IF;
  definition_text := replace(definition_text, ' AND receipt.id=item.source_receipt_id', '');
  EXECUTE definition_text;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(
  p_run_id UUID, p_completion_id UUID, p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
  -- Provider v2 validates provenance and converts only near_duplicate rows to
  -- accepted Task 4 evidence. Exact duplicates are never passed to the recorder.
  RETURN public.record_market_intelligence_provider_v2(p_run_id,p_completion_id,p_payload);
END;
$$;
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) TO service_role;
