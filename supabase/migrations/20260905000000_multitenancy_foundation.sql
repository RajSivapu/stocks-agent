-- Multi-tenant identity and schema foundation.
-- Supabase CLI migration; existing data must be backfilled before the next migration.
--
-- This migration is deliberately additive. Legacy owner rows remain nullable until the
-- operator-confirmed, fail-closed backfill in the next release gate has proved parity.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_agent_migration_owner') THEN
    CREATE ROLE stock_agent_migration_owner NOLOGIN NOINHERIT;
  END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION stock_agent_migration_owner;
CREATE SCHEMA IF NOT EXISTS api AUTHORIZATION stock_agent_migration_owner;
CREATE SCHEMA IF NOT EXISTS machine AUTHORIZATION stock_agent_migration_owner;

ALTER SCHEMA app OWNER TO stock_agent_migration_owner;
ALTER SCHEMA api OWNER TO stock_agent_migration_owner;
ALTER SCHEMA machine OWNER TO stock_agent_migration_owner;

REVOKE CREATE ON SCHEMA public FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SCHEMA app, machine FROM PUBLIC, anon, authenticated;
REVOKE CREATE ON SCHEMA api FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS app.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  display_name TEXT CHECK (display_name IS NULL OR char_length(display_name) <= 120),
  timezone TEXT NOT NULL DEFAULT 'America/Chicago'
    CHECK (char_length(timezone) BETWEEN 1 AND 100),
  status TEXT NOT NULL DEFAULT 'invited'
    CHECK (status IN ('invited', 'active', 'deactivated', 'deletion_pending')),
  onboarding_completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.app_admins (
  user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'operator' CHECK (role IN ('operator', 'admin')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.user_consents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  document_version TEXT NOT NULL CHECK (char_length(document_version) BETWEEN 1 AND 100),
  source TEXT NOT NULL CHECK (source IN ('web', 'operator')),
  accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, document_version)
);

CREATE TABLE IF NOT EXISTS app.notification_preferences (
  owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  pre_market_enabled BOOLEAN NOT NULL DEFAULT true,
  intraday_enabled BOOLEAN NOT NULL DEFAULT true,
  post_market_enabled BOOLEAN NOT NULL DEFAULT true,
  operational_enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.agent_connections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  provider TEXT NOT NULL CHECK (provider = 'claude'),
  credential_type TEXT NOT NULL CHECK (credential_type = 'claude_routine_v1'),
  inbound_token_digest BYTEA,
  outbound_trigger_secret_id UUID,
  capabilities JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(capabilities) = 'object'),
  contract_version INT NOT NULL DEFAULT 2 CHECK (contract_version > 0),
  status TEXT NOT NULL DEFAULT 'disabled'
    CHECK (status IN ('disabled', 'testing', 'ready', 'active', 'revoked')),
  last_handshake_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id)
);

