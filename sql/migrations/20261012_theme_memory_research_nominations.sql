-- V2 cross-run theme memory and research-nomination companion ledgers.
-- The 20261005 discovery rows remain readable historical v1 records and do
-- not authorize v2 memory, nominations, qualification, or action.

CREATE TABLE IF NOT EXISTS public.market_theme_episode_revisions_v2 (
  revision_id UUID PRIMARY KEY,
  theme_id TEXT NOT NULL CHECK (
    theme_id ~ '^[a-z][a-z0-9_]{2,79}$'
    OR theme_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
  ),
  episode_id UUID NOT NULL,
  revision INT NOT NULL CHECK (revision BETWEEN 1 AND 10000),
  identity_version INT NOT NULL DEFAULT 2 CHECK (identity_version=2),
  anchor_hash TEXT NOT NULL CHECK (anchor_hash ~ '^[0-9a-f]{64}$'),
  origin_run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  predecessor_revision_id UUID REFERENCES public.market_theme_episode_revisions_v2(revision_id) ON DELETE RESTRICT,
  predecessor_content_hash TEXT CHECK (predecessor_content_hash IS NULL OR predecessor_content_hash ~ '^[0-9a-f]{64}$'),
  theme_mechanism TEXT NOT NULL CHECK (char_length(theme_mechanism) BETWEEN 3 AND 240),
  subject_identity TEXT NOT NULL CHECK (char_length(subject_identity) BETWEEN 1 AND 240),
  jurisdiction TEXT NOT NULL CHECK (char_length(jurisdiction) BETWEEN 2 AND 80),
  effective_period_start DATE NOT NULL,
  effective_period_end DATE,
  authoritative_id TEXT CHECK (authoritative_id IS NULL OR char_length(authoritative_id) BETWEEN 1 AND 256),
  source_membership JSONB NOT NULL CHECK (jsonb_typeof(source_membership)='array' AND jsonb_array_length(source_membership) BETWEEN 1 AND 64 AND octet_length(source_membership::text)<=16384),
  source_ids JSONB NOT NULL CHECK (jsonb_typeof(source_ids)='array' AND jsonb_array_length(source_ids) BETWEEN 1 AND 64 AND octet_length(source_ids::text)<=8192),
  supporting_source_ids JSONB NOT NULL CHECK (jsonb_typeof(supporting_source_ids)='array' AND jsonb_array_length(supporting_source_ids)<=64 AND octet_length(supporting_source_ids::text)<=8192),
  opposing_source_ids JSONB NOT NULL CHECK (jsonb_typeof(opposing_source_ids)='array' AND jsonb_array_length(opposing_source_ids)<=64 AND octet_length(opposing_source_ids::text)<=8192),
  added_source_ids JSONB NOT NULL CHECK (jsonb_typeof(added_source_ids)='array' AND jsonb_array_length(added_source_ids)<=64 AND octet_length(added_source_ids::text)<=8192),
  investigated_entity_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(investigated_entity_ids)='array' AND jsonb_array_length(investigated_entity_ids)<=32 AND octet_length(investigated_entity_ids::text)<=8192),
  missing_questions JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(missing_questions)='array' AND jsonb_array_length(missing_questions)<=16 AND octet_length(missing_questions::text)<=8192),
  invalidation_conditions JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(invalidation_conditions)='array' AND jsonb_array_length(invalidation_conditions)<=16 AND octet_length(invalidation_conditions::text)<=8192),
  first_seen TIMESTAMPTZ NOT NULL,
  last_seen TIMESTAMPTZ NOT NULL,
  next_review_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('open','closed')),
  closure_reason TEXT CHECK (closure_reason IS NULL OR char_length(closure_reason) BETWEEN 3 AND 500),
  reopen_reason TEXT CHECK (reopen_reason IS NULL OR char_length(reopen_reason) BETWEEN 3 AND 500),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  execution_allowed BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_allowed),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (episode_id, revision),
  UNIQUE (predecessor_revision_id),
  UNIQUE (origin_run_id, content_hash),
  CHECK (effective_period_end IS NULL OR effective_period_end>=effective_period_start),
  CHECK (last_seen>=first_seen AND next_review_at>=first_seen AND next_review_at<=expires_at),
  CHECK (expires_at>first_seen AND expires_at<=first_seen+INTERVAL '30 days'),
  CHECK ((revision=1 AND predecessor_revision_id IS NULL AND predecessor_content_hash IS NULL)
      OR (revision>1 AND predecessor_revision_id IS NOT NULL AND predecessor_content_hash IS NOT NULL)),
  CHECK ((state='closed' AND closure_reason IS NOT NULL) OR (state='open' AND closure_reason IS NULL)),
  CHECK (reopen_reason IS NULL OR revision>1)
);
CREATE INDEX IF NOT EXISTS idx_market_theme_episode_v2_head
  ON public.market_theme_episode_revisions_v2(episode_id,revision DESC);
CREATE INDEX IF NOT EXISTS idx_market_theme_episode_v2_anchor
  ON public.market_theme_episode_revisions_v2(anchor_hash,created_at DESC);

