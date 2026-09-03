-- Provider-neutral run evidence. Model submissions reference these server-owned facts by digest.

CREATE TABLE app.run_evidence (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  run_id UUID NOT NULL,
  evidence_id TEXT NOT NULL CHECK (char_length(evidence_id) BETWEEN 1 AND 100),
  source_run_id UUID,
  category TEXT NOT NULL CHECK (category IN (
    'market_snapshot', 'source_search', 'filing', 'fundamentals', 'news',
    'technicals', 'corporate_action', 'issuer', 'exchange'
  )),
  source_identifier TEXT NOT NULL CHECK (char_length(source_identifier) BETWEEN 1 AND 200),
  reference_identifier TEXT CHECK (
    reference_identifier IS NULL OR char_length(reference_identifier) BETWEEN 1 AND 500
  ),
  observed_at TIMESTAMPTZ,
  retrieved_at TIMESTAMPTZ NOT NULL,
  revalidated_at TIMESTAMPTZ,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  claims JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(claims) = 'array' AND jsonb_array_length(claims) <= 10
    AND octet_length(claims::text) <= 6000
  ),
  status TEXT NOT NULL CHECK (status IN (
    'fresh', 'stale', 'conflicting', 'missing', 'no_new_material_evidence',
    'suspected', 'needs_review', 'clear'
  )),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, run_id, evidence_id),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE CASCADE,
  FOREIGN KEY (owner_id, source_run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE RESTRICT,
  CHECK (revalidated_at IS NULL OR revalidated_at >= retrieved_at)
);

CREATE TABLE app.source_search_receipts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  run_id UUID NOT NULL,
  searched_at TIMESTAMPTZ NOT NULL,
  categories TEXT[] NOT NULL CHECK (
    cardinality(categories) BETWEEN 1 AND 8
    AND categories <@ ARRAY['filing','fundamentals','news','issuer','exchange','sector','macro']::text[]
  ),
  sources JSONB NOT NULL CHECK (
    jsonb_typeof(sources) = 'array' AND jsonb_array_length(sources) BETWEEN 1 AND 20
    AND octet_length(sources::text) <= 4000
  ),
  result_status TEXT NOT NULL CHECK (
    result_status IN ('material_evidence_found', 'no_new_material_evidence', 'source_unavailable')
  ),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE CASCADE
);

ALTER TABLE app.run_evidence OWNER TO stock_agent_migration_owner;
ALTER TABLE app.source_search_receipts OWNER TO stock_agent_migration_owner;

CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.run_evidence
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.source_search_receipts
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

ALTER TABLE app.run_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.run_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE app.source_search_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.source_search_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY run_evidence_executor_all ON app.run_evidence
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY source_search_receipts_executor_all ON app.source_search_receipts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

REVOKE ALL ON app.run_evidence, app.source_search_receipts
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE INDEX run_evidence_run_category_idx
  ON app.run_evidence(owner_id, run_id, category, retrieved_at DESC);
CREATE INDEX source_search_receipts_run_idx
  ON app.source_search_receipts(owner_id, run_id, searched_at DESC);

CREATE TABLE app.market_quote_cache (
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'),
  price NUMERIC(24, 8) NOT NULL CHECK (price > 0),
  previous_close NUMERIC(24, 8) CHECK (previous_close IS NULL OR previous_close > 0),
  provider TEXT NOT NULL CHECK (char_length(provider) BETWEEN 1 AND 80),
  source_timestamp TIMESTAMPTZ NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL,
  session TEXT NOT NULL CHECK (session IN ('PRE','REGULAR','POST','CLOSED')),
  adjustment_status TEXT NOT NULL CHECK (
    adjustment_status IN ('raw','adjusted','corporate_action_pending')
  ),
  status TEXT NOT NULL CHECK (
    status IN ('fresh','delayed','stale','conflicting','unavailable')
  ),
  content_digest TEXT NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
  conflict_basis_points INT CHECK (conflict_basis_points IS NULL OR conflict_basis_points >= 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id, ticker),
  CHECK (source_timestamp <= retrieved_at + interval '5 minutes')
);

CREATE TABLE app.corporate_action_states (
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'),
  state TEXT NOT NULL CHECK (state IN ('clear','suspected','needs_review')),
  event_type TEXT CHECK (
    event_type IS NULL OR event_type IN ('split','reverse_split','symbol_change','merger','delisting')
  ),
  ratio NUMERIC(24, 8) CHECK (ratio IS NULL OR ratio > 0),
  source_identifier TEXT CHECK (
    source_identifier IS NULL OR char_length(source_identifier) BETWEEN 1 AND 200
  ),
  source_reference TEXT CHECK (
    source_reference IS NULL OR char_length(source_reference) BETWEEN 1 AND 500
  ),
  reason_code TEXT NOT NULL CHECK (
    reason_code IN ('confirmed_and_normalized','unverified_corporate_action',
                    'corporate_action_conflict','no_corporate_action_signal')
  ),
  detected_at TIMESTAMPTZ NOT NULL,
  reviewed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id, ticker),
  CHECK (state <> 'clear' OR reason_code IN ('confirmed_and_normalized','no_corporate_action_signal'))
);

ALTER TABLE app.market_quote_cache OWNER TO stock_agent_migration_owner;
ALTER TABLE app.corporate_action_states OWNER TO stock_agent_migration_owner;
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.market_quote_cache
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.corporate_action_states
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

ALTER TABLE app.market_quote_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.market_quote_cache FORCE ROW LEVEL SECURITY;
ALTER TABLE app.corporate_action_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.corporate_action_states FORCE ROW LEVEL SECURITY;
CREATE POLICY market_quote_cache_owner_select ON app.market_quote_cache
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY market_quote_cache_executor_all ON app.market_quote_cache
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY corporate_action_states_owner_select ON app.corporate_action_states
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY corporate_action_states_executor_all ON app.corporate_action_states
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY radar_owner_quote_select ON app.radar
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

REVOKE ALL ON app.market_quote_cache, app.corporate_action_states
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT SELECT (owner_id, ticker, price, previous_close, provider, source_timestamp,
              retrieved_at, session, adjustment_status, status, conflict_basis_points)
  ON app.market_quote_cache TO authenticated;
GRANT SELECT (owner_id, ticker, state, event_type, ratio, reason_code, detected_at, reviewed_at)
  ON app.corporate_action_states TO authenticated;
GRANT SELECT (owner_id, ticker) ON app.radar TO authenticated;

CREATE VIEW api.market_quotes WITH (security_invoker = true) AS
SELECT quote.ticker,
       quote.price,
       quote.previous_close,
       quote.provider,
       quote.source_timestamp AS as_of,
       quote.retrieved_at,
       quote.session,
       quote.adjustment_status,
       quote.status,
       quote.conflict_basis_points,
       coalesce(action.state, 'clear') AS corporate_action_state,
       coalesce(action.state IN ('suspected','needs_review'), false) AS alerts_suppressed
FROM app.market_quote_cache AS quote
LEFT JOIN app.corporate_action_states AS action
  ON action.owner_id = quote.owner_id AND action.ticker = quote.ticker
WHERE EXISTS (
  SELECT 1 FROM app.holdings AS holding
  WHERE holding.owner_id = quote.owner_id AND holding.ticker = quote.ticker
) OR EXISTS (
  SELECT 1 FROM app.radar AS watched
  WHERE watched.owner_id = quote.owner_id AND watched.ticker = quote.ticker
);
ALTER VIEW api.market_quotes OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.market_quotes FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.market_quotes TO authenticated;

CREATE INDEX market_quote_cache_freshness_idx
  ON app.market_quote_cache(owner_id, status, retrieved_at DESC);
CREATE INDEX corporate_action_states_review_idx
  ON app.corporate_action_states(owner_id, state, updated_at DESC);

-- Provider V2 gateway. The Edge function has no table privileges: every operation enters
-- through one owner-resolving, transaction-scoped SECURITY DEFINER function below.

ALTER TABLE app.market_gateway_requests
  ADD COLUMN connection_id UUID,
  ADD COLUMN input_digest TEXT CHECK (
    input_digest IS NULL OR input_digest ~ '^[0-9a-f]{64}$'
  );
ALTER TABLE app.market_gateway_requests
  ADD CONSTRAINT market_gateway_requests_owner_connection_fkey
  FOREIGN KEY (owner_id, connection_id)
  REFERENCES app.agent_connections(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE app.market_gateway_requests
  DROP CONSTRAINT market_gateway_requests_operation_check;
ALTER TABLE app.market_gateway_requests
  ADD CONSTRAINT market_gateway_requests_operation_check CHECK (operation IN (
    'start_run','read_context','record_artifacts','grade_due_decisions',
    'evaluate_and_publish','finish_run','read_bounded_context','submit_analysis',
    'record_permitted_artifacts'
  ));

ALTER TABLE app.analysis_runs
  ADD COLUMN connection_id UUID,
  ADD COLUMN market_date DATE,
  ADD COLUMN provider TEXT CHECK (provider IS NULL OR char_length(provider) BETWEEN 1 AND 50),
  ADD COLUMN model TEXT CHECK (model IS NULL OR char_length(model) BETWEEN 1 AND 100);
ALTER TABLE app.analysis_runs
  ADD CONSTRAINT analysis_runs_owner_connection_fkey
  FOREIGN KEY (owner_id, connection_id)
  REFERENCES app.agent_connections(owner_id, id) ON DELETE RESTRICT;

CREATE TABLE app.agent_analysis_submissions (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  run_id UUID NOT NULL,
  request_id UUID NOT NULL,
  provider TEXT NOT NULL CHECK (char_length(provider) BETWEEN 1 AND 50),
  model TEXT NOT NULL CHECK (char_length(model) BETWEEN 1 AND 100),
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  market_date DATE NOT NULL,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 65536),
  payload_digest TEXT NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status IN ('accepted','quarantined')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, request_id),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE CASCADE
);
ALTER TABLE app.agent_analysis_submissions OWNER TO stock_agent_migration_owner;
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.agent_analysis_submissions
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
ALTER TABLE app.agent_analysis_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.agent_analysis_submissions FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_analysis_submissions_executor_all ON app.agent_analysis_submissions
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
REVOKE ALL ON app.agent_analysis_submissions
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE INDEX agent_analysis_submissions_run_idx
  ON app.agent_analysis_submissions(owner_id, run_id, created_at DESC);

ALTER TABLE app.market_publications
  ADD COLUMN rendered_parts JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(rendered_parts) = 'array'
    AND jsonb_array_length(rendered_parts) <= 4
    AND octet_length(rendered_parts::text) <= 15000
  );
-- Preserve deliverable legacy rows without guessing prior multipart boundaries. An in-flight
-- legacy send is irrecoverably ambiguous and must never be retried after the migration.
UPDATE app.market_publications
SET rendered_parts = jsonb_build_array(rendered_body)
WHERE rendered_parts = '[]'::jsonb
  AND char_length(rendered_body) BETWEEN 1 AND 3500;
UPDATE app.market_publications
SET status = 'delivery_unknown', lease_token = NULL,
    error = 'MIGRATION_IN_FLIGHT_DELIVERY_UNKNOWN', updated_at = clock_timestamp()
WHERE status = 'sending';
UPDATE app.market_publications
SET status = 'suppressed', lease_token = NULL,
    error = 'MIGRATION_PUBLICATION_NOT_DELIVERABLE', updated_at = clock_timestamp()
WHERE status = 'ready' AND jsonb_array_length(rendered_parts) = 0;

-- FORCE RLS also applies to the table owner. These policies are intentionally granted only
-- to the non-login migration owner used by reviewed SECURITY DEFINER functions.
ALTER TABLE public.market_policy_config OWNER TO stock_agent_migration_owner;
ALTER TABLE public.market_policy_config FORCE ROW LEVEL SECURITY;
CREATE POLICY market_policy_config_executor_all ON public.market_policy_config
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
REVOKE ALL ON public.market_policy_config
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE POLICY agent_connections_executor_all ON app.agent_connections
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY user_consents_executor_all ON app.user_consents
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY analysis_schedules_executor_all ON app.analysis_schedules
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY analysis_runs_executor_all ON app.analysis_runs
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY market_gateway_requests_executor_all ON app.market_gateway_requests
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY decision_evaluations_executor_all ON app.decision_evaluations
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY notification_preferences_executor_all ON app.notification_preferences
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY dry_powder_executor_all ON app.dry_powder
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY suggestions_executor_all ON app.suggestions
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY suggestion_grades_executor_all ON app.suggestion_grades
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY stock_observations_executor_all ON app.stock_observations
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY daily_snapshots_executor_all ON app.daily_snapshots
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY radar_executor_all ON app.radar
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY lessons_executor_all ON app.lessons
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY paper_watches_executor_all ON app.paper_watches
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

CREATE OR REPLACE FUNCTION machine.agent_constant_time_equal(p_left BYTEA, p_right BYTEA)
RETURNS BOOLEAN
LANGUAGE plpgsql
IMMUTABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  left_padded BYTEA := substring(coalesce(p_left, ''::bytea) || decode(repeat('00', 32), 'hex') FROM 1 FOR 32);
  right_padded BYTEA := substring(coalesce(p_right, ''::bytea) || decode(repeat('00', 32), 'hex') FROM 1 FOR 32);
  difference INT := abs(octet_length(coalesce(p_left, ''::bytea)) - octet_length(coalesce(p_right, ''::bytea)));
  byte_index INT;
