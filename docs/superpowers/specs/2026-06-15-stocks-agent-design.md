# Stocks Agent — Design Spec

**Date:** 2026-06-15
**Owner:** Rajrupesh (rupesh.sivapu@gmail.com)
**Status:** Approved design, pending spec review → implementation plan

---

## 1. Purpose

A Claude Code–orchestrated personal investing assistant (the **`market-briefing`** skill) that
runs at a configurable cadence — in v1, every US-market weekday before the open — to produce ONE
simple, plain-English **brief + suggestion-only trade ideas** for a beginner, send it to the owner
**via Telegram**, and **log every call** for a self-improving track record. The skill is cadence-neutral and
mode-aware (v1 = pre-market; v2 adds intraday + on-demand), so it is not locked to "morning."

The owner is a **beginner investor**. The agent's job is to make him **better informed
and more disciplined**, not to trade for him.

### Hard rule (non-negotiable)
The agent is **suggestion-only and read-only by construction**. It has **no trade-execution
tools installed** (no Alpaca-trade, no robin_stocks, no order APIs). It therefore *cannot*
place, modify, or cancel a trade. The owner executes everything manually on Robinhood.
Autonomous execution may be revisited in a future version, behind explicit approval gates —
out of scope for v1.

### Portfolio awareness (Robinhood read-only in v1; NO execution)
Robinhood launched an official **Agentic Trading MCP** (May 2026) that bundles data access **and**
trade execution (`place_order`). For v1 we want the portfolio *data* but absolutely **not** the
execution capability (that would break the read-only-by-construction guardrail).

**v1 rule:** connect Robinhood **only via a genuine read-only scope** (portfolio/holdings data, no
trading verbs). If Robinhood does not offer a read-only-only connection (i.e. the only option bundles
`place_order`), we **do NOT connect it** and fall back to the manually-maintained
**`config/portfolio.json`**. Either way, holdings drive accurate hold/trim/sell advice, gain/loss
context, and over-concentration warnings — and the agent never gets a trading verb in v1.

The **full Robinhood Agentic Trading MCP (with execution)** is reserved for **Project 2**
(`stock-trader-bot`), behind its sandboxed sub-account + approval gates.

---

## 2. Architecture (Approach A: Claude Code–orchestrated)

Five layers:

| Layer | Responsibility | Implementation |
|---|---|---|
| **Data** | Quotes, fundamentals, earnings calendar, news + sentiment, technical indicators, **insider (Form 4) activity**, **whole-market screeners** | **Finnhub MCP** (primary, free 60 calls/min; insider transactions) + **Alpha Vantage MCP** (secondary, technical indicators + TOP_GAINERS_LOSERS) + yfinance MCP (fallback quotes + free predefined market screeners) |
| **Analysis** | Macro read, news synthesis, per-stock analysis, earnings/risk flags | **Our own orchestration skill** (the brain), reasoning through a **multi-role lens** (analyst → researcher → risk manager → portfolio manager), a pattern borrowed from TradingAgents. Borrows proven *patterns* from community skills but does not depend on them. |
| **Orchestration** | Encodes the owner's strategy, watchlist, output format, guardrails | Custom project skill `market-briefing` (`skills/market-briefing/SKILL.md`) |
| **Schedule** | Runs automatically pre-market | `/schedule` cloud routine, weekdays ~07:30 ET (06:30 Central) |
| **Delivery** | Sends the brief | **Telegram bot** (primary, v1) — reliable phone push, works without Gmail's OAuth friction. Email optional fallback. |

### Why these choices
- **Finnhub primary:** single free API covering news+sentiment+earnings+quotes at 60/min;
  far more reliable than yfinance, whose unofficial API lags and breaks without warning.
- **Own skill as brain:** community finance skills are experimental and low-adoption
  (e.g. ~16 stars) and run third-party logic in the owner's environment — a reliability
  and security risk. Building our own skill keeps the logic auditable and owned. Before any
  community skill is *referenced or installed*, its code is read and vetted.

---

## 3. Strategy encoded: 70/20/10 core-satellite