CREATE TABLE IF NOT EXISTS public.market_reviewer_identity_receipts_v2 (
  receipt_id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  packet_hash TEXT NOT NULL CHECK (packet_hash ~ '^[0-9a-f]{64}$'),
  reference_manifest_id UUID NOT NULL REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  actor_identity TEXT NOT NULL CHECK (actor_identity ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{2,127}$'),
  reviewed_role TEXT NOT NULL CHECK (reviewed_role IN ('analyst','checker')),
  predecessor_receipt_id UUID REFERENCES public.market_reviewer_identity_receipts_v2(receipt_id) ON DELETE RESTRICT,
  review_hash TEXT NOT NULL CHECK (review_hash ~ '^[0-9a-f]{64}$'),
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  execution_allowed BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_allowed),
  UNIQUE (packet_id,reviewed_role),
  UNIQUE (packet_id,actor_identity),
  CHECK ((reviewed_role='analyst' AND predecessor_receipt_id IS NULL)
      OR (reviewed_role='checker' AND predecessor_receipt_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS public.market_research_nomination_requests_v2 (
  request_id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  reviewer_receipt_id UUID NOT NULL REFERENCES public.market_reviewer_identity_receipts_v2(receipt_id) ON DELETE RESTRICT,
  request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  accepted_count INT NOT NULL CHECK (accepted_count BETWEEN 0 AND 3),
  response JSONB NOT NULL CHECK (jsonb_typeof(response)='object' AND octet_length(response::text)<=16384),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id,request_hash)
);

CREATE TABLE IF NOT EXISTS public.market_research_nominations_v2 (
  nomination_id UUID PRIMARY KEY,
  request_id UUID NOT NULL REFERENCES public.market_research_nomination_requests_v2(request_id) ON DELETE RESTRICT,
  origin_run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  packet_id UUID NOT NULL REFERENCES public.market_evidence_packets(id) ON DELETE RESTRICT,
  packet_hash TEXT NOT NULL CHECK (packet_hash ~ '^[0-9a-f]{64}$'),
  reviewer_receipt_id UUID NOT NULL REFERENCES public.market_reviewer_identity_receipts_v2(receipt_id) ON DELETE RESTRICT,
  actor_identity TEXT NOT NULL CHECK (char_length(actor_identity) BETWEEN 3 AND 128),
  reviewed_role TEXT NOT NULL CHECK (reviewed_role IN ('analyst','checker')),
  reference_manifest_id UUID NOT NULL REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  theme_id TEXT NOT NULL CHECK (theme_id ~ '^[a-z][a-z0-9_]{2,79}$' OR theme_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'),
  entity_id TEXT CHECK (entity_id IS NULL OR char_length(entity_id) BETWEEN 1 AND 128),
  security_id TEXT CHECK (security_id IS NULL OR char_length(security_id) BETWEEN 1 AND 128),
  relationship_role TEXT NOT NULL CHECK (relationship_role ~ '^[a-z][a-z0-9_]{2,79}$'),
  reason TEXT NOT NULL CHECK (char_length(reason) BETWEEN 20 AND 500),
  evidence_ids JSONB NOT NULL CHECK (jsonb_typeof(evidence_ids)='array' AND jsonb_array_length(evidence_ids) BETWEEN 1 AND 8 AND octet_length(evidence_ids::text)<=4096),
  required_evidence_kind TEXT NOT NULL CHECK (required_evidence_kind IN ('primary_exposure','contradictory_primary','current_filing','official_program','entity_identity','relationship','current_reference')),
  priority INT NOT NULL CHECK (priority BETWEEN 1 AND 5),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  expires_at TIMESTAMPTZ NOT NULL,
  execution_allowed BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_allowed),
  UNIQUE (origin_run_id,theme_id,entity_id,security_id,relationship_role,required_evidence_kind),
  CHECK (expires_at>created_at AND expires_at<=created_at+INTERVAL '7 days')
);

CREATE TABLE IF NOT EXISTS public.market_research_nomination_lifecycle_v2 (
  receipt_id UUID PRIMARY KEY,
  nomination_id UUID NOT NULL REFERENCES public.market_research_nominations_v2(nomination_id) ON DELETE RESTRICT,
  transition_run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  predecessor_receipt_id UUID REFERENCES public.market_research_nomination_lifecycle_v2(receipt_id) ON DELETE RESTRICT,
  state TEXT NOT NULL CHECK (state IN ('pending','selected','resolved','rejected','expired')),
  reason TEXT CHECK (reason IS NULL OR char_length(reason) BETWEEN 3 AND 500),
  selection_descriptor JSONB CHECK (selection_descriptor IS NULL OR (jsonb_typeof(selection_descriptor)='object' AND octet_length(selection_descriptor::text)<=8192)),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  receipt_hash TEXT NOT NULL CHECK (receipt_hash ~ '^[0-9a-f]{64}$'),
  execution_allowed BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_allowed),
  UNIQUE (predecessor_receipt_id)
);

CREATE TABLE IF NOT EXISTS public.market_intelligence_memory_context_bindings_v2 (
  run_id UUID PRIMARY KEY REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  as_of TIMESTAMPTZ NOT NULL,
  reference_manifest_id UUID REFERENCES public.market_reference_manifests(id) ON DELETE RESTRICT,
  reference_hash TEXT CHECK (reference_hash IS NULL OR reference_hash ~ '^[0-9a-f]{64}$'),
  selected_revision_ids JSONB NOT NULL CHECK (jsonb_typeof(selected_revision_ids)='array' AND jsonb_array_length(selected_revision_ids)<=25),
  selected_nomination_ids JSONB NOT NULL CHECK (jsonb_typeof(selected_nomination_ids)='array' AND jsonb_array_length(selected_nomination_ids)<=12),
  context JSONB NOT NULL CHECK (jsonb_typeof(context)='object' AND octet_length(context::text)<=65536),
  snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE OR REPLACE FUNCTION public.reject_theme_memory_v2_mutation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'theme memory v2 records are append-only' USING ERRCODE='55000';
END;
$$;

DROP TRIGGER IF EXISTS market_theme_episode_revisions_v2_append_only ON public.market_theme_episode_revisions_v2;
CREATE TRIGGER market_theme_episode_revisions_v2_append_only BEFORE UPDATE OR DELETE ON public.market_theme_episode_revisions_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();
DROP TRIGGER IF EXISTS market_reviewer_identity_receipts_v2_append_only ON public.market_reviewer_identity_receipts_v2;
CREATE TRIGGER market_reviewer_identity_receipts_v2_append_only BEFORE UPDATE OR DELETE ON public.market_reviewer_identity_receipts_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();
DROP TRIGGER IF EXISTS market_research_nomination_requests_v2_append_only ON public.market_research_nomination_requests_v2;
CREATE TRIGGER market_research_nomination_requests_v2_append_only BEFORE UPDATE OR DELETE ON public.market_research_nomination_requests_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();
DROP TRIGGER IF EXISTS market_research_nominations_v2_append_only ON public.market_research_nominations_v2;
CREATE TRIGGER market_research_nominations_v2_append_only BEFORE UPDATE OR DELETE ON public.market_research_nominations_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();
DROP TRIGGER IF EXISTS market_research_nomination_lifecycle_v2_append_only ON public.market_research_nomination_lifecycle_v2;
CREATE TRIGGER market_research_nomination_lifecycle_v2_append_only BEFORE UPDATE OR DELETE ON public.market_research_nomination_lifecycle_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();
DROP TRIGGER IF EXISTS market_intelligence_memory_context_bindings_v2_append_only ON public.market_intelligence_memory_context_bindings_v2;
CREATE TRIGGER market_intelligence_memory_context_bindings_v2_append_only BEFORE UPDATE OR DELETE ON public.market_intelligence_memory_context_bindings_v2 FOR EACH ROW EXECUTE FUNCTION public.reject_theme_memory_v2_mutation();

CREATE OR REPLACE FUNCTION public.market_theme_episode_uuid_v5(p_namespace UUID,p_name TEXT)
RETURNS UUID LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog,extensions AS $$
DECLARE v_digest BYTEA; v_hex TEXT;
BEGIN
  v_digest:=extensions.digest(uuid_send(p_namespace)||convert_to(p_name,'UTF8'),'sha1');
  v_digest:=set_byte(v_digest,6,(get_byte(v_digest,6)&15)|80);
  v_digest:=set_byte(v_digest,8,(get_byte(v_digest,8)&63)|128);
  v_hex:=encode(substring(v_digest FROM 1 FOR 16),'hex');
  RETURN (substring(v_hex,1,8)||'-'||substring(v_hex,9,4)||'-'||substring(v_hex,13,4)||'-'||substring(v_hex,17,4)||'-'||substring(v_hex,21,12))::uuid;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_theme_episode_revision_v2(p_run_id UUID,p_revision JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  v_existing public.market_theme_episode_revisions_v2%ROWTYPE;
  v_predecessor public.market_theme_episode_revisions_v2%ROWTYPE;
  v_anchor TEXT;
  v_hash TEXT;
  v_expected_episode UUID;
  v_expected_revision UUID;
  v_source JSONB;
  v_first_seen TIMESTAMPTZ;
  v_last_seen TIMESTAMPTZ;
  v_packet public.market_evidence_packets%ROWTYPE;
BEGIN
  IF p_run_id IS NULL OR jsonb_typeof(p_revision)<>'object'
     OR NOT(p_revision ?& ARRAY['revision_id','theme_id','episode_id','revision','identity_version','anchor_hash','origin_run_id','predecessor_revision_id','predecessor_content_hash','theme_mechanism','subject_identity','jurisdiction','effective_period_start','effective_period_end','authoritative_id','source_membership','source_ids','supporting_source_ids','opposing_source_ids','added_source_ids','investigated_entity_ids','missing_questions','invalidation_conditions','first_seen','last_seen','next_review_at','expires_at','state','closure_reason','reopen_reason','content_hash','execution_allowed'])
     OR (p_revision-ARRAY['revision_id','theme_id','episode_id','revision','identity_version','anchor_hash','origin_run_id','predecessor_revision_id','predecessor_content_hash','theme_mechanism','subject_identity','jurisdiction','effective_period_start','effective_period_end','authoritative_id','source_membership','source_ids','supporting_source_ids','opposing_source_ids','added_source_ids','investigated_entity_ids','missing_questions','invalidation_conditions','first_seen','last_seen','next_review_at','expires_at','state','closure_reason','reopen_reason','content_hash','execution_allowed'])<>'{}'::jsonb
     OR (p_revision->>'identity_version')::int<>2 OR (p_revision->>'execution_allowed')::boolean
     OR (p_revision->>'origin_run_id')::uuid<>p_run_id
     OR p_revision->>'theme_id' !~ '^(?:[a-z][a-z0-9_]{2,79}|[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$'
     OR p_revision->>'theme_mechanism' !~ '^[a-z0-9][a-z0-9:._/-]{2,239}$'
     OR p_revision->>'subject_identity' !~ '^[a-z0-9][a-z0-9:._/-]{0,239}$'
     OR p_revision->>'jurisdiction' !~ '^[A-Z0-9][A-Z0-9:._/-]{1,79}$'
     OR (p_revision->'authoritative_id'<>'null'::jsonb AND p_revision->>'authoritative_id' !~ '^[A-Za-z0-9][A-Za-z0-9:._/-]{0,255}$')
     OR p_revision->>'effective_period_start' !~ '^\d{4}-\d{2}-\d{2}$'
     OR to_char((p_revision->>'effective_period_start')::date,'YYYY-MM-DD')<>p_revision->>'effective_period_start'
     OR (p_revision->'effective_period_end'<>'null'::jsonb AND (
       p_revision->>'effective_period_end' !~ '^\d{4}-\d{2}-\d{2}$'
       OR to_char((p_revision->>'effective_period_end')::date,'YYYY-MM-DD')<>p_revision->>'effective_period_end'
       OR (p_revision->>'effective_period_end')::date<(p_revision->>'effective_period_start')::date
     ))
     OR jsonb_typeof(p_revision->'source_membership')<>'array' OR jsonb_array_length(p_revision->'source_membership') NOT BETWEEN 1 AND 64
     OR jsonb_typeof(p_revision->'source_ids')<>'array' OR jsonb_array_length(p_revision->'source_ids') NOT BETWEEN 1 AND 64
     OR jsonb_typeof(p_revision->'supporting_source_ids')<>'array' OR jsonb_array_length(p_revision->'supporting_source_ids')>64
     OR jsonb_typeof(p_revision->'opposing_source_ids')<>'array' OR jsonb_array_length(p_revision->'opposing_source_ids')>64
     OR jsonb_typeof(p_revision->'added_source_ids')<>'array' OR jsonb_array_length(p_revision->'added_source_ids') NOT BETWEEN 1 AND 64
     OR jsonb_typeof(p_revision->'investigated_entity_ids')<>'array' OR jsonb_array_length(p_revision->'investigated_entity_ids')>32
     OR jsonb_typeof(p_revision->'missing_questions')<>'array' OR jsonb_array_length(p_revision->'missing_questions')>16
     OR jsonb_typeof(p_revision->'invalidation_conditions')<>'array' OR jsonb_array_length(p_revision->'invalidation_conditions')>16
     OR p_revision->>'state' NOT IN ('open','closed')
     OR ((p_revision->>'state'='closed')<>(p_revision->'closure_reason'<>'null'::jsonb))
     OR ((p_revision->>'revision')::int=1 AND p_revision->'reopen_reason'<>'null'::jsonb)
     OR (p_revision->'closure_reason'<>'null'::jsonb AND (char_length(p_revision->>'closure_reason') NOT BETWEEN 3 AND 500 OR regexp_replace(trim(p_revision->>'closure_reason'),'\s+',' ','g')<>p_revision->>'closure_reason'))
     OR (p_revision->'reopen_reason'<>'null'::jsonb AND (char_length(p_revision->>'reopen_reason') NOT BETWEEN 3 AND 500 OR regexp_replace(trim(p_revision->>'reopen_reason'),'\s+',' ','g')<>p_revision->>'reopen_reason'))
     OR p_revision->>'first_seen' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$'
     OR p_revision->>'last_seen' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$'
     OR p_revision->>'next_review_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$'
     OR p_revision->>'expires_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$'
     OR p_revision->>'anchor_hash' !~ '^[0-9a-f]{64}$' OR p_revision->>'content_hash' !~ '^[0-9a-f]{64}$'
     OR (p_revision->>'revision')::int NOT BETWEEN 1 AND 10000 THEN
    RAISE EXCEPTION 'invalid theme episode revision v2' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
       SELECT 1 FROM (
         VALUES (p_revision->'investigated_entity_ids',256),(p_revision->'missing_questions',500),(p_revision->'invalidation_conditions',500)
       ) arrays(value,max_length)
       CROSS JOIN LATERAL jsonb_array_elements_text(arrays.value) entry
       WHERE entry.value='' OR char_length(entry.value)>arrays.max_length OR regexp_replace(trim(entry.value),'\s+',' ','g')<>entry.value
     )
     OR EXISTS(
       SELECT 1 FROM (
         VALUES (p_revision->'source_ids'),(p_revision->'supporting_source_ids'),(p_revision->'opposing_source_ids'),(p_revision->'added_source_ids')
       ) arrays(value) CROSS JOIN LATERAL jsonb_array_elements_text(arrays.value) entry
       WHERE entry.value !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
     ) THEN RAISE EXCEPTION 'invalid theme episode revision v2' USING ERRCODE='22023'; END IF;
  IF p_revision->'source_membership'<>(SELECT jsonb_agg(value ORDER BY convert_to(value->>'story_identity','UTF8'),convert_to(value->>'evidence_id','UTF8')) FROM jsonb_array_elements(p_revision->'source_membership'))
     OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_revision->'source_membership') member GROUP BY member->>'story_identity' HAVING count(*)>1)
     OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_revision->'source_membership') member GROUP BY member->>'evidence_id' HAVING count(*)>1)
     OR EXISTS(SELECT 1 FROM (VALUES(p_revision->'source_ids'),(p_revision->'supporting_source_ids'),(p_revision->'opposing_source_ids'),(p_revision->'added_source_ids'),(p_revision->'investigated_entity_ids'),(p_revision->'missing_questions'),(p_revision->'invalidation_conditions')) arrays(value) WHERE arrays.value<>(SELECT COALESCE(jsonb_agg(to_jsonb(entry.value) ORDER BY convert_to(entry.value,'UTF8')),'[]'::jsonb) FROM jsonb_array_elements_text(arrays.value) entry) OR jsonb_array_length(arrays.value)<>(SELECT count(DISTINCT entry.value) FROM jsonb_array_elements_text(arrays.value) entry)) THEN
    RAISE EXCEPTION 'theme episode canonical array mismatch' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs r JOIN public.market_intelligence_run_events e ON e.run_id=r.id AND e.status IN ('started','completed') WHERE r.id=p_run_id) THEN
    RAISE EXCEPTION 'theme origin run is not valid' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_packet FROM public.market_evidence_packets WHERE run_id=p_run_id AND status='completed' AND packet->>'contract_version'='2';
  IF NOT FOUND OR v_packet.packet_hash<>encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_packet.packet),'UTF8'),'sha256'),'hex') THEN
    RAISE EXCEPTION 'theme current packet is not valid' USING ERRCODE='22023';
  END IF;
  v_anchor:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(jsonb_build_object(
    'authoritative_id',p_revision->'authoritative_id',
    'effective_period',jsonb_build_object('end',p_revision->'effective_period_end','start',p_revision->>'effective_period_start'),
    'identity_version',2,'jurisdiction',upper(trim(p_revision->>'jurisdiction')),
    'subject_identity',lower(trim(p_revision->>'subject_identity')),
    'theme_id',p_revision->>'theme_id','theme_mechanism',lower(trim(p_revision->>'theme_mechanism'))
  )),'UTF8'),'sha256'),'hex');
  IF v_anchor<>p_revision->>'anchor_hash' THEN
    RAISE EXCEPTION 'theme episode anchor hash mismatch' USING ERRCODE='22023';
  END IF;
  v_expected_episode:=public.market_theme_episode_uuid_v5('6ba7b811-9dad-11d1-80b4-00c04fd430c8'::uuid,'market-theme-episode-v2:'||v_anchor);
  IF (p_revision->>'episode_id')::uuid<>v_expected_episode THEN
    RAISE EXCEPTION 'theme episode derived identity mismatch' USING ERRCODE='22023';
  END IF;
  FOR v_source IN SELECT value FROM jsonb_array_elements(p_revision->'source_membership') LOOP
    IF jsonb_typeof(v_source)<>'object' OR NOT(v_source ?& ARRAY['evidence_id','story_identity','polarity'])
       OR (v_source-ARRAY['evidence_id','story_identity','polarity'])<>'{}'::jsonb
       OR jsonb_typeof(v_source->'story_identity')<>'string'
       OR char_length(v_source->>'story_identity') NOT BETWEEN 1 AND 512
       OR regexp_replace(trim(v_source->>'story_identity'),'\s+',' ','g')<>v_source->>'story_identity'
       OR v_source->>'polarity' NOT IN ('supporting','opposing')
       OR v_source->>'evidence_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR NOT EXISTS(
         SELECT 1 FROM public.market_source_items item
         JOIN public.market_source_receipts receipt ON receipt.id=item.source_receipt_id
         JOIN public.market_intelligence_run_items used ON used.source_item_id=item.id AND used.source_receipt_id=receipt.id
         WHERE item.id=(v_source->>'evidence_id')::uuid
           AND receipt.status IN ('succeeded','cache_hit')
           AND used.disposition IN ('accepted','duplicate','near_duplicate')
       ) THEN
      RAISE EXCEPTION 'theme source membership mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;
  IF (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'source_ids'))
        IS DISTINCT FROM (SELECT array_agg(member->>'evidence_id' ORDER BY member->>'evidence_id') FROM jsonb_array_elements(p_revision->'source_membership') member)
     OR (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'supporting_source_ids'))
        IS DISTINCT FROM (SELECT array_agg(member->>'evidence_id' ORDER BY member->>'evidence_id') FROM jsonb_array_elements(p_revision->'source_membership') member WHERE member->>'polarity'='supporting')
     OR (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'opposing_source_ids'))
        IS DISTINCT FROM (SELECT array_agg(member->>'evidence_id' ORDER BY member->>'evidence_id') FROM jsonb_array_elements(p_revision->'source_membership') member WHERE member->>'polarity'='opposing') THEN
    RAISE EXCEPTION 'theme source partition mismatch' USING ERRCODE='22023';
  END IF;
  SELECT min(receipt.retrieved_at),max(receipt.retrieved_at) INTO v_first_seen,v_last_seen
  FROM public.market_source_items item JOIN public.market_source_receipts receipt ON receipt.id=item.source_receipt_id
  WHERE item.id IN (SELECT (member->>'evidence_id')::uuid FROM jsonb_array_elements(p_revision->'source_membership') member);
  v_hash:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(p_revision-ARRAY['revision_id','content_hash']),'UTF8'),'sha256'),'hex');
  IF v_hash<>p_revision->>'content_hash' THEN
    RAISE EXCEPTION 'theme revision content hash mismatch' USING ERRCODE='22023';
  END IF;
  v_expected_revision:=public.market_theme_episode_uuid_v5('6ba7b811-9dad-11d1-80b4-00c04fd430c8'::uuid,'market-theme-episode-revision-v2:'||v_expected_episode::text||':'||(p_revision->>'revision')||':'||v_hash);
  IF (p_revision->>'revision_id')::uuid<>v_expected_revision THEN
    RAISE EXCEPTION 'theme revision derived identity mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_existing FROM public.market_theme_episode_revisions_v2 WHERE revision_id=(p_revision->>'revision_id')::uuid;
  IF FOUND THEN
    IF v_existing.origin_run_id<>p_run_id OR v_existing.content_hash<>v_hash THEN
      RAISE EXCEPTION 'theme revision replay mismatch' USING ERRCODE='22023';
    END IF;
    RETURN jsonb_build_object('revision_id',v_existing.revision_id,'episode_id',v_existing.episode_id,'revision',v_existing.revision,'duplicate',true);
  END IF;
  IF (p_revision->>'revision')::int>1 THEN
    SELECT * INTO v_predecessor FROM public.market_theme_episode_revisions_v2 WHERE revision_id=(p_revision->>'predecessor_revision_id')::uuid FOR UPDATE;
    IF NOT FOUND OR v_predecessor.episode_id<>(p_revision->>'episode_id')::uuid
       OR v_predecessor.revision+1<>(p_revision->>'revision')::int
       OR v_predecessor.content_hash<>p_revision->>'predecessor_content_hash'
       OR v_predecessor.theme_id<>p_revision->>'theme_id'
       OR v_predecessor.anchor_hash<>v_anchor
       OR v_predecessor.first_seen<>(p_revision->>'first_seen')::timestamptz
       OR (p_revision->>'last_seen')::timestamptz<>GREATEST(v_predecessor.last_seen,v_last_seen)
       OR (p_revision->>'expires_at')::timestamptz<>v_predecessor.expires_at
       OR v_predecessor.origin_run_id=p_run_id
       OR (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'added_source_ids'))
          IS DISTINCT FROM (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'source_ids') current_source(value) WHERE NOT (v_predecessor.source_ids ? current_source.value))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_revision->'added_source_ids') added(value) WHERE NOT EXISTS(
         SELECT 1 FROM public.market_intelligence_run_items used
         JOIN public.market_source_items item ON item.id=used.source_item_id
         JOIN public.market_source_receipts receipt ON receipt.id=used.source_receipt_id
         WHERE used.run_id=p_run_id AND item.id=added.value::uuid AND item.source_receipt_id=used.source_receipt_id
           AND used.disposition IN ('accepted','duplicate','near_duplicate')
           AND receipt.status IN ('succeeded','cache_hit')
           AND receipt.run_id=p_run_id
       ))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_revision->'added_source_ids') added(value) WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_packet.packet->'evidence') evidence WHERE evidence->>'item_id'=added.value))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_predecessor.source_membership) prior WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_revision->'source_membership') current WHERE current->>'story_identity'=prior->>'story_identity'))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_predecessor.source_membership) prior JOIN LATERAL (SELECT current FROM jsonb_array_elements(p_revision->'source_membership') current WHERE current->>'evidence_id'=prior->>'evidence_id') matched ON true WHERE matched.current<>prior)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(v_predecessor.investigated_entity_ids) prior WHERE NOT(p_revision->'investigated_entity_ids' ? prior.value))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(v_predecessor.invalidation_conditions) prior WHERE NOT(p_revision->'invalidation_conditions' ? prior.value))
       OR (v_predecessor.state='closed' AND (p_revision->>'state'<>'open' OR p_revision->'reopen_reason'='null'::jsonb))
       OR (v_predecessor.state='open' AND p_revision->'reopen_reason'<>'null'::jsonb) THEN
      RAISE EXCEPTION 'theme predecessor lineage mismatch' USING ERRCODE='22023';
    END IF;
  ELSE
    IF (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'added_source_ids'))
       IS DISTINCT FROM (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(p_revision->'source_ids'))
       OR (p_revision->>'first_seen')::timestamptz<>v_first_seen
       OR (p_revision->>'last_seen')::timestamptz<>v_last_seen
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_revision->'added_source_ids') added(value) WHERE NOT EXISTS(
         SELECT 1 FROM public.market_intelligence_run_items used
         JOIN public.market_source_items item ON item.id=used.source_item_id AND item.source_receipt_id=used.source_receipt_id
         JOIN public.market_source_receipts receipt ON receipt.id=used.source_receipt_id
         WHERE used.run_id=p_run_id AND item.id=added.value::uuid AND receipt.run_id=p_run_id
           AND used.disposition IN ('accepted','duplicate','near_duplicate') AND receipt.status IN ('succeeded','cache_hit')
       ))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_revision->'added_source_ids') added(value) WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_packet.packet->'evidence') evidence WHERE evidence->>'item_id'=added.value))
       OR EXISTS(SELECT 1 FROM public.market_theme_episode_revisions_v2 WHERE anchor_hash=v_anchor) THEN
      RAISE EXCEPTION 'theme episode identity or initial evidence mismatch' USING ERRCODE='22023';
    END IF;
  END IF;
  IF (p_revision->>'next_review_at')::timestamptz<(p_revision->>'first_seen')::timestamptz
     OR (p_revision->>'next_review_at')::timestamptz>(p_revision->>'expires_at')::timestamptz
     OR (p_revision->>'expires_at')::timestamptz<=(p_revision->>'first_seen')::timestamptz
     OR (p_revision->>'expires_at')::timestamptz>(p_revision->>'first_seen')::timestamptz+INTERVAL '30 days' THEN
    RAISE EXCEPTION 'theme episode time bounds mismatch' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.market_theme_episode_revisions_v2(
    revision_id,theme_id,episode_id,revision,identity_version,anchor_hash,origin_run_id,
    predecessor_revision_id,predecessor_content_hash,theme_mechanism,subject_identity,jurisdiction,
    effective_period_start,effective_period_end,authoritative_id,source_membership,source_ids,
    supporting_source_ids,opposing_source_ids,added_source_ids,investigated_entity_ids,missing_questions,
    invalidation_conditions,first_seen,last_seen,next_review_at,expires_at,state,closure_reason,reopen_reason,
    content_hash,execution_allowed
  ) VALUES (
    (p_revision->>'revision_id')::uuid,p_revision->>'theme_id',(p_revision->>'episode_id')::uuid,
    (p_revision->>'revision')::int,2,v_anchor,p_run_id,(p_revision->>'predecessor_revision_id')::uuid,
    p_revision->>'predecessor_content_hash',p_revision->>'theme_mechanism',p_revision->>'subject_identity',
    p_revision->>'jurisdiction',(p_revision->>'effective_period_start')::date,(p_revision->>'effective_period_end')::date,
    p_revision->>'authoritative_id',p_revision->'source_membership',p_revision->'source_ids',
    p_revision->'supporting_source_ids',p_revision->'opposing_source_ids',p_revision->'added_source_ids',
    p_revision->'investigated_entity_ids',p_revision->'missing_questions',p_revision->'invalidation_conditions',
    (p_revision->>'first_seen')::timestamptz,(p_revision->>'last_seen')::timestamptz,
    (p_revision->>'next_review_at')::timestamptz,(p_revision->>'expires_at')::timestamptz,
    p_revision->>'state',p_revision->>'closure_reason',p_revision->>'reopen_reason',v_hash,false
  );
  RETURN jsonb_build_object('revision_id',p_revision->>'revision_id','episode_id',p_revision->>'episode_id','revision',(p_revision->>'revision')::int,'duplicate',false);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range OR unique_violation THEN
  RAISE EXCEPTION 'invalid or conflicting theme episode revision v2' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.record_research_review_identity_v2(p_run_id UUID,p_receipt_id UUID,p_review JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_packet public.market_evidence_packets%ROWTYPE; v_binding public.market_reference_run_bindings%ROWTYPE; v_prior public.market_reviewer_identity_receipts_v2%ROWTYPE; v_hash TEXT;
BEGIN
  IF jsonb_typeof(p_review)<>'object' OR NOT(p_review ?& ARRAY['actor_identity','reviewed_role','predecessor_receipt_id'])
     OR (p_review-ARRAY['actor_identity','reviewed_role','predecessor_receipt_id'])<>'{}'::jsonb
     OR p_review->>'reviewed_role' NOT IN ('analyst','checker') THEN
    RAISE EXCEPTION 'invalid reviewer identity receipt' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_packet FROM public.market_evidence_packets WHERE run_id=p_run_id AND status='completed' AND packet->>'contract_version'='2' ORDER BY created_at DESC,id DESC LIMIT 1;
  SELECT * INTO v_binding FROM public.market_reference_run_bindings WHERE run_id=p_run_id AND reference_status='healthy' ORDER BY created_at DESC LIMIT 1;
  IF NOT FOUND OR v_packet.id IS NULL OR v_packet.packet_hash<>encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_packet.packet),'UTF8'),'sha256'),'hex') THEN
    RAISE EXCEPTION 'review packet or reference binding unavailable' USING ERRCODE='22023';
  END IF;
  IF p_review->>'reviewed_role'='checker' THEN
    SELECT * INTO v_prior FROM public.market_reviewer_identity_receipts_v2 WHERE receipt_id=(p_review->>'predecessor_receipt_id')::uuid;
    IF NOT FOUND OR v_prior.run_id<>p_run_id OR v_prior.packet_id<>v_packet.id OR v_prior.reviewed_role<>'analyst' OR v_prior.actor_identity=p_review->>'actor_identity' THEN
      RAISE EXCEPTION 'checker identity lineage mismatch' USING ERRCODE='22023';
    END IF;
  ELSIF p_review->'predecessor_receipt_id'<>'null'::jsonb THEN
    RAISE EXCEPTION 'analyst predecessor is forbidden' USING ERRCODE='22023';
  END IF;
  v_hash:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(jsonb_build_object(
    'receipt_id',p_receipt_id,'run_id',p_run_id,'packet_id',v_packet.id,'packet_hash',v_packet.packet_hash,
    'reference_manifest_id',v_binding.manifest_id,'actor_identity',p_review->>'actor_identity',
    'reviewed_role',p_review->>'reviewed_role','predecessor_receipt_id',p_review->'predecessor_receipt_id'
  )),'UTF8'),'sha256'),'hex');
  SELECT * INTO v_prior FROM public.market_reviewer_identity_receipts_v2 WHERE receipt_id=p_receipt_id;
  IF FOUND THEN
    IF v_prior.review_hash<>v_hash THEN RAISE EXCEPTION 'review identity replay mismatch' USING ERRCODE='22023'; END IF;
    RETURN jsonb_build_object('receipt_id',v_prior.receipt_id,'review_hash',v_prior.review_hash,'duplicate',true);
  END IF;
  INSERT INTO public.market_reviewer_identity_receipts_v2(receipt_id,run_id,packet_id,packet_hash,reference_manifest_id,actor_identity,reviewed_role,predecessor_receipt_id,review_hash)
  VALUES(p_receipt_id,p_run_id,v_packet.id,v_packet.packet_hash,v_binding.manifest_id,p_review->>'actor_identity',p_review->>'reviewed_role',(p_review->>'predecessor_receipt_id')::uuid,v_hash);
  RETURN jsonb_build_object('receipt_id',p_receipt_id,'review_hash',v_hash,'duplicate',false);
