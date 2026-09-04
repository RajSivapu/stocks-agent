# Zero-Cost Personal Stock Agent V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the complete owner-only Personal Stock Agent V1 across checkpoints V1-C1 through V1-C6 with zero incremental provider/model cost, market-wide evidence discovery, personal comparison, append-only reports, a private dashboard, quiet Telegram delivery, and receipt-backed rollout.

**Architecture:** Python collectors use approved free sources through bounded adapters, deterministic normalization, quota, cache, deduplication, event mapping, exposure gates, and ranking. The existing scoped Supabase Edge gateway atomically reserves quota, persists append-only receipts, validates bounded Analyst/Checker submissions, applies deterministic policy, and stores immutable reports. The existing owner-only GET API and React application expose five receipt-derived surfaces; Telegram receives only policy-approved summaries and never gains brokerage authority.

**Tech Stack:** Python 3.13 stdlib plus existing Supabase client, PostgreSQL/Supabase migrations and RPCs, Deno 2.9.6 TypeScript Edge Functions, React 19, TypeScript 6, Vite 8, Vitest, Playwright, pytest, Node test runner.

**Spec:** `docs/superpowers/specs/2026-09-04-zero-cost-personal-stock-agent-v1-design.md`

## Global Constraints

- Zero incremental dollars: no paid provider, premium endpoint, paid trial, credit-card signup, metered runtime model API, or automatic upgrade.
- Approved sources only: GDELT, existing free Alpha Vantage, existing free Finnhub, Yahoo, SEC EDGAR, Federal Register, White House, DOE, DoD, EIA, Fed/FRED, BLS, and BEA. Reddit/social is hypothesis discovery only.
- Alpha Vantage has a hard internal ceiling of 20 requests/day: pre-market 8, intraday 4, post-market 4, on-demand 2, reserve 2.
- At most 12 ranked candidates enter a model run, with at most eight evidence items per candidate, 2,000 normalized characters per item, and a 96 KiB serialized packet.
- One owner account, public signup disabled, friend invitations disabled, least-privilege access, and no browser financial mutation routes.
- No brokerage integration, credentials, endpoint, order placement, or trade authority. Every output remains suggestion-only.
- External content is untrusted data. It cannot alter prompts, policy, source selection, delivery, or authority.
- Missing, stale, conflicting, quota-blocked, or insufficient evidence fails closed.
- No receipt means no persistence, publication, Telegram, viewing, or deployment claim.
- No duplicate live scheduled run for inspection; protected dry-run previews remain write-free and send-free.
- Do not apply either new production migration before V1-C6; all earlier migration work is local and verifier-backed.
- Learning records proposals only and cannot silently change weights, thresholds, sources, delivery, sizing, or authority.
- Follow TDD for every production change. Keep commits task-scoped and update `PROJECT_STATUS.md` at each completed checkpoint.

---

## File Structure

New Python intelligence code is isolated under `lib/intelligence/`:

- `types.py` — immutable normalized source, event, relationship, candidate, packet, and receipt types.
- `policy.py` — strict settings parser and phase/provider budgets.
- `http.py` — bounded HTTPS transport and cache protocol.
- `quota.py` — immutable reservation validation and local consumption accounting.
- `normalize.py` — untrusted-text bounds, canonical URLs, content hashes, timestamps, and source authority.
- `dedupe.py` — exact and near-duplicate grouping with explicit reason codes.
- `providers/` — one adapter per provider family; no ranking or model logic.
- `themes.py` — seed taxonomy and dynamic-theme proposal rules.
- `relationships.py` — event/theme/value-chain/entity/security links and exposure-evidence gate.
- `ranking.py` — deterministic component scores and stable ordering.
- `packet.py` — 12-candidate/eight-item/96-KiB model packet builder.
- `pipeline.py` — phase orchestration over injected adapters and gateway client.
- `reports.py` — immutable report input and content-hash construction.
- `learning.py` — deterministic outcome and missed-event observations.

New database and gateway boundaries:

- `sql/migrations/20260907_market_intelligence.sql` — append-only intelligence, quota reservation, event, relationship, packet, and report ledgers plus scoped RPCs.
- `sql/migrations/20260908_owner_dashboard_intelligence_read_role.sql` — exact dashboard column grants and SELECT-only policies for new tables.
- `supabase/functions/market-briefing-gateway/_shared/intelligence.ts` — payload parsing, bounds, hashes, and exposure checks.
- `supabase/functions/market-briefing-gateway/_shared/reports.ts` — report contract, delivery summary, and idempotency rules.
- Existing `contracts.ts`, `handler.ts`, `repository.ts`, `policy.ts`, and `renderer.ts` integrate those modules without duplicating them.

New dashboard feature folders:

- `apps/web/src/features/intelligence/` — event, theme, value-chain, source-coverage views.
- `apps/web/src/features/reports/` — immutable report index/detail and publication timeline.
- Existing Today/Companion content moves under Portfolio; Alerts/Runs content is reused by Intelligence, Reports, and System / Receipts.

---

### Task 1: V1-C1 Release Policy and Configuration Contract

**Files:**
- Create: `lib/intelligence/__init__.py`
- Create: `lib/intelligence/types.py`
- Create: `lib/intelligence/policy.py`
- Create: `tests/test_intelligence_policy.py`
- Modify: `config/settings.json`
- Modify: `tests/test_security_invariants.py`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Consumes: `config/settings.json` and phase names `pre-market | intraday | post-market | on-demand`.
- Produces: `load_intelligence_policy(settings: Mapping[str, object]) -> IntelligencePolicy`, `IntelligencePolicy.budget_for(provider: str, phase: str) -> int`, and frozen dataclasses used by every later task.

- [ ] **Step 1: Write the failing policy tests**

```python
from lib.intelligence.policy import load_intelligence_policy

def test_v1_provider_and_alpha_budget_contract(settings):
    policy = load_intelligence_policy(settings)
    assert policy.providers == (
        "gdelt", "alpha_vantage", "finnhub", "yahoo", "sec_edgar",
        "federal_register", "white_house", "doe", "dod", "eia",
        "fred", "bls", "bea",
    )
    assert sum(policy.alpha_vantage_phase_budget.values()) == 18
    assert policy.alpha_vantage_daily_ceiling == 20
    assert policy.packet.max_candidates == 12
    assert policy.packet.max_evidence_per_candidate == 8
    assert policy.packet.max_item_characters == 2_000
    assert policy.packet.max_serialized_bytes == 98_304

def test_paid_and_execution_authority_are_rejected(settings):
    settings["intelligence"]["providers"].append("benzinga")
    with pytest.raises(ValueError, match="provider allowlist"):
        load_intelligence_policy(settings)
```

- [ ] **Step 2: Run the focused tests and confirm the missing module failure**

Run: `.venv/bin/python -m pytest tests/test_intelligence_policy.py -q`

Expected: FAIL because `lib.intelligence.policy` does not exist.

- [ ] **Step 3: Add frozen types and strict configuration**

```python
@dataclass(frozen=True, slots=True)
class PacketLimits:
    max_candidates: int = 12
    max_evidence_per_candidate: int = 8
    max_item_characters: int = 2_000
    max_serialized_bytes: int = 96 * 1024

@dataclass(frozen=True, slots=True)
class IntelligencePolicy:
    providers: tuple[str, ...]
    alpha_vantage_daily_ceiling: int
    alpha_vantage_phase_budget: Mapping[str, int]
    provider_phase_budgets: Mapping[str, Mapping[str, int]]
    packet: PacketLimits
    suggestion_only: bool
    execution_allowed: bool

    def budget_for(self, provider: str, phase: str) -> int:
        if provider == "alpha_vantage":
            return int(self.alpha_vantage_phase_budget.get(phase, 0))
        return int(self.provider_phase_budgets.get(provider, {}).get(phase, 0))
```

