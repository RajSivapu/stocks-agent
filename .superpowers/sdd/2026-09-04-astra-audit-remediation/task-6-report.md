# Task 6 — Durable publication and command delivery states

## Red / green evidence

- RED: the report retry test observed `delivery_unknown` with a partial Telegram ID after an ambiguous send; the new expected receipt required `uncertain`, no stored IDs, and `retry_allowed: false`.
- RED: the periodic evaluation test observed `ready`; the required receipt is `suppressed` so only the deterministic report key can deliver the periodic briefing.
- GREEN: focused Deno gateway tests passed: 43 tests across `repository_test.ts` and `handler_test.ts`.
- GREEN: focused Node Telegram tests passed: 14 tests, including committed callback + failed `editMessageText` acknowledgement.
- GREEN: focused Python migration/security tests passed: 58 tests in `tests/test_verify_market_intelligence_migration.py`.
- GREEN: `git diff --check` passed.

## Changed files

- `sql/migrations/20260910_delivery_outbox.sql` and `sql/schema.sql`: service-role-only durable report delivery outbox/RPCs with pending, delivered, failed, uncertain, and suppressed states; a lease expiry becomes uncertain, and message IDs are retained only on delivered.
- Gateway repository/handler/tests: persist the immutable report and pending outbox receipt before a send, recover same-key delivery state without resending delivered/uncertain work, and suppress periodic standard-evaluation sends.
- Telegram portfolio callback helpers/tests: preserve the committed database result when acknowledgement editing/sending fails and state that acknowledgement is uncertain.

## Commit

`bf55dde26985325fc165a473a9aea362371c7156` — `fix: persist delivery uncertainty`.

## Residual risks

- This task did not run a live migration, Telegram send, database write, deployment, or scheduled run. The protected rollout must apply the migration and reconcile the resulting receipts.
- A network ambiguity is intentionally terminal for that key: an operator must reconcile it rather than retrying and risking duplicate delivery.

## Controller review round 1

- Added the fresh-schema order regression and delayed the report-outbox foreign key until after the intelligence report ledger exists.
- Added a service-role-only portfolio command acknowledgement ledger and atomic command-plus-pending-acknowledgement RPC. Duplicate callback updates re-enter only the idempotent acknowledgement path.
- Suppressed reports now create a report record and a terminal suppressed outbox receipt; scheduled intraday evaluation is also suppression-only so delivery is owned by the report/alert paths.
- Focused rerun: 43 Deno, 14 Node, and 59 Python tests passed; no live database or Telegram action occurred.
- Controller-round commit: `2c8dbbb29ec30ac734a40ca18b87381410ebcf86`.
- Follow-up commit: `8694a40d5ad16d4beb92900f075762b82421ef15` corrected same-request report retry truthfulness: failed report delivery now records a failed gateway receipt, and the report-specific gateway claim can reacquire that request while the deterministic outbox remains the send authority.
- Exact focused evidence after the follow-up: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/handler_test.ts` — 41 passed, 0 failed; `git diff --check` — passed.

## Controller review round 2 — integration recovery

- Inherited partials: the working tree already contained uncommitted
  `20260912_delivery_recovery_claims.sql`, `20260913_command_acknowledgement_lease.sql`,
  schema/controller changes, and focused tests. This round retained and integrated them; it did
  not discard or reset prior work.
- Fresh schema: the report-outbox table can be declared before the intelligence ledger, but every
  report-outbox function using `market_reports%ROWTYPE` now follows `market_reports`; the delayed
  foreign key remains final. The former test only checked the foreign key, and the old exact-head
  schema still compiled the `%ROWTYPE` function too early.
- Delivery recovery: `20260912` is an additive service-role migration that reinstalls the final
  `claim_market_gateway_request` path for failed `record_report` requests, mirrored by the final
  schema routine. An active pending report outbox now returns 409 without completing/caching the
  outer request; after the lease expires the same request re-enters, becomes terminally uncertain,
  and never resends.
- Command acknowledgement: `20260913` atomically leases one acknowledgement sender. Concurrent
  callback re-entry sees the active lease and does not send; expired lease completion transitions
  to terminal `uncertain`. If acknowledgement persistence fails after the command is committed,
  the webhook suppresses the false `Nothing was changed` fallback.
- Kept the prior suppression and intraday single-delivery-authority behavior unchanged.

Final focused Task 6 evidence (no full suite, live database, Telegram, deployment, or scheduled
work):

- `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts` — 44 passed, 0 failed.
- `npx --yes deno@2.9.6 check --config supabase/functions/deno.json supabase/functions/telegram-portfolio/index.ts` — passed.
- `node --test tests/test_telegram_webhook_utils.mjs` — 15 passed, 0 failed.
- `.venv/bin/python -m pytest -q tests/test_verify_market_intelligence_migration.py` — 61 passed.
- `git diff --check` — passed.

## Controller review round 4 — definitive rejection classification

- Durable acknowledgement lookup remains first at the atomic apply-and-ack boundary. A populated
  PostgreSQL SQLSTATE, PostgREST response code, or 4xx server status with no receipt is now the
  narrow proof required to report that no change was recorded. A durable receipt still overrides
  that classification.
- Empty/no-data responses, transport-style error codes, server 5xx responses, missing receipts,
  and unreadable receipts remain conservative uncertainty with required reconciliation.
- Focused tests retain committed-response-loss coverage and add definite server rejection, missing
  receipt, and unreadable receipt cases.

Focused Task 6 rerun (no full suite, live database, Telegram, deployment, or scheduled work):

- `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts` — 44 passed, 0 failed.
- `npx --yes deno@2.9.6 check --config supabase/functions/deno.json supabase/functions/telegram-portfolio/index.ts` — passed.
- `node --test tests/test_telegram_webhook_utils.mjs` — 19 passed, 0 failed.
- `.venv/bin/python -m pytest -q tests/test_verify_market_intelligence_migration.py` — 61 passed.
- `git diff --check` — passed.

## Controller review round 3 — lost atomic-RPC response

- An `error || !data` result from `apply_portfolio_command_with_acknowledgement` is no longer
  treated as proof that PostgreSQL rolled back. The webhook performs a service-role, owner-scoped
  durable receipt read using both the command ID and Telegram update ID.
- A recovered committed receipt produces a callback alert with the recorded outcome, uncertain
  acknowledgement, and required reconciliation; a recovered rejected receipt is the only path
  allowed to say no change was recorded. Missing or unreadable receipts produce only the uncertain
  outcome/reconciliation notice. No recovery path sends a duplicate command acknowledgement.
- Focused regression: a committed pending acknowledgement receipt with a lost RPC response returns
  the durable receipt and an uncertainty/reconciliation alert without `Nothing was changed`.

Focused Task 6 rerun (no full suite, live database, Telegram, deployment, or scheduled work):

- `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts` — 44 passed, 0 failed.
- `npx --yes deno@2.9.6 check --config supabase/functions/deno.json supabase/functions/telegram-portfolio/index.ts` — passed.
- `node --test tests/test_telegram_webhook_utils.mjs` — 16 passed, 0 failed.
- `.venv/bin/python -m pytest -q tests/test_verify_market_intelligence_migration.py` — 61 passed.
- `git diff --check` — passed.
