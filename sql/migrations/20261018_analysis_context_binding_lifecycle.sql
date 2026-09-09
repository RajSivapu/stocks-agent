BEGIN;

-- start_run creates the analysis row before the collector can calculate and
-- reserve its intelligence plan. Freeze the research-only memory snapshot at
-- that existing lifecycle boundary so the scheduled call order can proceed.
ALTER TABLE public.market_intelligence_memory_context_bindings_v2
  DROP CONSTRAINT market_intelligence_memory_context_bindings_v2_run_id_fkey;
ALTER TABLE public.market_intelligence_memory_context_bindings_v2
  ADD CONSTRAINT market_intelligence_memory_context_bindings_v2_run_id_fkey
  FOREIGN KEY (run_id) REFERENCES public.analysis_runs(id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION public.read_theme_memory_context(p_run_id UUID,p_as_of TIMESTAMPTZ DEFAULT statement_timestamp())
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_existing public.market_intelligence_memory_context_bindings_v2%ROWTYPE; v_context JSONB; v_revisions JSONB; v_nominations JSONB; v_urgent JSONB; v_material JSONB; v_radar JSONB; v_cursors JSONB:='[]'::jsonb; v_ref UUID; v_ref_hash TEXT; v_hash TEXT; v_active_available INT; v_due_available INT;
BEGIN
  SELECT * INTO v_existing FROM public.market_intelligence_memory_context_bindings_v2 WHERE run_id=p_run_id;
  IF FOUND THEN
    IF v_existing.snapshot_hash<>encode(extensions.digest(convert_to(public.market_canonical_jsonb(v_existing.context),'UTF8'),'sha256'),'hex') THEN RAISE EXCEPTION 'memory context hash mismatch' USING ERRCODE='55000'; END IF;
    RETURN v_existing.context;
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.analysis_runs WHERE id=p_run_id) THEN RAISE EXCEPTION 'unknown memory context run' USING ERRCODE='22023'; END IF;
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


REVOKE ALL ON FUNCTION public.read_theme_memory_context(UUID,TIMESTAMPTZ)
  FROM PUBLIC,anon,authenticated,stock_agent_dashboard,
       stock_agent_release_reader,stock_agent_release_reader_runtime;
GRANT EXECUTE ON FUNCTION public.read_theme_memory_context(UUID,TIMESTAMPTZ)
  TO service_role;

COMMIT;
