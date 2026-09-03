-- Owner-visible research/run transparency and a rate-limited on-demand trigger request.
-- Supabase CLI migration.

-- Web-requested runs are distinct from canonical scheduled slots and connection handshakes.
ALTER TABLE app.scheduled_run_slots
  DROP CONSTRAINT scheduled_run_slots_purpose_check;
ALTER TABLE app.scheduled_run_slots
  ADD CONSTRAINT scheduled_run_slots_purpose_check
  CHECK (purpose IN ('scheduled','handshake','on_demand'));
ALTER TABLE app.scheduled_run_slots
  DROP CONSTRAINT scheduled_run_slots_check2;
ALTER TABLE app.scheduled_run_slots
  ADD CONSTRAINT scheduled_run_slots_check2 CHECK (
    (purpose = 'handshake' AND phase = 'on-demand' AND NOT holiday)
    OR (purpose = 'on_demand' AND phase = 'on-demand' AND NOT holiday)
    OR (purpose = 'scheduled' AND phase <> 'on-demand')
  );
ALTER TABLE app.scheduled_run_slots
  DROP CONSTRAINT scheduled_run_slots_check3;
ALTER TABLE app.scheduled_run_slots
  ADD CONSTRAINT scheduled_run_slots_check3 CHECK (
    (purpose = 'handshake' AND octet_length(handshake_challenge) = 32)
    OR (purpose IN ('scheduled','on_demand') AND handshake_challenge IS NULL)
  );

CREATE POLICY decision_evaluations_owner_select ON app.decision_evaluations
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY market_publications_owner_select ON app.market_publications
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY suggestion_grades_owner_select ON app.suggestion_grades
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY run_evidence_owner_select ON app.run_evidence
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY agent_analysis_submissions_owner_select ON app.agent_analysis_submissions
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY scheduled_run_slots_owner_select ON app.scheduled_run_slots
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY routine_trigger_attempts_owner_select ON app.routine_trigger_attempts
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY profiles_executor_all ON app.profiles
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

CREATE OR REPLACE VIEW api.settings WITH (security_invoker = true) AS
SELECT profile.display_name,
       profile.timezone,
       coalesce(notification.pre_market_enabled, true) AS notify_pre_market,
       coalesce(notification.intraday_enabled, true) AS notify_intraday,
       coalesce(notification.post_market_enabled, true) AS notify_post_market,
       coalesce(notification.operational_enabled, true) AS notify_operational,
       schedule.primary_connection_id,
       coalesce(schedule.timezone, profile.timezone) AS schedule_timezone,
       coalesce(schedule.pre_market_enabled, true) AS schedule_pre_market,
       coalesce(schedule.intraday_enabled, true) AS schedule_intraday,
       coalesce(schedule.post_market_enabled, true) AS schedule_post_market
FROM app.profiles AS profile
LEFT JOIN app.notification_preferences AS notification ON notification.owner_id = profile.id
LEFT JOIN app.analysis_schedules AS schedule ON schedule.owner_id = profile.id;
ALTER VIEW api.settings OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.settings FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.settings TO authenticated;

GRANT SELECT (owner_id, id, run_id, ts, date, ticker, action, bucket, depth,
              entry_zone_low, entry_zone_high, valid_until, stop, target,
              confidence, bull, bear, decisive_factor, risk_verdict,
              invalidation_level, reason, risk_band, price_at_suggestion,
              evidence_as_of, evaluation_id, invalidation_price, decision_mode)
  ON app.suggestions TO authenticated;
GRANT SELECT (owner_id, id, run_id, raw_action, final_action, policy_status,
              reason_codes, explanations, analyst, checker, policy_version, created_at)
  ON app.decision_evaluations TO authenticated;
GRANT SELECT (owner_id, id, run_id, market_date, phase, kind, status,
              telegram_message_ids, delivered_at, created_at, updated_at)
  ON app.market_publications TO authenticated;
