# Project 1 — "Rigorous Mode" Analysis Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing `market-briefing` skill (and the two on-demand research skills + `settings.json`) so the agent reasons through an explicit, logged Bull/Bear + risk-veto debate, suggests fewer/safer buys behind a Medium-conviction gate, scores stocks reproducibly, and runs broad coverage on yfinance-primary free data — while staying 100% suggestion-only and keeping the delivered Telegram brief format unchanged.

**Architecture:** This is a *prompt-and-config* upgrade, not a code change. The deliverables are edits to three Markdown `SKILL.md` instruction files and one JSON config. There is no compiler and no unit-test runner; each task's "test cycle" is (a) a content/parse verification (grep for new text present + old conflicting text gone; `python -m json.tool` for JSON validity) and (b) a final end-to-end **live, read-only dry run** that produces a real brief and checks the spec's acceptance criteria against the log. The debates, checklists, scoring math, and gate all run **behind the scenes**; only the *quality* behind each brief line changes.

**Tech Stack:** Claude Code `SKILL.md` Markdown prompts · JSON config (`config/settings.json`) · read-only market data (yfinance primary, Finnhub secondary, Alpha Vantage optional backup) · indicators computed locally in-session · Telegram HTML delivery.

## Global Constraints

These apply to **every** task. Copied verbatim from the approved spec (`docs/superpowers/specs/2026-06-17-project1-rigorous-analysis-upgrade-design.md`) and project memory:

- **Suggestion-only / read-only by construction.** The no-execution guardrail is UNTOUCHED. The agent has no trade tools and must never place/modify/cancel an order. `guardrails.execution_allowed` stays `false`.
- **Local-only, NO git.** Do NOT run `git add`/`git commit`/`git init` or any git command. The project is not a git repo by design. A task "checkpoint" = run the verification commands, confirm expected output, and note progress — nothing is committed.
- **Brief format is UNCHANGED.** Do not edit the "Briefing format" / "Delivery" sections of `market-briefing/SKILL.md` (the one-screen Telegram-HTML beginner layout: bold section headers + bold sub-labels + bold tickers, blank line between blocks, "Why it matters:" teaching clauses). Every behavioral change runs behind the scenes.
- **Beginner tone, no jargon** — or explain any unavoidable term in the same breath.
- **Budget:** ≤ ~70 data API calls per run, one agent session, NEVER loop the full ticker universe; use pre-computed screeners.
- **Data order:** yfinance primary · Finnhub secondary · Alpha Vantage optional backup. Compute RSI/MACD/moving averages locally from yfinance price history.
- **No buy suggested below Medium conviction.** Sub-threshold ideas are demoted to "watch."
- **Never invent numbers or news.** Mark scores/checklist items **partial** when an input is missing; always note which fallback/source was used.
- **Project root:** `/Users/rajrupesh/Documents/Raj/stocks-agent`. All paths below are relative to it unless absolute.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `config/settings.json` | Reorder `data` to yfinance-primary + add local-indicator flag; add a new `rigor` block (debate on/off, depth tiers, confidence gate). Source of the field names every skill references. | 1 |
| `skills/market-briefing/SKILL.md` | The main upgrade: data-source + budget sections, the structured deliberation method + two depths, the full-depth checklists, the confidence/risk gate, Health-Score consistency, enriched logging. | 2–7 |
| `skills/equity-research/SKILL.md` | Adopt the Deep Dive + peer relative-valuation checklists for the on-demand note. | 8 |
| `skills/earnings-review/SKILL.md` | Adopt the Bear-Case red-flag checklist elements where transcript/results data allows. | 9 |
| — | End-to-end live dry-run verification against all acceptance criteria. | 10 |

**Ordering note:** Task 1 (config) is first because it defines the exact field names (`rigor.*`, `data.*`) that Tasks 2–9 reference. Tasks 2–7 all edit the *same* file (`market-briefing/SKILL.md`) but different, clearly-headed sections; execute them in order so each task's anchor text exists when the next one runs. Tasks 8 and 9 are independent of each other.

---

### Task 1: Config — yfinance-primary `data` swap + new `rigor` block

**Files:**
- Modify: `config/settings.json` — the `data` block (currently lines 48–53) and insert a new `rigor` block after it.

**Interfaces:**
- Consumes: nothing (foundation task).
- Produces: the field names referenced by later tasks —
  - `data.primary` = `"yfinance"`, `data.secondary` = `"finnhub"`, `data.fallback` = `"alphavantage"`, `data.compute_indicators_locally` = `true`.
  - `rigor.enabled`, `rigor.structured_debate`, `rigor.depth.full` (array), `rigor.depth.compact` (array), `rigor.confidence_gate.min_conviction_to_suggest_buy` = `"medium"`, `rigor.confidence_gate.below_threshold_action` = `"watch"`, `rigor.risk_gate_can_veto` = `true`, `rigor.full_depth_checklists` (array).

- [ ] **Step 1: Replace the `data` block** (currently lines 48–53)

Old:
```json
  "data": {
    "primary": "finnhub",
    "secondary": "alphavantage",
    "fallback": "yfinance",
    "price_delay_minutes": 15
  },
```

