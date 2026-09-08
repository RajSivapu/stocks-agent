-- Versioned research/suitability packet contract. V1 packet rows remain byte-for-byte
-- readable; only explicit contract_version=2 rows enter these promotion gates.

CREATE OR REPLACE FUNCTION public.market_portfolio_revision_v1()
RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
  SELECT encode(extensions.digest(convert_to(public.market_canonical_jsonb(
    COALESCE((SELECT jsonb_agg(to_jsonb(h) ORDER BY h.ticker) FROM (
      SELECT ticker,shares::text AS shares,avg_cost::text AS avg_cost,
        bucket,stop::text AS stop,target::text AS target
      FROM public.holdings WHERE shares>0 ORDER BY ticker
    ) h),'[]'::jsonb)
  ),'UTF8'),'sha256'),'hex')
$$;

ALTER TABLE public.market_intelligence_context_inputs
  ADD COLUMN IF NOT EXISTS portfolio_revision TEXT,
  ADD COLUMN IF NOT EXISTS portfolio_valuation_complete BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS cash_revision BIGINT;

ALTER FUNCTION public.refresh_market_intelligence_context(UUID)
  RENAME TO refresh_market_intelligence_context_v1_internal;

CREATE OR REPLACE FUNCTION public.refresh_market_intelligence_context(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_result JSONB;
  v_quotes JSONB;
  v_revision TEXT;
  v_cash BIGINT;
  v_complete BOOLEAN;
BEGIN
  v_result:=public.refresh_market_intelligence_context_v1_internal(p_run_id);
  SELECT COALESCE(jsonb_object_agg(a.ticker,a.quote||jsonb_build_object(
    'receipt_id',a.source_receipt_id,
    'expires_at',a.checkpoint->'receipt'->'expires_at'
  ) ORDER BY a.ticker),'{}'::jsonb)
  INTO v_quotes FROM public.market_intelligence_quote_attempts a
  WHERE a.run_id=p_run_id AND a.status='succeeded';
  v_revision:=public.market_portfolio_revision_v1();
  SELECT revision INTO v_cash FROM public.portfolio_cash_ledger_state
  WHERE singleton=true;
  v_complete:=(SELECT count(*)>0 FROM public.holdings WHERE shares>0)
    AND NOT EXISTS(
      SELECT 1 FROM public.holdings h WHERE h.shares>0
      AND NOT (v_result->'holding_market_values' ? h.ticker)
    )
    AND COALESCE(v_result->'overlap_by_ticker','{}'::jsonb)<>'{}'::jsonb;
  v_result:=jsonb_set(v_result,'{current_quotes}',v_quotes,true)||jsonb_build_object(
    'portfolio_revision',v_revision,
    'portfolio_valuation_complete',v_complete,
    'cash_revision',v_cash::text
  );
  UPDATE public.market_intelligence_context_inputs SET
    current_quotes=v_quotes,portfolio_revision=v_revision,
    portfolio_valuation_complete=v_complete,cash_revision=v_cash
  WHERE run_id=p_run_id;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.market_v2_sorted_unique_text_array(
  p_value JSONB,p_maximum INT,p_nonempty BOOLEAN DEFAULT false
) RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_count INT;
BEGIN
  IF jsonb_typeof(p_value)<>'array' OR jsonb_array_length(p_value)>p_maximum
     OR (p_nonempty AND jsonb_array_length(p_value)=0)
     OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_value) item WHERE jsonb_typeof(item)<>'string') THEN
    RETURN false;
  END IF;
  SELECT count(DISTINCT value) INTO v_count FROM jsonb_array_elements_text(p_value);
  RETURN v_count=jsonb_array_length(p_value)
    AND p_value=COALESCE((SELECT jsonb_agg(value ORDER BY value COLLATE "C")
                         FROM jsonb_array_elements_text(p_value) value),'[]'::jsonb);
