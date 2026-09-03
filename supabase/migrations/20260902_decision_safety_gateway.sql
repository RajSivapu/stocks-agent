-- Deterministic market-decision safety gateway, audit ledger, and transactional outbox.
-- Supabase CLI migration; replay-safe on the captured legacy baseline.
-- Additive and idempotent. Apply only after 20260901_reliable_stock_agent.sql.

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE TABLE IF NOT EXISTS public.market_gateway_requests (
  request_id UUID PRIMARY KEY,
  operation TEXT NOT NULL CHECK (operation IN (
    'start_run','read_context','record_artifacts','grade_due_decisions',
    'evaluate_and_publish','finish_run'
  )),
  run_id UUID REFERENCES public.analysis_runs(id) ON DELETE SET NULL,
  status TEXT NOT NULL CHECK (status IN ('claimed','completed','failed')),
  lease_token UUID NOT NULL,
  attempt_count INT NOT NULL DEFAULT 1 CHECK (attempt_count > 0),
  response JSONB CHECK (response IS NULL OR octet_length(response::text) <= 524288),
  response_digest TEXT CHECK (response_digest IS NULL OR response_digest ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS public.market_policy_config (
  version INT PRIMARY KEY CHECK (version > 0),
  config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
  active BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  activated_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_market_policy
  ON public.market_policy_config ((active)) WHERE active;

CREATE TABLE IF NOT EXISTS public.decision_evaluations (
  id UUID PRIMARY KEY,
  request_id UUID REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  run_id UUID REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  candidate_id UUID NOT NULL,
  policy_version INT REFERENCES public.market_policy_config(version),
  input_digest TEXT NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
  raw_action TEXT NOT NULL CHECK (raw_action IN (
    'buy','add','hold','reduce','sell','watch','avoid'
  )),
  final_action TEXT CHECK (final_action IS NULL OR final_action IN (
    'buy','add','hold','reduce','sell','watch','avoid'
  )),
  policy_status TEXT NOT NULL CHECK (policy_status IN (
    'approved','downgraded','vetoed','legacy_unverified'
  )),
  reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
  explanations JSONB NOT NULL DEFAULT '[]'::jsonb,
  normalized JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  analyst JSONB NOT NULL DEFAULT '{}'::jsonb,
  checker JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (
    (policy_status = 'legacy_unverified' AND request_id IS NULL AND run_id IS NULL
      AND policy_version IS NULL)
    OR
    (policy_status <> 'legacy_unverified' AND request_id IS NOT NULL AND run_id IS NOT NULL
      AND policy_version IS NOT NULL)
  ),
  UNIQUE(request_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS public.market_publications (
  id UUID PRIMARY KEY,
  idempotency_key UUID NOT NULL UNIQUE
    REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  run_id UUID REFERENCES public.analysis_runs(id) ON DELETE RESTRICT,
  market_date DATE NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('pre-market','intraday','post-market','on-demand')),
  kind TEXT NOT NULL CHECK (kind IN (
    'brief','new_idea','entry_trigger','stop_near','stop_breach','target_near',
    'target_hit','thesis_break','data_warning','holiday'
  )),
  template_version INT NOT NULL CHECK (template_version > 0),
  rendered_body TEXT NOT NULL CHECK (char_length(rendered_body) <= 14000),
  rendered_hash TEXT NOT NULL CHECK (rendered_hash ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status IN (
    'ready','sending','delivered','delivery_failed','delivery_unknown','suppressed'
  )),
  telegram_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  lease_token UUID,
  sending_started_at TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ,
  error TEXT CHECK (error IS NULL OR char_length(error) <= 1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS one_market_publication_per_run
  ON public.market_publications (run_id) WHERE run_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS one_holiday_publication_per_market_date
  ON public.market_publications (market_date, phase, kind) WHERE kind = 'holiday';

ALTER TABLE public.analysis_runs ADD COLUMN IF NOT EXISTS gateway_request_id UUID UNIQUE
  REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT;
ALTER TABLE public.daily_snapshots ADD COLUMN IF NOT EXISTS run_id UUID
  REFERENCES public.analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE public.lessons ADD COLUMN IF NOT EXISTS run_id UUID
  REFERENCES public.analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE public.radar ADD COLUMN IF NOT EXISTS updated_run_id UUID
  REFERENCES public.analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE public.paper_watches ADD COLUMN IF NOT EXISTS opened_run_id UUID
  REFERENCES public.analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE public.paper_watches ADD COLUMN IF NOT EXISTS closed_run_id UUID
  REFERENCES public.analysis_runs(id) ON DELETE SET NULL;
ALTER TABLE public.suggestions ADD COLUMN IF NOT EXISTS invalidation_price NUMERIC;
ALTER TABLE public.suggestions ADD COLUMN IF NOT EXISTS evaluation_id UUID;
ALTER TABLE public.suggestions
  ADD COLUMN IF NOT EXISTS decision_source TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE public.suggestions
  ADD COLUMN IF NOT EXISTS decision_mode TEXT NOT NULL DEFAULT 'discretionary';

-- Fail closed before changing historical labels. No unrecognized legacy row is guessed.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM public.suggestions
    WHERE lower(trim(action)) NOT IN (
      'buy','add','add slowly','add/dca','dca','dca/add slowly',
      'hold','hold/wait','reduce','trim','sell','exit','study','watch',
      'watch - alert at $285','watch - alert at $610','watch/add on pullback','avoid'
    )
       OR (confidence IS NOT NULL AND lower(trim(confidence)) NOT IN (
         'low','low-medium','medium','medium-high','high'
       ))
       OR (bucket IS NOT NULL AND lower(trim(bucket)) NOT IN ('core','growth','speculative'))
  ) THEN
    RAISE EXCEPTION 'legacy suggestion preflight failed';
  END IF;
END;
$$;

UPDATE public.suggestions
SET action = CASE
  WHEN lower(trim(action)) IN ('add slowly','add/dca','dca','dca/add slowly') THEN 'add'
  WHEN lower(trim(action)) = 'hold/wait' THEN 'hold'
  WHEN lower(trim(action)) IN ('study','watch - alert at $285','watch - alert at $610','watch/add on pullback') THEN 'watch'
  ELSE CASE lower(trim(action))
    WHEN 'trim' THEN 'reduce'
    WHEN 'exit' THEN 'sell'
    ELSE lower(trim(action))
  END
END,
confidence = CASE
  WHEN confidence IS NULL THEN NULL
  WHEN lower(trim(confidence)) IN ('low-medium','medium-high') THEN 'medium'
  ELSE lower(trim(confidence))
END,
bucket = CASE WHEN bucket IS NULL THEN NULL ELSE lower(trim(bucket)) END;

UPDATE public.suggestions
SET evaluation_id = gen_random_uuid()
WHERE evaluation_id IS NULL;

INSERT INTO public.decision_evaluations (
  id, request_id, run_id, candidate_id, policy_version, input_digest,
  raw_action, final_action, policy_status, reason_codes, explanations,
  normalized, evidence, analyst, checker
)
SELECT
  s.evaluation_id, NULL, NULL, gen_random_uuid(), NULL,
  encode(extensions.digest(to_jsonb(s)::text, 'sha256'), 'hex'),
  s.action, s.action, 'legacy_unverified',
  '["LEGACY_UNVERIFIED"]'::jsonb,
  '["Historical suggestion imported without deterministic gateway review."]'::jsonb,
  jsonb_build_object('ticker', s.ticker, 'suggestion_id', s.id),
  '[]'::jsonb, '{}'::jsonb, '{}'::jsonb
FROM public.suggestions AS s
LEFT JOIN public.decision_evaluations AS e ON e.id = s.evaluation_id
WHERE e.id IS NULL;

ALTER TABLE public.suggestions
  DROP CONSTRAINT IF EXISTS suggestions_gateway_actionable_complete;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_evaluation_id_fkey') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_evaluation_id_fkey
      FOREIGN KEY (evaluation_id) REFERENCES public.decision_evaluations(id) ON DELETE RESTRICT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_action_canonical') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_action_canonical
      CHECK (action IN ('buy','add','hold','reduce','sell','watch','avoid'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_confidence_canonical') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_confidence_canonical
      CHECK (confidence IS NULL OR confidence IN ('low','medium','high'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_bucket_canonical') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_bucket_canonical
      CHECK (bucket IS NULL OR bucket IN ('core','growth','speculative'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_decision_source_canonical') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_decision_source_canonical
      CHECK (decision_source IN ('legacy','gateway'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_decision_mode_canonical') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_decision_mode_canonical
      CHECK (decision_mode IN ('discretionary','owner_plan'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = 'suggestions_gateway_actionable_complete') THEN
    ALTER TABLE public.suggestions ADD CONSTRAINT suggestions_gateway_actionable_complete CHECK (
      decision_source = 'legacy' OR action NOT IN ('buy','add')
      OR (decision_mode = 'owner_plan' AND bucket = 'core') OR (
        bucket IS NOT NULL AND confidence IS NOT NULL AND entry_zone_low > 0
        AND entry_zone_high >= entry_zone_low AND stop > 0 AND target > entry_zone_high
        AND valid_until IS NOT NULL AND price_at_suggestion > 0
        AND evidence_as_of IS NOT NULL AND invalidation_price > 0
      )
    );
  END IF;
END;
$$;
ALTER TABLE public.suggestions ALTER COLUMN evaluation_id SET NOT NULL;

CREATE OR REPLACE FUNCTION public.reject_decision_evaluation_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'decision evaluations are append-only' USING ERRCODE = '55000';
END;
$$;
DROP TRIGGER IF EXISTS decision_evaluations_append_only ON public.decision_evaluations;
CREATE TRIGGER decision_evaluations_append_only
BEFORE UPDATE OR DELETE ON public.decision_evaluations
FOR EACH ROW EXECUTE FUNCTION public.reject_decision_evaluation_mutation();

ALTER TABLE public.market_gateway_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_policy_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decision_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_publications ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.activate_market_policy_config(p_version INT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_config JSONB;
BEGIN
  SELECT config INTO v_config FROM public.market_policy_config
  WHERE version = p_version FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'policy version unavailable' USING ERRCODE = '22023'; END IF;
  UPDATE public.market_policy_config SET active = false, activated_at = NULL WHERE active;
  UPDATE public.market_policy_config SET active = true, activated_at = now() WHERE version = p_version;
  RETURN jsonb_build_object('version', p_version, 'active', true);
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_market_gateway_request(
  p_request_id UUID, p_operation TEXT, p_run_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_lease UUID;
BEGIN
  IF p_operation NOT IN ('start_run','read_context','record_artifacts','grade_due_decisions','evaluate_and_publish','finish_run')
     OR (p_operation = 'start_run' AND p_run_id IS NOT NULL)
     OR (p_operation <> 'start_run' AND p_run_id IS NULL) THEN
    RAISE EXCEPTION 'invalid request identity' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id = p_request_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('market_gateway_rate', 0));
    IF (SELECT count(*) FROM public.market_gateway_requests WHERE created_at >= now()-interval '1 hour') >= 100
       OR (p_run_id IS NOT NULL AND (SELECT count(*) FROM public.market_gateway_requests WHERE run_id=p_run_id) >= 20) THEN
      RAISE EXCEPTION 'gateway rate limit exceeded' USING ERRCODE = '54000';
    END IF;
    v_lease := gen_random_uuid();
    INSERT INTO public.market_gateway_requests(request_id, operation, run_id, status, lease_token)
    VALUES (p_request_id, p_operation, p_run_id, 'claimed', v_lease);
    RETURN jsonb_build_object('claimed', true, 'lease_token', v_lease, 'attempt_count', 1);
  END IF;
  IF v_request.operation <> p_operation
     OR (p_operation <> 'start_run' AND v_request.run_id IS DISTINCT FROM p_run_id) THEN
    RAISE EXCEPTION 'request identity mismatch' USING ERRCODE = '22023';
  END IF;
  IF v_request.status IN ('completed','failed') THEN
    RETURN jsonb_build_object('claimed', false, 'status', v_request.status,
      'response', v_request.response, 'response_digest', v_request.response_digest);
  END IF;
  IF v_request.claimed_at > now() - interval '5 minutes' THEN
    RETURN jsonb_build_object('claimed', false, 'status', 'REQUEST_IN_PROGRESS');
  END IF;
  v_lease := gen_random_uuid();
  UPDATE public.market_gateway_requests SET lease_token = v_lease, claimed_at = now(),
    attempt_count = attempt_count + 1 WHERE request_id = p_request_id;
  RETURN jsonb_build_object('claimed', true, 'lease_token', v_lease,
    'attempt_count', v_request.attempt_count + 1);
END;
$$;

CREATE OR REPLACE FUNCTION public.complete_market_gateway_request(
  p_request_id UUID, p_lease_token UUID, p_status TEXT, p_response JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_digest TEXT;
BEGIN
  IF p_status NOT IN ('completed','failed') OR p_response IS NULL
     OR octet_length(p_response::text) > 524288 THEN
    RAISE EXCEPTION 'invalid request completion' USING ERRCODE = '22023';
  END IF;
  v_digest := encode(extensions.digest(p_response::text, 'sha256'), 'hex');
  UPDATE public.market_gateway_requests SET status = p_status, response = p_response,
    response_digest = v_digest, finished_at = now()
  WHERE request_id = p_request_id AND lease_token = p_lease_token AND status = 'claimed';
  IF NOT FOUND THEN RAISE EXCEPTION 'request lease unavailable' USING ERRCODE = '40001'; END IF;
  RETURN jsonb_build_object('status', p_status, 'response_digest', v_digest);
END;
$$;

CREATE OR REPLACE FUNCTION public.start_market_analysis_run(
  p_request_id UUID, p_lease_token UUID, p_kind TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_request public.market_gateway_requests%ROWTYPE; v_run_id UUID; v_existing_kind TEXT;
BEGIN
  IF p_kind NOT IN ('pre-market','intraday','post-market','on-demand') THEN
    RAISE EXCEPTION 'invalid run kind' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id = p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation <> 'start_run' OR v_request.lease_token <> p_lease_token
     OR v_request.status <> 'claimed' THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE = '40001';
  END IF;
  SELECT id, kind INTO v_run_id, v_existing_kind FROM public.analysis_runs
  WHERE gateway_request_id = p_request_id;
  IF v_run_id IS NULL THEN
    INSERT INTO public.analysis_runs(kind, status, gateway_request_id)
    VALUES (p_kind, 'running', p_request_id) RETURNING id INTO v_run_id;
  ELSIF v_existing_kind IS DISTINCT FROM p_kind THEN
    RAISE EXCEPTION 'run kind mismatch' USING ERRCODE = '22023';
  END IF;
  UPDATE public.market_gateway_requests SET run_id = v_run_id WHERE request_id = p_request_id;
  RETURN jsonb_build_object('run_id', v_run_id, 'kind', p_kind);
END;
$$;

CREATE OR REPLACE FUNCTION public.apply_market_artifacts(
  p_request_id UUID, p_run_id UUID, p_lease_token UUID, p_mutations JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE; v_item JSONB; v_kind TEXT;
  v_obs INT := 0; v_snap INT := 0; v_lesson INT := 0; v_radar_up INT := 0;
  v_radar_del INT := 0; v_watch_open INT := 0; v_watch_close INT := 0;
  v_watch_ids JSONB := '[]'::jsonb; v_watch_id BIGINT; v_rows INT; v_receipt JSONB;
BEGIN
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id = p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation <> 'record_artifacts' OR v_request.run_id <> p_run_id
     OR v_request.lease_token <> p_lease_token OR v_request.status <> 'claimed' THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE = '40001';
  END IF;
  PERFORM 1 FROM public.analysis_runs WHERE id = p_run_id AND status = 'running' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE = '22023'; END IF;
  IF jsonb_typeof(p_mutations) <> 'array' OR jsonb_array_length(p_mutations) > 100 THEN
    RAISE EXCEPTION 'invalid artifact batch' USING ERRCODE = '22023';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_mutations) LOOP
    IF jsonb_typeof(v_item) <> 'object' OR NOT (v_item ? 'kind') THEN
      RAISE EXCEPTION 'invalid artifact mutation' USING ERRCODE = '22023';
    END IF;
    v_kind := v_item->>'kind';
    IF v_kind = 'observation' THEN
      IF NOT (v_item ?& ARRAY['kind','ticker','obs_date','event_type','summary','price_reaction','confidence','source'])
         OR (v_item - ARRAY['kind','ticker','obs_date','event_type','summary','price_reaction','confidence','source']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid observation mutation' USING ERRCODE = '22023';
      END IF;
      INSERT INTO public.stock_observations(ticker, obs_date, event_type, summary, price_reaction, confidence, source, run_id)
      VALUES (v_item->>'ticker', (v_item->>'obs_date')::date, v_item->>'event_type', v_item->>'summary',
        v_item->>'price_reaction', v_item->>'confidence', v_item->>'source', p_run_id);
      v_obs := v_obs + 1;
    ELSIF v_kind = 'snapshot' THEN
      IF NOT (v_item ?& ARRAY['kind','snap_date','ticker','close','day_move_pct','rsi14','sma50','sma200','macd_hist'])
         OR (v_item - ARRAY['kind','snap_date','ticker','close','day_move_pct','rsi14','sma50','sma200','macd_hist']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid snapshot mutation' USING ERRCODE = '22023';
      END IF;
      INSERT INTO public.daily_snapshots(snap_date,ticker,close,day_move_pct,rsi14,sma50,sma200,macd_hist,run_id)
      VALUES ((v_item->>'snap_date')::date,v_item->>'ticker',(v_item->>'close')::numeric,
        (v_item->>'day_move_pct')::numeric,(v_item->>'rsi14')::numeric,(v_item->>'sma50')::numeric,
        (v_item->>'sma200')::numeric,(v_item->>'macd_hist')::numeric,p_run_id)
      ON CONFLICT (snap_date,ticker) DO UPDATE SET close=EXCLUDED.close,day_move_pct=EXCLUDED.day_move_pct,
        rsi14=EXCLUDED.rsi14,sma50=EXCLUDED.sma50,sma200=EXCLUDED.sma200,
        macd_hist=EXCLUDED.macd_hist,run_id=EXCLUDED.run_id;
      v_snap := v_snap + 1;
    ELSIF v_kind = 'lesson' THEN
      IF NOT (v_item ?& ARRAY['kind','entry_date','category','content'])
         OR (v_item - ARRAY['kind','entry_date','category','content']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid lesson mutation' USING ERRCODE = '22023';
      END IF;
      INSERT INTO public.lessons(entry_date,category,content,run_id)
      VALUES ((v_item->>'entry_date')::date,v_item->>'category',v_item->>'content',p_run_id);
      v_lesson := v_lesson + 1;
    ELSIF v_kind = 'radar_upsert' THEN
      IF NOT (v_item ?& ARRAY['kind','ticker','added','last_seen','days_relevant','reason','bucket_guess','promoted','promoted_on'])
         OR (v_item - ARRAY['kind','ticker','added','last_seen','days_relevant','reason','bucket_guess','promoted','promoted_on']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid radar mutation' USING ERRCODE = '22023';
      END IF;
      INSERT INTO public.radar(ticker,added,last_seen,days_relevant,reason,bucket_guess,promoted,promoted_on,updated_run_id)
      VALUES (v_item->>'ticker',(v_item->>'added')::date,(v_item->>'last_seen')::date,
        (v_item->>'days_relevant')::int,v_item->>'reason',v_item->>'bucket_guess',
        (v_item->>'promoted')::boolean,(v_item->>'promoted_on')::date,p_run_id)
      ON CONFLICT (ticker) DO UPDATE SET last_seen=EXCLUDED.last_seen,days_relevant=EXCLUDED.days_relevant,
        reason=EXCLUDED.reason,bucket_guess=EXCLUDED.bucket_guess,promoted=EXCLUDED.promoted,
        promoted_on=EXCLUDED.promoted_on,updated_run_id=EXCLUDED.updated_run_id;
      v_radar_up := v_radar_up + 1;
    ELSIF v_kind = 'radar_delete' THEN
      IF NOT (v_item ?& ARRAY['kind','ticker']) OR (v_item - ARRAY['kind','ticker']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid radar delete mutation' USING ERRCODE = '22023';
      END IF;
      DELETE FROM public.radar WHERE ticker = v_item->>'ticker';
      v_radar_del := v_radar_del + 1;
    ELSIF v_kind = 'paper_watch_create' THEN
      IF NOT (v_item ?& ARRAY['kind','ticker','created','entry_ref_price','target_price','hypothetical_amount','thesis','horizon','agent_view_at_open','agent_score_at_open'])
         OR (v_item - ARRAY['kind','ticker','created','entry_ref_price','target_price','hypothetical_amount','thesis','horizon','agent_view_at_open','agent_score_at_open']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid paper watch create mutation' USING ERRCODE = '22023';
      END IF;
      INSERT INTO public.paper_watches(ticker,created,entry_ref_price,target_price,hypothetical_amount,
        thesis,horizon,status,agent_view_at_open,agent_score_at_open,opened_run_id)
      VALUES (v_item->>'ticker',(v_item->>'created')::date,(v_item->>'entry_ref_price')::numeric,
        (v_item->>'target_price')::numeric,(v_item->>'hypothetical_amount')::numeric,
        v_item->>'thesis',v_item->>'horizon','active',v_item->>'agent_view_at_open',
        (v_item->>'agent_score_at_open')::int,p_run_id) RETURNING id INTO v_watch_id;
      v_watch_ids := v_watch_ids || jsonb_build_array(v_watch_id); v_watch_open := v_watch_open + 1;
    ELSIF v_kind = 'paper_watch_close' THEN
      IF NOT (v_item ?& ARRAY['kind','watch_id','ticker','closed_date','close_price'])
         OR (v_item - ARRAY['kind','watch_id','ticker','closed_date','close_price']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid paper watch close mutation' USING ERRCODE = '22023';
      END IF;
      UPDATE public.paper_watches SET status='closed',closed_date=(v_item->>'closed_date')::date,
        close_price=(v_item->>'close_price')::numeric,closed_run_id=p_run_id
      WHERE id=(v_item->>'watch_id')::bigint AND ticker=v_item->>'ticker' AND status='active';
      GET DIAGNOSTICS v_rows = ROW_COUNT;
      IF v_rows <> 1 THEN RAISE EXCEPTION 'paper watch unavailable' USING ERRCODE = '22023'; END IF;
      v_watch_close := v_watch_close + 1;
    ELSE
      RAISE EXCEPTION 'unsupported artifact kind' USING ERRCODE = '22023';
    END IF;
  END LOOP;
  v_receipt := jsonb_build_object('counts',jsonb_build_object('observation',v_obs,'snapshot',v_snap,
    'lesson',v_lesson,'radar_upsert',v_radar_up,'radar_delete',v_radar_del,
    'paper_watch_create',v_watch_open,'paper_watch_close',v_watch_close),'paper_watch_ids',v_watch_ids);
  UPDATE public.market_gateway_requests SET status='completed',response=v_receipt,
    response_digest=encode(extensions.digest(v_receipt::text,'sha256'),'hex'),finished_at=now()
  WHERE request_id=p_request_id AND lease_token=p_lease_token AND status='claimed';
  IF NOT FOUND THEN RAISE EXCEPTION 'request lease unavailable' USING ERRCODE = '40001'; END IF;
  RETURN v_receipt;
END;
$$;

CREATE OR REPLACE FUNCTION public.apply_market_decision_bundle(
  p_request_id UUID, p_run_id UUID, p_lease_token UUID, p_policy_version INT,
  p_evaluations JSONB, p_suggestions JSONB, p_publication JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE; v_existing public.market_publications%ROWTYPE;
  v_item JSONB; v_eval public.decision_evaluations%ROWTYPE; v_pub_id UUID := gen_random_uuid();
  v_holding JSONB; v_rows INT; v_eval_count INT := 0; v_suggestion_count INT := 0;
  v_is_holiday BOOLEAN := false;
BEGIN
  IF jsonb_typeof(p_evaluations)<>'array' OR jsonb_typeof(p_suggestions)<>'array'
     OR jsonb_typeof(p_publication)<>'object' THEN
    RAISE EXCEPTION 'invalid decision transaction' USING ERRCODE = '22023';
  END IF;
  v_is_holiday := COALESCE(p_publication->>'kind'='holiday' AND p_publication->>'phase'='pre-market'
    AND p_run_id IS NULL AND jsonb_array_length(p_evaluations)=0
    AND jsonb_array_length(p_suggestions)=0, false);
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.lease_token <> p_lease_token OR v_request.status <> 'claimed'
     OR (v_is_holiday AND (v_request.operation <> 'start_run' OR v_request.run_id IS NOT NULL))
     OR (NOT v_is_holiday AND (v_request.operation <> 'evaluate_and_publish'
       OR v_request.run_id IS DISTINCT FROM p_run_id OR p_run_id IS NULL)) THEN
    RAISE EXCEPTION 'request lease unavailable' USING ERRCODE = '40001';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_request_id::text, 0));
  SELECT * INTO v_existing FROM public.market_publications WHERE idempotency_key=p_request_id;
  IF FOUND THEN RETURN jsonb_build_object('publication_id',v_existing.id,'status',v_existing.status,'duplicate',true); END IF;
  IF v_is_holiday THEN
    SELECT * INTO v_existing FROM public.market_publications
    WHERE market_date=(p_publication->>'market_date')::date AND phase='pre-market' AND kind='holiday';
    IF FOUND THEN RETURN jsonb_build_object('publication_id',v_existing.id,'status',v_existing.status,'duplicate',true); END IF;
  ELSE
    PERFORM 1 FROM public.analysis_runs WHERE id=p_run_id AND status='running' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'run unavailable' USING ERRCODE = '22023'; END IF;
    SELECT * INTO v_existing FROM public.market_publications WHERE run_id=p_run_id;
    IF FOUND THEN RETURN jsonb_build_object('code','RUN_ALREADY_EVALUATED','publication_id',v_existing.id,'status',v_existing.status); END IF;
  END IF;
  PERFORM 1 FROM public.market_policy_config WHERE version=p_policy_version AND active;
  IF NOT FOUND THEN RAISE EXCEPTION 'active policy unavailable' USING ERRCODE = '22023'; END IF;
  IF jsonb_array_length(p_evaluations)>80 OR jsonb_array_length(p_suggestions)>80 THEN
    RAISE EXCEPTION 'invalid decision transaction' USING ERRCODE = '22023';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_evaluations) LOOP
    IF NOT (v_item ?& ARRAY['id','candidate_id','input_digest','raw_action','final_action','policy_status','reason_codes','explanations','normalized','evidence','analyst','checker'])
       OR (v_item - ARRAY['id','candidate_id','input_digest','raw_action','final_action','policy_status','reason_codes','explanations','normalized','evidence','analyst','checker']) <> '{}'::jsonb
       OR v_item->>'policy_status' NOT IN ('approved','downgraded','vetoed') THEN
      RAISE EXCEPTION 'invalid evaluation' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.decision_evaluations(id,request_id,run_id,candidate_id,policy_version,input_digest,
      raw_action,final_action,policy_status,reason_codes,explanations,normalized,evidence,analyst,checker)
    VALUES ((v_item->>'id')::uuid,p_request_id,p_run_id,(v_item->>'candidate_id')::uuid,p_policy_version,
      v_item->>'input_digest',v_item->>'raw_action',NULLIF(v_item->>'final_action',''),v_item->>'policy_status',
      v_item->'reason_codes',v_item->'explanations',v_item->'normalized',v_item->'evidence',
      v_item->'analyst',v_item->'checker');
    v_eval_count := v_eval_count + 1;
  END LOOP;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_suggestions) LOOP
    IF NOT (v_item ?& ARRAY['evaluation_id','candidate_id','date','ticker','action','decision_mode','bucket','depth',
      'entry_zone_low','entry_zone_high','valid_until','stop','target','confidence','bull','bear',
      'decisive_factor','risk_verdict','reason','score','price_at_suggestion','evidence_as_of','invalidation_price'])
       OR (v_item - ARRAY['evaluation_id','candidate_id','date','ticker','action','decision_mode','bucket','depth',
      'entry_zone_low','entry_zone_high','valid_until','stop','target','confidence','bull','bear',
      'decisive_factor','risk_verdict','reason','score','price_at_suggestion','evidence_as_of','invalidation_price']) <> '{}'::jsonb THEN
      RAISE EXCEPTION 'invalid suggestion' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_eval FROM public.decision_evaluations
    WHERE id=(v_item->>'evaluation_id')::uuid AND request_id=p_request_id AND run_id=p_run_id;
    IF NOT FOUND OR v_eval.candidate_id IS DISTINCT FROM (v_item->>'candidate_id')::uuid
       OR v_eval.final_action IS DISTINCT FROM v_item->>'action' OR v_eval.policy_version IS DISTINCT FROM p_policy_version
       OR v_eval.policy_status NOT IN ('approved','downgraded')
       OR v_eval.normalized->>'ticker' IS DISTINCT FROM v_item->>'ticker' THEN
      RAISE EXCEPTION 'suggestion evaluation mismatch' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.suggestions(date,ticker,action,decision_mode,bucket,depth,entry_zone_low,entry_zone_high,
      valid_until,stop,target,confidence,bull,bear,decisive_factor,risk_verdict,reason,score,
      price_at_suggestion,run_id,evidence_as_of,invalidation_price,evaluation_id,decision_source)
    VALUES ((v_item->>'date')::date,v_item->>'ticker',v_item->>'action',v_item->>'decision_mode',v_item->>'bucket',v_item->>'depth',
      (v_item->>'entry_zone_low')::numeric,(v_item->>'entry_zone_high')::numeric,(v_item->>'valid_until')::date,
      (v_item->>'stop')::numeric,(v_item->>'target')::numeric,v_item->>'confidence',v_item->>'bull',v_item->>'bear',
      v_item->>'decisive_factor',v_item->>'risk_verdict',v_item->>'reason',(v_item->>'score')::int,
      (v_item->>'price_at_suggestion')::numeric,p_run_id,(v_item->>'evidence_as_of')::timestamptz,
      (v_item->>'invalidation_price')::numeric,v_eval.id,'gateway');
    v_suggestion_count := v_suggestion_count + 1;
  END LOOP;
  IF p_publication ? 'holding_state' THEN
    IF jsonb_typeof(p_publication->'holding_state') <> 'array' THEN RAISE EXCEPTION 'invalid holding state'; END IF;
    FOR v_holding IN SELECT value FROM jsonb_array_elements(p_publication->'holding_state') LOOP
      IF NOT (v_holding ? 'ticker') OR (v_holding - ARRAY['ticker','high_water_price','stop_alert_active',
        'stop_near_alert_active','target_near_alert_active','target_alert_active']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid holding state keys' USING ERRCODE = '22023';
      END IF;
      UPDATE public.holdings SET
        high_water_price=CASE WHEN v_holding ? 'high_water_price' THEN
          GREATEST(COALESCE(high_water_price,0),(v_holding->>'high_water_price')::numeric) ELSE high_water_price END,
        stop_alert_active=CASE WHEN v_holding ? 'stop_alert_active' THEN (v_holding->>'stop_alert_active')::boolean ELSE stop_alert_active END,
        stop_near_alert_active=CASE WHEN v_holding ? 'stop_near_alert_active' THEN (v_holding->>'stop_near_alert_active')::boolean ELSE stop_near_alert_active END,
        target_near_alert_active=CASE WHEN v_holding ? 'target_near_alert_active' THEN (v_holding->>'target_near_alert_active')::boolean ELSE target_near_alert_active END,
        target_alert_active=CASE WHEN v_holding ? 'target_alert_active' THEN (v_holding->>'target_alert_active')::boolean ELSE target_alert_active END
      WHERE ticker=v_holding->>'ticker';
      GET DIAGNOSTICS v_rows = ROW_COUNT;
      IF v_rows <> 1 THEN RAISE EXCEPTION 'holding state target unavailable' USING ERRCODE = '22023'; END IF;
    END LOOP;
  END IF;
  IF NOT (p_publication ?& ARRAY['market_date','phase','kind','template_version','rendered_body','rendered_hash','status','holding_state'])
     OR (p_publication - ARRAY['market_date','phase','kind','template_version','rendered_body','rendered_hash','status','holding_state']) <> '{}'::jsonb
     OR p_publication->>'status' NOT IN ('ready','suppressed') THEN
    RAISE EXCEPTION 'invalid publication' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.market_publications(id,idempotency_key,run_id,market_date,phase,kind,
    template_version,rendered_body,rendered_hash,status)
  VALUES (v_pub_id,p_request_id,p_run_id,(p_publication->>'market_date')::date,p_publication->>'phase',
    p_publication->>'kind',(p_publication->>'template_version')::int,p_publication->>'rendered_body',
    p_publication->>'rendered_hash',p_publication->>'status');
  RETURN jsonb_build_object('publication_id',v_pub_id,'status',p_publication->>'status',
    'evaluation_count',v_eval_count,'suggestion_count',v_suggestion_count,'duplicate',false);
END;
$$;

CREATE OR REPLACE FUNCTION public.import_legacy_suggestion(p_row JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_action TEXT; v_eval_id UUID := gen_random_uuid(); v_candidate_id UUID := gen_random_uuid(); v_id BIGINT;
BEGIN
  IF jsonb_typeof(p_row)<>'object' OR NOT (p_row ?& ARRAY['date','ticker','action'])
     OR (p_row - ARRAY['date','ticker','action','bucket','depth','entry_zone_low','entry_zone_high','valid_until',
       'stop','target','confidence','bull','bear','decisive_factor','risk_verdict','invalidation_level','reason',
       'score','price_at_suggestion','evidence_as_of','invalidation_price']) <> '{}'::jsonb THEN
    RAISE EXCEPTION 'invalid legacy suggestion' USING ERRCODE = '22023';
  END IF;
  v_action := CASE lower(trim(p_row->>'action')) WHEN 'trim' THEN 'reduce' WHEN 'exit' THEN 'sell'
    ELSE lower(trim(p_row->>'action')) END;
  IF v_action NOT IN ('buy','add','hold','reduce','sell','watch','avoid') THEN
    RAISE EXCEPTION 'unknown legacy action' USING ERRCODE = '22023';
  END IF;
  IF p_row->>'ticker' !~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$' THEN
    RAISE EXCEPTION 'invalid legacy ticker' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.decision_evaluations(id,candidate_id,input_digest,raw_action,final_action,
    policy_status,reason_codes,explanations,normalized)
  VALUES (v_eval_id,v_candidate_id,encode(extensions.digest(p_row::text,'sha256'),'hex'),v_action,v_action,
    'legacy_unverified','["LEGACY_UNVERIFIED"]'::jsonb,
    '["Imported by local administrator without gateway review."]'::jsonb,
    jsonb_build_object('ticker',p_row->>'ticker'));
  INSERT INTO public.suggestions(date,ticker,action,bucket,depth,entry_zone_low,entry_zone_high,
    valid_until,stop,target,confidence,bull,bear,decisive_factor,risk_verdict,invalidation_level,
    reason,score,price_at_suggestion,evidence_as_of,invalidation_price,evaluation_id,decision_source)
  VALUES ((p_row->>'date')::date,p_row->>'ticker',v_action,lower(p_row->>'bucket'),p_row->>'depth',
    (p_row->>'entry_zone_low')::numeric,(p_row->>'entry_zone_high')::numeric,(p_row->>'valid_until')::date,
    (p_row->>'stop')::numeric,(p_row->>'target')::numeric,lower(p_row->>'confidence'),p_row->>'bull',p_row->>'bear',
    p_row->>'decisive_factor',p_row->>'risk_verdict',p_row->>'invalidation_level',p_row->>'reason',
    (p_row->>'score')::int,(p_row->>'price_at_suggestion')::numeric,(p_row->>'evidence_as_of')::timestamptz,
    (p_row->>'invalidation_price')::numeric,v_eval_id,'legacy') RETURNING id INTO v_id;
  RETURN jsonb_build_object('suggestion_id',v_id,'evaluation_id',v_eval_id);
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_market_publication(p_request_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_pub public.market_publications%ROWTYPE; v_lease UUID;
BEGIN
  SELECT * INTO v_pub FROM public.market_publications WHERE idempotency_key=p_request_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'publication unavailable' USING ERRCODE = '22023'; END IF;
  IF v_pub.status IN ('delivered','delivery_unknown','suppressed') THEN
    RETURN jsonb_build_object('claimed',false,'status',v_pub.status,'publication_id',v_pub.id);
  END IF;
  IF v_pub.status='sending' THEN
    IF v_pub.sending_started_at >= now()-interval '5 minutes' THEN
      RETURN jsonb_build_object('claimed',false,'status','sending','publication_id',v_pub.id);
    END IF;
    UPDATE public.market_publications SET status='delivery_unknown',lease_token=NULL,
      error='SEND_LEASE_EXPIRED',updated_at=now() WHERE id=v_pub.id;
    RETURN jsonb_build_object('claimed',false,'status','delivery_unknown','publication_id',v_pub.id);
  END IF;
  IF v_pub.status NOT IN ('ready','delivery_failed') THEN RAISE EXCEPTION 'invalid publication state'; END IF;
  v_lease := gen_random_uuid();
  UPDATE public.market_publications SET status='sending',lease_token=v_lease,sending_started_at=now(),
    attempt_count=attempt_count+1,updated_at=now(),error=NULL WHERE id=v_pub.id;
  RETURN jsonb_build_object('claimed',true,'status','sending','publication_id',v_pub.id,
    'lease_token',v_lease,'rendered_body',v_pub.rendered_body);
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_market_publication(
  p_request_id UUID, p_lease_token UUID, p_status TEXT, p_message_ids JSONB, p_error TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_valid_ids BOOLEAN;
BEGIN
  IF p_status NOT IN ('delivered','delivery_failed','delivery_unknown')
     OR jsonb_typeof(p_message_ids)<>'array' OR char_length(COALESCE(p_error,''))>1000 THEN
    RAISE EXCEPTION 'invalid publication completion' USING ERRCODE = '22023';
  END IF;
  SELECT COALESCE(bool_and(jsonb_typeof(value)='number' AND value::text ~ '^[0-9]+$'),true)
    INTO v_valid_ids FROM jsonb_array_elements(p_message_ids);
  IF NOT v_valid_ids OR (p_status='delivered' AND jsonb_array_length(p_message_ids)=0) THEN
    RAISE EXCEPTION 'invalid telegram message ids' USING ERRCODE = '22023';
  END IF;
  UPDATE public.market_publications SET status=p_status,telegram_message_ids=p_message_ids,
    delivered_at=CASE WHEN p_status='delivered' THEN now() ELSE NULL END,
    error=CASE WHEN p_error IS NULL THEN NULL ELSE regexp_replace(left(p_error,1000),'[^A-Za-z0-9_ .:-]','?','g') END,
    lease_token=NULL,updated_at=now()
  WHERE idempotency_key=p_request_id AND lease_token=p_lease_token AND status='sending';
  IF NOT FOUND THEN RAISE EXCEPTION 'publication lease unavailable' USING ERRCODE = '40001'; END IF;
  RETURN jsonb_build_object('status',p_status,'telegram_message_ids',p_message_ids);
END;
$$;

REVOKE ALL ON FUNCTION public.activate_market_policy_config(INT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.claim_market_gateway_request(UUID, TEXT, UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.complete_market_gateway_request(UUID, UUID, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.start_market_analysis_run(UUID, UUID, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_market_artifacts(UUID, UUID, UUID, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_market_decision_bundle(UUID, UUID, UUID, INT, JSONB, JSONB, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.import_legacy_suggestion(JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.claim_market_publication(UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_market_publication(UUID, UUID, TEXT, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.activate_market_policy_config(INT) TO service_role;
GRANT EXECUTE ON FUNCTION public.claim_market_gateway_request(UUID, TEXT, UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.complete_market_gateway_request(UUID, UUID, TEXT, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.start_market_analysis_run(UUID, UUID, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_market_artifacts(UUID, UUID, UUID, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_market_decision_bundle(UUID, UUID, UUID, INT, JSONB, JSONB, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.import_legacy_suggestion(JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.claim_market_publication(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_market_publication(UUID, UUID, TEXT, JSONB, TEXT) TO service_role;
