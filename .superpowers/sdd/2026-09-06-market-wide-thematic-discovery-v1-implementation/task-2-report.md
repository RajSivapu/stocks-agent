# Task 2 report: durable discovery references and stage ledgers

## Status

Complete on `codex/market-wide-discovery-v1` from base `cffb47d032bead96924d948932c6986ea5e15efe`.

The implementation is additive. The production reconciliation migration and receipt at version `20261004` remain byte-for-byte unchanged. Per the controller's plan-defect ruling, the new migration is `sql/migrations/20261005_market_wide_discovery.sql`; every Task 2 migration, verifier, recovery, restore, fixture, and test reference uses `20261005`. Existing collision rejection for `20261004` remains covered.

## Implemented behavior

- Added six durable ledgers: reference manifests, security reference revisions, discovery stage tasks, theme episode revisions, exposure facts, and research nominations.
- Preserved the Task 1 planning identity in persisted tasks through `capability_id`, approved aggregate `provider`, `query_kind`, `query_hash`, dependency IDs, and the requested window.
- Made manifests, security revisions, theme episodes, and exposure facts append-only. Task transitions are limited to `planned -> attempting -> succeeded|failed|deferred|uncertain`; identity changes and deletion fail closed. Research nominations have a constrained research-only lifecycle and immutable identity.
- Added exact, bounded, security-definer RPCs for recording references, checkpointing stages, and reading run-scoped context. Exact replays return durable duplicate receipts; altered or incomplete reference and stage-child replays fail closed.
- Added exact TypeScript parsers for UUIDs, hashes, approved providers, bounded windows/arrays/objects, unique dependencies, stage/result compatibility, and persisted context shape. Nested research content rejects price, valuation, portfolio overlap, action, authority, execution, brokerage, and order fields.
- Added gateway allowlist operations `record_discovery_reference`, `checkpoint_discovery_stage`, and `read_discovery_context`. Writes require the existing collection secret. Reads require the configured owner JWT. Owner JWT access remains confined to `read_discovery_context`; it cannot authorize existing service operations.
- Wired production owner verification through the existing owner-dashboard JWT verifier and a pinned `jose` dependency.
- Added all six datasets to protected read evidence, encrypted recovery export, exact field and lineage validation, restore registries, foreign-key restore order, isolated snapshot comparison, and managed restore dataset checks.
- Kept the system at zero incremental provider cost, owner-only and suggestion-only. This task did not add execution authority, a live collector call, a production run, a deployment, a schedule, Telegram traffic, credentials, or network mutation. Alert V3 remains disabled.

## Files

Created:

- `sql/migrations/20261005_market_wide_discovery.sql`
- `tests/test_market_wide_discovery_sql.py`
- `.superpowers/sdd/2026-09-06-market-wide-thematic-discovery-v1-implementation/task-2-report.md`

Modified:

- `sql/schema.sql`
- `scripts/verify_market_intelligence_migration.py`
- `scripts/protected_evidence.py`
- `scripts/export_recovery_bundle.py`
- `scripts/verify_recovery_bundle.py`
- `scripts/managed_isolated_restore.py`
- `lib/gateway.py`
- `supabase/functions/market-briefing-gateway/_shared/contracts.ts`
- `supabase/functions/market-briefing-gateway/_shared/handler.ts`
- `supabase/functions/market-briefing-gateway/_shared/handler_test.ts`
- `supabase/functions/market-briefing-gateway/_shared/intelligence.ts`
- `supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts`
- `supabase/functions/market-briefing-gateway/_shared/repository.ts`
- `supabase/functions/market-briefing-gateway/_shared/repository_test.ts`
- `supabase/functions/market-briefing-gateway/index.ts`
- `tests/test_gateway.py`
- `tests/test_managed_isolated_restore.py`
- `tests/test_production_schema_reconciliation_sql.py`
- `tests/test_recovery_bundle.py`
- `tests/test_security_invariants.py`
- `tests/test_verify_market_intelligence_migration.py`

