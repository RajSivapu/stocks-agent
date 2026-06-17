# Project 1 v2 — "Autonomous Daily Operator" (design spec)

**Date:** 2026-06-17
**Project:** `stocks-agent` (Project 1 — personal, suggestion-only investing assistant)
**Status:** Design — under owner review (brainstormed + decided 2026-06-17). Not yet planned/implemented.
**Builds on:** v1 (3 skills + local config) and v1.5 "Rigorous Mode" (structured debate, confidence
gate, yfinance-primary data, enriched logging) — both already shipped and live-verified.

---

## 1. Why / vision

Turn the personal stock agent from a *manually-run morning brief* into a **self-running daily
operator** that the owner never has to re-plan: it runs itself on a schedule, learns from its own
track record, remembers how each stock behaves over time, and pings the owner with specific,
late-look-friendly actions — while staying **100% suggestion-only** (it never trades).

The owner's words that shaped this: stop repeating the monthly pitch every day; deploy growth money
*when a good setup appears*, not all on day one; give entry *zones* (not stale prices) because he may
read alerts late; close the loop on what he actually bought; prove the strategy works and keep a
record of what didn't; remember recurring patterns (e.g. how AAPL behaves around iPhone launches);
and run forever without him coming back to plan.

## 2. Autonomy boundary (the load-bearing decision)

- **Autonomous in OPERATION — yes.** Runs on schedule, pulls data, reasons, decides suggestions,
  updates its own memory, grades itself, adapts. No human in the loop per run.
- **Autonomous in EXECUTION — no, by design.** It has no trade tools and never places/modifies/cancels
  an order. The owner executes on Robinhood and reports back. (Rationale: the locked suggestion-only
  guardrail + evidence that retail auto-bots underperform; auto-execution is the separate, gated
  `stock-trader-bot` Project 2.)
- **Two remaining human touchpoints:** (a) the owner **reports trades** so P&L is accurate; (b) the
  owner can **veto/adjust** anything. Everything else is hands-off.

## 3. Architecture

```
                ┌────────────────────────────────────────────┐
                │  PRIVATE GitHub repo  (code + editable cfg) │
                │  • skills/ + runner code                    │
                │  • config/settings.json, watchlist.json     │
                │  • data/lessons.md  (readable narrative)     │
                └───────────────▲────────────────────────────┘
                    clone (pull) │   (no DB file in git)
                ┌────────────────┴───────────────────────────┐
  cron  ──────► │   Anthropic Cloud Routine  (EPHEMERAL run)  │
  06:30 brief   │   1. clone repo (code + config)             │
  10:30/13:30   │   2. run market-briefing skill (Claude)     │
  15:10  EOD    │   3. connect to Postgres over network       │
                │   4. read state · fetch data · write rows   │
                │   5. send Telegram                          │
                └──┬───────────┬────────────────┬────────────┘
   env secrets     │  allowlist │ HTTPS          │ TLS
   (keys, token,   ▼            ▼                ▼
    PG conn str) ┌──────────────────┐ ┌──────────────┐ ┌──────────────────────┐
                 │ Yahoo + Finnhub  │ │ Telegram Bot │ │ Managed Postgres      │
                 │ (market data)    │ │ API → phone  │ │ (Supabase/Neon free)  │
                 └──────────────────┘ └──────────────┘ │ • holdings + txns     │
                                                        │ • suggestions+grades  │
   Reconcile (you, anytime):                            │ • per-stock behavior  │
     Claude session ──"bought 1 NVDA @207"────────────► │ • daily snapshots     │
       inserts a transaction row                        └──────────────────────┘
```

**Components & responsibilities**
- **Private GitHub repo** — single home for the *code* + *human-edited config* (`settings.json`,
  `watchlist.json`) + the *readable narrative* `lessons.md`. Cloud Routines clone a repo each run, so
  the code must live here. (This consciously reverses the prior "local-only, NO git" rule — *for the
  code*. Secrets never go here.)
