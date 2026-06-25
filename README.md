# stocks-agent

A **suggestion-only** AI investing assistant that runs as scheduled cloud jobs on Anthropic's platform.
It sends plain-English market briefs to Telegram, tracks a live watchlist, learns from its own call
history, and never executes trades. Built and maintained by **Rajrupesh**.

> **Guardrail:** zero trade-execution tools. The agent can only read market data, reason, and write
> to its own Supabase database. Every trade is placed by you.

---

## What it does

Three weekday runs, all headless on Anthropic Cloud Routines:

| Run | Time (CT) | What happens |
|---|---|---|
| **Pre-market brief** | 06:30 | Macro pulse check → full market scan → Telegram brief with Buy/Watch/Avoid calls, entry zones, targets, stops |
| **Intraday check** | ~12:00 | Monitors open zones + holdings; bounded opportunity scan; Telegram alert only if something fires |
| **Post-market analysis** | 15:10 | Records daily snapshots + observations + regime line; sends EOD Holdings Summary to Telegram when positions exist |

The **1st weekday of each month** the pre-market run switches to a monthly-plan brief with a 🏁 scorecard
(accuracy by bucket, biggest lesson, what's changing).

**EOD Holdings Summary** (sample):
```
📊 EOD — Jun 24

Portfolio
🟡 NVDA $199.00 · avg $213.37 · 4.6868 shares
📉 −$67.35 (-6.7%) · invested $1000 → now $933
Stop $196.30 · ⚠️ $2.70 gap — watch open · Target $230
Heads up: one weak open tests your stop. No action needed tonight — just stay aware.

Market
IWM +1.6% · QQQ -2.6% · SPY -2.3%
Software carnage day 3, small-caps diverging — rotation signal.

Tomorrow: SPY SMA50 barely held — watch the open.
```

---

## Architecture

```
Claude (Pro plan, Cloud Routines)
  ├── skills/market-briefing/SKILL.md   ← reasoning brain (pre-market / intraday / post-market)
  ├── skills/equity-research/SKILL.md   ← on-demand deep-dive ("is NVDA still a good hold?")
  ├── skills/earnings-review/SKILL.md   ← on-demand earnings digest
  ├── skills/reconcile-trade/SKILL.md   ← record trades you placed ("bought 1 NVDA @207")
  └── skills/paper-watch/SKILL.md       ← track your own hypotheses separately from agent calls

lib/                    Python helpers (stdlib urllib only + supabase-py)
  ├── config.py         secrets (env-var first, file fallback)
  ├── db.py             Supabase helpers via HTTPS (holdings, suggestions, lessons, radar, ...)
  ├── marketdata.py     Yahoo Finance — quotes, history, indicators (RSI/MACD/SMA) + holiday detection
  ├── fundamentals.py   Finnhub — metrics, news, earnings dates, insider MSPR, analyst consensus
  ├── telegram.py       Telegram delivery (HTML, auto-split)
  └── preload.py        Historical backfill — volatility, seasonality, notable moves

config/
  ├── settings.json     Strategy, allocation (70/20/10), cadence, scoring, risk — edit to personalise
  └── watchlist.json    Tickers to watch (Core / Growth / Speculative buckets)

sql/schema.sql          Supabase schema (10 tables — apply via Supabase SQL editor)
scripts/
  ├── healthcheck.py           Verify cloud can reach all services
  ├── run_preload.py           One-time historical backfill for watchlist names
  └── migrate_lessons_to_pg.py One-time: move data/lessons.md → lessons table (already run)
```

**All growing state lives in Supabase.** The only files the agent reads at runtime are
`config/settings.json` and `config/watchlist.json`. Holdings, suggestions, grades, observations,
snapshots, lessons, and radar are all Supabase tables. **US market holidays are detected
automatically** — all three runs exit silently (pre-market sends one Telegram notification).

---

## Signals & intelligence

The agent layers multiple signal types before making any suggestion:

| Layer | Signal | Source |
|---|---|---|
| **Macro gate** | VIX fear gauge (`^VIX`) | Yahoo Finance |
| **Macro gate** | 10-year yield + yield curve spread (`^TNX`, `^IRX`) | Yahoo Finance |
| **Macro gate** | Dollar strength (`DX-Y.NYB`) | Yahoo Finance |
| **Macro gate** | Internal market breadth (% watchlist above SMA50) | Our daily_snapshots |
| **Fundamentals** | Revenue/EPS growth, margins, P/E, debt | Finnhub |
| **Fundamentals** | Analyst consensus (Buy/Hold/Sell counts) | Finnhub free tier |
| **Fundamentals** | Insider MSPR — net insider buying/selling score | Finnhub free tier |
| **Technicals** | RSI-14, MACD, SMA50/200 — computed locally | Yahoo Finance history |
| **Sector** | Stock vs sector ETF relative strength | Yahoo Finance + snapshots |
| **News** | Company + market news, sentiment | Finnhub |
| **Memory** | Per-stock observations (seasonality, earnings reactions) | Supabase DB |
| **Memory** | Regime lines — today vs prior trend | Supabase DB |
| **Self-review** | Graded past calls (5/21/63-day horizons) | Supabase DB |
| **Self-review** | Reflexion post-mortems on wrong calls | Supabase DB |

**Reflexion learning:** when a call is graded "wrong" at the 5-day horizon, the agent writes a
structured post-mortem: what it bet on, which bear case proved true, the regime context, and a revised
rule for next time. Future analyses of the same stock start by reading these post-mortems.

---

## Data sources (all free)

| Source | Used for |
|---|---|
| Yahoo Finance (stdlib urllib) | Quotes, OHLC history, locally-computed RSI/MACD/SMA, macro symbols (VIX/TNX/DXY) |
| Finnhub (free tier, 60 req/min) | Fundamentals, company news, earnings dates, insider MSPR, analyst consensus |
| Alpha Vantage (free tier, 25/day) | Top movers / sector snapshot (backup) |
| Telegram Bot API | Delivery — HTML briefs and EOD summaries to your Telegram chat |
| Supabase (free tier) | Postgres — all persistent state via HTTPS (no raw TCP needed) |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/RajSivapu/stocks-agent.git
cd stocks-agent
```

> Python 3.14 on macOS is externally managed (PEP 668). Use a venv:
> `python3 -m venv .venv && source .venv/bin/activate && pip install supabase pytest`

### 2. Provision Supabase

Sign up at [supabase.com](https://supabase.com) (free tier). Create a project, then apply the schema
via the **Supabase SQL Editor** (Dashboard → SQL Editor → paste `sql/schema.sql` → Run).

### 3. Get free API keys

| Key | Where |
|---|---|
| `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io) — free tier, 60 calls/min |
| `ALPHAVANTAGE_API_KEY` | [alphavantage.co](https://www.alphavantage.co/support/#api-key) — free, 25 calls/day |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Create a bot with [@BotFather](https://t.me/BotFather), message it once, then call `getUpdates` to get your chat id |
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` — from Supabase Dashboard → Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Settings → API → `service_role` key |

### 4. Configure secrets locally

```bash
cp config/secrets.local.json.example config/secrets.local.json
# edit config/secrets.local.json — gitignored, never committed
```

### 5. Verify everything works

```bash
.venv/bin/python scripts/healthcheck.py
# Expected output: {"postgres":"ok","finnhub":"ok","yahoo":"ok","telegram":"ok"}
# + a Telegram DM from your bot
```

### 6. Backfill historical data (one-time)

Loads 5-year volatility, seasonality, and notable moves for every ticker in your watchlist:

```bash
.venv/bin/python scripts/run_preload.py
```

### 7. Personalise

- `config/settings.json` — monthly amount, allocation (70/20/10), risk settings, scoring weights
- `config/watchlist.json` — tickers grouped by bucket (Core / Growth / Speculative)

---

## Running manually

Open Claude Code in this folder and invoke any skill directly:

```
> Run the market-briefing skill as the pre-market full brief for today.
> Is NVDA still a good hold?              (triggers equity-research)
> How was NVDA's earnings?               (triggers earnings-review)
> Bought 4.68 NVDA at 213.37            (triggers reconcile-trade)
> Paper-watch SHOP from $80, thesis: breakout (triggers paper-watch)
```

---

## Cloud setup (Anthropic Routines)

See [routines/README.md](routines/README.md) for the full step-by-step:

1. In **claude.ai → Code → Routines**, click the cloud icon → **Add environment**
2. Name it `stocks-agent`, set **Network = Full**, add the 6 env var secrets, setup script: `pip install supabase --ignore-installed`
3. Create 4 Routines (Healthcheck + Pre-market + Intraday + Post-market), each using the `stocks-agent` environment
4. Run **Healthcheck** once — expect `{"postgres":"ok","finnhub":"ok","yahoo":"ok","telegram":"ok"}` in Telegram

Your laptop does not need to be on.

---

## Supabase schema (10 tables)

| Table | What it stores |
|---|---|
| `holdings` | What you actually own (shares, avg cost, stop, target, high-water price) |
| `transactions` | Every buy/sell you reported via reconcile-trade |
| `suggestions` | Every Buy/Watch/Avoid call the agent made, with full bull/bear/decisive-factor fields |
| `suggestion_grades` | Outcomes at 5/21/63-day horizons + Reflexion post-mortems on wrong calls |
| `stock_observations` | Per-stock behavioral memory — seasonality, earnings reactions, big moves |
| `daily_snapshots` | EOD close/RSI/MACD for watched/held names + macro symbols (VIX/TNX/DXY) |
| `dry_powder` | Undeployed growth/spec capital by month |
| `radar` | Agent's self-curated discovery queue (promising names not yet on watchlist) |
| `paper_watches` | Your own hypotheses (separate from agent calls and real holdings) |
| `lessons` | Regime lines, learned lessons, and Reflexion post-mortems (category: regime/lesson/post-mortem) |

---

## Skills

| Skill | When to use |
|---|---|
| `market-briefing` | Scheduled (via Routines) or on-demand brief |
| `equity-research` | "Is X still worth holding?" — full bull/bear deep-dive using live data |
| `earnings-review` | "How was X's earnings?" — results + reaction + guidance digest |
| `reconcile-trade` | You placed a trade → records it in Supabase (holdings + transactions) |
| `paper-watch` | Track a hypothesis ("I think SHOP breaks out next week") |

---

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
# 16 tests: DB roundtrips, marketdata math, preload math, fundamentals (insider MSPR, analyst consensus, macro symbols)
```

---

## Cost

Cloud runs use your Claude **Pro plan** token budget — no separate API bill. Data APIs and Telegram are
free. The three daily runs consume roughly 2–2.5× one full run. If your Pro budget runs tight, pause
the intraday check first (keeps 06:30 + 15:10) — that cuts ~33% with the least information loss.

---

## Safety

- **No execution tools** — the agent has no tools to place, modify, or cancel orders. You execute every trade yourself on Robinhood.
- **Secrets never in repo** — `config/secrets.local.json` and `.mcp.json` are gitignored. All keys go into the Supabase environment in the cloud Routine.
- **Suggestion-only** guardrail is stated in every skill's frontmatter and reinforced throughout.
- **RLS enabled** on all 10 Supabase tables — service role key bypasses for the agent; anon key is blocked.

---

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md).

---

## License

MIT
