# Task 3 report: dated U.S.-listed security reference

## Status

Complete on `codex/market-wide-discovery-v1` from base `e0411ae103ea0577f8cd3de081f30c8859a9b60b`.

The controller expanded Task 3 after review showed that the existing single-payload Task 2 RPC could not persist or recover a roughly 15,000-security initial snapshot without raising global transport limits. The approved correction is the new additive `sql/migrations/20261006_reference_snapshot_transfer.sql`. The immutable `20261004` reconciliation and reviewed `20261005_market_wide_discovery.sql` are byte-for-byte unchanged.

## Implemented behavior

- Added frozen issuer, security, eligibility, conflict, manifest, snapshot, refresh, and transfer types. Consumers use `entity_id` and `security_id`; tickers remain dated aliases with effective dates and former aliases. Exact one-to-one predecessor identity can preserve a rename, disappeared predecessor listings receive an explicit closed `delisted` revision, and ambiguous classes remain excluded rather than being inferred by company name.
- Added strict parsers for the exact SEC company-ticker bytes and offline Nasdaq-format fixtures. The SEC parser retains leading-zero CIKs, types clear instruments, hashes exact response bytes, retains the validated HTTP response timestamp and parser version, rejects malformed/duplicate identities, and labels coverage `scope_not_guaranteed`.
- Kept Nasdaq symbol-directory transport disabled as `disabled_pending_https_and_terms_review`; its parsers exist only for fixtures and future capability review.
- Added one bounded SEC refresh through `BoundedHttpClient` with the descriptive `stocks-agent security-reference/1.0 (rupesh.sivapu@gmail.com)` user agent, a 10-second request timeout, a 5 MB response cap, and server-backed last-healthy fallback. A failed refresh pins the prior finalized snapshot as `reference_stale` with its real age, or records `reference_unavailable` when no healthy snapshot exists.
- Added deterministic transfer construction for up to 15,000 members. Chunks contain at most 200 entries and 192 KiB; a full 15,000-member fixture produces 75 chunks and 78 total begin/chunk/finalize/pin calls within the 80-call and 16 MiB aggregate ceilings. Manifest, chunk, and root hashes bind canonical content; the manifest source hash binds the exact SEC bytes.
- Added four protected ledgers: immutable chunk receipts, complete snapshot memberships, finalization seals, and run bindings. Partial uploads remain invisible. Finalization verifies contiguous chunks, aggregate count, root hash, unique security/revision identities, and predecessor membership before publishing a manifest. Unchanged revisions are reused only through the validated predecessor membership.
- Added five service-only gateway operations: `begin_discovery_reference`, `record_discovery_reference_chunk`, `finalize_discovery_reference`, `pin_discovery_reference`, and `read_discovery_reference`. Exact retries return the existing receipt, altered retries fail closed, stale pin replay remains bound to the original snapshot even after a newer finalization, and paged reads return only the run's pinned finalized snapshot.
- Kept every transfer request and page under 192 KiB, the existing gateway request/response limits at 262 KiB/1 MiB, a requested page limit of at most 500, and byte-aware page truncation. TypeScript validates inconsistent bindings, pagination, duplicate identities, and all request shapes before returning data.
- Integrated the reference stage exactly once in the existing collector invocation, after durable run start and before provider collection. The resulting packet coverage records `coverage_status`, `reference_status`, `reference_manifest_id`, `reference_age_seconds`, and `execution_allowed=false`.
- Added all four ledgers to protected reads, encrypted recovery export, exact field/lineage validation, relationship roots, dependency-ordered restore, predecessor-first seal restore, managed snapshot checks, and exact source/restore comparison.
- Preserved the zero-cost, owner-only, suggestion-only boundary. No brokerage or execution authority was added. This task did not invoke the collector, call a live provider, send Telegram traffic, alter a schedule, deploy, or mutate production.

## TDD evidence

### Universe and bounded refresh

Initial RED:

```text
.venv/bin/python -m pytest -q tests/test_intelligence_universe.py
ERROR collecting tests/test_intelligence_universe.py
ModuleNotFoundError: No module named 'lib.intelligence.universe'
```

Review REDs subsequently proved that a same-issuer duplicate identity was accepted, a disappeared prior listing was omitted, and the SEC response timestamp was discarded:

```text
3 failed in 0.09s
```

GREEN after implementing those cases and the full-capacity fixture:

```text
.venv/bin/python -m pytest -q tests/test_intelligence_universe.py
21 passed in 5.27s
```

### Snapshot-transfer protocol

