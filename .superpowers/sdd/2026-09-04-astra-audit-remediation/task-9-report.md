# Task 9 — Scheduled lifecycle and bounded relevant history

## Red / green evidence

- RED: the focused Deno gate failed because `mergeRelevantSuggestions` did not exist; the Python gate also proved that an overdue scheduled phase was accepted and that the planned `20260911` migration could not be created without colliding with the ordered history.
- GREEN: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts` passed 50 tests.
- GREEN: `.venv/bin/python -m pytest -q tests/test_verify_owner_dashboard_deployment.py tests/test_verify_market_intelligence_migration.py` passed 81 tests.
- GREEN: `git diff --check` passed.

## Changed files

- `sql/migrations/20260923_scheduled_run_lifecycle.sql` adds the next ordered, additive scheduled-slot contract; it does not rewrite deployed migration history.
- `sql/schema.sql` mirrors the final fresh-schema lifecycle functions after their intelligence/report dependencies.
- Gateway repository and handler now bind scheduled starts to a market-date/phase slot, preserve request idempotency, merge bounded unresolved suggestions ahead of completed history, and surface specific missing-stage completion codes.
- The deployment verifier reads overdue scheduled phases through its scoped, read-only database path and rejects a non-empty overdue receipt.
- Focused Deno and Python regressions cover the bounded-history, scheduled-slot, missing-stage, schema, and overdue-verifier boundaries.

## Commit

`fix: enforce scheduled run lifecycle` (this report is included in that commit).

Controller remediation commit: `fix: close scheduled lifecycle receipt gaps`.

## Residual risks

- No live database, provider, Telegram delivery, scheduled run, migration application, or deployment was performed.
- The protected rollout must apply the ordered `20260923` migration and reconcile an existing scheduled chain; the verifier intentionally treats any overdue phase without a completed or explicit suppression receipt as a failure.

## Controller remediation

- Revoked the obsolete three-argument start RPC and bound every duplicate scheduled-start gateway request to the converged run.
- The lifecycle closure now requires completed, phase/date-bound collection; a persisted packet; completed evaluation plus a suppressed periodic publication; completed report plus a date-bound report; and a delivered or suppressed report outbox receipt. A wholly suppressed chain records `analysis_runs.status = 'suppressed'`.
- Overdue detection now covers every trading day since the effective deadline, uses the Chicago 06:30/12:00/15:10 deadlines with an explicit 15-minute grace period, and fails closed when the active annual calendar is missing.
- Context preserves bounded unresolved rows before completed history and orders equal-date history by durable ID descending.
- RED: the disposable fresh/ordered PostgreSQL regression first exposed fresh-schema retry rows not being bound, then exposed the migration overdue query's stale `d` alias.
- GREEN: focused Deno repository/handler tests passed `52`; deployment/migration verifier tests passed `81`; disposable fresh/ordered PostgreSQL scheduled-lifecycle tests passed `4` (with `13` intentionally deselected); `git diff --check` passed.

## Controller remediation round two

- Added ordered migration `20260924_scheduled_lifecycle_followup.sql`; it preserves prior schedule rows and versions deadlines by `(phase, effective_on)`.
- A scheduled report is now linked through the completed `record_report` response's report UUID and hashes, because that request intentionally stores no `run_id`. The matching report and its report-publication receipt must use a phase-allowed report kind.
- Unresolved suggestions use a 101-row overflow probe and fail closed rather than silently dropping the 101st pending row.
- The overdue reader selects the latest effective schedule per phase/date, validates the active calendar year and holiday array shape, and excludes actual configured holidays.
- RED: the new focused cases failed for silent unresolved truncation, single-row schedule keys, and the null-run report receipt contract.
- GREEN: focused Deno repository/handler tests passed `53`; deployment/migration verifier tests passed `81`; fresh/ordered disposable PostgreSQL scheduled tests passed `4` (with `13` intentionally deselected); `git diff --check` passed.
