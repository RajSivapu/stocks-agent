-- Increase the v2 reference chunk capacity within the existing 192 KiB
-- payload boundary. The former 88-row ceiling forced the normal SEC universe
-- into 119 serial gateway calls and exceeded the worker transfer deadline.

CREATE OR REPLACE FUNCTION public.record_market_discovery_reference_chunk(
  p_run_id UUID,p_payload JSONB,p_request_id UUID,p_encoded_bytes INT,p_request_hash TEXT
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_manifest_id UUID;
  v_begin public.market_reference_chunk_receipts%ROWTYPE;
  v_existing public.market_reference_chunk_receipts%ROWTYPE;
  v_entry JSONB;
  v_count INT;
  v_hash TEXT;
  v_format INT;
  v_result JSONB;
BEGIN
  v_manifest_id:=(p_payload->>'manifest_id')::uuid;
  SELECT * INTO v_begin FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=-1;
  v_format:=COALESCE((v_begin.payload->'manifest'->'manifest'->>'format_version')::int,1);
  IF v_format=1 THEN
    v_result:=public.record_market_discovery_reference_chunk_v1_internal(
      p_run_id,p_payload,p_request_id,p_encoded_bytes,p_request_hash
    );
    PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
    RETURN v_result;
  END IF;
  PERFORM public.claim_market_reference_transfer_request(
    p_run_id,'record_discovery_reference_chunk',p_payload,p_request_id,p_encoded_bytes,p_request_hash
  );
  IF v_format<>2 OR v_begin.run_id<>p_run_id
     OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>196608
     OR NOT(p_payload ?& ARRAY['manifest_id','chunk_index','chunk_count','entries','chunk_hash'])
     OR (p_payload-ARRAY['manifest_id','chunk_index','chunk_count','entries','chunk_hash'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'entries')<>'array'
     OR jsonb_array_length(p_payload->'entries') NOT BETWEEN 1 AND 200
     OR (p_payload->>'chunk_index')::int NOT BETWEEN 0 AND 511
     OR (p_payload->>'chunk_count')::int NOT BETWEEN 1 AND 512
     OR p_payload->>'chunk_hash' !~ '^[0-9a-f]{64}$'
     OR v_begin.chunk_count<>(p_payload->>'chunk_count')::int
     OR (p_payload->>'chunk_index')::int>=v_begin.chunk_count THEN
    RAISE EXCEPTION 'invalid reference chunk payload' USING ERRCODE='22023';
  END IF;
  FOR v_entry IN SELECT value FROM jsonb_array_elements(p_payload->'entries') LOOP
    IF jsonb_typeof(v_entry)<>'object'
       OR NOT(v_entry ?& ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash','semantic_encoding_version','issuer_names'])
       OR (v_entry-ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash','semantic_encoding_version','issuer_names'])<>'{}'::jsonb
       OR (v_entry->>'semantic_encoding_version')::int<>2
       OR v_entry->>'manifest_id'<>v_manifest_id::text
       OR v_entry->>'security_id' !~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'
       OR v_entry->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,14}$'
       OR v_entry->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR public.market_reference_issuer_names_canonical_v2(v_entry->'issuer_names')<>v_entry->'issuer_names'
       OR encode(extensions.digest(convert_to(public.market_reference_canonical_json_v1(
            public.market_reference_security_semantic_v2(v_entry)
          ),'UTF8'),'sha256'),'hex')<>v_entry->>'content_hash' THEN
      RAISE EXCEPTION 'reference security hash mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;
  IF EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'entries') e
    GROUP BY e->>'security_id' HAVING count(*)>1
  ) OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_payload->'entries') e
    GROUP BY (e->>'id')::uuid HAVING count(*)>1
  ) THEN
    RAISE EXCEPTION 'duplicate reference security identity' USING ERRCODE='22023';
  END IF;
  SELECT count(*),encode(extensions.digest(convert_to(COALESCE(string_agg(
    (e.value->>'security_id')||chr(31)||(e.value->>'id')||chr(31)||(e.value->>'content_hash'),
    E'\n' ORDER BY e.ordinality),''),'UTF8'),'sha256'),'hex')
  INTO v_count,v_hash FROM jsonb_array_elements(p_payload->'entries') WITH ORDINALITY e(value,ordinality);
  IF v_hash<>p_payload->>'chunk_hash' THEN
    RAISE EXCEPTION 'reference chunk hash mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_reference_chunk_receipts
  WHERE manifest_id=v_manifest_id AND chunk_index=(p_payload->>'chunk_index')::int;
  IF FOUND THEN
    IF v_existing.run_id<>p_run_id OR v_existing.chunk_count<>(p_payload->>'chunk_count')::int
       OR v_existing.entry_count<>v_count OR v_existing.chunk_hash<>v_hash
       OR v_existing.payload<>p_payload THEN
      RAISE EXCEPTION 'reference chunk idempotency mismatch' USING ERRCODE='22023';
    END IF;
    v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'chunk_index',v_existing.chunk_index,'duplicate',true);
  ELSE
    IF EXISTS(SELECT 1 FROM public.market_reference_finalization_seals WHERE manifest_id=v_manifest_id) THEN
      RAISE EXCEPTION 'reference snapshot is already finalized' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.market_reference_chunk_receipts(
      manifest_id,run_id,capability_id,chunk_index,chunk_count,entry_count,chunk_hash,
      predecessor_manifest_id,payload
    ) VALUES(
      v_manifest_id,p_run_id,v_begin.capability_id,(p_payload->>'chunk_index')::int,
      v_begin.chunk_count,v_count,v_hash,v_begin.predecessor_manifest_id,p_payload
    );
    v_result:=jsonb_build_object('manifest_id',v_manifest_id::text,'chunk_index',(p_payload->>'chunk_index')::int,'duplicate',false);
  END IF;
  PERFORM public.finish_market_reference_transfer_request(p_request_id,v_result);
  RETURN v_result;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid reference chunk payload' USING ERRCODE='22023';
END;
$$;


REVOKE ALL ON FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT)
  FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.record_market_discovery_reference_chunk(UUID,JSONB,UUID,INT,TEXT)
  TO service_role;
