# Project 1 — "Rigorous Mode" Analysis Upgrade (design spec)

**Date:** 2026-06-17
**Project:** `stocks-agent` (Project 1 — suggestion-only market-briefing assistant)
**Status:** Design approved by owner 2026-06-17; ready to turn into an implementation plan.
**Scope:** An upgrade to the EXISTING `market-briefing` skill + `config/settings.json`. NOT a new
project, NOT autonomous trading. The agent stays **suggestion-only / read-only by construction** —
the no-execution guardrail is untouched.

---

## 1. Why

Owner reviewed reels pitching TradingAgents / OpenBB / Paperclip and asked: can we use the
*strategy* those tools use to make Project 1 "more rigid" so the agent is well-versed and gives the
best investing advice for his situation (beginner, $500/mo, Year-1 foundation, 70/20/10).

Findings that shape this spec:
- **TradingAgents has a *method*, not a magic strategy** — a structured multi-agent deliberation
  (specialist analysts → Bull-vs-Bear debate → risk review → final call, with memory). Project 1
  already borrows a compressed version (the Analyst→Researcher→Risk→Portfolio "multi-role lens").
  This spec **formalizes** that method.
- **OpenBB is a *data* layer, not a strategy.** Its rigor contribution is consistent/standardized
  inputs. Deferred (local server, won't run in a cloud schedule); not required to start. Noted as a
  future option.
- **Paperclip** — irrelevant (agent-org orchestration, no trading). Rejected.

The honest framing the owner accepted: for a $500/mo DCA beginner, "best advice" comes from a
**rigorous reasoning method applied with depth where money actually moves**, **consistent data**, and
**memory that compounds** — NOT from brute-force debating every stock daily (diminishing returns,
false precision, overtrading risk).

## 2. Goal

Make Project 1 "more rigid" across three dimensions the owner selected (all three):
1. **Stronger reasoning** — an explicit, written, logged Bull-vs-Bear debate + risk-veto before any call.
2. **Fewer, safer calls** — a confidence/risk gate that demotes weak ideas to "watch" instead of suggesting them.
3. **More consistent scoring** — reproducible Health Score (0–100) via fixed input definitions + fallback order.

Non-goals: trade execution (ever, in Project 1), OpenBB adoption (future option), a trained
price-prediction model (out of scope per prior decisions — this stays memory + self-review).

## 3. Design

### 3.1 Structured deliberation method (replaces the quick mental "bull vs bear")
For each analyzed name the skill runs a structured, **internal + logged** deliberation:
1. **Specialist passes** — quick explicit reads: fundamentals · technicals (computed locally) · news/sentiment.
2. **Bull vs Bear** — strongest points each side, then the **decisive factor** that breaks the tie.
3. **Risk gate (can VETO)** — position size vs `risk.max_position_pct_of_bucket`, mandatory stop-loss,
   daily-loss-limit + circuit-breaker status, concentration vs the 70/20/10 target. A weak idea **dies here**.
4. **Verdict + confidence (Low/Med/High) + "what would prove me wrong."**

### 3.2 Two depths (every name scrutinized, stays in budget)
- **Full multi-round** debate → **money-moves** (the month's growth pick + 2–3 runner-ups), any
  **buy/trim/sell**, any watchlist **promote/retire**, and **every name the owner holds** (`portfolio.json`).
- **Compact one-round** structured pass (1 bull · 1 bear · 1 risk flag) → **every other** watchlist +
  scan-shortlist name, reusing data already pulled — **no extra API calls**.
- Result: nothing is skipped; depth concentrates where real money is at stake.

### 3.2a Full-depth analysis checklists (folded from owner's 3-prompt reel, 2026-06-17)
These concrete checklists run on **full-depth** names only (money-moves + holdings) and also power the
on-demand `equity-research` / `earnings-review` skills (where there's no daily-budget pressure). They
make the deliberation method (3.1) concrete; they do NOT change the architecture or the guardrail.
Honesty note: the reel's performance claims are survivorship-bias marketing — these are kept purely
as analysis *structure*. They suit the Growth pick + holdings mindset, NOT the Core 70% DCA.

1. **Deep Dive** (feeds the specialist + bull passes):
   - **Business model** — how they make money, core product, in plain English (beginner tone).
   - **Moat** — top ~3 competitors; durable edge? (patent / switching cost / network effect / cost structure).
   - **Catalyst** — concrete launches / earnings / regulatory events / partnerships in the next 12 months.
   - **Asymmetry** — valuation floor vs growth ceiling: is the risk/reward skewed up, and why/why not?
2. **Peer relative-valuation** (feeds the valuation sub-score, 3.5) — the agent picks ~2 sensible
   same-sector peers (and says which) and builds a small table: P/S (TTM + forward), P/FCF, EV/EBITDA,
   gross margin, YoY revenue growth, plus a transparent **value/growth ratio = P/S TTM ÷ revenue
   growth %** (lower = more growth per dollar of valuation). Affordable because it's full-depth-only
   (the name + ~2 peers). Data from yfinance (with Finnhub backup); mark partial / note gaps.
3. **Bear Case** (supercharges the bear pass + risk gate) — adopt a skeptical short-seller stance and
   surface the **3 most serious red flags, ranked by severity, with sources**, checking: customer
   concentration (>25% of revenue, from latest 10-K), margin compression, unusual insider selling,
   widening GAAP-vs-non-GAAP gap, guidance cuts in the last 12 months. Produce an explicit
   **invalidation level** (the price/condition where the thesis breaks) → this IS the stop-loss /
   "what would prove me wrong." Filings-derived items (10-K concentration, GAAP gap) are **best-effort
   on free data**: cite the source, and note honestly when an item can't be verified.

### 3.3 Confidence / risk gate (the "rigid" dial)
- A **buy** is suggested only at **Medium-or-higher** conviction *after* the debate.
- **Low** conviction → **demoted to "watch,"** never suggested as a buy.
- The **risk gate (3.1 step 3) can override** the debate outcome entirely (veto or downgrade).
- Net effect: fewer suggestions, higher bar, less noise — by design.

### 3.4 Data layer swap (removes the AlphaVantage bottleneck)
`config/settings.json.data` changes so broad coverage is feasible on free data:
- **yfinance = primary** — quotes, price history, fundamentals, predefined screeners. No hard daily cap.
- **Technical indicators computed locally** (RSI, MACD, moving averages) from yfinance price history —
  removes dependence on AlphaVantage's indicator calls.
- **Finnhub** — fundamentals (`stock/metric`), company news + sentiment, earnings dates (60/min free).
- **AlphaVantage = optional backup** only (its 25/day cap was the binding limit; demoted).
- Works in a cloud schedule (no local server required).
- The skill's "Data sources" + "API + quota budget" sections are updated to match (still: never loop
  the full universe; use pre-computed screeners; pace within Finnhub's 60/min).

### 3.5 Consistent Health Score (0–100)
- Fix the **input definitions and a fixed fallback order** for each sub-score (growth / financial
  health / valuation) so scores are reproducible run-to-run, sourced consistently from yfinance/Finnhub.
- Keep existing rules: PEG-style valuation (high P/E forgiven by strong growth), ETFs tagged
  "ETF — diversified" (not scored), **partial** score when an input is missing, never invent numbers.
- The **valuation sub-score** is grounded in the **peer relative-valuation** step (3.2a #2) — the
  peer table + value/growth ratio inform it. Kept as ONE headline Health Score (no competing scores).
- Achieved WITHOUT OpenBB (OpenBB would only further standardize; future option).

### 3.6 Brief format (unchanged) + logging (enriched)
- **Brief format is unchanged** — debates run **behind the scenes**; the message stays the
  one-screen, beginner, Telegram-HTML, bold-headers/sub-labels/tickers format with "why it matters"
  teaching clauses. Only the *quality* behind each line improves.
- **Logging** — each `data/suggestions-log.jsonl` entry gains the debate's key fields (e.g.
  `bull`, `bear`, `decisive_factor`, `risk_verdict`, plus existing `confidence`/score fields) so the
  track-record self-review and `data/learning.md` compound from richer history.

## 4. Config changes (`config/settings.json`)
- `data`: reorder to `primary: yfinance`, `secondary: finnhub`, `fallback/optional: alphavantage`;
  add a flag indicating indicators are computed locally.
- Add a `rigor` block: enable structured debate; depth tiers (full vs compact) and which categories
  get full depth; the confidence threshold for suggesting a buy (Medium+).
(Exact field names finalized in the implementation plan.)

## 5. Files touched
- `skills/market-briefing/SKILL.md` — main changes (deliberation method, depth tiers + full-depth
  checklists, confidence gate, data-source + budget sections, scoring consistency, logging fields).
- `skills/equity-research/SKILL.md` — adopt the Deep Dive (3.2a #1) + peer relative-valuation (3.2a #2)
  checklists for the on-demand research note.
- `skills/earnings-review/SKILL.md` — adopt the Bear-Case red-flag checklist (3.2a #3) elements
  (guidance cuts, margin compression, GAAP-vs-non-GAAP) where transcript/results data allows.
- `config/settings.json` — `data` reorder + `rigor` block.
- No new infrastructure. OpenBB noted as a future option only.

## 6. Acceptance criteria
- Every analyzed name receives a structured bull/bear/risk pass (full depth for money-moves +
  holdings; compact for the rest), produced internally and recorded in the log.
- No buy is suggested below Medium conviction; sub-threshold ideas appear as "watch."
- The risk gate can and does veto/downgrade ideas (verifiable in the log on a real run).
- A real run completes within the existing budget (≤ ~70 data calls, one session) using
  yfinance-primary data with locally-computed indicators; AlphaVantage not required.
- Health Score sub-scores use fixed definitions/fallback order; ETFs tagged diversified; partials marked.
- On a full-depth name, the log shows the reel-sourced checklists: Deep Dive (business/moat/catalyst/
  asymmetry), a peer relative-valuation table + value/growth ratio, and a ranked Bear-Case red-flag
  list with sources and an explicit invalidation level (= the stop-loss). Best-effort items note gaps.
- The delivered Telegram brief is still one screen and beginner-friendly (format unchanged).
- Guardrail intact: no execution tools; agent remains suggestion-only/read-only.

## 7. Out of scope / future
- OpenBB local data layer (richer transcripts/estimates/macro) — future option; needs a local server.
- FMP free key (250/day standardized fundamentals) — possible middle-ground later.
- Any execution/autonomy — Project 2 (`stock-trader-bot`) only, behind its charter gates.
- Reel-sourced ideas reviewed so far are folded in (3.2a). Any further reels the owner shares will be
  evaluated and added before the plan is finalized, if they fit.
- **TimesFM (Google time-series model) — evaluated 2026-06-17, REJECTED for Project 1.** It is a
  ~200M-param forecasting model (the "100B" is training *time-points*, not params), state-of-the-art on
  *general* series but it **"fails drastically" zero-shot on stock prices**, underperforms plain
  CatBoost/LightGBM, and even fine-tuned manages only ~3.6% annual S&P return in mock trading (worse
  than buying the index). Confirms the locked "no trained price-prediction model" decision (returns are
  near-efficient/noise-dominated). Would also add heavy ML infra that won't run in a cloud schedule.
  Sole *possible* future use = forecasting **volatility** (not price) to inform risk sizing — an
  optional experiment for Project 2's paper-trading lab, tested vs baselines, never real money, not here.
- **HKUDS/AI-Trader (ai4trade.ai) — evaluated 2026-06-17, REJECTED for both projects.** Branded
  "fully-automated agent-native trading" but is really a social copy-trading / crowd-signals platform
  (leaderboards, one-click copy, gamified reward points for posting signals) with live broker execution.
  No backtests / walk-forward / out-of-sample / performance attribution and no actual strategy code
  (see repo Issue #207); the "skills" just register with and post signals to ai4trade.ai. Crowd-signal
  copy-trading is zero/negative-alpha for followers after costs; live execution violates Project 1's
  guardrail; third-party platform lock-in conflicts with local-only. No net-new value vs what we have.
