-- One-time reconciliation of the independently verified 20260909-compatible
-- production shape. Baseline inventory receipt:
-- f1f08d635d59bb2e429c57deb1a2a9948d1ce631faa746bc8a19ea51372afb46
-- The protected runner verifies the exact catalog, encrypts its snapshot, locks
-- the baseline relations, and executes this SQL and its truthful ledger receipt
-- in one transaction. This file never writes an existing application row and
-- never invents historical migration receipts. The guard rejects retries before DDL.
DO $baseline$
DECLARE actual text[];
BEGIN
  SELECT array_agg(c.relname::text ORDER BY c.relname::text) INTO actual
  FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind IN ('r','p');
  IF actual IS DISTINCT FROM ARRAY['analysis_runs','daily_snapshots','decision_evaluations','dry_powder','holdings','lessons','market_alert_actions','market_alert_drafts','market_alert_events','market_alert_rule_versions','market_alert_rules','market_candidate_rankings','market_event_relationships','market_events','market_evidence_packets','market_gateway_requests','market_intelligence_run_events','market_intelligence_run_items','market_intelligence_runs','market_learning_observations','market_policy_config','market_publications','market_reports','market_source_items','market_source_quota_reservations','market_source_receipts','owner_investment_plans','paper_watches','portfolio_commands','radar','stock_observations','suggestion_grades','suggestions','telegram_updates','transactions']::text[]
     OR pg_catalog.to_regclass('supabase_migrations.schema_migrations') IS NOT NULL
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname IN ('stock_agent_release_reader','stock_agent_release_reader_runtime'))
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND NOT a.attisdropped AND
       ((c.relname='analysis_runs' AND a.attname IN ('scheduled_market_date','scheduled_phase'))
        OR (c.relname='market_intelligence_runs' AND a.attname='request_window')
        OR (c.relname='market_source_receipts' AND a.attname='cache_predecessor_receipt_id'))) THEN
    RAISE EXCEPTION 'production reconciliation baseline is not the reviewed legacy shape' USING ERRCODE='55000';
  END IF;
END;
$baseline$;

-- Empty native history is truthful: production had no native migration ledger.
CREATE SCHEMA IF NOT EXISTS supabase_migrations;
CREATE TABLE supabase_migrations.schema_migrations(version text PRIMARY KEY,statements text[]);
REVOKE ALL ON supabase_migrations.schema_migrations FROM PUBLIC,anon,authenticated,service_role;

-- Final relation shapes and metadata changes.

-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
CREATE TABLE IF NOT EXISTS public.market_report_publications (
  report_id UUID PRIMARY KEY REFERENCES public.market_reports(id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL UNIQUE CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status IN ('pending','delivered','failed','uncertain','suppressed')),
  telegram_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(telegram_message_ids) = 'array' AND
    (status = 'delivered' OR jsonb_array_length(telegram_message_ids) = 0)
  ),
  telegram_accepted_at TIMESTAMPTZ,
  attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  lease_token UUID,
  lease_expires_at TIMESTAMPTZ,
  error TEXT CHECK (error IS NULL OR char_length(error) <= 1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((status = 'delivered') = (telegram_accepted_at IS NOT NULL)),
  CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL))
);

-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
ALTER TABLE public.market_report_publications ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260911_command_acknowledgements.sql
CREATE TABLE IF NOT EXISTS public.portfolio_command_acknowledgements (
  command_id UUID PRIMARY KEY REFERENCES public.portfolio_commands(id) ON DELETE RESTRICT,
  telegram_update_id BIGINT NOT NULL UNIQUE CHECK (telegram_update_id >= 0),
  status TEXT NOT NULL CHECK (status IN ('pending','delivered','failed','uncertain')),
  result JSONB NOT NULL CHECK (jsonb_typeof(result)='object'),
  error TEXT CHECK (error IS NULL OR char_length(error)<=1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Final-state source: sql/migrations/20260911_command_acknowledgements.sql
ALTER TABLE public.portfolio_command_acknowledgements ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/202609120002_collection_cache_lineage.sql
ALTER TABLE public.market_source_receipts
  ADD COLUMN IF NOT EXISTS cache_predecessor_receipt_id UUID
  REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT;

-- Final-state source: sql/migrations/202609120002_collection_cache_lineage.sql
ALTER TABLE public.market_source_receipts
  DROP CONSTRAINT IF EXISTS market_source_receipts_cache_lineage_check;

-- Final-state source: sql/migrations/202609120002_collection_cache_lineage.sql
ALTER TABLE public.market_source_receipts
  ADD CONSTRAINT market_source_receipts_cache_lineage_check CHECK (
    (status = 'cache_hit' AND request_cost = 0 AND cache_predecessor_receipt_id IS NOT NULL)
    OR (status <> 'cache_hit' AND cache_predecessor_receipt_id IS NULL)
  );

-- Final-state source: sql/migrations/20260913_command_acknowledgement_lease.sql
ALTER TABLE public.portfolio_command_acknowledgements
  ADD COLUMN IF NOT EXISTS lease_token UUID,
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0);

-- Final-state source: sql/migrations/20260913_command_acknowledgement_lease.sql
ALTER TABLE public.portfolio_command_acknowledgements
  DROP CONSTRAINT IF EXISTS portfolio_command_acknowledgements_lease_pair_check;

-- Final-state source: sql/migrations/20260913_command_acknowledgement_lease.sql
ALTER TABLE public.portfolio_command_acknowledgements
  ADD CONSTRAINT portfolio_command_acknowledgements_lease_pair_check
  CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL));