GRANT SELECT (owner_id, suggestion_id, graded_at, result, horizon_days,
              benchmark_ticker, stock_return_pct, benchmark_return_pct,
              excess_return_pct, mfe_pct, mae_pct, entry_hit_at, stop_hit_at,
              target_hit_at, invalidation_hit_at, coverage_status,
              horizon_sessions, final_action, direction_success)
  ON app.suggestion_grades TO authenticated;
GRANT SELECT (owner_id, run_id, evidence_id, category, source_identifier,
              reference_identifier, observed_at, retrieved_at, revalidated_at,
              claims, status)
  ON app.run_evidence TO authenticated;
GRANT SELECT (owner_id, run_id, provider, model, phase, market_date, status, created_at)
  ON app.agent_analysis_submissions TO authenticated;
GRANT SELECT (owner_id, id, connection_id, market_date, phase, purpose, due_at,
              window_ends_at, holiday, canonical_run_id, status, created_at, updated_at)
  ON app.scheduled_run_slots TO authenticated;
GRANT SELECT (owner_id, id, slot_id, connection_id, status, response_status,
              provider_session_url, claimed_at, finished_at)
  ON app.routine_trigger_attempts TO authenticated;

CREATE VIEW api.research WITH (security_invoker = true) AS
SELECT suggestion.id::text AS id,
       suggestion.run_id,
       run.market_date,
       run.kind AS phase,
       run.status AS run_status,
       run.provider,
       run.model,
       suggestion.ts AS created_at,
       suggestion.ticker,
       coalesce(evaluation.final_action, suggestion.action) AS action,
       evaluation.raw_action,
       evaluation.policy_status,
       coalesce(evaluation.reason_codes, '[]'::jsonb) AS policy_reason_codes,
       coalesce(evaluation.explanations, '[]'::jsonb) AS policy_explanations,
       CASE WHEN octet_length(evaluation.analyst::text) <= 16000
            THEN evaluation.analyst ELSE '{"completed":false}'::jsonb END AS analyst,
       CASE WHEN octet_length(evaluation.checker::text) <= 16000
            THEN evaluation.checker ELSE '{"completed":false}'::jsonb END AS checker,
       suggestion.confidence,
       suggestion.price_at_suggestion::text AS verified_price,
       suggestion.evidence_as_of,
       suggestion.entry_zone_low::text AS entry_zone_low,
       suggestion.entry_zone_high::text AS entry_zone_high,
       suggestion.stop::text AS stop,
       suggestion.target::text AS target,
       suggestion.invalidation_price::text AS invalidation_price,
       suggestion.valid_until,
       suggestion.depth AS horizon,
       suggestion.bucket,
       suggestion.risk_verdict,
       left(suggestion.decisive_factor, 4000) AS decisive_factor,
       left(suggestion.reason, 4000) AS reason,
       left(suggestion.bull, 4000) AS bull_case,
       left(suggestion.bear, 4000) AS bear_case,
       CASE
         WHEN action.state IN ('suspected','needs_review') THEN 'corporate_action_pending'
         WHEN EXISTS (
           SELECT 1 FROM app.run_evidence conflict
           WHERE conflict.owner_id = suggestion.owner_id
             AND conflict.run_id = suggestion.run_id AND conflict.status = 'conflicting'
         ) THEN 'source_conflict'
         WHEN EXISTS (
           SELECT 1 FROM app.run_evidence stale
           WHERE stale.owner_id = suggestion.owner_id
             AND stale.run_id = suggestion.run_id AND stale.status = 'stale'
         ) THEN 'stale'
         WHEN suggestion.run_id IS NULL OR NOT EXISTS (
           SELECT 1 FROM app.run_evidence present
           WHERE present.owner_id = suggestion.owner_id AND present.run_id = suggestion.run_id
         ) THEN 'missing'
         ELSE 'fresh'
       END AS evidence_status,
       coalesce(evidence.items, '[]'::jsonb) AS evidence,
       publication.kind AS publication_kind,
       publication.status AS notification_status,
       publication.delivered_at,
       CASE WHEN publication.status = 'delivered'
            THEN coalesce((
              SELECT jsonb_agg(message_id #>> '{}')
              FROM jsonb_array_elements(publication.telegram_message_ids) AS message_id
            ), '[]'::jsonb)
            ELSE '[]'::jsonb END AS telegram_message_ids,
       CASE publication.status
         WHEN 'delivery_failed' THEN 'TELEGRAM_REJECTED'
         WHEN 'delivery_unknown' THEN 'TELEGRAM_OUTCOME_UNKNOWN'
         ELSE NULL
       END AS delivery_error_code,
       coalesce(outcome.items, '[]'::jsonb) AS outcomes
FROM app.suggestions AS suggestion
LEFT JOIN app.analysis_runs AS run
  ON run.owner_id = suggestion.owner_id AND run.id = suggestion.run_id
LEFT JOIN app.decision_evaluations AS evaluation
  ON evaluation.owner_id = suggestion.owner_id AND evaluation.id = suggestion.evaluation_id
LEFT JOIN app.market_publications AS publication
  ON publication.owner_id = suggestion.owner_id AND publication.run_id = suggestion.run_id
LEFT JOIN app.corporate_action_states AS action
  ON action.owner_id = suggestion.owner_id AND action.ticker = suggestion.ticker
LEFT JOIN LATERAL (
  SELECT jsonb_agg(jsonb_build_object(
    'evidence_id', bounded.evidence_id,
    'category', bounded.category,
    'source', bounded.source_identifier,
    'reference', bounded.reference_identifier,
    'observed_at', bounded.observed_at,
    'retrieved_at', bounded.retrieved_at,
    'revalidated_at', bounded.revalidated_at,
    'claims', bounded.claims,
    'status', bounded.status
  ) ORDER BY bounded.retrieved_at DESC, bounded.evidence_id) AS items
  FROM (
    SELECT evidence.evidence_id, evidence.category, evidence.source_identifier,
           evidence.reference_identifier, evidence.observed_at, evidence.retrieved_at,
           evidence.revalidated_at, evidence.claims, evidence.status
    FROM app.run_evidence AS evidence
    WHERE evidence.owner_id = suggestion.owner_id AND evidence.run_id = suggestion.run_id
    ORDER BY evidence.retrieved_at DESC, evidence.evidence_id
    LIMIT 25
  ) AS bounded
) AS evidence ON true
LEFT JOIN LATERAL (
  SELECT jsonb_agg(jsonb_build_object(
    'horizon_sessions', grade.horizon_days,
    'coverage_status', grade.coverage_status,
    'stock_return_pct', grade.stock_return_pct::text,
    'benchmark', grade.benchmark_ticker,
    'benchmark_return_pct', grade.benchmark_return_pct::text,
    'excess_return_pct', grade.excess_return_pct::text,
    'mfe_pct', grade.mfe_pct::text,
    'mae_pct', grade.mae_pct::text,
    'entry_hit_at', grade.entry_hit_at,
    'stop_hit_at', grade.stop_hit_at,
    'target_hit_at', grade.target_hit_at,
    'invalidation_hit_at', grade.invalidation_hit_at,
    'direction_success', grade.direction_success,
    'graded_at', grade.graded_at
  ) ORDER BY grade.horizon_days) AS items
  FROM app.suggestion_grades AS grade
  WHERE grade.owner_id = suggestion.owner_id
    AND grade.suggestion_id = suggestion.id AND grade.horizon_days IN (5,21,63)
) AS outcome ON true;

CREATE VIEW api.run_timeline WITH (security_invoker = true) AS
WITH timeline AS (
  SELECT slot.owner_id,
         slot.id AS slot_id,
         slot.market_date,
         slot.phase,
         slot.purpose,
         slot.due_at,
         slot.window_ends_at,
         slot.holiday,
         slot.status AS slot_status,
         slot.canonical_run_id AS run_id
  FROM app.scheduled_run_slots AS slot
  UNION ALL
  SELECT run.owner_id,
         NULL::uuid,
         run.market_date,
         run.kind,
         'provider_direct'::text,
         NULL::timestamptz,
         NULL::timestamptz,
         false,
         'provider_started'::text,
         run.id
  FROM app.analysis_runs AS run
  WHERE NOT EXISTS (
    SELECT 1 FROM app.scheduled_run_slots AS slot
    WHERE slot.owner_id = run.owner_id AND slot.canonical_run_id = run.id
  )
)
SELECT timeline.slot_id,
       timeline.market_date,
       timeline.phase,
       timeline.purpose,
       timeline.due_at AS expected_at,
       timeline.window_ends_at,
       timeline.holiday,
       timeline.slot_status,
       trigger.status AS trigger_status,
       trigger.response_status AS trigger_response_status,
       trigger.provider_session_url,
       trigger.claimed_at AS trigger_started_at,
       trigger.finished_at AS trigger_finished_at,
       run.id AS run_id,
       run.started_at,
       run.finished_at,
       run.status AS run_status,
       run.data_as_of,
       run.source_status,
       run.symbols,
       run.write_counts,
       run.summary,
       run.provider,
       run.model,
       submission.status AS submission_status,
       coalesce(policy.states, '[]'::jsonb) AS policy_states,
       CASE
         WHEN EXISTS (
           SELECT 1 FROM app.run_evidence evidence
           WHERE evidence.owner_id = timeline.owner_id
             AND evidence.run_id = timeline.run_id AND evidence.status = 'conflicting'
         ) THEN 'source_conflict'
         WHEN EXISTS (
           SELECT 1 FROM app.run_evidence evidence
           WHERE evidence.owner_id = timeline.owner_id
             AND evidence.run_id = timeline.run_id AND evidence.status = 'stale'
         ) THEN 'stale'
         WHEN timeline.run_id IS NULL THEN 'not_started'
         WHEN NOT EXISTS (
           SELECT 1 FROM app.run_evidence evidence
           WHERE evidence.owner_id = timeline.owner_id AND evidence.run_id = timeline.run_id
         ) THEN 'missing'
         ELSE 'fresh'
       END AS evidence_status,
       publication.kind AS publication_kind,
       publication.status AS publication_status,
       publication.delivered_at,
       CASE WHEN publication.status = 'delivered'
            THEN coalesce((
              SELECT jsonb_agg(message_id #>> '{}')
              FROM jsonb_array_elements(publication.telegram_message_ids) AS message_id
            ), '[]'::jsonb)
            ELSE '[]'::jsonb END AS telegram_message_ids,
       CASE
         WHEN timeline.slot_status = 'trigger_failed' THEN 'PROVIDER_TRIGGER_FAILED'
         WHEN timeline.slot_status = 'trigger_unknown' THEN 'TRIGGER_OUTCOME_UNKNOWN'
         WHEN timeline.slot_status = 'missed' THEN 'EXPECTED_RUN_MISSED'
         WHEN timeline.slot_status = 'budget_suppressed' THEN 'RUN_BUDGET_SUPPRESSED'
         WHEN run.status = 'partial' THEN 'RUN_PARTIAL'
         WHEN publication.status = 'delivery_failed' THEN 'TELEGRAM_REJECTED'
         WHEN publication.status = 'delivery_unknown' THEN 'TELEGRAM_OUTCOME_UNKNOWN'
         ELSE NULL
       END AS error_code
FROM timeline
LEFT JOIN app.routine_trigger_attempts AS trigger
  ON trigger.owner_id = timeline.owner_id AND trigger.slot_id = timeline.slot_id
LEFT JOIN app.analysis_runs AS run
  ON run.owner_id = timeline.owner_id AND run.id = timeline.run_id
LEFT JOIN LATERAL (
  SELECT submitted.status, submitted.created_at
  FROM app.agent_analysis_submissions AS submitted
  WHERE submitted.owner_id = timeline.owner_id AND submitted.run_id = timeline.run_id
  ORDER BY submitted.created_at DESC LIMIT 1
) AS submission ON true
LEFT JOIN LATERAL (
  SELECT jsonb_agg(DISTINCT evaluation.policy_status) AS states
  FROM app.decision_evaluations AS evaluation
  WHERE evaluation.owner_id = timeline.owner_id AND evaluation.run_id = timeline.run_id
) AS policy ON true
LEFT JOIN app.market_publications AS publication
  ON publication.owner_id = timeline.owner_id AND publication.run_id = timeline.run_id;

ALTER VIEW api.research OWNER TO stock_agent_migration_owner;
ALTER VIEW api.run_timeline OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.research, api.run_timeline FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.research, api.run_timeline TO authenticated;

CREATE OR REPLACE FUNCTION app.create_on_demand_run_slot(
  p_owner_id UUID,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  connection_row app.agent_connections%ROWTYPE;
  observed_at TIMESTAMPTZ := clock_timestamp();
  market_day DATE := (observed_at AT TIME ZONE 'America/New_York')::date;
  slot_uuid UUID := extensions.gen_random_uuid();
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY[]::text[]) THEN
    RAISE EXCEPTION 'invalid run request' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_owner_id::text || ':on-demand', 0));
  SELECT connection.* INTO connection_row
  FROM app.analysis_schedules AS schedule
  JOIN app.agent_connections AS connection
    ON connection.owner_id = schedule.owner_id
   AND connection.id = schedule.primary_connection_id
  WHERE schedule.owner_id = p_owner_id
    AND connection.status = 'active'
    AND connection.provider = 'claude'
    AND connection.trigger_url IS NOT NULL
    AND connection.outbound_trigger_secret_id IS NOT NULL
  FOR UPDATE OF connection;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'connection unavailable' USING ERRCODE = '42501';
  END IF;
  IF EXISTS (
    SELECT 1 FROM app.scheduled_run_slots
    WHERE owner_id = p_owner_id AND purpose = 'on_demand'
      AND created_at >= observed_at - interval '1 hour'
  ) THEN
    RAISE EXCEPTION 'on-demand run already requested' USING ERRCODE = '40001';
  END IF;
  INSERT INTO app.scheduled_run_slots(
    id, owner_id, connection_id, market_date, phase, purpose,
    due_at, window_ends_at, holiday, status
  ) VALUES (
    slot_uuid, p_owner_id, connection_row.id, market_day, 'on-demand', 'on_demand',
    observed_at, observed_at + interval '20 minutes', false, 'pending'
  );
  RETURN jsonb_build_object(
    'status', 'queued',
    'slot_id', slot_uuid,
    'phase', 'on-demand',
    'market_date', market_day,
    'expected_by', observed_at + interval '20 minutes',
    'telegram', 'suppressed'
  );
