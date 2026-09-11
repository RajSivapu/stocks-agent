-- Finalize the normal SEC universe with set-based validation and inserts.
-- The prior per-security PL/pgSQL loop exceeded the Edge Function request
-- deadline after all bounded chunks had already been accepted.

CREATE OR REPLACE FUNCTION public.finalize_market_discovery_reference(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_manifest_id UUID;
  v_begin public.market_reference_chunk_receipts%ROWTYPE;
  v_seal public.market_reference_finalization_seals%ROWTYPE;
  v_chunk_count INT; v_entry_count INT; v_min_chunk INT; v_max_chunk INT;
  v_root TEXT; v_manifest JSONB; v_result JSONB; v_format INT;
BEGIN
  v_manifest_id:=(p_payload->>'manifest_id')::uuid;
  SELECT * INTO v_begin FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1;
  v_format:=COALESCE((v_begin.payload->'manifest'->'manifest'->>'format_version')::int,1);
  IF v_format=1 THEN
    v_result:=public.finalize_market_discovery_reference_v1_internal(
      p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
    );
    PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
    RETURN v_result;
  END IF;
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'finalize_discovery_reference',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF v_format<>2 OR v_begin.run_id<>p_run_id OR jsonb_typeof(p_payload)<>'object'
     OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest_id','root_hash'])
     OR (p_payload-ARRAY['manifest_id','root_hash'])<>'{}'::jsonb
     OR p_payload->>'root_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid reference finalize payload' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_seal FROM public.market_reference_finalization_seals
  WHERE manifest_id=v_manifest_id;
  IF FOUND THEN
    IF v_seal.run_id<>p_run_id OR v_seal.root_hash<>p_payload->>'root_hash' THEN
      RAISE EXCEPTION 'reference finalization idempotency mismatch' USING ERRCODE='22023';
    END IF;
    v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_seal.security_count,'duplicate',true);
    PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
    RETURN v_result;
  END IF;
  SELECT count(*),COALESCE(sum(entry_count),0),min(chunk_index),max(chunk_index),
         encode(extensions.digest(convert_to(COALESCE(string_agg(chunk_hash,'' ORDER BY chunk_index),''),'UTF8'),'sha256'),'hex')
  INTO v_chunk_count,v_entry_count,v_min_chunk,v_max_chunk,v_root
  FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index>=0;
  IF v_chunk_count<>v_begin.chunk_count OR v_min_chunk<>0 OR v_max_chunk<>v_begin.chunk_count-1 THEN
    RAISE EXCEPTION 'reference chunk sequence mismatch' USING ERRCODE='22023';
  END IF;
  IF v_entry_count<>(v_begin.payload->>'security_count')::int THEN
    RAISE EXCEPTION 'reference snapshot count mismatch' USING ERRCODE='22023';
  END IF;
  IF v_root<>v_begin.chunk_hash OR v_root<>p_payload->>'root_hash' THEN
    RAISE EXCEPTION 'reference snapshot root mismatch' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    GROUP BY e->>'security_id' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    GROUP BY (e->>'id')::uuid HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate reference security identity' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
    GROUP BY e->>'entity_id' HAVING count(DISTINCT e->'issuer_names')>1
  ) THEN
    RAISE EXCEPTION 'same entity must have identical issuer names' USING ERRCODE='22023';
  END IF;
  IF v_begin.predecessor_manifest_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.market_reference_finalization_seals s
    WHERE s.manifest_id=v_begin.predecessor_manifest_id AND s.capability_id=v_begin.capability_id
  ) THEN
    RAISE EXCEPTION 'reference predecessor membership mismatch' USING ERRCODE='22023';
  END IF;
  v_manifest:=v_begin.payload->'manifest';
  IF encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
       public.market_reference_manifest_semantic_v1(v_manifest)
     ),'UTF8'),'sha256'),'hex')<>v_manifest->>'content_hash' THEN
    RAISE EXCEPTION 'reference manifest hash mismatch' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_reference_manifests(
    id,run_id,reference_version,revision,capability_version,taxonomy_version,source_hash,
    valid_from,valid_to,manifest,content_hash
  ) VALUES(
    v_manifest_id,p_run_id,v_manifest->>'reference_version',(v_manifest->>'revision')::int,
    (v_manifest->>'capability_version')::int,(v_manifest->>'taxonomy_version')::int,
    v_manifest->>'source_hash',(v_manifest->>'valid_from')::timestamptz,
    (v_manifest->>'valid_to')::timestamptz,v_manifest->'manifest',v_manifest->>'content_hash'
  );
  IF EXISTS(
    SELECT 1 FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries') e
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
      AND encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
            public.market_reference_security_semantic_v2(e.value)
          ),'UTF8'),'sha256'),'hex')<>e.value->>'content_hash'
  ) THEN
    RAISE EXCEPTION 'reference security hash mismatch' USING ERRCODE='22023';
  END IF;
  WITH entries AS MATERIALIZED (
    SELECT e.value,
      (row_number() OVER (ORDER BY c.chunk_index,e.item_ordinal)-1)::int AS ordinal
    FROM public.market_reference_chunk_receipts c
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'entries')
      WITH ORDINALITY e(value,item_ordinal)
    WHERE c.manifest_id=v_manifest_id AND c.chunk_index>=0
  ), resolved AS MATERIALIZED (
    SELECT entries.value,entries.ordinal,s.id AS prior_revision_id
    FROM entries
    LEFT JOIN public.market_reference_snapshot_memberships m
      ON v_begin.predecessor_manifest_id IS NOT NULL
      AND m.manifest_id=v_begin.predecessor_manifest_id
      AND m.security_id=entries.value->>'security_id'
    LEFT JOIN public.market_security_reference_revisions s
      ON s.id=m.security_revision_id
      AND s.content_hash=entries.value->>'content_hash'
      AND s.semantic_encoding_version=2
      AND s.issuer_names=entries.value->'issuer_names'
  ), inserted_revisions AS (
    INSERT INTO public.market_security_reference_revisions(
      id,manifest_id,run_id,revision,security_id,entity_id,ticker,exchange,instrument_type,
      eligible,exclusion_reasons,aliases,source_ids,valid_from,valid_to,content_hash,
      semantic_encoding_version,issuer_names
    )
    SELECT
      (value->>'id')::uuid,v_manifest_id,p_run_id,(value->>'revision')::int,
      value->>'security_id',value->>'entity_id',value->>'ticker',value->>'exchange',
      value->>'instrument_type',(value->>'eligible')::boolean,value->'exclusion_reasons',
      value->'aliases',value->'source_ids',(value->>'valid_from')::timestamptz,
      (value->>'valid_to')::timestamptz,value->>'content_hash',2,value->'issuer_names'
    FROM resolved WHERE prior_revision_id IS NULL
    RETURNING id
  )
  INSERT INTO public.market_reference_snapshot_memberships(
    manifest_id,security_revision_id,security_id,ordinal
  )
  SELECT v_manifest_id,COALESCE(resolved.prior_revision_id,(resolved.value->>'id')::uuid),
    resolved.value->>'security_id',resolved.ordinal
  FROM resolved
  CROSS JOIN (SELECT count(*) FROM inserted_revisions) inserted;
  INSERT INTO public.market_reference_finalization_seals(
    manifest_id,run_id,capability_id,predecessor_manifest_id,chunk_count,security_count,root_hash
  ) VALUES(
    v_manifest_id,p_run_id,v_begin.capability_id,v_begin.predecessor_manifest_id,
    v_begin.chunk_count,v_entry_count,v_root
  );
  v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'security_count',v_entry_count,'duplicate',false);
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range OR unique_violation OR foreign_key_violation THEN
  RAISE EXCEPTION 'reference finalization conflict' USING ERRCODE='22023';
END;
$$;
