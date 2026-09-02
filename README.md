# stocks-agent

A **suggestion-only** investing assistant that runs scheduled analyses on Anthropic's platform. Each
run builds a fresh evidence packet and records what it actually read, wrote, and sent. Telegram
delivers briefs and accepts deterministic, confirmation-based portfolio records; a bounded weekly
ChatGPT task audits process/data quality. No component executes trades. Built and maintained by
**Rajrupesh**.

> **Guardrail:** zero trade-execution tools. The agent can only read market data, reason, and write
> to its own Supabase database. Every trade is placed by you.

## Project continuity

Before planning or implementing the next version, read
[`docs/ROADMAP.md`](docs/ROADMAP.md) for current deployment state, priorities, deferred work, and
guardrails. Also read the roadmap's linked decision notes—including
[`docs/research/2026-09-02-external-stock-agent-ideas-review.md`](docs/research/2026-09-02-external-stock-agent-ideas-review.md)—so
evaluated external ideas are reconsidered only under their recorded conditions and rejected ideas are
not accidentally reintroduced.

Supabase remains the source of truth for changing portfolio data; do not copy current holdings into
planning documents where they will become stale.

---

## What it does

Three weekday runs, all headless on Anthropic Cloud Routines:

| Run | Time (CT) | What happens |
|---|---|---|
| **Pre-market brief** | 06:30 | Fresh overnight/macro packet → full scan → provisional scenarios and Telegram brief |
| **Intraday check** | ~12:00 | Independently refreshes macro/quotes/news/events and re-underwrites candidates; alerts only after freshness + Checker gates |
| **Post-market analysis** | 15:10 | Fresh close review → snapshots/observations/learning; only freshly checked breakdown alerts |

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
  ├── skills/market-briefing/SKILL.md   ← fresh Analyst → Checker runs (pre / intra / post)
  ├── skills/equity-research/SKILL.md   ← on-demand deep-dive ("is NVDA still a good hold?")
  ├── skills/earnings-review/SKILL.md   ← on-demand earnings digest
  ├── skills/reconcile-trade/SKILL.md   ← Claude-chat fallback for recordkeeping
  └── skills/paper-watch/SKILL.md       ← track your own hypotheses separately from agent calls

Telegram Bot API → Supabase Edge Function (no model)
  ├── deterministic /buy, /sell, /stop, /portfolio parser
  ├── owner chat + user verification and update-id deduplication
  └── Confirm/Cancel → atomic PostgreSQL RPC → holdings + transactions

ChatGPT desktop (Friday 16:30 CT, included account usage)
  └── skills/weekly-portfolio-audit/SKILL.md ← bounded, read-only process/data audit

lib/                    Python helpers (stdlib urllib only + supabase-py)
  ├── config.py         secrets (env-var first, file fallback)
  ├── db.py             Supabase helpers + auditable analysis-run lifecycle
  ├── marketdata.py     Yahoo quotes + exchange timestamps, history, indicators, holiday detection
  ├── fundamentals.py   Finnhub — metrics, news, earnings dates, insider MSPR, analyst consensus
  ├── telegram.py       Telegram delivery (HTML, auto-split)
  ├── preload.py        Historical backfill — volatility, seasonality, notable moves
  └── weekly_audit.py   Redacted, bounded packet builder

config/
  ├── settings.json     Strategy, allocation (70/20/10), cadence, scoring, risk — edit to personalise
  └── watchlist.json    Tickers to watch (Core / Growth / Speculative buckets)

supabase/functions/telegram-portfolio/  Secure two-way Telegram webhook
sql/schema.sql          Canonical Supabase schema (13 tables + atomic RPCs)
scripts/
  ├── healthcheck.py           Verify cloud can reach all services
  ├── run_preload.py           One-time historical backfill for watchlist names
  ├── weekly_audit_packet.py   Print one read-only weekly JSON packet
  ├── register_telegram_webhook.py Register/verify the Telegram webhook
  └── verify_portfolio_command_rpc.py Verify atomic commands using disposable TSTTG rows
```

**All growing state lives in Supabase.** The only files the agent reads at runtime are
`config/settings.json` and `config/watchlist.json`. Holdings, suggestions, grades, observations,
snapshots, lessons, radar, analysis runs, and Telegram command receipts are Supabase tables. **US market holidays are detected
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
| Telegram Bot API | Brief delivery + deterministic portfolio-record messages and buttons |
| Supabase (existing free-tier project) | Postgres + Edge Function; no Cloud Run project required |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/RajSivapu/stocks-agent.git
cd stocks-agent
```