-- Final-state source: sql/migrations/20260914_provider_evidence_integrity.sql
CREATE TABLE IF NOT EXISTS public.market_source_item_provenance (
  source_item_id UUID PRIMARY KEY REFERENCES public.market_source_items(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL,
  canonical_item_url TEXT NOT NULL CHECK (char_length(canonical_item_url) <= 2048 AND canonical_item_url ~ '^https://'),
  request_url TEXT NOT NULL CHECK (char_length(request_url) <= 2048 AND request_url ~ '^https://'),
  retrieved_at TIMESTAMPTZ NOT NULL,
  reporting_at TIMESTAMPTZ,
  entity_ids JSONB NOT NULL CHECK (jsonb_typeof(entity_ids)='array' AND jsonb_array_length(entity_ids)<=32 AND octet_length(entity_ids::text)<=4096),
  security_ids JSONB NOT NULL CHECK (jsonb_typeof(security_ids)='array' AND jsonb_array_length(security_ids)<=32 AND octet_length(security_ids::text)<=1024),
  discovery_status TEXT NOT NULL CHECK (discovery_status IN ('qualified','no_event','insufficient_coverage')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

-- Final-state source: sql/migrations/20260914_provider_evidence_integrity.sql
ALTER TABLE public.market_source_item_provenance ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260916_run_scoped_request_provenance.sql
CREATE TABLE IF NOT EXISTS public.market_run_source_item_provenance (
  run_item_id UUID PRIMARY KEY REFERENCES public.market_intelligence_run_items(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  source_item_id UUID NOT NULL REFERENCES public.market_source_items(id) ON DELETE RESTRICT,
  source_receipt_id UUID NOT NULL REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL,
  request_url TEXT NOT NULL CHECK (char_length(request_url)<=2048 AND request_url ~ '^https://' AND lower(request_url) !~ '(api[_-]?key|token|secret|password)='),
  retrieved_at TIMESTAMPTZ NOT NULL, reporting_at TIMESTAMPTZ,
  entity_ids JSONB NOT NULL CHECK (jsonb_typeof(entity_ids)='array' AND jsonb_array_length(entity_ids)<=32 AND octet_length(entity_ids::text)<=4096),
  security_ids JSONB NOT NULL CHECK (jsonb_typeof(security_ids)='array' AND jsonb_array_length(security_ids)<=32 AND octet_length(security_ids::text)<=1024),
  discovery_status TEXT NOT NULL CHECK (discovery_status IN ('qualified','no_event','insufficient_coverage')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE(run_id, source_item_id, source_receipt_id)
);

-- Final-state source: sql/migrations/20260916_run_scoped_request_provenance.sql
ALTER TABLE public.market_run_source_item_provenance ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260917_collection_checkpoint_hydration.sql
CREATE TABLE IF NOT EXISTS public.market_collection_checkpoints (
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  cache_key TEXT NOT NULL CHECK (char_length(cache_key) BETWEEN 1 AND 512),
  request_window JSONB NOT NULL CHECK (jsonb_typeof(request_window) = 'object'),
  source_receipt_id UUID NOT NULL REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 65536),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (run_id, cache_key)
);

-- Final-state source: sql/migrations/20260917_collection_checkpoint_hydration.sql
ALTER TABLE public.market_collection_checkpoints ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260918_durable_collection_controller.sql
ALTER TABLE public.market_intelligence_runs
  ADD COLUMN IF NOT EXISTS request_window JSONB;

-- Final-state source: sql/migrations/20260918_durable_collection_controller.sql
ALTER TABLE public.market_intelligence_runs
  DROP CONSTRAINT IF EXISTS market_intelligence_runs_request_window_check;

-- Final-state source: sql/migrations/20260918_durable_collection_controller.sql
ALTER TABLE public.market_intelligence_runs
  ADD CONSTRAINT market_intelligence_runs_request_window_check CHECK (
    request_window IS NULL OR (
      jsonb_typeof(request_window)='object'
      AND request_window ?& ARRAY['start','end','timezone','market_date','phase']
      AND (request_window - ARRAY['start','end','timezone','market_date','phase'])='{}'::jsonb
      AND request_window->>'timezone'='America/Chicago'
      AND request_window->>'phase'=phase
      AND request_window->>'market_date'=market_date::text
      AND (request_window->>'start')::timestamptz < (request_window->>'end')::timestamptz
    )
  );

-- Final-state source: sql/migrations/20260918_durable_collection_controller.sql
ALTER TABLE public.market_collection_checkpoints
  DROP CONSTRAINT IF EXISTS market_collection_checkpoints_source_receipt_id_fkey;

-- Final-state source: sql/migrations/20260918_durable_collection_controller.sql
ALTER TABLE public.market_collection_checkpoints
  DROP CONSTRAINT IF EXISTS market_collection_checkpoints_payload_check;

-- Final-state source: sql/migrations/20260918_durable_collection_controller.sql
ALTER TABLE public.market_collection_checkpoints
  ADD CONSTRAINT market_collection_checkpoints_payload_check CHECK (
    jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536
    AND payload ?& ARRAY['receipt','items']
    AND (payload - ARRAY['receipt','items'])='{}'::jsonb
    AND jsonb_typeof(payload->'receipt')='object'
    AND jsonb_typeof(payload->'items')='array' AND jsonb_array_length(payload->'items')<=50
  );

-- Final-state source: sql/migrations/20260919_controller_lineage_and_run_binding.sql
CREATE TABLE IF NOT EXISTS public.market_collection_checkpoint_history (
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  cache_key TEXT NOT NULL, source_receipt_id UUID NOT NULL,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536),
  replaced_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY(run_id,cache_key,source_receipt_id)
);

-- Final-state source: sql/migrations/20260919_controller_lineage_and_run_binding.sql
ALTER TABLE public.market_collection_checkpoint_history ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260920_terminal_checkpoint_lineage.sql
CREATE TABLE IF NOT EXISTS public.market_checkpoint_receipt_lineage (
  cache_receipt_id UUID PRIMARY KEY REFERENCES public.market_source_receipts(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  cache_predecessor_receipt_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

-- Final-state source: sql/migrations/20260920_terminal_checkpoint_lineage.sql
ALTER TABLE public.market_checkpoint_receipt_lineage ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260920_terminal_checkpoint_lineage.sql
ALTER TABLE public.market_source_receipts
  DROP CONSTRAINT IF EXISTS market_source_receipts_cache_lineage_check;

-- Final-state source: sql/migrations/20260920_terminal_checkpoint_lineage.sql
ALTER TABLE public.market_source_receipts
  ADD CONSTRAINT market_source_receipts_cache_lineage_check CHECK (
    (status = 'cache_hit' AND request_cost = 0)
    OR (status <> 'cache_hit' AND cache_predecessor_receipt_id IS NULL)
  );

-- Final-state source: sql/migrations/20260921_intelligence_context_inputs.sql
CREATE TABLE IF NOT EXISTS public.market_intelligence_context_inputs (
  run_id UUID PRIMARY KEY REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  holding_market_values JSONB NOT NULL CHECK (jsonb_typeof(holding_market_values)='object' AND octet_length(holding_market_values::text)<=16384),
  liquidity_by_ticker JSONB NOT NULL CHECK (jsonb_typeof(liquidity_by_ticker)='object' AND octet_length(liquidity_by_ticker::text)<=16384),
  overlap_by_ticker JSONB NOT NULL CHECK (jsonb_typeof(overlap_by_ticker)='object' AND octet_length(overlap_by_ticker::text)<=16384),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

-- Final-state source: sql/migrations/20260921_intelligence_context_inputs.sql
ALTER TABLE public.market_intelligence_context_inputs ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint
      WHERE conrelid='public.market_report_publications'::regclass AND conname='market_report_publications_report_id_fkey') THEN
    ALTER TABLE public.market_report_publications ADD CONSTRAINT market_report_publications_report_id_fkey
      FOREIGN KEY (report_id) REFERENCES public.market_reports(id) ON DELETE RESTRICT;
  END IF;
END; $$;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
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

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
CREATE TABLE IF NOT EXISTS public.market_intelligence_collection_completions (
  completion_id UUID PRIMARY KEY REFERENCES public.market_intelligence_run_events(id) ON DELETE RESTRICT,
  run_id UUID UNIQUE NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=1048576),
  receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
ALTER TABLE public.market_intelligence_collection_completions ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
ALTER TABLE public.market_checkpoint_receipt_lineage ADD CONSTRAINT distinct_cache_predecessor CHECK(cache_receipt_id<>cache_predecessor_receipt_id);

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
ALTER TABLE public.market_intelligence_context_inputs ADD COLUMN IF NOT EXISTS current_quotes JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
ALTER TABLE public.market_intelligence_context_inputs ADD COLUMN IF NOT EXISTS quote_receipt_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
CREATE TABLE IF NOT EXISTS public.market_intelligence_quote_attempts (
  source_receipt_id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id),
  reservation_id UUID NOT NULL REFERENCES public.market_source_quota_reservations(id), ticker TEXT NOT NULL,
  cache_key TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed')),
  quote JSONB, checkpoint JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
ALTER TABLE public.market_intelligence_quote_attempts ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
ALTER TABLE public.analysis_runs
  ADD COLUMN IF NOT EXISTS scheduled_market_date DATE,
  ADD COLUMN IF NOT EXISTS scheduled_phase TEXT;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                 WHERE conrelid='public.analysis_runs'::regclass
                   AND conname='analysis_runs_scheduled_phase_valid') THEN
    ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_scheduled_phase_valid
      CHECK (scheduled_phase IS NULL OR scheduled_phase IN ('pre-market','intraday','post-market'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                 WHERE conrelid='public.analysis_runs'::regclass
                   AND conname='analysis_runs_scheduled_slot_complete') THEN
    ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_scheduled_slot_complete
      CHECK ((scheduled_market_date IS NULL) = (scheduled_phase IS NULL));
  END IF;
END;
$$;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_runs_scheduled_slot
  ON public.analysis_runs(scheduled_market_date, scheduled_phase)
  WHERE scheduled_market_date IS NOT NULL AND scheduled_phase IS NOT NULL;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
CREATE TABLE IF NOT EXISTS public.market_scheduled_phase_deadlines (
  phase TEXT PRIMARY KEY CHECK (phase IN ('pre-market','intraday','post-market')),
  deadline_local TIME NOT NULL,
  grace_minutes INT NOT NULL DEFAULT 15 CHECK (grace_minutes BETWEEN 0 AND 60),
  effective_on DATE NOT NULL DEFAULT (timezone('America/Chicago', statement_timestamp())::date)
);

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
ALTER TABLE public.market_scheduled_phase_deadlines
  ADD COLUMN IF NOT EXISTS grace_minutes INT NOT NULL DEFAULT 15 CHECK (grace_minutes BETWEEN 0 AND 60);

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
ALTER TABLE public.market_scheduled_phase_deadlines ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
DROP FUNCTION IF EXISTS public.start_market_analysis_run(UUID, UUID, TEXT);

-- Final-state source: sql/migrations/20260924_scheduled_lifecycle_followup.sql
ALTER TABLE public.market_scheduled_phase_deadlines
  DROP CONSTRAINT IF EXISTS market_scheduled_phase_deadlines_pkey;

-- Final-state source: sql/migrations/20260924_scheduled_lifecycle_followup.sql
ALTER TABLE public.market_scheduled_phase_deadlines
  ADD CONSTRAINT market_scheduled_phase_deadlines_pkey PRIMARY KEY (phase, effective_on);

-- Final-state source: sql/migrations/20260925_scheduled_report_origins.sql
CREATE TABLE IF NOT EXISTS public.market_report_request_origins (
  request_id UUID PRIMARY KEY REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  scheduled_phase TEXT NOT NULL CHECK (scheduled_phase IN ('pre-market','intraday','post-market')),
  market_date DATE NOT NULL,
  requested_kind TEXT NOT NULL CHECK (requested_kind IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')),
  requested_report_id UUID NOT NULL,
  requested_packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  requested_idempotency_key TEXT NOT NULL CHECK (requested_idempotency_key ~ '^[0-9a-f]{64}$'),
  requested_report_hash TEXT NOT NULL CHECK (requested_report_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

-- Final-state source: sql/migrations/20260925_scheduled_report_origins.sql
CREATE INDEX IF NOT EXISTS idx_market_report_request_origins_run
  ON public.market_report_request_origins(run_id, scheduled_phase, market_date);

-- Final-state source: sql/migrations/20260925_scheduled_report_origins.sql
ALTER TABLE public.market_report_request_origins ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260926_report_suppression_reasons.sql
ALTER TABLE public.market_report_publications ADD COLUMN IF NOT EXISTS suppression_reason TEXT;

-- Final-state source: sql/migrations/20260926_report_suppression_reasons.sql
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid='public.market_report_publications'::regclass
      AND conname='report_suppression_reason_required'
  ) THEN
    ALTER TABLE public.market_report_publications ADD CONSTRAINT report_suppression_reason_required
      CHECK ((status='suppressed' AND suppression_reason IS NOT NULL AND suppression_reason IN ('no_trigger','not_actionable','REPORT_POLICY_MISMATCH'))
             OR (status<>'suppressed' AND suppression_reason IS NULL)) NOT VALID;
  END IF;
END $$;

-- Final-state source: sql/migrations/20260927_release_evidence_reader.sql
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='stock_agent_release_reader') THEN
    CREATE ROLE stock_agent_release_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='stock_agent_release_reader_runtime') THEN
    CREATE ROLE stock_agent_release_reader_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
END; $$;

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
ALTER TABLE public.suggestion_grades ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
ALTER TABLE public.lessons ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
ALTER TABLE public.daily_snapshots ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE TABLE IF NOT EXISTS public.portfolio_cash_ledger_state (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE TABLE IF NOT EXISTS public.reconciled_cash_snapshots (
  id UUID PRIMARY KEY,
  as_of TIMESTAMPTZ NOT NULL,
  fresh_through TIMESTAMPTZ NOT NULL,
  ledger_watermark BIGINT NOT NULL CHECK (ledger_watermark >= 0),
  core_available NUMERIC NOT NULL CHECK (core_available >= 0),
  growth_available NUMERIC NOT NULL CHECK (growth_available >= 0),
  speculative_available NUMERIC NOT NULL CHECK (speculative_available >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CHECK (fresh_through > as_of AND fresh_through <= as_of + interval '30 minutes')
);

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE INDEX IF NOT EXISTS idx_reconciled_cash_snapshots_current
  ON public.reconciled_cash_snapshots(as_of DESC,created_at DESC);

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE TABLE IF NOT EXISTS public.market_run_terminal_outcomes (
  run_id UUID PRIMARY KEY REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  evaluation_request_id UUID NOT NULL UNIQUE
    REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  outcome TEXT NOT NULL CHECK (outcome IN ('no_trigger','not_actionable')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
ALTER TABLE public.portfolio_cash_ledger_state ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
ALTER TABLE public.reconciled_cash_snapshots ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
ALTER TABLE public.market_run_terminal_outcomes ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
CREATE TABLE IF NOT EXISTS public.market_policy_comparisons (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  evaluation_id UUID NOT NULL REFERENCES public.decision_evaluations(id) ON DELETE RESTRICT,
  comparison JSONB NOT NULL CHECK (jsonb_typeof(comparison)='object' AND octet_length(comparison::text)<=16384),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
ALTER TABLE public.market_policy_comparisons ENABLE ROW LEVEL SECURITY;

-- Final-state source: sql/migrations/20261002_provider_registry_and_release_ledger_recovery.sql
CREATE TABLE IF NOT EXISTS public.stock_agent_release_migration_ledger (
  path TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
  UNIQUE (version, path)
);

-- Final-state source: sql/migrations/20261002_provider_registry_and_release_ledger_recovery.sql
ALTER TABLE public.stock_agent_release_migration_ledger ENABLE ROW LEVEL SECURITY;

-- Latest reviewed routine definitions; historical rename/text-rewrite shims omitted.
-- Chronology was not attested by relation presence. Install the reviewed base
-- explicitly, never rename an unverified live function body into a trusted helper.
-- Final-state source: sql/migrations/20260903_owner_investment_plans.sql
CREATE OR REPLACE FUNCTION public.apply_portfolio_command_without_chronology(
  p_command_id UUID,
  p_chat_id BIGINT,
  p_user_id BIGINT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  v_command public.portfolio_commands%ROWTYPE;
  v_holding public.holdings%ROWTYPE;
  v_plan public.owner_investment_plans%ROWTYPE;
  v_has_holding BOOLEAN;
  v_has_plan BOOLEAN;
  v_current_shares NUMERIC;
  v_new_shares NUMERIC;
  v_new_avg NUMERIC;
  v_realized NUMERIC;
  v_transaction_id BIGINT;
  v_executed_on DATE;
  v_next_month DATE;
  v_last_day DATE;
  v_next_due DATE;
  v_result JSONB;
BEGIN
  SELECT * INTO v_command
  FROM public.portfolio_commands
  WHERE id = p_command_id
  FOR UPDATE;

  IF NOT FOUND OR v_command.chat_id IS DISTINCT FROM p_chat_id
      OR v_command.user_id IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'command unavailable' USING ERRCODE = '42501';
  END IF;

  IF v_command.status = 'applied' THEN
    RETURN COALESCE(v_command.result, jsonb_build_object('ok', true, 'status', 'applied'))
      || jsonb_build_object('duplicate', true);
  ELSIF v_command.status <> 'pending' THEN
    RETURN jsonb_build_object('ok', false, 'status', v_command.status);
  END IF;

  IF v_command.expires_at <= now() THEN
    v_result := jsonb_build_object('ok', false, 'status', 'expired');
    UPDATE public.portfolio_commands
    SET status = 'expired', updated_at = now(), error = 'confirmation expired', result = v_result
    WHERE id = v_command.id;
    RETURN v_result;
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(v_command.ticker, 0));

  IF v_command.operation IN ('plan', 'cancel_plan') THEN
    SELECT * INTO v_plan
    FROM public.owner_investment_plans
    WHERE ticker = v_command.ticker
    FOR UPDATE;
    v_has_plan := FOUND;

    IF (v_has_plan AND v_command.expected_plan_updated_at IS NULL)
        OR (NOT v_has_plan AND v_command.expected_plan_updated_at IS NOT NULL)
        OR (v_has_plan AND v_plan.updated_at IS DISTINCT FROM v_command.expected_plan_updated_at) THEN
      v_result := jsonb_build_object(
        'ok', false, 'status', 'rejected', 'reason', 'plan changed; submit the command again'
      );
      UPDATE public.portfolio_commands
      SET status = 'rejected', updated_at = now(), error = 'plan changed', result = v_result
      WHERE id = v_command.id;
      RETURN v_result;
    END IF;

    IF v_command.operation = 'plan' THEN
      INSERT INTO public.owner_investment_plans (
        ticker, bucket, amount, cadence, next_due_on, due_day, active
      ) VALUES (
        v_command.ticker, 'core', v_command.amount, 'monthly', v_command.next_due_on,
        EXTRACT(day FROM v_command.next_due_on)::smallint, true
      )
      ON CONFLICT (ticker) DO UPDATE SET
        bucket = EXCLUDED.bucket,
        amount = EXCLUDED.amount,
        cadence = EXCLUDED.cadence,
        next_due_on = EXCLUDED.next_due_on,
        due_day = EXCLUDED.due_day,
        active = true,
        updated_at = now()
      RETURNING * INTO v_plan;
      v_result := jsonb_build_object(
        'ok', true, 'status', 'applied', 'operation', 'plan', 'ticker', v_plan.ticker,
        'amount', v_plan.amount, 'cadence', v_plan.cadence,
        'next_due_on', v_plan.next_due_on, 'bucket', v_plan.bucket
      );
    ELSE
      IF NOT v_has_plan OR NOT v_plan.active THEN
        v_result := jsonb_build_object(
          'ok', false, 'status', 'rejected', 'reason', 'active plan not found'
        );
        UPDATE public.portfolio_commands
        SET status = 'rejected', updated_at = now(), error = 'active plan not found', result = v_result
        WHERE id = v_command.id;
        RETURN v_result;
      END IF;
      UPDATE public.owner_investment_plans
      SET active = false, updated_at = now()
      WHERE id = v_plan.id;
      v_result := jsonb_build_object(
        'ok', true, 'status', 'applied', 'operation', 'cancel_plan', 'ticker', v_plan.ticker
      );
    END IF;

    UPDATE public.portfolio_commands
    SET status = 'applied', applied_at = now(), updated_at = now(), result = v_result, error = NULL
    WHERE id = v_command.id;
    RETURN v_result;
  END IF;

  SELECT * INTO v_holding
  FROM public.holdings
  WHERE ticker = v_command.ticker
  FOR UPDATE;
  v_has_holding := FOUND;
  v_current_shares := CASE WHEN v_has_holding THEN v_holding.shares ELSE 0 END;

  IF v_current_shares IS DISTINCT FROM v_command.expected_shares THEN
    v_result := jsonb_build_object(
      'ok', false, 'status', 'rejected', 'reason', 'holding changed; submit the command again'
    );
    UPDATE public.portfolio_commands
    SET status = 'rejected', updated_at = now(), error = 'holding changed', result = v_result
    WHERE id = v_command.id;
    RETURN v_result;
  END IF;

  IF v_command.operation IN ('buy', 'sell') THEN
    v_executed_on := COALESCE(
      v_command.executed_on,
      (CURRENT_TIMESTAMP AT TIME ZONE 'America/Chicago')::date
    );
    IF v_executed_on < DATE '2000-01-01'
        OR v_executed_on > (CURRENT_TIMESTAMP AT TIME ZONE 'America/Chicago')::date THEN
      v_result := jsonb_build_object(
        'ok', false, 'status', 'rejected', 'reason', 'invalid or future execution date'
      );
      UPDATE public.portfolio_commands
      SET status = 'rejected', updated_at = now(), error = 'invalid execution date', result = v_result
      WHERE id = v_command.id;
      RETURN v_result;
    END IF;
  END IF;

  IF v_command.operation = 'buy' THEN
    IF v_has_holding THEN
      v_new_shares := v_holding.shares + v_command.qty;
      v_new_avg := ((v_holding.shares * v_holding.avg_cost)
        + (v_command.qty * v_command.price)) / v_new_shares;
      UPDATE public.holdings
      SET shares = v_new_shares,
          avg_cost = v_new_avg,
          opened_at = LEAST(COALESCE(opened_at, v_executed_on), v_executed_on),
          high_water_price = GREATEST(COALESCE(high_water_price, v_command.price), v_command.price)
      WHERE ticker = v_command.ticker;
    ELSE
      IF v_command.bucket IS NULL THEN
        RAISE EXCEPTION 'bucket is required for a new holding' USING ERRCODE = '22023';
      END IF;
      v_new_shares := v_command.qty;
      v_new_avg := v_command.price;
      INSERT INTO public.holdings (
        ticker, shares, avg_cost, bucket, opened_at, high_water_price
      ) VALUES (
        v_command.ticker, v_new_shares, v_new_avg, v_command.bucket, v_executed_on,
        v_command.price
      );
    END IF;

    INSERT INTO public.transactions (ticker, side, qty, price, source, executed_on)
    VALUES (v_command.ticker, 'buy', v_command.qty, v_command.price, 'telegram', v_executed_on)
    RETURNING id INTO v_transaction_id;

    v_result := jsonb_build_object(
      'ok', true, 'status', 'applied', 'operation', 'buy', 'ticker', v_command.ticker,
      'shares', v_new_shares, 'avg_cost', v_new_avg,
      'bucket', CASE WHEN v_has_holding THEN v_holding.bucket ELSE v_command.bucket END,
      'executed_on', v_executed_on, 'transaction_id', v_transaction_id
    );

    SELECT * INTO v_plan
    FROM public.owner_investment_plans
    WHERE ticker = v_command.ticker AND active = true
    FOR UPDATE;
    v_has_plan := FOUND;
    IF v_has_plan AND v_executed_on >= v_plan.next_due_on
        AND abs((v_command.qty * v_command.price) - v_plan.amount)
          <= GREATEST(1, v_plan.amount * 0.02) THEN
      v_next_month := (date_trunc('month', v_plan.next_due_on)::date + interval '1 month')::date;
      v_last_day := ((v_next_month + interval '1 month')::date - 1);
      v_next_due := v_next_month
        + (LEAST(v_plan.due_day::int, EXTRACT(day FROM v_last_day)::int) - 1);
      UPDATE public.owner_investment_plans
      SET next_due_on = v_next_due, updated_at = now()
      WHERE id = v_plan.id;
      v_result := v_result || jsonb_build_object('plan_advanced_to', v_next_due);
    END IF;

  ELSIF v_command.operation = 'sell' THEN
    IF NOT v_has_holding THEN
      v_result := jsonb_build_object('ok', false, 'status', 'rejected', 'reason', 'holding not found');
      UPDATE public.portfolio_commands
      SET status = 'rejected', updated_at = now(), error = 'holding not found', result = v_result
      WHERE id = v_command.id;
      RETURN v_result;
    ELSIF v_command.qty > v_holding.shares THEN
      v_result := jsonb_build_object('ok', false, 'status', 'rejected', 'reason', 'sell exceeds recorded shares');
      UPDATE public.portfolio_commands
      SET status = 'rejected', updated_at = now(), error = 'sell exceeds recorded shares', result = v_result
      WHERE id = v_command.id;
      RETURN v_result;
    END IF;

    v_new_shares := v_holding.shares - v_command.qty;
    v_realized := (v_command.price - v_holding.avg_cost) * v_command.qty;
    INSERT INTO public.transactions (ticker, side, qty, price, source, executed_on)
    VALUES (v_command.ticker, 'sell', v_command.qty, v_command.price, 'telegram', v_executed_on)
    RETURNING id INTO v_transaction_id;
    IF v_new_shares = 0 THEN
      DELETE FROM public.holdings WHERE ticker = v_command.ticker;
    ELSE
      UPDATE public.holdings SET shares = v_new_shares WHERE ticker = v_command.ticker;
    END IF;
    v_result := jsonb_build_object(
      'ok', true, 'status', 'applied', 'operation', 'sell', 'ticker', v_command.ticker,
      'shares', v_new_shares,
      'avg_cost', CASE WHEN v_new_shares > 0 THEN v_holding.avg_cost ELSE NULL END,
      'realized_pnl', v_realized, 'executed_on', v_executed_on,
      'transaction_id', v_transaction_id
    );

  ELSIF v_command.operation = 'stop' THEN
    IF NOT v_has_holding THEN
      v_result := jsonb_build_object('ok', false, 'status', 'rejected', 'reason', 'holding not found');
      UPDATE public.portfolio_commands
      SET status = 'rejected', updated_at = now(), error = 'holding not found', result = v_result
      WHERE id = v_command.id;
      RETURN v_result;
    END IF;
    UPDATE public.holdings SET stop = v_command.stop WHERE ticker = v_command.ticker;
    v_new_shares := v_holding.shares;
    v_new_avg := v_holding.avg_cost;
    v_result := jsonb_build_object(
      'ok', true, 'status', 'applied', 'operation', 'stop', 'ticker', v_command.ticker,
      'shares', v_new_shares, 'avg_cost', v_new_avg, 'stop', v_command.stop
    );
  END IF;

  UPDATE public.portfolio_commands
  SET status = 'applied', applied_at = now(), updated_at = now(),
      realized_pnl = v_realized, result = v_result, error = NULL
  WHERE id = v_command.id;
  RETURN v_result;
END;
$$;

-- Final-state source: sql/migrations/20260909_transaction_chronology.sql
CREATE OR REPLACE FUNCTION public.apply_portfolio_command(
  p_command_id UUID,
  p_chat_id BIGINT,
  p_user_id BIGINT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  v_command public.portfolio_commands%ROWTYPE;
  v_holding public.holdings%ROWTYPE;
  v_has_holding BOOLEAN;
  v_current_shares NUMERIC;
  v_executed_on DATE;
  v_latest_transaction_on DATE;
  v_result JSONB;
BEGIN
  SELECT * INTO v_command
  FROM public.portfolio_commands
  WHERE id = p_command_id
  FOR UPDATE;

  IF NOT FOUND OR v_command.chat_id IS DISTINCT FROM p_chat_id
      OR v_command.user_id IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'command unavailable' USING ERRCODE = '42501';
  END IF;

  IF v_command.status = 'rejected'
      AND v_command.result->>'code' = 'TRANSACTION_OUT_OF_ORDER' THEN
    RETURN v_command.result || jsonb_build_object('duplicate', true);
  END IF;

  IF v_command.status = 'pending' AND v_command.operation IN ('buy', 'sell') THEN
    -- Preserve prerequisite receipts before assigning a chronology rejection.
    IF v_command.expires_at <= now() THEN
      RETURN public.apply_portfolio_command_without_chronology(p_command_id, p_chat_id, p_user_id);
    END IF;

    -- Hold the same ticker and holding locks through eligibility, chronology,
    -- and the delegated mutation; the legacy function can re-enter these locks.
    PERFORM pg_advisory_xact_lock(hashtextextended(v_command.ticker, 0));
    SELECT * INTO v_holding
    FROM public.holdings
    WHERE ticker = v_command.ticker
    FOR UPDATE;
    v_has_holding := FOUND;
    v_current_shares := CASE WHEN v_has_holding THEN v_holding.shares ELSE 0 END;

    IF v_current_shares IS DISTINCT FROM v_command.expected_shares THEN
      RETURN public.apply_portfolio_command_without_chronology(p_command_id, p_chat_id, p_user_id);
    END IF;
    IF v_command.operation = 'sell' AND (NOT v_has_holding OR v_command.qty > v_holding.shares) THEN
      RETURN public.apply_portfolio_command_without_chronology(p_command_id, p_chat_id, p_user_id);
    END IF;
    IF v_command.operation = 'buy' AND NOT v_has_holding AND v_command.bucket IS NULL THEN
      RETURN public.apply_portfolio_command_without_chronology(p_command_id, p_chat_id, p_user_id);
    END IF;

    v_executed_on := COALESCE(
      v_command.executed_on,
      (CURRENT_TIMESTAMP AT TIME ZONE 'America/Chicago')::date
    );
    IF v_executed_on >= DATE '2000-01-01'
        AND v_executed_on <= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Chicago')::date THEN
      -- Nullable columns can satisfy a CHECK through SQL NULL semantics.
      -- Reject corrupt amounts before chronology or legacy write arithmetic.
      IF v_command.qty IS NULL OR v_command.qty <= 0
          OR v_command.price IS NULL OR v_command.price <= 0 THEN
        v_result := jsonb_build_object(
          'ok', false, 'status', 'rejected',
          'reason', 'quantity and price must be positive'
        );
        UPDATE public.portfolio_commands
        SET status = 'rejected', updated_at = now(), error = 'invalid transaction amount', result = v_result
        WHERE id = v_command.id;
        RETURN v_result;
      END IF;

      SELECT MAX(COALESCE(executed_on, (ts AT TIME ZONE 'America/Chicago')::date))
      INTO v_latest_transaction_on
      FROM public.transactions
      WHERE ticker = v_command.ticker;

      IF v_executed_on < v_latest_transaction_on THEN
        v_result := jsonb_build_object(
          'ok', false,
          'status', 'rejected',
          'code', 'TRANSACTION_OUT_OF_ORDER',
          'reason', 'transaction execution date precedes the recorded ledger; reconciliation is required',
          'executed_on', v_executed_on,
          'latest_transaction_on', v_latest_transaction_on
        );
        UPDATE public.portfolio_commands
        SET status = 'rejected', updated_at = now(), error = 'TRANSACTION_OUT_OF_ORDER', result = v_result
        WHERE id = v_command.id;
        RETURN v_result;
      END IF;
    END IF;
  END IF;

  RETURN public.apply_portfolio_command_without_chronology(p_command_id, p_chat_id, p_user_id);
END;
$$;

REVOKE ALL ON FUNCTION public.apply_portfolio_command_without_chronology(UUID, BIGINT, BIGINT)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.apply_portfolio_command(UUID, BIGINT, BIGINT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_portfolio_command(UUID, BIGINT, BIGINT) TO service_role;


-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
CREATE OR REPLACE FUNCTION public.create_market_report_publication(
  p_run_id UUID, p_report_id UUID, p_idempotency_key TEXT, p_market_date DATE,
  p_kind TEXT, p_rendered_body TEXT, p_rendered_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_report public.market_reports%ROWTYPE; v_existing public.market_report_publications%ROWTYPE;
BEGIN
  IF p_run_id IS NULL OR p_report_id IS NULL OR p_idempotency_key !~ '^[0-9a-f]{64}$'
     OR p_kind NOT IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')
     OR p_rendered_body IS NULL OR char_length(p_rendered_body)>14000
     OR p_rendered_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid report publication' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_report FROM public.market_reports WHERE id=p_report_id FOR UPDATE;
  IF NOT FOUND OR v_report.run_id IS DISTINCT FROM p_run_id
     OR v_report.idempotency_key IS DISTINCT FROM p_idempotency_key
     OR v_report.market_date IS DISTINCT FROM p_market_date OR v_report.kind IS DISTINCT FROM p_kind
     OR v_report.rendered_text IS DISTINCT FROM p_rendered_body
     OR v_report.rendered_hash IS DISTINCT FROM p_rendered_hash THEN
    RAISE EXCEPTION 'report publication mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_report_publications
  WHERE report_id=p_report_id OR idempotency_key=p_idempotency_key FOR UPDATE;
  IF FOUND THEN
    IF v_existing.report_id IS DISTINCT FROM p_report_id OR v_existing.idempotency_key IS DISTINCT FROM p_idempotency_key THEN
      RAISE EXCEPTION 'report publication idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('report_id',v_existing.report_id,'idempotency_key',v_existing.idempotency_key,
      'status',v_existing.status,'telegram_message_ids',v_existing.telegram_message_ids,
      'telegram_accepted_at',v_existing.telegram_accepted_at,'lease_token',v_existing.lease_token);
  END IF;
  INSERT INTO public.market_report_publications(report_id,idempotency_key,status)
  VALUES (p_report_id,p_idempotency_key,'pending');
  RETURN jsonb_build_object('report_id',p_report_id,'idempotency_key',p_idempotency_key,
    'status','pending','telegram_message_ids','[]'::jsonb,'telegram_accepted_at',NULL,'lease_token',NULL);
END;
$$;

-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
CREATE OR REPLACE FUNCTION public.claim_market_report_publication(p_idempotency_key TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_pub public.market_report_publications%ROWTYPE; v_lease UUID;
BEGIN
  IF p_idempotency_key !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid report publication key' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_pub FROM public.market_report_publications WHERE idempotency_key=p_idempotency_key FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'report publication unavailable' USING ERRCODE='22023'; END IF;
  IF v_pub.status IN ('delivered','uncertain','suppressed') THEN
    RETURN jsonb_build_object('claimed',false,'report_id',v_pub.report_id,'idempotency_key',v_pub.idempotency_key,
      'status',v_pub.status,'telegram_message_ids',v_pub.telegram_message_ids,
      'telegram_accepted_at',v_pub.telegram_accepted_at,'lease_token',NULL);
  END IF;
  IF v_pub.lease_token IS NOT NULL THEN
    IF v_pub.lease_expires_at >= statement_timestamp() THEN
      RETURN jsonb_build_object('claimed',false,'report_id',v_pub.report_id,'idempotency_key',v_pub.idempotency_key,
        'status','pending','telegram_message_ids','[]'::jsonb,'telegram_accepted_at',NULL,'lease_token',NULL);
    END IF;
    UPDATE public.market_report_publications SET status='uncertain',lease_token=NULL,lease_expires_at=NULL,
      error='SEND_LEASE_EXPIRED',updated_at=now() WHERE report_id=v_pub.report_id;
    RETURN jsonb_build_object('claimed',false,'report_id',v_pub.report_id,'idempotency_key',v_pub.idempotency_key,
      'status','uncertain','telegram_message_ids','[]'::jsonb,'telegram_accepted_at',NULL,'lease_token',NULL);
  END IF;
  IF v_pub.status NOT IN ('pending','failed') THEN RAISE EXCEPTION 'invalid report publication state' USING ERRCODE='22023'; END IF;
  v_lease := gen_random_uuid();
  UPDATE public.market_report_publications SET lease_token=v_lease,
    lease_expires_at=statement_timestamp()+interval '5 minutes',attempt_count=attempt_count+1,
    error=NULL,updated_at=now() WHERE report_id=v_pub.report_id;
  RETURN jsonb_build_object('claimed',true,'report_id',v_pub.report_id,'idempotency_key',v_pub.idempotency_key,
    'status','pending','telegram_message_ids','[]'::jsonb,'telegram_accepted_at',NULL,'lease_token',v_lease);
END;
$$;

-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
CREATE OR REPLACE FUNCTION public.finish_market_report_publication(
  p_idempotency_key TEXT, p_lease_token UUID, p_status TEXT, p_message_ids JSONB, p_error TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_ids_valid BOOLEAN; v_result public.market_report_publications%ROWTYPE;
BEGIN
  IF p_idempotency_key !~ '^[0-9a-f]{64}$' OR p_status NOT IN ('delivered','failed','uncertain')
     OR jsonb_typeof(p_message_ids)<>'array' OR char_length(COALESCE(p_error,''))>1000 THEN
    RAISE EXCEPTION 'invalid report publication completion' USING ERRCODE='22023';
  END IF;
  SELECT COALESCE(bool_and(jsonb_typeof(value)='number' AND value::text ~ '^[0-9]+$'),true)
    INTO v_ids_valid FROM jsonb_array_elements(p_message_ids);
  IF NOT v_ids_valid OR (p_status='delivered' AND jsonb_array_length(p_message_ids)=0) THEN
    RAISE EXCEPTION 'invalid telegram message ids' USING ERRCODE='22023';
  END IF;
  UPDATE public.market_report_publications SET status=p_status,
    telegram_message_ids=CASE WHEN p_status='delivered' THEN p_message_ids ELSE '[]'::jsonb END,
    telegram_accepted_at=CASE WHEN p_status='delivered' THEN now() ELSE NULL END,
    lease_token=NULL,lease_expires_at=NULL,error=CASE WHEN p_error IS NULL THEN NULL ELSE regexp_replace(left(p_error,1000),'[^A-Za-z0-9_ .:-]','?','g') END,
    updated_at=now()
  WHERE idempotency_key=p_idempotency_key AND lease_token=p_lease_token AND status IN ('pending','failed')
  RETURNING * INTO v_result;
  IF NOT FOUND THEN RAISE EXCEPTION 'report publication lease unavailable' USING ERRCODE='40001'; END IF;
  RETURN jsonb_build_object('report_id',v_result.report_id,'idempotency_key',v_result.idempotency_key,
    'status',v_result.status,'telegram_message_ids',v_result.telegram_message_ids,
    'telegram_accepted_at',v_result.telegram_accepted_at,'lease_token',NULL);
END;
$$;

-- Final-state source: sql/migrations/20260926_report_suppression_reasons.sql
CREATE OR REPLACE FUNCTION public.suppress_market_report_publication(p_idempotency_key TEXT,p_reason TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result public.market_report_publications%ROWTYPE;
BEGIN
  IF p_idempotency_key IS NULL OR p_idempotency_key !~ '^[0-9a-f]{64}$' OR p_reason IS NULL
     OR p_reason NOT IN ('no_trigger','not_actionable','REPORT_POLICY_MISMATCH') THEN
    RAISE EXCEPTION 'invalid report suppression reason' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_result FROM public.market_report_publications WHERE idempotency_key=p_idempotency_key FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'report publication unavailable' USING ERRCODE='55000'; END IF;
  IF v_result.status='suppressed' THEN
    IF v_result.suppression_reason IS DISTINCT FROM p_reason THEN
      RAISE EXCEPTION 'report suppression reason is immutable' USING ERRCODE='55000';
    END IF;
  ELSIF v_result.status IN ('pending','failed') AND v_result.lease_token IS NULL THEN
    UPDATE public.market_report_publications SET status='suppressed',suppression_reason=p_reason,error=NULL,
      telegram_message_ids='[]'::jsonb,telegram_accepted_at=NULL,updated_at=statement_timestamp()
      WHERE report_id=v_result.report_id RETURNING * INTO v_result;
  ELSE
    RAISE EXCEPTION 'report publication cannot be suppressed' USING ERRCODE='55000';
  END IF;
  RETURN jsonb_build_object('report_id',v_result.report_id,'idempotency_key',v_result.idempotency_key,
    'status',v_result.status,'suppression_reason',v_result.suppression_reason,
    'telegram_message_ids',v_result.telegram_message_ids,'telegram_accepted_at',v_result.telegram_accepted_at);
END;
$$;

-- Final-state source: sql/migrations/202609120001_delivery_recovery_claims.sql
CREATE OR REPLACE FUNCTION public.claim_market_gateway_request(
  p_request_id UUID, p_operation TEXT, p_run_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_lease UUID;
  v_stored_run UUID := CASE WHEN p_operation='record_report' THEN NULL ELSE p_run_id END;
BEGIN
  IF p_operation NOT IN ('start_run','read_context','record_artifacts','grade_due_decisions','evaluate_and_publish','evaluate_alert_rules','finish_run','record_report')
     OR (p_operation='start_run' AND p_run_id IS NOT NULL) OR (p_operation<>'start_run' AND p_run_id IS NULL) THEN
    RAISE EXCEPTION 'invalid request identity' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('market_gateway_rate',0));
    IF (SELECT count(*) FROM public.market_gateway_requests WHERE created_at>=now()-interval '1 hour')>=100
       OR (v_stored_run IS NOT NULL AND (SELECT count(*) FROM public.market_gateway_requests WHERE run_id=v_stored_run)>=20) THEN
      RAISE EXCEPTION 'gateway rate limit exceeded' USING ERRCODE='54000';
    END IF;
    v_lease:=gen_random_uuid(); INSERT INTO public.market_gateway_requests(request_id,operation,run_id,status,lease_token)
      VALUES(p_request_id,p_operation,v_stored_run,'claimed',v_lease);
    RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,'attempt_count',1);
  END IF;
  IF v_request.operation<>p_operation OR v_request.run_id IS DISTINCT FROM v_stored_run THEN RAISE EXCEPTION 'request identity mismatch' USING ERRCODE='22023'; END IF;
  IF v_request.status='failed' AND p_operation='record_report' THEN
    v_lease:=gen_random_uuid(); UPDATE public.market_gateway_requests SET status='claimed',lease_token=v_lease,claimed_at=now(),attempt_count=attempt_count+1 WHERE request_id=p_request_id;
    RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,'attempt_count',v_request.attempt_count+1);
  END IF;
  IF v_request.status IN ('completed','failed') THEN RETURN jsonb_build_object('claimed',false,'status',v_request.status,'response',v_request.response,'response_digest',v_request.response_digest); END IF;
  IF v_request.claimed_at>now()-interval '5 minutes' THEN RETURN jsonb_build_object('claimed',false,'status','REQUEST_IN_PROGRESS'); END IF;
  v_lease:=gen_random_uuid(); UPDATE public.market_gateway_requests SET lease_token=v_lease,claimed_at=now(),attempt_count=attempt_count+1 WHERE request_id=p_request_id;
  RETURN jsonb_build_object('claimed',true,'lease_token',v_lease,'attempt_count',v_request.attempt_count+1);
END;
$$;

-- Final-state source: sql/migrations/20260913_command_acknowledgement_lease.sql
CREATE OR REPLACE FUNCTION public.apply_portfolio_command_with_acknowledgement(
  p_action TEXT, p_command_id UUID, p_chat_id BIGINT, p_user_id BIGINT, p_telegram_update_id BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_ack public.portfolio_command_acknowledgements%ROWTYPE; v_lease UUID;
BEGIN
  IF p_action NOT IN ('confirm','cancel') OR p_telegram_update_id<0 THEN
    RAISE EXCEPTION 'invalid command acknowledgement' USING ERRCODE='22023';
  END IF;
  IF p_action='confirm' THEN
    v_result:=public.apply_portfolio_command(p_command_id,p_chat_id,p_user_id);
  ELSE
    v_result:=public.cancel_portfolio_command(p_command_id,p_chat_id,p_user_id);
  END IF;
  INSERT INTO public.portfolio_command_acknowledgements(command_id,telegram_update_id,status,result)
  VALUES(p_command_id,p_telegram_update_id,'pending',v_result)
  ON CONFLICT (command_id) DO NOTHING;
  SELECT * INTO v_ack FROM public.portfolio_command_acknowledgements
  WHERE command_id=p_command_id FOR UPDATE;
  IF NOT FOUND OR v_ack.telegram_update_id IS DISTINCT FROM p_telegram_update_id THEN
    RAISE EXCEPTION 'acknowledgement target mismatch' USING ERRCODE='22023';
  END IF;
  IF v_ack.status IN ('delivered','uncertain') THEN
    RETURN jsonb_build_object('result',v_ack.result,'acknowledgement_status',v_ack.status,
      'acknowledgement_claimed',false,'acknowledgement_lease_token',NULL);
  END IF;
  IF v_ack.lease_token IS NOT NULL THEN
    IF v_ack.lease_expires_at >= statement_timestamp() THEN
      RETURN jsonb_build_object('result',v_ack.result,'acknowledgement_status',v_ack.status,
        'acknowledgement_claimed',false,'acknowledgement_lease_token',NULL);
    END IF;
    UPDATE public.portfolio_command_acknowledgements
    SET status='uncertain',lease_token=NULL,lease_expires_at=NULL,
      error='ACKNOWLEDGEMENT_LEASE_EXPIRED',updated_at=now()
    WHERE command_id=v_ack.command_id;
    RETURN jsonb_build_object('result',v_ack.result,'acknowledgement_status','uncertain',
      'acknowledgement_claimed',false,'acknowledgement_lease_token',NULL);
  END IF;
  v_lease:=gen_random_uuid();
  UPDATE public.portfolio_command_acknowledgements
  SET lease_token=v_lease,lease_expires_at=statement_timestamp()+interval '5 minutes',
    attempt_count=attempt_count+1,error=NULL,updated_at=now()
  WHERE command_id=v_ack.command_id;
  RETURN jsonb_build_object('result',v_ack.result,'acknowledgement_status',v_ack.status,
    'acknowledgement_claimed',true,'acknowledgement_lease_token',v_lease);
END;
$$;

-- Final-state source: sql/migrations/20260913_command_acknowledgement_lease.sql
CREATE OR REPLACE FUNCTION public.finish_portfolio_command_acknowledgement(
  p_command_id UUID, p_telegram_update_id BIGINT, p_lease_token UUID, p_status TEXT, p_error TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_ack public.portfolio_command_acknowledgements%ROWTYPE;
BEGIN
  IF p_lease_token IS NULL OR p_status NOT IN ('delivered','failed','uncertain')
     OR char_length(COALESCE(p_error,''))>1000 THEN
    RAISE EXCEPTION 'invalid acknowledgement completion' USING ERRCODE='22023';
  END IF;
  UPDATE public.portfolio_command_acknowledgements
  SET status='uncertain',error='ACKNOWLEDGEMENT_LEASE_EXPIRED',
    lease_token=NULL,lease_expires_at=NULL,updated_at=now()
  WHERE command_id=p_command_id AND telegram_update_id=p_telegram_update_id
    AND lease_token=p_lease_token AND lease_expires_at < statement_timestamp()
    AND status IN ('pending','failed')
  RETURNING * INTO v_ack;
  IF FOUND THEN
    RETURN jsonb_build_object('status',v_ack.status,'result',v_ack.result);
  END IF;
  UPDATE public.portfolio_command_acknowledgements
  SET status=p_status,error=p_error,lease_token=NULL,lease_expires_at=NULL,updated_at=now()
  WHERE command_id=p_command_id AND telegram_update_id=p_telegram_update_id
    AND lease_token=p_lease_token AND lease_expires_at >= statement_timestamp()
    AND status IN ('pending','failed')
  RETURNING * INTO v_ack;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'acknowledgement lease unavailable' USING ERRCODE='40001';
  END IF;
  RETURN jsonb_build_object('status',v_ack.status,'result',v_ack.result);
END;
$$;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
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

-- Final-state source: sql/migrations/20260916_run_scoped_request_provenance.sql
CREATE OR REPLACE FUNCTION public.reuse_market_source_item_if_immutable()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE existing_row public.market_source_items%ROWTYPE;
BEGIN
  SELECT * INTO existing_row FROM public.market_source_items WHERE id=NEW.id;
  IF NOT FOUND THEN RETURN NEW; END IF;
  IF existing_row.provider IS DISTINCT FROM NEW.provider OR existing_row.upstream_item_id IS DISTINCT FROM NEW.upstream_item_id OR existing_row.published_at IS DISTINCT FROM NEW.published_at OR existing_row.effective_at IS DISTINCT FROM NEW.effective_at OR existing_row.title IS DISTINCT FROM NEW.title OR existing_row.normalized_text IS DISTINCT FROM NEW.normalized_text OR existing_row.canonical_content IS DISTINCT FROM NEW.canonical_content OR existing_row.content_hash IS DISTINCT FROM NEW.content_hash OR existing_row.metadata IS DISTINCT FROM NEW.metadata THEN
    RAISE EXCEPTION 'conflicting immutable market source item identity' USING ERRCODE='22023';
  END IF;
  RETURN NULL;
END;
$$;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
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

-- Final-state source: sql/migrations/20261002_provider_registry_and_release_ledger_recovery.sql
CREATE OR REPLACE FUNCTION public.checkpoint_market_intelligence_collection(
  p_run_id UUID,
  p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_window JSONB;
  v_existing public.market_collection_checkpoints%ROWTYPE;
  r JSONB;
  v_reserved public.market_source_quota_reservations%ROWTYPE;
  v_used INT;
  v_existing_receipt JSONB;
  v_existing_uncertain BOOLEAN;
  v_incoming_uncertain BOOLEAN;
  v_same_attempt_identity BOOLEAN;
  v_valid_attempt_transition BOOLEAN;
BEGIN
  IF p_payload IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR NOT(p_payload ?& ARRAY['cache_key','receipt','items'])
     OR (p_payload-ARRAY['cache_key','receipt','items'])<>'{}'::jsonb
     OR octet_length(p_payload::text)>65536
     OR jsonb_typeof(p_payload->'items')<>'array'
     OR jsonb_array_length(p_payload->'items')>50 THEN
    RAISE EXCEPTION 'invalid collection checkpoint' USING ERRCODE='22023';
  END IF;
  SELECT request_window INTO v_window
  FROM public.market_intelligence_runs WHERE id=p_run_id FOR UPDATE;
  IF v_window IS NULL
     OR NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running')
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status='started')
     OR EXISTS(SELECT 1 FROM public.market_intelligence_run_events WHERE run_id=p_run_id AND status IN ('completed','failed')) THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;

  r:=p_payload->'receipt';
  IF r IS NULL OR NOT(r ?& ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])
     OR (r-ARRAY['provider','reservation_id','status','cache_key','requested_window','requested_limit',
       'retrieved_at','observed_at','expires_at','request_cost','upstream_remaining','returned_count','accepted_count',
       'duplicate_count','dropped_count','response_hash','error_code','source_receipt_id','cache_predecessor_receipt_id'])<>'{}'::jsonb
     OR r->>'status' NOT IN ('succeeded','failed','quota_blocked')
     OR (r->>'request_cost')::int NOT BETWEEN 1 AND 100
     OR r->'cache_predecessor_receipt_id'<>'null'::jsonb
     OR r->>'cache_key'<>p_payload->>'cache_key'
     OR (r->'requested_window'->>'start')::timestamptz<>(v_window->>'start')::timestamptz
     OR (r->'requested_window'->>'end')::timestamptz<>(v_window->>'end')::timestamptz
     OR (r->>'source_receipt_id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
     OR ((r->>'status')='succeeded'
       AND ((r->>'expires_at')::timestamptz<=(r->>'retrieved_at')::timestamptz
         OR r->>'response_hash' !~ '^[0-9a-f]{64}$'))
     OR ((r->>'status')<>'succeeded'
       AND (r->'expires_at'<>'null'::jsonb OR COALESCE(r->>'error_code','')='')) THEN
    RAISE EXCEPTION 'checkpoint must record an actual request outcome' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_reserved
  FROM public.market_source_quota_reservations
  WHERE id=(r->>'reservation_id')::uuid AND run_id=p_run_id AND provider=r->>'provider';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'wrong-run reservation' USING ERRCODE='22023';
  END IF;
  v_incoming_uncertain := r->>'status'='failed'
    AND r->>'error_code'='TRANSPORT_OUTCOME_UNCERTAIN';
  IF v_incoming_uncertain AND (
      v_reserved.provider NOT IN ('gdelt','alpha_vantage','finnhub','sec_edgar','federal_register','white_house','doe','dod','eia','fred','bls','bea')
      OR jsonb_array_length(p_payload->'items')<>0
      OR r->'observed_at'<>'null'::jsonb
      OR r->'expires_at'<>'null'::jsonb
      OR r->'upstream_remaining'<>'null'::jsonb
      OR r->'response_hash'<>'null'::jsonb
      OR (r->>'returned_count')::int<>0
      OR (r->>'accepted_count')::int<>0
      OR (r->>'duplicate_count')::int<>0
      OR (r->>'dropped_count')::int<>0
  ) THEN
    RAISE EXCEPTION 'invalid provider attempt barrier' USING ERRCODE='22023';
  END IF;

  SELECT * INTO v_existing
  FROM public.market_collection_checkpoints
  WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key' FOR UPDATE;
  IF FOUND AND v_existing.payload=p_payload-'cache_key' THEN
    RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
  END IF;
  SELECT COALESCE(sum((payload->'receipt'->>'request_cost')::int),0) INTO v_used FROM (
    SELECT payload FROM public.market_collection_checkpoints WHERE run_id=p_run_id
    UNION ALL SELECT payload FROM public.market_collection_checkpoint_history WHERE run_id=p_run_id
  ) paid WHERE payload->'receipt'->>'reservation_id'=r->>'reservation_id';
  IF v_existing.run_id IS NOT NULL THEN
    v_existing_receipt := v_existing.payload->'receipt';
    v_existing_uncertain := v_existing_receipt->>'status'='failed'
      AND v_existing_receipt->>'error_code'='TRANSPORT_OUTCOME_UNCERTAIN';
    v_same_attempt_identity := v_existing.source_receipt_id::text=r->>'source_receipt_id'
      AND v_existing_receipt->>'provider'=r->>'provider'
      AND v_existing_receipt->>'reservation_id'=r->>'reservation_id'
      AND v_existing_receipt->>'cache_key'=r->>'cache_key'
      AND v_existing_receipt->'requested_window'=r->'requested_window'
      AND v_existing_receipt->>'requested_limit'=r->>'requested_limit';
    v_valid_attempt_transition := v_existing_uncertain AND v_same_attempt_identity AND (
      (v_incoming_uncertain
       AND (r->>'request_cost')::int=(v_existing_receipt->>'request_cost')::int+1)
      OR (NOT v_incoming_uncertain
       AND (r->>'request_cost')::int=(v_existing_receipt->>'request_cost')::int)
    );
    IF v_valid_attempt_transition THEN
      IF v_used-(v_existing_receipt->>'request_cost')::int+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
        RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
      END IF;
      -- Never archive an attempt barrier: history plus its terminal row would
      -- count the same provider request twice during restart reconciliation.
      UPDATE public.market_collection_checkpoints
      SET payload=p_payload-'cache_key',created_at=statement_timestamp()
      WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
    ELSE
      IF v_existing.source_receipt_id=(r->>'source_receipt_id')::uuid
         OR (v_existing_receipt->>'expires_at')::timestamptz>statement_timestamp() THEN
        RAISE EXCEPTION 'checkpoint idempotency mismatch' USING ERRCODE='22023';
      END IF;
      IF v_used+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
        RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
      END IF;
      INSERT INTO public.market_collection_checkpoint_history(
        run_id,cache_key,source_receipt_id,payload
      ) VALUES(
        v_existing.run_id,v_existing.cache_key,v_existing.source_receipt_id,v_existing.payload
      );
      UPDATE public.market_collection_checkpoints
      SET source_receipt_id=(r->>'source_receipt_id')::uuid,
          payload=p_payload-'cache_key',created_at=statement_timestamp()
      WHERE run_id=p_run_id AND cache_key=p_payload->>'cache_key';
    END IF;
  ELSE
    IF v_used+(r->>'request_cost')::int>v_reserved.reserved_requests THEN
      RAISE EXCEPTION 'reservation use exceeds allocation' USING ERRCODE='54000';
    END IF;
    INSERT INTO public.market_collection_checkpoints(
      run_id,cache_key,request_window,source_receipt_id,payload
    ) VALUES(
      p_run_id,p_payload->>'cache_key',v_window,
      (r->>'source_receipt_id')::uuid,p_payload-'cache_key'
    );
  END IF;
  RETURN jsonb_build_object('run_id',p_run_id,'cache_key',p_payload->>'cache_key');
END;
$$;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
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

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
CREATE OR REPLACE FUNCTION public.initialize_market_intelligence_window()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  IF TG_OP='UPDATE' AND OLD.request_window IS NULL AND NEW.request_window IS NOT NULL
     AND to_jsonb(OLD)-'request_window'=to_jsonb(NEW)-'request_window' THEN RETURN NEW; END IF;
  RAISE EXCEPTION 'market intelligence records are append-only' USING ERRCODE='55000';
END; $$;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
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

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
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

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
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

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
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

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
CREATE OR REPLACE FUNCTION public.start_market_analysis_run(
  p_request_id UUID, p_lease_token UUID, p_kind TEXT, p_market_date DATE
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_run public.analysis_runs%ROWTYPE;
BEGIN
  IF p_kind NOT IN ('pre-market','intraday','post-market','on-demand') OR p_market_date IS NULL THEN
    RAISE EXCEPTION 'invalid analysis run slot' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'start_run' OR v_request.lease_token<>p_lease_token
     OR v_request.status<>'claimed' THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001';
  END IF;
  SELECT * INTO v_run FROM public.analysis_runs WHERE gateway_request_id=p_request_id;
  IF FOUND THEN
    IF v_run.kind IS DISTINCT FROM p_kind
       OR COALESCE(v_run.scheduled_market_date,p_market_date) IS DISTINCT FROM p_market_date THEN
      RAISE EXCEPTION 'run identity mismatch' USING ERRCODE='22023';
    END IF;
    UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id;
    RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
  END IF;
  IF p_kind <> 'on-demand' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('scheduled-analysis-run:'||p_market_date::text||':'||p_kind,0));
    SELECT * INTO v_run FROM public.analysis_runs
      WHERE scheduled_market_date=p_market_date AND scheduled_phase=p_kind FOR UPDATE;
    IF FOUND THEN
      UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id;
      RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
    END IF;
    INSERT INTO public.analysis_runs(kind,status,gateway_request_id,scheduled_market_date,scheduled_phase)
    VALUES (p_kind,'running',p_request_id,p_market_date,p_kind) RETURNING * INTO v_run;
  ELSE
    INSERT INTO public.analysis_runs(kind,status,gateway_request_id)
    VALUES (p_kind,'running',p_request_id) RETURNING * INTO v_run;
  END IF;
  UPDATE public.market_gateway_requests SET run_id=v_run.id WHERE request_id=p_request_id;
  RETURN jsonb_build_object('run_id',v_run.id,'duplicate',false);
END;
$$;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_run public.analysis_runs%ROWTYPE;
  v_counts JSONB;
  v_statuses JSONB;
  v_ids JSONB;
  v_status TEXT;
  v_quiet_intraday BOOLEAN := false;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS (
      SELECT 1 FROM public.market_intelligence_runs i
      WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase
        AND i.market_date=v_run.scheduled_market_date
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_intelligence_run_events e
      WHERE e.run_id=p_run_id AND e.status='completed'
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id
    ) THEN
      RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id
    ) THEN
      RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish'
        AND q.status='completed'
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_publications p
      WHERE p.run_id=p_run_id AND p.market_date=v_run.scheduled_market_date
        AND p.phase=v_run.scheduled_phase AND p.status='suppressed'
    ) THEN
      RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023';
    END IF;

    SELECT v_run.scheduled_phase='intraday' AND EXISTS (
      SELECT 1 FROM public.market_run_terminal_outcomes outcome
      JOIN public.market_gateway_requests request
        ON request.request_id=outcome.evaluation_request_id
      JOIN public.market_publications publication
        ON publication.run_id=outcome.run_id
       AND publication.idempotency_key=outcome.evaluation_request_id
      WHERE outcome.run_id=p_run_id
        AND outcome.outcome IN ('no_trigger','not_actionable')
        AND request.run_id=p_run_id AND request.operation='evaluate_and_publish'
        AND request.status='completed'
        AND publication.market_date=v_run.scheduled_market_date
        AND publication.phase='intraday' AND publication.status='suppressed'
        AND publication.telegram_message_ids='[]'::jsonb
    ) INTO v_quiet_intraday;

    IF NOT v_quiet_intraday AND NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_report_request_origins o ON o.request_id=q.request_id
      JOIN public.market_reports r ON r.id=CASE
        WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        THEN (q.response->>'report_id')::uuid END
      WHERE q.operation='record_report' AND q.run_id IS NULL
        AND q.status='completed' AND jsonb_typeof(q.response)='object'
        AND q.response->>'report_hash'=r.report_hash
        AND q.response->>'rendered_hash'=r.rendered_hash
        AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase
        AND o.market_date=v_run.scheduled_market_date
        AND o.requested_packet_id=r.packet_id
        AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
        AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent')))
    ) THEN
      RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT v_quiet_intraday AND NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_report_request_origins o ON o.request_id=q.request_id
      JOIN public.market_reports r ON r.id=CASE
        WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        THEN (q.response->>'report_id')::uuid END
      JOIN public.market_report_publications p ON p.report_id=r.id
      WHERE q.operation='record_report' AND q.run_id IS NULL
        AND q.status='completed' AND jsonb_typeof(q.response)='object'
        AND q.response->>'report_hash'=r.report_hash
        AND q.response->>'rendered_hash'=r.rendered_hash
        AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase
        AND o.market_date=v_run.scheduled_market_date
        AND o.requested_packet_id=r.packet_id
        AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
        AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent')))
        AND p.status IN ('delivered','suppressed')
    ) THEN
      RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023';
    END IF;
  END IF;

  SELECT jsonb_build_object(
    'evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),
    'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),
    'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),
    'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id),
    'run_outcomes',(SELECT count(*) FROM public.market_run_terminal_outcomes WHERE run_id=p_run_id)
  ) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb)
  INTO v_statuses FROM (
    SELECT status FROM public.market_publications WHERE run_id=p_run_id
    UNION ALL
    SELECT p.status FROM public.market_reports r
    JOIN public.market_report_publications p ON p.report_id=r.id
    WHERE r.run_id=p_run_id
  ) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb)
  INTO v_ids FROM (
    SELECT value AS message_id FROM public.market_publications p
    CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value
    WHERE p.run_id=p_run_id
    UNION ALL
    SELECT value AS message_id FROM public.market_reports r
    JOIN public.market_report_publications p ON p.report_id=r.id
    CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value
    WHERE r.run_id=p_run_id
  ) message_ids;
  SELECT CASE
    WHEN EXISTS(
      SELECT 1 FROM public.market_gateway_requests
      WHERE run_id=p_run_id AND status='failed'
    ) OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain']
      THEN 'partial'
    WHEN v_quiet_intraday OR (
      NOT EXISTS(
        SELECT 1 FROM public.market_reports r
        JOIN public.market_report_publications p ON p.report_id=r.id
        WHERE r.run_id=p_run_id AND p.status='delivered'
      ) AND EXISTS(
        SELECT 1 FROM public.market_reports r
        JOIN public.market_report_publications p ON p.report_id=r.id
        WHERE r.run_id=p_run_id AND p.status='suppressed'
      )
    ) THEN 'suppressed'
    ELSE 'completed'
  END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),
    write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object(
    'run_id',p_run_id,'status',v_status,'write_counts',v_counts,
    'publication_statuses',v_statuses,'telegram_message_ids',v_ids
  );
END;
$$;

-- Final-state source: sql/migrations/20260924_scheduled_lifecycle_followup.sql
CREATE OR REPLACE FUNCTION public.read_overdue_scheduled_market_phases(
  p_now TIMESTAMPTZ DEFAULT statement_timestamp()
) RETURNS TABLE(market_date DATE, phase TEXT, deadline_at TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_policy JSONB; v_today DATE := timezone('America/Chicago',p_now)::date; v_year TEXT := extract(year FROM v_today)::text;
BEGIN
  SELECT config INTO v_policy FROM public.market_policy_config WHERE active;
  IF NOT FOUND OR jsonb_typeof(v_policy->'nyse_holidays') IS DISTINCT FROM 'array'
     OR COALESCE(v_policy->>'market_calendar_year','') !~ '^[0-9]{4}$'
     OR v_policy->>'market_calendar_year' IS DISTINCT FROM v_year
     OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_policy->'nyse_holidays') AS holiday(value)
                WHERE jsonb_typeof(holiday.value)<>'string'
                   OR holiday.value #>> '{}' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                   OR left(holiday.value #>> '{}',4)<>v_year
                   OR to_char(to_date(holiday.value #>> '{}','FXYYYY-MM-DD'),'YYYY-MM-DD')<>holiday.value #>> '{}') THEN
    RAISE EXCEPTION 'calendar coverage missing' USING ERRCODE='22023';
  END IF;
  RETURN QUERY
    SELECT slot_days.market_date, scheduled.phase,
           (slot_days.market_date::timestamp + deadline.deadline_local + make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago'
    FROM generate_series((SELECT min(effective_on) FROM public.market_scheduled_phase_deadlines),v_today,interval '1 day') AS days(value)
    CROSS JOIN LATERAL (SELECT days.value::date AS market_date) slot_days
    CROSS JOIN (VALUES ('pre-market'::text),('intraday'::text),('post-market'::text)) AS scheduled(phase)
    JOIN LATERAL (
      SELECT deadline_local,grace_minutes FROM public.market_scheduled_phase_deadlines deadline
      WHERE deadline.phase=scheduled.phase AND deadline.effective_on<=slot_days.market_date
      ORDER BY deadline.effective_on DESC LIMIT 1
    ) deadline ON true
    WHERE extract(isodow FROM slot_days.market_date) BETWEEN 1 AND 5
      AND ((slot_days.market_date::timestamp + deadline.deadline_local + make_interval(mins=>deadline.grace_minutes)) AT TIME ZONE 'America/Chicago') < p_now
      AND NOT (v_policy->'nyse_holidays' ? slot_days.market_date::text)
      AND NOT EXISTS (SELECT 1 FROM public.analysis_runs run WHERE run.scheduled_market_date=slot_days.market_date
                      AND run.scheduled_phase=scheduled.phase AND run.status IN ('completed','suppressed'))
    ORDER BY slot_days.market_date, scheduled.phase;
END;
$$;

-- Final-state source: sql/migrations/20260925_scheduled_report_origins.sql
CREATE OR REPLACE FUNCTION public.record_market_report_origin(
  p_request_id UUID,
  p_lease_token UUID,
  p_run_id UUID,
  p_market_date DATE,
  p_requested_kind TEXT,
  p_requested_report_id UUID,
  p_requested_packet_id UUID,
  p_requested_idempotency_key TEXT,
  p_requested_report_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_run public.analysis_runs%ROWTYPE;
  v_origin public.market_report_request_origins%ROWTYPE;
  v_packet_hash TEXT;
  v_expected_key TEXT;
  v_expected_id UUID;
BEGIN
  IF p_request_id IS NULL OR p_lease_token IS NULL OR p_run_id IS NULL
     OR p_market_date IS NULL OR p_requested_packet_id IS NULL
     OR p_requested_report_id IS NULL
     OR p_requested_kind NOT IN ('morning','urgent','weekly','monthly','theme','on-demand','intraday')
     OR p_requested_idempotency_key !~ '^[0-9a-f]{64}$'
     OR p_requested_report_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid report origin' USING ERRCODE='22023';
  END IF;

  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'record_report'
     OR v_request.status<>'claimed' OR v_request.lease_token<>p_lease_token
     OR v_request.run_id IS NOT NULL THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001';
  END IF;

  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NULL THEN
    RETURN jsonb_build_object('scheduled',false,'duplicate',false);
  END IF;
  IF v_run.scheduled_market_date IS DISTINCT FROM p_market_date
     OR NOT ((v_run.scheduled_phase='pre-market' AND p_requested_kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND p_requested_kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND p_requested_kind IN ('weekly','monthly','theme','urgent'))) THEN
    RAISE EXCEPTION 'scheduled report origin mismatch' USING ERRCODE='22023';
  END IF;

  SELECT packet_hash INTO v_packet_hash FROM public.market_evidence_packets
  WHERE id=p_requested_packet_id AND run_id=p_run_id AND status='completed';
  IF NOT FOUND THEN RAISE EXCEPTION 'completed evidence packet unavailable' USING ERRCODE='22023'; END IF;
  v_expected_key := encode(extensions.digest(convert_to(
    'v2:' || p_requested_kind || ':' || p_market_date::text || ':' ||
      v_packet_hash || ':' || p_requested_report_hash,
    'UTF8'
  ),'sha256'),'hex');
  v_expected_id := (
    substr(v_expected_key,1,8) || '-' || substr(v_expected_key,9,4) || '-5' ||
    substr(v_expected_key,14,3) || '-8' || substr(v_expected_key,18,3) || '-' ||
    substr(v_expected_key,21,12)
  )::uuid;
  IF p_requested_idempotency_key<>v_expected_key OR p_requested_report_id<>v_expected_id THEN
    RAISE EXCEPTION 'scheduled report identity mismatch' USING ERRCODE='22023';
  END IF;

  SELECT * INTO v_origin FROM public.market_report_request_origins
  WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_origin.run_id IS DISTINCT FROM p_run_id
       OR v_origin.scheduled_phase IS DISTINCT FROM v_run.scheduled_phase
       OR v_origin.market_date IS DISTINCT FROM p_market_date
       OR v_origin.requested_kind IS DISTINCT FROM p_requested_kind
       OR v_origin.requested_report_id IS DISTINCT FROM p_requested_report_id
       OR v_origin.requested_packet_id IS DISTINCT FROM p_requested_packet_id
       OR v_origin.requested_idempotency_key IS DISTINCT FROM p_requested_idempotency_key
       OR v_origin.requested_report_hash IS DISTINCT FROM p_requested_report_hash THEN
      RAISE EXCEPTION 'scheduled report origin idempotency mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('scheduled',true,'duplicate',true);
  END IF;

  INSERT INTO public.market_report_request_origins(
    request_id,run_id,scheduled_phase,market_date,requested_kind,
    requested_report_id,requested_packet_id,requested_idempotency_key,requested_report_hash
  ) VALUES (
    p_request_id,p_run_id,v_run.scheduled_phase,p_market_date,p_requested_kind,
    p_requested_report_id,p_requested_packet_id,p_requested_idempotency_key,p_requested_report_hash
  );
  RETURN jsonb_build_object('scheduled',true,'duplicate',false);
END;
$$;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE OR REPLACE FUNCTION public.advance_portfolio_cash_ledger()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
  UPDATE public.portfolio_cash_ledger_state
  SET revision=revision+1,updated_at=statement_timestamp()
  WHERE singleton=true;
  RETURN NULL;
END;
$$;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE OR REPLACE FUNCTION public.read_portfolio_cash_ledger_watermark()
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
  SELECT jsonb_build_object(
    'ledger_watermark',revision::text,
    'ledger_updated_at',updated_at
  ) FROM public.portfolio_cash_ledger_state WHERE singleton=true
$$;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE OR REPLACE FUNCTION public.record_reconciled_cash_snapshot(
  p_snapshot_id UUID,
  p_as_of TIMESTAMPTZ,
  p_fresh_through TIMESTAMPTZ,
  p_ledger_watermark BIGINT,
  p_cash JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_revision BIGINT;
  v_ledger_updated_at TIMESTAMPTZ;
  v_existing public.reconciled_cash_snapshots%ROWTYPE;
  v_duplicate BOOLEAN := false;
BEGIN
  IF p_snapshot_id IS NULL OR p_as_of IS NULL OR p_fresh_through IS NULL
     OR p_ledger_watermark IS NULL OR jsonb_typeof(p_cash) IS DISTINCT FROM 'object'
     OR NOT (p_cash ?& ARRAY['core','growth','speculative'])
     OR (p_cash - ARRAY['core','growth','speculative']) <> '{}'::jsonb
     OR EXISTS (
       SELECT 1 FROM jsonb_each(p_cash) item
       WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'string'
         OR trim(both '"' from item.value::text) !~ '^(0|[1-9][0-9]{0,14})(\.[0-9]{1,6})?$'
     )
     OR p_as_of > statement_timestamp() + interval '1 minute'
     OR p_fresh_through <= statement_timestamp()
     OR p_fresh_through > p_as_of + interval '30 minutes' THEN
    RAISE EXCEPTION 'invalid reconciled cash snapshot' USING ERRCODE='22023';
  END IF;

  SELECT revision,updated_at INTO v_revision,v_ledger_updated_at
  FROM public.portfolio_cash_ledger_state
  WHERE singleton=true FOR SHARE;
  IF v_revision IS DISTINCT FROM p_ledger_watermark
     OR p_as_of < v_ledger_updated_at THEN
    RAISE EXCEPTION 'cash ledger watermark changed' USING ERRCODE='40001';
  END IF;

  SELECT * INTO v_existing FROM public.reconciled_cash_snapshots
  WHERE id=p_snapshot_id;
  IF FOUND THEN
    v_duplicate := true;
    IF v_existing.as_of IS DISTINCT FROM p_as_of
       OR v_existing.fresh_through IS DISTINCT FROM p_fresh_through
       OR v_existing.ledger_watermark IS DISTINCT FROM p_ledger_watermark
       OR v_existing.core_available IS DISTINCT FROM (p_cash->>'core')::numeric
       OR v_existing.growth_available IS DISTINCT FROM (p_cash->>'growth')::numeric
       OR v_existing.speculative_available IS DISTINCT FROM (p_cash->>'speculative')::numeric THEN
      RAISE EXCEPTION 'cash snapshot idempotency mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    INSERT INTO public.reconciled_cash_snapshots(
      id,as_of,fresh_through,ledger_watermark,
      core_available,growth_available,speculative_available
    ) VALUES (
      p_snapshot_id,p_as_of,p_fresh_through,p_ledger_watermark,
      (p_cash->>'core')::numeric,(p_cash->>'growth')::numeric,
      (p_cash->>'speculative')::numeric
    ) RETURNING * INTO v_existing;
  END IF;

  RETURN jsonb_build_object(
    'snapshot_id',v_existing.id,
    'as_of',v_existing.as_of,
    'fresh_through',v_existing.fresh_through,
    'ledger_watermark',v_existing.ledger_watermark::text,
    'spendable_cash',jsonb_build_object(
      'core',v_existing.core_available::text,
      'growth',v_existing.growth_available::text,
      'speculative',v_existing.speculative_available::text
    ),
    'duplicate',v_duplicate
  );
END;
$$;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE OR REPLACE FUNCTION public.read_reconciled_cash_snapshot(
  p_now TIMESTAMPTZ DEFAULT statement_timestamp()
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_snapshot public.reconciled_cash_snapshots%ROWTYPE;
BEGIN
  IF p_now IS NULL THEN RETURN NULL; END IF;
  SELECT snapshot.* INTO v_snapshot
  FROM public.reconciled_cash_snapshots snapshot
  JOIN public.portfolio_cash_ledger_state ledger
    ON ledger.singleton=true AND ledger.revision=snapshot.ledger_watermark
  WHERE snapshot.as_of<=p_now AND snapshot.fresh_through>=p_now
  ORDER BY snapshot.as_of DESC,snapshot.created_at DESC,snapshot.id
  LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  RETURN jsonb_build_object(
    'snapshot_id',v_snapshot.id,
    'as_of',v_snapshot.as_of,
    'fresh_through',v_snapshot.fresh_through,
    'ledger_watermark',v_snapshot.ledger_watermark::text,
    'spendable_cash',jsonb_build_object(
      'core',v_snapshot.core_available::text,
      'growth',v_snapshot.growth_available::text,
      'speculative',v_snapshot.speculative_available::text
    )
  );
END;
$$;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE OR REPLACE FUNCTION public.apply_market_decision_bundle_with_cash_snapshot(
  p_request_id UUID,
  p_run_id UUID,
  p_lease_token UUID,
  p_policy_version INT,
  p_evaluations JSONB,
  p_suggestions JSONB,
  p_publication JSONB,
  p_cash_snapshot_id UUID,
  p_cash_ledger_watermark BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_revision BIGINT;
  v_snapshot public.reconciled_cash_snapshots%ROWTYPE;
  v_requires_cash BOOLEAN;
BEGIN
  IF jsonb_typeof(p_evaluations) IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'invalid decision transaction' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_request_id::text,0));
  IF EXISTS (
    SELECT 1 FROM public.market_publications publication
    WHERE publication.idempotency_key=p_request_id
       OR (p_run_id IS NOT NULL AND publication.run_id=p_run_id)
  ) THEN
    -- The original RPC revalidates the active request lease, then returns the
    -- immutable same-request receipt or RUN_ALREADY_EVALUATED. Cash freshness
    -- must gate new money decisions, not make an already durable write orphaned.
    RETURN public.apply_market_decision_bundle(
      p_request_id,p_run_id,p_lease_token,p_policy_version,
      p_evaluations,p_suggestions,p_publication
    );
  END IF;
  SELECT EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_evaluations) evaluation
    WHERE evaluation->>'policy_status'='approved'
      AND evaluation->>'final_action' IN ('buy','add')
  ) INTO v_requires_cash;

  IF v_requires_cash THEN
    IF p_cash_snapshot_id IS NULL OR p_cash_ledger_watermark IS NULL THEN
      RAISE EXCEPTION 'CASH_UNAVAILABLE' USING ERRCODE='55000';
    END IF;
    SELECT revision INTO v_revision
    FROM public.portfolio_cash_ledger_state
    WHERE singleton=true FOR SHARE;
    SELECT * INTO v_snapshot
    FROM public.reconciled_cash_snapshots
    WHERE id=p_cash_snapshot_id FOR SHARE;
    IF NOT FOUND OR v_revision IS DISTINCT FROM p_cash_ledger_watermark
       OR v_snapshot.ledger_watermark IS DISTINCT FROM p_cash_ledger_watermark
       OR v_snapshot.as_of>statement_timestamp()
       OR v_snapshot.fresh_through<statement_timestamp() THEN
      RAISE EXCEPTION 'CASH_UNAVAILABLE' USING ERRCODE='55000';
    END IF;
  ELSIF p_cash_snapshot_id IS NOT NULL OR p_cash_ledger_watermark IS NOT NULL THEN
    RAISE EXCEPTION 'unexpected cash authority' USING ERRCODE='22023';
  END IF;

  RETURN public.apply_market_decision_bundle(
    p_request_id,p_run_id,p_lease_token,p_policy_version,
    p_evaluations,p_suggestions,p_publication
  );
END;
$$;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE OR REPLACE FUNCTION public.record_market_run_outcome(
  p_request_id UUID,
  p_lease_token UUID,
  p_run_id UUID,
  p_outcome TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_run public.analysis_runs%ROWTYPE;
  v_existing public.market_run_terminal_outcomes%ROWTYPE;
  v_evaluation_request_id UUID;
BEGIN
  IF p_request_id IS NULL OR p_lease_token IS NULL OR p_run_id IS NULL
     OR p_outcome NOT IN ('no_trigger','not_actionable') THEN
    RAISE EXCEPTION 'invalid market run outcome' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'evaluate_and_publish'
     OR v_request.run_id IS DISTINCT FROM p_run_id
     OR v_request.status<>'claimed' OR v_request.lease_token<>p_lease_token THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001';
  END IF;
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id;
  IF NOT FOUND OR v_run.scheduled_phase IS DISTINCT FROM 'intraday'
     OR v_run.scheduled_market_date IS NULL THEN
    RAISE EXCEPTION 'quiet outcome requires scheduled intraday run' USING ERRCODE='22023';
  END IF;
  SELECT publication.idempotency_key INTO v_evaluation_request_id
    FROM public.market_publications publication
    WHERE publication.run_id=p_run_id
      AND publication.idempotency_key=p_request_id
      AND publication.market_date=v_run.scheduled_market_date
      AND publication.phase='intraday' AND publication.status='suppressed'
      AND publication.telegram_message_ids='[]'::jsonb
    ORDER BY publication.created_at,publication.id
    LIMIT 1;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'suppressed evaluation receipt unavailable' USING ERRCODE='55000';
  END IF;
  SELECT * INTO v_existing FROM public.market_run_terminal_outcomes
  WHERE run_id=p_run_id;
  IF FOUND THEN
    IF v_existing.evaluation_request_id IS DISTINCT FROM v_evaluation_request_id
       OR v_existing.outcome IS DISTINCT FROM p_outcome THEN
      RAISE EXCEPTION 'market run outcome mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object(
      'run_id',v_existing.run_id,'outcome',v_existing.outcome,'duplicate',true
    );
  END IF;
  INSERT INTO public.market_run_terminal_outcomes(
    run_id,evaluation_request_id,outcome
  ) VALUES(p_run_id,v_evaluation_request_id,p_outcome) RETURNING * INTO v_existing;
  RETURN jsonb_build_object(
    'run_id',v_existing.run_id,'outcome',v_existing.outcome,'duplicate',false
  );
END;
$$;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
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
    'evidence_facts',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'candidate_key',candidate->>'candidate_key',
        'evidence_id',item.id,
        'category',CASE
          WHEN item.metadata->>'evidence_category' IN ('quote','fundamentals','technicals','news','event','macro','sector')
            THEN item.metadata->>'evidence_category'
          WHEN item.provider='sec_edgar' THEN 'fundamentals'
          WHEN item.provider IN ('fred','eia','bls','bea') THEN 'macro'
          WHEN item.provider IN ('white_house','doe','dod','federal_register') THEN 'event'
          WHEN item.provider IN ('gdelt','finnhub') THEN 'news'
          WHEN item.provider='yahoo' THEN 'quote'
          ELSE 'unknown' END,
        'source',item.provider,
        'source_status',CASE WHEN receipt.status IN ('succeeded','cache_hit') THEN receipt.status ELSE 'failed' END,
        'authority',CASE
          WHEN item.metadata->>'authority'='official' AND item.provider IN ('sec_edgar','fred','eia','bls','bea','white_house','doe','dod','federal_register') THEN 'official'
          WHEN item.provider IN ('yahoo','alpha_vantage') THEN 'market_data'
          WHEN item.provider IN ('gdelt','finnhub') THEN 'reported'
          ELSE 'unverified' END,
        'published_at',item.published_at,
        'retrieved_at',receipt.retrieved_at,
        'expires_at',receipt.expires_at,
        'reference',item.canonical_url,
        'normalized_text',item.normalized_text,
        'exposure_kind',CASE WHEN item.metadata->>'exposure_kind' IN ('filing','contract','backlog','revenue','capacity','official_fund') THEN item.metadata->>'exposure_kind' ELSE NULL END,
        'relationship_eligible',EXISTS (
          SELECT 1 FROM public.market_candidate_rankings ranking
          WHERE ranking.run_id=packet.run_id AND ranking.candidate_key=candidate->>'candidate_key'
            AND ranking.qualified AND ranking.exposure_item_ids ? item.id::text
        ),
        'claim_key',item.metadata->>'claim_key',
        'claim_polarity',CASE WHEN item.metadata->>'claim_polarity' IN ('affirmed','denied') THEN item.metadata->>'claim_polarity' ELSE NULL END
      ) ORDER BY candidate->>'candidate_key',item.id)
      FROM jsonb_array_elements(packet.packet->'candidates') candidate
      CROSS JOIN LATERAL jsonb_array_elements_text(candidate->'evidence_ids') evidence_id
      JOIN public.market_intelligence_run_items run_item ON run_item.run_id=packet.run_id
        AND run_item.source_item_id=evidence_id::uuid AND run_item.disposition='accepted'
      JOIN public.market_source_items item ON item.id=run_item.source_item_id
      JOIN public.market_source_receipts receipt ON receipt.id=run_item.source_receipt_id
    ),'[]'::jsonb),
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

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
CREATE OR REPLACE FUNCTION public.read_market_report_decisions(
  p_run_id UUID, p_packet_id UUID, p_decision_ids JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_result JSONB;
BEGIN
  IF jsonb_typeof(p_decision_ids) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_decision_ids) NOT BETWEEN 1 AND 96 THEN
    RAISE EXCEPTION 'invalid report decision IDs' USING ERRCODE='22023';
  END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'evaluation_id',evaluation.id,'candidate_id',evaluation.candidate_id,
    'run_id',evaluation.run_id,'packet_id',packet.id,'packet_hash',packet.packet_hash,
    'ticker',evaluation.normalized->>'ticker','status',evaluation.policy_status,
    'final_action',evaluation.final_action,
    'final_alert_urgency',CASE WHEN evaluation.policy_status='approved' AND evaluation.final_action='hold'
      THEN evaluation.normalized->'final_alert_urgency' ELSE 'null'::jsonb END,
    'approved_terms',CASE WHEN evaluation.policy_status='approved' AND evaluation.final_action IN ('buy','add','reduce','sell')
      THEN evaluation.normalized->'approved_terms' ELSE 'null'::jsonb END
  ) ORDER BY evaluation.id),'[]'::jsonb) INTO v_result
  FROM public.market_evidence_packets packet
  JOIN public.decision_evaluations evaluation ON evaluation.run_id=packet.run_id
    AND evaluation.analyst->>'packet_id'=packet.id::text
    AND evaluation.policy_version=packet.policy_version
  WHERE packet.id=p_packet_id AND packet.run_id=p_run_id AND packet.status='completed'
    AND p_decision_ids ? evaluation.id::text;
  IF jsonb_array_length(v_result)<>jsonb_array_length(p_decision_ids) THEN
    RAISE EXCEPTION 'report decision provenance mismatch' USING ERRCODE='22023';
  END IF;
  RETURN v_result;
END;
$$;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
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
     OR char_length(p_report->>'rendered_text')>14000
     OR p_report->>'rendered_hash' !~ '^[0-9a-f]{64}$'
     OR p_report->>'rendered_hash' <> encode(extensions.digest(
       convert_to(p_report->>'rendered_text','UTF8'),'sha256'
     ),'hex')
     OR NOT (p_report->'report' ?& ARRAY[
       'source_ids','policy_decision_ids','comparison_ids'
     ])
     OR jsonb_typeof(p_report->'report'->'source_ids') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_report->'report'->'policy_decision_ids') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_report->'report'->'comparison_ids') IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_report->'report'->'source_ids') = 0
     OR jsonb_array_length(p_report->'report'->'policy_decision_ids') = 0
     OR jsonb_array_length(p_report->'report'->'comparison_ids') > 96
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

