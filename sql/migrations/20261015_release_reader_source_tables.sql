BEGIN;

-- Repair the complete protected evidence-reader scope for tables created
-- before and after the dedicated reader. This keeps pre/post release snapshots
-- complete without granting direct privileges to the login role.
DO $$
DECLARE name TEXT;
BEGIN
  FOREACH name IN ARRAY ARRAY[
    'market_source_items',
    'market_intelligence_run_items',
    'market_source_item_provenance',
    'market_run_source_item_provenance',
    'market_enrichment_selection_manifests',
    'market_enrichment_request_descriptors'
  ] LOOP
    IF to_regclass(format('public.%I', name)) IS NULL THEN
      RAISE EXCEPTION 'release evidence relation missing: %', name USING ERRCODE = '42P01';
    END IF;
    EXECUTE format(
      'REVOKE ALL ON public.%I FROM stock_agent_release_reader,stock_agent_release_reader_runtime',
      name
    );
    EXECUTE format('GRANT SELECT ON public.%I TO stock_agent_release_reader', name);
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', name);
    EXECUTE format('DROP POLICY IF EXISTS release_evidence_select ON public.%I', name);
    EXECUTE format(
      'CREATE POLICY release_evidence_select ON public.%I FOR SELECT TO stock_agent_release_reader USING (true)',
      name
    );
  END LOOP;
END;
$$;

COMMIT;
