-- Owner-only dashboard role. This role can read only the columns used by Web v1.
-- It has no write, DDL, application-function, Auth, Telegram-command, or secret access.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_agent_dashboard') THEN
    CREATE ROLE stock_agent_dashboard
      NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END
$$;

ALTER ROLE stock_agent_dashboard
  NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM stock_agent_dashboard;
GRANT USAGE ON SCHEMA public TO stock_agent_dashboard;

REVOKE ALL PRIVILEGES ON TABLE
  public.holdings,
  public.transactions,
  public.owner_investment_plans,
  public.analysis_runs,
  public.market_gateway_requests,
  public.decision_evaluations,
  public.suggestions,
  public.suggestion_grades,
  public.market_publications,
  public.market_policy_config,
  public.market_alert_drafts,
  public.market_alert_rules,
  public.market_alert_rule_versions,
  public.market_alert_events,
  public.market_alert_actions
FROM stock_agent_dashboard;

GRANT SELECT (ticker, shares, avg_cost, bucket, opened_at, stop, target)
  ON public.holdings TO stock_agent_dashboard;
GRANT SELECT (id, ts, ticker, side, qty, price, source, executed_on)
  ON public.transactions TO stock_agent_dashboard;
GRANT SELECT (id, ticker, bucket, amount, cadence, next_due_on, due_day, active, created_at, updated_at)
  ON public.owner_investment_plans TO stock_agent_dashboard;
GRANT SELECT (id, kind, started_at, finished_at, status, data_as_of, source_status, symbols,
              write_counts, telegram_message_ids, summary, gateway_request_id)
  ON public.analysis_runs TO stock_agent_dashboard;
GRANT SELECT (request_id, operation, run_id, status, attempt_count, response, response_digest,
              created_at, claimed_at, finished_at)
  ON public.market_gateway_requests TO stock_agent_dashboard;
GRANT SELECT (id, request_id, run_id, candidate_id, policy_version, raw_action, final_action,
              policy_status, reason_codes, explanations, normalized, evidence, analyst, checker,
              created_at)
  ON public.decision_evaluations TO stock_agent_dashboard;
GRANT SELECT (id, ts, date, ticker, action, bucket, depth, entry_zone_low, entry_zone_high,
              valid_until, stop, target, confidence, bull, bear, decisive_factor, risk_verdict,
              invalidation_level, reason, score, risk_band, price_at_suggestion, run_id,
              evidence_as_of, invalidation_price, evaluation_id, decision_source, decision_mode)
  ON public.suggestions TO stock_agent_dashboard;
GRANT SELECT (id, suggestion_id, graded_at, result, price_then, price_later, horizon_days, note)
  ON public.suggestion_grades TO stock_agent_dashboard;
GRANT SELECT (id, idempotency_key, run_id, market_date, phase, kind, template_version,
              rendered_body, rendered_hash, status, telegram_message_ids, attempt_count,
              sending_started_at, delivered_at, error, created_at, updated_at,
              telegram_accepted_at)
  ON public.market_publications TO stock_agent_dashboard;
GRANT SELECT (version, config, active, created_at, activated_at)
  ON public.market_policy_config TO stock_agent_dashboard;
GRANT SELECT (id, request_id, source_evaluation_id, rule_snapshot, fingerprint, state,
              publication_id, expires_at, created_at, updated_at)
  ON public.market_alert_drafts TO stock_agent_dashboard;
GRANT SELECT (id, source_draft_id, current_version, state, ticker, profile, severity, session,
              confirmation, conditions, cooldown_seconds, fire_limit, trigger_count, valid_until,
              snoozed_until, owner_note, armed_at, last_triggered_at, updated_at)
  ON public.market_alert_rules TO stock_agent_dashboard;
GRANT SELECT (rule_id, version, snapshot, created_at)
  ON public.market_alert_rule_versions TO stock_agent_dashboard;
GRANT SELECT (id, request_id, rule_id, rule_version, fingerprint, status, reason_codes, observed_at,
              evaluated_at, persisted_at, market_session, condition_results, evidence_ids,
              publication_id)
  ON public.market_alert_events TO stock_agent_dashboard;
GRANT SELECT (id, draft_id, rule_id, event_id, publication_id, telegram_update_id, action,
              prior_state, new_state, expected_version, resulting_version, snoozed_until,
              received_at)
  ON public.market_alert_actions TO stock_agent_dashboard;

DROP POLICY IF EXISTS owner_dashboard_select_holdings ON public.holdings;
CREATE POLICY owner_dashboard_select_holdings ON public.holdings
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_transactions ON public.transactions;
CREATE POLICY owner_dashboard_select_transactions ON public.transactions
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_plans ON public.owner_investment_plans;
CREATE POLICY owner_dashboard_select_plans ON public.owner_investment_plans
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_runs ON public.analysis_runs;
CREATE POLICY owner_dashboard_select_runs ON public.analysis_runs
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_gateway_requests ON public.market_gateway_requests;
CREATE POLICY owner_dashboard_select_gateway_requests ON public.market_gateway_requests
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_evaluations ON public.decision_evaluations;
CREATE POLICY owner_dashboard_select_evaluations ON public.decision_evaluations
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_suggestions ON public.suggestions;
CREATE POLICY owner_dashboard_select_suggestions ON public.suggestions
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_grades ON public.suggestion_grades;
CREATE POLICY owner_dashboard_select_grades ON public.suggestion_grades
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_publications ON public.market_publications;
CREATE POLICY owner_dashboard_select_publications ON public.market_publications
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_policy ON public.market_policy_config;
CREATE POLICY owner_dashboard_select_policy ON public.market_policy_config
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_alert_drafts ON public.market_alert_drafts;
CREATE POLICY owner_dashboard_select_alert_drafts ON public.market_alert_drafts
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_alert_rules ON public.market_alert_rules;
CREATE POLICY owner_dashboard_select_alert_rules ON public.market_alert_rules
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_alert_versions ON public.market_alert_rule_versions;
CREATE POLICY owner_dashboard_select_alert_versions ON public.market_alert_rule_versions
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_alert_events ON public.market_alert_events;
CREATE POLICY owner_dashboard_select_alert_events ON public.market_alert_events
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_alert_actions ON public.market_alert_actions;
CREATE POLICY owner_dashboard_select_alert_actions ON public.market_alert_actions
  FOR SELECT TO stock_agent_dashboard USING (true);
