-- Route the normal V2 producer through a strict protected recorder while preserving
-- every earlier migration byte-for-byte. V1/failed completions keep their existing path.

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
  v_existing_source public.market_source_items%ROWTYPE;
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
    SELECT * INTO v_existing_source FROM public.market_source_items
    WHERE id=(v_row->>'id')::uuid;
    IF FOUND THEN
      IF v_existing_source.provider IS DISTINCT FROM v_receipt.provider
         OR v_existing_source.upstream_item_id IS DISTINCT FROM v_row->>'upstream_item_id'
         OR v_existing_source.published_at IS DISTINCT FROM (v_row->>'published_at')::timestamptz
         OR v_existing_source.effective_at IS DISTINCT FROM (v_row->>'effective_at')::timestamptz
         OR v_existing_source.title IS DISTINCT FROM v_row->>'title'
         OR v_existing_source.normalized_text IS DISTINCT FROM v_row->>'normalized_text'
         OR v_existing_source.canonical_content IS DISTINCT FROM v_row->>'canonical_content'
         OR v_existing_source.content_hash IS DISTINCT FROM v_row->>'content_hash'
         OR v_existing_source.metadata IS DISTINCT FROM v_row->'metadata' THEN
        RAISE EXCEPTION 'content-addressed source item mismatch' USING ERRCODE='22023';
      END IF;
    ELSE
      INSERT INTO public.market_source_items(
        id,source_receipt_id,provider,upstream_item_id,canonical_url,published_at,effective_at,title,
        normalized_text,canonical_content,content_hash,metadata
      ) VALUES (
        (v_row->>'id')::uuid,v_receipt.id,v_receipt.provider,v_row->>'upstream_item_id',
        v_row->>'canonical_url',(v_row->>'published_at')::timestamptz,
        (v_row->>'effective_at')::timestamptz,v_row->>'title',v_row->>'normalized_text',
        v_row->>'canonical_content',v_row->>'content_hash',v_row->'metadata'
      );
    END IF;
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
          ON receipt.id=run_item.source_receipt_id
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
          ON receipt.id=run_item.source_receipt_id
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
          ON receipt.id=run_item.source_receipt_id
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
          ON receipt.id=run_item.source_receipt_id
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
            ON receipt.id=run_item.source_receipt_id
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

CREATE OR REPLACE FUNCTION public.promote_market_evidence_packet_v2()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  IF TG_OP='UPDATE'
     AND (to_jsonb(NEW)-ARRAY['candidate_count','evidence_count','packet','packet_hash'])
         =(to_jsonb(OLD)-ARRAY['candidate_count','evidence_count','packet','packet_hash'])
     AND OLD.packet->'coverage'->>'v2_pending_packet_hash'=NEW.packet_hash
     AND NEW.packet->>'contract_version'='2'
     AND NEW.candidate_count=jsonb_array_length(NEW.packet->'research_candidates')
     AND NEW.evidence_count=jsonb_array_length(NEW.packet->'evidence')
     AND NEW.packet_hash=encode(extensions.digest(convert_to(
       public.market_canonical_jsonb(NEW.packet),'UTF8'
     ),'sha256'),'hex') THEN
    PERFORM public.validate_market_evidence_packet_v2(
      NEW.run_id,NEW.policy_version,NEW.packet
    );
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'market intelligence ledgers are append-only' USING ERRCODE='55000';
END;
$$;

DROP TRIGGER IF EXISTS market_evidence_packets_append_only ON public.market_evidence_packets;
CREATE TRIGGER market_evidence_packets_append_only BEFORE UPDATE OR DELETE
ON public.market_evidence_packets FOR EACH ROW
EXECUTE FUNCTION public.promote_market_evidence_packet_v2();