**Capital & stage (Year-1 foundation, $500/month):** the owner invests **$500/month** and is
building the base. The system is tuned to that reality: **Core ≈ $350/mo on autopilot** (DCA into
VOO/VTI), **Growth ≈ $100/mo into the single best pick** (not many names), **Speculative ≈ $50/mo to
an "opportunities fund" in LEARNING/paper mode** until it reaches ~$500, then it goes live. The
~$200/month income goal is honestly a **Year-2/3 target** (needs a real speculative bucket), not
Year 1. Cadence is therefore **weekly** (with monthly "money moves"), not daily — matched to the
capital and near-zero cost. Stored in `config/settings.json` (`strategy.stage`, `capital`, `schedule`).

**Selection strategy:** bucket-matched **multi-factor (Quality–Value–Momentum + catalyst)** —
Core favors Quality+Value, Growth favors Growth+Momentum, Speculative is catalyst/momentum at tiny
size. This mirrors proven approaches (Seeking Alpha quant factors, Motley Fool quality/value, IBD
CAN SLIM momentum) and the research that value & momentum work best as *separate sleeves* — which
the buckets already enforce. All gated by the multi-role lens; news/insider/sentiment are context,
never the sole reason.

The agent reasons inside three buckets (stored in `config/settings.json`, editable):

- **Core (70%)** — broad ETFs + a few large-cap stocks. Long-term buy-and-hold bias.
  Suggestions here are rare and high-conviction; agent nudges toward **dollar-cost averaging**
  rather than entry-timing.
- **Growth (20%)** — individual US stocks. Mix of long-term and swing ideas.
- **Speculative (10%)** — high-risk plays. Every suggestion carries loud risk warnings,
  a hard stop-loss, and small position sizing.

### Foundational guidance the agent will reinforce
1. Only invest money not needed for 3–5 years, *after* an emergency fund and high-interest
   debt are handled.
2. DCA the core; don't try to time it.
3. Position sizing and stop-losses are enforced, especially in the speculative bucket.

### Risk discipline (validated against trading-bot research, applied to suggestions)
The agent applies these as *discipline rules in its suggestions* (the owner acts manually; the
agent never executes). Stored in `config/settings.json`:
- **Daily loss limit** — if the owner's realized losses for the day exceed a set % of capital,
  the agent recommends stopping for the day rather than "revenge trading."
- **Circuit breaker** — after N consecutive losing suggestions, the agent recommends a pause and
  a review before issuing new speculative ideas.
- **Per-position size caps** per bucket. **Mandatory stop-loss** on every idea.
- **Explicitly rejected** from the bot reels: self-optimizing backtest loops (overfitting trap),
  day-trading/scalping strategies (off-strategy), and any autonomous execution.

---

## 4. Watchlist + whole-market scanner

There are **two** layers of coverage, and this is deliberate:

**(a) Watchlist** (`config/watchlist.json`) — tickers covered **in detail every day**. Seeded
**broad and sector-diversified across all 11 GICS sectors** + major ETFs (~31 core, ~12 growth),
so "all major tickers / all industries" are tracked closely. The **speculative** bucket is seeded
with a few **liquid, diversified higher-volatility thematic/sector ETFs** (e.g. SMH, ARKK, XBI) —
research-backed as appropriate satellites that give the bucket's high-upside character without
single-stock blow-up risk; individual speculative names come from the scanner + the owner's own
conviction. Owner edits freely. All are *things to watch*, not buy orders.

**(b) Market scanner** (`config/settings.json.market_scan`) — the agent **scans the entire US
market across every sector** each run for opportunities *beyond* the watchlist. Because free API
tiers can't deep-pull ~6,000 stocks, it uses a funnel: **broad pre-computed screeners** (Alpha
Vantage TOP_GAINERS_LOSERS; yfinance predefined screens) — ranked server-side, ~5–10 calls total —
narrow the whole market to a **shortlist (~10)**, which is then deep-analyzed through the
multi-role lens alongside the watchlist. A **quality/liquidity floor** (`universe_filters`: min
price/volume/market-cap) drops penny stocks and junk first, which improves results and cuts noise.

**(c) Self-curated radar** (`data/radar.json`, config `settings.json.radar`) — the agent
automatically maintains a **capped (≤15), auto-pruned** candidate list of names discovered by the
scanner/news. Names that keep showing relevance for several days become **promotion proposals** to
the watchlist — but the agent **never auto-edits `watchlist.json`**; the owner approves promotions.
This makes the system self-updating (it "learns" what to track) while staying bloat-, quota-, and
hype-safe, and keeping the owner in control of the deep list.