-- Triggers are installed after their final routine definitions.

-- Final-state source: sql/migrations/20260914_provider_evidence_integrity.sql
DROP TRIGGER IF EXISTS market_source_item_provenance_append_only ON public.market_source_item_provenance;

-- Final-state source: sql/migrations/20260914_provider_evidence_integrity.sql
CREATE TRIGGER market_source_item_provenance_append_only BEFORE UPDATE OR DELETE
  ON public.market_source_item_provenance FOR EACH ROW EXECUTE FUNCTION public.reject_market_intelligence_mutation();

-- Final-state source: sql/migrations/20260915_market_source_item_reuse.sql
DROP TRIGGER IF EXISTS market_source_items_reuse_immutable ON public.market_source_items;

-- Final-state source: sql/migrations/20260915_market_source_item_reuse.sql
CREATE TRIGGER market_source_items_reuse_immutable BEFORE INSERT ON public.market_source_items
FOR EACH ROW EXECUTE FUNCTION public.reuse_market_source_item_if_immutable();

-- Final-state source: sql/migrations/20260916_run_scoped_request_provenance.sql
DROP TRIGGER IF EXISTS market_run_source_item_provenance_append_only ON public.market_run_source_item_provenance;

-- Final-state source: sql/migrations/20260916_run_scoped_request_provenance.sql
CREATE TRIGGER market_run_source_item_provenance_append_only BEFORE UPDATE OR DELETE ON public.market_run_source_item_provenance FOR EACH ROW EXECUTE FUNCTION public.reject_market_intelligence_mutation();

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
DROP TRIGGER IF EXISTS market_intelligence_runs_append_only ON public.market_intelligence_runs;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
CREATE TRIGGER market_intelligence_runs_append_only BEFORE UPDATE OR DELETE ON public.market_intelligence_runs
FOR EACH ROW EXECUTE FUNCTION public.initialize_market_intelligence_window();

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
CREATE TRIGGER market_intelligence_collection_completions_append_only BEFORE UPDATE OR DELETE
ON public.market_intelligence_collection_completions FOR EACH ROW EXECUTE FUNCTION public.reject_market_intelligence_mutation();

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
DROP TRIGGER IF EXISTS transactions_advance_cash_ledger ON public.transactions;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE TRIGGER transactions_advance_cash_ledger
AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.transactions
FOR EACH STATEMENT EXECUTE FUNCTION public.advance_portfolio_cash_ledger();

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
DROP TRIGGER IF EXISTS reconciled_cash_snapshots_append_only
  ON public.reconciled_cash_snapshots;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE TRIGGER reconciled_cash_snapshots_append_only BEFORE UPDATE OR DELETE
