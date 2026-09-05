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
