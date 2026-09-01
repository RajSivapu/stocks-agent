# Reliable Stock Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify the fresh-analysis Claude workflow, confirmation-based Telegram portfolio recorder, and bounded weekly ChatGPT audit without adding a paid model API or any trade-execution capability.

**Architecture:** Claude Cloud Routines remain the scheduled analyst but every run creates an auditable run record and re-underwrites current evidence. Telegram delivers deterministic portfolio commands to a Supabase Edge Function, and a PostgreSQL RPC applies confirmed mutations atomically. ChatGPT receives a compact read-only weekly packet through one local scheduled task.

**Tech Stack:** Python 3.14, pytest, stdlib HTTP, Supabase/PostgREST, PostgreSQL/PLpgSQL, Supabase Edge Functions (Deno/TypeScript), JavaScript parser tests with Node, Telegram Bot API, Claude Cloud Routines, ChatGPT scheduled tasks.

**Spec:** `docs/design/2026-09-01-reliable-stock-agent-design.md`

## Global Constraints

- Suggestion-only: no code, prompt, tool, or dependency may place, modify, or cancel a brokerage order.
- No OpenAI, Anthropic, or other paid model API.
- Every Telegram mutation requires owner authentication, a stored pending command, and Confirm.
- A duplicate Telegram `update_id` must never produce a duplicate transaction.
- Missing or stale market data must veto actionable advice.
- Earlier run output is historical evidence only; a later run independently fetches and reasons.
- Secrets remain outside Git and are never printed in tests, logs, docs, or exception messages.
- All live-DB tests must use unique `TST*` records and clean them up in `finally` blocks.
- The weekly ChatGPT audit is read-only and sends no Telegram message.

---

### Task 1: Repository Secret Hygiene and Stable Baseline

**Files:**
- Modify: `.gitignore`
- Modify: `tests/test_db.py`

**Interfaces:**
- Consumes: current Git ignore rules and live Supabase test helpers.
- Produces: ignored `.codex/` and `.DS_Store`; a time-independent lessons round-trip test.

- [ ] **Step 1: Add a failing ignore verification**

Run:

```bash
git check-ignore .codex/config.toml .DS_Store
```

Expected before the change: one or both paths are not reported as ignored.

- [ ] **Step 2: Add local-machine ignores**

Append exactly:

```gitignore
# Codex and macOS machine-local state
.codex/
.DS_Store
```

- [ ] **Step 3: Make the lessons test unique and current**

Replace the fixed 2026-01-01 content with a UUID-backed value and today's date, then always delete it:

```python
def test_lessons_roundtrip():
    import datetime, uuid
    content = f"test regime line {uuid.uuid4()}"
    try:
        db.insert_lesson({
            "entry_date": str(datetime.date.today()),
            "category": "regime",
            "content": content,
        })
        rows = db.get_lessons(limit=50)
        match = [r for r in rows if r["content"] == content]
        assert len(match) == 1 and match[0]["category"] == "regime"
    finally:
        _sb().table("lessons").delete().eq("content", content).execute()
```

- [ ] **Step 4: Verify baseline**

Run:

```bash
git check-ignore .codex/config.toml .DS_Store
.venv/bin/python -m pytest tests/ -q
```

Expected: both paths print and the Python suite passes or only transient external-network tests skip.

- [ ] **Step 5: Commit**

```bash
git add .gitignore tests/test_db.py
git commit -m "chore: secure local config and stabilize DB test"
```

---

### Task 2: Market Quote Freshness Metadata

**Files:**
- Modify: `lib/marketdata.py`
- Modify: `tests/test_marketdata.py`

**Interfaces:**
- Consumes: Yahoo chart `meta.regularMarketTime` and `meta.marketState`.
- Produces: `quote(sym) -> {price, prev_close, day_pct, as_of, market_state, source}` and `quote_age_minutes(quote, now=None) -> float | None`.

- [ ] **Step 1: Write failing quote metadata tests**

Monkeypatch `_get` with fixed Yahoo metadata and assert:

```python
def test_quote_includes_freshness_metadata(monkeypatch):
    monkeypatch.setattr(m, "_get", lambda _: {"chart": {"result": [{"meta": {
        "regularMarketPrice": 101.0,
        "previousClose": 100.0,
        "regularMarketTime": 1788296400,
        "marketState": "REGULAR",
    }}]}})
    q = m.quote("TEST")
    assert q["source"] == "yahoo-chart"
    assert q["market_state"] == "REGULAR"
    assert q["as_of"].endswith("+00:00")
```