ON public.reconciled_cash_snapshots FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
DROP TRIGGER IF EXISTS market_run_terminal_outcomes_append_only
  ON public.market_run_terminal_outcomes;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
CREATE TRIGGER market_run_terminal_outcomes_append_only BEFORE UPDATE OR DELETE
ON public.market_run_terminal_outcomes FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
DROP TRIGGER IF EXISTS market_policy_comparisons_append_only ON public.market_policy_comparisons;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
CREATE TRIGGER market_policy_comparisons_append_only BEFORE UPDATE OR DELETE
ON public.market_policy_comparisons FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();

-- Explicit grants are required under Supabase public default privileges.

-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
REVOKE ALL ON TABLE public.market_report_publications FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
REVOKE ALL ON FUNCTION public.create_market_report_publication(UUID, UUID, TEXT, DATE, TEXT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
REVOKE ALL ON FUNCTION public.claim_market_report_publication(TEXT) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
REVOKE ALL ON FUNCTION public.finish_market_report_publication(TEXT, UUID, TEXT, JSONB, TEXT) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
GRANT EXECUTE ON FUNCTION public.create_market_report_publication(UUID, UUID, TEXT, DATE, TEXT, TEXT, TEXT) TO service_role;

-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
GRANT EXECUTE ON FUNCTION public.claim_market_report_publication(TEXT) TO service_role;

-- Final-state source: sql/migrations/20260910_delivery_outbox.sql
GRANT EXECUTE ON FUNCTION public.finish_market_report_publication(TEXT, UUID, TEXT, JSONB, TEXT) TO service_role;

-- Final-state source: sql/migrations/20260911_command_acknowledgements.sql
REVOKE ALL ON TABLE public.portfolio_command_acknowledgements FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260911_command_acknowledgements.sql
REVOKE ALL ON FUNCTION public.apply_portfolio_command_with_acknowledgement(TEXT, UUID, BIGINT, BIGINT, BIGINT) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260911_command_acknowledgements.sql
GRANT EXECUTE ON FUNCTION public.apply_portfolio_command_with_acknowledgement(TEXT, UUID, BIGINT, BIGINT, BIGINT) TO service_role;

-- Final-state source: sql/migrations/202609120001_delivery_recovery_claims.sql
REVOKE ALL ON FUNCTION public.claim_market_gateway_request(UUID, TEXT, UUID) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/202609120001_delivery_recovery_claims.sql
GRANT EXECUTE ON FUNCTION public.claim_market_gateway_request(UUID, TEXT, UUID) TO service_role;

-- Final-state source: sql/migrations/20260913_command_acknowledgement_lease.sql
REVOKE ALL ON FUNCTION public.apply_portfolio_command_with_acknowledgement(TEXT, UUID, BIGINT, BIGINT, BIGINT) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260913_command_acknowledgement_lease.sql
REVOKE ALL ON FUNCTION public.finish_portfolio_command_acknowledgement(UUID, BIGINT, UUID, TEXT, TEXT) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260913_command_acknowledgement_lease.sql
GRANT EXECUTE ON FUNCTION public.apply_portfolio_command_with_acknowledgement(TEXT, UUID, BIGINT, BIGINT, BIGINT) TO service_role;

-- Final-state source: sql/migrations/20260913_command_acknowledgement_lease.sql
GRANT EXECUTE ON FUNCTION public.finish_portfolio_command_acknowledgement(UUID, BIGINT, UUID, TEXT, TEXT) TO service_role;

-- Final-state source: sql/migrations/20260914_provider_evidence_integrity.sql
REVOKE ALL ON TABLE public.market_source_item_provenance FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260914_provider_evidence_integrity.sql
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260914_provider_evidence_integrity.sql
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) TO service_role;

