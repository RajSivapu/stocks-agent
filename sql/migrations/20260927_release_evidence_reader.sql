-- Dedicated SELECT-only evidence membership. The runtime starts NOLOGIN; the
-- protected operator may provision its password/login after reviewing the grants.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='stock_agent_release_reader') THEN
    CREATE ROLE stock_agent_release_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='stock_agent_release_reader_runtime') THEN
    CREATE ROLE stock_agent_release_reader_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
END; $$;
GRANT stock_agent_release_reader TO stock_agent_release_reader_runtime;
GRANT USAGE ON SCHEMA public,extensions TO stock_agent_release_reader;
DO $$
DECLARE name TEXT;
BEGIN
  FOREACH name IN ARRAY ARRAY[
    'holdings','transactions','portfolio_commands','analysis_runs','market_evidence_packets','market_reports',
    'market_report_publications','market_intelligence_runs','market_intelligence_collection_completions',
    'market_intelligence_run_events','market_collection_checkpoints','market_events','market_candidate_rankings',
    'market_gateway_requests','market_report_request_origins','market_publications',
    'market_source_quota_reservations','market_source_receipts'
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
