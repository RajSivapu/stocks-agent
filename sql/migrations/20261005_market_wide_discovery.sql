-- Durable, bounded reference and discovery-stage ledgers.  These records are
-- research provenance only; none grants portfolio, alert, or execution authority.

CREATE TABLE IF NOT EXISTS public.market_reference_manifests (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  reference_version TEXT NOT NULL CHECK (reference_version ~ '^[a-z0-9][a-z0-9:._-]{0,127}$'),
  revision INT NOT NULL CHECK (revision BETWEEN 1 AND 10000),
  capability_version INT NOT NULL CHECK (capability_version BETWEEN 1 AND 10000),
  taxonomy_version INT NOT NULL CHECK (taxonomy_version BETWEEN 1 AND 10000),
  source_hash TEXT NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  manifest JSONB NOT NULL CHECK (jsonb_typeof(manifest)='object' AND octet_length(manifest::text)<=65536),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CHECK (valid_to IS NULL OR valid_to>valid_from),
  UNIQUE (reference_version, revision),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_security_reference_revisions (
  id UUID PRIMARY KEY,
  manifest_id UUID NOT NULL REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  revision INT NOT NULL CHECK (revision BETWEEN 1 AND 10000),
  security_id TEXT NOT NULL CHECK (security_id ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'),
  entity_id TEXT NOT NULL CHECK (entity_id ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'),
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9.-]{0,14}$'),
  exchange TEXT CHECK (exchange IS NULL OR exchange ~ '^[A-Z][A-Z0-9._-]{0,31}$'),
  instrument_type TEXT NOT NULL CHECK (instrument_type IN ('COMMON_STOCK','ADR','ETF','PREFERRED','WARRANT','OTC_COMMON','OTHER')),
  eligible BOOLEAN NOT NULL,
  exclusion_reasons JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(exclusion_reasons)='array' AND jsonb_array_length(exclusion_reasons)<=16 AND octet_length(exclusion_reasons::text)<=4096),
  aliases JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(aliases)='array' AND jsonb_array_length(aliases)<=32 AND octet_length(aliases::text)<=4096),
  source_ids JSONB NOT NULL CHECK (jsonb_typeof(source_ids)='array' AND jsonb_array_length(source_ids) BETWEEN 1 AND 16 AND octet_length(source_ids::text)<=4096),
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CHECK (valid_to IS NULL OR valid_to>valid_from),
  UNIQUE (manifest_id, security_id, revision),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_discovery_stage_tasks (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  stage TEXT NOT NULL CHECK (stage IN ('reference','signals','resolve','enrich','screen','quote')),
  capability_id TEXT NOT NULL CHECK (capability_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  provider TEXT NOT NULL CHECK (provider IN ('gdelt','alpha_vantage','finnhub','yahoo','sec_edgar','federal_register','white_house','doe','dod','eia','fred','bls','bea','social')),
  query_kind TEXT NOT NULL CHECK (query_kind IN ('feed','theme_search','issuer_submissions','filing_document','series','screener','quote','universe')),
  query_hash TEXT NOT NULL CHECK (query_hash ~ '^[0-9a-f]{64}$'),
  dependency_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(dependency_ids)='array' AND jsonb_array_length(dependency_ids)<=32 AND octet_length(dependency_ids::text)<=2048),
  requested_window JSONB NOT NULL CHECK (jsonb_typeof(requested_window)='object' AND octet_length(requested_window::text)<=2048),
  state TEXT NOT NULL CHECK (state IN ('planned','attempting','succeeded','failed','deferred','uncertain')),
  attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 10),
  request_budget INT NOT NULL CHECK (request_budget BETWEEN 0 AND 100),
  result JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(result)='object' AND octet_length(result::text)<=65536),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, stage, query_hash)
);

