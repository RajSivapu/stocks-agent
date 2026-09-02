---
name: paper-watch
description: Use when the owner wants to create, inspect, or close a hypothetical stock idea without changing his real portfolio.
---

# Paper watch

Paper watches are hypothetical research records. They never place or simulate an order and can never
mutate holdings or transactions.

## Required lifecycle

Use only `python scripts/market_gateway.py` for context and mutations. Never call a database,
Supabase endpoint, messaging endpoint, or brokerage endpoint directly.

1. Call `start_run` with `phase: on-demand` and then `read_context` using its run ID.
2. For a status request, display only active paper watches from bounded context. Fetch current prices
   from read-only sources, show their provider timestamps, and calculate clearly labeled hypothetical
   return. Do not write a mark simply because the owner viewed status.
3. For a create request, require an uppercase ticker and owner thesis. Use the stated reference price
   or fetch a current timestamped quote; never guess. Submit one `paper_watch_create` mutation through
   `record_artifacts`, with optional target, hypothetical amount, and horizon. The gateway supplies
   server-owned dates and validates the latest evaluated view.
4. For a close request, identify exactly one active watch from context and fetch its current quote.
   Submit one `paper_watch_close` mutation with the watch ID. The gateway rechecks active identity and
   owns the close date/price fields; do not trust caller-supplied historical state.
5. Call `finish_run` after the read or mutation. Report only its actual receipt. Expect
   `status: suppressed`; paper-watch activity stays in this session with no Telegram notification.

Each operation has a new UUID request ID, reused only for an uncertain identical retry. If any
gateway operation fails, stop; do not fall back to direct storage or claim success. If a run exists
and the gateway remains reachable, call `finish_run`.

In a dry run, pass `--dry-run` to every operation, execute all reads/calculations, and label the
artifact as would-write only. Temporary JSON, if needed, belongs under `mktemp -d`, never the
checkout.

## Response

For create/close, state ticker, reference price, thesis/horizon, hypothetical return where relevant,
and the gateway-confirmed artifact receipt. For status, state entry/current price, hypothetical
percentage and dollars (if an amount exists), days open, thesis, and view at opening. Mark stale or
missing quotes rather than computing a misleading return.

Always call the records “hypothetical.” Never characterize a paper result as real P&L or permission
to trade.