Add age tests for valid, missing, and future timestamps.

- [ ] **Step 2: Run the focused tests and observe failure**

```bash
.venv/bin/python -m pytest tests/test_marketdata.py -q
```

Expected: missing keys/functions fail.

- [ ] **Step 3: Implement metadata and age calculation**

Use timezone-aware UTC datetimes. Return `None` for absent/unparseable timestamps and clamp a small
future clock skew to zero. Do not infer freshness from wall-clock request time.

- [ ] **Step 4: Verify focused and full suites**

```bash
.venv/bin/python -m pytest tests/test_marketdata.py -q
.venv/bin/python -m pytest tests/ -q
```

- [ ] **Step 5: Commit**

```bash
git add lib/marketdata.py tests/test_marketdata.py
git commit -m "feat: expose market quote freshness"
```

---

### Task 3: Auditable Analysis Runs and Bounded Weekly Queries

**Files:**
- Create: `sql/migrations/20260901_reliable_stock_agent.sql`
- Modify: `sql/schema.sql`
- Modify: `lib/db.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces: `start_analysis_run(kind, started_at=None) -> str`.
- Produces: `finish_analysis_run(run_id, *, status, data_as_of=None, source_status=None, symbols=None, write_counts=None, telegram_message_ids=None, summary=None, error=None) -> None`.
- Produces: `get_recent_transactions(limit=50)`, `get_recent_suggestions(limit=50)`, `get_recent_grades(limit=100)`, and `get_recent_snapshots(limit=100)`.

- [ ] **Step 1: Add failing helper tests**

Add one live-DB lifecycle test using `kind='test'`, asserting `running -> completed`, JSON fields, and
cleanup by run ID. Add pure unit tests that monkeypatch `_sb()` and verify each bounded query applies
its exact `.limit()`.

- [ ] **Step 2: Run focused tests and observe failure**

```bash
.venv/bin/python -m pytest tests/test_db.py -q
```

- [ ] **Step 3: Add schema objects**

The migration and canonical schema must create `analysis_runs`, add nullable `run_id` and
`evidence_as_of` to `suggestions`, add nullable `run_id` to `stock_observations`, add indexes, enable
RLS, and avoid destructive changes.

- [ ] **Step 4: Implement DB helpers**

Serialize dictionaries/lists directly as JSON-compatible values. Bound `summary` to 2000 characters
and `error` to 1000 characters before writing. Validate `limit` in `1..500`.

- [ ] **Step 5: Apply the migration before running the live lifecycle test**

Use the Supabase SQL editor or linked CLI. Run the SQL exactly once; all statements must be
idempotent.

- [ ] **Step 6: Verify tests**

```bash
.venv/bin/python -m pytest tests/test_db.py -q
.venv/bin/python -m pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add sql/migrations/20260901_reliable_stock_agent.sql sql/schema.sql lib/db.py tests/test_db.py
git commit -m "feat: add auditable analysis runs"
```

---

### Task 4: Deterministic Telegram Command Parser

**Files:**
- Create: `supabase/functions/telegram-portfolio/parser.mjs`
- Create: `tests/test_telegram_parser.mjs`

**Interfaces:**
- Produces: `parsePortfolioCommand(text) -> {ok: true, command: ParsedCommand} | {ok: false, error: string}`.
- `ParsedCommand` operations: `buy`, `sell`, `stop`, `portfolio`, `help`.
- Buy fields: `ticker`, `qty`, `price`, `bucket | null`.
- Sell fields: `ticker`, `qty: number | "all"`, `price`.
- Stop fields: `ticker`, `stop`.

- [ ] **Step 1: Write parser tests first**

Cover slash and natural forms, decimals, commas in prices, `@`, `all`, uppercase normalization,
period/dash ticker symbols, extra whitespace, zero/negative/NaN rejection, missing arguments,
unsupported prose, and injection-like strings such as `AAPL;DROP TABLE holdings`.

- [ ] **Step 2: Run tests and observe missing module failure**

```bash
node --test tests/test_telegram_parser.mjs
```

- [ ] **Step 3: Implement the smallest deterministic parser**

Use anchored regular expressions and explicit token validation. Do not use `eval`, dynamic SQL,
network calls, or fuzzy/model parsing. Error messages contain examples but no echo of secret values.

- [ ] **Step 4: Verify parser tests**

```bash
node --test tests/test_telegram_parser.mjs
```

- [ ] **Step 5: Commit**

```bash
git add supabase/functions/telegram-portfolio/parser.mjs tests/test_telegram_parser.mjs
git commit -m "feat: parse Telegram portfolio commands"
```

---

### Task 5: Atomic Portfolio Command Database Workflow

**Files:**
- Modify: `sql/migrations/20260901_reliable_stock_agent.sql`
- Modify: `sql/schema.sql`
- Add tests/verification script: `scripts/verify_portfolio_command_rpc.py`

**Interfaces:**
- Produces table `portfolio_commands` with unique `telegram_update_id`.
- Produces RPC `apply_portfolio_command(p_command_id uuid, p_chat_id bigint, p_user_id bigint) -> jsonb`.
- Produces RPC `cancel_portfolio_command(p_command_id uuid, p_chat_id bigint, p_user_id bigint) -> jsonb`.

- [ ] **Step 1: Define the table and indexes**

Use `status` values `pending`, `applied`, `cancelled`, `rejected`, `expired`, `error`. Include parsed
fields, expected shares, preview/result JSON, 15-minute expiry, confirmation message ID, realized
P&L, and bounded error. Enable RLS with no anon policies.

- [ ] **Step 2: Implement the apply RPC**

The `SECURITY DEFINER` function must set a safe `search_path`, lock the command and holding with
`FOR UPDATE`, verify identity/status/expiry/current shares, and handle:

- Buy: weighted-average cost; preserve existing bucket/stop/target; require bucket for a new holding.
- Sell: require existing holding, reject oversell, insert transaction, reduce or delete holding,
  calculate realized P&L.
- Stop: require existing holding and positive stop; update only stop.

Every Buy/Sell inserts one `transactions` row with `source='telegram'`. A database exception must roll
back the mutation.

- [ ] **Step 3: Implement cancel RPC**

Lock and verify the pending command and identity, then mark it cancelled. Repeated Cancel returns the
existing terminal status without applying anything.

- [ ] **Step 4: Add a verification script**

The script creates only `TSTTG` records, verifies Buy, duplicate apply, stale expected-shares
rejection, Sell, Cancel, and cleanup in `finally`. It prints pass/fail labels but no credentials.

- [ ] **Step 5: Apply the updated migration and run verification**

```bash
.venv/bin/python scripts/verify_portfolio_command_rpc.py
```

Expected: all checks pass and no `TSTTG` rows remain.

- [ ] **Step 6: Commit**

```bash
git add sql/migrations/20260901_reliable_stock_agent.sql sql/schema.sql scripts/verify_portfolio_command_rpc.py
git commit -m "feat: apply confirmed portfolio commands atomically"
```

---

### Task 6: Supabase Telegram Webhook Function

**Files:**
- Create: `supabase/config.toml`
- Create: `supabase/functions/telegram-portfolio/index.ts`
- Create: `scripts/register_telegram_webhook.py`
- Create: `docs/eval/telegram-portfolio-eval.yaml`

**Interfaces:**
- Consumes env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_OWNER_CHAT_ID`, `TELEGRAM_OWNER_USER_ID`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
- Consumes parser and database RPCs from Tasks 4-5.
- Produces HTTPS handler for Telegram `message` and `callback_query` updates.

