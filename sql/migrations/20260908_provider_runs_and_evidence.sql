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

CREATE TABLE app.market_quote_cache (
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'),
  price NUMERIC(24, 8) NOT NULL CHECK (price > 0),
  previous_close NUMERIC(24, 8) CHECK (previous_close IS NULL OR previous_close > 0),
  provider TEXT NOT NULL CHECK (char_length(provider) BETWEEN 1 AND 80),
  source_timestamp TIMESTAMPTZ NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL,
  session TEXT NOT NULL CHECK (session IN ('PRE','REGULAR','POST','CLOSED')),
  adjustment_status TEXT NOT NULL CHECK (
    adjustment_status IN ('raw','adjusted','corporate_action_pending')
  ),
  status TEXT NOT NULL CHECK (
    status IN ('fresh','delayed','stale','conflicting','unavailable')
  ),
  content_digest TEXT NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
  conflict_basis_points INT CHECK (conflict_basis_points IS NULL OR conflict_basis_points >= 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id, ticker),
  CHECK (source_timestamp <= retrieved_at + interval '5 minutes')
);

CREATE TABLE app.corporate_action_states (
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL CHECK (ticker ~ '^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$'),
  state TEXT NOT NULL CHECK (state IN ('clear','suspected','needs_review')),
  event_type TEXT CHECK (
    event_type IS NULL OR event_type IN ('split','reverse_split','symbol_change','merger','delisting')
  ),
  ratio NUMERIC(24, 8) CHECK (ratio IS NULL OR ratio > 0),
  source_identifier TEXT CHECK (
    source_identifier IS NULL OR char_length(source_identifier) BETWEEN 1 AND 200
  ),
  source_reference TEXT CHECK (
    source_reference IS NULL OR char_length(source_reference) BETWEEN 1 AND 500
  ),
  reason_code TEXT NOT NULL CHECK (
    reason_code IN ('confirmed_and_normalized','unverified_corporate_action',
                    'corporate_action_conflict','no_corporate_action_signal')
  ),
  detected_at TIMESTAMPTZ NOT NULL,
  reviewed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id, ticker),
  CHECK (state <> 'clear' OR reason_code IN ('confirmed_and_normalized','no_corporate_action_signal'))
);

ALTER TABLE app.market_quote_cache OWNER TO stock_agent_migration_owner;
ALTER TABLE app.corporate_action_states OWNER TO stock_agent_migration_owner;
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.market_quote_cache
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();
CREATE TRIGGER reject_owner_id_mutation
  BEFORE UPDATE OF owner_id ON app.corporate_action_states
  FOR EACH ROW EXECUTE FUNCTION app.reject_owner_id_mutation();

ALTER TABLE app.market_quote_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.market_quote_cache FORCE ROW LEVEL SECURITY;
ALTER TABLE app.corporate_action_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.corporate_action_states FORCE ROW LEVEL SECURITY;
CREATE POLICY market_quote_cache_owner_select ON app.market_quote_cache
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY market_quote_cache_executor_all ON app.market_quote_cache
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY corporate_action_states_owner_select ON app.corporate_action_states
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));
CREATE POLICY corporate_action_states_executor_all ON app.corporate_action_states
  FOR ALL TO stock_agent_migration_owner USING (true) WITH CHECK (true);
CREATE POLICY radar_owner_quote_select ON app.radar
  FOR SELECT TO authenticated
  USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()));

REVOKE ALL ON app.market_quote_cache, app.corporate_action_states
  FROM PUBLIC, anon, authenticated, service_role,
       stock_agent_gateway, stock_agent_scheduler, stock_agent_telegram, stock_agent_backup;
GRANT SELECT (owner_id, ticker, price, previous_close, provider, source_timestamp,
              retrieved_at, session, adjustment_status, status, conflict_basis_points)
  ON app.market_quote_cache TO authenticated;
GRANT SELECT (owner_id, ticker, state, event_type, ratio, reason_code, detected_at, reviewed_at)
  ON app.corporate_action_states TO authenticated;
GRANT SELECT (owner_id, ticker) ON app.radar TO authenticated;

CREATE VIEW api.market_quotes WITH (security_invoker = true) AS
SELECT quote.ticker,
       quote.price,
       quote.previous_close,
       quote.provider,
       quote.source_timestamp AS as_of,
       quote.retrieved_at,
       quote.session,
       quote.adjustment_status,
       quote.status,
       quote.conflict_basis_points,
       coalesce(action.state, 'clear') AS corporate_action_state,
       coalesce(action.state IN ('suspected','needs_review'), false) AS alerts_suppressed
FROM app.market_quote_cache AS quote
LEFT JOIN app.corporate_action_states AS action
  ON action.owner_id = quote.owner_id AND action.ticker = quote.ticker
WHERE EXISTS (
  SELECT 1 FROM app.holdings AS holding
  WHERE holding.owner_id = quote.owner_id AND holding.ticker = quote.ticker
) OR EXISTS (
  SELECT 1 FROM app.radar AS watched
  WHERE watched.owner_id = quote.owner_id AND watched.ticker = quote.ticker
);
ALTER VIEW api.market_quotes OWNER TO stock_agent_migration_owner;
REVOKE ALL ON api.market_quotes FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON api.market_quotes TO authenticated;

CREATE INDEX market_quote_cache_freshness_idx
  ON app.market_quote_cache(owner_id, status, retrieved_at DESC);
CREATE INDEX corporate_action_states_review_idx
  ON app.corporate_action_states(owner_id, state, updated_at DESC);
