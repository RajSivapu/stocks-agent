-- One browser RPC boundary: authenticated dispatch, database-backed limits, and body-free audit.

CREATE TABLE app.app_api_rate_limits (
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  scope TEXT NOT NULL CHECK (char_length(scope) BETWEEN 1 AND 80),
  client_key TEXT NOT NULL CHECK (
    client_key = 'owner' OR client_key ~ '^[0-9a-f]{64}$'
  ),
  window_started_at TIMESTAMPTZ NOT NULL,
  hit_count INT NOT NULL CHECK (hit_count > 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id, scope, client_key)
);
ALTER TABLE app.app_api_rate_limits OWNER TO stock_agent_migration_owner;
ALTER TABLE app.app_api_rate_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.app_api_rate_limits FORCE ROW LEVEL SECURITY;
CREATE POLICY app_api_rate_limits_executor_all ON app.app_api_rate_limits
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.app_api_rate_limits
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

CREATE TABLE app.app_api_audit_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  request_id UUID NOT NULL,
  route TEXT NOT NULL CHECK (char_length(route) BETWEEN 1 AND 100),
  result_code TEXT NOT NULL CHECK (result_code ~ '^[A-Z][A-Z0-9_]{1,99}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, request_id)
);
ALTER TABLE app.app_api_audit_events OWNER TO stock_agent_migration_owner;
ALTER TABLE app.app_api_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.app_api_audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY app_api_audit_events_executor_all ON app.app_api_audit_events
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.app_api_audit_events
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

