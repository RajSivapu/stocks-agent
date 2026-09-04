-- Immutable, receipt-backed market-intelligence and report ledgers.
-- Additive and idempotent. This migration is local-only until the V1-C6 gate.

CREATE TABLE IF NOT EXISTS public.market_intelligence_runs (
  id UUID PRIMARY KEY,
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  market_date DATE NOT NULL,
  policy_version INT NOT NULL REFERENCES public.market_policy_config(version) ON DELETE RESTRICT,
  reservation_plan JSONB NOT NULL CHECK (
    jsonb_typeof(reservation_plan)='object'
    AND octet_length(reservation_plan::text) <= 32768
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_market_intelligence_runs_date_phase
  ON public.market_intelligence_runs(market_date, phase, created_at DESC);

CREATE TABLE IF NOT EXISTS public.market_intelligence_run_events (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  status TEXT NOT NULL CHECK (status IN ('started','completed','failed')),
  detail JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (
    jsonb_typeof(detail)='object' AND octet_length(detail::text) <= 131072
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, status)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_market_intelligence_terminal_event
  ON public.market_intelligence_run_events(run_id) WHERE status IN ('completed','failed');

CREATE TABLE IF NOT EXISTS public.market_source_quota_reservations (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL CHECK (provider IN (
    'gdelt','alpha_vantage','finnhub','yahoo','sec_edgar','federal_register',
    'white_house','doe','dod','eia','fred','bls','bea','social'
  )),
  market_date DATE NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  reserved_requests INT NOT NULL CHECK (reserved_requests BETWEEN 1 AND 100),
  cache_keys JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(cache_keys)='array' AND jsonb_array_length(cache_keys) <= 20
    AND octet_length(cache_keys::text) <= 12288
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_market_source_quota_provider_date
  ON public.market_source_quota_reservations(provider, market_date);

CREATE TABLE IF NOT EXISTS public.market_source_receipts (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  reservation_id UUID NOT NULL REFERENCES public.market_source_quota_reservations(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL CHECK (provider IN (
    'gdelt','alpha_vantage','finnhub','yahoo','sec_edgar','federal_register',
    'white_house','doe','dod','eia','fred','bls','bea','social'
  )),
  status TEXT NOT NULL CHECK (status IN (
    'succeeded','failed','cache_hit','quota_blocked','configuration_missing'
  )),
  cache_key TEXT NOT NULL CHECK (char_length(cache_key) BETWEEN 1 AND 512),
  requested_window JSONB NOT NULL CHECK (
    jsonb_typeof(requested_window)='object' AND octet_length(requested_window::text) <= 8192
  ),
  retrieved_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ,
  request_cost INT NOT NULL CHECK (request_cost BETWEEN 0 AND 100),
  upstream_remaining INT CHECK (upstream_remaining IS NULL OR upstream_remaining >= 0),
  returned_count INT NOT NULL CHECK (returned_count BETWEEN 0 AND 10000),
  accepted_count INT NOT NULL CHECK (accepted_count BETWEEN 0 AND 10000),
  duplicate_count INT NOT NULL CHECK (duplicate_count BETWEEN 0 AND 10000),
  dropped_count INT NOT NULL CHECK (dropped_count BETWEEN 0 AND 10000),
  error JSONB CHECK (error IS NULL OR (
    jsonb_typeof(error)='object' AND octet_length(error::text) <= 4096
  )),
  response_hash TEXT CHECK (response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, id),
  CHECK (accepted_count + duplicate_count + dropped_count <= returned_count),
  CHECK (
    (status IN ('succeeded','cache_hit') AND response_hash IS NOT NULL
      AND expires_at IS NOT NULL AND expires_at > retrieved_at AND error IS NULL)
    OR
    (status NOT IN ('succeeded','cache_hit') AND expires_at IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_market_source_receipts_cache
  ON public.market_source_receipts(provider, cache_key, expires_at DESC)
  WHERE status IN ('succeeded','cache_hit');

CREATE TABLE IF NOT EXISTS public.market_source_items (
  id UUID PRIMARY KEY,
  source_receipt_id UUID NOT NULL REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL CHECK (provider IN (
    'gdelt','alpha_vantage','finnhub','yahoo','sec_edgar','federal_register',
    'white_house','doe','dod','eia','fred','bls','bea','social'
  )),
  upstream_item_id TEXT CHECK (upstream_item_id IS NULL OR char_length(upstream_item_id) <= 512),
  canonical_url TEXT CHECK (
    canonical_url IS NULL OR (char_length(canonical_url) <= 2048 AND canonical_url ~ '^https://')
  ),
  published_at TIMESTAMPTZ,
  effective_at TIMESTAMPTZ,
  title TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 500),
  normalized_text TEXT NOT NULL CHECK (char_length(normalized_text) <= 2000),
  canonical_content TEXT NOT NULL CHECK (char_length(canonical_content) <= 4096),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (
    jsonb_typeof(metadata)='object' AND octet_length(metadata::text) <= 8192
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (source_receipt_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_market_source_items_hash
  ON public.market_source_items(provider, content_hash);

CREATE TABLE IF NOT EXISTS public.market_intelligence_run_items (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  source_item_id UUID NOT NULL REFERENCES public.market_source_items(id) ON DELETE RESTRICT,
  source_receipt_id UUID NOT NULL REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT,
  disposition TEXT NOT NULL CHECK (disposition IN (
    'accepted','duplicate','near_duplicate','dropped'
  )),
  drop_reason TEXT CHECK (drop_reason IS NULL OR char_length(drop_reason) <= 200),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, source_item_id, source_receipt_id),
  CHECK (
    (disposition='accepted' AND drop_reason IS NULL)
    OR (disposition<>'accepted' AND drop_reason IS NOT NULL)
  )
);

CREATE TABLE IF NOT EXISTS public.market_events (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL CHECK (char_length(event_type) BETWEEN 1 AND 80),
  title TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 500),
  summary TEXT NOT NULL CHECK (char_length(summary) <= 4000),
  occurred_at TIMESTAMPTZ,
  effective_at TIMESTAMPTZ,
  materiality NUMERIC(8,7) NOT NULL CHECK (materiality BETWEEN 0 AND 1),
  confidence NUMERIC(8,7) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  evidence_item_ids JSONB NOT NULL CHECK (
    jsonb_typeof(evidence_item_ids)='array'
    AND jsonb_array_length(evidence_item_ids) BETWEEN 1 AND 96
    AND octet_length(evidence_item_ids::text) <= 40960
  ),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_event_relationships (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  event_id UUID NOT NULL REFERENCES public.market_events(id) ON DELETE RESTRICT,
  source_kind TEXT NOT NULL CHECK (source_kind IN ('event','theme','value_chain','entity','security')),
  source_key TEXT NOT NULL CHECK (char_length(source_key) BETWEEN 1 AND 256),
  target_kind TEXT NOT NULL CHECK (target_kind IN ('theme','value_chain','entity','security','etf')),
  target_key TEXT NOT NULL CHECK (char_length(target_key) BETWEEN 1 AND 256),
  relationship_type TEXT NOT NULL CHECK (char_length(relationship_type) BETWEEN 1 AND 80),
  hypothesis BOOLEAN NOT NULL DEFAULT true,
  evidence_item_ids JSONB NOT NULL CHECK (
    jsonb_typeof(evidence_item_ids)='array'
    AND jsonb_array_length(evidence_item_ids) BETWEEN 1 AND 8
    AND octet_length(evidence_item_ids::text) <= 4096
  ),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_candidate_rankings (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  event_id UUID REFERENCES public.market_events(id) ON DELETE RESTRICT,
  candidate_key TEXT NOT NULL CHECK (char_length(candidate_key) BETWEEN 1 AND 256),
  ticker TEXT CHECK (ticker IS NULL OR ticker ~ '^[A-Z][A-Z0-9.-]{0,14}$'),
  rank INT NOT NULL CHECK (rank BETWEEN 1 AND 100),
  component_scores JSONB NOT NULL CHECK (
    jsonb_typeof(component_scores)='object' AND octet_length(component_scores::text) <= 8192
  ),
  total_score NUMERIC(12,6) NOT NULL CHECK (total_score BETWEEN -100000 AND 100000),
  qualified BOOLEAN NOT NULL,
  veto_reasons JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(veto_reasons)='array' AND jsonb_array_length(veto_reasons) <= 20
    AND octet_length(veto_reasons::text) <= 4096
  ),
  exposure_item_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(exposure_item_ids)='array' AND jsonb_array_length(exposure_item_ids) <= 8
    AND octet_length(exposure_item_ids::text) <= 4096
  ),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, candidate_key),
  UNIQUE (run_id, rank),
  CHECK (NOT qualified OR jsonb_array_length(exposure_item_ids) >= 1)
);

CREATE TABLE IF NOT EXISTS public.market_evidence_packets (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL UNIQUE REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  policy_version INT NOT NULL REFERENCES public.market_policy_config(version) ON DELETE RESTRICT,
  status TEXT NOT NULL CHECK (status='completed'),
  candidate_count INT NOT NULL CHECK (candidate_count BETWEEN 0 AND 12),
  evidence_count INT NOT NULL CHECK (evidence_count BETWEEN 0 AND 96),
  packet JSONB NOT NULL CHECK (
    jsonb_typeof(packet)='object' AND octet_length(packet::text) <= 98304
  ),
  packet_hash TEXT NOT NULL CHECK (packet_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE IF NOT EXISTS public.market_reports (
  id UUID PRIMARY KEY,
  idempotency_key UUID NOT NULL UNIQUE,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  market_date DATE NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('morning','urgent','weekly','monthly','theme','on-demand')),
  report JSONB NOT NULL CHECK (
    jsonb_typeof(report)='object' AND octet_length(report::text) <= 131072
  ),
  report_hash TEXT NOT NULL CHECK (report_hash ~ '^[0-9a-f]{64}$'),
  rendered_text TEXT NOT NULL CHECK (char_length(rendered_text) <= 14000),
  rendered_hash TEXT NOT NULL CHECK (rendered_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_market_reports_date_kind
  ON public.market_reports(market_date DESC, kind, created_at DESC);

CREATE TABLE IF NOT EXISTS public.market_learning_observations (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  policy_version INT NOT NULL REFERENCES public.market_policy_config(version) ON DELETE RESTRICT,
  observation_type TEXT NOT NULL CHECK (observation_type IN (
    'outcome','missed-event','source-failure','noise'
  )),
  horizon_days INT NOT NULL CHECK (horizon_days BETWEEN 0 AND 3650),
  sample_size INT NOT NULL CHECK (sample_size BETWEEN 1 AND 1000000),
  benchmark TEXT CHECK (benchmark IS NULL OR char_length(benchmark) <= 100),
  observation JSONB NOT NULL CHECK (
    jsonb_typeof(observation)='object' AND octet_length(observation::text) <= 32768
  ),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, content_hash)
);

CREATE OR REPLACE FUNCTION public.reject_market_intelligence_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'market intelligence ledgers are append-only' USING ERRCODE = '55000';
END;
$$;

-- Canonical JSON is compact UTF-8 JSON: object keys are sorted lexicographically,
-- arrays retain order, and scalar values use PostgreSQL jsonb serialization.
CREATE OR REPLACE FUNCTION public.market_canonical_jsonb(p_value JSONB)
RETURNS TEXT LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog AS $$
DECLARE
  v_result TEXT;
BEGIN
  CASE jsonb_typeof(p_value)
    WHEN 'object' THEN
      SELECT '{' || COALESCE(string_agg(
        to_jsonb(entry.key)::text || ':' || public.market_canonical_jsonb(entry.value),
        ',' ORDER BY entry.key COLLATE "C"
      ), '') || '}' INTO v_result
      FROM jsonb_each(p_value) AS entry;
    WHEN 'array' THEN
      SELECT '[' || COALESCE(string_agg(
        public.market_canonical_jsonb(entry.value), ',' ORDER BY entry.ordinality
      ), '') || ']' INTO v_result
      FROM jsonb_array_elements(p_value) WITH ORDINALITY AS entry(value, ordinality);
    ELSE
      v_result := p_value::text;
  END CASE;
  RETURN v_result;
END;
$$;

DROP TRIGGER IF EXISTS market_intelligence_runs_append_only ON public.market_intelligence_runs;
CREATE TRIGGER market_intelligence_runs_append_only BEFORE UPDATE OR DELETE
ON public.market_intelligence_runs FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_intelligence_run_events_append_only ON public.market_intelligence_run_events;
CREATE TRIGGER market_intelligence_run_events_append_only BEFORE UPDATE OR DELETE
ON public.market_intelligence_run_events FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_source_quota_reservations_append_only ON public.market_source_quota_reservations;
CREATE TRIGGER market_source_quota_reservations_append_only BEFORE UPDATE OR DELETE
ON public.market_source_quota_reservations FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_source_receipts_append_only ON public.market_source_receipts;
CREATE TRIGGER market_source_receipts_append_only BEFORE UPDATE OR DELETE
ON public.market_source_receipts FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_source_items_append_only ON public.market_source_items;
CREATE TRIGGER market_source_items_append_only BEFORE UPDATE OR DELETE
ON public.market_source_items FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_intelligence_run_items_append_only ON public.market_intelligence_run_items;
CREATE TRIGGER market_intelligence_run_items_append_only BEFORE UPDATE OR DELETE
ON public.market_intelligence_run_items FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_events_append_only ON public.market_events;
CREATE TRIGGER market_events_append_only BEFORE UPDATE OR DELETE
ON public.market_events FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_event_relationships_append_only ON public.market_event_relationships;
CREATE TRIGGER market_event_relationships_append_only BEFORE UPDATE OR DELETE
ON public.market_event_relationships FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_candidate_rankings_append_only ON public.market_candidate_rankings;
CREATE TRIGGER market_candidate_rankings_append_only BEFORE UPDATE OR DELETE
ON public.market_candidate_rankings FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_evidence_packets_append_only ON public.market_evidence_packets;
CREATE TRIGGER market_evidence_packets_append_only BEFORE UPDATE OR DELETE
ON public.market_evidence_packets FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_reports_append_only ON public.market_reports;
CREATE TRIGGER market_reports_append_only BEFORE UPDATE OR DELETE
ON public.market_reports FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();
DROP TRIGGER IF EXISTS market_learning_observations_append_only ON public.market_learning_observations;
CREATE TRIGGER market_learning_observations_append_only BEFORE UPDATE OR DELETE
ON public.market_learning_observations FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();

ALTER TABLE public.market_intelligence_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_intelligence_run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_source_quota_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_source_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_source_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_intelligence_run_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_event_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_candidate_rankings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_evidence_packets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_learning_observations ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE
  public.market_intelligence_runs,
  public.market_intelligence_run_events,
  public.market_source_quota_reservations,
  public.market_source_receipts,
  public.market_source_items,
  public.market_intelligence_run_items,
  public.market_events,
  public.market_event_relationships,
  public.market_candidate_rankings,
  public.market_evidence_packets,
  public.market_reports,
  public.market_learning_observations
FROM PUBLIC, anon, authenticated, service_role;

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
  IF NOT FOUND OR jsonb_typeof(v_policy->'intelligence')<>'object' THEN
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

CREATE OR REPLACE FUNCTION public.record_market_intelligence(
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

CREATE OR REPLACE FUNCTION public.read_market_evidence_packet(
  p_packet_id UUID,
  p_run_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_result JSONB;
BEGIN
  IF p_packet_id IS NULL OR p_run_id IS NULL THEN
    RAISE EXCEPTION 'packet and run identifiers are required' USING ERRCODE = '22023';
  END IF;

  SELECT jsonb_build_object(
    'id',packet.id,
    'run_id',packet.run_id,
    'packet_hash',packet.packet_hash,
    'packet',packet.packet,
    'exposure_facts',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'candidate_key',fact.candidate_key,
        'evidence_id',fact.evidence_id,
        'exposure_kind',fact.exposure_kind,
        'status',fact.freshness,
        'observed_at',fact.observed_at,
        'retrieved_at',fact.retrieved_at
      ) ORDER BY fact.candidate_key,fact.evidence_id)
      FROM (
        SELECT DISTINCT ranking.candidate_key,
          item.id AS evidence_id,
          item.metadata->>'exposure_kind' AS exposure_kind,
          CASE WHEN receipt.expires_at>statement_timestamp()
                    AND COALESCE(item.effective_at,item.published_at) IS NOT NULL
            THEN 'fresh' ELSE 'stale' END AS freshness,
          COALESCE(item.effective_at,item.published_at) AS observed_at,
          receipt.retrieved_at
        FROM public.market_candidate_rankings ranking
        CROSS JOIN LATERAL jsonb_array_elements_text(ranking.exposure_item_ids)
          AS exposure_id(value)
        JOIN public.market_intelligence_run_items run_item
          ON run_item.run_id=ranking.run_id
          AND run_item.source_item_id=exposure_id.value::uuid
          AND run_item.disposition='accepted'
        JOIN public.market_source_items item
          ON item.id=run_item.source_item_id
          AND item.source_receipt_id=run_item.source_receipt_id
        JOIN public.market_source_receipts receipt
          ON receipt.id=run_item.source_receipt_id
          AND receipt.run_id=ranking.run_id
          AND receipt.status IN ('succeeded','cache_hit')
        WHERE ranking.run_id=packet.run_id AND ranking.qualified
          AND item.metadata->>'authority'='official'
          AND item.metadata->>'exposure_kind' IN (
            'filing','contract','backlog','revenue','capacity','official_fund'
          )
          AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(packet.packet->'candidates') candidate
            WHERE candidate->>'candidate_key'=ranking.candidate_key
              AND candidate->'evidence_ids' ? item.id::text
          )
      ) fact
    ),'[]'::jsonb)
  ) INTO v_result
  FROM public.market_evidence_packets packet
  JOIN public.market_intelligence_run_events event
    ON event.run_id=packet.run_id AND event.status='completed'
  WHERE packet.id=p_packet_id AND packet.run_id=p_run_id AND packet.status='completed';

  IF v_result IS NOT NULL AND octet_length(v_result::text)>131072 THEN
    RAISE EXCEPTION 'packet read exceeds bound' USING ERRCODE = '22023';
  END IF;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_report(
  p_run_id UUID,
  p_idempotency_key UUID,
  p_report JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_existing public.market_reports%ROWTYPE;
  v_packet public.market_evidence_packets%ROWTYPE;
BEGIN
  IF p_run_id IS NULL OR p_idempotency_key IS NULL OR jsonb_typeof(p_report)<>'object'
     OR octet_length(p_report::text)>196608
     OR NOT (p_report ?& ARRAY[
       'id','packet_id','market_date','kind','report','report_hash','rendered_text','rendered_hash'
     ])
     OR (p_report - ARRAY[
       'id','packet_id','market_date','kind','report','report_hash','rendered_text','rendered_hash'
     ]) <> '{}'::jsonb
     OR p_report->>'kind' NOT IN ('morning','urgent','weekly','monthly','theme','on-demand')
     OR jsonb_typeof(p_report->'report')<>'object'
     OR octet_length((p_report->'report')::text)>131072
     OR p_report->>'report_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'report_hash' <> encode(extensions.digest(convert_to(
       public.market_canonical_jsonb(p_report->'report'),'UTF8'
     ),'sha256'),'hex')
     OR jsonb_typeof(p_report->'rendered_text')<>'string'
     OR char_length(p_report->>'rendered_text')>14000
     OR p_report->>'rendered_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'rendered_hash' <> encode(extensions.digest(
       convert_to(p_report->>'rendered_text','UTF8'),'sha256'
     ),'hex') THEN
    RAISE EXCEPTION 'invalid market report' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-report:' || p_idempotency_key::text, 0
  ));
  SELECT * INTO v_existing FROM public.market_reports WHERE idempotency_key=p_idempotency_key;
  IF FOUND THEN
    IF v_existing.run_id IS DISTINCT FROM p_run_id
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
  SELECT packet.* INTO v_packet
  FROM public.market_evidence_packets packet
  JOIN public.market_intelligence_run_events event
    ON event.run_id=packet.run_id AND event.status='completed'
  WHERE packet.id=(p_report->>'packet_id')::uuid
    AND packet.run_id=p_run_id AND packet.status='completed';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'completed evidence packet unavailable' USING ERRCODE = '22023';
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

