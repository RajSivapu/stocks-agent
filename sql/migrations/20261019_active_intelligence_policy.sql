BEGIN;

-- Promote the reviewed decision policy without mutating historical policy rows.
-- Fresh installations have no active row yet, so policy publication remains an
-- explicit owner action there.
DO $policy$
DECLARE
  v_active_version INT;
  v_active_config JSONB;
  v_existing_v4 JSONB;
  v_expected_config JSONB;
  v_intelligence CONSTANT JSONB := $intelligence${"adaptive_enrichment_budget":{"intraday":4,"on-demand":4,"post-market":8,"pre-market":12},"alpha_vantage_daily_ceiling":20,"alpha_vantage_phase_budget":{"intraday":4,"on-demand":2,"post-market":4,"pre-market":8},"automatic_policy_changes_enabled":false,"execution_allowed":false,"packet":{"max_candidates":12,"max_evidence_per_candidate":8,"max_item_characters":2000,"max_serialized_bytes":98304},"paid_fallback_enabled":false,"provider_phase_budgets":{"bea":{"intraday":1,"on-demand":2,"post-market":2,"pre-market":4},"bls":{"intraday":1,"on-demand":2,"post-market":2,"pre-market":4},"dod":{"intraday":1,"on-demand":2,"post-market":2,"pre-market":4},"doe":{"intraday":1,"on-demand":2,"post-market":2,"pre-market":4},"eia":{"intraday":1,"on-demand":2,"post-market":2,"pre-market":4},"federal_register":{"intraday":1,"on-demand":2,"post-market":2,"pre-market":4},"finnhub":{"intraday":3,"on-demand":2,"post-market":4,"pre-market":6},"fred":{"intraday":1,"on-demand":2,"post-market":2,"pre-market":4},"gdelt":{"intraday":20,"on-demand":20,"post-market":40,"pre-market":80},"sec_edgar":{"intraday":3,"on-demand":3,"post-market":5,"pre-market":7},"white_house":{"intraday":1,"on-demand":2,"post-market":2,"pre-market":4},"yahoo":{"intraday":6,"on-demand":4,"post-market":8,"pre-market":12}},"provider_query_identifiers":{"cik_by_symbol":{"CENX":"0000949157","NVDA":"0001045810"},"series_by_provider":{"fred":"CPIAUCSL"},"version":1},"providers":["gdelt","alpha_vantage","finnhub","yahoo","sec_edgar","federal_register","white_house","doe","dod","eia","fred","bls","bea"],"required_baseline_capability_ids":["sec_company_tickers_universe","gdelt_theme_search"],"runtime_model_api_enabled":false,"seed_domains":["macro_and_policy","technology_ai_and_semiconductors","energy_nuclear_and_grid_infrastructure","industrial_infrastructure","critical_minerals_and_magnets","healthcare","consumer","defense_trade_and_geopolitics","earnings_and_mergers_and_acquisitions"],"source_capability_version":1,"suggestion_only":true,"theme_taxonomy_version":1}$intelligence$::jsonb;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('market-policy-v4', 0));
  SELECT version,config INTO v_active_version,v_active_config
  FROM public.market_policy_config WHERE active FOR UPDATE;
  IF NOT FOUND THEN
    RETURN;
  END IF;
  IF v_active_version=4 THEN
    IF v_active_config->>'version' IS DISTINCT FROM '4'
       OR v_active_config->'intelligence' IS DISTINCT FROM v_intelligence THEN
      RAISE EXCEPTION 'active policy v4 differs from reviewed intelligence policy'
        USING ERRCODE='22023';
    END IF;
    RETURN;
  END IF;
  IF v_active_version<>3 OR v_active_config->>'version' IS DISTINCT FROM '3' THEN
    RAISE EXCEPTION 'active policy is not the reviewed v3 predecessor'
      USING ERRCODE='22023';
  END IF;

  v_expected_config := jsonb_set(v_active_config,'{version}','4'::jsonb,true)
    || jsonb_build_object('intelligence',v_intelligence);
  SELECT config INTO v_existing_v4
  FROM public.market_policy_config WHERE version=4 FOR UPDATE;
  IF FOUND THEN
    IF v_existing_v4 IS DISTINCT FROM v_expected_config THEN
      RAISE EXCEPTION 'existing policy v4 differs from reviewed candidate'
        USING ERRCODE='22023';
    END IF;
  ELSE
    INSERT INTO public.market_policy_config(version,config,active)
    VALUES(4,v_expected_config,false);
  END IF;

  UPDATE public.market_policy_config
  SET active=false,activated_at=NULL WHERE active;
  UPDATE public.market_policy_config
  SET active=true,activated_at=statement_timestamp() WHERE version=4;
