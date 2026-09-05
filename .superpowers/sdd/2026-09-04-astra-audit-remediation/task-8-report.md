# Task 8 report — resumable intelligence collection and real ranking inputs

## Scope completed

- Added a receipt-preserving resumable collection cache whose keys bind provider, normalized query,
  collection window, and schema version. Completed same-run retries return the original packet and
  source receipts without a new gateway reservation, provider call, or persistence call.
- Quota accounting now distinguishes actual requests from cache hits. Only actual requests consume a
  reservation; cache hits are visible separately and carry zero request cost.
- Scheduled collection requires the supplied `--run-id` and uses it as the collection run identity.
  Explicit `--dry-run` fixture previews remain safe without a run ID and have no writes or sends.
- Replaced discovery's neutral recency/liquidity/portfolio placeholders with source timestamps,
  validated context liquidity, owner holding weight, overlap, and concentration. Missing required
  inputs fail closed with stable reason codes; a 40 percent-or-greater holding concentration cannot
  qualify. Comparison IDs and bounded learning inputs are retained in the receipt coverage payload
  for the established Task 4/7 persistence contracts.

## Red/green evidence

- RED: the initial focused run stopped during collection because
  `lib.intelligence.cache.ResumableCollectionCache` did not exist.
- RED: the follow-up provenance regression failed with `KeyError: 'comparison_ids'` before the
  pipeline retained Task 4/7 handoff values.
- GREEN: `.venv/bin/python -m pytest -q tests/test_intelligence_http.py
  tests/test_intelligence_quota.py tests/test_intelligence_pipeline.py
  tests/test_intelligence_ranking.py tests/test_collect_market_intelligence.py` — **51 passed**.
- `git diff --check` — passed.

## Residual risks

- Evidence is fixture-tested only. No provider/network request, database write, scheduled run,
  Telegram delivery, deployment, brokerage action, paid provider, or full-suite run was performed.
- The cache is intentionally supplied by the collection runtime. Durable gateway cache entries remain
  the persistence authority; a restarted worker must retain or rehydrate its cache backing before it
  can resume a partially completed collection without recollection.

## Controller remediation round 1

- Cache keys now use a market-date/phase-stable window, so a retry with a later wall-clock value
  resolves the same completed collection. A new pipeline instance can use retained completion work,
  emits a new current-run source receipt, and links it to the original receipt through
  `cache_predecessor_receipt_id`; it does not reuse the old reservation or charge another request.
- Receipt payloads and the SQL schema/migration now carry the cache predecessor field. Cache-hit
  request cost is zero; actual requests and hits remain separate receipt totals.
- Scheduled CLI accepts only `--run-id`; the legacy ambiguous `--request-id` is rejected. Dry runs
  retain an explicit fixture-only deterministic identity and cannot reach the gateway.
- Ranking no longer infers overlap from holding weight. Array holdings calculate market-value weights
  only when each position has authoritative value or shares/current price; missing overlap, liquidity,
  price/value, or unsupported authority is insufficient. Evidence authority and exposure strength
  derive from the retained evidence rather than neutral constants.
- Untyped comparison/learning fields are rejected at collection ingress. Existing typed report and
  learning gateway operations remain their only persistence path.

### Controller evidence

- RED: restart/cache-lineage regression initially failed because a new pipeline recalculated a
  different wall-clock window and no cache result could mint a fresh receipt.
- RED: overlap regression initially showed that a holding weight silently became overlap.
- GREEN: the five Task 8 Python files passed with **54 tests**; no full suite or live integration ran.

## Controller remediation round 2

- Added the additive `20260918_durable_collection_controller.sql` deployed-contract override.
  It persists the first Chicago-session request window, rejects checkpoint writes unless the supplied
  run is still running, retains each successful actual request with its full normalized source-item
  payload before packet construction, and requires every checkpoint receipt in the terminal packet.
- The gateway start receipt now returns the retained window and unexpired checkpoints. A fresh Python
  cache hydrates them into receipt-preserving cache hits. A cache hit gets the current reservation and
  source receipt ID, points at the original actual receipt, and cannot be its own predecessor.
- The collection transport remains redirect-explicit and manual; accounting uses provider receipt
  request cost for actual outbound transport only. Pre-open rejection remains zero-cost. Cache expiry
  is enforced when checkpoints are hydrated and served.
- Added focused recovery evidence using separate cache instances sharing only a persisted fake gateway:
  an interrupted final packet write leaves nine request checkpoints, and a new worker resumes with
  zero provider calls and nine cache hits. Gateway-context array holdings also prove a 42% TEST
  holding is vetoed on the production discovery path.

### Round 2 verification

- One focused Task 8 gate (five collector/ranking files plus migration structure) — 121 Python tests passed.
- Gateway contract/controller subset — 66 Deno tests passed.
- No live provider, database, scheduled job, Telegram, deployment, or full suite was run.

## Controller remediation round 3

- Added the runtime/controller operation allowlist and envelope regression for
  `checkpoint_intelligence_collection`.
- Removed the production overlap fallback: overlap is now unavailable unless its validated map is
  supplied. Secondary-source corroboration is grouped by claim/ticker before ranking, while a lone
  secondary remains insufficient.
- Transport now records each explicit redirect/open attempt, charges prevalidated actual attempts,
  and preserves a nonzero failed-request cost after an outbound failure. Checkpoint persistence
  failures abort collection rather than fabricating a zero-cost failed receipt.
- Added additive 20260919 SQL controller work: running `analysis_runs` binding, replacement
  checkpoint history after expiry, and idempotent same-payload behavior.