EXCEPTION WHEN invalid_text_representation OR unique_violation THEN
  RAISE EXCEPTION 'invalid or conflicting reviewer identity receipt' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.record_research_nominations(p_run_id UUID,p_request_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_receipt public.market_reviewer_identity_receipts_v2%ROWTYPE; v_request public.market_research_nomination_requests_v2%ROWTYPE; v_packet public.market_evidence_packets%ROWTYPE; v_nom JSONB; v_entry JSONB; v_entries JSONB:='[]'::jsonb; v_hash TEXT; v_response JSONB; v_count INT; v_matches INT; v_id UUID; v_created TIMESTAMPTZ:=statement_timestamp();
BEGIN
  IF p_run_id IS NULL OR p_request_id IS NULL OR jsonb_typeof(p_payload)<>'object'
     OR NOT(p_payload ?& ARRAY['reviewer_receipt_id','nominations'])
     OR (p_payload-ARRAY['reviewer_receipt_id','nominations'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload->'nominations')<>'array'
     OR jsonb_array_length(p_payload->'nominations') NOT BETWEEN 1 AND 3 THEN
    RAISE EXCEPTION 'invalid research nomination request' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_run_id::text,90210));
  v_hash:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(p_payload),'UTF8'),'sha256'),'hex');
  SELECT * INTO v_request FROM public.market_research_nomination_requests_v2 WHERE request_id=p_request_id;
  IF FOUND THEN
    IF v_request.run_id<>p_run_id OR v_request.request_hash<>v_hash THEN
      RAISE EXCEPTION 'research nomination request replay mismatch' USING ERRCODE='22023';
    END IF;
    RETURN v_request.response;
  END IF;
  SELECT * INTO v_receipt FROM public.market_reviewer_identity_receipts_v2 WHERE receipt_id=(p_payload->>'reviewer_receipt_id')::uuid AND run_id=p_run_id;
  SELECT * INTO v_packet FROM public.market_evidence_packets WHERE id=v_receipt.packet_id AND run_id=p_run_id AND status='completed' AND packet_hash=v_receipt.packet_hash AND packet->>'contract_version'='2';
  IF v_receipt.receipt_id IS NULL OR v_packet.id IS NULL OR v_packet.packet_hash<>encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_packet.packet),'UTF8'),'sha256'),'hex') THEN
    RAISE EXCEPTION 'nomination reviewer packet identity mismatch' USING ERRCODE='22023';
  END IF;
  v_count:=jsonb_array_length(p_payload->'nominations');
  IF (SELECT count(*) FROM public.market_research_nominations_v2 WHERE origin_run_id=p_run_id)+v_count>3 THEN
    RAISE EXCEPTION 'accepted nomination limit exceeded' USING ERRCODE='22023';
  END IF;
  FOR v_nom IN SELECT value FROM jsonb_array_elements(p_payload->'nominations') LOOP
    IF jsonb_typeof(v_nom)<>'object'
       OR NOT(v_nom ?& ARRAY['theme_id','entity_id','security_id','role','reason','evidence_ids','required_evidence_kind','priority'])
       OR (v_nom-ARRAY['theme_id','entity_id','security_id','role','reason','evidence_ids','required_evidence_kind','priority'])<>'{}'::jsonb
       OR NOT(v_nom->>'theme_id' ~ '^[a-z][a-z0-9_]{2,79}$' OR v_nom->>'theme_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
       OR v_nom->>'role' !~ '^[a-z][a-z0-9_]{2,79}$'
       OR v_nom->>'required_evidence_kind' NOT IN ('primary_exposure','contradictory_primary','current_filing','official_program','entity_identity','relationship','current_reference')
       OR (v_nom->>'priority')::int NOT BETWEEN 1 AND 5
       OR char_length(v_nom->>'reason') NOT BETWEEN 20 AND 500
       OR v_nom->>'reason' ~* '(https?://|www\.|(^|[^a-z])(buy|sell|trade|order|price|score|watchlist|holding|portfolio|cash|alert|policy|allocation|browse|search|query)([^a-z]|$))'
       OR jsonb_typeof(v_nom->'evidence_ids')<>'array' OR jsonb_array_length(v_nom->'evidence_ids') NOT BETWEEN 1 AND 8
       OR (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(v_nom->'evidence_ids'))<>jsonb_array_length(v_nom->'evidence_ids') THEN
      RAISE EXCEPTION 'invalid research nomination' USING ERRCODE='22023';
    END IF;
    SELECT count(*) INTO v_matches FROM jsonb_array_elements(v_packet.packet->'research_candidates') candidate
    WHERE candidate->'theme_ids' ? (v_nom->>'theme_id')
      AND candidate->'roles' ? (v_nom->>'role')
      AND candidate->'entity_id' IS NOT DISTINCT FROM v_nom->'entity_id'
      AND candidate->'security_id' IS NOT DISTINCT FROM v_nom->'security_id'
      AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements_text(v_nom->'evidence_ids') wanted
        WHERE NOT EXISTS(
          SELECT 1 FROM jsonb_array_elements(candidate->'evidence') evidence
          WHERE evidence->>'item_id'=wanted.value
            AND evidence->'relationship_eligible'='true'::jsonb
            AND (v_nom->>'required_evidence_kind'<>'contradictory_primary' OR evidence->>'role'='opposing')
        )
      );
    IF v_matches<>1 THEN
      RAISE EXCEPTION 'candidate evidence relationship mismatch' USING ERRCODE='22023';
    END IF;
  END LOOP;
  v_response:=jsonb_build_object('request_id',p_request_id,'run_id',p_run_id,'packet_id',v_packet.id,'accepted_count',v_count,'duplicate',false,'nominations','[]'::jsonb);
  FOR v_nom IN SELECT value FROM jsonb_array_elements(p_payload->'nominations') LOOP
    v_id:=extensions.gen_random_uuid();
    v_entries:=v_entries||jsonb_build_array(jsonb_build_object('nomination_id',v_id,'nomination',v_nom));
    v_response:=jsonb_set(v_response,'{nominations}',(v_response->'nominations')||jsonb_build_array(jsonb_build_object('nomination_id',v_id,'state','pending','expires_at',v_created+INTERVAL '7 days')));
  END LOOP;
  INSERT INTO public.market_research_nomination_requests_v2(request_id,run_id,packet_id,reviewer_receipt_id,request_hash,accepted_count,response)
  VALUES(p_request_id,p_run_id,v_packet.id,v_receipt.receipt_id,v_hash,v_count,v_response);
  FOR v_entry IN SELECT value FROM jsonb_array_elements(v_entries) LOOP
    v_id:=(v_entry->>'nomination_id')::uuid;
    v_nom:=v_entry->'nomination';
    INSERT INTO public.market_research_nominations_v2(nomination_id,request_id,origin_run_id,packet_id,packet_hash,reviewer_receipt_id,actor_identity,reviewed_role,reference_manifest_id,theme_id,entity_id,security_id,relationship_role,reason,evidence_ids,required_evidence_kind,priority,created_at,expires_at)
    VALUES(v_id,p_request_id,p_run_id,v_packet.id,v_packet.packet_hash,v_receipt.receipt_id,v_receipt.actor_identity,v_receipt.reviewed_role,v_receipt.reference_manifest_id,v_nom->>'theme_id',v_nom->>'entity_id',v_nom->>'security_id',v_nom->>'role',v_nom->>'reason',v_nom->'evidence_ids',v_nom->>'required_evidence_kind',(v_nom->>'priority')::int,v_created,v_created+INTERVAL '7 days');
    INSERT INTO public.market_research_nomination_lifecycle_v2(receipt_id,nomination_id,transition_run_id,state,receipt_hash)
    VALUES(extensions.gen_random_uuid(),v_id,p_run_id,'pending',encode(extensions.digest(convert_to(public.market_canonical_jsonb(jsonb_build_object('nomination_id',v_id,'state','pending','created_at',v_created)),'UTF8'),'sha256'),'hex'));
  END LOOP;
  RETURN v_response;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR unique_violation THEN
  RAISE EXCEPTION 'invalid or conflicting research nomination request' USING ERRCODE='22023';
