-- Provider-neutral run evidence. Model submissions reference these server-owned facts by digest.

CREATE TABLE app.run_evidence (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  run_id UUID NOT NULL,
  evidence_id TEXT NOT NULL CHECK (char_length(evidence_id) BETWEEN 1 AND 100),
  source_run_id UUID,
  category TEXT NOT NULL CHECK (category IN (
    'market_snapshot', 'source_search', 'filing', 'fundamentals', 'news',
    'technicals', 'corporate_action', 'issuer', 'exchange'
  )),
  source_identifier TEXT NOT NULL CHECK (char_length(source_identifier) BETWEEN 1 AND 200),
  reference_identifier TEXT CHECK (
    reference_identifier IS NULL OR char_length(reference_identifier) BETWEEN 1 AND 500
  ),
  observed_at TIMESTAMPTZ,
  retrieved_at TIMESTAMPTZ NOT NULL,
  revalidated_at TIMESTAMPTZ,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  claims JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
    jsonb_typeof(claims) = 'array' AND jsonb_array_length(claims) <= 10
    AND octet_length(claims::text) <= 6000
  ),
  status TEXT NOT NULL CHECK (status IN (
    'fresh', 'stale', 'conflicting', 'missing', 'no_new_material_evidence',
    'suspected', 'needs_review', 'clear'
  )),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, run_id, evidence_id),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE CASCADE,
  FOREIGN KEY (owner_id, source_run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE RESTRICT,
  CHECK (revalidated_at IS NULL OR revalidated_at >= retrieved_at)
);

CREATE TABLE app.source_search_receipts (
  id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  run_id UUID NOT NULL,
  searched_at TIMESTAMPTZ NOT NULL,
  categories TEXT[] NOT NULL CHECK (
    cardinality(categories) BETWEEN 1 AND 8
    AND categories <@ ARRAY['filing','fundamentals','news','issuer','exchange','sector','macro']::text[]
  ),
  sources JSONB NOT NULL CHECK (
    jsonb_typeof(sources) = 'array' AND jsonb_array_length(sources) BETWEEN 1 AND 20
    AND octet_length(sources::text) <= 4000
  ),
  result_status TEXT NOT NULL CHECK (
    result_status IN ('material_evidence_found', 'no_new_material_evidence', 'source_unavailable')
  ),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, id),
  FOREIGN KEY (owner_id, run_id)
    REFERENCES app.analysis_runs(owner_id, id) ON DELETE CASCADE
);

ALTER TABLE app.run_evidence OWNER TO stock_agent_migration_owner;
ALTER TABLE app.source_search_receipts OWNER TO stock_agent_migration_owner;

CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.run_evidence
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.source_search_receipts
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

ALTER TABLE app.run_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.run_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE app.source_search_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.source_search_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY run_evidence_executor_all ON app.run_evidence
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY source_search_receipts_executor_all ON app.source_search_receipts
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);

REVOKE ALL ON app.run_evidence, app.source_search_receipts
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;

CREATE INDEX run_evidence_run_category_idx
  ON app.run_evidence(owner_id, run_id, category, retrieved_at DESC);
CREATE INDEX source_search_receipts_run_idx
  ON app.source_search_receipts(owner_id, run_id, searched_at DESC);
