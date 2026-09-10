-- Keep the 100-task request ceiling while allowing a zero-request selection to
-- close an already saturated retry. Empty selections add no task or provider work.

CREATE OR REPLACE FUNCTION public.seal_market_enrichment_selection(p_run_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  m JSONB; r JSONB; v_hash TEXT; v_manifest_id UUID; v_existing public.market_enrichment_selection_manifests%ROWTYPE;
  v_request_count INT:=0; v_expected JSONB; v_sec_submissions INT:=0;
  v_sec_documents INT:=0; v_yahoo_quotes INT:=0;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>262144
     OR NOT (p_payload ?& ARRAY['manifest','requests'])
     OR (p_payload-ARRAY['manifest','requests'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'requests')<>'array'
     OR jsonb_array_length(p_payload->'requests')>100 THEN
    RAISE EXCEPTION 'invalid enrichment selection' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.analysis_runs a JOIN public.market_intelligence_runs i ON i.id=a.id
                WHERE a.id=p_run_id AND a.status='running') THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  m:=p_payload->'manifest';
  IF jsonb_typeof(m)<>'object'
     OR NOT (m ?& ARRAY['manifest_id','run_id','phase','selection_stage','schema_version','execution_allowed','provider_reservations','deferred_reasons','request_descriptors','semantic_hash'])
     OR (m-ARRAY['manifest_id','run_id','phase','selection_stage','schema_version','execution_allowed','provider_reservations','deferred_reasons','request_descriptors','semantic_hash'])<>'{}'::jsonb
     OR m->>'run_id' IS DISTINCT FROM p_run_id::text OR m->'schema_version'<>'1'::jsonb
     OR m->>'selection_stage' NOT IN ('holding_quotes','initial','filing_documents')
     OR m->'execution_allowed'<>'false'::jsonb OR jsonb_typeof(m->'provider_reservations')<>'object'
     OR jsonb_typeof(m->'deferred_reasons')<>'object' OR jsonb_typeof(m->'request_descriptors')<>'array'
     OR jsonb_array_length(m->'request_descriptors')<>jsonb_array_length(p_payload->'requests')
     OR m->>'semantic_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid enrichment selection manifest' USING ERRCODE='22023';
  END IF;
  v_expected:=CASE m->>'phase'
    WHEN 'pre-market' THEN '{"gdelt_reverse":2,"sec_filing_document":3,"sec_issuer_submissions":3,"yahoo_security_quote":4}'::jsonb
    WHEN 'intraday' THEN '{"gdelt_reverse":1,"sec_filing_document":1,"sec_issuer_submissions":1,"yahoo_security_quote":1}'::jsonb
    WHEN 'post-market' THEN '{"gdelt_reverse":2,"sec_filing_document":2,"sec_issuer_submissions":2,"yahoo_security_quote":2}'::jsonb
    WHEN 'on-demand' THEN '{"gdelt_reverse":1,"sec_filing_document":1,"sec_issuer_submissions":1,"yahoo_security_quote":1}'::jsonb
    ELSE NULL END;
  IF v_expected IS NULL OR m->'provider_reservations'<>v_expected THEN
    RAISE EXCEPTION 'enrichment provider reservation mismatch' USING ERRCODE='22023';
  END IF;
  v_hash:=public.market_enrichment_manifest_semantic_hash_v1(m);
  IF v_hash<>m->>'semantic_hash' THEN
    RAISE EXCEPTION 'enrichment selection hash mismatch' USING ERRCODE='22023';
  END IF;
  v_manifest_id:=(m->>'manifest_id')::uuid;
  SELECT * INTO v_existing FROM public.market_enrichment_selection_manifests
  WHERE run_id=p_run_id AND selection_stage=m->>'selection_stage';
  IF FOUND THEN
    IF v_existing.id<>v_manifest_id OR v_existing.manifest<>m OR v_existing.content_hash<>v_hash
       OR (SELECT count(*) FROM public.market_enrichment_request_descriptors d WHERE d.manifest_id=v_manifest_id)
          <>jsonb_array_length(p_payload->'requests')
       OR EXISTS(
         SELECT 1 FROM jsonb_array_elements(p_payload->'requests') x
         LEFT JOIN public.market_enrichment_request_descriptors d
           ON d.id=(x->>'request_id')::uuid AND d.manifest_id=v_manifest_id
         LEFT JOIN public.market_discovery_stage_tasks t
           ON t.id=d.task_id AND t.run_id=p_run_id
         WHERE jsonb_typeof(x)<>'object'
           OR NOT (x ?& ARRAY['request_id','task_id','stage','provider','capability_id','query_kind','descriptor','dependency_ids','requested_window','request_budget','execution_allowed','descriptor_hash'])
           OR (x-ARRAY['request_id','task_id','stage','provider','capability_id','query_kind','descriptor','dependency_ids','requested_window','request_budget','execution_allowed','descriptor_hash'])<>'{}'::jsonb
           OR d.id IS NULL OR d.task_id<>(x->>'task_id')::uuid
           OR d.provider<>x->>'provider' OR d.capability_id<>x->>'capability_id'
           OR d.query_kind<>x->>'query_kind' OR d.descriptor<>x->'descriptor'
           OR d.content_hash<>x->>'descriptor_hash' OR t.stage<>x->>'stage'
           OR t.dependency_ids<>x->'dependency_ids' OR t.requested_window<>x->'requested_window'
           OR t.request_budget<>(x->>'request_budget')::int OR x->'execution_allowed'<>'false'::jsonb
       ) THEN
      RAISE EXCEPTION 'enrichment selection replay mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'request_count',v_existing.request_count,'duplicate',true);
  END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'requests') x
            GROUP BY (x->>'request_id')::uuid HAVING count(*)>1) THEN
    RAISE EXCEPTION 'duplicate enrichment request identity' USING ERRCODE='22023';
  END IF;
  IF (jsonb_array_length(p_payload->'requests') > 0 AND
      (SELECT count(*) FROM public.market_discovery_stage_tasks WHERE run_id=p_run_id)
       + jsonb_array_length(p_payload->'requests') > 100)
     OR (m->>'selection_stage'='initial' AND (
       SELECT count(DISTINCT x->'descriptor'->>'entity_id')
       FROM jsonb_array_elements(p_payload->'requests') x
     ) > 4) THEN
    RAISE EXCEPTION 'enrichment task capacity exceeded' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_enrichment_selection_manifests(
    id,run_id,selection_stage,phase,request_count,provider_reservations,deferred_reasons,manifest,content_hash
  ) VALUES(
    v_manifest_id,p_run_id,m->>'selection_stage',m->>'phase',jsonb_array_length(p_payload->'requests'),
    m->'provider_reservations',m->'deferred_reasons',m,v_hash
  );
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'requests') LOOP
    IF jsonb_typeof(r)<>'object'
       OR NOT (r ?& ARRAY['request_id','task_id','stage','provider','capability_id','query_kind','descriptor','dependency_ids','requested_window','request_budget','execution_allowed','descriptor_hash'])
       OR (r-ARRAY['request_id','task_id','stage','provider','capability_id','query_kind','descriptor','dependency_ids','requested_window','request_budget','execution_allowed','descriptor_hash'])<>'{}'::jsonb
       OR r->>'request_id' IS DISTINCT FROM r->>'task_id'
       OR r->'execution_allowed'<>'false'::jsonb OR r->>'stage' NOT IN ('enrich','quote','resolve')
       OR jsonb_typeof(r->'descriptor')<>'object' OR jsonb_typeof(r->'dependency_ids')<>'array'
       OR jsonb_array_length(r->'dependency_ids')>32 OR jsonb_typeof(r->'requested_window')<>'object'
       OR (r->>'request_budget')::int<>1 OR r->>'descriptor_hash' !~ '^[0-9a-f]{64}$'
       OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(m->'request_descriptors') x
                     WHERE x->>'request_id'=r->>'request_id' AND x->>'descriptor_hash'=r->>'descriptor_hash') THEN
      RAISE EXCEPTION 'invalid enrichment request descriptor' USING ERRCODE='22023';
    END IF;
    IF NOT (r->'descriptor' ?& ARRAY['adverse_path','cache_key','cik','dependency_task_ids','entity_id','event_ids','hypothesis_ids','instrument_type','priority','reference_manifest_id','reservation_id','role','security_id','security_revision_id','source_item_ids','source_receipt_id','theme_id','ticker'])
       OR r->'descriptor'->'adverse_path' NOT IN ('true'::jsonb,'false'::jsonb)
       OR r->'descriptor'->>'cache_key' !~ '^[0-9a-f]{64}$'
       OR r->'descriptor'->>'cik' !~ '^[0-9]{10}$' OR r->'descriptor'->>'cik'='0000000000'
       OR r->'descriptor'->'dependency_task_ids'<>r->'dependency_ids'
       OR jsonb_typeof(r->'descriptor'->'event_ids')<>'array'
       OR jsonb_array_length(r->'descriptor'->'event_ids') NOT BETWEEN 1 AND 64
       OR jsonb_typeof(r->'descriptor'->'hypothesis_ids')<>'array'
       OR jsonb_array_length(r->'descriptor'->'hypothesis_ids') NOT BETWEEN 1 AND 64
       OR jsonb_typeof(r->'descriptor'->'source_item_ids')<>'array'
       OR jsonb_array_length(r->'descriptor'->'source_item_ids')>64
       OR r->'descriptor'->>'priority' !~ '^(0|[1-9][0-9]?|100)$'
       OR r->'descriptor'->>'source_receipt_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR NOT EXISTS(SELECT 1 FROM public.market_source_quota_reservations q
                     WHERE q.id=(r->'descriptor'->>'reservation_id')::uuid
                       AND q.run_id=p_run_id AND q.provider=r->>'provider'
                       AND q.reserved_requests>(
                         SELECT count(*) FROM public.market_enrichment_request_descriptors used
                         WHERE used.run_id=p_run_id AND used.provider=r->>'provider'
                       )) THEN
      RAISE EXCEPTION 'invalid enrichment request binding' USING ERRCODE='22023';
    END IF;
    IF r->>'query_kind'='issuer_submissions' THEN
      IF m->>'selection_stage'<>'initial' OR r->>'stage'<>'enrich'
         OR r->>'provider'<>'sec_edgar' OR r->>'capability_id'<>'sec_issuer_submissions'
         OR ((r->'descriptor')-ARRAY['adverse_path','cache_key','cik','dependency_task_ids','entity_id','event_ids','hypothesis_ids','instrument_type','issuer_entity_id','priority','reference_manifest_id','reservation_id','role','security_id','security_revision_id','source_item_ids','source_receipt_id','theme_id','ticker'])<>'{}'::jsonb
         OR r->'descriptor'->>'issuer_entity_id'<>r->'descriptor'->>'entity_id' THEN
        RAISE EXCEPTION 'invalid issuer submissions descriptor' USING ERRCODE='22023';
      END IF;
      v_sec_submissions:=v_sec_submissions+1;
    ELSIF r->>'query_kind'='filing_document' THEN
      IF m->>'selection_stage'<>'filing_documents' OR r->>'stage'<>'enrich'
         OR r->>'provider'<>'sec_edgar' OR r->>'capability_id'<>'sec_filing_document'
         OR ((r->'descriptor')-ARRAY['accepted_at','accession_number','adverse_path','cache_key','cik','dependency_task_ids','entity_id','event_ids','filing_date','form','hypothesis_ids','instrument_type','primary_document','priority','reference_manifest_id','reporting_period_end','reservation_id','role','security_id','security_revision_id','source_item_ids','source_receipt_id','submissions_response_hash','theme_id','ticker'])<>'{}'::jsonb
         OR r->'descriptor'->>'accession_number' !~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'
         OR r->'descriptor'->>'primary_document' !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$'
         OR r->'descriptor'->>'form' !~ '^(10-K|10-Q|8-K|20-F|40-F)(/A)?$'
         OR r->'descriptor'->>'submissions_response_hash' !~ '^[0-9a-f]{64}$'
         OR r->'descriptor'->>'filing_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
        RAISE EXCEPTION 'invalid filing document descriptor' USING ERRCODE='22023';
      END IF;
      v_sec_documents:=v_sec_documents+1;
    ELSIF r->>'query_kind'='quote' THEN
      IF m->>'selection_stage' NOT IN ('holding_quotes','initial') OR r->>'stage'<>'quote'
         OR r->>'provider'<>'yahoo' OR r->>'capability_id'<>'yahoo_security_quote'
         OR ((r->'descriptor')-ARRAY['adverse_path','cache_key','cik','dependency_task_ids','entity_id','event_ids','hypothesis_ids','instrument_type','priority','reference_manifest_id','reservation_id','role','security_id','security_revision_id','source_item_ids','source_receipt_id','theme_id','ticker'])<>'{}'::jsonb THEN
        RAISE EXCEPTION 'invalid selected quote descriptor' USING ERRCODE='22023';
      END IF;
      v_yahoo_quotes:=v_yahoo_quotes+1;
    ELSE
      RAISE EXCEPTION 'unsupported enrichment query kind' USING ERRCODE='22023';
    END IF;
    v_hash:=public.market_enrichment_request_semantic_hash_v1(r);
    IF v_hash<>r->>'descriptor_hash' THEN
      RAISE EXCEPTION 'enrichment request hash mismatch' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS(
      SELECT 1 FROM public.market_reference_run_bindings b
      JOIN public.market_reference_snapshot_memberships member ON member.manifest_id=b.manifest_id
      JOIN public.market_security_reference_revisions s ON s.id=member.security_revision_id
      WHERE b.run_id=p_run_id AND b.capability_id='sec_company_tickers_universe'
        AND b.reference_status IN ('healthy','reference_stale')
        AND b.manifest_id=(r->'descriptor'->>'reference_manifest_id')::uuid
        AND s.id=(r->'descriptor'->>'security_revision_id')::uuid AND s.eligible
        AND s.entity_id=r->'descriptor'->>'entity_id'
        AND s.security_id=r->'descriptor'->>'security_id'
        AND s.ticker=r->'descriptor'->>'ticker'
        AND s.instrument_type=r->'descriptor'->>'instrument_type'
    ) THEN
      RAISE EXCEPTION 'enrichment current reference membership mismatch' USING ERRCODE='22023';
    END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(r->'dependency_ids') dependency
              WHERE NOT EXISTS(SELECT 1 FROM public.market_discovery_stage_tasks d
                               WHERE d.id=dependency::uuid AND d.run_id=p_run_id AND d.state='succeeded')) THEN
      RAISE EXCEPTION 'enrichment dependency mismatch' USING ERRCODE='22023';
    END IF;
    IF r->>'query_kind'='filing_document' AND (
      jsonb_array_length(r->'dependency_ids')<>1 OR NOT EXISTS(
        SELECT 1
        FROM public.market_discovery_stage_tasks parent
        JOIN public.market_enrichment_request_descriptors parent_request
          ON parent_request.task_id=parent.id AND parent_request.run_id=parent.run_id
        JOIN (
          SELECT checkpoint.run_id,checkpoint.cache_key,checkpoint.source_receipt_id,checkpoint.payload
          FROM public.market_collection_checkpoints checkpoint
          UNION ALL
          SELECT history.run_id,history.cache_key,history.source_receipt_id,history.payload
          FROM public.market_collection_checkpoint_history history
        ) checkpoint
          ON checkpoint.run_id=parent.run_id
         AND checkpoint.cache_key=parent.result->'checkpoint'->>'cache_key'
         AND checkpoint.cache_key=parent_request.descriptor->>'cache_key'
         AND checkpoint.source_receipt_id=(parent_request.descriptor->>'source_receipt_id')::uuid
        CROSS JOIN LATERAL jsonb_array_elements(checkpoint.payload->'items') item
        WHERE parent.id=(r->'dependency_ids'->>0)::uuid
          AND parent.run_id=p_run_id AND parent.state='succeeded'
          AND parent.provider='sec_edgar' AND parent.capability_id='sec_issuer_submissions'
          AND parent.query_kind='issuer_submissions'
          AND parent.query_hash=parent_request.content_hash
          AND parent_request.provider='sec_edgar'
          AND parent_request.capability_id='sec_issuer_submissions'
          AND parent_request.query_kind='issuer_submissions'
          AND parent_request.descriptor->>'entity_id'=r->'descriptor'->>'entity_id'
          AND parent_request.descriptor->>'security_id'=r->'descriptor'->>'security_id'
          AND parent_request.descriptor->>'security_revision_id'=r->'descriptor'->>'security_revision_id'
          AND parent_request.descriptor->>'reference_manifest_id'=r->'descriptor'->>'reference_manifest_id'
          AND parent_request.descriptor->>'cik'=r->'descriptor'->>'cik'
          AND checkpoint.payload->'receipt'->>'provider'='sec_edgar'
          AND checkpoint.payload->'receipt'->>'status' IN ('succeeded','cache_hit')
          AND checkpoint.payload->'receipt'->>'cache_key'=checkpoint.cache_key
          AND checkpoint.payload->'receipt'->>'source_receipt_id'=checkpoint.source_receipt_id::text
          AND checkpoint.payload->'receipt'->>'response_hash'=r->'descriptor'->>'submissions_response_hash'
          AND item->>'provider'='sec_edgar' AND item->>'authority'='official'
          AND (item->>'request_url')=(
              'https://data.sec.gov/submissions/CIK'||(r->'descriptor'->>'cik')||'.json'
          )
          AND (item->>'source_url')=(
              'https://www.sec.gov/Archives/edgar/data/'
              ||((r->'descriptor'->>'cik')::bigint)::text||'/'
              ||replace(r->'descriptor'->>'accession_number','-','')||'/'
              ||(r->'descriptor'->>'primary_document')
          )
          AND item->'metadata'->>'issuer_cik'=r->'descriptor'->>'cik'
          AND item->'metadata'->>'accession_number'=r->'descriptor'->>'accession_number'
          AND item->'metadata'->>'form'=r->'descriptor'->>'form'
          AND item->'metadata'->>'primary_document'=r->'descriptor'->>'primary_document'
          AND item->'metadata'->>'filing_date'=r->'descriptor'->>'filing_date'
          AND item->'metadata'->>'reporting_period_end' IS NOT DISTINCT FROM
              r->'descriptor'->>'reporting_period_end'
          AND (item->'metadata'->>'accepted_at')::timestamptz IS NOT DISTINCT FROM
              (r->'descriptor'->>'accepted_at')::timestamptz
          AND item->'metadata'->>'submissions_response_hash'=
              r->'descriptor'->>'submissions_response_hash'
          AND (SELECT count(*) FROM jsonb_array_elements(checkpoint.payload->'items') candidate
               WHERE candidate->'metadata'->>'accession_number'=
                     r->'descriptor'->>'accession_number')=1
      )
    ) THEN
      RAISE EXCEPTION 'filing document lacks exact issuer submissions membership' USING ERRCODE='22023';
    END IF;
    IF r->>'query_kind'='quote' AND (
      r->>'provider'<>'yahoo' OR r->>'capability_id'<>'yahoo_security_quote'
      OR r->'descriptor'->>'source_receipt_id' !~ '^[0-9a-f-]{36}$'
      OR r->'descriptor'->>'cache_key' !~ '^[0-9a-f]{64}$'
      OR NOT EXISTS(SELECT 1 FROM public.market_source_quota_reservations q
                    WHERE q.id=(r->'descriptor'->>'reservation_id')::uuid AND q.run_id=p_run_id AND q.provider='yahoo')
    ) THEN
      RAISE EXCEPTION 'invalid selected quote descriptor' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.market_discovery_stage_tasks(
      id,run_id,stage,capability_id,provider,query_kind,query_hash,dependency_ids,requested_window,state,attempt_count,request_budget,result
    ) VALUES(
      (r->>'task_id')::uuid,p_run_id,r->>'stage',r->>'capability_id',r->>'provider',r->>'query_kind',
      r->>'descriptor_hash',r->'dependency_ids',r->'requested_window','planned',0,1,'{}'::jsonb
    );
    INSERT INTO public.market_enrichment_request_descriptors(
      id,manifest_id,run_id,task_id,provider,capability_id,query_kind,descriptor,content_hash
    ) VALUES(
      (r->>'request_id')::uuid,v_manifest_id,p_run_id,(r->>'task_id')::uuid,r->>'provider',
      r->>'capability_id',r->>'query_kind',r->'descriptor',r->>'descriptor_hash'
    );
    v_request_count:=v_request_count+1;
  END LOOP;
  IF (m->>'selection_stage'='initial' AND (
        v_sec_submissions>(v_expected->>'sec_issuer_submissions')::int
        OR v_yahoo_quotes>(v_expected->>'yahoo_security_quote')::int
      )) OR (m->>'selection_stage'='filing_documents'
        AND v_sec_documents>(v_expected->>'sec_filing_document')::int) THEN
    RAISE EXCEPTION 'enrichment provider capacity exceeded' USING ERRCODE='22023';
  END IF;
  RETURN jsonb_build_object('manifest_id',v_manifest_id::text,'request_count',v_request_count,'duplicate',false);
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR foreign_key_violation OR unique_violation THEN
  RAISE EXCEPTION 'invalid enrichment selection' USING ERRCODE='22023';
END;
$$;
