-- Fresh-install-only structural transition after 20260905.
-- Supabase CLI migration; existing installations mark this applied after reviewed owner backfill.
-- Every legacy table must be empty. Existing installations use the reviewed owner backfill instead.

DO $$
DECLARE
  target_table TEXT;
  row_count BIGINT;
BEGIN
  IF to_regclass('public.holdings') IS NULL OR to_regclass('app.holdings') IS NOT NULL THEN
    RAISE EXCEPTION 'fresh schema is not at the multitenancy bootstrap boundary';
  END IF;
  FOREACH target_table IN ARRAY ARRAY[
    'holdings', 'analysis_runs', 'transactions', 'portfolio_commands',
    'telegram_updates', 'suggestions', 'suggestion_grades', 'stock_observations',
    'daily_snapshots', 'dry_powder', 'radar', 'lessons', 'paper_watches',
    'market_gateway_requests', 'decision_evaluations', 'market_publications',
    'owner_investment_plans'
  ]
  LOOP
    EXECUTE format('SELECT count(*) FROM public.%I', target_table) INTO row_count;
    IF row_count <> 0 THEN
      RAISE EXCEPTION 'fresh bootstrap refuses non-empty table %', target_table;
    END IF;
    EXECUTE format('ALTER TABLE public.%I ALTER COLUMN owner_id SET NOT NULL', target_table);
  END LOOP;
END
$$;

DROP TRIGGER decision_evaluations_append_only ON public.decision_evaluations;

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
  FOREIGN KEY (owner_id, evaluation_id)
  REFERENCES public.decision_evaluations(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE public.suggestion_grades ADD CONSTRAINT suggestion_grades_owner_suggestion_fkey
  FOREIGN KEY (owner_id, suggestion_id)
  REFERENCES public.suggestions(owner_id, id) ON DELETE RESTRICT;
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
  FOREIGN KEY (owner_id, request_id)
  REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;
ALTER TABLE public.decision_evaluations ADD CONSTRAINT decision_evaluations_owner_run_fkey
  FOREIGN KEY (owner_id, run_id)
  REFERENCES public.analysis_runs(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE public.market_publications ADD CONSTRAINT market_publications_owner_request_fkey
  FOREIGN KEY (owner_id, idempotency_key)
  REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;
ALTER TABLE public.market_publications ADD CONSTRAINT market_publications_owner_run_fkey
  FOREIGN KEY (owner_id, run_id)
  REFERENCES public.analysis_runs(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE public.analysis_runs ADD CONSTRAINT analysis_runs_owner_gateway_request_fkey
  FOREIGN KEY (owner_id, gateway_request_id)
  REFERENCES public.market_gateway_requests(owner_id, request_id) ON DELETE RESTRICT;

DO $$
DECLARE
  target_table TEXT;
BEGIN
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
END
$$;

CREATE TRIGGER decision_evaluations_append_only
  BEFORE UPDATE OR DELETE ON app.decision_evaluations
  FOR EACH ROW EXECUTE FUNCTION public.reject_decision_evaluation_mutation();

DROP FUNCTION machine.backfill_single_owner_to_tenant(UUID);
DROP FUNCTION machine.single_owner_relationship_digest(NAME);
DROP FUNCTION machine.single_owner_row_digest(NAME);
DROP FUNCTION machine.single_owner_table_counts(NAME);