END;
$$;

CREATE OR REPLACE FUNCTION public.transition_research_nomination_v2(p_run_id UUID,p_nomination_id UUID,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_nom public.market_research_nominations_v2%ROWTYPE; v_head public.market_research_nomination_lifecycle_v2%ROWTYPE; v_state TEXT; v_receipt UUID; v_hash TEXT;
BEGIN
  IF jsonb_typeof(p_payload)<>'object' OR NOT(p_payload ?& ARRAY['state','reason','selection_descriptor'])
     OR (p_payload-ARRAY['state','reason','selection_descriptor'])<>'{}'::jsonb THEN RAISE EXCEPTION 'invalid nomination lifecycle request' USING ERRCODE='22023'; END IF;
  SELECT * INTO v_nom FROM public.market_research_nominations_v2 WHERE nomination_id=p_nomination_id;
  SELECT * INTO v_head FROM public.market_research_nomination_lifecycle_v2 WHERE nomination_id=p_nomination_id ORDER BY created_at DESC,receipt_id DESC LIMIT 1 FOR UPDATE;
  v_state:=p_payload->>'state';
  IF NOT FOUND OR (v_head.state='pending' AND v_state NOT IN ('pending','selected','rejected','expired'))
     OR (v_head.state='selected' AND v_state NOT IN ('resolved','rejected','expired'))
     OR v_head.state IN ('resolved','rejected','expired')
     OR (v_state='pending' AND NULLIF(trim(p_payload->>'reason'),'') IS NULL)
     OR (v_state='selected' AND p_payload->'selection_descriptor'='null'::jsonb)
     OR (v_state<>'selected' AND p_payload->'selection_descriptor'<>'null'::jsonb)
     OR (v_state='selected' AND p_run_id=v_nom.origin_run_id)
     OR (v_state='expired' AND statement_timestamp()<v_nom.expires_at)
     OR NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs run WHERE run.id=p_run_id)
     OR (v_state='selected' AND (
       jsonb_typeof(p_payload->'selection_descriptor')<>'object'
       OR NOT(p_payload->'selection_descriptor' ?& ARRAY['request_id','descriptor_hash','uncertain_outcome_barrier','execution_allowed'])
       OR ((p_payload->'selection_descriptor')-ARRAY['request_id','descriptor_hash','uncertain_outcome_barrier','execution_allowed'])<>'{}'::jsonb
       OR p_payload->'selection_descriptor'->'uncertain_outcome_barrier'<>'true'::jsonb
       OR p_payload->'selection_descriptor'->'execution_allowed'<>'false'::jsonb
       OR p_payload->'selection_descriptor'->>'descriptor_hash' !~ '^[0-9a-f]{64}$'
       OR NOT EXISTS(
         SELECT 1 FROM public.market_enrichment_request_descriptors descriptor
         JOIN public.market_enrichment_selection_manifests manifest ON manifest.id=descriptor.manifest_id
         JOIN public.market_discovery_stage_tasks task ON task.id=descriptor.task_id
         JOIN public.analysis_runs analysis ON analysis.id=descriptor.run_id
         WHERE descriptor.id=(p_payload->'selection_descriptor'->>'request_id')::uuid
           AND descriptor.run_id=p_run_id
           AND descriptor.content_hash=p_payload->'selection_descriptor'->>'descriptor_hash'
           AND task.state='planned' AND task.query_hash=descriptor.content_hash
           AND manifest.run_id=p_run_id
           AND manifest.created_at>v_nom.created_at
           AND ((analysis.scheduled_phase IS NOT NULL AND analysis.scheduled_market_date IS NOT NULL)
             OR (analysis.kind='on-demand' AND analysis.scheduled_phase IS NULL))
       )
     )) THEN
    RAISE EXCEPTION 'invalid nomination lifecycle transition' USING ERRCODE='22023';
  END IF;
  v_receipt:=extensions.gen_random_uuid();
  v_hash:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(jsonb_build_object('nomination_id',p_nomination_id,'run_id',p_run_id,'predecessor',v_head.receipt_id,'state',v_state,'reason',p_payload->'reason','selection_descriptor',p_payload->'selection_descriptor')),'UTF8'),'sha256'),'hex');
  INSERT INTO public.market_research_nomination_lifecycle_v2(receipt_id,nomination_id,transition_run_id,predecessor_receipt_id,state,reason,selection_descriptor,receipt_hash)
  VALUES(v_receipt,p_nomination_id,p_run_id,v_head.receipt_id,v_state,p_payload->>'reason',NULLIF(p_payload->'selection_descriptor','null'::jsonb),v_hash);
  RETURN jsonb_build_object('receipt_id',v_receipt,'nomination_id',p_nomination_id,'state',v_state,'receipt_hash',v_hash);
