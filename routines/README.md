# Cloud Routine setup (Anthropic Routines)

The agent runs as ephemeral **Anthropic Cloud Routines** on the owner's **Pro plan**, headless.
Each run clones this repo, runs the `market-briefing` skill, reaches Supabase + the data APIs +
Telegram, then exits. **All persistent state lives in Supabase Postgres — nothing persists in the
runtime between runs.**

## One-time configuration (owner, in claude.ai → Code → Routines)

### 1. Create a shared cloud environment

In the Routine editor, click the cloud icon (bottom-right of the Instructions box) → **Add environment**:

- **Name:** `stocks-agent`
- **Network access:** `Full` (unrestricted — needed for Supabase HTTPS + external APIs)
- **Environment variables:**
  ```
  SUPABASE_URL=https://<project-ref>.supabase.co
  SUPABASE_SERVICE_ROLE_KEY=eyJ...
  FINNHUB_API_KEY=...
  ALPHAVANTAGE_API_KEY=...
  TELEGRAM_BOT_TOKEN=...
  TELEGRAM_CHAT_ID=...
  ```
- **Setup script:**
  ```bash
  #!/bin/bash
  pip install supabase --ignore-installed
  ```

All 4 Routines use this same environment.

### 2. Connect the repo

Point each Routine at the `RajSivapu/stocks-agent` GitHub repo.

### 3. Permissions tab

Leave **Allow unrestricted git push** OFF — the agent is read-only on the repo.

---

## Healthcheck routine (run once to verify)

- **Instructions:** `Run "python scripts/healthcheck.py" and report the JSON output.`
- **Schedule:** once / manual trigger.
- **Expected:** Telegram DM with `{"postgres":"ok","finnhub":"ok","yahoo":"ok","telegram":"ok"}`.

---

## Scheduled runs (go-live)

Create **three weekday (Mon–Fri) Routines**, all using the `stocks-agent` environment above.

**Timezone:** crons below are UTC. US switches CDT↔CST on the 2nd Sun of March / 1st Sun of Nov — update the UTC offset twice a year.

| Run | Time (CT) | Cron (CDT = summer, UTC−5) | Cron (CST = winter, UTC−6) | Instructions to paste |
|---|---|---|---|---|
| **Pre-market full brief** | 06:30 | `30 11 * * 1-5` | `30 12 * * 1-5` | `Run the market-briefing skill as the pre-market full brief for today. On the 1st weekday of the month produce the monthly-plan brief, otherwise the daily-status brief. Read state from Supabase via lib/, run the full pipeline, send the brief to Telegram, and log every suggestion. Suggestion-only — never execute trades.` |
| **Intraday check** | ~12:00 | `00 17 * * 1-5` | `00 18 * * 1-5` | `Run the market-briefing skill as an intraday check. Do BOTH: (A) monitor open entry-zones (lib.db.get_open_suggestions) + holdings (lib.db.get_holdings) for zone triggers / stop / invalidation; (B) a BOUNDED opportunity scan within settings.intraday (<=25 data calls, deep-analyze <=3 names, compact depth) — refresh the radar, pull today's movers + breaking news, run promising names through the historical check + buy-gate. Send Telegram ONLY if a new buy cleared the gate, a buy zone triggered, or a holding hit its stop/invalidation — otherwise log to the radar/Watch suggestions silently. Suggestion-only.` |
| **Post-market analysis** | 15:10 | `10 20 * * 1-5` | `10 21 * * 1-5` | `Run the market-briefing skill as the post-market analysis. For the relevant slice (watched + held names) write daily_snapshots + notable stock_observations and insert one regime line via lib.db.insert_lesson(). Stay quiet (no Telegram) unless a holding broke down. Suggestion-only.` |

**Verify go-live (trigger each once, manually):**
1. Pre-market → a full brief posts to Telegram and new `suggestions` rows appear for today.
2. Intraday → stays **silent** when nothing triggered (correct), or posts a `⚡ Market Alert` if something fires.
3. Post-market → **no** Telegram (unless a breakdown); `daily_snapshots` + `stock_observations` rows for today appear and a new `lessons` row with `category='regime'` exists in Supabase for today.

---

## Pause / adjust cadence

- **Pause** any Routine from its page in claude.ai → Code → Routines (toggle off).
- **Lighten** by pausing the intraday Routine (keep 06:30 + 15:10 only) — cuts ~33% of runs.
- **Change times** by editing the cron; keep the Instructions' run-kind wording intact.

## Budget / measurement plan (~2 weeks)

The three runs ≈ 2–2.5× one full daily run. Start on **Pro** and watch the daily run-cap + token
budget for ~2 weeks. If it's tight, in order: (1) pause the intraday check; (2) lighten the morning
scan (smaller shortlist); (3) only then consider Max.
