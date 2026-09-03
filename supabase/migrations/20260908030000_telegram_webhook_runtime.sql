-- Multi-user Telegram webhook runtime: atomic callbacks, bounded reads, and body-free receipts.
-- Supabase CLI migration.

CREATE TABLE app.telegram_deliveries (
  id BIGSERIAL PRIMARY KEY,
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  telegram_update_id BIGINT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('message', 'callback_query')),
  status TEXT NOT NULL CHECK (status IN ('delivered', 'delivery_failed', 'delivery_unknown')),
  telegram_message_id BIGINT,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (owner_id, telegram_update_id)
    REFERENCES app.telegram_updates(owner_id, telegram_update_id) ON DELETE CASCADE,
  UNIQUE (owner_id, telegram_update_id),
  CHECK (telegram_message_id IS NULL OR telegram_message_id > 0)
);
ALTER TABLE app.telegram_deliveries OWNER TO stock_agent_migration_owner;
ALTER SEQUENCE app.telegram_deliveries_id_seq OWNER TO stock_agent_migration_owner;
ALTER TABLE app.telegram_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.telegram_deliveries FORCE ROW LEVEL SECURITY;
CREATE POLICY telegram_deliveries_executor_all ON app.telegram_deliveries
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.telegram_deliveries
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

REVOKE ALL ON app.telegram_deliveries FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
REVOKE ALL ON SEQUENCE app.telegram_deliveries_id_seq FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram,
  stock_agent_backup;

