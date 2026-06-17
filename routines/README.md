# Cloud Routine setup (Anthropic Routines)

The agent runs as ephemeral **Anthropic Cloud Routines** (`/schedule`) on the owner's **Pro plan**,
headless. Each run clones this private repo, runs the `market-briefing` skill, reaches Postgres + the
data APIs + Telegram, then exits. **All persistent state lives in Postgres or repo files — nothing
persists in the runtime between runs.**

## One-time configuration (owner, in claude.ai → Code → Routines)

1. **Connect the repo:** point the Routine at the private `stocks-agent` GitHub repo.
2. **Network mode = Custom** (the default cloud network is blocklisted). Allowlist these hosts:
   - `finnhub.io`
   - `query1.finance.yahoo.com`
   - `query2.finance.yahoo.com`
   - `api.telegram.org`
   - `github.com`
   - the **Postgres host** (the `HOST` portion of your `POSTGRES_URL`, e.g. `db.xxxx.supabase.co`
     or `ep-xxxx.neon.tech`)
3. **Environment variables / secrets** (never commit these):
   - `FINNHUB_API_KEY`
   - `ALPHAVANTAGE_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `POSTGRES_URL`
4. **Run dependency install** in the routine setup/prompt: `pip install "psycopg[binary]"` (the cloud
   Linux runtime has pip).

## Healthcheck routine (Slice 1 — prove the cloud can reach everything)

- **Prompt:** `Run "pip install psycopg[binary]" then "python scripts/healthcheck.py" and report the JSON output.`
- **Schedule:** once / manual trigger.
- **Expected:** the cloud run posts `{"postgres":"ok","finnhub":"ok","yahoo":"ok","telegram":"ok"}`
  to Telegram. This proves the cloud runtime reaches DB + APIs + Telegram.

## Scheduled runs (Slice 7 — go-live)

Four weekday Routines, owner's local Central time (brief reference = 07:30 ET = 06:30 CT). Set each
schedule in the Routine UI using the appropriate UTC offset for the season (CDT = UTC−5, CST = UTC−6):

| Run | Time (CT) | Prompt names the run kind |
|---|---|---|
| Pre-market full brief | 06:30 | "pre-market full brief" (monthly-plan on 1st weekday, else daily-status) |
| Intraday check | 10:30 | "intraday check — quiet unless a buy zone triggered or a holding hit invalidation" |
| Intraday check | 13:30 | same as 10:30 |
| Post-market analysis | 15:10 | "post-market analysis — record how each watched/held name behaved; quiet unless action needed" |

(Detailed go-live steps + how to pause/adjust cadence are filled in at Slice 7 / Task 19.)