**Why this hybrid (watchlist + scanner) and not one or the other:** research into how others run
this confirms it. Even TradingAgents (84k stars) has *no* whole-market scanner — it runs on a
user-picked watchlist; retail best practice is explicitly *"analyze your watchlist AND let the
screener come to you"*; and AI-agent pros screen a *defined* universe then deep-dive. Watchlist
alone misses new names; scan alone loses continuity and surfaces junk. The hybrid funnel is the
validated approach.

**Cost note:** one run ≈ one agent session and ≤ ~70 data API calls; the scanner adds only ~5–10
screener calls, **never** a full-universe loop. See the plan's "API + quota budget per run" rules.

---

## 5. Suggestion format

Every buy/sell idea includes:

| Field | Meaning |
|---|---|
| Action | Buy / Sell / Trim / Add / Hold / Watch |
| Ticker | Symbol |
| Bucket | Core / Growth / Speculative |
| Entry zone | Suggested price range |
| Stop-loss | Hard exit on the downside |
| Target / exit | Upside target or exit condition |
| Position size | % of that bucket |
| Confidence | Low / Medium / High |
| Why | 2–3 line reasoning |
| Invalidation | What would make this thesis wrong |

This keeps the owner *learning the reasoning*, never blindly following.

---

## 6. Brief format — ONE simple message (beginner-first)

The owner is a beginner who wants something he understands at a glance, in plain English, with clear
symbols, no jargon, no repetition, one screen. So the brief is **a single short message**, not a
multi-section report. All the heavy machinery (scan, radar, insider check, multi-role reasoning, the
full §5 suggestion fields) runs **behind the scenes** and only its *results* surface as simple lines;
the full fields are logged for the track record.

The message blocks, in order:
- **🌅 Title** — Your Market Brief — <date>.
- **📈 Today** — one line: market up 🟢 / down 🔴 + a simple weekly **prediction/lean** (honestly a guess).
- **What I'd do today** — 3–7 lines, each = symbol + verdict + rough price + one plain reason
  (safety notes inline): ✅ Buy/Add · 🟢 keep adding slowly (core) · 🟡 hold/wait · 🛑 avoid/sell ·
  👀 watching.
- **💡 Why** — 1–2 kid-simple sentences on the main thing moving the market (only if it matters).
- **💼 Your money** — holdings status (or "none yet").
- **📊 My track record** — running accuracy % from the self-review loop (§ below).
- **💡 Tip of the day** — one tiny beginner concept (every day).
- **One-line disclaimer.**

The §5 suggestion fields are computed internally for each line and **logged** (`suggestions-log.jsonl`)
even though only the simple line is shown — that feeds the track record.