REVOKE ALL ON app.app_api_rate_limits, app.app_api_audit_events
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON SEQUENCE app.app_api_audit_events_id_seq
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION app.consume_rate_limit(
  p_owner_id UUID,
  p_scope TEXT,
  p_client_key TEXT,
  p_limit INT,
  p_window_seconds INT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  observed_at TIMESTAMPTZ := clock_timestamp();
  window_value TIMESTAMPTZ;
  count_value INT;
  retry_value INT;
BEGIN
  IF p_owner_id IS NULL OR p_scope !~ '^[a-z][a-z0-9_]{0,79}$'
     OR (p_client_key <> 'owner' AND p_client_key !~ '^[0-9a-f]{64}$')
     OR p_limit NOT BETWEEN 1 AND 1000
     OR p_window_seconds NOT BETWEEN 1 AND 86400 THEN
    RAISE EXCEPTION 'invalid rate limit parameters';
  END IF;
  window_value := to_timestamp(
    floor(extract(epoch FROM observed_at) / p_window_seconds) * p_window_seconds
  );
  INSERT INTO app.app_api_rate_limits (
    owner_id, scope, client_key, window_started_at, hit_count, updated_at
  ) VALUES (
    p_owner_id, p_scope, p_client_key, window_value, 1, observed_at
  )
  ON CONFLICT (owner_id, scope, client_key) DO UPDATE
  SET window_started_at = CASE
        WHEN app.app_api_rate_limits.window_started_at = window_value
          THEN app.app_api_rate_limits.window_started_at
        ELSE window_value END,
      hit_count = CASE
        WHEN app.app_api_rate_limits.window_started_at = window_value
          THEN app.app_api_rate_limits.hit_count + 1
        ELSE 1 END,
      updated_at = observed_at
  RETURNING hit_count INTO count_value;
  retry_value := greatest(
    1,
    ceil(extract(epoch FROM window_value + make_interval(secs => p_window_seconds) - observed_at))::int
  );
  RETURN jsonb_build_object(
    'allowed', count_value <= p_limit,
    'retry_after_seconds', CASE WHEN count_value <= p_limit THEN 0 ELSE retry_value END
  );
END
$$;

CREATE OR REPLACE FUNCTION api.app_dispatch(
  p_route TEXT,
  p_request_id UUID,
  p_ip_digest TEXT,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID := auth.uid();
  scope_value TEXT;
  limit_value INT;
  window_value INT;
  owner_limit JSONB;
  client_limit JSONB;
  data_value JSONB;
  result_code TEXT;
BEGIN
  IF owner_value IS NULL OR p_request_id IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'UNAUTHORIZED'));
  END IF;
  IF jsonb_typeof(p_request) <> 'object' OR p_ip_digest !~ '^[0-9a-f]{64}$' THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'INVALID_REQUEST'));
  END IF;

  CASE p_route
    WHEN 'POST /portfolio/preview',
         'POST /portfolio/correction/preview',
         'POST /plans/preview' THEN
      scope_value := 'command_preview'; limit_value := 30; window_value := 60;
    WHEN 'POST /portfolio/confirm',
         'POST /portfolio/correction/confirm',
         'POST /plans/confirm' THEN
      scope_value := 'command_confirm'; limit_value := 20; window_value := 60;
    WHEN 'POST /telegram/pairing-code' THEN
      scope_value := 'telegram_pairing'; limit_value := 5; window_value := 600;
    WHEN 'POST /connections/create',
         'POST /connections/handshake',
         'POST /connections/activate',
         'POST /connections/revoke' THEN
      scope_value := 'connection_change'; limit_value := 10; window_value := 3600;
    WHEN 'GET /settings', 'PATCH /settings' THEN
      scope_value := 'settings'; limit_value := 30; window_value := 60;
    WHEN 'GET /export' THEN
      scope_value := 'export'; limit_value := 2; window_value := 86400;
    WHEN 'POST /account/delete/request', 'POST /account/delete/confirm' THEN
      scope_value := 'account_delete'; limit_value := 3; window_value := 86400;
    ELSE
      RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'ROUTE_NOT_ALLOWED'));
  END CASE;

  owner_limit := app.consume_rate_limit(
    owner_value, scope_value, 'owner', limit_value, window_value
  );
  client_limit := app.consume_rate_limit(
    owner_value, scope_value, p_ip_digest, limit_value, window_value
  );
  IF NOT (owner_limit->>'allowed')::boolean OR NOT (client_limit->>'allowed')::boolean THEN
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, 'RATE_LIMITED');
    RETURN jsonb_build_object(
      'ok', false,
      'error', jsonb_build_object(
        'code', 'RATE_LIMITED',
        'retry_after_seconds', greatest(
          (owner_limit->>'retry_after_seconds')::int,
          (client_limit->>'retry_after_seconds')::int
        )
      )
    );
  END IF;

  BEGIN
    CASE p_route
      WHEN 'POST /portfolio/preview',
           'POST /portfolio/correction/preview',
           'POST /plans/preview' THEN
        data_value := api.preview_portfolio_command(p_request);
      WHEN 'POST /portfolio/confirm',
           'POST /portfolio/correction/confirm',
           'POST /plans/confirm' THEN
        IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['command_id','preview_digest']) THEN
          RAISE EXCEPTION 'invalid confirmation request';
        END IF;
        data_value := api.confirm_portfolio_command(
          (p_request->>'command_id')::uuid,
          p_request->>'preview_digest'
        );
      WHEN 'POST /telegram/pairing-code' THEN
        IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['code_digest'])
           OR p_request->>'code_digest' !~ '^[0-9a-f]{64}$' THEN
          RAISE EXCEPTION 'invalid pairing code request';
        END IF;
        data_value := app.issue_telegram_pairing_code(
          owner_value, p_request->>'code_digest'
        );
      WHEN 'POST /connections/create' THEN
        data_value := app.create_agent_connection(owner_value, p_request);
      WHEN 'POST /connections/handshake' THEN
        data_value := app.begin_agent_connection_handshake(owner_value, p_request);
      WHEN 'POST /connections/activate' THEN
        data_value := app.activate_agent_connection(owner_value, p_request);
      WHEN 'POST /connections/revoke' THEN
        data_value := app.revoke_agent_connection(owner_value, p_request);
      ELSE
        INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
        VALUES (owner_value, p_request_id, p_route, 'NOT_READY');
        RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'NOT_READY'));
    END CASE;
    result_code := upper(coalesce(data_value->>'status', 'OK'));
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, result_code);
    RETURN jsonb_build_object('ok', true, 'data', data_value, 'request_id', p_request_id);
  EXCEPTION
    WHEN invalid_text_representation OR invalid_parameter_value OR check_violation
      OR numeric_value_out_of_range OR datetime_field_overflow THEN
      result_code := 'INVALID_REQUEST';
    WHEN unique_violation OR serialization_failure THEN
      result_code := 'CONFLICT';
    WHEN insufficient_privilege THEN
      result_code := 'NOT_FOUND';
    WHEN OTHERS THEN
      result_code := CASE
        WHEN SQLERRM ~* '(invalid|cash total|bucket|required|holding not found|sell exceeds|expired)'
          THEN 'INVALID_REQUEST'
        WHEN SQLERRM ~* '(stale|changed|digest|idempotency|cancelled|already)'
          THEN 'CONFLICT'
        ELSE 'INTERNAL_ERROR'
      END;
  END;
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, result_code);
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', result_code));
END
$$;

ALTER FUNCTION app.consume_rate_limit(UUID, TEXT, TEXT, INT, INT)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB)
  OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.consume_rate_limit(UUID, TEXT, TEXT, INT, INT)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION api.preview_portfolio_command(JSONB) FROM authenticated;
REVOKE ALL ON FUNCTION api.confirm_portfolio_command(UUID, TEXT) FROM authenticated;
REVOKE ALL ON FUNCTION api.cancel_portfolio_command(UUID, TEXT) FROM authenticated;
REVOKE ALL ON FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB)
  FROM PUBLIC, anon, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB) TO authenticated;
