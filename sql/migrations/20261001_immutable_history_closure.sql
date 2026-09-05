-- Additive closure of changes previously embedded in immutable 20260907.
-- Cache lineage column already exists from 202609120002; the consolidated
-- controller owns receipt ingestion. Do not replace its final implementation.

CREATE TABLE IF NOT EXISTS public.market_policy_comparisons (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  evaluation_id UUID NOT NULL REFERENCES public.decision_evaluations(id) ON DELETE RESTRICT,
  comparison JSONB NOT NULL CHECK (jsonb_typeof(comparison)='object' AND octet_length(comparison::text)<=16384),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

DROP TRIGGER IF EXISTS market_policy_comparisons_append_only ON public.market_policy_comparisons;
CREATE TRIGGER market_policy_comparisons_append_only BEFORE UPDATE OR DELETE
ON public.market_policy_comparisons FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
ALTER TABLE public.market_policy_comparisons ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_policy_comparisons FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.read_market_evidence_packet(
  p_packet_id UUID,
  p_run_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_result JSONB;
BEGIN
  IF p_packet_id IS NULL OR p_run_id IS NULL THEN
    RAISE EXCEPTION 'packet and run identifiers are required' USING ERRCODE = '22023';
  END IF;

  SELECT jsonb_build_object(
    'id',packet.id,
    'run_id',packet.run_id,
    'packet_hash',packet.packet_hash,
    'packet',packet.packet,
    'evidence_facts',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'candidate_key',candidate->>'candidate_key',
        'evidence_id',item.id,
        'category',CASE
          WHEN item.metadata->>'evidence_category' IN ('quote','fundamentals','technicals','news','event','macro','sector')
            THEN item.metadata->>'evidence_category'
          WHEN item.provider='sec_edgar' THEN 'fundamentals'
          WHEN item.provider IN ('fred','eia','bls','bea') THEN 'macro'
          WHEN item.provider IN ('white_house','doe','dod','federal_register') THEN 'event'
          WHEN item.provider IN ('gdelt','finnhub') THEN 'news'
          WHEN item.provider='yahoo' THEN 'quote'
          ELSE 'unknown' END,
        'source',item.provider,
        'source_status',CASE WHEN receipt.status IN ('succeeded','cache_hit') THEN receipt.status ELSE 'failed' END,
        'authority',CASE
          WHEN item.metadata->>'authority'='official' AND item.provider IN ('sec_edgar','fred','eia','bls','bea','white_house','doe','dod','federal_register') THEN 'official'
          WHEN item.provider IN ('yahoo','alpha_vantage') THEN 'market_data'
          WHEN item.provider IN ('gdelt','finnhub') THEN 'reported'
          ELSE 'unverified' END,
        'published_at',item.published_at,
        'retrieved_at',receipt.retrieved_at,
        'expires_at',receipt.expires_at,
        'reference',item.canonical_url,
        'normalized_text',item.normalized_text,
        'exposure_kind',CASE WHEN item.metadata->>'exposure_kind' IN ('filing','contract','backlog','revenue','capacity','official_fund') THEN item.metadata->>'exposure_kind' ELSE NULL END,
        'relationship_eligible',EXISTS (
          SELECT 1 FROM public.market_candidate_rankings ranking
          WHERE ranking.run_id=packet.run_id AND ranking.candidate_key=candidate->>'candidate_key'
            AND ranking.qualified AND ranking.exposure_item_ids ? item.id::text
        ),
        'claim_key',item.metadata->>'claim_key',
        'claim_polarity',CASE WHEN item.metadata->>'claim_polarity' IN ('affirmed','denied') THEN item.metadata->>'claim_polarity' ELSE NULL END
      ) ORDER BY candidate->>'candidate_key',item.id)
      FROM jsonb_array_elements(packet.packet->'candidates') candidate
      CROSS JOIN LATERAL jsonb_array_elements_text(candidate->'evidence_ids') evidence_id
      JOIN public.market_intelligence_run_items run_item ON run_item.run_id=packet.run_id
        AND run_item.source_item_id=evidence_id::uuid AND run_item.disposition='accepted'
      JOIN public.market_source_items item ON item.id=run_item.source_item_id
      JOIN public.market_source_receipts receipt ON receipt.id=run_item.source_receipt_id
    ),'[]'::jsonb),
    'exposure_facts',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'candidate_key',fact.candidate_key,
        'evidence_id',fact.evidence_id,
        'exposure_kind',fact.exposure_kind,
        'status',fact.freshness,
        'observed_at',fact.observed_at,
        'retrieved_at',fact.retrieved_at
      ) ORDER BY fact.candidate_key,fact.evidence_id)
      FROM (
        SELECT DISTINCT ranking.candidate_key,
          item.id AS evidence_id,
          item.metadata->>'exposure_kind' AS exposure_kind,
          CASE WHEN receipt.expires_at>statement_timestamp()
                    AND COALESCE(item.effective_at,item.published_at) IS NOT NULL
            THEN 'fresh' ELSE 'stale' END AS freshness,
          COALESCE(item.effective_at,item.published_at) AS observed_at,
          receipt.retrieved_at
        FROM public.market_candidate_rankings ranking
        CROSS JOIN LATERAL jsonb_array_elements_text(ranking.exposure_item_ids)
          AS exposure_id(value)
        JOIN public.market_intelligence_run_items run_item
          ON run_item.run_id=ranking.run_id
          AND run_item.source_item_id=exposure_id.value::uuid
          AND run_item.disposition='accepted'
        JOIN public.market_source_items item
          ON item.id=run_item.source_item_id
          AND item.source_receipt_id=run_item.source_receipt_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id
          AND receipt.run_id=ranking.run_id
          AND receipt.status IN ('succeeded','cache_hit')
        WHERE ranking.run_id=packet.run_id AND ranking.qualified
          AND item.metadata->>'authority'='official'
          AND item.metadata->>'exposure_kind' IN (
            'filing','contract','backlog','revenue','capacity','official_fund'
          )
          AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(packet.packet->'candidates') candidate
            WHERE candidate->>'candidate_key'=ranking.candidate_key
              AND candidate->'evidence_ids' ? item.id::text
          )
      ) fact
    ),'[]'::jsonb)
  ) INTO v_result
  FROM public.market_evidence_packets packet
  JOIN public.market_intelligence_run_events event
    ON event.run_id=packet.run_id AND event.status='completed'
  WHERE packet.id=p_packet_id AND packet.run_id=p_run_id AND packet.status='completed';

  IF v_result IS NOT NULL AND octet_length(v_result::text)>131072 THEN
    RAISE EXCEPTION 'packet read exceeds bound' USING ERRCODE = '22023';
  END IF;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.read_market_report_decisions(
  p_run_id UUID, p_packet_id UUID, p_decision_ids JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_result JSONB;
BEGIN
  IF jsonb_typeof(p_decision_ids) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_decision_ids) NOT BETWEEN 1 AND 96 THEN
    RAISE EXCEPTION 'invalid report decision IDs' USING ERRCODE='22023';
  END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'evaluation_id',evaluation.id,'candidate_id',evaluation.candidate_id,
    'run_id',evaluation.run_id,'packet_id',packet.id,'packet_hash',packet.packet_hash,
    'ticker',evaluation.normalized->>'ticker','status',evaluation.policy_status,
    'final_action',evaluation.final_action,
    'final_alert_urgency',CASE WHEN evaluation.policy_status='approved' AND evaluation.final_action='hold'
      THEN evaluation.normalized->'final_alert_urgency' ELSE 'null'::jsonb END,
    'approved_terms',CASE WHEN evaluation.policy_status='approved' AND evaluation.final_action IN ('buy','add','reduce','sell')
      THEN evaluation.normalized->'approved_terms' ELSE 'null'::jsonb END
  ) ORDER BY evaluation.id),'[]'::jsonb) INTO v_result
  FROM public.market_evidence_packets packet
  JOIN public.decision_evaluations evaluation ON evaluation.run_id=packet.run_id
    AND evaluation.analyst->>'packet_id'=packet.id::text
    AND evaluation.policy_version=packet.policy_version
  WHERE packet.id=p_packet_id AND packet.run_id=p_run_id AND packet.status='completed'
    AND p_decision_ids ? evaluation.id::text;
  IF jsonb_array_length(v_result)<>jsonb_array_length(p_decision_ids) THEN
    RAISE EXCEPTION 'report decision provenance mismatch' USING ERRCODE='22023';
  END IF;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_report(
  p_run_id UUID,
  p_idempotency_key TEXT,
  p_report JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_existing public.market_reports%ROWTYPE;
  v_packet public.market_evidence_packets%ROWTYPE;
  v_expected_key TEXT;
  v_expected_id UUID;
BEGIN
  IF p_run_id IS NULL OR p_idempotency_key IS NULL
     OR p_idempotency_key !~ '^[0-9a-f]{64}$' OR jsonb_typeof(p_report)<>'object'
     OR octet_length(p_report::text)>196608
     OR NOT (p_report ?& ARRAY[
       'id','packet_id','market_date','kind','report','report_hash','rendered_text','rendered_hash'
     ])
     OR (p_report - ARRAY[
       'id','packet_id','market_date','kind','report','report_hash','rendered_text','rendered_hash'
     ]) <> '{}'::jsonb
     OR p_report->>'kind' NOT IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')
     OR jsonb_typeof(p_report->'report')<>'object'
     OR octet_length((p_report->'report')::text)>131072
     OR p_report->>'report_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'report_hash' <> encode(extensions.digest(convert_to(
       public.market_canonical_jsonb(p_report->'report'),'UTF8'
     ),'sha256'),'hex')
     OR jsonb_typeof(p_report->'rendered_text')<>'string'
     OR char_length(p_report->>'rendered_text')>14000
     OR p_report->>'rendered_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'rendered_hash' <> encode(extensions.digest(
       convert_to(p_report->>'rendered_text','UTF8'),'sha256'
     ),'hex')
     OR NOT (p_report->'report' ?& ARRAY[
       'source_ids','policy_decision_ids','comparison_ids'
     ])
     OR jsonb_typeof(p_report->'report'->'source_ids') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_report->'report'->'policy_decision_ids') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_report->'report'->'comparison_ids') IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_report->'report'->'source_ids') = 0
     OR jsonb_array_length(p_report->'report'->'policy_decision_ids') = 0
     OR jsonb_array_length(p_report->'report'->'comparison_ids') > 96
     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'source_ids') item
       WHERE item !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'policy_decision_ids') item
       WHERE item !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') THEN
    RAISE EXCEPTION 'invalid market report' USING ERRCODE = '22023';
  END IF;
  SELECT packet.* INTO v_packet
  FROM public.market_evidence_packets packet
  JOIN public.market_intelligence_run_events event
    ON event.run_id=packet.run_id AND event.status='completed'
  WHERE packet.id=(p_report->>'packet_id')::uuid
    AND packet.run_id=p_run_id AND packet.status='completed';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'completed evidence packet unavailable' USING ERRCODE = '22023';
  END IF;
  v_expected_key := encode(extensions.digest(convert_to(
    'v2:' || (p_report->>'kind') || ':' || (p_report->>'market_date') || ':' ||
      v_packet.packet_hash || ':' || (p_report->>'report_hash'),
    'UTF8'
  ), 'sha256'), 'hex');
  v_expected_id := (
    substr(v_expected_key,1,8) || '-' || substr(v_expected_key,9,4) || '-5' ||
    substr(v_expected_key,14,3) || '-8' || substr(v_expected_key,18,3) || '-' ||
    substr(v_expected_key,21,12)
  )::uuid;
  IF p_idempotency_key <> v_expected_key OR p_report->>'id' <> v_expected_id::text
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'source_ids') source_id
       WHERE NOT EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_packet.packet->'evidence') evidence
         JOIN public.market_source_items item ON item.id=(evidence->>'item_id')::uuid
         JOIN public.market_intelligence_run_items run_item ON run_item.source_item_id=item.id
           AND run_item.run_id=p_run_id AND run_item.disposition='accepted'
         JOIN public.market_source_receipts receipt ON receipt.id=run_item.source_receipt_id
           AND receipt.status IN ('succeeded','cache_hit')
         WHERE evidence->>'item_id'=source_id
       )
     )
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'policy_decision_ids') decision_id
       WHERE NOT EXISTS (
         SELECT 1 FROM public.decision_evaluations evaluation
         WHERE evaluation.id=decision_id::uuid AND evaluation.run_id=p_run_id
           AND evaluation.analyst->>'packet_id'=v_packet.id::text
           AND evaluation.policy_version=v_packet.policy_version
       )
     ) OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'comparison_ids') comparison_id
       WHERE NOT EXISTS (
         SELECT 1 FROM public.market_policy_comparisons comparison
         JOIN public.decision_evaluations evaluation ON evaluation.id=comparison.evaluation_id
           AND evaluation.run_id=p_run_id AND evaluation.analyst->>'packet_id'=v_packet.id::text
         WHERE comparison.id=comparison_id::uuid AND comparison.run_id=p_run_id
           AND comparison.packet_id=v_packet.id
           AND p_report->'report'->'policy_decision_ids' ? evaluation.id::text
       )
     ) THEN
    RAISE EXCEPTION 'market report chain mismatch' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-report:' || p_run_id::text || ':' || v_packet.id::text || ':' ||
      (p_report->>'market_date') || ':' || (p_report->>'kind'), 0
  ));
  SELECT * INTO v_existing FROM public.market_reports
  WHERE run_id=p_run_id AND packet_id=v_packet.id
    AND market_date=(p_report->>'market_date')::date AND kind=p_report->>'kind';
  IF FOUND THEN
    IF v_existing.run_id IS DISTINCT FROM p_run_id
       OR v_existing.id IS DISTINCT FROM v_expected_id
       OR v_existing.packet_id IS DISTINCT FROM (p_report->>'packet_id')::uuid
       OR v_existing.report_hash IS DISTINCT FROM p_report->>'report_hash'
       OR v_existing.rendered_text IS DISTINCT FROM p_report->>'rendered_text'
       OR v_existing.rendered_hash IS DISTINCT FROM p_report->>'rendered_hash' THEN
      RAISE EXCEPTION 'market report idempotency mismatch' USING ERRCODE = '22023';
    END IF;
    RETURN jsonb_build_object(
      'report_id',v_existing.id,
      'report_hash',v_existing.report_hash,
      'rendered_hash',v_existing.rendered_hash,
      'duplicate',true
    );
  END IF;
  INSERT INTO public.market_reports(
    id,idempotency_key,run_id,packet_id,market_date,kind,report,report_hash,rendered_text,rendered_hash
  ) VALUES (
    (p_report->>'id')::uuid,p_idempotency_key,p_run_id,v_packet.id,
    (p_report->>'market_date')::date,p_report->>'kind',p_report->'report',
    p_report->>'report_hash',p_report->>'rendered_text',p_report->>'rendered_hash'
  ) RETURNING * INTO v_existing;
  RETURN jsonb_build_object(
    'report_id',v_existing.id,
    'report_hash',v_existing.report_hash,
    'rendered_hash',v_existing.rendered_hash,
    'duplicate',false
  );
END;
$$;

REVOKE ALL ON FUNCTION public.read_market_evidence_packet(UUID, UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.read_market_evidence_packet(UUID, UUID) TO service_role;
REVOKE ALL ON FUNCTION public.read_market_report_decisions(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.read_market_report_decisions(UUID, UUID, JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.record_market_report(UUID, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_report(UUID, TEXT, JSONB) TO service_role;

-- Recovery must preserve policy comparison provenance and its evaluation FK.
REVOKE ALL ON public.decision_evaluations, public.market_policy_comparisons
  FROM stock_agent_release_reader, stock_agent_release_reader_runtime;
GRANT SELECT ON public.decision_evaluations, public.market_policy_comparisons TO stock_agent_release_reader;
DROP POLICY IF EXISTS release_evidence_select ON public.decision_evaluations;
CREATE POLICY release_evidence_select ON public.decision_evaluations
  FOR SELECT TO stock_agent_release_reader USING (true);
DROP POLICY IF EXISTS release_evidence_select ON public.market_policy_comparisons;
CREATE POLICY release_evidence_select ON public.market_policy_comparisons
  FOR SELECT TO stock_agent_release_reader USING (true);
