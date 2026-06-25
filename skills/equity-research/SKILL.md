---
name: equity-research
description: On-demand plain-English research note on a US stock for Rajrupesh — re-checks the bull/bear thesis ("does my reason to hold still hold?") using read-only free data (Finnhub/Alpha Vantage/yfinance). Suggestion-only; NEVER executes trades.
---

# Equity Research — plain-English research note (on-demand)

Use when Rajrupesh asks "is <TICKER> still a good hold/buy?", "give me a research note on <TICKER>",
or "does my reason to own <TICKER> still hold?". Produce a SHORT, plain-English note a **beginner**
can act on. **Suggestion-only — you have no trade tools and must never place/modify/cancel a trade.**

## Inputs
- The ticker(s) the owner names. If it's a holding, read holdings via `lib.db.get_holdings()` for shares + avg cost.
- `config/settings.json` (buckets, risk, scoring) and `config/secrets.local.json` (keys via the MCPs).

## Data (read-only): yfinance primary, Finnhub secondary, Alpha Vantage backup
Pull: current quote (note ~15-min delay), key fundamentals (growth, margins, debt/cash, P/E), latest
news + sentiment, analyst ratings/price targets, insider activity, next earnings date. Prefer the
read-only MCP tools; fall back to direct read-only HTTPS calls with the keys in
`config/secrets.local.json` if the tools aren't available. Never use a write/order endpoint.

## Produce (ONE screen, plain English, no jargon)
**🔎 Research note — <TICKER> (~$price)**
- **What they do** — one kid-simple sentence.
- **Health score** — the 0–100 score + risk band (compute exactly as in the `market-briefing` skill).
- **Bull case** — 2–3 plain reasons it could go up.
- **Bear case** — 2–3 plain reasons it could go down (be honest; never hide the downside).
- **Deep Dive** — in plain beginner English: **Business model** (how they make money), **Moat** (top
  ~3 competitors + is the edge durable: patent / switching cost / network effect / cost structure),
  **Catalyst** (concrete events in the next 12 months), **Asymmetry** (valuation floor vs growth
  ceiling — is risk/reward skewed up, and why/why not?).
- **Peer relative-valuation** — pick ~2 sensible same-sector peers (say which) and show a small table:
  P/S (TTM + forward), P/FCF, EV/EBITDA, gross margin, YoY revenue growth, plus the **value/growth
  ratio = P/S TTM ÷ revenue growth %** (lower = more growth per dollar). Data from yfinance (Finnhub
  backup); mark partial / note any gaps — never invent.
- **Does the thesis still hold?** (holdings only) — compare to the original reason + avg cost →
  💎 still holds / 🟡 weakening / 🔴 broken, in plain words.
- **Verdict** — Buy / Hold / Trim / Avoid + confidence (Low/Med/High) + the ONE thing that would change it.
- *Footer:* "Not financial advice — you decide and place trades."

## Rules
- Never invent numbers or news; note any missing data + the fallback used.
- Context + reasoning, not a guarantee; always show what would prove the idea wrong.
- If the verdict is actionable, append a line to `data/suggestions-log.jsonl` (same fields as
  `market-briefing`, including the score fields).