END;
$$;

CREATE OR REPLACE FUNCTION public.read_theme_memory_context(p_run_id UUID,p_as_of TIMESTAMPTZ DEFAULT statement_timestamp())
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_existing public.market_intelligence_memory_context_bindings_v2%ROWTYPE; v_context JSONB; v_revisions JSONB; v_nominations JSONB; v_urgent JSONB; v_material JSONB; v_radar JSONB; v_cursors JSONB:='[]'::jsonb; v_ref UUID; v_ref_hash TEXT; v_hash TEXT; v_active_available INT; v_due_available INT;
BEGIN
  SELECT * INTO v_existing FROM public.market_intelligence_memory_context_bindings_v2 WHERE run_id=p_run_id;
  IF FOUND THEN
    IF v_existing.snapshot_hash<>encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_existing.context),'UTF8'),'sha256'),'hex') THEN RAISE EXCEPTION 'memory context hash mismatch' USING ERRCODE='55000'; END IF;
    RETURN v_existing.context;
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.market_intelligence_runs WHERE id=p_run_id) THEN RAISE EXCEPTION 'unknown memory context run' USING ERRCODE='22023'; END IF;
  SELECT b.manifest_id,m.content_hash INTO v_ref,v_ref_hash FROM public.market_reference_run_bindings b JOIN public.market_reference_manifests m ON m.id=b.manifest_id WHERE b.run_id=p_run_id AND b.reference_status='healthy' ORDER BY b.created_at DESC LIMIT 1;
  SELECT COALESCE(jsonb_agg(to_jsonb(head)-ARRAY['created_at','priority_adverse','priority_overdue','priority_unresolved'] ORDER BY head.priority_adverse DESC,head.priority_overdue DESC,head.priority_unresolved DESC,head.next_review_at,head.episode_id),'[]'::jsonb)
  INTO v_revisions FROM (
    SELECT latest.*,(latest.opposing_source_ids<>'[]'::jsonb) AS priority_adverse,
      (latest.next_review_at<=p_as_of) AS priority_overdue,
      (latest.missing_questions<>'[]'::jsonb) AS priority_unresolved
    FROM (
      SELECT DISTINCT ON (revision.episode_id) revision.*
      FROM public.market_theme_episode_revisions_v2 revision
      WHERE revision.created_at<=p_as_of ORDER BY revision.episode_id,revision.revision DESC
    ) latest
    JOIN public.market_intelligence_run_events terminal
      ON terminal.run_id=latest.origin_run_id AND terminal.status='completed'
    WHERE latest.state='open' AND latest.expires_at>p_as_of
    ORDER BY priority_adverse DESC,priority_overdue DESC,priority_unresolved DESC,
      latest.next_review_at,latest.episode_id LIMIT 25
  ) head;
  SELECT COALESCE(jsonb_agg(to_jsonb(due)-'created_at' ORDER BY due.priority DESC,due.expires_at,due.nomination_id),'[]'::jsonb) INTO v_nominations FROM (
    SELECT nomination.* FROM public.market_research_nominations_v2 nomination
    JOIN LATERAL (SELECT state FROM public.market_research_nomination_lifecycle_v2 life WHERE life.nomination_id=nomination.nomination_id ORDER BY life.created_at DESC,life.receipt_id DESC LIMIT 1) current ON current.state='pending'
    WHERE nomination.expires_at>p_as_of AND nomination.created_at<=p_as_of ORDER BY nomination.priority DESC,nomination.expires_at,nomination.nomination_id LIMIT 12
  ) due;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('event_id',event.id,'event_type',event.event_type,'title',left(event.title,240),'summary',left(event.summary,500),'occurred_at',event.occurred_at,'effective_at',event.effective_at,'materiality',event.materiality,'confidence',event.confidence) ORDER BY event.materiality DESC,event.id),'[]'::jsonb) INTO v_urgent FROM (
    SELECT event.* FROM public.market_events event JOIN public.market_intelligence_run_events done ON done.run_id=event.run_id AND done.status='completed' WHERE event.created_at<=p_as_of ORDER BY event.materiality DESC,event.created_at DESC,event.id LIMIT 10
  ) event;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('theme_id',head.theme_id,'episode_id',head.episode_id,'revision_id',head.revision_id,'mechanism',head.theme_mechanism,'adverse',head.opposing_source_ids<>'[]'::jsonb,'next_review_at',head.next_review_at) ORDER BY jsonb_array_length(head.opposing_source_ids) DESC,head.next_review_at,head.episode_id),'[]'::jsonb) INTO v_material FROM (
    SELECT latest.* FROM (
      SELECT DISTINCT ON (revision.episode_id) revision.* FROM public.market_theme_episode_revisions_v2 revision
      WHERE revision.created_at<=p_as_of ORDER BY revision.episode_id,revision.revision DESC
    ) latest JOIN public.market_intelligence_run_events done ON done.run_id=latest.origin_run_id AND done.status='completed'
    WHERE latest.state='open' AND latest.expires_at>p_as_of
    ORDER BY (latest.opposing_source_ids<>'[]'::jsonb) DESC,(latest.next_review_at<=p_as_of) DESC,
      (latest.missing_questions<>'[]'::jsonb) DESC,latest.next_review_at,latest.episode_id LIMIT 10
  ) head;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('ticker',ticker,'reason',left(COALESCE(reason,''),500),'days_relevant',days_relevant) ORDER BY last_seen DESC NULLS LAST,ticker),'[]'::jsonb) INTO v_radar FROM (SELECT * FROM public.radar ORDER BY last_seen DESC NULLS LAST,ticker LIMIT 20) bounded;
  SELECT COALESCE(jsonb_agg(cursor.value ORDER BY cursor.ordinality),'[]'::jsonb)
  INTO v_cursors FROM (
    SELECT value,ordinality FROM jsonb_array_elements(
      public.read_market_discovery_cursor_context(p_run_id,100)->'source_cursors'
    ) WITH ORDINALITY LIMIT 100
  ) cursor;
  SELECT count(*) INTO v_active_available FROM (
    SELECT DISTINCT ON (revision.episode_id) revision.* FROM public.market_theme_episode_revisions_v2 revision
    WHERE revision.created_at<=p_as_of ORDER BY revision.episode_id,revision.revision DESC
  ) latest JOIN public.market_intelligence_run_events done ON done.run_id=latest.origin_run_id AND done.status='completed'
  WHERE latest.state='open' AND latest.expires_at>p_as_of;
  SELECT count(*) INTO v_due_available FROM public.market_research_nominations_v2 nomination
  JOIN LATERAL (SELECT state FROM public.market_research_nomination_lifecycle_v2 life WHERE life.nomination_id=nomination.nomination_id ORDER BY life.created_at DESC,life.receipt_id DESC LIMIT 1) current ON current.state='pending'
  WHERE nomination.expires_at>p_as_of AND nomination.created_at<=p_as_of;
  v_context:=jsonb_build_object('memory_version',2,'as_of',p_as_of,'reference_manifest_id',v_ref,'reference_hash',v_ref_hash,'active_theme_heads',v_revisions,'due_nominations',v_nominations,'urgent_events',v_urgent,'high_materiality_themes',v_material,'radar',v_radar,'source_cursors',v_cursors,'available_counts',jsonb_build_object('theme_heads',v_active_available,'due_nominations',v_due_available),'returned_counts',jsonb_build_object('theme_heads',jsonb_array_length(v_revisions),'due_nominations',jsonb_array_length(v_nominations),'urgent_events',jsonb_array_length(v_urgent),'high_materiality_themes',jsonb_array_length(v_material),'radar',jsonb_array_length(v_radar),'source_cursors',jsonb_array_length(v_cursors)),'deferred_counts',jsonb_build_object('theme_heads',GREATEST(v_active_available-jsonb_array_length(v_revisions),0),'due_nominations',GREATEST(v_due_available-jsonb_array_length(v_nominations),0)),'byte_truncated',false,'research_only',true,'execution_allowed',false);
  WHILE octet_length(v_context::text)>65536 AND jsonb_array_length(v_context->'active_theme_heads')>0 LOOP
    v_context:=jsonb_set(v_context,'{active_theme_heads}',(SELECT COALESCE(jsonb_agg(value ORDER BY ordinality),'[]'::jsonb) FROM jsonb_array_elements(v_context->'active_theme_heads') WITH ORDINALITY WHERE ordinality<jsonb_array_length(v_context->'active_theme_heads')));
    v_context:=jsonb_set(v_context,'{byte_truncated}','true'::jsonb);
  END LOOP;
  v_context:=jsonb_set(v_context,'{returned_counts,theme_heads}',to_jsonb(jsonb_array_length(v_context->'active_theme_heads')));
  v_context:=jsonb_set(v_context,'{deferred_counts,theme_heads}',to_jsonb(GREATEST((v_context->'available_counts'->>'theme_heads')::int-jsonb_array_length(v_context->'active_theme_heads'),0)));
  IF octet_length(v_context::text)>65536 THEN RAISE EXCEPTION 'protected memory context exceeds bound' USING ERRCODE='22023'; END IF;
  v_hash:=encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_context),'UTF8'),'sha256'),'hex');
  INSERT INTO public.market_intelligence_memory_context_bindings_v2(run_id,as_of,reference_manifest_id,reference_hash,selected_revision_ids,selected_nomination_ids,context,snapshot_hash)
  VALUES(p_run_id,p_as_of,v_ref,v_ref_hash,(SELECT COALESCE(jsonb_agg(value->'revision_id'),'[]'::jsonb) FROM jsonb_array_elements(v_context->'active_theme_heads') value),(SELECT COALESCE(jsonb_agg(value->'nomination_id'),'[]'::jsonb) FROM jsonb_array_elements(v_context->'due_nominations') value),v_context,v_hash)
  ON CONFLICT (run_id) DO NOTHING;
  SELECT * INTO v_existing FROM public.market_intelligence_memory_context_bindings_v2 WHERE run_id=p_run_id;
  IF v_existing.snapshot_hash<>encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_existing.context),'UTF8'),'sha256'),'hex') THEN RAISE EXCEPTION 'memory context hash mismatch' USING ERRCODE='55000'; END IF;
  RETURN v_existing.context;
