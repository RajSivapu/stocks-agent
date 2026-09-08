-- Tighten official-source completion and cross-run cursor provenance.
-- This migration is additive; reviewed migrations through 20261007 remain immutable.
CREATE OR REPLACE FUNCTION public.record_market_intelligence_provider_v2(
  p_run_id UUID, p_completion_id UUID, p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_row JSONB;
  v_legacy_items JSONB;
  v_result JSONB;
  v_request_host TEXT;
  v_valid_request_host BOOLEAN;
BEGIN
  IF p_run_id IS NULL OR p_completion_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR jsonb_typeof(p_payload->'items')<>'array' THEN
    RAISE EXCEPTION 'invalid intelligence completion payload' USING ERRCODE='22023';
  END IF;
  FOR v_row IN SELECT value FROM jsonb_array_elements(p_payload->'items') LOOP
    IF jsonb_typeof(v_row)<>'object'
       OR NOT (v_row ?& ARRAY[
         'id','run_item_id','receipt_id','provider','upstream_item_id','canonical_url','request_url',
         'published_at','retrieved_at','effective_at','reporting_at','entity_ids','security_ids',
         'discovery_status','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ])
       OR (v_row - ARRAY[
         'id','run_item_id','receipt_id','provider','upstream_item_id','canonical_url','request_url',
         'published_at','retrieved_at','effective_at','reporting_at','entity_ids','security_ids',
         'discovery_status','title','normalized_text','canonical_content','content_hash','metadata','disposition','drop_reason'
       ]) <> '{}'::jsonb
       OR COALESCE(char_length(v_row->>'upstream_item_id'),0) NOT BETWEEN 1 AND 512
       OR COALESCE(char_length(v_row->>'canonical_url'),0) NOT BETWEEN 1 AND 2048
       OR COALESCE(char_length(v_row->>'request_url'),0) NOT BETWEEN 1 AND 2048
       OR v_row->>'canonical_url' !~ '^https://' OR v_row->>'request_url' !~ '^https://'
       OR lower(v_row->>'request_url') ~ '(api[_-]?key|token|secret|password)='
       OR jsonb_typeof(v_row->'entity_ids')<>'array' OR jsonb_array_length(v_row->'entity_ids')>32
       OR jsonb_typeof(v_row->'security_ids')<>'array' OR jsonb_array_length(v_row->'security_ids')>32
       OR v_row->>'discovery_status' NOT IN ('qualified','no_event','insufficient_coverage')
       OR (v_row->>'discovery_status'='qualified' AND jsonb_array_length(v_row->'security_ids')=0) THEN
      RAISE EXCEPTION 'invalid provider evidence provenance' USING ERRCODE='22023';
    END IF;
    v_request_host := lower(substring(v_row->>'request_url' FROM '^https://([^/:?#]+)'));
    v_valid_request_host := CASE v_row->>'provider'
      WHEN 'gdelt' THEN v_request_host='api.gdeltproject.org'
      WHEN 'alpha_vantage' THEN v_request_host='www.alphavantage.co'
      WHEN 'finnhub' THEN v_request_host='finnhub.io'
      WHEN 'yahoo' THEN v_request_host='query1.finance.yahoo.com'
      WHEN 'sec_edgar' THEN v_request_host IN ('www.sec.gov','data.sec.gov')
      WHEN 'federal_register' THEN v_request_host='www.federalregister.gov'
      WHEN 'white_house' THEN v_request_host='www.whitehouse.gov'
      WHEN 'doe' THEN v_request_host='www.energy.gov'
      WHEN 'dod' THEN v_row->>'request_url' ~ '^https://www\.(defense|war)\.gov/DesktopModules/ArticleCS/RSS\.ashx\?ContentType=(1|9)&Site=945&max=10$'
      WHEN 'eia' THEN v_request_host IN ('api.eia.gov','www.eia.gov')
      WHEN 'fred' THEN v_request_host IN ('api.stlouisfed.org','fred.stlouisfed.org')
      WHEN 'bls' THEN v_request_host IN ('api.bls.gov','www.bls.gov')
      WHEN 'bea' THEN v_request_host IN ('apps.bea.gov','www.bea.gov')
      WHEN 'social' THEN v_request_host IN ('www.reddit.com','oauth.reddit.com')
      ELSE false END;
    IF NOT v_valid_request_host THEN
      RAISE EXCEPTION 'provider request URL host mismatch' USING ERRCODE='22023';
    END IF;
    IF v_row->>'provider'='dod' AND NOT (
      (
        v_row->>'request_url' ~ 'ContentType=9&Site=945&max=10$'
        AND v_row->>'canonical_url' ~ '^https://www\.(defense|war)\.gov/News/(Releases|Contracts)/[^?#]+$'
      ) OR (
        v_row->>'request_url' ~ 'ContentType=1&Site=945&max=10$'
        AND v_row->>'canonical_url' ~ '^https://www\.(defense|war)\.gov/News/(News-Stories|Features|Releases)/[^?#]+$'
      )
    ) THEN
      RAISE EXCEPTION 'provider item URL path mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;

  SELECT jsonb_agg(
    ((value - ARRAY['provider','request_url','retrieved_at','reporting_at','entity_ids','security_ids','discovery_status'])
      || jsonb_build_object(
        'canonical_url',
        CASE WHEN value->>'provider'='dod'
          THEN regexp_replace(value->>'request_url','^https://www\.war\.gov','https://www.defense.gov')
          ELSE value->>'request_url'
        END
      ) || CASE WHEN value->>'disposition'='near_duplicate' THEN jsonb_build_object('disposition','accepted','drop_reason',NULL) ELSE '{}'::jsonb END)
  ) INTO v_legacy_items FROM jsonb_array_elements(p_payload->'items');
  v_result := public.record_market_intelligence_legacy(
    p_run_id, p_completion_id, jsonb_set(p_payload, '{items}', COALESCE(v_legacy_items,'[]'::jsonb))
  );
  INSERT INTO public.market_source_item_provenance(
    source_item_id,provider,canonical_item_url,request_url,retrieved_at,reporting_at,
    entity_ids,security_ids,discovery_status
  )
  SELECT (value->>'id')::uuid,value->>'provider',value->>'canonical_url',value->>'request_url',
    (value->>'retrieved_at')::timestamptz,(value->>'reporting_at')::timestamptz,
    value->'entity_ids',value->'security_ids',value->>'discovery_status'
  FROM jsonb_array_elements(p_payload->'items')
  ON CONFLICT (source_item_id) DO NOTHING;
  RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.record_market_intelligence_provider_v2(UUID,UUID,JSONB)
  FROM PUBLIC,anon,authenticated,service_role;


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
  v_consuming_started_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  SELECT started_at INTO v_consuming_started_at
  FROM public.analysis_runs WHERE id=p_run_id AND status='running';
  IF p_run_id IS NULL OR p_limit NOT BETWEEN 1 AND 100
     OR v_consuming_started_at IS NULL OR v_consuming_started_at>v_now THEN
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
      AND a.finished_at IS NOT NULL AND a.finished_at<v_consuming_started_at
    WHERE t.run_id<>p_run_id
      AND t.state='succeeded'
      AND t.updated_at<=a.finished_at
      AND jsonb_typeof(t.result)='object'
      AND jsonb_typeof(t.result->'source_cursor')='object'
      AND (t.result->'source_cursor') ?& ARRAY[
        'provider','capability_id','completed_through','active_window_start',
        'active_window_end','backlog_token','page','accepted_item_ids','next_retry_phase'
      ]
      AND ((t.result->'source_cursor')-ARRAY[
        'provider','capability_id','completed_through','active_window_start',
        'active_window_end','backlog_token','page','accepted_item_ids','next_retry_phase',
        'continuation_token_history'
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
          )<=a.finished_at
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
          )<=a.finished_at
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
        WHEN t.result->'source_cursor'->>'page' ~ '^[0-9]{1,4}$'
        THEN (t.result->'source_cursor'->>'page')::int BETWEEN 1 AND
          CASE WHEN t.capability_id='white_house_sitemap' THEN 2001 ELSE 10 END
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
        NOT (t.result->'source_cursor' ? 'continuation_token_history') OR (
          jsonb_typeof(t.result->'source_cursor'->'continuation_token_history')='array'
          AND jsonb_array_length(t.result->'source_cursor'->'continuation_token_history')<=64
          AND NOT EXISTS(
            SELECT 1 FROM jsonb_array_elements(
              t.result->'source_cursor'->'continuation_token_history'
            ) identity
            WHERE jsonb_typeof(identity)<>'string'
               OR identity#>>'{}' !~ '^[0-9a-f]{64}$'
          )
          AND NOT EXISTS(
            SELECT 1 FROM jsonb_array_elements_text(
              t.result->'source_cursor'->'continuation_token_history'
            ) identity(value) GROUP BY value HAVING count(*)>1
          )
          AND (
            t.result->'source_cursor'->'backlog_token'='null'::jsonb
            OR encode(extensions.digest(convert_to(
              t.result->'source_cursor'->>'backlog_token','UTF8'
            ),'sha256'),'hex') IN (
              SELECT value FROM jsonb_array_elements_text(
                t.result->'source_cursor'->'continuation_token_history'
              ) identity(value)
            )
          )
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
