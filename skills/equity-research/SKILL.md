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
- `config/settings.json` (buckets, risk, scoring). Data helpers read credentials from environment;
  never open a local secrets file directly.

## Data (read-only): yfinance primary, Finnhub secondary, Alpha Vantage backup
Pull: current quote (note ~15-min delay), key fundamentals (growth, margins, debt/cash, P/E), latest
news + sentiment, analyst ratings/price targets, insider activity, next earnings date. Prefer the
project helper library; use another read-only source only when available and never expose a key.
Never use a write/order endpoint.

Also pull `lib.edgar.recent_filings(ticker)` and `lib.edgar.ownership_filings(ticker)` (SEC EDGAR
— free, no key). Both degrade to `[]` silently on any lookup failure (unmapped ticker, network
issue) — never invent a filing or treat an empty list as "nothing has happened," just omit the
note (see Produce section below for exactly when to surface each).

## Produce (ONE screen, plain English, no jargon)
**🔎 Research note — <TICKER> (~$price)**
- **What they do** — one kid-simple sentence.
- **Health score** — the 0–100 score + risk band (compute exactly as in the `market-briefing` skill).
- **Bull case** — 2–3 plain reasons it could go up. If `ownership_filings()`'s newest entry is an
  INITIAL filing (`SC 13D` or `SC 13G`, not an amendment) within ~90 days of today, add it as a
  bull point: "New 5%+ stake disclosed via [form] on [date]" (13D = investor stated intent to
  influence, often an activist catalyst; 13G = passive institution accumulating). Otherwise say
  nothing about ownership.
- **Bear case** — 2–3 plain reasons it could go down (be honest; never hide the downside). If
  `ownership_filings()`'s newest entry is an AMENDMENT (`SC 13D/A` or `SC 13G/A`) within ~90 days,
  add a neutral note (not framed as bullish or bearish — direction isn't in the raw data): "A
  previously disclosed 5%+ holder amended their filing on [date]." Always caveat any EDGAR-derived
  note as a periodic, lagging disclosure — never present it as a timing signal.
- **Deep Dive** — in plain beginner English: **Business model** (how they make money), **Moat** (top
  ~3 competitors + is the edge durable: patent / switching cost / network effect / cost structure),
  **Catalyst** (concrete events in the next 12 months — if `recent_filings()`'s newest entry is
  within ~10 days of today, note it here, e.g. "8-K filed 3 days ago — check for material news";
  otherwise say nothing about filings), **Asymmetry** (valuation floor vs growth ceiling — is
  risk/reward skewed up, and why/why not?).
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
- If the verdict is actionable, persist one row with `lib.db.insert_suggestion({...})` using the
  `market-briefing` Logging fields, including `evidence_as_of` from the quote. Never write a local
  suggestion log file.