CREATE TABLE IF NOT EXISTS public.market_theme_episode_revisions (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  task_id UUID NOT NULL REFERENCES public.market_discovery_stage_tasks(id) ON DELETE RESTRICT,
  theme_id TEXT NOT NULL CHECK (theme_id ~ '^[a-z][a-z0-9_]{2,79}$'),
  revision INT NOT NULL CHECK (revision BETWEEN 1 AND 10000),
  episode JSONB NOT NULL CHECK (jsonb_typeof(episode)='object' AND octet_length(episode::text)<=32768),
  source_ids JSONB NOT NULL CHECK (jsonb_typeof(source_ids)='array' AND jsonb_array_length(source_ids) BETWEEN 1 AND 64 AND octet_length(source_ids::text)<=8192),
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CHECK (valid_to IS NULL OR valid_to>valid_from),
  UNIQUE (run_id, theme_id, revision),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_exposure_facts (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  task_id UUID NOT NULL REFERENCES public.market_discovery_stage_tasks(id) ON DELETE RESTRICT,
  security_revision_id UUID NOT NULL REFERENCES public.market_security_reference_revisions(id) ON DELETE RESTRICT,
  theme_episode_revision_id UUID REFERENCES public.market_theme_episode_revisions(id) ON DELETE RESTRICT,
  exposure_kind TEXT NOT NULL CHECK (exposure_kind IN ('filing','contract','backlog','revenue','capacity','official_fund','supply_chain','customer','segment')),
  fact JSONB NOT NULL CHECK (jsonb_typeof(fact)='object' AND octet_length(fact::text)<=32768),
  source_ids JSONB NOT NULL CHECK (jsonb_typeof(source_ids)='array' AND jsonb_array_length(source_ids) BETWEEN 1 AND 64 AND octet_length(source_ids::text)<=8192),
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CHECK (valid_to IS NULL OR valid_to>valid_from),
  UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS public.market_research_nominations (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  task_id UUID NOT NULL REFERENCES public.market_discovery_stage_tasks(id) ON DELETE RESTRICT,
  security_revision_id UUID NOT NULL REFERENCES public.market_security_reference_revisions(id) ON DELETE RESTRICT,
  theme_episode_revision_id UUID REFERENCES public.market_theme_episode_revisions(id) ON DELETE RESTRICT,
  exposure_fact_ids JSONB NOT NULL CHECK (jsonb_typeof(exposure_fact_ids)='array' AND jsonb_array_length(exposure_fact_ids) BETWEEN 1 AND 32 AND octet_length(exposure_fact_ids::text)<=2048),
  state TEXT NOT NULL CHECK (state IN ('nominated','researching','accepted','rejected','deferred')),
  rationale JSONB NOT NULL CHECK (jsonb_typeof(rationale)='object' AND octet_length(rationale::text)<=16384),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, security_revision_id, task_id)
);

CREATE OR REPLACE FUNCTION public.reject_market_discovery_mutation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'market discovery revision records are append-only' USING ERRCODE='55000';
END;
$$;

CREATE OR REPLACE FUNCTION public.enforce_market_discovery_stage_task_transition()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'market discovery task deletion is forbidden' USING ERRCODE='55000';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.stage IS DISTINCT FROM OLD.stage OR NEW.capability_id IS DISTINCT FROM OLD.capability_id
     OR NEW.provider IS DISTINCT FROM OLD.provider OR NEW.query_kind IS DISTINCT FROM OLD.query_kind
     OR NEW.query_hash IS DISTINCT FROM OLD.query_hash OR NEW.dependency_ids IS DISTINCT FROM OLD.dependency_ids
     OR NEW.requested_window IS DISTINCT FROM OLD.requested_window
     OR NEW.request_budget IS DISTINCT FROM OLD.request_budget OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'discovery task identity mismatch' USING ERRCODE='22023';
  END IF;
  IF NOT (
    (OLD.state='planned' AND NEW.state='attempting' AND NEW.attempt_count=OLD.attempt_count+1 AND NEW.result=OLD.result)
    OR (OLD.state='attempting' AND NEW.state IN ('succeeded','failed','deferred','uncertain') AND NEW.attempt_count=OLD.attempt_count)
  ) OR NEW.updated_at<=OLD.updated_at THEN
    RAISE EXCEPTION 'invalid discovery task state transition' USING ERRCODE='22023';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.enforce_market_research_nomination_transition()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'market research nomination deletion is forbidden' USING ERRCODE='55000';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.task_id IS DISTINCT FROM OLD.task_id
     OR NEW.security_revision_id IS DISTINCT FROM OLD.security_revision_id
     OR NEW.theme_episode_revision_id IS DISTINCT FROM OLD.theme_episode_revision_id
     OR NEW.exposure_fact_ids IS DISTINCT FROM OLD.exposure_fact_ids
     OR NEW.rationale IS DISTINCT FROM OLD.rationale OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'research nomination identity mismatch' USING ERRCODE='22023';
  END IF;
  IF NOT (
    (OLD.state='nominated' AND NEW.state IN ('researching','rejected','deferred'))
    OR (OLD.state='researching' AND NEW.state IN ('accepted','rejected','deferred'))
  ) OR NEW.updated_at<=OLD.updated_at THEN
    RAISE EXCEPTION 'invalid research nomination state transition' USING ERRCODE='22023';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS market_reference_manifests_append_only ON public.market_reference_manifests;
CREATE TRIGGER market_reference_manifests_append_only BEFORE UPDATE OR DELETE ON public.market_reference_manifests
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_security_reference_revisions_append_only ON public.market_security_reference_revisions;
CREATE TRIGGER market_security_reference_revisions_append_only BEFORE UPDATE OR DELETE ON public.market_security_reference_revisions
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_theme_episode_revisions_append_only ON public.market_theme_episode_revisions;
CREATE TRIGGER market_theme_episode_revisions_append_only BEFORE UPDATE OR DELETE ON public.market_theme_episode_revisions
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_exposure_facts_append_only ON public.market_exposure_facts;
CREATE TRIGGER market_exposure_facts_append_only BEFORE UPDATE OR DELETE ON public.market_exposure_facts
FOR EACH ROW EXECUTE FUNCTION public.reject_market_discovery_mutation();
DROP TRIGGER IF EXISTS market_discovery_stage_tasks_transition_guard ON public.market_discovery_stage_tasks;
CREATE TRIGGER market_discovery_stage_tasks_transition_guard BEFORE UPDATE OR DELETE ON public.market_discovery_stage_tasks
FOR EACH ROW EXECUTE FUNCTION public.enforce_market_discovery_stage_task_transition();
DROP TRIGGER IF EXISTS market_research_nominations_transition_guard ON public.market_research_nominations;
CREATE TRIGGER market_research_nominations_transition_guard BEFORE UPDATE OR DELETE ON public.market_research_nominations
FOR EACH ROW EXECUTE FUNCTION public.enforce_market_research_nomination_transition();

CREATE OR REPLACE FUNCTION public.record_market_discovery_reference(p_run_id UUID, p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE m JSONB; r JSONB;
  v_existing_manifest public.market_reference_manifests%ROWTYPE;
  v_existing_security public.market_security_reference_revisions%ROWTYPE;
  v_count INT:=0; v_duplicate BOOLEAN:=true;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>1048576
     OR NOT(p_payload ?& ARRAY['manifest','security_revisions'])
     OR (p_payload-ARRAY['manifest','security_revisions'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'security_revisions')<>'array'
     OR jsonb_array_length(p_payload->'security_revisions')>15000 THEN
    RAISE EXCEPTION 'invalid discovery reference payload' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs i JOIN public.analysis_runs a ON a.id=i.id
                WHERE i.id=p_run_id AND a.status='running') THEN
    RAISE EXCEPTION 'intelligence run is not running' USING ERRCODE='22023';
  END IF;
  m:=p_payload->'manifest';
  IF jsonb_typeof(m)<>'object' OR NOT(m ?& ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])
     OR (m-ARRAY['id','reference_version','revision','capability_version','taxonomy_version','source_hash','valid_from','valid_to','manifest','content_hash'])<>'{}'::jsonb
     OR m->>'source_hash' !~ '^[0-9a-f]{64}$' OR m->>'content_hash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(m->'manifest')<>'object' OR octet_length((m->'manifest')::text)>65536 THEN
    RAISE EXCEPTION 'invalid discovery reference manifest' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing_manifest FROM public.market_reference_manifests WHERE id=(m->>'id')::uuid;
  IF FOUND THEN
    IF v_existing_manifest.run_id<>p_run_id
       OR v_existing_manifest.reference_version<>m->>'reference_version'
       OR v_existing_manifest.revision<>(m->>'revision')::int
       OR v_existing_manifest.capability_version<>(m->>'capability_version')::int
       OR v_existing_manifest.taxonomy_version<>(m->>'taxonomy_version')::int
       OR v_existing_manifest.source_hash<>m->>'source_hash'
       OR v_existing_manifest.valid_from<>(m->>'valid_from')::timestamptz
       OR v_existing_manifest.valid_to IS DISTINCT FROM (m->>'valid_to')::timestamptz
       OR v_existing_manifest.manifest<>m->'manifest'
       OR v_existing_manifest.content_hash<>m->>'content_hash' THEN
      RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    INSERT INTO public.market_reference_manifests(id,run_id,reference_version,revision,capability_version,taxonomy_version,source_hash,valid_from,valid_to,manifest,content_hash)
    VALUES((m->>'id')::uuid,p_run_id,m->>'reference_version',(m->>'revision')::int,(m->>'capability_version')::int,(m->>'taxonomy_version')::int,m->>'source_hash',(m->>'valid_from')::timestamptz,(m->>'valid_to')::timestamptz,m->'manifest',m->>'content_hash');
    v_duplicate:=false;
  END IF;
  FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'security_revisions') LOOP
    IF jsonb_typeof(r)<>'object' OR NOT(r ?& ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])
       OR (r-ARRAY['id','manifest_id','revision','security_id','entity_id','ticker','exchange','instrument_type','eligible','exclusion_reasons','aliases','source_ids','valid_from','valid_to','content_hash'])<>'{}'::jsonb
       OR r->>'manifest_id'<>m->>'id' OR r->>'content_hash' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(r->'source_ids')<>'array' OR jsonb_array_length(r->'source_ids') NOT BETWEEN 1 AND 16 THEN
      RAISE EXCEPTION 'invalid security reference revision' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_existing_security FROM public.market_security_reference_revisions WHERE id=(r->>'id')::uuid;
    IF FOUND THEN
      IF v_existing_security.manifest_id<>(r->>'manifest_id')::uuid
         OR v_existing_security.run_id<>p_run_id
         OR v_existing_security.revision<>(r->>'revision')::int
         OR v_existing_security.security_id<>r->>'security_id'
         OR v_existing_security.entity_id<>r->>'entity_id'
         OR v_existing_security.ticker<>r->>'ticker'
         OR v_existing_security.exchange IS DISTINCT FROM r->>'exchange'
         OR v_existing_security.instrument_type<>r->>'instrument_type'
         OR v_existing_security.eligible<>(r->>'eligible')::boolean
         OR v_existing_security.exclusion_reasons<>r->'exclusion_reasons'
         OR v_existing_security.aliases<>r->'aliases'
         OR v_existing_security.source_ids<>r->'source_ids'
         OR v_existing_security.valid_from<>(r->>'valid_from')::timestamptz
         OR v_existing_security.valid_to IS DISTINCT FROM (r->>'valid_to')::timestamptz
         OR v_existing_security.content_hash<>r->>'content_hash' THEN
        RAISE EXCEPTION 'discovery reference idempotency mismatch' USING ERRCODE='22023';
      END IF;
    ELSE
      INSERT INTO public.market_security_reference_revisions(id,manifest_id,run_id,revision,security_id,entity_id,ticker,exchange,instrument_type,eligible,exclusion_reasons,aliases,source_ids,valid_from,valid_to,content_hash)
      VALUES((r->>'id')::uuid,(r->>'manifest_id')::uuid,p_run_id,(r->>'revision')::int,r->>'security_id',r->>'entity_id',r->>'ticker',r->>'exchange',r->>'instrument_type',(r->>'eligible')::boolean,r->'exclusion_reasons',r->'aliases',r->'source_ids',(r->>'valid_from')::timestamptz,(r->>'valid_to')::timestamptz,r->>'content_hash');
      v_duplicate:=false;
    END IF;
    v_count:=v_count+1;
  END LOOP;
  RETURN jsonb_build_object('manifest_id',m->>'id','security_revision_count',v_count,'duplicate',v_duplicate);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid discovery reference payload' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.checkpoint_market_discovery_stage(p_run_id UUID, p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t JSONB; r JSONB; v_existing public.market_discovery_stage_tasks%ROWTYPE; v_duplicate BOOLEAN:=false;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>262144
     OR NOT(p_payload ?& ARRAY['task','exposure_facts','theme_episode_revisions','research_nominations'])
     OR (p_payload-ARRAY['task','exposure_facts','theme_episode_revisions','research_nominations'])<>'{}'::jsonb
     OR EXISTS(SELECT 1 FROM jsonb_each(p_payload) item WHERE item.key<>'task' AND jsonb_typeof(item.value)<>'array')
     OR jsonb_array_length(p_payload->'exposure_facts')>100
     OR jsonb_array_length(p_payload->'theme_episode_revisions')>50
     OR jsonb_array_length(p_payload->'research_nominations')>50 THEN
    RAISE EXCEPTION 'invalid discovery stage checkpoint' USING ERRCODE='22023';
  END IF;
  t:=p_payload->'task';
  IF jsonb_typeof(t)<>'object'
     OR NOT(t ?& ARRAY['id','stage','provider','capability_id','query_kind','query_hash','dependency_ids','requested_window','state','attempt_count','request_budget','result'])
     OR (t-ARRAY['id','stage','provider','capability_id','query_kind','query_hash','dependency_ids','requested_window','state','attempt_count','request_budget','result'])<>'{}'::jsonb
     OR t->>'query_hash' !~ '^[0-9a-f]{64}$' OR jsonb_typeof(t->'dependency_ids')<>'array'
     OR jsonb_array_length(t->'dependency_ids')>32 OR jsonb_typeof(t->'requested_window')<>'object'
     OR jsonb_typeof(t->'result')<>'object' OR octet_length((t->'result')::text)>65536 THEN
    RAISE EXCEPTION 'invalid discovery stage task' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs WHERE id=p_run_id)
     OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(t->'dependency_ids') dependency
               WHERE dependency !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                  OR NOT EXISTS(SELECT 1 FROM public.market_discovery_stage_tasks d WHERE d.id=dependency::uuid AND d.run_id=p_run_id AND d.state='succeeded')) THEN
    RAISE EXCEPTION 'discovery task dependency mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_discovery_stage_tasks WHERE id=(t->>'id')::uuid FOR UPDATE;
  IF NOT FOUND THEN
    IF t->>'state'<>'planned' OR (t->>'attempt_count')::int<>0 OR t->'result'<>'{}'::jsonb
       OR jsonb_array_length(p_payload->'exposure_facts')<>0 OR jsonb_array_length(p_payload->'theme_episode_revisions')<>0 OR jsonb_array_length(p_payload->'research_nominations')<>0 THEN
      RAISE EXCEPTION 'new discovery task must be planned' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.market_discovery_stage_tasks(id,run_id,stage,capability_id,provider,query_kind,query_hash,dependency_ids,requested_window,state,attempt_count,request_budget,result)
    VALUES((t->>'id')::uuid,p_run_id,t->>'stage',t->>'capability_id',t->>'provider',t->>'query_kind',t->>'query_hash',t->'dependency_ids',t->'requested_window',t->>'state',(t->>'attempt_count')::int,(t->>'request_budget')::int,t->'result');
  ELSE
    IF v_existing.run_id<>p_run_id OR v_existing.stage<>t->>'stage' OR v_existing.capability_id<>t->>'capability_id'
       OR v_existing.provider<>t->>'provider' OR v_existing.query_kind<>t->>'query_kind'
       OR v_existing.query_hash<>t->>'query_hash' OR v_existing.dependency_ids<>t->'dependency_ids'
       OR v_existing.requested_window<>t->'requested_window' OR v_existing.request_budget<>(t->>'request_budget')::int THEN
      RAISE EXCEPTION 'discovery task identity mismatch' USING ERRCODE='22023';
    END IF;
    IF v_existing.state=t->>'state' AND v_existing.attempt_count=(t->>'attempt_count')::int AND v_existing.result=t->'result' THEN
      v_duplicate:=true;
    ELSE
      UPDATE public.market_discovery_stage_tasks SET state=t->>'state',attempt_count=(t->>'attempt_count')::int,result=t->'result',updated_at=statement_timestamp()
      WHERE id=v_existing.id;
    END IF;
  END IF;
  IF t->>'state'='succeeded' THEN
    IF (jsonb_array_length(p_payload->'theme_episode_revisions')>0 AND t->>'stage'<>'signals')
       OR (jsonb_array_length(p_payload->'exposure_facts')>0 AND t->>'stage'<>'enrich')
       OR (jsonb_array_length(p_payload->'research_nominations')>0 AND t->>'stage'<>'screen') THEN
      RAISE EXCEPTION 'discovery result does not match stage' USING ERRCODE='22023';
    END IF;
    FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'theme_episode_revisions') LOOP
      IF v_duplicate THEN
        IF NOT EXISTS(
          SELECT 1 FROM public.market_theme_episode_revisions e
          WHERE e.id=(r->>'id')::uuid AND e.run_id=p_run_id AND e.task_id=(t->>'id')::uuid
            AND e.theme_id=r->>'theme_id' AND e.revision=(r->>'revision')::int
            AND e.episode=r->'episode' AND e.source_ids=r->'source_ids'
            AND e.valid_from=(r->>'valid_from')::timestamptz
            AND e.valid_to IS NOT DISTINCT FROM (r->>'valid_to')::timestamptz
            AND e.content_hash=r->>'content_hash'
        ) THEN
          RAISE EXCEPTION 'discovery stage idempotency mismatch' USING ERRCODE='22023';
        END IF;
      ELSE
        INSERT INTO public.market_theme_episode_revisions(id,run_id,task_id,theme_id,revision,episode,source_ids,valid_from,valid_to,content_hash)
        VALUES((r->>'id')::uuid,p_run_id,(t->>'id')::uuid,r->>'theme_id',(r->>'revision')::int,r->'episode',r->'source_ids',(r->>'valid_from')::timestamptz,(r->>'valid_to')::timestamptz,r->>'content_hash');
      END IF;
    END LOOP;
    FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'exposure_facts') LOOP
      IF v_duplicate THEN
        IF NOT EXISTS(
          SELECT 1 FROM public.market_exposure_facts f
          WHERE f.id=(r->>'id')::uuid AND f.run_id=p_run_id AND f.task_id=(t->>'id')::uuid
            AND f.security_revision_id=(r->>'security_revision_id')::uuid
            AND f.theme_episode_revision_id IS NOT DISTINCT FROM (r->>'theme_episode_revision_id')::uuid
            AND f.exposure_kind=r->>'exposure_kind' AND f.fact=r->'fact' AND f.source_ids=r->'source_ids'
            AND f.valid_from=(r->>'valid_from')::timestamptz
            AND f.valid_to IS NOT DISTINCT FROM (r->>'valid_to')::timestamptz
            AND f.content_hash=r->>'content_hash'
        ) THEN
          RAISE EXCEPTION 'discovery stage idempotency mismatch' USING ERRCODE='22023';
        END IF;
      ELSE
        INSERT INTO public.market_exposure_facts(id,run_id,task_id,security_revision_id,theme_episode_revision_id,exposure_kind,fact,source_ids,valid_from,valid_to,content_hash)
        VALUES((r->>'id')::uuid,p_run_id,(t->>'id')::uuid,(r->>'security_revision_id')::uuid,(r->>'theme_episode_revision_id')::uuid,r->>'exposure_kind',r->'fact',r->'source_ids',(r->>'valid_from')::timestamptz,(r->>'valid_to')::timestamptz,r->>'content_hash');
      END IF;
    END LOOP;
    FOR r IN SELECT value FROM jsonb_array_elements(p_payload->'research_nominations') LOOP
      IF v_duplicate THEN
        IF NOT EXISTS(
          SELECT 1 FROM public.market_research_nominations n
          WHERE n.id=(r->>'id')::uuid AND n.run_id=p_run_id AND n.task_id=(t->>'id')::uuid
            AND n.security_revision_id=(r->>'security_revision_id')::uuid
            AND n.theme_episode_revision_id IS NOT DISTINCT FROM (r->>'theme_episode_revision_id')::uuid
            AND n.exposure_fact_ids=r->'exposure_fact_ids' AND n.state=r->>'state' AND n.rationale=r->'rationale'
        ) THEN
          RAISE EXCEPTION 'discovery stage idempotency mismatch' USING ERRCODE='22023';
        END IF;
      ELSE
        INSERT INTO public.market_research_nominations(id,run_id,task_id,security_revision_id,theme_episode_revision_id,exposure_fact_ids,state,rationale)
        VALUES((r->>'id')::uuid,p_run_id,(t->>'id')::uuid,(r->>'security_revision_id')::uuid,(r->>'theme_episode_revision_id')::uuid,r->'exposure_fact_ids',r->>'state',r->'rationale');
      END IF;
    END LOOP;
    IF v_duplicate AND (
      (SELECT count(*) FROM public.market_theme_episode_revisions WHERE task_id=(t->>'id')::uuid)<>jsonb_array_length(p_payload->'theme_episode_revisions')
      OR (SELECT count(*) FROM public.market_exposure_facts WHERE task_id=(t->>'id')::uuid)<>jsonb_array_length(p_payload->'exposure_facts')
      OR (SELECT count(*) FROM public.market_research_nominations WHERE task_id=(t->>'id')::uuid)<>jsonb_array_length(p_payload->'research_nominations')
    ) THEN
      RAISE EXCEPTION 'discovery stage idempotency mismatch' USING ERRCODE='22023';
    END IF;
  ELSIF jsonb_array_length(p_payload->'exposure_facts')<>0 OR jsonb_array_length(p_payload->'theme_episode_revisions')<>0 OR jsonb_array_length(p_payload->'research_nominations')<>0 THEN
    RAISE EXCEPTION 'non-success discovery checkpoint has result rows' USING ERRCODE='22023';
  END IF;
  RETURN jsonb_build_object('task',to_jsonb((SELECT d FROM public.market_discovery_stage_tasks d WHERE d.id=(t->>'id')::uuid))-'run_id'-'created_at'-'updated_at','duplicate',v_duplicate);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid discovery stage checkpoint' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.read_market_discovery_context(p_run_id UUID, p_limit INT DEFAULT 100)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB;