BEGIN
  FOR byte_index IN 0..31 LOOP
    difference := difference | (get_byte(left_padded, byte_index) # get_byte(right_padded, byte_index));
  END LOOP;
  RETURN difference = 0;
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_claim_operation(p_request JSONB, p_expected TEXT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_row app.agent_connections%ROWTYPE;
  run_row app.analysis_runs%ROWTYPE;
  existing_request app.market_gateway_requests%ROWTYPE;
  presented_digest BYTEA;
  request_digest TEXT;
  request_uuid UUID;
  run_uuid UUID;
  digest_matches BOOLEAN;
  connection_found BOOLEAN;
  operation_lease UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request,
    ARRAY['connection_id','secret_digest','contract_version','operation','request_id','run_id','dry_run','payload']
  ) OR jsonb_typeof(p_request->'payload') <> 'object'
     OR jsonb_typeof(p_request->'dry_run') <> 'boolean'
     OR p_request->>'operation' <> p_expected
     OR p_request->>'contract_version' <> '2'
     OR p_request->>'connection_id' !~ '^[0-9a-fA-F-]{36}$'
     OR p_request->>'request_id' !~ '^[0-9a-fA-F-]{36}$'
     OR p_request->>'secret_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;

  BEGIN
    request_uuid := (p_request->>'request_id')::uuid;
    presented_digest := decode(p_request->>'secret_digest', 'hex');
    IF p_expected = 'start_run' THEN
      IF p_request->'run_id' <> 'null'::jsonb THEN
        RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
      END IF;
    ELSE
      IF jsonb_typeof(p_request->'run_id') <> 'string' THEN
        RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
      END IF;
      run_uuid := (p_request->>'run_id')::uuid;
    END IF;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END;

  SELECT * INTO connection_row
  FROM app.agent_connections
  WHERE public_id = (p_request->>'connection_id')::uuid;
  connection_found := FOUND;

  digest_matches := machine.agent_constant_time_equal(
    coalesce(connection_row.inbound_token_digest, decode(repeat('00', 32), 'hex')),
    presented_digest
  );
  IF NOT connection_found OR NOT digest_matches OR connection_row.status NOT IN ('testing','active')
     OR connection_row.contract_version <> 2 THEN
    RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
  END IF;

  request_digest := encode(
    extensions.digest(convert_to((p_request - 'secret_digest')::text, 'UTF8'), 'sha256'),
    'hex'
  );
  SELECT * INTO existing_request
  FROM app.market_gateway_requests
  WHERE request_id = request_uuid;
  IF FOUND THEN
    IF existing_request.owner_id <> connection_row.owner_id
       OR existing_request.connection_id IS DISTINCT FROM connection_row.id
       OR existing_request.operation <> p_expected
       OR existing_request.input_digest IS DISTINCT FROM request_digest THEN
      RAISE EXCEPTION 'request replay conflict' USING ERRCODE = '23505';
    END IF;
    IF existing_request.status = 'completed' THEN
      RETURN jsonb_build_object(
        'duplicate', true, 'response', existing_request.response,
        'owner_id', connection_row.owner_id, 'connection_id', connection_row.id,
        'request_id', request_uuid, 'run_id', existing_request.run_id,
        'dry_run', false, 'connection_status', connection_row.status
      );
    END IF;
    RAISE EXCEPTION 'request replay conflict' USING ERRCODE = '23505';
  END IF;

  IF p_expected <> 'start_run' AND NOT (p_request->>'dry_run')::boolean THEN
    SELECT * INTO run_row
    FROM app.analysis_runs
    WHERE owner_id = connection_row.owner_id
      AND id = run_uuid
      AND connection_id = connection_row.id
      AND status = 'running';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
    END IF;
  END IF;

  IF (p_request->>'dry_run')::boolean THEN
    RETURN jsonb_build_object(
      'duplicate', false, 'owner_id', connection_row.owner_id,
      'connection_id', connection_row.id, 'request_id', request_uuid,
      'run_id', run_uuid, 'dry_run', true, 'provider', connection_row.provider,
      'connection_status', connection_row.status, 'lease_token', extensions.gen_random_uuid()
    );
  END IF;

  IF run_uuid IS NOT NULL AND (
    SELECT count(*) FROM app.market_gateway_requests
    WHERE owner_id = connection_row.owner_id AND run_id = run_uuid
      AND operation <> 'start_run'
  ) >= 12 THEN
    RAISE EXCEPTION 'run operation limit reached' USING ERRCODE = '54000';
  END IF;

  operation_lease := extensions.gen_random_uuid();
  INSERT INTO app.market_gateway_requests(
    owner_id, request_id, operation, run_id, connection_id, input_digest,
    status, lease_token
  ) VALUES (
    connection_row.owner_id, request_uuid, p_expected, run_uuid,
    connection_row.id, request_digest, 'claimed', operation_lease
  );
  RETURN jsonb_build_object(
    'duplicate', false, 'owner_id', connection_row.owner_id,
    'connection_id', connection_row.id, 'request_id', request_uuid,
    'run_id', run_uuid, 'dry_run', false, 'provider', connection_row.provider,
    'connection_status', connection_row.status, 'lease_token', operation_lease
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_complete_operation(
  p_owner_id UUID, p_request_id UUID, p_response JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
  IF octet_length(p_response::text) > 524288 THEN
    RAISE EXCEPTION 'agent response exceeds limit' USING ERRCODE = '54000';
  END IF;
  UPDATE app.market_gateway_requests
  SET status = 'completed', response = p_response,
      response_digest = encode(extensions.digest(convert_to(p_response::text, 'UTF8'), 'sha256'), 'hex'),
      finished_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND request_id = p_request_id AND status = 'claimed';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'agent request unavailable' USING ERRCODE = '42501';
  END IF;
  RETURN p_response;
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_start_run(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  payload JSONB := p_request->'payload';
  owner_uuid UUID;
  connection_uuid UUID;
  request_uuid UUID;
  run_uuid UUID := extensions.gen_random_uuid();
  phase_value TEXT;
  market_day DATE;
  trigger_uuid UUID;
  slot_row RECORD;
  slot_found BOOLEAN;
  response JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(payload, ARRAY['trigger_request_id'])
     OR (payload->'trigger_request_id' <> 'null'::jsonb
         AND jsonb_typeof(payload->'trigger_request_id') <> 'string') THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    IF payload->'trigger_request_id' <> 'null'::jsonb THEN
      trigger_uuid := (payload->>'trigger_request_id')::uuid;
    END IF;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END;

  claim := machine.agent_claim_operation(p_request, 'start_run');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  IF (claim->>'dry_run')::boolean THEN
    RETURN jsonb_build_object(
      'status', 'dry_run', 'run_id', run_uuid, 'phase', 'on-demand',
      'market_date', (clock_timestamp() AT TIME ZONE 'America/New_York')::date,
      'writes', 0
    );
  END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  connection_uuid := (claim->>'connection_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;

  IF trigger_uuid IS NULL THEN
    phase_value := 'on-demand';
    market_day := (clock_timestamp() AT TIME ZONE 'America/New_York')::date;
  ELSE
    SELECT * INTO slot_row FROM app.scheduled_run_slots
    WHERE owner_id = owner_uuid AND connection_id = connection_uuid
      AND trigger_request_id = trigger_uuid FOR UPDATE;
    slot_found := FOUND;
    IF NOT slot_found OR slot_row.holiday
       OR slot_row.status NOT IN ('claimed','triggered','trigger_unknown','provider_started') THEN
      RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
    END IF;
    phase_value := slot_row.phase;
    market_day := slot_row.market_date;
    IF slot_row.canonical_run_id IS NOT NULL THEN
      run_uuid := slot_row.canonical_run_id;
      UPDATE app.market_gateway_requests SET run_id = run_uuid
      WHERE owner_id = owner_uuid AND request_id = request_uuid;
      response := jsonb_build_object(
        'status', 'running', 'run_id', run_uuid, 'phase', phase_value,
        'market_date', market_day, 'canonical', true
      );
      RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
    END IF;
  END IF;
  IF claim->>'connection_status' = 'testing'
     AND (trigger_uuid IS NULL OR slot_row.purpose <> 'handshake') THEN
    RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(owner_uuid::text || ':' || connection_uuid::text || ':' || market_day::text, 0));
  IF (SELECT count(*) FROM app.analysis_runs
      WHERE owner_id = owner_uuid AND connection_id = connection_uuid
        AND market_date = market_day) >= 6 THEN
    RAISE EXCEPTION 'daily run limit reached' USING ERRCODE = '54000';
  END IF;
  IF phase_value = 'on-demand' AND EXISTS (
    SELECT 1 FROM app.analysis_runs
    WHERE owner_id = owner_uuid AND connection_id = connection_uuid
      AND kind = 'on-demand' AND started_at >= now() - interval '1 hour'
  ) THEN
    RAISE EXCEPTION 'on-demand run limit reached' USING ERRCODE = '54000';
  END IF;

  INSERT INTO app.analysis_runs(
    owner_id, id, kind, status, gateway_request_id, connection_id,
    market_date, provider
  ) VALUES (
    owner_uuid, run_uuid, phase_value, 'running', request_uuid, connection_uuid,
    market_day, claim->>'provider'
  );
  UPDATE app.market_gateway_requests
  SET run_id = run_uuid
  WHERE owner_id = owner_uuid AND request_id = request_uuid;
  IF trigger_uuid IS NOT NULL THEN
    UPDATE app.scheduled_run_slots SET canonical_run_id = run_uuid,
      status = 'provider_started', updated_at = clock_timestamp()
    WHERE owner_id = owner_uuid AND id = slot_row.id;
    UPDATE app.routine_trigger_attempts SET status = 'provider_started'
    WHERE owner_id = owner_uuid AND slot_id = slot_row.id
      AND status IN ('claimed','triggered','trigger_unknown');
  END IF;
  response := jsonb_build_object(
    'status', 'running', 'run_id', run_uuid, 'phase', phase_value,
    'market_date', market_day
  );
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_build_owner_context(
  p_owner_id UUID, p_run_id UUID, p_phase TEXT, p_market_date DATE, p_started_at TIMESTAMPTZ
) RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  SELECT jsonb_build_object(
    'run_id', p_run_id,
    'contract_version', 2,
    'phase', p_phase,
    'market_date', p_market_date,
    'started_at', p_started_at,
    'holdings', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT ticker, shares::text AS shares, avg_cost::text AS avg_cost, bucket,
             stop::text AS stop, target::text AS target,
             high_water_price::text AS high_water_price, hold_override_until,
             stop_alert_active, stop_near_alert_active,
             target_near_alert_active, target_alert_active
      FROM app.holdings WHERE owner_id = p_owner_id ORDER BY ticker LIMIT 100
    ) row_value),
    'holding_quotes', '{}'::jsonb,
    'realized_pnl_today', (
      SELECT coalesce(sum((result->>'realized_pnl')::numeric), 0)::text
      FROM app.portfolio_commands
      WHERE owner_id = p_owner_id AND status = 'applied'
        AND operation IN ('sell','sell_all')
        AND (applied_at AT TIME ZONE 'America/New_York')::date = p_market_date
        AND result->>'realized_pnl' ~ '^-?[0-9]+(?:\.[0-9]+)?$'
    ),
    'portfolio_command_coverage_complete', NOT EXISTS (
      SELECT 1 FROM app.portfolio_commands
      WHERE owner_id = p_owner_id AND status = 'applied'
        AND operation IN ('sell','sell_all')
        AND (applied_at AT TIME ZONE 'America/New_York')::date = p_market_date
        AND coalesce(result->>'realized_pnl', '') !~ '^-?[0-9]+(?:\.[0-9]+)?$'
    ),
    'consecutive_completed_losses', (
      SELECT count(*) FROM (
        SELECT direction_success,
               sum(CASE WHEN direction_success THEN 1 ELSE 0 END)
                 OVER (ORDER BY graded_at DESC, id DESC ROWS UNBOUNDED PRECEDING) AS prior_successes
        FROM app.suggestion_grades
        WHERE owner_id = p_owner_id AND coverage_status = 'complete'
          AND direction_success IS NOT NULL
        ORDER BY graded_at DESC, id DESC LIMIT 50
      ) recent WHERE NOT direction_success AND prior_successes = 0
    ),
    'owner_plans', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, ticker, bucket, amount::text AS amount, cadence, next_due_on, active,
             updated_at
      FROM app.owner_investment_plans WHERE owner_id = p_owner_id AND active
      ORDER BY next_due_on, ticker LIMIT 20
    ) row_value),
    'plans', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, ticker, bucket, amount::text AS amount, cadence, next_due_on, active,
             updated_at
      FROM app.owner_investment_plans WHERE owner_id = p_owner_id AND active
      ORDER BY next_due_on, ticker LIMIT 20
    ) row_value),
    'recent_suggestions', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, date, ticker, action, bucket, confidence, score,
             stop::text AS stop, target::text AS target,
             invalidation_price::text AS invalidation_price,
             valid_until, evidence_as_of
      FROM app.suggestions WHERE owner_id = p_owner_id
      ORDER BY ts DESC, id DESC LIMIT 100
    ) row_value),
    'observations', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, ticker, obs_date, event_type, summary, price_reaction, confidence, source
      FROM app.stock_observations WHERE owner_id = p_owner_id
      ORDER BY obs_date DESC, id DESC LIMIT 100
    ) row_value),
    'lessons', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, entry_date, category, content FROM app.lessons
      WHERE owner_id = p_owner_id ORDER BY entry_date DESC, id DESC LIMIT 40
    ) row_value),
    'radar', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT ticker, added, last_seen, days_relevant, reason, bucket_guess, promoted, promoted_on
      FROM app.radar WHERE owner_id = p_owner_id ORDER BY last_seen DESC NULLS LAST, ticker LIMIT 20
    ) row_value),
    'recent_grades', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT suggestion_id, horizon_days, coverage_status, excess_return_pct::text AS excess_return_pct,
             direction_success
      FROM app.suggestion_grades WHERE owner_id = p_owner_id
      ORDER BY graded_at DESC, id DESC LIMIT 150
    ) row_value),
    'dry_powder', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT month, growth_available::text AS growth_available,
             spec_available::text AS spec_available, rolled_months
      FROM app.dry_powder WHERE owner_id = p_owner_id ORDER BY month DESC LIMIT 12
    ) row_value),
    'paper_watches', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, ticker, created, entry_ref_price::text AS entry_ref_price,
             target_price::text AS target_price, hypothetical_amount::text AS hypothetical_amount,
             thesis, horizon, agent_view_at_open, agent_score_at_open
      FROM app.paper_watches WHERE owner_id = p_owner_id AND status = 'active'
      ORDER BY created DESC, id DESC LIMIT 50
    ) row_value),
    'evidence', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT evidence_id, category, source_identifier, reference_identifier, observed_at,
             retrieved_at, revalidated_at, content_hash, claims, status
      FROM app.run_evidence WHERE owner_id = p_owner_id AND run_id = p_run_id
      ORDER BY retrieved_at DESC, evidence_id LIMIT 100
    ) row_value),
    'quotes', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT ticker, price::text AS price, previous_close::text AS previous_close,
             provider, source_timestamp, retrieved_at, session, adjustment_status,
             status, conflict_basis_points
      FROM app.market_quote_cache WHERE owner_id = p_owner_id ORDER BY ticker LIMIT 60
    ) row_value)
  )
$$;