-- Final-state source: sql/migrations/20260915_market_source_item_reuse.sql
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260915_market_source_item_reuse.sql
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) TO service_role;

-- Final-state source: sql/migrations/20260916_run_scoped_request_provenance.sql
REVOKE ALL ON TABLE public.market_run_source_item_provenance FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260916_run_scoped_request_provenance.sql
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260916_run_scoped_request_provenance.sql
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID, UUID, JSONB) TO service_role;

-- Final-state source: sql/migrations/20260917_collection_checkpoint_hydration.sql
REVOKE ALL ON public.market_collection_checkpoints FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260918_durable_collection_controller.sql
REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260918_durable_collection_controller.sql
REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260918_durable_collection_controller.sql
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) TO service_role;

-- Final-state source: sql/migrations/20260918_durable_collection_controller.sql
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;

-- Final-state source: sql/migrations/20260919_controller_lineage_and_run_binding.sql
REVOKE ALL ON public.market_collection_checkpoint_history FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260919_controller_lineage_and_run_binding.sql
REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260919_controller_lineage_and_run_binding.sql
REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260919_controller_lineage_and_run_binding.sql
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) TO service_role;

-- Final-state source: sql/migrations/20260919_controller_lineage_and_run_binding.sql
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;

-- Final-state source: sql/migrations/20260920_terminal_checkpoint_lineage.sql
REVOKE ALL ON public.market_checkpoint_receipt_lineage FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260920_terminal_checkpoint_lineage.sql
REVOKE ALL ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260920_terminal_checkpoint_lineage.sql
REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260920_terminal_checkpoint_lineage.sql
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB) TO service_role;

