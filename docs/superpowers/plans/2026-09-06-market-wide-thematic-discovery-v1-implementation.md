# Market-Wide Thematic Discovery V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete V1-C3 with bounded cross-sector discovery that can turn ticker-free market events into evidence-backed research candidates outside the owner's existing holdings and watchlist while preserving every existing action and release safety gate.

**Architecture:** Replace round-robin target assignment with one durable, capability-aware, staged collector. The collector persists ticker-independent events, resolves entities against a dated U.S.-listed reference, enriches a bounded queue with primary exposure and market evidence, then produces separate research and action lanes for the existing Analyst, Checker, and deterministic policy gateway.

**Tech Stack:** Python 3.14, TypeScript/Deno, PostgreSQL/Supabase, React, pytest, Deno tests, Vitest, Playwright, bounded HTTPS/RSS/HTML/JSON parsing

**Spec:** `docs/superpowers/specs/2026-09-06-market-wide-thematic-discovery-v1-design.md`

## Global Constraints

- Zero incremental dollars: no paid provider, paid trial, brokerage API, metered model API, or paid fallback.
- Owner-only and suggestion-only; `execution_allowed` remains `false` in configuration and gateway policy.
- Exactly one `collect_market_intelligence.py` invocation per scheduled routine.
- Analyst and Checker use one persisted bounded packet and perform no arbitrary browsing.
- `complete_market_coverage` remains `false`; user-facing language says bounded cross-sector discovery.
- Missing, stale, ambiguous, contradictory, unsupported, or quota-blocked evidence cannot authorize an action.
- Research candidates may survive missing portfolio suitability; action candidates may not.
- No duplicate production run for evidence; use only normal scheduled receipts.
- Existing packet maximums remain 12 candidates, 8 evidence items per candidate, 2,000 characters per item, and 98,304 serialized bytes.
- Existing terminal payload bounds remain 100 receipts, 500 items, 100 events, 500 relationships, 100 rankings, 32 KiB coverage, and 1 MiB total.
- Alpha Vantage stays at or below 20 requests/day and is optional; the zero-key baseline must work without it.
- SEC requests use a descriptive User-Agent and an application throttle below the published 10 requests/second ceiling.
- Alert V3 stays disabled/shadow-only; this plan does not arm alerts or add a 15-minute schedule.
- New database objects are additive, least-privilege, included in release evidence and restore coverage, and never weaken existing RLS or append-only rules.

---

## File structure

New focused modules:

- `config/intelligence_sources.json`: versioned source capabilities, hosts, path rules, query kinds, budgets, retention class, and health.
- `config/theme_taxonomy.json`: versioned theme vocabulary, value-chain roles, adverse paths, and query packs without permanent ticker buy lists.
- `lib/intelligence/planner.py`: capability matching, fair task planning, adaptive stage budgets, stable task IDs, and plan coverage.
- `lib/intelligence/universe.py`: SEC/Nasdaq reference parsing, security identity, eligibility, and reference manifests.
- `lib/intelligence/cursors.py`: persisted overlap windows, bounded catch-up, truncation, and backlog status.
- `lib/intelligence/discovery.py`: ticker-independent event construction and signal clustering.
- `lib/intelligence/entities.py`: issuer/entity/security resolution and ambiguity handling.
- `lib/intelligence/exposure.py`: typed exposure facts, primary-passage validation, and relationship eligibility.
- `lib/intelligence/screening.py`: bounded broad-screen normalization and transparent screen coverage.
- `lib/intelligence/research_queue.py`: deterministic enrichment selection and next-run research nominations.
- `lib/intelligence/providers/rss.py`: bounded safe RSS/Atom parser shared by official feed adapters.
- `lib/intelligence/providers/white_house.py`: bounded category/sitemap collection.
- `lib/intelligence/providers/energy.py`: DOE and EIA feed/statistics adapters.
- `lib/intelligence/providers/sec.py`: issuer map, submissions, Company Facts, filing index/document, and Form 4 parsing.
- `sql/migrations/20261005_market_wide_discovery.sql` through
  `sql/migrations/20261012_theme_memory_research_nominations.sql`: immutable additive discovery,
  reference-transfer, cursor, official-source, issuer-name, enrichment, research/suitability, and
  theme-memory ledgers. `20261004` is the separate immutable production reconciliation baseline.

Existing files retain these responsibilities:

- `lib/intelligence/pipeline.py`: orchestration, checkpoint recovery, terminal persistence, and one bounded return packet.
- `lib/intelligence/ranking.py`: reproducible research-priority and action-suitability scoring.
- `lib/intelligence/packet.py`: bounded two-lane packet serialization.
- `lib/intelligence/themes.py` and `relationships.py`: typed theme and graph contracts.
- `lib/intelligence/policy.py` and `types.py`: checked-in policy validation.
- `scripts/collect_market_intelligence.py`: the sole scheduled collector entrypoint.
- `supabase/functions/market-briefing-gateway/_shared/*`: protected contracts, persistence, context, quote enrichment, and owner read models.
- `sql/schema.sql`: canonical schema matching every additive migration.
- `apps/web/src/*`: owner-only research, coverage, and evidence presentation.

---

### Task 1: Versioned source capabilities and fair collection planner

**Files:**
- Create: `config/intelligence_sources.json`
- Create: `lib/intelligence/planner.py`
- Create: `tests/test_intelligence_planner.py`
- Modify: `lib/intelligence/types.py`
- Modify: `lib/intelligence/policy.py`
- Modify: `config/settings.json`
- Modify: `tests/test_intelligence_policy.py`

**Interfaces:**
- Produces: `SourceCapability`, `DiscoveryTask`, `DiscoveryPlan`, `load_source_capabilities()`, and `build_discovery_plan()`.
- Consumes: phase, protected run time, seed themes, configured provider budgets, enabled credentials by name only, and the current reference version.
- Later tasks rely on `DiscoveryTask.task_id`, `capability_id`, `query_kind`, `theme_id`, `provider`, `window`, `dependencies`, and `max_attempts` remaining stable across retry.

- [ ] **Step 1: Write failing policy and planner tests**

```python
def test_real_pre_market_plan_gives_every_seed_theme_a_supported_discovery_task():
    policy = load_intelligence_policy(load_settings())
    plan = build_discovery_plan(
        policy,
        load_source_capabilities(),
        phase="pre-market",
        run_id="11111111-1111-4111-8111-111111111111",
        reference_version="sec:fixture-v1",
        requested_window=WINDOW,
        available_credentials=frozenset(),
    )
    covered = {task.theme_id for task in plan.tasks if task.stage == "signals"}
    assert set(policy.seed_domains) <= covered
    assert all(task.query_kind == plan.capabilities[task.capability_id].query_kind for task in plan.tasks)
    assert all(task.provider != "alpha_vantage" for task in plan.tasks if task.requires_credential)


def test_plan_is_fair_and_never_round_robins_unsupported_provider_target_pairs():
    plan = fixture_plan(themes=("magnets", "energy", "healthcare"), request_budget=3)
    assert [task.theme_id for task in plan.tasks] == ["energy", "healthcare", "magnets"]
    assert plan.coverage["unsupported_pairs"] == []


def test_provider_can_have_separate_keyless_and_keyed_capabilities():
    capabilities = load_source_capabilities()
    assert capabilities["eia_today_in_energy_rss"].required_credential is None
    assert capabilities["eia_statistics_v2"].required_credential == "EIA_API_KEY"
    assert capabilities["eia_today_in_energy_rss"].provider == "eia"
    assert capabilities["eia_statistics_v2"].provider == "eia"
```

- [ ] **Step 2: Run the new tests and verify the present planner fails**

Run: `.venv/bin/python -m pytest tests/test_intelligence_planner.py tests/test_intelligence_policy.py -q`
Expected: FAIL because `planner.py`, capability policy fields, and `seed_domains` on `IntelligencePolicy` do not exist.

- [ ] **Step 3: Add immutable planner types**

```python
QueryKind = Literal[
    "feed", "theme_search", "issuer_submissions", "filing_document",
    "series", "screener", "quote", "universe",
]

@dataclass(frozen=True, slots=True)
class SourceCapability:
    capability_id: str
    provider: str
    query_kind: QueryKind
    themes: frozenset[str]
    phases: frozenset[str]
    allowed_hosts: frozenset[str]
    allowed_path_patterns: tuple[str, ...]
    required_credential: str | None
    authority: str
    retention_class: str
    max_requests_per_run: int
    max_items_per_request: int
    requirement_tier: Literal["required_baseline", "optional"]
    health: Literal["enabled", "configuration_missing", "unsupported", "degraded", "disabled"]
    enabled: bool

@dataclass(frozen=True, slots=True)
class DiscoveryTask:
    task_id: str
    stage: Literal["reference", "signals", "resolve", "enrich", "screen", "quote"]
    provider: str
    capability_id: str
    query_kind: QueryKind
    theme_id: str | None
    query: Mapping[str, object]
    window: Mapping[str, str]
    dependencies: tuple[str, ...]
    max_attempts: int
    requires_credential: bool
```

