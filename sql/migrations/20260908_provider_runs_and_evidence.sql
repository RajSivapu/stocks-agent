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

-- FORCE RLS also applies to the table owner. These policies are intentionally granted only
-- to the non-login migration owner used by reviewed SECURITY DEFINER functions.
CREATE POLICY agent_connections_executor_all ON app.agent_connections
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY analysis_runs_executor_all ON app.analysis_runs
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY market_gateway_requests_executor_all ON app.market_gateway_requests
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
  IF NOT connection_found OR NOT digest_matches OR connection_row.status <> 'active'
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
        'dry_run', false
      );
    END IF;
    RAISE EXCEPTION 'request replay conflict' USING ERRCODE = '23505';
  END IF;

  IF p_expected <> 'start_run' THEN
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
      'run_id', run_uuid, 'dry_run', true, 'provider', connection_row.provider
    );
  END IF;

  IF run_uuid IS NOT NULL AND (
    SELECT count(*) FROM app.market_gateway_requests
    WHERE owner_id = connection_row.owner_id AND run_id = run_uuid
      AND operation <> 'start_run'
  ) >= 12 THEN
    RAISE EXCEPTION 'run operation limit reached' USING ERRCODE = '54000';
  END IF;

  INSERT INTO app.market_gateway_requests(
    owner_id, request_id, operation, run_id, connection_id, input_digest,
    status, lease_token
  ) VALUES (
    connection_row.owner_id, request_uuid, p_expected, run_uuid,
    connection_row.id, request_digest, 'claimed', extensions.gen_random_uuid()
  );
  RETURN jsonb_build_object(
    'duplicate', false, 'owner_id', connection_row.owner_id,
    'connection_id', connection_row.id, 'request_id', request_uuid,
    'run_id', run_uuid, 'dry_run', false, 'provider', connection_row.provider
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
  response JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(payload, ARRAY['phase','market_date','trigger_request_id'])
     OR jsonb_typeof(payload->'phase') <> 'string'
     OR jsonb_typeof(payload->'market_date') <> 'string'
     OR payload->>'phase' NOT IN ('pre-market','intraday','post-market','on-demand')
     OR payload->>'market_date' !~ '^\d{4}-\d{2}-\d{2}$'
     OR (payload->'trigger_request_id' <> 'null'::jsonb
         AND jsonb_typeof(payload->'trigger_request_id') <> 'string') THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  BEGIN
    market_day := (payload->>'market_date')::date;
    IF payload->'trigger_request_id' <> 'null'::jsonb THEN
      PERFORM (payload->>'trigger_request_id')::uuid;
    END IF;
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END;

  claim := machine.agent_claim_operation(p_request, 'start_run');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  IF (claim->>'dry_run')::boolean THEN
    RETURN jsonb_build_object('status', 'dry_run', 'run_id', null, 'writes', 0);
  END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  connection_uuid := (claim->>'connection_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;
  phase_value := payload->>'phase';

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
  response := jsonb_build_object(
    'status', 'running', 'run_id', run_uuid, 'phase', phase_value,
    'market_date', market_day
  );
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
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
BEGIN
  claim := machine.agent_claim_operation(p_request, 'read_bounded_context');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  IF NOT app.jsonb_has_exact_keys(p_request->'payload', ARRAY[]::text[]) THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;

  SELECT jsonb_build_object(
    'run_id', run_uuid,
    'holdings', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT ticker, shares::text AS shares, avg_cost::text AS avg_cost, bucket,
             stop::text AS stop, target::text AS target,
             high_water_price::text AS high_water_price, hold_override_until
      FROM app.holdings WHERE owner_id = owner_uuid ORDER BY ticker LIMIT 40
    ) row_value),
    'plans', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, ticker, bucket, amount::text AS amount, cadence, next_due_on, active
      FROM app.owner_investment_plans WHERE owner_id = owner_uuid AND active
      ORDER BY next_due_on, ticker LIMIT 20
    ) row_value),
    'recent_suggestions', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT id, date, ticker, action, bucket, confidence, score, stop::text AS stop,
             target::text AS target, invalidation_price::text AS invalidation_price,
             valid_until, evidence_as_of
      FROM app.suggestions WHERE owner_id = owner_uuid ORDER BY ts DESC, id DESC LIMIT 20
    ) row_value),
    'radar', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT ticker, added, last_seen, days_relevant, reason, bucket_guess, promoted, promoted_on
      FROM app.radar WHERE owner_id = owner_uuid ORDER BY last_seen DESC NULLS LAST, ticker LIMIT 20
    ) row_value),
    'evidence', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT evidence_id, category, source_identifier, reference_identifier, observed_at,
             retrieved_at, revalidated_at, content_hash, claims, status
      FROM app.run_evidence WHERE owner_id = owner_uuid AND run_id = run_uuid
      ORDER BY retrieved_at DESC, evidence_id LIMIT 100
    ) row_value),
    'quotes', (SELECT coalesce(jsonb_agg(to_jsonb(row_value)), '[]'::jsonb) FROM (
      SELECT ticker, price::text AS price, previous_close::text AS previous_close,
             provider, source_timestamp, retrieved_at, session, adjustment_status,
             status, conflict_basis_points
      FROM app.market_quote_cache WHERE owner_id = owner_uuid
      ORDER BY ticker LIMIT 60
    ) row_value)
  ) INTO response;
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
  request_uuid UUID;
  run_started TIMESTAMPTZ;
  response JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(payload, ARRAY[
      'phase','market_date','title','suggestion_only','provider','model','analyst',
      'checker','dimensions','evidence_refs','prior_suggestion_ids','candidates'
    ]) OR payload->>'suggestion_only' <> 'true'
       OR octet_length(payload::text) > 65536
       OR jsonb_typeof(payload->'evidence_refs') <> 'array'
       OR jsonb_array_length(payload->'evidence_refs') > 100
       OR jsonb_typeof(payload->'candidates') <> 'array'
       OR jsonb_array_length(payload->'candidates') > 20 THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
  claim := machine.agent_claim_operation(p_request, 'submit_analysis');
  IF (claim->>'duplicate')::boolean THEN RETURN claim->'response'; END IF;
  owner_uuid := (claim->>'owner_id')::uuid;
  run_uuid := (claim->>'run_id')::uuid;
  request_uuid := (claim->>'request_id')::uuid;
  SELECT started_at INTO run_started FROM app.analysis_runs
  WHERE owner_id = owner_uuid AND id = run_uuid;

  IF NOT EXISTS (
    SELECT 1 FROM app.run_evidence
    WHERE owner_id = owner_uuid AND run_id = run_uuid
      AND category = 'market_snapshot' AND status = 'fresh'
      AND retrieved_at >= run_started
  ) THEN
    RAISE EXCEPTION 'evidence_missing' USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM app.source_search_receipts
    WHERE owner_id = owner_uuid AND run_id = run_uuid
      AND searched_at >= run_started AND result_status <> 'source_unavailable'
  ) THEN
    RAISE EXCEPTION 'evidence_missing' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(payload->'evidence_refs') AS reference
    LEFT JOIN app.run_evidence AS evidence
      ON evidence.owner_id = owner_uuid AND evidence.run_id = run_uuid
     AND evidence.evidence_id = reference->>'evidence_id'
     AND evidence.content_hash = reference->>'content_hash'
    WHERE reference->>'run_id' IS DISTINCT FROM run_uuid::text OR evidence.id IS NULL
  ) THEN
    RAISE EXCEPTION 'evidence_conflicting' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(payload->'candidates') AS candidate
    JOIN app.corporate_action_states AS action
      ON action.owner_id = owner_uuid AND action.ticker = upper(candidate->>'ticker')
    WHERE action.state IN ('suspected','needs_review')
  ) THEN
    RAISE EXCEPTION 'corporate_action_pending' USING ERRCODE = '22023';
  END IF;

  response := jsonb_build_object('status', 'accepted', 'run_id', run_uuid, 'submission_id', extensions.gen_random_uuid());
  IF (claim->>'dry_run')::boolean THEN
    RETURN response - 'submission_id' || jsonb_build_object('status', 'dry_run', 'writes', 0);
  END IF;
  INSERT INTO app.agent_analysis_submissions(
    id, owner_id, run_id, request_id, provider, model, phase, market_date,
    payload, payload_digest, status
  ) VALUES (
    (response->>'submission_id')::uuid, owner_uuid, run_uuid, request_uuid,
    payload->>'provider', payload->>'model', payload->>'phase', (payload->>'market_date')::date,
    payload, encode(extensions.digest(convert_to(payload::text, 'UTF8'), 'sha256'), 'hex'), 'accepted'
  );
  UPDATE app.analysis_runs
  SET provider = payload->>'provider', model = payload->>'model',
      data_as_of = (SELECT max(retrieved_at) FROM app.run_evidence
                    WHERE owner_id = owner_uuid AND run_id = run_uuid),
      source_status = jsonb_build_object('analysis', 'accepted')
  WHERE owner_id = owner_uuid AND id = run_uuid;
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
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
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request->'payload', ARRAY[]::text[]) THEN
    RAISE EXCEPTION 'invalid agent request' USING ERRCODE = '22023';
  END IF;
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
  final_status := CASE WHEN failed_count > 0 OR claimed_count > 0 THEN 'partial' ELSE 'completed' END;
  response := jsonb_build_object(
    'status', CASE WHEN (claim->>'dry_run')::boolean THEN 'dry_run' ELSE final_status END,
    'run_id', run_uuid,
    'operations', jsonb_build_object(
      'completed', completed_count, 'failed', failed_count, 'in_progress', claimed_count
    ),
    'telegram', jsonb_build_object('status', 'not_sent', 'message_ids', '[]'::jsonb)
  );
  IF (claim->>'dry_run')::boolean THEN RETURN response; END IF;
  UPDATE app.analysis_runs SET status = final_status, finished_at = clock_timestamp(),
    write_counts = jsonb_build_object('gateway_operations', completed_count),
    telegram_message_ids = '[]'::jsonb,
    summary = CASE WHEN final_status = 'completed' THEN 'Run completed; no Telegram send was recorded.'
                   ELSE 'Run finished partially; no Telegram send was recorded.' END
  WHERE owner_id = owner_uuid AND id = run_uuid;
  RETURN machine.agent_complete_operation(owner_uuid, request_uuid, response);
END
$$;

ALTER FUNCTION machine.agent_constant_time_equal(BYTEA, BYTEA) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_claim_operation(JSONB, TEXT) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_complete_operation(UUID, UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_start_run(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_read_bounded_context(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_submit_analysis(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_record_permitted_artifacts(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_grade_due_decisions(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.agent_finish_run(JSONB) OWNER TO stock_agent_migration_owner;

REVOKE ALL ON FUNCTION machine.agent_constant_time_equal(BYTEA, BYTEA) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_claim_operation(JSONB, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_complete_operation(UUID, UUID, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_start_run(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_read_bounded_context(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_submit_analysis(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_record_permitted_artifacts(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_grade_due_decisions(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION machine.agent_finish_run(JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION machine.agent_start_run(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_read_bounded_context(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_submit_analysis(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_record_permitted_artifacts(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_grade_due_decisions(JSONB) TO stock_agent_gateway;
GRANT EXECUTE ON FUNCTION machine.agent_finish_run(JSONB) TO stock_agent_gateway;