No prior migration or `scripts/deploy_owner_dashboard_api.py` bytes changed.

## TDD evidence

### Schema and migration catalog

RED command:

```text
.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py tests/test_verify_market_intelligence_migration.py -q
```

RED output:

```text
5 failed, 68 passed in 0.56s
```

The failures identified the missing migration and missing `discovery_ledgers` verification receipt.

Initial GREEN output after adding the migration and verifier catalog:

```text
73 passed in 0.76s
```

A disposable PostgreSQL replay test then exposed that an altered security child was accepted as a reference replay. After field-by-field replay comparison, the focused test passed. Context-shape tests subsequently exposed and fixed extra task timestamps and the omission of security revisions from bounded context.

Self-review added a terminal-stage child replay regression test.

RED command:

```text
.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py::test_discovery_stage_replay_is_exact_and_altered_child_fails_closed -q
```

RED output:

```text
FAILED tests/test_market_wide_discovery_sql.py::test_discovery_stage_replay_is_exact_and_altered_child_fails_closed
E Failed: DID NOT RAISE InvalidParameterValue
1 failed in 1.09s
```

GREEN command:

```text
.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py::test_discovery_stage_replay_is_exact_and_altered_child_fails_closed tests/test_market_wide_discovery_sql.py::test_schema_appends_the_new_immutable_migration_verbatim -q
```

GREEN output:

```text
2 passed in 1.00s
```

### TypeScript parsers and repository

RED command:

```text
npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts
```

RED output:

```text
TypeScript compilation failed with 6 missing parser/repository symbols.
```

After the first implementation, an additional parser RED showed duplicate dependencies and an unsupported exposure kind were accepted:

```text
4 passed, 2 failed
```

GREEN output after exact parsers and repository methods:

```text
ok | 20 passed | 0 failed (182ms)
```

### Gateway allowlist and authorization

RED command:

```text
npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/handler_test.ts
```

RED output after the initial compile fix:

```text
49 passed, 3 failed
```

The failures were anonymous/non-owner status handling, wrong-stage validation, and missing replay receipt behavior. A later focused RED proved authenticated non-owner rejection returned `401` instead of `403` before that path was corrected.

Self-review found that a valid owner JWT could authorize legacy service operations.

RED command:

```text
npx --yes deno@2.9.6 test --config supabase/functions/deno.json --filter "discovery reads require" supabase/functions/market-briefing-gateway/_shared/handler_test.ts
```

RED output:

```text
discovery reads require the owner while writes require the collection secret ... FAILED
error: Error: expected 403, got 200
FAILED | 0 passed | 1 failed | 52 filtered out (44ms)
```

GREEN output after restricting owner JWT authorization to the discovery read:

```text
discovery reads require the owner while writes require the collection secret ... ok
ok | 1 passed | 0 failed | 52 filtered out (53ms)
```

Final handler GREEN:

```text
ok | 53 passed | 0 failed (236ms)
```

### Recovery and isolated restore

RED command:

```text
.venv/bin/python -m pytest tests/test_recovery_bundle.py tests/test_managed_isolated_restore.py -q
```

RED output:

```text
73 failed, 60 passed in 5.33s
```

The failures showed all six datasets were absent from the concrete recovery registries and managed restore expectations. The first integrated run also exposed the planned `20261004` version collision; the controller ruled this a plan defect and required the additive `20261005` migration while preserving collision rejection.

Targeted discovery recovery GREEN:

```text
12 passed, 81 deselected in 0.15s
```

Final recovery GREEN:

```text
133 passed in 9.73s
```

## Final verification

Required Task 2 commands, rerun after self-review fixes:

