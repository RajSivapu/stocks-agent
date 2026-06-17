# Stocks Agent (v1 — Market Briefing)

A Claude Code–orchestrated, **suggestion-only** investing assistant for a beginner investing
**$500/month (Year-1 foundation stage)**. Each **weekday morning** it sends you ONE simple,
plain-English brief on Telegram and logs every call. (Your actual money *moves* stay monthly — shown
in full on the first brief of each month.) It has **no trading tools** and **cannot execute
trades** — you place all trades yourself.

## What the brief looks like (one screen)
- **📈 Today** — is the market up/down + a one-line guess for the day.
- **📰 This week's setup** (Mondays) — what you missed last week + which of your names report earnings this week.
- **💰 This month's money moves** — your $500: 🟢 DCA $350 into VOO/VTI · ✅ add $100 to your ONE best
  growth pick (+ a 2–3 name shortlist to learn) · 🧪 $50 to the opportunities fund (speculative =
  learning mode until it hits ~$500).
- **What I'd watch** — 👀/🟡/🛑 lines with a plain reason + rough price (learning, not extra buys).
- **💡 Why** — the one thing moving the market, explained simply.
- **💼 Your money** — how your holdings are doing (once you add them).
- **📊 My track record** — how accurate my past calls have been (review + adjust).
- **💡 Tip of the day** — one small thing to learn.

Each pick/watch line carries a tiny **Health Score** tag (e.g. "76/100, low–med risk") — a
transparent quality+risk summary built from growth, financial health, and valuation (growth forgives
a high P/E, the way it does for Nvidia). It's a teaching cue, **not** a buy signal.

Behind the scenes it scans the whole US market, reads the latest news, checks insider activity, keeps
a self-curated radar that dynamically updates your watchlist, scores each stock 0–100, keeps a
learning memory it reviews each run (comparing the latest trend to the last one), and reasons in a
70/20/10 plan that scales as you grow — but the message stays simple and matched to your reality; that
machinery never clutters it.

## Safety
- Read-only by construction: **no execution tools installed** (verified 2026-06-17 via `claude mcp list` —
  only read-only data sources Finnhub/Alpha Vantage/yfinance, plus delivery). You make and place every trade.
- Honest by design: shows confidence, tracks its own accuracy, never hypes.

## Setup (v2 — autonomous daily operator)
The agent runs as an ephemeral Anthropic cloud Routine that clones this private repo each run and
keeps all growing state in managed Postgres. To set up a dev environment or a fresh runtime:

1. **Clone** this private repo.
2. **Install the Postgres driver:** `pip install "psycopg[binary]"` (the cloud Routine has pip; on the
   owner's Mac, Python 3.14 has no pip packages — run DB-touching steps in the cloud or install locally).
3. **Provide secrets.** Copy `config/secrets.local.json.example` → `config/secrets.local.json`
   (git-ignored) and fill in the values, OR set them as environment variables (env wins — used by the
   cloud Routine): `FINNHUB_API_KEY`, `ALPHAVANTAGE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
   `POSTGRES_URL`. **Secrets are never committed.**
4. **Verify connectivity:** `python scripts/healthcheck.py` → expects `{"postgres":"ok","finnhub":"ok",
   "yahoo":"ok","telegram":"ok"}` and a Telegram DM.

See `routines/README.md` for the cloud Routine configuration (schedule, network allowlist, env vars).

## Configure
- `config/settings.json` — strategy, allocation, schedule, delivery, risk, scoring, learning, and your
  monthly amount (a range: $500 min → $1,000 max, plus the current amount — the advice scales with it).
- `config/watchlist.json` — tickers to WATCH, by bucket. **Dynamic:** the agent adds promising new
  names and retires stale ones it added, and tells you in the brief. Edit freely; it never drops names
  you own or added.
- `config/portfolio.json` — what you actually OWN (drives hold/sell advice). Update after trades,
  or tell the agent "I bought/sold X" and it edits this.
- `data/learning.md` — the agent's "learning memory": lessons + a trend log it reads and updates each
  run so it gets smarter over time. (It's an LLM agent with memory + self-review — not a trained
  prediction model, which would overfit at this stage.)
- `config/secrets.local.json` — free API keys + Telegram bot token/chat id. **Never share this file.**

## Delivery
Telegram (primary). Email is an optional fallback (needs a Google sign-in; may not work on the
cloud schedule).

## Holdings / brokers
You enter holdings in `portfolio.json` (you haven't invested yet, so it starts empty). When you do
invest, a **read-only** connection via Plaid or SnapTrade can auto-sync holdings from beginner
brokers (Fidelity, Schwab, Public, Wealthsimple, etc.) — read-only, no trading. Robinhood's official
MCP bundles execution, so it's NOT used here (it's reserved for the separate `stock-trader-bot`).

## Deep dives (on-demand)
Open Claude Code in this folder and ask any time — built on free data, no paid plugins:
- **`equity-research`** — "Is NVDA still a good hold?" → a plain-English research note (bull/bear,
  health score, "does the thesis still hold?", verdict). Most useful on stocks you own.
- **`earnings-review`** — "How was NVDA's earnings?" → beat/miss vs expectations, guidance, and the
  reaction. Paste an earnings-call transcript for a deeper call digest (full transcripts are paid, so
  the free version covers the results + reaction).

## Run manually
Open Claude Code in this folder and invoke the `market-briefing` skill.

## Automatic schedule
A `/schedule` cloud routine runs it **every weekday (Mon–Fri 07:30 ET / 06:30 Central)**. Your
laptop does not need to be on. Money *moves* stay monthly (full block on the month's first brief).
Cadence is config-driven and can change later. (Daily uses ~5× the Claude usage of weekly; free data
APIs are unaffected since Alpha Vantage's 25/day resets daily.)

## Cost
Cloud runs use your Claude Pro usage quota (no separate bill). Data APIs + Telegram are free.

## Not in v1 (future)
- Intraday breaking-news alerts; monthly deep accuracy report (Phase 3–4).
- Paid earnings-call transcripts (auto, no paste); a dedicated `valuation` skill; CAN SLIM lens (v2).
- Read-only broker auto-sync of holdings (Plaid/SnapTrade) once you've invested.
- **Optional Airtable dashboard (v1.1):** mirror the watchlist + Health Scores + suggestions log into
  a free Airtable base for a browsable, filterable scoreboard (phone + web). Local JSON/Markdown stays
  the source of truth; Airtable is just a *view*. Deferred because (a) Telegram already delivers the
  brief and the Airtable connector may not run in scheduled cloud jobs, and (b) the dashboard is only
  useful once there are holdings + scored history to browse. ~20-min add later; the logged score data
  already feeds it.
- Real trade execution (separate `stock-trader-bot`, paper-first, ~Sept 2026).