END;
$$;

ALTER FUNCTION public.refresh_market_intelligence_context(UUID) RENAME TO refresh_market_intelligence_context_v2_internal;
CREATE OR REPLACE FUNCTION public.refresh_market_intelligence_context(p_run_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_base JSONB; v_memory JSONB; v_snapshot_hash TEXT;
BEGIN
  v_base:=public.refresh_market_intelligence_context_v2_internal(p_run_id);
  v_memory:=public.read_theme_memory_context(p_run_id,statement_timestamp());
  SELECT snapshot_hash INTO STRICT v_snapshot_hash FROM public.market_intelligence_memory_context_bindings_v2 WHERE run_id=p_run_id;
  RETURN v_base||jsonb_build_object('theme_memory',v_memory,'theme_memory_snapshot_hash',v_snapshot_hash,'radar',v_memory->'radar','urgent_events',v_memory->'urgent_events','high_materiality_themes',v_memory->'high_materiality_themes');
END;
$$;

CREATE OR REPLACE FUNCTION public.read_owner_intelligence_v2(p_limit INT DEFAULT 25)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_result JSONB; v_themes JSONB; v_run UUID; v_as_of TIMESTAMPTZ; v_available INT;
BEGIN
  IF p_limit NOT BETWEEN 1 AND 25 THEN RAISE EXCEPTION 'invalid intelligence projection limit' USING ERRCODE='22023'; END IF;
  SELECT packet.run_id,packet.created_at INTO v_run,v_as_of FROM public.market_evidence_packets packet JOIN public.market_intelligence_run_events done ON done.run_id=packet.run_id AND done.status='completed' WHERE packet.packet->>'contract_version'='2' AND packet.packet->>'execution_allowed'='false' ORDER BY packet.created_at DESC,packet.id DESC LIMIT 1;
  IF v_run IS NULL THEN RETURN jsonb_build_object('intelligence_version',2,'run_id',NULL,'data_as_of',NULL,'themes','[]'::jsonb,'companies','[]'::jsonb,'evidence','[]'::jsonb,'source_health','[]'::jsonb,'coverage',jsonb_build_object('mode','bounded','complete_market_coverage',false),'reference',jsonb_build_object('state','unavailable'),'scope',jsonb_build_object('research_only',true,'market_wide',true),'backlog',jsonb_build_object('available',0,'returned',0,'deferred',0,'byte_truncated',false),'omissions',jsonb_build_array('No completed validated v2 research packet is available.'),'boundaries',jsonb_build_object('research_only',true,'execution_disabled',true,'valuation_unavailable',true)); END IF;
  SELECT count(DISTINCT revision.episode_id) INTO v_available
  FROM public.market_theme_episode_revisions_v2 revision
  JOIN public.market_intelligence_run_events done ON done.run_id=revision.origin_run_id AND done.status='completed'
  WHERE revision.created_at<=v_as_of;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('theme_id',head.theme_id,'episode_id',head.episode_id,'revision_id',head.revision_id,'revision',head.revision,'mechanism',head.theme_mechanism,'subject',head.subject_identity,'jurisdiction',head.jurisdiction,'state',CASE WHEN head.state='open' AND head.expires_at>v_as_of THEN 'active' WHEN head.expires_at<=v_as_of THEN 'expired' ELSE 'closed' END,'first_seen',head.first_seen,'last_seen',head.last_seen,'next_review_at',head.next_review_at,'expires_at',head.expires_at,'adverse_evidence_count',jsonb_array_length(head.opposing_source_ids),'missing_questions',COALESCE((SELECT jsonb_agg(to_jsonb(left(question.value,500)) ORDER BY question.ordinality) FROM (SELECT value,ordinality FROM jsonb_array_elements_text(head.missing_questions) WITH ORDINALITY LIMIT 24) question),'[]'::jsonb),'invalidation_conditions',COALESCE((SELECT jsonb_agg(to_jsonb(left(condition.value,500)) ORDER BY condition.ordinality) FROM (SELECT value,ordinality FROM jsonb_array_elements_text(head.invalidation_conditions) WITH ORDINALITY LIMIT 24) condition),'[]'::jsonb)) ORDER BY (head.revision>1) DESC,(head.opposing_source_ids<>'[]'::jsonb) DESC,head.last_seen DESC,head.episode_id),'[]'::jsonb) INTO v_themes FROM (
    SELECT latest.* FROM (
      SELECT DISTINCT ON (episode_id) * FROM public.market_theme_episode_revisions_v2
      WHERE created_at<=v_as_of ORDER BY episode_id,revision DESC
    ) latest
    JOIN public.market_intelligence_run_events done
      ON done.run_id=latest.origin_run_id AND done.status='completed'
    ORDER BY (latest.revision>1) DESC,(latest.opposing_source_ids<>'[]'::jsonb) DESC,
      latest.last_seen DESC,latest.episode_id LIMIT p_limit
  ) head;
  v_result:=jsonb_build_object('intelligence_version',2,'data_as_of',v_as_of,'run_id',v_run,'themes',v_themes,
    'companies',(SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'company_id',COALESCE(candidate->>'entity_id',candidate->>'candidate_key'),
      'name',left(candidate->>'candidate_key',240),'ticker',candidate->'ticker',
      'outside_watchlist',CASE WHEN candidate->'ticker'='null'::jsonb THEN 'null'::jsonb ELSE to_jsonb(
        NOT COALESCE((SELECT input.holding_market_values ? (candidate->>'ticker') FROM public.market_intelligence_context_inputs input WHERE input.run_id=v_run),false)
        AND NOT COALESCE((SELECT EXISTS(SELECT 1 FROM jsonb_array_elements(binding.context->'radar') watch WHERE watch->>'ticker'=candidate->>'ticker') FROM public.market_intelligence_memory_context_bindings_v2 binding WHERE binding.run_id=v_run),false)
      ) END,
      'relationship_paths',COALESCE((SELECT jsonb_agg(to_jsonb(left(path.value,500)) ORDER BY path.ordinality) FROM (SELECT value,ordinality FROM jsonb_array_elements_text(candidate->'roles') WITH ORDINALITY LIMIT 4) path),'[]'::jsonb),
      'evidence_ids',COALESCE((SELECT jsonb_agg(ref->'item_id' ORDER BY ref->>'item_id') FROM (SELECT value AS ref FROM jsonb_array_elements(candidate->'evidence') LIMIT 8) bounded_ref),'[]'::jsonb),
      'missing_inputs',COALESCE((SELECT jsonb_agg(to_jsonb(left(missing.value,500)) ORDER BY missing.ordinality) FROM (SELECT value,ordinality FROM jsonb_array_elements_text(COALESCE(candidate->'suitability'->'missing_reasons','[]'::jsonb)) WITH ORDINALITY LIMIT 16) missing),'[]'::jsonb)
    ) ORDER BY candidate->>'candidate_key'),'[]'::jsonb) FROM (SELECT value AS candidate FROM public.market_evidence_packets packet CROSS JOIN LATERAL jsonb_array_elements(packet.packet->'research_candidates') WHERE packet.run_id=v_run ORDER BY value->>'candidate_key' LIMIT 12) candidates),
    'evidence',(SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'evidence_id',item->'item_id','label',left(item->'source_identity'->>'provider'||' evidence',256),
      'url',CASE WHEN item->>'canonical_url' ~ '^https://(www\.sec\.gov/(Archives|files|ixviewer)/|data\.sec\.gov/(submissions|api/xbrl)/|www\.federalregister\.gov/(documents|api/v1/documents)/|www\.whitehouse\.gov/briefing-room/|www\.energy\.gov/(articles|gdo|ceser|oe)/|www\.defense\.gov/(News|Contracts)/|api\.eia\.gov/v2/|www\.eia\.gov/(todayinenergy|electricity)/|api\.stlouisfed\.org/fred/|fred\.stlouisfed\.org/series/|api\.bls\.gov/publicAPI/|www\.bls\.gov/(news\.release|regions)/|apps\.bea\.gov/api/|www\.bea\.gov/(news|data)/|api\.gdeltproject\.org/api/v2/|www\.alphavantage\.co/query|finnhub\.io/api/v1/|query1\.finance\.yahoo\.com/(v8/finance/chart|v10/finance/quoteSummary)/)' AND item->>'canonical_url' !~ 'https://[^/]*@' AND item->>'canonical_url' !~ '^https://[^/]+:[0-9]+' THEN item->'canonical_url' ELSE 'null'::jsonb END,
      'passage',left(item->>'normalized_text',2000),
      'role',CASE WHEN EXISTS(SELECT 1 FROM public.market_evidence_packets packet2 CROSS JOIN LATERAL jsonb_array_elements(packet2.packet->'research_candidates') candidate2 CROSS JOIN LATERAL jsonb_array_elements(candidate2->'evidence') ref WHERE packet2.run_id=v_run AND ref->>'item_id'=item->>'item_id' AND ref->>'role'='opposing') THEN 'opposing' ELSE 'supporting' END,
      'retrieved_at',item->'retrieved_at'
    ) ORDER BY item->>'item_id'),'[]'::jsonb) FROM (SELECT value AS item FROM public.market_evidence_packets packet CROSS JOIN LATERAL jsonb_array_elements(packet.packet->'evidence') WHERE packet.run_id=v_run ORDER BY value->>'item_id' LIMIT 96) evidence_items),
    'source_health',(SELECT COALESCE(jsonb_agg(jsonb_build_object('provider',provider,'status',status,'retrieved_at',retrieved_at,'accepted_count',accepted_count,'dropped_count',dropped_count) ORDER BY provider,retrieved_at DESC),'[]'::jsonb) FROM (SELECT DISTINCT ON(provider) provider,status,retrieved_at,accepted_count,dropped_count FROM public.market_source_receipts WHERE run_id=v_run ORDER BY provider,retrieved_at DESC) source),
    'reference',COALESCE((SELECT jsonb_strip_nulls(jsonb_build_object('state',reference_status,'manifest_id',manifest_id,'as_of',reference_as_of,'age_seconds',reference_age_seconds)) FROM public.market_reference_run_bindings WHERE run_id=v_run ORDER BY created_at DESC LIMIT 1),jsonb_build_object('state','unavailable')),'coverage',jsonb_build_object('mode','bounded','complete_market_coverage',false),'scope',jsonb_build_object('research_only',true,'market_wide',true),'omissions',jsonb_build_array('Historical memory cannot become current evidence or suitability without current validation.'),'backlog',jsonb_build_object('available',v_available,'returned',jsonb_array_length(v_themes),'deferred',GREATEST(v_available-jsonb_array_length(v_themes),0),'byte_truncated',false),'boundaries',jsonb_build_object('research_only',true,'execution_disabled',true,'valuation_unavailable',true));
  WHILE octet_length(v_result::text)>98304 LOOP
    IF jsonb_array_length(v_result->'evidence')>0 THEN
      v_result:=jsonb_set(v_result,'{evidence}',(v_result->'evidence')-(jsonb_array_length(v_result->'evidence')-1));
      v_result:=jsonb_set(v_result,'{companies}',COALESCE((
        SELECT jsonb_agg(jsonb_set(company.value,'{evidence_ids}',COALESCE((
          SELECT jsonb_agg(to_jsonb(ref.value) ORDER BY ref.ordinality)
          FROM jsonb_array_elements_text(company.value->'evidence_ids') WITH ORDINALITY ref(value,ordinality)
          WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(v_result->'evidence') kept WHERE kept->>'evidence_id'=ref.value)
        ),'[]'::jsonb)) ORDER BY company.ordinality)
        FROM jsonb_array_elements(v_result->'companies') WITH ORDINALITY company(value,ordinality)
      ),'[]'::jsonb));
    ELSIF jsonb_array_length(v_result->'companies')>0 THEN
      v_result:=jsonb_set(v_result,'{companies}',(v_result->'companies')-(jsonb_array_length(v_result->'companies')-1));
    ELSIF jsonb_array_length(v_result->'themes')>0 THEN
      v_result:=jsonb_set(v_result,'{themes}',(v_result->'themes')-(jsonb_array_length(v_result->'themes')-1));
      v_result:=jsonb_set(v_result,'{backlog,returned}',to_jsonb(jsonb_array_length(v_result->'themes')));
      v_result:=jsonb_set(v_result,'{backlog,deferred}',to_jsonb(GREATEST(v_available-jsonb_array_length(v_result->'themes'),0)));
    ELSE
      RAISE EXCEPTION 'owner intelligence projection cannot satisfy byte bound' USING ERRCODE='22023';
    END IF;
    v_result:=jsonb_set(v_result,'{backlog,byte_truncated}','true'::jsonb);
  END LOOP;
  RETURN v_result;