END;
$policy$;

-- SQL NULL must fail closed. PostgreSQL three-valued logic made the previous
-- <> guard skip a missing intelligence block and later mislabel it as quota.
CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,
  p_phase TEXT,
  p_market_date DATE,
  p_policy_version INT,
  p_reservation_plan JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_existing public.market_intelligence_runs%ROWTYPE;
  v_policy JSONB;
  v_reservation JSONB;
  v_provider TEXT;
  v_requested INT;
  v_phase_budget INT;
  v_daily_ceiling INT;
  v_existing_total INT;
  v_existing_phase INT;
  v_reservation_ids JSONB;
  v_cache_entries JSONB;
  v_was_existing BOOLEAN := false;
BEGIN
  IF p_run_id IS NULL OR p_market_date IS NULL OR p_policy_version <= 0
     OR p_phase NOT IN ('pre-market','intraday','post-market','on-demand')
     OR jsonb_typeof(p_reservation_plan)<>'object'
     OR NOT (p_reservation_plan ?& ARRAY['reservations'])
     OR (p_reservation_plan - ARRAY['reservations']) <> '{}'::jsonb
     OR jsonb_typeof(p_reservation_plan->'reservations')<>'array'
     OR jsonb_array_length(p_reservation_plan->'reservations') > 13
     OR octet_length(p_reservation_plan::text) > 32768 THEN
    RAISE EXCEPTION 'invalid intelligence reservation plan' USING ERRCODE = '22023';
  END IF;
  SELECT config INTO v_policy FROM public.market_policy_config
  WHERE version=p_policy_version;
  IF NOT FOUND OR jsonb_typeof(v_policy->'intelligence') IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'intelligence policy unavailable' USING ERRCODE = '22023';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-run:' || p_run_id::text, 0
  ));
  SELECT * INTO v_existing FROM public.market_intelligence_runs WHERE id=p_run_id;
  v_was_existing := FOUND;
  IF v_was_existing THEN
    IF v_existing.phase IS DISTINCT FROM p_phase
       OR v_existing.market_date IS DISTINCT FROM p_market_date
       OR v_existing.policy_version IS DISTINCT FROM p_policy_version
       OR v_existing.reservation_plan IS DISTINCT FROM p_reservation_plan THEN
      RAISE EXCEPTION 'intelligence run idempotency mismatch' USING ERRCODE = '22023';
    END IF;
  ELSE
    IF (SELECT count(*) FROM jsonb_array_elements(p_reservation_plan->'reservations')) <>
       (SELECT count(DISTINCT value->>'provider')
          FROM jsonb_array_elements(p_reservation_plan->'reservations')) THEN
      RAISE EXCEPTION 'duplicate provider reservation' USING ERRCODE = '22023';
    END IF;
    FOR v_reservation IN
      SELECT value FROM jsonb_array_elements(p_reservation_plan->'reservations')
      ORDER BY value->>'provider'
    LOOP
      IF jsonb_typeof(v_reservation)<>'object'
         OR NOT (v_reservation ?& ARRAY['id','provider','requests','cache_keys'])
         OR (v_reservation - ARRAY['id','provider','requests','cache_keys']) <> '{}'::jsonb
         OR v_reservation->>'provider' NOT IN (
           'gdelt','alpha_vantage','finnhub','yahoo','sec_edgar','federal_register',
           'white_house','doe','dod','eia','fred','bls','bea','social'
         )
         OR jsonb_typeof(v_reservation->'requests')<>'number'
         OR (v_reservation->>'requests') !~ '^[0-9]+$'
         OR (v_reservation->>'requests')::int NOT BETWEEN 1 AND 100
         OR jsonb_typeof(v_reservation->'cache_keys')<>'array'
         OR jsonb_array_length(v_reservation->'cache_keys') > 20
         OR EXISTS (
           SELECT 1 FROM jsonb_array_elements(v_reservation->'cache_keys') key
            WHERE jsonb_typeof(key)<>'string' OR char_length(key #>> '{}') NOT BETWEEN 1 AND 512
         ) THEN
        RAISE EXCEPTION 'invalid provider reservation' USING ERRCODE = '22023';
      END IF;
      BEGIN
        PERFORM (v_reservation->>'id')::uuid;
      EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'invalid provider reservation id' USING ERRCODE = '22023';
      END;
      v_provider := v_reservation->>'provider';
      v_requested := (v_reservation->>'requests')::int;
      IF v_provider='alpha_vantage' THEN
        v_phase_budget := COALESCE((v_policy #>> ARRAY[
          'intelligence','alpha_vantage_phase_budget',p_phase
        ])::int, 0);
        v_daily_ceiling := LEAST(COALESCE((v_policy #>> ARRAY[
          'intelligence','alpha_vantage_daily_ceiling'
        ])::int, 0), 20);
      ELSE
        v_phase_budget := COALESCE((v_policy #>> ARRAY[
          'intelligence','provider_phase_budgets',v_provider,p_phase
        ])::int, 0);
        v_daily_ceiling := 1000000;
      END IF;
      IF v_requested > v_phase_budget THEN
        RAISE EXCEPTION 'provider phase quota exceeded' USING ERRCODE = '54000';
      END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended(
        'market-intelligence-quota:' || v_provider || ':' || p_market_date::text, 0
      ));
      SELECT COALESCE(sum(reserved_requests),0) INTO v_existing_total
      FROM public.market_source_quota_reservations
      WHERE provider=v_provider AND market_date=p_market_date;
      SELECT COALESCE(sum(reserved_requests),0) INTO v_existing_phase
      FROM public.market_source_quota_reservations
      WHERE provider=v_provider AND market_date=p_market_date AND phase=p_phase;
      IF v_provider='alpha_vantage' AND v_existing_phase + v_requested > v_phase_budget THEN
        RAISE EXCEPTION 'alpha vantage phase quota exceeded' USING ERRCODE = '54000';
      END IF;
      IF v_provider='alpha_vantage' AND v_existing_total + v_requested > v_daily_ceiling THEN
        RAISE EXCEPTION 'alpha vantage daily quota exceeded' USING ERRCODE = '54000';
      END IF;
    END LOOP;

    INSERT INTO public.market_intelligence_runs(
      id,phase,market_date,policy_version,reservation_plan
    ) VALUES (p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan);
    FOR v_reservation IN SELECT value FROM jsonb_array_elements(p_reservation_plan->'reservations')
    LOOP
      INSERT INTO public.market_source_quota_reservations(
        id,run_id,provider,market_date,phase,reserved_requests,cache_keys
      ) VALUES (
        (v_reservation->>'id')::uuid,p_run_id,v_reservation->>'provider',p_market_date,p_phase,
        (v_reservation->>'requests')::int,v_reservation->'cache_keys'
      );
    END LOOP;
    INSERT INTO public.market_intelligence_run_events(id,run_id,status,detail)
    VALUES (gen_random_uuid(),p_run_id,'started',jsonb_build_object(
      'policy_version',p_policy_version,'phase',p_phase,'market_date',p_market_date
    ));
  END IF;

  SELECT COALESCE(jsonb_agg(id::text ORDER BY provider),'[]'::jsonb)
  INTO v_reservation_ids FROM public.market_source_quota_reservations WHERE run_id=p_run_id;
  SELECT COALESCE(jsonb_agg(cache_entry ORDER BY retrieved_at DESC),'[]'::jsonb)
  INTO v_cache_entries
  FROM (
    SELECT receipt.retrieved_at, jsonb_build_object(
      'receipt_id',receipt.id,
      'item_id',item.id,
      'provider',receipt.provider,
      'cache_key',receipt.cache_key,
      'retrieved_at',receipt.retrieved_at,
      'expires_at',receipt.expires_at,
      'content_hash',item.content_hash,
      'title',item.title,
      'normalized_text',item.normalized_text,
      'canonical_url',item.canonical_url,
      'published_at',item.published_at,
      'effective_at',item.effective_at,
      'metadata',item.metadata
    ) AS cache_entry
    FROM public.market_source_receipts receipt
    JOIN public.market_source_items item ON item.source_receipt_id=receipt.id
    WHERE receipt.status IN ('succeeded','cache_hit')
      AND receipt.expires_at > statement_timestamp()
      AND EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_reservation_plan->'reservations') requested
        WHERE requested->>'provider'=receipt.provider
          AND requested->'cache_keys' ? receipt.cache_key
      )
    ORDER BY receipt.retrieved_at DESC, item.id
    LIMIT 50
  ) bounded_cache;
  RETURN jsonb_build_object(
    'run_id',p_run_id,
    'reservation_ids',v_reservation_ids,
    'cache_entries',v_cache_entries,
    'duplicate',v_was_existing
  );
END;
$$;

REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB)
  TO service_role;

COMMIT;
