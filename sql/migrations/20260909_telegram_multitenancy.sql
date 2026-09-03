-- Private-chat pairing, opaque callbacks, and update replay membership.

CREATE TABLE app.telegram_pairing_codes (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  code_digest BYTEA NOT NULL UNIQUE CHECK (octet_length(code_digest) = 32),
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id),
  CHECK (expires_at > created_at)
);
CREATE UNIQUE INDEX telegram_pairing_codes_one_active_owner_idx
  ON app.telegram_pairing_codes(owner_id) WHERE consumed_at IS NULL;
ALTER TABLE app.telegram_pairing_codes OWNER TO stock_agent_migration_owner;
ALTER TABLE app.telegram_pairing_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.telegram_pairing_codes FORCE ROW LEVEL SECURITY;
CREATE POLICY telegram_pairing_codes_executor_all ON app.telegram_pairing_codes
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.telegram_pairing_codes
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

CREATE TABLE app.telegram_callback_tokens (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  command_id UUID NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('confirm','cancel')),
  token_digest BYTEA NOT NULL UNIQUE CHECK (octet_length(token_digest) = 32),
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  invalidated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (owner_id, command_id)
    REFERENCES app.portfolio_commands(owner_id, id) ON DELETE CASCADE,
  UNIQUE (owner_id, command_id, action),
  CHECK (expires_at > created_at),
  CHECK (consumed_at IS NULL OR invalidated_at IS NULL)
);
ALTER TABLE app.telegram_callback_tokens OWNER TO stock_agent_migration_owner;
ALTER TABLE app.telegram_callback_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.telegram_callback_tokens FORCE ROW LEVEL SECURITY;
CREATE POLICY telegram_callback_tokens_executor_all ON app.telegram_callback_tokens
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.telegram_callback_tokens
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

