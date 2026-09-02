-- Deterministic grading of final policy-evaluated suggestions.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM public.suggestion_grades
    WHERE suggestion_id IS NOT NULL AND horizon_days IS NOT NULL
    GROUP BY suggestion_id, horizon_days HAVING count(*) > 1
  ) THEN
    RAISE EXCEPTION 'duplicate suggestion grades require review before outcome migration';
  END IF;
END;
$$;

ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS benchmark_ticker TEXT;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS stock_return_pct NUMERIC;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS benchmark_return_pct NUMERIC;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS excess_return_pct NUMERIC;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS mfe_pct NUMERIC;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS mae_pct NUMERIC;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS entry_hit_at DATE;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS stop_hit_at DATE;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS target_hit_at DATE;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS invalidation_hit_at DATE;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS coverage_status TEXT;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS horizon_sessions INT;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS policy_version INT;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS final_action TEXT;
ALTER TABLE public.suggestion_grades ADD COLUMN IF NOT EXISTS direction_success BOOLEAN;

CREATE UNIQUE INDEX IF NOT EXISTS idx_suggestion_grades_suggestion_horizon
  ON public.suggestion_grades(suggestion_id, horizon_days);
CREATE INDEX IF NOT EXISTS idx_suggestion_grades_coverage
  ON public.suggestion_grades(coverage_status, horizon_days, graded_at DESC);

