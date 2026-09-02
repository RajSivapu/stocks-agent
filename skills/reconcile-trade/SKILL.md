---
name: reconcile-trade
description: Use when Rajrupesh reports a completed buy, sell, skipped trade, stop change, or hold override that must be reconciled into the recorded portfolio, including a trade reported after its execution date.
---

# Reconcile a Trade — keep holdings & P&L accurate

The owner executes trades himself, then records what already happened so the scheduled analyst sees
the real portfolio. This is recordkeeping only. If any brokerage execution tool appears, do not use
it; stop and report a guardrail breach.

## Preferred path: Telegram Confirm

For ordinary Buy, Sell, full exit, and Stop records, direct the owner to the existing Telegram bot:

- `/buy AAPL 2 210 growth`
- `/sell AAPL 1.5 225`
- `/sell NVDA all 210`
- `/sell NVDA all 210 on 2026-08-28` for a trade reported later
- `/stop AAPL 195`
- `/portfolio`

The bot is deterministic—there is no model behind the parser. It reads current holdings, sends a
preview, and changes nothing until the owner presses **Confirm**. The Supabase RPC then validates the
owner, expiry, current share count, quantity/bucket, and applies the transaction + holding change in
one database transaction. Cancel, stale, expired, ambiguous, or duplicate commands change nothing.

For an immediate Buy/Sell without `on YYYY-MM-DD`, the bot records the Telegram message date in
America/Chicago as `executed_on`. For a delayed report, include the exact broker execution date.
Impossible and future dates are rejected before a pending command exists. The transaction `ts`
continues to mean when the event was recorded; it is never backdated.

## Claude-chat fallback

Use this fallback only when Telegram is unavailable or the report is outside the bot's intentionally
narrow grammar (for example, a hold override). Apply the same safety bar:

1. Parse one event into operation, uppercase ticker, positive finite quantity, exact execution price,
   actual execution date, and bucket for a new holding. `all` means the exact currently recorded
   shares. If a delayed report omits its date, ask; never substitute today's date.
2. Read `lib.db.get_holdings()` and reject a missing holding, oversell, non-positive stop, or changed
   share count. Never infer an execution price from a current quote; ask for the broker fill price.
3. Restate the exact database change in one line and obtain explicit owner confirmation before any
   write. Several reported trades are confirmed and processed one at a time.
4. Use public `lib.db` helpers only. Do not issue raw `_sb()` table mutations when a helper exists.
5. Read holdings back after the write and report only the state that actually persisted. Never invent
   success or P&L.

### Buy fallback

- `new_shares = old_shares + qty`.
- `new_avg = (old_shares*old_avg + qty*price) / new_shares`.
- Preserve the existing bucket, stop, target, opened date, notes, and high-water mark. A new holding
  requires `core`, `growth`, or `speculative`.
- Use `lib.db.get_latest_buy_levels(ticker)` for a prior suggestion; do not query tables directly.
- After confirmation, call `lib.db.insert_transaction(...)` then `lib.db.upsert_holding(...)` and
  verify with `lib.db.get_holdings()`.

### Sell fallback

- Require an exact broker fill price and `qty <= current shares`.
- Realized P&L is `(price - old_avg) * qty`; average cost does not change on a partial sale.
- After confirmation, call `lib.db.insert_transaction(...)`. For a partial sale call
  `lib.db.upsert_holding(...)`; for a full exit call `lib.db.delete_holding(ticker)`. Read back and
  verify. Prefer Telegram whenever possible because its RPC makes these writes atomic.

### Stop and hold-override fallback

- Stop: after confirmation call `lib.db.update_holding_stop(ticker, stop=new_stop)`; do not alter
  target or high-water price.
- Hold override: resolve an exact date and reason, confirm it, then call
  `lib.db.set_hold_override(ticker, until_date, reason=f"owner: {reason}")`. Explain that this mutes
  routine stop pushes only; thesis-breaking invalidation alerts still apply.

### Skip

"Skipped it" / "didn't buy" creates no transaction and no holding change. Acknowledge only.

## Guardrails

- Never place, modify, or cancel a brokerage order.
- Never guess quantity, bucket, fill price, execution date, current shares, or persisted success.
- Never treat recordkeeping as a recommendation that the trade was good.
- Telegram Confirm is the default because it is deterministic, identity-checked, stale-safe,
  idempotent, and atomic. Claude chat is a deliberate fallback, not a second automation path.
