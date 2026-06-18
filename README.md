# stocks-agent

A **suggestion-only** AI investing assistant that runs as scheduled cloud jobs on Anthropic's platform.
It sends plain-English market briefs to Telegram, tracks a live watchlist, learns from its own call history,
and never executes trades. Built and maintained by **Rajrupesh**.

> **Guardrail:** zero trade-execution tools. The agent can only read market data, reason, and write
> to its own Postgres database. Every trade is placed by you.

---

## What it does

Three weekday runs, all headless on Anthropic Cloud Routines:

| Run | Time (CT) | What happens |
|---|---|---|
| **Pre-market brief** | 06:30 | Full market scan → Telegram brief with Buy/Watch/Avoid calls, entry zones, targets, stops |
| **Intraday check** | ~12:00 | Monitors open zones + holdings; bounded opportunity scan; Telegram alert only if something fires |
| **Post-market analysis** | 15:10 | Records daily snapshots + stock observations; inserts regime line into Postgres; silent unless a holding broke down |

The **1st weekday of each month** the pre-market run switches to a monthly-plan brief with a 🏁 scorecard
(accuracy by bucket, biggest lesson, what's changing).

---

## Architecture

```
Claude (Pro plan, Cloud Routines)
  ├── skills/market-briefing/SKILL.md   ← reasoning brain (pre-market / intraday / post-market)
  ├── skills/equity-research/SKILL.md   ← on-demand deep-dive ("is NVDA still a good hold?")
  ├── skills/earnings-review/SKILL.md   ← on-demand earnings digest
  ├── skills/reconcile-trade/SKILL.md   ← record trades you placed ("bought 1 NVDA @207")
  └── skills/paper-watch/SKILL.md       ← track your own hypotheses separately from the agent's calls

lib/                    Python helpers (no third-party deps except psycopg)
  ├── config.py         secrets (env-var first, file fallback)
  ├── db.py             Postgres helpers (holdings, suggestions, lessons, observations, ...)
  ├── marketdata.py     Yahoo Finance — quotes, history, indicators (RSI/MACD/SMA, pure Python)
  ├── fundamentals.py   Finnhub — metrics, news, earnings dates
  ├── telegram.py       Telegram delivery (HTML, auto-split)
  └── preload.py        Historical backfill — volatility, seasonality, notable moves

config/
  ├── settings.json     Strategy, allocation, cadence, scoring, risk — edit to personalise
  ├── watchlist.json    Tickers to watch (Core / Growth / Speculative buckets)
  └── portfolio.json    Your actual holdings (update after trades or use reconcile-trade skill)

sql/schema.sql          Postgres schema (all 10 tables)
scripts/
  ├── healthcheck.py    Verify cloud can reach all services
  ├── run_preload.py    One-time historical backfill for watchlist names
  └── migrate_lessons_to_pg.py  One-time: move data/lessons.md → lessons table
```

**All growing state lives in Postgres.** The only files the agent reads at runtime are
`config/settings.json` and `config/watchlist.json`. Lessons, suggestions, grades, observations,
snapshots, and radar are all DB tables.

---

## Data sources (all free)

| Source | Used for |
|---|---|
| Yahoo Finance (stdlib urllib) | Quotes, OHLC history, locally-computed RSI/MACD/SMA |
| Finnhub (free tier) | Fundamentals, company news, earnings dates |
| Alpha Vantage (free tier) | Top movers / sector snapshot (backup) |
| Telegram Bot API | Delivery — HTML briefs to your Telegram chat |
| Supabase / Neon (free tier) | Postgres — all persistent state |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/stocks-agent.git
cd stocks-agent
pip install "psycopg[binary]"
```

> Python 3.14 on macOS is externally managed (PEP 668). Use a venv:
> `python3 -m venv .venv && source .venv/bin/activate && pip install "psycopg[binary]" pytest`

### 2. Provision Postgres

Free options: [Supabase](https://supabase.com) or [Neon](https://neon.tech). Both have generous free tiers.

Apply the schema:

```bash
psql "$POSTGRES_URL" -f sql/schema.sql
```

### 3. Get free API keys

| Key | Where |
|---|---|
| `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io) — free tier, 60 calls/min |
| `ALPHAVANTAGE_API_KEY` | [alphavantage.co](https://www.alphavantage.co/support/#api-key) — free, 25 calls/day |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Create a bot with [@BotFather](https://t.me/BotFather), then message it and call `getUpdates` to find your chat id |
| `POSTGRES_URL` | From your Supabase/Neon project dashboard |

### 4. Configure secrets

Copy the example and fill in your values:

```bash
cp config/secrets.local.json.example config/secrets.local.json
# edit config/secrets.local.json
```

Or set environment variables directly (env takes precedence over the file — required for cloud runs):

```
POSTGRES_URL=postgresql://USER:PASS@HOST:5432/DB?sslmode=require
FINNHUB_API_KEY=...
ALPHAVANTAGE_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### 5. Verify everything works

```bash
python scripts/healthcheck.py
# Expected: {"postgres":"ok","finnhub":"ok","yahoo":"ok","telegram":"ok"}
# and a Telegram DM from your bot
```

### 6. Backfill historical data (one-time)

Loads 5-year volatility, seasonality, and notable moves for every ticker in your watchlist:

```bash
python scripts/run_preload.py
```

### 7. Personalise

- `config/settings.json` — monthly amount, allocation (70/20/10), risk settings, scoring weights
- `config/watchlist.json` — tickers grouped by bucket (Core / Growth / Speculative)

---

## Running manually

Open Claude Code in this folder and invoke the `market-briefing` skill, or any of the on-demand skills.

```
# In Claude Code (claude.ai or CLI)
> Run the market-briefing skill as the pre-market full brief for today.
> Is NVDA still a good hold? (triggers equity-research)
> How was NVDA's earnings? (triggers earnings-review)
> Bought 2 NVDA at 207 (triggers reconcile-trade)
```

---

## Cloud setup (Anthropic Routines)

See [routines/README.md](routines/README.md) for the complete step-by-step:

1. Connect this repo in **claude.ai → Code → Routines**
2. Set **Network = Custom** — allowlist the 7 hosts listed in routines/README.md
3. Add the 5 environment variable secrets
4. Create the three weekday Routines (crons + exact prompts are in the README)
5. Run the Healthcheck Routine once to verify the cloud reaches all services

Your laptop does not need to be on.

---

## Postgres schema (10 tables)

| Table | What it stores |
|---|---|
| `holdings` | What you actually own (shares, avg cost, stop, target, high-water) |
| `transactions` | Every buy/sell you reported via reconcile-trade |
| `suggestions` | Every Buy/Watch/Avoid call the agent made, with full debate fields |
| `suggestion_grades` | Outcomes at 5/21/63-day horizons (how accurate were the calls) |
| `stock_observations` | Per-stock behavioral memory — seasonality, earnings reactions, big moves |
| `daily_snapshots` | EOD close/RSI/MACD for watched/held names |
| `dry_powder` | Undeployed growth/spec capital by month |
| `radar` | Agent's self-curated discovery queue (names it's watching but not yet on watchlist) |
| `paper_watches` | Your own hypotheses (separate from agent calls and real holdings) |
| `lessons` | Regime lines + learned lessons (replaces the old lessons.md file) |

---

## Skills

| Skill | When to use |
|---|---|
| `market-briefing` | Scheduled (via Routines) or on-demand brief |
| `equity-research` | "Is X still worth holding?" — full bull/bear deep-dive |
| `earnings-review` | "How was X's earnings?" — results + reaction + guidance |
| `reconcile-trade` | You placed a trade → record it in Postgres |
| `paper-watch` | Track a hypothesis ("I think SHOP breaks out next week") |

---

## Cost

Cloud runs use your Claude **Pro plan** token budget — no separate bill. Data APIs and Telegram are free.
The three daily runs consume roughly 2–2.5× one full run. If your Pro budget runs tight, pause the
intraday check first (keep 06:30 + 15:10) — that cuts ~33% with the least information loss.

---

## Safety

- **No execution tools** — verified via `claude mcp list`. The agent has no tools to place, modify,
  or cancel orders. You execute every trade yourself.
- **Secrets never in repo** — `config/secrets.local.json` and `.mcp.json` are gitignored.
- **Suggestion-only** guardrail is enforced in every skill's frontmatter with `NEVER executes trades`.

---

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md).

---

## License

MIT
