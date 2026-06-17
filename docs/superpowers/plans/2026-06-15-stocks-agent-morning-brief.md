# Stocks Agent — Market Briefing (v1, pre-market cadence) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code–orchestrated personal investing assistant for a **beginner investing $500/month (Year-1 foundation stage)** that produces ONE simple, plain-English brief (suggestion-only), sends it via Telegram on a **weekly** cadence (with monthly "money moves"), logs every call for a self-improving track record, and runs automatically without the owner's machine on.

**Architecture:** Approach A — Claude Code is the orchestrator. A custom project skill (`market-briefing`) is the brain and holds all strategy + guardrails. Market data comes from read-only MCP servers (Finnhub primary, Alpha Vantage secondary, yfinance fallback). The agent has **no trade-execution tools**, so it is read-only by construction. Delivery is via a **Telegram bot** (email optional fallback). A `/schedule` cloud routine runs it **weekly (Monday 07:30 ET)** — the owner's laptop does not need to be on. (Cadence is config-driven; daily is overkill at $500/mo and can be enabled later.)

**Tech Stack:** Claude Code (skills + MCP + `/schedule`), JSON config files, Markdown skill, Finnhub / Alpha Vantage / yfinance MCP servers, Telegram Bot API (delivery).

**Scope:** This plan delivers v1 = spec Phases 1 (manual run) + 2 (daily schedule). Phase 3 (intraday watch) and Phase 4 (Telegram + accuracy report) are deliberately out of scope and get their own plans.

**Execution note:** This plan is designed to be executed on **Sonnet**. Several tasks require the **owner** to perform an external action (create a free API key, approve an MCP install, confirm an email arrived). Those steps are marked **[OWNER ACTION]** — pause and ask the owner, do not fabricate keys or skip verification.

**No-git note:** v1 is local-only by request. Steps that would normally `git commit` instead say **[no commit — local only]**. Do not run `git init` or `git commit` unless the owner later asks.

---

## Decisions (2026-06-16, confirmed with owner before implementation)

These OVERRIDE the as-written plan where they conflict:

1. **Cadence = DAILY, weekdays (Mon–Fri 07:30 ET)** — not weekly. The owner wants the daily learning habit. Money *moves* stay **monthly** (full block on the month's first brief, 1-line reminder otherwise). Affects `settings.json.schedule` (Task 2), the skill's run mode + format wording (Task 7), README (Task 9), and the `/schedule` routine (Task 10 = daily weekdays). Cost note: daily ≈ 5× the Claude (Pro) usage of weekly; free data APIs are fine because Alpha Vantage's 25/day resets each day.
2. **Growth pick = one best pick + reason, PLUS a 2–3 name shortlist** to learn from. The $100 still goes to the single best pick unless the owner says otherwise; the shortlist is for awareness, not for splitting the $100. Affects the skill's capital section + briefing format (Task 7).
3. **Delivery = Telegram primary**, email optional fallback (as the plan already states).
4. **Delete the stray `skills/morning-briefing/` skill** (older design) as part of Task 1 — only `skills/market-briefing/` should exist.
5. **No Anthropic API key needed** — the agent IS Claude (runs on the owner's Claude Pro subscription / `/schedule` cloud). Finnhub + Alpha Vantage keys already exist in `secrets.local.json`; only the Telegram bot token + chat id are still needed (Task 4).
6. **Add a transparent Stock Health Score (0–100)** (inspired by a scorecard reel — NVDA example), applied to *every* analyzed single stock: sub-scores for growth, financial health, valuation (growth forgives high P/E, PEG-style) → risk band (≥70 lower / 50–69 medium / <50 higher). Shown as a small tag on pick/watch lines; full sub-scores logged. It is a quality/risk SUMMARY, not a buy signal; the multi-role lens still decides. ETFs are tagged "diversified" not scored; missing inputs → partial score, never invented. Config in `settings.json.scoring` (Task 2); methodology in the skill (Task 7); fields in the log (Task 7).
7. **Add the "3 deep-dive checks"** (inspired by an Anthropic-finance reel), built on free data ($0, no dependency on paid Claude-for-Financial-Services plugins): (a) a **weekly catch-up** (news/ratings/filings/earnings-dates for watchlist + holdings) folded into the FIRST brief of each week (Task 7 + `settings.json.deep_dives`); (b) a new on-demand **`equity-research`** skill — plain-English research note re-checking a stock's thesis (Task 7B); (c) a new on-demand **`earnings-review`** skill — free earnings *results* digest, with a deeper call digest when the owner pastes a transcript, since full transcripts are paid (Task 7C). #b/#c are most useful once the owner owns stocks but work now on watchlist names for learning. Pulls forward the "valuation + earnings-review skills" the README listed as future.
8. **Make the agent adaptive & self-improving** (owner wants a tool that "learns from day 1," compares old vs new trends, and personalizes as he grows — clarified that the backend is an **LLM agent with memory + self-review, NOT a trained price-prediction model**, which is deliberately out of scope as it overfits/misleads at this stage). Three additions: (a) **Dynamic watchlist** — the radar now AUTO-PROMOTES persistently-strong names into `watchlist.json` and RETIRES stale ones it added, reporting every change in the brief, never removing held/owner-added names, respecting per-bucket caps (`settings.json.radar.auto_manage_watchlist`). This changes the old "propose-only, never auto-edit" rule and the Task 8 watchlist-unchanged check. (b) **Learning memory** — new `data/learning.md` the agent reads + updates each run (distilled lessons + a market-regime log for trend comparison); config `settings.json.learning`. (c) **Adaptive capital** — `capital` now a range ($500 min → $1,000 max, current $500), split by the 70/20/10 allocation and scaled as the portfolio grows. First real buy ~end of June 2026; the agent will suggest a concrete day with live data (DCA timing is mostly noise — consistency matters more).

---

## File Structure

All paths are relative to `/Users/rajrupesh/Documents/Raj/stocks-agent/`.

| File | Responsibility |
|---|---|
| `README.md` | How the agent works, setup, how to run, how to edit config. |
| `config/settings.json` | Strategy (70/20/10), schedule time, delivery prefs, risk params, guardrail flags. |
| `config/watchlist.json` | Watchlist tickers grouped by bucket (things to watch, not buy orders). **Dynamic:** the agent auto-adds/retires names it discovered and reports changes; never removes held/owner-added names. |
| `config/portfolio.json` | What the owner actually OWNS (holdings + avg cost). Manually maintained (Robinhood has no API). Drives hold/trim/sell + concentration checks. |
| `config/secrets.local.json` | Free data API keys + Telegram bot token/chat id. Local only, never shared/committed. |
| `skills/market-briefing/SKILL.md` | The orchestration brain: data usage, briefing format, suggestion format, guardrails, logging, tone, disclaimer. Also produces the weekly catch-up. |
| `skills/equity-research/SKILL.md` | On-demand: plain-English research note re-checking a stock's bull/bear thesis ("does my reason to hold still hold?"). Read-only, suggestion-only. |
| `skills/earnings-review/SKILL.md` | On-demand: earnings *results* digest (actual vs expected, guidance, reaction) from free data; deeper call digest if the owner pastes a transcript. Read-only, suggestion-only. |
| `data/briefings/` | Dated archive of every generated briefing (Markdown). |
| `data/suggestions-log.jsonl` | One JSON line per suggestion for later accuracy review. |
| `data/radar.json` | Agent-curated candidate list (capped, auto-pruned). Created by the skill if missing. NOT the watchlist; feeds auto-promotion into it. |
| `data/learning.md` | The agent's running "learning memory": distilled lessons + a market-regime log (trend-vs-prior-trend), read back and updated each run so it improves over time. Memory + self-review, not a trained model. |

---

## Task 1: Create the folder skeleton

**Files:**
- Create: `config/`, `skills/market-briefing/`, `data/briefings/` (directories)
- Create: `data/suggestions-log.jsonl` (empty)
- Create: `data/learning.md` (seeded learning-memory notebook — see Decision 8 / `settings.json.learning`)
- Create: `data/.gitkeep`, `data/briefings/.gitkeep`

- [ ] **Step 1: Create directories and empty files**

```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
mkdir -p config skills/market-briefing data/briefings
touch data/suggestions-log.jsonl data/.gitkeep data/briefings/.gitkeep
```

- [ ] **Step 2: Verify structure**

Run: `find /Users/rajrupesh/Documents/Raj/stocks-agent -type d -not -path '*/docs/*' | sort`
Expected output includes:
```
/Users/rajrupesh/Documents/Raj/stocks-agent
/Users/rajrupesh/Documents/Raj/stocks-agent/config
/Users/rajrupesh/Documents/Raj/stocks-agent/data
/Users/rajrupesh/Documents/Raj/stocks-agent/data/briefings
/Users/rajrupesh/Documents/Raj/stocks-agent/skills/market-briefing
```

- [ ] **Step 3: [no commit — local only]**

---

## Task 2: Write `config/settings.json`

**Files:**
- Create: `config/settings.json`

- [ ] **Step 1: Write the settings file**

```json
{
  "owner": {
    "name": "Rajrupesh",
    "email": "rupesh.sivapu@gmail.com",
    "timezone": "America/Chicago"
  },
  "strategy": {
    "style": "long-term core + some swing",
    "allocation": { "core": 0.70, "growth": 0.20, "speculative": 0.10 },
    "core_prefers_dollar_cost_averaging": true,
    "stage": "year1_foundation"
  },
  "capital": {
    "monthly_investment_usd_min": 500,
    "monthly_investment_usd_max": 1000,
    "monthly_investment_usd_current": 500,
    "scale_with_portfolio": true,
    "split_by_allocation": true,
    "monthly_split_usd_at_500": { "core": 350, "growth": 100, "opportunities_fund": 50 },
    "speculative_mode": "learning",
    "speculative_go_live_when_bucket_usd": 500,
    "income_goal_usd_per_month": 200,
    "income_goal_realistic_from": "year_2_to_3",
    "_note": "Owner starts at $500/mo (first buy by end of June 2026) and will scale toward $1,000/mo, building slowly. Each run, read monthly_investment_usd_current AND the actual portfolio size, then split by the 70/20/10 allocation (so $500 -> 350/100/50; $1000 -> 700/200/100) and ADAPT emphasis as the portfolio grows. $200/mo income stays a Year-2/3 target once the speculative bucket is real money — NOT Year-1. Until the opportunities fund reaches $500, speculative ideas are LEARNING/paper-trade only."
  },
  "schedule": {
    "cadence": "daily_weekdays",
    "brief_days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    "brief_time_et": "07:30",
    "monthly_money_moves_on_first_brief_of_month": true,
    "market_open_et": "09:30",
    "market_close_et": "16:00",
    "_cadence_note": "Owner chose a DAILY weekday brief (07:30 ET) for the learning habit. Actionable money moves stay MONTHLY (full block on the first brief of the calendar month; a 1-line reminder otherwise). Free data APIs are fine — Alpha Vantage's 25/day budget resets each day. Cost: daily uses ~5x the Claude (Pro) usage of weekly; no separate API bill. v2 may add an intraday mode."
  },
  "delivery": {
    "telegram": { "enabled": true },
    "email": { "enabled": false, "to": "rupesh.sivapu@gmail.com", "note": "optional fallback; needs Gmail sign-in, may not work in scheduled cloud runs" }
  },
  "risk": {
    "max_position_pct_of_bucket": { "core": 25, "growth": 20, "speculative": 10 },
    "always_require_stop_loss": true,
    "loud_warning_on_speculative": true,
    "daily_loss_limit_pct": 3,
    "circuit_breaker_consecutive_losses": 3
  },
  "data": {
    "primary": "finnhub",
    "secondary": "alphavantage",
    "fallback": "yfinance",
    "price_delay_minutes": 15
  },
  "market_scan": {
    "enabled": true,
    "universe": "all_us_stocks_all_sectors",
    "method": "broad free screeners narrow the whole market to a shortlist, then deep-analyze only the shortlist + watchlist (respects API rate limits). NEVER iterate the full ticker list.",
    "universe_filters": {
      "min_price": 5,
      "min_avg_daily_volume": 1000000,
      "min_market_cap": 2000000000,
      "note": "Quality/liquidity floor — removes penny stocks & illiquid junk before scoring. Eliminates most noise and keeps the scan cheap. (~$2B = mid-cap and up.)"
    },
    "screens": [
      "top_gainers", "top_losers", "most_active", "unusual_volume",
      "near_52w_high_quality", "oversold_quality", "insider_buying_clusters"
    ],
    "max_candidates_surfaced": 10,
    "sectors": "all"
  },
  "radar": {
    "enabled": true,
    "max_size": 15,
    "auto_prune_after_days_inactive": 7,
    "promote_to_watchlist_after_days_relevant": 5,
    "promotion_requires_owner_approval": false,
    "auto_manage_watchlist": true,
    "report_watchlist_changes_in_brief": true,
    "never_remove_held_or_owner_added": true,
    "watchlist_max_per_bucket": { "core": 35, "growth": 20, "speculative": 6 },
    "note": "DYNAMIC watchlist (owner opted into autonomous management). The agent self-curates data/radar.json from scanner/news finds, then AUTO-PROMOTES persistently-strong names into watchlist.json and RETIRES stale/broken ones it previously added — reporting every change in the brief (transparent + reversible). It NEVER removes a name the owner holds or manually added (only names IT added), respects watchlist_max_per_bucket, and the owner can veto/revert anytime."
  },
  "learning": {
    "enabled": true,
    "memory_file": "data/learning.md",
    "_note": "The agent keeps a running 'learning memory': distilled lessons + trend observations (e.g. 'my biotech calls underperform', 'growth picks do better bought on red days', 'market regime shifted from X to Y'). It READS this back every run and APPLIES it, and UPDATES it with new lessons + a compare of the latest trend vs the prior trend. This is the honest, robust version of 'learn from day 1 / get smarter over time' — memory + self-review over an LLM agent, NOT a trained price-prediction model (deliberately out of scope; such models overfit and mislead at this stage)."
  },
  "scoring": {
    "enabled": true,
    "model": "health_score_0_100",
    "weights": { "growth": 35, "financial_health": 35, "valuation": 30 },
    "risk_bands": { "lower_risk_min": 70, "medium_risk_min": 50 },
    "_note": "Transparent 0-100 stock quality/risk score, shown as a small tag (e.g. '76/100 low-med risk'). It is a SUMMARY of quality+risk, NOT a buy signal — the multi-role lens still makes the call. Valuation forgives a high P/E when growth is strong (PEG-style), like NVDA. Broad ETFs are diversified, so the score is skipped/noted rather than computed like a single stock. Qualitative risk flags (e.g. customer concentration) are best-effort from news; the score records which inputs it had and degrades gracefully when one is missing."
  },
  "deep_dives": {
    "weekly_catchup_in_brief": true,
    "catchup_day": "Monday",
    "on_demand_skills": ["equity-research", "earnings-review"],
    "_note": "Inspired by the Anthropic-finance reel, built on FREE data ($0). Weekly catch-up = the first brief of each week (Monday) summarizes last week's news/ratings/filings + this week's earnings dates for watchlist + holdings. Two deeper checks are ON-DEMAND skills: 'equity-research' (plain-English research note re-checking a stock's thesis) and 'earnings-review' (earnings results digest; paste a transcript for a fuller call digest — full transcripts are paid, so the free version covers actual-vs-expected, guidance, and reaction). Most useful once you own stocks; usable now on watchlist names for learning."
  },
  "guardrails": {
    "execution_allowed": false,
    "rule": "Suggestion-only. The agent must never place, modify, or cancel a trade."
  }
}
```

- [ ] **Step 2: Validate JSON**

Run: `python3 -c "import json; json.load(open('/Users/rajrupesh/Documents/Raj/stocks-agent/config/settings.json')); print('valid')"`
Expected: `valid`

- [ ] **Step 3: [no commit — local only]**

---

## Task 3: Write `config/watchlist.json` (starter watchlist)

**Files:**
- Create: `config/watchlist.json`

The watchlist is the set the agent covers **in detail every day**. It is intentionally broad and
**sector-diversified across all 11 GICS sectors** + major ETFs, so "all major tickers / all
industries" are tracked closely. Separately, the **market scanner** (Task 7) screens the *entire*
US market for opportunities beyond this list. These are tickers to WATCH, not buy orders.

- [ ] **Step 1: Write the watchlist file**

```json
{
  "_comment": "Tickers covered in DETAIL daily, sector-diversified. The agent ALSO scans the WHOLE US market (all sectors) for opportunities beyond this list — see settings.json market_scan. Edit freely.",
  "core": [
    "VOO", "VTI", "QQQ", "SCHD", "DIA", "IWM", "VXUS",
    "AAPL", "MSFT", "GOOGL", "AMZN", "BRK.B",
    "JPM", "V", "MA", "UNH", "JNJ", "LLY",
    "PG", "KO", "WMT", "COST", "HD",
    "XOM", "CVX", "CAT", "HON", "LIN", "NEE", "PLD", "AMT"
  ],
  "growth": [
    "NVDA", "AMD", "META", "TSLA", "AVGO", "CRM",
    "NFLX", "ORCL", "ADBE", "NOW", "SHOP", "UBER"
  ],
  "_speculative_note": "Seeded with liquid, diversified HIGHER-VOLATILITY thematic/sector ETFs (watch, not buy). This is the character of the bucket without single-stock blow-up risk. Individual speculative names come from the daily scanner + the owner's own conviction. Per-position sizing stays small (see settings.json risk).",
  "speculative": ["SMH", "ARKK", "XBI"]
}
```

- [ ] **Step 2: Validate JSON**

Run: `python3 -c "import json; d=json.load(open('/Users/rajrupesh/Documents/Raj/stocks-agent/config/watchlist.json')); print(len(d['core']), len(d['growth']), len(d['speculative']))"`
Expected: `31 12 3`

- [ ] **Step 3: [OWNER ACTION] Confirm the watchlist**

Tell the owner: "The watchlist spans all 11 sectors + major ETFs (31 core, 12 growth), plus the speculative bucket is seeded with 3 liquid higher-volatility ETFs (SMH semis, ARKK innovation, XBI biotech) as *watch* items — research-backed as appropriate satellites without single-stock risk. On top of all this, the agent scans the *entire* US market daily for opportunities outside the list. Want to add/remove any names, including your own speculative picks?" Apply edits if requested. (More names = more API calls; if rate limits bite, the agent prioritizes movers + scan results.)

- [ ] **Step 4: [no commit — local only]**

---

## Task 3B: Write `config/portfolio.json` (what you actually own)

**Files:**
- Create: `config/portfolio.json`

This is the owner's real holdings — separate from the watchlist — so the agent can give accurate
hold/trim/sell advice and concentration warnings. **If the read-only Robinhood connection is set up
(Task 5B), holdings auto-sync from there** and this file is the fallback/cache. If not, it's the
manual source of truth (owner updates it, or says "I bought/sold X").

- [ ] **Step 1: Write the portfolio file (starts empty)**

```json
{
  "_comment": "Your ACTUAL holdings (not the watchlist). Robinhood has no API, so update this manually after trades — or tell the agent 'I bought 10 NVDA at 210' and it edits this file. Leave holdings [] if you own nothing yet.",
  "cash_available_usd": 0,
  "holdings": []
}
```

Example of a filled holding (for reference — do not add unless the owner provides real positions):
`{"ticker": "NVDA", "shares": 10, "avg_cost": 210.00, "bucket": "growth"}`

- [ ] **Step 2: Validate JSON**

Run: `python3 -c "import json; d=json.load(open('/Users/rajrupesh/Documents/Raj/stocks-agent/config/portfolio.json')); print('holdings:', len(d['holdings']))"`
Expected: `holdings: 0`

- [ ] **Step 3: [OWNER ACTION] Ask for current holdings**

Ask the owner: "Do you already own any stocks/ETFs? If so, give me ticker + roughly how many shares + your average buy price, and I'll add them so the brief can track them. If not, we start empty and add as you buy." Fill `holdings` with anything provided.

- [ ] **Step 4: [no commit — local only]**

---

## Task 4: Create free data API keys

**Files:**
- Create: `config/secrets.local.json`

- [ ] **Step 1: [OWNER ACTION] Create a free Finnhub key**

Ask the owner to:
1. Go to https://finnhub.io/register and sign up (free, no card).
2. Copy the API key from the dashboard.
Wait for the key. Do not proceed without it.

- [ ] **Step 2: [OWNER ACTION] Create a free Alpha Vantage key**

Ask the owner to:
1. Go to https://www.alphavantage.co/support/#api-key and request a free key (no card).
2. Copy the key.
Wait for the key.

- [ ] **Step 2b: [OWNER ACTION] Create a free Telegram bot (delivery channel)**

Ask the owner to:
1. In Telegram, message **@BotFather** → `/newbot` → follow prompts → copy the **bot token**.
2. Message the new bot once (say "hi"), then get the **chat id**: open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the `chat.id` value.
Wait for both the bot token and chat id. (All free, no card.)

- [ ] **Step 3: Write the secrets file**

Replace the placeholders with the real keys the owner provided:

```json
{
  "finnhub_api_key": "<OWNER_PROVIDED_FINNHUB_KEY>",
  "alphavantage_api_key": "<OWNER_PROVIDED_ALPHAVANTAGE_KEY>",
  "telegram_bot_token": "<OWNER_PROVIDED_TELEGRAM_BOT_TOKEN>",
  "telegram_chat_id": "<OWNER_PROVIDED_TELEGRAM_CHAT_ID>"
}
```

- [ ] **Step 4: Verify the keys work (read-only smoke test)**

Run (Finnhub quote for AAPL):
```bash
KEY=$(python3 -c "import json;print(json.load(open('/Users/rajrupesh/Documents/Raj/stocks-agent/config/secrets.local.json'))['finnhub_api_key'])")
curl -s "https://finnhub.io/api/v1/quote?symbol=AAPL&token=$KEY"
```
Expected: a JSON object containing a current price field `"c"` with a non-zero number.

Run (Alpha Vantage news sentiment smoke test):
```bash
KEY=$(python3 -c "import json;print(json.load(open('/Users/rajrupesh/Documents/Raj/stocks-agent/config/secrets.local.json'))['alphavantage_api_key'])")
curl -s "https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers=AAPL&apikey=$KEY" | head -c 300
```
Expected: JSON beginning with a `"feed"` or `"items"` field (not an error/"rate limit" message).

Run (Finnhub insider transactions smoke test — powers the Smart Money section):
```bash
KEY=$(python3 -c "import json;print(json.load(open('/Users/rajrupesh/Documents/Raj/stocks-agent/config/secrets.local.json'))['finnhub_api_key'])")
curl -s "https://finnhub.io/api/v1/stock/insider-transactions?symbol=AAPL&token=$KEY" | head -c 300
```
Expected: JSON with a `"data"` array. If this specific endpoint is not on the free tier, note it
and treat insider data as best-effort (skip gracefully when unavailable).

Run (Telegram send test — the delivery channel):
```bash
python3 -c "import json;d=json.load(open('/Users/rajrupesh/Documents/Raj/stocks-agent/config/secrets.local.json'));print(d['telegram_bot_token'],d['telegram_chat_id'])" | { read T C; curl -s "https://api.telegram.org/bot$T/sendMessage" -d "chat_id=$C" -d "text=✅ Stocks agent connected. Test message."; }
```
Expected: `{"ok":true,...}` and the owner receives the test message in Telegram. If it fails, fix the
bot token / chat id before continuing.

If any required source (quote, news) or Telegram fails, stop and resolve with the owner before continuing.

- [ ] **Step 5: [no commit — local only]** Note: `config/secrets.local.json` must never be shared.

---

## Task 5: Add the read-only data MCP servers

**Files:** none (Claude Code MCP config)

> The exact MCP install incantation can drift. **Confirm the current command via docs first**, then install. This is a real verification step, not a placeholder.

- [ ] **Step 1: Confirm current install commands**

Use context7 / web to confirm the current way to add: (a) the Alpha Vantage MCP server, (b) a Finnhub MCP server, (c) a yfinance MCP server, to Claude Code. Record the exact commands found.

References to check:
- Alpha Vantage MCP: https://mcp.alphavantage.co/
- Finnhub / yfinance community MCP servers (e.g. search `mcpservers.org`, `glama.ai/mcp`).

- [ ] **Step 2: Add Alpha Vantage MCP (hosted)**

Using the confirmed command (example form — verify before running):
```bash
claude mcp add --transport http alphavantage "https://mcp.alphavantage.co/mcp?apikey=<ALPHAVANTAGE_KEY>"
```

- [ ] **Step 3: Add Finnhub MCP**

Install the confirmed Finnhub MCP server, passing the Finnhub key via its documented env var / arg.

- [ ] **Step 4: Add yfinance MCP (fallback, no key)**

Install the confirmed yfinance MCP server.

- [ ] **Step 5: Verify the servers are connected and return data**

Run: `claude mcp list`
Expected: `alphavantage`, the finnhub server, and the yfinance server all listed as connected.

Then, in a Claude Code session, ask the agent to fetch the current AAPL quote via the MCP tools.
Expected: a price is returned (within ~15-min delay). If a server fails, fall back per `settings.json` (`finnhub → alphavantage → yfinance`) and note which server is unavailable.

- [ ] **Step 6: [no commit — local only]**

---

## Task 5B: (Optional, READ-ONLY) Portfolio sync — only once the owner has invested

**Files:** none. Goal: auto-sync holdings WITHOUT any trading capability — when there's a portfolio
to sync. **Robinhood's official MCP bundles `place_order` (execution), so we do NOT use it in v1.**

- [ ] **Step 1: [OWNER ACTION] Skip if not invested yet**

Confirm with the owner: have you funded a brokerage account yet? **If no → skip this whole task**;
holdings stay in the manual `config/portfolio.json` (empty for now). Revisit when you invest.

- [ ] **Step 2: When invested — connect a READ-ONLY aggregator (no trading)**

Use a read-only-by-design source so the agent gets holdings but **never** a trading verb:
- **Plaid Investments** — read-only only (cannot place trades at all). Or
- **SnapTrade** — read-only by default (supports Fidelity, Schwab, Webull, E*TRADE, Public,
  Wealthsimple, Robinhood, 20+; trading stays OFF unless explicitly enabled — we do NOT enable it).
Pick a beginner-friendly broker the owner uses, connect it read-only, and sync positions into
`portfolio.json` (or read live).

- [ ] **Step 3: Verify it is truly read-only**

Confirm the connection exposes ONLY data (positions/holdings) and **NO** order/execution verb. If any
`place_order`/buy/sell/cancel verb appears, remove it immediately — not acceptable for v1.

- [ ] **Step 4: [no commit — local only]**

---

## Task 6: Verify the guardrail — no trade-execution tools exist

**Files:** none (verification only). This is the single most important safety check.

- [ ] **Step 1: List all available tools/MCP servers**

Run: `claude mcp list`
Inspect the connected MCP servers.

- [ ] **Step 2: Assert no execution capability is present**

Confirm NONE of the following are installed/connected: Alpaca *trading* MCP, the Robinhood **trading** MCP (`agent.robinhood.com/mcp/trading`), `robin_stocks`, any broker order API, any "place_order"/"submit_order"/"buy"/"sell"/"cancel" execution verb. Only read-only tools may be present: market data (Finnhub, Alpha Vantage, yfinance), the Robinhood **read-only** portfolio connection (if connected per Task 5B — data verbs only), and delivery (Gmail).

Expected: the only finance tools are read-only data/portfolio sources — zero execution verbs anywhere (including from the Robinhood connection). If any execution-capable tool/verb is found, **remove it** before continuing:
```bash
claude mcp remove <offending-server-name>
```

- [ ] **Step 3: Document the result**

Record in the README (Task 9) that as of this date, the agent has no execution tools and is read-only by construction.

- [ ] **Step 4: [no commit — local only]**

---

## Task 7: Write the orchestration skill `skills/market-briefing/SKILL.md`

**Files:**
- Create: `skills/market-briefing/SKILL.md`

This is the brain. It must be self-contained: an agent reading only this file should be able to produce a correct, safe briefing.

- [ ] **Step 1: Write the skill file**

````markdown
---
name: market-briefing
description: Use to generate Rajrupesh's US stock market briefing and suggestion-only trade ideas for his watchlist. Runs daily on weekdays pre-market (v1; v2 adds intraday) and on-demand. Reads config + watchlist + portfolio, pulls market data/news from read-only sources, scores each stock, sends the briefing to Telegram, and logs every suggestion. NEVER executes trades.
---

# Market Briefing — Personal Investing Assistant

You produce a US stock market briefing for a **beginner** investor (Rajrupesh) and send it to his
Telegram. You help him become better informed and more disciplined. You teach as you go.

## Run modes (the agent runs at a cadence, not only "morning")
Determine the mode from how/when you were invoked, and tailor the output:
- **pre-market** (v1; scheduled DAILY on weekdays, Mon–Fri ~07:30 ET): the full brief described below.
- **intraday** (v2): a quick scan that emails ONLY if something material changed since the last run
  — not a full report. (Not implemented in v1.)
- **on-demand**: the owner asks for a brief any time → produce the full brief for "now."
For v1, treat every run as **pre-market** unless told otherwise.

## ABSOLUTE RULE — READ FIRST
You are **suggestion-only**. You may **NEVER place, modify, or cancel any trade**, and you have
no tools to do so. You only produce written suggestions; Rajrupesh executes them manually on
Robinhood. If you ever appear to have an execution/order tool, **do not use it** — stop and warn
him that a guardrail has been violated.

## Inputs (read these first, every run)
1. `config/settings.json` — strategy, allocation (70/20/10), schedule, risk params, delivery.
2. `config/watchlist.json` — tickers to WATCH (interest), grouped by bucket.
3. `config/portfolio.json` — what the owner ACTUALLY OWNS (holdings + avg cost). Distinct from the
   watchlist. See "Portfolio awareness" below.
4. `config/secrets.local.json` — data API keys (used only by the read-only data MCP servers).

## Portfolio awareness (holdings source: Robinhood read-only OR portfolio.json)
Know what the owner actually OWNS. Two possible sources (set at build time):
- **Robinhood READ-ONLY MCP** (if connected per Task 5B) — auto-synced holdings, data verbs only.
  **You must never have a trading verb** (`place_order` etc.); if you ever see one, refuse to use it
  and warn the owner (guardrail breach). Execution is Project 2 only.
- **`config/portfolio.json`** — manual holdings (owner updates after trades, or says "I bought/sold
  X" and you edit the file). Use this if Robinhood read-only isn't connected; also a fallback/cache.
Use whichever is available to:
- Only say "💎 hold / 🔴 trim / sell what you own" for tickers ACTUALLY in `holdings`. If `holdings`
  is empty, do NOT fabricate ownership — skip those groups or note "no holdings logged yet."
- Warn on **over-concentration** vs the 70/20/10 target and on any oversized single position.
- Avoid suggesting buying MORE of something he's already heavily weighted in (suggest hold instead).
- Frame Sell/Trim against his real positions (use avg cost for gain/loss context).
**Never assume ownership from the watchlist** — watchlist = interest, portfolio = actual holdings.

## Data sources (read-only)
- **Primary: Finnhub** — quotes, fundamentals, earnings calendar, news + sentiment, and
  **insider (Form 4) transactions**.
- **Secondary: Alpha Vantage** — technical indicators (RSI, MACD, moving averages), news
  sentiment, and **TOP_GAINERS_LOSERS** (whole-market top movers/most-active in one call).
- **Fallback: yfinance** — quotes, and **predefined market screeners** (day_gainers, day_losers,
  most_actives, undervalued_large_caps, etc.) that pre-scan the entire US market for free.
Prices may be delayed ~15 min — fine for long-term/swing, never present them as live.

**Access method + fallback:** prefer the read-only MCP tools (`finnhub`, `alphavantage`, `yfinance`)
when they're available in this run. If they aren't (e.g. a restricted scheduled cloud run, or the
tools aren't exposed), **fall back to direct read-only HTTPS calls** to the same endpoints using the
keys in `config/secrets.local.json` (e.g. `https://finnhub.io/api/v1/quote?symbol=…&token=…`,
Alpha Vantage `NEWS_SENTIMENT`/`TOP_GAINERS_LOSERS`). These are GET/read-only — never any write/order
endpoint. Note in the brief which method/source you used if a primary was unavailable.

## News — always read the LATEST (do this every run)
1. **General market news:** pull the latest top market headlines (Finnhub market-news endpoint /
   Alpha Vantage news-sentiment) to drive §2 "What's driving the market."
2. **Per-ticker news:** for watchlist + scan-shortlist tickers, pull the latest company news +
   sentiment for §3 and §5.
3. **Web supplement (if web tools are available in this run):** use web search to catch the latest
   breaking headlines the APIs may lag on. If web tools are unavailable (e.g. restricted scheduled
   run), rely on the APIs and note it.
Always prefer the freshest item; show the source/date; never present stale news as new, and never
invent a headline. (Truly breaking *intraday* news is the v2 intraday-watch feature.)

## Weekly catch-up (FIRST brief of the week only — settings.json `deep_dives`)
On the first brief of each week (`deep_dives.catchup_day`, default Monday), add a short "this week's
setup" block. Other days: skip it. Cover, in a few plain lines, for the watchlist + holdings:
- **What happened last week** — the biggest moves, notable analyst rating/price-target changes, and
  any important filings (8-K/major news). Keep it to what actually matters.
- **What's coming this week** — which of these names report **earnings** or have known events, with dates.
This is the "what did I miss?" check, done for you. Free data only; note anything you couldn't pull.

## Market scan — cover the WHOLE market, all sectors (do this every run)
The owner wants opportunities from across the entire US market, not just the watchlist. You CANNOT
pull deep data on all ~6,000 stocks (rate limits). Use this funnel, controlled by
`settings.json.market_scan`:
1. **Broad screen (cheap, whole market):** pull the free **pre-computed screener endpoints** —
   Alpha Vantage `TOP_GAINERS_LOSERS` (1 call), yfinance predefined screens (a few calls). These
   are ranked server-side, so this covers the whole market in ~5–10 calls. Apply
   `market_scan.universe_filters` (min price/volume/market-cap) to drop penny stocks & junk.
2. **Shortlist:** narrow to the best ~`max_candidates_surfaced` (default 10) by relevance: real
   catalyst/news, healthy fundamentals, fits a bucket, not a pump.
3. **Deep-analyze** only the shortlist (+ the watchlist) through the multi-role lens below.
Candidates that survive become suggestions; the rest are listed as "watch" ideas in the scan section.
If a screener source is unavailable, note it and scan with whatever sources remain.

## Self-curated radar + DYNAMIC watchlist (do this every run, controlled by settings.json `radar`)
The agent maintains `data/radar.json` — a capped, auto-pruned candidate list of names discovered
from the scan/news — and, when `radar.auto_manage_watchlist` is true, **actively manages
`config/watchlist.json`** (the owner opted into autonomous management). The watchlist is therefore
**dynamic**: it evolves as the market does. Guardrails (NON-NEGOTIABLE): never remove a name the owner
**holds** (`portfolio.json`) or **manually added** — only retire names the agent itself added; respect
`radar.watchlist_max_per_bucket`; and **report every add/retire in the brief** (see "📋 Watchlist
update"). The owner can veto/revert anytime. To know which names it added, tag promoted candidates in
`radar.json` with `"promoted":true,"promoted_on":"YYYY-MM-DD"`; treat all other watchlist names as
owner-owned and never auto-remove them.
If `data/radar.json` is missing, create it as `{"candidates": []}`. Each run:
1. **Add:** for strong scan/news finds NOT already in the watchlist or radar, append a candidate:
   `{"ticker","added":"YYYY-MM-DD","last_seen":"YYYY-MM-DD","days_relevant":1,"reason":"…","bucket_guess":"core|growth|speculative"}`.
2. **Refresh:** for radar names that show up again / stay relevant today, set `last_seen` to today
   and increment `days_relevant`.
3. **Prune:** drop any candidate whose `last_seen` is older than `auto_prune_after_days_inactive` days.
4. **Cap:** if over `max_size`, keep the most relevant and drop the weakest.
5. **Auto-promote (dynamic watchlist):** any candidate with `days_relevant >=`
   `promote_to_watchlist_after_days_relevant` → **add it to the right bucket in `watchlist.json`**
   (since `promotion_requires_owner_approval` is false), tag it `promoted` in `radar.json`, and note
   the add in the brief's "📋 Watchlist update" line. If a bucket is at `watchlist_max_per_bucket`,
   only add if it's stronger than the weakest agent-added name there (and retire that one).
6. **Auto-retire:** any **agent-added** watchlist name that has gone stale (no relevance for
   `auto_prune_after_days_inactive` days) or whose thesis broke → remove it from `watchlist.json` and
   note it in "📋 Watchlist update". **Never** retire an owner-held or owner-added name — flag it for
   the owner instead. If `auto_manage_watchlist` is false, fall back to PROPOSING changes only (don't edit).

## API + quota budget per run (HARD RULES — do not exceed)
Read these as constraints, not suggestions:
- **NEVER loop over the full ticker universe.** Use the pre-computed screener endpoints only. If you
  ever find yourself about to request data for hundreds of symbols, STOP — you're doing it wrong.
- **Target ≤ ~70 data API calls per run total:** ~5–10 for the broad scan + the watchlist (~46
  names) + the shortlist (~10). This fits Finnhub free (60/min — pace if needed) and stays mindful
  of Alpha Vantage's 25/day (use it sparingly: indicators on the shortlist + the one movers call).
- **One run = one agent session.** The whole brief is a single pass; do not spawn per-ticker
  sub-runs. Reading a 20-row screener vs a 5-row screener costs the same to your Pro quota.
- If rate-limited, **prioritize:** (1) market snapshot, (2) watchlist movers/news, (3) scan
  shortlist — and note in the brief that some data was skipped, rather than hammering the API.

## Reason through a multi-role lens (do this internally before writing suggestions)
Borrowed from the TradingAgents framework. For each candidate idea, think in four passes:
1. **Analyst** — what do the data/fundamentals/technicals say?
2. **Researcher** — what's the bull case vs bear case from news/sentiment/insider activity?
3. **Risk manager** — position size, stop-loss, daily-loss-limit and circuit-breaker status,
   over-concentration vs the 70/20/10 target. Kill weak ideas here.
4. **Portfolio manager** — does this fit the owner's buckets and current holdings? Final call +
   confidence. Only ideas that survive all four passes become suggestions.

## The owner's strategy (apply it)
Three buckets by target allocation (from settings.json):
- **Core (70%)** — broad ETFs + a few large-caps. Long-term buy-and-hold. Suggestions here are
  RARE and high-conviction. Prefer **dollar-cost averaging** over entry-timing; say so.
- **Growth (20%)** — individual US stocks; long-term + swing.
- **Speculative (10%)** — high-risk plays. ALWAYS attach a loud risk warning, a hard stop-loss,
  and small position sizing. Never let a speculative idea sound safe.

Reinforce foundations when relevant: invest only money not needed for 3–5 years, after an
emergency fund + high-interest debt; size positions; always use stop-losses (esp. speculative).

## Stage & capital — Year-1 foundation, adaptive $500→$1,000/month (read `settings.json.capital`)
The owner is in the **foundation-building stage**, investing **`monthly_investment_usd_current`**
(starts at $500, scaling toward $1,000 as he grows — first real buy ~end of June 2026). **Every run,
read `capital.monthly_investment_usd_current` AND the actual portfolio size, then split the amount by
the 70/20/10 allocation and ADAPT** (so $500 → $350/$100/$50; $1,000 → $700/$200/$100; and as the
portfolio grows, shift emphasis sensibly — e.g. once a foundation is set, the growth/speculative work
matters more). Tailor everything to where he actually is — not where he wants to be in 3 years. Be
honest about this. The dollar examples below assume $500; recompute for the current amount.
- **Core (~$350/mo) = autopilot.** Each month: "Put $350 into VOO/VTI (dollar-cost average)." Buy
  regardless of price; lean to buy *more* when the market is down. Core is boring on purpose.
- **Growth (~$100/mo) = ONE best pick + a 2–3 name shortlist.** Surface the **single strongest**
  growth idea to add the $100 to this month — say which one and why, in plain words. Then list a
  **2–3 name shortlist** of runner-ups (one short phrase each) so the owner learns the field and has
  alternatives. The $100 goes to the ONE best pick unless the owner says otherwise — the shortlist
  is for awareness, NOT for splitting $100 across many (that's just noise). Owning 1–3 growth
  positions over time is the point.
- **Speculative (~$50/mo) = LEARNING mode for now.** Do NOT tell him to buy speculative stocks yet.
  The $50/mo goes to an "opportunities fund" that he parks. Track/teach speculative setups so he
  builds the skill; flip to live suggestions only once that fund reaches
  `speculative_go_live_when_bucket_usd` (~$500).
- **Income goal honesty:** if he asks about making ~$200/month, be honest — that's a **Year-2/3
  target** once the speculative bucket is real money (a 10% move on a ~$2,000 bucket). At a ~$600
  bucket it would need ~33% monthly = gambling. Year 1 is about building the base, not income.
Give **dollar amounts** (he has $500 to allocate), not just percentages. The most valuable things
you give him now: the right month to DCA, the one best growth add, and teaching him to read
catalysts so he's ready when the speculative bucket is real.

## How you decide something is a buy (selection strategy — apply PER BUCKET)
Use a **multi-factor (Quality–Value–Momentum + catalyst)** approach, matched to each bucket. This
mirrors how the best services work (Seeking Alpha's quant factors, Motley Fool's quality/value,
IBD's CAN SLIM momentum) and follows the research finding that value and momentum work best held
as *separate sleeves* — which the 70/20/10 buckets already do:
- **Core (70%) → Quality + Value.** Durable, profitable businesses / broad ETFs bought at
  fair-or-better prices. A core buy needs solid fundamentals + reasonable valuation (vs history,
  peers, analyst fair value). Favor dollar-cost averaging. Rarely "exciting" — that's the point.
- **Growth (20%) → Growth + Momentum.** Strong revenue/earnings growth + positive price trend +
  a real catalyst (product, earnings beat). (v2 adds the CAN SLIM checklist here.)
- **Speculative (10%) → Catalyst/Momentum, tiny size, hard stop.** Only with a clear catalyst and a
  defined max loss; never sized large; always a loud risk warning.
Across all buckets: the multi-role lens (Analyst→Researcher→Risk→Portfolio) must agree; overall
market direction tempers conviction; news/insider/sentiment are **context, never the sole reason**.
Be honest — no strategy wins every time; always show confidence + what would prove the idea wrong.

## Stock Health Score (0–100) — compute for every analyzed stock (settings.json `scoring`)
A transparent quality/risk score, shown as a small tag (e.g. "NVDA — 76/100, low–med risk"). It is a
**SUMMARY of quality + risk, NOT a buy signal** — the multi-role lens above still makes the actual
call. A high score on an overpriced name is still a bad entry; a low score on a speculative idea is
expected, not a veto. Compute it BEHIND THE SCENES for watchlist + scan-shortlist single stocks using
free Finnhub data (`stock/metric?metric=all` for P/E, growth, margins, debt/cash; financials as
backup). Three components, weighted per `settings.json.scoring.weights`:
1. **Growth (0–35)** — revenue (and, if available, earnings) growth YoY. Guide: ≥30% → ~30–35;
   15–30% → ~22–29; 5–15% → ~12–21; 0–5% → ~5–11; negative → 0–5.
2. **Financial health (0–35)** — net cash vs debt + profitability, minus qualitative risk flags.
   Guide: net cash + profitable + no flags → ~30–35; manageable debt + profitable → ~20–29; high
   debt or thin/again margins → ~10–19; unprofitable + leveraged → 0–9. Subtract a few points for
   risk flags found in news/filings (customer concentration, going-concern, big litigation) —
   **best-effort; note it when you can't check.**
3. **Valuation (0–30)** — P/E vs the stock's own history / peers, **growth-adjusted (PEG-style):
   forgive a high P/E when growth is strong** (this is why NVDA can be "expensive" yet still score
   well). Use P/S if the company has no earnings. Guide: cheap vs history or PEG ≤1 → ~24–30; fair
   (PEG ~1–2) → ~15–23; rich (PEG ~2–3) → ~8–14; very rich + weak growth → 0–7.

Total 0–100 → **risk band** from `settings.json.scoring.risk_bands`: ≥70 = **lower risk**, 50–69 =
**medium risk**, <50 = **higher risk**.
Rules: (a) **Broad ETFs** (most of Core) are diversified — don't score them like a single stock; tag
them "ETF — diversified" instead. (b) If an input is missing, compute from what you have, mark the
score **partial**, and say which inputs you had. (c) Never invent the underlying numbers. (d) The
score is context — it informs sizing/confidence and the brief tag, but the buy/hold/avoid verdict
still comes from the multi-role lens.

## Risk discipline (apply from settings.json `risk`)
- Respect `max_position_pct_of_bucket` per bucket; never suggest oversizing.
- **Daily loss limit:** if the owner notes realized losses today exceeding `daily_loss_limit_pct`
  of capital, recommend stopping for the day — explicitly discourage revenge trading.
- **Circuit breaker:** if the last `circuit_breaker_consecutive_losses` logged suggestions were
  losers, recommend a pause + review before issuing new speculative ideas.
- Never propose self-optimizing/backtest-tuned strategies or day-trading scalps; out of scope.

## Each suggestion: compute every field INTERNALLY (show only the simple line)
For every action you put in "What I'd do today," internally work out: Action · Ticker · Bucket ·
Entry zone · Stop-loss · Target/exit · Position size (% of bucket) · Confidence (Low/Med/High) ·
Health score (0–100) + risk band · Why · What would invalidate it. The EMAIL/message shows only the plain one-liner (verdict + rough
price + one reason + any inline safety note). The full fields are **logged** (see Logging) so the
track-record score can grade them later. Never act without an internal stop-loss and reason.

## Briefing format — ONE simple message (the owner is a BEGINNER)
Plain English only — explain like to a smart 10-year-old. **NO jargon** ("forward PE", "RSI",
"RankIC" — if a term is unavoidable, explain it in the same breath). Use clear symbols. Keep it to
**ONE screen, understandable at a glance, NO repetition.** The scan, radar, insider check,
multi-role reasoning, and detailed suggestion fields all run **BEHIND THE SCENES** — their results
appear only as simple action lines and get logged; they are **NOT shown as their own sections**.

**Layout rules (owner preference — apply every time):** bold each section header (Telegram HTML),
blank line between blocks, short sentences (prefer "·"-separated mini-lists over prose), and
**teach as you go** — add a short plain-English *why-it-matters* clause to each money-move/watch item
so a beginner learns the concept, not just the call (one short clause; never bloat past one screen).
Delivery uses `parse_mode=HTML`: `<b>` headers, `<i>` footer/teaching asides, escape `& < >`.

Produce exactly these blocks, in order:

**🌅 Your Market Brief — <Day, Mon DD>**

**📈 Today** — ONE line: market up 🟢 / down 🔴 + a simple read for the day, flagged honestly
as a guess (e.g. "likely drifts up unless the oil deal falls apart").

**📰 This week's setup** (FIRST brief of the week only — see Weekly catch-up) — a few plain lines:
what happened last week across your watchlist + holdings (big moves, rating changes, key filings) and
which names report earnings / have events this week (with dates). Skip this block on other days.

**💰 This month's money moves** (show in full on the FIRST brief of the month; a 1-line reminder
otherwise — see `settings.json.capital` / `schedule`). Plain dollar amounts for the $500/month:
- 🟢 **Core — DCA $350 into VOO/VTI** this month (buy more if the market's down). Autopilot.
- ✅ **Growth — add $100 to your best pick:** <the single best growth idea> (~$price, **score/100
  + risk band**) — one plain reason. Then: *Runner-ups to learn (not for splitting the $100):* <2–3
  tickers, each with score/100 + one phrase>.
- 🧪 **Speculative — learning only:** park $50 in the opportunities fund. Today's setup to *study*
  (not buy): <ticker + what to watch>. Goes live when the fund hits ~$500.

**What I'd watch** — a few lines; each = symbol + plain note + (rough price). This is for
learning/awareness, not extra buys (you've got one growth add this month):
- 👀 **Watching** TICKER (~$price, score/100 + risk band) — what's happening, what would make it a
  future buy.
- 🟡 **Hold / wait** TICKER — why wait.
- 🛑 **Avoid for now** TICKER — plain reason.
Only include lines that matter. If nothing's notable, say so plainly. The **score/100 + risk band**
(e.g. "76/100, low–med risk") is a small tag for quality/risk only — a quick teaching cue, never a
buy signal on its own. Omit the tag for broad ETFs (tag "ETF — diversified") and when the score is
unavailable.

**📋 Watchlist update** (ONLY when the agent changed the watchlist this run) — one line listing what
it **added** (and why) and what it **retired** (and why), e.g. "Added PLTR (growth) — strong, kept
showing up; retired SHOP — momentum faded." Omit this block entirely if nothing changed. Never list
removals of names the owner holds or added (those are never auto-removed).

**💡 Why** — 1–2 kid-simple sentences on the ONE thing moving the market (only if it matters today).

**💼 Your money** — holdings from `portfolio.json`, each: up/down 🟢/🔴 + one note. If none yet:
"No holdings added yet — tell me when you buy and I'll track them."

**📊 My track record** — running accuracy from the self-review (below), e.g. "Last month: right on
6 of 10 calls (60%); being more careful on risky picks." Show "building track record" until ≥1 month.

**💡 Tip of the day** — ONE tiny beginner concept in ONE plain sentence (every day).

*Footer (one line):* "Not financial advice — you decide and place trades."

Tone: plain, calm, encouraging, honest about uncertainty. Never hype. If it can't be said simply,
it doesn't go in.

## Logging (do this every run, before sending)
For each action line you produced, append one line to `data/suggestions-log.jsonl` with the full
internal fields (even though the message showed only the simple line):
```json
{"date":"YYYY-MM-DD","ticker":"XXX","action":"Buy","bucket":"growth","price_at_suggestion":123.45,"stop":110.00,"target":150.00,"confidence":"Medium","reason":"…","score":76,"score_growth":30,"score_health":28,"score_valuation":18,"risk_band":"low-med","score_inputs":"pe,revGrowth,netCash; concentration flag from news","score_partial":false}
```
(Omit the `score_*` fields, or set `score`:null, for broad ETFs and when the score couldn't be computed.)
Then save the full message to `data/briefings/YYYY-MM-DD.md`.

## Track record + self-review (learn from past calls — show in the brief)
Every run, BEFORE writing suggestions:
1. Read recent entries in `data/suggestions-log.jsonl`. For past calls old enough to judge, compare
   the price then vs now to mark each roughly right/wrong.
2. Compute a simple **accuracy %** (e.g. last 30 days) and note where you've been weak (e.g.
   "speculative calls mostly wrong").
3. **Adjust this run accordingly** — lower confidence / be more cautious in the buckets where you've
   been wrong. This is the "learn from mistakes" loop (review + recalibrate; not model retraining).
4. Surface the headline number in the "📊 My track record" line. Show "building track record" until
   there is ≥1 month of data. Be honest — never inflate the score.

## Learning memory — get smarter from day 1 (read + update `data/learning.md`, settings.json `learning`)
This is the honest version of "learn over time and compare trends" — **memory + self-review over an
LLM agent, NOT a trained price-prediction model** (deliberately out of scope; such models overfit and
mislead at this stage). If `data/learning.md` is missing, create it with "Lessons learned" and
"Market regime log" sections. Each run:
1. **Read** `data/learning.md` and let its lessons temper today's calls (be more cautious in buckets
   where past lessons say you've been wrong; lean into what's worked).
2. **Compare** today's market backdrop to the **previous "Market regime log" entry** — note what
   changed (direction, sector leadership, volatility, rates/news theme). This is the "latest trend vs
   old trend" comparison the owner wants; let it inform the brief's 📈 Today + 💡 Why lines.
3. **Update** the file: append one dated regime line; add or revise lessons when the track-record
   review (above) teaches something new, citing evidence from `data/suggestions-log.jsonl`. Keep
   entries short and falsifiable; prune lessons that prove wrong. Never invent results to look smart.

## Delivery — Telegram (primary in v1)
Send the rendered message to the owner's Telegram via the bot token + chat id in
`config/secrets.local.json` (`telegram_bot_token`, `telegram_chat_id`). Keep it short and phone-friendly
(split if very long). Title line by mode: pre-market → `🌅 Your Market Brief — <date>`; intraday (v2)
→ `⚡ Market Alert — <ticker/topic>`; on-demand → `🌅 Market Brief — <date HH:MM>`.
Email via Gmail is an OPTIONAL fallback only if `delivery.email.enabled` is true AND Gmail is
authenticated (it needs a one-time Google sign-in; may be unavailable in scheduled runs).

## If data is missing
Note any data source that failed and which fallback you used. Never invent prices or news.
If you cannot get core market data at all, send a short message saying so rather than guessing.
````

- [ ] **Step 2: Verify frontmatter parses**

Run: `python3 -c "import re,sys; t=open('/Users/rajrupesh/Documents/Raj/stocks-agent/skills/market-briefing/SKILL.md').read(); assert t.startswith('---'); assert 'name: market-briefing' in t; assert 'NEVER' in t.upper(); print('ok')"`
Expected: `ok`

- [ ] **Step 3: [no commit — local only]**

---

## Task 7B: Write the on-demand `skills/equity-research/SKILL.md`

**Files:**
- Create: `skills/equity-research/SKILL.md`

A plain-English research note that re-checks a stock's thesis ("does my reason to hold still hold?").
On-demand, read-only, suggestion-only.

- [ ] **Step 1: Write the skill file**

````markdown
---
name: equity-research
description: On-demand plain-English research note on a US stock for Rajrupesh — re-checks the bull/bear thesis ("does my reason to hold still hold?") using read-only free data (Finnhub/Alpha Vantage/yfinance). Suggestion-only; NEVER executes trades.
---

# Equity Research — plain-English research note (on-demand)

Use when Rajrupesh asks "is <TICKER> still a good hold/buy?", "give me a research note on <TICKER>",
or "does my reason to own <TICKER> still hold?". Produce a SHORT, plain-English note a **beginner**
can act on. **Suggestion-only — you have no trade tools and must never place/modify/cancel a trade.**

## Inputs
- The ticker(s) the owner names. If it's a holding, read `config/portfolio.json` for shares + avg cost.
- `config/settings.json` (buckets, risk, scoring) and `config/secrets.local.json` (keys via the MCPs).

## Data (read-only): Finnhub primary, Alpha Vantage secondary, yfinance fallback
Pull: current quote (note ~15-min delay), key fundamentals (growth, margins, debt/cash, P/E), latest
news + sentiment, analyst ratings/price targets, insider activity, next earnings date.

## Produce (ONE screen, plain English, no jargon)
**🔎 Research note — <TICKER> (~$price)**
- **What they do** — one kid-simple sentence.
- **Health score** — the 0–100 score + risk band (compute exactly as in the `market-briefing` skill).
- **Bull case** — 2–3 plain reasons it could go up.
- **Bear case** — 2–3 plain reasons it could go down (be honest; never hide the downside).
- **Does the thesis still hold?** (holdings only) — compare to the original reason + avg cost →
  💎 still holds / 🟡 weakening / 🔴 broken, in plain words.
- **Verdict** — Buy / Hold / Trim / Avoid + confidence (Low/Med/High) + the ONE thing that would change it.
- *Footer:* "Not financial advice — you decide and place trades."

## Rules
- Never invent numbers or news; note any missing data + the fallback used.
- Context + reasoning, not a guarantee; always show what would prove the idea wrong.
- If the verdict is actionable, append a line to `data/suggestions-log.jsonl` (same fields as
  `market-briefing`, including the score fields).
````

- [ ] **Step 2: Verify frontmatter parses**

Run: `python3 -c "t=open('/Users/rajrupesh/Documents/Raj/stocks-agent/skills/equity-research/SKILL.md').read(); assert t.startswith('---'); assert 'name: equity-research' in t; assert 'NEVER' in t.upper() or 'never' in t; print('ok')"`
Expected: `ok`

- [ ] **Step 3: [no commit — local only]**

---

## Task 7C: Write the on-demand `skills/earnings-review/SKILL.md`

**Files:**
- Create: `skills/earnings-review/SKILL.md`

An earnings digest: free **results** summary by default; a deeper **call** digest if the owner pastes
a transcript (full transcripts are paid on the free tier). On-demand, read-only, suggestion-only.

- [ ] **Step 1: Write the skill file**

````markdown
---
name: earnings-review
description: On-demand earnings digest for a US stock for Rajrupesh — summarizes the latest quarter (actual vs expected EPS/revenue, guidance, market reaction) from free data, and gives a deeper call digest if the owner pastes a transcript. Suggestion-only; NEVER executes trades.
---

# Earnings Review — what changed last quarter (on-demand)

Use when Rajrupesh asks "how was <TICKER>'s earnings?" or pastes an earnings call transcript.
**Suggestion-only — no trade tools; never place/modify/cancel a trade.**

## Two modes
1. **Results digest (default, FREE data):** pull the latest reported quarter from Finnhub (EPS actual
   vs estimate, revenue, surprise %), recent guidance/news, and the price reaction.
2. **Transcript digest (only if the owner pastes a transcript):** read the pasted text and summarize
   the call. Full transcripts are paid via API, so this mode relies on the owner pasting one — if
   asked for a call digest without a transcript, do the results digest and offer the paste option.

## Produce (ONE screen, plain English, no jargon)
**📞 Earnings review — <TICKER> (quarter, date)**
- **Did they beat?** — EPS actual vs expected + revenue actual vs expected, plainly (✅ beat / ⚠️ miss / ➖ in line).
- **Guidance** — what they said about the future, simply.
- **Market reaction** — how the stock moved after, and why.
- **What it means for the thesis** — 1–2 plain sentences: stronger or weaker case?
- **(If a transcript was pasted)** 3–5 bullet highlights from the call (themes, tone, risks).
- **So what** — Hold / Buy more / Trim / Avoid lean + confidence + what to watch next quarter.
- *Footer:* "Not financial advice — you decide and place trades."

## Rules
- Never invent numbers; if the free API lacks the latest quarter, say so and offer the paste option.
- Prices ~15-min delayed. If the lean is actionable, log it to `data/suggestions-log.jsonl` (same fields).
````

- [ ] **Step 2: Verify frontmatter parses**

Run: `python3 -c "t=open('/Users/rajrupesh/Documents/Raj/stocks-agent/skills/earnings-review/SKILL.md').read(); assert t.startswith('---'); assert 'name: earnings-review' in t; print('ok')"`
Expected: `ok`

- [ ] **Step 3: [no commit — local only]**

---

## Task 8: Manual end-to-end dry run (Phase 1 acceptance)

**Files:**
- Produces: `data/briefings/<today>.md`, appends to `data/suggestions-log.jsonl`

- [ ] **Step 1: Run the skill manually**

In a Claude Code session in `/Users/rajrupesh/Documents/Raj/stocks-agent`, invoke the
`market-briefing` skill. Let it read config, pull live data via the MCPs, and generate the briefing.

- [ ] **Step 2: Verify it's ONE simple, jargon-free message**

Open `data/briefings/<today>.md`.
Expected blocks in order: **🌅 title**, **📈 Today** (mood + one-line daily lean), **📰 This week's
setup** (Mondays only — last week's catch-up + this week's earnings dates), **💰 This
month's money moves** (Core DCA $350 / one Growth pick $100 + 2–3 shortlist / Speculative learning $50
— full on the first brief of the month, 1-line reminder otherwise), **What I'd watch** (👀/🟡/🛑 lines
with rough price + score tag + one plain reason), **💡 Why** (1–2 kid-simple sentences), **💼 Your money**,
**📊 My track record**, **💡 Tip of the day**, one-line footer. It must fit ~one screen, contain NO
jargon (no "forward PE"/"RSI" without a plain explanation), and NOT repeat itself. There must be
**no** "smart money", "market scan", "radar", or "full suggestions table" sections in the message
(those run behind the scenes). If it reads like a multi-section report, fix before continuing.

- [ ] **Step 3: Verify the behind-the-scenes scan actually ran broad**

Confirm at least one action/"👀 Watching" line is a ticker NOT in `config/watchlist.json` on a normal
day (proof the whole-market scan surfaced something new), and that `data/radar.json` got updated.
If suggestions only ever come from the watchlist, the broad screeners aren't being used — fix.

- [ ] **Step 4: Verify every action line was logged with full fields**

Run: `wc -l < /Users/rajrupesh/Documents/Raj/stocks-agent/data/suggestions-log.jsonl`
Expected: equals the number of ✅/🟢/🟡/🛑 action lines in the message (👀 watch-only lines optional),
and each logged line includes stop/target/confidence/reason (logged even though the message showed
only the simple line). 0 is valid on a quiet day.

- [ ] **Step 4b: Verify the radar self-curated and the DYNAMIC watchlist behaved safely**

Run: `python3 -c "import json; r=json.load(open('/Users/rajrupesh/Documents/Raj/stocks-agent/data/radar.json')); print('candidates', len(r['candidates']), '<=15:', len(r['candidates'])<=15)"`
Expected: `data/radar.json` exists with a `candidates` list (≤ max_size 15). The watchlist is now **dynamic**, so it MAY differ from `31 12 3`. If it changed: (a) the change was **reported** in the brief's "📋 Watchlist update" line, (b) only **agent-added** names were retired (check `radar.json` `promoted` tags) — **no owner-held/owner-added name was removed**, and (c) per-bucket counts respect `radar.watchlist_max_per_bucket` (core ≤35, growth ≤20, speculative ≤6). On a quiet day it's fine for the watchlist to be unchanged with no update line.

- [ ] **Step 4c: Verify the learning memory updated**

Run: `python3 -c "t=open('/Users/rajrupesh/Documents/Raj/stocks-agent/data/learning.md').read(); print('regime log present:', 'Market regime log' in t); print('len:', len(t))"`
Expected: `data/learning.md` exists and gained at least a dated "Market regime log" line for today (and any new lessons). The brief's 📈 Today / 💡 Why should reflect the latest-vs-prior trend comparison.

- [ ] **Step 5: Verify data is real, not invented**

Spot-check one quoted price against the Finnhub smoke-test command from Task 4 Step 4.
Expected: prices match within the ~15-min delay window. If the briefing shows a price no source returned, fix the skill before continuing.

- [ ] **Step 6: [no commit — local only]**

---

## Task 9: Telegram delivery test + README

**Files:**
- Create: `README.md`
- Uses: Telegram bot (token + chat id from Task 4)

- [ ] **Step 1: [OWNER ACTION] Send today's brief to Telegram**

Have the skill send today's generated brief to the owner's Telegram (bot token + chat id from
`config/secrets.local.json`). Ask the owner to confirm it arrived and reads cleanly on the phone.
Expected: owner confirms receipt. If it doesn't arrive, re-check the bot token / chat id (Task 4 Step 2b).

- [ ] **Step 2: Write the README**

```markdown
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
- Read-only by construction: **no execution tools installed.** You make and place every trade.
- Honest by design: shows confidence, tracks its own accuracy, never hypes.

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
```

- [ ] **Step 3: [no commit — local only]**

---

## Task 10: Schedule the daily cloud routine (Phase 2 acceptance)

**Files:** none (creates a `/schedule` routine)

- [ ] **Step 1: [OWNER ACTION] Confirm scheduling environment can reach data + Gmail**

Before scheduling, confirm with the owner (and by testing if possible) that a scheduled cloud
routine has access to the data MCPs and Gmail. If the cloud routine cannot reach them, the
fallback is: keep it as a one-command manual run each morning, and stop after this step.

- [ ] **Step 2: Create the schedule**

Use the `/schedule` skill to create a routine:
- Cadence: **daily on weekdays (Mon–Fri) at 07:30 America/New_York** (= 06:30 America/Chicago).
- Action: run the `market-briefing` skill in the `stocks-agent` project and send the result via Telegram.
- (The first brief of each calendar month shows the full "money moves"; other days show the 1-line reminder.)

- [ ] **Step 3: Verify the routine exists**

List scheduled routines (via the `/schedule` skill's list/management view).
Expected: one daily-weekday (Mon–Fri) 07:30 ET routine targeting the market-briefing skill.

- [ ] **Step 4: [OWNER ACTION] Trigger a one-off run to confirm**

Run the routine once on demand. Ask the owner to confirm a Telegram message arrived from the
scheduled run (not just the manual run in Task 9).
Expected: owner confirms receipt. This proves the end-to-end automated path works. (If Telegram
can't be reached from the scheduled cloud run, fall back to a one-command manual run each morning.)

- [ ] **Step 5: [no commit — local only]**

---

## Done = v1 acceptance criteria
- [ ] Folder + config + watchlist (`31 12 3`) + portfolio + secrets exist and validate.
- [ ] Brief gives "hold/trim/sell" ONLY for tickers in `portfolio.json` (no fabricated ownership).
- [ ] Read-only data MCPs connected and returning real data.
- [ ] **Guardrail verified: no execution tools present.**
- [ ] `market-briefing` skill produces ONE simple, jargon-free message (📈 Today + lean, 📰 weekly catch-up on Mondays, 💰 money moves in dollars, What I'd watch, Why, Your money, track record, tip) from live data — no multi-section report.
- [ ] Each pick/watch line carries a Health Score tag (0–100 + risk band); ETFs tagged "diversified"; sub-scores logged.
- [ ] On-demand `equity-research` and `earnings-review` skills exist, parse, and run on a watchlist name.
- [ ] Behind-the-scenes scan surfaces names beyond the watchlist; radar self-curates and DYNAMICALLY manages the watchlist (auto-add/retire of agent-added names only), reporting changes in the brief and never dropping held/owner-added names.
- [ ] Learning memory (`data/learning.md`) is read + updated each run (lessons + trend-vs-prior-trend); the brief reflects it. (Honest scope: memory + self-review, not a trained prediction model.)
- [ ] Capital is adaptive: advice scales with `monthly_investment_usd_current` ($500→$1,000) and portfolio size.
- [ ] Every action line logged to `suggestions-log.jsonl` with full fields (incl. score); track-record % shown (or "building").
- [ ] Test brief received on Telegram and reads cleanly on phone.
- [ ] Daily-weekday (Mon–Fri) 07:30 ET cloud schedule created and confirmed via a real run (or documented manual fallback).