CREATE OR REPLACE FUNCTION machine.agent_read_bounded_context(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  owner_uuid UUID;
  run_uuid UUID;
  request_uuid UUID;
  response JSONB;
  handshake_run BOOLEAN;
  challenge_value TEXT;
  phase_value TEXT;
  market_day DATE;
  started_value TIMESTAMPTZ;
BEGIN
  claim := machine.agent_claim_operation(p_request, 'read_bounded_context');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  IF NOT (
    app.jsonb_has_exact_keys(p_request->'payload', ARRAY[]::text[])
    OR (
      app.jsonb_has_exact_keys(p_request->'payload', ARRAY['research'])
      AND jsonb_typeof(p_request->'payload'->'research') = 'object'
      AND octet_length((p_request->'payload'->'research')::text) <= 12000
    )
  ) THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;
  SELECT kind, market_date, started_at INTO phase_value, market_day, started_value
  FROM app.analysis_runs WHERE owner_id = owner_uuid AND id = run_uuid;
  IF (claim->>'dry_run')::boolean THEN
    phase_value := 'on-demand';
    market_day := (clock_timestamp() AT TIME ZONE 'America/New_York')::date;
    started_value := clock_timestamp();
  END IF;

  SELECT encode(handshake_challenge, 'hex') INTO challenge_value
  FROM app.scheduled_run_slots
  WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake';
  handshake_run := FOUND;
  IF handshake_run THEN
    response := jsonb_build_object(
      'run_id', run_uuid, 'handshake', true, 'contract_version', 2,
      'challenge', challenge_value, 'phase', phase_value, 'market_date', market_day,
      'holdings', '[]'::jsonb, 'plans', '[]'::jsonb,
      'recent_suggestions', '[]'::jsonb, 'radar', '[]'::jsonb,
      'evidence', '[]'::jsonb, 'quotes', '[]'::jsonb,
      'allowed_source_hosts', jsonb_build_array(
        'query1.finance.yahoo.com', 'www.sec.gov', 'finnhub.io'
      )
    );
    IF (claim->>'dry_run')::boolean THEN RETURN response || jsonb_build_object('dry_run', true); END IF;
    RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
  END IF;

  response := machine.agent_build_owner_context(
    owner_uuid, run_uuid, phase_value, market_day, started_value
  );
  IF (claim->>'dry_run')::boolean THEN RETURN response || jsonb_build_object('dry_run', true); END IF;
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_submit_analysis(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  payload JSONB := p_request->'payload';
  owner_uuid UUID;
  run_uuid UUID;
  run_started TIMESTAMPTZ;
  phase_value TEXT;
  market_day DATE;
  policy_value JSONB;
  context_value JSONB;
  corporate_value JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(payload, ARRAY[
      'phase','market_date','title','suggestion_only','provider','model','analyst',
      'checker','dimensions','evidence_packets','evidence_refs','prior_suggestion_ids','candidates'
    ]) OR payload->>'suggestion_only' <> 'true'
       OR octet_length(payload::text) > 65536
       OR jsonb_typeof(payload->'evidence_packets') <> 'array'
       OR jsonb_array_length(payload->'evidence_packets') NOT BETWEEN 1 AND 4
       OR jsonb_typeof(payload->'evidence_refs') <> 'array'
       OR jsonb_array_length(payload->'evidence_refs') > 100
       OR jsonb_typeof(payload->'candidates') <> 'array'
       OR jsonb_array_length(payload->'candidates') > 20 THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  claim := machine.agent_claim_operation(p_request, 'submit_analysis');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  IF claim->>'connection_status' <> 'active' THEN
    RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
  END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  IF NOT (claim->>'dry_run')::boolean AND EXISTS (
    SELECT 1 FROM app.scheduled_run_slots
    WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake'
  ) THEN
    RAISE EXCEPTION 'handshake operation is not permitted' USING ERRCODE = '42501';
  END IF;
  IF (claim->>'dry_run')::boolean THEN
    phase_value := 'on-demand';
    market_day := (clock_timestamp() AT TIME ZONE 'America/New_York')::date;
    run_started := clock_timestamp();
  ELSE
    SELECT kind, market_date, started_at INTO phase_value, market_day, run_started
    FROM app.analysis_runs
    WHERE owner_id = owner_uuid AND id = run_uuid AND status = 'running';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
    END IF;
  END IF;
  SELECT config || jsonb_build_object('version', version) INTO policy_value
  FROM public.market_policy_config WHERE active ORDER BY version DESC LIMIT 1;
  IF policy_value IS NULL THEN
    RAISE EXCEPTION 'active policy unavailable' USING ERRCODE = '22023';
  END IF;
  context_value := machine.agent_build_owner_context(
    owner_uuid, run_uuid, phase_value, market_day, run_started
  );
  SELECT coalesce(jsonb_agg(jsonb_build_object('ticker', ticker, 'state', state) ORDER BY ticker), '[]'::jsonb)
  INTO corporate_value FROM app.corporate_action_states WHERE owner_id = owner_uuid;
  RETURN jsonb_build_object(
    'status', CASE WHEN (claim->>'dry_run')::boolean THEN 'dry_run' ELSE 'claimed' END,
    'lease_token', claim->>'lease_token',
    'run_id', run_uuid,
    'phase', phase_value,
    'market_date', market_day,
    'started_at', run_started,
    'policy', policy_value,
    'context', context_value,
    'corporate_actions', corporate_value
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_apply_analysis(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_row app.agent_connections%ROWTYPE;
  request_row app.market_gateway_requests%ROWTYPE;
  run_row app.analysis_runs%ROWTYPE;
  decision JSONB := p_request->'decision';
  submission JSONB;
  publication JSONB;
  item JSONB;
  evaluation_row app.decision_evaluations%ROWTYPE;
  owner_uuid UUID;
  request_uuid UUID;
  run_uuid UUID;
  lease_uuid UUID;
  publication_uuid UUID := extensions.gen_random_uuid();
  delivery_lease UUID;
  chat_value BIGINT;
  notify_enabled BOOLEAN := false;
  final_publication_status TEXT;
  evaluation_count INT := 0;
  suggestion_count INT := 0;
  updated_rows INT;
  run_started TIMESTAMPTZ;
  response JSONB;
  presented_digest BYTEA;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY[
      'connection_id','secret_digest','request_id','run_id','lease_token','decision'
    ]) OR jsonb_typeof(decision) <> 'object'
       OR NOT app.jsonb_has_exact_keys(decision, ARRAY[
         'provider_submission','evidence','search_receipts','policy_quotes','policy_version',
         'evaluations','suggestions','holding_state','publication'
       ]) OR octet_length(decision::text) > 262144 THEN
    RAISE EXCEPTION 'invalid analysis transaction' USING ERRCODE = '22023';
  END IF;
  BEGIN
    request_uuid := (p_request->>'request_id')::uuid;
    run_uuid := (p_request->>'run_id')::uuid;
    lease_uuid := (p_request->>'lease_token')::uuid;
    presented_digest := decode(p_request->>'secret_digest', 'hex');
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid analysis transaction' USING ERRCODE = '22023';
  END;
  SELECT * INTO connection_row FROM app.agent_connections
  WHERE public_id = (p_request->>'connection_id')::uuid;
  IF NOT FOUND OR connection_row.status <> 'active'
     OR NOT machine.agent_constant_time_equal(connection_row.inbound_token_digest, presented_digest) THEN
    RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
  END IF;
  owner_uuid := connection_row.owner_id;
  SELECT * INTO request_row FROM app.market_gateway_requests
  WHERE owner_id = owner_uuid AND request_id = request_uuid FOR UPDATE;
  IF NOT FOUND OR request_row.connection_id <> connection_row.id
     OR request_row.run_id <> run_uuid OR request_row.operation <> 'submit_analysis'
     OR request_row.status <> 'claimed' OR request_row.lease_token <> lease_uuid THEN
    RAISE EXCEPTION 'analysis request unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO run_row FROM app.analysis_runs
  WHERE owner_id = owner_uuid AND id = run_uuid AND connection_id = connection_row.id
    AND status = 'running' FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'analysis run unavailable' USING ERRCODE = '42501';
  END IF;
  run_started := run_row.started_at;
  submission := decision->'provider_submission';
  publication := decision->'publication';
  IF jsonb_typeof(submission) <> 'object'
     OR submission->>'phase' IS DISTINCT FROM run_row.kind
     OR submission->>'market_date' IS DISTINCT FROM run_row.market_date::text
     OR submission->>'suggestion_only' <> 'true'
     OR jsonb_typeof(decision->'evidence') <> 'array'
     OR jsonb_array_length(decision->'evidence') > 100
     OR jsonb_typeof(decision->'search_receipts') <> 'array'
     OR jsonb_array_length(decision->'search_receipts') > 4
     OR jsonb_typeof(decision->'policy_quotes') <> 'array'
     OR jsonb_array_length(decision->'policy_quotes') > 60
     OR jsonb_typeof(decision->'evaluations') <> 'array'
     OR jsonb_array_length(decision->'evaluations') > 20
     OR jsonb_typeof(decision->'suggestions') <> 'array'
     OR jsonb_array_length(decision->'suggestions') > 20
     OR jsonb_typeof(decision->'holding_state') <> 'array'
     OR jsonb_array_length(decision->'holding_state') > 100
     OR jsonb_typeof(publication) <> 'object' THEN
    RAISE EXCEPTION 'invalid analysis transaction' USING ERRCODE = '22023';
  END IF;
  PERFORM 1 FROM public.market_policy_config
  WHERE active AND version = (decision->>'policy_version')::int;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'active policy unavailable' USING ERRCODE = '22023';
  END IF;

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'evidence') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'evidence_id','source_run_id','category','source_identifier','reference_identifier',
        'observed_at','retrieved_at','revalidated_at','content_hash','claims','status'
      ]) OR item->>'evidence_id' !~ '^[a-z0-9][a-z0-9._:-]{0,99}$'
         OR item->>'category' NOT IN (
           'market_snapshot','source_search','filing','fundamentals','news','technicals',
           'corporate_action','issuer','exchange'
         ) OR item->>'content_hash' !~ '^[0-9a-f]{64}$'
         OR jsonb_typeof(item->'claims') <> 'array'
         OR jsonb_array_length(item->'claims') > 10
         OR item->>'status' NOT IN (
           'fresh','stale','conflicting','missing','no_new_material_evidence',
           'suspected','needs_review','clear'
         ) THEN
      RAISE EXCEPTION 'invalid evidence' USING ERRCODE = '22023';
    END IF;
    IF (item->>'retrieved_at')::timestamptz < run_started
       OR (item->>'retrieved_at')::timestamptz > clock_timestamp() + interval '5 minutes'
       OR (item->>'revalidated_at') IS NOT NULL
          AND (item->>'revalidated_at')::timestamptz < run_started THEN
      RAISE EXCEPTION 'evidence_stale' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.run_evidence(
      owner_id, run_id, evidence_id, source_run_id, category, source_identifier,
      reference_identifier, observed_at, retrieved_at, revalidated_at,
      content_hash, claims, status
    ) VALUES (
      owner_uuid, run_uuid, item->>'evidence_id',
      CASE WHEN item->'source_run_id' = 'null'::jsonb THEN NULL ELSE (item->>'source_run_id')::uuid END,
      item->>'category', item->>'source_identifier', item->>'reference_identifier',
      (item->>'observed_at')::timestamptz, (item->>'retrieved_at')::timestamptz,
      (item->>'revalidated_at')::timestamptz, item->>'content_hash', item->'claims', item->>'status'
    );
  END LOOP;

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'search_receipts') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'searched_at','categories','sources','result_status','content_hash'
      ]) OR jsonb_typeof(item->'categories') <> 'array'
         OR jsonb_typeof(item->'sources') <> 'array'
         OR item->>'result_status' NOT IN (
           'material_evidence_found','no_new_material_evidence','source_unavailable'
         ) OR item->>'content_hash' !~ '^[0-9a-f]{64}$'
         OR (item->>'searched_at')::timestamptz < run_started
         OR (item->>'searched_at')::timestamptz > clock_timestamp() + interval '5 minutes' THEN
      RAISE EXCEPTION 'invalid source receipt' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.source_search_receipts(
      owner_id, run_id, searched_at, categories, sources, result_status, content_hash
    ) VALUES (
      owner_uuid, run_uuid, (item->>'searched_at')::timestamptz,
      ARRAY(SELECT jsonb_array_elements_text(item->'categories')),
      item->'sources', item->>'result_status', item->>'content_hash'
    );
  END LOOP;

  IF NOT EXISTS (
    SELECT 1 FROM app.run_evidence WHERE owner_id = owner_uuid AND run_id = run_uuid
      AND category = 'market_snapshot' AND status = 'fresh' AND retrieved_at >= run_started
  ) OR NOT EXISTS (
    SELECT 1 FROM app.source_search_receipts WHERE owner_id = owner_uuid AND run_id = run_uuid
      AND searched_at >= run_started AND result_status <> 'source_unavailable'
  ) OR NOT EXISTS (
    SELECT 1 FROM app.run_evidence WHERE owner_id = owner_uuid AND run_id = run_uuid
      AND category = 'source_search' AND status IN ('fresh','no_new_material_evidence')
      AND retrieved_at >= run_started
  ) THEN
    RAISE EXCEPTION 'evidence_missing' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(submission->'evidence_refs') reference
    LEFT JOIN app.run_evidence evidence
      ON evidence.owner_id = owner_uuid AND evidence.run_id = run_uuid
     AND evidence.evidence_id = reference->>'evidence_id'
     AND evidence.content_hash = reference->>'content_hash'
    WHERE reference->>'run_id' IS DISTINCT FROM run_uuid::text OR evidence.id IS NULL
  ) THEN
    RAISE EXCEPTION 'evidence_conflicting' USING ERRCODE = '22023';
  END IF;

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'policy_quotes') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'ticker','price','previous_close','as_of','market_state','source','retrieved_at'
      ]) OR item->>'ticker' !~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'
         OR item->>'price' !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
         OR item->>'market_state' NOT IN ('PRE','REGULAR','POST','CLOSED')
         OR item->>'source' <> 'yahoo-chart'
         OR (item->>'as_of')::timestamptz > (item->>'retrieved_at')::timestamptz + interval '5 minutes'
         OR (item->>'retrieved_at')::timestamptz < run_started THEN
      RAISE EXCEPTION 'invalid server quote' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.market_quote_cache(
      owner_id, ticker, price, previous_close, provider, source_timestamp,
      retrieved_at, session, adjustment_status, status, content_digest, conflict_basis_points
    ) VALUES (
      owner_uuid, item->>'ticker', (item->>'price')::numeric,
      CASE WHEN item->'previous_close' = 'null'::jsonb THEN NULL ELSE (item->>'previous_close')::numeric END,
      item->>'source', (item->>'as_of')::timestamptz, (item->>'retrieved_at')::timestamptz,
      item->>'market_state', 'raw',
      CASE
        WHEN run_row.kind IN ('intraday','on-demand')
             AND item->>'market_state' = 'REGULAR'
             AND (item->>'as_of')::timestamptz >= (item->>'retrieved_at')::timestamptz - interval '20 minutes'
          THEN 'fresh'
        WHEN run_row.kind IN ('pre-market','post-market')
             AND (item->>'as_of')::timestamptz >= (item->>'retrieved_at')::timestamptz - interval '18 hours'
          THEN 'fresh'
        ELSE 'stale'
      END,
      encode(extensions.digest(convert_to(item::text, 'UTF8'), 'sha256'), 'hex'), NULL
    ) ON CONFLICT (owner_id, ticker) DO UPDATE SET
      price = excluded.price, previous_close = excluded.previous_close,
      provider = excluded.provider, source_timestamp = excluded.source_timestamp,
      retrieved_at = excluded.retrieved_at, session = excluded.session,
      adjustment_status = excluded.adjustment_status, status = excluded.status,
      content_digest = excluded.content_digest, conflict_basis_points = NULL,
      updated_at = clock_timestamp();
  END LOOP;

  INSERT INTO app.agent_analysis_submissions(
    owner_id, run_id, request_id, provider, model, phase, market_date,
    payload, payload_digest, status
  ) VALUES (
    owner_uuid, run_uuid, request_uuid, submission->>'provider', submission->>'model',
    submission->>'phase', (submission->>'market_date')::date, submission,
    encode(extensions.digest(convert_to(submission::text, 'UTF8'), 'sha256'), 'hex'), 'accepted'
  );

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'evaluations') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'id','candidate_id','input_digest','raw_action','final_action','policy_status',
        'reason_codes','explanations','normalized','evidence','analyst','checker'
      ]) OR item->>'policy_status' NOT IN ('approved','downgraded','vetoed')
         OR item->>'input_digest' !~ '^[0-9a-f]{64}$' THEN
      RAISE EXCEPTION 'invalid evaluation' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.decision_evaluations(
      owner_id, id, request_id, run_id, candidate_id, policy_version, input_digest,
      raw_action, final_action, policy_status, reason_codes, explanations,
      normalized, evidence, analyst, checker
    ) VALUES (
      owner_uuid, (item->>'id')::uuid, request_uuid, run_uuid,
      (item->>'candidate_id')::uuid, (decision->>'policy_version')::int,
      item->>'input_digest', item->>'raw_action', NULLIF(item->>'final_action',''),
      item->>'policy_status', item->'reason_codes', item->'explanations',
      item->'normalized', item->'evidence', item->'analyst', item->'checker'
    );
    evaluation_count := evaluation_count + 1;
  END LOOP;

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'suggestions') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'evaluation_id','candidate_id','date','ticker','action','decision_mode','bucket','depth',
        'entry_zone_low','entry_zone_high','valid_until','stop','target','confidence','bull','bear',
        'decisive_factor','risk_verdict','reason','score','price_at_suggestion','evidence_as_of',
        'invalidation_price'
      ]) THEN
      RAISE EXCEPTION 'invalid suggestion' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO evaluation_row FROM app.decision_evaluations
    WHERE owner_id = owner_uuid AND id = (item->>'evaluation_id')::uuid
      AND request_id = request_uuid AND run_id = run_uuid;
    IF NOT FOUND OR evaluation_row.candidate_id <> (item->>'candidate_id')::uuid
       OR evaluation_row.final_action IS DISTINCT FROM item->>'action'
       OR evaluation_row.policy_status NOT IN ('approved','downgraded')
       OR evaluation_row.normalized->>'ticker' IS DISTINCT FROM item->>'ticker' THEN
      RAISE EXCEPTION 'suggestion evaluation mismatch' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.suggestions(
      owner_id, date, ticker, action, decision_mode, bucket, depth,
      entry_zone_low, entry_zone_high, valid_until, stop, target, confidence,
      bull, bear, decisive_factor, risk_verdict, reason, score, price_at_suggestion,
      run_id, evidence_as_of, invalidation_price, evaluation_id, decision_source
    ) VALUES (
      owner_uuid, (item->>'date')::date, item->>'ticker', item->>'action',
      item->>'decision_mode', item->>'bucket', item->>'depth',
      (item->>'entry_zone_low')::numeric, (item->>'entry_zone_high')::numeric,
      (item->>'valid_until')::date, (item->>'stop')::numeric, (item->>'target')::numeric,
      item->>'confidence', item->>'bull', item->>'bear', item->>'decisive_factor',
      item->>'risk_verdict', item->>'reason', (item->>'score')::int,
      (item->>'price_at_suggestion')::numeric, run_uuid,
      (item->>'evidence_as_of')::timestamptz, (item->>'invalidation_price')::numeric,
      evaluation_row.id, 'gateway'
    );
    suggestion_count := suggestion_count + 1;
  END LOOP;

  FOR item IN SELECT value FROM jsonb_array_elements(decision->'holding_state') LOOP
    IF NOT app.jsonb_has_exact_keys(item, ARRAY[
        'ticker','high_water_price','stop_alert_active','stop_near_alert_active',
        'target_near_alert_active','target_alert_active'
      ]) THEN
      RAISE EXCEPTION 'invalid holding state' USING ERRCODE = '22023';
    END IF;
    UPDATE app.holdings SET
      high_water_price = greatest(coalesce(high_water_price, 0), (item->>'high_water_price')::numeric),
      stop_alert_active = (item->>'stop_alert_active')::boolean,
      stop_near_alert_active = (item->>'stop_near_alert_active')::boolean,
      target_near_alert_active = (item->>'target_near_alert_active')::boolean,
      target_alert_active = (item->>'target_alert_active')::boolean
    WHERE owner_id = owner_uuid AND ticker = item->>'ticker';
    GET DIAGNOSTICS updated_rows = ROW_COUNT;
    IF updated_rows <> 1 THEN
      RAISE EXCEPTION 'holding state target unavailable' USING ERRCODE = '22023';
    END IF;
  END LOOP;

  IF NOT app.jsonb_has_exact_keys(publication, ARRAY[
      'market_date','phase','kind','template_version','rendered_body','rendered_parts','rendered_hash','status'
    ]) OR publication->>'market_date' IS DISTINCT FROM run_row.market_date::text
       OR publication->>'phase' IS DISTINCT FROM run_row.kind
       OR publication->>'status' NOT IN ('ready','suppressed')
       OR publication->>'rendered_hash' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(publication->'rendered_parts') <> 'array'
       OR jsonb_array_length(publication->'rendered_parts') > 4
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(publication->'rendered_parts') part
         WHERE jsonb_typeof(part) <> 'string' OR char_length(part #>> '{}') NOT BETWEEN 1 AND 3500
       )
       OR (publication->>'status' = 'ready' AND jsonb_array_length(publication->'rendered_parts') = 0)
       OR (publication->>'status' = 'suppressed' AND jsonb_array_length(publication->'rendered_parts') <> 0)
       OR publication->>'rendered_hash' IS DISTINCT FROM encode(
         extensions.digest(convert_to(publication->>'rendered_body', 'UTF8'), 'sha256'), 'hex'
       )
       OR publication->>'rendered_body' IS DISTINCT FROM coalesce((
         SELECT string_agg(part.value, E'\n\n' ORDER BY part.ordinality)
         FROM jsonb_array_elements_text(publication->'rendered_parts')
           WITH ORDINALITY AS part(value, ordinality)
       ), '')
       OR char_length(publication->>'rendered_body') > 14000 THEN
    RAISE EXCEPTION 'invalid publication' USING ERRCODE = '22023';
  END IF;
  SELECT CASE run_row.kind
      WHEN 'pre-market' THEN pre_market_enabled
      WHEN 'intraday' THEN intraday_enabled
      WHEN 'post-market' THEN post_market_enabled
      ELSE false
    END INTO notify_enabled
  FROM app.notification_preferences WHERE owner_id = owner_uuid;
  SELECT telegram_chat_id INTO chat_value FROM app.telegram_links
  WHERE owner_id = owner_uuid AND status = 'active';
  final_publication_status := CASE
    WHEN publication->>'status' = 'ready' AND coalesce(notify_enabled, false) AND chat_value IS NOT NULL
      THEN 'sending'
    ELSE 'suppressed'
  END;
  IF final_publication_status = 'sending' THEN delivery_lease := extensions.gen_random_uuid(); END IF;
  INSERT INTO app.market_publications(
    owner_id, id, idempotency_key, run_id, market_date, phase, kind,
    template_version, rendered_body, rendered_parts, rendered_hash, status, lease_token,
    sending_started_at, attempt_count
  ) VALUES (
    owner_uuid, publication_uuid, request_uuid, run_uuid, run_row.market_date,
    run_row.kind, publication->>'kind', (publication->>'template_version')::int,
    publication->>'rendered_body', publication->'rendered_parts', publication->>'rendered_hash', final_publication_status,
    delivery_lease, CASE WHEN delivery_lease IS NULL THEN NULL ELSE clock_timestamp() END,
    CASE WHEN delivery_lease IS NULL THEN 0 ELSE 1 END
  );
  UPDATE app.analysis_runs SET
    provider = submission->>'provider', model = submission->>'model',
    data_as_of = (SELECT max(retrieved_at) FROM app.run_evidence
                  WHERE owner_id = owner_uuid AND run_id = run_uuid),
    source_status = jsonb_build_object(
      'analysis', 'accepted', 'evidence', 'server_verified',
      'publication', final_publication_status
    )
  WHERE owner_id = owner_uuid AND id = run_uuid;
  response := jsonb_build_object(
    'status', 'accepted', 'run_id', run_uuid, 'publication_id', publication_uuid,
    'publication_status', final_publication_status,
    'evaluation_count', evaluation_count, 'suggestion_count', suggestion_count,
    'telegram_message_ids', '[]'::jsonb
  );
  IF run_row.kind = 'on-demand' THEN
    response := response || jsonb_build_object('preview', publication->>'rendered_body');
  END IF;
  IF final_publication_status = 'suppressed' THEN
    RETURN jsonb_build_object(
      'delivery_required', false,
      'response', machine.agent_complete_operation(owner_uuid, request_uuid, response)
    );
  END IF;
  RETURN jsonb_build_object(
    'delivery_required', true,
    'chat_id', chat_value::text,
    'delivery_lease', delivery_lease,
    'response', response
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_finish_analysis_delivery(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_row app.agent_connections%ROWTYPE;
  request_row app.market_gateway_requests%ROWTYPE;
  publication_row app.market_publications%ROWTYPE;
  owner_uuid UUID;
  request_uuid UUID;
  run_uuid UUID;
  delivery_lease UUID;
  status_value TEXT;
  message_ids JSONB;
  response JSONB;
  presented_digest BYTEA;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY[
      'connection_id','secret_digest','request_id','run_id','delivery_lease','status','message_ids'
    ]) OR p_request->>'status' NOT IN ('delivered','delivery_failed','delivery_unknown')
       OR jsonb_typeof(p_request->'message_ids') <> 'array'
       OR jsonb_array_length(p_request->'message_ids') > 4 THEN
    RAISE EXCEPTION 'invalid delivery completion' USING ERRCODE = '22023';
  END IF;
  BEGIN
    request_uuid := (p_request->>'request_id')::uuid;
    run_uuid := (p_request->>'run_id')::uuid;
    delivery_lease := (p_request->>'delivery_lease')::uuid;
    presented_digest := decode(p_request->>'secret_digest', 'hex');
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid delivery completion' USING ERRCODE = '22023';
  END;
  status_value := p_request->>'status';
  message_ids := p_request->'message_ids';
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(message_ids) item
    WHERE jsonb_typeof(item) <> 'number' OR (item::text)::bigint <= 0
  ) OR (status_value = 'delivered' AND jsonb_array_length(message_ids) = 0)
     OR (status_value = 'delivery_failed' AND jsonb_array_length(message_ids) <> 0) THEN
    RAISE EXCEPTION 'invalid delivery completion' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO connection_row FROM app.agent_connections
  WHERE public_id = (p_request->>'connection_id')::uuid;
  IF NOT FOUND OR NOT machine.agent_constant_time_equal(connection_row.inbound_token_digest, presented_digest) THEN
    RAISE EXCEPTION 'agent credential or run unavailable' USING ERRCODE = '42501';
  END IF;
  owner_uuid := connection_row.owner_id;
  SELECT * INTO request_row FROM app.market_gateway_requests
  WHERE owner_id = owner_uuid AND request_id = request_uuid FOR UPDATE;
  IF NOT FOUND OR request_row.connection_id <> connection_row.id
     OR request_row.run_id <> run_uuid OR request_row.operation <> 'submit_analysis'
     OR request_row.status <> 'claimed' THEN
    RAISE EXCEPTION 'analysis request unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO publication_row FROM app.market_publications
  WHERE owner_id = owner_uuid AND idempotency_key = request_uuid AND run_id = run_uuid FOR UPDATE;
  IF NOT FOUND OR publication_row.status <> 'sending' OR publication_row.lease_token <> delivery_lease THEN
    RAISE EXCEPTION 'publication lease unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.market_publications SET
    status = status_value,
    telegram_message_ids = message_ids,
    lease_token = NULL,
    delivered_at = CASE WHEN status_value = 'delivered' THEN clock_timestamp() ELSE NULL END,
    error = CASE status_value
      WHEN 'delivery_failed' THEN 'TELEGRAM_REJECTED'
      WHEN 'delivery_unknown' THEN 'TELEGRAM_OUTCOME_UNKNOWN'
      ELSE NULL
    END,
    updated_at = clock_timestamp()
  WHERE owner_id = owner_uuid AND id = publication_row.id;
  UPDATE app.analysis_runs SET
    telegram_message_ids = message_ids,
    source_status = source_status || jsonb_build_object('publication', status_value)
  WHERE owner_id = owner_uuid AND id = run_uuid;
  response := jsonb_build_object(
    'status', 'accepted', 'run_id', run_uuid,
    'publication_id', publication_row.id, 'publication_status', status_value,
    'telegram_message_ids', message_ids
  );
  RETURN jsonb_build_object(
    'response', machine.agent_complete_operation(owner_uuid, request_uuid, response)
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_record_permitted_artifacts(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  payload JSONB := p_request->'payload';
  owner_uuid UUID;
  run_uuid UUID;
  request_uuid UUID;
  item JSONB;
  item_kind TEXT;
  counts JSONB := '{}'::jsonb;
  response JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(payload, ARRAY['mutations'])
     OR jsonb_typeof(payload->'mutations') <> 'array'
     OR jsonb_array_length(payload->'mutations') NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  claim := machine.agent_claim_operation(p_request, 'record_permitted_artifacts');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;
  IF EXISTS (
    SELECT 1 FROM app.scheduled_run_slots
    WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake'
  ) THEN
    RAISE EXCEPTION 'handshake operation is not permitted' USING ERRCODE = '42501';
  END IF;

  FOR item IN SELECT value FROM jsonb_array_elements(payload->'mutations') LOOP
    item_kind := item->>'kind';
    IF item_kind NOT IN (
      'observation','snapshot','lesson','radar_upsert','radar_delete',
      'paper_watch_create','paper_watch_close'
    ) THEN
      RAISE EXCEPTION 'artifact kind is not permitted' USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(item) <> 'object' THEN
      RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
    END IF;
    counts := jsonb_set(
      counts, ARRAY[item_kind],
      to_jsonb(coalesce((counts->>item_kind)::int, 0) + 1), true
    );
    IF (claim->>'dry_run')::boolean THEN CONTINUE; END IF;

    CASE item_kind
      WHEN 'observation' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','ticker','obs_date','event_type','summary','price_reaction','confidence','source']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        INSERT INTO app.stock_observations(
          owner_id, ticker, obs_date, event_type, summary, price_reaction,
          confidence, source, run_id
        ) VALUES (
          owner_uuid, upper(item->>'ticker'), (item->>'obs_date')::date,
          item->>'event_type', item->>'summary', item->>'price_reaction',
          item->>'confidence', item->>'source', run_uuid
        );
      WHEN 'snapshot' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','snap_date','ticker','close','day_move_pct','rsi14','sma50','sma200','macd_hist']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        INSERT INTO app.daily_snapshots(
          owner_id, snap_date, ticker, close, day_move_pct, rsi14, sma50,
          sma200, macd_hist, run_id
        ) VALUES (
          owner_uuid, (item->>'snap_date')::date, upper(item->>'ticker'),
          (item->>'close')::numeric, (item->>'day_move_pct')::numeric,
          (item->>'rsi14')::numeric, (item->>'sma50')::numeric,
          (item->>'sma200')::numeric, (item->>'macd_hist')::numeric, run_uuid
        ) ON CONFLICT (owner_id, snap_date, ticker) DO UPDATE
          SET close = excluded.close, day_move_pct = excluded.day_move_pct,
              rsi14 = excluded.rsi14, sma50 = excluded.sma50,
              sma200 = excluded.sma200, macd_hist = excluded.macd_hist,
              run_id = excluded.run_id;
      WHEN 'lesson' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','entry_date','category','content']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        INSERT INTO app.lessons(owner_id, entry_date, category, content, run_id)
        VALUES (owner_uuid, (item->>'entry_date')::date, item->>'category', item->>'content', run_uuid);
      WHEN 'radar_upsert' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','ticker','added','last_seen','days_relevant','reason','bucket_guess','promoted','promoted_on']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        INSERT INTO app.radar(
          owner_id, ticker, added, last_seen, days_relevant, reason,
          bucket_guess, promoted, promoted_on, updated_run_id
        ) VALUES (
          owner_uuid, upper(item->>'ticker'), (item->>'added')::date,
          (item->>'last_seen')::date, (item->>'days_relevant')::int,
          item->>'reason', item->>'bucket_guess', (item->>'promoted')::boolean,
          (item->>'promoted_on')::date, run_uuid
        ) ON CONFLICT (owner_id, ticker) DO UPDATE SET
          last_seen = excluded.last_seen, days_relevant = excluded.days_relevant,
          reason = excluded.reason, bucket_guess = excluded.bucket_guess,
          promoted = excluded.promoted, promoted_on = excluded.promoted_on,
          updated_run_id = excluded.updated_run_id;
      WHEN 'radar_delete' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','ticker']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        DELETE FROM app.radar WHERE owner_id = owner_uuid AND ticker = upper(item->>'ticker');
      WHEN 'paper_watch_create' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','ticker','entry_ref_price','target_price','hypothetical_amount','thesis','horizon']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        INSERT INTO app.paper_watches(
          owner_id, ticker, created, entry_ref_price, target_price,
          hypothetical_amount, thesis, horizon, opened_run_id
        ) VALUES (
          owner_uuid, upper(item->>'ticker'), current_date,
          (item->>'entry_ref_price')::numeric, (item->>'target_price')::numeric,
          (item->>'hypothetical_amount')::numeric, item->>'thesis', item->>'horizon', run_uuid
        );
      WHEN 'paper_watch_close' THEN
        IF NOT app.jsonb_has_exact_keys(item, ARRAY['kind','watch_id','ticker']) THEN
          RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
        END IF;
        UPDATE app.paper_watches SET status = 'closed', closed_date = current_date,
          closed_run_id = run_uuid
        WHERE owner_id = owner_uuid AND id = (item->>'watch_id')::bigint
          AND ticker = upper(item->>'ticker') AND status = 'active';
        IF NOT FOUND THEN RAISE EXCEPTION 'paper watch unavailable' USING ERRCODE = '22023'; END IF;
    END CASE;
  END LOOP;
  response := jsonb_build_object('status', CASE WHEN (claim->>'dry_run')::boolean THEN 'dry_run' ELSE 'recorded' END, 'counts', counts);
  IF (claim->>'dry_run')::boolean THEN RETURN response || jsonb_build_object('writes', 0); END IF;
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_grade_due_decisions(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  owner_uuid UUID;
  run_uuid UUID;
  request_uuid UUID;
  result_limit INT;
  response JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request->'payload', ARRAY['limit'])
     OR p_request->'payload'->>'limit' !~ '^\d{1,2}$' THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  result_limit := (p_request->'payload'->>'limit')::int;
  IF result_limit NOT BETWEEN 1 AND 50 THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  claim := machine.agent_claim_operation(p_request, 'grade_due_decisions');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;
  IF EXISTS (
    SELECT 1 FROM app.scheduled_run_slots
    WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake'
  ) THEN
    RAISE EXCEPTION 'handshake operation is not permitted' USING ERRCODE = '42501';
  END IF;
  SELECT jsonb_build_object(
    'status', CASE WHEN (claim->>'dry_run')::boolean THEN 'dry_run' ELSE 'due_returned' END,
    'run_id', run_uuid,
    'due', coalesce(jsonb_agg(to_jsonb(due_row)), '[]'::jsonb)
  ) INTO response
  FROM (
    SELECT suggestion.id AS suggestion_id, suggestion.ticker, suggestion.action,
           suggestion.date, suggestion.valid_until
    FROM app.suggestions AS suggestion
    WHERE suggestion.owner_id = owner_uuid
      AND suggestion.date < current_date
      AND NOT EXISTS (
        SELECT 1 FROM app.suggestion_grades AS grade
        WHERE grade.owner_id = owner_uuid AND grade.suggestion_id = suggestion.id
      )
    ORDER BY suggestion.date, suggestion.id LIMIT result_limit
  ) due_row;
  IF (claim->>'dry_run')::boolean THEN RETURN response; END IF;
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
$$;

