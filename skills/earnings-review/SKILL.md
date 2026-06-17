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

## Data (read-only): yfinance primary, Finnhub secondary, Alpha Vantage backup
Prefer the read-only MCP tools; fall back to direct read-only HTTPS calls with the keys in
`config/secrets.local.json` if the tools aren't available. Never use a write/order endpoint.

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

## Rules
- Never invent numbers; if the free API lacks the latest quarter, say so and offer the paste option.
- Prices ~15-min delayed. If the lean is actionable, log it to `data/suggestions-log.jsonl` (same fields).
