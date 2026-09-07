-- Immutable adaptive-enrichment selection, current-reference binding, and
-- primary-evidence validation. These records authorize bounded research
-- transport only; execution authority is structurally absent.

CREATE TABLE IF NOT EXISTS public.market_enrichment_selection_manifests (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  selection_stage TEXT NOT NULL CHECK (selection_stage IN ('holding_quotes','initial','filing_documents')),
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  request_count INT NOT NULL CHECK (request_count BETWEEN 0 AND 100),
  provider_reservations JSONB NOT NULL CHECK (jsonb_typeof(provider_reservations)='object' AND octet_length(provider_reservations::text)<=2048),
  deferred_reasons JSONB NOT NULL CHECK (jsonb_typeof(deferred_reasons)='object' AND octet_length(deferred_reasons::text)<=16384),
  manifest JSONB NOT NULL CHECK (jsonb_typeof(manifest)='object' AND octet_length(manifest::text)<=65536),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id,selection_stage)
);

CREATE TABLE IF NOT EXISTS public.market_enrichment_request_descriptors (
  id UUID PRIMARY KEY,
  manifest_id UUID NOT NULL REFERENCES public.market_enrichment_selection_manifests(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  task_id UUID NOT NULL UNIQUE REFERENCES public.market_discovery_stage_tasks(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL CHECK (provider IN ('sec_edgar','yahoo','gdelt')),
  capability_id TEXT NOT NULL CHECK (capability_id IN ('sec_issuer_submissions','sec_filing_document','yahoo_security_quote','gdelt_theme_search')),
  query_kind TEXT NOT NULL CHECK (query_kind IN ('issuer_submissions','filing_document','quote','theme_search')),
  descriptor JSONB NOT NULL CHECK (jsonb_typeof(descriptor)='object' AND octet_length(descriptor::text)<=32768),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (manifest_id,content_hash)
);

DROP TRIGGER IF EXISTS market_enrichment_selection_manifests_append_only ON public.market_enrichment_selection_manifests;
CREATE TRIGGER market_enrichment_selection_manifests_append_only BEFORE UPDATE OR DELETE ON public.market_enrichment_selection_manifests
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_enrichment_request_descriptors_append_only ON public.market_enrichment_request_descriptors;
CREATE TRIGGER market_enrichment_request_descriptors_append_only BEFORE UPDATE OR DELETE ON public.market_enrichment_request_descriptors
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();

CREATE OR REPLACE FUNCTION public.market_enrichment_manifest_semantic_hash_v1(p_manifest JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
  SELECT encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
    p_manifest-ARRAY['manifest_id','semantic_hash']
  ),'UTF8'),'sha256'),'hex')
$$;

CREATE OR REPLACE FUNCTION public.market_enrichment_request_semantic_hash_v1(p_request JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
  SELECT encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
    p_request-'descriptor_hash'
  ),'UTF8'),'sha256'),'hex')
$$;

CREATE OR REPLACE FUNCTION public.market_exposure_fact_semantic_hash_v1(p_fact JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
  SELECT encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
    p_fact
  ),'UTF8'),'sha256'),'hex')
$$;

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
  IF (SELECT count(*) FROM public.market_discovery_stage_tasks WHERE run_id=p_run_id)
       + jsonb_array_length(p_payload->'requests') > 100
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

ALTER FUNCTION public.claim_market_intelligence_quote(UUID,JSONB)
  RENAME TO claim_market_intelligence_quote_v1_internal;