```text
.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py tests/test_verify_market_intelligence_migration.py -q
76 passed in 1.73s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts
ok | 20 passed | 0 failed (182ms)

.venv/bin/python -m pytest tests/test_recovery_bundle.py tests/test_managed_isolated_restore.py -q
133 passed in 9.73s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/handler_test.ts
ok | 53 passed | 0 failed (236ms)
```

Repository-wide verification after the last change:

```text
.venv/bin/python -m pytest -q
976 passed, 3 skipped, 4 deselected in 61.22s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared
ok | 240 passed | 0 failed (961ms)

npx --yes deno@2.9.6 fmt --check <8 changed TypeScript files>
Checked 8 files

npx --yes deno@2.9.6 check --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/index.ts
Check supabase/functions/market-briefing-gateway/index.ts

.venv/bin/python -m py_compile <changed Python files>
git diff --check
exit 0
```

The skipped tests are environment-dependent existing tests; the disposable PostgreSQL tests in `test_market_wide_discovery_sql.py` executed and passed on this host.

## Self-review

- Confirmed the migration is appended verbatim to `sql/schema.sql` and no previous migration has a diff.
- Confirmed all gateway operation names match across Python, TypeScript contracts, handler routing, repository RPC calls, and tests.
- Confirmed service credentials remain required for every pre-existing operation and both new writes; only the new bounded discovery context read accepts owner JWT authorization.
- Confirmed the SQL RPC response preserves the exact public `DiscoveryStageTask` shape without database timestamps or `run_id`.
- Confirmed reference and successful-stage replays compare exact persisted child rows and reject altered or omitted children.
- Confirmed all recovery rows use exact field allowlists, canonical sorting, unique identities, same-run relationships, approved providers, bounded research JSON, and foreign-key restore order.
- Confirmed dashboard grants omit task results and dependency/query-window internals while the release reader retains exact recovery evidence.
- Confirmed no deployment, production database, provider, Telegram, credential, brokerage, or schedule mutation occurred.

## Concerns

No material implementation concern remains. Live deployment and protected production restore were intentionally not performed under Task 2's no-production/no-network-mutation boundary; verification used static parsers, exact registries, and a disposable local PostgreSQL instance.

## Review fix round 1

The first review identified two critical lineage/idempotency gaps and four important validation and verification gaps. I added executable regression cases before changing production code.

### RED evidence

Incomplete replay, cross-run/orphan lineage, terminal-run writes, and actual PostgreSQL grant collection:

```text
.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py -q
7 failed, 8 passed in 1.39s
```

The failures demonstrated that an incomplete reference replay was accepted, a valid cross-run security reference was accepted, completed/failed parent runs could create or advance checkpoints, and discovery ACLs were absent from the collected catalog snapshot. The same suite also added valid cross-run theme/exposure-fact IDs and orphan exposure-fact IDs so every protected-write lineage variant executes against PostgreSQL.

Recursive authority-key parsing:

```text
npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts
FAILED | 6 passed | 1 failed (20ms)
```

Nested `order`, `order_details`, `portfolioOverlap`, `executionAllowed`, and `ORDER-ID` keys were not all rejected after canonicalization.

Recovery parser parity:

```text
.venv/bin/python -m pytest tests/test_recovery_bundle.py -q
4 failed, 93 passed in 7.76s
```

Camel-case and order-semantic authority keys passed through the manifest, task-result, exposure-fact, or nomination recovery validators.

Exact grant-drift verification:

```text
.venv/bin/python -m pytest tests/test_verify_market_intelligence_migration.py -q
28 failed, 41 passed in 0.76s
```

The verifier rejected the intended discovery grants because it did not model them, and it could not report extra discovery table, column, or function grants from actual ACLs.

### Implemented fixes