Initial RED failed because `20261006_reference_snapshot_transfer.sql` and the five operations did not exist. The first PostgreSQL GREEN reached eight functional/static tests, then grew as review added stale-unavailable behavior and concurrency/idempotency cases.

Two review REDs exposed late exact chunk retry rejection and the wrong manifest identity on a reused-revision page:

```text
2 failed in 1.43s
```

A further stale-pin replay RED showed that a newer concurrent finalization could change resolution of the same idempotent request:

```text
1 failed in 1.01s
```

All now pass. The exact chunk replay is accepted after finalization, membership pages project the pinned manifest, and a stale binding remains immutable across later finalizations.

Final self-review added a real two-connection PostgreSQL race. Both exact finalize callers were held after their optimistic seal lookup; after release, the second caller originally raised `reference finalization conflict` instead of returning the committed seal:

```text
1 failed in 1.17s
```

The finalizer now rechecks the seal while holding the begin-receipt lock. The focused race test passed, proving one new finalization receipt and one exact duplicate receipt.

The same review found that a stale request which resolved to `reference_unavailable` could accept an altered retry whose requested status was already `reference_unavailable`:

```text
1 failed in 1.10s
```

Run bindings now retain the exact normalized request payload, recovery validates and preserves it, and any altered retry fails closed. Real concurrent begin, chunk, and pin tests then reproduced unique-key failures (`1 failed in 1.16s`, `1 failed in 1.06s`, and `1 failed in 1.14s`). Begin and pin now serialize on the running-run rows, while chunk and finalize serialize on the begin receipt. The completed PostgreSQL protocol suite is green:

```text
.venv/bin/python -m pytest -q tests/test_reference_snapshot_transfer_sql.py
14 passed in 2.72s
```

### Gateway and collector

TypeScript RED initially failed compilation on the missing operation parsers, contracts, handler routes, and repository methods. Python collector REDs showed the missing one-stage integration and missing envelope/aggregate accounting. The implemented path uses deterministic request IDs and validates every gateway receipt.

The first repository-wide Python run found three integration regressions in a timestamp test double, the legacy/fresh schema equivalence suffix, and the fixed RPC allowlist:

```text
3 failed, 1032 passed, 3 skipped, 4 deselected in 60.97s
```

The focused rerun after correction passed all three. A TypeScript persisted-page RED then rejected no inconsistent binding; after adding fail-closed binding, pagination, and duplicate checks it passed 12 tests.

A final collector RED proved that the aggregate timer started after the SEC request and therefore excluded fetch time:

```text
1 failed in 0.08s
```

The timer now starts before refresh; the complete collector file is green at 10 tests.

### Recovery and restore

Recovery RED after adding the four required datasets:

```text
77 failed, 64 passed
```

This exposed every missing allowlist, query, validation, relationship, restore, managed snapshot, and exact-comparison path. The completed recovery suite is green:

```text
.venv/bin/python -m pytest -q tests/test_recovery_bundle.py tests/test_managed_isolated_restore.py
142 passed in 8.57s
```

## Final verification

```text
.venv/bin/python -m pytest -q
1041 passed, 3 skipped, 4 deselected in 63.62s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/_shared \
  supabase/functions/owner-dashboard-api
ok | 307 passed | 0 failed

npx --yes deno@2.9.6 check --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/index.ts \
  supabase/functions/owner-dashboard-api/index.ts
Check .../market-briefing-gateway/index.ts
Check .../owner-dashboard-api/index.ts

npx --yes deno@2.9.6 fmt --check <8 changed TypeScript files>
Checked 8 files

.venv/bin/python -m compileall -q lib scripts tests
.venv/bin/python -m json.tool config/intelligence_sources.json
pglast.parse_sql(20261006 migration and consolidated schema)
git diff --check
all passed

git diff --exit-code e0411ae103ea0577f8cd3de081f30c8859a9b60b -- \
  sql/reconciliation/20261004_production_schema_reconciliation.sql \
  sql/migrations/20261005_market_wide_discovery.sql
exit 0
```

The skipped/deselected tests are existing environment-dependent or explicitly deselected tests. Disposable PostgreSQL transfer, reconciliation, migration-verifier, recovery, and isolated-restore tests ran on this host.

## Self-review