CREATE TABLE machine.telegram_pairing_attempts (
  identity_digest BYTEA PRIMARY KEY CHECK (octet_length(identity_digest) = 32),
  window_started_at TIMESTAMPTZ NOT NULL,
  failed_attempts SMALLINT NOT NULL DEFAULT 0 CHECK (failed_attempts BETWEEN 0 AND 5),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE machine.telegram_pairing_attempts OWNER TO stock_agent_migration_owner;

DROP POLICY telegram_links_executor_select ON app.telegram_links;
CREATE POLICY telegram_links_executor_all ON app.telegram_links
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY telegram_updates_executor_all ON app.telegram_updates
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

REVOKE ALL ON app.telegram_pairing_codes, app.telegram_callback_tokens
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON machine.telegram_pairing_attempts
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION app.issue_telegram_pairing_code(
  p_owner_id UUID,
  p_code_digest TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  code_value app.telegram_pairing_codes%ROWTYPE;
BEGIN
  IF p_owner_id IS NULL OR p_code_digest !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (SELECT 1 FROM app.profiles WHERE id = p_owner_id AND status = 'active') THEN
    RAISE EXCEPTION 'invalid pairing code request';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('telegram-pair:' || p_owner_id::text, 0));
  UPDATE app.telegram_pairing_codes
  SET consumed_at = now()
  WHERE owner_id = p_owner_id AND consumed_at IS NULL;
  INSERT INTO app.telegram_pairing_codes(owner_id, code_digest, expires_at)
  VALUES (p_owner_id, decode(p_code_digest, 'hex'), now() + interval '10 minutes')
  RETURNING * INTO code_value;
  RETURN jsonb_build_object(
    'pairing_id', code_value.id,
    'status', 'issued',
    'expires_at', code_value.expires_at
  );
END
$$;

CREATE OR REPLACE FUNCTION app.create_telegram_callback_tokens(
  p_owner_id UUID,
  p_command_id UUID,
  p_confirm_digest TEXT,
  p_cancel_digest TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  command_value app.portfolio_commands%ROWTYPE;
BEGIN
  IF p_confirm_digest !~ '^[0-9a-f]{64}$' OR p_cancel_digest !~ '^[0-9a-f]{64}$'
     OR p_confirm_digest = p_cancel_digest THEN
    RAISE EXCEPTION 'invalid callback token digests';
  END IF;
  SELECT * INTO command_value
  FROM app.portfolio_commands
  WHERE owner_id = p_owner_id AND id = p_command_id
    AND channel = 'telegram' AND status = 'previewed'
  FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'previewed telegram command not found'; END IF;
  INSERT INTO app.telegram_callback_tokens(
    owner_id, command_id, action, token_digest, expires_at
  ) VALUES
    (p_owner_id, p_command_id, 'confirm', decode(p_confirm_digest, 'hex'), command_value.expires_at),
    (p_owner_id, p_command_id, 'cancel', decode(p_cancel_digest, 'hex'), command_value.expires_at);
  RETURN jsonb_build_object('status', 'created', 'expires_at', command_value.expires_at);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_consume_pairing(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  identity_value BYTEA;
  code_value app.telegram_pairing_codes%ROWTYPE;
  attempt_value machine.telegram_pairing_attempts%ROWTYPE;
  existing_owner_link app.telegram_links%ROWTYPE;
  claimed_update BIGINT;
  chat_value BIGINT;
  update_value BIGINT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request,
    ARRAY['chat_id','user_id','update_id','code_digest','identity_digest','confirm_relink']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'code_digest' !~ '^[0-9a-f]{64}$'
    OR p_request->>'identity_digest' !~ '^[0-9a-f]{64}$'
    OR jsonb_typeof(p_request->'confirm_relink') <> 'boolean' THEN
    RETURN jsonb_build_object('status', 'invalid_code');
  END IF;
  chat_value := (p_request->>'chat_id')::bigint;
  update_value := (p_request->>'update_id')::bigint;
  IF chat_value <> (p_request->>'user_id')::bigint THEN
    RETURN jsonb_build_object('status', 'private_chat_required');
  END IF;
  identity_value := decode(p_request->>'identity_digest', 'hex');
  INSERT INTO machine.telegram_pairing_attempts(
    identity_digest, window_started_at, failed_attempts
  ) VALUES (identity_value, now(), 0)
  ON CONFLICT (identity_digest) DO UPDATE
  SET window_started_at = CASE
        WHEN machine.telegram_pairing_attempts.window_started_at <= now() - interval '10 minutes'
          THEN now() ELSE machine.telegram_pairing_attempts.window_started_at END,
      failed_attempts = CASE
        WHEN machine.telegram_pairing_attempts.window_started_at <= now() - interval '10 minutes'
          THEN 0 ELSE machine.telegram_pairing_attempts.failed_attempts END,
      updated_at = now()
  RETURNING * INTO attempt_value;
  IF attempt_value.failed_attempts >= 5 THEN
    RETURN jsonb_build_object('status', 'rate_limited');
  END IF;

  SELECT * INTO code_value
  FROM app.telegram_pairing_codes
  WHERE code_digest = decode(p_request->>'code_digest', 'hex')
    AND consumed_at IS NULL AND expires_at > now()
  FOR UPDATE;
  IF NOT FOUND THEN
    UPDATE machine.telegram_pairing_attempts
    SET failed_attempts = least(5, failed_attempts + 1), updated_at = now()
    WHERE identity_digest = identity_value;
    RETURN jsonb_build_object('status', 'invalid_code');
  END IF;

  IF EXISTS (
    SELECT 1 FROM app.telegram_links
    WHERE owner_id <> code_value.owner_id
      AND (telegram_chat_id = chat_value OR telegram_user_id = chat_value)
  ) THEN
    UPDATE machine.telegram_pairing_attempts
    SET failed_attempts = least(5, failed_attempts + 1), updated_at = now()
    WHERE identity_digest = identity_value;
    RETURN jsonb_build_object('status', 'invalid_code');
  END IF;
  SELECT * INTO existing_owner_link
  FROM app.telegram_links
  WHERE owner_id = code_value.owner_id
  FOR UPDATE;
  IF FOUND AND existing_owner_link.status = 'active'
     AND (existing_owner_link.telegram_chat_id <> chat_value
          OR existing_owner_link.telegram_user_id <> chat_value)
     AND NOT (p_request->>'confirm_relink')::boolean THEN
    RETURN jsonb_build_object('status', 'relink_required');
  END IF;

  INSERT INTO app.telegram_updates(owner_id, telegram_update_id, kind)
  VALUES (code_value.owner_id, update_value, 'message')
  ON CONFLICT (telegram_update_id) DO NOTHING
  RETURNING telegram_update_id INTO claimed_update;
  IF claimed_update IS NULL THEN
    RETURN jsonb_build_object('status', 'duplicate');
  END IF;

  INSERT INTO app.telegram_links(
    owner_id, telegram_chat_id, telegram_user_id, status, linked_at, revoked_at
  ) VALUES (code_value.owner_id, chat_value, chat_value, 'active', now(), NULL)
  ON CONFLICT (owner_id) DO UPDATE
  SET telegram_chat_id = EXCLUDED.telegram_chat_id,
      telegram_user_id = EXCLUDED.telegram_user_id,
      status = 'active', linked_at = now(), revoked_at = NULL;
  UPDATE app.telegram_pairing_codes SET consumed_at = now() WHERE id = code_value.id;
  DELETE FROM machine.telegram_pairing_attempts WHERE identity_digest = identity_value;
  RETURN jsonb_build_object('status', 'linked');
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_claim_update(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  claimed_value BIGINT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id','update_id','kind'])
     OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
     OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
     OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
     OR p_request->>'kind' NOT IN ('message','callback_query') THEN
    RAISE EXCEPTION 'invalid telegram update';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501'; END IF;
  INSERT INTO app.telegram_updates(owner_id, telegram_update_id, kind)
  VALUES (owner_value, (p_request->>'update_id')::bigint, p_request->>'kind')
  ON CONFLICT (telegram_update_id) DO NOTHING
  RETURNING telegram_update_id INTO claimed_value;
  RETURN jsonb_build_object('claimed', claimed_value IS NOT NULL);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_resolve_link(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  linked_value BOOLEAN;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id']) THEN
    RETURN jsonb_build_object('linked', false);
  END IF;
  SELECT EXISTS (
    SELECT 1 FROM app.telegram_links
    WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
      AND telegram_user_id = (p_request->>'user_id')::bigint
      AND status = 'active'
  ) INTO linked_value;
  RETURN jsonb_build_object('linked', linked_value);
EXCEPTION WHEN OTHERS THEN
  RETURN jsonb_build_object('linked', false);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_unlink(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id']) THEN
    RAISE EXCEPTION 'invalid unlink request';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active'
  FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('status', 'unlinked'); END IF;
  UPDATE app.telegram_links
  SET status = 'revoked', revoked_at = now()
  WHERE owner_id = owner_value;
  UPDATE app.portfolio_commands
  SET status = 'cancelled', updated_at = now()
  WHERE owner_id = owner_value AND channel = 'telegram' AND status = 'previewed';
  UPDATE app.telegram_callback_tokens
  SET invalidated_at = now()
  WHERE owner_id = owner_value AND consumed_at IS NULL AND invalidated_at IS NULL;
  RETURN jsonb_build_object('status', 'unlinked');
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_resolve_callback(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  callback_value RECORD;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id','token_digest'])
     OR p_request->>'token_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid callback request';
  END IF;
  SELECT token.action, token.command_id, command.preview_digest
  INTO callback_value
  FROM app.telegram_callback_tokens AS token
  JOIN app.portfolio_commands AS command
    ON command.owner_id = token.owner_id AND command.id = token.command_id
  JOIN app.telegram_links AS link ON link.owner_id = token.owner_id
  WHERE token.token_digest = decode(p_request->>'token_digest', 'hex')
    AND token.expires_at > now() AND token.consumed_at IS NULL
    AND token.invalidated_at IS NULL
    AND link.status = 'active'
    AND link.telegram_chat_id = (p_request->>'chat_id')::bigint
    AND link.telegram_user_id = (p_request->>'user_id')::bigint;
  IF NOT FOUND THEN RAISE EXCEPTION 'callback unavailable' USING ERRCODE = '42501'; END IF;
  RETURN jsonb_build_object(
    'action', callback_value.action,
    'command_id', callback_value.command_id,
    'preview_digest', callback_value.preview_digest
  );
END
$$;

ALTER FUNCTION app.issue_telegram_pairing_code(UUID, TEXT) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.create_telegram_callback_tokens(UUID, UUID, TEXT, TEXT) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_consume_pairing(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_claim_update(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_resolve_link(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_unlink(JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_resolve_callback(JSONB) OWNER TO stock_agent_migration_owner;

REVOKE ALL ON FUNCTION app.issue_telegram_pairing_code(UUID, TEXT),
  app.create_telegram_callback_tokens(UUID, UUID, TEXT, TEXT)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON FUNCTION machine.telegram_consume_pairing(JSONB),
  machine.telegram_claim_update(JSONB), machine.telegram_resolve_link(JSONB),
  machine.telegram_unlink(JSONB), machine.telegram_resolve_callback(JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_backup;
GRANT EXECUTE ON FUNCTION machine.telegram_consume_pairing(JSONB),
  machine.telegram_claim_update(JSONB), machine.telegram_resolve_link(JSONB),
  machine.telegram_unlink(JSONB), machine.telegram_resolve_callback(JSONB)
  TO stock_agent_telegram;

CREATE INDEX telegram_updates_received_idx ON app.telegram_updates(received_at);