CREATE OR REPLACE FUNCTION public.claim_market_intelligence_quote(p_run_id UUID,p_input JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_descriptor public.market_enrichment_request_descriptors%ROWTYPE; v_task public.market_discovery_stage_tasks%ROWTYPE;
BEGIN
  IF p_input IS NULL OR jsonb_typeof(p_input)<>'object'
     OR NOT (p_input ?& ARRAY['ticker','instrument_type','security_revision_id','reference_manifest_id','selection_manifest_id','selected_task_id','cache_key','source_receipt_id','reservation_id'])
     OR (p_input-ARRAY['ticker','instrument_type','security_revision_id','reference_manifest_id','selection_manifest_id','selected_task_id','cache_key','source_receipt_id','reservation_id'])<>'{}'::jsonb THEN
    RAISE EXCEPTION 'invalid quote collection identity' USING ERRCODE='22023';
  END IF;
  SELECT d.* INTO v_descriptor FROM public.market_enrichment_request_descriptors d
  WHERE d.run_id=p_run_id AND d.manifest_id=(p_input->>'selection_manifest_id')::uuid
    AND d.task_id=(p_input->>'selected_task_id')::uuid AND d.provider='yahoo'
    AND d.capability_id='yahoo_security_quote' AND d.query_kind='quote';
  IF NOT FOUND OR v_descriptor.descriptor->>'ticker' IS DISTINCT FROM p_input->>'ticker'
     OR v_descriptor.descriptor->>'instrument_type' IS DISTINCT FROM p_input->>'instrument_type'
     OR v_descriptor.descriptor->>'security_revision_id' IS DISTINCT FROM p_input->>'security_revision_id'
     OR v_descriptor.descriptor->>'reference_manifest_id' IS DISTINCT FROM p_input->>'reference_manifest_id'
     OR v_descriptor.descriptor->>'cache_key' IS DISTINCT FROM p_input->>'cache_key'
     OR v_descriptor.descriptor->>'source_receipt_id' IS DISTINCT FROM p_input->>'source_receipt_id'
     OR v_descriptor.descriptor->>'reservation_id' IS DISTINCT FROM p_input->>'reservation_id' THEN
    RAISE EXCEPTION 'quote is not a frozen selected descriptor' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_task FROM public.market_discovery_stage_tasks WHERE id=v_descriptor.task_id AND run_id=p_run_id;
  IF NOT FOUND OR v_task.state<>'attempting' OR v_task.attempt_count<>1 OR v_task.query_hash<>v_descriptor.content_hash THEN
    RAISE EXCEPTION 'quote attempt barrier is unavailable' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(
    SELECT 1 FROM public.market_reference_run_bindings b
    JOIN public.market_reference_snapshot_memberships member ON member.manifest_id=b.manifest_id
    JOIN public.market_security_reference_revisions s ON s.id=member.security_revision_id
    WHERE b.run_id=p_run_id AND b.capability_id='sec_company_tickers_universe'
      AND b.manifest_id=(p_input->>'reference_manifest_id')::uuid
      AND s.id=(p_input->>'security_revision_id')::uuid AND s.ticker=p_input->>'ticker'
      AND s.instrument_type=p_input->>'instrument_type' AND s.eligible
  ) THEN
    RAISE EXCEPTION 'quote current reference membership mismatch' USING ERRCODE='22023';
  END IF;
  RETURN public.claim_market_intelligence_quote_v1_internal(p_run_id,jsonb_build_object(
    'ticker',p_input->'ticker','cache_key',p_input->'cache_key',
    'source_receipt_id',p_input->'source_receipt_id','reservation_id',p_input->'reservation_id'
  ));
EXCEPTION WHEN invalid_text_representation OR foreign_key_violation THEN
  RAISE EXCEPTION 'invalid quote collection identity' USING ERRCODE='22023';
END;
$$;

ALTER FUNCTION public.checkpoint_market_discovery_stage(UUID,JSONB)
  RENAME TO checkpoint_market_discovery_stage_v1_internal;

CREATE OR REPLACE FUNCTION public.checkpoint_market_discovery_stage(p_run_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r JSONB; v_value JSONB; v_task public.market_discovery_stage_tasks%ROWTYPE; v_result JSONB;
BEGIN
  IF jsonb_typeof(p_payload->'exposure_facts')<>'array' OR NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'exposure_facts') x
    WHERE x->'fact'->>'kind'='exposure_fact' AND x->'fact'->'value'->>'schema_version'='1'
  ) THEN
    RETURN public.checkpoint_market_discovery_stage_v1_internal(p_run_id,p_payload);
  END IF;
  IF p_payload->'task'->>'state'<>'succeeded' OR p_payload->'task'->>'stage'<>'enrich'
     OR jsonb_array_length(p_payload->'theme_episode_revisions')<>0
     OR jsonb_array_length(p_payload->'research_nominations')<>0 THEN
    RAISE EXCEPTION 'invalid typed exposure checkpoint' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_task FROM public.market_discovery_stage_tasks
  WHERE id=(p_payload->'task'->>'id')::uuid AND run_id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'typed exposure task is not planned' USING ERRCODE='22023'; END IF;
  IF v_task.state='succeeded' THEN
    IF v_task.capability_id<>p_payload->'task'->>'capability_id' OR v_task.query_hash<>p_payload->'task'->>'query_hash'
       OR v_task.result<>p_payload->'task'->'result'
       OR (SELECT count(*) FROM public.market_exposure_facts f WHERE f.task_id=v_task.id)
          <>jsonb_array_length(p_payload->'exposure_facts') THEN
      RAISE EXCEPTION 'typed exposure replay mismatch' USING ERRCODE='22023';
    END IF;
    FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'exposure_facts') LOOP
      IF NOT EXISTS(SELECT 1 FROM public.market_exposure_facts f WHERE f.id=(r->>'id')::uuid
                    AND f.run_id=p_run_id AND f.task_id=v_task.id AND f.fact=r->'fact'
                    AND f.source_ids=r->'source_ids' AND f.content_hash=r->>'content_hash') THEN
        RAISE EXCEPTION 'typed exposure replay mismatch' USING ERRCODE='22023';
      END IF;
    END LOOP;
    RETURN jsonb_build_object('task',to_jsonb(v_task)-'run_id'-'created_at'-'updated_at','duplicate',true);
  END IF;
  IF v_task.state<>'attempting' OR v_task.attempt_count<>1 THEN
    RAISE EXCEPTION 'typed exposure attempt barrier is unavailable' USING ERRCODE='22023';
  END IF;
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'exposure_facts') LOOP
    v_value:=r->'fact'->'value';
    IF jsonb_typeof(r)<>'object' OR r->>'exposure_kind'<>'filing'
       OR NOT (r ?& ARRAY['id','security_revision_id','theme_episode_revision_id','exposure_kind','fact','source_ids','valid_from','valid_to','content_hash'])
       OR (r-ARRAY['id','security_revision_id','theme_episode_revision_id','exposure_kind','fact','source_ids','valid_from','valid_to','content_hash'])<>'{}'::jsonb
       OR r->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR public.market_exposure_fact_semantic_hash_v1(r->'fact')<>r->>'content_hash'
       OR jsonb_typeof(r->'fact')<>'object'
       OR NOT (r->'fact' ?& ARRAY['kind','semantic_encoding_version','value'])
       OR ((r->'fact')-ARRAY['kind','semantic_encoding_version','value'])<>'{}'::jsonb
       OR r->'fact'->>'kind'<>'exposure_fact' OR r->'fact'->'semantic_encoding_version'<>'1'::jsonb
       OR jsonb_typeof(v_value)<>'object'
       OR NOT (v_value ?& ARRAY['accepted_at','accession_number','business_exposure','claim_state','effective_at','entity_id','event_ids','execution_allowed','filing_date','filing_rule_version','financial_materiality','form','is_amendment','hypothesis_ids','issuer_cik','limitations','metric','normalized_passage_hash','parser_version','passage','period_end','period_start','primary_document','reference_manifest_id','reporting_period_end','retrieved_at','role','schema_version','security_id','security_revision_id','source_cache_key','source_item_content_hash','source_item_id','source_locator','source_receipt_id','source_response_hash','source_url','status','submissions_response_hash','ticker','unit','value'])
       OR (v_value-ARRAY['accepted_at','accession_number','business_exposure','claim_state','effective_at','entity_id','event_ids','execution_allowed','filing_date','filing_rule_version','financial_materiality','form','is_amendment','hypothesis_ids','issuer_cik','limitations','metric','normalized_passage_hash','parser_version','passage','period_end','period_start','primary_document','reference_manifest_id','reporting_period_end','retrieved_at','role','schema_version','security_id','security_revision_id','source_cache_key','source_item_content_hash','source_item_id','source_locator','source_receipt_id','source_response_hash','source_url','status','submissions_response_hash','ticker','unit','value'])<>'{}'::jsonb
       OR v_value->'execution_allowed'<>'false'::jsonb OR v_value->>'status' NOT IN ('supported','contradicted','superseded','insufficient')
       OR v_value->>'claim_state' NOT IN ('operational','planned','forecast','customer','contradicted','insufficient')
       OR v_value->>'business_exposure' NOT IN ('supported','contradicted','unresolved')
       OR v_value->>'financial_materiality' NOT IN ('supported','contradicted','unknown')
       OR v_value->>'metric' NOT IN ('business_exposure','revenue_share')
       OR v_value->>'issuer_cik' !~ '^[0-9]{10}$' OR v_value->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$'
       OR v_value->>'entity_id'<>('sec-cik:'||(v_value->>'issuer_cik'))
       OR v_value->>'security_revision_id'<>r->>'security_revision_id'
       OR v_value->>'accession_number' !~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'
       OR v_value->>'primary_document' !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$'
       OR v_value->>'form' !~ '^(10-K|10-Q|8-K|20-F|40-F)(/A)?$'
       OR v_value->>'submissions_response_hash' !~ '^[0-9a-f]{64}$'
       OR v_value->>'source_cache_key' !~ '^[0-9a-f]{64}$'
       OR v_value->>'source_item_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR v_value->>'source_receipt_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR length(v_value->>'passage') NOT BETWEEN 1 AND 2000 OR length(v_value->>'source_locator') NOT BETWEEN 1 AND 256
       OR v_value->>'source_response_hash' !~ '^[0-9a-f]{64}$'
       OR v_value->>'source_url'<>(
         'https://www.sec.gov/Archives/edgar/data/'
         ||((v_value->>'issuer_cik')::bigint)::text||'/'
         ||replace((v_value->>'accession_number'),'-','')||'/'
         ||(v_value->>'primary_document')
       )
       OR v_value->>'normalized_passage_hash' !~ '^[0-9a-f]{64}$'
       OR v_value->>'source_item_content_hash' !~ '^[0-9a-f]{64}$'
       OR encode(extensions.digest(convert_to(v_value->>'passage','UTF8'),'sha256'),'hex')<>v_value->>'normalized_passage_hash'
       OR jsonb_typeof(v_value->'event_ids')<>'array' OR jsonb_array_length(v_value->'event_ids') NOT BETWEEN 1 AND 64
       OR jsonb_typeof(v_value->'hypothesis_ids')<>'array' OR jsonb_array_length(v_value->'hypothesis_ids') NOT BETWEEN 1 AND 64
       OR jsonb_typeof(v_value->'limitations')<>'array' OR jsonb_array_length(v_value->'limitations')>16
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_value->'event_ids') x WHERE jsonb_typeof(x)<>'string')
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(v_value->'event_ids') x WHERE length(x) NOT BETWEEN 1 AND 160)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_value->'hypothesis_ids') x WHERE jsonb_typeof(x)<>'string')
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(v_value->'hypothesis_ids') x WHERE length(x) NOT BETWEEN 1 AND 160)
       OR v_value->>'filing_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
       OR (v_value->'accepted_at'<>'null'::jsonb AND v_value->>'accepted_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T')
       OR (v_value->'reporting_period_end'<>'null'::jsonb AND v_value->>'reporting_period_end' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
       OR (v_value->'period_start'<>'null'::jsonb AND v_value->>'period_start' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
       OR (v_value->'period_end'<>'null'::jsonb AND v_value->>'period_end' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
       OR v_value->>'retrieved_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
       OR r->>'valid_from'<>v_value->>'filing_date' OR r->'valid_to'<>'null'::jsonb
       OR ((v_value->>'metric'='business_exposure')<>(v_value->'value'='null'::jsonb AND v_value->'unit'='null'::jsonb))
       OR (v_value->>'metric'='revenue_share' AND (v_value->>'value' !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$' OR (v_value->>'value')::numeric NOT BETWEEN 0 AND 100 OR v_value->>'unit'<>'percent_of_revenue'))
       OR (v_value->>'status'='supported' AND (v_value->>'claim_state'<>'operational' OR v_value->>'business_exposure'<>'supported'))
       OR (v_value->>'status'='contradicted' AND (v_value->>'claim_state'<>'contradicted' OR v_value->>'business_exposure'<>'contradicted'))
       OR (v_value->>'status'='insufficient' AND (v_value->>'claim_state' NOT IN ('planned','forecast','customer','insufficient') OR v_value->>'business_exposure'<>'unresolved'))
       OR r->'source_ids'<>jsonb_build_array(v_value->'source_item_id')
       OR NOT EXISTS(
         SELECT 1 FROM public.market_reference_run_bindings b
         JOIN public.market_reference_snapshot_memberships member ON member.manifest_id=b.manifest_id
         JOIN public.market_security_reference_revisions s ON s.id=member.security_revision_id
         WHERE b.run_id=p_run_id AND b.capability_id='sec_company_tickers_universe'
           AND b.reference_status IN ('healthy','reference_stale')
           AND b.manifest_id=(v_value->>'reference_manifest_id')::uuid
           AND member.security_revision_id=(r->>'security_revision_id')::uuid
           AND s.eligible AND s.entity_id=v_value->>'entity_id'
           AND s.security_id=v_value->>'security_id' AND s.ticker=v_value->>'ticker'
       )
       OR NOT EXISTS(
         SELECT 1 FROM public.market_enrichment_request_descriptors d
         WHERE d.run_id=p_run_id AND d.task_id=v_task.id
           AND d.provider='sec_edgar' AND d.capability_id='sec_filing_document'
           AND d.query_kind='filing_document'
           AND d.descriptor->>'reference_manifest_id'=v_value->>'reference_manifest_id'
           AND d.descriptor->>'security_revision_id'=v_value->>'security_revision_id'
           AND d.descriptor->>'security_id'=v_value->>'security_id'
           AND d.descriptor->>'entity_id'=v_value->>'entity_id'
           AND d.descriptor->>'ticker'=v_value->>'ticker'
           AND d.descriptor->>'cik'=v_value->>'issuer_cik'
           AND d.descriptor->>'accession_number'=v_value->>'accession_number'
           AND d.descriptor->>'form'=v_value->>'form'
           AND d.descriptor->>'primary_document'=v_value->>'primary_document'
           AND d.descriptor->>'submissions_response_hash'=v_value->>'submissions_response_hash'
           AND d.descriptor->>'source_receipt_id'=v_value->>'source_receipt_id'
           AND d.descriptor->>'cache_key'=v_value->>'source_cache_key'
           AND d.descriptor->>'filing_date'=v_value->>'filing_date'
           AND (d.descriptor->>'accepted_at')::timestamptz IS NOT DISTINCT FROM (v_value->>'accepted_at')::timestamptz
           AND (d.descriptor->>'reporting_period_end')::date IS NOT DISTINCT FROM (v_value->>'reporting_period_end')::date
       )
       OR NOT EXISTS(
         SELECT 1 FROM (
           SELECT c.cache_key,c.payload FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id
           UNION ALL SELECT h.cache_key,h.payload FROM public.market_collection_checkpoint_history h WHERE h.run_id=p_run_id
         ) checkpoint
         WHERE checkpoint.cache_key=v_value->>'source_cache_key'
           AND checkpoint.payload->'receipt'->>'source_receipt_id'=v_value->>'source_receipt_id'
           AND checkpoint.payload->'receipt'->>'response_hash'=v_value->>'source_response_hash'
           AND EXISTS(SELECT 1 FROM jsonb_array_elements(checkpoint.payload->'items') item
                      WHERE item->>'content_hash'=v_value->>'source_item_content_hash'
                        AND item->>'authority'='official'
                        AND item->>'source_url'=v_value->>'source_url'
                        AND item->>'request_url'=v_value->>'source_url'
                        AND item->>'normalized_text'=v_value->>'passage'
                        AND octet_length(item->>'canonical_content')<=8192
                        AND item->'metadata'->>'raw_response_hash'=v_value->>'source_response_hash'
                        AND item->'metadata'->>'normalized_passage_hash'=v_value->>'normalized_passage_hash'
                        AND item->'metadata'->>'source_locator'=v_value->>'source_locator'
                        AND item->'metadata'->>'parser_version'=v_value->>'parser_version'
                        AND item->'metadata'->>'filing_rule_version'=v_value->>'filing_rule_version')
       ) THEN
      RAISE EXCEPTION 'invalid typed exposure fact lineage' USING ERRCODE='22023';
    END IF;
  END LOOP;
  v_result:=public.checkpoint_market_discovery_stage_v1_internal(
    p_run_id,jsonb_set(p_payload,'{exposure_facts}','[]'::jsonb)
  );
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'exposure_facts') LOOP
    INSERT INTO public.market_exposure_facts(
      id,run_id,task_id,security_revision_id,theme_episode_revision_id,exposure_kind,fact,source_ids,valid_from,valid_to,content_hash
    ) VALUES(
      (r->>'id')::uuid,p_run_id,(p_payload->'task'->>'id')::uuid,(r->>'security_revision_id')::uuid,
      (r->>'theme_episode_revision_id')::uuid,r->>'exposure_kind',r->'fact',r->'source_ids',
      (r->>'valid_from')::timestamptz,(r->>'valid_to')::timestamptz,r->>'content_hash'
    );
  END LOOP;
  RETURN v_result;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range OR foreign_key_violation OR unique_violation THEN
  RAISE EXCEPTION 'invalid typed exposure checkpoint' USING ERRCODE='22023';
END;
$$;

ALTER FUNCTION public.record_market_intelligence(UUID,UUID,JSONB)
  RENAME TO record_market_intelligence_v3_internal;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(p_run_id UUID,p_completion_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB;
BEGIN
  v_result:=public.record_market_intelligence_v3_internal(p_run_id,p_completion_id,p_payload);
  IF EXISTS(
    SELECT 1 FROM public.market_exposure_facts f
    CROSS JOIN LATERAL jsonb_array_elements_text(f.source_ids) source_id
    WHERE f.run_id=p_run_id AND f.fact->>'kind'='exposure_fact'
      AND NOT EXISTS(
        SELECT 1 FROM public.market_source_items i
        JOIN public.market_run_source_item_provenance provenance
          ON provenance.source_item_id=i.id AND provenance.run_id=p_run_id
        WHERE i.id=source_id::uuid
          AND i.content_hash=f.fact->'value'->>'source_item_content_hash'
          AND provenance.request_url=f.fact->'value'->>'source_url'
          AND provenance.source_receipt_id=(f.fact->'value'->>'source_receipt_id')::uuid
      )
  ) THEN
    RAISE EXCEPTION 'exposure final source reconciliation mismatch' USING ERRCODE='22023';
  END IF;
  RETURN v_result;
END;
$$;

ALTER FUNCTION public.read_market_discovery_context(UUID,INT)
  RENAME TO read_market_discovery_context_v1_internal;

CREATE OR REPLACE FUNCTION public.read_market_discovery_context(p_run_id UUID,p_limit INT DEFAULT 100)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB;
BEGIN
  v_result:=public.read_market_discovery_context_v1_internal(p_run_id,p_limit);
  v_result:=v_result||jsonb_build_object(
    'enrichment_selections',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'manifest',m.manifest,
        'requests',COALESCE((
          SELECT jsonb_agg(jsonb_build_object(
            'request_id',d.id::text,'task_id',d.task_id::text,'stage',t.stage,
            'provider',d.provider,'capability_id',d.capability_id,
            'query_kind',d.query_kind,'descriptor',d.descriptor,
            'dependency_ids',t.dependency_ids,'requested_window',t.requested_window,
            'request_budget',t.request_budget,'execution_allowed',false,
            'descriptor_hash',d.content_hash
          ) ORDER BY d.created_at,d.id)
          FROM public.market_enrichment_request_descriptors d
          JOIN public.market_discovery_stage_tasks t ON t.id=d.task_id AND t.run_id=d.run_id
          WHERE d.manifest_id=m.id
        ),'[]'::jsonb)
      ) ORDER BY m.created_at,m.id)
      FROM (
        SELECT * FROM public.market_enrichment_selection_manifests
        WHERE run_id=p_run_id ORDER BY created_at,id LIMIT 3
      ) m
    ),'[]'::jsonb)
  );
  IF octet_length(v_result::text)>1048576 THEN
    RAISE EXCEPTION 'discovery context exceeds bound' USING ERRCODE='54000';
  END IF;
  RETURN v_result;
END;
$$;

ALTER TABLE public.market_enrichment_selection_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_enrichment_request_descriptors ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_enrichment_selection_manifests,public.market_enrichment_request_descriptors FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.market_enrichment_selection_manifests,public.market_enrichment_request_descriptors TO stock_agent_release_reader;

REVOKE ALL ON FUNCTION public.seal_market_enrichment_selection(UUID,JSONB),public.claim_market_intelligence_quote(UUID,JSONB),
  public.checkpoint_market_discovery_stage(UUID,JSONB),public.record_market_intelligence(UUID,UUID,JSONB)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.seal_market_enrichment_selection(UUID,JSONB),public.claim_market_intelligence_quote(UUID,JSONB),
  public.checkpoint_market_discovery_stage(UUID,JSONB),public.record_market_intelligence(UUID,UUID,JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.claim_market_intelligence_quote_v1_internal(UUID,JSONB),
  public.checkpoint_market_discovery_stage_v1_internal(UUID,JSONB),public.record_market_intelligence_v3_internal(UUID,UUID,JSONB)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_context_v1_internal(UUID,INT)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_context(UUID,INT)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.read_market_discovery_context(UUID,INT) TO service_role;
REVOKE ALL ON FUNCTION public.market_enrichment_manifest_semantic_hash_v1(JSONB),
  public.market_enrichment_request_semantic_hash_v1(JSONB),public.market_exposure_fact_semantic_hash_v1(JSONB)
  FROM PUBLIC,anon,authenticated,service_role;