-- Final-state source: sql/migrations/20260920_terminal_checkpoint_lineage.sql
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;

-- Final-state source: sql/migrations/20260920_terminal_checkpoint_lineage.sql
REVOKE ALL ON FUNCTION public.record_market_intelligence(UUID,UUID,JSONB) FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260920_terminal_checkpoint_lineage.sql
GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID,UUID,JSONB) TO service_role;

-- Final-state source: sql/migrations/20260921_intelligence_context_inputs.sql
REVOKE ALL ON public.market_intelligence_context_inputs FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
GRANT EXECUTE ON FUNCTION public.read_market_evidence_packet(UUID, UUID) TO service_role;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
GRANT EXECUTE ON FUNCTION public.record_market_report(UUID, TEXT, JSONB) TO service_role;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
GRANT EXECUTE ON FUNCTION public.record_market_learning(UUID, JSONB) TO service_role;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
REVOKE ALL ON public.market_intelligence_collection_completions FROM PUBLIC,anon,authenticated,service_role;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
REVOKE ALL ON FUNCTION public.record_market_intelligence_legacy(UUID,UUID,JSONB),public.record_market_intelligence_provider_v2(UUID,UUID,JSONB) FROM PUBLIC,anon,authenticated,service_role;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
REVOKE ALL ON FUNCTION public.read_market_intelligence_completion(UUID,UUID),public.record_market_intelligence(UUID,UUID,JSONB),public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB),public.checkpoint_market_intelligence_collection(UUID,JSONB) FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
GRANT EXECUTE ON FUNCTION public.read_market_intelligence_completion(UUID,UUID),public.record_market_intelligence(UUID,UUID,JSONB),public.start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB),public.checkpoint_market_intelligence_collection(UUID,JSONB) TO service_role;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
REVOKE ALL ON public.market_intelligence_context_inputs FROM PUBLIC,anon,authenticated,service_role;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
REVOKE ALL ON public.market_intelligence_quote_attempts FROM PUBLIC,anon,authenticated,service_role;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
REVOKE ALL ON FUNCTION public.claim_market_intelligence_quote(UUID,JSONB),public.record_market_intelligence_quote(UUID,UUID,JSONB,JSONB) FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
GRANT EXECUTE ON FUNCTION public.claim_market_intelligence_quote(UUID,JSONB),public.record_market_intelligence_quote(UUID,UUID,JSONB,JSONB) TO service_role;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
REVOKE ALL ON FUNCTION public.refresh_market_intelligence_context(UUID) FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260922_collection_controller_contract.sql
GRANT EXECUTE ON FUNCTION public.refresh_market_intelligence_context(UUID) TO service_role;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
REVOKE ALL ON public.market_scheduled_phase_deadlines FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
REVOKE ALL ON FUNCTION public.start_market_analysis_run(UUID,UUID,TEXT,DATE) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
REVOKE ALL ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
GRANT EXECUTE ON FUNCTION public.start_market_analysis_run(UUID,UUID,TEXT,DATE) TO service_role;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;

