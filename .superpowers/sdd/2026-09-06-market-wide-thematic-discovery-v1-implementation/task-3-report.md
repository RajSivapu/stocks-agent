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