New:
```json
  "data": {
    "primary": "yfinance",
    "secondary": "finnhub",
    "fallback": "alphavantage",
    "compute_indicators_locally": true,
    "indicators_from": "yfinance_price_history",
    "price_delay_minutes": 15,
    "_note": "DATA SWAP (2026-06-17 Rigorous Mode): yfinance is now PRIMARY (quotes, full price history, fundamentals, predefined screeners — no hard daily cap). Technical indicators (RSI-14, MACD 12/26/9, SMA/EMA 50 & 200) are COMPUTED LOCALLY in-session from yfinance price history, removing the old Alpha Vantage indicator dependency. Finnhub is SECONDARY (stock/metric fundamentals, company news+sentiment, earnings dates, insider Form 4; 60/min free). Alpha Vantage is an OPTIONAL last-resort BACKUP only (its 25/day cap was the binding limit, so it is demoted). Works in a cloud schedule (no local server)."
  },
```

- [ ] **Step 2: Insert the new `rigor` block** immediately after the `data` block (before `"market_scan"`)

```json
  "rigor": {
    "enabled": true,
    "structured_debate": true,
    "depth": {
      "full": ["money_moves", "holdings"],
      "compact": ["watchlist", "scan_shortlist"],
      "_note": "FULL multi-round debate runs on money-moves (the month's growth pick + 2-3 runner-ups), any buy/trim/sell, any watchlist promote/retire, and EVERY name the owner holds (portfolio.json). COMPACT one-round structured pass (1 bull / 1 bear / 1 risk flag, reusing data already pulled, no extra API calls) runs on every other watchlist + scan-shortlist name. Nothing is skipped; depth concentrates where real money is at stake."
    },
    "confidence_gate": {
      "min_conviction_to_suggest_buy": "medium",
      "below_threshold_action": "watch",
      "_note": "A BUY is suggested only at Medium-or-higher conviction AFTER the debate. Low conviction is demoted to 'watch', never suggested as a buy."
    },
    "risk_gate_can_veto": true,
    "full_depth_checklists": ["deep_dive", "peer_relative_valuation", "bear_case"],
    "_note": "RIGOROUS MODE (spec 2026-06-17): formalizes the TradingAgents-style deliberation already used loosely. Each analyzed name gets a structured, logged Bull-vs-Bear + risk-veto pass; the risk gate can override the debate (veto/downgrade). Full-depth names additionally run three reel-sourced checklists (Deep Dive / Peer relative-valuation / Bear Case). Suggestion-only and brief format are UNCHANGED — all of this runs behind the scenes and is enriched into data/suggestions-log.jsonl."
  },
```

- [ ] **Step 3: Verify the JSON still parses and the new keys exist**

Run:
```bash
python3 -m json.tool /Users/rajrupesh/Documents/Raj/stocks-agent/config/settings.json > /dev/null && echo "JSON OK"
python3 -c "import json; d=json.load(open('/Users/rajrupesh/Documents/Raj/stocks-agent/config/settings.json')); print(d['data']['primary'], d['data']['compute_indicators_locally'], d['rigor']['confidence_gate']['min_conviction_to_suggest_buy'], d['rigor']['risk_gate_can_veto'])"
```
Expected: `JSON OK` then `yfinance True medium True`

- [ ] **Step 4: Checkpoint** (NO git — local only)

Confirm Step 3 printed the expected output. Note Task 1 complete; proceed to Task 2.

---

### Task 2: market-briefing — yfinance-primary data, local indicators, updated budget

**Files:**
- Modify: `skills/market-briefing/SKILL.md` — the "Data sources (read-only)" section (currently lines 47–61), the "News" intro reference to Alpha Vantage (lines 64–65), and the "API + quota budget per run" section (currently lines 123–133).

**Interfaces:**
- Consumes: `data.primary/secondary/fallback/compute_indicators_locally` from Task 1.
- Produces: the data-source vocabulary ("computed locally", "yfinance-primary") that the deliberation (Task 3) and Health Score (Task 6) sections rely on.

- [ ] **Step 1: Replace the "Data sources (read-only)" section** (lines 47–61, from the `## Data sources (read-only)` header through the `Alpha Vantage `NEWS_SENTIMENT`/`TOP_GAINERS_LOSERS`...note in the brief which method/source you used if a primary was unavailable.` line)

