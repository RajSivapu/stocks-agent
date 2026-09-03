-- Private account lifecycle, step-up receipts, secret-free exports, and operator reset controls.

CREATE TABLE app.account_step_up_challenges (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  initial_session_digest BYTEA NOT NULL CHECK (octet_length(initial_session_digest) = 32),
  expires_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id),
  CHECK (expires_at > created_at),
  CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE TABLE app.account_step_up_receipts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  challenge_id UUID NOT NULL,
  session_digest BYTEA NOT NULL CHECK (octet_length(session_digest) = 32),
  authenticated_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id),
  UNIQUE (owner_id, challenge_id),
  FOREIGN KEY (owner_id, challenge_id)
    REFERENCES app.account_step_up_challenges(owner_id, id) ON DELETE RESTRICT,
  CHECK (expires_at > created_at),
  CHECK (authenticated_at <= created_at + interval '30 seconds'),
  CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);

CREATE TABLE app.account_deletion_requests (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  step_up_receipt_id UUID NOT NULL,
  status TEXT NOT NULL DEFAULT 'confirmation_pending' CHECK (
    status IN ('confirmation_pending','pending','cancelled','processing','completed')
  ),
  confirmation_expires_at TIMESTAMPTZ NOT NULL,
  requested_at TIMESTAMPTZ,
  cancel_until TIMESTAMPTZ,
  delete_by TIMESTAMPTZ,
  telegram_cleanup_token_digest BYTEA CHECK (
    telegram_cleanup_token_digest IS NULL OR octet_length(telegram_cleanup_token_digest) = 32
  ),
  telegram_cleanup_status JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (
    jsonb_typeof(telegram_cleanup_status) = 'object'
    AND octet_length(telegram_cleanup_status::text) <= 2000
  ),
  cancelled_at TIMESTAMPTZ,
  processing_started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, step_up_receipt_id)
    REFERENCES app.account_step_up_receipts(owner_id, id) ON DELETE RESTRICT,
  CHECK (confirmation_expires_at > created_at),
  CHECK (
    status = 'confirmation_pending'
    OR (requested_at IS NOT NULL AND cancel_until = requested_at + interval '72 hours'
      AND delete_by = requested_at + interval '7 days')
  ),
  CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL)),
  CHECK (status NOT IN ('processing','completed') OR processing_started_at IS NOT NULL),
  CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);
CREATE UNIQUE INDEX account_deletion_one_active_owner_idx
  ON app.account_deletion_requests(owner_id)
  WHERE status IN ('confirmation_pending','pending','processing');

-- This table intentionally has no Auth foreign key. It survives identity deletion and prevents
-- a 35-day recovery archive from resurrecting an account the owner deleted.
CREATE TABLE app.deletion_tombstones (
  owner_id UUID PRIMARY KEY,
  deletion_request_id UUID NOT NULL UNIQUE,
  deleted_at TIMESTAMPTZ NOT NULL,
  archives_expire_after TIMESTAMPTZ NOT NULL,
  CHECK (archives_expire_after = deleted_at + interval '35 days')
);

-- Non-financial, immutable evidence that an offline ledger reset occurred.
CREATE TABLE app.owner_ledger_reset_receipts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL,
  step_up_receipt_id UUID NOT NULL UNIQUE,
  export_digest TEXT NOT NULL CHECK (export_digest ~ '^[0-9a-f]{64}$'),
  row_counts JSONB NOT NULL CHECK (
    jsonb_typeof(row_counts) = 'object' AND octet_length(row_counts::text) <= 2000
  ),
  reset_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE app.account_step_up_challenges OWNER TO stock_agent_migration_owner;
ALTER TABLE app.account_step_up_receipts OWNER TO stock_agent_migration_owner;
ALTER TABLE app.account_deletion_requests OWNER TO stock_agent_migration_owner;
ALTER TABLE app.deletion_tombstones OWNER TO stock_agent_migration_owner;
ALTER TABLE app.owner_ledger_reset_receipts OWNER TO stock_agent_migration_owner;

DO $$
DECLARE target_table TEXT;
BEGIN
  FOREACH target_table IN ARRAY ARRAY[
    'account_step_up_challenges','account_step_up_receipts','account_deletion_requests',
    'deletion_tombstones','owner_ledger_reset_receipts'
  ] LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', target_table);
    EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY', target_table);
    EXECUTE format(
      'CREATE POLICY %I ON app.%I FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true)',
      target_table || '_executor_all', target_table
    );
    EXECUTE format(
      'CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.%I '
      'FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation()', target_table
    );
  END LOOP;
END
$$;

CREATE POLICY account_deletion_requests_owner_select ON app.account_deletion_requests
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

-- These pre-existing forced-RLS tables need an owner-only executor policy so ungranted
-- lifecycle functions can remove their rows during a verified account purge.
CREATE POLICY app_admins_lifecycle_executor_all ON app.app_admins
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY single_owner_receipts_lifecycle_executor_all
  ON app.single_owner_migration_receipts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY owner_policy_lifecycle_executor_all ON app.owner_policy_overrides
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

REVOKE ALL ON app.account_step_up_challenges, app.account_step_up_receipts,
  app.account_deletion_requests, app.deletion_tombstones, app.owner_ledger_reset_receipts
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT SELECT (owner_id, id, status, requested_at, cancel_until, delete_by,
              telegram_cleanup_status, cancelled_at, completed_at, created_at, updated_at)
  ON app.account_deletion_requests TO authenticated;

CREATE OR REPLACE FUNCTION app.prevent_immutable_receipt_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF TG_OP = 'DELETE' AND TG_TABLE_NAME = 'owner_ledger_reset_receipts'
     AND current_setting('stock_agent.account_purge_owner', true) = OLD.owner_id::text THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'immutable lifecycle receipt';
END
$$;
ALTER FUNCTION app.prevent_immutable_receipt_mutation() OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.prevent_immutable_receipt_mutation() FROM PUBLIC, anon, authenticated,
  service_role, stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
CREATE TRIGGER prevent_deletion_tombstone_mutation
  BEFORE UPDATE OR DELETE ON app.deletion_tombstones
  FOR EACH ROW EXECUTE FUNCTION app.prevent_immutable_receipt_mutation();
CREATE TRIGGER prevent_ledger_reset_receipt_mutation
  BEFORE UPDATE OR DELETE ON app.owner_ledger_reset_receipts
  FOR EACH ROW EXECUTE FUNCTION app.prevent_immutable_receipt_mutation();

CREATE OR REPLACE FUNCTION app.accept_owner_consent(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE current_version TEXT;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY['document_version'])
     OR jsonb_typeof(p_request->'document_version') <> 'string' THEN
    RAISE EXCEPTION 'invalid consent request' USING ERRCODE = '22023';
  END IF;
  SELECT current_consent_version INTO current_version
  FROM machine.connection_policy WHERE singleton;
  IF current_version IS NULL OR p_request->>'document_version' <> current_version THEN
    RAISE EXCEPTION 'invalid consent version' USING ERRCODE = '22023';
  END IF;
  PERFORM 1 FROM app.profiles
  WHERE id = p_owner_id AND status IN ('invited','active') FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'profile unavailable' USING ERRCODE = '42501'; END IF;
  INSERT INTO app.user_consents(owner_id, document_version, source)
  VALUES (p_owner_id, current_version, 'web')
  ON CONFLICT (owner_id, document_version) DO NOTHING;
  UPDATE app.profiles SET status = 'active',
    onboarding_completed_at = coalesce(onboarding_completed_at, clock_timestamp()),
    updated_at = clock_timestamp()
  WHERE id = p_owner_id;
  RETURN jsonb_build_object('status', 'accepted', 'document_version', current_version);
END
$$;

CREATE OR REPLACE FUNCTION app.create_account_step_up_challenge(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE challenge_id UUID := extensions.gen_random_uuid();
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY['session_digest'])
     OR p_request->>'session_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid step up request' USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM app.profiles WHERE id = p_owner_id
      AND status IN ('invited','active','deletion_pending')
  ) THEN RAISE EXCEPTION 'profile unavailable' USING ERRCODE = '42501'; END IF;
  UPDATE app.account_step_up_challenges SET completed_at = observed_at
  WHERE owner_id = p_owner_id AND completed_at IS NULL;
  INSERT INTO app.account_step_up_challenges(
    id, owner_id, initial_session_digest, expires_at, created_at
  ) VALUES (
    challenge_id, p_owner_id, decode(p_request->>'session_digest', 'hex'),
    observed_at + interval '10 minutes', observed_at
  );
  RETURN jsonb_build_object(
    'status', 'challenge_created', 'challenge_id', challenge_id,
    'expires_at', observed_at + interval '10 minutes'
  );
END
$$;

