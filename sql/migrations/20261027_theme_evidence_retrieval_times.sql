-- Return the immutable source receipt timestamps used by theme-memory SQL.
-- A repeated collection can observe the same content-addressed source item at
-- a later time, so caller payload timestamps are not canonical for episode
-- lineage or content hashes.

CREATE OR REPLACE FUNCTION public.read_market_theme_evidence_retrieval_times_v2(
  p_run_id UUID
) RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
  SELECT COALESCE(
    jsonb_object_agg(
      canonical.id::text,
      to_jsonb(to_char(
        canonical.retrieved_at AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
      ))
      ORDER BY canonical.id::text
    ),
    '{}'::jsonb
  )
  FROM (
    SELECT DISTINCT item.id,receipt.retrieved_at
    FROM public.market_evidence_packets packet
    CROSS JOIN LATERAL jsonb_array_elements(packet.packet->'evidence') evidence
    JOIN public.market_source_items item
      ON item.id=(evidence->>'item_id')::uuid
    JOIN public.market_source_receipts receipt
      ON receipt.id=item.source_receipt_id
    WHERE packet.run_id=p_run_id
      AND packet.status='completed'
      AND packet.packet->>'contract_version'='2'
  ) canonical;
$$;

CREATE OR REPLACE FUNCTION public.read_market_intelligence_completion(
  p_run_id UUID,p_completion_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_saved public.market_intelligence_collection_completions%ROWTYPE;
  v_providers JSONB;
BEGIN
  SELECT * INTO v_saved
  FROM public.market_intelligence_collection_completions
  WHERE run_id=p_run_id AND completion_id=p_completion_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT jsonb_object_agg(id::text,provider) INTO v_providers
  FROM public.market_source_quota_reservations
  WHERE run_id=p_run_id;
  RETURN jsonb_build_object(
    'receipt',v_saved.receipt||jsonb_build_object(
      'canonical_evidence_retrieved_at',
      public.read_market_theme_evidence_retrieval_times_v2(p_run_id)
    ),
    'payload',v_saved.payload,
    'providers',v_providers
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(
  p_run_id UUID,p_completion_id UUID,p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_result JSONB;
BEGIN
  IF p_payload->>'status'='completed'
     AND p_payload->'packet'->'packet'->>'contract_version'='2' THEN
    v_result:=public.record_market_intelligence_v2_completion(
      p_run_id,p_completion_id,p_payload
    );
    RETURN v_result||jsonb_build_object(
      'canonical_evidence_retrieved_at',
      public.read_market_theme_evidence_retrieval_times_v2(p_run_id)
    );
  END IF;
  RETURN public.record_market_intelligence_v4_internal(
    p_run_id,p_completion_id,p_payload
  );
END;
$$;

REVOKE ALL ON FUNCTION public.read_market_theme_evidence_retrieval_times_v2(UUID)
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