END;
$$;

ALTER TABLE public.market_theme_episode_revisions_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_reviewer_identity_receipts_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_research_nomination_requests_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_research_nominations_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_research_nomination_lifecycle_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_intelligence_memory_context_bindings_v2 ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.market_theme_episode_revisions_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON public.market_reviewer_identity_receipts_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON public.market_research_nomination_requests_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON public.market_research_nominations_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON public.market_research_nomination_lifecycle_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON public.market_intelligence_memory_context_bindings_v2 FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT SELECT ON public.market_theme_episode_revisions_v2,public.market_reviewer_identity_receipts_v2,public.market_research_nomination_requests_v2,public.market_research_nominations_v2,public.market_research_nomination_lifecycle_v2,public.market_intelligence_memory_context_bindings_v2 TO stock_agent_release_reader;
CREATE POLICY release_theme_memory_v2_select ON public.market_theme_episode_revisions_v2 FOR SELECT TO stock_agent_release_reader USING(true);
CREATE POLICY release_reviewer_identity_v2_select ON public.market_reviewer_identity_receipts_v2 FOR SELECT TO stock_agent_release_reader USING(true);
CREATE POLICY release_nomination_request_v2_select ON public.market_research_nomination_requests_v2 FOR SELECT TO stock_agent_release_reader USING(true);
CREATE POLICY release_nomination_v2_select ON public.market_research_nominations_v2 FOR SELECT TO stock_agent_release_reader USING(true);
CREATE POLICY release_nomination_lifecycle_v2_select ON public.market_research_nomination_lifecycle_v2 FOR SELECT TO stock_agent_release_reader USING(true);
CREATE POLICY release_memory_context_v2_select ON public.market_intelligence_memory_context_bindings_v2 FOR SELECT TO stock_agent_release_reader USING(true);