- [ ] **Step 1: Configure only this function with `verify_jwt = false`**

The function must still reject requests without the Telegram secret header and exact owner IDs.

- [ ] **Step 2: Implement authentication and idempotency first**

Use constant-time byte comparison for the secret. Reject unauthorized calls before parsing. Insert
the unique update ID before side effects; duplicate inserts return HTTP 200 without another reply.

- [ ] **Step 3: Implement message handling**

- `/help`: send accepted examples.
- `/portfolio`: read holdings and send a compact position list.
- Buy/Sell/Stop: fetch current holding, resolve/validate fields, insert pending command, send preview
  with `Confirm` and `Cancel` callback buttons, then store returned message ID.
- Ambiguous input: send help; no pending mutation.

- [ ] **Step 4: Implement callback handling**

Validate callback identity and `pc:confirm:<uuid>` / `pc:cancel:<uuid>`. Invoke the appropriate RPC,
answer the callback query, and edit the original message with applied/cancelled/rejected results.

- [ ] **Step 5: Implement webhook registration utility**

Read all values from environment/local ignored secrets. Call `setWebhook` with the HTTPS function
URL, secret token, `allowed_updates=["message","callback_query"]`, and do not print the token-bearing
URL.

- [ ] **Step 6: Add behavioral eval cases**

Cover unauthorized chat, wrong secret, duplicate update, ambiguous input, missing new-buy bucket,
oversell, Confirm, Cancel, expired/stale confirmation, DB rollback, `/portfolio`, and no brokerage
tools.