Add `seed_domains`, `source_capability_version`, `theme_taxonomy_version`,
`required_baseline_capability_ids`, and `adaptive_enrichment_budget` to `IntelligencePolicy`. Key the
registry by unique `capability_id`; keep provider as a separate aggregate quota key. Reject unknown
query kinds, unapproved hosts, positive paid-fallback flags, duplicate capability IDs, missing or
disabled required-baseline IDs, phase totals above provider ceilings, and capability entries whose
configured path does not match an allowed host.

- [ ] **Step 4: Implement deterministic capability matching and fairness**

```python
def build_discovery_plan(
    policy: IntelligencePolicy,
    capabilities: Mapping[str, SourceCapability],
    *,
    phase: str,
    run_id: str,
    reference_version: str,
    requested_window: Mapping[str, str],
    available_credentials: frozenset[str],
) -> DiscoveryPlan:
    """Return a stable bounded plan; unsupported work becomes coverage, never a guessed request."""
```

Sort theme opportunities by `(last_completed_scan, theme_id, provider_priority, provider)`. Give each
enabled theme one zero-key capable task before allocating repeat or credentialed tasks. Reserve
holding quotes and the maximum adaptive enrichment allowance before broad screens. Derive every
`task_id` with UUIDv5 over canonical task inputs.

- [ ] **Step 5: Replace query-term gaps with capability-specific query packs**

Move provider query semantics from sparse `settings.json.provider_query_terms` into
`intelligence_sources.json`. Alpha Vantage uses its documented fixed topic values; it never receives
free-form theme text. SEC and macro series tasks require identifiers. Finnhub and Yahoo quote tasks
require securities. GDELT and Federal Register receive bounded theme terms.

- [ ] **Step 6: Run focused policy and planner tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_planner.py tests/test_intelligence_policy.py -q`
Expected: PASS, including a real-configuration assertion that every required seed theme has an
executable zero-key discovery path or an explicit configuration error, and that required baseline
capabilities are independently identifiable from optional capabilities on the same provider.

- [ ] **Step 7: Commit the planner boundary**

```bash
git add config/intelligence_sources.json config/settings.json lib/intelligence/planner.py lib/intelligence/policy.py lib/intelligence/types.py tests/test_intelligence_planner.py tests/test_intelligence_policy.py
git commit -m "feat: add capability-aware discovery planning"
```

---

### Task 2: Add durable discovery reference and stage ledgers

**Files:**
- Create: `sql/migrations/20261005_market_wide_discovery.sql`
- Create: `tests/test_market_wide_discovery_sql.py`
- Modify: `sql/schema.sql`
- Modify: `scripts/verify_market_intelligence_migration.py`
- Modify: `tests/test_verify_market_intelligence_migration.py`
- Modify: `supabase/functions/market-briefing-gateway/_shared/contracts.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/intelligence.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/repository.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/repository_test.ts`
- Modify: `lib/gateway.py`
- Modify: `scripts/protected_evidence.py`
- Modify: `scripts/export_recovery_bundle.py`
- Modify: `scripts/verify_recovery_bundle.py`
- Modify: `scripts/managed_isolated_restore.py`
- Modify: `tests/test_recovery_bundle.py`
- Modify: `tests/test_managed_isolated_restore.py`

**Interfaces:**
- Produces protected operations `record_discovery_reference`, `checkpoint_discovery_stage`, and `read_discovery_context`.
- Produces tables `market_reference_manifests`, `market_security_reference_revisions`, `market_discovery_stage_tasks`, `market_exposure_facts`, `market_theme_episode_revisions`, and `market_research_nominations`.
- Task state is append-only through revisions or a constrained `planned -> attempting -> succeeded|failed|deferred|uncertain` transition enforced by security-definer functions.

- [ ] **Step 1: Write schema contract tests**

```python
def test_discovery_migration_adds_all_ledgers_and_protects_them():
    sql = MIGRATION.read_text()
    for table in (
        "market_reference_manifests", "market_security_reference_revisions",
        "market_discovery_stage_tasks", "market_exposure_facts",
        "market_theme_episode_revisions", "market_research_nominations",
    ):
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in sql
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in sql
        assert table in release_reader_relations(sql)


def test_discovery_task_transition_rejects_reselection_after_attempt():
    task = insert_task(state="attempting", query_hash=HASH_A)
    with pytest.raises(DatabaseError, match="discovery task identity mismatch"):
        checkpoint_task(task.id, state="succeeded", query_hash=HASH_B)
```

- [ ] **Step 2: Run schema tests and verify failure**

Run: `.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py tests/test_verify_market_intelligence_migration.py -q`
Expected: FAIL because the migration and protected operations do not exist.

- [ ] **Step 3: Add the additive tables and exact constraints**

```sql
CREATE TABLE IF NOT EXISTS public.market_discovery_stage_tasks (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES public.market_intelligence_runs(id) ON DELETE RESTRICT,
  stage TEXT NOT NULL CHECK (stage IN ('reference','signals','resolve','enrich','screen','quote')),
  capability_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  query_kind TEXT NOT NULL CHECK (query_kind IN ('feed','theme_search','issuer_submissions','filing_document','series','screener','quote','universe')),
  query_hash TEXT NOT NULL CHECK (query_hash ~ '^[0-9a-f]{64}$'),
  dependency_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  requested_window JSONB NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('planned','attempting','succeeded','failed','deferred','uncertain')),
  attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 10),
  request_budget INT NOT NULL CHECK (request_budget BETWEEN 0 AND 100),
  result JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (run_id, stage, query_hash)
);
```

Apply equivalent bounded columns, foreign keys, hashes, validity intervals, and size checks to the
other five tables. Use immutable revision rows for manifests, securities, exposure facts, and theme
episodes. Research nominations use constrained lifecycle transitions and never mutate owner assets.

- [ ] **Step 4: Add protected RPCs and exact TypeScript parsers**

```typescript
export interface DiscoveryStageTask {
  id: string;
  stage: "reference" | "signals" | "resolve" | "enrich" | "screen" | "quote";
  provider: string;
  capability_id: string;
  query_kind: DiscoveryQueryKind;
  query_hash: string;
  dependency_ids: string[];
  requested_window: Record<string, string>;
  state: "planned" | "attempting" | "succeeded" | "failed" | "deferred" | "uncertain";
  attempt_count: number;
  request_budget: number;
  result: Record<string, unknown>;
}
```

Route `record_discovery_reference`, `checkpoint_discovery_stage`, and `read_discovery_context`
through the gateway handler's explicit operation allowlist. Require the existing service-owned
collection authorization for writes and owner authorization for bounded reads. Handler tests cover
anonymous, non-owner, wrong-stage, replay, and oversized requests. Parsers reject extra fields,
invalid UUIDs, oversized arrays/objects, wrong-run dependencies,
terminal-task rewrites, unapproved providers, and any caller-supplied price, valuation, portfolio
overlap, action, or authority field.

- [ ] **Step 5: Add least-privilege and recovery coverage**

Grant writes only to service-owned protected functions. Grant the dashboard role only the columns
needed for Research and coverage views. Add all six tables to release evidence, encrypted export,
isolated restore, exact snapshot comparison, and cleanup verification. Update the concrete
`READ_TABLES`, `REQUIRED_RECOVERY_RECORDS`, `_RESTORE_TABLES`, relationship validators, restore order,
and managed isolated-restore dataset checks; prove missing, orphaned, reordered, and altered discovery
records fail closed.

- [ ] **Step 6: Run Python and Deno contract tests**

Run: `.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py tests/test_verify_market_intelligence_migration.py -q`
Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts`
Run: `.venv/bin/python -m pytest tests/test_recovery_bundle.py tests/test_managed_isolated_restore.py -q`
Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/handler_test.ts`
Expected: PASS with mutation, replay, size, RLS, operation authorization, export/restore order, and
wrong-run cases covered.

- [ ] **Step 7: Commit the durable ledger**

```bash
git add sql/migrations/20261005_market_wide_discovery.sql sql/schema.sql scripts/verify_market_intelligence_migration.py tests/test_market_wide_discovery_sql.py tests/test_verify_market_intelligence_migration.py supabase/functions/market-briefing-gateway/_shared/contracts.ts supabase/functions/market-briefing-gateway/_shared/handler.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts supabase/functions/market-briefing-gateway/_shared/intelligence.ts supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts supabase/functions/market-briefing-gateway/_shared/repository.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts lib/gateway.py scripts/protected_evidence.py scripts/export_recovery_bundle.py scripts/verify_recovery_bundle.py scripts/managed_isolated_restore.py tests/test_recovery_bundle.py tests/test_managed_isolated_restore.py
git commit -m "feat: persist market discovery stages and references"
```

---

### Task 3: Build the dated U.S.-listed security reference

**Files:**
- Create: `lib/intelligence/universe.py`
- Create: `tests/test_intelligence_universe.py`
- Create: `tests/fixtures/intelligence/sec_company_tickers.json`
- Create: `tests/fixtures/intelligence/nasdaq_listed.txt`
- Create: `tests/fixtures/intelligence/other_listed.txt`
- Modify: `scripts/collect_market_intelligence.py`
- Modify: `config/intelligence_sources.json`

**Interfaces:**
- Produces `ReferenceManifest`, `IssuerIdentity`, `SecurityIdentity`, `parse_sec_company_tickers()`, `parse_symbol_directory()`, `merge_reference_sources()`, and `eligible_for_research()`.
- Consumers refer to securities by `security_id` and issuers by `entity_id`; ticker remains a dated alias.

- [ ] **Step 1: Write identity and eligibility tests**

```python
def test_reference_keeps_cik_leading_zeroes_and_types_instruments():
    snapshot = parse_sec_company_tickers(SEC_FIXTURE, retrieved_at=NOW)
    mp = snapshot.by_ticker["MP"]
    assert mp.cik == "0001801368"
    assert mp.entity_id == "sec-cik:0001801368"