CREATE OR REPLACE FUNCTION app.complete_account_step_up(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE challenge_row app.account_step_up_challenges%ROWTYPE;
DECLARE receipt_id UUID := extensions.gen_random_uuid();
DECLARE session_value BYTEA;
DECLARE authenticated_value TIMESTAMPTZ;
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(
       p_request, ARRAY['challenge_id','session_digest','auth_method','authenticated_at']
     ) OR p_request->>'challenge_id' !~
       '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
     OR p_request->>'session_digest' !~ '^[0-9a-f]{64}$'
     OR p_request->>'auth_method' <> 'otp' THEN
    RAISE EXCEPTION 'invalid step up request' USING ERRCODE = '22023';
  END IF;
  BEGIN authenticated_value := (p_request->>'authenticated_at')::timestamptz;
  EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'invalid step up request' USING ERRCODE = '22023'; END;
  SELECT * INTO challenge_row FROM app.account_step_up_challenges
  WHERE owner_id = p_owner_id AND id = (p_request->>'challenge_id')::uuid FOR UPDATE;
  session_value := decode(p_request->>'session_digest', 'hex');
  IF NOT FOUND OR challenge_row.completed_at IS NOT NULL OR challenge_row.expires_at <= observed_at
     OR challenge_row.initial_session_digest = session_value
     OR authenticated_value < challenge_row.created_at - interval '5 seconds'
     OR authenticated_value < observed_at - interval '5 minutes'
     OR authenticated_value > observed_at + interval '30 seconds' THEN
    RAISE EXCEPTION 'fresh OTP step up unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.account_step_up_challenges SET completed_at = observed_at WHERE id = challenge_row.id;
  INSERT INTO app.account_step_up_receipts(
    id, owner_id, challenge_id, session_digest, authenticated_at, expires_at, created_at
  ) VALUES (
    receipt_id, p_owner_id, challenge_row.id, session_value, authenticated_value,
    observed_at + interval '5 minutes', observed_at
  );
  RETURN jsonb_build_object(
    'status', 'verified', 'step_up_receipt_id', receipt_id,
    'expires_at', observed_at + interval '5 minutes'
  );
END
$$;

CREATE OR REPLACE FUNCTION app.assert_fresh_step_up(
  p_owner_id UUID,
  p_receipt_id UUID,
  p_session_digest TEXT,
  p_consume BOOLEAN DEFAULT false
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
BEGIN
  IF p_owner_id IS NULL OR p_receipt_id IS NULL
     OR p_session_digest !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'fresh step up required' USING ERRCODE = '42501';
  END IF;
  PERFORM 1 FROM app.account_step_up_receipts
  WHERE owner_id = p_owner_id AND id = p_receipt_id
    AND session_digest = decode(p_session_digest, 'hex')
    AND consumed_at IS NULL AND expires_at > observed_at
  FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'fresh step up required' USING ERRCODE = '42501'; END IF;
  IF p_consume THEN
    UPDATE app.account_step_up_receipts SET consumed_at = observed_at
    WHERE owner_id = p_owner_id AND id = p_receipt_id;
  END IF;
END
$$;

CREATE OR REPLACE FUNCTION app.export_owner_account(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE document JSONB;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY[]::text[])
     OR NOT EXISTS (SELECT 1 FROM app.profiles WHERE id = p_owner_id) THEN
    RAISE EXCEPTION 'export unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT jsonb_build_object(
    'schema_version', 1,
    'exported_at', clock_timestamp(),
    'profile', jsonb_build_object(
      'display_name', profile.display_name,
      'timezone', profile.timezone,
      'status', profile.status,
      'onboarding_completed_at', profile.onboarding_completed_at,
      'created_at', profile.created_at,
      'updated_at', profile.updated_at
    ),
    'consents', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'document_version', consent.document_version,
      'source', consent.source,
      'accepted_at', consent.accepted_at
    ) ORDER BY consent.accepted_at) FROM app.user_consents consent
      WHERE consent.owner_id = p_owner_id), '[]'::jsonb),
    'preferences', coalesce((SELECT jsonb_build_object(
      'pre_market_enabled', preference.pre_market_enabled,
      'intraday_enabled', preference.intraday_enabled,
      'post_market_enabled', preference.post_market_enabled,
      'operational_enabled', preference.operational_enabled,
      'updated_at', preference.updated_at
    ) FROM app.notification_preferences preference WHERE preference.owner_id = p_owner_id), '{}'::jsonb),
    'portfolio', jsonb_build_object(
      'holdings', coalesce((SELECT jsonb_agg(jsonb_build_object(
        'ticker', holding.ticker, 'shares', holding.shares::text,
        'avg_cost', holding.avg_cost::text, 'bucket', holding.bucket,
        'opened_at', holding.opened_at, 'stop', holding.stop::text,
        'target', holding.target::text, 'projection_sequence', holding.projection_sequence::text
      ) ORDER BY holding.ticker) FROM app.holdings holding
        WHERE holding.owner_id = p_owner_id), '[]'::jsonb),
      'transactions', coalesce((SELECT jsonb_agg(jsonb_build_object(
        'transaction_id', transaction_row.public_id, 'created_at', transaction_row.ts,
        'ticker', transaction_row.ticker, 'event_type', transaction_row.event_type,
        'side', transaction_row.side, 'quantity', transaction_row.qty::text,
        'price', transaction_row.price::text, 'fees', transaction_row.fees::text,
        'executed_on', transaction_row.executed_on,
        'ledger_sequence', transaction_row.ledger_sequence::text,
        'bucket', transaction_row.bucket, 'source_channel', transaction_row.source_channel,
        'corrects_transaction_id', corrected.public_id
      ) ORDER BY transaction_row.ledger_sequence) FROM app.transactions transaction_row
        LEFT JOIN app.transactions corrected
          ON corrected.owner_id = transaction_row.owner_id
         AND corrected.id = transaction_row.corrects_transaction_id
        WHERE transaction_row.owner_id = p_owner_id), '[]'::jsonb),
      'plans', coalesce((SELECT jsonb_agg(jsonb_build_object(
        'id', plan.id, 'ticker', plan.ticker, 'bucket', plan.bucket,
        'amount', plan.amount::text, 'cadence', plan.cadence,
        'next_due_on', plan.next_due_on, 'due_day', plan.due_day, 'active', plan.active,
        'created_at', plan.created_at, 'updated_at', plan.updated_at
      ) ORDER BY plan.created_at, plan.id) FROM app.owner_investment_plans plan
        WHERE plan.owner_id = p_owner_id), '[]'::jsonb)
    ),
    'commands', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'id', command.id, 'channel', command.channel, 'operation', command.operation,
      'before', command.before_projection, 'after', command.after_projection,
      'warnings', command.warnings, 'status', command.status,
      'expires_at', command.expires_at, 'confirmed_at', command.confirmed_at,
      'applied_at', command.applied_at, 'result', command.result,
      'error_code', command.error_code, 'created_at', command.created_at
    ) ORDER BY command.created_at, command.id) FROM app.portfolio_commands command
      WHERE command.owner_id = p_owner_id), '[]'::jsonb),
    'recommendations', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'id', suggestion.id::text, 'run_id', suggestion.run_id,
      'created_at', suggestion.ts, 'date', suggestion.date,
      'ticker', suggestion.ticker, 'action', suggestion.action,
      'bucket', suggestion.bucket, 'confidence', suggestion.confidence,
      'valid_until', suggestion.valid_until, 'risk_verdict', suggestion.risk_verdict,
      'price_at_suggestion', suggestion.price_at_suggestion::text,
      'evidence_as_of', suggestion.evidence_as_of
    ) ORDER BY suggestion.ts, suggestion.id) FROM app.suggestions suggestion
      WHERE suggestion.owner_id = p_owner_id), '[]'::jsonb),
    'evidence_metadata', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'evidence_id', evidence.evidence_id, 'run_id', evidence.run_id,
      'category', evidence.category, 'source', evidence.source_identifier,
      'reference', evidence.reference_identifier, 'observed_at', evidence.observed_at,
      'retrieved_at', evidence.retrieved_at, 'revalidated_at', evidence.revalidated_at,
      'content_hash', evidence.content_hash, 'status', evidence.status
    ) ORDER BY evidence.retrieved_at, evidence.id) FROM app.run_evidence evidence
      WHERE evidence.owner_id = p_owner_id), '[]'::jsonb),
    'runs', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'id', run.id, 'kind', run.kind, 'market_date', run.market_date,
      'provider', run.provider, 'model', run.model, 'started_at', run.started_at,
      'finished_at', run.finished_at, 'status', run.status,
      'data_as_of', run.data_as_of, 'source_status', run.source_status,
      'symbols', run.symbols, 'write_counts', run.write_counts, 'summary', run.summary
    ) ORDER BY run.started_at, run.id) FROM app.analysis_runs run
      WHERE run.owner_id = p_owner_id), '[]'::jsonb),
    'connections', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'id', connection.id, 'provider', connection.provider,
      'credential_type', connection.credential_type,
      'capabilities', connection.capabilities,
      'contract_version', connection.contract_version, 'status', connection.status,
      'last_handshake_at', connection.last_handshake_at,
      'created_at', connection.created_at, 'updated_at', connection.updated_at
    ) ORDER BY connection.created_at, connection.id) FROM app.agent_connections connection
      WHERE connection.owner_id = p_owner_id), '[]'::jsonb),
    'telegram', coalesce((SELECT jsonb_build_object(
      'status', link.status, 'linked_at', link.linked_at, 'revoked_at', link.revoked_at
    ) FROM app.telegram_links link WHERE link.owner_id = p_owner_id),
      jsonb_build_object('status', 'unlinked'))
  ) INTO document
  FROM app.profiles profile WHERE profile.id = p_owner_id;
  RETURN jsonb_build_object(
    'status', 'ready', 'format', 'json',
    'filename', 'stock-agent-account.json',
    'media_type', 'application/json; charset=utf-8',
    'body', jsonb_pretty(document) || E'\n'
  );
END
$$;