- [ ] **Step 7: Type-check and serve locally**

```bash
deno check supabase/functions/telegram-portfolio/index.ts
supabase functions serve telegram-portfolio --env-file supabase/.env.local
```

If the CLIs are absent, install/use project-local tooling without committing caches, then repeat.

- [ ] **Step 8: Commit**

```bash
git add supabase/config.toml supabase/functions/telegram-portfolio/index.ts scripts/register_telegram_webhook.py docs/eval/telegram-portfolio-eval.yaml
git commit -m "feat: receive confirmed portfolio updates from Telegram"
```

---

### Task 7: Fresh Analyst and Checker Routine Contract

**Files:**
- Modify: `skills/market-briefing/SKILL.md`
- Modify: `config/settings.json`
- Modify: `docs/eval/market-briefing-eval.yaml`
- Modify: `routines/README.md`

**Interfaces:**
- Consumes `quote.as_of`, `quote_age_minutes`, and analysis-run DB helpers.
- Produces explicit pre-market, intraday, and post-market freshness/checker behavior.

- [ ] **Step 1: Add failing behavioral cases**

Add cases asserting:

- morning-zone entry alone cannot trigger an intraday alert;
- intraday must fetch fresh macro/quote/news and re-underwrite;
- a quote older than 20 minutes vetoes a new actionable conclusion;
- pre-market prior-close data is labeled provisional;
- checker cannot upgrade confidence and must veto prior-plan leakage;
- every live run starts/finalizes one `analysis_runs` row;
- summary send/write claims match run-log evidence.

- [ ] **Step 2: Correct contradictory watchlist instructions**

Remove every instruction to edit `config/watchlist.json` during Cloud Routines. All proposed changes
must become `lessons(category='watchlist-change')` records for local owner review.

- [ ] **Step 3: Add run lifecycle and freshness gates**

At run start call `start_analysis_run`. Track actual writes/sends. For intraday/post-market action,
use quote age <= `settings.data.max_actionable_quote_age_minutes` (20). Finalize the run with actual
status and IDs; never claim unobserved success.

- [ ] **Step 4: Replace stale-zone trigger semantics**

An open zone adds a candidate. Require fresh evidence, recomputed zone/stop/target, Analyst pass, and
Checker approval before Telegram. If the new result differs, store Watch/invalidated observation and
stay silent unless the fresh result itself is actionable.

- [ ] **Step 5: Add pre-market provisional semantics**

The pre-market run may identify scenarios and risks but cannot present a prior-close quote as a live
entry trigger. Monthly Core DCA remains planning, not execution.

- [ ] **Step 6: Update exact Routine prompts**

Document three copy-paste prompts that explicitly identify run kind, require an independent fresh
packet, run lifecycle, checker pass, and notification policy.

- [ ] **Step 7: Validate structure**

```bash
python -m json.tool config/settings.json >/dev/null
python - <<'PY'
import pathlib, yaml
for p in pathlib.Path('docs/eval').glob('*.yaml'):
    yaml.safe_load(p.read_text())
PY
rg -n "edit.*watchlist.json|add it to.*watchlist.json|remove it from.*watchlist.json" skills/market-briefing/SKILL.md
```

Expected: JSON/YAML parse and the contradiction search returns no live-edit instructions.

- [ ] **Step 8: Commit**

```bash
git add skills/market-briefing/SKILL.md config/settings.json docs/eval/market-briefing-eval.yaml routines/README.md
git commit -m "feat: require fresh checked market analysis"
```

---

### Task 8: Read-Only Weekly ChatGPT Audit Packet

**Files:**
- Create: `lib/weekly_audit.py`
- Create: `scripts/weekly_audit_packet.py`
- Create: `tests/test_weekly_audit.py`
- Create: `skills/weekly-portfolio-audit/SKILL.md`

**Interfaces:**
- Produces `build_packet(*, holdings, transactions, suggestions, grades, lessons, snapshots, generated_at) -> dict`.
- CLI prints one compact JSON packet to stdout and performs no writes.

- [ ] **Step 1: Write failing pure packet tests**

Verify stable keys, ticker filtering, bounded list sizes, no secret fields, explicit missing-stop flags,
and no mutation of inputs.

- [ ] **Step 2: Run and observe failure**

```bash
.venv/bin/python -m pytest tests/test_weekly_audit.py -q
```

- [ ] **Step 3: Implement packet builder and CLI**

Read bounded DB helpers only. Include at most 50 suggestions, 100 grades, 50 transactions, 40 lessons,
and 150 snapshots. Redact keys containing `token`, `secret`, `key`, or `authorization`. Print JSON;
do not write a file, DB row, or Telegram message.

