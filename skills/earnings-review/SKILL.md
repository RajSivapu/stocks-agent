---
name: earnings-review
description: On-demand earnings digest for a US stock for Rajrupesh — summarizes the latest quarter (actual vs expected EPS/revenue, guidance, market reaction) from free data, and gives a deeper call digest if the owner pastes a transcript. Suggestion-only; NEVER executes trades.
---

# Earnings Review — what changed last quarter (on-demand)

Use when Rajrupesh asks "how was <TICKER>'s earnings?" or pastes an earnings call transcript.
**Suggestion-only — no trade tools; never place/modify/cancel a trade.**

## Two modes
1. **Results digest (default, FREE data):** pull the latest reported quarter from Finnhub (EPS actual
   vs estimate, revenue, surprise %), recent guidance/news, and the price reaction.
2. **Transcript digest (only if the owner pastes a transcript):** read the pasted text and summarize
   the call. Full transcripts are paid via API, so this mode relies on the owner pasting one — if
   asked for a call digest without a transcript, do the results digest and offer the paste option.

## Data (read-only): use the helper library
Use `lib.fundamentals` (Finnhub `metric` / `company_news` / `earnings_dates`) and `lib.marketdata`
(`quote` / `history` / `indicators`) — they read keys via `lib.config.secret()` (env-first) and use
read-only HTTPS only. A read-only MCP / `yfinance` library may be used if available, but the helpers are
the reliable baseline. Never use a write/order endpoint.

## Produce (ONE screen, plain English, no jargon)
**📞 Earnings review — <TICKER> (quarter, date)**
- **Did they beat?** — EPS actual vs expected + revenue actual vs expected, plainly (✅ beat / ⚠️ miss / ➖ in line).
- **Guidance** — what they said about the future, simply.
- **Market reaction** — how the stock moved after, and why.
- **What it means for the thesis** — 1–2 plain sentences: stronger or weaker case?
- **Bear-case check** (best-effort on free data; cite source, note when unverifiable) — flag any of:
  **guidance cut** vs prior, **margin compression** quarter-over-quarter, a widening
  **GAAP-vs-non-GAAP gap**, and (if a transcript was pasted) unusual hedging/tone. End with the
  **invalidation level** — the price/condition that would break the thesis (= the stop / "what would
  prove me wrong").
- **(If a transcript was pasted)** 3–5 bullet highlights from the call (themes, tone, risks).
- **So what** — Hold / Buy more / Trim / Avoid lean + confidence + what to watch next quarter.
- *Footer:* "Not financial advice — you decide and place trades."

## Record the earnings reaction (per-stock memory)
After the digest, write ONE `stock_observations` row so the agent remembers how this name behaved at
earnings (re-read by `market-briefing` when it next analyzes the stock):
```python
lib.db.insert_observation({"ticker":T,"obs_date":"YYYY-MM-DD","event_type":"earnings",
  "summary":"Q_ beat/miss EPS x vs y, rev …; guidance …","price_reaction":"+6.2% next day",
  "confidence":"high","source":"finnhub+earnings-review"})
```
Keep it factual and short; never invent the numbers. This builds the seasonality/event memory over time.

## Rules
- Never invent numbers; if the free API lacks the latest quarter, say so and offer the paste option.
- Prices ~15-min delayed. If the lean is actionable, persist it via `lib.db.insert_suggestion({...})`
  (same fields as `market-briefing`'s Logging section).
