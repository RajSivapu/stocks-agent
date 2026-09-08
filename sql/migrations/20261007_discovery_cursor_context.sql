-- Carry only validated terminal source cursors into a later protected collection run.
-- The source task/run provenance stays attached so a caller cannot invent a watermark.
CREATE OR REPLACE FUNCTION public.try_market_discovery_cursor_timestamp(p_value TEXT)
RETURNS TIMESTAMPTZ LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
  IF p_value IS NULL OR p_value !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
    RETURN NULL;
  END IF;
  RETURN p_value::timestamptz;
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
  RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION public.try_market_discovery_cursor_timestamp(TEXT)
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;

CREATE OR REPLACE FUNCTION public.read_market_discovery_cursor_context(
  p_run_id UUID,
  p_limit INT DEFAULT 100
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_now TIMESTAMPTZ:=statement_timestamp();
  v_result JSONB;
BEGIN
  IF p_run_id IS NULL OR p_limit NOT BETWEEN 1 AND 100 OR NOT EXISTS(
    SELECT 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running'
  ) THEN
    RAISE EXCEPTION 'invalid discovery cursor context request' USING ERRCODE='22023';
  END IF;

  WITH candidate AS (
    SELECT
      t.id AS source_task_id,
      t.run_id AS source_run_id,
      t.capability_id,
      t.provider,
      t.updated_at AS source_updated_at,
      t.result->'source_cursor' AS cursor,
      t.result->>'theme_id' AS theme_id,
      t.result->>'cursor_key' AS cursor_key
    FROM public.market_discovery_stage_tasks t
    JOIN public.analysis_runs a ON a.id=t.run_id AND a.status='completed'
    WHERE t.run_id<>p_run_id
      AND t.state='succeeded'
      AND t.updated_at<=v_now
      AND jsonb_typeof(t.result)='object'
      AND jsonb_typeof(t.result->'source_cursor')='object'
      AND (t.result->'source_cursor') ?& ARRAY[
        'provider','capability_id','completed_through','active_window_start',
        'active_window_end','backlog_token','page','accepted_item_ids','next_retry_phase'
      ]
      AND ((t.result->'source_cursor')-ARRAY[
        'provider','capability_id','completed_through','active_window_start',
        'active_window_end','backlog_token','page','accepted_item_ids','next_retry_phase'
      ])='{}'::jsonb
      AND t.result->'source_cursor'->>'provider'=t.provider
      AND t.result->'source_cursor'->>'capability_id'=t.capability_id
      AND (
        t.result->'theme_id'='null'::jsonb
        OR (
          jsonb_typeof(t.result->'theme_id')='string'
          AND t.result->>'theme_id' ~ '^[a-z][a-z0-9_]{2,79}$'
        )
      )
      AND t.result->>'cursor_key'=t.capability_id||':'||COALESCE(t.result->>'theme_id','default')
      AND octet_length(t.result->>'cursor_key')<=161
      AND (
        t.result->'source_cursor'->'completed_through'='null'::jsonb
        OR (
          jsonb_typeof(t.result->'source_cursor'->'completed_through')='string'
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'completed_through'
          ) IS NOT NULL
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'completed_through'
          )<=v_now
        )
      )
      AND (
        (
          t.result->'source_cursor'->'active_window_start'='null'::jsonb
          AND t.result->'source_cursor'->'active_window_end'='null'::jsonb
        ) OR (
          jsonb_typeof(t.result->'source_cursor'->'active_window_start')='string'
          AND jsonb_typeof(t.result->'source_cursor'->'active_window_end')='string'
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_start'
          ) IS NOT NULL
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_end'
          ) IS NOT NULL
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_start'
          )<=public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_end'
          )
          AND public.try_market_discovery_cursor_timestamp(
            t.result->'source_cursor'->>'active_window_end'
          )<=v_now
          AND (
            t.result->'source_cursor'->'completed_through'='null'::jsonb
            OR (
              public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'active_window_start'
              )<=public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'completed_through'
              )
              AND public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'completed_through'
              )<=public.try_market_discovery_cursor_timestamp(
                t.result->'source_cursor'->>'active_window_end'
              )
            )
          )
        )
      )
      AND (
        t.result->'source_cursor'->'backlog_token'='null'::jsonb
        OR (
          jsonb_typeof(t.result->'source_cursor'->'backlog_token')='string'
          AND octet_length(t.result->'source_cursor'->>'backlog_token') BETWEEN 1 AND 2048
          AND t.result->'source_cursor'->>'backlog_token' !~ '[[:cntrl:]]'
          AND t.result->'source_cursor'->'active_window_start'<>'null'::jsonb
        )
      )
      AND jsonb_typeof(t.result->'source_cursor'->'page')='number'
      AND CASE
        WHEN t.result->'source_cursor'->>'page' ~ '^(?:[1-9]|10)$'
        THEN (t.result->'source_cursor'->>'page')::int BETWEEN 1 AND 10
        ELSE false
      END
      AND (
        t.result->'source_cursor'->'backlog_token'='null'::jsonb
        OR (t.result->'source_cursor'->>'page')::int>=2
      )
      AND jsonb_typeof(t.result->'source_cursor'->'accepted_item_ids')='array'
      AND jsonb_array_length(t.result->'source_cursor'->'accepted_item_ids')<=500
      AND NOT EXISTS(
        SELECT 1
        FROM jsonb_array_elements(t.result->'source_cursor'->'accepted_item_ids') item
        WHERE jsonb_typeof(item)<>'string'
           OR octet_length(item#>>'{}') NOT BETWEEN 1 AND 512
           OR item#>>'{}' ~ '[[:cntrl:]]'
      )
      AND NOT EXISTS(
        SELECT 1
        FROM jsonb_array_elements_text(t.result->'source_cursor'->'accepted_item_ids') item(value)
        GROUP BY value HAVING count(*)>1
      )
      AND (
        t.result->'source_cursor'->'next_retry_phase'='null'::jsonb
        OR t.result->'source_cursor'->>'next_retry_phase' IN (
          'pre-market','intraday','post-market','on-demand'
        )
      )
      AND (
        t.result->'source_cursor'->'active_window_start'<>'null'::jsonb
        OR (
          t.result->'source_cursor'->'backlog_token'='null'::jsonb
          AND jsonb_array_length(t.result->'source_cursor'->'accepted_item_ids')=0
        )
      )
  ), ranked AS (
    SELECT candidate.*,
      row_number() OVER (
        PARTITION BY cursor_key ORDER BY source_updated_at DESC,source_task_id DESC
      ) AS cursor_rank
    FROM candidate
  ), selected AS (
    SELECT * FROM ranked WHERE cursor_rank=1
    ORDER BY cursor_key LIMIT p_limit
  )
  SELECT jsonb_build_object(
    'source_cursors',COALESCE((
      SELECT jsonb_agg(
        cursor||jsonb_build_object(
          'task_key',cursor_key,
          'source_run_id',source_run_id,
          'source_task_id',source_task_id,
          'source_updated_at',source_updated_at
        ) ORDER BY cursor_key
      ) FROM selected
    ),'[]'::jsonb),
    'last_completed_scans',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'capability_id',capability_id,
        'theme_id',COALESCE(theme_id,'default'),
        'completed_through',cursor->>'completed_through',
        'source_run_id',source_run_id,
        'source_task_id',source_task_id
      ) ORDER BY cursor_key)
      FROM selected WHERE cursor->'completed_through'<>'null'::jsonb
    ),'[]'::jsonb)
  ) INTO v_result;

  IF octet_length(v_result::text)>524288 THEN
    RAISE EXCEPTION 'discovery cursor context exceeds bound' USING ERRCODE='54000';
  END IF;
  RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.read_market_discovery_cursor_context(UUID,INT)
  FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.read_market_discovery_cursor_context(UUID,INT)
  TO service_role;