- Exact discovery-reference replay now compares the complete persisted security-child count and every supplied identity/content tuple. A subset, extra child, or changed child fails closed.
- `checkpoint_discovery_stage` locks and requires a market-intelligence row linked to a running parent analysis run for both first writes and advances.
- Every security, theme, exposure-fact, and nomination relationship is checked against `p_run_id` before persistence. Orphan and cross-run UUIDs fail at the protected RPC boundary.
- TypeScript and recovery JSON validators collapse key names to lowercase alphanumerics and reject the promised authority, execution, portfolio-overlap, broker, and order semantics at any nesting level.
- The migration verifier collects grants for every discovery table and function plus discovery dashboard column ACLs, compares them to the exact intended role/grant sets, and derives `unexpected_grants` from catalog data.
- Trigger helpers explicitly revoke execute from `PUBLIC`, `anon`, `authenticated`, and `service_role`; only the protected security-definer RPCs retain the exact service-role execution grants.
- The wrong-run gateway test now uses a structurally valid UUID from another run so TypeScript accepts the wire shape and PostgreSQL remains responsible for same-run enforcement.
- The additive migration remains `20261005_market_wide_discovery.sql`; immutable `20261004_production_schema_reconciliation.sql` and its collision rejection remain unchanged.

### GREEN evidence

Focused SQL and migration-verifier suite:

```text
.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py tests/test_verify_market_intelligence_migration.py -q
84 passed in 1.67s
```

Focused parser and repository suite:

```text
npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts
ok | 21 passed | 0 failed (148ms)
```

Focused recovery and managed-restore suite:

```text
.venv/bin/python -m pytest tests/test_recovery_bundle.py tests/test_managed_isolated_restore.py -q
137 passed in 8.82s
```

Gateway handler suite:

```text
npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/handler_test.ts
ok | 53 passed | 0 failed (183ms)
```

Full regression after the final implementation changes:

```text
.venv/bin/python -m pytest -q
988 passed, 3 skipped, 4 deselected in 53.88s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared
ok | 241 passed | 0 failed (860ms)
```

Formatting, compile, and whitespace checks:

```text
npx --yes deno@2.9.6 fmt --check supabase/functions/market-briefing-gateway/_shared/intelligence.ts supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts
Checked 2 files

.venv/bin/python -m py_compile <changed Python implementation and test files>
git diff --check
exit 0
```

### Review-round self-review

- Verified the incomplete-replay check runs after child comparison and also rejects a newly supplied child when the manifest identity already exists.
- Verified same-run checks occur before inserts, including nomination exposure IDs supplied as a bounded JSON array.
- Verified completed and failed parent states reject both create and advance while valid running-state replay remains idempotent.
- Verified grant collection reads real PostgreSQL information-schema and catalog ACLs; fixtures cover missing intended grants and extra table, column, and helper-function grants.
- Verified `sql/schema.sql` contains the exact updated `20261005` migration tail and no `20261004` bytes changed.
- Verified the fixes preserve owner-only, suggestion-only behavior, `execution_allowed = false`, and the no-production/no-network-mutation boundary.

No new material concern was found. The skipped full-suite tests remain pre-existing environment-dependent cases; all disposable PostgreSQL discovery tests executed and passed locally.

## Review fix round 2

The second review found that array length plus per-supplied-row replay checks did not prove child identity-set equality. A persisted `[A, B]` result could be replayed as `[A, A]`. It also found that nomination `exposure_fact_ids` uniqueness was enforced during recovery but not at the gateway parser or protected SQL write boundary.

### RED evidence

TypeScript parser regressions were added for duplicate security revision IDs, every stage result-child collection, and duplicate nomination exposure-fact IDs:

```text
npx --yes deno@2.9.6 test --config supabase/functions/deno.json --filter "duplicate" supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts
FAILED | 0 passed | 3 failed | 7 filtered out (12ms)
```

Disposable PostgreSQL regressions persisted exact two-child manifests and stage results, verified an exact replay, then substituted `[A, A]` for `[A, B]`. Coverage includes security revisions, theme episode revisions, exposure facts, research nominations, and duplicate nomination exposure-fact IDs:

```text
.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py -q -k 'reference_replay_is_exact or stage_replay_rejects_duplicate or duplicate_nomination_exposure'
5 failed, 14 deselected in 1.18s
```

After the first uniqueness implementation, self-review identified a UUID canonicalization bypass: lowercase and uppercase spellings of the same UUID grouped as different JSON strings even though PostgreSQL casts them to the same identity. The tests were tightened to use case-varied duplicate identities and failed before the SQL grouping fix:

```text
.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py -q -k 'reference_replay_is_exact or stage_replay_rejects_duplicate or duplicate_nomination_exposure'
5 failed, 14 deselected in 1.06s
```

### Implemented fixes

- The TypeScript reference parser rejects duplicate parsed security revision UUIDs.
- The TypeScript checkpoint parser rejects duplicate parsed IDs independently in theme episode, exposure fact, and research nomination collections.
- The TypeScript nomination parser rejects duplicate parsed `exposure_fact_ids`.
- `record_market_discovery_reference` rejects duplicate security revision UUID identities before manifest or child persistence and before replay comparison.
- `checkpoint_market_discovery_stage` rejects duplicate child UUID identities in all three stage result arrays before task transition, child persistence, or replay comparison.
- The checkpoint RPC rejects duplicate nomination exposure-fact UUID identities before lineage validation or persistence.
- PostgreSQL groups duplicate checks by the UUID value rather than raw JSON text, so case variants cannot bypass identity uniqueness.
- The audit confirmed the recovery validator already rejects duplicate record identities and duplicate nomination exposure-fact IDs. The gateway, protected database boundary, and recovery verifier now enforce the same identity rule.
- `sql/schema.sql` retains the exact `20261005_market_wide_discovery.sql` tail. Immutable `20261004_production_schema_reconciliation.sql` remains unchanged.

### GREEN evidence

Direct parser regressions:

```text
npx --yes deno@2.9.6 test --config supabase/functions/deno.json --filter "duplicate" supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts
ok | 3 passed | 0 failed | 7 filtered out (6ms)
```

Direct disposable PostgreSQL regressions after UUID canonicalization:

```text
.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py -q -k 'reference_replay_is_exact or stage_replay_rejects_duplicate or duplicate_nomination_exposure'
5 passed, 14 deselected in 0.97s
```

Focused SQL and migration-verifier suite:

```text
.venv/bin/python -m pytest tests/test_market_wide_discovery_sql.py tests/test_verify_market_intelligence_migration.py -q
88 passed in 1.43s
```

Focused parser and repository suite:

```text
npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts
ok | 24 passed | 0 failed (185ms)
```

Recovery, managed restore, and handler integration remained green:

```text
.venv/bin/python -m pytest tests/test_recovery_bundle.py tests/test_managed_isolated_restore.py -q
137 passed in 8.37s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/handler_test.ts
ok | 53 passed | 0 failed (194ms)
```

Full regression on the final implementation:

```text
.venv/bin/python -m pytest -q
992 passed, 3 skipped, 4 deselected in 54.60s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared
ok | 244 passed | 0 failed (814ms)
```

### Review-round self-review

- Confirmed duplicate detection uses parsed lowercase UUIDs in TypeScript and PostgreSQL UUID casts in SQL.
- Confirmed each top-level child collection is checked independently; equal UUIDs in different ledger tables are not conflated.
- Confirmed duplicate rejection occurs before a protected write or replay receipt can succeed.
- Confirmed exact `[A, B]` replay still returns `duplicate: true`, while incomplete, changed, and duplicate-substitution payloads fail closed.
- Confirmed recovery already enforced the same record and nomination lineage uniqueness rules and needed no implementation change.
- Confirmed no prior migration, deployment script, production database, provider, Telegram, credential, schedule, or network state was changed.

No new material concern was found. The three Python skips remain pre-existing environment-dependent tests; every disposable PostgreSQL discovery test ran and passed on this host.