- Confirmed every Python operation name matches the TypeScript contract, handler, repository method, SQL RPC, and test allowlist.
- Confirmed a full initial snapshot fits 75 chunks and 78 calls without changing the global gateway limits.
- Confirmed exact manifest bytes, canonical transfer hashes, contiguous chunk roots, membership counts, and unique security identities are independently checked at their boundaries.
- Confirmed concurrent exact finalization retries serialize on the begin receipt and return the committed seal after an unknown outcome.
- Confirmed concurrent exact begin, chunk, and pin retries return their existing receipt or binding; altered pin payloads are rejected even when stale fallback changes the resolved status.
- Confirmed partial uploads have no manifest, seal, binding, or readable page; only atomic finalization makes a snapshot selectable.
- Confirmed unchanged cross-run entries reuse prior revisions through a finalized predecessor membership while current pages retain the pinned manifest identity.
- Confirmed stale fallback selects finalized snapshots only, records source timestamp and real age, stays stable after later finalization, and fails closed to `reference_unavailable` without a predecessor.
- Confirmed the owner discovery reader is unchanged and cannot call the new service-only reference operations.
- Confirmed recovery includes all four new ledgers with exact fields, root/count/lineage checks, dependency order, and predecessor-first restoration.
- Confirmed `execution_allowed=false` is present in packet coverage and absent as an accepted caller authority field.
- Confirmed no network, collector, Telegram, schedule, deployment, credential, brokerage, or production mutation occurred.

## Concerns

- The enabled SEC company-ticker file does not provide a stable listing identifier or exchange and does not guarantee complete U.S.-listing scope. The implementation retains `scope_not_guaranteed`, avoids name-based inference, and supports safe predecessor-assisted ticker continuity, but an SEC-only ambiguous class or rename remains unresolved unless a trusted predecessor makes the mapping one-to-one.
- Nasdaq symbol-directory ingestion remains deliberately disabled until its HTTPS path, capability approval, and usage terms are reviewed.

## Fix round 1 from `f1543ba`

### Status and reviewed interface correction

Complete in the Task 3 working tree. This round addresses the Task 3 review findings without changing the ordinary decision-operation quotas, the owner discovery reader, or the immutable `20261004` and `20261005` migrations.

The controller approved a bounded correction to the transfer interface. `pin_discovery_reference` and `read_discovery_reference` now require `binding_role=predecessor|current`. A predecessor is pinned immutably in `market_reference_predecessor_pins`; the refreshed or stale current reference remains in `market_reference_run_bindings`. Every one of the five service-only transfer operations records an exact request claim in `market_reference_transfer_requests`. Both new ledgers join chunk receipts, memberships, seals, and current bindings in protected export, dependency-ordered restore, managed snapshot checks, relationship roots, and exact source/restore comparison.

The independent transfer bounds are now 160 unique calls, 32 MiB of exact encoded request traffic, and 45 seconds. The global 262 KiB request and 1 MiB response limits remain unchanged. Chunks and returned pages remain capped at 192 KiB, chunks at 200 entries, requested pages at 500 rows with byte-aware truncation, and snapshots at 15,000 members and 512 chunks. Exact retries after an unknown outcome reuse the immutable claim and operation receipt; altered replays fail closed. The collector caps each gateway timeout by the remaining 45-second deadline and includes SEC refresh time in its local deadline.

### Canonical hashes at all write boundaries

- Defined semantic encoding version 1 for manifests and security revisions in Python, TypeScript, and PostgreSQL. All three build the same typed semantic document, sort object keys by UTF-8 bytes, normalize timezone-bearing timestamps to UTC milliseconds, encode UTF-8 JSON without insignificant whitespace, and hash those exact bytes with SHA-256.
- Manifest hashes authenticate the reference identity, versions, exact SEC source hash, effective dates, and manifest facts. Security hashes authenticate the stable security and issuer identities, dated ticker alias, instrument and eligibility state, provenance, and effective dates. Transfer-only revision and manifest membership identifiers are excluded from the security semantic document so a byte-identical predecessor revision can be reused safely.
- The TypeScript gateway recomputes semantic hashes before repository calls and accepts transfer requests only when the raw body is the exact canonical envelope whose byte count and hash are sent to PostgreSQL.
- `begin_market_discovery_reference` and `record_market_discovery_reference_chunk` recompute hashes on first receipt; finalization recomputes the manifest and every revision again immediately before ledger inserts. The new migration also redefines the legacy service-only `record_market_discovery_reference(UUID,JSONB)` function so its first writes and replays receive the same protection while leaving `20261005` byte-identical.

### Predecessor identity and stale recovery

