-- Align protected V2 persistence with canonical non-action output.
-- Empty bounded research and retained near-duplicates remain suggestion-only.

CREATE OR REPLACE FUNCTION public.validate_market_evidence_packet_v2(
  p_run_id UUID,p_policy_version INT,p_packet JSONB
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  c JSONB;
  e JSONB;
  r JSONB;
  a JSONB;
  s JSONB;
  l JSONB;
  v_candidate_hash TEXT;
  v_suitability_hash TEXT;
  v_observed TIMESTAMPTZ;
BEGIN
  IF jsonb_typeof(p_packet)<>'object' OR p_packet->>'contract_version'<>'2'
     OR p_packet->'execution_allowed'<>'false'::jsonb
     OR NOT (p_packet ?& ARRAY[
       'action_candidates','contract_version','coverage','evidence','execution_allowed',
       'limitations','observed_at','omissions','policy_version','research_candidates','run_id'
     ])
     OR (p_packet-ARRAY[
       'action_candidates','contract_version','coverage','evidence','execution_allowed',
       'limitations','observed_at','omissions','policy_version','research_candidates','run_id'
     ])<>'{}'::jsonb
     OR p_packet->>'run_id'<>p_run_id::text
     OR (p_packet->>'policy_version')::int<>p_policy_version
     OR p_packet->>'observed_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$'
     OR jsonb_typeof(p_packet->'coverage')<>'object'
     OR jsonb_typeof(p_packet->'limitations')<>'array'
     OR jsonb_typeof(p_packet->'omissions')<>'array'
     OR jsonb_array_length(p_packet->'omissions')>1000
     OR jsonb_typeof(p_packet->'research_candidates')<>'array'
     OR jsonb_array_length(p_packet->'research_candidates')>12
     OR jsonb_typeof(p_packet->'action_candidates')<>'array'
     -- No protected issuer-valuation ledger exists in this schema version.
     -- A caller cannot seal that gate by rehashing a suitability object.
     OR jsonb_array_length(p_packet->'action_candidates')<>0
     OR jsonb_typeof(p_packet->'evidence')<>'array'
     OR jsonb_array_length(p_packet->'evidence')>96
     OR octet_length(p_packet::text)>98304 THEN
    RAISE EXCEPTION 'invalid evidence packet v2 envelope' USING ERRCODE='22023';
  END IF;
  v_observed:=(p_packet->>'observed_at')::timestamptz;
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_packet->'research_candidates') x
    GROUP BY x->>'candidate_key' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_packet->'action_candidates') x
    GROUP BY x->>'candidate_key' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_packet->'evidence') x
    GROUP BY x->>'item_id' HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate evidence packet v2 identity' USING ERRCODE='22023';
  END IF;
  IF NOT public.market_v2_sorted_unique_text_array(p_packet->'limitations',100,false)
     OR p_packet->'evidence'<>COALESCE((
       SELECT jsonb_agg(value ORDER BY value->>'item_id' COLLATE "C")
       FROM jsonb_array_elements(p_packet->'evidence')
     ),'[]'::jsonb)
     OR EXISTS(
       SELECT 1 FROM jsonb_array_elements(p_packet->'omissions') omission
       WHERE jsonb_typeof(omission)<>'object'
          OR NOT(omission ?& ARRAY['candidate_key','item_id','kind','reason','stage'])
          OR (omission-ARRAY['candidate_key','item_id','kind','reason','stage'])<>'{}'::jsonb
          OR COALESCE(omission->>'kind','')='' OR COALESCE(omission->>'reason','')=''
          OR COALESCE(omission->>'stage','')=''
          OR (omission->'item_id'<>'null'::jsonb AND omission->>'item_id'
              !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
     )
     OR EXISTS(
       SELECT 1 FROM jsonb_array_elements(p_packet->'research_candidates') candidate
       WHERE candidate->'ticker'<>'null'::jsonb
       GROUP BY candidate->>'ticker' HAVING count(*)>1
     ) THEN
    RAISE EXCEPTION 'invalid evidence packet v2 ordering or omission identity' USING ERRCODE='22023';
  END IF;

  FOR e IN SELECT value FROM jsonb_array_elements(p_packet->'evidence') LOOP
    IF jsonb_typeof(e)<>'object' OR NOT (e ?& ARRAY[
      'authority','canonical_url','claim_type','content_hash','effective_at','item_id',
      'normalized_text','published_at','reporting_at','retrieved_at','source_identity'
    ]) OR (e-ARRAY[
      'authority','canonical_url','claim_type','content_hash','effective_at','item_id',
      'normalized_text','published_at','reporting_at','retrieved_at','source_identity'
    ])<>'{}'::jsonb OR e->>'item_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR e->>'content_hash' !~ '^[0-9a-f]{64}$' OR e->>'canonical_url' !~ '^https://'
       OR e->>'retrieved_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$'
       OR (e->'published_at'<>'null'::jsonb AND e->>'published_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$')
       OR (e->'reporting_at'<>'null'::jsonb AND e->>'reporting_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$')
       OR (e->'effective_at'<>'null'::jsonb AND e->>'effective_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$')
       OR jsonb_typeof(e->'source_identity')<>'object'
       OR NOT (e->'source_identity' ?& ARRAY['provider','receipt_id','upstream_item_id'])
       OR ((e->'source_identity')-ARRAY['provider','receipt_id','upstream_item_id'])<>'{}'::jsonb
       OR e->'source_identity'->>'receipt_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(e->'source_identity'->>'provider','')=''
       OR COALESCE(e->'source_identity'->>'upstream_item_id','')=''
       OR NOT EXISTS(
         SELECT 1 FROM public.market_source_items i
         JOIN public.market_intelligence_run_items ri
           ON ri.source_item_id=i.id AND ri.run_id=p_run_id AND ri.disposition IN ('accepted','near_duplicate')
         JOIN public.market_run_source_item_provenance provenance
           ON provenance.run_item_id=ri.id AND provenance.run_id=p_run_id
         JOIN public.market_source_item_provenance source
           ON source.source_item_id=i.id
         JOIN public.market_source_receipts receipt
           ON receipt.id=ri.source_receipt_id AND receipt.run_id=p_run_id
              AND receipt.status IN ('succeeded','cache_hit')
         WHERE i.id=(e->>'item_id')::uuid
           AND i.content_hash=e->>'content_hash'
           AND source.canonical_item_url=e->>'canonical_url'
           AND i.provider=e->'source_identity'->>'provider'
           AND i.upstream_item_id=e->'source_identity'->>'upstream_item_id'
           AND ri.source_receipt_id=(e->'source_identity'->>'receipt_id')::uuid
           AND i.normalized_text=e->>'normalized_text'
           AND i.published_at IS NOT DISTINCT FROM (e->>'published_at')::timestamptz
           AND i.effective_at IS NOT DISTINCT FROM (e->>'effective_at')::timestamptz
           AND provenance.reporting_at IS NOT DISTINCT FROM (e->>'reporting_at')::timestamptz
           AND provenance.retrieved_at=(e->>'retrieved_at')::timestamptz
       ) THEN
      RAISE EXCEPTION 'evidence packet v2 source lineage mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;

  FOR c IN SELECT value FROM jsonb_array_elements(p_packet->'research_candidates') LOOP
    IF jsonb_typeof(c)<>'object' OR NOT (c ?& ARRAY[
      'adverse_paths','candidate_hash','candidate_key','entity_id','event_ids','evidence',
      'exposure_fact_ids','limitations','priority_components','priority_score','research_state',
      'roles','security_id','suitability','theme_ids','ticker'
    ]) OR (c-ARRAY[
      'adverse_paths','candidate_hash','candidate_key','entity_id','event_ids','evidence',
      'exposure_fact_ids','limitations','priority_components','priority_score','research_state',
      'roles','security_id','suitability','theme_ids','ticker'
    ])<>'{}'::jsonb OR c->>'candidate_hash' !~ '^[0-9a-f]{64}$'
       OR c->>'research_state' NOT IN ('unresolved','resolved','exposure_supported','analysis_ready')
       OR jsonb_typeof(c->'event_ids')<>'array' OR jsonb_array_length(c->'event_ids')<1
       OR jsonb_typeof(c->'evidence')<>'array' OR jsonb_array_length(c->'evidence') NOT BETWEEN 1 AND 8
       OR jsonb_typeof(c->'exposure_fact_ids')<>'array'
       OR jsonb_typeof(c->'roles')<>'array' OR jsonb_typeof(c->'theme_ids')<>'array'
       OR jsonb_typeof(c->'limitations')<>'array' OR jsonb_typeof(c->'adverse_paths')<>'array'
       OR NOT public.market_v2_sorted_unique_text_array(c->'event_ids',50,true)
       OR NOT public.market_v2_sorted_unique_text_array(c->'exposure_fact_ids',50,false)
       OR NOT public.market_v2_sorted_unique_text_array(c->'roles',50,false)
       OR NOT public.market_v2_sorted_unique_text_array(c->'theme_ids',50,false)
       OR NOT public.market_v2_sorted_unique_text_array(c->'limitations',100,false)
       OR NOT public.market_v2_sorted_unique_text_array(c->'adverse_paths',50,false)
       OR jsonb_typeof(c->'priority_components')<>'object'
       OR NOT(c->'priority_components' ?& ARRAY['authority_corroboration','exposure','materiality','recency'])
       OR ((c->'priority_components')-ARRAY['authority_corroboration','exposure','materiality','recency'])<>'{}'::jsonb
       OR c->>'priority_score' !~ '^-?(0|[1-9][0-9]*)\.[0-9]{6}$'
       OR EXISTS(SELECT 1 FROM jsonb_each_text(c->'priority_components') x WHERE x.value !~ '^-?(0|[1-9][0-9]*)\.[0-9]{6}$' OR x.value='-0.000000')
       OR c->>'priority_score'='-0.000000' THEN
      RAISE EXCEPTION 'invalid research candidate v2' USING ERRCODE='22023';
    END IF;
    v_candidate_hash:=encode(extensions.digest(convert_to(
      public.market_canonical_jsonb(c-'candidate_hash'),'UTF8'
    ),'sha256'),'hex');
    IF v_candidate_hash<>c->>'candidate_hash' THEN
      RAISE EXCEPTION 'research candidate hash mismatch' USING ERRCODE='22023';
    END IF;
    IF EXISTS(
      SELECT 1 FROM jsonb_array_elements(c->'evidence') ref
      WHERE jsonb_typeof(ref)<>'object'
         OR NOT(ref ?& ARRAY['claim_type','item_id','relationship_eligible','role'])
         OR (ref-ARRAY['claim_type','item_id','relationship_eligible','role'])<>'{}'::jsonb
         OR ref->>'role' NOT IN ('supporting','opposing')
         OR jsonb_typeof(ref->'relationship_eligible')<>'boolean'
         OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_packet->'evidence') shared WHERE shared->>'item_id'=ref->>'item_id')
    ) OR EXISTS(
      SELECT 1 FROM jsonb_array_elements(c->'evidence') ref
      GROUP BY ref->>'item_id' HAVING count(*)>1
    ) THEN
      RAISE EXCEPTION 'candidate evidence relationship mismatch' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS(
      SELECT 1 FROM public.market_candidate_rankings ranking
      JOIN public.market_events event ON event.id=ranking.event_id AND event.run_id=ranking.run_id
      WHERE ranking.run_id=p_run_id AND ranking.candidate_key=c->>'candidate_key'
        AND EXISTS(SELECT 1 FROM jsonb_array_elements(c->'evidence') ref WHERE event.evidence_item_ids ? (ref->>'item_id'))
    ) THEN
      RAISE EXCEPTION 'research candidate lacks source-backed event' USING ERRCODE='22023';
    END IF;
    IF c->>'research_state'='unresolved' THEN
      IF c->'security_id'<>'null'::jsonb OR c->'ticker'<>'null'::jsonb
         OR COALESCE(c->>'entity_id','') !~ '^unresolved:' THEN
        RAISE EXCEPTION 'invalid unresolved research identity' USING ERRCODE='22023';
      END IF;
    ELSIF (
      c->'security_id'='null'::jsonb OR c->'ticker'='null'::jsonb OR c->'entity_id'='null'::jsonb
    ) THEN
      RAISE EXCEPTION 'resolved research identity is missing' USING ERRCODE='22023';
    ELSIF c->'security_id'<>'null'::jsonb AND c->>'candidate_key'<>c->>'security_id' THEN
      RAISE EXCEPTION 'resolved candidate key does not match security identity' USING ERRCODE='22023';
    END IF;
    s:=c->'suitability';
    IF jsonb_typeof(s)<>'object' OR NOT(s ?& ARRAY[
      'component_scores','evaluation_hash','lineage','missing_reasons','state','veto_reasons'
    ]) OR (s-ARRAY[
      'component_scores','evaluation_hash','lineage','missing_reasons','state','veto_reasons'
    ])<>'{}'::jsonb OR s->>'state' NOT IN ('unknown','eligible','vetoed')
       OR s->>'evaluation_hash' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(s->'missing_reasons')<>'array' OR jsonb_typeof(s->'veto_reasons')<>'array'
       OR NOT public.market_v2_sorted_unique_text_array(s->'missing_reasons',50,false)
       OR NOT public.market_v2_sorted_unique_text_array(s->'veto_reasons',50,false)
       OR jsonb_typeof(s->'component_scores')<>'object'
       OR NOT(s->'component_scores' ?& ARRAY['concentration_penalty','duplication_penalty','liquidity','portfolio_relevance'])
       OR ((s->'component_scores')-ARRAY['concentration_penalty','duplication_penalty','liquidity','portfolio_relevance'])<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each_text(s->'component_scores') x WHERE x.value !~ '^-?(0|[1-9][0-9]*)\.[0-9]{6}$' OR x.value='-0.000000') THEN
      RAISE EXCEPTION 'invalid suitability v2' USING ERRCODE='22023';
    END IF;
    v_suitability_hash:=encode(extensions.digest(convert_to(
      public.market_canonical_jsonb(s-'evaluation_hash'),'UTF8'
    ),'sha256'),'hex');
    IF v_suitability_hash<>s->>'evaluation_hash' THEN
      RAISE EXCEPTION 'suitability hash mismatch' USING ERRCODE='22023';
    END IF;
    l:=s->'lineage';
    IF l<>'null'::jsonb AND (
      jsonb_typeof(l)<>'object' OR NOT(l ?& ARRAY[
        'cash_revision','evidence_receipt_ids','observed_at','policy_version',
        'portfolio_revision','quote_as_of','quote_expires_at','quote_receipt_id',
        'reference_expires_at','reference_manifest_id','reference_revision','run_id',
        'security_revision_id'
      ]) OR (l-ARRAY[
        'cash_revision','evidence_receipt_ids','observed_at','policy_version',
        'portfolio_revision','quote_as_of','quote_expires_at','quote_receipt_id',
        'reference_expires_at','reference_manifest_id','reference_revision','run_id',
        'security_revision_id'
      ])<>'{}'::jsonb OR jsonb_typeof(l->'evidence_receipt_ids')<>'object'
      OR l->>'run_id'<>p_run_id::text OR (l->>'policy_version')::int<>p_policy_version
      OR l->>'observed_at'<>p_packet->>'observed_at'
      OR (SELECT count(*) FROM jsonb_object_keys(l->'evidence_receipt_ids'))
         <>jsonb_array_length(c->'evidence')
      OR EXISTS(SELECT 1 FROM jsonb_each_text(l->'evidence_receipt_ids') receipt
                WHERE receipt.key !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                   OR receipt.value !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
      OR EXISTS(
        SELECT 1 FROM jsonb_array_elements(c->'evidence') ref
        JOIN LATERAL (
          SELECT shared->'source_identity'->>'receipt_id' AS receipt_id
          FROM jsonb_array_elements(p_packet->'evidence') shared
          WHERE shared->>'item_id'=ref->>'item_id'
        ) source ON true
        WHERE l->'evidence_receipt_ids'->>(ref->>'item_id')
              IS DISTINCT FROM source.receipt_id
      )
    ) THEN
      RAISE EXCEPTION 'candidate protected lineage mismatch' USING ERRCODE='22023';
    END IF;
    IF c->>'research_state'='analysis_ready' THEN
      IF c->'security_id'='null'::jsonb OR c->'ticker'='null'::jsonb
         OR jsonb_array_length(c->'exposure_fact_ids')=0
         OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(c->'evidence') ref WHERE ref->>'role'='supporting')
         OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(c->'evidence') ref WHERE ref->>'role'='opposing')
         OR jsonb_typeof(l)<>'object'
         OR l->>'run_id'<>p_run_id::text OR (l->>'policy_version')::int<>p_policy_version
         OR (l->>'observed_at')::timestamptz<>v_observed
         OR NOT EXISTS(
           SELECT 1 FROM public.market_reference_run_bindings b
           JOIN public.market_reference_manifests manifest ON manifest.id=b.manifest_id
           JOIN public.market_reference_snapshot_memberships member ON member.manifest_id=b.manifest_id
           JOIN public.market_security_reference_revisions security ON security.id=member.security_revision_id
           WHERE b.run_id=p_run_id AND b.reference_status='healthy'
             AND b.manifest_id=(l->>'reference_manifest_id')::uuid
             AND manifest.revision=(l->>'reference_revision')::int
             AND member.security_revision_id=(l->>'security_revision_id')::uuid
             AND security.eligible AND security.security_id=c->>'security_id'
             AND security.entity_id=c->>'entity_id' AND security.ticker=c->>'ticker'
             AND (l->>'reference_expires_at')::timestamptz>v_observed
         ) OR EXISTS(
           SELECT 1 FROM jsonb_array_elements_text(c->'exposure_fact_ids') claimed
           WHERE NOT EXISTS(
             SELECT 1 FROM public.market_exposure_facts fact
             WHERE fact.id::text=claimed.value
               AND fact.run_id=p_run_id
               AND fact.security_revision_id=(l->>'security_revision_id')::uuid
               AND fact.fact->'value'->>'security_id'=c->>'security_id'
               AND fact.fact->'value'->>'entity_id'=c->>'entity_id'
               AND fact.fact->'value'->>'ticker'=c->>'ticker'
               AND fact.fact->'value'->>'status'='supported'
               AND fact.fact->'value'->>'claim_state'='operational'
               AND fact.fact->'value'->>'business_exposure'='supported'
               AND NOT EXISTS(
                 SELECT 1 FROM jsonb_array_elements_text(fact.fact->'value'->'event_ids') event_id
                 WHERE NOT(c->'event_ids' ? event_id.value)
               )
               AND c->'roles' ? (fact.fact->'value'->>'role')
               AND EXISTS(SELECT 1 FROM jsonb_array_elements(c->'evidence') ref
                 WHERE ref->>'item_id'=fact.fact->'value'->>'source_item_id'
                   AND ref->>'claim_type'='issuer_exposure'
                   AND ref->'relationship_eligible'='true'::jsonb)
           )
         ) THEN
        RAISE EXCEPTION 'analysis-ready lineage mismatch' USING ERRCODE='22023';
      END IF;
    END IF;
    IF s->>'state'='eligible' OR NOT(s->'missing_reasons' ? 'valuation_missing') THEN
      RAISE EXCEPTION 'protected issuer valuation unavailable' USING ERRCODE='22023';
    ELSIF s->>'state'='unknown' AND jsonb_array_length(s->'missing_reasons')=0 THEN
      RAISE EXCEPTION 'unknown suitability requires missing reasons' USING ERRCODE='22023';
    ELSIF s->>'state'='vetoed' AND jsonb_array_length(s->'veto_reasons')=0 THEN
      RAISE EXCEPTION 'vetoed suitability requires veto reasons' USING ERRCODE='22023';
    END IF;
  END LOOP;

  FOR a IN SELECT value FROM jsonb_array_elements(p_packet->'action_candidates') LOOP
    IF jsonb_typeof(a)<>'object' OR NOT(a ?& ARRAY['candidate_hash','candidate_key','suitability_hash'])
       OR (a-ARRAY['candidate_hash','candidate_key','suitability_hash'])<>'{}'::jsonb
       OR NOT EXISTS(
         SELECT 1 FROM jsonb_array_elements(p_packet->'research_candidates') candidate
         WHERE candidate->>'candidate_key'=a->>'candidate_key'
           AND candidate->>'candidate_hash'=a->>'candidate_hash'
           AND candidate->'suitability'->>'evaluation_hash'=a->>'suitability_hash'
           AND candidate->>'research_state'='analysis_ready'
           AND candidate->'suitability'->>'state'='eligible'
       ) THEN
      RAISE EXCEPTION 'action lane is not an exact eligible subset' USING ERRCODE='22023';
    END IF;
  END LOOP;
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
  v_research_only BOOLEAN;
  v_honest_empty BOOLEAN;
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

REVOKE ALL ON FUNCTION public.validate_market_evidence_packet_v2(UUID,INT,JSONB),
  public.record_market_report(UUID,TEXT,JSONB)
  FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.record_market_report(UUID,TEXT,JSONB)
  TO service_role;