BEGIN
  IF p_run_id IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid discovery context request' USING ERRCODE='22023';
  END IF;
  SELECT jsonb_build_object(
    'manifests',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC) FROM (SELECT id,reference_version,revision,capability_version,taxonomy_version,source_hash,valid_from,valid_to,manifest,content_hash,created_at FROM public.market_reference_manifests WHERE run_id=p_run_id ORDER BY created_at DESC LIMIT p_limit) x),'[]'::jsonb),
    'security_revisions',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC) FROM (SELECT id,manifest_id,revision,security_id,entity_id,ticker,exchange,instrument_type,eligible,exclusion_reasons,aliases,source_ids,valid_from,valid_to,content_hash,created_at FROM public.market_security_reference_revisions WHERE run_id=p_run_id ORDER BY created_at DESC LIMIT p_limit) x),'[]'::jsonb),
    'tasks',COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT id,stage,capability_id,provider,query_kind,query_hash,dependency_ids,requested_window,state,attempt_count,request_budget,result FROM public.market_discovery_stage_tasks WHERE run_id=p_run_id ORDER BY created_at LIMIT p_limit) x),'[]'::jsonb),
    'theme_episodes',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC) FROM (SELECT id,task_id,theme_id,revision,episode,source_ids,valid_from,valid_to,content_hash,created_at FROM public.market_theme_episode_revisions WHERE run_id=p_run_id ORDER BY created_at DESC LIMIT p_limit) x),'[]'::jsonb),
    'exposure_facts',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC) FROM (SELECT id,task_id,security_revision_id,theme_episode_revision_id,exposure_kind,fact,source_ids,valid_from,valid_to,content_hash,created_at FROM public.market_exposure_facts WHERE run_id=p_run_id ORDER BY created_at DESC LIMIT p_limit) x),'[]'::jsonb),
    'research_nominations',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC) FROM (SELECT id,task_id,security_revision_id,theme_episode_revision_id,exposure_fact_ids,state,rationale,created_at,updated_at FROM public.market_research_nominations WHERE run_id=p_run_id ORDER BY created_at DESC LIMIT p_limit) x),'[]'::jsonb)
  ) INTO v_result;
  IF octet_length(v_result::text)>1048576 THEN
    RAISE EXCEPTION 'discovery context exceeds bound' USING ERRCODE='54000';
  END IF;
  RETURN v_result;