- The collector pins the latest finalized predecessor for the exact run and capability, reads only that immutable pin through bounded pages, validates every binding/cursor/page, and merges it before building transfer revision identities.
- A single active predecessor listing and single current listing for the same CIK preserve the prior `security_id`, original effective date, and former ticker aliases across an `OLD` to `NEW` rename. Multiple plausible predecessor or current listings are not guessed; their provisional identities remain unresolved.
- A healthy refresh finalizes and pins the new snapshot as `current`. A failed SEC refresh pins the already selected predecessor with its actual source time and age as `reference_stale`; without one, the current binding is `reference_unavailable`. Partial uploads never participate in latest/head selection or reads.
- Preferred or preference depositary-share names are classified as excluded `PREFERRED` instruments before the generic ADR rule.

### TDD evidence

The review tests were written and observed RED before production changes:

```text
preferred depositary-share classification: 1 failed (returned ADR)
TypeScript forged semantic manifest: 1 failed (accepted stale hash)
PostgreSQL forged first manifest/revision writes: 2 failed (accepted stale hashes)
collector predecessor continuity: 1 failed (begin occurred before a predecessor pin/read)
transfer accounting/deadline contract: 4 failed (80 calls, 16 MiB, integer timeout, late timer)
```

The real capacity test initially had no handler-to-PostgreSQL bridge. Its committed harness now sends Python-built canonical envelopes through the actual Deno handler and Supabase repository, forwards only the five fixed RPCs as `service_role` into disposable PostgreSQL, reads every returned page through the same path, and checks all 15,000 membership frames against the Python root and expected membership hash.

Subsequent self-review added two more RED cases. The reviewed `20261005` legacy RPC accepted a forged first manifest until `20261006` redefined it with semantic validation:

```text
FAILED test_legacy_reference_rpc_recomputes_semantic_hashes_before_first_write
Failed: DID NOT RAISE InvalidParameterValue
```

Python's exported semantic helper also retained a syntactically equivalent `...00Z` timestamp while TypeScript and PostgreSQL normalized it to `...00.000Z`:

```text
FAILED test_reference_semantic_encoding_normalizes_timestamps_to_milliseconds
AssertionError: '2026-09-06T12:00:00Z' != '2026-09-06T12:00:00.000Z'
```

Both are GREEN. The older discovery PostgreSQL fixture was updated to install its real `extensions.pgcrypto` dependency and generate canonical fixture hashes, keeping legacy discovery regression behavior covered under the hardened function.

Representative GREEN evidence:

```text
actual 15,000-member envelope -> handler/repository -> PostgreSQL bridge
1 passed in 23.57s

recovery and managed-restore surface
213 passed

focused Task 3 surface, excluding only a duplicate capacity rerun
321 passed in 45.08s

full Python regression after final self-review change
1054 passed, 3 skipped, 4 deselected in 86.14s

full applicable Deno regression
ok | 308 passed | 0 failed
```

The capacity proof persisted exactly 15,000 memberships, read all pages, matched the finalization root and membership hash, and stayed within 160 calls, 32 MiB aggregate encoded traffic, 262 KiB per request, and 192 KiB per returned page. The PostgreSQL protocol tests also cover partial invisibility, predecessor/current pin separation, exact-retry idempotency, altered-retry rejection, concurrent begin/chunk/finalize/pin retries, finalized-only selection, service-only execution, and the 160th/161st call boundary.

### Final verification and self-review

```text
.venv/bin/python -m pytest -q
1054 passed, 3 skipped, 4 deselected in 86.14s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/_shared \
  supabase/functions/owner-dashboard-api
ok | 308 passed | 0 failed

npx --yes deno@2.9.6 check --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/index.ts \
  supabase/functions/owner-dashboard-api/index.ts
passed

npx --yes deno@2.9.6 fmt --check <8 changed TypeScript files>
Checked 8 files

.venv/bin/python -m compileall -q lib scripts tests
pglast.parse_sql(20261006 migration and consolidated schema)
git diff --check
all passed

git diff --exit-code f1543ba -- \
  sql/reconciliation/20261004_production_schema_reconciliation.sql \
  sql/migrations/20261005_market_wide_discovery.sql
exit 0
```

Self-review of `f1543ba..HEAD` confirmed that Python request bytes equal the bytes emitted by `lib.gateway.call`; the gateway supplies the same byte count and SHA-256 to the fixed repository RPC; PostgreSQL reconstructs and verifies the canonical envelope before creating a claim. It confirmed that exact retries do not double-count calls or bytes, all new accepted requests are serialized per running run, and the five reference routes remain outside the ordinary `market_request_claims` quotas. It also confirmed that the predecessor pin is immutable across concurrent newer finalizations, reads cannot switch snapshots mid-page, and current binding remains available for Task 6 without changing Task 6 business lineage yet.