@pytest.mark.parametrize("instrument,eligible,reason", [
    ("COMMON_STOCK", True, None),
    ("ADR", True, None),
    ("ETF", True, None),
    ("PREFERRED", False, "instrument_type_excluded"),
    ("WARRANT", False, "instrument_type_excluded"),
    ("OTC_COMMON", False, "market_excluded"),
])
def test_suggestion_universe_is_explicit(instrument, eligible, reason):
    result = eligible_for_research(security(instrument))
    assert (result.eligible, result.reason) == (eligible, reason)
```

- [ ] **Step 2: Run universe tests and verify failure**

Run: `.venv/bin/python -m pytest tests/test_intelligence_universe.py -q`
Expected: FAIL because the reference module does not exist.

- [ ] **Step 3: Implement immutable identity types and parsers**

```python
@dataclass(frozen=True, slots=True)
class SecurityIdentity:
    security_id: str
    entity_id: str
    ticker: str
    exchange: str | None
    instrument_type: str
    valid_from: date
    valid_to: date | None
    aliases: tuple[str, ...]
    source_ids: tuple[str, ...]
    eligible: bool
    exclusion_reasons: tuple[str, ...]

def merge_reference_sources(
    sec_rows: Sequence[SecurityIdentity],
    listing_rows: Sequence[SecurityIdentity],
    *,
    as_of: date,
) -> ReferenceSnapshot:
    """Merge only matching identities; disagreements remain explicit conflicts."""
```

Reject test issues, missing symbols, unsupported exchanges, malformed CIKs, duplicate conflicting
classes, and ambiguous foreign/ADR mappings. Preserve former tickers and effective dates. Do not
infer a parent from a similar company name.

- [ ] **Step 4: Add bounded refresh and manifest persistence**

Fetch the documented SEC company-ticker file through `BoundedHttpClient` with a descriptive
User-Agent. Hash exact response bytes, retain the source timestamp and parser version, persist one
manifest plus changed revisions, and reuse the last healthy manifest when refresh fails while
reporting `reference_stale`.

Keep Nasdaq symbol-directory ingestion disabled until its HTTPS retrieval path and usage terms pass
the capability check. The SEC reference is sufficient for the first keyless entity-resolution
baseline but is labeled `scope_not_guaranteed`.

- [ ] **Step 5: Run reference tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_universe.py tests/test_collect_market_intelligence.py -q`
Expected: PASS for CIK zero-padding, listing types, conflicts, delistings, aliases, manifest reuse, and bounds.

- [ ] **Step 6: Commit the reference layer**

```bash
git add config/intelligence_sources.json lib/intelligence/universe.py scripts/collect_market_intelligence.py tests/test_intelligence_universe.py tests/fixtures/intelligence/sec_company_tickers.json tests/fixtures/intelligence/nasdaq_listed.txt tests/fixtures/intelligence/other_listed.txt
git commit -m "feat: add dated security reference"
```

---

### Task 4: Implement official feeds, safe cursors, and truthful source coverage

**Files:**
- Create: `lib/intelligence/cursors.py`
- Create: `lib/intelligence/providers/rss.py`
- Create: `lib/intelligence/providers/white_house.py`
- Create: `lib/intelligence/providers/energy.py`
- Create: `lib/intelligence/providers/sec.py`
- Create: `tests/test_intelligence_cursors.py`
- Create: `tests/test_intelligence_official_sources.py`
- Create: `tests/test_healthcheck.py`
- Create: `tests/fixtures/intelligence/doe_energy_news.xml`
- Create: `tests/fixtures/intelligence/eia_today_in_energy.xml`
- Create: `tests/fixtures/intelligence/white_house_fact_sheets.html`
- Create: `tests/fixtures/intelligence/white_house_sitemap.xml`
- Modify: `lib/intelligence/providers/official.py`
- Modify: `lib/intelligence/providers/__init__.py`
- Modify: `tests/test_intelligence_providers.py`
- Modify: `routines/README.md`
- Modify: `scripts/healthcheck.py`

**Interfaces:**
- Produces `SourceCursor`, `CollectionWindow`, `window_from_cursor()`, and source-specific adapters returning canonical `SourceItem` values without requiring tickers.
- Provider results include `cursor_start`, `cursor_end`, `overlap_seconds`, `truncated`, `backlog_remaining`, and `next_retry_phase` in receipt metadata.

- [ ] **Step 1: Write failing cursor and parser tests**

```python
def test_long_weekend_window_resumes_from_cursor_with_overlap():
    cursor = SourceCursor("doe", "energy", completed_through="2026-09-04T20:00:00Z")
    window = window_from_cursor(cursor, run_at=parse_time("2026-09-08T11:30:00Z"), overlap=timedelta(hours=2), max_backfill=timedelta(days=7))
    assert window.start == parse_time("2026-09-04T18:00:00Z")
    assert window.end == parse_time("2026-09-08T11:30:00Z")


def test_failed_source_is_not_reported_as_no_event():
    outcome = source_outcome(status="failed", returned=0)
    assert outcome.coverage_status == "source_failed"


def test_newest_first_truncation_saves_backlog_without_advancing_completed_window():
    next_cursor = update_cursor(cursor(), newest_first_page(truncated=True))
    assert next_cursor.completed_through == cursor().completed_through
    assert next_cursor.active_window_end == PROTECTED_RUN_AT
    assert next_cursor.backlog_token


def test_exhausted_empty_window_advances_completed_window():
    next_cursor = update_cursor(cursor(), empty_page(exhausted=True))
    assert next_cursor.completed_through == PROTECTED_RUN_AT
    assert next_cursor.backlog_token is None
```

- [ ] **Step 2: Run official-source tests and verify failure**

Run: `.venv/bin/python -m pytest tests/test_intelligence_cursors.py tests/test_intelligence_official_sources.py tests/test_intelligence_providers.py -q`
Expected: FAIL because cursor and source-specific adapters do not exist and current official adapters return `UNSUPPORTED_QUERY`.

- [ ] **Step 3: Implement safe bounded RSS/Atom parsing**

```python
def parse_bounded_feed(raw: bytes, *, source_url: str, max_bytes: int, max_items: int) -> tuple[FeedItem, ...]:
    if len(raw) > max_bytes or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise SourceFailure("INVALID_FEED")
    root = ElementTree.fromstring(raw)
    return tuple(parse_feed_entry(node, source_url) for node in feed_nodes(root)[:max_items])
```

Accept DOE's valid XML even when its MIME type says `text/html`. Reject DTDs, entities, script-like
HTML, wrong hosts, non-HTTPS links, missing item identity, invalid dates, oversized content, and
pagination beyond configured bounds.

