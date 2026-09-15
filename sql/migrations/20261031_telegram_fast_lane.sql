BEGIN;

ALTER TABLE public.market_intelligence_runs
  ADD COLUMN IF NOT EXISTS lane TEXT;

ALTER TABLE public.market_intelligence_runs
  DISABLE TRIGGER market_intelligence_runs_append_only;
UPDATE public.market_intelligence_runs
SET lane=CASE WHEN phase='on-demand' THEN 'research' ELSE 'alert' END
WHERE lane IS NULL;
ALTER TABLE public.market_intelligence_runs
  ENABLE TRIGGER market_intelligence_runs_append_only;

ALTER TABLE public.market_intelligence_runs
  ALTER COLUMN lane SET NOT NULL;
ALTER TABLE public.market_intelligence_runs
  DROP CONSTRAINT IF EXISTS market_intelligence_runs_lane_check;
ALTER TABLE public.market_intelligence_runs
  ADD CONSTRAINT market_intelligence_runs_lane_check
  CHECK (lane IN ('alert','research'));
CREATE INDEX IF NOT EXISTS idx_market_intelligence_runs_lane_date
  ON public.market_intelligence_runs(lane,market_date DESC,created_at DESC);

CREATE OR REPLACE FUNCTION public.assign_market_intelligence_lane()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v_expected TEXT;
BEGIN
  v_expected:=CASE WHEN NEW.phase='on-demand' THEN 'research' ELSE 'alert' END;
  IF NEW.lane IS NULL THEN
    NEW.lane:=v_expected;
  ELSIF NEW.lane IS DISTINCT FROM v_expected THEN
    RAISE EXCEPTION 'intelligence lane does not match analysis phase'
      USING ERRCODE='22023';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS market_intelligence_runs_assign_lane
  ON public.market_intelligence_runs;
CREATE TRIGGER market_intelligence_runs_assign_lane
BEFORE INSERT ON public.market_intelligence_runs
FOR EACH ROW EXECUTE FUNCTION public.assign_market_intelligence_lane();

