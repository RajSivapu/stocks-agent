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

## Residual risks

- No live database, provider, Telegram delivery, scheduled run, migration application, or deployment was performed.
- The protected rollout must apply the ordered `20260923` migration and reconcile an existing scheduled chain; the verifier intentionally treats any overdue phase without a completed or explicit suppression receipt as a failure.