CREATE OR REPLACE FUNCTION public.get_due_market_decisions(p_limit INT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE v_result JSONB;
BEGIN
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 50 THEN
    RAISE EXCEPTION 'invalid decision limit' USING ERRCODE = '22023';
  END IF;
  SELECT COALESCE(jsonb_agg(row_value ORDER BY decision_date, suggestion_id), '[]'::jsonb)
  INTO v_result
  FROM (
    SELECT
      s.date AS decision_date,
      s.id AS suggestion_id,
      jsonb_build_object(
        'suggestion_id', s.id,
        'decision_date', s.date,
        'ticker', s.ticker,
        'bucket', s.bucket,
        'final_action', e.final_action,
        'confidence', s.confidence,
        'policy_version', e.policy_version,
        'decision_price', s.price_at_suggestion,
        'entry_zone_low', s.entry_zone_low,
        'entry_zone_high', s.entry_zone_high,
        'stop', s.stop,
        'target', s.target,
        'invalidation_price', s.invalidation_price,
        'completed_horizons', COALESCE(
          to_jsonb(array_agg(DISTINCT g.horizon_days ORDER BY g.horizon_days)
            FILTER (WHERE g.coverage_status = 'complete')),
          '[]'::jsonb
        )
      ) AS row_value
    FROM public.suggestions AS s
    JOIN public.decision_evaluations AS e ON e.id = s.evaluation_id
    LEFT JOIN public.suggestion_grades AS g ON g.suggestion_id = s.id
    WHERE s.decision_source = 'gateway'
      AND e.policy_version IS NOT NULL
      AND s.bucket IS NOT NULL
      AND s.confidence IS NOT NULL
      AND s.price_at_suggestion IS NOT NULL
      AND s.date >= CURRENT_DATE - 370
    GROUP BY s.id, s.date, s.ticker, s.bucket, e.final_action, s.confidence,
      e.policy_version, s.price_at_suggestion, s.entry_zone_low, s.entry_zone_high,
      s.stop, s.target, s.invalidation_price
    HAVING count(DISTINCT g.horizon_days)
      FILTER (WHERE g.coverage_status = 'complete' AND g.horizon_days IN (5,21,63)) < 3
    ORDER BY s.date, s.id
    LIMIT p_limit
  ) AS due;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.upsert_market_outcome_grades(p_grades JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  v_item JSONB;
  v_existing public.suggestion_grades%ROWTYPE;
  v_ticker TEXT;
  v_decision_price NUMERIC;
  v_policy_version INT;
  v_final_action TEXT;
  v_horizon INT;
  v_status TEXT;
  v_expected_benchmark TEXT;
  v_inserted INT := 0;
  v_updated INT := 0;
  v_incomplete INT := 0;
  v_numeric_key TEXT;
  v_had_existing BOOLEAN;
BEGIN
  IF jsonb_typeof(p_grades) <> 'array' OR jsonb_array_length(p_grades) > 150 THEN
    RAISE EXCEPTION 'invalid outcome grade batch' USING ERRCODE = '22023';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_grades)
  LOOP
    IF jsonb_typeof(v_item) <> 'object'
      OR NOT (v_item ?& ARRAY[
        'suggestion_id','horizon_days','horizon_sessions','coverage_status','benchmark_ticker',
        'stock_return_pct','benchmark_return_pct','excess_return_pct','mfe_pct','mae_pct',
        'entry_hit_at','stop_hit_at','target_hit_at','invalidation_hit_at','policy_version',
        'final_action','direction_success'
      ])
      OR (v_item - ARRAY[
        'suggestion_id','horizon_days','horizon_sessions','coverage_status','benchmark_ticker',
        'stock_return_pct','benchmark_return_pct','excess_return_pct','mfe_pct','mae_pct',
        'entry_hit_at','stop_hit_at','target_hit_at','invalidation_hit_at','policy_version',
        'final_action','direction_success'
      ]) <> '{}'::jsonb THEN
      RAISE EXCEPTION 'invalid outcome grade' USING ERRCODE = '22023';
    END IF;

    v_horizon := (v_item->>'horizon_days')::int;
    v_status := v_item->>'coverage_status';
    IF v_horizon NOT IN (5,21,63)
      OR (v_item->>'horizon_sessions')::int NOT BETWEEN 0 AND v_horizon
      OR v_status NOT IN ('incomplete','complete','missing_history','missing_benchmark','corporate_action_review')
      OR (v_status = 'complete' AND (v_item->>'horizon_sessions')::int <> v_horizon)
      OR v_item->>'benchmark_ticker' NOT IN ('VOO','VXUS')
      OR jsonb_typeof(v_item->'direction_success') NOT IN ('boolean','null') THEN
      RAISE EXCEPTION 'invalid outcome grade status' USING ERRCODE = '22023';
    END IF;

    FOREACH v_numeric_key IN ARRAY ARRAY[
      'stock_return_pct','benchmark_return_pct','excess_return_pct','mfe_pct','mae_pct'
    ]
    LOOP
      IF v_item->>v_numeric_key IS NOT NULL
        AND (v_item->>v_numeric_key !~ '^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$'
          OR char_length(v_item->>v_numeric_key) > 40) THEN
        RAISE EXCEPTION 'invalid outcome decimal' USING ERRCODE = '22023';
      END IF;
    END LOOP;

    SELECT s.ticker, s.price_at_suggestion, e.policy_version, e.final_action
    INTO v_ticker, v_decision_price, v_policy_version, v_final_action
    FROM public.suggestions AS s
    JOIN public.decision_evaluations AS e ON e.id = s.evaluation_id
    WHERE s.id = (v_item->>'suggestion_id')::bigint
      AND s.decision_source = 'gateway';
    IF NOT FOUND OR v_policy_version IS DISTINCT FROM (v_item->>'policy_version')::int
      OR v_final_action IS DISTINCT FROM v_item->>'final_action' THEN
      RAISE EXCEPTION 'outcome provenance mismatch' USING ERRCODE = '22023';
    END IF;
    v_expected_benchmark := CASE WHEN v_ticker = 'VXUS' THEN 'VXUS' ELSE 'VOO' END;
    IF v_item->>'benchmark_ticker' <> v_expected_benchmark THEN
      RAISE EXCEPTION 'outcome benchmark mismatch' USING ERRCODE = '22023';
    END IF;
    IF v_status = 'corporate_action_review' AND (
      v_item->>'entry_hit_at' IS NOT NULL OR v_item->>'stop_hit_at' IS NOT NULL
      OR v_item->>'target_hit_at' IS NOT NULL OR v_item->>'invalidation_hit_at' IS NOT NULL
    ) THEN
      RAISE EXCEPTION 'split outcome contains raw threshold result' USING ERRCODE = '22023';
    END IF;
    IF v_item->'direction_success' <> 'null'::jsonb AND (
      v_status <> 'complete' OR v_final_action NOT IN ('buy','add','reduce','sell')
    ) THEN
      RAISE EXCEPTION 'invalid directional outcome' USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock((v_item->>'suggestion_id')::bigint);
    SELECT * INTO v_existing FROM public.suggestion_grades
    WHERE suggestion_id = (v_item->>'suggestion_id')::bigint AND horizon_days = v_horizon
    FOR UPDATE;
    v_had_existing := FOUND;
    IF v_had_existing AND v_existing.coverage_status = 'complete' THEN
      CONTINUE;
    END IF;

    INSERT INTO public.suggestion_grades(
      suggestion_id, graded_at, result, price_then, horizon_days, benchmark_ticker,
      stock_return_pct, benchmark_return_pct, excess_return_pct, mfe_pct, mae_pct,
      entry_hit_at, stop_hit_at, target_hit_at, invalidation_hit_at, coverage_status,
      horizon_sessions, policy_version, final_action, direction_success, note
    ) VALUES (
      (v_item->>'suggestion_id')::bigint, now(),
      CASE WHEN v_item->'direction_success' = 'true'::jsonb THEN 'right'
           WHEN v_item->'direction_success' = 'false'::jsonb THEN 'wrong' ELSE NULL END,
      v_decision_price, v_horizon, v_expected_benchmark,
      NULLIF(v_item->>'stock_return_pct','')::numeric,
      NULLIF(v_item->>'benchmark_return_pct','')::numeric,
      NULLIF(v_item->>'excess_return_pct','')::numeric,
      NULLIF(v_item->>'mfe_pct','')::numeric,
      NULLIF(v_item->>'mae_pct','')::numeric,
      NULLIF(v_item->>'entry_hit_at','')::date,
      NULLIF(v_item->>'stop_hit_at','')::date,
      NULLIF(v_item->>'target_hit_at','')::date,
      NULLIF(v_item->>'invalidation_hit_at','')::date,
      v_status, (v_item->>'horizon_sessions')::int,
      v_policy_version, v_final_action,
      CASE WHEN v_item->'direction_success' = 'null'::jsonb THEN NULL
           ELSE (v_item->>'direction_success')::boolean END,
      'deterministic gateway outcome'
    )
    ON CONFLICT (suggestion_id, horizon_days) DO UPDATE SET
      graded_at = EXCLUDED.graded_at,
      result = EXCLUDED.result,
      price_then = EXCLUDED.price_then,
      benchmark_ticker = EXCLUDED.benchmark_ticker,
      stock_return_pct = EXCLUDED.stock_return_pct,
      benchmark_return_pct = EXCLUDED.benchmark_return_pct,
      excess_return_pct = EXCLUDED.excess_return_pct,
      mfe_pct = EXCLUDED.mfe_pct,
      mae_pct = EXCLUDED.mae_pct,
      entry_hit_at = EXCLUDED.entry_hit_at,
      stop_hit_at = EXCLUDED.stop_hit_at,
      target_hit_at = EXCLUDED.target_hit_at,
      invalidation_hit_at = EXCLUDED.invalidation_hit_at,
      coverage_status = EXCLUDED.coverage_status,
      horizon_sessions = EXCLUDED.horizon_sessions,
      policy_version = EXCLUDED.policy_version,
      final_action = EXCLUDED.final_action,
      direction_success = EXCLUDED.direction_success,
      note = EXCLUDED.note;
    IF NOT v_had_existing THEN v_inserted := v_inserted + 1;
    ELSE v_updated := v_updated + 1;
    END IF;
    IF v_status <> 'complete' THEN v_incomplete := v_incomplete + 1; END IF;
  END LOOP;
  RETURN jsonb_build_object(
    'inserted', v_inserted, 'updated', v_updated, 'incomplete', v_incomplete
  );
END;
$$;

REVOKE ALL ON FUNCTION public.get_due_market_decisions(INT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.upsert_market_outcome_grades(JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_due_market_decisions(INT) TO service_role;
GRANT EXECUTE ON FUNCTION public.upsert_market_outcome_grades(JSONB) TO service_role;