REVOKE ALL ON FUNCTION public.reject_theme_memory_v2_mutation(),public.refresh_market_intelligence_context_v2_internal(UUID) FROM PUBLIC,anon,authenticated,service_role,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
REVOKE ALL ON FUNCTION public.market_theme_episode_uuid_v5(UUID,TEXT),public.record_theme_episode_revision_v2(UUID,JSONB),public.record_research_review_identity_v2(UUID,UUID,JSONB),public.record_research_nominations(UUID,UUID,JSONB),public.transition_research_nomination_v2(UUID,UUID,JSONB),public.read_theme_memory_context(UUID,TIMESTAMPTZ),public.refresh_market_intelligence_context(UUID) FROM PUBLIC,anon,authenticated,stock_agent_dashboard,stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.record_theme_episode_revision_v2(UUID,JSONB),public.record_research_review_identity_v2(UUID,UUID,JSONB),public.transition_research_nomination_v2(UUID,UUID,JSONB),public.read_theme_memory_context(UUID,TIMESTAMPTZ),public.refresh_market_intelligence_context(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_research_nominations(UUID,UUID,JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.read_owner_intelligence_v2(INT) FROM PUBLIC,anon,authenticated,service_role,stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.read_owner_intelligence_v2(INT) TO stock_agent_dashboard;