CREATE TABLE app.telegram_pairing_deliveries (
  telegram_update_id BIGINT PRIMARY KEY,
  pairing_status TEXT NOT NULL CHECK (
    pairing_status IN ('linked', 'relink_required', 'rate_limited', 'invalid_code', 'private_chat_required', 'duplicate')
  ),
  status TEXT NOT NULL CHECK (status IN ('delivered', 'delivery_failed', 'delivery_unknown')),
  telegram_message_id BIGINT,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (telegram_message_id IS NULL OR telegram_message_id > 0)
);
ALTER TABLE app.telegram_pairing_deliveries OWNER TO stock_agent_migration_owner;
ALTER TABLE app.telegram_pairing_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.telegram_pairing_deliveries FORCE ROW LEVEL SECURITY;
CREATE POLICY telegram_pairing_deliveries_executor_all ON app.telegram_pairing_deliveries
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
REVOKE ALL ON app.telegram_pairing_deliveries FROM PUBLIC, anon, authenticated, service_role,
  stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION machine.telegram_prepare_command(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  claim_value JSONB;
  preview_value JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request,
    ARRAY['chat_id','user_id','update_id','idempotency_key','command','confirm_digest','cancel_digest']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'idempotency_key' !~
      '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
    OR p_request->>'confirm_digest' !~ '^[0-9a-f]{64}$'
    OR p_request->>'cancel_digest' !~ '^[0-9a-f]{64}$'
    OR p_request->>'confirm_digest' = p_request->>'cancel_digest' THEN
    RAISE EXCEPTION 'invalid telegram command request';
  END IF;
  claim_value := machine.telegram_claim_update(jsonb_build_object(
    'chat_id', p_request->>'chat_id',
    'user_id', p_request->>'user_id',
    'update_id', p_request->>'update_id',
    'kind', 'message'
  ));
  IF claim_value->>'claimed' <> 'true' THEN
    RETURN jsonb_build_object('claimed', false);
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  preview_value := app.preview_portfolio_command(
    owner_value,
    'telegram',
    'telegram:' || owner_value::text,
    (p_request->>'idempotency_key')::uuid,
    p_request->'command'
  );
  PERFORM app.create_telegram_callback_tokens(
    owner_value,
    (preview_value->>'command_id')::uuid,
    p_request->>'confirm_digest',
    p_request->>'cancel_digest'
  );
  RETURN preview_value || jsonb_build_object('claimed', true);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_apply_callback(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  callback_value RECORD;
  command_result JSONB;
  claim_value JSONB;
  rejection_reason TEXT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['chat_id','user_id','update_id','action','token_digest']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'action' NOT IN ('confirm','cancel')
    OR p_request->>'token_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid callback request';
  END IF;

  claim_value := machine.telegram_claim_update(jsonb_build_object(
    'chat_id', p_request->>'chat_id',
    'user_id', p_request->>'user_id',
    'update_id', p_request->>'update_id',
    'kind', 'callback_query'
  ));
  IF claim_value->>'claimed' <> 'true' THEN
    RETURN jsonb_build_object('claimed', false);
  END IF;

  SELECT token.id, token.owner_id, token.command_id, token.action,
         command.preview_digest
  INTO callback_value
  FROM app.telegram_callback_tokens AS token
  JOIN app.portfolio_commands AS command
    ON command.owner_id = token.owner_id AND command.id = token.command_id
  JOIN app.telegram_links AS link ON link.owner_id = token.owner_id
  WHERE token.token_digest = decode(p_request->>'token_digest', 'hex')
    AND token.action = p_request->>'action'
    AND token.expires_at > now()
    AND token.consumed_at IS NULL
    AND token.invalidated_at IS NULL
    AND link.status = 'active'
    AND link.telegram_chat_id = (p_request->>'chat_id')::bigint
    AND link.telegram_user_id = (p_request->>'user_id')::bigint
  FOR UPDATE OF token;
  IF NOT FOUND THEN
    RETURN jsonb_build_object(
      'claimed', true, 'action', p_request->>'action',
      'status', 'unavailable', 'reason', 'callback_unavailable'
    );
  END IF;

  BEGIN
    IF callback_value.action = 'confirm' THEN
      command_result := app.confirm_portfolio_command(
        callback_value.owner_id,
        'telegram',
        'telegram:' || callback_value.owner_id::text,
        callback_value.command_id,
        callback_value.preview_digest
      );
    ELSE
      command_result := app.cancel_portfolio_command(
        callback_value.owner_id,
        'telegram',
        'telegram:' || callback_value.owner_id::text,
        callback_value.command_id,
        callback_value.preview_digest
      );
    END IF;
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    rejection_reason := 'command_no_longer_applicable';
    UPDATE app.portfolio_commands
    SET status = CASE WHEN expires_at <= now() THEN 'expired' ELSE 'error' END,
        error_code = 'TELEGRAM_CALLBACK_REJECTED', updated_at = now()
    WHERE owner_id = callback_value.owner_id
      AND id = callback_value.command_id
      AND status = 'previewed';
  END;

  UPDATE app.telegram_callback_tokens
  SET consumed_at = now()
  WHERE id = callback_value.id;
  UPDATE app.telegram_callback_tokens
  SET invalidated_at = now()
  WHERE owner_id = callback_value.owner_id
    AND command_id = callback_value.command_id
    AND id <> callback_value.id
    AND consumed_at IS NULL
    AND invalidated_at IS NULL;

  IF rejection_reason IS NOT NULL THEN
    RETURN jsonb_build_object(
      'claimed', true,
      'action', callback_value.action,
      'command_id', callback_value.command_id,
      'status', 'rejected',
      'reason', rejection_reason
    );
  END IF;

  RETURN jsonb_build_object(
    'claimed', true,
    'action', callback_value.action,
    'command_id', callback_value.command_id,
    'status', command_result->>'status',
    'result', command_result->'result'
  );
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_portfolio(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  holdings_value JSONB;
  claim_value JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id','update_id'])
    OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$' THEN
    RAISE EXCEPTION 'invalid telegram portfolio request';
  END IF;
  claim_value := machine.telegram_claim_update(jsonb_build_object(
    'chat_id', p_request->>'chat_id', 'user_id', p_request->>'user_id',
    'update_id', p_request->>'update_id', 'kind', 'message'
  ));
  IF claim_value->>'claimed' <> 'true' THEN
    RETURN jsonb_build_object('claimed', false);
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT coalesce(jsonb_agg(to_jsonb(holding) ORDER BY holding.ticker), '[]'::jsonb)
  INTO holdings_value
  FROM (
    SELECT ticker, shares, avg_cost, bucket, stop, target, projection_sequence
    FROM app.holdings
    WHERE owner_id = owner_value
    ORDER BY ticker
    LIMIT 40
  ) AS holding;
  RETURN jsonb_build_object('claimed', true, 'holdings', holdings_value);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_plans(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  plans_value JSONB;
  claim_value JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id','update_id'])
    OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$' THEN
    RAISE EXCEPTION 'invalid telegram plans request';
  END IF;
  claim_value := machine.telegram_claim_update(jsonb_build_object(
    'chat_id', p_request->>'chat_id', 'user_id', p_request->>'user_id',
    'update_id', p_request->>'update_id', 'kind', 'message'
  ));
  IF claim_value->>'claimed' <> 'true' THEN
    RETURN jsonb_build_object('claimed', false);
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT coalesce(jsonb_agg(to_jsonb(plan) ORDER BY plan.next_due_on, plan.ticker), '[]'::jsonb)
  INTO plans_value
  FROM (
    SELECT ticker, amount, cadence, next_due_on, bucket
    FROM app.owner_investment_plans
    WHERE owner_id = owner_value AND active
    ORDER BY next_due_on, ticker
    LIMIT 20
  ) AS plan;
  RETURN jsonb_build_object('claimed', true, 'plans', plans_value);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_record_delivery(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
  inserted_value BIGINT;
  message_value BIGINT;
  existing_value app.telegram_deliveries%ROWTYPE;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['chat_id','user_id','update_id','kind','status','message_id']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'kind' NOT IN ('message','callback_query')
    OR p_request->>'status' NOT IN ('delivered','delivery_failed','delivery_unknown')
    OR (p_request->'message_id' <> 'null'::jsonb
        AND p_request->>'message_id' !~ '^[1-9][0-9]{0,15}$') THEN
    RAISE EXCEPTION 'invalid telegram delivery receipt';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM app.telegram_updates
    WHERE owner_id = owner_value
      AND telegram_update_id = (p_request->>'update_id')::bigint
      AND kind = p_request->>'kind'
  ) THEN
    RAISE EXCEPTION 'telegram update was not claimed';
  END IF;
  message_value := CASE WHEN p_request->'message_id' = 'null'::jsonb
                        THEN NULL ELSE (p_request->>'message_id')::bigint END;

  INSERT INTO app.telegram_deliveries(
    owner_id, telegram_update_id, kind, status, telegram_message_id
  ) VALUES (
    owner_value,
    (p_request->>'update_id')::bigint,
    p_request->>'kind',
    p_request->>'status',
    message_value
  )
  ON CONFLICT (owner_id, telegram_update_id) DO NOTHING
  RETURNING id INTO inserted_value;

  IF inserted_value IS NULL THEN
    SELECT * INTO existing_value
    FROM app.telegram_deliveries
    WHERE owner_id = owner_value
      AND telegram_update_id = (p_request->>'update_id')::bigint;
    IF existing_value.kind <> p_request->>'kind'
       OR existing_value.status <> p_request->>'status'
       OR existing_value.telegram_message_id IS DISTINCT FROM message_value THEN
      RAISE EXCEPTION 'delivery receipt idempotency mismatch';
    END IF;
  END IF;
  RETURN jsonb_build_object('recorded', true, 'status', p_request->>'status');
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_record_pairing_delivery(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  message_value BIGINT;
  existing_value app.telegram_pairing_deliveries%ROWTYPE;
  inserted_value BIGINT;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['update_id','pairing_status','status','message_id']
  ) OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'pairing_status' NOT IN (
      'linked', 'relink_required', 'rate_limited', 'invalid_code', 'private_chat_required', 'duplicate'
    )
    OR p_request->>'status' NOT IN ('delivered','delivery_failed','delivery_unknown')
    OR (p_request->'message_id' <> 'null'::jsonb
        AND p_request->>'message_id' !~ '^[1-9][0-9]{0,15}$') THEN
    RAISE EXCEPTION 'invalid telegram pairing delivery receipt';
  END IF;
  message_value := CASE WHEN p_request->'message_id' = 'null'::jsonb
                        THEN NULL ELSE (p_request->>'message_id')::bigint END;
  INSERT INTO app.telegram_pairing_deliveries(
    telegram_update_id, pairing_status, status, telegram_message_id
  ) VALUES (
    (p_request->>'update_id')::bigint,
    p_request->>'pairing_status',
    p_request->>'status',
    message_value
  )
  ON CONFLICT (telegram_update_id) DO NOTHING
  RETURNING telegram_update_id INTO inserted_value;
  IF inserted_value IS NULL THEN
    SELECT * INTO existing_value
    FROM app.telegram_pairing_deliveries
    WHERE telegram_update_id = (p_request->>'update_id')::bigint;
    IF existing_value.pairing_status <> p_request->>'pairing_status'
       OR existing_value.status <> p_request->>'status'
       OR existing_value.telegram_message_id IS DISTINCT FROM message_value THEN
      RAISE EXCEPTION 'pairing delivery receipt idempotency mismatch';
    END IF;
  END IF;
  RETURN jsonb_build_object('recorded', true, 'status', p_request->>'status');
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
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id'])
    OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id' THEN
    RETURN jsonb_build_object('linked', false);
  END IF;
  SELECT EXISTS (
    SELECT 1 FROM app.telegram_links
    WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
      AND telegram_user_id = (p_request->>'user_id')::bigint
      AND status = 'active'
  ) INTO linked_value;
  RETURN jsonb_build_object('linked', linked_value);
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
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$'
    OR p_request->>'kind' NOT IN ('message','callback_query') THEN
    RAISE EXCEPTION 'invalid telegram update';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  INSERT INTO app.telegram_updates(owner_id, telegram_update_id, kind)
  VALUES (owner_value, (p_request->>'update_id')::bigint, p_request->>'kind')
  ON CONFLICT (telegram_update_id) DO NOTHING
  RETURNING telegram_update_id INTO claimed_value;
  RETURN jsonb_build_object('claimed', claimed_value IS NOT NULL);
END
$$;

CREATE OR REPLACE FUNCTION machine.telegram_preview_command(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  owner_value UUID;
BEGIN
  IF NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['chat_id','user_id','idempotency_key','command']
  ) OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'idempotency_key' !~
      '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$' THEN
    RAISE EXCEPTION 'invalid telegram command request';
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_links
  WHERE telegram_chat_id = (p_request->>'chat_id')::bigint
    AND telegram_user_id = (p_request->>'user_id')::bigint
    AND status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'telegram link unavailable' USING ERRCODE = '42501';
  END IF;
  RETURN app.preview_portfolio_command(
    owner_value,
    'telegram',
    'telegram:' || owner_value::text,
    (p_request->>'idempotency_key')::uuid,
    p_request->'command'
  );
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
  claim_value JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['chat_id','user_id','update_id'])
    OR p_request->>'chat_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'user_id' !~ '^[1-9][0-9]{0,15}$'
    OR p_request->>'chat_id' <> p_request->>'user_id'
    OR p_request->>'update_id' !~ '^[0-9][0-9]{0,18}$' THEN
    RAISE EXCEPTION 'invalid unlink request';
  END IF;
  claim_value := machine.telegram_claim_update(jsonb_build_object(
    'chat_id', p_request->>'chat_id', 'user_id', p_request->>'user_id',
    'update_id', p_request->>'update_id', 'kind', 'message'
  ));
  IF claim_value->>'claimed' <> 'true' THEN
    RETURN jsonb_build_object('claimed', false);
  END IF;
  SELECT owner_id INTO owner_value
  FROM app.telegram_updates
  WHERE telegram_update_id = (p_request->>'update_id')::bigint;
  UPDATE app.telegram_links
  SET status = 'revoked', revoked_at = now()
  WHERE owner_id = owner_value;
  UPDATE app.portfolio_commands
  SET status = 'cancelled', updated_at = now()
  WHERE owner_id = owner_value AND channel = 'telegram' AND status = 'previewed';
  UPDATE app.telegram_callback_tokens
  SET invalidated_at = now()
  WHERE owner_id = owner_value AND consumed_at IS NULL AND invalidated_at IS NULL;
  RETURN jsonb_build_object('claimed', true, 'status', 'unlinked');
END
$$;

ALTER FUNCTION machine.telegram_prepare_command(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_apply_callback(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_portfolio(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_plans(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_record_delivery(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_record_pairing_delivery(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_resolve_link(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_claim_update(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_preview_command(JSONB)
  OWNER TO stock_agent_migration_owner;
ALTER FUNCTION machine.telegram_unlink(JSONB)
  OWNER TO stock_agent_migration_owner;

REVOKE ALL ON FUNCTION machine.telegram_confirm_command(JSONB),
  machine.telegram_cancel_command(JSONB), machine.telegram_resolve_callback(JSONB),
  machine.telegram_preview_command(JSONB)
  FROM stock_agent_telegram;
REVOKE ALL ON FUNCTION machine.telegram_prepare_command(JSONB),
  machine.telegram_apply_callback(JSONB), machine.telegram_portfolio(JSONB),
  machine.telegram_plans(JSONB), machine.telegram_record_delivery(JSONB),
  machine.telegram_record_pairing_delivery(JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION machine.telegram_prepare_command(JSONB),
  machine.telegram_apply_callback(JSONB), machine.telegram_portfolio(JSONB),
  machine.telegram_plans(JSONB), machine.telegram_record_delivery(JSONB),
  machine.telegram_record_pairing_delivery(JSONB)
  TO stock_agent_telegram;

CREATE INDEX telegram_deliveries_recorded_idx ON app.telegram_deliveries(recorded_at);
CREATE INDEX telegram_pairing_deliveries_recorded_idx
  ON app.telegram_pairing_deliveries(recorded_at);