New:
```markdown
## Data sources (read-only) — yfinance primary
- **Primary: yfinance** — quotes, full **price history**, fundamentals, and **predefined market
  screeners** (day_gainers, day_losers, most_actives, undervalued_large_caps, etc.) that pre-scan the
  entire US market for free. **No hard daily cap** — this is now the workhorse for breadth.
- **Technical indicators are COMPUTED LOCALLY** (per `settings.json.data.compute_indicators_locally`)
  from the yfinance price history in THIS session — not fetched from a vendor. Use standard
  definitions: **RSI-14, MACD 12/26/9, SMA/EMA 50 & 200.** This removes the old dependence on Alpha
  Vantage's per-call indicator endpoints (its 25/day cap was the bottleneck). If the price history is
  too short for an indicator, mark it **partial** and say so — never fabricate a value.
- **Secondary: Finnhub** — fundamentals (`stock/metric?metric=all`), company news + sentiment,
  earnings calendar/dates, and **insider (Form 4) transactions**. Free tier is 60 req/min — pace within it.
- **Optional backup: Alpha Vantage** — use ONLY if yfinance AND Finnhub both fail for a needed field.
  Its `TOP_GAINERS_LOSERS` is a fine 1-call movers backup. Demoted because of the 25/day cap.
Prices may be delayed ~15 min — fine for long-term/swing, never present them as live.

**Access method + fallback:** prefer read-only MCP tools / the `yfinance` library when available in
this run. If they aren't (e.g. a restricted scheduled cloud run, or the tools aren't exposed), **fall
back to direct read-only HTTPS calls** to the same endpoints using the keys in
`config/secrets.local.json` (e.g. `https://finnhub.io/api/v1/quote?symbol=…&token=…`, Alpha Vantage
`NEWS_SENTIMENT`/`TOP_GAINERS_LOSERS`). These are GET/read-only — never any write/order endpoint.
Note in the brief which method/source you used if a primary was unavailable.
```

- [ ] **Step 2: Update the "News" section's source reference** (line 64–65)

Old:
```markdown
1. **General market news:** pull the latest top market headlines (Finnhub market-news endpoint /
   Alpha Vantage news-sentiment) to drive the "what's driving the market" read.
```

New:
```markdown
1. **General market news:** pull the latest top market headlines (Finnhub market-news endpoint;
   Alpha Vantage news-sentiment only as a backup) to drive the "what's driving the market" read.
```

- [ ] **Step 3: Replace the "API + quota budget per run" section** (lines 123–133, from the `## API + quota budget per run` header through `...rather than hammering the API.`)

New:
```markdown
## API + quota budget per run (HARD RULES — do not exceed)
Read these as constraints, not suggestions:
- **NEVER loop over the full ticker universe.** Use the pre-computed screener endpoints only. If you
  ever find yourself about to request data for hundreds of symbols, STOP — you're doing it wrong.
- **Target ≤ ~70 data API calls per run total:** ~5–10 for the broad scan + the watchlist (~46
  names) + the shortlist (~10). **yfinance (primary) has no hard daily cap**, so breadth is cheap;
  it carries quotes, price history (for the locally-computed indicators), fundamentals, and screeners.
  **Pace Finnhub within its 60/min free limit** for fundamentals/news/earnings/insider. **Alpha
  Vantage is last-resort backup only** — do not spend its 25/day unless yfinance + Finnhub both fail.
- **One run = one agent session.** The whole brief is a single pass; do not spawn per-ticker
  sub-runs. Reading a 20-row screener vs a 5-row screener costs the same to your Pro quota.
- If rate-limited, **prioritize:** (1) market snapshot, (2) watchlist movers/news, (3) scan
  shortlist — and note in the brief that some data was skipped, rather than hammering the API.
```

- [ ] **Step 4: Verify new text present and old AlphaVantage-primary language gone**

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
grep -c "Primary: yfinance" skills/market-briefing/SKILL.md
grep -c "COMPUTED LOCALLY" skills/market-briefing/SKILL.md
grep -c "yfinance (primary) has no hard daily cap" skills/market-briefing/SKILL.md
grep -c "Primary: Finnhub" skills/market-briefing/SKILL.md
```
Expected: `1`, `1`, `1`, then `0` (the old "Primary: Finnhub" line is replaced).

- [ ] **Step 5: Checkpoint** (NO git) — confirm Step 4 output, note Task 2 complete.

---

### Task 3: market-briefing — structured deliberation method + two depths

**Files:**
- Modify: `skills/market-briefing/SKILL.md` — replace the "Reason through a multi-role lens" section (currently lines 135–142).

**Interfaces:**
- Consumes: `rigor.structured_debate`, `rigor.depth.full`, `rigor.depth.compact` (Task 1); `risk.max_position_pct_of_bucket`, `risk.daily_loss_limit_pct`, `risk.circuit_breaker_consecutive_losses` (existing).
- Produces: the deliberation step names referenced by the confidence gate (Task 5), the checklists (Task 4), and the log fields (Task 7): the four steps (specialist passes · Bull-vs-Bear + decisive factor · risk gate veto · verdict + conviction + "what would prove me wrong"); the terms **full depth** and **compact pass**.

- [ ] **Step 1: Replace the "Reason through a multi-role lens" section** (lines 135–142, from `## Reason through a multi-role lens` through `...become suggestions.`)