> Python 3.14 on macOS is externally managed (PEP 668). Use a venv:
> `python3 -m venv .venv && source .venv/bin/activate && pip install supabase pytest "psycopg[binary]"`

### 2. Provision Supabase

Sign up at [supabase.com](https://supabase.com) and create a project. For a new project, apply
`sql/schema.sql` in the **Supabase SQL Editor**. For an existing installation, apply the additive,
idempotent `sql/migrations/20260901_reliable_stock_agent.sql` instead.

### 3. Get free API keys

| Key | Where |
|---|---|
| `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io) — free tier, 60 calls/min |
| `ALPHAVANTAGE_API_KEY` | [alphavantage.co](https://www.alphavantage.co/support/#api-key) — free, 25 calls/day |
| `TELEGRAM_BOT_TOKEN`, owner chat ID, owner user ID | Create a bot with [@BotFather](https://t.me/BotFather), message it once, then inspect `getUpdates` before registering a webhook |
| `TELEGRAM_WEBHOOK_SECRET` | Generate a new high-entropy ASCII value (letters, digits, `_`, `-`); this is separate from the bot token |
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` — from Supabase Dashboard → Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Settings → API → `service_role` key |
| `POSTGRES_URL` | Supabase → Connect → transaction-pooler URI; used by migration verification |

### 4. Configure secrets locally

```bash
cp config/secrets.local.json.example config/secrets.local.json
# edit config/secrets.local.json — gitignored, never committed
```

If a token/key has ever appeared in a terminal transcript, screenshot, chat, or tracked file, rotate
it before deployment. In particular, rotate the Telegram bot token, Finnhub key, and Alpha Vantage
key before enabling this webhook.

### 5. Deploy the Telegram recorder

The webhook has no model and no brokerage integration. It only writes this Supabase project after
an owner-authenticated Confirm button.

```bash
cp supabase/.env.example supabase/.env.local
# fill the ignored file with the ROTATED token, new webhook secret, and owner IDs
npx supabase login
npx supabase link --project-ref <project-ref>
npx supabase secrets set --env-file supabase/.env.local
npx supabase functions deploy telegram-portfolio --no-verify-jwt
.venv/bin/python scripts/verify_holding_open_date_migration.py
.venv/bin/python scripts/verify_portfolio_command_rpc.py
.venv/bin/python scripts/register_telegram_webhook.py
```

JWT verification is disabled only for this function because Telegram cannot send a Supabase JWT.
The function instead requires Telegram's secret header plus the exact configured chat and user IDs.
Supabase injects `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` into its function runtime. RLS stays
enabled, and the atomic RPCs are executable only by `service_role`.

### 6. Verify everything works

```bash
.venv/bin/python scripts/healthcheck.py
# Expected output: {"postgres":"ok","finnhub":"ok","yahoo":"ok","telegram":"ok"}
# + a Telegram DM from your bot
```

### 7. Backfill historical data (one-time)

Loads 5-year volatility, seasonality, and notable moves for every ticker in your watchlist:

```bash
.venv/bin/python scripts/run_preload.py
```

### 8. Personalise

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
> Run the weekly-portfolio-audit skill.      (read-only process audit)
```

For ordinary portfolio updates, Telegram is easier and safer than Claude chat:

```text
/buy AAPL 2 210 growth
/sell AAPL 1.5 225
/sell NVDA all 210
/sell NVDA all 210 on 2026-08-28
/stop AAPL 195
/portfolio
```

Buy/Sell/Stop first returns a preview with **Confirm** and **Cancel**. Nothing changes before
Confirm. Commands expire after 15 minutes and are rejected if the recorded shares changed. For an
older trade, append `on YYYY-MM-DD`; otherwise the bot uses the Telegram message date in
America/Chicago. The immutable record time and actual execution date remain separate.

---

## Cloud setup (Anthropic Routines)

See [routines/README.md](routines/README.md) for the full step-by-step:

1. In **claude.ai → Code → Routines**, click the cloud icon → **Add environment**
2. Name it `stocks-agent`, set **Network = Full**, add the 6 env var secrets, setup script: `pip install supabase --ignore-installed`
3. Create 4 Routines (Healthcheck + Pre-market + Intraday + Post-market), each using the `stocks-agent` environment
4. Run **Healthcheck** once — expect `{"postgres":"ok","finnhub":"ok","yahoo":"ok","telegram":"ok"}` in Telegram

Your laptop does not need to be on for Claude Routines or Telegram/Supabase handling.

### Weekly ChatGPT audit

The optional Friday 16:30 CT audit runs locally in the ChatGPT/Codex desktop app using the included
account allowance—there is no OpenAI API key or per-call API charge in this repo. It reads one
bounded JSON packet and never writes Supabase or Telegram. The computer and desktop app must be
running when the local scheduled task fires; missing the audit does not affect the three Claude
runs. Use GPT-5.6 Sol manually only when you deliberately want a separate opinion before acting on
a real-money decision; do not duplicate all three daily runs in ChatGPT.

---

## Supabase schema (13 tables)

| Table | What it stores |
|---|---|
| `holdings` | What you actually own (shares, avg cost, stop, target, high-water price) |
| `transactions` | Every recorded buy/sell, with separate record and actual execution dates |
| `suggestions` | Every Buy/Watch/Avoid call the agent made, with full bull/bear/decisive-factor fields |
| `suggestion_grades` | Outcomes at 5/21/63-day horizons + Reflexion post-mortems on wrong calls |
| `stock_observations` | Per-stock behavioral memory — seasonality, earnings reactions, big moves |
| `daily_snapshots` | EOD close/RSI/MACD for watched/held names + macro symbols (VIX/TNX/DXY) |
| `dry_powder` | Undeployed growth/spec capital by month |
| `radar` | Agent's self-curated discovery queue (promising names not yet on watchlist) |
| `paper_watches` | Your own hypotheses (separate from agent calls and real holdings) |
| `lessons` | Regime lines, learned lessons, and Reflexion post-mortems (category: regime/lesson/post-mortem) |
| `analysis_runs` | Per-run status, evidence time/source health, actual writes, and Telegram message IDs |
| `portfolio_commands` | Owner-authenticated pending/confirmed/cancelled Telegram mutations and results |
| `telegram_updates` | Update IDs used to suppress duplicate Telegram deliveries before side effects |

---

## Skills

| Skill | When to use |
|---|---|
| `market-briefing` | Scheduled (via Routines) or on-demand brief |
| `equity-research` | "Is X still worth holding?" — full bull/bear deep-dive using live data |
| `earnings-review` | "How was X's earnings?" — results + reaction + guidance digest |
| `reconcile-trade` | You placed a trade → records it in Supabase (holdings + transactions) |
| `paper-watch` | Track a hypothesis ("I think SHOP breaks out next week") |
| `weekly-portfolio-audit` | Friday read-only audit of portfolio data and analysis process |

---

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
node --test tests/test_telegram_parser.mjs tests/test_telegram_webhook_utils.mjs
npx --yes deno check supabase/functions/telegram-portfolio/index.ts
```

---

## Cost

Cloud runs use your Claude plan allowance—there is no Anthropic model API key in this repo. The
weekly ChatGPT audit uses the desktop account allowance—there is no OpenAI model API key. Telegram's
Bot API, free data tiers, and the existing Supabase project avoid Cloud Run and a separate Google
Cloud project. Provider plan limits still apply. The three Claude runs consume roughly 2–2.5× one
full run; if allowance gets tight, pause intraday first.

---

## Safety

- **No execution tools** — the agent has no tools to place, modify, or cancel orders. You execute every trade yourself on Robinhood.
- **Telegram writes records, not orders** — identity + webhook-secret checks happen before parsing; Buy/Sell/Stop require Confirm and an atomic stale-safe RPC.
- **Secrets never in repo** — local secret files, `.codex/`, and Supabase local env files are gitignored. Routine secrets stay in its environment; webhook secrets stay in Supabase.
- **Rotate exposed values** — rotate any bot/API key that has appeared in a transcript or local plaintext configuration before live deployment.
- **Suggestion-only** guardrail is stated in every skill's frontmatter and reinforced throughout.
- **RLS enabled** on all 13 Supabase tables; atomic RPC execution is revoked from public/anon/authenticated and granted only to `service_role`.

---

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md).

---

## License

MIT
