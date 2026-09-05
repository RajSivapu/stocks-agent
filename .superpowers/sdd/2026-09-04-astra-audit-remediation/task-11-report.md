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
