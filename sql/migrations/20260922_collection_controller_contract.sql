-- Consolidated collection controller. Explicit definitions supersede historical
-- rename shims, so fresh and additive installs have the same nonrecursive chain.

-- The legacy fresh schema declares the outbox before the report ledger. Keep
-- its deferred FK and the remaining original gateway grants in both installs.
DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint
      WHERE conrelid='public.market_report_publications'::regclass AND conname='market_report_publications_report_id_fkey') THEN
    ALTER TABLE public.market_report_publications ADD CONSTRAINT market_report_publications_report_id_fkey
      FOREIGN KEY (report_id) REFERENCES public.market_reports(id) ON DELETE RESTRICT;
  END IF;
END; $$;
GRANT EXECUTE ON FUNCTION public.read_market_evidence_packet(UUID, UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_market_report_decisions(UUID, UUID, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_report(UUID, TEXT, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_learning(UUID, JSONB) TO service_role;

CREATE OR REPLACE FUNCTION public.record_market_intelligence_legacy(
  p_run_id UUID,
  p_completion_id UUID,
  p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_run public.market_intelligence_runs%ROWTYPE;
  v_existing public.market_intelligence_run_events%ROWTYPE;
  v_row JSONB;
  v_receipt public.market_source_receipts%ROWTYPE;
  v_reservation public.market_source_quota_reservations%ROWTYPE;
  v_packet JSONB;
  v_candidate JSONB;
  v_evidence_id JSONB;
  v_source_host TEXT;
  v_payload_hash TEXT;
  v_receipt_json JSONB;
  v_item_count INT := 0;
  v_receipt_count INT := 0;
  v_event_count INT := 0;
  v_relationship_count INT := 0;
  v_ranking_count INT := 0;
BEGIN
  IF p_run_id IS NULL OR p_completion_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR octet_length(p_payload::text) > 1048576
     OR NOT (p_payload ?& ARRAY[
       'status','coverage','receipts','items','events','relationships','rankings','packet','error'
     ])
     OR (p_payload - ARRAY[
       'status','coverage','receipts','items','events','relationships','rankings','packet','error'
     ]) <> '{}'::jsonb
     OR p_payload->>'status' NOT IN ('completed','failed')
     OR jsonb_typeof(p_payload->'coverage')<>'object'
     OR octet_length((p_payload->'coverage')::text)>32768
     OR jsonb_typeof(p_payload->'receipts')<>'array'
     OR jsonb_array_length(p_payload->'receipts')>100
     OR jsonb_typeof(p_payload->'items')<>'array'
     OR jsonb_array_length(p_payload->'items')>500
     OR jsonb_typeof(p_payload->'events')<>'array'
     OR jsonb_array_length(p_payload->'events')>100
     OR jsonb_typeof(p_payload->'relationships')<>'array'
     OR jsonb_array_length(p_payload->'relationships')>500
     OR jsonb_typeof(p_payload->'rankings')<>'array'
     OR jsonb_array_length(p_payload->'rankings')>100
     OR (p_payload->>'status'='completed' AND jsonb_typeof(p_payload->'packet')<>'object')
     OR (p_payload->>'status'='completed' AND p_payload->'error'<>'null'::jsonb)
     OR (p_payload->>'status'='failed' AND p_payload->'packet'<>'null'::jsonb)
     OR (p_payload->>'status'='failed' AND jsonb_typeof(p_payload->'error')<>'object')
     OR (p_payload->>'status'='failed' AND octet_length((p_payload->'error')::text)>4096) THEN
    RAISE EXCEPTION 'invalid intelligence completion payload' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-completion:' || p_completion_id::text, 0
  ));
  v_payload_hash := encode(extensions.digest(p_payload::text,'sha256'),'hex');
  SELECT * INTO v_existing FROM public.market_intelligence_run_events WHERE id=p_completion_id;
  IF FOUND THEN
    IF v_existing.run_id IS DISTINCT FROM p_run_id
       OR v_existing.status IS DISTINCT FROM p_payload->>'status'
       OR v_existing.detail->>'payload_hash' IS DISTINCT FROM v_payload_hash THEN
      RAISE EXCEPTION 'intelligence completion idempotency mismatch' USING ERRCODE = '22023';
    END IF;
    RETURN (v_existing.detail->'receipt') || jsonb_build_object('duplicate',true);
  END IF;
  SELECT * INTO v_run FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'intelligence run unavailable' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.market_intelligence_run_events
    WHERE run_id=p_run_id AND status IN ('completed','failed')
  ) THEN
    RAISE EXCEPTION 'intelligence run already terminal' USING ERRCODE = '22023';
  END IF;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'receipts') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','reservation_id','status','cache_key','requested_window','retrieved_at','expires_at',
         'request_cost','upstream_remaining','returned_count','accepted_count','duplicate_count',
         'dropped_count','error','response_hash'
       ])
       OR (v_row - ARRAY[
         'id','reservation_id','status','cache_key','requested_window','retrieved_at','expires_at',
         'request_cost','upstream_remaining','returned_count','accepted_count','duplicate_count',
         'dropped_count','error','response_hash'
       ]) <> '{}'::jsonb
       OR v_row->>'status' NOT IN (
         'succeeded','failed','cache_hit','quota_blocked','configuration_missing'
       )
       OR char_length(v_row->>'cache_key') NOT BETWEEN 1 AND 512
       OR jsonb_typeof(v_row->'requested_window')<>'object'
       OR octet_length((v_row->'requested_window')::text)>8192
       OR (v_row->>'request_cost') !~ '^[0-9]+$'
       OR (v_row->>'request_cost')::int NOT BETWEEN 0 AND 100
       OR (v_row->>'returned_count') !~ '^[0-9]+$'
       OR (v_row->>'accepted_count') !~ '^[0-9]+$'
       OR (v_row->>'duplicate_count') !~ '^[0-9]+$'
       OR (v_row->>'dropped_count') !~ '^[0-9]+$'
       OR (v_row->>'status' IN ('succeeded','cache_hit')
         AND COALESCE(v_row->>'response_hash','') !~ '^[0-9a-f]{64}$')
       OR (v_row->>'status' NOT IN ('succeeded','cache_hit')
         AND v_row->'response_hash'<>'null'::jsonb
         AND v_row->>'response_hash' !~ '^[0-9a-f]{64}$')
       OR (v_row->>'status' NOT IN ('succeeded','cache_hit') AND v_row->'expires_at'<>'null'::jsonb)
       OR (v_row->>'status' IN ('succeeded','cache_hit') AND v_row->'expires_at'='null'::jsonb)
       OR (v_row->'error'<>'null'::jsonb AND (
         jsonb_typeof(v_row->'error')<>'object' OR octet_length((v_row->'error')::text)>4096
       )) THEN
      RAISE EXCEPTION 'invalid market source receipt' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_reservation FROM public.market_source_quota_reservations
    WHERE id=(v_row->>'reservation_id')::uuid AND run_id=p_run_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE = '22023';
    END IF;
    IF (v_row->>'request_cost')::int > v_reservation.reserved_requests THEN
      RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE = '54000';
    END IF;
    INSERT INTO public.market_source_receipts(
      id,run_id,reservation_id,provider,status,cache_key,requested_window,retrieved_at,expires_at,
      request_cost,upstream_remaining,returned_count,accepted_count,duplicate_count,dropped_count,
      error,response_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,v_reservation.id,v_reservation.provider,v_row->>'status',
      v_row->>'cache_key',v_row->'requested_window',(v_row->>'retrieved_at')::timestamptz,
      (v_row->>'expires_at')::timestamptz,(v_row->>'request_cost')::int,
      (v_row->>'upstream_remaining')::int,(v_row->>'returned_count')::int,
      (v_row->>'accepted_count')::int,(v_row->>'duplicate_count')::int,
      (v_row->>'dropped_count')::int,NULLIF(v_row->'error','null'::jsonb),v_row->>'response_hash'
    );
    v_receipt_count := v_receipt_count + 1;
  END LOOP;
  IF EXISTS (
    SELECT 1
    FROM public.market_source_quota_reservations reservation
    JOIN (
      SELECT (value->>'reservation_id')::uuid AS reservation_id,
             sum((value->>'request_cost')::int) AS used
      FROM jsonb_array_elements(p_payload->'receipts') GROUP BY 1
    ) usage ON usage.reservation_id=reservation.id
    WHERE reservation.run_id=p_run_id AND usage.used>reservation.reserved_requests
  ) THEN
    RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE = '54000';
  END IF;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'items') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','run_item_id','receipt_id','upstream_item_id','canonical_url','published_at',
         'effective_at','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ])
       OR (v_row - ARRAY[
         'id','run_item_id','receipt_id','upstream_item_id','canonical_url','published_at',
         'effective_at','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ]) <> '{}'::jsonb
       OR char_length(v_row->>'title') NOT BETWEEN 1 AND 500
       OR char_length(v_row->>'normalized_text') > 2000
       OR char_length(v_row->>'canonical_content') > 4096
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(
         extensions.digest(convert_to(v_row->>'canonical_content','UTF8'),'sha256'),'hex'
       )
       OR jsonb_typeof(v_row->'metadata')<>'object'
       OR octet_length((v_row->'metadata')::text)>8192
       OR v_row->>'disposition' NOT IN ('accepted','duplicate','near_duplicate','dropped')
       OR (v_row->>'disposition'='accepted' AND v_row->'drop_reason'<>'null'::jsonb)
       OR (v_row->>'disposition'<>'accepted' AND (
         v_row->'drop_reason'='null'::jsonb OR char_length(v_row->>'drop_reason')>200
       ))
       OR (v_row->'canonical_url'<>'null'::jsonb AND (
         char_length(v_row->>'canonical_url')>2048 OR v_row->>'canonical_url' !~ '^https://'
       )) THEN
      RAISE EXCEPTION 'invalid market source item or content hash' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_receipt FROM public.market_source_receipts
    WHERE id=(v_row->>'receipt_id')::uuid AND run_id=p_run_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'source item receipt unavailable' USING ERRCODE = '22023';
    END IF;
    IF v_row->>'disposition'='accepted'
       AND v_receipt.status NOT IN ('succeeded','cache_hit') THEN
      RAISE EXCEPTION 'accepted source item requires successful receipt' USING ERRCODE = '22023';
    END IF;
    IF v_row->'canonical_url'<>'null'::jsonb THEN
      v_source_host := lower(substring(v_row->>'canonical_url' FROM '^https://([^/:?#]+)'));
      IF NOT (CASE v_receipt.provider
        WHEN 'gdelt' THEN v_source_host='api.gdeltproject.org'
        WHEN 'alpha_vantage' THEN v_source_host='www.alphavantage.co'
        WHEN 'finnhub' THEN v_source_host='finnhub.io'
        WHEN 'yahoo' THEN v_source_host='query1.finance.yahoo.com'
        WHEN 'sec_edgar' THEN v_source_host IN ('www.sec.gov','data.sec.gov')
        WHEN 'federal_register' THEN v_source_host='www.federalregister.gov'
        WHEN 'white_house' THEN v_source_host='www.whitehouse.gov'
        WHEN 'doe' THEN v_source_host='www.energy.gov'
        WHEN 'dod' THEN v_source_host='www.defense.gov'
        WHEN 'eia' THEN v_source_host IN ('api.eia.gov','www.eia.gov')
        WHEN 'fred' THEN v_source_host IN ('api.stlouisfed.org','fred.stlouisfed.org')
        WHEN 'bls' THEN v_source_host IN ('api.bls.gov','www.bls.gov')
        WHEN 'bea' THEN v_source_host IN ('apps.bea.gov','www.bea.gov')
        WHEN 'social' THEN v_source_host IN ('www.reddit.com','oauth.reddit.com')
        ELSE false END) THEN
        RAISE EXCEPTION 'source URL host mismatch' USING ERRCODE = '22023';
      END IF;
    END IF;
    INSERT INTO public.market_source_items(
      id,source_receipt_id,provider,upstream_item_id,canonical_url,published_at,effective_at,title,
      normalized_text,canonical_content,content_hash,metadata
    ) VALUES (
      (v_row->>'id')::uuid,v_receipt.id,v_receipt.provider,v_row->>'upstream_item_id',
      v_row->>'canonical_url',(v_row->>'published_at')::timestamptz,
      (v_row->>'effective_at')::timestamptz,v_row->>'title',v_row->>'normalized_text',
      v_row->>'canonical_content',v_row->>'content_hash',v_row->'metadata'
    );
    INSERT INTO public.market_intelligence_run_items(
      id,run_id,source_item_id,source_receipt_id,disposition,drop_reason
    ) VALUES (
      (v_row->>'run_item_id')::uuid,p_run_id,(v_row->>'id')::uuid,v_receipt.id,
      v_row->>'disposition',v_row->>'drop_reason'
    );
    v_item_count := v_item_count + 1;
  END LOOP;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'events') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','event_type','title','summary','occurred_at','effective_at','materiality',
         'confidence','evidence_item_ids','content_hash'
       ])
       OR (v_row - ARRAY[
         'id','event_type','title','summary','occurred_at','effective_at','materiality',
         'confidence','evidence_item_ids','content_hash'
       ]) <> '{}'::jsonb
       OR char_length(v_row->>'event_type') NOT BETWEEN 1 AND 80
       OR char_length(v_row->>'title') NOT BETWEEN 1 AND 500
       OR char_length(v_row->>'summary')>4000
       OR (v_row->>'materiality')::numeric NOT BETWEEN 0 AND 1
       OR (v_row->>'confidence')::numeric NOT BETWEEN 0 AND 1
       OR jsonb_typeof(v_row->'evidence_item_ids')<>'array'
       OR jsonb_array_length(v_row->'evidence_item_ids') NOT BETWEEN 1 AND 96
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_row - ARRAY['id','content_hash']),'UTF8'
       ),'sha256'),'hex') THEN
      RAISE EXCEPTION 'invalid market event' USING ERRCODE = '22023';
    END IF;
    FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_row->'evidence_item_ids') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1
        FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND run_item.disposition='accepted' AND receipt.run_id=p_run_id
          AND receipt.status IN ('succeeded','cache_hit')
      ) THEN
        RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023';
      END IF;
    END LOOP;
    INSERT INTO public.market_events(
      id,run_id,event_type,title,summary,occurred_at,effective_at,materiality,confidence,
      evidence_item_ids,content_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,v_row->>'event_type',v_row->>'title',v_row->>'summary',
      (v_row->>'occurred_at')::timestamptz,(v_row->>'effective_at')::timestamptz,
      (v_row->>'materiality')::numeric,(v_row->>'confidence')::numeric,
      v_row->'evidence_item_ids',v_row->>'content_hash'
    );
    v_event_count := v_event_count + 1;
  END LOOP;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'relationships') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','event_id','source_kind','source_key','target_kind','target_key',
         'relationship_type','hypothesis','evidence_item_ids','content_hash'
       ])
       OR (v_row - ARRAY[
         'id','event_id','source_kind','source_key','target_kind','target_key',
         'relationship_type','hypothesis','evidence_item_ids','content_hash'
       ]) <> '{}'::jsonb
       OR v_row->>'source_kind' NOT IN ('event','theme','value_chain','entity','security')
       OR v_row->>'target_kind' NOT IN ('theme','value_chain','entity','security','etf')
       OR char_length(v_row->>'source_key') NOT BETWEEN 1 AND 256
       OR char_length(v_row->>'target_key') NOT BETWEEN 1 AND 256
       OR char_length(v_row->>'relationship_type') NOT BETWEEN 1 AND 80
       OR jsonb_typeof(v_row->'hypothesis')<>'boolean'
       OR jsonb_typeof(v_row->'evidence_item_ids')<>'array'
       OR jsonb_array_length(v_row->'evidence_item_ids') NOT BETWEEN 1 AND 8
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_row - ARRAY['id','content_hash']),'UTF8'
       ),'sha256'),'hex')
       OR NOT EXISTS (
         SELECT 1 FROM public.market_events
         WHERE id=(v_row->>'event_id')::uuid AND run_id=p_run_id
       ) THEN
      RAISE EXCEPTION 'invalid market event relationship' USING ERRCODE = '22023';
    END IF;
    FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_row->'evidence_item_ids') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1 FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
      ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
    END LOOP;
    INSERT INTO public.market_event_relationships(
      id,run_id,event_id,source_kind,source_key,target_kind,target_key,relationship_type,
      hypothesis,evidence_item_ids,content_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,(v_row->>'event_id')::uuid,v_row->>'source_kind',
      v_row->>'source_key',v_row->>'target_kind',v_row->>'target_key',
      v_row->>'relationship_type',(v_row->>'hypothesis')::boolean,
      v_row->'evidence_item_ids',v_row->>'content_hash'
    );
    v_relationship_count := v_relationship_count + 1;
  END LOOP;

  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'rankings') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','event_id','candidate_key','ticker','rank','component_scores','total_score',
         'qualified','veto_reasons','exposure_item_ids','content_hash'
       ])
       OR (v_row - ARRAY[
         'id','event_id','candidate_key','ticker','rank','component_scores','total_score',
         'qualified','veto_reasons','exposure_item_ids','content_hash'
       ]) <> '{}'::jsonb
       OR char_length(v_row->>'candidate_key') NOT BETWEEN 1 AND 256
       OR (v_row->'ticker'<>'null'::jsonb AND v_row->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$')
       OR (v_row->>'rank')::int NOT BETWEEN 1 AND 100
       OR jsonb_typeof(v_row->'component_scores')<>'object'
       OR octet_length((v_row->'component_scores')::text)>8192
       OR (v_row->>'total_score')::numeric NOT BETWEEN -100000 AND 100000
       OR jsonb_typeof(v_row->'qualified')<>'boolean'
       OR jsonb_typeof(v_row->'veto_reasons')<>'array'
       OR jsonb_array_length(v_row->'veto_reasons')>20
       OR jsonb_typeof(v_row->'exposure_item_ids')<>'array'
       OR jsonb_array_length(v_row->'exposure_item_ids')>8
       OR ((v_row->>'qualified')::boolean AND jsonb_array_length(v_row->'exposure_item_ids')=0)
       OR v_row->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR v_row->>'content_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_row - ARRAY['id','content_hash']),'UTF8'
       ),'sha256'),'hex')
       OR (v_row->'event_id'<>'null'::jsonb AND NOT EXISTS (
         SELECT 1 FROM public.market_events
         WHERE id=(v_row->>'event_id')::uuid AND run_id=p_run_id
       )) THEN
      RAISE EXCEPTION 'invalid market candidate ranking' USING ERRCODE = '22023';
    END IF;
    FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_row->'exposure_item_ids') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1 FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
      ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
    END LOOP;
    INSERT INTO public.market_candidate_rankings(
      id,run_id,event_id,candidate_key,ticker,rank,component_scores,total_score,qualified,
      veto_reasons,exposure_item_ids,content_hash
    ) VALUES (
      (v_row->>'id')::uuid,p_run_id,(v_row->>'event_id')::uuid,v_row->>'candidate_key',
      v_row->>'ticker',(v_row->>'rank')::int,v_row->'component_scores',
      (v_row->>'total_score')::numeric,(v_row->>'qualified')::boolean,
      v_row->'veto_reasons',v_row->'exposure_item_ids',v_row->>'content_hash'
    );
    v_ranking_count := v_ranking_count + 1;
  END LOOP;

  IF p_payload->>'status'='completed' THEN
    v_packet := p_payload->'packet';
    IF NOT (v_packet ?& ARRAY['id','candidate_count','evidence_count','packet','packet_hash'])
       OR (v_packet - ARRAY['id','candidate_count','evidence_count','packet','packet_hash']) <> '{}'::jsonb
       OR (v_packet->>'candidate_count')::int NOT BETWEEN 0 AND 12
       OR (v_packet->>'evidence_count')::int NOT BETWEEN 0 AND 96
       OR jsonb_typeof(v_packet->'packet')<>'object'
       OR octet_length((v_packet->'packet')::text)>98304
       OR v_packet->>'packet_hash' !~ '^[0-9a-f]{64}$'
       OR v_packet->>'packet_hash' <> encode(extensions.digest(convert_to(
         public.market_canonical_jsonb(v_packet->'packet'),'UTF8'
       ),'sha256'),'hex')
       OR NOT (v_packet->'packet' ?& ARRAY[
         'candidates','evidence','coverage','limitations','policy_version'
       ])
       OR jsonb_typeof(v_packet->'packet'->'candidates')<>'array'
       OR jsonb_array_length(v_packet->'packet'->'candidates')<>(v_packet->>'candidate_count')::int
       OR jsonb_typeof(v_packet->'packet'->'evidence')<>'array'
       OR jsonb_array_length(v_packet->'packet'->'evidence')<>(v_packet->>'evidence_count')::int
       OR (v_packet->'packet'->>'policy_version')::int<>v_run.policy_version THEN
      RAISE EXCEPTION 'invalid or oversized evidence packet' USING ERRCODE = '22023';
    END IF;
    FOR v_candidate IN SELECT value FROM jsonb_array_elements(v_packet->'packet'->'candidates') LOOP
      IF jsonb_typeof(v_candidate)<>'object'
         OR jsonb_typeof(v_candidate->'evidence_ids')<>'array'
         OR jsonb_array_length(v_candidate->'evidence_ids')>8 THEN
        RAISE EXCEPTION 'evidence per candidate exceeds limit' USING ERRCODE = '22023';
      END IF;
    END LOOP;
    FOR v_evidence_id IN SELECT value->'item_id'
      FROM jsonb_array_elements(v_packet->'packet'->'evidence') LOOP
      IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
        SELECT 1 FROM public.market_intelligence_run_items run_item
        JOIN public.market_source_items item ON item.id=run_item.source_item_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
        WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
          AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
          AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
      ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
    END LOOP;
    FOR v_candidate IN SELECT value FROM jsonb_array_elements(v_packet->'packet'->'candidates') LOOP
      FOR v_evidence_id IN SELECT value FROM jsonb_array_elements(v_candidate->'evidence_ids') LOOP
        IF jsonb_typeof(v_evidence_id)<>'string' OR NOT EXISTS (
          SELECT 1 FROM public.market_intelligence_run_items run_item
          JOIN public.market_source_items item ON item.id=run_item.source_item_id
          JOIN public.market_source_receipts receipt
            ON receipt.id=run_item.source_receipt_id AND receipt.id=item.source_receipt_id
          WHERE run_item.run_id=p_run_id AND run_item.disposition='accepted'
            AND run_item.source_item_id=(v_evidence_id #>> '{}')::uuid
            AND receipt.run_id=p_run_id AND receipt.status IN ('succeeded','cache_hit')
        ) THEN RAISE EXCEPTION 'ineligible evidence item' USING ERRCODE = '22023'; END IF;
      END LOOP;
    END LOOP;
    INSERT INTO public.market_evidence_packets(
      id,run_id,policy_version,status,candidate_count,evidence_count,packet,packet_hash
    ) VALUES (
      (v_packet->>'id')::uuid,p_run_id,v_run.policy_version,'completed',
      (v_packet->>'candidate_count')::int,(v_packet->>'evidence_count')::int,
      v_packet->'packet',v_packet->>'packet_hash'
    );
  END IF;

  v_receipt_json := jsonb_build_object(
    'run_id',p_run_id,
    'completion_id',p_completion_id,
    'status',p_payload->>'status',
    'counts',jsonb_build_object(
      'source_receipts',v_receipt_count,
      'source_items',v_item_count,
      'events',v_event_count,
      'relationships',v_relationship_count,
      'rankings',v_ranking_count,
      'packets',CASE WHEN p_payload->>'status'='completed' THEN 1 ELSE 0 END
    ),
    'packet_id',CASE WHEN p_payload->>'status'='completed' THEN v_packet->>'id' ELSE NULL END,
    'packet_hash',CASE WHEN p_payload->>'status'='completed' THEN v_packet->>'packet_hash' ELSE NULL END,
    'duplicate',false
  );
  INSERT INTO public.market_intelligence_run_events(id,run_id,status,detail)
  VALUES (
    p_completion_id,p_run_id,p_payload->>'status',jsonb_build_object(
      'payload_hash',v_payload_hash,
      'coverage',p_payload->'coverage',
      'error',p_payload->'error',
      'receipt',v_receipt_json
    )
  );
  RETURN v_receipt_json;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_intelligence_provider_v2(
  p_run_id UUID, p_completion_id UUID, p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_row JSONB;
  v_legacy_items JSONB;
  v_result JSONB;
  v_request_host TEXT;
  v_valid_request_host BOOLEAN;
BEGIN
  IF p_run_id IS NULL OR p_completion_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR jsonb_typeof(p_payload->'items')<>'array' THEN
    RAISE EXCEPTION 'invalid intelligence completion payload' USING ERRCODE='22023';
  END IF;
  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'items') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','run_item_id','receipt_id','provider','upstream_item_id','canonical_url','request_url',
         'published_at','retrieved_at','effective_at','reporting_at','entity_ids','security_ids',
         'discovery_status','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ])
       OR (v_row - ARRAY[
         'id','run_item_id','receipt_id','provider','upstream_item_id','canonical_url','request_url',
         'published_at','retrieved_at','effective_at','reporting_at','entity_ids','security_ids',
         'discovery_status','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ]) <> '{}'::jsonb
       OR COALESCE(char_length(v_row->>'upstream_item_id'),0) NOT BETWEEN 1 AND 512
       OR COALESCE(char_length(v_row->>'canonical_url'),0) NOT BETWEEN 1 AND 2048
       OR COALESCE(char_length(v_row->>'request_url'),0) NOT BETWEEN 1 AND 2048
       OR v_row->>'canonical_url' !~ '^https://' OR v_row->>'request_url' !~ '^https://'
       OR lower(v_row->>'request_url') ~ '(api[_-]?key|token|secret|password)='
       OR jsonb_typeof(v_row->'entity_ids')<>'array' OR jsonb_array_length(v_row->'entity_ids')>32
       OR jsonb_typeof(v_row->'security_ids')<>'array' OR jsonb_array_length(v_row->'security_ids')>32
       OR v_row->>'discovery_status' NOT IN ('qualified','no_event','insufficient_coverage')
       OR (v_row->>'discovery_status'='qualified' AND jsonb_array_length(v_row->'security_ids')=0) THEN
      RAISE EXCEPTION 'invalid provider evidence provenance' USING ERRCODE='22023';
    END IF;
    v_request_host := lower(substring(v_row->>'request_url' FROM '^https://([^/:?#]+)'));
    v_valid_request_host := CASE v_row->>'provider'
      WHEN 'gdelt' THEN v_request_host='api.gdeltproject.org'
      WHEN 'alpha_vantage' THEN v_request_host='www.alphavantage.co'
      WHEN 'finnhub' THEN v_request_host='finnhub.io'
      WHEN 'yahoo' THEN v_request_host='query1.finance.yahoo.com'
      WHEN 'sec_edgar' THEN v_request_host IN ('www.sec.gov','data.sec.gov')
      WHEN 'federal_register' THEN v_request_host='www.federalregister.gov'
      WHEN 'white_house' THEN v_request_host='www.whitehouse.gov'
      WHEN 'doe' THEN v_request_host='www.energy.gov'
      WHEN 'dod' THEN v_request_host='www.defense.gov'
      WHEN 'eia' THEN v_request_host IN ('api.eia.gov','www.eia.gov')
      WHEN 'fred' THEN v_request_host IN ('api.stlouisfed.org','fred.stlouisfed.org')
      WHEN 'bls' THEN v_request_host IN ('api.bls.gov','www.bls.gov')
      WHEN 'bea' THEN v_request_host IN ('apps.bea.gov','www.bea.gov')
      WHEN 'social' THEN v_request_host IN ('www.reddit.com','oauth.reddit.com')
      ELSE false END;
    IF NOT v_valid_request_host THEN
      RAISE EXCEPTION 'provider request URL host mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;

  SELECT jsonb_agg(
    ((value - ARRAY['provider','request_url','retrieved_at','reporting_at','entity_ids','security_ids','discovery_status'])
      || jsonb_build_object('canonical_url', value->'request_url') || CASE WHEN value->>'disposition'='near_duplicate' THEN jsonb_build_object('disposition','accepted','drop_reason',NULL) ELSE '{}'::jsonb END)
  ) INTO v_legacy_items FROM jsonb_array_elements(p_payload->'items');
  v_result := public.record_market_intelligence_legacy(
    p_run_id, p_completion_id, jsonb_set(p_payload, '{items}', COALESCE(v_legacy_items,'[]'::jsonb))
  );
  INSERT INTO public.market_source_item_provenance(
    source_item_id,provider,canonical_item_url,request_url,retrieved_at,reporting_at,
    entity_ids,security_ids,discovery_status
  )
  SELECT (value->>'id')::uuid,value->>'provider',value->>'canonical_url',value->>'request_url',
    (value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,
    value->'entity_ids',value->'security_ids',value->>'discovery_status'
  FROM jsonb_array_elements(p_payload->'items')
  ON CONFLICT (source_item_id) DO NOTHING;
  RETURN v_result;
END;
$$;

-- Only first initialization of the immutable session window is permitted.
CREATE OR REPLACE FUNCTION public.initialize_market_intelligence_window()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  IF TG_OP='UPDATE' AND OLD.request_window IS NULL AND NEW.request_window IS NOT NULL
     AND to_jsonb(OLD)-'request_window'=to_jsonb(NEW)-'request_window' THEN RETURN NEW; END IF;
  RAISE EXCEPTION 'market intelligence records are append-only' USING ERRCODE='55000';
END; $$;
DROP TRIGGER IF EXISTS market_intelligence_runs_append_only ON public.market_intelligence_runs;
CREATE TRIGGER market_intelligence_runs_append_only BEFORE UPDATE OR DELETE ON public.market_intelligence_runs
FOR EACH ROW EXECUTE FUNCTION public.initialize_market_intelligence_window();

CREATE TABLE IF NOT EXISTS public.market_intelligence_collection_completions (
  completion_id UUID PRIMARY KEY REFERENCES public.market_intelligence_run_events(id) ON DELETE RESTRICT,
  run_id UUID UNIQUE NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=1048576),
  receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.market_intelligence_collection_completions ENABLE ROW LEVEL SECURITY;
CREATE TRIGGER market_intelligence_collection_completions_append_only BEFORE UPDATE OR DELETE
ON public.market_intelligence_collection_completions FOR EACH ROW EXECUTE FUNCTION public.reject_market_intelligence_mutation();
REVOKE ALL ON public.market_intelligence_collection_completions FROM PUBLIC,anon,authenticated,service_role;
ALTER TABLE public.market_checkpoint_receipt_lineage ADD CONSTRAINT distinct_cache_predecessor CHECK(cache_receipt_id<>cache_predecessor_receipt_id);

CREATE OR REPLACE FUNCTION public.read_market_intelligence_completion(p_run_id UUID,p_completion_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_saved public.market_intelligence_collection_completions%ROWTYPE; v_providers JSONB;
BEGIN
  SELECT * INTO v_saved FROM public.market_intelligence_collection_completions
    WHERE run_id=p_run_id AND completion_id=p_completion_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT jsonb_object_agg(id::text,provider) INTO v_providers FROM public.market_source_quota_reservations WHERE run_id=p_run_id;
  RETURN jsonb_build_object('receipt',v_saved.receipt,'payload',v_saved.payload,'providers',v_providers);
END; $$;

CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,p_phase TEXT,p_market_date DATE,p_policy_version INT,p_reservation_plan JSONB,p_request_window JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_window JSONB; v_entries JSONB; v_usage JSONB;
BEGIN
  PERFORM 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'analysis run is not running' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.market_intelligence_quote_attempts WHERE run_id=p_run_id AND status='pending') THEN
    RAISE EXCEPTION 'quote collection outcome uncertain' USING ERRCODE='55000'; END IF;
  IF p_request_window IS NULL OR jsonb_typeof(p_request_window)<>'object'
     OR NOT(p_request_window ?& ARRAY['start','end','timezone','market_date','phase'])
     OR (p_request_window-ARRAY['start','end','timezone','market_date','phase'])<>'{}'::jsonb
     OR p_request_window->>'timezone'<>'America/Chicago' OR p_request_window->>'phase'<>p_phase
     OR p_request_window->>'market_date'<>p_market_date::text
     OR (p_request_window->>'start')::timestamptz >= (p_request_window->>'end')::timestamptz THEN
    RAISE EXCEPTION 'invalid intelligence request window' USING ERRCODE='22023'; END IF;
  v_result:=public.start_market_intelligence_run(p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan);
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL THEN
    UPDATE public.market_intelligence_runs SET request_window=p_request_window WHERE id=p_run_id RETURNING request_window INTO v_window;
  END IF;
  SELECT COALESCE(jsonb_agg(payload||jsonb_build_object('cache_key',cache_key) ORDER BY cache_key),'[]'::jsonb)
    INTO v_entries FROM public.market_collection_checkpoints WHERE run_id=p_run_id
      AND ((payload->'receipt'->>'status') IN ('failed','quota_blocked') OR (payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp());
  SELECT COALESCE(jsonb_object_agg(reservation_id,used),'{}'::jsonb) INTO v_usage FROM (
    SELECT payload->'receipt'->>'reservation_id' reservation_id,sum((payload->'receipt'->>'request_cost')::int) used
    FROM (SELECT payload FROM public.market_collection_checkpoints WHERE run_id=p_run_id
      UNION ALL SELECT payload FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id) paid GROUP BY 1
  ) usage;
  RETURN jsonb_build_object('run_id',p_run_id,'reservation_ids',v_result->'reservation_ids','cache_entries',v_entries,
    'request_window',v_window,'reservation_usage',v_usage,'duplicate',(v_result->>'duplicate')::boolean);
END; $$;

CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(p_run_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_window JSONB; v_existing public.market_collection_checkpoints%ROWTYPE; r JSONB;
  v_reserved public.market_source_quota_reservations%ROWTYPE; v_used INT;
BEGIN
  IF p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR NOT(p_payload ?& ARRAY['cache_key','receipt','items'])
     OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb OR octet_length(p_payload::text)>65536
     OR jsonb_typeof(p_payload->'items')<>'array' OR jsonb_array_length(p_payload->'items')>50 THEN
    RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023'; END IF;
  SELECT request_window INTO v_window FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  r:=p_payload->'receipt';
  IF r IS NULL OR NOT(r ?& ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])
     OR (r-ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])<>'{}'::jsonb
     OR r->>'status' NOT IN ('succeeded','failed','quota_blocked') OR (r->>'request_cost')::int NOT BETWEEN 1 AND 100
     OR r->'cache_predecessor_receipt_id'<>'null'::jsonb OR r->>'cache_key'<>p_payload->>'cache_key'
     OR (r->'requested_window'->>'start')::timestamptz<>(v_window->>'start')::timestamptz
     OR (r->'requested_window'->>'end')::timestamptz<>(v_window->>'end')::timestamptz
     OR ((r->>'status')='succeeded' AND ((r->>'expires_at')::timestamptz<=(r->>'retrieved_at')::timestamptz OR r->>'response_hash' !~ '^[0-9a-f]{64}$'))
     OR ((r->>'status')<>'succeeded' AND (r->'expires_at'<>'null'::jsonb OR COALESCE(r->>'error_code','')='')) THEN
    RAISE EXCEPTION 'checkpoint must record an actual request outcome' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_reserved FROM public.market_source_quota_reservations WHERE run_id=p_run_id AND id=(r->>'reservation_id')::uuid AND provider=r->>'provider';
  IF NOT FOUND THEN RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_existing FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key' FOR UPDATE;
  IF FOUND AND v_existing.payload=p_payload-'cache_key' THEN RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key'); END IF;
  IF FOUND AND (v_existing.source_receipt_id=(r->>'source_receipt_id')::uuid
     OR (v_existing.payload->'receipt'->>'expires_at')::timestamptz>statement_timestamp()) THEN
    RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(sum((payload->'receipt'->>'request_cost')::int),0) INTO v_used FROM (
    SELECT payload FROM public.market_collection_checkpoints WHERE run_id=p_run_id
    UNION ALL SELECT payload FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id
  ) paid WHERE payload->'receipt'->>'reservation_id'=r->>'reservation_id';
  IF v_used+(r->>'request_cost')::int>v_reserved.reserved_requests THEN RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000'; END IF;
  IF v_existing.run_id IS NOT NULL THEN
    INSERT INTO public.market_collection_checkpoint_history(run_id,cache_key,source_receipt_id,payload)
      VALUES(v_existing.run_id,v_existing.cache_key,v_existing.source_receipt_id,v_existing.payload);
    UPDATE public.market_collection_checkpoints SET source_receipt_id=(r->>'source_receipt_id')::uuid,
      payload=p_payload-'cache_key',created_at=statement_timestamp() WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
  ELSE
    INSERT INTO public.market_collection_checkpoints(run_id,cache_key,request_window,source_receipt_id,payload)
      VALUES(p_run_id,p_payload->>'cache_key',v_window,(r->>'source_receipt_id')::uuid,p_payload-'cache_key');
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
END; $$;

-- This outer contract owns lineage; the inner recorder accepts exactly its original keys.
CREATE OR REPLACE FUNCTION public.record_market_intelligence(p_run_id UUID,p_completion_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_effective JSONB; v_result JSONB; r JSONB; c JSONB; v_provider TEXT;
  v_saved public.market_intelligence_collection_completions%ROWTYPE; v_receipts JSONB;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('market-intelligence-completion:'||p_completion_id::text,0));
  SELECT * INTO v_saved FROM public.market_intelligence_collection_completions WHERE completion_id=p_completion_id;
  IF FOUND THEN
    IF v_saved.run_id<>p_run_id OR v_saved.payload IS DISTINCT FROM p_payload THEN RAISE EXCEPTION 'intelligence completion idempotency mismatch' USING ERRCODE='22023'; END IF;
    RETURN v_saved.receipt||jsonb_build_object('duplicate',true);
  END IF;
  PERFORM 1 FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.market_intelligence_quote_attempts WHERE run_id=p_run_id AND status='pending') THEN
    RAISE EXCEPTION 'quote collection outcome uncertain' USING ERRCODE='55000'; END IF;
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'receipts') LOOP
    IF NOT(r ? 'cache_predecessor_receipt_id') THEN RAISE EXCEPTION 'missing cache lineage' USING ERRCODE='22023'; END IF;
    SELECT provider INTO v_provider FROM public.market_source_quota_reservations WHERE run_id=p_run_id AND id=(r->>'reservation_id')::uuid;
    IF NOT FOUND THEN RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE='22023'; END IF;
    IF r->>'status'='cache_hit' THEN
      IF r->>'id'=r->>'cache_predecessor_receipt_id' OR (r->>'request_cost')::int<>0 THEN RAISE EXCEPTION 'invalid cache lineage' USING ERRCODE='22023'; END IF;
      SELECT payload->'receipt' INTO c FROM (
        SELECT source_receipt_id,payload FROM public.market_collection_checkpoints WHERE run_id=p_run_id
        UNION ALL SELECT source_receipt_id,payload FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id
      ) checkpoint WHERE source_receipt_id=(r->>'cache_predecessor_receipt_id')::uuid;
      IF c IS NULL OR c->>'status'<>'succeeded' OR c->>'provider' IS DISTINCT FROM v_provider
        OR c->>'reservation_id' IS DISTINCT FROM r->>'reservation_id'
        OR c->>'cache_key' IS DISTINCT FROM r->>'cache_key' OR c->'requested_window' IS DISTINCT FROM r->'requested_window'
        OR c->>'response_hash' IS DISTINCT FROM r->>'response_hash'
        OR c->'retrieved_at' IS DISTINCT FROM r->'retrieved_at' OR c->'expires_at' IS DISTINCT FROM r->'expires_at' THEN
        RAISE EXCEPTION 'cache predecessor checkpoint unavailable' USING ERRCODE='22023'; END IF;
    ELSIF r->'cache_predecessor_receipt_id'<>'null'::jsonb THEN RAISE EXCEPTION 'invalid cache lineage' USING ERRCODE='22023'; END IF;
  END LOOP;
  SELECT COALESCE(jsonb_agg(value-'cache_predecessor_receipt_id'),'[]'::jsonb) INTO v_receipts FROM jsonb_array_elements(p_payload->'receipts');
  -- Restore every actual checkpoint into the authoritative quota/source ledger.
  -- A cache-hit receipt never replaces or rewrites the original paid receipt.
  FOR c IN SELECT payload->'receipt' FROM public.market_collection_checkpoints WHERE run_id=p_run_id
    UNION ALL SELECT payload->'receipt' FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id LOOP
    r:=(c-ARRAY['provider','source_receipt_id','requested_limit','observed_at','error_code','cache_predecessor_receipt_id'])
      ||jsonb_build_object('id',c->'source_receipt_id','error',CASE WHEN c->'error_code'='null'::jsonb THEN 'null'::jsonb ELSE jsonb_build_object('code',c->'error_code') END);
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(v_receipts) value WHERE value->>'id'=r->>'id') THEN
      IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_receipts) value WHERE value=r) THEN RAISE EXCEPTION 'checkpoint receipt mismatch' USING ERRCODE='22023'; END IF;
    ELSE v_receipts:=v_receipts||jsonb_build_array(r); END IF;
  END LOOP;
  v_effective:=jsonb_set(p_payload,'{receipts}',v_receipts);
  v_result:=public.record_market_intelligence_provider_v2(p_run_id,p_completion_id,v_effective);
  INSERT INTO public.market_run_source_item_provenance(run_item_id,run_id,source_item_id,source_receipt_id,provider,request_url,retrieved_at,reporting_at,entity_ids,security_ids,discovery_status)
    SELECT (value->>'run_item_id')::uuid,p_run_id,(value->>'id')::uuid,(value->>'receipt_id')::uuid,value->>'provider',value->>'request_url',(value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,value->'entity_ids',value->'security_ids',value->>'discovery_status'
    FROM jsonb_array_elements(p_payload->'items') value;
  INSERT INTO public.market_checkpoint_receipt_lineage(cache_receipt_id,run_id,cache_predecessor_receipt_id)
    SELECT (value->>'id')::uuid,p_run_id,(value->>'cache_predecessor_receipt_id')::uuid FROM jsonb_array_elements(p_payload->'receipts') value WHERE value->>'status'='cache_hit';
  INSERT INTO public.market_intelligence_collection_completions(completion_id,run_id,payload,receipt) VALUES(p_completion_id,p_run_id,p_payload,v_result);
  RETURN v_result;
END; $$;

REVOKE ALL ON FUNCTION public.record_market_intelligence_legacy(UUID,UUID,JSONB),public.record_market_intelligence_provider_v2(UUID,UUID,JSONB) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.read_market_intelligence_completion(UUID,UUID),public.record_market_intelligence(UUID,UUID,JSONB),public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB),public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.read_market_intelligence_completion(UUID,UUID),public.record_market_intelligence(UUID,UUID,JSONB),public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB),public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;

