-- Owner-only, receipt-backed alert lifecycle. Additive and idempotent.
-- Renderer v3 remains disabled by policy until the shadow rollout is approved.

ALTER TABLE public.market_gateway_requests
  DROP CONSTRAINT IF EXISTS market_gateway_requests_operation_check;
ALTER TABLE public.market_gateway_requests
  ADD CONSTRAINT market_gateway_requests_operation_check CHECK (operation IN (
    'start_run','read_context','record_artifacts','grade_due_decisions',
    'evaluate_and_publish','evaluate_alert_rules','finish_run'
  ));

CREATE TABLE IF NOT EXISTS public.market_alert_drafts (
  id UUID PRIMARY KEY,
  request_id UUID NOT NULL REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  source_evaluation_id UUID NOT NULL REFERENCES public.decision_evaluations(id) ON DELETE RESTRICT,
  rule_snapshot JSONB NOT NULL CHECK (
    jsonb_typeof(rule_snapshot) = 'object' AND octet_length(rule_snapshot::text) <= 32768
  ),
  fingerprint TEXT NOT NULL UNIQUE CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
  state TEXT NOT NULL DEFAULT 'draft' CHECK (state IN ('draft','armed','dismissed','expired')),
  owner_chat_id BIGINT,
  owner_user_id BIGINT,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((owner_chat_id IS NULL) = (owner_user_id IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_market_alert_drafts_state_expiry
  ON public.market_alert_drafts(state, expires_at);

CREATE TABLE IF NOT EXISTS public.market_alert_rules (
  id UUID PRIMARY KEY,
  source_draft_id UUID NOT NULL UNIQUE REFERENCES public.market_alert_drafts(id) ON DELETE RESTRICT,
  current_version INT NOT NULL CHECK (current_version > 0),
  state TEXT NOT NULL CHECK (state IN ('active','paused','snoozed','dismissed','expired')),
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
  profile TEXT NOT NULL CHECK (profile IN ('long_term','balanced','active')),
  severity TEXT NOT NULL CHECK (severity IN ('critical','review','update','watch','system')),
  session TEXT NOT NULL CHECK (session IN ('regular','pre_market','post_market','all')),
  confirmation TEXT NOT NULL CHECK (confirmation IN ('bar_close','two_quote')),
  conditions JSONB NOT NULL CHECK (
    jsonb_typeof(conditions) = 'array' AND jsonb_array_length(conditions) BETWEEN 1 AND 5
    AND octet_length(conditions::text) <= 16384
  ),
  cooldown_seconds INT NOT NULL CHECK (cooldown_seconds BETWEEN 60 AND 604800),
  fire_limit INT NOT NULL CHECK (fire_limit BETWEEN 1 AND 100),
  trigger_count INT NOT NULL DEFAULT 0 CHECK (trigger_count >= 0),
  valid_until TIMESTAMPTZ NOT NULL,
  snoozed_until TIMESTAMPTZ,
  owner_note TEXT NOT NULL DEFAULT '' CHECK (char_length(owner_note) <= 500),
  owner_chat_id BIGINT NOT NULL,
  owner_user_id BIGINT NOT NULL,
  armed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_triggered_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_market_alert_rules_state_ticker
  ON public.market_alert_rules(state, ticker);

CREATE TABLE IF NOT EXISTS public.market_alert_rule_versions (
  rule_id UUID NOT NULL REFERENCES public.market_alert_rules(id) ON DELETE RESTRICT,
  version INT NOT NULL CHECK (version > 0),
  snapshot JSONB NOT NULL CHECK (
    jsonb_typeof(snapshot) = 'object' AND octet_length(snapshot::text) <= 32768
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (rule_id, version)
);

CREATE TABLE IF NOT EXISTS public.market_alert_events (
  id UUID PRIMARY KEY,
  request_id UUID NOT NULL REFERENCES public.market_gateway_requests(request_id) ON DELETE RESTRICT,
  rule_id UUID NOT NULL REFERENCES public.market_alert_rules(id) ON DELETE RESTRICT,
  rule_version INT NOT NULL CHECK (rule_version > 0),
  fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status IN ('triggered','unsafe_to_evaluate')),
  reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(reason_codes) = 'array'),
  observed_at TIMESTAMPTZ,
  evaluated_at TIMESTAMPTZ NOT NULL,
  persisted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  market_session TEXT NOT NULL CHECK (market_session IN ('regular','pre_market','post_market')),
  condition_results JSONB NOT NULL CHECK (
    jsonb_typeof(condition_results) = 'array' AND jsonb_array_length(condition_results) BETWEEN 1 AND 5
    AND octet_length(condition_results::text) <= 32768
  ),
  evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(evidence_ids) = 'array' AND jsonb_array_length(evidence_ids) <= 100
  ),
  publication_id UUID REFERENCES public.market_publications(id) ON DELETE RESTRICT,
  UNIQUE (rule_id, rule_version, fingerprint),
  FOREIGN KEY (rule_id, rule_version)
    REFERENCES public.market_alert_rule_versions(rule_id, version) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_market_alert_events_rule_evaluated
  ON public.market_alert_events(rule_id, evaluated_at DESC);

CREATE TABLE IF NOT EXISTS public.market_alert_actions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  draft_id UUID REFERENCES public.market_alert_drafts(id) ON DELETE RESTRICT,
  rule_id UUID REFERENCES public.market_alert_rules(id) ON DELETE RESTRICT,
  telegram_update_id BIGINT NOT NULL,
  owner_chat_id BIGINT NOT NULL,
  owner_user_id BIGINT NOT NULL,
  action TEXT NOT NULL CHECK (action IN (
    'arm','pause','resume','snooze','acknowledge','dismiss'
  )),
  prior_state TEXT,
  new_state TEXT,
  expected_version INT NOT NULL CHECK (expected_version > 0),
  resulting_version INT NOT NULL CHECK (resulting_version > 0),
  snoozed_until TIMESTAMPTZ,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (telegram_update_id),
  CHECK ((draft_id IS NOT NULL)::int + (rule_id IS NOT NULL)::int = 1)
);

CREATE OR REPLACE FUNCTION public.reject_market_alert_ledger_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF TG_TABLE_NAME='market_alert_events' AND TG_OP='UPDATE'
     AND OLD.publication_id IS NULL AND NEW.publication_id IS NOT NULL
     AND (to_jsonb(OLD)-'publication_id')=(to_jsonb(NEW)-'publication_id') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'market alert ledgers are append-only' USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS market_alert_rule_versions_append_only ON public.market_alert_rule_versions;
CREATE TRIGGER market_alert_rule_versions_append_only
BEFORE UPDATE OR DELETE ON public.market_alert_rule_versions
FOR EACH ROW EXECUTE FUNCTION public.reject_market_alert_ledger_mutation();
DROP TRIGGER IF EXISTS market_alert_events_append_only ON public.market_alert_events;
CREATE TRIGGER market_alert_events_append_only
BEFORE UPDATE OR DELETE ON public.market_alert_events
FOR EACH ROW EXECUTE FUNCTION public.reject_market_alert_ledger_mutation();
DROP TRIGGER IF EXISTS market_alert_actions_append_only ON public.market_alert_actions;
CREATE TRIGGER market_alert_actions_append_only
BEFORE UPDATE OR DELETE ON public.market_alert_actions
FOR EACH ROW EXECUTE FUNCTION public.reject_market_alert_ledger_mutation();

ALTER TABLE public.market_alert_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_alert_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_alert_rule_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_alert_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_alert_actions ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.create_market_alert_drafts(
  p_request_id UUID, p_drafts JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE;
  v_item JSONB; v_snapshot JSONB; v_condition JSONB;
  v_ids JSONB := '[]'::jsonb; v_id UUID; v_count INT := 0; v_existing INT;
BEGIN
  IF jsonb_typeof(p_drafts) <> 'array' OR jsonb_array_length(p_drafts) > 5 THEN
    RAISE EXCEPTION 'invalid alert drafts' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests
  WHERE request_id=p_request_id;
  IF NOT FOUND OR v_request.operation <> 'evaluate_and_publish' OR v_request.status <> 'completed' THEN
    RAISE EXCEPTION 'approved evaluation request unavailable' USING ERRCODE = '22023';
  END IF;
  UPDATE public.market_alert_drafts SET state='expired',updated_at=now()
  WHERE state='draft' AND expires_at<=now();
  SELECT count(*) INTO v_existing FROM public.market_alert_drafts
  WHERE created_at>=now()-interval '1 hour';
  IF v_existing + jsonb_array_length(p_drafts) > 5 THEN
    RAISE EXCEPTION 'alert draft rate limit exceeded' USING ERRCODE = '54000';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_drafts) LOOP
    IF jsonb_typeof(v_item)<>'object'
       OR NOT (v_item ?& ARRAY['id','source_evaluation_id','rule_snapshot','fingerprint'])
       OR (v_item - ARRAY['id','source_evaluation_id','rule_snapshot','fingerprint']) <> '{}'::jsonb THEN
      RAISE EXCEPTION 'invalid alert draft item' USING ERRCODE = '22023';
    END IF;
    v_snapshot := v_item->'rule_snapshot';
    IF jsonb_typeof(v_snapshot)<>'object'
       OR NOT (v_snapshot ?& ARRAY['rule_id','version','state','ticker','profile','severity','session',
         'confirmation','conditions','cooldown_seconds','fire_limit','valid_until','owner_note'])
       OR (v_snapshot - ARRAY['rule_id','version','state','ticker','profile','severity','session',
         'confirmation','conditions','cooldown_seconds','fire_limit','valid_until','owner_note']) <> '{}'::jsonb
       OR v_snapshot->>'rule_id' IS DISTINCT FROM v_item->>'id'
       OR v_snapshot->>'state' <> 'draft'
       OR (v_snapshot->>'version')::int <= 0
       OR v_snapshot->>'ticker' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
       OR v_snapshot->>'profile' NOT IN ('long_term','balanced','active')
       OR v_snapshot->>'severity' NOT IN ('critical','review','update','watch','system')
       OR v_snapshot->>'session' NOT IN ('regular','pre_market','post_market','all')
       OR v_snapshot->>'confirmation' NOT IN ('bar_close','two_quote')
       OR jsonb_typeof(v_snapshot->'conditions')<>'array'
       OR jsonb_array_length(v_snapshot->'conditions') NOT BETWEEN 1 AND 5
       OR (v_snapshot->>'cooldown_seconds')::int NOT BETWEEN 60 AND 604800
       OR (v_snapshot->>'fire_limit')::int NOT BETWEEN 1 AND 100
       OR char_length(v_snapshot->>'owner_note') > 500
       OR (v_snapshot->>'valid_until')::timestamptz <= now()
       OR octet_length(v_snapshot::text) > 32768 THEN
      RAISE EXCEPTION 'invalid alert rule snapshot' USING ERRCODE = '22023';
    END IF;
    FOR v_condition IN SELECT value FROM jsonb_array_elements(v_snapshot->'conditions') LOOP
      IF jsonb_typeof(v_condition)<>'object'
         OR NOT (v_condition ?& ARRAY['kind','operator','left','right','timeframe'])
         OR (v_condition - ARRAY['kind','operator','left','right','timeframe']) <> '{}'::jsonb
         OR v_condition->>'kind' NOT IN ('price_cross','price_zone','sma_cross','rsi_range',
           'volume_multiple','recorded_stop','recorded_target','screen_entry','event_window')
         OR v_condition->>'operator' NOT IN ('above','below','inside','outside')
         OR v_condition->>'timeframe' NOT IN ('quote','15m','1h','1d') THEN
        RAISE EXCEPTION 'invalid alert condition' USING ERRCODE = '22023';
      END IF;
    END LOOP;
    PERFORM 1 FROM public.decision_evaluations
    WHERE id=(v_item->>'source_evaluation_id')::uuid AND request_id=p_request_id
      AND policy_status='approved' AND final_action IS NOT NULL;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'approved source evaluation unavailable' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.market_alert_drafts(
      id,request_id,source_evaluation_id,rule_snapshot,fingerprint,expires_at
    ) VALUES (
      (v_item->>'id')::uuid,p_request_id,(v_item->>'source_evaluation_id')::uuid,
      v_snapshot,v_item->>'fingerprint',now()+interval '24 hours'
    ) ON CONFLICT (fingerprint) DO NOTHING RETURNING id INTO v_id;
    IF v_id IS NOT NULL THEN
      v_ids := v_ids || jsonb_build_array(v_id); v_count := v_count + 1;
    END IF;
    v_id := NULL;
  END LOOP;
  RETURN jsonb_build_object('created_count',v_count,'draft_ids',v_ids);
END;
$$;

CREATE OR REPLACE FUNCTION public.apply_market_alert_action(
  p_draft_or_rule_id UUID, p_action TEXT, p_update_id BIGINT, p_chat_id BIGINT,
  p_user_id BIGINT, p_expected_version INT, p_snooze_until TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_duplicate public.market_alert_actions%ROWTYPE;
  v_draft public.market_alert_drafts%ROWTYPE;
  v_rule public.market_alert_rules%ROWTYPE;
  v_snapshot JSONB; v_prior TEXT; v_new TEXT; v_version INT; v_action_id UUID;
BEGIN
  IF p_update_id<=0 OR p_chat_id<=0 OR p_user_id<=0 OR p_expected_version<=0 THEN
    RAISE EXCEPTION 'invalid alert action identity' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_duplicate FROM public.market_alert_actions
  WHERE telegram_update_id=p_update_id;
  IF FOUND THEN
    IF v_duplicate.action IS DISTINCT FROM p_action
       OR COALESCE(v_duplicate.draft_id,v_duplicate.rule_id) IS DISTINCT FROM p_draft_or_rule_id
       OR v_duplicate.owner_chat_id IS DISTINCT FROM p_chat_id
       OR v_duplicate.owner_user_id IS DISTINCT FROM p_user_id THEN
      RAISE EXCEPTION 'telegram update replay mismatch' USING ERRCODE = '22023';
    END IF;
    RETURN jsonb_build_object('ok',true,'duplicate',true,'state',v_duplicate.new_state,
      'version',v_duplicate.resulting_version,'action_id',v_duplicate.id);
  END IF;

  SELECT * INTO v_draft FROM public.market_alert_drafts
  WHERE id=p_draft_or_rule_id AND state='draft' FOR UPDATE;
  IF FOUND THEN
    v_snapshot := v_draft.rule_snapshot;
    IF v_draft.owner_chat_id IS NOT NULL AND
       (v_draft.owner_chat_id IS DISTINCT FROM p_chat_id OR v_draft.owner_user_id IS DISTINCT FROM p_user_id) THEN
      RAISE EXCEPTION 'alert owner mismatch' USING ERRCODE = '42501';
    END IF;
    IF v_draft.expires_at<=now() THEN RAISE EXCEPTION 'draft expired' USING ERRCODE = '22023'; END IF;
    IF (v_snapshot->>'version')::int IS DISTINCT FROM p_expected_version THEN
      RAISE EXCEPTION 'stale alert version' USING ERRCODE = '40001';
    END IF;
    IF NOT p_action IN ('arm','dismiss') OR v_draft.state<>'draft' THEN
      RAISE EXCEPTION 'invalid alert transition' USING ERRCODE = '22023';
    END IF;
    v_prior := v_draft.state; v_version := p_expected_version;
    IF p_action='dismiss' THEN
      v_new := 'dismissed';
      UPDATE public.market_alert_drafts SET state=v_new,owner_chat_id=p_chat_id,
        owner_user_id=p_user_id,updated_at=now() WHERE id=v_draft.id;
    ELSE
      v_new := 'active';
      v_snapshot := jsonb_set(v_snapshot,'{state}','"active"'::jsonb);
      INSERT INTO public.market_alert_rules(id,source_draft_id,current_version,state,ticker,profile,
        severity,session,confirmation,conditions,cooldown_seconds,fire_limit,valid_until,owner_note,
        owner_chat_id,owner_user_id)
      VALUES (v_draft.id,v_draft.id,v_version,v_new,v_snapshot->>'ticker',v_snapshot->>'profile',
        v_snapshot->>'severity',v_snapshot->>'session',v_snapshot->>'confirmation',
        v_snapshot->'conditions',(v_snapshot->>'cooldown_seconds')::int,
        (v_snapshot->>'fire_limit')::int,(v_snapshot->>'valid_until')::timestamptz,
        v_snapshot->>'owner_note',p_chat_id,p_user_id);
      INSERT INTO public.market_alert_rule_versions(rule_id,version,snapshot)
      VALUES (v_draft.id,v_version,v_snapshot);
      UPDATE public.market_alert_drafts SET state='armed',owner_chat_id=p_chat_id,
        owner_user_id=p_user_id,updated_at=now() WHERE id=v_draft.id;
    END IF;
    INSERT INTO public.market_alert_actions(draft_id,telegram_update_id,owner_chat_id,owner_user_id,
      action,prior_state,new_state,expected_version,resulting_version)
    VALUES (v_draft.id,p_update_id,p_chat_id,p_user_id,p_action,v_prior,v_new,
      p_expected_version,v_version) RETURNING id INTO v_action_id;
    RETURN jsonb_build_object('ok',true,'duplicate',false,'state',v_new,'version',v_version,
      'rule_id',CASE WHEN p_action='arm' THEN v_draft.id ELSE NULL END,'action_id',v_action_id);
  END IF;

  SELECT * INTO v_rule FROM public.market_alert_rules
  WHERE id=p_draft_or_rule_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'alert unavailable' USING ERRCODE = '42501'; END IF;
  IF v_rule.owner_chat_id IS DISTINCT FROM p_chat_id OR v_rule.owner_user_id IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'alert owner mismatch' USING ERRCODE = '42501';
  END IF;
  IF v_rule.current_version IS DISTINCT FROM p_expected_version THEN
    RAISE EXCEPTION 'stale alert version' USING ERRCODE = '40001';
  END IF;
  IF NOT p_action IN ('pause','resume','snooze','acknowledge','dismiss') THEN
    RAISE EXCEPTION 'invalid alert transition' USING ERRCODE = '22023';
  END IF;
  v_prior := v_rule.state; v_new := v_prior; v_version := v_rule.current_version;
  IF p_action='pause' AND v_prior IN ('active','snoozed') THEN v_new := 'paused';
  ELSIF p_action='resume' AND v_prior IN ('paused','snoozed') THEN v_new := 'active';
  ELSIF p_action='snooze' AND v_prior='active' THEN
    IF p_snooze_until IS NULL OR p_snooze_until<=now()
       OR p_snooze_until>now()+interval '1 day 1 minute' THEN
      RAISE EXCEPTION 'invalid snooze window' USING ERRCODE = '22023';
    END IF;
    v_new := 'snoozed';
  ELSIF p_action='dismiss' AND v_prior IN ('active','paused','snoozed') THEN v_new := 'dismissed';
  ELSIF p_action='acknowledge' AND v_prior IN ('active','paused','snoozed') THEN v_new := v_prior;
  ELSE RAISE EXCEPTION 'invalid alert transition' USING ERRCODE = '22023';
  END IF;
  IF p_action<>'acknowledge' THEN
    v_version := v_version+1;
    SELECT snapshot INTO v_snapshot FROM public.market_alert_rule_versions
    WHERE rule_id=v_rule.id AND version=v_rule.current_version;
    v_snapshot := jsonb_set(jsonb_set(v_snapshot,'{state}',to_jsonb(v_new)),
      '{version}',to_jsonb(v_version));
    UPDATE public.market_alert_rules SET state=v_new,current_version=v_version,
      snoozed_until=CASE WHEN p_action='snooze' THEN p_snooze_until ELSE NULL END,
      updated_at=now() WHERE id=v_rule.id;
    INSERT INTO public.market_alert_rule_versions(rule_id,version,snapshot)
    VALUES (v_rule.id,v_version,v_snapshot);
  END IF;
  INSERT INTO public.market_alert_actions(rule_id,telegram_update_id,owner_chat_id,owner_user_id,
    action,prior_state,new_state,expected_version,resulting_version,snoozed_until)
  VALUES (v_rule.id,p_update_id,p_chat_id,p_user_id,p_action,v_prior,v_new,
    p_expected_version,v_version,CASE WHEN p_action='snooze' THEN p_snooze_until ELSE NULL END)
  RETURNING id INTO v_action_id;
  RETURN jsonb_build_object('ok',true,'duplicate',false,'state',v_new,'version',v_version,
    'rule_id',v_rule.id,'action_id',v_action_id);
END;
$$;

CREATE OR REPLACE FUNCTION public.record_market_alert_evaluations(
  p_request_id UUID, p_evaluations JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
  v_request public.market_gateway_requests%ROWTYPE; v_item JSONB;
  v_rule public.market_alert_rules%ROWTYPE; v_id UUID;
  v_ids JSONB := '[]'::jsonb; v_count INT := 0;
BEGIN
  IF jsonb_typeof(p_evaluations)<>'array' OR jsonb_array_length(p_evaluations)>20 THEN
    RAISE EXCEPTION 'invalid alert evaluations' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_request FROM public.market_gateway_requests WHERE request_id=p_request_id FOR UPDATE;
  IF NOT FOUND OR v_request.operation<>'evaluate_alert_rules' OR v_request.status<>'claimed' THEN
    RAISE EXCEPTION 'alert evaluation request unavailable' USING ERRCODE = '40001';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_evaluations) LOOP
    IF jsonb_typeof(v_item)<>'object'
       OR NOT (v_item ?& ARRAY['id','rule_id','rule_version','fingerprint','status','reason_codes',
         'observed_at','evaluated_at','market_session','condition_results','evidence_ids'])
       OR (v_item - ARRAY['id','rule_id','rule_version','fingerprint','status','reason_codes',
         'observed_at','evaluated_at','market_session','condition_results','evidence_ids']) <> '{}'::jsonb
       OR v_item->>'status' NOT IN ('triggered','unsafe_to_evaluate')
       OR v_item->>'market_session' NOT IN ('regular','pre_market','post_market')
       OR jsonb_typeof(v_item->'condition_results')<>'array'
       OR jsonb_array_length(v_item->'condition_results') NOT BETWEEN 1 AND 5
       OR jsonb_typeof(v_item->'reason_codes')<>'array'
       OR jsonb_typeof(v_item->'evidence_ids')<>'array'
       OR (v_item->>'evaluated_at')::timestamptz > now()+interval '1 minute' THEN
      RAISE EXCEPTION 'invalid alert evaluation item' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_rule FROM public.market_alert_rules
    WHERE id=(v_item->>'rule_id')::uuid FOR UPDATE;
    IF NOT FOUND OR v_rule.current_version IS DISTINCT FROM (v_item->>'rule_version')::int THEN
      RAISE EXCEPTION 'stale alert version' USING ERRCODE = '40001';
    END IF;
    IF v_item->>'status'='triggered' THEN
      IF v_rule.state<>'active' OR v_rule.valid_until<=now() OR v_rule.trigger_count>=v_rule.fire_limit THEN
        RAISE EXCEPTION 'alert rule unavailable' USING ERRCODE = '22023';
      END IF;
      IF EXISTS (SELECT 1 FROM public.market_alert_events WHERE rule_id=v_rule.id
        AND status='triggered' AND evaluated_at>
          (v_item->>'evaluated_at')::timestamptz-make_interval(secs=>v_rule.cooldown_seconds)) THEN
        RAISE EXCEPTION 'alert cooldown active' USING ERRCODE = '22023';
      END IF;
    END IF;
    INSERT INTO public.market_alert_events(id,request_id,rule_id,rule_version,fingerprint,status,
      reason_codes,observed_at,evaluated_at,market_session,condition_results,evidence_ids)
    VALUES ((v_item->>'id')::uuid,p_request_id,v_rule.id,(v_item->>'rule_version')::int,
      v_item->>'fingerprint',v_item->>'status',v_item->'reason_codes',
      (v_item->>'observed_at')::timestamptz,(v_item->>'evaluated_at')::timestamptz,
      v_item->>'market_session',v_item->'condition_results',v_item->'evidence_ids')
    RETURNING id INTO v_id;
    IF v_item->>'status'='triggered' THEN
      UPDATE public.market_alert_rules SET trigger_count=trigger_count+1,
        last_triggered_at=(v_item->>'evaluated_at')::timestamptz,updated_at=now()
      WHERE id=v_rule.id;
    END IF;
    v_ids := v_ids || jsonb_build_array(v_id); v_count := v_count+1;
  END LOOP;
  RETURN jsonb_build_object('event_count',v_count,'event_ids',v_ids);
END;
$$;

CREATE OR REPLACE FUNCTION public.link_market_alert_publication(
  p_event_ids UUID[], p_publication_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_expected INT; v_rows INT;
BEGIN
  v_expected := COALESCE(array_length(p_event_ids,1),0);
  IF v_expected<1 OR v_expected>20 OR p_publication_id IS NULL THEN
    RAISE EXCEPTION 'invalid alert publication link' USING ERRCODE = '22023';
  END IF;
  PERFORM 1 FROM public.market_publications
  WHERE id=p_publication_id AND template_version=3;
  IF NOT FOUND THEN RAISE EXCEPTION 'alert publication unavailable' USING ERRCODE = '22023'; END IF;
  UPDATE public.market_alert_events SET publication_id=p_publication_id
  WHERE id=ANY(p_event_ids) AND status='triggered' AND publication_id IS NULL;
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  IF v_rows<>v_expected THEN
    RAISE EXCEPTION 'alert event link mismatch' USING ERRCODE = '22023';
  END IF;
  RETURN jsonb_build_object('linked_count',v_rows,'publication_id',p_publication_id);
END;
$$;

REVOKE ALL ON FUNCTION public.create_market_alert_drafts(UUID, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_market_alert_action(UUID, TEXT, BIGINT, BIGINT, BIGINT, INT, TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_market_alert_evaluations(UUID, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.link_market_alert_publication(UUID[], UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_market_alert_drafts(UUID, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_market_alert_action(UUID, TEXT, BIGINT, BIGINT, BIGINT, INT, TIMESTAMPTZ) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_market_alert_evaluations(UUID, JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.link_market_alert_publication(UUID[], UUID) TO service_role;
