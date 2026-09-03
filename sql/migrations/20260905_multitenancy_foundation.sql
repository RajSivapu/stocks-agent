-- Multi-tenant identity and schema foundation.
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

