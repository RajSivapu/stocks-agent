-- Permit one immutable Friday owner-status revision after an earlier
-- suppressed weekly receipt. The original report and publication remain unchanged.

DROP INDEX IF EXISTS public.uq_market_reports_packet_kind_date;
CREATE UNIQUE INDEX uq_market_reports_packet_kind_date
  ON public.market_reports(run_id, packet_id, market_date, kind, report_hash);

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
  v_research_only BOOLEAN;
  v_honest_empty BOOLEAN;
  v_run public.analysis_runs%ROWTYPE;
  v_publication public.market_report_publications%ROWTYPE;
  v_slot_count INT;
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
     OR octet_length(convert_to(p_report->>'rendered_text','UTF8'))>14000
     OR p_report->>'rendered_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'rendered_hash' <> encode(extensions.digest(
       convert_to(p_report->>'rendered_text','UTF8'),'sha256'
     ),'hex')
     OR NOT (p_report->'report' ?& ARRAY[
       'actionable_risk','comparison_ids','full_markdown','intraday_triggered',
       'material_thesis_change','policy_decision_ids','source_ids','suggestion_only',
       'summary','title'
     ])
     OR ((p_report->'report')-ARRAY[
       'actionable_risk','comparison_ids','full_markdown','intraday_triggered',
       'material_thesis_change','policy_decision_ids','source_ids','suggestion_only',
       'summary','title'
     ])<>'{}'::jsonb
     OR jsonb_typeof(p_report->'report'->'title')<>'string'
     OR octet_length(convert_to(p_report->'report'->>'title','UTF8')) NOT BETWEEN 1 AND 200
     OR jsonb_typeof(p_report->'report'->'summary')<>'string'
     OR octet_length(convert_to(p_report->'report'->>'summary','UTF8'))>1000
     OR jsonb_typeof(p_report->'report'->'full_markdown')<>'string'
     OR octet_length(convert_to(p_report->'report'->>'full_markdown','UTF8')) NOT BETWEEN 1 AND 14000
     OR jsonb_typeof(p_report->'report'->'actionable_risk')<>'boolean'
     OR jsonb_typeof(p_report->'report'->'material_thesis_change')<>'boolean'
     OR jsonb_typeof(p_report->'report'->'intraday_triggered')<>'boolean'
     OR p_report->'report'->'suggestion_only'<>'true'::jsonb
     OR jsonb_typeof(p_report->'report'->'source_ids') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_report->'report'->'policy_decision_ids') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_report->'report'->'comparison_ids') IS DISTINCT FROM 'array'
     OR NOT public.market_v2_sorted_unique_text_array(p_report->'report'->'source_ids',96,false)
     OR NOT public.market_v2_sorted_unique_text_array(p_report->'report'->'policy_decision_ids',96,false)
     OR NOT public.market_v2_sorted_unique_text_array(p_report->'report'->'comparison_ids',96,false)
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
  v_research_only:=v_packet.packet->>'contract_version'='2'
    AND jsonb_array_length(v_packet.packet->'research_candidates')>0
    AND jsonb_array_length(v_packet.packet->'action_candidates')=0
    AND jsonb_array_length(p_report->'report'->'policy_decision_ids')=0;
  v_honest_empty:=v_packet.packet->>'contract_version'='2'
    AND jsonb_array_length(v_packet.packet->'research_candidates')=0
    AND jsonb_array_length(v_packet.packet->'action_candidates')=0
    AND jsonb_array_length(v_packet.packet->'evidence')=0
    AND jsonb_array_length(p_report->'report'->'source_ids')=0
    AND jsonb_array_length(p_report->'report'->'policy_decision_ids')=0;
  IF jsonb_array_length(p_report->'report'->'policy_decision_ids')=0
     AND NOT (v_research_only OR v_honest_empty) THEN
    RAISE EXCEPTION 'report policy decision provenance missing' USING ERRCODE='22023';
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
     OR ((v_research_only OR v_honest_empty) AND (
       jsonb_array_length(p_report->'report'->'comparison_ids')<>0
       OR p_report->'report'->'actionable_risk'<>'false'::jsonb
       OR p_report->'report'->'material_thesis_change'<>'false'::jsonb
       OR p_report->'report'->'intraday_triggered'<>'false'::jsonb
       OR (v_research_only AND (
         jsonb_array_length(p_report->'report'->'source_ids')<>(
           SELECT count(DISTINCT ref->>'item_id')
           FROM jsonb_array_elements(v_packet.packet->'research_candidates') candidate
           CROSS JOIN LATERAL jsonb_array_elements(candidate->'evidence') ref
         )
         OR EXISTS(
           SELECT 1 FROM jsonb_array_elements(v_packet.packet->'research_candidates') candidate
           CROSS JOIN LATERAL jsonb_array_elements(candidate->'evidence') ref
           WHERE NOT (p_report->'report'->'source_ids' ? (ref->>'item_id'))
         )
       ))
     ))
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(p_report->'report'->'source_ids') source_id
       WHERE NOT EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_packet.packet->'evidence') evidence
         JOIN public.market_source_items item ON item.id=(evidence->>'item_id')::uuid
         JOIN public.market_intelligence_run_items run_item ON run_item.source_item_id=item.id
           AND run_item.run_id=p_run_id
           AND run_item.disposition IN ('accepted','near_duplicate')
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

  SELECT * INTO v_existing
  FROM public.market_reports
  WHERE id=v_expected_id OR idempotency_key=p_idempotency_key
  FOR UPDATE;
  IF FOUND THEN
    IF v_existing.id IS DISTINCT FROM v_expected_id
       OR v_existing.idempotency_key IS DISTINCT FROM p_idempotency_key
       OR v_existing.run_id IS DISTINCT FROM p_run_id
       OR v_existing.packet_id IS DISTINCT FROM (p_report->>'packet_id')::uuid
       OR v_existing.market_date IS DISTINCT FROM (p_report->>'market_date')::date
       OR v_existing.kind IS DISTINCT FROM p_report->>'kind'
       OR v_existing.report IS DISTINCT FROM p_report->'report'
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

  SELECT count(*) INTO v_slot_count
  FROM public.market_reports
  WHERE run_id=p_run_id AND packet_id=v_packet.id
    AND market_date=(p_report->>'market_date')::date
    AND kind=p_report->>'kind';
  IF v_slot_count > 0 THEN
    SELECT * INTO v_run
    FROM public.analysis_runs
    WHERE id=p_run_id;
    SELECT publication.* INTO v_publication
    FROM public.market_reports report
    JOIN public.market_report_publications publication
      ON publication.report_id=report.id
    WHERE report.run_id=p_run_id AND report.packet_id=v_packet.id
      AND report.market_date=(p_report->>'market_date')::date
      AND report.kind=p_report->>'kind'
    FOR UPDATE OF publication;
    IF v_slot_count <> 1
       OR NOT FOUND
       OR p_report->>'kind' IS DISTINCT FROM 'weekly'
       OR v_run.id IS NULL
       OR v_run.scheduled_phase IS DISTINCT FROM 'post-market'
       OR v_run.scheduled_market_date IS DISTINCT FROM (p_report->>'market_date')::date
       OR EXTRACT(ISODOW FROM (p_report->>'market_date')::date) <> 5
       OR v_publication.status IS DISTINCT FROM 'suppressed'
       OR v_publication.suppression_reason IS DISTINCT FROM 'not_actionable'
       OR jsonb_array_length(v_publication.telegram_message_ids) <> 0
       OR v_publication.telegram_accepted_at IS NOT NULL THEN
      RAISE EXCEPTION 'market report idempotency mismatch' USING ERRCODE = '22023';
    END IF;
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


REVOKE ALL ON FUNCTION public.record_market_report(UUID,TEXT,JSONB)
  FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.record_market_report(UUID,TEXT,JSONB)
  TO service_role;
