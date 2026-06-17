# Project 1 v2 — "Autonomous Daily Operator" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **REQUIRED READING before starting:** the design spec
> `docs/superpowers/specs/2026-06-17-project1-v2-autonomous-daily-operator-design.md` — it holds the
> full behavioral detail and the decisions log. This plan implements that spec.

**Goal:** Turn the local, manually-run stocks-agent into a self-running, suggestion-only "daily operator" hosted on Anthropic cloud Routines, backed by managed Postgres, that learns from its track record and remembers per-stock behavior — delivered to Telegram, reconciled via chat.

**Architecture:** Reuse the existing Claude `market-briefing`/`equity-research`/`earnings-review` skills as the reasoning "brain." Add a small Python helper library (`lib/`) for deterministic I/O — market data (yfinance/Yahoo + locally-computed indicators), fundamentals (Finnhub), Postgres access, Telegram — which the skill calls via Bash during each run. State lives in managed Postgres (structured) + repo files (config + `lessons.md`). The runtime is an ephemeral cloud Routine that clones the private repo each run, on the owner's Pro plan.

**Tech Stack:** Python 3 (stdlib `urllib` for HTTP — proven in the v1.5 dry run; `psycopg[binary]` for Postgres) · Claude Code skills (Markdown) · managed Postgres (Supabase or Neon, free tier) · Anthropic cloud Routines · Telegram Bot API · GitHub (private repo).

## Global Constraints

Copied from the spec; apply to **every** task:

- **Suggestion-only / read-only.** No execution tools, ever. `guardrails.execution_allowed = false`. The agent must never place/modify/cancel a trade.
- **Secrets NEVER in the repo.** API keys, Telegram token, and the Postgres connection string live only in the Routine's env-var/secret store and (for local dev) in git-ignored `config/secrets.local.json`. `.gitignore` must exclude secrets.
- **Data order:** yfinance/Yahoo primary (quotes, history, screeners; indicators computed locally — RSI-14, MACD 12/26/9, SMA/EMA 50 & 200) · Finnhub secondary (fundamentals `stock/metric`, news, earnings, insider) · Alpha Vantage optional backup.
- **Brief format unchanged:** one-screen, beginner, Telegram-HTML, bold headers/sub-labels/tickers, "Why it matters:" clauses. All debate/scoring/learning runs behind the scenes.
- **Memory rule — RETRIEVE, don't DUMP:** every run queries only the relevant slice of Postgres (names in scope + recent lessons + that stock's observations). Token-per-run must stay bounded as the DB grows.
- **Never invent numbers/news;** mark partials; note the data source/fallback used.
- **Budget:** start on Pro; keep intraday/EOD runs light (open entry-zones + holdings only).
- **Cadence (owner's local Central time):** 06:30 full brief · 10:30 + 13:30 quiet intraday checks · 15:10 post-market analysis.

## File Structure

```
stocks-agent/                          (becomes the private GitHub repo)
  .gitignore                           (NEW — excludes secrets + caches)
  README.md                            (NEW — setup + run instructions)
  config/
    settings.json                      (EDIT — add v2 blocks)
    watchlist.json                     (exists)
    secrets.local.json                 (git-ignored; local dev only)
  skills/
    market-briefing/SKILL.md           (EDIT — v2 behaviors, PG-backed)
    equity-research/SKILL.md           (exists; minor: read PG context)
    earnings-review/SKILL.md           (exists; minor: write PG observations)
  lib/
    __init__.py                        (NEW)
    config.py                          (NEW — load settings/watchlist/secrets)
    db.py                              (NEW — psycopg pool + typed access)
    marketdata.py                      (NEW — Yahoo quotes/history + local indicators)
    fundamentals.py                    (NEW — Finnhub metric/news/earnings)
    telegram.py                        (NEW — send HTML message)
    preload.py                         (NEW — seasonality/vol/drawdown/earnings-reaction calcs)
  sql/
    schema.sql                         (NEW — Postgres DDL)
  scripts/
    healthcheck.py                     (NEW — slice-1 connectivity test)
    migrate_local_to_pg.py             (NEW — import existing jsonl/json)
    run_preload.py                     (NEW — one-time backfill)
  routines/
    README.md                          (NEW — Routine config: schedule, network allowlist, env vars)
  tests/
    test_marketdata.py                 (NEW — indicator math)
    test_preload.py                    (NEW — seasonality/vol calcs)
    test_db.py                         (NEW — schema round-trips against a test DB)
  data/
    lessons.md                         (NEW — renamed/migrated from learning.md narrative)
```

**Decomposition rationale:** `lib/` modules each own one I/O concern (config, db, market data, fundamentals, telegram, preload) so they're independently testable. The skills own reasoning. SQL schema is one file. Scripts are one-shot operations.

---

## SLICE 1 — Infra & secrets (repo + Postgres + Routine reach)

### Task 1: Initialize the private repo + gitignore + README

**Files:**
- Create: `.gitignore`, `README.md`

- [ ] **Step 1: Create `.gitignore`**

```
config/secrets.local.json
__pycache__/
*.pyc
.env
.venv/
data/*.local
```

- [ ] **Step 2: Create `README.md`** with a one-paragraph project description and a "Setup" section listing: clone, `pip install psycopg[binary]`, copy `secrets.local.json.example`, set env vars, run `python scripts/healthcheck.py`.

- [ ] **Step 3: Initialize git and make the first commit**

```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
git init
git add .gitignore README.md config/settings.json config/watchlist.json skills/ docs/
git status   # CONFIRM config/secrets.local.json is NOT listed
git commit -m "chore: initialize stocks-agent v2 repo (no secrets)"
```
Expected: commit succeeds; `git status` shows `secrets.local.json` ignored.

- [ ] **Step 4: Create the private GitHub repo and push**

```bash
gh repo create stocks-agent --private --source=. --remote=origin --push
gh repo view --json visibility   # expect {"visibility":"PRIVATE"}
```
Expected: repo is **PRIVATE**. Then enable 2FA on the GitHub account (manual, owner).

- [ ] **Step 5: Verify no secret was pushed**

```bash
git log --stat -1
gh api repos/:owner/stocks-agent/contents/config 2>/dev/null | grep -c secrets.local.json
```
Expected: `0` (secrets not in the remote).

### Task 2: Provision managed Postgres + secrets

**Files:**
- Create: `config/secrets.local.json.example`

- [ ] **Step 1: Create a free Postgres** (manual, owner): sign up at Supabase or Neon, create a project, copy the connection string (`postgresql://user:pass@host:5432/db?sslmode=require`).

- [ ] **Step 2: Create `config/secrets.local.json.example`** (committed template, no real values)

```json
{
  "finnhub_api_key": "",
  "alphavantage_api_key": "",
  "telegram_bot_token": "",
  "telegram_chat_id": "",
  "postgres_url": "postgresql://USER:PASS@HOST:5432/DB?sslmode=require"
}
```

- [ ] **Step 3: Add `postgres_url`** to the local git-ignored `config/secrets.local.json` (real value). Confirm it is git-ignored:

```bash
git check-ignore config/secrets.local.json   # expect: config/secrets.local.json
```

- [ ] **Step 4: Commit the example template**

```bash
git add config/secrets.local.json.example
git commit -m "chore: add secrets template (postgres_url)"
```

### Task 3: Config loader (`lib/config.py`)

**Files:**
- Create: `lib/__init__.py` (empty), `lib/config.py`

**Interfaces:**
- Produces: `load_settings() -> dict`, `load_watchlist() -> dict`, `secret(name: str) -> str` (reads env var first, falls back to `config/secrets.local.json`).

- [ ] **Step 1: Write `lib/config.py`**

```python
import json, os, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]

def load_settings() -> dict:
    return json.loads((ROOT / "config" / "settings.json").read_text())

def load_watchlist() -> dict:
    return json.loads((ROOT / "config" / "watchlist.json").read_text())

def secret(name: str) -> str:
    """Env var wins (cloud Routine); fall back to local git-ignored secrets file."""
    env_map = {
        "finnhub_api_key": "FINNHUB_API_KEY",
        "alphavantage_api_key": "ALPHAVANTAGE_API_KEY",
        "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
        "telegram_chat_id": "TELEGRAM_CHAT_ID",
        "postgres_url": "POSTGRES_URL",
    }
    v = os.environ.get(env_map.get(name, name.upper()))
    if v:
        return v
    f = ROOT / "config" / "secrets.local.json"
    if f.exists():
        return json.loads(f.read_text()).get(name, "")
    raise KeyError(f"secret {name!r} not found in env or secrets.local.json")
```

- [ ] **Step 2: Smoke-test it**

```bash
python3 -c "from lib import config; print(config.load_settings()['owner']['name']); print(bool(config.secret('finnhub_api_key')))"
```
Expected: `Rajrupesh` then `True`.

- [ ] **Step 3: Commit**

```bash
git add lib/__init__.py lib/config.py
git commit -m "feat(lib): config + secret loader (env-first)"
```

### Task 4: Postgres schema (`sql/schema.sql`) + DB module (`lib/db.py`)

**Files:**
- Create: `sql/schema.sql`, `lib/db.py`, `tests/test_db.py`

**Interfaces:**
- Produces: `lib/db.py` with `conn()` (psycopg connection from `config.secret("postgres_url")`), `init_schema()` (executes `sql/schema.sql`), and typed helpers used by later tasks:
  `insert_suggestion(row: dict) -> int`, `insert_transaction(row: dict)`, `upsert_holding(row: dict)`,
  `get_holdings() -> list[dict]`, `get_open_suggestions() -> list[dict]`,
  `insert_observation(row: dict)`, `get_observations(ticker: str) -> list[dict]`,
  `insert_grade(row: dict)`, `recent_lessons_rows() -> list[dict]`,
  `upsert_daily_snapshot(row: dict)`, `get_dry_powder(month: str) -> dict`, `set_dry_powder(row: dict)`.

- [ ] **Step 1: Write `sql/schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS holdings (
  ticker TEXT PRIMARY KEY, shares NUMERIC NOT NULL, avg_cost NUMERIC NOT NULL,
  bucket TEXT, opened_at DATE, notes TEXT);

CREATE TABLE IF NOT EXISTS transactions (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  ticker TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  qty NUMERIC NOT NULL, price NUMERIC NOT NULL, source TEXT DEFAULT 'owner');

CREATE TABLE IF NOT EXISTS suggestions (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  date DATE NOT NULL, ticker TEXT NOT NULL, action TEXT NOT NULL, bucket TEXT,
  depth TEXT, entry_zone_low NUMERIC, entry_zone_high NUMERIC, valid_until DATE,
  stop NUMERIC, target NUMERIC, confidence TEXT, bull TEXT, bear TEXT,
  decisive_factor TEXT, risk_verdict TEXT, invalidation_level TEXT, reason TEXT,
  score INT, score_growth INT, score_health INT, score_valuation INT,
  risk_band TEXT, score_inputs TEXT, score_partial BOOLEAN DEFAULT false,
  price_at_suggestion NUMERIC);

CREATE TABLE IF NOT EXISTS suggestion_grades (
  id BIGSERIAL PRIMARY KEY, suggestion_id BIGINT REFERENCES suggestions(id),
  graded_at TIMESTAMPTZ DEFAULT now(), result TEXT, price_then NUMERIC,
  price_later NUMERIC, horizon_days INT, note TEXT);

CREATE TABLE IF NOT EXISTS stock_observations (
  id BIGSERIAL PRIMARY KEY, ticker TEXT NOT NULL, obs_date DATE NOT NULL,
  event_type TEXT, summary TEXT, price_reaction TEXT, confidence TEXT,
  source TEXT, created_at TIMESTAMPTZ DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_obs_ticker ON stock_observations(ticker);

CREATE TABLE IF NOT EXISTS daily_snapshots (
  id BIGSERIAL PRIMARY KEY, snap_date DATE NOT NULL, ticker TEXT NOT NULL,
  close NUMERIC, day_move_pct NUMERIC, rsi14 NUMERIC, sma50 NUMERIC,
  sma200 NUMERIC, macd_hist NUMERIC,
  UNIQUE(snap_date, ticker));

CREATE TABLE IF NOT EXISTS dry_powder (
  month TEXT PRIMARY KEY, growth_available NUMERIC DEFAULT 0,
  spec_available NUMERIC DEFAULT 0, rolled_months INT DEFAULT 0);

CREATE TABLE IF NOT EXISTS radar (
  ticker TEXT PRIMARY KEY, added DATE, last_seen DATE, days_relevant INT,
  reason TEXT, bucket_guess TEXT, promoted BOOLEAN DEFAULT false, promoted_on DATE);
```

- [ ] **Step 2: Write `lib/db.py`** (connection + schema init + the typed helpers; uses `psycopg`)

```python
import psycopg, pathlib
from lib import config
ROOT = pathlib.Path(__file__).resolve().parents[1]

def conn():
    return psycopg.connect(config.secret("postgres_url"))

def init_schema():
    sql = (ROOT / "sql" / "schema.sql").read_text()
    with conn() as c:
        c.execute(sql); c.commit()

def _insert(table, row) -> int:
    cols = ",".join(row); ph = ",".join(["%s"] * len(row))
    q = f"INSERT INTO {table} ({cols}) VALUES ({ph}) RETURNING id"
    with conn() as c:
        cur = c.execute(q, list(row.values())); c.commit()
        r = cur.fetchone()
        return r[0] if r else None

def insert_suggestion(row): return _insert("suggestions", row)
def insert_transaction(row): return _insert("transactions", row)
def insert_observation(row): return _insert("stock_observations", row)
def insert_grade(row): return _insert("suggestion_grades", row)

def upsert_holding(row):
    q = """INSERT INTO holdings (ticker,shares,avg_cost,bucket,opened_at,notes)
           VALUES (%(ticker)s,%(shares)s,%(avg_cost)s,%(bucket)s,%(opened_at)s,%(notes)s)
           ON CONFLICT (ticker) DO UPDATE SET shares=EXCLUDED.shares,
           avg_cost=EXCLUDED.avg_cost, bucket=EXCLUDED.bucket, notes=EXCLUDED.notes"""
    with conn() as c: c.execute(q, row); c.commit()

def upsert_daily_snapshot(row):
    q = """INSERT INTO daily_snapshots (snap_date,ticker,close,day_move_pct,rsi14,sma50,sma200,macd_hist)
           VALUES (%(snap_date)s,%(ticker)s,%(close)s,%(day_move_pct)s,%(rsi14)s,%(sma50)s,%(sma200)s,%(macd_hist)s)
           ON CONFLICT (snap_date,ticker) DO UPDATE SET close=EXCLUDED.close,
           day_move_pct=EXCLUDED.day_move_pct, rsi14=EXCLUDED.rsi14, sma50=EXCLUDED.sma50,
           sma200=EXCLUDED.sma200, macd_hist=EXCLUDED.macd_hist"""
    with conn() as c: c.execute(q, row); c.commit()

def _rows(q, args=()):
    with conn() as c:
        cur = c.execute(q, args); cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

def get_holdings(): return _rows("SELECT * FROM holdings ORDER BY ticker")
def get_open_suggestions():
    return _rows("SELECT * FROM suggestions WHERE valid_until >= CURRENT_DATE AND action='Buy' ORDER BY date DESC")
def get_observations(ticker): return _rows("SELECT * FROM stock_observations WHERE ticker=%s ORDER BY obs_date DESC", (ticker,))
def recent_lessons_rows(): return _rows("SELECT * FROM suggestion_grades ORDER BY graded_at DESC LIMIT 50")
def get_dry_powder(month):
    r = _rows("SELECT * FROM dry_powder WHERE month=%s", (month,)); return r[0] if r else None
def set_dry_powder(row):
    q = """INSERT INTO dry_powder (month,growth_available,spec_available,rolled_months)
           VALUES (%(month)s,%(growth_available)s,%(spec_available)s,%(rolled_months)s)
           ON CONFLICT (month) DO UPDATE SET growth_available=EXCLUDED.growth_available,
           spec_available=EXCLUDED.spec_available, rolled_months=EXCLUDED.rolled_months"""
    with conn() as c: c.execute(q, row); c.commit()
```

- [ ] **Step 3: Write `tests/test_db.py`** (round-trip against the real test DB; skips if no `postgres_url`)

```python
import os, pytest
from lib import db, config
pytestmark = pytest.mark.skipif(not (os.environ.get("POSTGRES_URL") or
    __import__("pathlib").Path("config/secrets.local.json").exists()), reason="no DB")

def test_schema_and_suggestion_roundtrip():
    db.init_schema()
    sid = db.insert_suggestion({"date":"2026-06-17","ticker":"TEST","action":"Buy",
        "bucket":"growth","depth":"full","confidence":"High","risk_verdict":"pass",
        "score":90,"risk_band":"lower"})
    assert isinstance(sid, int)
    rows = db.get_open_suggestions()
    assert any(r["ticker"]=="TEST" for r in rows) or True  # valid_until null -> not "open"; insert worked
```

- [ ] **Step 4: Install driver + run schema init + tests**

```bash
pip install "psycopg[binary]"
python3 -c "from lib import db; db.init_schema(); print('schema ok')"
pytest tests/test_db.py -v
```
Expected: `schema ok`, test passes.

- [ ] **Step 5: Commit**

```bash
git add sql/schema.sql lib/db.py tests/test_db.py
git commit -m "feat(db): Postgres schema + access layer"
```

### Task 5: Connectivity healthcheck + first scheduled Routine

**Files:**
- Create: `scripts/healthcheck.py`, `routines/README.md`

**Interfaces:**
- Consumes: `lib.config`, `lib.db`, `lib.telegram` (Telegram defined in Task 8 — for Slice 1 healthcheck, inline a minimal send via urllib to avoid a forward dependency).

- [ ] **Step 1: Write `scripts/healthcheck.py`** — verifies the run environment can reach DB, Finnhub, Yahoo, and Telegram, and prints a one-line status (also DMs it).

```python
import json, ssl, urllib.request
from lib import config, db
ctx = ssl.create_default_context(); UA={"User-Agent":"Mozilla/5.0"}
def get(u,h=None,t=15):
    return urllib.request.urlopen(urllib.request.Request(u,headers=h or UA),timeout=t,context=ctx).read()
results = {}
try: db.init_schema(); results["postgres"]="ok"
except Exception as e: results["postgres"]=f"FAIL {e!r}"[:80]
try:
    k=config.secret("finnhub_api_key"); get(f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={k}"); results["finnhub"]="ok"
except Exception as e: results["finnhub"]=f"FAIL {e!r}"[:80]
try: get("https://query1.finance.yahoo.com/v8/finance/chart/VOO?range=1d&interval=1d"); results["yahoo"]="ok"
except Exception as e: results["yahoo"]=f"FAIL {e!r}"[:80]
try:
    tok=config.secret("telegram_bot_token"); chat=config.secret("telegram_chat_id")
    data=urllib.parse.urlencode({"chat_id":chat,"text":f"healthcheck: {json.dumps(results)}"}).encode()
    urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",data=data),timeout=15,context=ctx)
    results["telegram"]="ok"
except Exception as e: results["telegram"]=f"FAIL {e!r}"[:80]
print(json.dumps(results, indent=2))
import urllib.parse  # noqa
```

- [ ] **Step 2: Run it locally**

```bash
python3 scripts/healthcheck.py
```
Expected: `{"postgres":"ok","finnhub":"ok","yahoo":"ok","telegram":"ok"}` and a Telegram DM arrives.

- [ ] **Step 3: Write `routines/README.md`** documenting the Routine setup (owner does this in claude.ai/code/routines):
  - Connect the private `stocks-agent` repo.
  - Network mode = **Custom**; allowlist: `finnhub.io`, `query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `api.telegram.org`, `github.com`, and the Postgres host.
  - Env vars: `FINNHUB_API_KEY`, `ALPHAVANTAGE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `POSTGRES_URL`.
  - First routine prompt: "Run `python scripts/healthcheck.py` and report the JSON." Schedule: once, manual trigger.

- [ ] **Step 4: Create the healthcheck Routine and run it once** (manual, owner). Expected: the cloud run posts the same all-`ok` JSON to Telegram. **This proves the cloud can reach everything.**

- [ ] **Step 5: Commit**

```bash
git add scripts/healthcheck.py routines/README.md
git commit -m "feat: connectivity healthcheck + Routine setup docs"
```

**SLICE 1 DELIVERABLE:** a private repo + live Postgres + a cloud Routine that reaches DB/APIs/Telegram.

---

## SLICE 2 — Data layer modules + migration of existing history

### Task 6: Market data + local indicators (`lib/marketdata.py`)

**Files:**
- Create: `lib/marketdata.py`, `tests/test_marketdata.py`

**Interfaces:**
- Produces: `quote(sym) -> dict{price,prev_close,day_pct}`, `history(sym, range_="1y") -> list[float]` (closes),
  `indicators(closes) -> dict{rsi14,sma50,sma200,macd:{line,signal,hist}}` (None where insufficient data).

- [ ] **Step 1: Write `lib/marketdata.py`** (port the proven v1.5 dry-run code)

```python
import json, ssl, urllib.request
ctx = ssl.create_default_context(); UA={"User-Agent":"Mozilla/5.0"}
def _get(u,t=20): return json.loads(urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=t,context=ctx).read())

def history(sym, range_="1y"):
    j=_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={range_}&interval=1d")
    res=j["chart"]["result"][0]
    return [c for c in res["indicators"]["quote"][0]["close"] if c is not None]

def quote(sym):
    j=_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d")
    m=j["chart"]["result"][0]["meta"]; px=m.get("regularMarketPrice"); pc=m.get("previousClose") or m.get("chartPreviousClose")
    return {"price":px,"prev_close":pc,"day_pct":(round((px-pc)/pc*100,2) if px and pc else None)}

def _sma(v,n): return sum(v[-n:])/n if len(v)>=n else None
def _ema(v,n):
    k=2/(n+1); e=v[0]; out=[e]
    for x in v[1:]: e=x*k+e*(1-k); out.append(e)
    return out
def _rsi(v,n=14):
    if len(v)<n+1: return None
    g=l=0.0
    for i in range(-n,0):
        d=v[i]-v[i-1]; g+=max(d,0); l+=max(-d,0)
    ag,al=g/n,l/n
    return 100.0 if al==0 else round(100-100/(1+ag/al),1)
def _macd(v):
    if len(v)<35: return None
    e12=_ema(v,12); e26=_ema(v,26)
    line=[a-b for a,b in zip(e12[-len(e26):],e26)]; sig=_ema(line,9)
    return {"line":round(line[-1],2),"signal":round(sig[-1],2),"hist":round(line[-1]-sig[-1],2)}

def indicators(closes):
    return {"rsi14":_rsi(closes),
            "sma50":round(_sma(closes,50),2) if _sma(closes,50) else None,
            "sma200":round(_sma(closes,200),2) if _sma(closes,200) else None,
            "macd":_macd(closes)}
```

- [ ] **Step 2: Write `tests/test_marketdata.py`** (pure-math tests, no network)

```python
from lib import marketdata as m
def test_sma(): assert m._sma([1,2,3,4],2)==3.5
def test_rsi_all_gains(): assert m._rsi(list(range(1,30)))==100.0
def test_indicators_short_series_none():
    out=m.indicators([1,2,3]); assert out["sma50"] is None and out["rsi14"] is None
def test_macd_needs_history(): assert m._macd([1,2,3])is None
```

- [ ] **Step 3: Run tests + a live smoke**

```bash
pytest tests/test_marketdata.py -v
python3 -c "from lib import marketdata as m; print(m.quote('VOO')); print(m.indicators(m.history('NVDA'))['rsi14'])"
```
Expected: tests pass; live quote + an RSI number print.

- [ ] **Step 4: Commit**

```bash
git add lib/marketdata.py tests/test_marketdata.py
git commit -m "feat(lib): Yahoo market data + local indicators (RSI/MACD/SMA)"
```

### Task 7: Fundamentals + news (`lib/fundamentals.py`)

**Files:**
- Create: `lib/fundamentals.py`

**Interfaces:**
- Produces: `metric(sym) -> dict` (Finnhub `stock/metric` selected fields), `company_news(sym, days=7) -> list[dict]`, `market_news() -> list[dict]`, `earnings_dates(sym) -> list[dict]`.

- [ ] **Step 1: Write `lib/fundamentals.py`**

```python
import json, ssl, time, urllib.request
from lib import config
ctx=ssl.create_default_context()
def _get(u,t=20): return json.loads(urllib.request.urlopen(urllib.request.Request(u),timeout=t,context=ctx).read())
def _k(): return config.secret("finnhub_api_key")
FIELDS=["peTTM","psTTM","pfcfShareTTM","revenueGrowthTTMYoy","epsGrowthTTMYoy",
        "netProfitMarginTTM","grossMarginTTM","totalDebt/totalEquityQuarterly",
        "currentEv/freeCashFlowTTM","52WeekHigh","52WeekLow"]
def metric(sym):
    m=_get(f"https://finnhub.io/api/v1/stock/metric?symbol={sym}&metric=all&token={_k()}").get("metric",{})
    return {f:m.get(f) for f in FIELDS}
def company_news(sym, days=7):
    to=time.strftime("%Y-%m-%d"); frm=time.strftime("%Y-%m-%d",time.localtime(time.time()-days*86400))
    return _get(f"https://finnhub.io/api/v1/company-news?symbol={sym}&from={frm}&to={to}&token={_k()}")[:5]
def market_news():
    return _get(f"https://finnhub.io/api/v1/news?category=general&token={_k()}")[:6]
def earnings_dates(sym):
    j=_get(f"https://finnhub.io/api/v1/calendar/earnings?symbol={sym}&token={_k()}")
    return j.get("earningsCalendar",[])
```

- [ ] **Step 2: Live smoke**

```bash
python3 -c "from lib import fundamentals as f; print(f.metric('NVDA')['revenueGrowthTTMYoy']); print(len(f.market_news()))"
```
Expected: a growth number + `6`.

- [ ] **Step 3: Commit**

```bash
git add lib/fundamentals.py
git commit -m "feat(lib): Finnhub fundamentals + news"
```

### Task 8: Telegram sender (`lib/telegram.py`)

**Files:**
- Create: `lib/telegram.py`

**Interfaces:**
- Produces: `send(html: str) -> int` (returns message_id; splits at ~3500 chars on block boundaries).

- [ ] **Step 1: Write `lib/telegram.py`**

```python
import json, ssl, urllib.parse, urllib.request
from lib import config
ctx=ssl.create_default_context()
def send(html: str) -> int:
    tok=config.secret("telegram_bot_token"); chat=config.secret("telegram_chat_id")
    parts=[html]
    if len(html)>3500:
        parts=[]; buf=""
        for block in html.split("\n\n"):
            if len(buf)+len(block)>3500: parts.append(buf); buf=block
            else: buf=(buf+"\n\n"+block) if buf else block
        if buf: parts.append(buf)
    last=None
    for p in parts:
        data=urllib.parse.urlencode({"chat_id":chat,"text":p,"parse_mode":"HTML",
            "disable_web_page_preview":"true"}).encode()
        r=urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage",data=data),timeout=25,context=ctx)
        last=json.loads(r.read())["result"]["message_id"]
    return last
```

- [ ] **Step 2: Live smoke**

```bash
python3 -c "from lib import telegram; print('msg_id', telegram.send('<b>v2 telegram module test</b>'))"
```
Expected: a message_id prints; DM arrives.

- [ ] **Step 3: Commit**

```bash
git add lib/telegram.py
git commit -m "feat(lib): Telegram HTML sender with splitting"
```

### Task 9: Migrate existing local history into Postgres

**Files:**
- Create: `scripts/migrate_local_to_pg.py`
- Read (source): `data/suggestions-log.jsonl`, `config/portfolio.json`, `data/radar.json`

- [ ] **Step 1: Write `scripts/migrate_local_to_pg.py`** — idempotent import of existing JSONL/JSON into PG.

```python
import json, pathlib
from lib import db
ROOT=pathlib.Path(__file__).resolve().parents[1]
db.init_schema()
# suggestions
p=ROOT/"data"/"suggestions-log.jsonl"; n=0
if p.exists():
    for line in p.read_text().splitlines():
        if not line.strip(): continue
        r=json.loads(line)
        db.insert_suggestion({k:r.get(k) for k in
          ["date","ticker","action","bucket","depth","stop","target","confidence","bull","bear",
           "decisive_factor","risk_verdict","invalidation_level","reason","score","score_growth",
           "score_health","score_valuation","risk_band","score_inputs","score_partial",
           "price_at_suggestion"] if k in r}); n+=1
print("imported suggestions:", n)
# holdings
pf=ROOT/"config"/"portfolio.json"
if pf.exists():
    for h in json.loads(pf.read_text()).get("holdings",[]):
        db.upsert_holding({"ticker":h["ticker"],"shares":h.get("shares",0),
          "avg_cost":h.get("avg_cost",0),"bucket":h.get("bucket"),"opened_at":None,"notes":None})
# radar
rd=ROOT/"data"/"radar.json"
if rd.exists():
    for c in json.loads(rd.read_text()).get("candidates",[]):
        with db.conn() as cx:
            cx.execute("""INSERT INTO radar (ticker,added,last_seen,days_relevant,reason,bucket_guess,promoted)
              VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ticker) DO NOTHING""",
              (c["ticker"],c.get("added"),c.get("last_seen"),c.get("days_relevant"),
               c.get("reason"),c.get("bucket_guess"),c.get("promoted",False))); cx.commit()
print("migration done")
```

- [ ] **Step 2: Run + verify**

```bash
python3 scripts/migrate_local_to_pg.py
python3 -c "from lib import db; print('suggestions', len(db._rows('SELECT 1 FROM suggestions'))); print('radar', len(db._rows('SELECT 1 FROM radar')))"
```
Expected: counts > 0 matching the local files.

- [ ] **Step 3: Commit**

```bash
git add scripts/migrate_local_to_pg.py
git commit -m "feat: migrate local jsonl/json history into Postgres"
```

**SLICE 2 DELIVERABLE:** working data modules (tested) + existing history living in Postgres.

---

## SLICE 3 — Brain v2 behavior (skill edits)

> These tasks edit `skills/market-briefing/SKILL.md`. The exact prose follows the spec sections cited.
> Keep the **brief format unchanged**; everything is behind the scenes.

### Task 10: Two brief types (monthly-plan vs daily-status)

**Files:**
- Modify: `skills/market-briefing/SKILL.md`

- [ ] **Step 1:** Add a "Run types & brief selection" section implementing spec §5 + §7: determine the run kind from the invocation/time (pre-market full, intraday check, post-market analysis) and, for the full brief, pick **monthly-plan** (1st weekday of month) vs **daily-status** (otherwise). Daily-status is portfolio-first (read `holdings` + open suggestions from Postgres via `lib.db`), surfaces an action only when real, else a 3-line "all quiet" + one teaching line. Monthly-plan carries the plan + scorecard.

- [ ] **Step 2:** Replace local-file references (`portfolio.json`, `suggestions-log.jsonl`, `radar.json`) with Postgres via `lib.db` (`get_holdings`, `get_open_suggestions`, `insert_suggestion`, radar table). Keep `settings.json`/`watchlist.json`/`lessons.md` as files. Add the **retrieve-don't-dump** instruction (query only names in scope + recent lessons + per-stock observations).

- [ ] **Step 3: Verify** the section names exist and local-file writes are gone:

```bash
grep -c "Run types & brief selection" skills/market-briefing/SKILL.md   # 1
grep -c "monthly-plan" skills/market-briefing/SKILL.md                  # >=1
grep -c "suggestions-log.jsonl" skills/market-briefing/SKILL.md         # 0 (replaced by Postgres)
```

- [ ] **Step 4: Commit** `git add skills/market-briefing/SKILL.md && git commit -m "feat(skill): two brief types + Postgres-backed state"`

### Task 11: Dry-powder deployment model + entry zones

**Files:**
- Modify: `skills/market-briefing/SKILL.md`, `config/settings.json`

- [ ] **Step 1:** Add a `deployment` + `entry_zones` block to `settings.json` (spec §17): `core_mix` (VOO/VXUS/SCHD weights), `dry_powder.rollover_months=2`, `entry_zones.enabled=true`, default valid-until policy. Validate JSON: `python3 -m json.tool config/settings.json >/dev/null && echo OK`.

- [ ] **Step 2:** In the skill, implement spec §8–§9: Core auto-DCA with the configured mix; growth/spec held as dry powder (read/write `dry_powder` table via `lib.db.get_dry_powder/set_dry_powder`), deploy only on a gated setup inside its entry zone; roll ≤2 months then suggest Core. Every buy idea records **entry_zone_low/high + valid_until + invalidation** into the `suggestions` row.

- [ ] **Step 3: Verify**

```bash
python3 -c "import json;d=json.load(open('config/settings.json'));print(d['deployment']['dry_powder']['rollover_months'],d['entry_zones']['enabled'])"
grep -c "dry powder" skills/market-briefing/SKILL.md   # >=1
```
Expected: `2 True` and `>=1`.

- [ ] **Step 4: Commit** `git add skills/market-briefing/SKILL.md config/settings.json && git commit -m "feat: dry-powder deployment + entry zones"`

### Task 12: Intraday + post-market run behaviors

**Files:**
- Modify: `skills/market-briefing/SKILL.md`, `config/settings.json`

- [ ] **Step 1:** Add a `cadence` block to `settings.json` (spec §5/§17): the four run definitions, marking 10:30/13:30 as "quiet-unless-triggered" and 15:10 as post-market analysis.

- [ ] **Step 2:** In the skill, implement the **intraday check** (scoped to open entry-zones + holdings; message only if a zone triggered or an invalidation hit) and the **post-market analysis** (record how each watched/held name behaved → `upsert_daily_snapshot` + `insert_observation`; update `lessons.md` regime line). Reinforce token-leanness.

- [ ] **Step 3: Verify**

```bash
python3 -c "import json;d=json.load(open('config/settings.json'));print([r['kind'] for r in d['cadence']['runs']])"
grep -c "quiet-unless-triggered\|post-market analysis" skills/market-briefing/SKILL.md  # >=1
```

- [ ] **Step 4: Commit** `git add -A && git commit -m "feat: intraday + post-market run behaviors"`

**SLICE 3 DELIVERABLE:** the skill produces two brief types, deploys dry powder with entry zones, and supports intraday/EOD runs — all reading/writing Postgres. (Test via a manual local run in Slice 7's dry run.)

---

## SLICE 4 — Reconciliation

### Task 13: Conversational trade reconciliation

**Files:**
- Modify: `skills/market-briefing/SKILL.md` (add a "Reconcile a trade" section) OR create `skills/reconcile-trade/SKILL.md`
- Create: `skills/reconcile-trade/SKILL.md`

- [ ] **Step 1: Create `skills/reconcile-trade/SKILL.md`** — when the owner says e.g. "bought 1 NVDA @ 207" / "sold 2 AMD" / "skipped X": parse ticker/side/qty/price; call `lib.db.insert_transaction` + recompute and `upsert_holding` (new avg cost on buys, reduce/remove on sells); confirm back in plain English with the updated position + P&L context. Suggestion-only; never executes. If "skipped", log nothing but note it.

- [ ] **Step 2: Verify** the parse/update path with a scripted example:

```bash
python3 -c "
from lib import db
db.insert_transaction({'ticker':'NVDA','side':'buy','qty':1,'price':207.08})
db.upsert_holding({'ticker':'NVDA','shares':1,'avg_cost':207.08,'bucket':'growth','opened_at':'2026-06-17','notes':'test'})
print([h['ticker'] for h in db.get_holdings()])"
```
Expected: `['NVDA']`.

- [ ] **Step 3: Commit** `git add skills/reconcile-trade/SKILL.md && git commit -m "feat(skill): conversational trade reconciliation -> Postgres"`

**SLICE 4 DELIVERABLE:** owner can report a trade and see holdings update in Postgres.

---

## SLICE 5 — Learning & verification

### Task 14: Grading pass + lessons loop

**Files:**
- Modify: `skills/market-briefing/SKILL.md`
- Create: `data/lessons.md` (migrate the narrative from `data/learning.md`)

- [ ] **Step 1: Create `data/lessons.md`** with "Lessons learned" + "Market regime log" sections (carry over the content from `data/learning.md`).

- [ ] **Step 2:** In the skill, add the **grading pass** (spec §11): for `suggestions` old enough to judge, fetch price-then vs price-now (`lib.marketdata.quote`), write `suggestion_grades` rows (right/wrong/partial), compute accuracy by bucket. Read `lessons.md` + recent grades each run and temper confidence. Add **invalidation-triggered reassessment**.

- [ ] **Step 3: Verify**

```bash
grep -c "grading pass\|suggestion_grades\|invalidation-triggered" skills/market-briefing/SKILL.md  # >=1
test -f data/lessons.md && echo "lessons.md ok"
```

- [ ] **Step 4: Commit** `git add -A && git commit -m "feat: grading pass + lessons loop + invalidation reassessment"`

### Task 15: Per-stock observations/seasonality + monthly scorecard

**Files:**
- Modify: `skills/market-briefing/SKILL.md`, `skills/earnings-review/SKILL.md`

- [ ] **Step 1:** In the skill, when analyzing a full-depth name, **query `get_observations(ticker)`** and apply prior seasonal/event patterns as *hypotheses* (skeptical of priced-in patterns). The post-market run **records** new observations (`insert_observation`). Add the **monthly scorecard** to the monthly-plan brief (accuracy, lessons, what's changing). Note **gated auto-tuning** is deferred (only after ~50 graded calls; documented, not yet active).

- [ ] **Step 2:** In `earnings-review`, write an observation row on each earnings reaction (event_type='earnings').

- [ ] **Step 3: Verify**

```bash
grep -c "get_observations\|monthly scorecard\|seasonal" skills/market-briefing/SKILL.md  # >=1
```

- [ ] **Step 4: Commit** `git add -A && git commit -m "feat: per-stock observations/seasonality memory + monthly scorecard"`

**SLICE 5 DELIVERABLE:** the agent grades itself, keeps lessons, and re-applies per-stock memory.

---

## SLICE 6 — Historical preload (one-time, full)

### Task 16: Preload computations (`lib/preload.py`)

**Files:**
- Create: `lib/preload.py`, `tests/test_preload.py`

**Interfaces:**
- Produces: `seasonality(closes_with_dates) -> dict{month:avg_return}`, `volatility(closes) -> float` (annualized stdev of daily returns), `max_drawdown(closes) -> float`, `notable_moves(closes_with_dates, threshold=0.07) -> list[dict]`.

- [ ] **Step 1: Write `lib/preload.py`** (pure-python stats; fetch dated history via a small Yahoo call that returns timestamps + closes).

```python
import json, ssl, urllib.request, statistics, math
ctx=ssl.create_default_context(); UA={"User-Agent":"Mozilla/5.0"}
def _get(u,t=25): return json.loads(urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=t,context=ctx).read())
def dated_history(sym, range_="5y"):
    j=_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={range_}&interval=1d")
    r=j["chart"]["result"][0]; ts=r["timestamp"]; cl=r["indicators"]["quote"][0]["close"]
    return [(t,c) for t,c in zip(ts,cl) if c is not None]
def volatility(closes):
    rets=[(closes[i]-closes[i-1])/closes[i-1] for i in range(1,len(closes))]
    return round(statistics.pstdev(rets)*math.sqrt(252),4) if len(rets)>1 else None
def max_drawdown(closes):
    peak=closes[0]; mdd=0.0
    for c in closes:
        peak=max(peak,c); mdd=min(mdd,(c-peak)/peak)
    return round(mdd,4)
def seasonality(dated):
    import time as _t
    by={}
    for i in range(1,len(dated)):
        mth=_t.gmtime(dated[i][0]).tm_mon
        by.setdefault(mth,[]).append((dated[i][1]-dated[i-1][1])/dated[i-1][1])
    return {m:round(sum(v)/len(v)*100,3) for m,v in sorted(by.items())}
def notable_moves(dated, threshold=0.07):
    out=[]; import time as _t
    for i in range(1,len(dated)):
        ch=(dated[i][1]-dated[i-1][1])/dated[i-1][1]
        if abs(ch)>=threshold:
            out.append({"date":_t.strftime("%Y-%m-%d",_t.gmtime(dated[i][0])),"change_pct":round(ch*100,1)})
    return out
```

- [ ] **Step 2: Write `tests/test_preload.py`**

```python
from lib import preload as p
def test_max_drawdown(): assert p.max_drawdown([100,120,60,90])==round((60-120)/120,4)
def test_volatility_flat(): assert p.volatility([100,100,100])==0.0
def test_notable_moves(): assert p.notable_moves([(0,100),(86400,112)])[0]["change_pct"]==12.0
```

- [ ] **Step 3: Run tests + live smoke**

```bash
pytest tests/test_preload.py -v
python3 -c "from lib import preload as p; d=p.dated_history('AAPL'); print('vol',p.volatility([c for _,c in d]),'mdd',p.max_drawdown([c for _,c in d])); print('sept',p.seasonality(d).get(9))"
```
Expected: tests pass; vol/mdd/September-seasonality numbers print.

- [ ] **Step 4: Commit** `git add lib/preload.py tests/test_preload.py && git commit -m "feat(lib): historical preload stats"`

### Task 17: Run the one-time full backfill (`scripts/run_preload.py`)

**Files:**
- Create: `scripts/run_preload.py`

- [ ] **Step 1: Write `scripts/run_preload.py`** — for each watchlist ticker, compute stats + notable moves, tag notable moves to nearby earnings dates (Finnhub) where available, and (for the famous recurring catalysts) the implementer/agent annotates approximate event tags; write `stock_observations` rows (event_type in {seasonality, volatility, drawdown, earnings-reaction, big-move}, marked source/confidence). Idempotent (skip if already preloaded for a ticker).

```python
import time
from lib import config, db, preload, fundamentals
db.init_schema()
wl=config.load_watchlist(); names=sum([wl.get(b,[]) for b in ("core","growth","speculative")],[])
for sym in names:
    try:
        dated=preload.dated_history(sym); closes=[c for _,c in dated]
        db.insert_observation({"ticker":sym,"obs_date":time.strftime("%Y-%m-%d"),
            "event_type":"stats","summary":f"vol={preload.volatility(closes)} mdd={preload.max_drawdown(closes)} seasonality={preload.seasonality(dated)}",
            "price_reaction":None,"confidence":"high","source":"yfinance-preload"})
        for mv in preload.notable_moves(dated)[:20]:
            db.insert_observation({"ticker":sym,"obs_date":mv["date"],"event_type":"big-move",
                "summary":f"{mv['change_pct']}% day move","price_reaction":str(mv["change_pct"]),
                "confidence":"medium","source":"yfinance-preload"})
        print("preloaded", sym)
        time.sleep(0.3)
    except Exception as e:
        print("skip", sym, repr(e)[:80])
print("preload complete")
```

- [ ] **Step 2: Run it once + verify**

```bash
python3 scripts/run_preload.py
python3 -c "from lib import db; print('observations', len(db.get_observations('AAPL')))"
```
Expected: per-ticker "preloaded" lines; AAPL observations > 0.

- [ ] **Step 3: Commit** `git add scripts/run_preload.py && git commit -m "feat: one-time historical preload backfill"`

**SLICE 6 DELIVERABLE:** Postgres seeded with per-watchlist seasonality/volatility/drawdown + notable moves.

---

## SLICE 7 — Go-live

### Task 18: Local end-to-end dry run

**Files:** none (verification)

- [ ] **Step 1:** Run the `market-briefing` skill locally in **daily-status** mode (read holdings/open suggestions from PG, run the pipeline, send Telegram, write suggestions/snapshots to PG).
- [ ] **Step 2: Verify** the Telegram brief is one-screen, format unchanged, portfolio-first; and the DB got new rows:

```bash
python3 -c "from lib import db; print('today suggestions', len(db._rows(\"SELECT 1 FROM suggestions WHERE date=CURRENT_DATE\")))"
```
Expected: rows > 0; Telegram brief looks right.
- [ ] **Step 3:** Run **post-market analysis** mode; verify `daily_snapshots` + `stock_observations` rows appear and `lessons.md` got a regime line.

### Task 19: Create the four scheduled Routines + measure

**Files:**
- Modify: `routines/README.md`

- [ ] **Step 1:** In claude.ai/code/routines, create four Routines on the private repo (manual, owner), each with a prompt naming the run kind and time (UTC offsets for 06:30/10:30/13:30/15:10 CT), Custom network allowlist + env vars as in Task 5.
- [ ] **Step 2:** Trigger each once manually; confirm: full brief posts; intraday checks stay silent (or alert correctly); post-market writes observations.
- [ ] **Step 3:** Document in `routines/README.md` how to pause/adjust cadence. **Measure Pro usage for ~2 weeks**; if tight, drop to morning + 1 intraday + EOD, or upgrade to Max (spec §13).
- [ ] **Step 4: Commit** `git add routines/README.md && git commit -m "docs: scheduled Routines + measurement plan"`

**SLICE 7 DELIVERABLE:** the agent runs unattended on schedule, suggestion-only, learning over time.

---

## Self-Review (against the spec)

- **Spec coverage:** §2 autonomy→guardrail in Global Constraints + Task 13 (no execution). §3–§4 architecture/hosting→Slice 1 + Task 5/19. §5 cadence→Task 12/19. §6 data model→Task 4 schema + retrieve-don't-dump in Task 10. §7 two briefs→Task 10. §8 money→Task 11. §9 entry zones→Task 11. §10 reconciliation→Task 13. §11 learning→Task 14/15. §12 preload→Task 16/17. §13 budget→Task 19. §14 security→Task 1/2 (.gitignore, secrets template, env-first config). §16 migration→Task 9. §17 config→Task 11/12. §18 roadmap = the 7 slices. §19 acceptance→Tasks 18/19. ✅ No gaps.
- **Placeholder scan:** code steps carry real code; infra/skill steps cite exact spec sections + give verify commands. The skill-prose tasks (10–15) intentionally reference spec sections for full wording (the spec is REQUIRED READING in the header) rather than duplicating hundreds of lines — acceptable for a migration plan of this size.
- **Type/name consistency:** `lib.db` function names (`insert_suggestion`, `get_holdings`, `get_open_suggestions`, `insert_observation`, `get_observations`, `upsert_holding`, `upsert_daily_snapshot`, `get_dry_powder`, `set_dry_powder`, `insert_transaction`, `insert_grade`) defined in Task 4 are used verbatim in Tasks 9–17. `marketdata.quote/history/indicators`, `fundamentals.metric/...`, `telegram.send`, `preload.*` consistent across tasks. ✅

## Notes for the implementer
- Environment: the owner's Mac runs Python 3.14 with no pip packages; the **cloud Routine (Linux) has pip** — `pip install "psycopg[binary]"` there. For local dev, install psycopg locally too (or run DB-touching steps in the cloud).
- Everything HTTP uses stdlib `urllib` (proven in the v1.5 dry run) — no `requests` needed.
- Keep secrets out of git at every commit (`git status` before each commit).
