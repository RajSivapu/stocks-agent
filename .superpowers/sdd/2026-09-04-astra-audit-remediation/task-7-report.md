# Task 7 report — discoverable provider evidence and contradiction preservation

## Scope completed

- Provider records now retain a stable upstream item ID, canonical item URL, secret-free request URL,
  entity/security identifiers, and distinct publication, retrieval, effective, and reporting-period
  timestamps.
- Versioned settings contain the CIK and macro-series mappings used by pipeline query construction.
  Unsupported official abstract queries stop before quota admission or HTTP with `UNSUPPORTED_QUERY`.
- Collection-window admission uses publication time (or retrieval only when the provider omitted a
  publication field), rather than effective or reporting-period time. Newly published prior-period
  filings and future-effective rules are therefore retained and labeled.
- Exact identity/content duplicates may be marked as duplicates, but different item URLs sharing a
  request URL and opposing affirmed/denied claims remain distinct persisted evidence rows.
- Pipeline rows persist `discovery_status` as `qualified`, `no_event`, or
  `insufficient_coverage`; security identifiers feed discovery without test-only ticker mutation.

## Evidence

- RED: the new Task 7 regressions initially failed because prior-period/future-effective facts were
  filtered out, request and item URLs were conflated, identity/time fields were absent, and opposite
  claims were treated as duplicates.
- GREEN: `.venv/bin/python -m pytest -q tests/test_intelligence_providers.py tests/test_intelligence_pipeline.py tests/test_intelligence_dedupe.py`
  completed with `60 passed` on 2026-09-05.
- `git diff --check` passed.

## Residual risks

- White House, DOE, DoD, EIA, BLS, and BEA are declared free sources but their currently configured
  abstract queries are intentionally `UNSUPPORTED_QUERY` before HTTP until a reviewed provider-
  specific free endpoint/identifier mapping is added; this is explicit insufficient coverage, not a
  claim of collection.
- This task used fixtures only. It made no network/provider call, database write, Telegram send,
  deployment, brokerage action, or scheduled run.

## Controller remediation (round 1)

- Added an additive provenance ledger and migration which accepts the gateway's provider, request
  URL, retrieval/reporting time, identifiers, and discovery status fields. The legacy fact tables
  keep their Task 4 interface; the provider request URL is used only to satisfy its provider-host
  contract, while the provenance ledger retains the canonical publisher item URL.
- Gateway parsing now validates the provider-specific request host separately from the external
  publisher URL, bounds identifier arrays, rejects secret-bearing request URLs, and requires a
  security relationship for a `qualified` item.
- Evidence identity now includes provider, stable upstream ID, canonical item URL, and claim
  polarity, so corroborating sources do not collide and opposite polarity cannot share an item UUID.
  Yahoo now has a distinct publisher canonical URL and request URL.
- Pipeline targets are translated through versioned local mappings before provider use. Invalid or
  missing CIK/series mappings fail `UNSUPPORTED_QUERY` before quota/HTTP. Discovery outcomes are
  included with source receipts; `qualified` requires a ranking-eligible relationship.

### Controller evidence

- Final focused gate: `122 passed` across the three Task 7 Python suites plus the migration
  verifier, and `3 passed` in the gateway Deno contract test.
- `git diff --check` passed. No network/provider call, database mutation, deployment, Telegram
  action, brokerage action, or paid-provider action was performed.

### Controller residual risks

- The new provenance table stores the publisher link separately so legacy Task 4 fact interfaces
  continue to read the provider request reference. A subsequent display-layer migration can join
  provenance for publisher-link presentation without changing those persisted fact contracts.

## Controller remediation (round 2)

- Exact duplicate evidence rows are now collapsed before persistence, preventing duplicate source-item
  UUID inserts. Their receipt/item/reason references are retained in bounded completion coverage and
  receipt accounting, while near-duplicate corroboration remains in discovery and packet evidence.
- Discovery outcomes are derived only after relationship qualification and use only `qualified`,
  `no_event`, or `insufficient_coverage`.
- Gateway parsing rejects secret-bearing request URL query/path values before any RPC. Schema now
  exactly uses the migration's identifier-array byte bounds. Federal Register requests use its
  documented `conditions[...]`, `per_page`, and `order` parameter shape.

### Controller round 2 evidence

- Final focused gate: `126 passed` across focused Task 7 Python and migration tests; gateway Deno
  contract tests: `3 passed`; `git diff --check` passed.
- Fixtures only: no network/provider calls, database writes, deployment, Telegram send, brokerage
  action, paid provider, or full-suite run.
