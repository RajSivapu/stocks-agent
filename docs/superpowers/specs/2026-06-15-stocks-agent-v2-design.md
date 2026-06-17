# Stocks Agent v2 — Design Spec (Enhancements)

**Date:** 2026-06-15
**Owner:** Rajrupesh (rupesh.sivapu@gmail.com)
**Status:** Scope locked. Detailed step-by-step plan intentionally deferred until v1 has run
~1–2 weeks, because several parameters need real usage data (see §7 Calibration).
**Builds on:** v1 (`2026-06-15-stocks-agent-design.md`). All v1 guardrails carry over unchanged:
**suggestion-only, read-only, no trade-execution tools.**

---

## 1. Purpose
Extend the v1 morning brief with: (a) intraday awareness of breaking events, (b) a second
delivery channel (Telegram), (c) accountability via a suggestion-accuracy report, and (d) richer
signals (social sentiment, congressional/13F "smart money"). None of these add execution; the
agent stays a read-only advisor.

---

## 2. Phase 3 — Intraday breaking-news watch

**Goal:** catch material mid-day events without spamming the owner.

- **Mechanism:** a *second* `/schedule` cloud routine that runs the **same `market-briefing` skill
  in `intraday` mode** (not a separate skill — one briefing brain, multiple cadences).
- **Window:** market hours only, 09:30–16:00 ET, weekdays.
- **Cadence:** default **every ~60 min** (calibrated — see §7).
- **What it scans each wake:** watchlist tickers + broad market for *material* changes —
  price move beyond a threshold, high-impact news, earnings surprise, trading halt.
- **Alert rule:** send **only when something crosses the threshold**. Quiet ticks send nothing.
- **De-duplication:** a small `data/intraday-state.json` records what's already been alerted on,
  so the same event isn't sent twice in a day.
- **Alert contents:** ticker, what happened, magnitude, a 1–2 line "why it matters," and a
  reminder that any action is the owner's manual decision.
- **Budget control:** cadence + thresholds are config-driven to stay within Pro usage limits.

**New/changed files:** `config/settings.json` (intraday cadence block), `data/intraday-state.json`,
and the `intraday` mode added to the existing `skills/market-briefing/SKILL.md` (no separate skill).

---

## 3. Phase 4a — Telegram delivery — ✅ MOVED INTO v1

Telegram is now the **primary v1 delivery channel** (decided 2026-06-16, given Gmail's OAuth
friction). No longer a v2 item. v2 only needs to extend it to **intraday alerts** (Phase 3) once
that exists.

---

## 4. Phase 4b — Deeper suggestion-accuracy report (postmortem)

**Goal:** honestly measure how good past calls were — the accountability piece + the gate for whether
Project 2 (auto-trader) is worth attempting.

**Note:** a *lightweight* version is already in v1 — each run reviews the log, computes a rolling
accuracy %, recalibrates caution, and shows the headline number in the brief. **v2 = the deeper
monthly report:** hit rate + average outcome **by bucket** (core/growth/speculative), best/worst
calls, and confidence-vs-outcome calibration (did "High confidence" calls actually do better?).

- **Cadence:** monthly `/schedule` routine. **Input:** `data/suggestions-log.jsonl`.
- **Method:** look up each past call's price move since the call date; score vs stated entry/target/stop.
- **Honesty rule:** report real numbers including losers; never massage results.

**New/changed files:** `skills/accuracy-report/SKILL.md` (new skill).

---

## 5. Phase 5 — Social sentiment (Reddit / X / RSS)

**Goal:** add crowd sentiment as an *extra* lens in the briefing.

- **Sources:** Reddit (e.g. investing subreddits), X, curated RSS finance feeds.
- **Use:** a small sentiment read per watchlist ticker, surfaced in the "What's driving the
  market" / "Your watchlist" sections.
- **Hard caveats (must be stated in output):** social sentiment is **noisy and easily
  manipulated** (pump-and-dump, bots). It is context only — never a buy reason on its own, and it
  must never outrank fundamentals/risk in the multi-role reasoning.

**New/changed files:** `config/settings.json` (sources + on/off), data fetch via an appropriate
read-only MCP or helper.

---

## 6. Phase 6 — Congressional + 13F "smart money" expansion

**Goal:** broaden the v1 insider (Form 4) signal into the full smart-money picture.

- **Congressional trades:** from a free source (Capitol Trades / Quiver free tier / FMP).
- **13F institutional holdings:** what large funds hold (SEC EDGAR / FMP).
- **Framing (must be stated):** both are **delayed up to ~45 days**, so they are
  **periodic idea-lists, not daily/timing signals.** Surface as a weekly/periodic "what is smart
  money accumulating" digest rather than a daily section. Never present as actionable timing.

