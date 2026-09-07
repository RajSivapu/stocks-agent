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