CREATE TABLE IF NOT EXISTS app.analysis_schedules (
  owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  primary_connection_id UUID,
  timezone TEXT NOT NULL DEFAULT 'America/Chicago'
    CHECK (char_length(timezone) BETWEEN 1 AND 100),
  pre_market_enabled BOOLEAN NOT NULL DEFAULT true,
  intraday_enabled BOOLEAN NOT NULL DEFAULT true,
  post_market_enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT analysis_schedules_owner_connection_fkey
    FOREIGN KEY (owner_id, primary_connection_id)
    REFERENCES app.agent_connections(owner_id, id)
    ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS app.telegram_links (
  owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  telegram_chat_id BIGINT NOT NULL UNIQUE,
  telegram_user_id BIGINT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ,
  CHECK (
    (status = 'active' AND revoked_at IS NULL)
    OR (status = 'revoked' AND revoked_at IS NOT NULL)
  )
);

ALTER TABLE app.profiles OWNER TO stock_agent_migration_owner;
ALTER TABLE app.app_admins OWNER TO stock_agent_migration_owner;
ALTER TABLE app.user_consents OWNER TO stock_agent_migration_owner;
ALTER TABLE app.notification_preferences OWNER TO stock_agent_migration_owner;
ALTER TABLE app.agent_connections OWNER TO stock_agent_migration_owner;
ALTER TABLE app.analysis_schedules OWNER TO stock_agent_migration_owner;
ALTER TABLE app.telegram_links OWNER TO stock_agent_migration_owner;

DO $$
DECLARE
  target_table TEXT;
  key_column TEXT;
  constraint_name TEXT;
BEGIN
  FOR target_table, key_column IN
    SELECT * FROM (VALUES
      ('holdings', 'ticker'),
      ('analysis_runs', 'id'),
      ('transactions', 'id'),
      ('portfolio_commands', 'id'),
      ('telegram_updates', 'telegram_update_id'),
      ('suggestions', 'id'),
      ('suggestion_grades', 'id'),
      ('stock_observations', 'id'),
      ('daily_snapshots', 'id'),
      ('dry_powder', 'month'),
      ('radar', 'ticker'),
      ('lessons', 'id'),
      ('paper_watches', 'id'),
      ('market_gateway_requests', 'request_id'),
      ('decision_evaluations', 'id'),
      ('market_publications', 'id'),
      ('owner_investment_plans', 'id')
    ) AS owner_tables(table_name, primary_key_column)
  LOOP
    EXECUTE format(
      'ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS owner_id UUID',
      target_table
    );

    constraint_name := target_table || '_owner_id_fkey';
    IF NOT EXISTS (
      SELECT 1
      FROM pg_constraint
      WHERE conrelid = format('public.%I', target_table)::regclass
        AND conname = constraint_name
    ) THEN
      EXECUTE format(
        'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (owner_id) REFERENCES auth.users(id) ON DELETE RESTRICT NOT VALID',
        target_table,
        constraint_name
      );
    END IF;

    constraint_name := target_table || '_owner_' || key_column || '_key';
    IF NOT EXISTS (
      SELECT 1
      FROM pg_constraint
      WHERE conrelid = format('public.%I', target_table)::regclass
        AND conname = constraint_name
    ) THEN
      EXECUTE format(
        'ALTER TABLE public.%I ADD CONSTRAINT %I UNIQUE (owner_id, %I)',
        target_table,
        constraint_name,
        key_column
      );
    END IF;
  END LOOP;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA app FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA app FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS app.single_owner_migration_receipts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE RESTRICT,
  owner_id_hash TEXT NOT NULL UNIQUE CHECK (owner_id_hash ~ '^[0-9a-f]{64}$'),
  before_counts JSONB NOT NULL CHECK (jsonb_typeof(before_counts) = 'object'),
  after_counts JSONB NOT NULL CHECK (jsonb_typeof(after_counts) = 'object'),
  row_digest TEXT NOT NULL CHECK (row_digest ~ '^[0-9a-f]{64}$'),
  relationship_digest TEXT NOT NULL CHECK (relationship_digest ~ '^[0-9a-f]{64}$'),
  passed BOOLEAN NOT NULL CHECK (passed),
  completed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE app.single_owner_migration_receipts OWNER TO stock_agent_migration_owner;
REVOKE ALL ON app.single_owner_migration_receipts FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION app.reject_owner_id_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
    RAISE EXCEPTION 'owner_id is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$$;
ALTER FUNCTION app.reject_owner_id_mutation() OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.reject_owner_id_mutation() FROM PUBLIC, anon, authenticated, service_role;
DROP TRIGGER IF EXISTS reject_owner_id_mutation ON app.single_owner_migration_receipts;
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.single_owner_migration_receipts
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

CREATE OR REPLACE FUNCTION machine.single_owner_table_counts(p_schema NAME)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  target_table TEXT;
  row_count BIGINT;
  result JSONB := '{}'::jsonb;
BEGIN
  IF p_schema::text NOT IN ('public', 'app') THEN
    RAISE EXCEPTION 'unsupported migration schema';
  END IF;
  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format('SELECT count(*) FROM %I.%I', p_schema, target_table) INTO row_count;
    result := result || jsonb_build_object(target_table, row_count);
  END LOOP;
  RETURN result;
END
$$;

CREATE OR REPLACE FUNCTION machine.single_owner_row_digest(p_schema NAME)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  target_table TEXT;
  table_digest TEXT;
  digest_material TEXT := '';
BEGIN
  IF p_schema::text NOT IN ('public', 'app') THEN
    RAISE EXCEPTION 'unsupported migration schema';
  END IF;
  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format(
      'SELECT encode(extensions.digest(coalesce(jsonb_agg(to_jsonb(r) - ''owner_id'' ORDER BY (to_jsonb(r) - ''owner_id'')::text)::text, ''[]''), ''sha256''), ''hex'') FROM %I.%I AS r',
      p_schema,
      target_table
    ) INTO table_digest;
    digest_material := digest_material || target_table || ':' || table_digest || E'\n';
  END LOOP;
  RETURN encode(extensions.digest(digest_material, 'sha256'), 'hex');
END
$$;

CREATE OR REPLACE FUNCTION machine.single_owner_relationship_digest(p_schema NAME)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  target_table TEXT;
  relationship_columns TEXT;
  table_digest TEXT;
  digest_material TEXT := '';
BEGIN
  IF p_schema::text NOT IN ('public', 'app') THEN
    RAISE EXCEPTION 'unsupported migration schema';
  END IF;
  FOR target_table, relationship_columns IN
    SELECT * FROM (VALUES
      ('analysis_runs', 'id, gateway_request_id'),
      ('suggestions', 'id, run_id, evaluation_id'),
      ('suggestion_grades', 'id, suggestion_id'),
      ('stock_observations', 'id, run_id'),
      ('daily_snapshots', 'id, run_id'),
      ('radar', 'ticker, updated_run_id'),
      ('lessons', 'id, run_id'),
      ('paper_watches', 'id, opened_run_id, closed_run_id'),
      ('market_gateway_requests', 'request_id, run_id'),
      ('decision_evaluations', 'id, request_id, run_id'),
      ('market_publications', 'id, idempotency_key, run_id')
    ) AS relationships(table_name, column_list)
  LOOP
    EXECUTE format(
      'SELECT encode(extensions.digest(coalesce(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text)::text, ''[]''), ''sha256''), ''hex'') FROM (SELECT %s FROM %I.%I) AS r',
      relationship_columns,
      p_schema,
      target_table
    ) INTO table_digest;
    digest_material := digest_material || target_table || ':' || table_digest || E'\n';
  END LOOP;
  RETURN encode(extensions.digest(digest_material, 'sha256'), 'hex');
END
$$;

CREATE OR REPLACE FUNCTION machine.backfill_single_owner_to_tenant(p_owner_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  target_table TEXT;
  unknown_rows BIGINT;
  duplicate_rows BIGINT;
  before_counts JSONB;
  after_counts JSONB;
  before_row_digest TEXT;
  after_row_digest TEXT;
  before_relationship_digest TEXT;
  after_relationship_digest TEXT;
  receipt JSONB;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('stock-agent-single-owner-backfill', 0));

  IF p_owner_id IS NULL OR NOT EXISTS (SELECT 1 FROM auth.users WHERE id = p_owner_id) THEN
    RAISE EXCEPTION 'owner must be exactly one existing Auth user';
  END IF;

  SELECT to_jsonb(r) - 'id' - 'owner_id'
  INTO receipt
  FROM app.single_owner_migration_receipts AS r
  WHERE r.owner_id = p_owner_id AND r.passed;
  IF receipt IS NOT NULL THEN
    RETURN receipt;
  END IF;

  IF to_regclass('public.holdings') IS NULL OR to_regclass('app.holdings') IS NOT NULL THEN
    RAISE EXCEPTION 'legacy schema is not in a clean pre-migration state';
  END IF;

  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format(
      'SELECT count(*) FROM public.%I WHERE owner_id IS NOT NULL AND owner_id <> $1',
      target_table
    ) INTO unknown_rows USING p_owner_id;
    IF unknown_rows > 0 THEN
      RAISE EXCEPTION 'unknown owner rows in %', target_table;
    END IF;
  END LOOP;

  IF EXISTS (
    SELECT 1 FROM public.holdings
    WHERE bucket IS NOT NULL AND bucket NOT IN ('core', 'growth', 'speculative', 'unclassified')
  ) OR EXISTS (
    SELECT 1 FROM public.radar
    WHERE bucket_guess IS NOT NULL AND bucket_guess NOT IN ('core', 'growth', 'speculative', 'unclassified')
  ) OR EXISTS (
    SELECT 1 FROM public.suggestions
    WHERE action NOT IN ('buy', 'add', 'hold', 'reduce', 'sell', 'watch', 'avoid')
       OR (bucket IS NOT NULL AND bucket NOT IN ('core', 'growth', 'speculative', 'unclassified'))
       OR (confidence IS NOT NULL AND confidence NOT IN ('low', 'medium', 'high'))
  ) OR EXISTS (
    SELECT 1 FROM public.portfolio_commands
    WHERE bucket IS NOT NULL AND bucket NOT IN ('core', 'growth', 'speculative')
  ) OR EXISTS (
    SELECT 1 FROM public.owner_investment_plans WHERE bucket <> 'core'
  ) THEN
    RAISE EXCEPTION 'unsupported legacy label';
  END IF;

  IF EXISTS (
    SELECT 1 FROM public.suggestion_grades AS c
    LEFT JOIN public.suggestions AS p ON p.id = c.suggestion_id
    WHERE c.suggestion_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.suggestions AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.run_id
    WHERE c.run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.suggestions AS c
    LEFT JOIN public.decision_evaluations AS p ON p.id = c.evaluation_id
    WHERE c.evaluation_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.stock_observations AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.run_id
    WHERE c.run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.daily_snapshots AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.run_id
    WHERE c.run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.lessons AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.run_id
    WHERE c.run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.radar AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.updated_run_id
    WHERE c.updated_run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.paper_watches AS c
    LEFT JOIN public.analysis_runs AS opened ON opened.id = c.opened_run_id
    LEFT JOIN public.analysis_runs AS closed ON closed.id = c.closed_run_id
    WHERE (c.opened_run_id IS NOT NULL AND opened.id IS NULL)
       OR (c.closed_run_id IS NOT NULL AND closed.id IS NULL)
  ) OR EXISTS (
    SELECT 1 FROM public.market_gateway_requests AS c
    LEFT JOIN public.analysis_runs AS p ON p.id = c.run_id
    WHERE c.run_id IS NOT NULL AND p.id IS NULL
  ) OR EXISTS (
    SELECT 1 FROM public.decision_evaluations AS c
    LEFT JOIN public.market_gateway_requests AS request ON request.request_id = c.request_id
    LEFT JOIN public.analysis_runs AS run ON run.id = c.run_id
    WHERE (c.request_id IS NOT NULL AND request.request_id IS NULL)
       OR (c.run_id IS NOT NULL AND run.id IS NULL)
  ) OR EXISTS (
    SELECT 1 FROM public.market_publications AS c
    LEFT JOIN public.market_gateway_requests AS request ON request.request_id = c.idempotency_key
    LEFT JOIN public.analysis_runs AS run ON run.id = c.run_id
    WHERE request.request_id IS NULL OR (c.run_id IS NOT NULL AND run.id IS NULL)
  ) OR EXISTS (
    SELECT 1 FROM public.analysis_runs AS c
    LEFT JOIN public.market_gateway_requests AS p ON p.request_id = c.gateway_request_id
    WHERE c.gateway_request_id IS NOT NULL AND p.request_id IS NULL
  ) THEN
    RAISE EXCEPTION 'orphaned relationship';
  END IF;

  FOR target_table, duplicate_rows IN
    SELECT 'holdings', count(*) FROM (
      SELECT ticker FROM public.holdings GROUP BY ticker HAVING count(*) > 1
    ) AS duplicates
    UNION ALL
    SELECT 'dry_powder', count(*) FROM (
      SELECT month FROM public.dry_powder GROUP BY month HAVING count(*) > 1
    ) AS duplicates
    UNION ALL
    SELECT 'radar', count(*) FROM (
      SELECT ticker FROM public.radar GROUP BY ticker HAVING count(*) > 1
    ) AS duplicates
    UNION ALL
    SELECT 'daily_snapshots', count(*) FROM (
      SELECT snap_date, ticker FROM public.daily_snapshots
      GROUP BY snap_date, ticker HAVING count(*) > 1
    ) AS duplicates
    UNION ALL
    SELECT 'owner_investment_plans', count(*) FROM (
      SELECT ticker FROM public.owner_investment_plans GROUP BY ticker HAVING count(*) > 1
    ) AS duplicates
  LOOP
    IF duplicate_rows > 0 THEN
      RAISE EXCEPTION 'duplicate owner key in %', target_table;
    END IF;
  END LOOP;

  before_counts := machine.single_owner_table_counts('public');
  before_row_digest := machine.single_owner_row_digest('public');
  before_relationship_digest := machine.single_owner_relationship_digest('public');

  INSERT INTO app.profiles (id, status)
  VALUES (p_owner_id, 'active')
  ON CONFLICT (id) DO NOTHING;
  INSERT INTO app.app_admins (user_id, role)
  VALUES (p_owner_id, 'operator')
  ON CONFLICT (user_id) DO NOTHING;
  INSERT INTO app.notification_preferences (owner_id)
  VALUES (p_owner_id)
  ON CONFLICT (owner_id) DO NOTHING;

  -- The legacy append-only trigger intentionally rejects every update. Disable only this
  -- reviewed maintenance trigger inside the migration transaction, then restore it below.
  DROP TRIGGER decision_evaluations_append_only ON public.decision_evaluations;

  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format('UPDATE public.%I SET owner_id = $1 WHERE owner_id IS NULL', target_table)
      USING p_owner_id;
    EXECUTE format('ALTER TABLE public.%I ALTER COLUMN owner_id SET NOT NULL', target_table);
  END LOOP;

  ALTER TABLE public.holdings DROP CONSTRAINT holdings_owner_ticker_key;
  ALTER TABLE public.holdings DROP CONSTRAINT holdings_pkey;
  ALTER TABLE public.holdings ADD CONSTRAINT holdings_pkey PRIMARY KEY (owner_id, ticker);

  ALTER TABLE public.dry_powder DROP CONSTRAINT dry_powder_owner_month_key;
  ALTER TABLE public.dry_powder DROP CONSTRAINT dry_powder_pkey;
  ALTER TABLE public.dry_powder ADD CONSTRAINT dry_powder_pkey PRIMARY KEY (owner_id, month);

  ALTER TABLE public.radar DROP CONSTRAINT radar_owner_ticker_key;
  ALTER TABLE public.radar DROP CONSTRAINT radar_pkey;
  ALTER TABLE public.radar ADD CONSTRAINT radar_pkey PRIMARY KEY (owner_id, ticker);

  ALTER TABLE public.daily_snapshots DROP CONSTRAINT daily_snapshots_snap_date_ticker_key;
  ALTER TABLE public.daily_snapshots ADD CONSTRAINT daily_snapshots_owner_date_ticker_key
    UNIQUE (owner_id, snap_date, ticker);

  ALTER TABLE public.owner_investment_plans DROP CONSTRAINT owner_investment_plans_ticker_key;
  ALTER TABLE public.owner_investment_plans ADD CONSTRAINT owner_investment_plans_owner_ticker_key
    UNIQUE (owner_id, ticker);

  DROP INDEX public.one_market_publication_per_run;
  CREATE UNIQUE INDEX one_market_publication_per_run
    ON public.market_publications (owner_id, run_id) WHERE run_id IS NOT NULL;
  DROP INDEX public.one_holiday_publication_per_market_date;
  CREATE UNIQUE INDEX one_holiday_publication_per_market_date
    ON public.market_publications (owner_id, market_date, phase, kind) WHERE kind = 'holiday';

  ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (run_id);
  ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_owner_evaluation_fkey
    FOREIGN KEY (owner_id, evaluation_id) REFERENCES public.decision_evaluations(owner_id, id) ON DELETE RESTRICT;
  ALTER TABLE public.suggestion_grades ADD CONSTRAINT suggestion_grades_owner_suggestion_fkey
    FOREIGN KEY (owner_id, suggestion_id) REFERENCES public.suggestions(owner_id, id) ON DELETE RESTRICT;
  ALTER TABLE public.stock_observations ADD CONSTRAINT stock_observations_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (run_id);
  ALTER TABLE public.daily_snapshots ADD CONSTRAINT daily_snapshots_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (run_id);
  ALTER TABLE public.lessons ADD CONSTRAINT lessons_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (run_id);
  ALTER TABLE public.radar ADD CONSTRAINT radar_owner_run_fkey
    FOREIGN KEY (owner_id, updated_run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (updated_run_id);
  ALTER TABLE public.paper_watches ADD CONSTRAINT paper_watches_owner_opened_run_fkey
    FOREIGN KEY (owner_id, opened_run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (opened_run_id);
  ALTER TABLE public.paper_watches ADD CONSTRAINT paper_watches_owner_closed_run_fkey
    FOREIGN KEY (owner_id, closed_run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (closed_run_id);
  ALTER TABLE public.market_gateway_requests ADD CONSTRAINT market_gateway_requests_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id)
    ON DELETE SET NULL (run_id);
  ALTER TABLE public.decision_evaluations ADD CONSTRAINT decision_evaluations_owner_request_fkey
    FOREIGN KEY (owner_id, request_id) REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;
  ALTER TABLE public.decision_evaluations ADD CONSTRAINT decision_evaluations_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id) ON DELETE RESTRICT;
  ALTER TABLE public.market_publications ADD CONSTRAINT market_publications_owner_request_fkey
    FOREIGN KEY (owner_id, idempotency_key) REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;
  ALTER TABLE public.market_publications ADD CONSTRAINT market_publications_owner_run_fkey
    FOREIGN KEY (owner_id, run_id) REFERENCES public.analysis_runs(owner_id, id) ON DELETE RESTRICT;
  ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_owner_gateway_request_fkey
    FOREIGN KEY (owner_id, gateway_request_id) REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;

  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format('ALTER TABLE public.%I SET SCHEMA app', target_table);
    EXECUTE format('ALTER TABLE app.%I OWNER TO stock_agent_migration_owner', target_table);
    EXECUTE format(
      'CREATE TRIGGER reject_owner_id_mutation BEFORE UPDATE OF owner_id ON app.%I FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation()',
      target_table
    );
  END LOOP;

  CREATE TRIGGER decision_evaluations_append_only
    BEFORE UPDATE OR DELETE ON app.decision_evaluations
    FOR EACH ROW EXECUTE FUNCTION public.reject_decision_evaluation_mutation();

  after_counts := machine.single_owner_table_counts('app');
  after_row_digest := machine.single_owner_row_digest('app');
  after_relationship_digest := machine.single_owner_relationship_digest('app');

  IF before_counts IS DISTINCT FROM after_counts THEN
    RAISE EXCEPTION 'row count changed during owner migration';
  END IF;
  IF before_row_digest IS DISTINCT FROM after_row_digest THEN
    RAISE EXCEPTION 'row digest changed during owner migration';
  END IF;
  IF before_relationship_digest IS DISTINCT FROM after_relationship_digest THEN
    RAISE EXCEPTION 'relationship digest changed during owner migration';
  END IF;

  INSERT INTO app.single_owner_migration_receipts (
    owner_id, owner_id_hash, before_counts, after_counts, row_digest,
    relationship_digest, passed
  ) VALUES (
    p_owner_id,
    encode(extensions.digest(p_owner_id::text, 'sha256'), 'hex'),
    before_counts,
    after_counts,
    after_row_digest,
    after_relationship_digest,
    true
  )
  RETURNING to_jsonb(single_owner_migration_receipts) - 'id' - 'owner_id' INTO receipt;

  RETURN receipt;
END
$$;

REVOKE ALL ON FUNCTION machine.single_owner_table_counts(NAME) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION machine.single_owner_row_digest(NAME) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION machine.single_owner_relationship_digest(NAME) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION machine.backfill_single_owner_to_tenant(UUID) FROM PUBLIC, anon, authenticated, service_role;