**New/changed files:** `config/settings.json` (sources), additional data fetch, a periodic
digest section or a separate weekly routine.

---

## 7. On-demand companion skills (valuation + earnings review)

Inspired by Anthropic's finance agents (Model builder, Earnings reviewer), but rebuilt as **our own
skills on our free data** — the enterprise versions are waitlist/Enterprise-only and assume paid
data connectors (Moody's, broker research) we don't have. These are **on-demand** (the owner
invokes them when seriously considering or holding a stock), so they add **zero daily quota** and
are NOT part of the scheduled morning brief. Both stay **suggestion-only / read-only**.

**Build step 0 (before writing either):** evaluate the public
[`anthropics/financial-services-plugins`](https://github.com/anthropics/financial-services-plugins)
repo + the [financial skills cookbook](https://platform.claude.com/cookbook/skills-notebooks-02-skills-financial-applications).
Reuse their proven prompts/structure where possible, and confirm what is actually installable on a
**Pro** plan in Claude Code. Only rebuild what we can't reuse.

### 7a. `valuation` skill (model-builder-lite)
- **Goal:** judge whether a stock is cheap or overpriced before buying — the gap our suggestion
  engine doesn't fill.
- **Method:** pull fundamentals (Finnhub) → compute a simple **DCF** + **peer/comps multiples**
  (P/E, EV/EBITDA, P/S vs sector peers) → output a fair-value range vs current price, with the
  assumptions shown so the owner learns the math.
- **Caveats stated in output:** valuation is assumption-sensitive; show the inputs; it's a guide,
  not a guarantee. Never the sole buy reason — feeds the multi-role "analyst/researcher" passes.

### 7b. `earnings-review` skill
- **Goal:** after a company reports, decide in plain English whether the original reason to own
  still holds — or whether to trim/sell.
- **Method:** owner pastes the earnings-call transcript (or the skill pulls the report) → plain-
  English summary of what management actually said → compare against the owner's stated thesis for
  that holding → flag thesis-confirming vs thesis-breaking changes, with a Hold/Trim/Sell *lean*
  (suggestion only, with reasoning + confidence).
- **Ties in:** v1 already tracks earnings *dates*; this adds the *judgment* after the call.

## 8. CAN SLIM lens (analysis refinement for swing/growth)

**Goal:** give swing/growth ideas a battle-tested framework (William O'Neil's CAN SLIM, the method
behind IBD SwingTrader/MarketSurge) instead of ad-hoc reasoning. We **replicate the public
methodology on our free data** — we do NOT buy or integrate IBD's paid, closed products ($699/yr,
no public API).

- **Where it applies:** the market scanner's screen criteria + the multi-role "analyst" pass, for
  **growth and speculative** candidates (not the buy-and-hold core).
- **Criteria (computed from data we already fetch):**
  - **C** — current quarterly EPS growth ≥ ~25% (Finnhub fundamentals)
  - **A** — annual EPS growth ≥ ~25% over 3–5y (Finnhub fundamentals)
  - **N** — new highs / breakout from a base (Alpha Vantage near-52w-high)
  - **S** — supply/demand: volume confirmation (quotes + volume)
  - **L** — leader: relative strength rank high (computed from price vs market)
  - **I** — institutional/insider buying increasing (Finnhub insider/ownership)
  - **M** — overall market in an uptrend (our market snapshot)
- **Output:** a short CAN SLIM scorecard on growth/swing candidates in the briefing, so the owner
  sees *why* an idea is (or isn't) a quality swing setup. Still suggestion-only.
- **Free tools that already do CAN SLIM-style screening** (optional cross-check): Finviz free tier.

## 9. Calibration items — why the detailed plan is deferred
These need **real v1 data** before they can be set responsibly:
1. **Intraday cadence** (30 vs 60 vs 90 min) — depends on measured per-run Pro-quota cost from v1.
2. **Alert thresholds** (what % move / news level is "material") — depends on what the owner finds
   too-noisy vs too-quiet after reading real briefings.
3. **Accuracy-report metrics** — exact scoring/holding-period definitions tuned to the kinds of
   suggestions v1 actually produces.

When v1 has run ~1–2 weeks, run a short brainstorm to set these three, then write the v2
implementation plan under `docs/superpowers/plans/`.

---

## 10. Out of scope for v2
- Trade execution of any kind (that is Project 2, `stock-trader-bot`, paper-first, ~Sept 2026).
- Self-optimizing backtest loops, day-trading/scalping strategies (rejected in v1, still rejected).