New:
```markdown
## Structured deliberation method — run BEFORE writing any suggestion (settings.json `rigor`)
Formalized from the TradingAgents multi-role method. For each analyzed name, run a structured,
**internal + logged** deliberation. This replaces the old quick mental "bull vs bear" with an explicit,
recorded one. It runs **behind the scenes** — the brief format does not change.

Four steps per name:
1. **Specialist passes** — quick explicit reads of: **fundamentals** · **technicals** (the
   locally-computed RSI/MACD/moving averages) · **news/sentiment** (+ insider activity).
2. **Bull vs Bear** — state the strongest point on each side, then name the single **decisive factor**
   that breaks the tie.
3. **Risk gate (can VETO — `rigor.risk_gate_can_veto`)** — check position size vs
   `risk.max_position_pct_of_bucket`, a mandatory stop-loss, daily-loss-limit + circuit-breaker
   status (`risk.daily_loss_limit_pct`, `risk.circuit_breaker_consecutive_losses`), and concentration
   vs the 70/20/10 target. **A weak idea dies here** — the gate can veto or downgrade the debate's
   outcome entirely.
4. **Verdict + conviction (Low / Medium / High) + "what would prove me wrong"** (the invalidation
   level / stop). Only ideas that survive all four steps — and clear the confidence gate below — can
   become buy suggestions.

### Two depths (every name scrutinized, stays in budget) — `rigor.depth`
- **Full multi-round** debate → **money-moves** (the month's growth pick + 2–3 runner-ups), any
  **buy/trim/sell**, any watchlist **promote/retire**, and **every name the owner holds**
  (`portfolio.json`). Full-depth names ALSO run the three checklists in the next section.
- **Compact one-round** structured pass (**1 bull · 1 bear · 1 risk flag**) → **every other**
  watchlist + scan-shortlist name, **reusing data already pulled — no extra API calls.**
- Result: nothing is skipped; depth concentrates where real money is at stake.
```

- [ ] **Step 2: Verify the new section is present and the old header is gone**

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
grep -c "## Structured deliberation method" skills/market-briefing/SKILL.md
grep -c "decisive factor" skills/market-briefing/SKILL.md
grep -c "Compact one-round" skills/market-briefing/SKILL.md
grep -c "## Reason through a multi-role lens" skills/market-briefing/SKILL.md
```
Expected: `1`, `1`, `1`, then `0`.

- [ ] **Step 3: Confirm no orphaned reference to the old section name** elsewhere in the file

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
grep -n "multi-role lens" skills/market-briefing/SKILL.md
```
Expected: remaining mentions (e.g. in the Health Score section and "How you decide" section, lines ~196, ~204, ~225) describe scoring as context vs the lens. Update each remaining `multi-role lens` mention to `structured deliberation` so the file is internally consistent. Re-run; expected final count `0`.

```bash
grep -c "multi-role lens" skills/market-briefing/SKILL.md
```
Expected after edits: `0`.

- [ ] **Step 4: Checkpoint** (NO git) — confirm Steps 2–3 output, note Task 3 complete.

---

### Task 4: market-briefing — full-depth analysis checklists (Deep Dive / Peer table / Bear Case)

**Files:**
- Modify: `skills/market-briefing/SKILL.md` — insert a new section immediately after the "Two depths" subsection added in Task 3 (i.e. after the "Structured deliberation method" section, before "## The owner's strategy" at old line 144).

**Interfaces:**
- Consumes: "full depth" definition (Task 3); `rigor.full_depth_checklists` (Task 1).
- Produces: the three named checklists — **Deep Dive** (business/moat/catalyst/asymmetry), **Peer relative-valuation** (peer table + `value/growth ratio = P/S TTM ÷ revenue growth %`), **Bear Case** (ranked red flags + invalidation level) — reused by `equity-research` (Task 8), `earnings-review` (Task 9), Health-Score valuation grounding (Task 6), and the log (Task 7).

- [ ] **Step 1: Insert the new "Full-depth analysis checklists" section** after the "Two depths" subsection

```markdown
## Full-depth analysis checklists (run on FULL-depth names only — `rigor.full_depth_checklists`)
These concrete checklists run on **full-depth** names (money-moves + holdings) and make the
deliberation method above concrete. They also power the on-demand `equity-research` /
`earnings-review` skills. They do NOT change the architecture, the budget approach, or the
guardrail, and they run **behind the scenes** (results are logged; the brief format is unchanged).
Honesty note: the source reel's performance claims are survivorship-bias marketing — these are kept
purely as analysis *structure*, and they suit the Growth pick + holdings mindset, NOT the Core 70% DCA.

1. **Deep Dive** (feeds the specialist + bull passes):
   - **Business model** — how they make money / core product, in plain beginner English.
   - **Moat** — top ~3 competitors; is the edge durable? (patent · switching cost · network effect ·
     cost structure).
   - **Catalyst** — concrete launches / earnings / regulatory events / partnerships in the next 12 months.
   - **Asymmetry** — valuation floor vs growth ceiling: is the risk/reward skewed up, and why / why not?
2. **Peer relative-valuation** (feeds the valuation sub-score) — pick ~2 sensible same-sector peers
   (say which), and build a small table: **P/S (TTM + forward), P/FCF, EV/EBITDA, gross margin, YoY
   revenue growth**, plus a transparent **value/growth ratio = P/S TTM ÷ revenue growth %** (lower =
   more growth per dollar of valuation). Affordable because it is full-depth-only (the name + ~2
   peers). Data from **yfinance** (Finnhub backup); **mark partial / note gaps** — never invent.
3. **Bear Case** (supercharges the bear pass + risk gate) — adopt a skeptical short-seller stance and
   surface the **3 most serious red flags, ranked by severity, with sources**, checking: customer
   concentration (>25% of revenue, from the latest 10-K), margin compression, unusual insider selling,
   a widening GAAP-vs-non-GAAP gap, and guidance cuts in the last 12 months. Produce an explicit
   **invalidation level** (the price/condition where the thesis breaks) → this IS the stop-loss /
   "what would prove me wrong." Filings-derived items (10-K concentration, GAAP gap) are **best-effort
   on free data**: cite the source, and **note honestly when an item can't be verified.**
```