- [ ] **Step 4: Implement verified source routes**

Add adapters for:

- DOE Energy News RSS `https://www.energy.gov/rss/energygov/2193718`;
- EIA Today in Energy and press RSS feeds;
- Defense.gov official RSS feeds;
- White House fact-sheet, presidential-action, and briefing/statement listing pages plus URLs from
  `https://www.whitehouse.gov/sitemap_index.xml`;
- Federal Register's existing documented API with explicit document status and effective date;
- SEC issuer and filing routes with `stocks-agent owner research contact=<configured non-secret contact>` User-Agent.

Do not add EIA statistics requests unless a free key is present and the exact v2 route/facets are
configured. Record `configuration_missing` instead.

- [ ] **Step 5: Implement cursors and bounded catch-up**

Keep `completed_through` as the end of the latest fully exhausted contiguous window. Store active
window bounds and the provider page/backlog token separately. A newest-first truncated response
retains the old completed watermark and resumes its saved older pages before opening a new window; a
fully exhausted empty response advances the watermark to the protected window end. Use at least two
hours of overlap, source-specific maximum backfill, stable page identities, and retained accepted-item
IDs. On failure, retain both the prior watermark and safe resume state and schedule the next normal
eligible phase.

- [ ] **Step 6: Align the actual Routine environment contract**

Update `routines/README.md` and `healthcheck.py` to test exact enabled hosts, path capabilities, and
credential presence without outputting values. Remove the false statement that current code cannot
call Alpha Vantage. Document Alpha Vantage as optional and keep the keyless official/GDELT baseline
independent.

- [ ] **Step 7: Run source and cursor tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_cursors.py tests/test_intelligence_official_sources.py tests/test_intelligence_providers.py tests/test_intelligence_http.py tests/test_healthcheck.py -q`
Expected: PASS for wrong MIME, DTD/entity rejection, redirects, host/path rules, cursor overlap, truncation, configuration-missing, and source-failure coverage.

- [ ] **Step 8: Commit official-source breadth**

```bash
git add lib/intelligence/cursors.py lib/intelligence/providers/rss.py lib/intelligence/providers/white_house.py lib/intelligence/providers/energy.py lib/intelligence/providers/sec.py lib/intelligence/providers/official.py lib/intelligence/providers/__init__.py routines/README.md scripts/healthcheck.py tests/test_intelligence_cursors.py tests/test_intelligence_official_sources.py tests/test_intelligence_providers.py tests/test_healthcheck.py tests/fixtures/intelligence
git commit -m "feat: collect official market themes with cursors"
```

---

### Task 5: Detect events before tickers and resolve entities safely

**Files:**
- Create: `config/theme_taxonomy.json`
- Create: `lib/intelligence/discovery.py`
- Create: `lib/intelligence/entities.py`
- Create: `tests/test_intelligence_discovery.py`
- Create: `tests/test_intelligence_entities.py`
- Modify: `lib/intelligence/themes.py`
- Modify: `lib/intelligence/relationships.py`
- Modify: `lib/intelligence/pipeline.py`
- Modify: `tests/test_intelligence_pipeline.py`

**Interfaces:**
- Produces `EventDraft`, `ThemeMatch`, `EntityResolution`, `ValueChainHypothesis`, `detect_events()`, `match_themes()`, `expand_value_chain()`, `build_reverse_discovery_tasks()`, and `resolve_entities()`.
- Consumes canonical source items, the security reference version, checked-in taxonomy, and reviewed aliases.
- Returns zero, one, or several resolutions without choosing an arbitrary first ticker.

- [ ] **Step 1: Write ticker-free and ambiguity tests**

```python
def test_private_magnet_announcement_survives_without_ticker():
    event = detect_events((niron_announcement_without_ticker(),), taxonomy())[0]
    assert event.theme_ids == ("critical_minerals_magnets",)
    assert event.security_ids == ()
    assert event.research_state == "observed"


def test_multiple_entities_do_not_collapse_to_first_sorted_security():
    resolutions = resolve_entities(multi_company_item(), reference_fixture())
    assert {row.security_id for row in resolutions if row.status == "resolved"} == {"sec:AAA", "sec:ZZZ"}


@pytest.mark.parametrize("token", ["AI", "ON", "IT"])
def test_ordinary_words_are_not_resolved_as_tickers(token):
    assert resolve_entities(text_item(token), reference_fixture()) == ()


def test_private_recipient_creates_bounded_search_that_finds_unseen_public_supplier():
    event = detect_events((private_magnet_award(),), taxonomy())[0]
    tasks = build_reverse_discovery_tasks(event, expand_value_chain(event, taxonomy()), max_tasks=4)
    assert any(task.query_kind == "theme_search" for task in tasks)
    assert all("PUBLIC_SUPPLIER" not in task.query_text for task in tasks)
    returned = run_fixture_search(tasks, result_mentioning="PUBLIC_SUPPLIER")
    assert resolve_entities(returned[0], reference_fixture())[0].security_id == "sec:PUBLIC_SUPPLIER"
```

- [ ] **Step 2: Run discovery tests and verify failure**

Run: `.venv/bin/python -m pytest tests/test_intelligence_discovery.py tests/test_intelligence_entities.py tests/test_intelligence_pipeline.py -q`
Expected: FAIL because current `_discover` drops ticker-free items and selects only one security ID.

- [ ] **Step 3: Implement ticker-independent event construction**

```python
@dataclass(frozen=True, slots=True)
class EventDraft:
    event_id: str
    event_type: str
    title: str
    summary: str
    occurred_at: datetime | None
    effective_at: datetime | None
    theme_ids: tuple[str, ...]
    entity_ids: tuple[str, ...]
    security_ids: tuple[str, ...]
    evidence: tuple[SourceItem, ...]
    research_state: str
    limitations: tuple[str, ...]
```

Construct identity from normalized claim, dates, publisher identity, and retained evidence rather
than ticker. Separate statement, forecast, proposed policy, effective policy, announced funding,
awarded funding, contract, capacity, demand, supply, earnings, and transaction event types.

- [ ] **Step 4: Implement deterministic entity resolution**

Resolve explicit CIK/security ID, then exact unique canonical/former names, then reviewed aliases.
Return `ambiguous`, `private`, `foreign_only`, or `unresolved` states explicitly. Preserve ticker
validity dates, share classes, and name collisions. Never map a private recipient to a public peer.

- [ ] **Step 5: Add the initial value-chain taxonomy**

Encode magnets, aluminum/copper, data-center power, nuclear/uranium, and robotics roles from the
approved spec. Each edge declares direction, role, geography, horizon, evidence requirement,
adverse-path flag, and invalidation rule. Taxonomy rows contain no permanent buy/sell action and no
portfolio mutation.

- [ ] **Step 6: Add bounded reverse discovery for previously unseen issuers**

Derive role/theme-search queries from the persisted event, taxonomy synonyms, geography, and horizon.
Persist the exact tasks before transport, allocate fairly across value-chain roles and adverse paths,
and use at least one keyless approved broad source such as GDELT in the baseline. Parse returned
organization names without requiring tickers, then pass them through ordinary entity/security
resolution. Never encode a permanent company list or treat a taxonomy role as exposure proof.

- [ ] **Step 7: Connect dynamic themes to the pipeline**

Call `propose_dynamic_theme()` only after publisher-independent corroboration. Use publisher and
upstream identities so syndicated copies do not count twice. Persist eligible proposals as theme
episode revisions; retain ineligible proposals as unresolved research with reasons.

- [ ] **Step 8: Run discovery and entity tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_discovery.py tests/test_intelligence_entities.py tests/test_intelligence_themes.py tests/test_intelligence_pipeline.py -q`
Expected: PASS for ticker-free events, multiple entities, private companies, reverse discovery of
previously unseen issuers, ordinary words, share classes, ticker changes, adverse paths, and
independent-source corroboration.

- [ ] **Step 9: Commit event-first discovery**

```bash
git add config/theme_taxonomy.json lib/intelligence/discovery.py lib/intelligence/entities.py lib/intelligence/themes.py lib/intelligence/relationships.py lib/intelligence/pipeline.py tests/test_intelligence_discovery.py tests/test_intelligence_entities.py tests/test_intelligence_themes.py tests/test_intelligence_pipeline.py
git commit -m "feat: resolve ticker-free market events"
```

---

### Task 6: Add bounded adaptive enrichment and primary exposure facts

