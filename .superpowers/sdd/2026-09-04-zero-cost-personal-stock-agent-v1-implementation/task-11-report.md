# Task 11 Report

- Base: `2ae3742`
- Scope: Task 11 owner-only intelligence/report dashboard contracts, read API, and least privilege only.

## RED

The exact Step 2 command was run once after the Task 11 tests were written. Dashboard contract tests
passed because the new interfaces are erased at runtime, then Deno stopped at the expected nine
type errors for the absent intelligence/report mappers and route names. The `&&` chain therefore did
not reach pytest. Tests were frozen after this run.

## Implementation

- Added canonical bounded `IntelligenceView`, `ReportsView`, and `ReportDetailView` contracts and
  receipt-backed extensions to Portfolio, Ideas, and System.
- Added authenticated GET routing for `/v1/intelligence`, `/v1/reports`, and UUID-only
  `/v1/reports/:id`. Existing handler authentication, owner UUID enforcement, exact-origin CORS,
  no-store responses, bounded opaque cursors, and safe public errors remain the common boundary.
- Added fixed direct-SELECT repository statements for the latest completed intelligence run,
  events, themes, ranked candidates, source coverage, report lists, report detail, allowlisted
  source links, and publication receipts. No RPC, provider call, mutation statement, or raw provider
  payload is in the request path.
- Added bounded mappers that return only accepted report fields and structured receipt evidence.
  Unsafe source schemes become non-clickable; raw source text, canonical provider content,
  metadata, quota plans, component scores, evidence packets, owner identifiers, secrets, and hidden
  model state are omitted.
- Added a local-only revoke-first migration with exact column SELECT grants and SELECT-only RLS
  policies for eight required immutable tables. It grants no function execution and leaves the
  other new ledgers inaccessible. The canonical schema ends with the exact migration text.
- Expanded the privilege verifier's exact allowlist; unexpected columns, table/write privileges,
  policy coverage, memberships, function execution, ownership, or role authority still fail closed.

## Verification

- Exact Step 4 command was run once. Contracts passed 6/6 and the owner-dashboard Deno suite passed
  41/42; the sole failure showed that the empty intelligence state returned before the source query
  could be structurally observed. Because the command used `&&`, pytest was not reached.
- Fixed only that production failure by issuing the same fixed bounded intelligence selects with a
  null run ID before returning `unavailable`.
- The failed repository component then passed 12/12, and the unreached role/supply-chain continuation
  passed 16/16.
- `git diff --check` passed and the schema mirror comparison returned true.

No full suite, live provider/model/database/Telegram call, production migration, deployment,
scheduled run, financial mutation, brokerage integration, or trade execution occurred.

## Concerns / boundaries

- The migration is local-only until V1-C6; the evidence does not claim production grants,
  deployment, or live dashboard receipts.
- Report source links are resolved only for bounded persisted source identifiers that match stored
  source-item IDs. Missing links remain absent rather than reconstructed from prose.

## Fix round 1

- Replaced the incorrect report-to-`market_publications` run linkage with a fixed bounded SELECT
  from authoritative completed `record_report` gateway requests, matched exactly by the stored
  `response.report_id` and report UUID.
- The report mapper reads only bounded `publication_receipt` fields from the stored response and
  emits request ID, completed gateway status, attempt count, finish time, stored outcome, and
  bounded Telegram message IDs. It allowlists `accepted_by_telegram`, `delivery_failed`,
  `delivery_unknown`, `duplicate`, and `suppressed`; it never upgrades acceptance to delivery and
  never exposes the raw response.
- Existing grants were sufficient, so no migration, schema, verifier, or test changed. The exact
  Task 11 Step 4 gate passed in one run: 6 contracts, 42 Deno API tests, and 16 role/supply-chain
  tests.