-- Final-state source: sql/migrations/20260923_scheduled_run_lifecycle.sql
GRANT EXECUTE ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) TO stock_agent_dashboard;

-- Final-state source: sql/migrations/20260924_scheduled_lifecycle_followup.sql
REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID),public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260924_scheduled_lifecycle_followup.sql
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;

-- Final-state source: sql/migrations/20260924_scheduled_lifecycle_followup.sql
GRANT EXECUTE ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ) TO stock_agent_dashboard;

-- Final-state source: sql/migrations/20260925_scheduled_report_origins.sql
REVOKE ALL ON public.market_report_request_origins FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260925_scheduled_report_origins.sql
REVOKE ALL ON FUNCTION public.record_market_report_origin(UUID,UUID,UUID,DATE,TEXT,UUID,UUID,TEXT,TEXT) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260925_scheduled_report_origins.sql
REVOKE ALL ON FUNCTION public.finish_market_analysis_run(UUID) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20260925_scheduled_report_origins.sql
GRANT EXECUTE ON FUNCTION public.record_market_report_origin(UUID,UUID,UUID,DATE,TEXT,UUID,UUID,TEXT,TEXT) TO service_role;

-- Final-state source: sql/migrations/20260925_scheduled_report_origins.sql
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;

-- Final-state source: sql/migrations/20260926_report_suppression_reasons.sql
REVOKE ALL ON FUNCTION public.suppress_market_report_publication(TEXT,TEXT) FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260926_report_suppression_reasons.sql
GRANT EXECUTE ON FUNCTION public.suppress_market_report_publication(TEXT,TEXT) TO service_role;