ALTER TABLE public.market_intelligence_context_inputs ADD COLUMN IF NOT EXISTS current_quotes JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.market_intelligence_context_inputs ADD COLUMN IF NOT EXISTS quote_receipt_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
REVOKE ALL ON public.market_intelligence_context_inputs FROM PUBLIC,anon,authenticated,service_role;

CREATE TABLE IF NOT EXISTS public.market_intelligence_quote_attempts (
  source_receipt_id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id),
  reservation_id UUID NOT NULL REFERENCES public.market_source_quota_reservations(id), ticker TEXT NOT NULL,
  cache_key TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed')),
  quote JSONB, checkpoint JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.market_intelligence_quote_attempts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.market_intelligence_quote_attempts FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.claim_market_intelligence_quote(p_run_id UUID,p_input JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_run public.market_intelligence_runs%ROWTYPE; v_existing public.market_intelligence_quote_attempts%ROWTYPE;
  v_reserved INT; v_used INT;
BEGIN
  SELECT * INTO v_run FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND OR v_run.request_window IS NULL OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
    OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023'; END IF;
  IF p_input IS NULL OR NOT(p_input ?& ARRAY['ticker','cache_key','source_receipt_id','reservation_id'])
    OR (p_input-ARRAY['ticker','cache_key','source_receipt_id','reservation_id'])<>'{}'::jsonb
    OR p_input->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$' OR p_input->>'cache_key' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid quote collection identity' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_existing FROM public.market_intelligence_quote_attempts WHERE source_receipt_id=(p_input->>'source_receipt_id')::uuid;
  IF FOUND THEN
    IF v_existing.run_id<>p_run_id OR v_existing.ticker<>p_input->>'ticker' OR v_existing.cache_key<>p_input->>'cache_key'
      OR v_existing.reservation_id<>(p_input->>'reservation_id')::uuid THEN RAISE EXCEPTION 'quote identity mismatch' USING ERRCODE='22023'; END IF;
    RETURN jsonb_build_object('status',CASE WHEN v_existing.status='pending' THEN 'uncertain' ELSE 'completed' END,'checkpoint',v_existing.checkpoint);
  END IF;
  SELECT reserved_requests INTO v_reserved FROM public.market_source_quota_reservations
    WHERE id=(p_input->>'reservation_id')::uuid AND run_id=p_run_id AND provider='yahoo';
  IF NOT FOUND THEN RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(sum(cost),0) INTO v_used FROM (
    SELECT (payload->'receipt'->>'request_cost')::int cost FROM public.market_collection_checkpoints WHERE run_id=p_run_id AND payload->'receipt'->>'provider'='yahoo'
    UNION ALL SELECT (payload->'receipt'->>'request_cost')::int FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id AND payload->'receipt'->>'provider'='yahoo'
    UNION ALL SELECT 1 FROM public.market_intelligence_quote_attempts WHERE run_id=p_run_id AND status='pending'
  ) used;
  IF v_used>=v_reserved THEN RETURN jsonb_build_object('status','quota_blocked','request_window',v_run.request_window); END IF;
  INSERT INTO public.market_intelligence_quote_attempts(source_receipt_id,run_id,reservation_id,ticker,cache_key,status)
    VALUES((p_input->>'source_receipt_id')::uuid,p_run_id,(p_input->>'reservation_id')::uuid,p_input->>'ticker',p_input->>'cache_key','pending');
  RETURN jsonb_build_object('status','claimed','request_window',v_run.request_window);
END; $$;

-- Called only by the server producer after its own bounded provider fetch.
-- There is deliberately no gateway operation accepting p_quote or p_checkpoint.
CREATE OR REPLACE FUNCTION public.record_market_intelligence_quote(p_run_id UUID,p_receipt_id UUID,p_quote JSONB,p_checkpoint JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_attempt public.market_intelligence_quote_attempts%ROWTYPE;
BEGIN
  SELECT * INTO v_attempt FROM public.market_intelligence_quote_attempts WHERE source_receipt_id=p_receipt_id AND run_id=p_run_id FOR UPDATE;
  IF NOT FOUND OR v_attempt.status<>'pending' THEN RAISE EXCEPTION 'quote attempt unavailable' USING ERRCODE='22023'; END IF;
  IF p_checkpoint->'receipt'->>'source_receipt_id' IS DISTINCT FROM p_receipt_id::text
    OR p_checkpoint->'receipt'->>'reservation_id' IS DISTINCT FROM v_attempt.reservation_id::text
    OR p_checkpoint->>'cache_key' IS DISTINCT FROM v_attempt.cache_key
    OR (p_checkpoint->'receipt'->>'request_cost')::int<>1
    OR p_checkpoint->'receipt'->>'provider' IS DISTINCT FROM 'yahoo'
    OR p_checkpoint->'receipt'->>'status' IS DISTINCT FROM (CASE WHEN p_quote IS NULL THEN 'failed' ELSE 'succeeded' END)
    OR (p_quote IS NOT NULL AND p_checkpoint->'receipt'->>'response_hash' IS DISTINCT FROM p_quote->>'response_hash')
    OR (p_quote IS NOT NULL AND (p_quote->>'ticker' IS DISTINCT FROM v_attempt.ticker OR p_quote->>'currency' IS DISTINCT FROM 'USD')) THEN
    RAISE EXCEPTION 'quote outcome identity mismatch' USING ERRCODE='22023'; END IF;
  PERFORM public.checkpoint_market_intelligence_collection(p_run_id,p_checkpoint);
  UPDATE public.market_intelligence_quote_attempts SET status=CASE WHEN p_quote IS NULL THEN 'failed' ELSE 'succeeded' END,
    quote=p_quote,checkpoint=p_checkpoint WHERE source_receipt_id=p_receipt_id;
  RETURN p_checkpoint;
END; $$;
REVOKE ALL ON FUNCTION public.claim_market_intelligence_quote(UUID,JSONB),public.record_market_intelligence_quote(UUID,UUID,JSONB,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.claim_market_intelligence_quote(UUID,JSONB),public.record_market_intelligence_quote(UUID,UUID,JSONB,JSONB) TO service_role;

-- No caller-supplied score, valuation, or overlap parameter exists. This producer
-- reads server-acquired Yahoo outcomes and the current reconciled holdings table.
CREATE OR REPLACE FUNCTION public.refresh_market_intelligence_context(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_quotes JSONB:='{}'; v_values JSONB:='{}'; v_liquidity JSONB:='{}'; v_overlap JSONB:='{}'; v_ids JSONB:='[]';
  row_data RECORD; q JSONB; v_total NUMERIC:=0; v_complete BOOLEAN:=true; v_equities BOOLEAN:=true; v_result JSONB;
BEGIN
  IF NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id) THEN RAISE EXCEPTION 'analysis run unavailable' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    SELECT jsonb_build_object('holding_market_values',holding_market_values,'liquidity_by_ticker',liquidity_by_ticker,
      'overlap_by_ticker',overlap_by_ticker,'current_quotes',current_quotes,'quote_receipt_ids',quote_receipt_ids)
      INTO v_result FROM public.market_intelligence_context_inputs WHERE run_id=p_run_id;
    RETURN v_result;
  END IF;
  FOR row_data IN SELECT source_receipt_id,quote FROM public.market_intelligence_quote_attempts
    WHERE run_id=p_run_id AND status='succeeded' ORDER BY created_at LOOP
    q:=row_data.quote;
    IF q IS NULL OR NOT(q ?& ARRAY['ticker','currency','price','as_of','instrument_type','average_daily_dollar_volume'])
      OR q->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$' OR q->>'currency'<>'USD'
      OR q->>'instrument_type' NOT IN ('EQUITY','ETF') OR q->>'price' !~ '^[0-9]+(\.[0-9]+)?$'
      OR (q->>'average_daily_dollar_volume' IS NOT NULL AND q->>'average_daily_dollar_volume' !~ '^[0-9]+(\.[0-9]+)?$')
      THEN CONTINUE; END IF;
    IF (q->>'price')::numeric<=0 OR (q->>'average_daily_dollar_volume')::numeric<=0
      OR (q->>'as_of')::timestamptz<statement_timestamp()-interval '20 minutes'
      OR (q->>'as_of')::timestamptz>statement_timestamp()+interval '5 minutes' THEN CONTINUE; END IF;
    v_quotes:=v_quotes||jsonb_build_object(q->>'ticker',q);
    IF q->>'average_daily_dollar_volume' IS NOT NULL THEN
      v_liquidity:=v_liquidity||jsonb_build_object(q->>'ticker',round(least(1,(q->>'average_daily_dollar_volume')::numeric/10000000),6)::text);
    END IF;
    v_ids:=v_ids||jsonb_build_array(row_data.source_receipt_id);
  END LOOP;
  FOR row_data IN SELECT ticker,shares FROM public.holdings WHERE shares>0 ORDER BY ticker LIMIT 101 LOOP
    q:=v_quotes->row_data.ticker;
    IF q IS NULL THEN v_complete:=false; v_equities:=false; CONTINUE; END IF;
    v_values:=v_values||jsonb_build_object(row_data.ticker,(row_data.shares*(q->>'price')::numeric)::text);
    v_total:=v_total+row_data.shares*(q->>'price')::numeric;
    IF q->>'instrument_type'<>'EQUITY' THEN v_equities:=false; END IF;
  END LOOP;
  IF (SELECT count(*) FROM public.holdings WHERE shares>0)>100 THEN RAISE EXCEPTION 'holdings exceed context bound' USING ERRCODE='54000'; END IF;
  -- Overlap is provable for an all-equity inventory. ETF underlying composition
  -- is unavailable from a quote, so it cannot silently contribute zero overlap.
  IF v_complete AND v_equities AND v_total>0 THEN
    FOR row_data IN SELECT key,value FROM jsonb_each(v_quotes) LOOP
      IF row_data.value->>'instrument_type'='EQUITY' THEN
        v_overlap:=v_overlap||jsonb_build_object(row_data.key,round(COALESCE((v_values->>row_data.key)::numeric,0)/v_total,6)::text);
      END IF;
    END LOOP;
  END IF;
  INSERT INTO public.market_intelligence_context_inputs(run_id,holding_market_values,liquidity_by_ticker,overlap_by_ticker,current_quotes,quote_receipt_ids)
    VALUES(p_run_id,v_values,v_liquidity,v_overlap,v_quotes,v_ids)
    ON CONFLICT(run_id) DO UPDATE SET holding_market_values=EXCLUDED.holding_market_values,
      liquidity_by_ticker=EXCLUDED.liquidity_by_ticker,overlap_by_ticker=EXCLUDED.overlap_by_ticker,
      current_quotes=EXCLUDED.current_quotes,quote_receipt_ids=EXCLUDED.quote_receipt_ids;
  RETURN jsonb_build_object('holding_market_values',v_values,'liquidity_by_ticker',v_liquidity,'overlap_by_ticker',v_overlap,
    'current_quotes',v_quotes,'quote_receipt_ids',v_ids);
END; $$;
REVOKE ALL ON FUNCTION public.refresh_market_intelligence_context(UUID) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.refresh_market_intelligence_context(UUID) TO service_role;