END;
$$;

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
     OR jsonb_array_length(p_packet->'action_candidates')>12
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
           ON ri.source_item_id=i.id AND ri.run_id=p_run_id AND ri.disposition='accepted'
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
    IF s->>'state'='eligible' THEN
      IF c->>'research_state'<>'analysis_ready' OR jsonb_array_length(s->'missing_reasons')<>0
         OR jsonb_array_length(s->'veto_reasons')<>0 OR jsonb_typeof(l)<>'object'
         OR l->>'portfolio_revision'<>public.market_portfolio_revision_v1()
         OR (l->>'cash_revision')::bigint IS DISTINCT FROM (
           SELECT revision FROM public.portfolio_cash_ledger_state WHERE singleton=true
         ) OR NOT EXISTS(
           SELECT 1 FROM public.market_intelligence_context_inputs context
           WHERE context.run_id=p_run_id AND context.portfolio_revision=l->>'portfolio_revision'
             AND context.cash_revision=(l->>'cash_revision')::bigint
             AND context.portfolio_valuation_complete
             AND context.liquidity_by_ticker ? (c->>'ticker')
             AND context.overlap_by_ticker ? (c->>'ticker')
         ) OR NOT EXISTS(
           SELECT 1 FROM public.market_intelligence_quote_attempts quote
           JOIN public.market_source_receipts receipt
             ON receipt.id=quote.source_receipt_id AND receipt.run_id=quote.run_id
           WHERE quote.run_id=p_run_id AND quote.status='succeeded'
             AND quote.source_receipt_id=(l->>'quote_receipt_id')::uuid
             AND quote.ticker=c->>'ticker'
             AND (quote.quote->>'as_of')::timestamptz=(l->>'quote_as_of')::timestamptz
             AND receipt.expires_at=(l->>'quote_expires_at')::timestamptz
             AND receipt.expires_at>v_observed
         ) THEN
        RAISE EXCEPTION 'eligible suitability protected input mismatch' USING ERRCODE='22023';
      END IF;
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

CREATE OR REPLACE FUNCTION public.enforce_market_evidence_packet_v2()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
  IF NEW.packet->>'contract_version'='2' THEN
    PERFORM public.validate_market_evidence_packet_v2(NEW.run_id,NEW.policy_version,NEW.packet);
    IF NEW.candidate_count<>jsonb_array_length(NEW.packet->'research_candidates')
       OR NEW.evidence_count<>jsonb_array_length(NEW.packet->'evidence')
       OR NEW.packet_hash<>encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(NEW.packet),'UTF8'
       ),'sha256'),'hex') THEN
      RAISE EXCEPTION 'packet v2 count or hash mismatch' USING ERRCODE='22023';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS market_evidence_packet_v2_guard ON public.market_evidence_packets;
CREATE TRIGGER market_evidence_packet_v2_guard BEFORE INSERT ON public.market_evidence_packets
FOR EACH ROW EXECUTE FUNCTION public.enforce_market_evidence_packet_v2();

