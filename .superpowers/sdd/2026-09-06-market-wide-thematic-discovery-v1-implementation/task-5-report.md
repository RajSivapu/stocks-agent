# Task 5 report — Event-first discovery and deterministic entity resolution

Status: DONE

Base: `899b69d010caa95e839d6cfea668fec915ee2857`

## Implemented behavior

- Added ticker-independent `EventDraft` construction from normalized claims, polarity, effective and
  observed dates, publisher identity, upstream identity, and retained evidence. Ticker-free items
  survive into persisted market events, and contradictory positive and negative claims remain
  separate events with a ranking veto.
- Added deterministic entity resolution for explicit security IDs and dated tickers, CIK/entity IDs,
  exact canonical names, same-snapshot observed names, dated former names, and reviewed aliases.
  Resolution retains every eligible share class and explicitly returns ambiguous, private,
  foreign-only, and unresolved outcomes; it never chooses the first ticker from several matches.
- Added the checked-in version-1 taxonomy for magnets and critical minerals, aluminum/copper,
  data-center power, nuclear/uranium, and robotics. Every value-chain edge declares direction, role,
  geography, horizon, required evidence, adverse-path status, and an invalidation rule. Generated
  hypotheses remain unproved exposure research with `execution_allowed=false`.
- Added bounded reverse discovery for unresolved events through the existing keyless GDELT theme
  capability. Exact tasks are persisted before transport, include source-task dependencies and the
  typed hypothesis, rotate fairly across events and adverse paths, use the existing protected stage
  checkpoint/replay path, and contain no permanent issuer list.
- Added dynamic-theme evaluation after independent publisher and independent upstream
  corroboration. Eligible proposals are persisted as theme-episode revisions; ineligible proposals
  are retained in the signals task as unresolved research with exact missing reasons.
- Connected event detection, entity resolution, reverse discovery, dynamic themes, and reference
  hydration to the sole collection pipeline. A direct gateway `.call()` execution retains the
  hydrated reference snapshot and aliases when protected context is refreshed.

## Additive reference v2 contract

- Added `20261009_reference_issuer_names.sql`. Migrations `20261004` through `20261008` have no diff
  from the task base, and `sql/schema.sql` ends with the exact new migration text.
- Added semantic encoding version 2 for security revisions with a canonical issuer-name object:
  canonical name, same-snapshot observed names, and dated former names. Every security for one
  entity must carry the identical object. Version-1 rows, hashes, manifests, and reads remain valid;
  their binding explicitly reports `issuer_names_unavailable` and supports only explicit ID/ticker
  resolution.
- Reused the existing begin/chunk/finalize/pin/read protocol and memberships. Python, TypeScript,
  PostgreSQL, recovery export/verification/restore, and protected evidence enforce the same v2 hash
  and schema. The shared v2 revision vector is
  `6ed1594329bb40358f01357932fbae01bf5d241336d6f2989634a6f2ba630759`.
- Enforced at most 88 v2 rows per byte-aware chunk and the reviewed aggregate ceiling of 384 unique
  calls, 48 MiB request bytes, 64 MiB response bytes, and 90 seconds while retaining the existing
  per-call and 15,000-security bounds. A fresh refresh installs its validated in-memory snapshot;
  restart hydration pages the immutable current pin without another SEC request.

## TDD evidence

The implementation began with failing event/entity imports and pipeline assertions. Subsequent RED
tests demonstrated that ticker-free evidence was dropped, multiple resolutions collapsed, reverse
tasks and dynamic-theme checkpoints were absent, taxonomy theme terms were omitted from reverse
queries, opposite claim polarities merged, and direct `.call()` context refresh discarded the
hydrated reference. Each failure was followed by a focused GREEN.

The v2 protocol also began RED across Python hashes, SQL schema/functions, TypeScript validation,
collector hydration, recovery, and capacity. Later adversarial RED cases found missing observed and
dated-former name behavior, an invalid stable-ID fallback, missing issuer-name availability binding,
and a stale Deno v1 fixture. The final implementation and fixture corrections passed the affected
slices. A real PostgreSQL predecessor/read plus 15,000-row v2 upload/finalize/pin/restart test passed
in 62.82 seconds without a live provider call.

## Final verification

```text
.venv/bin/python -m pytest -q
1191 passed, 3 skipped, 4 deselected in 234.72s

.venv/bin/python -m pytest \
  tests/test_intelligence_discovery.py tests/test_intelligence_entities.py \
  tests/test_intelligence_themes.py tests/test_intelligence_pipeline.py \
  tests/test_reference_issuer_names.py tests/test_collect_market_intelligence.py \
  tests/test_recovery_bundle.py tests/test_managed_isolated_restore.py \
  tests/test_verify_market_intelligence_migration.py -q
312 passed in 13.92s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/_shared/repository_test.ts
16 passed, 0 failed

npm run test:all
exit 0: Python 1191 passed/3 skipped/4 deselected; Node 71 passed;
Deno 311 passed; package tests 6 + 52 passed; typechecks, ESLint, dependency
licenses, production build, and bundle verification passed; Playwright 21 passed/1 skipped
```

`git diff --check` exited 0. The new migration parsed as 43 PostgreSQL statements, schema parity
passed, and the protected migration integration tests exercised the v2 functions and 15,000-row
capacity path.

## Self-review

- Confirmed ticker-free event persistence and full multi-security iteration in the production
  pipeline; no sorted-first security fallback remains.
- Confirmed private and unresolved mentions cannot map to public peers, ordinary words are never
  inferred as tickers, name collisions stay ambiguous, and dates control ticker/former-name use.
- Confirmed taxonomy roles and reverse search hypotheses never become exposure proof, action
  authority, or portfolio mutation.
- Confirmed reverse tasks consume only the pre-reserved adaptive GDELT budget, remain within the
  100-stage-task ceiling, persist before transport, and replay without a second transport call.
- Confirmed dynamic corroboration counts publisher and upstream identities independently and stores
  ineligible labels with reasons rather than promoting them.
- Confirmed the reference v2 client/server/recovery hash and byte bounds agree, v1 semantics remain
  explicit, and migrations `20261004` through `20261008` are unchanged.
- All tests used fixtures and local PostgreSQL containers/test doubles. No live source, collector,
  Telegram, schedule, deployment, or production mutation was invoked.

## Concerns

- Dynamic labels are bounded source metadata proposals. They remain unresolved unless two distinct
  publisher identities and two distinct upstream identities corroborate them; the taxonomy stays
  the reviewed source of durable theme definitions.
- Legacy v1 reference snapshots intentionally cannot resolve issuer prose names. They retain exact
  stable-ID and dated-ticker resolution and report `issuer_names_unavailable` until a v2 refresh is
  finalized and pinned.
- Reverse discovery can schedule fewer tasks than the phase reserve when the 100-task protected
  ledger ceiling leaves less room. Coverage is reported through the existing bounded source/task
  receipts rather than implying completeness.
