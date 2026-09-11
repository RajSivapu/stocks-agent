-- Unresolved research candidates intentionally have no ticker. Their evidence
-- remains authenticated in the packet, but it is not decision-lane evidence
-- and must not be returned through the ticker-keyed evidence-facts contract.

CREATE OR REPLACE FUNCTION public.read_market_evidence_packet(p_packet_id UUID,p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_packet public.market_evidence_packets%ROWTYPE; v_result JSONB;
BEGIN
  SELECT * INTO v_packet FROM public.market_evidence_packets
  WHERE id=p_packet_id AND run_id=p_run_id AND status='completed';
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF v_packet.packet->>'contract_version' IS DISTINCT FROM '2' THEN
    RETURN public.read_market_evidence_packet_v1_internal(p_packet_id,p_run_id);
  END IF;
  PERFORM public.validate_market_evidence_packet_v2(v_packet.run_id,v_packet.policy_version,v_packet.packet);
  SELECT jsonb_build_object(
    'id',v_packet.id,'run_id',v_packet.run_id,'packet_hash',v_packet.packet_hash,
    'packet',v_packet.packet,
    'evidence_facts',COALESCE(jsonb_agg(jsonb_build_object(
      'candidate_key',candidate->>'ticker','evidence_id',item.id,
      'category',CASE WHEN item.provider='sec_edgar' THEN 'fundamentals'
        WHEN item.provider IN ('fred','eia','bls','bea') THEN 'macro'
        WHEN item.provider IN ('white_house','doe','dod','federal_register') THEN 'event'
        WHEN item.provider IN ('gdelt','finnhub') THEN 'news'
        WHEN item.provider='yahoo' THEN 'quote' ELSE 'unknown' END,
      'source',item.provider,'source_status',receipt.status,
      'authority',CASE WHEN item.metadata->>'authority'='official' THEN 'official'
        WHEN item.provider IN ('yahoo','alpha_vantage') THEN 'market_data'
        WHEN item.provider IN ('gdelt','finnhub') THEN 'reported' ELSE 'unverified' END,
      'published_at',item.published_at,'retrieved_at',receipt.retrieved_at,
      'expires_at',receipt.expires_at,'reference',source.canonical_item_url,
      'normalized_text',item.normalized_text,
      'exposure_kind',CASE WHEN ref->>'claim_type'='issuer_exposure'
        THEN COALESCE(fact.exposure_kind,'filing') ELSE NULL END,
      'relationship_eligible',ref->'relationship_eligible',
      'claim_key',item.metadata->>'claim_key',
      'claim_polarity',CASE WHEN item.metadata->>'claim_polarity' IN ('affirmed','denied')
        THEN item.metadata->>'claim_polarity' ELSE NULL END
    ) ORDER BY candidate->>'ticker',item.id)
      FILTER (WHERE candidate->>'ticker' IS NOT NULL),'[]'::jsonb),
    'exposure_facts',COALESCE(jsonb_agg(jsonb_build_object(
      'candidate_key',candidate->>'ticker','evidence_id',item.id,
      'exposure_kind',fact.exposure_kind,
      'status',CASE WHEN receipt.expires_at>statement_timestamp()
        AND fact.fact->'value'->>'status'='supported' THEN 'fresh' ELSE 'stale' END,
      'observed_at',COALESCE(item.effective_at,item.published_at),
      'retrieved_at',receipt.retrieved_at
    ) ORDER BY candidate->>'ticker',item.id)
      FILTER (WHERE candidate->>'ticker' IS NOT NULL AND fact.id IS NOT NULL),'[]'::jsonb)
  ) INTO v_result
  FROM jsonb_array_elements(v_packet.packet->'research_candidates') candidate
  CROSS JOIN LATERAL jsonb_array_elements(candidate->'evidence') ref
  JOIN public.market_source_items item ON item.id=(ref->>'item_id')::uuid
  JOIN public.market_source_item_provenance source ON source.source_item_id=item.id
  JOIN public.market_source_receipts receipt ON receipt.id=item.source_receipt_id
  LEFT JOIN public.market_exposure_facts fact ON fact.run_id=v_packet.run_id
    AND fact.id::text IN (SELECT value FROM jsonb_array_elements_text(candidate->'exposure_fact_ids'))
    AND fact.fact->'value'->>'source_item_id'=item.id::text;
  IF octet_length(v_result::text)>131072 THEN
    RAISE EXCEPTION 'packet read exceeds bound' USING ERRCODE='22023';
  END IF;
  RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.read_market_evidence_packet(UUID,UUID)
  FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.read_market_evidence_packet(UUID,UUID)
  TO service_role;