Add an `intelligence` settings object containing the exact provider allowlist, seed domains, phase budgets, packet limits, `paid_fallback_enabled: false`, and `runtime_model_api_enabled: false`. Reject unknown providers, nonzero execution authority, ceilings above 20, phase allocations above 18, or packet limits above the approved values.

- [ ] **Step 4: Run policy and security invariants**

Run: `.venv/bin/python -m pytest tests/test_intelligence_policy.py tests/test_security_invariants.py -q`

Expected: PASS with no paid provider, brokerage, friend invitation, or automatic policy-change path enabled.

- [ ] **Step 5: Record approval and commit V1-C1**

Update `PROJECT_STATUS.md` so the written-spec approval and plan creation are checked, move V1-C1 to Done, and set V1-C2 as In Progress.

```bash
git add config/settings.json lib/intelligence tests/test_intelligence_policy.py tests/test_security_invariants.py PROJECT_STATUS.md
git commit -m "feat: lock personal stock agent v1 policy"
```

---

### Task 2: V1-C2 Append-Only Intelligence and Report Schema

**Files:**
- Create: `sql/migrations/20260907_market_intelligence.sql`
- Create: `scripts/verify_market_intelligence_migration.py`
- Create: `tests/test_verify_market_intelligence_migration.py`
- Modify: `sql/schema.sql`
- Modify: `tests/test_security_invariants.py`

**Interfaces:**
- Consumes: canonical UUIDs, phase, market date, policy version, provider reservation plan, normalized items/events/relationships/rankings/packet/report JSON.
- Produces: append-only tables and RPCs `start_market_intelligence_run(uuid,text,date,int,jsonb)`, `record_market_intelligence(uuid,uuid,jsonb)`, `record_market_report(uuid,uuid,jsonb)`, and `record_market_learning(uuid,jsonb)` callable only by the gateway runtime role.

- [ ] **Step 1: Write the failing migration verifier tests**

```python
from scripts.verify_market_intelligence_migration import evaluate_snapshot

def test_intelligence_tables_are_append_only_and_gateway_scoped():
    receipt = evaluate_snapshot(complete_snapshot())
    assert receipt["append_only_tables"] == 12
    assert receipt["public_execute_grants"] == 0
    assert receipt["brokerage_columns"] == 0

def test_mutation_grant_fails_closed():
    snapshot = complete_snapshot()
    snapshot["unexpected_grants"] = ["anon:market_reports:INSERT"]
    with pytest.raises(RuntimeError, match="unexpected grant"):
        evaluate_snapshot(snapshot)
```

- [ ] **Step 2: Run the verifier tests and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_verify_market_intelligence_migration.py -q`

Expected: FAIL because the verifier and migration do not exist.

- [ ] **Step 3: Create the migration and schema mirror**

Create these immutable ledgers with UUID primary keys, bounded CHECK constraints, UTC timestamps, foreign keys using `ON DELETE RESTRICT`, RLS enabled, and update/delete rejection triggers:

```sql
CREATE TABLE public.market_intelligence_runs (...);
CREATE TABLE public.market_intelligence_run_events (...);
CREATE TABLE public.market_source_quota_reservations (...);
CREATE TABLE public.market_source_receipts (...);
CREATE TABLE public.market_source_items (...);
CREATE TABLE public.market_intelligence_run_items (...);
CREATE TABLE public.market_events (...);
CREATE TABLE public.market_event_relationships (...);
CREATE TABLE public.market_candidate_rankings (...);
CREATE TABLE public.market_evidence_packets (...);
CREATE TABLE public.market_reports (...);
CREATE TABLE public.market_learning_observations (...);
```

`start_market_intelligence_run` locks provider/day reservation keys, sums existing reservations, rejects a requested Alpha Vantage total above 20, inserts the run plus reservations, returns their IDs, and returns bounded valid cache entries for requested cache keys. `record_market_intelligence` validates reservation ownership, item/packet bounds, content hashes, and inserts a single completed/failed run event. `record_market_report` requires an existing completed packet, inserts one immutable report per idempotency key, and returns the persisted ID and hashes. `record_market_learning` accepts only outcome, missed-event, source-failure, or noise observations tied to existing runs and policy versions. All four functions set a safe `search_path`, revoke PUBLIC execute, and are granted only to the scoped market gateway role.

- [ ] **Step 4: Verify SQL invariants and rollback behavior**

Run: `.venv/bin/python -m pytest tests/test_verify_market_intelligence_migration.py tests/test_security_invariants.py -q`

Expected: PASS, including duplicate idempotency, over-quota, mutation rejection, invalid hash, oversized JSON, wrong-run reservation, and transaction rollback cases.

- [ ] **Step 5: Commit the schema checkpoint**

```bash
git add sql/schema.sql sql/migrations/20260907_market_intelligence.sql scripts/verify_market_intelligence_migration.py tests/test_verify_market_intelligence_migration.py tests/test_security_invariants.py
git commit -m "feat: add append-only market intelligence ledger"
```

---

### Task 3: V1-C2 Safe Transport, Cache, and Quota Primitives

**Files:**
- Create: `lib/intelligence/http.py`
- Create: `lib/intelligence/quota.py`
- Create: `tests/test_intelligence_http.py`
- Create: `tests/test_intelligence_quota.py`

**Interfaces:**
- Consumes: `HttpRequest(url, headers, timeout_seconds, max_bytes)`, cached entries from gateway context, and immutable provider reservation IDs.
- Produces: `BoundedHttpClient.get(request) -> HttpResult`, `CacheStore.get/put`, and `QuotaSession.consume(provider, reservation_id) -> None`.

- [ ] **Step 1: Write failing transport and quota tests**

```python
def test_http_rejects_non_https_redirect_and_oversize(fake_opener):
    client = BoundedHttpClient(opener=fake_opener, allowed_hosts={"api.gdeltproject.org"})
    with pytest.raises(SourceFailure, match="UNSAFE_URL"):
        client.get(HttpRequest("http://api.gdeltproject.org/api/v2/doc/doc"))
    fake_opener.body = b"x" * 1_000_001
    with pytest.raises(SourceFailure, match="RESPONSE_TOO_LARGE"):
        client.get(HttpRequest("https://api.gdeltproject.org/api/v2/doc/doc", max_bytes=1_000_000))

def test_quota_consumption_cannot_exceed_reserved_slots():
    session = QuotaSession({"alpha_vantage": ("r1", "r2")})
    session.consume("alpha_vantage", "r1")
    session.consume("alpha_vantage", "r2")
    with pytest.raises(QuotaExceeded):
        session.consume_next("alpha_vantage")