**Files:**
- Create: `lib/intelligence/exposure.py`
- Create: `lib/intelligence/research_queue.py`
- Create: `tests/test_intelligence_exposure.py`
- Create: `tests/test_intelligence_research_queue.py`
- Create: `tests/fixtures/intelligence/sec_filing_exposure.html`
- Modify: `lib/intelligence/providers/sec.py`
- Modify: `lib/intelligence/pipeline.py`
- Modify: `lib/intelligence/ranking.py`
- Modify: `scripts/collect_market_intelligence.py`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/repository.ts`
- Modify: `tests/test_intelligence_pipeline.py`

**Interfaces:**
- Produces `ExposureFact`, `EnrichmentRequest`, `select_enrichment_queue()`, `extract_exposure_facts()`, and `evaluate_exposure()`.
- Uses only pre-reserved adaptive budget and persisted stage tasks.
- Quote enrichment calls the existing protected `collect_intelligence_quote` operation for newly resolved tickers.

- [ ] **Step 1: Write exposure and restart tests**

```python
def test_filing_metadata_alone_never_qualifies_exposure():
    assert extract_exposure_facts(submission_stub(), issuer=MP) == ()


def test_bounded_primary_passage_can_support_material_exposure():
    facts = extract_exposure_facts(filing_fixture(), issuer=MP)
    assert facts[0].role == "magnet_manufacturing"
    assert facts[0].source_locator == "item-2:magnetics-segment"
    assert facts[0].passage
    assert facts[0].status == "supported"


def test_restart_resumes_same_enrichment_queue_without_duplicate_transport():
    first = run_until_crash(stage="enrich", after_attempt=True)
    resumed = resume(first.run_id)
    assert resumed.selected_task_ids == first.selected_task_ids
    assert resumed.duplicate_transport_count == 0
```

- [ ] **Step 2: Run exposure tests and verify failure**

Run: `.venv/bin/python -m pytest tests/test_intelligence_exposure.py tests/test_intelligence_research_queue.py tests/test_intelligence_pipeline.py -q`
Expected: FAIL because filing-body extraction, adaptive queue persistence, and new-ticker quote enrichment do not exist.

- [ ] **Step 3: Implement typed exposure facts**

```python
@dataclass(frozen=True, slots=True)
class ExposureFact:
    fact_id: str
    entity_id: str
    security_id: str | None
    role: str
    metric: str
    value: str | None
    unit: str | None
    period_start: date | None
    period_end: date | None
    passage: str
    source_locator: str
    source_item_id: str
    reporting_at: datetime | None
    effective_at: datetime | None
    status: Literal["supported", "contradicted", "superseded", "insufficient"]
    limitations: tuple[str, ...]
```

Bound passages to 2,000 characters and locators to 256 characters. Reject a fact unless issuer,
role, source link/hash, passage, and reporting date are bound. Preserve units and periods; missing
revenue share remains unknown.

- [ ] **Step 4: Fetch shortlisted filing content safely**

Use SEC submissions to locate candidate 10-K, 10-Q, 8-K, 20-F, 40-F, and relevant exhibits. Fetch a
bounded document only after entity resolution and queue selection. Validate SEC host/accession/CIK,
content length, content type or safe HTML parsing, and descriptive User-Agent. Do not persist the full
filing.

- [ ] **Step 5: Select and persist the adaptive enrichment queue**

```python
def select_enrichment_queue(
    hypotheses: Sequence[ValueChainHypothesis],
    *,
    max_entities: int,
    max_requests: int,
    required_holding_quote_requests: int,
) -> tuple[EnrichmentRequest, ...]:
    """Prioritize official support, novelty, adverse paths, and theme fairness within reserved capacity."""
```

Persist the selected queue before any enrichment call. Allocate at least one slot per represented
theme when capacity allows, retain downside hypotheses, cap issuers per event, and record deferred
requests with reasons.

- [ ] **Step 6: Hydrate protected quote and liquidity inputs for new securities**

Call `collect_intelligence_quote` only for the persisted queue. Validate returned ticker, currency,
instrument type, timestamp, price, and average daily dollar volume through the gateway. Do not accept
caller-supplied values. Store unavailable overlap as unavailable rather than zero.

- [ ] **Step 7: Run enrichment, gateway, and recovery tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_exposure.py tests/test_intelligence_research_queue.py tests/test_intelligence_pipeline.py -q`
Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/handler_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts`
Expected: PASS for filing stubs, primary passages, ambiguous issuer documents, queue fairness, quota exhaustion, uncertain transport, retry, and gateway-owned quotes.

- [ ] **Step 8: Commit adaptive enrichment**

```bash
git add lib/intelligence/exposure.py lib/intelligence/research_queue.py lib/intelligence/providers/sec.py lib/intelligence/pipeline.py lib/intelligence/ranking.py scripts/collect_market_intelligence.py supabase/functions/market-briefing-gateway/_shared/handler.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts supabase/functions/market-briefing-gateway/_shared/repository.ts tests/test_intelligence_exposure.py tests/test_intelligence_research_queue.py tests/test_intelligence_pipeline.py tests/fixtures/intelligence/sec_filing_exposure.html
git commit -m "feat: enrich discovery with primary exposure evidence"
```

---

### Task 7: Implement bounded broad screens with explicit feasibility states

**Files:**
- Create: `lib/intelligence/screening.py`
- Create: `lib/intelligence/providers/yahoo_screen.py`
- Create: `tests/test_intelligence_screening.py`
- Create: `tests/fixtures/intelligence/yahoo_screen.json`
- Modify: `config/intelligence_sources.json`
- Modify: `config/settings.json`
- Modify: `lib/intelligence/providers/__init__.py`
- Modify: `lib/intelligence/pipeline.py`
- Modify: `tests/test_intelligence_providers.py`

**Interfaces:**
- Produces `ScreenDefinition`, `ScreenResult`, `run_bounded_screens()`, and normalized `screen_signal` source items.
- Screen outputs are discovery leads only and must resolve against the dated security reference before enrichment.

- [ ] **Step 1: Write screen transport and semantics tests**

```python
def test_screener_counts_every_redirect_cookie_crumb_and_retry_attempt():
    result = screen_adapter(http=FixtureHttp(SCREEN_FLOW)).collect(day_gainers_query())
    assert result.receipt.request_cost == len(SCREEN_FLOW.requests)


def test_market_filters_block_action_but_keep_research_reason():
    result = apply_market_filters(candidate(price="3.00", market_cap="500000000", average_volume="250000"), POLICY)
    assert result.research_visible is True
    assert result.action_eligible is False
    assert set(result.reasons) == {"below_min_price", "below_min_market_cap", "below_min_average_volume"}
```

- [ ] **Step 2: Run screening tests and verify failure**

Run: `.venv/bin/python -m pytest tests/test_intelligence_screening.py tests/test_intelligence_providers.py -q`
Expected: FAIL because configured screens are not executable.

- [ ] **Step 3: Implement explicit screen definitions and normalization**

Activate `top_gainers`, `top_losers`, `most_active`, `unusual_volume`, `near_52w_high_quality`, and
`oversold_quality` only when the feasibility contract proves explicit bounded HTTPS requests and
attempt accounting. Implement `insider_buying_clusters` through qualifying SEC Form 4 purchase
transactions, not Yahoo, 13D, 13G, grants, gifts, awards, option exercises, or amendments.

- [ ] **Step 4: Add replaceable Yahoo screen capability**

Use direct `BoundedHttpClient` requests with exact host/path allowlisting and fixture-defined parsing;
do not call a high-level library method that hides network attempts. Store the endpoint/parser
version and owner-only personal-use retention class. A challenge, format change, denied request, or
terms incompatibility changes the capability to `degraded` or `disabled` and leaves an explicit
coverage result.

- [ ] **Step 5: Enforce the action filters from configuration**

Parse `market_scan` into immutable policy. Apply minimum price `$5`, average daily volume `1,000,000`,
and market cap `$2,000,000,000` only to action eligibility. Unknown values fail the action gate.
Limit surfaced screen leads to 10 and preserve sector/screen fairness.

- [ ] **Step 6: Run screening tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_screening.py tests/test_intelligence_providers.py tests/test_intelligence_pipeline.py -q`
Expected: PASS for every screen state, request accounting, Form 4 transaction semantics, filters, outages, malformed responses, and reference resolution.

- [ ] **Step 7: Commit the broad-screen path**

```bash
git add config/intelligence_sources.json config/settings.json lib/intelligence/screening.py lib/intelligence/providers/yahoo_screen.py lib/intelligence/providers/__init__.py lib/intelligence/pipeline.py tests/test_intelligence_screening.py tests/test_intelligence_providers.py tests/test_intelligence_pipeline.py tests/fixtures/intelligence/yahoo_screen.json
git commit -m "feat: add bounded cross-market screens"
```

