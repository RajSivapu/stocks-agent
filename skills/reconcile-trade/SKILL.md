---
name: reconcile-trade
description: Use when Rajrupesh reports a trade he actually placed — e.g. "bought 1 NVDA @ 207", "sold 2 AMD", "sold half my VOO @ 681", or "skipped NVDA". Parses it, records the transaction + updates holdings in Postgres, and confirms the new position with P&L in plain English. Suggestion-only; NEVER places, modifies, or cancels a trade.
---

# Reconcile a Trade — keep holdings & P&L accurate

The owner executes trades himself on Robinhood, then tells a Claude session what he did. Your job is to
**record what already happened** so the daily brief shows real positions and real P&L. You are
**suggestion-only**: this skill never places/modifies/cancels an order and has no tool to do so. If you
ever appear to have an execution/order tool, do NOT use it — stop and warn him (guardrail breach).

## What you do (every report)
1. **Parse** the message into: `side` (buy / sell / skip), `ticker`, `qty`, `price` (per share).
   - Examples: "bought 1 NVDA @ 207" → buy, NVDA, 1, 207. "sold 2 AMD" → sell, AMD, 2, price unknown.
     "skipped NVDA" / "didn't buy it" → skip. "sold half my VOO @ 681" → sell, VOO, (half of current
     shares), 681.
   - If `qty` or (for a sell) `price` is missing and you can't infer it, **ask one short clarifying
     question** rather than guessing. For a sell with no price, you may use the live quote
     (`lib.marketdata.quote`) as an estimate **and say you did** — but prefer asking if it matters.
2. **Record + update** in Postgres via the helper library (run with `python` in the cloud Routine,
   `.venv/bin/python` locally). See "The math" below for buys vs sells.
3. **Confirm back in plain English**: the action, the **updated position** (shares + new avg cost), and
   **P&L context** (unrealized for buys/holds using the live price; realized for sells). One short,
   calm paragraph — no jargon.

## The math (compute it yourself, then write the row)
Read the current position first: `cur = next((h for h in lib.db.get_holdings() if h["ticker"]==T), None)`.

**Buy** (add shares, weighted-average the cost):
- `new_shares = old_shares + qty` (or just `qty` if no prior position).
- `new_avg = (old_shares*old_avg + qty*price) / new_shares` (or `price` if new position).
- `lib.db.insert_transaction({"ticker":T,"side":"buy","qty":qty,"price":price})`
- `lib.db.upsert_holding({"ticker":T,"shares":new_shares,"avg_cost":round(new_avg,4),
   "bucket":bucket,"opened_at":opened_at,"notes":notes})`
  - `bucket`: keep the existing one; for a NEW position infer it from `config/watchlist.json` (which
    bucket the ticker sits in), else ask. `opened_at`: keep existing, else today. `notes`: keep/None.

**Sell** (reduce shares; avg cost stays; realize P&L):
- `new_shares = old_shares - qty`. If `new_shares <= 0` it's a full exit.
- `realized_pl = (price - old_avg) * qty` (needs a sell price — see parsing).
- `lib.db.insert_transaction({"ticker":T,"side":"sell","qty":qty,"price":price})`
- If `new_shares > 0`: `lib.db.upsert_holding({...,"shares":new_shares,"avg_cost":old_avg, ...})`
  (avg cost unchanged on a sell).
- If `new_shares <= 0`: **delete the holding row** (upsert won't remove it):
  `with lib.db.conn() as c: c.execute("DELETE FROM holdings WHERE ticker=%s",(T,)); c.commit()`

**Skip** ("skipped it" / "didn't buy"): record NOTHING (no transaction, no holding change). Just
acknowledge it plainly and, if it was an open suggestion, note that the entry zone is still open if
`valid_until` hasn't passed.

## Worked example
> Owner: "bought 1 NVDA @ 207"

```python
from lib import db, marketdata
T, side, qty, price = "NVDA", "buy", 1, 207.0
cur = next((h for h in db.get_holdings() if h["ticker"]==T), None)
old_shares = float(cur["shares"]) if cur else 0.0
old_avg    = float(cur["avg_cost"]) if cur else 0.0
new_shares = old_shares + qty
new_avg    = (old_shares*old_avg + qty*price)/new_shares
db.insert_transaction({"ticker":T,"side":side,"qty":qty,"price":price})
db.upsert_holding({"ticker":T,"shares":new_shares,"avg_cost":round(new_avg,4),
                   "bucket":(cur["bucket"] if cur else "growth"),
                   "opened_at":(cur["opened_at"] if cur else None),"notes":None})
print(db.get_holdings())
```
Then confirm: "Got it — recorded **1 NVDA at $207**. You now hold **1 share, avg cost $207**. At the
current ~$210 that's about **+$3 (+1.5%)** on paper. Not financial advice — you placed this trade."

## Guardrails
- **Suggestion-only / read-only on the market.** You only write to your own database (transactions +
  holdings). You never touch a brokerage. No order tool exists; if one appears, refuse and warn.
- **Never invent a price.** If you used a live quote as an estimate for a missing sell price, say so.
- **One trade at a time, confirmed back.** If the owner reports several, process each and summarize.