- **Anthropic Cloud Routine** — the ephemeral runtime. Clones the repo, runs the `market-briefing`
  skill headless on the owner's Pro plan, connects to Postgres + external APIs + Telegram, exits.
  Nothing persists in the runtime between runs; all state lives in Postgres (or the repo for config).
- **Managed Postgres (Supabase or Neon, free tier)** — always-on store for all structured, growing,
  queryable state. Concurrency-safe (no binary-in-git problem); the same DB a future MarketPal web
  app would read.
- **Secrets** — API keys, Telegram bot token, and the Postgres connection string live in the
  **Routine's env-var/secret store**, never in the repo.
- **External read-only APIs** — yfinance/Yahoo (primary: quotes, history, screeners; indicators
  computed locally), Finnhub (fundamentals, news, earnings, insider). Reached via a **Custom network
  allowlist** (default cloud network is blocklisted).
- **Telegram** — delivery to the owner's phone (unchanged bot).
- **Reconciliation path** — the owner tells a Claude session pointed at the same repo/DB what he
  traded; it writes holdings + transaction rows to Postgres; the next scheduled run sees the real
  position.

## 4. Runtime & hosting

- **Platform:** Anthropic Cloud **Routines** (`/schedule`), Pro plan, headless (no approval prompts).
- **Network:** set the Routine environment to **Custom** and allowlist: `finnhub.io`,
  `query1.finance.yahoo.com` (+ `query2`), `api.telegram.org`, `github.com`, and the Postgres host.
- **Secrets:** provided as environment variables in the Routine environment.
- **State persistence:** Postgres (structured) + repo files (config + lessons). No reliance on local
  laptop files. Each run is a fresh clone.
- **Concurrency rule:** writes to Postgres are effectively single-writer (scheduled runs at fixed
  times + occasional reconciliation); use transactions; safe for one user.

## 5. Cadence & run types (owner-chosen)

All times the owner's local (Central). Brief reference time is 07:30 ET = 06:30 CT.

| Run | Time (CT) | Weight | What it does |
|---|---|---|---|
| **Pre-market brief** | 06:30 | Heavy | Full brief. Monthly-plan brief on the 1st weekday of the month; daily-status brief otherwise. Runs the full Rigorous-Mode pipeline (scan + watchlist + debates + scoring). |
| **Intraday check** | 10:30 | Light | Scoped to **open entry-zones + current holdings only**. Sends Telegram **only if** a buy zone triggered or a holding hit its invalidation. Silent otherwise. |
| **Intraday check** | 13:30 | Light | Same as 10:30. |
| **Post-market analysis** | 15:10 | Medium | Records how each watched/held name *actually behaved* vs expectation → updates per-stock observations + lessons. The "learn the stock" run. Quiet unless something needs the owner. |

Token-leanness is a hard requirement (see §11): intraday/EOD runs read only the relevant slice, so
the chosen cadence is ~2.5–3× one full run, not 4×.

## 6. Data model

**Postgres (structured, growing, queryable):**
- `holdings` — current positions: ticker, shares, avg_cost, bucket, opened_at, notes.
- `transactions` — append-only ledger: ts, ticker, side (buy/sell), qty, price, source (owner-reported).
- `suggestions` — every call with full reasoning: ts, ticker, action, bucket, depth, entry_zone_low,
  entry_zone_high, valid_until, stop/invalidation, target, confidence, bull, bear, decisive_factor,
  risk_verdict, score + sub-scores, score_inputs, score_partial.
- `suggestion_grades` — outcome grading: suggestion_id, graded_at, result (right/wrong/partial),
  price_then, price_later, horizon, note.
- `stock_observations` — per-stock behavior/seasonality memory: ticker, date, event_type
  (earnings/product-launch/macro/big-move), what happened (price reaction), confidence, source.