END
$$;
ALTER FUNCTION app.create_on_demand_run_slot(UUID, JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.create_on_demand_run_slot(UUID, JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE OR REPLACE FUNCTION app.read_owner_settings(
  p_owner_id UUID,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  result_value JSONB;
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY[]::text[]) THEN
    RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
  END IF;
  SELECT jsonb_build_object(
    'display_name', coalesce(profile.display_name, 'Stock Agent owner'),
    'timezone', profile.timezone,
    'notify_pre_market', coalesce(notification.pre_market_enabled, true),
    'notify_intraday', coalesce(notification.intraday_enabled, true),
    'notify_post_market', coalesce(notification.post_market_enabled, true),
    'notify_operational', coalesce(notification.operational_enabled, true),
    'primary_connection_id', schedule.primary_connection_id,
    'schedule_timezone', coalesce(schedule.timezone, profile.timezone),
    'schedule_pre_market', coalesce(schedule.pre_market_enabled, true),
    'schedule_intraday', coalesce(schedule.intraday_enabled, true),
    'schedule_post_market', coalesce(schedule.post_market_enabled, true)
  ) INTO result_value
  FROM app.profiles AS profile
  LEFT JOIN app.notification_preferences AS notification ON notification.owner_id = profile.id
  LEFT JOIN app.analysis_schedules AS schedule ON schedule.owner_id = profile.id
  WHERE profile.id = p_owner_id AND profile.status = 'active';
  IF result_value IS NULL THEN
    RAISE EXCEPTION 'settings unavailable' USING ERRCODE = '42501';
  END IF;
  RETURN result_value;
END
$$;

CREATE OR REPLACE FUNCTION app.update_owner_settings(
  p_owner_id UUID,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  allowed_keys CONSTANT TEXT[] := ARRAY[
    'display_name','timezone','notify_pre_market','notify_intraday','notify_post_market',
    'notify_operational','schedule_pre_market','schedule_intraday','schedule_post_market'
  ];
  request_key TEXT;
  timezone_value TEXT;
BEGIN
  IF p_owner_id IS NULL OR jsonb_typeof(p_request) <> 'object'
     OR p_request = '{}'::jsonb THEN
    RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
  END IF;
  FOR request_key IN SELECT jsonb_object_keys(p_request) LOOP
    IF NOT request_key = ANY(allowed_keys) THEN
      RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
    END IF;
    IF request_key = 'display_name' THEN
      IF jsonb_typeof(p_request->request_key) <> 'string'
         OR char_length(btrim(p_request->>request_key)) NOT BETWEEN 1 AND 120 THEN
        RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
      END IF;
    ELSIF request_key = 'timezone' THEN
      IF jsonb_typeof(p_request->request_key) <> 'string'
         OR char_length(p_request->>request_key) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
      END IF;
    ELSIF jsonb_typeof(p_request->request_key) <> 'boolean' THEN
      RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
    END IF;
  END LOOP;

  SELECT timezone INTO timezone_value
  FROM app.profiles WHERE id = p_owner_id AND status = 'active' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'settings unavailable' USING ERRCODE = '42501'; END IF;
  IF p_request ? 'timezone' AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name = p_request->>'timezone'
  ) THEN
    RAISE EXCEPTION 'invalid settings request' USING ERRCODE = '22023';
  END IF;
  IF p_request ? 'timezone' THEN timezone_value := p_request->>'timezone'; END IF;

  UPDATE app.profiles SET
    display_name = CASE WHEN p_request ? 'display_name' THEN btrim(p_request->>'display_name') ELSE display_name END,
    timezone = timezone_value,
    updated_at = clock_timestamp()
  WHERE id = p_owner_id;

  INSERT INTO app.notification_preferences(
    owner_id, pre_market_enabled, intraday_enabled, post_market_enabled, operational_enabled
  ) VALUES (
    p_owner_id,
    coalesce((p_request->>'notify_pre_market')::boolean, true),
    coalesce((p_request->>'notify_intraday')::boolean, true),
    coalesce((p_request->>'notify_post_market')::boolean, true),
    coalesce((p_request->>'notify_operational')::boolean, true)
  ) ON CONFLICT (owner_id) DO UPDATE SET
    pre_market_enabled = CASE WHEN p_request ? 'notify_pre_market' THEN (p_request->>'notify_pre_market')::boolean ELSE app.notification_preferences.pre_market_enabled END,
    intraday_enabled = CASE WHEN p_request ? 'notify_intraday' THEN (p_request->>'notify_intraday')::boolean ELSE app.notification_preferences.intraday_enabled END,
    post_market_enabled = CASE WHEN p_request ? 'notify_post_market' THEN (p_request->>'notify_post_market')::boolean ELSE app.notification_preferences.post_market_enabled END,
    operational_enabled = CASE WHEN p_request ? 'notify_operational' THEN (p_request->>'notify_operational')::boolean ELSE app.notification_preferences.operational_enabled END,
    updated_at = clock_timestamp();

  INSERT INTO app.analysis_schedules(
    owner_id, timezone, pre_market_enabled, intraday_enabled, post_market_enabled
  ) VALUES (
    p_owner_id,
    timezone_value,
    coalesce((p_request->>'schedule_pre_market')::boolean, true),
    coalesce((p_request->>'schedule_intraday')::boolean, true),
    coalesce((p_request->>'schedule_post_market')::boolean, true)
  ) ON CONFLICT (owner_id) DO UPDATE SET
    timezone = CASE WHEN p_request ? 'timezone' THEN timezone_value ELSE app.analysis_schedules.timezone END,
    pre_market_enabled = CASE WHEN p_request ? 'schedule_pre_market' THEN (p_request->>'schedule_pre_market')::boolean ELSE app.analysis_schedules.pre_market_enabled END,
    intraday_enabled = CASE WHEN p_request ? 'schedule_intraday' THEN (p_request->>'schedule_intraday')::boolean ELSE app.analysis_schedules.intraday_enabled END,
    post_market_enabled = CASE WHEN p_request ? 'schedule_post_market' THEN (p_request->>'schedule_post_market')::boolean ELSE app.analysis_schedules.post_market_enabled END,
    updated_at = clock_timestamp();
  RETURN jsonb_build_object('status', 'updated');
END
$$;

CREATE OR REPLACE FUNCTION app.unlink_owner_telegram(
  p_owner_id UUID,
  p_request JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
  IF p_owner_id IS NULL OR NOT app.jsonb_has_exact_keys(p_request, ARRAY[]::text[]) THEN
    RAISE EXCEPTION 'invalid unlink request' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('telegram-link:' || p_owner_id::text, 0));
  UPDATE app.telegram_links SET status = 'revoked', revoked_at = coalesce(revoked_at, clock_timestamp())
  WHERE owner_id = p_owner_id AND status = 'active';
  UPDATE app.telegram_pairing_codes SET consumed_at = coalesce(consumed_at, clock_timestamp())
  WHERE owner_id = p_owner_id AND consumed_at IS NULL;
  UPDATE app.portfolio_commands SET status = 'cancelled', updated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND channel = 'telegram' AND status = 'previewed';
  UPDATE app.telegram_callback_tokens SET invalidated_at = clock_timestamp()
  WHERE owner_id = p_owner_id AND consumed_at IS NULL AND invalidated_at IS NULL;
  RETURN jsonb_build_object('status', 'unlinked');
END
$$;

ALTER FUNCTION app.read_owner_settings(UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.update_owner_settings(UUID, JSONB) OWNER TO stock_agent_migration_owner;
ALTER FUNCTION app.unlink_owner_telegram(UUID, JSONB) OWNER TO stock_agent_migration_owner;
REVOKE ALL ON FUNCTION app.read_owner_settings(UUID, JSONB),
  app.update_owner_settings(UUID, JSONB), app.unlink_owner_telegram(UUID, JSONB)
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

-- Replace the browser dispatcher to include the reviewed run-now route. The Edge layer
-- authenticates and validates an exact empty body before entering this function.
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
DECLARE
  owner_value UUID := auth.uid();
  scope_value TEXT;
  limit_value INT;
  window_value INT;
  owner_limit JSONB;
  client_limit JSONB;
  data_value JSONB;
  result_code TEXT;
BEGIN
  IF owner_value IS NULL OR p_request_id IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'UNAUTHORIZED'));
  END IF;
  IF jsonb_typeof(p_request) <> 'object' OR p_ip_digest !~ '^[0-9a-f]{64}$' THEN
    RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'INVALID_REQUEST'));
  END IF;

  CASE p_route
    WHEN 'POST /portfolio/preview',
         'POST /portfolio/correction/preview',
         'POST /plans/preview' THEN
      scope_value := 'command_preview'; limit_value := 30; window_value := 60;
    WHEN 'POST /portfolio/confirm',
         'POST /portfolio/correction/confirm',
         'POST /plans/confirm' THEN
      scope_value := 'command_confirm'; limit_value := 20; window_value := 60;
    WHEN 'POST /telegram/pairing-code' THEN
      scope_value := 'telegram_pairing'; limit_value := 5; window_value := 600;
    WHEN 'POST /telegram/unlink' THEN
      scope_value := 'telegram_change'; limit_value := 10; window_value := 3600;
    WHEN 'POST /connections/create',
         'POST /connections/handshake',
         'POST /connections/activate',
         'POST /connections/revoke' THEN
      scope_value := 'connection_change'; limit_value := 10; window_value := 3600;
    WHEN 'POST /runs/on-demand' THEN
      scope_value := 'run_on_demand'; limit_value := 1; window_value := 3600;
    WHEN 'GET /settings', 'PATCH /settings' THEN
      scope_value := 'settings'; limit_value := 30; window_value := 60;
    WHEN 'GET /export' THEN
      scope_value := 'export'; limit_value := 2; window_value := 86400;
    WHEN 'POST /account/delete/request', 'POST /account/delete/confirm' THEN
      scope_value := 'account_delete'; limit_value := 3; window_value := 86400;
    ELSE
      RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'ROUTE_NOT_ALLOWED'));
  END CASE;

  owner_limit := app.consume_rate_limit(
    owner_value, scope_value, 'owner', limit_value, window_value
  );
  client_limit := app.consume_rate_limit(
    owner_value, scope_value, p_ip_digest, limit_value, window_value
  );
  IF NOT (owner_limit->>'allowed')::boolean OR NOT (client_limit->>'allowed')::boolean THEN
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, 'RATE_LIMITED');
    RETURN jsonb_build_object(
      'ok', false,
      'error', jsonb_build_object(
        'code', 'RATE_LIMITED',
        'retry_after_seconds', greatest(
          (owner_limit->>'retry_after_seconds')::int,
          (client_limit->>'retry_after_seconds')::int
        )
      )
    );
  END IF;

  BEGIN
    CASE p_route
      WHEN 'POST /portfolio/preview',
           'POST /portfolio/correction/preview',
           'POST /plans/preview' THEN
        data_value := api.preview_portfolio_command(p_request);
      WHEN 'POST /portfolio/confirm',
           'POST /portfolio/correction/confirm',
           'POST /plans/confirm' THEN
        IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['command_id','preview_digest']) THEN
          RAISE EXCEPTION 'invalid confirmation request';
        END IF;
        data_value := api.confirm_portfolio_command(
          (p_request->>'command_id')::uuid,
          p_request->>'preview_digest'
        );
      WHEN 'POST /telegram/pairing-code' THEN
        IF NOT app.jsonb_has_exact_keys(p_request, ARRAY['code_digest'])
           OR p_request->>'code_digest' !~ '^[0-9a-f]{64}$' THEN
          RAISE EXCEPTION 'invalid pairing code request';
        END IF;
        data_value := app.issue_telegram_pairing_code(
          owner_value, p_request->>'code_digest'
        );
      WHEN 'POST /telegram/unlink' THEN
        data_value := app.unlink_owner_telegram(owner_value, p_request);
      WHEN 'POST /connections/create' THEN
        data_value := app.create_agent_connection(owner_value, p_request);
      WHEN 'POST /connections/handshake' THEN
        data_value := app.begin_agent_connection_handshake(owner_value, p_request);
      WHEN 'POST /connections/activate' THEN
        data_value := app.activate_agent_connection(owner_value, p_request);
      WHEN 'POST /connections/revoke' THEN
        data_value := app.revoke_agent_connection(owner_value, p_request);
      WHEN 'POST /runs/on-demand' THEN
        data_value := app.create_on_demand_run_slot(owner_value, p_request);
      WHEN 'GET /settings' THEN
        data_value := app.read_owner_settings(owner_value, p_request);
      WHEN 'PATCH /settings' THEN
        data_value := app.update_owner_settings(owner_value, p_request);
      ELSE
        INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
        VALUES (owner_value, p_request_id, p_route, 'NOT_READY');
        RETURN jsonb_build_object('ok', false, 'error', jsonb_build_object('code', 'NOT_READY'));
    END CASE;
    result_code := upper(coalesce(data_value->>'status', 'OK'));
    INSERT INTO app.app_api_audit_events(owner_id, request_id, route, result_code)
    VALUES (owner_value, p_request_id, p_route, result_code);
    RETURN jsonb_build_object('ok', true, 'data', data_value, 'request_id', p_request_id);
  EXCEPTION
    WHEN invalid_text_representation OR invalid_parameter_value OR check_violation
      OR numeric_value_out_of_range OR datetime_field_overflow THEN
      result_code := 'INVALID_REQUEST';
    WHEN unique_violation OR serialization_failure THEN
      result_code := 'CONFLICT';
    WHEN insufficient_privilege THEN
      result_code := 'NOT_FOUND';
    WHEN OTHERS THEN
      result_code := CASE
        WHEN SQLERRM ~* '(invalid|cash total|bucket|required|holding not found|sell exceeds|expired)'
          THEN 'INVALID_REQUEST'
        WHEN SQLERRM ~* '(stale|changed|digest|idempotency|cancelled|already)'
          THEN 'CONFLICT'
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