---

### Task 8: Separate research priority, suitability, and action packets

**Files:**
- Modify: `lib/intelligence/ranking.py`
- Modify: `lib/intelligence/packet.py`
- Modify: `lib/intelligence/types.py`
- Modify: `lib/intelligence/reports.py`
- Modify: `lib/intelligence/pipeline.py`
- Modify: `tests/test_intelligence_ranking.py`
- Modify: `tests/test_intelligence_packet.py`
- Modify: `tests/test_intelligence_reports.py`
- Modify: `tests/test_intelligence_pipeline.py`
- Modify: `supabase/functions/market-briefing-gateway/_shared/contracts.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/intelligence.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts`
- Create: `sql/migrations/20261011_research_suitability_packet_contract.sql`
- Modify: `sql/schema.sql`

**Interfaces:**
- Produces `ResearchCandidate`, `SuitabilityEvaluation`, and a two-lane `EvidencePacket` containing `research_candidates` and `action_candidates`.
- Existing Analyst/Checker decisions may downgrade or veto but cannot promote a research-only candidate into the action lane.

- [ ] **Step 1: Write research/action separation tests**

```python
def test_etf_only_portfolio_preserves_research_and_blocks_action_when_overlap_unknown():
    ranked = rank_candidates([magnet_candidate()], holdings={"VTI": Decimal("1")}, plans=[])
    packet = build_evidence_packet(ranked)
    assert [row.candidate_key for row in packet.research_candidates] == ["MP"]
    assert packet.action_candidates == ()
    assert "portfolio_overlap_missing" in packet.research_candidates[0].limitations


def test_checker_cannot_upgrade_research_only_candidate():
    with pytest.raises(ValueError, match="research-only candidate cannot become actionable"):
        evaluate_checker(research_only_packet(), checker_action="buy")
```

- [ ] **Step 2: Run ranking and packet tests and verify failure**

Run: `.venv/bin/python -m pytest tests/test_intelligence_ranking.py tests/test_intelligence_packet.py tests/test_intelligence_reports.py tests/test_intelligence_pipeline.py -q`
Expected: FAIL because current qualification requires every component and the packet drops research-only candidates.

- [ ] **Step 3: Implement separate result types**

```python
@dataclass(frozen=True, slots=True)
class ResearchCandidate:
    candidate_key: str
    security_id: str
    theme_ids: tuple[str, ...]
    roles: tuple[str, ...]
    research_state: str
    priority_score: Decimal
    evidence: tuple[SourceItem, ...]
    exposure_fact_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    adverse_paths: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class SuitabilityEvaluation:
    candidate_key: str
    state: Literal["unknown", "eligible", "vetoed"]
    component_scores: Mapping[str, Decimal]
    missing_reasons: tuple[str, ...]
    veto_reasons: tuple[str, ...]
```

Research priority requires an event, resolved security or explicit unresolved state, and retained
evidence. `analysis_ready` requires primary exposure evidence. Suitability separately requires
current protected market and portfolio inputs. Action eligibility remains a strict subset.

- [ ] **Step 4: Expand the packet without exceeding existing bounds**

Packet evidence adds reference URL, authority, published/retrieved/reporting/effective timestamps,
claim type, exposure role, and relationship eligibility. Keep 12 total candidates and 8 evidence
items per candidate across both lanes. Prefer official and adverse evidence when thinning. Persist
every removed candidate/evidence reason.

- [ ] **Step 5: Update TypeScript and SQL exact-key contracts**

Update parsers, canonical hashes, evidence-membership checks, packet reads, and protected decision
validation. Reject action decisions for candidates absent from `action_candidates`. Ensure the SQL
canonical hash matches Python and TypeScript for both packet lanes.

- [ ] **Step 6: Render research, wait, insufficient, and action outcomes distinctly**

Reports label research priority as research, not a buy signal. Show missing suitability evidence,
opposing paths, source coverage, and next review. Telegram includes only policy-approved actionable
or material risk output and remains quiet for ordinary research discoveries.

- [ ] **Step 7: Run all ranking/packet/contract tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_ranking.py tests/test_intelligence_packet.py tests/test_intelligence_reports.py tests/test_intelligence_pipeline.py -q`
Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts supabase/functions/market-briefing-gateway/_shared/contracts_test.ts`
Expected: PASS for empty portfolios, ETF-only portfolios, missing valuation/overlap, action-lane integrity, evidence provenance, hashing, and byte limits.

- [ ] **Step 8: Commit the two-lane decision contract**

```bash
git add lib/intelligence/ranking.py lib/intelligence/packet.py lib/intelligence/types.py lib/intelligence/reports.py lib/intelligence/pipeline.py tests/test_intelligence_ranking.py tests/test_intelligence_packet.py tests/test_intelligence_reports.py tests/test_intelligence_pipeline.py supabase/functions/market-briefing-gateway/_shared/contracts.ts supabase/functions/market-briefing-gateway/_shared/intelligence.ts supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts sql/migrations/20261011_research_suitability_packet_contract.sql sql/schema.sql
git commit -m "feat: separate research from action eligibility"
```

---

### Task 9: Persist theme memory and bounded next-run research nominations

**Files:**
- Modify: `lib/intelligence/research_queue.py`
- Create: `tests/test_intelligence_theme_memory.py`
- Modify: `lib/intelligence/themes.py`
- Modify: `lib/intelligence/pipeline.py`
- Modify: `lib/intelligence/packet.py`
- Modify: `scripts/collect_market_intelligence.py`
- Modify: `skills/market-briefing/SKILL.md`
- Modify: `supabase/functions/market-briefing-gateway/_shared/contracts.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/repository.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/repository_test.ts`
- Modify: `supabase/functions/owner-dashboard-api/repository.ts`
- Modify: `supabase/functions/owner-dashboard-api/repository_test.ts`
- Modify: `supabase/functions/owner-dashboard-api/mappers.ts`
- Modify: `supabase/functions/owner-dashboard-api/mappers_test.ts`
- Modify: `supabase/functions/owner-dashboard-api/handler_test.ts`
- Modify: `packages/dashboard-contracts/src/index.ts`
- Modify: `packages/dashboard-contracts/src/index.test.ts`
- Modify: `apps/web/src/features/intelligence/IntelligencePage.tsx`
- Modify: `apps/web/src/features/intelligence/IntelligencePage.test.tsx`

**Interfaces:**
- Produces `ThemeEpisodeRevision`, `ResearchNomination`, `revise_theme_episode()`,
  `validate_research_nominations()`, and protected `record_research_nominations`.
- Protected context returns active episodes, due nominations, radar, urgent events, high-materiality themes, source cursors, and the reference version.
- The owner dashboard API returns a bounded typed Intelligence read model; the browser never queries
  discovery tables directly.

- [ ] **Step 1: Write theme-memory and nomination tests**

```python
def test_paraphrased_story_revises_existing_theme_episode():
    revised = revise_theme_episode(existing_magnet_episode(), paraphrased_supported_event())
    assert revised.episode_id == existing_magnet_episode().episode_id
    assert revised.revision == existing_magnet_episode().revision + 1


def test_nomination_is_next_run_research_only():
    nomination = validate_research_nominations(analyst_nomination(), current_packet())
    assert nomination.state == "pending"
    assert nomination.authorizes_action is False
    assert nomination.may_mutate_watchlist is False
```

- [ ] **Step 2: Run theme-memory tests and verify failure**

Run: `.venv/bin/python -m pytest tests/test_intelligence_theme_memory.py tests/test_intelligence_themes.py tests/test_collect_market_intelligence.py -q`
Expected: FAIL because durable theme episodes and nominations are not wired to protected context or the packet.

- [ ] **Step 3: Implement episode revisions and nominations**

```python
@dataclass(frozen=True, slots=True)
class ResearchNomination:
    nomination_id: str
    theme_id: str
    entity_id: str | None
    security_id: str | None
    role: str
    reason: str
    evidence_ids: tuple[str, ...]
    required_evidence_kind: str
    priority: int
    expires_at: datetime
    state: Literal["pending", "selected", "resolved", "rejected", "expired"]
```

Limit model nominations to three per run, require packet evidence membership, reject arbitrary URLs,
prices, scores, actions, policy changes, and owner-data mutations, and defer collection to the next
normal run or an explicit owner on-demand request.

