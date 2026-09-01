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

| Run | Time (CT) | Cron (CDT = summer, UTC−5) | Cron (CST = winter, UTC−6) |
|---|---|---|---|
| **Pre-market full brief** | 06:30 | `30 11 * * 1-5` | `30 12 * * 1-5` |
| **Intraday check** | ~12:00 | `00 17 * * 1-5` | `00 18 * * 1-5` |
| **Post-market analysis** | 15:10 | `10 20 * * 1-5` | `10 21 * * 1-5` |

### Pre-market instructions to paste

> Run the market-briefing skill with run kind `pre-market`. Start and finalize one analysis_runs
> lifecycle row and keep an evidence-backed ledger of actual DB writes and returned Telegram message
> IDs. Build a fresh packet for this run; run Analyst then Checker before every actionable
> conclusion. Label prior-session quotes as prior close/provisional with their as_of timestamps and
> never call a prior-close zone a live trigger. On the first weekday of the month produce the
> monthly-plan brief; otherwise produce daily-status. Send/log only what the skill's notification
> policy requires. Suggestion-only: never place, modify, or cancel a trade, and never edit or commit
> repository files.

### Intraday instructions to paste

> Run the market-briefing skill with run kind `intraday`. Start and finalize one analysis_runs row.
> Treat morning suggestions and entry zones only as historical candidate seeds. Independently fetch
> current holdings, macro, timestamped quotes, relevant company/market news, earnings/events, and
> technical context within settings.intraday (at most 25 data calls and 3 compact-depth names).
> Recompute zone, stop, target, invalidation, and sizing; run Analyst then Checker. A quote older than
> settings.data.max_actionable_quote_age_minutes, missing freshness, conflicting prices, or prior-plan
> leakage vetoes a Buy/Add/Trim/Exit alert. Send Telegram only when the freshly checked result meets
> the notification policy; otherwise perform only the permitted silent DB writes. Record actual
> writes/sends in the run row. Suggestion-only; never execute trades or write to git files.

### Post-market instructions to paste

> Run the market-briefing skill with run kind `post-market`. Start and finalize one analysis_runs
> row and build a fresh close packet for watched and held names; do not inherit the intraday verdict.
> Write snapshots, sparse notable observations with run_id, and one regime lesson. Any new
> Trim/Exit/breakdown alert requires a quote no older than the configured 20-minute maximum,
> recomputed levels, Analyst pass, and Checker approval. Keep Telegram quiet except for the skill's
> explicit fresh-crossing/breakdown policy. The final summary must match actual successful DB calls
> and returned Telegram message IDs. Suggestion-only; never execute trades or write to git files.

**Verify go-live (trigger each once, manually):**
1. Pre-market → a full brief posts to Telegram, prior-close data is labeled provisional, and the
   `analysis_runs` row matches its suggestions/message IDs.
2. Intraday → stays **silent** when nothing triggered (correct), or posts a `⚡ Market Alert` if something fires.
3. Post-market → **no** Telegram (unless a freshly checked breakdown); `daily_snapshots` +
   `stock_observations` rows for today appear, a new `lessons` row with `category='regime'` exists,
   and the run row records actual write counts.

---

## Manual / dry-run testing

**"Run now" on a saved Routine always runs live** — it uses the Routine's fixed Instructions verbatim
(Telegram sends + Supabase writes happen for real), and there's no prompt to override it. Don't use
"Run now" to poke at a Routine out of curiosity.

To test safely, start a **new session** (not "Run now") in the `stocks-agent` environment and ask for
a dry run, e.g.:

> Run the market-briefing skill as a dry-run pre-market brief for today — preview only, don't send
> Telegram or write anything to Supabase.

The skill's dry-run gate (see `skills/market-briefing/SKILL.md`) runs the full pipeline for real but
prints what it *would* have sent/written instead of actually sending or writing. Only activates when
the request says "dry run" / "test mode" / "preview" — scheduled runs never trigger it.

---

## Pause / adjust cadence

- **Pause** any Routine from its page in claude.ai → Code → Routines (toggle off).
- **Lighten** by pausing the intraday Routine (keep 06:30 + 15:10 only) — cuts ~33% of runs.
- **Change times** by editing the cron; keep the Instructions' run-kind wording intact.

## Budget / measurement plan (~2 weeks)

The three runs ≈ 2–2.5× one full daily run. Start on **Pro** and watch the daily run-cap + token
budget for ~2 weeks. If it's tight, in order: (1) pause the intraday check; (2) lighten the morning
scan (smaller shortlist); (3) only then consider Max.