CREATE OR REPLACE FUNCTION public.record_market_learning(
  p_run_id UUID,
  p_observation JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_run public.market_intelligence_runs%ROWTYPE;
  v_existing public.market_learning_observations%ROWTYPE;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_observation)<>'object'
     OR octet_length(p_observation::text)>49152
     OR NOT (p_observation ?& ARRAY[
       'id','policy_version','observation_type','horizon_days','sample_size','benchmark',
       'observation','content_hash'
     ])
     OR (p_observation - ARRAY[
       'id','policy_version','observation_type','horizon_days','sample_size','benchmark',
       'observation','content_hash'
     ]) <> '{}'::jsonb
     OR p_observation->>'observation_type' NOT IN (
       'outcome','missed-event','source-failure','noise'
     )
     OR (p_observation->>'policy_version')::int <= 0
     OR (p_observation->>'horizon_days')::int NOT BETWEEN 0 AND 3650
     OR (p_observation->>'sample_size')::int NOT BETWEEN 1 AND 1000000
     OR (p_observation->'benchmark'<>'null'::jsonb
       AND char_length(p_observation->>'benchmark')>100)
     OR jsonb_typeof(p_observation->'observation')<>'object'
     OR octet_length((p_observation->'observation')::text)>32768
     OR p_observation->>'content_hash' !~ '^[0-9a-f]{64}$'
     OR p_observation->>'content_hash' <> encode(extensions.digest(convert_to(
       public.market_canonical_jsonb(p_observation->'observation'),'UTF8'
     ),'sha256'),'hex') THEN
    RAISE EXCEPTION 'invalid market learning observation' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'market-intelligence-learning:' || (p_observation->>'id')::uuid::text, 0
  ));
  SELECT * INTO v_run FROM public.market_intelligence_runs WHERE id=p_run_id;
  IF NOT FOUND OR v_run.policy_version IS DISTINCT FROM (p_observation->>'policy_version')::int THEN
    RAISE EXCEPTION 'learning run or policy version mismatch' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_learning_observations
  WHERE id=(p_observation->>'id')::uuid;
  IF FOUND THEN
    IF v_existing.run_id IS DISTINCT FROM p_run_id
       OR v_existing.content_hash IS DISTINCT FROM p_observation->>'content_hash' THEN
      RAISE EXCEPTION 'market learning idempotency mismatch' USING ERRCODE = '22023';
    END IF;
    RETURN jsonb_build_object(
      'observation_id',v_existing.id,'content_hash',v_existing.content_hash,'duplicate',true
    );
  END IF;
  INSERT INTO public.market_learning_observations(
    id,run_id,policy_version,observation_type,horizon_days,sample_size,benchmark,
    observation,content_hash
  ) VALUES (
    (p_observation->>'id')::uuid,p_run_id,(p_observation->>'policy_version')::int,
    p_observation->>'observation_type',(p_observation->>'horizon_days')::int,
    (p_observation->>'sample_size')::int,p_observation->>'benchmark',
    p_observation->'observation',p_observation->>'content_hash'
  ) RETURNING * INTO v_existing;
  RETURN jsonb_build_object(
    'observation_id',v_existing.id,'content_hash',v_existing.content_hash,'duplicate',false
  );
END;
$$;

REVOKE ALL ON FUNCTION public.reject_market_intelligence_mutation()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.market_canonical_jsonb(JSONB)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID, TEXT, DATE, INT, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.read_market_evidence_packet(UUID, UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_market_report(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_market_learning(UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID, TEXT, DATE, INT, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_market_evidence_packet(UUID, UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_report(UUID, UUID, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_learning(UUID, JSONB) TO service_role;