CREATE OR REPLACE FUNCTION app.csv_safe_cell(p_value TEXT)
RETURNS TEXT
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog
AS $$
DECLARE value TEXT := coalesce(p_value, '');
BEGIN
  IF value ~ '^[=+@-]' THEN value := '''' || value; END IF;
  RETURN '"' || replace(value, '"', '""') || '"';
END
$$;

CREATE OR REPLACE FUNCTION app.export_owner_ledger(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE csv_body TEXT;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY[]::text[])
     OR NOT EXISTS (SELECT 1 FROM app.profiles WHERE id = p_owner_id) THEN
    RAISE EXCEPTION 'export unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT 'transaction_id,created_at,ticker,event_type,side,quantity,price,fees,executed_on,ledger_sequence,bucket,source_channel,corrects_transaction_id' || E'\n'
    || coalesce(string_agg(
      app.csv_safe_cell(transaction_row.public_id::text) || ',' ||
      app.csv_safe_cell(transaction_row.ts::text) || ',' ||
      app.csv_safe_cell(transaction_row.ticker) || ',' ||
      app.csv_safe_cell(transaction_row.event_type) || ',' ||
      app.csv_safe_cell(transaction_row.side) || ',' ||
      app.csv_safe_cell(transaction_row.qty::text) || ',' ||
      app.csv_safe_cell(transaction_row.price::text) || ',' ||
      app.csv_safe_cell(transaction_row.fees::text) || ',' ||
      app.csv_safe_cell(transaction_row.executed_on::text) || ',' ||
      app.csv_safe_cell(transaction_row.ledger_sequence::text) || ',' ||
      app.csv_safe_cell(transaction_row.bucket) || ',' ||
      app.csv_safe_cell(transaction_row.source_channel) || ',' ||
      app.csv_safe_cell(corrected.public_id::text), E'\n'
      ORDER BY transaction_row.ledger_sequence
    ), '') || CASE WHEN EXISTS (
      SELECT 1 FROM app.transactions existing WHERE existing.owner_id = p_owner_id
    ) THEN E'\n' ELSE '' END
  INTO csv_body
  FROM app.transactions transaction_row
  LEFT JOIN app.transactions corrected
    ON corrected.owner_id = transaction_row.owner_id
   AND corrected.id = transaction_row.corrects_transaction_id
  WHERE transaction_row.owner_id = p_owner_id;
  RETURN jsonb_build_object(
    'status', 'ready', 'format', 'csv',
    'filename', 'stock-agent-ledger.csv',
    'media_type', 'text/csv; charset=utf-8', 'body', csv_body
  );
END
$$;

CREATE OR REPLACE FUNCTION app.request_account_deletion(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE receipt_id UUID;
DECLARE deletion_id UUID := extensions.gen_random_uuid();
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
DECLARE existing_row app.account_deletion_requests%ROWTYPE;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['step_up_receipt_id','session_digest']
  ) THEN RAISE EXCEPTION 'invalid deletion request' USING ERRCODE = '22023'; END IF;
  BEGIN receipt_id := (p_request->>'step_up_receipt_id')::uuid;
  EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'invalid deletion request' USING ERRCODE = '22023'; END;
  PERFORM app.assert_fresh_step_up(p_owner_id, receipt_id, p_request->>'session_digest', false);
  SELECT * INTO existing_row FROM app.account_deletion_requests
  WHERE owner_id = p_owner_id AND status IN ('confirmation_pending','pending','processing')
  FOR UPDATE;
  IF FOUND THEN
    RETURN jsonb_build_object(
      'status', existing_row.status,
      'deletion_request_id', existing_row.id,
      'confirmation_phrase', 'DELETE MY ACCOUNT',
      'confirmation_expires_at', existing_row.confirmation_expires_at
    );
  END IF;
  INSERT INTO app.account_deletion_requests(
    id, owner_id, step_up_receipt_id, confirmation_expires_at, created_at, updated_at
  ) VALUES (
    deletion_id, p_owner_id, receipt_id, observed_at + interval '5 minutes', observed_at, observed_at
  );
  RETURN jsonb_build_object(
    'status', 'confirmation_pending', 'deletion_request_id', deletion_id,
    'confirmation_phrase', 'DELETE MY ACCOUNT',
    'confirmation_expires_at', observed_at + interval '5 minutes'
  );
END
$$;

CREATE OR REPLACE FUNCTION app.confirm_account_deletion(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE deletion_row app.account_deletion_requests%ROWTYPE;
DECLARE receipt_id UUID;
DECLARE deletion_id UUID;
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
DECLARE connection_row RECORD;
DECLARE recent_messages INT;
DECLARE cleanup_messages JSONB := '[]'::jsonb;
DECLARE cleanup_chat_id BIGINT;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['deletion_request_id','step_up_receipt_id','session_digest',
      'confirmation_phrase','cleanup_token_digest']
  ) OR p_request->>'confirmation_phrase' <> 'DELETE MY ACCOUNT'
     OR p_request->>'cleanup_token_digest' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid deletion confirmation' USING ERRCODE = '22023';
  END IF;
  BEGIN
    deletion_id := (p_request->>'deletion_request_id')::uuid;
    receipt_id := (p_request->>'step_up_receipt_id')::uuid;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'invalid deletion confirmation' USING ERRCODE = '22023';
  END;
  SELECT * INTO deletion_row FROM app.account_deletion_requests
  WHERE owner_id = p_owner_id AND id = deletion_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'deletion unavailable' USING ERRCODE = '42501'; END IF;
  IF deletion_row.status = 'pending' THEN
    IF coalesce(deletion_row.telegram_cleanup_status->>'status', '')
       IN ('queued','partial','failed','unavailable') THEN
      UPDATE app.account_deletion_requests
      SET telegram_cleanup_token_digest = decode(p_request->>'cleanup_token_digest', 'hex'),
          updated_at = observed_at
      WHERE id = deletion_row.id;
      SELECT telegram_chat_id INTO cleanup_chat_id FROM app.telegram_links
        WHERE owner_id = p_owner_id;
      SELECT coalesce(jsonb_agg(message_id ORDER BY message_id), '[]'::jsonb)
      INTO cleanup_messages FROM (
        SELECT DISTINCT candidate.message_id
        FROM (
          SELECT delivery.telegram_message_id::text AS message_id
          FROM app.telegram_deliveries delivery
          WHERE delivery.owner_id = p_owner_id AND delivery.telegram_message_id IS NOT NULL
            AND delivery.recorded_at >= observed_at - interval '48 hours'
          UNION ALL
          SELECT message_id
          FROM app.market_publications publication
          CROSS JOIN LATERAL jsonb_array_elements_text(publication.telegram_message_ids)
            AS messages(message_id)
          WHERE publication.owner_id = p_owner_id AND publication.delivered_at IS NOT NULL
            AND publication.delivered_at >= observed_at - interval '48 hours'
          UNION ALL
          SELECT message_id
          FROM app.operational_alerts alert
          CROSS JOIN LATERAL jsonb_array_elements_text(alert.telegram_message_ids)
            AS messages(message_id)
          WHERE alert.owner_id = p_owner_id AND alert.delivered_at IS NOT NULL
            AND alert.delivered_at >= observed_at - interval '48 hours'
        ) candidate
        WHERE candidate.message_id ~ '^[1-9][0-9]{0,18}$'
        ORDER BY candidate.message_id
        LIMIT 100
      ) recent;
    END IF;
    RETURN jsonb_build_object(
      'status', 'pending', 'deletion_request_id', deletion_row.id,
      'cancel_until', deletion_row.cancel_until, 'delete_by', deletion_row.delete_by,
      'older_telegram_history_requires_manual_removal', true, 'duplicate', true,
      '_telegram_cleanup', jsonb_build_object(
        'record_required', coalesce(deletion_row.telegram_cleanup_status->>'status', '')
          IN ('queued','partial','failed','unavailable'),
        'previous_status', CASE
          WHEN deletion_row.telegram_cleanup_status->>'status' IN ('completed','nothing_recent')
            THEN deletion_row.telegram_cleanup_status->>'status'
          ELSE NULL
        END,
        'attempted', coalesce((deletion_row.telegram_cleanup_status->>'attempted')::int, 0),
        'deleted', coalesce((deletion_row.telegram_cleanup_status->>'deleted')::int, 0),
        'failed', coalesce((deletion_row.telegram_cleanup_status->>'failed')::int, 0),
        'chat_id', cleanup_chat_id::text, 'message_ids', cleanup_messages
      )
    );
  END IF;
  IF deletion_row.status <> 'confirmation_pending'
     OR deletion_row.step_up_receipt_id <> receipt_id
     OR deletion_row.confirmation_expires_at <= observed_at THEN
    RAISE EXCEPTION 'deletion confirmation expired' USING ERRCODE = '42501';
  END IF;
  PERFORM app.assert_fresh_step_up(p_owner_id, receipt_id, p_request->>'session_digest', true);

  FOR connection_row IN
    SELECT id FROM app.agent_connections WHERE owner_id = p_owner_id AND status <> 'revoked'
  LOOP
    PERFORM app.revoke_agent_connection(
      p_owner_id, jsonb_build_object('connection_id', connection_row.id)
    );
  END LOOP;
  UPDATE app.analysis_schedules SET primary_connection_id = NULL,
    pre_market_enabled = false, intraday_enabled = false, post_market_enabled = false,
    updated_at = observed_at WHERE owner_id = p_owner_id;
  UPDATE app.notification_preferences SET pre_market_enabled = false,
    intraday_enabled = false, post_market_enabled = false, operational_enabled = false,
    updated_at = observed_at WHERE owner_id = p_owner_id;
  UPDATE app.telegram_links SET status = 'revoked', revoked_at = coalesce(revoked_at, observed_at)
    WHERE owner_id = p_owner_id AND status = 'active';
  UPDATE app.telegram_pairing_codes SET consumed_at = coalesce(consumed_at, observed_at)
    WHERE owner_id = p_owner_id AND consumed_at IS NULL;
  UPDATE app.telegram_callback_tokens SET invalidated_at = observed_at
    WHERE owner_id = p_owner_id AND consumed_at IS NULL AND invalidated_at IS NULL;
  UPDATE app.portfolio_commands SET status = 'cancelled', updated_at = observed_at
    WHERE owner_id = p_owner_id AND status IN ('submitted','previewed');
  SELECT telegram_chat_id INTO cleanup_chat_id FROM app.telegram_links
    WHERE owner_id = p_owner_id;
  SELECT count(DISTINCT candidate.message_id)::int INTO recent_messages
  FROM (
    SELECT delivery.telegram_message_id::text AS message_id
    FROM app.telegram_deliveries delivery
    WHERE delivery.owner_id = p_owner_id AND delivery.telegram_message_id IS NOT NULL
      AND delivery.recorded_at >= observed_at - interval '48 hours'
    UNION ALL
    SELECT message_id
    FROM app.market_publications publication
    CROSS JOIN LATERAL jsonb_array_elements_text(publication.telegram_message_ids)
      AS messages(message_id)
    WHERE publication.owner_id = p_owner_id AND publication.delivered_at IS NOT NULL
      AND publication.delivered_at >= observed_at - interval '48 hours'
    UNION ALL
    SELECT message_id
    FROM app.operational_alerts alert
    CROSS JOIN LATERAL jsonb_array_elements_text(alert.telegram_message_ids)
      AS messages(message_id)
    WHERE alert.owner_id = p_owner_id AND alert.delivered_at IS NOT NULL
      AND alert.delivered_at >= observed_at - interval '48 hours'
  ) candidate
  WHERE candidate.message_id ~ '^[1-9][0-9]{0,18}$';
  SELECT coalesce(jsonb_agg(message_id ORDER BY message_id), '[]'::jsonb)
  INTO cleanup_messages FROM (
    SELECT DISTINCT candidate.message_id
    FROM (
      SELECT delivery.telegram_message_id::text AS message_id
      FROM app.telegram_deliveries delivery
      WHERE delivery.owner_id = p_owner_id AND delivery.telegram_message_id IS NOT NULL
        AND delivery.recorded_at >= observed_at - interval '48 hours'
      UNION ALL
      SELECT message_id
      FROM app.market_publications publication
      CROSS JOIN LATERAL jsonb_array_elements_text(publication.telegram_message_ids)
        AS messages(message_id)
      WHERE publication.owner_id = p_owner_id AND publication.delivered_at IS NOT NULL
        AND publication.delivered_at >= observed_at - interval '48 hours'
      UNION ALL
      SELECT message_id
      FROM app.operational_alerts alert
      CROSS JOIN LATERAL jsonb_array_elements_text(alert.telegram_message_ids)
        AS messages(message_id)
      WHERE alert.owner_id = p_owner_id AND alert.delivered_at IS NOT NULL
        AND alert.delivered_at >= observed_at - interval '48 hours'
    ) candidate
    WHERE candidate.message_id ~ '^[1-9][0-9]{0,18}$'
    ORDER BY candidate.message_id
    LIMIT 100
  ) recent;
  UPDATE app.account_deletion_requests SET
    status = 'pending', requested_at = observed_at,
    cancel_until = observed_at + interval '72 hours',
    delete_by = observed_at + interval '7 days',
    telegram_cleanup_status = jsonb_build_object(
      'status', CASE WHEN recent_messages > 0 THEN 'queued' ELSE 'nothing_recent' END,
      'recent_eligible_count', least(recent_messages, 100),
      'older_history_requires_manual_removal', true
    ),
    telegram_cleanup_token_digest = decode(p_request->>'cleanup_token_digest', 'hex'),
    updated_at = observed_at
  WHERE id = deletion_id;
  UPDATE app.profiles SET status = 'deletion_pending', updated_at = observed_at
    WHERE id = p_owner_id;
  RETURN jsonb_build_object(
    'status', 'pending', 'deletion_request_id', deletion_id,
    'cancel_until', observed_at + interval '72 hours',
    'delete_by', observed_at + interval '7 days',
    'older_telegram_history_requires_manual_removal', true, 'duplicate', false,
    '_telegram_cleanup', jsonb_build_object(
      'record_required', true, 'previous_status', NULL,
      'attempted', 0, 'deleted', 0, 'failed', 0,
      'chat_id', cleanup_chat_id::text, 'message_ids', cleanup_messages
    )
  );
END
$$;

CREATE OR REPLACE FUNCTION app.record_account_telegram_cleanup(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE deletion_id UUID;
DECLARE attempted_count INT;
DECLARE deleted_count INT;
DECLARE failed_count INT;
DECLARE status_value TEXT;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['deletion_request_id','cleanup_token','attempted','deleted','failed','status']
  ) OR p_request->>'deletion_request_id' !~
       '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
     OR p_request->>'cleanup_token' !~ '^[A-Za-z0-9_-]{43}$'
     OR p_request->>'attempted' !~ '^[0-9]{1,3}$'
     OR p_request->>'deleted' !~ '^[0-9]{1,3}$'
     OR p_request->>'failed' !~ '^[0-9]{1,3}$'
     OR p_request->>'status' NOT IN ('nothing_recent','completed','partial','failed','unavailable') THEN
    RAISE EXCEPTION 'invalid Telegram cleanup result' USING ERRCODE = '22023';
  END IF;
  deletion_id := (p_request->>'deletion_request_id')::uuid;
  attempted_count := (p_request->>'attempted')::int;
  deleted_count := (p_request->>'deleted')::int;
  failed_count := (p_request->>'failed')::int;
  status_value := p_request->>'status';
  IF attempted_count > 100 OR deleted_count + failed_count <> attempted_count
     OR NOT EXISTS (
       SELECT 1 FROM app.account_deletion_requests
       WHERE owner_id = p_owner_id AND id = deletion_id AND status = 'pending'
         AND telegram_cleanup_token_digest = extensions.digest(
           p_request->>'cleanup_token', 'sha256'
         )
     ) THEN
    RAISE EXCEPTION 'Telegram cleanup unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.account_deletion_requests SET
    telegram_cleanup_status = jsonb_build_object(
      'status', status_value, 'attempted', attempted_count,
      'deleted', deleted_count, 'failed', failed_count,
      'older_history_requires_manual_removal', true,
      'recorded_at', clock_timestamp()
    ),
    telegram_cleanup_token_digest = NULL,
    updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND id = deletion_id;
  RETURN jsonb_build_object('status', 'recorded');
END
$$;

CREATE OR REPLACE FUNCTION app.cancel_account_deletion(p_owner_id UUID, p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE deletion_row app.account_deletion_requests%ROWTYPE;
DECLARE receipt_id UUID;
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(
    p_request, ARRAY['step_up_receipt_id','session_digest']
  ) THEN RAISE EXCEPTION 'invalid cancellation request' USING ERRCODE = '22023'; END IF;
  BEGIN receipt_id := (p_request->>'step_up_receipt_id')::uuid;
  EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'invalid cancellation request' USING ERRCODE = '22023'; END;
  SELECT * INTO deletion_row FROM app.account_deletion_requests
  WHERE owner_id = p_owner_id AND status = 'pending'
  ORDER BY requested_at DESC LIMIT 1 FOR UPDATE;
  IF NOT FOUND OR deletion_row.cancel_until <= observed_at THEN
    RAISE EXCEPTION 'cancellation window expired' USING ERRCODE = '42501';
  END IF;
  PERFORM app.assert_fresh_step_up(p_owner_id, receipt_id, p_request->>'session_digest', true);
  UPDATE app.account_deletion_requests SET status = 'cancelled', cancelled_at = observed_at,
    updated_at = observed_at WHERE id = deletion_row.id;
  UPDATE app.profiles SET status = 'active', updated_at = observed_at WHERE id = p_owner_id;
  RETURN jsonb_build_object(
    'status', 'cancelled', 'deletion_request_id', deletion_row.id,
    'credentials_require_reconnection', true
  );
END
$$;

-- Prepare the active-data purge in one transaction. The Auth identity remains until the
-- offline operator has committed this function and can delete Auth through the Admin API.
CREATE OR REPLACE FUNCTION app.operator_prepare_account_deletion(
  p_owner_id UUID,
  p_deletion_request_id UUID,
  p_confirmation TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE deletion_row app.account_deletion_requests%ROWTYPE;
DECLARE tombstone_row app.deletion_tombstones%ROWTYPE;
DECLARE observed_at TIMESTAMPTZ := clock_timestamp();
DECLARE connection_row RECORD;
DECLARE target_table TEXT;
DECLARE residue_count BIGINT;
BEGIN
  IF p_owner_id IS NULL OR p_deletion_request_id IS NULL
     OR p_confirmation <> 'DELETE AUTH ' || p_owner_id::text THEN
    RAISE EXCEPTION 'account deletion confirmation mismatch' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('account-delete:' || p_owner_id::text, 0));
  SELECT * INTO tombstone_row
  FROM app.deletion_tombstones
  WHERE owner_id = p_owner_id;
  IF FOUND THEN
    IF tombstone_row.deletion_request_id <> p_deletion_request_id THEN
      RAISE EXCEPTION 'account deletion unavailable' USING ERRCODE = '42501';
    END IF;
    RETURN jsonb_build_object(
      'status', 'ready_for_auth_deletion',
      'deletion_request_id', tombstone_row.deletion_request_id,
      'deleted_at', tombstone_row.deleted_at,
      'archives_expire_after', tombstone_row.archives_expire_after,
      'duplicate', true
    );
  END IF;
  SELECT * INTO deletion_row
  FROM app.account_deletion_requests
  WHERE owner_id = p_owner_id AND id = p_deletion_request_id
  FOR UPDATE;
  IF NOT FOUND OR deletion_row.status <> 'pending'
     OR deletion_row.cancel_until > observed_at
     OR NOT EXISTS (
       SELECT 1 FROM app.profiles
       WHERE id = p_owner_id AND status = 'deletion_pending'
     ) THEN
    RAISE EXCEPTION 'account deletion unavailable' USING ERRCODE = '42501';
  END IF;
  UPDATE app.account_deletion_requests
  SET status = 'processing', processing_started_at = observed_at, updated_at = observed_at
  WHERE id = deletion_row.id;

  -- Revoke any authority that appeared through an operator error after the owner confirmed.
  FOR connection_row IN
    SELECT id FROM app.agent_connections
    WHERE owner_id = p_owner_id AND (
      status <> 'revoked' OR inbound_token_digest IS NOT NULL
      OR outbound_trigger_secret_id IS NOT NULL OR trigger_url IS NOT NULL
    )
  LOOP
    PERFORM app.revoke_agent_connection(
      p_owner_id, jsonb_build_object('connection_id', connection_row.id)
    );
  END LOOP;
  IF EXISTS (
    SELECT 1 FROM app.agent_connections
    WHERE owner_id = p_owner_id AND (
      status <> 'revoked' OR inbound_token_digest IS NOT NULL
      OR outbound_trigger_secret_id IS NOT NULL OR trigger_url IS NOT NULL
    )
  ) THEN
    RAISE EXCEPTION 'account authority revocation incomplete';
  END IF;

  INSERT INTO app.deletion_tombstones(
    owner_id, deletion_request_id, deleted_at, archives_expire_after
  ) VALUES (
    p_owner_id, p_deletion_request_id, observed_at, observed_at + interval '35 days'
  );
  PERFORM set_config('stock_agent.account_purge_owner', p_owner_id::text, true);

  -- Children and cross-references precede their parents. Failed statements roll back the
  -- tombstone and every deletion, so the Auth identity is never removed after a partial purge.
  DELETE FROM app.telegram_pairing_deliveries delivery
  USING app.telegram_updates update_row
  WHERE delivery.telegram_update_id = update_row.telegram_update_id
    AND update_row.owner_id = p_owner_id;
  DELETE FROM app.telegram_callback_tokens WHERE owner_id = p_owner_id;
  DELETE FROM app.telegram_deliveries WHERE owner_id = p_owner_id;
  DELETE FROM app.telegram_pairing_codes WHERE owner_id = p_owner_id;
  DELETE FROM app.telegram_updates WHERE owner_id = p_owner_id;

  DELETE FROM app.routine_trigger_attempts WHERE owner_id = p_owner_id;
  DELETE FROM app.operational_alerts WHERE owner_id = p_owner_id;
  DELETE FROM app.operational_events WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_operational_state WHERE owner_id = p_owner_id;
  DELETE FROM app.scheduled_run_slots WHERE owner_id = p_owner_id;

  DELETE FROM app.run_evidence WHERE owner_id = p_owner_id;
  DELETE FROM app.source_search_receipts WHERE owner_id = p_owner_id;
  DELETE FROM app.agent_analysis_submissions WHERE owner_id = p_owner_id;
  DELETE FROM app.suggestion_grades WHERE owner_id = p_owner_id;
  DELETE FROM app.suggestions WHERE owner_id = p_owner_id;
  DELETE FROM app.market_publications WHERE owner_id = p_owner_id;
  DELETE FROM app.decision_evaluations WHERE owner_id = p_owner_id;
  DELETE FROM app.stock_observations WHERE owner_id = p_owner_id;
  DELETE FROM app.daily_snapshots WHERE owner_id = p_owner_id;
  DELETE FROM app.lessons WHERE owner_id = p_owner_id;
  DELETE FROM app.radar WHERE owner_id = p_owner_id;
  DELETE FROM app.paper_watches WHERE owner_id = p_owner_id;
  DELETE FROM app.market_quote_cache WHERE owner_id = p_owner_id;
  DELETE FROM app.corporate_action_states WHERE owner_id = p_owner_id;

  DELETE FROM app.transactions WHERE owner_id = p_owner_id AND event_type = 'void';
  DELETE FROM app.transactions WHERE owner_id = p_owner_id;
  DELETE FROM app.portfolio_commands WHERE owner_id = p_owner_id;
  DELETE FROM app.holdings WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_investment_plans WHERE owner_id = p_owner_id;
  DELETE FROM app.dry_powder WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_ledger_counters WHERE owner_id = p_owner_id;

  DELETE FROM app.analysis_runs WHERE owner_id = p_owner_id;
  DELETE FROM app.market_gateway_requests WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_run_allowances WHERE owner_id = p_owner_id;
  DELETE FROM app.app_api_audit_events WHERE owner_id = p_owner_id;
  DELETE FROM app.app_api_rate_limits WHERE owner_id = p_owner_id;
  DELETE FROM app.analysis_schedules WHERE owner_id = p_owner_id;
  DELETE FROM app.agent_connections WHERE owner_id = p_owner_id;
  DELETE FROM app.notification_preferences WHERE owner_id = p_owner_id;
  DELETE FROM app.telegram_links WHERE owner_id = p_owner_id;
  DELETE FROM app.user_consents WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_policy_overrides WHERE owner_id = p_owner_id;
  DELETE FROM app.single_owner_migration_receipts WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_ledger_reset_receipts WHERE owner_id = p_owner_id;
  DELETE FROM app.app_admins WHERE user_id = p_owner_id;
  DELETE FROM app.account_deletion_requests WHERE owner_id = p_owner_id;
  DELETE FROM app.account_step_up_receipts WHERE owner_id = p_owner_id;
  DELETE FROM app.account_step_up_challenges WHERE owner_id = p_owner_id;
  PERFORM set_config('stock_agent.account_purge_owner', '', true);

  FOR target_table IN
    SELECT column_row.table_name
    FROM information_schema.columns column_row
    JOIN pg_catalog.pg_class table_row ON table_row.relname = column_row.table_name
    JOIN pg_catalog.pg_namespace schema_row
      ON schema_row.oid = table_row.relnamespace AND schema_row.nspname = column_row.table_schema
    WHERE column_row.table_schema = 'app' AND column_row.column_name = 'owner_id'
      AND column_row.table_name <> 'deletion_tombstones' AND table_row.relkind = 'r'
    ORDER BY column_row.table_name
  LOOP
    EXECUTE format('SELECT count(*) FROM app.%I WHERE owner_id = $1', target_table)
      INTO residue_count USING p_owner_id;
    IF residue_count <> 0 THEN
      RAISE EXCEPTION 'account purge verification failed for %', target_table;
    END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM app.app_admins WHERE user_id = p_owner_id) THEN
    RAISE EXCEPTION 'account purge verification failed for app_admins';
  END IF;
  RETURN jsonb_build_object(
    'status', 'ready_for_auth_deletion',
    'deletion_request_id', p_deletion_request_id,
    'deleted_at', observed_at,
    'archives_expire_after', observed_at + interval '35 days',
    'duplicate', false
  );
END
$$;

CREATE OR REPLACE FUNCTION app.operator_preview_ledger_reset(
  p_owner_id UUID,
  p_step_up_receipt_id UUID
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE result JSONB;
BEGIN
  IF p_owner_id IS NULL OR p_step_up_receipt_id IS NULL OR NOT EXISTS (
    SELECT 1 FROM app.account_step_up_receipts
    WHERE owner_id = p_owner_id AND id = p_step_up_receipt_id
      AND consumed_at IS NULL AND expires_at > clock_timestamp()
  ) THEN RAISE EXCEPTION 'fresh step up required' USING ERRCODE = '42501'; END IF;
  SELECT jsonb_build_object(
    'holdings', (SELECT count(*) FROM app.holdings WHERE owner_id = p_owner_id),
    'transactions', (SELECT count(*) FROM app.transactions WHERE owner_id = p_owner_id),
    'commands', (SELECT count(*) FROM app.portfolio_commands WHERE owner_id = p_owner_id),
    'plans', (SELECT count(*) FROM app.owner_investment_plans WHERE owner_id = p_owner_id),
    'dry_powder', (SELECT count(*) FROM app.dry_powder WHERE owner_id = p_owner_id)
  ) INTO result;
  RETURN result;
END
$$;

CREATE OR REPLACE FUNCTION app.operator_apply_ledger_reset(
  p_owner_id UUID,
  p_step_up_receipt_id UUID,
  p_export_digest TEXT,
  p_confirmation TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE counts JSONB;
DECLARE reset_id UUID := extensions.gen_random_uuid();
BEGIN
  IF p_export_digest !~ '^[0-9a-f]{64}$'
     OR p_confirmation <> 'RESET ' || p_owner_id::text THEN
    RAISE EXCEPTION 'reset confirmation mismatch' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('ledger-reset:' || p_owner_id::text, 0));
  counts := app.operator_preview_ledger_reset(p_owner_id, p_step_up_receipt_id);
  UPDATE app.account_step_up_receipts SET consumed_at = clock_timestamp()
    WHERE owner_id = p_owner_id AND id = p_step_up_receipt_id
      AND consumed_at IS NULL AND expires_at > clock_timestamp();
  IF NOT FOUND THEN RAISE EXCEPTION 'fresh step up required' USING ERRCODE = '42501'; END IF;
  PERFORM set_config('stock_agent.ledger_reset_owner', p_owner_id::text, true);
  DELETE FROM app.telegram_callback_tokens WHERE owner_id = p_owner_id;
  DELETE FROM app.transactions WHERE owner_id = p_owner_id AND event_type = 'void';
  DELETE FROM app.transactions WHERE owner_id = p_owner_id;
  DELETE FROM app.portfolio_commands WHERE owner_id = p_owner_id;
  DELETE FROM app.holdings WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_investment_plans WHERE owner_id = p_owner_id;
  DELETE FROM app.dry_powder WHERE owner_id = p_owner_id;
  DELETE FROM app.owner_ledger_counters WHERE owner_id = p_owner_id;
  PERFORM set_config('stock_agent.ledger_reset_owner', '', true);
  IF EXISTS (SELECT 1 FROM app.holdings WHERE owner_id = p_owner_id)
     OR EXISTS (SELECT 1 FROM app.transactions WHERE owner_id = p_owner_id) THEN
    RAISE EXCEPTION 'ledger reset verification failed';
  END IF;
  INSERT INTO app.owner_ledger_reset_receipts(
    id, owner_id, step_up_receipt_id, export_digest, row_counts
  ) VALUES (reset_id, p_owner_id, p_step_up_receipt_id, p_export_digest, counts);
  RETURN jsonb_build_object('status', 'reset', 'reset_receipt_id', reset_id, 'row_counts', counts);
END
$$;

-- Preserve append-only behavior for every normal path. Only the ungranted operator reset function
-- can set this transaction-local owner marker before deleting that owner's ledger rows.
CREATE OR REPLACE FUNCTION app.reject_ledger_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF TG_OP = 'DELETE'
     AND (
       current_setting('stock_agent.ledger_reset_owner', true) = OLD.owner_id::text
       OR current_setting('stock_agent.account_purge_owner', true) = OLD.owner_id::text
     ) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'ledger is append-only' USING ERRCODE = '55000';
END
$$;
ALTER FUNCTION app.reject_ledger_mutation() OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.reject_ledger_mutation()
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION public.reject_decision_evaluation_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF TG_OP = 'DELETE'
     AND current_setting('stock_agent.account_purge_owner', true) = OLD.owner_id::text THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'decision evaluations are append-only' USING ERRCODE = '55000';
END
$$;
ALTER FUNCTION public.reject_decision_evaluation_mutation()
  OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION public.reject_decision_evaluation_mutation()
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

DO $$
DECLARE signature REGPROCEDURE;
BEGIN
  FOREACH signature IN ARRAY ARRAY[
    'app.accept_owner_consent(uuid,jsonb)'::regprocedure,
    'app.create_account_step_up_challenge(uuid,jsonb)'::regprocedure,
    'app.complete_account_step_up(uuid,jsonb)'::regprocedure,
    'app.assert_fresh_step_up(uuid,uuid,text,boolean)'::regprocedure,
    'app.export_owner_account(uuid,jsonb)'::regprocedure,
    'app.csv_safe_cell(text)'::regprocedure,
    'app.export_owner_ledger(uuid,jsonb)'::regprocedure,
    'app.request_account_deletion(uuid,jsonb)'::regprocedure,
    'app.confirm_account_deletion(uuid,jsonb)'::regprocedure,
    'app.record_account_telegram_cleanup(uuid,jsonb)'::regprocedure,
    'app.cancel_account_deletion(uuid,jsonb)'::regprocedure,
    'app.operator_prepare_account_deletion(uuid,uuid,text)'::regprocedure,
    'app.operator_preview_ledger_reset(uuid,uuid)'::regprocedure,
    'app.operator_apply_ledger_reset(uuid,uuid,text,text)'::regprocedure
  ] LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO stock_agent_migration_owner', signature);
    EXECUTE format(
      'REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated, service_role, '
      'stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup',
      signature
    );
  END LOOP;
END
$$;

-- Offline invitation bootstrap. The service-role credential is allowed only in the operator CLI;
-- no runtime function contains it, and ordinary authenticated users cannot execute this RPC.
GRANT SELECT (id) ON auth.users TO stock_agent_migration_owner;
CREATE OR REPLACE FUNCTION api.operator_initialize_invited_user(p_owner_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
  IF p_owner_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM auth.users WHERE id = p_owner_id)
     OR EXISTS (SELECT 1 FROM app.deletion_tombstones WHERE owner_id = p_owner_id)
     OR EXISTS (SELECT 1 FROM app.profiles WHERE id = p_owner_id) THEN
    RAISE EXCEPTION 'invitation identity unavailable' USING ERRCODE = '42501';
  END IF;
  INSERT INTO app.profiles(id, status) VALUES (p_owner_id, 'invited');
  INSERT INTO app.notification_preferences(owner_id) VALUES (p_owner_id);
  RETURN jsonb_build_object('status', 'invited');
END
$$;
ALTER FUNCTION api.operator_initialize_invited_user(UUID) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION api.operator_initialize_invited_user(UUID)
  FROM PUBLIC, anon, authenticated,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION api.operator_initialize_invited_user(UUID) TO service_role;
GRANT USAGE ON SCHEMA api TO service_role;

DROP VIEW IF EXISTS api.account_status;
CREATE VIEW api.account_status WITH (security_invoker = true) AS
SELECT profile.status AS account_status,
       deletion.status AS deletion_status,
       deletion.requested_at,
       deletion.cancel_until,
       deletion.delete_by,
       deletion.telegram_cleanup_status,
       deletion.cancelled_at,
       deletion.completed_at
FROM app.profiles profile
LEFT JOIN LATERAL (
  SELECT request.status, request.requested_at, request.cancel_until, request.delete_by,
         request.telegram_cleanup_status, request.cancelled_at, request.completed_at
  FROM app.account_deletion_requests request
  WHERE request.owner_id = profile.id
  ORDER BY request.created_at DESC LIMIT 1
) deletion ON true;
ALTER VIEW api.account_status OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.account_status FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.account_status TO authenticated;

-- Once deletion starts, user JWTs may reach only the lifecycle view. Machine roles retain access
-- long enough to finish revocation/cleanup, while every normal invoker view becomes empty.
DO $$
DECLARE target_table TEXT;
BEGIN
  FOREACH target_table IN ARRAY ARRAY[
    'notification_preferences','analysis_schedules','agent_connections','telegram_links',
    'telegram_pairing_codes','telegram_callback_tokens','telegram_deliveries',
    'run_evidence','source_search_receipts','market_quote_cache','corporate_action_states',
    'agent_analysis_submissions','scheduled_run_slots','routine_trigger_attempts',
    'owner_run_allowances','operational_events','owner_operational_state','operational_alerts',
    'owner_ledger_counters','app_api_rate_limits','app_api_audit_events','holdings',
    'analysis_runs','transactions','portfolio_commands','telegram_updates','suggestions',
    'suggestion_grades','stock_observations','daily_snapshots','dry_powder','radar','lessons',
    'paper_watches','market_gateway_requests','decision_evaluations','market_publications',
    'owner_investment_plans'
  ] LOOP
    EXECUTE format(
      'CREATE POLICY %I ON app.%I AS RESTRICTIVE FOR ALL TO authenticated '
      'USING (auth.uid() IS NOT NULL AND EXISTS ('
      'SELECT 1 FROM app.profiles lifecycle_profile '
      'WHERE lifecycle_profile.id = (SELECT auth.uid()) AND lifecycle_profile.status = ''active'')) '
      'WITH CHECK (auth.uid() IS NOT NULL AND EXISTS ('
      'SELECT 1 FROM app.profiles lifecycle_profile '
      'WHERE lifecycle_profile.id = (SELECT auth.uid()) AND lifecycle_profile.status = ''active''))',
      target_table || '_active_account_guard', target_table
    );
  END LOOP;
END
$$;

-- Keep the previous reviewed dispatcher private, then add a lifecycle-aware public wrapper.
ALTER FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB) SET SCHEMA app;
ALTER FUNCTION app.app_dispatch(TEXT, UUID, TEXT, JSONB) RENAME TO dispatch_active_request;
REVOKE ALL ON FUNCTION app.dispatch_active_request(TEXT, UUID, TEXT, JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

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
DECLARE owner_value UUID := auth.uid();
DECLARE profile_status TEXT;
DECLARE scope_value TEXT;
DECLARE owner_limit JSONB;
DECLARE client_limit JSONB;
DECLARE data_value JSONB;
DECLARE result_code TEXT;
BEGIN
  IF owner_value IS NULL OR p_request_id IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'UNAUTHORIZED'));
  END IF;
  IF jsonb_typeof(p_request) <> 'object' OR p_ip_digest !~ '^[0-9a-f]{64}$' THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'INVALID_REQUEST'));
  END IF;
  SELECT status INTO profile_status FROM app.profiles WHERE id = owner_value;
  IF profile_status IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'NOT_FOUND'));
  END IF;
  IF profile_status = 'deletion_pending' AND p_route NOT IN (
    'GET /account/status','GET /export/account.json','GET /export/ledger.csv',
    'POST /account/step-up/challenge','POST /account/step-up/complete',
    'POST /account/delete/confirm','POST /account/delete/cancel',
    'POST /account/delete/cleanup-result'
  ) THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'ACCOUNT_DELETION_PENDING'));
  END IF;
  IF profile_status = 'invited' AND p_route NOT IN (
    'POST /consents/accept','GET /account/status','GET /export/account.json',
    'GET /export/ledger.csv','POST /account/step-up/challenge','POST /account/step-up/complete'
  ) THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'CONSENT_REQUIRED'));
  END IF;
  IF p_route = 'POST /telegram/pairing-code' THEN
    IF NOT app.jsonb_has_exact_keys(
      p_request, ARRAY['code_digest','step_up_receipt_id','session_digest']
    ) THEN
      RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'INVALID_REQUEST'));
    END IF;
    PERFORM app.assert_fresh_step_up(
      owner_value, (p_request->>'step_up_receipt_id')::uuid,
      p_request->>'session_digest', false
    );
    RETURN app.dispatch_active_request(
      p_route, p_request_id, p_ip_digest,
      p_request - 'step_up_receipt_id' - 'session_digest'
    );
  END IF;
  IF p_route = 'POST /connections/handshake' THEN
    IF NOT app.jsonb_has_exact_keys(
      p_request, ARRAY['connection_id','trigger_url','trigger_token',
        'step_up_receipt_id','session_digest']
    ) THEN
      RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'INVALID_REQUEST'));
    END IF;
    PERFORM app.assert_fresh_step_up(
      owner_value, (p_request->>'step_up_receipt_id')::uuid,
      p_request->>'session_digest', false
    );
    RETURN app.dispatch_active_request(
      p_route, p_request_id, p_ip_digest,
      p_request - 'step_up_receipt_id' - 'session_digest'
    );
  END IF;
  IF p_route NOT IN (
    'POST /consents/accept','GET /account/status','GET /export/account.json',
    'GET /export/ledger.csv','POST /account/step-up/challenge','POST /account/step-up/complete',
    'POST /account/delete/request','POST /account/delete/confirm','POST /account/delete/cancel',
    'POST /account/delete/cleanup-result'
  ) THEN
    RETURN app.dispatch_active_request(p_route, p_request_id, p_ip_digest, p_request);
  END IF;

  scope_value := CASE
    WHEN p_route = 'POST /consents/accept' THEN 'consent'
    WHEN p_route = 'GET /account/status' THEN 'account_status'
    WHEN p_route LIKE 'GET /export/%' THEN 'export'
    WHEN p_route LIKE 'POST /account/step-up/%' THEN 'account_step_up'
    ELSE 'account_delete'
  END;
  owner_limit := app.consume_rate_limit(owner_value, scope_value, 'owner',
    CASE scope_value WHEN 'account_status' THEN 60 WHEN 'account_step_up' THEN 10 WHEN 'export' THEN 4 ELSE 5 END,
    CASE scope_value WHEN 'account_status' THEN 60 WHEN 'account_step_up' THEN 600 ELSE 86400 END);
  client_limit := app.consume_rate_limit(owner_value, scope_value, p_ip_digest,
    CASE scope_value WHEN 'account_status' THEN 60 WHEN 'account_step_up' THEN 10 WHEN 'export' THEN 4 ELSE 5 END,
    CASE scope_value WHEN 'account_status' THEN 60 WHEN 'account_step_up' THEN 600 ELSE 86400 END);
  IF NOT (owner_limit->>'allowed')::boolean OR NOT (client_limit->>'allowed')::boolean THEN
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, 'RATE_LIMITED');
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object(
      'code', 'RATE_LIMITED', 'retry_after_seconds', greatest(
        (owner_limit->>'retry_after_seconds')::int,
        (client_limit->>'retry_after_seconds')::int
      )
    ));
  END IF;
  BEGIN
    CASE p_route
      WHEN 'POST /consents/accept' THEN data_value := app.accept_owner_consent(owner_value, p_request);
      WHEN 'GET /account/status' THEN
        SELECT jsonb_build_object(
          'account_status', account_status, 'deletion_status', deletion_status,
          'requested_at', requested_at, 'cancel_until', cancel_until, 'delete_by', delete_by,
          'telegram_cleanup_status', coalesce(telegram_cleanup_status, '{}'::jsonb),
          'cancelled_at', cancelled_at, 'completed_at', completed_at
        ) INTO data_value FROM api.account_status;
      WHEN 'GET /export/account.json' THEN data_value := app.export_owner_account(owner_value, p_request);
      WHEN 'GET /export/ledger.csv' THEN data_value := app.export_owner_ledger(owner_value, p_request);
      WHEN 'POST /account/step-up/challenge' THEN
        data_value := app.create_account_step_up_challenge(owner_value, p_request);
      WHEN 'POST /account/step-up/complete' THEN
        data_value := app.complete_account_step_up(owner_value, p_request);
      WHEN 'POST /account/delete/request' THEN
        data_value := app.request_account_deletion(owner_value, p_request);
      WHEN 'POST /account/delete/confirm' THEN
        data_value := app.confirm_account_deletion(owner_value, p_request);
      WHEN 'POST /account/delete/cleanup-result' THEN
        data_value := app.record_account_telegram_cleanup(owner_value, p_request);
      WHEN 'POST /account/delete/cancel' THEN
        data_value := app.cancel_account_deletion(owner_value, p_request);
      ELSE RAISE EXCEPTION 'route unavailable' USING ERRCODE = '42501';
    END CASE;
    result_code := upper(coalesce(data_value->>'status', 'OK'));
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, result_code);
    RETURN jsonb_build_object('ok', true, 'data', data_value, 'request_id', p_request_id);
  EXCEPTION
    WHEN invalid_text_representation OR invalid_parameter_value OR check_violation
      OR numeric_value_out_of_range OR datetime_field_overflow THEN result_code := 'INVALID_REQUEST';
    WHEN unique_violation OR serialization_failure THEN result_code := 'CONFLICT';
    WHEN insufficient_privilege THEN result_code := 'NOT_FOUND';
    WHEN OTHERS THEN
      result_code := CASE
        WHEN SQLERRM ~* '(invalid|confirmation mismatch)' THEN 'INVALID_REQUEST'
        WHEN SQLERRM ~* '(fresh step up|unavailable|expired|cancellation window)' THEN 'NOT_FOUND'
        ELSE 'INTERNAL_ERROR'
      END;
  END;
  INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
  VALUES (owner_value, p_request_id, p_route, result_code);
  RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', result_code));
END
$$;
ALTER FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB)
  FROM PUBLIC, anon, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT EXECUTE ON FUNCTION api.app_dispatch(TEXT, UUID, TEXT, JSONB) TO authenticated;

ALTER DEFAULT PRIVILEGES FOR ROLE stock_agent_migration_owner IN SCHEMA app
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;

-- Execute-only disaster-recovery export contract. The contract snapshots every app table's
-- columns at migration time, so a later unreviewed table or column makes export fail closed.
CREATE TABLE app.backup_restore_receipts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  archive_digest TEXT NOT NULL CHECK (archive_digest ~ '^[0-9a-f]{64}$'),
  source_exported_at TIMESTAMPTZ NOT NULL,
  row_counts JSONB NOT NULL CHECK (
    jsonb_typeof(row_counts) = 'object' AND octet_length(row_counts::text) <= 10000
  ),
  relationship_digest TEXT NOT NULL CHECK (relationship_digest ~ '^[0-9a-f]{64}$'),
  projection_digest TEXT NOT NULL CHECK (projection_digest ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status = 'verified'),
  restored_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE app.backup_restore_receipts OWNER TO stock_agent_migration_owner;
ALTER TABLE app.backup_restore_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.backup_restore_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY backup_restore_receipts_executor_all ON app.backup_restore_receipts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE TRIGGER prevent_backup_restore_receipt_mutation
  BEFORE UPDATE OR DELETE ON app.backup_restore_receipts
  FOR EACH ROW EXECUTE FUNCTION app.prevent_immutable_receipt_mutation();
REVOKE ALL ON app.backup_restore_receipts
  FROM PUBLIC, anon, authenticated, service_role, stock_agent_gateway,
       stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE TABLE machine.backup_dataset_contract (
  name TEXT PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]{1,62}$'),
  disposition TEXT NOT NULL CHECK (
    disposition IN ('include','exclude_transient','exclude_rebuildable')
  ),
  excluded_columns TEXT[] NOT NULL DEFAULT '{}'::text[],
  source_columns TEXT[] NOT NULL DEFAULT '{}'::text[],
  reason_code TEXT NOT NULL CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
  CHECK (disposition = 'include' OR cardinality(excluded_columns) = 0)
);
ALTER TABLE machine.backup_dataset_contract OWNER TO stock_agent_migration_owner;
REVOKE ALL ON machine.backup_dataset_contract
  FROM PUBLIC, anon, authenticated, service_role, stock_agent_gateway,
       stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

INSERT INTO machine.backup_dataset_contract(name, disposition, excluded_columns, reason_code)
VALUES
  ('profiles', 'include', '{}', 'DURABLE_IDENTITY_PROFILE'),
  ('app_admins', 'include', '{}', 'DURABLE_OPERATOR_ROLE'),
  ('user_consents', 'include', '{}', 'DURABLE_CONSENT'),
  ('notification_preferences', 'include', '{}', 'DURABLE_OWNER_SETTING'),
  ('agent_connections', 'include', ARRAY['inbound_token_digest','outbound_trigger_secret_id','trigger_url'], 'SANITIZED_PROVIDER_CONNECTION'),
  ('analysis_schedules', 'include', '{}', 'DURABLE_OWNER_SETTING'),
  ('telegram_links', 'include', '{}', 'ENCRYPTED_OWNER_LINK'),
  ('single_owner_migration_receipts', 'exclude_rebuildable', '{}', 'LEGACY_MIGRATION_RECEIPT'),
  ('owner_policy_overrides', 'include', '{}', 'DURABLE_POLICY'),
  ('holdings', 'include', '{}', 'DURABLE_PORTFOLIO_PROJECTION'),
  ('analysis_runs', 'include', '{}', 'DURABLE_RUN_AUDIT'),
  ('transactions', 'include', '{}', 'DURABLE_LEDGER'),
  ('portfolio_commands', 'include', '{}', 'DURABLE_COMMAND_AUDIT'),
  ('telegram_updates', 'include', '{}', 'BOUNDED_REPLAY_AUDIT'),
  ('suggestions', 'include', '{}', 'DURABLE_RECOMMENDATION'),
  ('suggestion_grades', 'include', '{}', 'DURABLE_OUTCOME_GRADE'),
  ('stock_observations', 'include', '{}', 'DURABLE_RESEARCH'),
  ('daily_snapshots', 'include', '{}', 'DURABLE_MARKET_SNAPSHOT'),
  ('dry_powder', 'include', '{}', 'DURABLE_OWNER_SETTING'),
  ('radar', 'include', '{}', 'DURABLE_WATCHLIST'),
  ('lessons', 'include', '{}', 'DURABLE_RESEARCH'),
  ('paper_watches', 'include', '{}', 'DURABLE_OWNER_RESEARCH'),
  ('market_gateway_requests', 'include', '{}', 'DURABLE_GATEWAY_AUDIT'),
  ('decision_evaluations', 'include', '{}', 'DURABLE_POLICY_AUDIT'),
  ('market_publications', 'include', ARRAY['lease_token'], 'SANITIZED_PUBLICATION_AUDIT'),
  ('owner_investment_plans', 'include', '{}', 'DURABLE_INVESTMENT_PLAN'),
  ('owner_ledger_counters', 'include', '{}', 'DURABLE_LEDGER_SEQUENCE'),
  ('app_api_rate_limits', 'exclude_transient', '{}', 'TRANSIENT_RATE_LIMIT'),
  ('app_api_audit_events', 'include', '{}', 'BOUNDED_REQUEST_AUDIT'),
  ('run_evidence', 'include', '{}', 'DURABLE_EVIDENCE'),
  ('source_search_receipts', 'include', '{}', 'DURABLE_SOURCE_AUDIT'),
  ('market_quote_cache', 'exclude_rebuildable', '{}', 'REFETCH_MARKET_QUOTE'),
  ('corporate_action_states', 'include', '{}', 'DURABLE_SAFETY_STATE'),
  ('agent_analysis_submissions', 'include', '{}', 'DURABLE_PROVIDER_AUDIT'),
  ('scheduled_run_slots', 'exclude_transient', '{}', 'REBUILD_RUN_SLOTS'),
  ('routine_trigger_attempts', 'exclude_transient', '{}', 'TRANSIENT_PROVIDER_TRIGGER'),
  ('owner_run_allowances', 'exclude_transient', '{}', 'TRANSIENT_DAILY_ALLOWANCE'),
  ('operational_events', 'include', '{}', 'DURABLE_OPERATIONAL_AUDIT'),
  ('owner_operational_state', 'include', '{}', 'DURABLE_SAFETY_STATE'),
  ('operational_alerts', 'include', ARRAY['lease_token'], 'SANITIZED_ALERT_AUDIT'),
  ('telegram_pairing_codes', 'exclude_transient', '{}', 'SECRET_PAIRING_CLAIM'),
  ('telegram_callback_tokens', 'exclude_transient', '{}', 'SECRET_CALLBACK_CLAIM'),
  ('telegram_deliveries', 'include', '{}', 'DURABLE_DELIVERY_AUDIT'),
  ('telegram_pairing_deliveries', 'exclude_transient', '{}', 'TRANSIENT_PAIRING_DELIVERY'),
  ('account_step_up_challenges', 'exclude_transient', '{}', 'SECRET_SESSION_CHALLENGE'),
  ('account_step_up_receipts', 'exclude_transient', '{}', 'SECRET_SESSION_RECEIPT'),
  ('account_deletion_requests', 'exclude_transient', '{}', 'REBUILD_DELETION_WORKFLOW'),
  ('deletion_tombstones', 'include', '{}', 'DURABLE_DELETION_TOMBSTONE'),
  ('owner_ledger_reset_receipts', 'include', '{}', 'DURABLE_RESET_AUDIT'),
  ('backup_restore_receipts', 'exclude_rebuildable', '{}', 'TARGET_RESTORE_RECEIPT');

UPDATE machine.backup_dataset_contract AS contract
SET source_columns = catalog.columns
FROM (
  SELECT c.relname AS name, array_agg(a.attname::text ORDER BY a.attnum) AS columns
  FROM pg_catalog.pg_class AS c
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
  WHERE n.nspname = 'app' AND c.relkind = 'r'
    AND a.attnum > 0 AND NOT a.attisdropped
  GROUP BY c.relname
) AS catalog
WHERE contract.name = catalog.name;

DO $$
DECLARE target_table TEXT;
BEGIN
  FOR target_table IN SELECT name FROM machine.backup_dataset_contract LOOP
    EXECUTE format('DROP POLICY IF EXISTS backup_export_executor_all ON app.%I', target_table);
    EXECUTE format(
      'CREATE POLICY backup_export_executor_all ON app.%I FOR SELECT '
      'TO stock_agent_migration_owner USING (true)', target_table
    );
  END LOOP;
END
$$;

GRANT SELECT (id, email) ON auth.users TO stock_agent_migration_owner;

CREATE OR REPLACE FUNCTION machine.backup_export_catalog(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE expected_tables TEXT[];
DECLARE actual_tables TEXT[];
DECLARE drifted_table TEXT;
DECLARE table_manifest JSONB;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['schema_version'])
     OR jsonb_typeof(p_request->'schema_version') <> 'number'
     OR (p_request->>'schema_version')::int <> 1 THEN
    RAISE EXCEPTION 'backup contract unavailable' USING ERRCODE = '22023';
  END IF;
  SELECT array_agg(name ORDER BY name) INTO expected_tables
  FROM machine.backup_dataset_contract;
  SELECT array_agg(c.relname ORDER BY c.relname) INTO actual_tables
  FROM pg_catalog.pg_class AS c
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  WHERE n.nspname = 'app' AND c.relkind = 'r';
  IF expected_tables IS DISTINCT FROM actual_tables THEN
    RAISE EXCEPTION 'backup contract unavailable: app table drift' USING ERRCODE = '55000';
  END IF;
  SELECT contract.name INTO drifted_table
  FROM machine.backup_dataset_contract AS contract
  WHERE contract.source_columns IS DISTINCT FROM (
    SELECT array_agg(a.attname::text ORDER BY a.attnum)
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
    WHERE n.nspname = 'app' AND c.relname = contract.name
      AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
  )
  LIMIT 1;
  IF drifted_table IS NOT NULL THEN
    RAISE EXCEPTION 'backup contract unavailable: app column drift' USING ERRCODE = '55000';
  END IF;
  SELECT jsonb_agg(jsonb_build_object(
    'name', name,
    'disposition', disposition,
    'columns', source_columns,
    'excluded_columns', excluded_columns,
    'reason_code', reason_code
  ) ORDER BY name) INTO table_manifest
  FROM machine.backup_dataset_contract;
  RETURN jsonb_build_object('schema_version', 1, 'tables', table_manifest);
END
$$;
ALTER FUNCTION machine.backup_export_catalog(JSONB) OWNER TO stock_agent_migration_owner;

CREATE OR REPLACE FUNCTION machine.backup_export_dataset(p_request JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE dataset_name TEXT;
DECLARE contract machine.backup_dataset_contract%ROWTYPE;
DECLARE exported_rows JSONB;
DECLARE exported_columns TEXT[];
DECLARE row_patch JSONB := '{}'::jsonb;
BEGIN
  IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['schema_version','dataset'])
     OR jsonb_typeof(p_request->'schema_version') <> 'number'
     OR jsonb_typeof(p_request->'dataset') <> 'string'
     OR (p_request->>'schema_version')::int <> 1 THEN
    RAISE EXCEPTION 'backup contract unavailable' USING ERRCODE = '22023';
  END IF;
  PERFORM machine.backup_export_catalog(jsonb_build_object('schema_version', 1));
  dataset_name := p_request->>'dataset';
  IF dataset_name = 'identity_recovery' THEN
    SELECT coalesce(jsonb_agg(jsonb_build_object(
      'email', lower(auth_user.email), 'owner_id', profile.id
    ) ORDER BY profile.id), '[]'::jsonb)
    INTO exported_rows
    FROM app.profiles AS profile
    JOIN auth.users AS auth_user ON auth_user.id = profile.id
    WHERE auth_user.email IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM app.deletion_tombstones AS tombstone
        WHERE tombstone.owner_id = profile.id
      );
    RETURN jsonb_build_object(
      'dataset', dataset_name,
      'columns', jsonb_build_array('email','owner_id'),
      'rows', exported_rows
    );
  END IF;
  SELECT * INTO contract
  FROM machine.backup_dataset_contract
  WHERE name = dataset_name AND disposition = 'include';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'backup dataset unavailable' USING ERRCODE = '42501';
  END IF;
  IF dataset_name = 'agent_connections' THEN
    row_patch := jsonb_build_object('status', 'disabled', 'last_handshake_at', NULL);
  END IF;
  EXECUTE format(
    'SELECT coalesce(jsonb_agg(row_value ORDER BY row_value::text), ''[]''::jsonb) '
    'FROM (SELECT (to_jsonb(source_row) - $1::text[]) || $2::jsonb AS row_value '
    'FROM app.%I AS source_row) AS exported',
    contract.name
  ) INTO exported_rows USING contract.excluded_columns, row_patch;
  SELECT array_agg(column_name ORDER BY column_name) INTO exported_columns
  FROM unnest(contract.source_columns) AS column_name
  WHERE NOT (column_name = ANY(contract.excluded_columns));
  RETURN jsonb_build_object(
    'dataset', dataset_name,
    'columns', to_jsonb(exported_columns),
    'rows', exported_rows
  );
END
$$;
ALTER FUNCTION machine.backup_export_dataset(JSONB) OWNER TO stock_agent_migration_owner;

REVOKE ALL ON FUNCTION machine.backup_export_catalog(JSONB),
  machine.backup_export_dataset(JSONB)
  FROM PUBLIC, anon, authenticated, service_role, stock_agent_gateway,
       stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT USAGE ON SCHEMA machine TO stock_agent_backup;
GRANT EXECUTE ON FUNCTION machine.backup_export_catalog(JSONB),
  machine.backup_export_dataset(JSONB) TO stock_agent_backup;
