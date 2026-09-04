-- Redacted owner-dashboard reads for immutable intelligence and report ledgers.
-- This migration grants direct column SELECT only; no application RPC is executable.

REVOKE ALL PRIVILEGES ON TABLE
  public.market_intelligence_runs,
  public.market_intelligence_run_events,
  public.market_source_quota_reservations,
  public.market_source_receipts,
  public.market_source_items,
  public.market_intelligence_run_items,
  public.market_events,
  public.market_event_relationships,
  public.market_candidate_rankings,
  public.market_evidence_packets,
  public.market_reports,
  public.market_learning_observations
FROM stock_agent_dashboard;

GRANT SELECT (id, phase, market_date, policy_version, created_at)
  ON public.market_intelligence_runs TO stock_agent_dashboard;
GRANT SELECT (run_id, status)
  ON public.market_intelligence_run_events TO stock_agent_dashboard;
GRANT SELECT (run_id, provider, status, retrieved_at, accepted_count, dropped_count)
  ON public.market_source_receipts TO stock_agent_dashboard;
GRANT SELECT (id, canonical_url, title)
  ON public.market_source_items TO stock_agent_dashboard;
GRANT SELECT (id, run_id, event_type, title, summary, occurred_at, effective_at, materiality,
              confidence, evidence_item_ids)
  ON public.market_events TO stock_agent_dashboard;
GRANT SELECT (id, run_id, source_key, target_kind, target_key, relationship_type, evidence_item_ids)
  ON public.market_event_relationships TO stock_agent_dashboard;
GRANT SELECT (id, run_id, event_id, candidate_key, ticker, rank, total_score, qualified,
              veto_reasons, exposure_item_ids)
  ON public.market_candidate_rankings TO stock_agent_dashboard;
GRANT SELECT (id, run_id, market_date, kind, report, report_hash, created_at)
  ON public.market_reports TO stock_agent_dashboard;

DROP POLICY IF EXISTS owner_dashboard_select_intelligence_runs ON public.market_intelligence_runs;
CREATE POLICY owner_dashboard_select_intelligence_runs ON public.market_intelligence_runs
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_intelligence_run_events ON public.market_intelligence_run_events;
CREATE POLICY owner_dashboard_select_intelligence_run_events ON public.market_intelligence_run_events
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_source_receipts ON public.market_source_receipts;
CREATE POLICY owner_dashboard_select_source_receipts ON public.market_source_receipts
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_source_items ON public.market_source_items;
CREATE POLICY owner_dashboard_select_source_items ON public.market_source_items
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_market_events ON public.market_events;
CREATE POLICY owner_dashboard_select_market_events ON public.market_events
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_event_relationships ON public.market_event_relationships;
CREATE POLICY owner_dashboard_select_event_relationships ON public.market_event_relationships
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_candidate_rankings ON public.market_candidate_rankings;
CREATE POLICY owner_dashboard_select_candidate_rankings ON public.market_candidate_rankings
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_reports ON public.market_reports;
CREATE POLICY owner_dashboard_select_reports ON public.market_reports
  FOR SELECT TO stock_agent_dashboard USING (true);