- `daily_snapshots` — light daily record per tracked name (close, key indicators, day move) to
  support behavior learning without re-deriving everything. (Raw OHLC is NOT stored — re-fetched.)
- `dry_powder` — running cash buckets: month, growth_available, spec_available, rolled_months.

**Repo files (human-edited / human-read):**
- `config/settings.json`, `config/watchlist.json` — owner edits these.
- `data/lessons.md` — readable "what worked / what didn't" narrative + market-regime log.

**Memory principle (critical for cost): RETRIEVE, don't DUMP.** Postgres may grow to years of data,
but each run queries only the **relevant slice** (names in scope, recent lessons, that stock's
observations). Token-per-run stays roughly flat as history grows.

## 7. Two brief types

- **Monthly-plan brief** (1st weekday of month) — the *only* one that lays out "here's the plan":
  Core DCA amount + fund mix (justified), the month's growth/spec **dry-powder budget**, and the
  setups to watch for. Includes the **monthly scorecard** (how last month's calls did, what was
  learned, what's changing).
- **Daily-status brief** (other weekdays) — **portfolio-first**: holdings, each position's price vs
  the owner's cost, total value, up/down. Surfaces an **action only when there's a real one** (a
  watched zone entered, or a holding hit invalidation). Nothing to do → a 3-line "all quiet" + one
  teaching line (keeps the daily learning habit without noise).
- **Format unchanged:** the delivered Telegram message stays the one-screen, beginner, HTML,
  bold-headers/sub-labels/tickers, "Why it matters:" style from v1/v1.5. All debate/scoring/learning
  runs behind the scenes.

## 8. Money model

- **Core (~70%): auto-DCA**, fixed monthly amount, on a set day. Default mix = mostly **VOO** + a
  small **VXUS** (international) + **SCHD** (dividend) tilt — justified in the monthly brief, so it's
  not "why only VOO." (Owner can keep it pure VOO; mix is config-driven.)
- **Growth + speculative (~30%): dry powder.** Held as cash; deployed **only** when a setup clears the
  Rigorous-Mode gate (Medium+ conviction, risk gate passed) **and** price is in its entry zone.
- **Roll ≤2 months, then DCA to Core.** If no qualifying setup appears, growth cash waits up to ~2
  months, then the agent tells the owner to put it into Core so money isn't idle forever.

## 9. Entry zones (late-look-friendly)

Every buy idea carries: a **buy zone** ("buy under $210, ideal near $195"), a **valid-until**
("good through Friday or until it closes above $215"), and the **invalidation/stop** (= the
Bear-Case invalidation level). The intraday checks re-evaluate open zones against the live price and
tell the owner if he's still in range.

## 10. Trade reconciliation flow

- The owner tells a Claude session on the repo/DB what he did: "bought 1 NVDA @ 207", "skipped it",
  "sold 2 AMD". The agent parses it, updates `holdings` + appends to `transactions` in Postgres.
- This drives **real P&L** in the daily brief and lets the agent grade the owner's *actual* outcomes
  (not just hypothetical suggestions).
- If the owner doesn't report, the agent assumes no change and periodically asks "did you act on X?"

## 11. Learning & verification ("combination of all")

1. **Log every call** (Postgres `suggestions`) with full reasoning.
2. **Grade each call** vs what the stock actually did (`suggestion_grades`) → accuracy by bucket.
3. **`lessons.md`** — dedicated, honest, dated, falsifiable "what worked / what didn't" ledger, **read
   every run** to temper confidence and choices.
4. **Per-stock behavior + seasonality memory** (`stock_observations`) — recurring patterns (e.g.
   "AAPL tends to move around Sept iPhone launches") recorded by the post-market run and **read when
   that stock is next analyzed**. Treated as a *hypothesis* (n=1) that strengthens over years; the
   agent stays skeptical of well-known patterns that may be priced in.
5. **Invalidation-triggered reassessment** — when a holding/idea hits its invalidation level, the
   agent stops defending the old thesis and reasons from the stock's actual behavior (trim/exit/hold).
6. **Gated numeric auto-tuning** — only after ~50 graded calls in a bucket may the agent adjust
   numeric parameters (sizing, score weights); even then transparently, conservatively, logged to a
   changelog, reversible. Judgment-only until then.
7. **Monthly scorecard** in the 1st-of-month brief.

Honest framing: this is **memory + self-review**, not a trained price-prediction model. It learns
patterns and its own biases — real but slow (small sample), never a crystal ball.

## 12. Historical preload (one-time, "full")

A one-time backfill job, bounded to the ~46 watchlist names, seeds Postgres so the agent has context
from run #1.

- **Solid free half (price-derived):** from yfinance multi-year history + Finnhub earnings dates,
  compute per name: **seasonality** (monthly/seasonal tendencies), **volatility** (for sane sizing),
  typical **drawdowns**, and **earnings reactions**. Written to `stock_observations`/`daily_snapshots`.
- **Best-effort half (catalyst labeling):** detect notable past moves from price, tag them to
  earnings dates (free), and for famous recurring catalysts (AAPL Sept launches, NVDA GTC/earnings,
  etc.) annotate from known-catalyst knowledge during the backfill, **marked approximate**. Free news
  APIs only reach back ~1 year, so older event labels are inferred, not gospel.
- **Honest ceiling:** strong price/seasonality stats everywhere + best-effort catalyst tags that
  sharpen over time as the agent observes real events going forward. No paid datasets.

## 13. Token / budget management

- **Retrieve-don't-dump** memory (§6) keeps cost flat as history grows.
- **Light intraday/EOD runs** scoped to open zones + holdings.
- **Limits in play:** Pro usage budget (shared with interactive use) + Routines daily run cap.
- **Plan:** start on **Pro**, measure usage ~2 weeks; if tight, pull a dial — reduce cadence (morning
  + 1 intraday + EOD), lighten the morning scan, or upgrade to **Max**. No premature spend.

## 14. Security & privacy

- **Secrets** (API keys, Telegram token, Postgres conn string) in the Routine env-var/secret store —
  never committed. `secrets.local.json` stays git-ignored.
- **Private repo** + GitHub **2FA**. State/personal data (positions, suggestions, lessons) in a
  private repo/DB is acceptable for a personal tool; it contains no brokerage credentials, account
  numbers, or SSN (the agent is suggestion-only and never holds a brokerage login).
- **Data minimization** — store shares/avg-cost + notes; no sensitive identifiers.
- **Postgres** access restricted by connection string + host allowlist + provider auth.

## 15. Guardrail (unchanged)

Suggestion-only. No execution tools exist in the agent. `guardrails.execution_allowed = false`. If an
execution tool ever appears, refuse and warn. Auto-execution is Project 2 only.

## 16. Migration from the current local setup

- **Reused as-is (the brain):** the `market-briefing`, `equity-research`, `earnings-review` skills +
  their Rigorous-Mode logic. Behavior changes (two-brief types, dry powder, entry zones) are edits to
  these skills.
- **Changes:** data layer moves from local JSON files (`portfolio.json`, `suggestions-log.jsonl`,
  `radar.json`) to **Postgres**; `settings.json`/`watchlist.json`/`lessons.md` stay as repo files;
  the runner moves from manual/launchd to **cloud Routines**; secrets move to the env store.
- **One-time data move:** import existing `suggestions-log.jsonl` + `portfolio.json` + `radar.json`
  into the corresponding Postgres tables so no history is lost.

## 17. Config changes (`config/settings.json`)

Add blocks (exact field names finalized in the implementation plan):
- `runtime`: platform=routines, schedule times, network allowlist domains.
- `data_backend`: postgres (provider, conn via env var name), retrieve-don't-dump note.
- `cadence`: the four run definitions + which are "quiet-unless-triggered."
- `deployment`: core_mix (VOO/VXUS/SCHD weights), dry_powder rollover_months=2.
- `entry_zones`: enabled, default valid-until policy.
- `learning`: grading horizons, auto_tune_after_graded_calls=50, observations enabled.
- `preload`: mode=full, sources, one-time flag.

## 18. Phased implementation roadmap (slices — each independently testable)

1. **Infra & secrets** — private GitHub repo; Postgres provisioned (Supabase/Neon free); secrets in
   place; network allowlist; a trivial scheduled Routine "hello, can reach DB + Telegram + APIs".
2. **Data layer** — Postgres schema + a small data-access module; migrate existing local JSON history.
3. **Brain v2 behavior** — two-brief types, dry-powder deployment, entry zones (edits to the skills,
   reading/writing Postgres).
4. **Reconciliation** — conversational trade reporting → Postgres holdings/transactions.
5. **Learning & verification** — grading pass, lessons.md loop, stock_observations, invalidation
   reassessment, monthly scorecard. (Gated auto-tuning is a later sub-slice.)
6. **Historical preload** — the one-time full backfill job.
7. **Go-live** — enable the four scheduled runs; measure Pro usage; tune.

## 19. Acceptance criteria

- The agent runs unattended on the four-run weekday schedule in Anthropic's cloud, on the Pro plan.
- Daily briefs are portfolio-first and do **not** repeat the monthly pitch; the monthly brief carries
  the plan + scorecard.
- Growth/spec money is held as dry powder and only suggested when a gated setup is in its entry zone;
  unused cash rolls ≤2 months then → Core.
- Every buy idea has a buy zone + valid-until + invalidation; intraday checks re-evaluate open zones.
- The owner can report a trade in chat and see holdings + P&L update from Postgres.
- Every call is logged + later graded; `lessons.md` is read each run; per-stock observations are
  recorded and re-applied (demonstrable on a recurring-event name).
- A one-time preload populates per-watchlist seasonality/volatility/earnings-reaction stats.
- Memory is retrieved-not-dumped (token-per-run stays bounded as the DB grows).
- Guardrail intact: no execution tools; suggestion-only throughout. Secrets never in the repo.

## 20. Out of scope / future

- **Web app / dashboard = MarketPal** (parked). This v2 keeps the Postgres schema dashboard-ready so
  MarketPal can later read it. Resume MarketPal when the owner wants visual browsing or to bring
  people in. Hosting it later ≈ $0–5/mo via Cloud Run + Firestore/Postgres + (optionally) Gemini free
  tier — **not** GKE (~$15–25/mo, rejected as overkill).
- **Gemini-free brain** — technically viable (free-tier limits fine at this volume) but a different,
  re-validated brain, with a privacy caveat (free tier may train on data). Not adopted; the brain
  stays on Claude.
- **Auto-execution** — Project 2 (`stock-trader-bot`) only, behind its charter gates (~Sept 2026).
- **Trained price-prediction models** — out of scope (overfit on small samples; prior decision).

## 21. Decisions log (do not re-litigate)

- Autonomy = operator-autonomous, suggestion-only (owner, 2026-06-17).
- Idle growth cash = roll ≤2 months then DCA to Core.
- Learning = graded record + lessons ledger now; gated numeric auto-tuning after ~50 calls.
- Hosting = Anthropic cloud Routines, Pro plan; build cloud-native (no laptop-first throwaway).
- Code in a private GitHub repo (reverses prior "no-git" for code); secrets in env store.
- Data backend = managed Postgres (Supabase/Neon free), not SQLite-in-repo (avoids binary-in-git).
- Storage split = structured→Postgres; config + lessons→repo files.
- Cadence = 06:30 full + 10:30 + 13:30 quiet intraday + 15:10 post-market analysis.
- Preload = full (price-derived solid + best-effort catalysts).
- Budget = start on Pro, measure, tune dials; Max only if needed.
- Web app (MarketPal) stays parked; not built now.