### Self-improving track record
Each run, the agent reviews its own past logged calls, scores recent ones (right/wrong vs price
since), computes an accuracy %, and **recalibrates** (more caution where it's been wrong). This is a
review-and-adjust loop ("learn from mistakes"), not model retraining. The headline % shows in the brief.

---

## 7. Guardrails

- **No execution tools exist** in the agent environment — read-only data MCPs only.
- Orchestration skill states explicitly: *"You may never place, modify, or cancel a trade.
  Output suggestions only."*
- **Every suggestion logged** to `data/suggestions-log.jsonl` (date, ticker, action,
  price-at-suggestion, confidence) for honest accuracy review months later.
- Disclaimer on every briefing.
- **Security gate:** every community skill or MCP server is code-reviewed before use.

---

## 8. Folder structure

```
stocks-agent/
  README.md                      # how it works, how to run, how to edit config
  docs/superpowers/specs/        # this spec + future specs
  config/
    watchlist.json               # tickers by bucket
    settings.json                # allocation (70/20/10), delivery prefs, schedule, risk params
  skills/market-briefing/
    SKILL.md                     # the orchestration brain + guardrails
  data/
    briefings/                   # dated archive of every briefing
    suggestions-log.jsonl        # every suggestion, for accuracy tracking
```

---

## 9. Build phases

- **Phase 1 (v1):** folder + config + orchestration skill + data MCPs → **manual run**,
  verify guardrails (confirm no trade tools), email a test briefing.
- **Phase 2:** enable the daily `/schedule` cloud routine (07:30 ET weekdays).
- **Phase 3 (fast-follow):** **intraday breaking-news watch** — a second scheduled cloud
  routine that wakes every ~60 min during market hours (09:30–16:00 ET), scans the watchlist +
  market for *material* moves/news above a configurable threshold, and alerts **only when
  something crosses the threshold** (no spam). Cadence tuned against real Pro-plan usage.
- **Phase 4 (optional):** Telegram delivery alongside email + monthly **suggestion-accuracy
  report** (postmortem loop — reads `suggestions-log.jsonl`, scores past calls).

### v2 candidate enhancements (designed after v1 runs ~1–2 weeks)
- Intraday breaking-news watch (Phase 3 above).
- Telegram delivery + suggestion-accuracy report (Phase 4 above).
- **Social sentiment** (Reddit / X / RSS) added to the news layer — with loud manipulation caveats.
- **Congressional + 13F trades** ("smart money" expansion beyond fresh insider data) — treated as
  periodic idea-lists, not daily signals, due to ~45-day disclosure delay.

**Future (owner's call only):** execution with approval gates — see the separate
`stock-trader-bot` project charter.

---

## 10. Runtime model (where & how it runs)

- **Cloud-scheduled, not local.** The `/schedule` routine runs on Anthropic's servers, so the
  owner's laptop does **not** need to be on/awake. (A local cron alternative was rejected: a
  laptop sleeps and would miss the 07:30 ET run.)
- **Machine requirements:** none meaningful. LLM reasoning runs server-side; the agent only
  orchestrates API calls. Any modern Mac is far more than sufficient; at run time (cloud
  schedule) the machine isn't involved at all.
- **Polling, not streaming.** The agent is not a continuously-running live feed. It *wakes on a
  schedule, acts, and sleeps.* "Real-time" = "checks every X minutes." Adequate for a
  long-term/swing investor; not intended for day trading.
- **Pro-plan budget awareness:** the morning brief is one run/day (cheap). The Phase 3 intraday
  watch polls during market hours and uses more quota, so its cadence (default ~60 min) and
  alert thresholds are configurable to stay within Pro limits.

### Data accounts needed (free, no brokerage, no payment)
- **Finnhub** — free account to obtain an API key (primary data).
- **Alpha Vantage** — free API key (technical indicators, secondary).
- **yfinance** — no account or key (free fallback).
- These are data-only credentials, entirely separate from Robinhood. The agent holds **no
  brokerage credentials** and has **no trading account access**.

### Cost model
- Cloud-scheduled runs have **no separate bill** — they run under the existing Claude **Pro
  subscription**, not pay-as-you-go.
- They are **not unlimited**: each run consumes Pro **usage quota** like a normal session.
- Morning brief = 1 run/weekday (light, fits Pro easily). Phase 3 intraday watch (~7 runs/day)
  is the quota-sensitive part — hence tunable cadence + threshold-only alerts.
- Data APIs (Finnhub / Alpha Vantage free tiers) are genuinely free, no card.
- Future option if intraday monitoring outgrows Pro limits: run on the Anthropic API
  (pay-as-you-go, per-token cost, separate from the subscription). Not needed for v1.

### Version control
- **No git for v1** — local-only on the owner's machine by request. May be pushed to git later.

## 11. Implementation model

- **Planning:** Opus (this session).
- **Implementation:** **Sonnet** (Pro plan) — the spec + plan are written to be
  self-contained so Sonnet can execute reliably.

---

## 12. Honest limitations

- Free data carries a 15–20 min delay — fine for long-term/swing, **not** for day trading.
- Must verify the scheduled cloud routine can reach Gmail + the data MCPs; if not, fallback
  is a one-command manual run each morning.
- **No system guarantees profit.** This maximizes informed, disciplined decisions and
  learning — the things that actually compound for a beginner.

---

## 13. Open items to confirm at build time

- Finnhub + Alpha Vantage free API keys (owner to create; agent will guide).
- Confirm Gmail MCP availability inside scheduled routines.
- Final starter watchlist tickers.
