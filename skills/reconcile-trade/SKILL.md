---
name: reconcile-trade
description: Use when the owner reports a completed buy, sell, skipped trade, stop change, or another real-portfolio recordkeeping change.
---

# Reconcile a completed trade

This records what the owner already executed. It never recommends, places, modifies, or cancels a
brokerage order.

## Confirmed Telegram path only

For supported portfolio operations, explain and direct the owner to the deterministic,
owner-authenticated Telegram command:

```text
/buy AAPL 2 210 growth
/buy AAPL 2 210 growth on 2026-08-28
/sell AAPL 1.5 225
/sell NVDA all 210 on 2026-08-28
/stop AAPL 195
/portfolio
```

The bot reads current state, returns a precise preview, and writes nothing until the owner presses
Confirm. Its database transaction validates identity, command expiry, duplicate execution, current
shares, oversells, positive quantity/price, bucket, and execution date atomically. `all` means the
exact shares present when confirmed. A command without `on YYYY-MM-DD` uses its Telegram message date
in America/Chicago; never substitute today's date for a delayed trade.

This cloud skill has no portfolio write authority. It may explain or format one supported command,
but it may not parse the report and write it itself, call storage directly, or claim the command was
confirmed. The owner must see the bot preview and press Confirm.

If Telegram is unavailable, if the command grammar does not support the requested change (including
a hold override), or if required quantity, fill price, execution date, or bucket is unknown, stop.
Tell the owner the exact missing fact and direct him to explicit local-admin reconciliation from a
trusted checkout. Do not create a fallback write path. Several trades are reconciled one at a time.

“Skipped it” or “didn't buy” is write-free: acknowledge it and make no portfolio change.

## Reporting rules

- Use the broker's actual fill quantity, price, and execution date; never infer them from a quote.
- Never guess current shares, average cost, realized P&L, bucket, stop, or persisted success.
- A partial sale keeps average cost unchanged; a full sale removes the holding only after confirmed
  atomic processing.
- A stop command changes only the recorded stop. It is not a brokerage stop order.
- Recordkeeping does not imply the trade was wise.
- If preview, confirmation, verification, or read-back fails, say it was not confirmed; never invent
  success.
