# Task 11 — Outcomes, privileges, dependencies, and market sessions

Base SHA: `6da3e84c959f166624bf9cf5ef6161ed861feae2`

## Implementation

- Outcome returns now align stock and benchmark exposure at the same decision-session adjusted close. MFE/MAE use adjusted session highs/lows, while the weekly methodology explicitly distinguishes a market-touch entry from an unproven owner fill.
- Consecutive losses are grouped by recommendation ID and use the longest complete horizon once, so one recommendation with losing 5/21/63-session grades contributes one loss.
- Weekly packets retain veto-only evaluations and their run-linked publication receipts, yielding nonzero veto summaries even when policy emits no suggestion.
- The weekly audit now connects only through the project-matched `stock_agent_dashboard_runtime` Supavisor session role, begins a read-only transaction, and runs eight fixed bounded SELECT statements. Migration `20260928_weekly_audit_read_scope.sql` adds only the missing column-level SELECT grants and RLS policies.
- CI installs a complete 40-package universal Python lock with hash and binary-wheel enforcement. The lock includes `cryptography==46.0.5` for Task 10 recovery tests.
- The maintained 2026 market calendar models the November 27 and December 24 13:00 America/New_York closes and fails closed outside reviewed coverage. Quote parsing rejects symbol/currency mismatches and marks unknown halt, spread, or liquidity evidence unavailable for actionable pricing.

## Focused red/green evidence

- RED: the initial Deno gate failed because recommendation-level loss grouping did not exist and the old outcome expectations used mismatched entry bases/close-only excursions. Focused market tests also failed before quote actionability and early-close behavior were implemented.
- RED: the initial Python gate failed because the restricted weekly-reader helpers, veto-only retention, SELECT grants, and enforced hashed lock were absent.
- GREEN: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/outcomes_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/market-calendar_test.ts supabase/functions/market-briefing-gateway/_shared/market-data_test.ts` — `46 passed, 0 failed`.
- GREEN: `.venv/bin/python -m pytest -q tests/test_weekly_audit.py tests/test_security_invariants.py` — `35 passed`.
- Hygiene: `git diff --check` passed.

## Safety boundary

This task performed local implementation and focused fixture tests only. It did not connect to a live database, apply a migration, deploy, send Telegram messages, trigger a scheduled run, call a market-data/model provider, or add brokerage authority. The system remains owner-only, suggestion-only, and zero incremental cost.

## Fix round 1 — Important review findings

- Loss streak rows now embed the authoritative suggestion timestamp, use stable database tie-breakers, and independently sort recommendation groups newest-to-oldest by suggestion timestamp and durable ID. The regression places an older winner before two newer losses with identical grade timestamps and confirms the circuit-breaker input is `2`.
- Intraday quotes now require the maintained calendar to report an open regular session. A 13:00 `REGULAR` quote is rejected at 13:01 America/New_York on the November 27 half-day.
- `verify_owner_dashboard_role.py` now admits exactly the extended `suggestion_grades`, `lessons`, and `daily_snapshots` SELECT columns. Focused checks prove the exact shape passes and missing or extra columns fail.
- Both protected release workflows install only `requirements.lock` with `--require-hashes --only-binary=:all:`; neither consumes `requirements-test.txt`.
- The weekly audit connects through psycopg with `sslmode=verify-full`, while the existing exact Supavisor hostname and scoped-role DSN validation remains in force.

### Fix-round red/green evidence

- RED: the two focused Deno files reported `17 passed, 2 failed`: tied grade chronology returned `0` instead of `2`, and post-half-day-close intraday quote validation returned actionable.
- RED: the Task 11 Python gate reported `35 passed, 3 failed` for missing TLS verification, stale dashboard verifier columns, and the nondeterministic grade query.
- RED: the workflow contract reported `10 passed, 2 failed` because both protected workflows still consumed the unhashed input file.
- GREEN: the exact Task 11 Deno gate passed `48 passed, 0 failed`.
- GREEN: the exact Task 11 Python gate passed `38 passed`.
- GREEN: the affected protected-workflow contract passed `12 passed`.
- `git diff --check` passed. No full suite or external/live action was run.