The safety posture remains owner-only, suggestion-only, receipt-supported, and zero incremental cost. `execution_allowed=false` remains mandatory. No live SEC request, production RPC, scheduled collector, Telegram publication, brokerage interface, deployment, or production mutation was performed.

### Remaining concerns

- The SEC company-ticker file still does not guarantee complete U.S.-listing scope or provide a stable listing identifier/exchange. `scope_not_guaranteed` remains explicit, and ambiguous same-CIK class changes remain unresolved rather than guessed.
- Nasdaq symbol-directory ingestion remains disabled pending a reviewed HTTPS capability and terms decision.

## Fix round 2 from `aed2c9e2`

This round fixes stale-reference lineage so a run cannot switch from the predecessor it selected before refresh to a newer snapshot that finalizes while the run is in progress. The five-operation transfer interface, its independent 160-call/32-MiB/45-second budget, ordinary decision quotas, and owner discovery reader are unchanged.

### RED evidence

The regressions were added before the production fixes and observed failing against `aed2c9e2`:

```text
.venv/bin/python -m pytest -q tests/test_reference_snapshot_transfer_sql.py \
  -k 'current_stale_pin_uses or current_stale_pin_rejects or unavailable_predecessor_cannot'
3 failed, 20 deselected in 1.31s

null current/reference_stale selected newer finalized B instead of pinned A
explicit caller-selected finalized B was accepted
an unavailable predecessor pin was upgraded to a later finalized snapshot

.venv/bin/python -m pytest -q tests/test_recovery_bundle.py \
  -k '<three inconsistent stale-current/predecessor lineage tests>'
3 failed, 104 deselected in 1.11s

recovery validation accepted inconsistent lineage
restore reached the database mutation boundary
exact comparison reached record comparison instead of rejecting lineage first

npx --yes deno@2.9.6 test --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/_shared/handler_test.ts \
  --filter 'dry-run reference read reports the requested binding role'
0 passed, 1 failed, 54 filtered out
expected predecessor; received undefined
```

### Lineage correction

- A `current/reference_stale` pin now resolves only the immutable predecessor pin for the same run and capability. A null manifest selects that exact predecessor; an explicit manifest is accepted only when it equals the predecessor. A different finalized manifest fails closed.
- If the predecessor pin recorded `reference_unavailable`, a later finalization cannot upgrade it. The current binding is recorded as `reference_unavailable`. A caller that directly requests `reference_unavailable` must also match an unavailable predecessor pin.
- Recovery validation now requires every stale or unavailable current binding to match the same run/capability predecessor manifest and status. Export validation, pre-mutation restore validation, and exact recovery comparison therefore reject inconsistent lineage.
- Dry-run `read_discovery_reference` responses now include the requested `binding_role`, matching the live response contract.
- The reviewed `20261004` reconciliation and `20261005` market-wide discovery migrations remain byte-identical. The correction is confined to `20261006`, the consolidated schema, recovery validation, the gateway response, and their tests.

### GREEN evidence

```text
focused real PostgreSQL stale-lineage cases
4 passed, 19 deselected in 1.18s

focused recovery validation, restore, and exact-comparison cases
3 passed, 104 deselected in 0.40s

complete recovery and managed-restore surface
147 passed in 8.72s

focused Deno gateway handler/contracts/parser surface
90 passed, 0 failed

.venv/bin/python -m pytest -q
1060 passed, 3 skipped, 4 deselected in 86.66s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/_shared \
  supabase/functions/owner-dashboard-api
309 passed, 0 failed

.venv/bin/python -m compileall -q lib scripts tests
npx --yes deno@2.9.6 check --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/index.ts \
  supabase/functions/owner-dashboard-api/index.ts
npx --yes deno@2.9.6 fmt --check \
  supabase/functions/market-briefing-gateway/_shared/handler.ts \
  supabase/functions/market-briefing-gateway/_shared/handler_test.ts
pglast.parse_sql(20261006 migration and consolidated schema)
schema tail equals 20261006 migration
git diff --check
git diff --exit-code aed2c9e2 -- 20261004 reconciliation 20261005 migration
all passed
```

Self-review of `aed2c9e2..HEAD` confirmed that the stale-current insert derives its manifest and status from the immutable predecessor row after the run lock is acquired, no latest-head query remains in the current branch, and exact retries retain the original request and binding. Recovery checks use the run/capability composite identity before any restore mutation or exact comparison. No network request, production RPC, collector invocation, Telegram action, schedule mutation, deployment, or production database mutation was performed.