ALTER FUNCTION public.read_market_evidence_packet(UUID,UUID)
  RENAME TO read_market_evidence_packet_v1_internal;

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
    ) ORDER BY candidate->>'ticker',item.id),'[]'::jsonb),
    'exposure_facts',COALESCE(jsonb_agg(jsonb_build_object(
      'candidate_key',candidate->>'ticker','evidence_id',item.id,
      'exposure_kind',fact.exposure_kind,
      'status',CASE WHEN receipt.expires_at>statement_timestamp()
        AND fact.fact->'value'->>'status'='supported' THEN 'fresh' ELSE 'stale' END,
      'observed_at',COALESCE(item.effective_at,item.published_at),
      'retrieved_at',receipt.retrieved_at
    ) ORDER BY candidate->>'ticker',item.id) FILTER (WHERE fact.id IS NOT NULL),'[]'::jsonb)
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
     OR NOT public.market_v2_sorted_unique_text_array(p_report->'report'->'source_ids',96,true)
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
  IF jsonb_array_length(p_report->'report'->'policy_decision_ids')=0
     AND NOT v_research_only THEN
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
     OR (v_research_only AND (
       jsonb_array_length(p_report->'report'->'comparison_ids')<>0
       OR p_report->'report'->'actionable_risk'<>'false'::jsonb
       OR p_report->'report'->'material_thesis_change'<>'false'::jsonb
       OR p_report->'report'->'intraday_triggered'<>'false'::jsonb
       OR jsonb_array_length(p_report->'report'->'source_ids')<>(
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


ALTER FUNCTION public.apply_market_decision_bundle_with_cash_snapshot(
  UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT
) RENAME TO apply_market_decision_bundle_with_cash_snapshot_v1_internal;

CREATE OR REPLACE FUNCTION public.apply_market_decision_bundle_with_cash_snapshot(
  p_request_id UUID,p_run_id UUID,p_lease_token UUID,p_policy_version INT,
  p_evaluations JSONB,p_suggestions JSONB,p_publication JSONB,
  p_cash_snapshot_id UUID,p_cash_ledger_watermark BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE evaluation JSONB; packet public.market_evidence_packets%ROWTYPE; candidate JSONB;
BEGIN
  IF jsonb_typeof(p_evaluations)<>'array' THEN
    RAISE EXCEPTION 'invalid decision transaction' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_request_id::text,0));
  IF EXISTS(
    SELECT 1 FROM public.market_publications publication
    WHERE publication.idempotency_key=p_request_id
       OR (p_run_id IS NOT NULL AND publication.run_id=p_run_id)
  ) THEN
    RETURN public.apply_market_decision_bundle_with_cash_snapshot_v1_internal(
      p_request_id,p_run_id,p_lease_token,p_policy_version,p_evaluations,
      p_suggestions,p_publication,p_cash_snapshot_id,p_cash_ledger_watermark
    );
  END IF;
  FOR evaluation IN SELECT value FROM jsonb_array_elements(p_evaluations) LOOP
    IF evaluation->>'policy_status'='approved'
       AND evaluation->>'final_action' IN ('buy','add','reduce','sell') THEN
      SELECT * INTO packet FROM public.market_evidence_packets
      WHERE id=(evaluation->'analyst'->>'packet_id')::uuid
        AND run_id=p_run_id AND policy_version=p_policy_version AND status='completed';
      IF NOT FOUND OR packet.packet->>'contract_version'<>'2' THEN
        RAISE EXCEPTION 'ACTION_LANE_REQUIRED' USING ERRCODE='55000';
      END IF;
      PERFORM public.validate_market_evidence_packet_v2(packet.run_id,packet.policy_version,packet.packet);
      SELECT value INTO candidate FROM jsonb_array_elements(packet.packet->'research_candidates')
      WHERE value->>'ticker'=evaluation->'normalized'->>'ticker';
      IF candidate IS NULL OR NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(packet.packet->'action_candidates') action
        WHERE action->>'candidate_key'=candidate->>'candidate_key'
          AND action->>'candidate_hash'=candidate->>'candidate_hash'
          AND action->>'suitability_hash'=candidate->'suitability'->>'evaluation_hash'
      ) OR candidate->'suitability'->'lineage'->>'portfolio_revision'
            <>public.market_portfolio_revision_v1()
         OR (candidate->'suitability'->'lineage'->>'cash_revision')::bigint
            IS DISTINCT FROM (SELECT revision FROM public.portfolio_cash_ledger_state WHERE singleton=true)
         OR candidate->'suitability'->'lineage'->>'reference_expires_at' IS NULL
         OR (candidate->'suitability'->'lineage'->>'reference_expires_at')::timestamptz
            <=statement_timestamp()
         OR candidate->'suitability'->'lineage'->>'quote_expires_at' IS NULL
         OR (candidate->'suitability'->'lineage'->>'quote_expires_at')::timestamptz
            <=statement_timestamp() THEN
        RAISE EXCEPTION 'ACTION_LANE_CONTEXT_CHANGED' USING ERRCODE='55000';
      END IF;
    END IF;
  END LOOP;
  RETURN public.apply_market_decision_bundle_with_cash_snapshot_v1_internal(
    p_request_id,p_run_id,p_lease_token,p_policy_version,p_evaluations,
    p_suggestions,p_publication,p_cash_snapshot_id,p_cash_ledger_watermark
  );
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid action lane identity' USING ERRCODE='22023';
END;
$$;

-- V2 recovery needs the protected source/event lineage that the packet guard
-- authenticates.  Keep this read-only and confined to the existing release
-- evidence role; the runtime role receives it only through membership.
DO $$
DECLARE name TEXT;
BEGIN
  FOREACH name IN ARRAY ARRAY[
    'market_source_receipts','market_source_items','market_intelligence_run_items',
    'market_source_item_provenance','market_run_source_item_provenance',
    'market_events','market_candidate_rankings'
  ] LOOP
    EXECUTE format(
      'REVOKE ALL ON public.%I FROM stock_agent_release_reader,stock_agent_release_reader_runtime',
      name
    );
    EXECUTE format('GRANT SELECT ON public.%I TO stock_agent_release_reader',name);
    EXECUTE format('DROP POLICY IF EXISTS release_evidence_select ON public.%I',name);
    EXECUTE format(
      'CREATE POLICY release_evidence_select ON public.%I FOR SELECT TO stock_agent_release_reader USING (true)',
      name
    );
  END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION public.market_portfolio_revision_v1(),
  public.market_v2_sorted_unique_text_array(JSONB,INT,BOOLEAN),
  public.validate_market_evidence_packet_v2(UUID,INT,JSONB),
  public.enforce_market_evidence_packet_v2(),
  public.refresh_market_intelligence_context_v1_internal(UUID),
  public.read_market_evidence_packet_v1_internal(UUID,UUID),
  public.apply_market_decision_bundle_with_cash_snapshot_v1_internal(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT)
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON FUNCTION public.refresh_market_intelligence_context(UUID),
  public.read_market_evidence_packet(UUID,UUID),
  public.apply_market_decision_bundle_with_cash_snapshot(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT)
  FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.refresh_market_intelligence_context(UUID),
  public.read_market_evidence_packet(UUID,UUID),
  public.apply_market_decision_bundle_with_cash_snapshot(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT)
  TO service_role;