- [ ] **Step 4: Write the weekly audit skill**

Require the scheduled agent to run the packet script, audit process/data quality, distinguish facts
from inferences, avoid fresh trade recommendations, and remain read-only.

- [ ] **Step 5: Verify**

```bash
.venv/bin/python -m pytest tests/test_weekly_audit.py -q
.venv/bin/python scripts/weekly_audit_packet.py | .venv/bin/python -m json.tool >/dev/null
```

- [ ] **Step 6: Commit**

```bash
git add lib/weekly_audit.py scripts/weekly_audit_packet.py tests/test_weekly_audit.py skills/weekly-portfolio-audit/SKILL.md
git commit -m "feat: build read-only weekly audit packet"
```

---

### Task 9: Reconciliation Skill and User Documentation

**Files:**
- Modify: `skills/reconcile-trade/SKILL.md`
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Consumes the Telegram recorder and existing manual reconciliation path.
- Produces clear owner instructions and removes stale claims about file-based holdings.

- [ ] **Step 1: Update reconciliation guidance**

Make Telegram Confirm the preferred path. Keep Claude chat reconciliation as a fallback, but require
the same validation and prohibit direct `_sb()` mutations when a public helper/RPC exists.

- [ ] **Step 2: Update README architecture/setup/cost/security**

Document accepted commands, confirmation behavior, free Telegram/Supabase boundary, token rotation,
Edge Function deployment, ChatGPT usage impact, and computer-on requirement.

- [ ] **Step 3: Update roadmap status**

Mark the reliable-run, Telegram recorder, and weekly audit work as implemented locally, with live
deployment status recorded separately from code completion.

- [ ] **Step 4: Verify stale text is gone**

```bash
rg -n "edits this file|No more portfolio.json|auto-promotes.*watchlist.json" README.md skills config
git diff --check
```

- [ ] **Step 5: Commit**

```bash
git add skills/reconcile-trade/SKILL.md README.md docs/ROADMAP.md
git commit -m "docs: explain reliable portfolio workflow"
```

---

### Task 10: Integrated Verification and Deployment

**Files:**
- Modify as required by verified defects only.

**Interfaces:**
- Produces deployed Supabase schema/function, registered Telegram webhook, revised Cloud Routine
  instructions, and active weekly ChatGPT scheduled task.

- [ ] **Step 1: Run all local checks**

```bash
.venv/bin/python -m pytest tests/ -q
node --test tests/test_telegram_parser.mjs
deno check supabase/functions/telegram-portfolio/index.ts
python -m json.tool config/settings.json >/dev/null
git diff --check
git status --short
```

- [ ] **Step 2: Rotate credentials**

Rotate Telegram bot token, Finnhub key, and Alpha Vantage key in their provider consoles. Replace
values in ignored local secrets, Claude Routine environment, Supabase function secrets, and local
Codex MCP config. Verify old credentials fail and never print either old or new values.

- [ ] **Step 3: Deploy Supabase changes**

Apply migration, set six function secrets, deploy `telegram-portfolio`, and register the webhook.
Verify `getWebhookInfo` reports the expected URL and zero recent errors without printing the token.

- [ ] **Step 4: Exercise Telegram safely**

Run `/help` and `/portfolio`, Cancel a test command, apply disposable `TSTTG` Buy/Sell, replay the
same update, attempt an oversell, and confirm cleanup. Then record the owner's real current holdings
through confirmed commands.

- [ ] **Step 5: Update Claude Routines**

Replace each saved prompt with the documented exact prompt. Run each in dry-run mode from a separate
safe session; confirm no Telegram/DB side effects and inspect freshness/checker/run-log behavior.

- [ ] **Step 6: Create the ChatGPT weekly task**

Create a local scheduled task for Friday 16:30 America/Chicago, model GPT-5.6 Terra, high reasoning,
prompted to read `skills/weekly-portfolio-audit/SKILL.md`. Run it once manually and confirm no writes
or Telegram sends.

- [ ] **Step 7: Final security and behavior review**

Confirm no secrets are tracked, no brokerage dependency/tool exists, Edge Function auth fails closed,
market action gates use freshness, and task/routine summaries match observed calls.

- [ ] **Step 8: Commit verified corrections and report deployment truthfully**

Any correction discovered here must be committed with the exact files that failed the preceding
check; if every check passes, create no empty final commit. Do not claim a deployment step succeeded
unless its provider response or UI state was directly observed.