Add `record_research_nominations` to the gateway's explicit operation allowlist. It accepts only the
current run's Analyst/Checker identity, packet-member evidence IDs, and exact nomination keys; the
repository resolves all authority fields. Handler tests reject anonymous, owner-browser, stale-run,
non-packet evidence, extra-field, action-bearing, watchlist-mutating, and oversized writes.

- [ ] **Step 4: Extend protected context and remove the radar discard**

Return bounded active theme revisions, due nominations, source cursors, reference manifest, radar,
urgent events, and high-materiality themes from `read_intelligence_context`. Update
`protected_collection_context()` to accept only the exact typed server-owned fields. Keep scratch
files non-authoritative.

- [ ] **Step 5: Show theme history and coverage in the owner dashboard**

Under Advanced → Intelligence, show active/revised themes, new outside-watchlist companies,
relationship paths, primary exposure passages, adverse evidence, missing inputs, source health,
reference version, truncation/backlog, and next review. Preserve the existing three primary tabs and
progressive disclosure. Extend the owner-dashboard repository query, strict mappers, and response
contract with only bounded owner-readable fields. Prove the API still rejects anonymous and
non-owner callers and does not expose raw filings, task queries, service metadata, or hidden owner
records.

- [ ] **Step 6: Run Python, contract, and web tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_theme_memory.py tests/test_intelligence_themes.py tests/test_collect_market_intelligence.py -q`
Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/handler_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/owner-dashboard-api/handler_test.ts supabase/functions/owner-dashboard-api/repository_test.ts supabase/functions/owner-dashboard-api/mappers_test.ts`
Run: `npm test --workspace @stocks-agent/dashboard-contracts`
Run: `npm test --workspace @stocks-agent/web`
Expected: PASS for episode identity, contradiction revisions, nomination authorization and bounds,
protected context, dashboard query/mapping, accessibility, evidence disclosure, and owner-only behavior.

- [ ] **Step 7: Commit theme memory and owner visibility**

```bash
git add lib/intelligence/research_queue.py lib/intelligence/themes.py lib/intelligence/pipeline.py lib/intelligence/packet.py scripts/collect_market_intelligence.py skills/market-briefing/SKILL.md supabase/functions/market-briefing-gateway/_shared/contracts.ts supabase/functions/market-briefing-gateway/_shared/handler.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts supabase/functions/market-briefing-gateway/_shared/repository.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/owner-dashboard-api/repository.ts supabase/functions/owner-dashboard-api/repository_test.ts supabase/functions/owner-dashboard-api/mappers.ts supabase/functions/owner-dashboard-api/mappers_test.ts supabase/functions/owner-dashboard-api/handler_test.ts packages/dashboard-contracts/src/index.ts packages/dashboard-contracts/src/index.test.ts apps/web/src/features/intelligence/IntelligencePage.tsx apps/web/src/features/intelligence/IntelligencePage.test.tsx tests/test_intelligence_theme_memory.py tests/test_intelligence_themes.py tests/test_collect_market_intelligence.py
git commit -m "feat: retain theme research memory"
```

---

### Task 10: Prove thematic generalization, safety, recovery, and capacity

**Files:**
- Create: `config/nyse_calendar.json`
- Create: `scripts/sync_market_calendar.py`
- Create: `tests/test_sync_market_calendar.py`
- Create: `supabase/functions/market-briefing-gateway/_shared/nyse-calendar.generated.ts`
- Create: `supabase/functions/owner-dashboard-api/nyse-calendar.generated.ts`
- Create: `tests/test_market_wide_discovery_acceptance.py`
- Create: `tests/fixtures/intelligence/magnets_private_recipient.json`
- Create: `tests/fixtures/intelligence/aluminum_data_centers.json`
- Create: `tests/fixtures/intelligence/data_center_power.json`
- Create: `tests/fixtures/intelligence/uranium_fuel_cycle.json`
- Create: `tests/fixtures/intelligence/robotics_forecast.json`
- Create: `tests/fixtures/intelligence/held_out_healthcare_event.json`
- Create: `tests/fixtures/intelligence/entity_collisions.json`
- Modify: `tests/test_intelligence_pipeline.py`
- Modify: `tests/test_intelligence_quota.py`
- Modify: `tests/test_intelligence_http.py`
- Modify: `tests/test_recovery_bundle.py`
- Modify: `tests/test_release_upgrade_integration.py`
- Modify: `tests/test_managed_isolated_restore.py`
- Modify: `scripts/test_all.sh`
- Modify: `scripts/verify_personal_stock_agent_v1.py`
- Modify: `tests/test_verify_personal_stock_agent_v1.py`
- Modify: `lib/marketdata.py`
- Modify: `lib/policy_config.py`
- Modify: `tests/test_marketdata.py`
- Modify: `tests/test_policy_config.py`
- Modify: `supabase/functions/market-briefing-gateway/_shared/market-calendar.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/market-calendar_test.ts`
- Modify: `supabase/functions/owner-dashboard-api/freshness.ts`
- Modify: `supabase/functions/owner-dashboard-api/freshness_test.ts`
- Modify: `supabase/functions/owner-dashboard-api/repository.ts`
- Modify: `supabase/functions/owner-dashboard-api/repository_test.ts`

**Interfaces:**
- Produces a release-blocking capability verifier separate from the existing operational receipt-integrity verifier.
- Capability verification accepts an honestly empty production outcome only when every due
  required-baseline task has a parsed, receipt-backed `success_empty` state; terminal failures never
  satisfy closure.

- [ ] **Step 1: Write the five thematic and held-out acceptance scenarios**

```python
@pytest.mark.parametrize("fixture_name", [
    "magnets_private_recipient",
    "aluminum_data_centers",
    "data_center_power",
    "uranium_fuel_cycle",
    "robotics_forecast",
    "held_out_healthcare_event",
])
def test_outside_watchlist_discovery_scenarios(fixture_name):
    result = run_acceptance_fixture(fixture_name, holdings=[], plans=[], radar=[], watchlist=[])
    assert result.coverage["complete_market_coverage"] is False
    assert result.events
    assert result.research_candidates
    assert set(result.fixture_expected_candidate_keys) <= {
        candidate.candidate_key for candidate in result.research_candidates
    }
    assert all(candidate.candidate_key not in result.input_tickers for candidate in result.research_candidates)
    assert result.unauthorized_actions == []
```

- [ ] **Step 2: Add adversarial and capacity cases**

Cover hostile article instructions, spoofed issuer links, private/public collisions, multiple tickers,
ordinary-word tickers, share classes, ticker changes, stale filings, syndicated duplicates,
contradictory claims, Form 4 non-purchases, long weekends, truncation, total source failure, ETF-only
portfolio, empty portfolio, quota exhaustion, hidden transport attempts, crash points, and output
limits. Add a capability-closure case where every required task is terminal but failed, disabled,
unsupported, deferred, uncertain, or quota-blocked; none may pass. Parsed `success_empty` is the only
empty required-capability state that may satisfy the due baseline.

- [ ] **Step 3: Run acceptance tests and verify any missing integration fails**

Run: `.venv/bin/python -m pytest tests/test_market_wide_discovery_acceptance.py -q`
Expected before final integration: FAIL on any incomplete cross-module path; no fixture may pass
vacuously with an empty research set or through a hardcoded ticker list.

- [ ] **Step 4: Add generalization controls**

Programmatically rename fixture companies, aliases, CIKs, and tickers while preserving evidence
structure. Require the same state transitions. Add one held-out sector that is absent from the five
priority taxonomy examples but present in the broad seed-domain policy.

- [ ] **Step 5: Extend and synchronize the maintained NYSE calendar**

