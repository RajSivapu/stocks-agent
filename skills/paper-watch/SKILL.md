---
name: paper-watch
description: Use when the owner wants to track his OWN hypothetical stock idea (paper trade) separately from real holdings and the agent's radar — "paper-watch X from $P", "how are my paper watches doing", "close my X paper watch".
---

# Paper Watch — track owner hypotheses (suggestion-only)

Lets the owner add, check, and close hypothetical stock ideas. Writes only to `paper_watches` — never to `holdings` or `transactions`, never places an order.

## ABSOLUTE RULE

**Suggestion-only.** This skill is hypothesis tracking only. It writes ONLY to `paper_watches` — never to the `holdings` or `transactions` tables, and never executes or simulates an order. All entries are hypothetical.

---

## Add a paper watch

**Trigger:** owner says things like "paper-watch NVDA from $200, thesis: GPU cycle has legs, track a month."

**Steps:**

1. **Parse inputs** from the owner's message:
   - `ticker` — uppercased stock symbol
   - `entry_ref_price` — use the stated price; if none stated, fetch live via `lib.marketdata.quote(ticker)`
   - `target_price` — optional, from message
   - `hypothetical_amount` — optional dollar amount (e.g. "$500 hypothetical")
   - `thesis` — the owner's reason/idea (required; ask if missing)
   - `horizon` — from message (e.g. "a month", "6 weeks"); if not stated, use `settings.paper_watches.default_horizon` from `config/settings.json`
   - `created` — today's date (YYYY-MM-DD)

2. **Snapshot the agent's current stance** for this ticker:
   ```python
   import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)).replace('/skills/paper-watch',''))
   import lib.db as db
   rows = db._rows("SELECT action,score FROM suggestions WHERE ticker=%s ORDER BY date DESC LIMIT 1", (ticker,))
   agent_view_at_open = rows[0]["action"] if rows else "no prior view"
   agent_score_at_open = rows[0]["score"] if rows else None
   ```

3. **Insert the row:**
   ```python
   pid = db.insert_paper_watch({
       "ticker": ticker,
       "created": created,
       "entry_ref_price": entry_ref_price,
       "target_price": target_price,          # None if not stated
       "hypothetical_amount": hypothetical_amount,  # None if not stated
       "thesis": thesis,
       "horizon": horizon,
       "agent_view_at_open": agent_view_at_open,
       "agent_score_at_open": agent_score_at_open,
   })
   ```

4. **Confirm in plain English** — one short paragraph: what was tracked, at what price, the thesis, the horizon, and whether the agent had a prior view on it.

---

## Status check

**Trigger:** "how are my paper watches doing" or "show paper watches."

**Steps:**

1. Fetch all open hypotheses:
   ```python
   import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)).replace('/skills/paper-watch',''))
   import lib.db as db
   import lib.marketdata as mkt
   watches = db.get_active_paper_watches()
   ```

2. Mark-to-market each entry:
   - Fetch `current_price = mkt.quote(row["ticker"])`
   - Compute `return_pct = (current_price - row["entry_ref_price"]) / row["entry_ref_price"] * 100`
   - If `hypothetical_amount` is set, compute `return_dollars = hypothetical_amount * return_pct / 100`

3. **Report** as a plain-English list (one entry per watch):
   - Ticker, entry price, current price, return % (and $ if amount set)
   - Thesis (brief)
   - Agent's view at open vs agent's current view (fetch the most recent suggestion row again if wanted, or just show what was stored)
   - Days open (today minus `created`)
   - You vs agent: whether the move so far agrees or disagrees with the agent's view at open

---

## Close a paper watch

**Trigger:** "close my NVDA paper watch" or "close paper watch X."

**Steps:**

1. Fetch active watches and find the matching ticker:
   ```python
   import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)).replace('/skills/paper-watch',''))
   import lib.db as db
   import lib.marketdata as mkt
   from datetime import date
   watches = db.get_active_paper_watches()
   row = next((w for w in watches if w["ticker"] == ticker.upper()), None)
   ```

2. Fetch the current price and close the watch:
   ```python
   close_price = mkt.quote(row["ticker"])
   db.close_paper_watch(row["id"], close_price=close_price, closed_date=date.today().isoformat())
   ```

3. **Report final hypothetical P&L** in plain English:
   - Entry price → close price → return %
   - If `hypothetical_amount` was set, show the dollar gain/loss
   - Whether the outcome matched or contradicted the agent's view at open
   - One-sentence takeaway (did the thesis play out?)

---

## Interpreter / sys.path

- **Locally:** `.venv/bin/python`
- **Cloud Routine:** `python`
- All inline code snippets include the `sys.path` bootstrap:
  `import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)).replace('/skills/paper-watch',''))`
  so `lib.*` imports resolve correctly from any working directory.