CREATE OR REPLACE FUNCTION machine.agent_finish_run(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim JSONB;
  owner_uuid UUID;
  run_uuid UUID;
  request_uuid UUID;
  completed_count INT;
  failed_count INT;
  claimed_count INT;
  final_status TEXT;
  response JSONB;
  handshake_run BOOLEAN;
  handshake_challenge_value BYTEA;
  check_row JSONB;
  check_observed_at TIMESTAMPTZ;
  required_host TEXT;
  telegram_status TEXT;
  telegram_ids JSONB;
  publication_incomplete_count INT;
BEGIN
  claim := machine.agent_claim_operation(p_request, 'finish_run');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;
  SELECT count(*) FILTER (WHERE status = 'completed'),
         count(*) FILTER (WHERE status = 'failed'),
         count(*) FILTER (WHERE status = 'claimed' AND request_id <> request_uuid)
  INTO completed_count, failed_count, claimed_count
  FROM app.market_gateway_requests
  WHERE owner_id = owner_uuid AND run_id = run_uuid;
  SELECT CASE
      WHEN bool_or(status = 'delivery_unknown') THEN 'delivery_unknown'
      WHEN bool_or(status = 'delivery_failed') THEN 'delivery_failed'
      WHEN bool_or(status = 'delivered') THEN 'delivered'
      ELSE 'not_sent'
    END,
    coalesce((
      SELECT jsonb_agg(message_id ORDER BY publication.created_at, message_id::text)
      FROM app.market_publications AS publication
      CROSS JOIN LATERAL jsonb_array_elements(publication.telegram_message_ids) AS message_id
      WHERE publication.owner_id = owner_uuid AND publication.run_id = run_uuid
    ), '[]'::jsonb),
    count(*) FILTER (WHERE status IN ('ready','sending','delivery_failed','delivery_unknown'))
  INTO telegram_status, telegram_ids, publication_incomplete_count
  FROM app.market_publications
  WHERE owner_id = owner_uuid AND run_id = run_uuid;
  final_status := CASE
    WHEN failed_count > 0 OR claimed_count > 0 OR publication_incomplete_count > 0
      THEN 'partial'
    ELSE 'completed'
  END;
  SELECT handshake_challenge INTO handshake_challenge_value
  FROM app.scheduled_run_slots
  WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake'
  FOR UPDATE;
  handshake_run := FOUND;
  IF handshake_run THEN
    IF NOT app.jsonb_has_exact_keys(
         p_request->'payload', ARRAY['contract_version','challenge','source_checks']
       ) OR p_request->'payload'->>'contract_version' <> '2'
       OR p_request->'payload'->>'challenge' <> encode(handshake_challenge_value, 'hex')
       OR jsonb_typeof(p_request->'payload'->'source_checks') <> 'array'
       OR jsonb_array_length(p_request->'payload'->'source_checks') <> 3 THEN
      RAISE EXCEPTION 'handshake verification failed' USING ERRCODE = '42501';
    END IF;
    FOREACH required_host IN ARRAY ARRAY[
      'query1.finance.yahoo.com', 'www.sec.gov', 'finnhub.io'
    ] LOOP
      IF (
        SELECT count(*) FROM jsonb_array_elements(p_request->'payload'->'source_checks') item
        WHERE item->>'host' = required_host
      ) <> 1 THEN
        RAISE EXCEPTION 'handshake verification failed' USING ERRCODE = '42501';
      END IF;
    END LOOP;
    FOR check_row IN
      SELECT value FROM jsonb_array_elements(p_request->'payload'->'source_checks')
    LOOP
      IF NOT app.jsonb_has_exact_keys(
           check_row, ARRAY['host','status','content_hash','observed_at']
         ) OR check_row->>'host' NOT IN (
           'query1.finance.yahoo.com', 'www.sec.gov', 'finnhub.io'
         ) OR check_row->>'status' <> 'reachable'
         OR check_row->>'content_hash' !~ '^[0-9a-f]{64}$'
         OR jsonb_typeof(check_row->'observed_at') <> 'string' THEN
        RAISE EXCEPTION 'handshake verification failed' USING ERRCODE = '42501';
      END IF;
      BEGIN
        check_observed_at := (check_row->>'observed_at')::timestamptz;
      EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
        RAISE EXCEPTION 'handshake verification failed' USING ERRCODE = '42501';
      END;
      IF check_observed_at < clock_timestamp() - interval '15 minutes'
         OR check_observed_at > clock_timestamp() + interval '5 minutes' THEN
        RAISE EXCEPTION 'handshake verification failed' USING ERRCODE = '42501';
      END IF;
    END LOOP;
  ELSIF NOT app.jsonb_has_exact_keys(p_request->'payload', ARRAY[]::text[]) THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  IF handshake_run AND NOT EXISTS (
    SELECT 1 FROM app.market_gateway_requests
    WHERE owner_id = owner_uuid AND run_id = run_uuid
      AND operation = 'read_bounded_context' AND status = 'completed'
  ) THEN
    final_status := 'partial';
  END IF;
  response := jsonb_build_object(
    'status', CASE WHEN (claim->>'dry_run')::boolean THEN 'dry_run' ELSE final_status END,
    'run_id', run_uuid,
    'operations', jsonb_build_object(
      'completed', completed_count, 'failed', failed_count, 'in_progress', claimed_count
    ),
    'telegram', jsonb_build_object('status', telegram_status, 'message_ids', telegram_ids)
  );
  IF (claim->>'dry_run')::boolean THEN RETURN response; END IF;
  UPDATE app.analysis_runs SET status = final_status, finished_at = clock_timestamp(),
    write_counts = jsonb_build_object('gateway_operations', completed_count),
    telegram_message_ids = telegram_ids,
    summary = CASE
      WHEN telegram_status = 'delivered' THEN 'Run completed with a recorded Telegram delivery.'
      WHEN telegram_status = 'delivery_failed' THEN 'Run finished partially; Telegram rejected the message.'
      WHEN telegram_status = 'delivery_unknown' THEN 'Run finished partially; Telegram delivery is unknown.'
      WHEN final_status = 'completed' THEN 'Run completed; no Telegram send was recorded.'
      ELSE 'Run finished partially; no Telegram send was recorded.'
    END
  WHERE owner_id = owner_uuid AND id = run_uuid;
  IF handshake_run AND final_status = 'completed' THEN
    UPDATE app.scheduled_run_slots SET status = 'completed',
      handshake_receipt = p_request->'payload', updated_at = clock_timestamp()
    WHERE owner_id = owner_uuid AND canonical_run_id = run_uuid AND purpose = 'handshake';
    UPDATE app.agent_connections SET status = 'ready', last_handshake_at = clock_timestamp(),
      updated_at = clock_timestamp()
    WHERE owner_id = owner_uuid AND id = (claim->>'connection_id')::uuid AND status = 'testing';
  END IF;
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
$$;

ALTER FUNCTION machine.agent_constant_time_equal(BYTEA, BYTEA) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_claim_operation(JSONB, TEXT) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_complete_operation(UUID, UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_start_run(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_build_owner_context(UUID, UUID, TEXT, DATE, TIMESTAMPTZ) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_read_bounded_context(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_submit_analysis(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_apply_analysis(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_finish_analysis_delivery(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_record_permitted_artifacts(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_grade_due_decisions(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_finish_run(JSONB) OWNER TO stock_agent_migration_owner;

REVOKE ALL ON FUNCTION machine.agent_constant_time_equal(BYTEA, BYTEA) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_claim_operation(JSONB, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_complete_operation(UUID, UUID, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_start_run(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_build_owner_context(UUID, UUID, TEXT, DATE, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_read_bounded_context(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_submit_analysis(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_apply_analysis(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_finish_analysis_delivery(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_record_permitted_artifacts(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_grade_due_decisions(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_finish_run(JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION machine.agent_start_run(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_read_bounded_context(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_submit_analysis(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_apply_analysis(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_finish_analysis_delivery(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_record_permitted_artifacts(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_grade_due_decisions(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_finish_run(JSONB) TO stock_agent_gateway;

-- Canonical market-session slots and the release-one Claude Routine trigger adapter.

ALTER TABLE app.agent_connections ADD COLUMN trigger_url TEXT CHECK (
  trigger_url IS NULL OR trigger_url ~
    '^https://api\.anthropic\.com/v1/claude_code/routines/trig_[A-Za-z0-9]{6,128}/fire$'
);

CREATE TABLE app.scheduled_run_slots (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  connection_id UUID NOT NULL,
  market_date DATE NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  purpose TEXT NOT NULL DEFAULT 'scheduled' CHECK (purpose IN ('scheduled','handshake')),
  handshake_challenge BYTEA,
  handshake_receipt JSONB,
  due_at TIMESTAMPTZ NOT NULL,
  window_ends_at TIMESTAMPTZ NOT NULL,
  holiday BOOLEAN NOT NULL DEFAULT false,
  trigger_request_id UUID NOT NULL DEFAULT extensions.gen_random_uuid(),
  canonical_run_id UUID,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
    'pending','claimed','triggered','trigger_failed','trigger_unknown',
    'provider_started','completed','missed','holiday_ready','budget_suppressed'
  )),
  lease_token UUID,
  lease_expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, trigger_request_id),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, connection_id)
    REFERENCES app.agent_connections(owner_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (owner_id, canonical_run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE RESTRICT,
  CHECK (window_ends_at > due_at),
  CHECK ((status = 'pending' AND lease_token IS NULL) OR status <> 'pending'),
  CHECK ((purpose = 'handshake' AND phase = 'on-demand' AND NOT holiday)
      OR (purpose = 'scheduled' AND phase <> 'on-demand')),
  CHECK ((purpose = 'handshake' AND octet_length(handshake_challenge) = 32)
      OR (purpose = 'scheduled' AND handshake_challenge IS NULL)),
  CHECK (handshake_receipt IS NULL OR purpose = 'handshake')
);

CREATE TABLE app.routine_trigger_attempts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  slot_id UUID NOT NULL,
  connection_id UUID NOT NULL,
  trigger_request_id UUID NOT NULL,
  status TEXT NOT NULL DEFAULT 'claimed' CHECK (status IN (
    'claimed','triggered','trigger_failed','trigger_unknown','provider_started'
  )),
  response_status INT CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
  provider_session_url TEXT CHECK (
    provider_session_url IS NULL OR provider_session_url ~
      '^https://claude\.ai/code/session_[A-Za-z0-9_-]{6,200}$'
  ),
  response_digest TEXT CHECK (response_digest IS NULL OR response_digest ~ '^[0-9a-f]{64}$'),
  claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  UNIQUE (owner_id, slot_id),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, slot_id)
    REFERENCES app.scheduled_run_slots(owner_id, id) ON DELETE CASCADE,
  FOREIGN KEY (owner_id, connection_id)
    REFERENCES app.agent_connections(owner_id, id) ON DELETE RESTRICT
);

CREATE TABLE app.owner_run_allowances (
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  allowance_date DATE NOT NULL,
  invocation_count INT NOT NULL DEFAULT 0 CHECK (invocation_count BETWEEN 0 AND 6),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id, allowance_date)
);

CREATE TABLE app.operational_events (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  code TEXT NOT NULL CHECK (code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
  period_key TEXT NOT NULL CHECK (char_length(period_key) BETWEEN 1 AND 32),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','notified','resolved')),
  detail JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (
    jsonb_typeof(detail) = 'object' AND octet_length(detail::text) <= 2000
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, code, period_key),
  UNIQUE (owner_id, id)
);

CREATE TABLE app.owner_operational_state (
  owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  mutations_paused BOOLEAN NOT NULL DEFAULT false,
  reason_code TEXT CHECK (
    reason_code IS NULL OR reason_code IN ('LEDGER_PROJECTION_MISMATCH')
  ),
  last_projection_check_at TIMESTAMPTZ,
  last_projection_ok BOOLEAN,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (mutations_paused = (reason_code IS NOT NULL))
);

CREATE TABLE app.operational_alerts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  event_id UUID NOT NULL,
  code TEXT NOT NULL CHECK (code IN (
    'EXPECTED_RUN_MISSED','PROVIDER_DISCONNECTED','LEDGER_PROJECTION_MISMATCH',
    'RUN_PARTIAL','BACKUP_STALE'
  )),
  status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN (
    'ready','sending','delivered','suppressed','delivery_failed','delivery_unknown'
  )),
  telegram_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(telegram_message_ids) = 'array'
    AND jsonb_array_length(telegram_message_ids) <= 4
  ),
  attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
  lease_token UUID,
  sending_started_at TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ,
  error_code TEXT CHECK (error_code IS NULL OR error_code IN (
    'TELEGRAM_REJECTED','TELEGRAM_OUTCOME_UNKNOWN','DELIVERY_LEASE_EXPIRED'
  )),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, event_id),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, event_id)
    REFERENCES app.operational_events(owner_id, id) ON DELETE CASCADE,
  CHECK ((status = 'sending') = (lease_token IS NOT NULL)),
  CHECK (status <> 'sending' OR sending_started_at IS NOT NULL),
  CHECK (status <> 'delivered' OR (
    delivered_at IS NOT NULL AND jsonb_array_length(telegram_message_ids) > 0
  ))
);

ALTER TABLE app.scheduled_run_slots OWNER TO stock_agent_migration_owner;
ALTER TABLE app.routine_trigger_attempts OWNER TO stock_agent_migration_owner;
ALTER TABLE app.owner_run_allowances OWNER TO stock_agent_migration_owner;
ALTER TABLE app.operational_events OWNER TO stock_agent_migration_owner;
ALTER TABLE app.owner_operational_state OWNER TO stock_agent_migration_owner;
ALTER TABLE app.operational_alerts OWNER TO stock_agent_migration_owner;
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.scheduled_run_slots
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.routine_trigger_attempts
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.owner_run_allowances
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.operational_events
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.owner_operational_state
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.operational_alerts
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
ALTER TABLE app.scheduled_run_slots ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.scheduled_run_slots FORCE ROW LEVEL SECURITY;
ALTER TABLE app.routine_trigger_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.routine_trigger_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE app.owner_run_allowances ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.owner_run_allowances FORCE ROW LEVEL SECURITY;
ALTER TABLE app.operational_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.operational_events FORCE ROW LEVEL SECURITY;
ALTER TABLE app.owner_operational_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.owner_operational_state FORCE ROW LEVEL SECURITY;
ALTER TABLE app.operational_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.operational_alerts FORCE ROW LEVEL SECURITY;
CREATE POLICY scheduled_run_slots_executor_all ON app.scheduled_run_slots
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY routine_trigger_attempts_executor_all ON app.routine_trigger_attempts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY owner_run_allowances_executor_all ON app.owner_run_allowances
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY operational_events_executor_all ON app.operational_events
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY owner_operational_state_executor_all ON app.owner_operational_state
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY operational_alerts_executor_all ON app.operational_alerts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
REVOKE ALL ON app.scheduled_run_slots, app.routine_trigger_attempts,
  app.owner_run_allowances, app.operational_events, app.owner_operational_state,
  app.operational_alerts
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE INDEX scheduled_run_slots_due_idx ON app.scheduled_run_slots(status, due_at, window_ends_at);
CREATE UNIQUE INDEX scheduled_run_slots_canonical_idx
  ON app.scheduled_run_slots(owner_id, market_date, phase) WHERE purpose = 'scheduled';
CREATE INDEX routine_trigger_attempts_status_idx ON app.routine_trigger_attempts(status, claimed_at);
CREATE INDEX operational_events_status_idx ON app.operational_events(status, created_at);
CREATE INDEX operational_alerts_status_idx ON app.operational_alerts(status, created_at);

CREATE OR REPLACE FUNCTION app.enforce_owner_mutation_safety()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM app.owner_operational_state
    WHERE owner_id = NEW.owner_id AND mutations_paused
  ) THEN
    RAISE EXCEPTION 'owner mutations are paused' USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END
$$;
ALTER FUNCTION app.enforce_owner_mutation_safety() OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.enforce_owner_mutation_safety()
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE TRIGGER enforce_owner_mutation_safety
  BEFORE UPDATE OF status ON app.portfolio_commands
  FOR EACH ROW
  WHEN (NEW.status IN ('confirmed','applied'))
  EXECUTE FUNCTION app.enforce_owner_mutation_safety();
CREATE TRIGGER enforce_owner_ledger_safety
  BEFORE INSERT ON app.transactions
  FOR EACH ROW
  WHEN (NEW.event_type IN ('trade','void') AND NEW.source_channel <> 'migration')
  EXECUTE FUNCTION app.enforce_owner_mutation_safety();

CREATE TABLE machine.market_calendar_days (
  market_date DATE PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('open','holiday')),
  early_close BOOLEAN NOT NULL DEFAULT false,
  reviewed BOOLEAN NOT NULL DEFAULT false,
  CHECK (status = 'open' OR NOT early_close)
);
CREATE TABLE machine.routine_budget_config (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  monthly_limit INT NOT NULL CHECK (monthly_limit BETWEEN 1 AND 100000),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE machine.routine_monthly_usage (
  usage_month DATE PRIMARY KEY CHECK (usage_month = date_trunc('month', usage_month)::date),
  invocation_count INT NOT NULL DEFAULT 0 CHECK (invocation_count >= 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE machine.market_calendar_days OWNER TO stock_agent_migration_owner;
ALTER TABLE machine.routine_budget_config OWNER TO stock_agent_migration_owner;
ALTER TABLE machine.routine_monthly_usage OWNER TO stock_agent_migration_owner;
REVOKE ALL ON machine.market_calendar_days, machine.routine_budget_config,
  machine.routine_monthly_usage FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
INSERT INTO machine.routine_budget_config(singleton, monthly_limit) VALUES (true, 1000);
INSERT INTO machine.market_calendar_days(market_date, status, early_close, reviewed)
SELECT day_value::date,
       CASE WHEN day_value::date = ANY(ARRAY[
         DATE '2026-01-01', DATE '2026-01-19', DATE '2026-02-16', DATE '2026-04-03',
         DATE '2026-05-25', DATE '2026-06-19', DATE '2026-07-03', DATE '2026-09-07',
         DATE '2026-11-26', DATE '2026-12-25'
       ]) THEN 'holiday' ELSE 'open' END,
       day_value::date = ANY(ARRAY[DATE '2026-07-02', DATE '2026-11-27', DATE '2026-12-24']),
       true
FROM generate_series(DATE '2026-01-01', DATE '2026-12-31', interval '1 day') AS day_value
WHERE extract(isodow FROM day_value) BETWEEN 1 AND 5;

CREATE OR REPLACE FUNCTION machine.scheduler_claim_due_slots(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  effective_now TIMESTAMPTZ;
  claim_limit INT;
  market_day DATE;
  calendar_row machine.market_calendar_days%ROWTYPE;
  calendar_available BOOLEAN;
  candidate app.scheduled_run_slots%ROWTYPE;
  budget_limit INT;
  usage_month_value DATE;
  owner_consumed BOOLEAN;
  product_consumed BOOLEAN;
  attempt_uuid UUID;
  slots JSONB := '[]'::jsonb;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['now','limit'])
     OR jsonb_typeof(p_request->'now') <> 'string'
     OR p_request->>'limit' !~ '^\d{1,2}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN effective_now := (p_request->>'now')::timestamptz;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  claim_limit := (p_request->>'limit')::int;
  IF claim_limit NOT BETWEEN 1 AND 20 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  market_day := (effective_now AT TIME ZONE 'America/New_York')::date;
  SELECT * INTO calendar_row FROM machine.market_calendar_days
  WHERE market_date = market_day AND reviewed;
  calendar_available := FOUND;

  IF calendar_available AND calendar_row.status = 'holiday' THEN
    INSERT INTO app.scheduled_run_slots(
      owner_id, connection_id, market_date, phase, due_at, window_ends_at, holiday
    )
    SELECT schedule.owner_id, connection.id, market_day, 'pre-market',
      (market_day + TIME '09:30') AT TIME ZONE 'America/New_York' - interval '120 minutes',
      (market_day + TIME '09:30') AT TIME ZONE 'America/New_York' - interval '60 minutes', true
    FROM app.analysis_schedules AS schedule
    JOIN app.agent_connections AS connection
      ON connection.owner_id = schedule.owner_id AND connection.id = schedule.primary_connection_id
    WHERE schedule.pre_market_enabled AND connection.status = 'active'
      AND connection.provider = 'claude' AND connection.trigger_url IS NOT NULL
    ON CONFLICT (owner_id, market_date, phase) WHERE purpose = 'scheduled' DO NOTHING;
  ELSIF calendar_available THEN
    INSERT INTO app.scheduled_run_slots(
      owner_id, connection_id, market_date, phase, due_at, window_ends_at, holiday
    )
    SELECT schedule.owner_id, connection.id, market_day, phase_value,
      CASE phase_value
        WHEN 'pre-market' THEN (market_day + TIME '09:30') AT TIME ZONE 'America/New_York' - interval '120 minutes'
        WHEN 'intraday' THEN (market_day + TIME '09:30') AT TIME ZONE 'America/New_York'
          + CASE WHEN calendar_row.early_close THEN interval '120 minutes' ELSE interval '210 minutes' END
        ELSE (market_day + CASE WHEN calendar_row.early_close THEN TIME '13:00' ELSE TIME '16:00' END)
          AT TIME ZONE 'America/New_York' + interval '10 minutes'
      END,
      CASE phase_value
        WHEN 'pre-market' THEN (market_day + TIME '09:30') AT TIME ZONE 'America/New_York' - interval '60 minutes'
        WHEN 'intraday' THEN (market_day + TIME '09:30') AT TIME ZONE 'America/New_York'
          + CASE WHEN calendar_row.early_close THEN interval '180 minutes' ELSE interval '270 minutes' END
        ELSE (market_day + CASE WHEN calendar_row.early_close THEN TIME '13:00' ELSE TIME '16:00' END)
          AT TIME ZONE 'America/New_York' + interval '70 minutes'
      END,
      false
    FROM app.analysis_schedules AS schedule
    JOIN app.agent_connections AS connection
      ON connection.owner_id = schedule.owner_id AND connection.id = schedule.primary_connection_id
    CROSS JOIN unnest(ARRAY['pre-market','intraday','post-market']) AS phase_value
    WHERE connection.status = 'active' AND connection.provider = 'claude'
      AND connection.trigger_url IS NOT NULL
      AND CASE phase_value WHEN 'pre-market' THEN schedule.pre_market_enabled
          WHEN 'intraday' THEN schedule.intraday_enabled ELSE schedule.post_market_enabled END
    ON CONFLICT (owner_id, market_date, phase) WHERE purpose = 'scheduled' DO NOTHING;
  END IF;

  SELECT monthly_limit INTO budget_limit FROM machine.routine_budget_config WHERE singleton;
  usage_month_value := date_trunc('month', market_day)::date;
  FOR candidate IN
    SELECT * FROM app.scheduled_run_slots
    WHERE market_date = market_day AND status = 'pending'
      AND (purpose = 'handshake' OR calendar_available)
      AND due_at <= effective_now AND window_ends_at >= effective_now
    ORDER BY due_at, owner_id
    LIMIT claim_limit FOR UPDATE SKIP LOCKED
  LOOP
    IF candidate.holiday THEN
      UPDATE app.scheduled_run_slots SET status = 'claimed', lease_token = extensions.gen_random_uuid(),
        lease_expires_at = candidate.window_ends_at, updated_at = clock_timestamp()
      WHERE owner_id = candidate.owner_id AND id = candidate.id;
      slots := slots || jsonb_build_array(jsonb_build_object(
        'slot_id', candidate.id, 'trigger_request_id', candidate.trigger_request_id,
        'phase', candidate.phase, 'market_date', candidate.market_date,
        'holiday', true, 'attempt_id', null
      ));
      CONTINUE;
    END IF;

    INSERT INTO machine.routine_monthly_usage(usage_month) VALUES (usage_month_value)
      ON CONFLICT (usage_month) DO NOTHING;
    UPDATE machine.routine_monthly_usage SET invocation_count = invocation_count + 1,
      updated_at = clock_timestamp()
    WHERE routine_monthly_usage.usage_month = usage_month_value
      AND invocation_count < budget_limit;
    product_consumed := FOUND;
    IF NOT product_consumed THEN
      UPDATE app.scheduled_run_slots SET status = 'budget_suppressed', updated_at = clock_timestamp()
      WHERE owner_id = candidate.owner_id AND id = candidate.id;
      INSERT INTO app.operational_events(owner_id, code, period_key)
      VALUES (candidate.owner_id, 'ROUTINE_MONTHLY_BUDGET_REACHED', to_char(usage_month_value, 'YYYY-MM'))
      ON CONFLICT (owner_id, code, period_key) DO NOTHING;
      CONTINUE;
    END IF;

    INSERT INTO app.owner_run_allowances(owner_id, allowance_date)
    VALUES (candidate.owner_id, market_day)
    ON CONFLICT (owner_id, allowance_date) DO NOTHING;
    UPDATE app.owner_run_allowances SET invocation_count = invocation_count + 1,
      updated_at = clock_timestamp()
    WHERE owner_id = candidate.owner_id AND allowance_date = market_day
      AND invocation_count < 6;
    owner_consumed := FOUND;
    IF NOT owner_consumed THEN
      UPDATE machine.routine_monthly_usage SET invocation_count = invocation_count - 1,
        updated_at = clock_timestamp() WHERE routine_monthly_usage.usage_month = usage_month_value;
      UPDATE app.scheduled_run_slots SET status = 'budget_suppressed', updated_at = clock_timestamp()
      WHERE owner_id = candidate.owner_id AND id = candidate.id;
      INSERT INTO app.operational_events(owner_id, code, period_key)
      VALUES (candidate.owner_id, 'OWNER_DAILY_RUN_LIMIT_REACHED', market_day::text)
      ON CONFLICT (owner_id, code, period_key) DO NOTHING;
      CONTINUE;
    END IF;

    attempt_uuid := extensions.gen_random_uuid();
    UPDATE app.scheduled_run_slots SET status = 'claimed', lease_token = extensions.gen_random_uuid(),
      lease_expires_at = candidate.window_ends_at, updated_at = clock_timestamp()
    WHERE owner_id = candidate.owner_id AND id = candidate.id;
    INSERT INTO app.routine_trigger_attempts(
      id, owner_id, slot_id, connection_id, trigger_request_id
    ) VALUES (
      attempt_uuid, candidate.owner_id, candidate.id, candidate.connection_id,
      candidate.trigger_request_id
    );
    slots := slots || jsonb_build_array(jsonb_build_object(
      'slot_id', candidate.id, 'trigger_request_id', candidate.trigger_request_id,
      'phase', candidate.phase, 'market_date', candidate.market_date,
      'holiday', false, 'attempt_id', attempt_uuid
    ));
  END LOOP;
  RETURN jsonb_build_object(
    'slots', slots,
    'calendar_status', CASE WHEN calendar_available THEN calendar_row.status ELSE 'unavailable' END
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_read_trigger_secret(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  attempt_uuid UUID;
  endpoint_value TEXT;
  token_value TEXT;
  request_uuid UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['attempt_id']) THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN attempt_uuid := (p_request->>'attempt_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  SELECT connection.trigger_url, secret.secret, attempt.trigger_request_id
  INTO endpoint_value, token_value, request_uuid
  FROM app.routine_trigger_attempts AS attempt
  JOIN app.scheduled_run_slots AS slot
    ON slot.owner_id = attempt.owner_id AND slot.id = attempt.slot_id
  JOIN app.agent_connections AS connection
    ON connection.owner_id = attempt.owner_id AND connection.id = attempt.connection_id
  JOIN vault.decrypted_secrets AS secret ON secret.id = connection.outbound_trigger_secret_id
  WHERE attempt.id = attempt_uuid AND attempt.status = 'claimed'
    AND slot.status = 'claimed'
    AND (
      connection.status = 'active'
      OR (connection.status = 'testing' AND slot.purpose = 'handshake')
    );
  IF NOT FOUND OR endpoint_value !~
    '^https://api\.anthropic\.com/v1/claude_code/routines/trig_[A-Za-z0-9]{6,128}/fire$'
    OR token_value IS NULL OR char_length(token_value) NOT BETWEEN 24 AND 500
    OR token_value ~ '\s' THEN
    RAISE EXCEPTION 'scheduler trigger unavailable' USING ERRCODE = '42501';
  END IF;
  RETURN jsonb_build_object('endpoint', endpoint_value, 'token', token_value,
    'trigger_request_id', request_uuid);
END
$$;

GRANT USAGE ON SCHEMA vault TO stock_agent_migration_owner;
GRANT SELECT ON vault.decrypted_secrets TO stock_agent_migration_owner;

CREATE OR REPLACE FUNCTION machine.scheduler_record_trigger_result(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  attempt_uuid UUID;
  attempt_row app.routine_trigger_attempts%ROWTYPE;
  status_value TEXT;
  response_code INT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['attempt_id','status','response_status','session_url','response_digest']
  ) OR p_request->>'status' NOT IN ('triggered','trigger_failed','trigger_unknown')
     OR p_request->>'response_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    attempt_uuid := (p_request->>'attempt_id')::uuid;
    response_code := CASE WHEN p_request->'response_status' = 'null'::jsonb THEN NULL
      ELSE (p_request->>'response_status')::int END;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  IF response_code IS NOT NULL AND response_code NOT BETWEEN 100 AND 599 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  IF p_request->'session_url' <> 'null'::jsonb AND p_request->>'session_url' !~
    '^https://claude\.ai/code/session_[A-Za-z0-9_-]{6,200}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO attempt_row FROM app.routine_trigger_attempts
  WHERE id = attempt_uuid FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'scheduler trigger unavailable' USING ERRCODE = '42501'; END IF;
  status_value := p_request->>'status';
  IF attempt_row.status <> 'claimed' THEN
    IF attempt_row.status = 'provider_started' AND attempt_row.response_digest IS NULL THEN
      UPDATE app.routine_trigger_attempts SET
        response_status = response_code,
        provider_session_url = CASE WHEN p_request->'session_url' = 'null'::jsonb THEN NULL ELSE p_request->>'session_url' END,
        response_digest = p_request->>'response_digest', finished_at = clock_timestamp()
      WHERE id = attempt_uuid;
      RETURN jsonb_build_object('status', 'provider_started', 'duplicate', false);
    END IF;
    IF attempt_row.status = status_value
       AND attempt_row.response_status IS NOT DISTINCT FROM response_code
       AND attempt_row.provider_session_url IS NOT DISTINCT FROM nullif(p_request->>'session_url', '')
       AND attempt_row.response_digest = p_request->>'response_digest' THEN
      RETURN jsonb_build_object('status', attempt_row.status, 'duplicate', true);
    END IF;
    RAISE EXCEPTION 'scheduler result conflict' USING ERRCODE = '23505';
  END IF;
  UPDATE app.routine_trigger_attempts SET status = status_value,
    response_status = response_code,
    provider_session_url = CASE WHEN p_request->'session_url' = 'null'::jsonb THEN NULL ELSE p_request->>'session_url' END,
    response_digest = p_request->>'response_digest', finished_at = clock_timestamp()
  WHERE id = attempt_uuid;
  UPDATE app.scheduled_run_slots SET status = status_value, updated_at = clock_timestamp()
  WHERE owner_id = attempt_row.owner_id AND id = attempt_row.slot_id AND status = 'claimed';
  RETURN jsonb_build_object('status', status_value, 'duplicate', false);
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_publish_holiday(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  slot_uuid UUID;
  slot_row app.scheduled_run_slots%ROWTYPE;
  publication_uuid UUID;
  body_value TEXT := '🏛 Market closed today — US public holiday. No brief.';
  body_digest TEXT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['slot_id']) THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN slot_uuid := (p_request->>'slot_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  SELECT * INTO slot_row FROM app.scheduled_run_slots
  WHERE id = slot_uuid AND holiday AND phase = 'pre-market' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'scheduler slot unavailable' USING ERRCODE = '42501'; END IF;
  SELECT id INTO publication_uuid FROM app.market_publications
  WHERE owner_id = slot_row.owner_id AND market_date = slot_row.market_date
    AND phase = 'pre-market' AND kind = 'holiday';
  IF FOUND THEN RETURN jsonb_build_object('status', 'ready', 'publication_id', publication_uuid, 'duplicate', true); END IF;
  IF slot_row.status <> 'claimed' THEN
    RAISE EXCEPTION 'scheduler slot unavailable' USING ERRCODE = '42501';
  END IF;
  body_digest := encode(extensions.digest(convert_to(body_value, 'UTF8'), 'sha256'), 'hex');
  INSERT INTO app.market_gateway_requests(
    owner_id, request_id, operation, connection_id, input_digest, status,
    lease_token, response, response_digest, finished_at
  ) VALUES (
    slot_row.owner_id, slot_row.trigger_request_id, 'start_run', slot_row.connection_id,
    encode(extensions.digest(convert_to(slot_row.id::text, 'UTF8'), 'sha256'), 'hex'),
    'completed', extensions.gen_random_uuid(), jsonb_build_object('holiday', true),
    encode(extensions.digest(convert_to('{"holiday": true}', 'UTF8'), 'sha256'), 'hex'), clock_timestamp()
  );
  publication_uuid := extensions.gen_random_uuid();
  INSERT INTO app.market_publications(
    owner_id, id, idempotency_key, run_id, market_date, phase, kind,
    template_version, rendered_body, rendered_parts, rendered_hash, status
  ) VALUES (
    slot_row.owner_id, publication_uuid, slot_row.trigger_request_id, NULL,
    slot_row.market_date, 'pre-market', 'holiday', 1, body_value,
    jsonb_build_array(body_value), body_digest, 'ready'
  );
  UPDATE app.scheduled_run_slots SET status = 'holiday_ready', updated_at = clock_timestamp()
  WHERE owner_id = slot_row.owner_id AND id = slot_uuid;
  RETURN jsonb_build_object('status', 'ready', 'publication_id', publication_uuid, 'duplicate', false);
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_run_maintenance(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  effective_now TIMESTAMPTZ;
  expired_commands INT := 0;
  expired_pairing_codes INT := 0;
  expired_callback_tokens INT := 0;
  deleted_updates INT := 0;
  missed_slots INT := 0;
  stale_publications INT := 0;
  stale_operational_alerts INT := 0;
  projection_failures INT := 0;
  alerts_created INT := 0;
  publication_row app.market_publications%ROWTYPE;
  slot_row app.scheduled_run_slots%ROWTYPE;
  owner_row RECORD;
  identity_row RECORD;
  holding_row app.holdings%ROWTYPE;
  folded JSONB;
  mismatch BOOLEAN;
  event_uuid UUID;
  final_response JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['now'])
     OR jsonb_typeof(p_request->'now') <> 'string' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN effective_now := (p_request->>'now')::timestamptz;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  IF effective_now < clock_timestamp() - interval '1 day'
     OR effective_now > clock_timestamp() + interval '5 minutes' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;

  WITH expired AS (
    SELECT owner_id, id FROM app.portfolio_commands
    WHERE status IN ('submitted','previewed') AND expires_at <= effective_now
    ORDER BY expires_at, id LIMIT 200 FOR UPDATE SKIP LOCKED
  )
  UPDATE app.portfolio_commands AS command SET
    status = 'expired',
    preview_digest = coalesce(
      command.preview_digest,
      encode(extensions.digest(convert_to('expired-unpreviewed:' || command.id::text, 'UTF8'), 'sha256'), 'hex')
    ),
    updated_at = effective_now
  FROM expired
  WHERE command.owner_id = expired.owner_id AND command.id = expired.id;
  GET DIAGNOSTICS expired_commands = ROW_COUNT;

  WITH expired AS (
    SELECT owner_id, id FROM app.telegram_pairing_codes
    WHERE consumed_at IS NULL AND expires_at <= effective_now
    ORDER BY expires_at, id LIMIT 200 FOR UPDATE SKIP LOCKED
  )
  UPDATE app.telegram_pairing_codes AS code SET consumed_at = effective_now
  FROM expired WHERE code.owner_id = expired.owner_id AND code.id = expired.id;
  GET DIAGNOSTICS expired_pairing_codes = ROW_COUNT;
  WITH expired AS (
    SELECT owner_id, id FROM app.telegram_callback_tokens
    WHERE consumed_at IS NULL AND invalidated_at IS NULL AND expires_at <= effective_now
    ORDER BY expires_at, id LIMIT 400 FOR UPDATE SKIP LOCKED
  )
  UPDATE app.telegram_callback_tokens AS token SET invalidated_at = effective_now
  FROM expired WHERE token.owner_id = expired.owner_id AND token.id = expired.id;
  GET DIAGNOSTICS expired_callback_tokens = ROW_COUNT;
  WITH expired AS (
    SELECT owner_id, telegram_update_id FROM app.telegram_updates
    WHERE received_at < effective_now - interval '30 days'
    ORDER BY received_at, owner_id, telegram_update_id LIMIT 500
  )
  DELETE FROM app.telegram_updates AS update_row USING expired
  WHERE update_row.owner_id = expired.owner_id
    AND update_row.telegram_update_id = expired.telegram_update_id;
  GET DIAGNOSTICS deleted_updates = ROW_COUNT;

  FOR publication_row IN
    SELECT * FROM app.market_publications
    WHERE status = 'sending'
      AND sending_started_at < effective_now - interval '5 minutes'
    ORDER BY sending_started_at, id LIMIT 100
    FOR UPDATE
  LOOP
    UPDATE app.market_publications SET
      status = 'delivery_unknown', lease_token = NULL,
      error = 'DELIVERY_LEASE_EXPIRED', updated_at = effective_now
    WHERE owner_id = publication_row.owner_id AND id = publication_row.id;
    IF publication_row.run_id IS NOT NULL THEN
      final_response := jsonb_build_object(
        'status', 'accepted', 'run_id', publication_row.run_id,
        'publication_id', publication_row.id,
        'publication_status', 'delivery_unknown',
        'telegram_message_ids', publication_row.telegram_message_ids
      );
      UPDATE app.market_gateway_requests SET
        status = 'completed', response = final_response,
        response_digest = encode(extensions.digest(convert_to(final_response::text, 'UTF8'), 'sha256'), 'hex'),
        finished_at = effective_now
      WHERE owner_id = publication_row.owner_id
        AND request_id = publication_row.idempotency_key AND status = 'claimed';
      UPDATE app.analysis_runs SET
        telegram_message_ids = publication_row.telegram_message_ids,
        source_status = source_status || jsonb_build_object('publication', 'delivery_unknown')
      WHERE owner_id = publication_row.owner_id AND id = publication_row.run_id;
    END IF;
    stale_publications := stale_publications + 1;
  END LOOP;

  WITH stale AS (
    SELECT owner_id, id FROM app.operational_alerts
    WHERE status = 'sending'
      AND sending_started_at < effective_now - interval '5 minutes'
    ORDER BY sending_started_at, id LIMIT 100 FOR UPDATE SKIP LOCKED
  )
  UPDATE app.operational_alerts AS alert SET
    status = 'delivery_unknown', lease_token = NULL,
    error_code = 'DELIVERY_LEASE_EXPIRED', updated_at = effective_now
  FROM stale WHERE alert.owner_id = stale.owner_id AND alert.id = stale.id;
  GET DIAGNOSTICS stale_operational_alerts = ROW_COUNT;

  FOR slot_row IN
    SELECT * FROM app.scheduled_run_slots
    WHERE status IN (
      'pending','claimed','triggered','trigger_failed','trigger_unknown','provider_started'
    ) AND window_ends_at < effective_now
    ORDER BY window_ends_at, id LIMIT 100
    FOR UPDATE
  LOOP
    UPDATE app.scheduled_run_slots SET
      status = 'missed', lease_token = NULL, lease_expires_at = NULL,
      updated_at = effective_now
    WHERE owner_id = slot_row.owner_id AND id = slot_row.id;
    missed_slots := missed_slots + 1;
    IF slot_row.purpose = 'scheduled' THEN
      INSERT INTO app.operational_events(owner_id, code, period_key)
      VALUES (
        slot_row.owner_id, 'EXPECTED_RUN_MISSED',
        slot_row.market_date::text || ':' || slot_row.phase
      ) ON CONFLICT (owner_id, code, period_key) DO NOTHING;
    END IF;
  END LOOP;

  INSERT INTO app.operational_events(owner_id, code, period_key)
  SELECT schedule.owner_id, 'PROVIDER_DISCONNECTED',
         (effective_now AT TIME ZONE 'America/New_York')::date::text
  FROM app.analysis_schedules AS schedule
  JOIN app.agent_connections AS connection
    ON connection.owner_id = schedule.owner_id AND connection.id = schedule.primary_connection_id
  JOIN app.profiles AS profile ON profile.id = schedule.owner_id AND profile.status = 'active'
  WHERE connection.status <> 'active'
    AND NOT EXISTS (
      SELECT 1 FROM app.operational_events AS existing
      WHERE existing.owner_id = schedule.owner_id
        AND existing.code = 'PROVIDER_DISCONNECTED'
        AND existing.period_key = (effective_now AT TIME ZONE 'America/New_York')::date::text
    )
  ORDER BY schedule.owner_id LIMIT 100
  ON CONFLICT (owner_id, code, period_key) DO NOTHING;

  INSERT INTO app.operational_events(owner_id, code, period_key)
  SELECT run.owner_id, 'RUN_PARTIAL', substr(md5(run.id::text), 1, 32)
  FROM app.analysis_runs AS run
  WHERE run.status = 'partial'
    AND NOT EXISTS (
      SELECT 1 FROM app.operational_events AS existing
      WHERE existing.owner_id = run.owner_id AND existing.code = 'RUN_PARTIAL'
        AND existing.period_key = substr(md5(run.id::text), 1, 32)
    )
  ORDER BY run.finished_at NULLS FIRST, run.id LIMIT 100
  ON CONFLICT (owner_id, code, period_key) DO NOTHING;

  FOR owner_row IN
    SELECT profile.id
    FROM app.profiles AS profile
    LEFT JOIN app.owner_operational_state AS state ON state.owner_id = profile.id
    WHERE profile.status = 'active'
      AND (
        state.last_projection_check_at IS NULL
        OR (state.last_projection_check_at AT TIME ZONE 'America/New_York')::date
           < (effective_now AT TIME ZONE 'America/New_York')::date
    )
    ORDER BY profile.id LIMIT 20
  LOOP
    mismatch := false;
    BEGIN
      FOR identity_row IN
        SELECT ticker FROM app.holdings WHERE owner_id = owner_row.id
        UNION
        SELECT ticker FROM app.transactions
        WHERE owner_id = owner_row.id AND event_type IN ('opening','trade','void')
        ORDER BY ticker
      LOOP
        folded := app.fold_holding(owner_row.id, identity_row.ticker);
        SELECT * INTO holding_row FROM app.holdings
        WHERE owner_id = owner_row.id AND ticker = identity_row.ticker;
        IF (folded->>'shares')::numeric = 0 THEN
          IF FOUND THEN mismatch := true; EXIT; END IF;
        ELSIF NOT FOUND
           OR holding_row.shares IS DISTINCT FROM (folded->>'shares')::numeric
           OR holding_row.avg_cost IS DISTINCT FROM (folded->>'avg_cost')::numeric
           OR holding_row.projection_sequence IS DISTINCT FROM (folded->>'projection_sequence')::bigint THEN
          mismatch := true;
          EXIT;
        END IF;
      END LOOP;
    EXCEPTION WHEN OTHERS THEN
      mismatch := true;
    END;
    INSERT INTO app.owner_operational_state(
      owner_id, mutations_paused, reason_code, last_projection_check_at,
      last_projection_ok, updated_at
    ) VALUES (
      owner_row.id, mismatch,
      CASE WHEN mismatch THEN 'LEDGER_PROJECTION_MISMATCH' ELSE NULL END,
      effective_now, NOT mismatch, effective_now
    ) ON CONFLICT (owner_id) DO UPDATE SET
      mutations_paused = excluded.mutations_paused,
      reason_code = excluded.reason_code,
      last_projection_check_at = excluded.last_projection_check_at,
      last_projection_ok = excluded.last_projection_ok,
      updated_at = excluded.updated_at;
    IF mismatch THEN
      projection_failures := projection_failures + 1;
      INSERT INTO app.operational_events(owner_id, code, period_key)
      VALUES (
        owner_row.id, 'LEDGER_PROJECTION_MISMATCH',
        (effective_now AT TIME ZONE 'America/New_York')::date::text
      ) ON CONFLICT (owner_id, code, period_key) DO NOTHING;
    ELSE
      UPDATE app.operational_events SET status = 'resolved', updated_at = effective_now
      WHERE owner_id = owner_row.id AND code = 'LEDGER_PROJECTION_MISMATCH' AND status = 'open';
      UPDATE app.operational_alerts SET status = 'suppressed', updated_at = effective_now
      WHERE owner_id = owner_row.id AND code = 'LEDGER_PROJECTION_MISMATCH' AND status = 'ready';
    END IF;
  END LOOP;

  INSERT INTO app.operational_alerts(owner_id, event_id, code)
  SELECT event.owner_id, event.id, event.code
  FROM app.operational_events AS event
  WHERE event.status = 'open' AND event.code IN (
    'EXPECTED_RUN_MISSED','PROVIDER_DISCONNECTED','LEDGER_PROJECTION_MISMATCH',
    'RUN_PARTIAL','BACKUP_STALE'
  )
    AND NOT EXISTS (
      SELECT 1 FROM app.operational_alerts AS existing
      WHERE existing.owner_id = event.owner_id AND existing.event_id = event.id
    )
  ORDER BY event.created_at, event.id LIMIT 100
  ON CONFLICT (owner_id, event_id) DO NOTHING;
  GET DIAGNOSTICS alerts_created = ROW_COUNT;

  RETURN jsonb_build_object(
    'expired_commands', expired_commands,
    'expired_pairing_codes', expired_pairing_codes,
    'expired_callback_tokens', expired_callback_tokens,
    'deleted_telegram_updates', deleted_updates,
    'missed_slots', missed_slots,
    'stale_publications', stale_publications,
    'stale_operational_alerts', stale_operational_alerts,
    'projection_failures', projection_failures,
    'alerts_created', alerts_created
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_claim_publications(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  effective_now TIMESTAMPTZ;
  claim_limit INT;
  publication_row app.market_publications%ROWTYPE;
  lease_uuid UUID;
  chat_value BIGINT;
  enabled_value BOOLEAN;
  publications JSONB := '[]'::jsonb;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['now','limit'])
     OR p_request->>'limit' !~ '^\d{1,2}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    effective_now := (p_request->>'now')::timestamptz;
    claim_limit := (p_request->>'limit')::int;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  IF claim_limit NOT BETWEEN 1 AND 20 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  FOR publication_row IN
    SELECT * FROM app.market_publications
    WHERE status = 'ready'
    ORDER BY created_at, id LIMIT claim_limit FOR UPDATE SKIP LOCKED
  LOOP
    SELECT telegram_chat_id INTO chat_value FROM app.telegram_links
    WHERE owner_id = publication_row.owner_id AND status = 'active';
    SELECT CASE publication_row.phase
      WHEN 'pre-market' THEN pre_market_enabled
      WHEN 'intraday' THEN intraday_enabled
      WHEN 'post-market' THEN post_market_enabled
      ELSE false END
    INTO enabled_value FROM app.notification_preferences
    WHERE owner_id = publication_row.owner_id;
    IF chat_value IS NULL OR NOT coalesce(enabled_value, false) THEN
      UPDATE app.market_publications SET status = 'suppressed', updated_at = effective_now
      WHERE owner_id = publication_row.owner_id AND id = publication_row.id;
      CONTINUE;
    END IF;
    lease_uuid := extensions.gen_random_uuid();
    UPDATE app.market_publications SET
      status = 'sending', lease_token = lease_uuid,
      sending_started_at = effective_now, attempt_count = attempt_count + 1,
      error = NULL, updated_at = effective_now
    WHERE owner_id = publication_row.owner_id AND id = publication_row.id;
    publications := publications || jsonb_build_array(jsonb_build_object(
      'publication_id', publication_row.id, 'lease_token', lease_uuid,
      'chat_id', chat_value::text, 'parts', publication_row.rendered_parts
    ));
  END LOOP;
  RETURN jsonb_build_object('publications', publications);
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_finish_publication(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  publication_uuid UUID;
  lease_uuid UUID;
  status_value TEXT;
  message_ids JSONB;
  publication_row app.market_publications%ROWTYPE;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
       p_request, ARRAY['publication_id','lease_token','status','message_ids']
     ) OR p_request->>'status' NOT IN ('delivered','delivery_failed','delivery_unknown')
       OR jsonb_typeof(p_request->'message_ids') <> 'array'
       OR jsonb_array_length(p_request->'message_ids') > 4 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    publication_uuid := (p_request->>'publication_id')::uuid;
    lease_uuid := (p_request->>'lease_token')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  status_value := p_request->>'status';
  message_ids := p_request->'message_ids';
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(message_ids) item
    WHERE jsonb_typeof(item) <> 'number' OR (item::text)::bigint <= 0
  ) OR (status_value = 'delivered' AND jsonb_array_length(message_ids) = 0)
     OR (status_value = 'delivery_failed' AND jsonb_array_length(message_ids) <> 0) THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO publication_row FROM app.market_publications
  WHERE id = publication_uuid FOR UPDATE;
  IF NOT FOUND OR publication_row.status <> 'sending'
     OR publication_row.lease_token <> lease_uuid THEN
    RAISE EXCEPTION 'publication lease unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.market_publications SET
    status = status_value, telegram_message_ids = message_ids, lease_token = NULL,
    delivered_at = CASE WHEN status_value = 'delivered' THEN clock_timestamp() ELSE NULL END,
    error = CASE status_value
      WHEN 'delivery_failed' THEN 'TELEGRAM_REJECTED'
      WHEN 'delivery_unknown' THEN 'TELEGRAM_OUTCOME_UNKNOWN'
      ELSE NULL END,
    updated_at = clock_timestamp()
  WHERE owner_id = publication_row.owner_id AND id = publication_uuid;
  RETURN jsonb_build_object(
    'status', status_value, 'publication_id', publication_uuid,
    'telegram_message_ids', message_ids
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_claim_operational_alerts(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  effective_now TIMESTAMPTZ;
  claim_limit INT;
  alert_row app.operational_alerts%ROWTYPE;
  lease_uuid UUID;
  chat_value BIGINT;
  enabled_value BOOLEAN;
  alerts JSONB := '[]'::jsonb;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['now','limit'])
     OR p_request->>'limit' !~ '^\d{1,2}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    effective_now := (p_request->>'now')::timestamptz;
    claim_limit := (p_request->>'limit')::int;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  IF claim_limit NOT BETWEEN 1 AND 20 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  FOR alert_row IN
    SELECT * FROM app.operational_alerts
    WHERE status = 'ready'
    ORDER BY created_at, id LIMIT claim_limit FOR UPDATE SKIP LOCKED
  LOOP
    SELECT operational_enabled INTO enabled_value FROM app.notification_preferences
    WHERE owner_id = alert_row.owner_id;
    SELECT telegram_chat_id INTO chat_value FROM app.telegram_links
    WHERE owner_id = alert_row.owner_id AND status = 'active';
    IF chat_value IS NULL OR NOT coalesce(enabled_value, false) THEN
      UPDATE app.operational_alerts SET status = 'suppressed', updated_at = effective_now
      WHERE owner_id = alert_row.owner_id AND id = alert_row.id;
      UPDATE app.operational_events SET status = 'resolved', updated_at = effective_now
      WHERE owner_id = alert_row.owner_id AND id = alert_row.event_id;
      CONTINUE;
    END IF;
    lease_uuid := extensions.gen_random_uuid();
    UPDATE app.operational_alerts SET
      status = 'sending', lease_token = lease_uuid,
      sending_started_at = effective_now, attempt_count = attempt_count + 1,
      error_code = NULL, updated_at = effective_now
    WHERE owner_id = alert_row.owner_id AND id = alert_row.id;
    alerts := alerts || jsonb_build_array(jsonb_build_object(
      'alert_id', alert_row.id, 'lease_token', lease_uuid,
      'chat_id', chat_value::text, 'code', alert_row.code
    ));
  END LOOP;
  RETURN jsonb_build_object('alerts', alerts);
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_finish_operational_alert(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  alert_uuid UUID;
  lease_uuid UUID;
  status_value TEXT;
  message_ids JSONB;
  alert_row app.operational_alerts%ROWTYPE;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
       p_request, ARRAY['alert_id','lease_token','status','message_ids']
     ) OR p_request->>'status' NOT IN ('delivered','delivery_failed','delivery_unknown')
       OR jsonb_typeof(p_request->'message_ids') <> 'array'
       OR jsonb_array_length(p_request->'message_ids') > 1 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    alert_uuid := (p_request->>'alert_id')::uuid;
    lease_uuid := (p_request->>'lease_token')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  status_value := p_request->>'status';
  message_ids := p_request->'message_ids';
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(message_ids) item
    WHERE jsonb_typeof(item) <> 'number' OR (item::text)::bigint <= 0
  ) OR (status_value = 'delivered' AND jsonb_array_length(message_ids) <> 1)
     OR (status_value = 'delivery_failed' AND jsonb_array_length(message_ids) <> 0) THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO alert_row FROM app.operational_alerts
  WHERE id = alert_uuid FOR UPDATE;
  IF NOT FOUND OR alert_row.status <> 'sending' OR alert_row.lease_token <> lease_uuid THEN
    RAISE EXCEPTION 'operational alert lease unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.operational_alerts SET
    status = status_value, telegram_message_ids = message_ids, lease_token = NULL,
    delivered_at = CASE WHEN status_value = 'delivered' THEN clock_timestamp() ELSE NULL END,
    error_code = CASE status_value
      WHEN 'delivery_failed' THEN 'TELEGRAM_REJECTED'
      WHEN 'delivery_unknown' THEN 'TELEGRAM_OUTCOME_UNKNOWN'
      ELSE NULL END,
    updated_at = clock_timestamp()
  WHERE owner_id = alert_row.owner_id AND id = alert_uuid;
  UPDATE app.operational_events SET
    status = CASE WHEN status_value = 'delivered' THEN 'notified' ELSE 'open' END,
    updated_at = clock_timestamp()
  WHERE owner_id = alert_row.owner_id AND id = alert_row.event_id;
  RETURN jsonb_build_object(
    'status', status_value, 'alert_id', alert_uuid,
    'telegram_message_ids', message_ids
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_read_due_decisions(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  effective_now TIMESTAMPTZ;
  claim_limit INT;
  due_rows JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['now','limit'])
     OR p_request->>'limit' !~ '^\d{1,2}$' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    effective_now := (p_request->>'now')::timestamptz;
    claim_limit := (p_request->>'limit')::int;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END;
  IF claim_limit NOT BETWEEN 1 AND 20
     OR effective_now < clock_timestamp() - interval '1 day'
     OR effective_now > clock_timestamp() + interval '5 minutes' THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  SELECT coalesce(jsonb_agg(row_value ORDER BY decision_date, suggestion_id), '[]'::jsonb)
  INTO due_rows
  FROM (
    SELECT suggestion.date AS decision_date, suggestion.id AS suggestion_id,
      jsonb_build_object(
        'owner_id', suggestion.owner_id,
        'suggestion_id', suggestion.id,
        'decision_date', suggestion.date,
        'ticker', suggestion.ticker,
        'bucket', suggestion.bucket,
        'final_action', evaluation.final_action,
        'confidence', suggestion.confidence,
        'policy_version', evaluation.policy_version,
        'decision_price', suggestion.price_at_suggestion::text,
        'entry_zone_low', suggestion.entry_zone_low::text,
        'entry_zone_high', suggestion.entry_zone_high::text,
        'stop', suggestion.stop::text,
        'target', suggestion.target::text,
        'invalidation_price', suggestion.invalidation_price::text,
        'completed_horizons', coalesce(
          to_jsonb(array_agg(DISTINCT grade.horizon_days ORDER BY grade.horizon_days)
            FILTER (WHERE grade.coverage_status = 'complete'
              OR (grade.graded_at AT TIME ZONE 'America/New_York')::date
                 = (effective_now AT TIME ZONE 'America/New_York')::date)),
          '[]'::jsonb
        )
      ) AS row_value
    FROM app.suggestions AS suggestion
    JOIN app.decision_evaluations AS evaluation
      ON evaluation.owner_id = suggestion.owner_id AND evaluation.id = suggestion.evaluation_id
    LEFT JOIN app.suggestion_grades AS grade
      ON grade.owner_id = suggestion.owner_id AND grade.suggestion_id = suggestion.id
    JOIN app.profiles AS profile
      ON profile.id = suggestion.owner_id AND profile.status = 'active'
    WHERE suggestion.decision_source = 'gateway'
      AND evaluation.policy_version IS NOT NULL
      AND suggestion.bucket IN ('core','growth','speculative')
      AND suggestion.confidence IN ('low','medium','high')
      AND suggestion.price_at_suggestion IS NOT NULL
      AND evaluation.final_action IN ('buy','add','hold','reduce','sell','watch','avoid')
      AND suggestion.date >= (effective_now AT TIME ZONE 'America/New_York')::date - 370
    GROUP BY suggestion.owner_id, suggestion.id, suggestion.date, suggestion.ticker,
      suggestion.bucket, evaluation.final_action, suggestion.confidence,
      evaluation.policy_version, suggestion.price_at_suggestion,
      suggestion.entry_zone_low, suggestion.entry_zone_high, suggestion.stop,
      suggestion.target, suggestion.invalidation_price
    HAVING count(DISTINCT grade.horizon_days)
      FILTER (WHERE grade.coverage_status = 'complete' AND grade.horizon_days IN (5,21,63)) < 3
      AND count(DISTINCT grade.horizon_days)
      FILTER (WHERE grade.coverage_status <> 'complete'
        AND (grade.graded_at AT TIME ZONE 'America/New_York')::date
          = (effective_now AT TIME ZONE 'America/New_York')::date) < 3
    ORDER BY suggestion.date, suggestion.id
    LIMIT claim_limit
  ) due;
  RETURN jsonb_build_object('due', due_rows);
END
$$;

CREATE OR REPLACE FUNCTION machine.scheduler_apply_outcome_grades(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  grade_item JSONB;
  owner_uuid UUID;
  suggestion_value BIGINT;
  existing_grade app.suggestion_grades%ROWTYPE;
  ticker_value TEXT;
  decision_price_value NUMERIC;
  policy_version_value INT;
  final_action_value TEXT;
  horizon_value INT;
  sessions_value INT;
  status_value TEXT;
  expected_benchmark TEXT;
  numeric_key TEXT;
  had_existing BOOLEAN;
  inserted_count INT := 0;
  updated_count INT := 0;
  incomplete_count INT := 0;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['grades'])
     OR jsonb_typeof(p_request->'grades') <> 'array'
     OR jsonb_array_length(p_request->'grades') > 60 THEN
    RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
  END IF;
  FOR grade_item IN SELECT value FROM jsonb_array_elements(p_request->'grades')
  LOOP
    IF jsonb_typeof(grade_item) <> 'object'
       OR NOT app.jsonb_has_exact_keys(grade_item, ARRAY[
         'owner_id','suggestion_id','horizon_days','horizon_sessions','coverage_status',
         'benchmark_ticker','stock_return_pct','benchmark_return_pct','excess_return_pct',
         'mfe_pct','mae_pct','entry_hit_at','stop_hit_at','target_hit_at',
         'invalidation_hit_at','policy_version','final_action','direction_success'
       ])
       OR grade_item->>'suggestion_id' !~ '^[1-9][0-9]*$' THEN
      RAISE EXCEPTION 'invalid scheduler request' USING ERRCODE = '22023';
    END IF;
    BEGIN
      owner_uuid := (grade_item->>'owner_id')::uuid;
      suggestion_value := (grade_item->>'suggestion_id')::bigint;
      horizon_value := (grade_item->>'horizon_days')::int;
      sessions_value := (grade_item->>'horizon_sessions')::int;
      policy_version_value := (grade_item->>'policy_version')::int;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR null_value_not_allowed THEN
      RAISE EXCEPTION 'invalid outcome grade' USING ERRCODE = '22023';
    END;
    status_value := grade_item->>'coverage_status';
    IF horizon_value NOT IN (5, 21, 63)
       OR sessions_value NOT BETWEEN 0 AND horizon_value
       OR status_value NOT IN (
         'incomplete','complete','missing_history','missing_benchmark','corporate_action_review'
       )
       OR (status_value = 'complete' AND sessions_value <> horizon_value)
       OR grade_item->>'benchmark_ticker' NOT IN ('VOO','VXUS')
       OR jsonb_typeof(grade_item->'direction_success') NOT IN ('boolean','null')
       OR policy_version_value < 1
       OR grade_item->>'final_action' NOT IN ('buy','add','hold','reduce','sell','watch','avoid')
       OR EXISTS (
         SELECT 1 FROM unnest(ARRAY[
           grade_item->>'entry_hit_at', grade_item->>'stop_hit_at',
           grade_item->>'target_hit_at', grade_item->>'invalidation_hit_at'
         ]) date_text
         WHERE date_text IS NOT NULL AND date_text !~ '^\d{4}-\d{2}-\d{2}$'
       ) THEN
      RAISE EXCEPTION 'invalid outcome grade' USING ERRCODE = '22023';
    END IF;
    FOREACH numeric_key IN ARRAY ARRAY[
      'stock_return_pct','benchmark_return_pct','excess_return_pct','mfe_pct','mae_pct'
    ]
    LOOP
      IF grade_item->>numeric_key IS NOT NULL
         AND (grade_item->>numeric_key !~ '^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$'
           OR char_length(grade_item->>numeric_key) > 40) THEN
        RAISE EXCEPTION 'invalid outcome decimal' USING ERRCODE = '22023';
      END IF;
    END LOOP;

    SELECT suggestion.ticker, suggestion.price_at_suggestion,
           evaluation.policy_version, evaluation.final_action
    INTO ticker_value, decision_price_value, policy_version_value, final_action_value
    FROM app.suggestions AS suggestion
    JOIN app.decision_evaluations AS evaluation
      ON evaluation.owner_id = suggestion.owner_id
     AND evaluation.id = suggestion.evaluation_id
    WHERE suggestion.owner_id = owner_uuid
      AND suggestion.id = suggestion_value
      AND suggestion.decision_source = 'gateway';
    IF NOT FOUND
       OR policy_version_value IS DISTINCT FROM (grade_item->>'policy_version')::int
       OR final_action_value IS DISTINCT FROM grade_item->>'final_action' THEN
      RAISE EXCEPTION 'outcome provenance mismatch' USING ERRCODE = '22023';
    END IF;
    expected_benchmark := CASE WHEN ticker_value = 'VXUS' THEN 'VXUS' ELSE 'VOO' END;
    IF grade_item->>'benchmark_ticker' <> expected_benchmark THEN
      RAISE EXCEPTION 'outcome benchmark mismatch' USING ERRCODE = '22023';
    END IF;
    IF status_value = 'corporate_action_review' AND (
      grade_item->>'entry_hit_at' IS NOT NULL OR grade_item->>'stop_hit_at' IS NOT NULL
      OR grade_item->>'target_hit_at' IS NOT NULL
      OR grade_item->>'invalidation_hit_at' IS NOT NULL
    ) THEN
      RAISE EXCEPTION 'split outcome contains raw threshold result' USING ERRCODE = '22023';
    END IF;
    IF status_value IN ('missing_history','missing_benchmark') AND (
      grade_item->>'stock_return_pct' IS NOT NULL
      OR grade_item->>'benchmark_return_pct' IS NOT NULL
      OR grade_item->>'excess_return_pct' IS NOT NULL
      OR grade_item->>'mfe_pct' IS NOT NULL OR grade_item->>'mae_pct' IS NOT NULL
      OR grade_item->>'entry_hit_at' IS NOT NULL OR grade_item->>'stop_hit_at' IS NOT NULL
      OR grade_item->>'target_hit_at' IS NOT NULL
      OR grade_item->>'invalidation_hit_at' IS NOT NULL
      OR grade_item->'direction_success' <> 'null'::jsonb
    ) THEN
      RAISE EXCEPTION 'missing outcome contains result' USING ERRCODE = '22023';
    END IF;
    IF status_value = 'complete' AND (
      grade_item->>'stock_return_pct' IS NULL
      OR grade_item->>'benchmark_return_pct' IS NULL
      OR grade_item->>'excess_return_pct' IS NULL
      OR grade_item->>'mfe_pct' IS NULL OR grade_item->>'mae_pct' IS NULL
    ) THEN
      RAISE EXCEPTION 'complete outcome missing result' USING ERRCODE = '22023';
    END IF;
    IF grade_item->'direction_success' <> 'null'::jsonb AND (
      status_value <> 'complete' OR final_action_value NOT IN ('buy','add','reduce','sell')
    ) THEN
      RAISE EXCEPTION 'invalid directional outcome' USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(suggestion_value);
    SELECT * INTO existing_grade
    FROM app.suggestion_grades
    WHERE owner_id = owner_uuid AND suggestion_id = suggestion_value
      AND horizon_days = horizon_value
    FOR UPDATE;
    had_existing := FOUND;
    IF had_existing AND existing_grade.coverage_status = 'complete' THEN
      CONTINUE;
    END IF;

    INSERT INTO app.suggestion_grades(
      owner_id, suggestion_id, graded_at, result, price_then, horizon_days,
      benchmark_ticker, stock_return_pct, benchmark_return_pct, excess_return_pct,
      mfe_pct, mae_pct, entry_hit_at, stop_hit_at, target_hit_at,
      invalidation_hit_at, coverage_status, horizon_sessions, policy_version,
      final_action, direction_success, note
    ) VALUES (
      owner_uuid, suggestion_value, clock_timestamp(),
      CASE WHEN grade_item->'direction_success' = 'true'::jsonb THEN 'right'
           WHEN grade_item->'direction_success' = 'false'::jsonb THEN 'wrong'
           ELSE NULL END,
      decision_price_value, horizon_value, expected_benchmark,
      NULLIF(grade_item->>'stock_return_pct','')::numeric,
      NULLIF(grade_item->>'benchmark_return_pct','')::numeric,
      NULLIF(grade_item->>'excess_return_pct','')::numeric,
      NULLIF(grade_item->>'mfe_pct','')::numeric,
      NULLIF(grade_item->>'mae_pct','')::numeric,
      NULLIF(grade_item->>'entry_hit_at','')::date,
      NULLIF(grade_item->>'stop_hit_at','')::date,
      NULLIF(grade_item->>'target_hit_at','')::date,
      NULLIF(grade_item->>'invalidation_hit_at','')::date,
      status_value, sessions_value, policy_version_value, final_action_value,
      CASE WHEN grade_item->'direction_success' = 'null'::jsonb THEN NULL
           ELSE (grade_item->>'direction_success')::boolean END,
      'deterministic scheduler outcome'
    )
    ON CONFLICT (suggestion_id, horizon_days) DO UPDATE SET
      graded_at = EXCLUDED.graded_at,
      result = EXCLUDED.result,
      price_then = EXCLUDED.price_then,
      benchmark_ticker = EXCLUDED.benchmark_ticker,
      stock_return_pct = EXCLUDED.stock_return_pct,
      benchmark_return_pct = EXCLUDED.benchmark_return_pct,
      excess_return_pct = EXCLUDED.excess_return_pct,
      mfe_pct = EXCLUDED.mfe_pct,
      mae_pct = EXCLUDED.mae_pct,
      entry_hit_at = EXCLUDED.entry_hit_at,
      stop_hit_at = EXCLUDED.stop_hit_at,
      target_hit_at = EXCLUDED.target_hit_at,
      invalidation_hit_at = EXCLUDED.invalidation_hit_at,
      coverage_status = EXCLUDED.coverage_status,
      horizon_sessions = EXCLUDED.horizon_sessions,
      policy_version = EXCLUDED.policy_version,
      final_action = EXCLUDED.final_action,
      direction_success = EXCLUDED.direction_success,
      note = EXCLUDED.note;
    IF had_existing THEN
      updated_count := updated_count + 1;
    ELSE
      inserted_count := inserted_count + 1;
    END IF;
    IF status_value <> 'complete' THEN
      incomplete_count := incomplete_count + 1;
    END IF;
  END LOOP;
  RETURN jsonb_build_object(
    'inserted', inserted_count, 'updated', updated_count, 'incomplete', incomplete_count
  );
END
$$;

CREATE POLICY market_publications_executor_all ON app.market_publications
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

ALTER FUNCTION machine.scheduler_claim_due_slots(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_read_trigger_secret(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_record_trigger_result(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_publish_holiday(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_run_maintenance(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_claim_publications(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_finish_publication(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_claim_operational_alerts(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_finish_operational_alert(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_read_due_decisions(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.scheduler_apply_outcome_grades(JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION machine.scheduler_claim_due_slots(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_read_trigger_secret(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_record_trigger_result(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_publish_holiday(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_run_maintenance(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_claim_publications(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_finish_publication(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_claim_operational_alerts(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_finish_operational_alert(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_read_due_decisions(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.scheduler_apply_outcome_grades(JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION machine.scheduler_claim_due_slots(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_read_trigger_secret(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_record_trigger_result(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_publish_holiday(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_run_maintenance(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_claim_publications(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_finish_publication(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_claim_operational_alerts(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_finish_operational_alert(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_read_due_decisions(JSONB) TO stock_agent_scheduler;
GRANT EXECUTE ON FUNCTION machine.scheduler_apply_outcome_grades(JSONB) TO stock_agent_scheduler;

-- User-owned provider connection lifecycle. Plain inbound secrets never enter SQL; the
-- outbound Routine token is accepted once and immediately moved into Vault.

CREATE TABLE machine.connection_policy (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  current_consent_version TEXT NOT NULL CHECK (char_length(current_consent_version) BETWEEN 3 AND 100),
  contract_version INT NOT NULL CHECK (contract_version = 2)
);
ALTER TABLE machine.connection_policy OWNER TO stock_agent_migration_owner;
REVOKE ALL ON machine.connection_policy FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
INSERT INTO machine.connection_policy(singleton, current_consent_version, contract_version)
VALUES (true, 'provider-data-v1', 2);

GRANT EXECUTE ON FUNCTION vault.create_secret(TEXT, TEXT, TEXT) TO stock_agent_migration_owner;
GRANT EXECUTE ON FUNCTION vault.delete_secret(UUID) TO stock_agent_migration_owner;

CREATE OR REPLACE FUNCTION app.create_agent_connection(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  consent_version_value TEXT;
  connection_uuid UUID := extensions.gen_random_uuid();
  public_uuid UUID := extensions.gen_random_uuid();
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['provider','consent_version','inbound_token_digest']
  ) OR p_request->>'provider' <> 'claude'
     OR p_request->>'inbound_token_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END IF;
  SELECT current_consent_version INTO consent_version_value
  FROM machine.connection_policy WHERE singleton;
  IF p_request->>'consent_version' <> consent_version_value
     OR NOT EXISTS (
       SELECT 1 FROM app.user_consents
       WHERE owner_id = p_owner_id AND document_version = consent_version_value
     ) OR NOT EXISTS (
       SELECT 1 FROM app.profiles WHERE id = p_owner_id AND status = 'active'
     ) THEN
    RAISE EXCEPTION 'current provider consent is required' USING ERRCODE = '42501';
  END IF;
  INSERT INTO app.agent_connections(
    id, owner_id, public_id, provider, credential_type, inbound_token_digest,
    capabilities, contract_version, status
  ) VALUES (
    connection_uuid, p_owner_id, public_uuid, 'claude', 'claude_routine_v1',
    decode(p_request->>'inbound_token_digest', 'hex'),
    jsonb_build_object(
      'suggestion_only', true, 'bounded_context', true,
      'analyst_checker', true, 'contract_version', 2
    ), 2, 'disabled'
  );
  RETURN jsonb_build_object(
    'connection_id', connection_uuid, 'public_id', public_uuid,
    'provider', 'claude', 'status', 'disabled', 'contract_version', 2
  );
END
$$;

CREATE OR REPLACE FUNCTION app.begin_agent_connection_handshake(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_uuid UUID;
  connection_row app.agent_connections%ROWTYPE;
  vault_uuid UUID;
  previous_vault_uuid UUID;
  slot_uuid UUID;
  trigger_uuid UUID;
  consent_version_value TEXT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['connection_id','trigger_url','trigger_token']
  ) OR p_request->>'trigger_url' !~
       '^https://api\.anthropic\.com/v1/claude_code/routines/trig_[A-Za-z0-9]{6,128}/fire$'
     OR char_length(p_request->>'trigger_token') NOT BETWEEN 24 AND 500
     OR p_request->>'trigger_token' ~ '\s' THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END IF;
  BEGIN connection_uuid := (p_request->>'connection_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END;
  SELECT * INTO connection_row FROM app.agent_connections
  WHERE owner_id = p_owner_id AND id = connection_uuid FOR UPDATE;
  IF NOT FOUND OR connection_row.status NOT IN ('disabled','testing') THEN
    RAISE EXCEPTION 'connection unavailable' USING ERRCODE = '42501';
  END IF;
  IF connection_row.status = 'testing' THEN
    SELECT id, trigger_request_id INTO slot_uuid, trigger_uuid
    FROM app.scheduled_run_slots
    WHERE owner_id = p_owner_id AND connection_id = connection_uuid
      AND purpose = 'handshake'
      AND status IN ('pending','claimed','triggered','trigger_unknown','provider_started')
    ORDER BY created_at DESC LIMIT 1;
    IF FOUND THEN
      RETURN jsonb_build_object('connection_id', connection_uuid, 'status', 'testing',
        'handshake_id', slot_uuid, 'trigger_request_id', trigger_uuid, 'duplicate', true);
    END IF;
  END IF;
  SELECT current_consent_version INTO consent_version_value
  FROM machine.connection_policy WHERE singleton;
  IF NOT EXISTS (
    SELECT 1 FROM app.user_consents
    WHERE owner_id = p_owner_id AND document_version = consent_version_value
  ) THEN
    RAISE EXCEPTION 'current provider consent is required' USING ERRCODE = '42501';
  END IF;
  vault_uuid := vault.create_secret(
    p_request->>'trigger_token', 'routine-trigger-' || connection_uuid::text,
    'User-owned Claude Routine trigger token'
  );
  previous_vault_uuid := connection_row.outbound_trigger_secret_id;
  UPDATE app.agent_connections SET
    outbound_trigger_secret_id = vault_uuid,
    trigger_url = p_request->>'trigger_url', status = 'testing',
    updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND id = connection_uuid;
  IF previous_vault_uuid IS NOT NULL THEN
    PERFORM vault.delete_secret(previous_vault_uuid);
  END IF;
  slot_uuid := extensions.gen_random_uuid();
  trigger_uuid := extensions.gen_random_uuid();
  INSERT INTO app.scheduled_run_slots(
    id, owner_id, connection_id, market_date, phase, purpose, handshake_challenge, due_at,
    window_ends_at, trigger_request_id, status
  ) VALUES (
    slot_uuid, p_owner_id, connection_uuid,
    (clock_timestamp() AT TIME ZONE 'America/New_York')::date,
    'on-demand', 'handshake', extensions.gen_random_bytes(32), clock_timestamp(),
    clock_timestamp() + interval '1 hour', trigger_uuid, 'pending'
  );
  RETURN jsonb_build_object('connection_id', connection_uuid, 'status', 'testing',
    'handshake_id', slot_uuid, 'trigger_request_id', trigger_uuid, 'duplicate', false);
END
$$;

CREATE OR REPLACE FUNCTION app.activate_agent_connection(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_uuid UUID;
  consent_version_value TEXT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['connection_id']) THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END IF;
  BEGIN connection_uuid := (p_request->>'connection_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END;
  SELECT current_consent_version INTO consent_version_value
  FROM machine.connection_policy WHERE singleton;
  IF NOT EXISTS (
    SELECT 1 FROM app.user_consents
    WHERE owner_id = p_owner_id AND document_version = consent_version_value
  ) OR NOT EXISTS (
    SELECT 1 FROM app.agent_connections
    WHERE owner_id = p_owner_id AND id = connection_uuid AND status = 'ready'
      AND last_handshake_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'connection unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.agent_connections SET status = 'ready', updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND id <> connection_uuid AND status = 'active';
  UPDATE app.agent_connections SET status = 'active', updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND id = connection_uuid;
  INSERT INTO app.analysis_schedules(owner_id, primary_connection_id)
  VALUES (p_owner_id, connection_uuid)
  ON CONFLICT (owner_id) DO UPDATE SET primary_connection_id = excluded.primary_connection_id,
    updated_at = clock_timestamp();
  RETURN jsonb_build_object('connection_id', connection_uuid, 'status', 'active');
END
$$;

CREATE OR REPLACE FUNCTION app.revoke_agent_connection(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_uuid UUID;
  vault_uuid UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['connection_id']) THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END IF;
  BEGIN connection_uuid := (p_request->>'connection_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid connection request' USING ERRCODE = '22023';
  END;
  SELECT outbound_trigger_secret_id INTO vault_uuid
  FROM app.agent_connections WHERE owner_id = p_owner_id AND id = connection_uuid FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'connection unavailable' USING ERRCODE = '42501'; END IF;
  UPDATE app.analysis_schedules SET primary_connection_id = NULL, updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND primary_connection_id = connection_uuid;
  UPDATE app.scheduled_run_slots SET status = 'missed', updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND connection_id = connection_uuid
    AND status IN ('pending','claimed','triggered','trigger_unknown');
  UPDATE app.routine_trigger_attempts SET status = 'trigger_failed', finished_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND connection_id = connection_uuid AND status = 'claimed';
  UPDATE app.agent_connections SET status = 'revoked', inbound_token_digest = NULL,
    outbound_trigger_secret_id = NULL, trigger_url = NULL, updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND id = connection_uuid;
  IF vault_uuid IS NOT NULL THEN PERFORM vault.delete_secret(vault_uuid); END IF;
  RETURN jsonb_build_object('connection_id', connection_uuid, 'status', 'revoked');
END
$$;

ALTER FUNCTION app.create_agent_connection(UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.begin_agent_connection_handshake(UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.activate_agent_connection(UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.revoke_agent_connection(UUID, JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.create_agent_connection(UUID, JSONB) FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION app.begin_agent_connection_handshake(UUID, JSONB) FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION app.activate_agent_connection(UUID, JSONB) FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION app.revoke_agent_connection(UUID, JSONB) FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