-- Final-state source: sql/migrations/20260926_report_suppression_reasons.sql
GRANT SELECT (report_id,idempotency_key,status,telegram_message_ids,telegram_accepted_at,suppression_reason)
  ON public.market_report_publications TO stock_agent_dashboard;

-- Final-state source: sql/migrations/20260926_report_suppression_reasons.sql
DROP POLICY IF EXISTS owner_dashboard_select_report_publications ON public.market_report_publications;

-- Final-state source: sql/migrations/20260926_report_suppression_reasons.sql
CREATE POLICY owner_dashboard_select_report_publications ON public.market_report_publications
  FOR SELECT TO stock_agent_dashboard USING (true);

-- Final-state source: sql/migrations/20260927_release_evidence_reader.sql
GRANT stock_agent_release_reader TO stock_agent_release_reader_runtime;

-- Final-state source: sql/migrations/20260927_release_evidence_reader.sql
GRANT USAGE ON SCHEMA public,extensions TO stock_agent_release_reader;

-- Final-state source: sql/migrations/20260927_release_evidence_reader.sql
DO $$
DECLARE name TEXT;
BEGIN
  FOREACH name IN ARRAY ARRAY[
    'holdings','transactions','portfolio_commands','analysis_runs','market_evidence_packets','market_reports',
    'market_report_publications','market_intelligence_runs','market_intelligence_collection_completions',
    'market_intelligence_run_events','market_collection_checkpoints','market_events','market_candidate_rankings',
    'market_gateway_requests','market_report_request_origins','market_publications',
    'market_source_quota_reservations','market_source_receipts','market_alert_drafts','market_alert_events','market_alert_actions'
  ] LOOP
    EXECUTE format('REVOKE ALL ON public.%I FROM stock_agent_release_reader,stock_agent_release_reader_runtime',name);
    EXECUTE format('GRANT SELECT ON public.%I TO stock_agent_release_reader',name);
    EXECUTE format('DROP POLICY IF EXISTS release_evidence_select ON public.%I',name);
    EXECUTE format('CREATE POLICY release_evidence_select ON public.%I FOR SELECT TO stock_agent_release_reader USING (true)',name);
  END LOOP;
  IF to_regclass('supabase_migrations.schema_migrations') IS NOT NULL THEN
    GRANT USAGE ON SCHEMA supabase_migrations TO stock_agent_release_reader;
    GRANT SELECT(version,statements) ON supabase_migrations.schema_migrations TO stock_agent_release_reader;
  END IF;
END; $$;

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
REVOKE ALL PRIVILEGES ON TABLE
  public.suggestion_grades,
  public.lessons,
  public.daily_snapshots
FROM stock_agent_dashboard;

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
GRANT SELECT (id, suggestion_id, graded_at, result, price_then, price_later,
              horizon_days, note, benchmark_ticker, stock_return_pct,
              benchmark_return_pct, excess_return_pct, mfe_pct, mae_pct,
              entry_hit_at, stop_hit_at, target_hit_at, invalidation_hit_at,
              coverage_status, horizon_sessions, policy_version, final_action,
              direction_success)
  ON public.suggestion_grades TO stock_agent_dashboard;

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
GRANT SELECT (id, entry_date, category, content, created_at)
  ON public.lessons TO stock_agent_dashboard;

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
GRANT SELECT (id, snap_date, ticker, close, day_move_pct, rsi14, sma50, sma200, macd_hist)
  ON public.daily_snapshots TO stock_agent_dashboard;

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
DROP POLICY IF EXISTS owner_dashboard_select_grades ON public.suggestion_grades;

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
CREATE POLICY owner_dashboard_select_grades ON public.suggestion_grades
  FOR SELECT TO stock_agent_dashboard USING (true);

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
DROP POLICY IF EXISTS owner_dashboard_select_lessons ON public.lessons;

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
CREATE POLICY owner_dashboard_select_lessons ON public.lessons
  FOR SELECT TO stock_agent_dashboard USING (true);

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
DROP POLICY IF EXISTS owner_dashboard_select_snapshots ON public.daily_snapshots;

-- Final-state source: sql/migrations/20260928_weekly_audit_read_scope.sql
CREATE POLICY owner_dashboard_select_snapshots ON public.daily_snapshots
  FOR SELECT TO stock_agent_dashboard USING (true);

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
REVOKE ALL ON TABLE public.portfolio_cash_ledger_state,
  public.reconciled_cash_snapshots,public.market_run_terminal_outcomes
  FROM PUBLIC,anon,authenticated,service_role;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
REVOKE ALL ON FUNCTION public.advance_portfolio_cash_ledger(),
  public.read_portfolio_cash_ledger_watermark(),
  public.record_reconciled_cash_snapshot(UUID,TIMESTAMPTZ,TIMESTAMPTZ,BIGINT,JSONB),
  public.read_reconciled_cash_snapshot(TIMESTAMPTZ),
  public.apply_market_decision_bundle_with_cash_snapshot(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT),
  public.record_market_run_outcome(UUID,UUID,UUID,TEXT),
  public.finish_market_analysis_run(UUID)
  FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
REVOKE EXECUTE ON FUNCTION public.apply_market_decision_bundle(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB)
  FROM service_role;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
GRANT EXECUTE ON FUNCTION public.record_reconciled_cash_snapshot(UUID,TIMESTAMPTZ,TIMESTAMPTZ,BIGINT,JSONB) TO service_role;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
GRANT EXECUTE ON FUNCTION public.read_portfolio_cash_ledger_watermark() TO service_role;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
GRANT EXECUTE ON FUNCTION public.read_reconciled_cash_snapshot(TIMESTAMPTZ) TO service_role;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
GRANT EXECUTE ON FUNCTION public.apply_market_decision_bundle_with_cash_snapshot(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT) TO service_role;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
GRANT EXECUTE ON FUNCTION public.record_market_run_outcome(UUID,UUID,UUID,TEXT) TO service_role;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;

-- Final-state source: sql/migrations/20260930_provider_attempt_and_recovery_closure.sql
REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB)
  FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20260930_provider_attempt_and_recovery_closure.sql
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB)
  TO service_role;

-- Final-state source: sql/migrations/20260930_provider_attempt_and_recovery_closure.sql
DO $$
DECLARE name TEXT;
BEGIN
  FOREACH name IN ARRAY ARRAY[
    'portfolio_command_acknowledgements','market_policy_config',
    'portfolio_cash_ledger_state','reconciled_cash_snapshots',
    'market_run_terminal_outcomes','market_collection_checkpoint_history'
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

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
REVOKE ALL ON public.market_policy_comparisons FROM PUBLIC, anon, authenticated, service_role;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
REVOKE ALL ON FUNCTION public.read_market_evidence_packet(UUID, UUID) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
GRANT EXECUTE ON FUNCTION public.read_market_evidence_packet(UUID, UUID) TO service_role;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
REVOKE ALL ON FUNCTION public.read_market_report_decisions(UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
GRANT EXECUTE ON FUNCTION public.read_market_report_decisions(UUID, UUID, JSONB) TO service_role;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
REVOKE ALL ON FUNCTION public.record_market_report(UUID, TEXT, JSONB) FROM PUBLIC, anon, authenticated;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
GRANT EXECUTE ON FUNCTION public.record_market_report(UUID, TEXT, JSONB) TO service_role;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
REVOKE ALL ON public.decision_evaluations, public.market_policy_comparisons
  FROM stock_agent_release_reader, stock_agent_release_reader_runtime;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
GRANT SELECT ON public.decision_evaluations, public.market_policy_comparisons TO stock_agent_release_reader;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
DROP POLICY IF EXISTS release_evidence_select ON public.decision_evaluations;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
CREATE POLICY release_evidence_select ON public.decision_evaluations
  FOR SELECT TO stock_agent_release_reader USING (true);

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
DROP POLICY IF EXISTS release_evidence_select ON public.market_policy_comparisons;

-- Final-state source: sql/migrations/20261001_immutable_history_closure.sql
CREATE POLICY release_evidence_select ON public.market_policy_comparisons
  FOR SELECT TO stock_agent_release_reader USING (true);

-- Final-state source: sql/migrations/20261002_provider_registry_and_release_ledger_recovery.sql
REVOKE ALL ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB)
  FROM PUBLIC,anon,authenticated;

-- Final-state source: sql/migrations/20261002_provider_registry_and_release_ledger_recovery.sql
GRANT EXECUTE ON FUNCTION public.checkpoint_market_intelligence_collection(UUID,JSONB)
  TO service_role;

-- Final-state source: sql/migrations/20261002_provider_registry_and_release_ledger_recovery.sql
REVOKE ALL ON public.stock_agent_release_migration_ledger
  FROM PUBLIC,anon,authenticated,stock_agent_release_reader,stock_agent_release_reader_runtime;

-- Final-state source: sql/migrations/20261002_provider_registry_and_release_ledger_recovery.sql
GRANT SELECT ON public.stock_agent_release_migration_ledger TO stock_agent_release_reader;

-- Final-state source: sql/migrations/20261002_provider_registry_and_release_ledger_recovery.sql
DROP POLICY IF EXISTS release_evidence_select ON public.stock_agent_release_migration_ledger;

-- Final-state source: sql/migrations/20261002_provider_registry_and_release_ledger_recovery.sql
CREATE POLICY release_evidence_select ON public.stock_agent_release_migration_ledger
  FOR SELECT TO stock_agent_release_reader USING (true);

-- Supabase's public default privileges can grant service_role direct ledger
-- writes. Application credentials must never manufacture deployment receipts.
REVOKE ALL ON TABLE public.stock_agent_release_migration_ledger FROM service_role;

-- Only new control state is initialized; no historical facts are backfilled.

-- Final-state source: sql/migrations/20260924_scheduled_lifecycle_followup.sql
INSERT INTO public.market_scheduled_phase_deadlines(phase,deadline_local,grace_minutes,effective_on)
VALUES
  ('pre-market','06:30',15,timezone('America/Chicago', statement_timestamp())::date),
  ('intraday','12:00',15,timezone('America/Chicago', statement_timestamp())::date),
  ('post-market','15:10',15,timezone('America/Chicago', statement_timestamp())::date)
ON CONFLICT (phase,effective_on) DO NOTHING;

-- Final-state source: sql/migrations/20260929_policy_lifecycle_closure.sql
INSERT INTO public.portfolio_cash_ledger_state(singleton) VALUES(true)
ON CONFLICT(singleton) DO NOTHING;