Create `config/nyse_calendar.json` as the reviewed source for official NYSE holidays and early closes
through 2028, using the [published NYSE schedule](https://www.nyse.com/trade/hours-calendars).
Generate the two Edge-runtime TypeScript copies with
`sync_market_calendar.py`; Python loads the same checked-in source. Remove the gateway's
`MAINTAINED_YEAR = "2026"` and dashboard's independent 2026 list. Fail closed after 2028. Test 2027
Good Friday and observed Independence Day, the 2027 day-after-Thanksgiving 1 p.m. close, the special
2028 New Year's handling, 2028 early closes, generator drift, and Python/gateway/dashboard parity.

- [ ] **Step 6: Extend the release verifier**

```python
def verify_discovery_capability(receipt: Mapping[str, object]) -> VerificationResult:
    require_reference_manifest(receipt)
    require_terminal_theme_tasks(receipt)
    require_successful_required_capabilities(
        receipt,
        accepted_states={"success_empty", "success_nonempty"},
    )
    distinguish_no_event_from_failure(receipt)
    require_saved_stage_lineage(receipt)
    require_research_action_lane_integrity(receipt)
    return VerificationResult(ok=True)
```

Operational receipt integrity may pass with zero events. V1-C3 capability passes only when the new
source plan, reference version, stage lineage, and coverage semantics exist and every due
`required_baseline` capability has a parsed, receipt-backed success. Optional failures remain visible.
It never requires a forced candidate or positive suggestion.

- [ ] **Step 7: Run focused acceptance, calendar, recovery, and full local gates**

Run: `.venv/bin/python -m pytest tests/test_market_wide_discovery_acceptance.py tests/test_intelligence_pipeline.py tests/test_intelligence_quota.py tests/test_intelligence_http.py tests/test_recovery_bundle.py tests/test_release_upgrade_integration.py tests/test_managed_isolated_restore.py tests/test_verify_personal_stock_agent_v1.py -q`
Run: `.venv/bin/python -m pytest tests/test_sync_market_calendar.py tests/test_marketdata.py tests/test_policy_config.py -q`
Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/market-calendar_test.ts supabase/functions/owner-dashboard-api/freshness_test.ts supabase/functions/owner-dashboard-api/repository_test.ts`
Run: `npm run test:all`
Expected: all tests pass, generated calendars match the canonical source through 2028, dates beyond
coverage fail closed, and credentialed production mutation tests remain intentionally opt-in and
deselected by the ordinary gate.

- [ ] **Step 8: Commit release-blocking capability and calendar coverage**

```bash
git add config/nyse_calendar.json scripts/sync_market_calendar.py tests/test_sync_market_calendar.py supabase/functions/market-briefing-gateway/_shared/nyse-calendar.generated.ts supabase/functions/owner-dashboard-api/nyse-calendar.generated.ts tests/test_market_wide_discovery_acceptance.py tests/fixtures/intelligence tests/test_intelligence_pipeline.py tests/test_intelligence_quota.py tests/test_intelligence_http.py tests/test_recovery_bundle.py tests/test_release_upgrade_integration.py tests/test_managed_isolated_restore.py scripts/test_all.sh scripts/verify_personal_stock_agent_v1.py tests/test_verify_personal_stock_agent_v1.py lib/marketdata.py lib/policy_config.py tests/test_marketdata.py tests/test_policy_config.py supabase/functions/market-briefing-gateway/_shared/market-calendar.ts supabase/functions/market-briefing-gateway/_shared/market-calendar_test.ts supabase/functions/owner-dashboard-api/freshness.ts supabase/functions/owner-dashboard-api/freshness_test.ts supabase/functions/owner-dashboard-api/repository.ts supabase/functions/owner-dashboard-api/repository_test.ts
git commit -m "test: prove market discovery and calendar coverage"
```

---

### Task 11: Document, review, deploy, and close V1 only with normal receipts

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/V1_IMPLEMENTATION_CHECKLIST.md`
- Modify: `docs/rollouts/2026-09-04-personal-stock-agent-v1.md`
- Create: `docs/rollouts/2026-09-06-market-wide-thematic-discovery-v1.md`
- Modify: `.github/workflows/owner-dashboard-release.yml`
- Modify: `.github/workflows/owner-dashboard-release-recovery.yml`
- Modify: `.github/workflows/managed-isolated-restore.yml`
- Modify: `.github/workflows/existing-v1-runtime-attestation.yml`
- Verify: `.openai/hosting.json`
- Verify: `scripts/build_owner_dashboard_static.py`
- Verify: `scripts/verify_owner_dashboard_deployment.py`
- Verify: `tests/test_build_owner_dashboard_static.py`
- Verify: `tests/test_verify_owner_dashboard_deployment.py`

**Interfaces:**
- Produces one exact candidate review/CI/release record and later normal scheduled capability receipts.
- Does not reuse the Site-only publication path for database or Edge Function changes.

- [ ] **Step 1: Record the exact implementation boundary**

Update canonical documents with completed task commits, schema migration hash, changed components,
test counts, source capability status, known disabled sources, rollback identity, and the distinction
between September 8 operational evidence and new V1-C3 capability evidence.

- [ ] **Step 2: Run independent reviews**

Request one whole-change GPT-6 Astra review and focused security/recovery review on the exact commit.
Resolve every Critical, Important, P1, and P2 finding. Re-run affected tests after each fix and record
the reviewed final SHA.

- [ ] **Step 3: Run the final exact-candidate gate**

Run: `npm run test:all`
Run: `git diff --check origin/main...HEAD`
Run: `.venv/bin/python -m pytest tests/test_verify_personal_stock_agent_v1.py -q`
Expected: all ordinary gates pass, no whitespace errors, and the verifier tests prove both operational and capability contracts without claiming production.

- [ ] **Step 4: Commit the rollout candidate**

```bash
git add PROJECT_STATUS.md docs/ROADMAP.md docs/V1_IMPLEMENTATION_CHECKLIST.md docs/rollouts/2026-09-04-personal-stock-agent-v1.md docs/rollouts/2026-09-06-market-wide-thematic-discovery-v1.md .github/workflows/owner-dashboard-release.yml .github/workflows/owner-dashboard-release-recovery.yml .github/workflows/managed-isolated-restore.yml .github/workflows/existing-v1-runtime-attestation.yml
git commit -m "docs: prepare market discovery rollout"
```

- [ ] **Step 5: Merge through protected main and exact-main CI**

Push the feature branch, open the normal pull request, verify exact-head CI, merge only the reviewed
SHA, and verify exact-main CI. Do not deploy an unreviewed descendant.

- [ ] **Step 6: Release the protected backend, then publish the exact candidate through native Sites**

Apply the immutable additive discovery chain from `20261005_market_wide_discovery.sql` through
`20261013_v2_runtime_completion.sql`, publish changed Edge Functions, update the scheduled
environment's approved domains and credential-presence configuration, and retain encrypted rollback
state. Read back exact migration bytes, table/ACL state, source manifests, Edge bytes/configuration,
environment capability status, owner/anonymous API behavior, and rollback identities. If any
component cannot be captured or restored, fail before mutation or execute the recorded recovery.

After the backend release and owner API canaries pass, build the exact reviewed web commit through
the existing static supply-chain verifier and publish it with the owner-scoped Sites path. Retain the
current Site v9 deployment as rollback until the new Site passes readback for exact application asset
hashes, sole-owner allowlist, no groups, zero external visitors, API origin, and login/recovery flow.
Validate the bounded native Sites receipt with `scripts/verify_native_site_release.py` and keep it
beside the protected backend release receipt; neither receipt substitutes for the other.

- [ ] **Step 7: Observe normal scheduled shadow discovery**

Wait for existing pre-market, intraday, and post-market routines. Reconcile source plans, task
lineage, reference version, cursors, exposure facts, packet hash, Analyst/Checker IDs, policy result,
report hash, and original Telegram delivery or explicit suppression. Never dispatch a duplicate run
to manufacture an outside-watchlist event.

- [ ] **Step 8: Close V1-C3 and the final V1 goal only when every gate passes**

If normal receipts prove the capability contract or an honestly empty outcome where every due
`required_baseline` capability has a parsed, receipt-backed `success_empty` result, mark V1-C3
complete. Failed, disabled, unsupported, uncertain, deferred, configuration-missing, or quota-blocked
required capabilities keep it open. Close V1-C2 through V1-C6 only where their production receipts
also pass. Mark the active goal complete only after every checkpoint is complete. Preserve Alert V3
shadow review as the next separately approved rollout.

- [ ] **Step 9: Commit the receipt-backed closure**

```bash
git add PROJECT_STATUS.md docs/ROADMAP.md docs/V1_IMPLEMENTATION_CHECKLIST.md docs/rollouts/2026-09-04-personal-stock-agent-v1.md docs/rollouts/2026-09-06-market-wide-thematic-discovery-v1.md
git commit -m "docs: close receipt-backed Personal Stock Agent V1"
```

---

## Plan self-review checklist

- [x] Every requirement in the approved design maps to one or more tasks above.
- [x] Source breadth, entity resolution, exposure evidence, screening, research/action separation,
  theme memory, protected persistence, dashboard visibility, recovery, and release receipts each have
  an explicit test and owner-visible result.
- [x] The actual-config planner test prevents a repeat of the current configuration-only coverage
  claim.
- [x] The capability verifier cannot treat empty arrays as proof of working discovery.
- [x] All names and signatures consumed by later tasks are introduced in earlier tasks.
- [x] No task authorizes brokerage execution, friend access, paid fallback, model API calls, automatic
  policy changes, duplicate live runs, or Alert V3 activation.
