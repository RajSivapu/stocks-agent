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

Create **three weekday (Mon–Fri) Routines** on the private repo, all reusing the same Custom-network
allowlist + 5 env-var secrets above. Each one's prompt **must name its run kind** so the skill's "Run
types & brief selection" logic picks the right behavior.

**Timezone (do this the easy way):** if the Routine scheduler lets you pick a timezone, set it to
**America/Chicago** and just use the CT times below — the platform then handles DST for you. If it only
accepts UTC cron, use the season-correct column (the US switches CDT↔CST on the 2nd Sun of March / 1st
Sun of Nov), and re-check twice a year.

| Run | Time (CT) | Cron (CDT = summer, UTC−5) | Cron (CST = winter, UTC−6) | Prompt to paste |
|---|---|---|---|---|
| **Pre-market full brief** | 06:30 | `30 11 * * 1-5` | `30 12 * * 1-5` | `Run the market-briefing skill as the pre-market full brief for today. First: pip install "psycopg[binary]". On the 1st weekday of the month produce the monthly-plan brief, otherwise the daily-status brief. Read state from Postgres via lib/, run the full pipeline, send the brief to Telegram, and log every suggestion. Suggestion-only — never execute trades.` |
| **Intraday check** | ~12:00 | `00 17 * * 1-5` | `00 18 * * 1-5` | `Run the market-briefing skill as an intraday check. First: pip install "psycopg[binary]". Do BOTH: (A) monitor open entry-zones (lib.db.get_open_suggestions) + holdings (lib.db.get_holdings) for zone triggers / stop / invalidation; (B) a BOUNDED opportunity scan within settings.intraday (<=25 data calls, deep-analyze <=3 names, compact depth) — refresh the radar, pull today's movers + breaking news, run promising names through the historical check + buy-gate. Send Telegram ONLY if a new buy cleared the gate, a buy zone triggered, or a holding hit its stop/invalidation — otherwise log to the radar/Watch suggestions silently. Suggestion-only.` |
| **Post-market analysis** | 15:10 | `10 20 * * 1-5` | `10 21 * * 1-5` | `Run the market-briefing skill as the post-market analysis. First: pip install "psycopg[binary]". For the relevant slice (watched + held names) write daily_snapshots + notable stock_observations and append one regime line to data/lessons.md. Stay quiet (no Telegram) unless a holding broke down. Suggestion-only.` |

**Verify go-live (trigger each once, manually):**
1. Pre-market → a full brief posts to Telegram (one screen, correct type for the date) and new
   `suggestions` rows appear for today.
2. Intraday → stays **silent** when nothing triggered (correct), or posts a `⚡ Market Alert` if a
   new buy cleared the gate, a buy zone triggered, or a holding hit its stop/invalidation.
3. Post-market → **no** Telegram (unless a breakdown); `daily_snapshots` + `stock_observations` rows
   for today appear and `data/lessons.md` got a new regime line (the cloud run commits/pushes the
   file change, or writes via the repo — confirm the regime line landed).

## Pause / adjust cadence

- **Pause** any Routine from its page in claude.ai → Code → Routines (toggle off) — e.g. pause the
  intraday check first if Pro usage runs tight.
- **Lighten** by pausing the intraday Routine (keep 06:30 + 15:10 only) — the cheapest way
  to cut ~33% of runs.
- **Change times** by editing the cron; keep the prompt's run-kind wording intact.

## Budget / measurement plan (~2 weeks)

The three runs ≈ 2–2.5× one full daily run. Start on **Pro** and watch the daily run-cap + token
budget for ~2 weeks. If it's tight, in order: (1) pause the intraday check (keep morning + post-market);
(2) lighten the morning scan (smaller shortlist); (3) only then consider Max. Record the decision
here once measured.