END;
$$;

ALTER TABLE public.market_reference_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_security_reference_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_discovery_stage_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_exposure_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_theme_episode_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_research_nominations ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.market_reference_manifests,public.market_security_reference_revisions,
  public.market_discovery_stage_tasks,public.market_exposure_facts,
  public.market_theme_episode_revisions,public.market_research_nominations
FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;

REVOKE ALL ON FUNCTION public.record_market_discovery_reference(UUID, JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.record_market_discovery_reference(UUID, JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.checkpoint_market_discovery_stage(UUID, JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.checkpoint_market_discovery_stage(UUID, JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.read_market_discovery_context(UUID, INT) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.read_market_discovery_context(UUID, INT) TO service_role;

GRANT SELECT ON public.market_reference_manifests TO stock_agent_release_reader;
GRANT SELECT ON public.market_security_reference_revisions TO stock_agent_release_reader;
GRANT SELECT ON public.market_discovery_stage_tasks TO stock_agent_release_reader;
GRANT SELECT ON public.market_exposure_facts TO stock_agent_release_reader;
GRANT SELECT ON public.market_theme_episode_revisions TO stock_agent_release_reader;
GRANT SELECT ON public.market_research_nominations TO stock_agent_release_reader;

GRANT SELECT (id,reference_version,revision,capability_version,taxonomy_version,source_hash,valid_from,valid_to,manifest,content_hash,created_at) ON public.market_reference_manifests TO stock_agent_dashboard;
GRANT SELECT (id,security_id,entity_id,ticker,exchange,instrument_type,eligible,exclusion_reasons,aliases,valid_from,valid_to,content_hash,created_at) ON public.market_security_reference_revisions TO stock_agent_dashboard;
GRANT SELECT (id,stage,capability_id,provider,query_kind,query_hash,state,attempt_count,request_budget,created_at,updated_at) ON public.market_discovery_stage_tasks TO stock_agent_dashboard;
GRANT SELECT (id,security_revision_id,theme_episode_revision_id,exposure_kind,fact,source_ids,valid_from,valid_to,content_hash,created_at) ON public.market_exposure_facts TO stock_agent_dashboard;
GRANT SELECT (id,theme_id,revision,episode,source_ids,valid_from,valid_to,content_hash,created_at) ON public.market_theme_episode_revisions TO stock_agent_dashboard;
GRANT SELECT (id,security_revision_id,theme_episode_revision_id,exposure_fact_ids,state,rationale,created_at,updated_at) ON public.market_research_nominations TO stock_agent_dashboard;

DO $$ DECLARE name TEXT; BEGIN
  FOREACH name IN ARRAY ARRAY['market_reference_manifests','market_security_reference_revisions','market_discovery_stage_tasks','market_exposure_facts','market_theme_episode_revisions','market_research_nominations'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS release_evidence_select ON public.%I',name);
    EXECUTE format('CREATE POLICY release_evidence_select ON public.%I FOR SELECT TO stock_agent_release_reader USING (true)',name);
    EXECUTE format('DROP POLICY IF EXISTS owner_dashboard_select_discovery ON public.%I',name);
    EXECUTE format('CREATE POLICY owner_dashboard_select_discovery ON public.%I FOR SELECT TO stock_agent_dashboard USING (true)',name);
  END LOOP;
END; $$;