CREATE OR REPLACE FUNCTION public.record_market_intelligence_v2_completion(
  p_run_id UUID,p_completion_id UUID,p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_effective JSONB;
  v_result JSONB;
  v_actual_packet JSONB;
  v_shadow_packet JSONB;
  v_shadow_body JSONB;
  r JSONB;
  c JSONB;
  v_provider TEXT;
  v_saved public.market_intelligence_collection_completions%ROWTYPE;
  v_receipts JSONB;
BEGIN
  IF p_run_id IS NULL OR p_completion_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR octet_length(p_payload::text)>1048576
     OR NOT(p_payload ?& ARRAY[
       'status','coverage','receipts','items','events','relationships','rankings','packet','error'
     ])
     OR (p_payload-ARRAY[
       'status','coverage','receipts','items','events','relationships','rankings','packet','error'
     ])<>'{}'::jsonb
     OR p_payload->>'status'<>'completed' OR p_payload->'error'<>'null'::jsonb
     OR jsonb_typeof(p_payload->'packet')<>'object'
     OR jsonb_typeof(p_payload->'packet'->'packet')<>'object'
     OR p_payload->'packet'->'packet'->>'contract_version'<>'2'
     OR NOT(p_payload->'packet' ?& ARRAY[
       'id','candidate_count','evidence_count','packet','packet_hash'
     ])
     OR ((p_payload->'packet')-ARRAY[
       'id','candidate_count','evidence_count','packet','packet_hash'
     ])<>'{}'::jsonb
     OR p_payload->'packet'->>'packet_hash' !~ '^[0-9a-f]{64}$'
     OR p_payload->'packet'->>'packet_hash'<>encode(extensions.digest(convert_to(
       public.market_canonical_jsonb(p_payload->'packet'->'packet'),'UTF8'
     ),'sha256'),'hex')
     OR (p_payload->'packet'->>'candidate_count')::int
        <>jsonb_array_length(p_payload->'packet'->'packet'->'research_candidates')
     OR (p_payload->'packet'->>'evidence_count')::int
        <>jsonb_array_length(p_payload->'packet'->'packet'->'evidence') THEN
    RAISE EXCEPTION 'invalid V2 intelligence completion payload' USING ERRCODE='22023';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-completion:'||p_completion_id::text,0
  ));
  SELECT * INTO v_saved FROM public.market_intelligence_collection_completions
  WHERE completion_id=p_completion_id;
  IF FOUND THEN
    IF v_saved.run_id<>p_run_id OR v_saved.payload IS DISTINCT FROM p_payload THEN
      RAISE EXCEPTION 'intelligence completion idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN v_saved.receipt||jsonb_build_object('duplicate',true);
  END IF;
  PERFORM 1 FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND OR NOT EXISTS(
       SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running'
     ) OR EXISTS(
       SELECT 1 FROM public.market_intelligence_run_events
       WHERE run_id=p_run_id AND status IN ('completed','failed')
     ) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM public.market_intelligence_quote_attempts
    WHERE run_id=p_run_id AND status='pending'
  ) THEN
    RAISE EXCEPTION 'quote collection outcome uncertain' USING ERRCODE='55000';
  END IF;

  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'receipts') LOOP
    IF NOT(r ? 'cache_predecessor_receipt_id') THEN
      RAISE EXCEPTION 'missing cache lineage' USING ERRCODE='22023';
    END IF;
    SELECT provider INTO v_provider FROM public.market_source_quota_reservations
    WHERE run_id=p_run_id AND id=(r->>'reservation_id')::uuid;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE='22023';
    END IF;
    IF r->>'status'='cache_hit' THEN
      IF r->>'id'=r->>'cache_predecessor_receipt_id' OR (r->>'request_cost')::int<>0 THEN
        RAISE EXCEPTION 'invalid cache lineage' USING ERRCODE='22023';
      END IF;
      SELECT payload->'receipt' INTO c FROM (
        SELECT source_receipt_id,payload FROM public.market_collection_checkpoints
        WHERE run_id=p_run_id
        UNION ALL
        SELECT source_receipt_id,payload FROM public.market_collection_checkpoint_history
        WHERE run_id=p_run_id
      ) checkpoint
      WHERE source_receipt_id=(r->>'cache_predecessor_receipt_id')::uuid;
      IF c IS NULL OR c->>'status'<>'succeeded'
         OR c->>'provider' IS DISTINCT FROM v_provider
         OR c->>'reservation_id' IS DISTINCT FROM r->>'reservation_id'
         OR c->>'cache_key' IS DISTINCT FROM r->>'cache_key'
         OR c->'requested_window' IS DISTINCT FROM r->'requested_window'
         OR c->>'response_hash' IS DISTINCT FROM r->>'response_hash'
         OR c->'retrieved_at' IS DISTINCT FROM r->'retrieved_at'
         OR c->'expires_at' IS DISTINCT FROM r->'expires_at' THEN
        RAISE EXCEPTION 'cache predecessor checkpoint unavailable' USING ERRCODE='22023';
      END IF;
    ELSIF r->'cache_predecessor_receipt_id'<>'null'::jsonb THEN
      RAISE EXCEPTION 'invalid cache lineage' USING ERRCODE='22023';
    END IF;
  END LOOP;
  SELECT COALESCE(jsonb_agg(value-'cache_predecessor_receipt_id'),'[]'::jsonb)
  INTO v_receipts FROM jsonb_array_elements(p_payload->'receipts');
  FOR c IN
    SELECT payload->'receipt' FROM public.market_collection_checkpoints
    WHERE run_id=p_run_id
    UNION ALL
    SELECT payload->'receipt' FROM public.market_collection_checkpoint_history
    WHERE run_id=p_run_id
  LOOP
    r:=(c-ARRAY[
      'provider','source_receipt_id','requested_limit','observed_at','error_code',
      'cache_predecessor_receipt_id'
    ])||jsonb_build_object(
      'id',c->'source_receipt_id','error',
      CASE WHEN c->'error_code'='null'::jsonb THEN 'null'::jsonb
           ELSE jsonb_build_object('code',c->'error_code') END
    );
    IF EXISTS(
      SELECT 1 FROM jsonb_array_elements(v_receipts) value WHERE value->>'id'=r->>'id'
    ) THEN
      IF NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(v_receipts) value WHERE value=r
      ) THEN
        RAISE EXCEPTION 'checkpoint receipt mismatch' USING ERRCODE='22023';
      END IF;
    ELSE
      v_receipts:=v_receipts||jsonb_build_array(r);
    END IF;
  END LOOP;

  v_actual_packet:=p_payload->'packet';
  v_shadow_body:=jsonb_build_object(
    'candidates','[]'::jsonb,
    'evidence',v_actual_packet->'packet'->'evidence',
    'coverage',(v_actual_packet->'packet'->'coverage')||jsonb_build_object(
      'v2_pending_packet_hash',v_actual_packet->>'packet_hash'
    ),
    'limitations','[]'::jsonb,
    'policy_version',v_actual_packet->'packet'->'policy_version'
  );
  v_shadow_packet:=jsonb_build_object(
    'id',v_actual_packet->'id',
    'candidate_count',0,
    'evidence_count',v_actual_packet->'evidence_count',
    'packet',v_shadow_body,
    'packet_hash',encode(extensions.digest(convert_to(
      public.market_canonical_jsonb(v_shadow_body),'UTF8'
    ),'sha256'),'hex')
  );
  v_effective:=jsonb_set(jsonb_set(
    p_payload,'{receipts}',v_receipts
  ),'{packet}',v_shadow_packet);
  v_result:=public.record_market_intelligence_provider_v2(
    p_run_id,p_completion_id,v_effective
  );

  INSERT INTO public.market_run_source_item_provenance(
    run_item_id,run_id,source_item_id,source_receipt_id,provider,request_url,
    retrieved_at,reporting_at,entity_ids,security_ids,discovery_status
  )
  SELECT (value->>'run_item_id')::uuid,p_run_id,(value->>'id')::uuid,
    (value->>'receipt_id')::uuid,value->>'provider',value->>'request_url',
    (value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,
    value->'entity_ids',value->'security_ids',value->>'discovery_status'
  FROM jsonb_array_elements(p_payload->'items') value;

  UPDATE public.market_evidence_packets SET
    candidate_count=(v_actual_packet->>'candidate_count')::int,
    evidence_count=(v_actual_packet->>'evidence_count')::int,
    packet=v_actual_packet->'packet',
    packet_hash=v_actual_packet->>'packet_hash'
  WHERE id=(v_actual_packet->>'id')::uuid AND run_id=p_run_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'V2 evidence packet promotion failed' USING ERRCODE='22023';
  END IF;
  v_result:=v_result||jsonb_build_object(
    'packet_id',v_actual_packet->>'id',
    'packet_hash',v_actual_packet->>'packet_hash'
  );

  INSERT INTO public.market_checkpoint_receipt_lineage(
    cache_receipt_id,run_id,cache_predecessor_receipt_id
  )
  SELECT (value->>'id')::uuid,p_run_id,
    (value->>'cache_predecessor_receipt_id')::uuid
  FROM jsonb_array_elements(p_payload->'receipts') value
  WHERE value->>'status'='cache_hit';
  INSERT INTO public.market_intelligence_collection_completions(
    completion_id,run_id,payload,receipt
  ) VALUES(p_completion_id,p_run_id,p_payload,v_result);

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

ALTER FUNCTION public.record_market_intelligence(UUID,UUID,JSONB)
  RENAME TO record_market_intelligence_v4_internal;

CREATE OR REPLACE FUNCTION public.record_market_intelligence(
  p_run_id UUID,p_completion_id UUID,p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
  IF p_payload->>'status'='completed'
     AND p_payload->'packet'->'packet'->>'contract_version'='2' THEN
    RETURN public.record_market_intelligence_v2_completion(
      p_run_id,p_completion_id,p_payload
    );
  END IF;
  RETURN public.record_market_intelligence_v4_internal(
    p_run_id,p_completion_id,p_payload
  );
END;
$$;

REVOKE ALL ON FUNCTION public.promote_market_evidence_packet_v2(),
  public.record_market_intelligence_v2_completion(UUID,UUID,JSONB),
  public.record_market_intelligence_v4_internal(UUID,UUID,JSONB)
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID,UUID,JSONB)
  FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID,UUID,JSONB)
  TO service_role;