- [ ] **Step 2: Verify the section and its key terms are present**

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
grep -c "## Full-depth analysis checklists" skills/market-briefing/SKILL.md
grep -c "value/growth ratio = P/S TTM ÷ revenue growth %" skills/market-briefing/SKILL.md
grep -c "invalidation level" skills/market-briefing/SKILL.md
grep -c "survivorship-bias marketing" skills/market-briefing/SKILL.md
```
Expected: `1`, `1`, `1`, `1`.

- [ ] **Step 3: Confirm placement** — the checklists section sits between "Structured deliberation method" and "The owner's strategy"

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
grep -n "^## " skills/market-briefing/SKILL.md | grep -E "Structured deliberation|Full-depth analysis|owner.s strategy"
```
Expected: the three headers appear in that order (deliberation → full-depth checklists → owner's strategy).

- [ ] **Step 4: Checkpoint** (NO git) — confirm Steps 2–3, note Task 4 complete.

---

### Task 5: market-briefing — confidence / risk gate

**Files:**
- Modify: `skills/market-briefing/SKILL.md` — insert a "Confidence / risk gate" section immediately after the "Full-depth analysis checklists" section (Task 4), and add one cross-reference line to the Growth money-move so the gate's effect on the brief is explicit (without changing the *format*).

**Interfaces:**
- Consumes: the four deliberation steps + conviction levels (Task 3); `rigor.confidence_gate.min_conviction_to_suggest_buy` = `"medium"`, `rigor.confidence_gate.below_threshold_action` = `"watch"`, `rigor.risk_gate_can_veto` (Task 1).
- Produces: the rule the log (Task 7) records (`risk_verdict`, gated/demoted) and the dry-run checks (Task 10).

- [ ] **Step 1: Insert the "Confidence / risk gate" section** after the full-depth checklists section

```markdown
## Confidence / risk gate (the "rigid" dial — `rigor.confidence_gate`)
After the deliberation, apply the gate before anything is suggested as a buy:
- A **buy is suggested only at Medium-or-higher conviction** (`min_conviction_to_suggest_buy`).
- **Low conviction → demoted to "watch"** (`below_threshold_action`), never suggested as a buy.
- The **risk gate (deliberation step 3) can override the debate entirely** — veto or downgrade — even
  a High-conviction idea (`rigor.risk_gate_can_veto`).
- Net effect by design: **fewer suggestions, a higher bar, less noise.**
- **If nothing clears the bar this month** (e.g. no Growth idea reaches Medium): say so honestly in
  the existing brief blocks — the Growth money-move line states no idea cleared the Medium bar and the
  candidates appear under "What I'd watch" instead. This uses the existing layout; the **format does
  not change**, only the honesty of the call does. (Core DCA still proceeds — it is autopilot, not a
  conviction call.)
```

- [ ] **Step 2: Add a one-line gate reference to the Growth money-move** (in the "Briefing format" block, the `✅ **Growth — add $100...` bullet, currently ~line 283). Append to the end of that bullet's text — this is a clarifying clause, NOT a format change.

Old (end of the Growth bullet):
```markdown
  splitting the $100): list 2–3, **each with its ticker bolded `<b>TICKER</b>` + score/100 + one
  phrase** (same visual weight as the main pick).
```

New:
```markdown
  splitting the $100): list 2–3, **each with its ticker bolded `<b>TICKER</b>` + score/100 + one
  phrase** (same visual weight as the main pick). If NO growth idea cleared the Medium-conviction gate
  this month, say so plainly here and move the best candidates to "What I'd watch" (no forced buy).
```

- [ ] **Step 3: Verify the gate section and the brief clause are present**

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
grep -c "## Confidence / risk gate" skills/market-briefing/SKILL.md
grep -c "Medium-or-higher conviction" skills/market-briefing/SKILL.md
grep -c "no forced buy" skills/market-briefing/SKILL.md
```
Expected: `1`, `1`, `1`.

- [ ] **Step 4: Checkpoint** (NO git) — confirm Step 3, note Task 5 complete.

---

### Task 6: market-briefing — consistent Health Score (fixed definitions + peer-grounded valuation)

**Files:**
- Modify: `skills/market-briefing/SKILL.md` — the "Stock Health Score (0–100)" section (currently lines 200–225). Add a fixed-input-definitions + fixed-fallback-order paragraph and ground the valuation sub-score in the peer table; keep the owner's existing scoring rules intact.

**Interfaces:**
- Consumes: Peer relative-valuation checklist (Task 4); `scoring.weights`, `scoring.risk_bands` (existing); `data.*` order (Task 1).
- Produces: reproducible sub-score definitions used by the log (Task 7) and `equity-research` (Task 8, which already says "compute exactly as in market-briefing").

- [ ] **Step 1: Insert a "fixed definitions + fallback order" paragraph** at the END of the Health Score section, immediately after the existing rules line that ends `...still comes from the multi-role lens.` (note: Task 3 Step 3 already changed "multi-role lens" → "structured deliberation" here; match the current text)

Insert after that final rules sentence:
```markdown

**Consistency (Rigorous Mode — makes the score reproducible run-to-run):** use FIXED input definitions
and a FIXED fallback order for every sub-score so the same stock scores the same way each run:
- **Growth (0–35):** YoY **revenue** growth (and earnings growth if available). Source order:
  yfinance → Finnhub `stock/metric` → Alpha Vantage (backup). Use TTM where available; else most
  recent reported year.
- **Financial health (0–35):** net cash vs debt + profitability, minus qualitative risk flags. Source
  order: yfinance balance sheet / margins → Finnhub `stock/metric` → Alpha Vantage (backup).
- **Valuation (0–30):** P/E vs the stock's own history/peers, **PEG-style growth-adjusted**, P/S when
  there are no earnings. **Ground it in the Peer relative-valuation table** (Full-depth checklist #2):
  the peer P/S, EV/EBITDA, and the **value/growth ratio** inform whether the name is cheap/fair/rich
  for its growth. Source order: yfinance → Finnhub → Alpha Vantage (backup).
Keep this as ONE headline Health Score (no competing scores). If an input is missing after the fallback
order, compute from what you have, mark the score **partial**, and record which inputs you used. Never
invent the underlying numbers.
```

- [ ] **Step 2: Verify the consistency paragraph is present and the score stays singular**

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
grep -c "FIXED input definitions" skills/market-briefing/SKILL.md
grep -c "Ground it in the Peer relative-valuation table" skills/market-briefing/SKILL.md
grep -c "ONE headline Health Score" skills/market-briefing/SKILL.md
```
Expected: `1`, `1`, `1`.

- [ ] **Step 3: Checkpoint** (NO git) — confirm Step 2, note Task 6 complete.

---

### Task 7: market-briefing — enriched logging (debate fields)

**Files:**
- Modify: `skills/market-briefing/SKILL.md` — the "Logging" section (currently lines 325–332): extend the JSONL schema with the debate's key fields.

**Interfaces:**
- Consumes: deliberation outputs (Task 3: bull / bear / decisive_factor / risk_verdict / conviction / invalidation), depth (Task 3), Bear-Case invalidation level (Task 4).
- Produces: the enriched `data/suggestions-log.jsonl` schema the track-record self-review + `data/learning.md` compound from, and that the dry run (Task 10) verifies.

- [ ] **Step 1: Replace the logging schema paragraph + JSON example** (lines 325–331, from `## Logging` header through the `(Omit the `score_*` fields...)` line)

New:
```markdown
## Logging (do this every run, before sending)
For each action line you produced, append one line to `data/suggestions-log.jsonl` with the full
internal fields (even though the message showed only the simple line). Rigorous Mode adds the debate
fields — `depth`, `bull`, `bear`, `decisive_factor`, `risk_verdict`, `invalidation_level` — alongside
the existing confidence/score fields, so the track-record self-review and `data/learning.md` compound
from richer history:
```json
{"date":"YYYY-MM-DD","ticker":"XXX","action":"Buy","bucket":"growth","price_at_suggestion":123.45,"stop":110.00,"target":150.00,"confidence":"Medium","depth":"full","bull":"AI demand + margin expansion","bear":"customer concentration; rich multiple","decisive_factor":"backlog visibility beats valuation worry","risk_verdict":"pass","invalidation_level":"close below 110 / loss of top customer","reason":"…","score":76,"score_growth":30,"score_health":28,"score_valuation":18,"risk_band":"low-med","score_inputs":"pe,revGrowth,netCash; concentration flag from news","score_partial":false}
```
Field rules: `depth` is `"full"` or `"compact"`; `risk_verdict` is `"pass"`, `"veto"`, or `"downgrade"`
(record veto/downgrade even when no buy was suggested, so the gate is auditable); `invalidation_level`
mirrors the Bear-Case invalidation / stop. Omit the `score_*` fields, or set `score`:null, for broad
ETFs and when the score couldn't be computed.
```

- [ ] **Step 2: Verify the new fields are in the schema**

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
grep -c '"decisive_factor"' skills/market-briefing/SKILL.md
grep -c '"risk_verdict"' skills/market-briefing/SKILL.md
grep -c '"invalidation_level"' skills/market-briefing/SKILL.md
grep -c '"depth"' skills/market-briefing/SKILL.md
```
Expected: `1`, `1`, `1`, `1`.

- [ ] **Step 3: Confirm the example line is still valid JSON** (extract and parse it)

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
python3 -c "import json,re; t=open('skills/market-briefing/SKILL.md').read(); l=[x for x in t.splitlines() if x.strip().startswith('{\"date\"')][0]; json.loads(l); print('log example JSON OK')"
```
Expected: `log example JSON OK`

- [ ] **Step 4: Checkpoint** (NO git) — confirm Steps 2–3, note Task 7 complete.

---

### Task 8: equity-research — adopt Deep Dive + peer relative-valuation

**Files:**
- Modify: `skills/equity-research/SKILL.md` — the "Data" line (line 16) to yfinance-primary; the "Produce" section (lines 22–31) to add the Deep Dive + peer relative-valuation checklists.

**Interfaces:**
- Consumes: Deep Dive + Peer relative-valuation checklists (Task 4); Health Score "compute exactly as in market-briefing" (already referenced, now consistent per Task 6).
- Produces: nothing downstream (leaf skill).

- [ ] **Step 1: Update the Data line** (line 16) to match the data swap

Old:
```markdown
## Data (read-only): Finnhub primary, Alpha Vantage secondary, yfinance fallback
```
New:
```markdown
## Data (read-only): yfinance primary, Finnhub secondary, Alpha Vantage backup
```

- [ ] **Step 2: Insert the Deep Dive + Peer table bullets** into the "Produce" block, after the `- **Bull case**` / `- **Bear case**` lines and before `- **Does the thesis still hold?**` (currently between lines 27 and 28)

Insert:
```markdown
- **Deep Dive** — in plain beginner English: **Business model** (how they make money), **Moat** (top
  ~3 competitors + is the edge durable: patent / switching cost / network effect / cost structure),
  **Catalyst** (concrete events in the next 12 months), **Asymmetry** (valuation floor vs growth
  ceiling — is risk/reward skewed up, and why/why not?).
- **Peer relative-valuation** — pick ~2 sensible same-sector peers (say which) and show a small table:
  P/S (TTM + forward), P/FCF, EV/EBITDA, gross margin, YoY revenue growth, plus the **value/growth
  ratio = P/S TTM ÷ revenue growth %** (lower = more growth per dollar). Data from yfinance (Finnhub
  backup); mark partial / note any gaps — never invent.
```

- [ ] **Step 3: Verify**

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
grep -c "yfinance primary, Finnhub secondary" skills/equity-research/SKILL.md
grep -c "Deep Dive" skills/equity-research/SKILL.md
grep -c "value/growth ratio = P/S TTM ÷ revenue growth %" skills/equity-research/SKILL.md
```
Expected: `1`, `1`, `1`.

- [ ] **Step 4: Checkpoint** (NO git) — confirm Step 3, note Task 8 complete.

---

### Task 9: earnings-review — adopt Bear-Case red-flag elements

**Files:**
- Modify: `skills/earnings-review/SKILL.md` — the "Data" line (line 18) to yfinance-primary; the "Produce" section (lines 22–30) to add the Bear-Case red-flag checks where results/transcript data allows.

**Interfaces:**
- Consumes: Bear-Case checklist (Task 4) — specifically guidance cuts, margin compression, GAAP-vs-non-GAAP gap, plus invalidation level.
- Produces: nothing downstream (leaf skill).

- [ ] **Step 1: Update the Data line** (line 18)

Old:
```markdown
## Data (read-only): Finnhub primary, Alpha Vantage secondary, yfinance fallback
```
New:
```markdown
## Data (read-only): yfinance primary, Finnhub secondary, Alpha Vantage backup
```

- [ ] **Step 2: Insert a "Bear-case check" bullet** into the "Produce" block, after the `- **What it means for the thesis**` line and before `- **(If a transcript was pasted)**` (currently between lines 27 and 28)

Insert:
```markdown
- **Bear-case check** (best-effort on free data; cite source, note when unverifiable) — flag any of:
  **guidance cut** vs prior, **margin compression** quarter-over-quarter, a widening
  **GAAP-vs-non-GAAP gap**, and (if a transcript was pasted) unusual hedging/tone. End with the
  **invalidation level** — the price/condition that would break the thesis (= the stop / "what would
  prove me wrong").
```

- [ ] **Step 3: Verify**

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
grep -c "yfinance primary, Finnhub secondary" skills/earnings-review/SKILL.md
grep -c "Bear-case check" skills/earnings-review/SKILL.md
grep -c "invalidation level" skills/earnings-review/SKILL.md
```
Expected: `1`, `1`, `1`.

- [ ] **Step 4: Checkpoint** (NO git) — confirm Step 3, note Task 9 complete.

---

### Task 10: End-to-end live dry-run verification (acceptance criteria)

**Files:**
- No edits. This task runs the upgraded `market-briefing` skill once, read-only, and checks the spec's acceptance criteria. (Mirrors how original Tasks 1–9 were verified — live data → brief → Telegram → log.)

**Interfaces:**
- Consumes: everything from Tasks 1–9.
- Produces: confirmation the upgrade meets acceptance criteria + a fresh enriched log line to inspect.

- [ ] **Step 1: Pre-flight — confirm all four files are internally consistent**

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
python3 -m json.tool config/settings.json > /dev/null && echo "settings.json OK"
grep -c "multi-role lens" skills/market-briefing/SKILL.md   # expect 0
grep -c "Structured deliberation method" skills/market-briefing/SKILL.md  # expect 1
for f in skills/market-briefing/SKILL.md skills/equity-research/SKILL.md skills/earnings-review/SKILL.md; do grep -c "yfinance primary\|Primary: yfinance" "$f"; done  # expect 1,1,1
```
Expected: `settings.json OK`, `0`, `1`, then `1`/`1`/`1`.

- [ ] **Step 2: Run the upgraded brief once (read-only, suggestion-only)**

Invoke the `market-briefing` skill in pre-market/on-demand mode (the normal way the owner runs it). It must pull live yfinance-primary data, compute indicators locally, run the structured debate at the two depths, apply the confidence gate, score consistently, deliver the Telegram brief, and write `data/suggestions-log.jsonl` + `data/briefings/YYYY-MM-DD.md`.

- [ ] **Step 3: Verify the delivered brief is unchanged in format** — one screen, Telegram-HTML, bold headers/sub-labels/tickers, "Why it matters" clauses, footer. (Visual check on Telegram + the saved `data/briefings/<today>.md`.)

Expected: format identical to prior briefs; only the reasoning quality differs.

- [ ] **Step 4: Verify the log now carries the debate fields and the gate is auditable**

Run:
```bash
cd /Users/rajrupesh/Documents/Raj/stocks-agent
tail -n 5 data/suggestions-log.jsonl | python3 -c "import sys,json; [print({k:r.get(k) for k in ('ticker','depth','confidence','risk_verdict','decisive_factor','invalidation_level')}) for r in (json.loads(l) for l in sys.stdin if l.strip())]"
```
Expected: recent entries show `depth` (`full`/`compact`), `confidence`, `risk_verdict` (`pass`/`veto`/`downgrade`), and non-empty `decisive_factor` / `invalidation_level`.

- [ ] **Step 5: Check the spec's acceptance criteria against this run**

Confirm each:
- [ ] Every analyzed name got a structured bull/bear/risk pass (full for money-moves + holdings, compact for the rest) — visible in the log `depth` field across names.
- [ ] No buy was suggested below Medium conviction; any sub-threshold idea appears as "watch."
- [ ] The risk gate can/did veto or downgrade at least where applicable — `risk_verdict` shows it (and at minimum the rule is present and a veto would be recorded).
- [ ] The run completed within budget (≤ ~70 data calls, one session) on yfinance-primary data with locally-computed indicators; Alpha Vantage was not required.
- [ ] Health Score sub-scores used fixed definitions/fallback order; ETFs tagged "ETF — diversified"; partials marked.
- [ ] On a full-depth name, the log/working notes show the three checklists: Deep Dive (business/moat/catalyst/asymmetry), a peer relative-valuation table + value/growth ratio, and a ranked Bear-Case red-flag list with sources + an explicit invalidation level. Best-effort items note gaps.
- [ ] Guardrail intact: no execution tools were available or used; agent stayed suggestion-only.

- [ ] **Step 6: Update learning + memory; report results.** Confirm `data/learning.md` got its dated regime line. Report the dry-run outcome to the owner (what worked, any data gaps, the example enriched log line). NO git.

---

## Self-Review (performed against the spec)

- **Spec coverage:** §3.1 structured deliberation → Task 3. §3.2 two depths → Task 1 (`rigor.depth`) + Task 3. §3.2a checklists → Task 4 (+ Tasks 8/9 for the on-demand skills). §3.3 confidence/risk gate → Task 1 + Task 5. §3.4 data swap → Task 1 + Task 2 (+ Tasks 8/9 data lines). §3.5 consistent Health Score → Task 6. §3.6 format unchanged + enriched logging → Task 7 (format explicitly NOT touched, enforced in Global Constraints + Task 10 Step 3). §4 config field names → finalized in Task 1. §5 files touched → all four covered. §6 acceptance criteria → Task 10 Step 5. §7 out-of-scope (OpenBB/FMP/TimesFM/AI-Trader/execution) → not implemented; no task adds them. ✅ No gaps.
- **Placeholder scan:** every edit step shows the exact old→new Markdown/JSON; verification steps give exact commands + expected output. No "TBD"/"add error handling"/"similar to Task N." ✅
- **Type/name consistency:** config keys defined in Task 1 (`rigor.confidence_gate.min_conviction_to_suggest_buy`, `rigor.depth.full/compact`, `rigor.risk_gate_can_veto`, `data.compute_indicators_locally`, `data.primary=yfinance`) are referenced verbatim in Tasks 2–9. Log fields defined in Task 7 (`depth`, `bull`, `bear`, `decisive_factor`, `risk_verdict`, `invalidation_level`) match the deliberation outputs named in Task 3 and the dry-run checks in Task 10. Checklist names (`deep_dive`, `peer_relative_valuation`, `bear_case`) match across Tasks 1, 4, 8, 9. ✅
- **No-git constraint:** all "Commit" steps replaced with "Checkpoint (NO git)" per the local-only rule. ✅