CREATE OR REPLACE FUNCTION public.start_market_analysis_run(
  p_request_id UUID, p_lease_token UUID, p_kind TEXT, p_market_date DATE
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_run public.analysis_runs%ROWTYPE;
  v_attempt INT:=1;
  v_research_market_date DATE;
BEGIN
  IF p_kind NOT IN ('pre-market','intraday','post-market','on-demand')
     OR p_market_date IS NULL THEN
    RAISE EXCEPTION 'invalid analysis run slot' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'start_run'
     OR v_request.lease_token<>p_lease_token OR v_request.status<>'claimed' THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE='40001';
  END IF;

  SELECT * INTO v_run FROM public.analysis_runs
  WHERE gateway_request_id=p_request_id;
  IF FOUND THEN
    IF v_run.kind IS DISTINCT FROM p_kind
       OR COALESCE(v_run.scheduled_market_date,p_market_date) IS DISTINCT FROM p_market_date THEN
      RAISE EXCEPTION 'run identity mismatch' USING ERRCODE='22023';
    END IF;
    UPDATE public.market_gateway_requests SET run_id=v_run.id
    WHERE request_id=p_request_id;
    IF p_kind='on-demand' THEN
      SELECT market_date INTO v_research_market_date
      FROM public.market_intelligence_runs WHERE id=v_run.id AND lane='research';
      RETURN jsonb_build_object(
        'run_id',v_run.id,'duplicate',true,
        'market_date',COALESCE(v_research_market_date,p_market_date)
      );
    END IF;
    RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
  END IF;

  IF p_kind<>'on-demand' THEN
    PERFORM pg_advisory_xact_lock(
      hashtextextended('scheduled-analysis-run:'||p_market_date::text||':'||p_kind,0)
    );
    SELECT * INTO v_run FROM public.analysis_runs
    WHERE scheduled_market_date=p_market_date AND scheduled_phase=p_kind
    ORDER BY scheduled_attempt DESC LIMIT 1 FOR UPDATE;
    IF FOUND THEN
      IF v_run.finished_at IS NOT NULL AND v_run.status IN ('partial','failed')
         AND v_run.scheduled_attempt=1 THEN
        v_attempt:=2;
      ELSE
        UPDATE public.market_gateway_requests SET run_id=v_run.id
        WHERE request_id=p_request_id;
        RETURN jsonb_build_object('run_id',v_run.id,'duplicate',true);
      END IF;
    END IF;
    INSERT INTO public.analysis_runs(
      kind,status,gateway_request_id,scheduled_market_date,scheduled_phase,scheduled_attempt
    ) VALUES(p_kind,'running',p_request_id,p_market_date,p_kind,v_attempt)
    RETURNING * INTO v_run;
  ELSE
    PERFORM pg_advisory_xact_lock(hashtextextended('research-analysis-run',0));
    SELECT analysis.* INTO v_run
    FROM public.analysis_runs analysis
    LEFT JOIN public.market_intelligence_runs intelligence ON intelligence.id=analysis.id
    WHERE analysis.kind='on-demand' AND analysis.status='running'
      AND (intelligence.id IS NULL OR intelligence.lane='research')
      AND (
        (intelligence.id IS NOT NULL
          AND intelligence.market_date BETWEEN p_market_date-5 AND p_market_date)
        OR (intelligence.id IS NULL
          AND analysis.started_at>=statement_timestamp()-interval '5 days')
      )
      AND NOT EXISTS(
        SELECT 1 FROM public.market_intelligence_run_events event
        WHERE event.run_id=analysis.id AND event.status IN ('completed','failed')
      )
      AND (
        NOT EXISTS(
          SELECT 1 FROM public.market_discovery_stage_tasks task
          WHERE task.run_id=analysis.id
        ) OR EXISTS(
          SELECT 1 FROM public.market_discovery_stage_tasks task
          WHERE task.run_id=analysis.id AND task.state IN ('planned','attempting')
        )
      )
    ORDER BY analysis.started_at,analysis.id LIMIT 1 FOR UPDATE OF analysis;
    IF FOUND THEN
      UPDATE public.market_gateway_requests SET run_id=v_run.id
      WHERE request_id=p_request_id;
      SELECT market_date INTO v_research_market_date
      FROM public.market_intelligence_runs WHERE id=v_run.id AND lane='research';
      RETURN jsonb_build_object(
        'run_id',v_run.id,'duplicate',true,
        'market_date',COALESCE(v_research_market_date,p_market_date)
      );
    END IF;
    INSERT INTO public.analysis_runs(kind,status,gateway_request_id)
    VALUES(p_kind,'running',p_request_id) RETURNING * INTO v_run;
  END IF;
  UPDATE public.market_gateway_requests SET run_id=v_run.id
  WHERE request_id=p_request_id;
  IF p_kind='on-demand' THEN
    RETURN jsonb_build_object(
      'run_id',v_run.id,'duplicate',false,'market_date',p_market_date
    );
  END IF;
  RETURN jsonb_build_object('run_id',v_run.id,'duplicate',false);
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_research_analysis_after_collection()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
  IF NEW.payload->>'status'='completed' AND EXISTS(
    SELECT 1 FROM public.market_intelligence_runs
    WHERE id=NEW.run_id AND lane='research'
  ) THEN
    UPDATE public.analysis_runs SET
      status='completed',finished_at=NEW.created_at,telegram_message_ids='[]'::jsonb
    WHERE id=NEW.run_id AND status='running';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS market_research_analysis_finish
  ON public.market_intelligence_collection_completions;
CREATE TRIGGER market_research_analysis_finish
AFTER INSERT ON public.market_intelligence_collection_completions
FOR EACH ROW EXECUTE FUNCTION public.finish_research_analysis_after_collection();

UPDATE public.analysis_runs analysis SET
  status='completed',
  finished_at=(
    SELECT completion.created_at
    FROM public.market_intelligence_collection_completions completion
    WHERE completion.run_id=analysis.id AND completion.payload->>'status'='completed'
    ORDER BY completion.created_at DESC,completion.completion_id DESC LIMIT 1
  ),
  telegram_message_ids='[]'::jsonb
WHERE analysis.status='running'
  AND EXISTS(
    SELECT 1 FROM public.market_intelligence_runs intelligence
    WHERE intelligence.id=analysis.id AND intelligence.lane='research'
  )
  AND EXISTS(
    SELECT 1 FROM public.market_intelligence_collection_completions completion
    WHERE completion.run_id=analysis.id AND completion.payload->>'status'='completed'
  );

CREATE OR REPLACE FUNCTION public.start_market_intelligence_run(
  p_run_id UUID,
  p_phase TEXT,
  p_market_date DATE,
  p_policy_version INT,
  p_reservation_plan JSONB,
  p_request_window JSONB,
  p_lane TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_expected TEXT; v_result JSONB; v_lane TEXT;
BEGIN
  v_expected:=CASE
    WHEN p_phase='on-demand' THEN 'research'
    WHEN p_phase IN ('pre-market','intraday','post-market') THEN 'alert'
    ELSE NULL
  END;
  IF p_lane NOT IN ('alert','research') OR p_lane IS DISTINCT FROM v_expected THEN
    RAISE EXCEPTION 'intelligence lane does not match analysis phase'
      USING ERRCODE='22023';
  END IF;
  v_result:=public.start_market_intelligence_run(
    p_run_id,p_phase,p_market_date,p_policy_version,p_reservation_plan,p_request_window
  );
  SELECT lane INTO v_lane
  FROM public.market_intelligence_runs WHERE id=p_run_id;
  IF v_lane IS DISTINCT FROM p_lane THEN
    RAISE EXCEPTION 'intelligence lane idempotency mismatch'
      USING ERRCODE='22023';
  END IF;
  RETURN v_result||jsonb_build_object('lane',v_lane);
END;
$$;

CREATE OR REPLACE FUNCTION public.read_latest_terminal_research_packet(
  p_alert_run_id UUID,
  p_market_date DATE,
  p_now TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_holidays JSONB;
BEGIN
  IF p_market_date IS NULL OR p_now IS NULL OR NOT EXISTS(
    SELECT 1 FROM public.market_intelligence_runs
    WHERE id=p_alert_run_id AND lane='alert' AND market_date=p_market_date
  ) THEN
    RAISE EXCEPTION 'alert intelligence lane unavailable' USING ERRCODE='22023';
  END IF;
  SELECT config->'nyse_holidays' INTO v_holidays
  FROM public.market_policy_config WHERE active;
  v_holidays:=COALESCE(v_holidays,'[]'::jsonb);
  SELECT jsonb_build_object(
    'packet_id',packet.id,
    'packet_hash',packet.packet_hash,
    'market_date',run.market_date,
    'created_at',packet.created_at,
    'age_days',p_market_date-run.market_date
  ) INTO v_result
  FROM public.market_intelligence_runs run
  JOIN public.analysis_runs analysis ON analysis.id=run.id AND analysis.status='completed'
  JOIN public.market_intelligence_run_events event
    ON event.run_id=run.id AND event.status='completed'
  JOIN public.market_evidence_packets packet
    ON packet.run_id=run.id AND packet.status='completed'
  WHERE run.lane='research'
    AND run.market_date<p_market_date
    AND run.market_date>=p_market_date-5
    AND extract(isodow FROM run.market_date) BETWEEN 1 AND 5
    AND NOT (v_holidays ? run.market_date::text)
    AND packet.created_at<=p_now
  ORDER BY run.market_date DESC,packet.created_at DESC,packet.id
  LIMIT 1;
  RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.enforce_alert_lane_publication()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v_run_id UUID; v_lane TEXT;
BEGIN
  IF TG_TABLE_NAME='market_reports' THEN
    v_run_id:=NEW.run_id;
  ELSIF TG_TABLE_NAME='market_report_publications' THEN
    SELECT run_id INTO v_run_id FROM public.market_reports WHERE id=NEW.report_id;
  ELSE
    v_run_id:=NEW.run_id;
  END IF;
  IF v_run_id IS NULL THEN RETURN NEW; END IF;
  SELECT lane INTO v_lane FROM public.market_intelligence_runs WHERE id=v_run_id;
  IF v_lane='research' THEN
    RAISE EXCEPTION 'research intelligence cannot publish'
      USING ERRCODE='22023';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS market_reports_alert_lane_only ON public.market_reports;
CREATE TRIGGER market_reports_alert_lane_only
BEFORE INSERT OR UPDATE ON public.market_reports
FOR EACH ROW EXECUTE FUNCTION public.enforce_alert_lane_publication();

DROP TRIGGER IF EXISTS market_report_publications_alert_lane_only
  ON public.market_report_publications;
CREATE TRIGGER market_report_publications_alert_lane_only
BEFORE INSERT OR UPDATE ON public.market_report_publications
FOR EACH ROW EXECUTE FUNCTION public.enforce_alert_lane_publication();

DROP TRIGGER IF EXISTS market_publications_alert_lane_only
  ON public.market_publications;
CREATE TRIGGER market_publications_alert_lane_only
BEFORE INSERT OR UPDATE ON public.market_publications
FOR EACH ROW EXECUTE FUNCTION public.enforce_alert_lane_publication();

REVOKE ALL ON FUNCTION public.start_market_intelligence_run(
  UUID,TEXT,DATE,INT,JSONB,JSONB,TEXT
) FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
  stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.start_market_intelligence_run(
  UUID,TEXT,DATE,INT,JSONB,JSONB,TEXT
) TO service_role;

REVOKE ALL ON FUNCTION public.finish_research_analysis_after_collection()
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,
  stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON FUNCTION public.assign_market_intelligence_lane(),
  public.enforce_alert_lane_publication()
  FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,
  stock_agent_release_reader,stock_agent_release_reader_runtime;

REVOKE ALL ON FUNCTION public.read_latest_terminal_research_packet(
  UUID,DATE,TIMESTAMPTZ
) FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
  stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.read_latest_terminal_research_packet(UUID,DATE,TIMESTAMPTZ) TO service_role;

COMMIT;