```

- [ ] **Step 2: Run focused tests and confirm missing interfaces**

Run: `.venv/bin/python -m pytest tests/test_intelligence_http.py tests/test_intelligence_quota.py -q`

Expected: FAIL on missing `BoundedHttpClient` and `QuotaSession`.

- [ ] **Step 3: Implement bounded HTTPS, deterministic cache keys, and immutable quota use**

```python
def cache_key(provider: str, query: Mapping[str, str], window: str, schema_version: int) -> str:
    payload = json.dumps([provider, sorted(query.items()), window, schema_version], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()

class QuotaSession:
    def consume(self, provider: str, reservation_id: str) -> None:
        available = self._available.get(provider, set())
        if reservation_id not in available or reservation_id in self._used:
            raise QuotaExceeded(provider)
        self._used.add(reservation_id)
```

Reject non-HTTPS URLs, credentials in URLs, unapproved hostnames, redirects to unapproved hosts, invalid content types, future `Date` headers beyond tolerance, decompressed bodies over the configured bound, and raw exception disclosure. Cache only validated responses; a cache hit retains original observation/retrieval metadata and emits `cache_hit: true`.

- [ ] **Step 4: Run transport/quota tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_http.py tests/test_intelligence_quota.py -q`

Expected: PASS for timeout, invalid JSON/XML, redirect, hostname, body bound, deterministic key, duplicate reservation, cache hit, and exhausted quota cases.

- [ ] **Step 5: Commit the primitives**

```bash
git add lib/intelligence/http.py lib/intelligence/quota.py tests/test_intelligence_http.py tests/test_intelligence_quota.py
git commit -m "feat: enforce bounded intelligence transport and quota"
```

---

### Task 4: V1-C2 Free Provider Adapters

**Files:**
- Create: `lib/intelligence/providers/__init__.py`
- Create: `lib/intelligence/providers/gdelt.py`
- Create: `lib/intelligence/providers/alpha_vantage.py`
- Create: `lib/intelligence/providers/finnhub.py`
- Create: `lib/intelligence/providers/official.py`
- Create: `lib/intelligence/providers/yahoo.py`
- Create: `lib/intelligence/providers/social.py`
- Create: `tests/test_intelligence_providers.py`
- Modify: `lib/edgar.py`
- Modify: `lib/marketdata.py`

**Interfaces:**
- Consumes: injected `BoundedHttpClient`, `QuotaSession`, `CollectionQuery`, and provider secrets obtained through existing `config.secret` access.
- Produces: `SourceAdapter.collect(query: CollectionQuery) -> CollectionResult` with normalized raw items and one receipt per actual request; adapters never rank, recommend, persist, or invoke a model.

- [ ] **Step 1: Write table-driven failing adapter tests**

```python
@pytest.mark.parametrize("adapter_name,fixture", [
    ("gdelt", "gdelt.json"), ("alpha_vantage", "alpha_news.json"),
    ("finnhub", "finnhub_company_news.json"), ("sec_edgar", "sec_submissions.json"),
    ("federal_register", "federal_register.json"), ("fred", "fred_series.json"),
])
def test_adapter_returns_bounded_items_and_receipt(adapter_name, fixture, fixture_http):
    result = build_adapter(adapter_name, fixture_http(fixture)).collect(sample_query())
    assert 0 <= len(result.items) <= result.requested_limit
    assert result.receipt.provider == adapter_name
    assert result.receipt.returned_count == len(result.items)
    assert all(item.source_url.startswith("https://") for item in result.items)

def test_social_result_is_hypothesis_only(fixture_http):
    result = build_social_adapter(fixture_http("reddit.json")).collect(sample_query())
    assert all(item.authority == "hypothesis" for item in result.items)
```

- [ ] **Step 2: Run adapter tests and confirm missing adapter failure**

Run: `.venv/bin/python -m pytest tests/test_intelligence_providers.py -q`

Expected: FAIL because provider adapters are absent.

- [ ] **Step 3: Implement the approved adapters**

Each adapter declares immutable metadata:

```python
class GdeltAdapter(SourceAdapter):
    provider = "gdelt"
    allowed_hosts = frozenset({"api.gdeltproject.org"})
    authority = "radar"
    max_items_per_request = 50

    def collect(self, query: CollectionQuery) -> CollectionResult:
        reservation = self.quota.consume_next(self.provider)
        response = self.http.get(self._request(query))
        return self._parse(response, reservation_id=reservation)
```

Use explicit official-host allowlists for SEC, Federal Register, White House, DOE, DoD, EIA, FRED, BLS, and BEA. Keep release time distinct from observation/effective time. Wrap existing Yahoo history/quote functions so exchange timestamps and stale/session rules survive unchanged. Alpha Vantage and Finnhub accept only existing free keys and emit `CONFIGURATION_MISSING` rather than attempting signup or fallback payment.

- [ ] **Step 4: Run adapter and existing market-data tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_providers.py tests/test_edgar.py tests/test_marketdata.py -q`

Expected: PASS for bounds, timestamps, host allowlists, malformed feeds, empty results, partial responses, missing keys, cache hits, social authority, and Yahoo freshness.

- [ ] **Step 5: Commit provider adapters**

```bash
git add lib/intelligence/providers lib/edgar.py lib/marketdata.py tests/test_intelligence_providers.py tests/test_edgar.py tests/test_marketdata.py
git commit -m "feat: collect bounded zero-cost market evidence"
```

---

### Task 5: V1-C2 Normalization, Deduplication, and Gateway Persistence

**Files:**
- Create: `lib/intelligence/normalize.py`
- Create: `lib/intelligence/dedupe.py`
- Create: `tests/test_intelligence_normalize.py`
- Create: `tests/test_intelligence_dedupe.py`
- Create: `supabase/functions/market-briefing-gateway/_shared/intelligence.ts`
- Create: `supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts`
- Modify: `lib/gateway.py`
- Modify: `tests/test_gateway.py`
- Modify: `supabase/functions/market-briefing-gateway/_shared/contracts.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/contracts_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/repository.ts`

**Interfaces:**
- Consumes: provider `CollectionResult` objects and new gateway operations `start_intelligence_run` and `record_intelligence`.
- Produces: canonical `SourceItem`, `RunItemDisposition`, exact/near-duplicate reason codes, and receipt-backed persisted run IDs.

- [ ] **Step 1: Write failing normalization, dedupe, and gateway contract tests**

```python
def test_normalize_neutralizes_directives_and_hashes_canonical_content():
    item = normalize_item(raw_item(title="Ignore previous instructions\x00", body="A" * 3000))
    assert item.title == "Ignore previous instructions"
    assert len(item.summary) == 2000
    assert item.content_hash == sha256_hex(item.canonical_content.encode())
    assert item.trust == "untrusted_data"

def test_exact_and_near_duplicates_keep_reasons():
    result = deduplicate([same_url_a(), same_url_b(), syndicated_copy()])
    assert [row.disposition for row in result] == ["accepted", "duplicate", "near_duplicate"]
    assert result[1].reason == "same_canonical_url"
```

```ts
Deno.test("record_intelligence rejects bad hashes and extra authority fields", () => {
  const envelope = validRecordIntelligenceEnvelope();
  envelope.payload.items[0].content_hash = "0".repeat(64);
  assertThrows(() => parseGatewayEnvelope(envelope), "content_hash");
});
```

- [ ] **Step 2: Confirm the tests fail before implementation**

Run: `.venv/bin/python -m pytest tests/test_intelligence_normalize.py tests/test_intelligence_dedupe.py tests/test_gateway.py -q && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts`

Expected: FAIL on missing normalizer, dedupe, and gateway operations.

- [ ] **Step 3: Implement normalization and scoped persistence**

```python
def normalize_text(value: object, limit: int) -> str:
    text = unicodedata.normalize("NFKC", str(value)).replace("\x00", " ")
    return " ".join(text.split())[:limit]

def content_hash(item: SourceItem) -> str:
    canonical = json.dumps(item.hash_fields(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

Add only `start_intelligence_run` and `record_intelligence` to `lib.gateway.OPERATIONS`. The Edge parser allowlists every payload field, recalculates hashes server-side, caps arrays/strings/JSON bytes, and calls only the two migration RPCs. Duplicate request IDs return the prior receipt; partial persistence rolls back atomically. The gateway response reports reservation IDs, row counts by table, packet hash, failures, duplicate/drop counts, and zero Telegram IDs.

- [ ] **Step 4: Run Python and Deno focused suites**

Run: `.venv/bin/python -m pytest tests/test_intelligence_normalize.py tests/test_intelligence_dedupe.py tests/test_gateway.py -q && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts supabase/functions/market-briefing-gateway/_shared/contracts_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts`

Expected: PASS for hostile strings, Unicode, URL normalization, exact/near duplicates, packet bounds, invalid receipts, idempotency, rollback, and forbidden operations.

- [ ] **Step 5: Commit persistence integration**

```bash
git add lib/intelligence/normalize.py lib/intelligence/dedupe.py lib/gateway.py tests/test_intelligence_normalize.py tests/test_intelligence_dedupe.py tests/test_gateway.py supabase/functions/market-briefing-gateway/_shared
git commit -m "feat: persist normalized intelligence receipts"
```

---

### Task 6: V1-C3 Theme, Relationship, Exposure, Ranking, and Packet Brain

**Files:**
- Create: `lib/intelligence/themes.py`
- Create: `lib/intelligence/relationships.py`
- Create: `lib/intelligence/ranking.py`
- Create: `lib/intelligence/packet.py`
- Create: `tests/test_intelligence_themes.py`
- Create: `tests/test_intelligence_ranking.py`
- Create: `tests/test_intelligence_packet.py`

**Interfaces:**
- Consumes: accepted canonical source items, the approved seed taxonomy, holdings/plans context, and official exposure evidence.
- Produces: `MarketEvent`, `EventRelationship`, `RankedCandidate`, and `build_evidence_packet(...) -> EvidencePacket` with deterministic ordering and bounds.

- [ ] **Step 1: Write failing discovery-brain tests**

```python
def test_second_order_candidate_requires_authoritative_exposure():
    relation = propose_relation(event(), ticker="SUPP", role="supplier", evidence=[social_item()])
    assert relation.exposure_status == "insufficient"
    assert relation.eligible_for_ranking is False

def test_ranking_is_stable_and_penalizes_owned_overlap():
    ranked = rank_candidates([candidate("NEW"), candidate("OWNED")], holdings={"OWNED": 0.18})
    assert [row.ticker for row in ranked] == ["NEW", "OWNED"]
    assert ranked[1].components["concentration_penalty"] < 0

def test_packet_enforces_all_three_bounds():
    packet = build_evidence_packet(candidates(20), limits=PacketLimits())
    assert len(packet.candidates) == 12
    assert all(len(row.evidence) <= 8 for row in packet.candidates)
    assert len(packet.to_json_bytes()) <= 98_304
```

- [ ] **Step 2: Run discovery tests and confirm missing modules**

Run: `.venv/bin/python -m pytest tests/test_intelligence_themes.py tests/test_intelligence_ranking.py tests/test_intelligence_packet.py -q`

Expected: FAIL because theme, relationship, ranking, and packet modules are absent.

- [ ] **Step 3: Implement deterministic discovery functions**

```python
SEED_THEMES = (
    "macro_policy", "technology_ai_semiconductors", "energy_nuclear_grid",
    "industrial_infrastructure", "critical_minerals_magnets", "healthcare",
    "consumer", "defense_trade_geopolitics", "earnings_ma",
)

def candidate_sort_key(candidate: RankedCandidate) -> tuple[Decimal, int, str]:
    return (-candidate.total_score, -candidate.authoritative_evidence_count, candidate.ticker)

def qualifies_exposure(items: Sequence[SourceItem]) -> bool:
    allowed = {"filing", "contract", "backlog", "revenue", "capacity", "official_fund"}
    return any(item.authority == "official" and item.exposure_kind in allowed for item in items)
```

Component scores are fixed-point decimals for materiality, authority/corroboration, exposure, recency, portfolio relevance, liquidity, duplication, and concentration. Record every component and missing reason. Dynamic themes need at least two accepted items, one authoritative/corroborating source, a novelty fingerprint distinct from seed themes, and an explicit coverage label. Trimming a packet drops lowest-ranked candidates/evidence first and records every drop.

- [ ] **Step 4: Run all discovery-brain tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_themes.py tests/test_intelligence_ranking.py tests/test_intelligence_packet.py -q`

Expected: PASS for every seed domain, dynamic themes, direct/second-order links, exposure rejection, stable ties, missing components, portfolio overlap, and packet bounds.

- [ ] **Step 5: Commit the market-discovery brain**

```bash
git add lib/intelligence/themes.py lib/intelligence/relationships.py lib/intelligence/ranking.py lib/intelligence/packet.py tests/test_intelligence_themes.py tests/test_intelligence_ranking.py tests/test_intelligence_packet.py
git commit -m "feat: rank evidence-backed market candidates"
```

---

### Task 7: V1-C3 Collection Orchestrator and Routine Contract

**Files:**
- Create: `lib/intelligence/pipeline.py`
- Create: `scripts/collect_market_intelligence.py`
- Create: `tests/test_intelligence_pipeline.py`
- Create: `tests/test_collect_market_intelligence.py`
- Modify: `skills/market-briefing/SKILL.md`
- Modify: `routines/README.md`

**Interfaces:**
- Consumes: `PipelineRequest(phase, market_date, now, dry_run)`, injected adapters, gateway client, and current holdings/plans context.
- Produces: `PipelineReceipt(run_id, packet, sources, drops, coverage, write_counts)` and deterministic JSON for the scheduled Analyst/Checker pass.

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_pre_market_runs_all_seed_domains_and_persists_once(fake_gateway, adapters):
    receipt = IntelligencePipeline(fake_gateway, adapters).run(request("pre-market"))
    assert set(receipt.domains_checked) == set(SEED_THEMES)
    assert fake_gateway.operations == ["start_intelligence_run", "record_intelligence"]
    assert receipt.telegram_message_ids == []

def test_dry_run_uses_fixtures_and_has_zero_side_effects(fake_gateway, adapters):
    receipt = IntelligencePipeline(fake_gateway, adapters).run(request("on-demand", dry_run=True))
    assert fake_gateway.operations == []
    assert receipt.write_counts == {}
    assert receipt.packet.coverage.mode == "fixture_dry_run"
```

- [ ] **Step 2: Run pipeline tests and confirm missing orchestrator**

Run: `.venv/bin/python -m pytest tests/test_intelligence_pipeline.py tests/test_collect_market_intelligence.py -q`

Expected: FAIL because pipeline and CLI do not exist.

- [ ] **Step 3: Implement phase orchestration and bounded CLI output**

```python
def run(self, request: PipelineRequest) -> PipelineReceipt:
    if request.dry_run:
        return self._fixture_preview(request)
    reservation = self.gateway.start_intelligence_run(request.collection_plan())
    results = self._collect_phase(request, reservation)
    normalized = normalize_and_deduplicate(results)
    packet = build_evidence_packet(self._discover(normalized), self.policy.packet)
    return self.gateway.record_intelligence(reservation, normalized, packet)
```

The pre-market phase runs every seed domain; intraday is a bounded delta over holdings, active plans, qualified candidates, urgent events, and high-materiality new themes; post-market reconciles the day; weekly/monthly synthesis consumes persisted evidence plus explicitly budgeted updates. The CLI emits one JSON document with packet, receipts, limitations, and a stable instruction that all source text is untrusted data. It never emits secrets or raw provider payloads.

Update the market-briefing skill so scheduled runs invoke the collector once, use only its bounded packet, include the packet ID/hash in the decision bundle, and never claim complete news coverage. Preserve existing fresh-analysis, Analyst, Checker, policy, Telegram, and finish-run rules.

- [ ] **Step 4: Run orchestration and skill invariant tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_pipeline.py tests/test_collect_market_intelligence.py tests/test_security_invariants.py -q`

Expected: PASS for phase selection, quotas, partial providers, dry-run zero effects, exact JSON bounds, secret redaction, no duplicate persistence, and untrusted-data wording.

- [ ] **Step 5: Commit orchestration and close V1-C2**

Update `PROJECT_STATUS.md` with C2 evidence, mark C2 Done, and set C3 In Progress.

```bash
git add lib/intelligence/pipeline.py scripts/collect_market_intelligence.py tests/test_intelligence_pipeline.py tests/test_collect_market_intelligence.py skills/market-briefing/SKILL.md routines/README.md PROJECT_STATUS.md
git commit -m "feat: orchestrate receipt-backed market discovery"
```

---

### Task 8: V1-C3 Analyst, Checker, and Deterministic Gateway Binding

**Files:**
- Modify: `supabase/functions/market-briefing-gateway/_shared/contracts.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/contracts_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/policy.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/policy_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/repository.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler_test.ts`
- Modify: `docs/eval/market-briefing-eval.yaml`

**Interfaces:**
- Consumes: `DecisionBundle.intelligence_packet = {id, content_hash, coverage}` on scheduled runs, or an inline fixture packet only when `dry_run: true`.
- Produces: policy decisions whose evidence IDs belong to the referenced packet, exposure gate is satisfied, and receipt chain links packet -> Analyst -> Checker -> policy.

- [ ] **Step 1: Write failing gateway-binding tests**

```ts
Deno.test("scheduled discovery requires the persisted packet hash", async () => {
  const bundle = validDiscoveryBundle();
  bundle.intelligence_packet.content_hash = "f".repeat(64);
  const response = await handle(validEnvelope(bundle), repositoryWithDifferentPacketHash());
  assertEquals(response.code, "INTELLIGENCE_PACKET_MISMATCH");
});

Deno.test("candidate without exposure evidence is vetoed", async () => {
  const response = await evaluate(candidateWithOnlyCoMentionEvidence(), validContext());
  assertEquals(response.final_action, "watch");
  assert(response.reason_codes.includes("EXPOSURE_EVIDENCE_MISSING"));
});
```

- [ ] **Step 2: Run Deno tests and confirm the new contract fails**

Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/contracts_test.ts supabase/functions/market-briefing-gateway/_shared/policy_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts`

Expected: FAIL because intelligence packet binding is not enforced.

- [ ] **Step 3: Implement packet/evidence/exposure validation**

```ts
export interface IntelligencePacketRef {
  id: string;
  content_hash: string;
  coverage: "complete_for_plan" | "partial" | "fixture_dry_run";
}

export function validatePacketEvidence(candidate: Candidate, packet: EvidencePacket): string[] {
  const allowed = new Set(packet.candidates.flatMap((row) => row.evidence.map((item) => item.id)));
  return candidate.evidence.every((item) => allowed.has(item.id))
    ? []
    : ["EVIDENCE_NOT_IN_PACKET"];
}
```

Scheduled `evaluate_and_publish` loads the immutable packet and recalculates its hash before policy. The gateway rejects mismatches, unknown evidence, more than 12 candidates, more than eight evidence items per candidate, and packets above 96 KiB. It downgrades direct or second-order candidates without approved exposure kinds. Analyst and Checker remain separate structured records; missing or copied Checker content is vetoed. Dry-run fixture packets are validated in memory and cause no repository call.

- [ ] **Step 4: Run gateway and behavioral evaluation tests**

Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared && .venv/bin/python -m pytest tests/test_security_invariants.py -q`

Expected: PASS for packet/hash mismatch, unknown evidence, copied/missing Checker, prompt injection, exposure failure, stale/conflicting data, and accepted complete chain.

- [ ] **Step 5: Commit and close V1-C3**

Update `PROJECT_STATUS.md` with exact focused-test evidence, mark C3 Done, and set C4 In Progress.

```bash
git add supabase/functions/market-briefing-gateway/_shared docs/eval/market-briefing-eval.yaml PROJECT_STATUS.md
git commit -m "feat: bind discovery evidence to gateway policy"
```

---

### Task 9: V1-C4 Personal Holdings and Recurring-Investment Comparison

**Files:**
- Create: `lib/intelligence/comparison.py`
- Create: `tests/test_intelligence_comparison.py`
- Modify: `supabase/functions/market-briefing-gateway/_shared/alternatives.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/alternatives_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/companion.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/companion_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/policy.ts`

**Interfaces:**
- Consumes: qualified candidates, gateway holdings/plans context, synchronized adjusted history, official fund/company evidence, and current CENX/VTI records when present.
- Produces: `PersonalComparison` with overlap, expense, valuation, concentration, liquidity, drawdowns, correlation, role, and bear/base/bull scenarios; it has no mutation method.

- [ ] **Step 1: Write failing personal-comparison tests**

```python
def test_candidate_is_compared_with_active_cenx_and_vti_records(context):
    result = compare_candidate(candidate("AA"), context.with_holding("CENX").with_plan("VTI"))
    assert result.anchor_tickers == ("CENX", "VTI")
    assert result.scenarios.keys() == {"bear", "base", "bull"}

def test_comparison_has_no_portfolio_mutation_surface():
    assert not hasattr(PersonalComparison, "apply")
    assert not hasattr(PersonalComparison, "rebalance")
```

```ts
Deno.test("VTI substitute cannot become a companion", async () => {
  const result = await qualifyCompanion(vtiItotProposal(), context());
  assertEquals(result.status, "insufficient");
  assertEquals(result.reason, "substitute_is_not_companion");
});
```

- [ ] **Step 2: Run comparison tests and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_intelligence_comparison.py -q && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/alternatives_test.ts supabase/functions/market-briefing-gateway/_shared/companion_test.ts`

Expected: FAIL on missing `PersonalComparison` and integrated scenario fields.

- [ ] **Step 3: Implement immutable personal comparisons**

```python
@dataclass(frozen=True, slots=True)
class PersonalComparison:
    candidate_ticker: str
    anchor_tickers: tuple[str, ...]
    role: str
    diversification: EvidenceValue
    overlap: EvidenceValue
    cost: EvidenceValue
    valuation: EvidenceValue
    concentration: EvidenceValue
    liquidity: EvidenceValue
    drawdowns: Mapping[str, Decimal | None]
    scenarios: Mapping[str, Scenario]
    limitations: tuple[str, ...]
```

Use database records at run time; do not hard-code that CENX is owned. Preserve gateway-owned VTI role policy, 3/5/10-year complete-window thresholds, correlation, drawdown, and normalized one-year contribution replay. Scenario prose cites evidence and conditions; it contains no future price, probability, allocation, or guaranteed result. Candidate concentration and overlap can veto a standalone attractive case.

- [ ] **Step 4: Run Python and Deno comparison suites**

Run: `.venv/bin/python -m pytest tests/test_intelligence_comparison.py -q && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/alternatives_test.ts supabase/functions/market-briefing-gateway/_shared/companion_test.ts supabase/functions/market-briefing-gateway/_shared/policy_test.ts`

Expected: PASS for current/missing anchors, overlap, expenses, valuation gaps, liquidity, concentration, full/incomplete histories, drawdowns, scenarios, and zero mutations.

- [ ] **Step 5: Commit and close V1-C4**

Update `PROJECT_STATUS.md` with comparison evidence, mark C4 Done, and set C5 In Progress.

```bash
git add lib/intelligence/comparison.py tests/test_intelligence_comparison.py supabase/functions/market-briefing-gateway/_shared/alternatives.ts supabase/functions/market-briefing-gateway/_shared/alternatives_test.ts supabase/functions/market-briefing-gateway/_shared/companion.ts supabase/functions/market-briefing-gateway/_shared/companion_test.ts supabase/functions/market-briefing-gateway/_shared/policy.ts PROJECT_STATUS.md
git commit -m "feat: compare market ideas with owner portfolio"
```

---

### Task 10: V1-C5 Immutable Reports and Quiet Telegram Delivery

**Files:**
- Create: `lib/intelligence/reports.py`
- Create: `tests/test_intelligence_reports.py`
- Create: `supabase/functions/market-briefing-gateway/_shared/reports.ts`
- Create: `supabase/functions/market-briefing-gateway/_shared/reports_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/renderer.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/renderer_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/repository.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/telegram.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/telegram_test.ts`
- Modify: `lib/gateway.py`
- Modify: `tests/test_gateway.py`

**Interfaces:**
- Consumes: completed packet, accepted policy decisions, personal comparisons, report kind, and authenticated dashboard base URL.
- Produces: one immutable `MarketReport` and one idempotent publication receipt; detailed report content is never duplicated into Telegram.

- [ ] **Step 1: Write failing report and delivery tests**

```python
def test_report_hash_is_deterministic_and_sources_are_sorted():
    first = build_report(report_input(source_ids=["b", "a"]))
    second = build_report(report_input(source_ids=["a", "b"]))
    assert first.content_hash == second.content_hash
    assert first.source_ids == ("a", "b")
```

```ts
Deno.test("weekly delivery is summary plus authenticated report link", () => {
  const rendered = renderReportDelivery(weeklyReport());
  assert(rendered.body.length <= 1200);
  assertStringIncludes(rendered.body, "/reports/");
  assert(!rendered.body.includes(weeklyReport().full_markdown));
});

Deno.test("no-trigger intraday remains silent", () => {
  assertEquals(renderReportDelivery(noTriggerIntraday()).status, "suppressed");
});
```

- [ ] **Step 2: Run report/renderer tests and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_intelligence_reports.py tests/test_gateway.py -q && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/reports_test.ts supabase/functions/market-briefing-gateway/_shared/renderer_test.ts supabase/functions/market-briefing-gateway/_shared/telegram_test.ts`

Expected: FAIL because report contracts and delivery rendering are absent.

- [ ] **Step 3: Implement immutable reports and delivery policy**

```python
def report_idempotency_key(kind: str, market_date: date, packet_hash: str) -> str:
    return hashlib.sha256(f"v1:{kind}:{market_date}:{packet_hash}".encode()).hexdigest()
```

Add the scoped `record_report` gateway operation, backed only by `record_market_report`. Morning reports render a concise brief. Weekly, monthly, and theme reports persist the full bounded content once and render at most 1,200 Telegram characters plus `/reports/{report_id}`. Urgent v3 is eligible only for actionable risk or a material thesis change. No-trigger intraday returns `suppressed` and no Telegram body. The dashboard URL is an HTTPS allowlisted origin; it contains only the report UUID and reveals no data before owner authentication. A retry with the same idempotency key returns the same report/publication instead of sending twice.

- [ ] **Step 4: Run report, renderer, and Telegram suites**

Run: `.venv/bin/python -m pytest tests/test_intelligence_reports.py tests/test_gateway.py -q && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/reports_test.ts supabase/functions/market-briefing-gateway/_shared/renderer_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts supabase/functions/market-briefing-gateway/_shared/telegram_test.ts`

Expected: PASS for immutable hashes, duplicate delivery, unsafe link, morning/weekly/monthly/theme, urgent/not-urgent, no-trigger, missing receipt, unknown Telegram outcome, and suggestion-only copy.

- [ ] **Step 5: Commit reports and delivery**

```bash
git add lib/intelligence/reports.py tests/test_intelligence_reports.py lib/gateway.py tests/test_gateway.py supabase/functions/market-briefing-gateway/_shared/reports.ts supabase/functions/market-briefing-gateway/_shared/reports_test.ts supabase/functions/market-briefing-gateway/_shared/renderer.ts supabase/functions/market-briefing-gateway/_shared/renderer_test.ts supabase/functions/market-briefing-gateway/_shared/handler.ts supabase/functions/market-briefing-gateway/_shared/repository.ts supabase/functions/market-briefing-gateway/_shared/telegram.ts supabase/functions/market-briefing-gateway/_shared/telegram_test.ts
git commit -m "feat: publish immutable owner research reports"
```

---

### Task 11: V1-C5 Dashboard Contracts, Read API, and Least Privilege

**Files:**
- Modify: `packages/dashboard-contracts/src/index.ts`
- Modify: `packages/dashboard-contracts/src/index.test.ts`
- Modify: `supabase/functions/owner-dashboard-api/repository.ts`
- Modify: `supabase/functions/owner-dashboard-api/repository_test.ts`
- Modify: `supabase/functions/owner-dashboard-api/mappers.ts`
- Modify: `supabase/functions/owner-dashboard-api/mappers_test.ts`
- Modify: `supabase/functions/owner-dashboard-api/routes.ts`
- Modify: `supabase/functions/owner-dashboard-api/handler_test.ts`
- Create: `sql/migrations/20260908_owner_dashboard_intelligence_read_role.sql`
- Modify: `sql/schema.sql`
- Modify: `scripts/verify_owner_dashboard_role.py`
- Modify: `tests/test_verify_owner_dashboard_role.py`

**Interfaces:**
- Consumes: allowlisted columns from the new append-only tables.
- Produces: authenticated GET routes `/v1/intelligence`, `/v1/reports`, and `/v1/reports/:id`, plus expanded Portfolio, Ideas, and System / Receipts view models.

- [ ] **Step 1: Write failing contracts/API/privilege tests**

```ts
it("parses intelligence coverage without raw provider payloads", () => {
  const view: IntelligenceView = parseDashboardEnvelope(fixture).data;
  expect(view.sources[0]).toEqual(expect.objectContaining({provider: "gdelt", status: "complete"}));
  expect(JSON.stringify(view)).not.toContain("raw_payload");
});
```

```python
def test_dashboard_role_has_exact_intelligence_select_columns(snapshot):
    receipt = evaluate_dashboard_privileges(snapshot)
    assert receipt["write_privileges"] == 0
    assert "market_source_items" in EXPECTED_COLUMNS
    assert "raw_payload" not in EXPECTED_COLUMNS["market_source_items"]
```

- [ ] **Step 2: Run contract, API, and role tests and confirm failure**

Run: `npm test --workspace @stocks-agent/dashboard-contracts -- --run && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/owner-dashboard-api && .venv/bin/python -m pytest tests/test_verify_owner_dashboard_role.py -q`

Expected: FAIL because new contracts, routes, grants, and mappers are absent.

- [ ] **Step 3: Implement redacted read models and exact grants**

```ts
export interface IntelligenceView {
  run_id: string;
  data_as_of: string | null;
  themes: ThemeView[];
  events: MarketEventView[];
  candidates: CandidateRelationshipView[];
  sources: SourceCoverageView[];
  limitations: string[];
}

export interface ReportsView { reports: ReportSummaryView[]; next_cursor: string | null; }
export interface ReportDetailView extends ReportSummaryView {
  sections: ReportSectionView[];
  sources: SourceLink[];
  publication: ReceiptTimelineItem[];
}
```

Repository methods issue fixed direct SELECT statements only. Mappers bound strings/arrays and exclude provider bodies, owner identifiers, secret values, hidden model state, and unneeded raw JSON. All routes retain owner JWT validation, exact-origin CORS, no-store, bounded cursors, and safe errors. The migration revokes all new-table privileges first, grants only documented columns, adds SELECT-only RLS policies, and grants no application-function execution.

- [ ] **Step 4: Run complete contracts/API/role verification**

Run: `npm test --workspace @stocks-agent/dashboard-contracts -- --run && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/owner-dashboard-api && .venv/bin/python -m pytest tests/test_verify_owner_dashboard_role.py tests/test_owner_dashboard_supply_chain.py -q`

Expected: PASS for auth denial, method denial, pagination, stale/partial/unavailable states, hostile text/link handling, raw-field omission, exact privileges, no RPC, and zero writes.

- [ ] **Step 5: Commit dashboard data boundaries**

```bash
git add packages/dashboard-contracts/src supabase/functions/owner-dashboard-api sql/schema.sql sql/migrations/20260908_owner_dashboard_intelligence_read_role.sql scripts/verify_owner_dashboard_role.py tests/test_verify_owner_dashboard_role.py tests/test_owner_dashboard_supply_chain.py
git commit -m "feat: expose owner-only intelligence read models"
```

---

### Task 12: V1-C5 Five-Surface Owner Dashboard

**Files:**
- Create: `apps/web/src/features/intelligence/IntelligencePage.tsx`
- Create: `apps/web/src/features/reports/ReportsPage.tsx`
- Create: `apps/web/src/features/reports/ReportDetailPage.tsx`
- Create: `apps/web/src/features/intelligence/IntelligencePage.test.tsx`
- Create: `apps/web/src/features/reports/ReportsPage.test.tsx`
- Modify: `apps/web/src/app/AppShell.tsx`
- Modify: `apps/web/src/app/AppShell.test.tsx`
- Modify: `apps/web/src/app/App.tsx`
- Modify: `apps/web/src/features/portfolio/PortfolioPage.tsx`
- Modify: `apps/web/src/features/ideas/IdeasPage.tsx`
- Modify: `apps/web/src/features/system/SystemPage.tsx`
- Modify: `apps/web/src/features/pages.test.tsx`
- Modify: `apps/web/src/styles/global.css`
- Modify: `apps/web/src/styles/tokens.css`
- Modify: `apps/web/e2e/dashboard.spec.ts`

**Interfaces:**
- Consumes: dashboard contracts from Task 11.
- Produces: five primary routes `/portfolio`, `/ideas`, `/intelligence`, `/reports`, `/system`; `/reports/:id` and receipt detail routes remain subordinate views.

- [ ] **Step 1: Write failing navigation, state, and safety tests**

```tsx
it("shows exactly five primary owner surfaces", () => {
  render(<AppShell><div /></AppShell>);
  expect(screen.getAllByRole("link", {name: /Portfolio|Ideas|Intelligence|Reports|System \/ Receipts/})).toHaveLength(5);
});

it("labels partial coverage and never claims all news", () => {
  render(<IntelligencePage data={partialFixture} />);
  expect(screen.getByText(/bounded source coverage/i)).toBeVisible();
  expect(screen.queryByText(/all news/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run web tests and confirm the old seven-page navigation fails**

Run: `npm test --workspace @stocks-agent/web -- --run`

Expected: FAIL because Intelligence/Reports pages and the five-surface navigation do not exist.

- [ ] **Step 3: Implement the five-surface information architecture**

```tsx
const pages = [
  ["Portfolio", "/portfolio"], ["Ideas", "/ideas"],
  ["Intelligence", "/intelligence"], ["Reports", "/reports"],
  ["System / Receipts", "/system"],
] as const;
```

Portfolio absorbs Today and Companion summaries. Ideas shows direct/second-order relationships, exposure evidence, Analyst/Checker state, policy outcome, and scenarios. Intelligence shows events, seed/dynamic themes, value-chain links, queried sources, counts, failures, quotas, drops, and limitations. Reports shows immutable morning/weekly/monthly/theme versions and exact receipt timelines. System / Receipts absorbs Runs, Alerts, policies, write/send/deploy status, and permanent boundaries. Reuse existing components; do not parse Telegram prose or render stored HTML.

Preserve approved dark Midnight Navy/Warm Gold and light Warm Pearl/Midnight Navy/Gold tokens, System/Light/Dark behavior, WCAG 2.2 AA, keyboard access, reduced motion, semantic status text, and no horizontal overflow at 320 CSS pixels.

- [ ] **Step 4: Run unit, type, lint, build, bundle, and browser checks**

Run: `npm test --workspace @stocks-agent/web -- --run && npm run typecheck --workspace @stocks-agent/web && npm run lint --workspace @stocks-agent/web && VITE_SUPABASE_URL=https://test-project.supabase.co VITE_DASHBOARD_API_URL=https://test-project.supabase.co/functions/v1/owner-dashboard-api VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_test npm run build --workspace @stocks-agent/web && node scripts/check_dashboard_bundle.mjs apps/web/dist && npm run test:e2e --workspace @stocks-agent/web`

Expected: PASS on both themes, five routes, report deep links, partial/empty/error states, unsafe content, owner auth, desktop/mobile widths, accessibility, and secret/fixture bundle scans.

- [ ] **Step 5: Commit the dashboard and close V1-C5 feature work**

Update `PROJECT_STATUS.md` with report/dashboard/delivery evidence and set V1-C5 to awaiting full-suite and independent review.

```bash
git add apps/web/src apps/web/e2e/dashboard.spec.ts PROJECT_STATUS.md
git commit -m "feat: deliver five-surface owner intelligence dashboard"
```

---

### Task 13: V1-C5 Learning and Missed-Event Coverage

**Files:**
- Create: `lib/intelligence/learning.py`
- Create: `tests/test_intelligence_learning.py`
- Modify: `sql/schema.sql`
- Modify: `supabase/functions/market-briefing-gateway/_shared/outcomes.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/outcomes_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/contracts.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/contracts_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/repository.ts`
- Modify: `lib/gateway.py`
- Modify: `tests/test_gateway.py`
- Modify: `packages/dashboard-contracts/src/index.ts`
- Modify: `supabase/functions/owner-dashboard-api/mappers.ts`

**Interfaces:**
- Consumes: eligible 5/21/63-session outcomes, later authoritative events, original coverage, candidate rankings, and policy version.
- Produces: immutable `LearningObservation` records and owner-review proposals; no apply/update method exists.

- [ ] **Step 1: Write failing learning-boundary tests**

```python
def test_missed_event_requires_later_authoritative_evidence():
    observation = evaluate_missed_event(original_run(), later_items=[social_item()])
    assert observation is None

def test_learning_proposal_cannot_apply_policy():
    proposal = build_learning_proposal(outcomes())
    assert proposal.status == "owner_review"
    assert not hasattr(proposal, "apply")
    assert proposal.sample_size == len(outcomes())
```

- [ ] **Step 2: Run learning tests and confirm missing module**

Run: `.venv/bin/python -m pytest tests/test_intelligence_learning.py -q && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/outcomes_test.ts`

Expected: FAIL because missed-event and learning proposal contracts are absent.

- [ ] **Step 3: Implement deterministic observations only**

```python
@dataclass(frozen=True, slots=True)
class LearningObservation:
    kind: Literal["outcome", "missed_event", "source_failure", "noise"]
    original_run_id: UUID
    policy_version: int
    sample_size: int
    evidence_ids: tuple[UUID, ...]
    limitations: tuple[str, ...]
    proposed_change: Mapping[str, object] | None
    status: Literal["observation", "owner_review"]
```

Missed-event observations compare a prior run's declared source/window coverage with later official evidence; they do not assume the event was discoverable when outside that coverage. Record sample size, horizon, benchmark, policy version, false-positive/noise indicators, and limitations. Add a scoped `record_learning` gateway operation backed only by `record_market_learning`. The gateway inserts immutable observations only. It has no RPC or operation that activates policy, changes weights, adds providers, modifies holdings/plans, or changes delivery.

- [ ] **Step 4: Run learning, outcome, and security suites**

Run: `.venv/bin/python -m pytest tests/test_intelligence_learning.py tests/test_gateway.py tests/test_security_invariants.py -q && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/outcomes_test.ts supabase/functions/market-briefing-gateway/_shared/contracts_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts`

Expected: PASS for insufficient samples, out-of-coverage events, authoritative corroboration, version linkage, benchmark outcomes, owner-review proposals, and absence of self-tuning authority.

- [ ] **Step 5: Commit learning and mark V1-C5 complete locally**

Update `PROJECT_STATUS.md` with learning evidence and mark C5 Done locally, with production status still unclaimed.

```bash
git add lib/intelligence/learning.py tests/test_intelligence_learning.py lib/gateway.py tests/test_gateway.py sql/schema.sql supabase/functions/market-briefing-gateway/_shared/outcomes.ts supabase/functions/market-briefing-gateway/_shared/outcomes_test.ts supabase/functions/market-briefing-gateway/_shared/contracts.ts supabase/functions/market-briefing-gateway/_shared/contracts_test.ts supabase/functions/market-briefing-gateway/_shared/handler.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts supabase/functions/market-briefing-gateway/_shared/repository.ts packages/dashboard-contracts/src/index.ts supabase/functions/owner-dashboard-api/mappers.ts PROJECT_STATUS.md
git commit -m "feat: record review-only market learning"
```

---

### Task 14: V1-C6 Full Verification, Independent Review, and Protected Rollout

**Files:**
- Create: `docs/reviews/2026-09-04-personal-stock-agent-v1-review.md`
- Create: `docs/rollouts/2026-09-04-personal-stock-agent-v1.md`
- Create: `scripts/verify_personal_stock_agent_v1.py`
- Create: `tests/test_verify_personal_stock_agent_v1.py`
- Modify: `scripts/test_all.sh`
- Modify: `.github/workflows/owner-dashboard-ci.yml`
- Modify: `scripts/deploy_owner_dashboard_api.py`
- Modify: `scripts/verify_owner_dashboard_deployment.py`
- Modify: `docs/ROADMAP.md`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Consumes: exact candidate SHA, local/CI results, independent review verdict, migration/function/static versions, dry-run output, row-count deltas, provider/source receipts, owner/anonymous/non-owner canaries, scheduled run receipts, and rollback evidence.
- Produces: one fail-closed verification receipt and canonical C6 status; production claims are impossible without their exact receipt fields.

- [ ] **Step 1: Write the failing release verifier tests**

```python
def test_release_receipt_requires_every_gate():
    receipt = complete_release_receipt()
    assert verify_release(receipt)["status"] == "verified"

@pytest.mark.parametrize("missing", [
    "exact_head_ci", "independent_review", "quota_receipts", "dry_run_zero_writes",
    "dry_run_zero_sends", "migration_version", "gateway_version", "dashboard_api_version",
    "site_version", "owner_canary", "anonymous_denial", "non_owner_denial",
    "source_parity", "scheduled_receipt", "rollback_check",
])
def test_release_receipt_fails_when_gate_is_missing(missing):
    receipt = complete_release_receipt()
    receipt[missing] = None
    with pytest.raises(RuntimeError, match=missing):
        verify_release(receipt)
```

- [ ] **Step 2: Run verifier tests and confirm missing verifier**

Run: `.venv/bin/python -m pytest tests/test_verify_personal_stock_agent_v1.py -q`

Expected: FAIL because the V1 release verifier is absent.

- [ ] **Step 3: Implement the fail-closed release verifier and CI coverage**

```python
REQUIRED_GATES = (
    "exact_head_ci", "independent_review", "quota_receipts", "dry_run_zero_writes",
    "dry_run_zero_sends", "migration_version", "gateway_version", "dashboard_api_version",
    "site_version", "owner_canary", "anonymous_denial", "non_owner_denial",
    "source_parity", "scheduled_receipt", "rollback_check",
)

def verify_release(receipt: Mapping[str, object]) -> dict[str, object]:
    for gate in REQUIRED_GATES:
        if not receipt.get(gate):
            raise RuntimeError(f"missing release gate: {gate}")
    if receipt["dry_run_zero_writes"] is not True or receipt["dry_run_zero_sends"] is not True:
        raise RuntimeError("dry-run side-effect gate failed")
    return {"status": "verified", "candidate_sha": receipt["candidate_sha"]}
```

Add every new test directory and verifier to `scripts/test_all.sh` and exact-head CI. Extend protected deployment so it applies/verifies both new migrations, deploys only changed functions, builds immutable static assets from the reviewed SHA, and rolls back new secrets/functions/runtime login on a failed initial deployment. Never deploy the superseded thin dashboard.

- [ ] **Step 4: Run full local verification and obtain independent exact-head review**

Run: `npm run test:all`

Expected: exit 0 with Python, Node, Deno, dashboard contracts, web unit, typecheck, lint, license, production build, bundle scan, and Playwright suites all passing.

Push the exact candidate through the protected branch path and require the GitHub workflow SHA to equal local `git rev-parse HEAD`. Record counts and run URL. Obtain an adversarial independent review of that exact SHA covering: provider terms/budgets, prompt injection, quota races, hash/persistence integrity, exposure bypass, Analyst/Checker/policy bypass, personal-data leakage, dashboard privilege/CORS/auth, report duplication, Telegram semantics, rollback, and receipt claims. Resolve every Critical or Important finding, rerun the full suite, and repeat review on the new exact SHA.

- [ ] **Step 5: Run protected dry-run and deployment sequence**

1. Run fixture-backed `on-demand --dry-run`; verify zero table deltas, zero message IDs, bounded packet, expected provider simulation, and unchanged holdings/plans.
2. Apply and verify migrations through the protected project-bound administrator path.
3. Deploy the changed market gateway and dashboard API from the exact reviewed SHA; verify deployed source parity and health.
4. Create/save/deploy the private Site immutable version; reverify one-account allowlist and strict security headers.
5. Run GET-only owner, anonymous-denial, and non-owner-denial canaries across all five surfaces without triggering research or Telegram.
6. Reconcile persisted intelligence/report/publication rows and hashes with independent database reads.
7. Inspect the next existing scheduled pre-market/intraday/post-market/Friday chain; do not start a duplicate run.
8. Enable only the owner-approved alert class after a real shadow example and record Telegram acceptance separately from owner acknowledgement.

Expected: every step returns a narrow receipt; any missing/unknown result leaves production unclaimed and invokes the documented rollback where applicable.

- [ ] **Step 6: Final documentation, completion audit, and commit**

Populate `docs/reviews/2026-09-04-personal-stock-agent-v1-review.md` and `docs/rollouts/2026-09-04-personal-stock-agent-v1.md` with exact evidence, not template claims. Update `docs/ROADMAP.md` and `PROJECT_STATUS.md`: mark C6 and V1 complete only when every verifier field and scheduled receipt is present. Re-run `npm run test:all`, `git diff --check`, and `git status --short`; then commit the final evidence.

```bash
git add .github/workflows/owner-dashboard-ci.yml scripts/test_all.sh scripts/verify_personal_stock_agent_v1.py tests/test_verify_personal_stock_agent_v1.py scripts/deploy_owner_dashboard_api.py scripts/verify_owner_dashboard_deployment.py docs/reviews/2026-09-04-personal-stock-agent-v1-review.md docs/rollouts/2026-09-04-personal-stock-agent-v1.md docs/ROADMAP.md PROJECT_STATUS.md
git commit -m "docs: record personal stock agent v1 release receipts"
```

Expected final state: clean exact-head worktree; C1–C6 checked in `PROJECT_STATUS.md`; independent review has no unresolved Critical/Important finding; local and exact-head CI pass; private owner access and denial canaries pass; dry-run has zero writes/sends; scheduled receipts reconcile; the release remains zero-cost, suggestion-only, owner-only, friend-invitations-disabled, and brokerage-free.
