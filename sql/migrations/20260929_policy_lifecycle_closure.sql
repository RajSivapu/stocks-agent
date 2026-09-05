-- Final policy/lifecycle closure. Legacy dry_powder is deliberately not a cash
-- authority. Spendable cash must be an explicit, fresh reconciliation bound to
-- the transaction ledger watermark.
CREATE TABLE IF NOT EXISTS public.portfolio_cash_ledger_state (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
INSERT INTO public.portfolio_cash_ledger_state(singleton) VALUES(true)
ON CONFLICT(singleton) DO NOTHING;

CREATE OR REPLACE FUNCTION public.advance_portfolio_cash_ledger()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
  UPDATE public.portfolio_cash_ledger_state
  SET revision=revision+1,updated_at=statement_timestamp()
  WHERE singleton=true;
  RETURN NULL;
END;
$$;
DROP TRIGGER IF EXISTS transactions_advance_cash_ledger ON public.transactions;
CREATE TRIGGER transactions_advance_cash_ledger
AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.transactions
FOR EACH STATEMENT EXECUTE FUNCTION public.advance_portfolio_cash_ledger();

CREATE OR REPLACE FUNCTION public.read_portfolio_cash_ledger_watermark()
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
  SELECT jsonb_build_object(
    'ledger_watermark',revision::text,
    'ledger_updated_at',updated_at
  ) FROM public.portfolio_cash_ledger_state WHERE singleton=true
$$;

CREATE TABLE IF NOT EXISTS public.reconciled_cash_snapshots (
  id UUID PRIMARY KEY,
  as_of TIMESTAMPTZ NOT NULL,
  fresh_through TIMESTAMPTZ NOT NULL,
  ledger_watermark BIGINT NOT NULL CHECK (ledger_watermark >= 0),
  core_available NUMERIC NOT NULL CHECK (core_available >= 0),
  growth_available NUMERIC NOT NULL CHECK (growth_available >= 0),
  speculative_available NUMERIC NOT NULL CHECK (speculative_available >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CHECK (fresh_through > as_of AND fresh_through <= as_of + interval '30 minutes')
);
CREATE INDEX IF NOT EXISTS idx_reconciled_cash_snapshots_current
  ON public.reconciled_cash_snapshots(as_of DESC,created_at DESC);
DROP TRIGGER IF EXISTS reconciled_cash_snapshots_append_only
  ON public.reconciled_cash_snapshots;
CREATE TRIGGER reconciled_cash_snapshots_append_only BEFORE UPDATE OR DELETE
ON public.reconciled_cash_snapshots FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();

CREATE OR REPLACE FUNCTION public.record_reconciled_cash_snapshot(
  p_snapshot_id UUID,
  p_as_of TIMESTAMPTZ,
  p_fresh_through TIMESTAMPTZ,
  p_ledger_watermark BIGINT,
  p_cash JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_revision BIGINT;
  v_ledger_updated_at TIMESTAMPTZ;
  v_existing public.reconciled_cash_snapshots%ROWTYPE;
  v_duplicate BOOLEAN := false;
BEGIN
  IF p_snapshot_id IS NULL OR p_as_of IS NULL OR p_fresh_through IS NULL
     OR p_ledger_watermark IS NULL OR jsonb_typeof(p_cash) IS DISTINCT FROM 'object'
     OR NOT (p_cash ?& ARRAY['core','growth','speculative'])
     OR (p_cash - ARRAY['core','growth','speculative']) <> '{}'::jsonb
     OR EXISTS (
       SELECT 1 FROM jsonb_each(p_cash) item
       WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'string'
         OR trim(both '"' from item.value::text) !~ '^(0|[1-9][0-9]{0,14})(\.[0-9]{1,6})?$'
     )
     OR p_as_of > statement_timestamp() + interval '1 minute'
     OR p_fresh_through <= statement_timestamp()
     OR p_fresh_through > p_as_of + interval '30 minutes' THEN
    RAISE EXCEPTION 'invalid reconciled cash snapshot' USING ERRCODE='22023';
  END IF;

  SELECT revision,updated_at INTO v_revision,v_ledger_updated_at
  FROM public.portfolio_cash_ledger_state
  WHERE singleton=true FOR SHARE;
  IF v_revision IS DISTINCT FROM p_ledger_watermark
     OR p_as_of < v_ledger_updated_at THEN
    RAISE EXCEPTION 'cash ledger watermark changed' USING ERRCODE='40001';
  END IF;

  SELECT * INTO v_existing FROM public.reconciled_cash_snapshots
  WHERE id=p_snapshot_id;
  IF FOUND THEN
    v_duplicate := true;
    IF v_existing.as_of IS DISTINCT FROM p_as_of
       OR v_existing.fresh_through IS DISTINCT FROM p_fresh_through
       OR v_existing.ledger_watermark IS DISTINCT FROM p_ledger_watermark
       OR v_existing.core_available IS DISTINCT FROM (p_cash->>'core')::numeric
       OR v_existing.growth_available IS DISTINCT FROM (p_cash->>'growth')::numeric
       OR v_existing.speculative_available IS DISTINCT FROM (p_cash->>'speculative')::numeric THEN
      RAISE EXCEPTION 'cash snapshot idempotency mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    INSERT INTO public.reconciled_cash_snapshots(
      id,as_of,fresh_through,ledger_watermark,
      core_available,growth_available,speculative_available
    ) VALUES (
      p_snapshot_id,p_as_of,p_fresh_through,p_ledger_watermark,
      (p_cash->>'core')::numeric,(p_cash->>'growth')::numeric,
      (p_cash->>'speculative')::numeric
    ) RETURNING * INTO v_existing;
  END IF;

  RETURN jsonb_build_object(
    'snapshot_id',v_existing.id,
    'as_of',v_existing.as_of,
    'fresh_through',v_existing.fresh_through,
    'ledger_watermark',v_existing.ledger_watermark::text,
    'spendable_cash',jsonb_build_object(
      'core',v_existing.core_available::text,
      'growth',v_existing.growth_available::text,
      'speculative',v_existing.speculative_available::text
    ),
    'duplicate',v_duplicate
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.read_reconciled_cash_snapshot(
  p_now TIMESTAMPTZ DEFAULT statement_timestamp()
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_snapshot public.reconciled_cash_snapshots%ROWTYPE;
BEGIN
  IF p_now IS NULL THEN RETURN NULL; END IF;
  SELECT snapshot.* INTO v_snapshot
  FROM public.reconciled_cash_snapshots snapshot
  JOIN public.portfolio_cash_ledger_state ledger
    ON ledger.singleton=true AND ledger.revision=snapshot.ledger_watermark
  WHERE snapshot.as_of<=p_now AND snapshot.fresh_through>=p_now
  ORDER BY snapshot.as_of DESC,snapshot.created_at DESC,snapshot.id
  LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  RETURN jsonb_build_object(
    'snapshot_id',v_snapshot.id,
    'as_of',v_snapshot.as_of,
    'fresh_through',v_snapshot.fresh_through,
    'ledger_watermark',v_snapshot.ledger_watermark::text,
    'spendable_cash',jsonb_build_object(
      'core',v_snapshot.core_available::text,
      'growth',v_snapshot.growth_available::text,
      'speculative',v_snapshot.speculative_available::text
    )
  );
END;
$$;

-- Keep cash authority valid through the decision write itself. The shared
-- ledger lock prevents a confirmed transaction mutation from committing
-- between this check and apply_market_decision_bundle's durable writes.
CREATE OR REPLACE FUNCTION public.apply_market_decision_bundle_with_cash_snapshot(
  p_request_id UUID,
  p_run_id UUID,
  p_lease_token UUID,
  p_policy_version INT,
  p_evaluations JSONB,
  p_suggestions JSONB,
  p_publication JSONB,
  p_cash_snapshot_id UUID,
  p_cash_ledger_watermark BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_revision BIGINT;
  v_snapshot public.reconciled_cash_snapshots%ROWTYPE;
  v_requires_cash BOOLEAN;
BEGIN
  IF jsonb_typeof(p_evaluations) IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'invalid decision transaction' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_request_id::text,0));
  IF EXISTS (
    SELECT 1 FROM public.market_publications publication
    WHERE publication.idempotency_key=p_request_id
       OR (p_run_id IS NOT NULL AND publication.run_id=p_run_id)
  ) THEN
    -- The original RPC revalidates the active request lease, then returns the
    -- immutable same-request receipt or RUN_ALREADY_EVALUATED. Cash freshness
    -- must gate new money decisions, not make an already durable write orphaned.
    RETURN public.apply_market_decision_bundle(
      p_request_id,p_run_id,p_lease_token,p_policy_version,
      p_evaluations,p_suggestions,p_publication
    );
  END IF;
  SELECT EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_evaluations) evaluation
    WHERE evaluation->>'policy_status'='approved'
      AND evaluation->>'final_action' IN ('buy','add')
  ) INTO v_requires_cash;

  IF v_requires_cash THEN
    IF p_cash_snapshot_id IS NULL OR p_cash_ledger_watermark IS NULL THEN
      RAISE EXCEPTION 'CASH_UNAVAILABLE' USING ERRCODE='55000';
    END IF;
    SELECT revision INTO v_revision
    FROM public.portfolio_cash_ledger_state
    WHERE singleton=true FOR SHARE;
    SELECT * INTO v_snapshot
    FROM public.reconciled_cash_snapshots
    WHERE id=p_cash_snapshot_id FOR SHARE;
    IF NOT FOUND OR v_revision IS DISTINCT FROM p_cash_ledger_watermark
       OR v_snapshot.ledger_watermark IS DISTINCT FROM p_cash_ledger_watermark
       OR v_snapshot.as_of>statement_timestamp()
       OR v_snapshot.fresh_through<statement_timestamp() THEN
      RAISE EXCEPTION 'CASH_UNAVAILABLE' USING ERRCODE='55000';
    END IF;
  ELSIF p_cash_snapshot_id IS NOT NULL OR p_cash_ledger_watermark IS NOT NULL THEN
    RAISE EXCEPTION 'unexpected cash authority' USING ERRCODE='22023';
  END IF;

  RETURN public.apply_market_decision_bundle(
    p_request_id,p_run_id,p_lease_token,p_policy_version,
    p_evaluations,p_suggestions,p_publication
  );
END;
$$;

-- A quiet scheduled intraday run closes with a durable policy outcome, not a
-- fabricated immutable report or Telegram publication.
CREATE TABLE IF NOT EXISTS public.market_run_terminal_outcomes (
  run_id UUID PRIMARY KEY REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  evaluation_request_id UUID NOT NULL UNIQUE
    REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  outcome TEXT NOT NULL CHECK (outcome IN ('no_trigger','not_actionable')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
DROP TRIGGER IF EXISTS market_run_terminal_outcomes_append_only
  ON public.market_run_terminal_outcomes;
CREATE TRIGGER market_run_terminal_outcomes_append_only BEFORE UPDATE OR DELETE
ON public.market_run_terminal_outcomes FOR EACH ROW
EXECUTE FUNCTION public.reject_market_intelligence_mutation();

CREATE OR REPLACE FUNCTION public.record_market_run_outcome(
  p_request_id UUID,
  p_lease_token UUID,
  p_run_id UUID,
  p_outcome TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_run public.analysis_runs%ROWTYPE;
  v_existing public.market_run_terminal_outcomes%ROWTYPE;
  v_evaluation_request_id UUID;
BEGIN
  IF p_request_id IS NULL OR p_lease_token IS NULL OR p_run_id IS NULL
     OR p_outcome NOT IN ('no_trigger','not_actionable') THEN
    RAISE EXCEPTION 'invalid market run outcome' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'evaluate_and_publish'
     OR v_request.run_id IS DISTINCT FROM p_run_id
     OR v_request.status<>'claimed' OR v_request.lease_token<>p_lease_token THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001';
  END IF;
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id;
  IF NOT FOUND OR v_run.scheduled_phase IS DISTINCT FROM 'intraday'
     OR v_run.scheduled_market_date IS NULL THEN
    RAISE EXCEPTION 'quiet outcome requires scheduled intraday run' USING ERRCODE='22023';
  END IF;
  SELECT publication.idempotency_key INTO v_evaluation_request_id
    FROM public.market_publications publication
    WHERE publication.run_id=p_run_id
      AND publication.idempotency_key=p_request_id
      AND publication.market_date=v_run.scheduled_market_date
      AND publication.phase='intraday' AND publication.status='suppressed'
      AND publication.telegram_message_ids='[]'::jsonb
    ORDER BY publication.created_at,publication.id
    LIMIT 1;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'suppressed evaluation receipt unavailable' USING ERRCODE='55000';
  END IF;
  SELECT * INTO v_existing FROM public.market_run_terminal_outcomes
  WHERE run_id=p_run_id;
  IF FOUND THEN
    IF v_existing.evaluation_request_id IS DISTINCT FROM v_evaluation_request_id
       OR v_existing.outcome IS DISTINCT FROM p_outcome THEN
      RAISE EXCEPTION 'market run outcome mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object(
      'run_id',v_existing.run_id,'outcome',v_existing.outcome,'duplicate',true
    );
  END IF;
  INSERT INTO public.market_run_terminal_outcomes(
    run_id,evaluation_request_id,outcome
  ) VALUES(p_run_id,v_evaluation_request_id,p_outcome) RETURNING * INTO v_existing;
  RETURN jsonb_build_object(
    'run_id',v_existing.run_id,'outcome',v_existing.outcome,'duplicate',false
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_analysis_run(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_run public.analysis_runs%ROWTYPE;
  v_counts JSONB;
  v_statuses JSONB;
  v_ids JSONB;
  v_status TEXT;
  v_quiet_intraday BOOLEAN := false;
BEGIN
  SELECT * INTO v_run FROM public.analysis_runs WHERE id=p_run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE='22023'; END IF;
  IF v_run.scheduled_phase IS NOT NULL THEN
    IF NOT EXISTS (
      SELECT 1 FROM public.market_intelligence_runs i
      WHERE i.id=p_run_id AND i.phase=v_run.scheduled_phase
        AND i.market_date=v_run.scheduled_market_date
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_intelligence_run_events e
      WHERE e.run_id=p_run_id AND e.status='completed'
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_collection_checkpoints c WHERE c.run_id=p_run_id
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_intelligence_collection_completions c WHERE c.run_id=p_run_id
    ) THEN
      RAISE EXCEPTION 'MISSING_COLLECTION_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_evidence_packets p WHERE p.run_id=p_run_id
    ) THEN
      RAISE EXCEPTION 'MISSING_PACKET_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      WHERE q.run_id=p_run_id AND q.operation='evaluate_and_publish'
        AND q.status='completed'
    ) OR NOT EXISTS (
      SELECT 1 FROM public.market_publications p
      WHERE p.run_id=p_run_id AND p.market_date=v_run.scheduled_market_date
        AND p.phase=v_run.scheduled_phase AND p.status='suppressed'
    ) THEN
      RAISE EXCEPTION 'MISSING_EVALUATION_RECEIPT' USING ERRCODE='22023';
    END IF;

    SELECT v_run.scheduled_phase='intraday' AND EXISTS (
      SELECT 1 FROM public.market_run_terminal_outcomes outcome
      JOIN public.market_gateway_requests request
        ON request.request_id=outcome.evaluation_request_id
      JOIN public.market_publications publication
        ON publication.run_id=outcome.run_id
       AND publication.idempotency_key=outcome.evaluation_request_id
      WHERE outcome.run_id=p_run_id
        AND outcome.outcome IN ('no_trigger','not_actionable')
        AND request.run_id=p_run_id AND request.operation='evaluate_and_publish'
        AND request.status='completed'
        AND publication.market_date=v_run.scheduled_market_date
        AND publication.phase='intraday' AND publication.status='suppressed'
        AND publication.telegram_message_ids='[]'::jsonb
    ) INTO v_quiet_intraday;

    IF NOT v_quiet_intraday AND NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_report_request_origins o ON o.request_id=q.request_id
      JOIN public.market_reports r ON r.id=CASE
        WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        THEN (q.response->>'report_id')::uuid END
      WHERE q.operation='record_report' AND q.run_id IS NULL
        AND q.status='completed' AND jsonb_typeof(q.response)='object'
        AND q.response->>'report_hash'=r.report_hash
        AND q.response->>'rendered_hash'=r.rendered_hash
        AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase
        AND o.market_date=v_run.scheduled_market_date
        AND o.requested_packet_id=r.packet_id
        AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
        AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent')))
    ) THEN
      RAISE EXCEPTION 'MISSING_REPORT_RECEIPT' USING ERRCODE='22023';
    END IF;
    IF NOT v_quiet_intraday AND NOT EXISTS (
      SELECT 1 FROM public.market_gateway_requests q
      JOIN public.market_report_request_origins o ON o.request_id=q.request_id
      JOIN public.market_reports r ON r.id=CASE
        WHEN q.response->>'report_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        THEN (q.response->>'report_id')::uuid END
      JOIN public.market_report_publications p ON p.report_id=r.id
      WHERE q.operation='record_report' AND q.run_id IS NULL
        AND q.status='completed' AND jsonb_typeof(q.response)='object'
        AND q.response->>'report_hash'=r.report_hash
        AND q.response->>'rendered_hash'=r.rendered_hash
        AND o.run_id=p_run_id AND o.scheduled_phase=v_run.scheduled_phase
        AND o.market_date=v_run.scheduled_market_date
        AND o.requested_packet_id=r.packet_id
        AND r.run_id=p_run_id AND r.market_date=v_run.scheduled_market_date
        AND ((v_run.scheduled_phase='pre-market' AND o.requested_kind IN ('morning','monthly'))
          OR (v_run.scheduled_phase='intraday' AND o.requested_kind IN ('intraday','urgent'))
          OR (v_run.scheduled_phase='post-market' AND o.requested_kind IN ('weekly','monthly','theme','urgent')))
        AND p.status IN ('delivered','suppressed')
    ) THEN
      RAISE EXCEPTION 'MISSING_PUBLICATION_RECEIPT' USING ERRCODE='22023';
    END IF;
  END IF;

  SELECT jsonb_build_object(
    'evaluations',(SELECT count(*) FROM public.decision_evaluations WHERE run_id=p_run_id),
    'suggestions',(SELECT count(*) FROM public.suggestions WHERE run_id=p_run_id),
    'publications',(SELECT count(*) FROM public.market_publications WHERE run_id=p_run_id),
    'reports',(SELECT count(*) FROM public.market_reports WHERE run_id=p_run_id),
    'run_outcomes',(SELECT count(*) FROM public.market_run_terminal_outcomes WHERE run_id=p_run_id)
  ) INTO v_counts;
  SELECT COALESCE(jsonb_agg(status ORDER BY status),'[]'::jsonb)
  INTO v_statuses FROM (
    SELECT status FROM public.market_publications WHERE run_id=p_run_id
    UNION ALL
    SELECT p.status FROM public.market_reports r
    JOIN public.market_report_publications p ON p.report_id=r.id
    WHERE r.run_id=p_run_id
  ) states;
  SELECT COALESCE(jsonb_agg(DISTINCT message_id),'[]'::jsonb)
  INTO v_ids FROM (
    SELECT value AS message_id FROM public.market_publications p
    CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value
    WHERE p.run_id=p_run_id
    UNION ALL
    SELECT value AS message_id FROM public.market_reports r
    JOIN public.market_report_publications p ON p.report_id=r.id
    CROSS JOIN LATERAL jsonb_array_elements(p.telegram_message_ids) value
    WHERE r.run_id=p_run_id
  ) message_ids;
  SELECT CASE
    WHEN EXISTS(
      SELECT 1 FROM public.market_gateway_requests
      WHERE run_id=p_run_id AND status='failed'
    ) OR v_statuses ?| ARRAY['delivery_failed','delivery_unknown','failed','uncertain']
      THEN 'partial'
    WHEN v_quiet_intraday OR (
      NOT EXISTS(
        SELECT 1 FROM public.market_reports r
        JOIN public.market_report_publications p ON p.report_id=r.id
        WHERE r.run_id=p_run_id AND p.status='delivered'
      ) AND EXISTS(
        SELECT 1 FROM public.market_reports r
        JOIN public.market_report_publications p ON p.report_id=r.id
        WHERE r.run_id=p_run_id AND p.status='suppressed'
      )
    ) THEN 'suppressed'
    ELSE 'completed'
  END INTO v_status;
  UPDATE public.analysis_runs SET status=v_status,finished_at=statement_timestamp(),
    write_counts=v_counts,telegram_message_ids=v_ids WHERE id=p_run_id;
  RETURN jsonb_build_object(
    'run_id',p_run_id,'status',v_status,'write_counts',v_counts,
    'publication_statuses',v_statuses,'telegram_message_ids',v_ids
  );
END;
$$;

ALTER TABLE public.portfolio_cash_ledger_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reconciled_cash_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_run_terminal_outcomes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.portfolio_cash_ledger_state,
  public.reconciled_cash_snapshots,public.market_run_terminal_outcomes
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.advance_portfolio_cash_ledger(),
  public.read_portfolio_cash_ledger_watermark(),
  public.record_reconciled_cash_snapshot(UUID,TIMESTAMPTZ,TIMESTAMPTZ,BIGINT,JSONB),
  public.read_reconciled_cash_snapshot(TIMESTAMPTZ),
  public.apply_market_decision_bundle_with_cash_snapshot(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT),
  public.record_market_run_outcome(UUID,UUID,UUID,TEXT),
  public.finish_market_analysis_run(UUID)
  FROM PUBLIC,anon,authenticated;
REVOKE EXECUTE ON FUNCTION public.apply_market_decision_bundle(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB)
  FROM service_role;
GRANT EXECUTE ON FUNCTION public.record_reconciled_cash_snapshot(UUID,TIMESTAMPTZ,TIMESTAMPTZ,BIGINT,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.read_portfolio_cash_ledger_watermark() TO service_role;
GRANT EXECUTE ON FUNCTION public.read_reconciled_cash_snapshot(TIMESTAMPTZ) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_market_decision_bundle_with_cash_snapshot(UUID,UUID,UUID,INT,JSONB,JSONB,JSONB,UUID,BIGINT) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_run_outcome(UUID,UUID,UUID,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_analysis_run(UUID) TO service_role;
