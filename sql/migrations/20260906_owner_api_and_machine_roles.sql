-- Forced row isolation and the release-one browser API allow-list.
-- Apply only after the single-owner backfill has moved owner tables into app.

REVOKE CREATE ON SCHEMA public FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SCHEMA app, machine FROM PUBLIC, anon, authenticated, service_role;
REVOKE CREATE ON SCHEMA api FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA api, app TO authenticated;
GRANT USAGE ON SCHEMA api TO anon;

CREATE TABLE IF NOT EXISTS app.owner_policy_overrides (
  owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  policy_version INT NOT NULL DEFAULT 1 CHECK (policy_version > 0),
  max_single_position_pct NUMERIC(5, 2) NOT NULL DEFAULT 20
    CHECK (max_single_position_pct > 0 AND max_single_position_pct <= 20),
  max_speculative_position_pct NUMERIC(5, 2) NOT NULL DEFAULT 5
    CHECK (max_speculative_position_pct > 0 AND max_speculative_position_pct <= 5),
  max_portfolio_drawdown_pct NUMERIC(5, 2) NOT NULL DEFAULT 15
    CHECK (max_portfolio_drawdown_pct > 0 AND max_portfolio_drawdown_pct <= 15),
  min_reward_risk NUMERIC(5, 2) NOT NULL DEFAULT 2
    CHECK (min_reward_risk >= 2 AND min_reward_risk <= 100),
  max_live_quote_age_seconds INT NOT NULL DEFAULT 900
    CHECK (max_live_quote_age_seconds > 0 AND max_live_quote_age_seconds <= 900),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE app.owner_policy_overrides OWNER TO stock_agent_migration_owner;
DROP TRIGGER IF EXISTS reject_owner_id_mutation ON app.owner_policy_overrides;
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.owner_policy_overrides
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

DO $$
DECLARE
  target_table TEXT;
  existing_policy RECORD;
BEGIN
  FOREACH target_table IN ARRAY ARRAY[
    'profiles', 'app_admins', 'user_consents', 'notification_preferences',
    'analysis_schedules', 'agent_connections', 'telegram_links',
    'single_owner_migration_receipts', 'owner_policy_overrides',
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', target_table);
    EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY', target_table);
    FOR existing_policy IN
      SELECT policyname
      FROM pg_policies
      WHERE schemaname = 'app' AND tablename = target_table
    LOOP
      EXECUTE format(
        'DROP POLICY %I ON app.%I', existing_policy.policyname, target_table
      );
    END LOOP;
  END LOOP;
END
$$;

CREATE POLICY profiles_owner_select ON app.profiles
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND id = (SELECT auth.uid()));
CREATE POLICY profiles_owner_update ON app.profiles
  FOR UPDATE TO authenticated
  USING (auth.uid() IS NOT NULL AND id = (SELECT auth.uid()))
  WITH CHECK (auth.uid() IS NOT NULL AND id = (SELECT auth.uid()));

CREATE POLICY user_consents_owner_select ON app.user_consents
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY user_consents_owner_insert ON app.user_consents
  FOR INSERT TO authenticated
  WITH CHECK (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

CREATE POLICY notification_preferences_owner_select ON app.notification_preferences
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY notification_preferences_owner_update ON app.notification_preferences
  FOR UPDATE TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()))
  WITH CHECK (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

CREATE POLICY analysis_schedules_owner_select ON app.analysis_schedules
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY agent_connections_owner_select ON app.agent_connections
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY telegram_links_owner_select ON app.telegram_links
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

CREATE POLICY holdings_owner_select ON app.holdings
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY analysis_runs_owner_select ON app.analysis_runs
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY transactions_owner_select ON app.transactions
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY suggestions_owner_select ON app.suggestions
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY owner_investment_plans_owner_select ON app.owner_investment_plans
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

REVOKE ALL ON ALL TABLES IN SCHEMA app FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA app FROM PUBLIC, anon, authenticated, service_role;

GRANT SELECT (id, display_name, timezone, status, onboarding_completed_at, created_at, updated_at)
  ON app.profiles TO authenticated;
GRANT UPDATE (display_name, timezone)
  ON app.profiles TO authenticated;
GRANT SELECT (owner_id, document_version, accepted_at)
  ON app.user_consents TO authenticated;
GRANT SELECT (owner_id, pre_market_enabled, intraday_enabled, post_market_enabled,
              operational_enabled, created_at, updated_at)
  ON app.notification_preferences TO authenticated;
GRANT UPDATE (pre_market_enabled, intraday_enabled, post_market_enabled, operational_enabled)
  ON app.notification_preferences TO authenticated;
GRANT SELECT (owner_id, primary_connection_id, timezone, pre_market_enabled,
              intraday_enabled, post_market_enabled, created_at, updated_at)
  ON app.analysis_schedules TO authenticated;
GRANT SELECT (owner_id, id, public_id, provider, credential_type, capabilities,
              contract_version, status, last_handshake_at, created_at, updated_at)
  ON app.agent_connections TO authenticated;
GRANT SELECT (owner_id, status, linked_at, revoked_at)
  ON app.telegram_links TO authenticated;
GRANT SELECT (owner_id, ticker, shares, avg_cost, bucket, opened_at, stop, target,
              high_water_price, hold_override_until)
  ON app.holdings TO authenticated;
GRANT SELECT (owner_id, id, ts, ticker, side, qty, price, source, executed_on)
  ON app.transactions TO authenticated;
GRANT SELECT (owner_id, id, ticker, bucket, amount, cadence, next_due_on, due_day,
              active, created_at, updated_at)
  ON app.owner_investment_plans TO authenticated;
GRANT SELECT (owner_id, id, ts, date, ticker, action, bucket, depth, entry_zone_low,
              entry_zone_high, valid_until, stop, target, confidence, decisive_factor,
              risk_verdict, invalidation_level, reason, score, risk_band,
              price_at_suggestion, evidence_as_of, decision_mode)
  ON app.suggestions TO authenticated;
GRANT SELECT (owner_id, id, kind, started_at, finished_at, status, data_as_of,
              source_status, symbols, write_counts, summary)
  ON app.analysis_runs TO authenticated;

DROP VIEW IF EXISTS api.profile;
CREATE VIEW api.profile WITH (security_invoker = true) AS
SELECT id, display_name, timezone, status, onboarding_completed_at, created_at, updated_at
FROM app.profiles;

DROP VIEW IF EXISTS api.consents;
CREATE VIEW api.consents WITH (security_invoker = true) AS
SELECT document_version, accepted_at
FROM app.user_consents;

DROP VIEW IF EXISTS api.today;
CREATE VIEW api.today WITH (security_invoker = true) AS
SELECT id AS run_id,
       kind,
       started_at,
       finished_at,
       status,
       data_as_of,
       source_status,
       symbols,
       write_counts,
       summary
FROM app.analysis_runs
WHERE (started_at AT TIME ZONE 'America/Chicago')::date =
      (now() AT TIME ZONE 'America/Chicago')::date;

DROP VIEW IF EXISTS api.holdings;
CREATE VIEW api.holdings WITH (security_invoker = true) AS
SELECT ticker, shares, avg_cost, bucket, opened_at, stop, target,
       high_water_price, hold_override_until
FROM app.holdings;

DROP VIEW IF EXISTS api.transactions;
CREATE VIEW api.transactions WITH (security_invoker = true) AS
SELECT id, ts, ticker, side, qty, price, source, executed_on
FROM app.transactions;

DROP VIEW IF EXISTS api.plans;
CREATE VIEW api.plans WITH (security_invoker = true) AS
SELECT id, ticker, bucket, amount, cadence, next_due_on, due_day,
       active, created_at, updated_at
FROM app.owner_investment_plans;

DROP VIEW IF EXISTS api.recommendations;
CREATE VIEW api.recommendations WITH (security_invoker = true) AS
SELECT id, ts, date, ticker, action, bucket, depth, entry_zone_low,
       entry_zone_high, valid_until, stop, target, confidence, decisive_factor,
       risk_verdict, invalidation_level, reason, score, risk_band,
       price_at_suggestion, evidence_as_of, decision_mode
FROM app.suggestions;

DROP VIEW IF EXISTS api.runs;
CREATE VIEW api.runs WITH (security_invoker = true) AS
SELECT id, kind, started_at, finished_at, status, data_as_of,
       source_status, symbols, write_counts, summary
FROM app.analysis_runs;

DROP VIEW IF EXISTS api.connections;
CREATE VIEW api.connections WITH (security_invoker = true) AS
SELECT id, public_id, provider, credential_type, capabilities,
       contract_version, status, last_handshake_at, created_at, updated_at
FROM app.agent_connections;

DROP VIEW IF EXISTS api.telegram_status;
CREATE VIEW api.telegram_status WITH (security_invoker = true) AS
SELECT status, linked_at, revoked_at
FROM app.telegram_links;

DROP VIEW IF EXISTS api.settings;
CREATE VIEW api.settings WITH (security_invoker = true) AS
SELECT p.display_name,
       p.timezone,
       n.pre_market_enabled AS notify_pre_market,
       n.intraday_enabled AS notify_intraday,
       n.post_market_enabled AS notify_post_market,
       n.operational_enabled AS notify_operational,
       s.primary_connection_id,
       s.timezone AS schedule_timezone,
       s.pre_market_enabled AS schedule_pre_market,
       s.intraday_enabled AS schedule_intraday,
       s.post_market_enabled AS schedule_post_market
FROM app.profiles AS p
LEFT JOIN app.notification_preferences AS n ON n.owner_id = p.id
LEFT JOIN app.analysis_schedules AS s ON s.owner_id = p.id;

ALTER VIEW api.profile OWNER TO stock_agent_migration_owner;
ALTER VIEW api.consents OWNER TO stock_agent_migration_owner;
ALTER VIEW api.today OWNER TO stock_agent_migration_owner;
ALTER VIEW api.holdings OWNER TO stock_agent_migration_owner;
ALTER VIEW api.transactions OWNER TO stock_agent_migration_owner;
ALTER VIEW api.plans OWNER TO stock_agent_migration_owner;
ALTER VIEW api.recommendations OWNER TO stock_agent_migration_owner;
ALTER VIEW api.runs OWNER TO stock_agent_migration_owner;
ALTER VIEW api.connections OWNER TO stock_agent_migration_owner;
ALTER VIEW api.telegram_status OWNER TO stock_agent_migration_owner;
ALTER VIEW api.settings OWNER TO stock_agent_migration_owner;

REVOKE ALL ON ALL TABLES IN SCHEMA api FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.profile, api.consents, api.today, api.holdings, api.transactions, api.plans,
  api.recommendations, api.runs, api.connections, api.telegram_status, api.settings
  TO authenticated;

DO $$
DECLARE
  exposed_function REGPROCEDURE;
BEGIN
  FOR exposed_function IN
    SELECT p.oid::regprocedure
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
  LOOP
    EXECUTE format(
      'REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated',
      exposed_function
    );
  END LOOP;
END
$$;

ALTER DEFAULT PRIVILEGES IN SCHEMA app
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA api
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE stock_agent_migration_owner IN SCHEMA app
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE stock_agent_migration_owner IN SCHEMA api
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;

DO $$
DECLARE
  role_name TEXT;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'stock_agent_gateway',
    'stock_agent_scheduler',
    'stock_agent_telegram',
    'stock_agent_backup'
  ]
  LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS', role_name);
    END IF;
    EXECUTE format('REVOKE ALL ON SCHEMA public, app, api, auth FROM %I', role_name);
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'vault') THEN
      EXECUTE format('REVOKE ALL ON SCHEMA vault FROM %I', role_name);
    END IF;
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA app FROM %I', role_name);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA app FROM %I', role_name);
    EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA machine FROM %I', role_name);
    EXECUTE format('GRANT USAGE ON SCHEMA machine TO %I', role_name);
  END LOOP;
END
$$;
