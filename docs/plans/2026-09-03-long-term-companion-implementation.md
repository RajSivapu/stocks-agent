# Long-Term Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a gateway-qualified Long-Term Companion research section that can nominate one additive candidate beside VTI, show deterministic long-horizon and rolling-contribution history, and never alter a plan, holding, or brokerage account.

**Architecture:** Extend the existing portfolio-alternatives request with one optional nomination. Parse it strictly, validate the nomination against a gateway-owned fund-role catalog and the existing comparison/evaluation evidence, fetch a separate ten-year adjusted-price series, and pass a pure computed analysis to the Telegram renderer. Persist no new portfolio state; dry-run and on-demand behavior remains send-free and write-free.

**Tech Stack:** Deno TypeScript Edge Function, fixed-point portfolio math, Yahoo adjusted daily chart data behind the protected gateway, Python configuration invariants, Markdown product documentation.

**Spec:** `docs/design/2026-09-03-long-term-companion-design.md`

## Global Constraints

- Access stays `owner_only`; `friend_invitations_enabled` stays `false`.
- No brokerage credential, endpoint, order authority, or automatic plan/holding mutation.
- VTI stays the recorded recurring baseline until the owner separately confirms a change.
- A like-for-like substitute cannot be presented as an additive companion.
- Only VTI + VXUS may initially be marked eligible for an owner-reviewed additional recurring reminder.
- A stock or unsupported fund is a research-only satellite and never a recurring core candidate.
- Historical results are gateway-computed, normalized, and explicitly not forecasts.
- Missing, malformed, stale, conflicting, or inadequate evidence fails closed.
- On-demand and dry-run output sends no Telegram message and writes no Supabase business rows.

---

### Task 1: Strict companion request contract

**Files:**
- Modify: `supabase/functions/market-briefing-gateway/_shared/contracts.ts`
- Test: `supabase/functions/market-briefing-gateway/_shared/contracts_test.ts`

**Interfaces:**
- Produces: `CompanionRole`, `LongTermCompanionRequest`, and `DecisionBundle.companion_proposal?`.
- Consumes: validated candidate tickers, comparison pairs, candidate evidence, and phase restrictions already enforced by `parseDecisionBundle`.

- [ ] **Step 1: Write failing contract tests**

Add a valid on-demand bundle containing:

```ts
companion_proposal: {
  baseline_ticker: "VTI",
  companion_ticker: "VXUS",
  role: "diversifier",
  thesis: "Non-U.S. exposure adds a distinct geographic role.",
  risk_note: "Currency and foreign-market risks can cause long periods of lagging U.S. stocks.",
  evidence_ids: ["vxus-profile"],
}
```

Assert parsing succeeds only when both tickers are candidates, the pair exists in `comparisons`,
the evidence belongs to the companion and supports one of its factors, and the phase is pre-market
or on-demand. Add independent failures for an unknown ticker, absent comparison, unknown evidence,
intraday use, and `like_for_like` as a companion role.

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
deno test --allow-env supabase/functions/market-briefing-gateway/_shared/contracts_test.ts
```

Expected: FAIL because `companion_proposal` is not an accepted bundle key.

- [ ] **Step 3: Implement the minimal parser and types**

Add:

```ts
export type CompanionRole = "diversifier" | "tilt" | "satellite";

export interface LongTermCompanionRequest {
  baseline_ticker: string;
  companion_ticker: string;
  role: CompanionRole;
  thesis: string;
  risk_note: string;
  evidence_ids: string[];
}
```

Extend the exact-key branches in `parseDecisionBundle`, parse at most one strict object, reject
`like_for_like`, require a matching comparison pair, and reuse the existing companion-candidate
evidence/factor membership checks.

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run the command from Step 2. Expected: all contract tests pass.

- [ ] **Step 5: Commit**

```bash
git add supabase/functions/market-briefing-gateway/_shared/contracts.ts supabase/functions/market-briefing-gateway/_shared/contracts_test.ts
git commit -m "feat: validate long-term companion requests"
```

---

### Task 2: Ten-year provider range and pure companion analytics

**Files:**
- Create: `supabase/functions/market-briefing-gateway/_shared/companion.ts`
- Create: `supabase/functions/market-briefing-gateway/_shared/companion_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/market-data.ts`
- Test: `supabase/functions/market-briefing-gateway/_shared/market-data_test.ts`

**Interfaces:**
- Consumes: `LongTermCompanionRequest`, `PortfolioAlternativeRequest.relationship`, and synchronized `AdjustedBar[]`.
- Produces: `LongTermCompanionAnalysis`, `qualifyCompanionRole(...)`, and `analyzeLongTermCompanion(...)`.

- [ ] **Step 1: Write failing provider and analytics tests**

Provider test: call `fetchAdjustedHistory("VTI", "10y", fixtureFetch)` and assert the URL contains
`range=10y&interval=1d`, accepts at most 3,000 observations, and preserves the existing 400-row cap
for `1y`.

Analytics tests use literal price paths and assert:

- ITOT/SCHB and VT cannot qualify as companions;
- VOO/SCHD qualify only as `tilt`, VXUS only as `diversifier`, and unknown tickers only as
  `satellite`;
- only the VTI/VXUS pair is recurring-plan-review eligible;
- three, five, and ten-year rows appear only with at least 240 synchronized sessions per year;
- annualized return, max drawdown, and daily-return correlation use synchronized adjusted prices;
- rolling twelve-contribution history reports `$1200` contributed, deterministic p10/p50/p90
  ending values, and sample count; and
- fewer than three years produces `insufficient` without invented metrics.

- [ ] **Step 2: Run the focused tests and confirm RED**

```bash
deno test --allow-env \
  supabase/functions/market-briefing-gateway/_shared/market-data_test.ts \
  supabase/functions/market-briefing-gateway/_shared/companion_test.ts
```

Expected: FAIL because the range and module do not exist.

- [ ] **Step 3: Implement provider range validation**

Change the signature to:

```ts
export type AdjustedHistoryRange = "1y" | "10y";
export async function fetchAdjustedHistory(
  ticker: string,
  range: AdjustedHistoryRange,
  fetchImpl: FetchLike = fetch,
): Promise<AdjustedBar[]>
```

Use a range-specific observation cap of 400 or 3,000 while retaining response-size, decimal,
timestamp, split, ordering, and URL-encoding validation.

- [ ] **Step 4: Implement pure policy and analytics**

Define these output shapes:

```ts
export interface CompanionHorizon {
  years: 3 | 5 | 10;
  period_start: string;
  period_end: string;
  common_sessions: number;
  baseline_annualized_return_pct: string;
  companion_annualized_return_pct: string;
  baseline_max_drawdown_pct: string;
  companion_max_drawdown_pct: string;
  daily_return_correlation: string;
}

export interface RollingContributionScenario {
  monthly_contribution_usd: "100";
  total_contributed_usd: "1200";
  sample_windows: number;
  weak_ending_value_usd: string;
  middle_ending_value_usd: string;
  strong_ending_value_usd: string;
}

export interface LongTermCompanionAnalysis extends LongTermCompanionRequest {
  qualification_status: "qualified" | "insufficient";
  qualification_reason: string;
  recurring_plan_review_eligible: boolean;
  horizons: CompanionHorizon[];
  rolling_one_year: RollingContributionScenario | null;
}
```

Use fixed input validation and deterministic four-decimal percentage/correlation output. Compute
annualization from actual calendar-day span, Pearson correlation from synchronized daily returns,
and nearest-rank percentiles from all twelve-month contribution windows. Require a complete 3-year
row and a rolling scenario for `qualified`.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run the command from Step 2. Expected: all provider and companion tests pass.

- [ ] **Step 6: Commit**

```bash
git add supabase/functions/market-briefing-gateway/_shared/market-data.ts supabase/functions/market-briefing-gateway/_shared/market-data_test.ts supabase/functions/market-briefing-gateway/_shared/companion.ts supabase/functions/market-briefing-gateway/_shared/companion_test.ts
git commit -m "feat: compute long-term companion evidence"
```

---

### Task 3: Gateway qualification and receipt integration

**Files:**
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler.ts`
- Test: `supabase/functions/market-briefing-gateway/_shared/handler_test.ts`

**Interfaces:**
- Consumes: parsed `companion_proposal`, corresponding computed comparison, policy evaluation, fresh/fallback evidence, and ten-year history.
- Produces: `companion_status`, `companion_analysis`, and renderer input while creating no additional repository mutation.

- [ ] **Step 1: Write failing dry-run integration tests**

Add an on-demand VTI/VXUS fixture whose `fetchHistory` records the requested range. Assert:

```ts
assertEquals(result.status, "suppressed");
assertEquals(result.companion_status, "qualified");
assertEquals(result.write_counts, {});
assertEquals(result.telegram_message_ids, []);
assertEquals(repository.mutationCalls, 0);
assertEquals(sent, []);
```

Also assert `1y` is requested for the existing comparison, `10y` for the companion analysis, and a
policy-rejected or stale-evidence candidate returns `insufficient` without a plan/holding write.

- [ ] **Step 2: Run the focused test and confirm RED**

```bash
deno test --allow-env supabase/functions/market-briefing-gateway/_shared/handler_test.ts
```

Expected: FAIL because the handler ignores the companion proposal and range.

- [ ] **Step 3: Implement range-aware dependencies and qualification**

Change the dependency interface to:

```ts
fetchHistory?: (
  ticker: string,
  range?: AdjustedHistoryRange,
) => Promise<AdjustedBar[]>;
```

Keep outcome grading and existing comparisons on explicit `1y`. Add `buildLongTermCompanion` that
checks owner baseline, matching comparison, gateway role policy, approved companion evaluation,
and fresh/fallback evidence before calling the pure ten-year analysis. Pass its result only to the
renderer and response receipt; do not add it to `PersistedBundle` or repository mutation methods.

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run the command from Step 2. Expected: all handler tests pass.

- [ ] **Step 5: Commit**

```bash
git add supabase/functions/market-briefing-gateway/_shared/handler.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts
git commit -m "feat: qualify companion proposals in gateway"
```

---

### Task 4: Compact Telegram rendering and directive safety

**Files:**
- Modify: `supabase/functions/market-briefing-gateway/_shared/renderer.ts`
- Test: `supabase/functions/market-briefing-gateway/_shared/renderer_test.ts`

**Interfaces:**
- Consumes: `LongTermCompanionAnalysis | undefined` and the existing owner plan context.
- Produces: an optional escaped-HTML `🧠 LONG-TERM COMPANION` section.

- [ ] **Step 1: Write failing renderer tests**

Assert a qualified VTI/VXUS fixture includes the core plan amount, candidate role, thesis, risk,
available 3/5/10-year rows, per-$100 rolling one-year values, plan-review eligibility, and both
non-forecast/no-change statements. Assert a satellite says `research-only` and `not eligible for a
recurring core reminder`. Assert comparisons with no proposal say no additive candidate cleared.

Extend directive tests so thesis/risk text rejects buy/sell/switch/replace/reallocate/allocate,
share quantities, dollar proposals, stop/target levels, guaranteed profit, and return-promise
phrasing.

- [ ] **Step 2: Run the focused test and confirm RED**

```bash
deno test --allow-env supabase/functions/market-briefing-gateway/_shared/renderer_test.ts
```

Expected: FAIL because the companion section is absent.

- [ ] **Step 3: Implement the compact renderer**

Render only available horizon rows. Format percentages to one decimal and correlation to two
decimals. Escape all text and links. Do not emit a companion section when no alternatives review
ran. Reuse the existing forbidden-decision validator for thesis/risk and extend it only where tests
prove a missing unsafe phrase.

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run the command from Step 2. Expected: all renderer tests pass.

- [ ] **Step 5: Commit**

```bash
git add supabase/functions/market-briefing-gateway/_shared/renderer.ts supabase/functions/market-briefing-gateway/_shared/renderer_test.ts
git commit -m "feat: render long-term companion review"
```

---

### Task 5: Routine, configuration, and owner documentation

**Files:**
- Modify: `config/settings.json`
- Modify: `skills/market-briefing/SKILL.md`
- Modify: `routines/README.md`
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`
- Test: `tests/test_policy_config.py`
- Test: `docs/eval/market-briefing-eval.yaml`

**Interfaces:**
- Consumes: the final JSON contract and gateway behavior from Tasks 1-4.
- Produces: exact Routine nomination instructions and durable personal-product boundaries.

- [ ] **Step 1: Write failing configuration/evaluation assertions**

Require `portfolio_alternatives.long_term_companion` to specify enabled status, max one proposal,
history years `[3,5,10]`, the normalized `$100` scenario, VTI/VXUS recurring-review eligibility,
and false automatic plan/holding changes. Add eval scenarios for substitute rejection, VXUS
diversifier nomination, satellite non-eligibility, and no-qualified-candidate output.

- [ ] **Step 2: Run the focused Python test and confirm RED**

```bash
pytest -q tests/test_policy_config.py
```

Expected: FAIL because the companion settings are absent.

- [ ] **Step 3: Update settings and instructions**

Teach the Routine to screen all supported alternatives, nominate at most one only after separate
Analyst/Checker/evidence work, omit the proposal when none qualifies, and never submit historical
numbers. State that ITOT/SCHB duplicate VTI's role, VT is a replacement, VXUS can diversify, tilts
must disclose overlap, and stocks are concentrated research-only satellites.

- [ ] **Step 4: Run focused and documentation tests**

```bash
pytest -q tests/test_policy_config.py tests/test_security_invariants.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add config/settings.json skills/market-briefing/SKILL.md routines/README.md README.md docs/ROADMAP.md docs/eval/market-briefing-eval.yaml tests/test_policy_config.py tests/test_security_invariants.py
git commit -m "docs: teach routines long-term companion policy"
```

---

### Task 6: Full verification, review, deployment, and protected proof

**Files:**
- Modify after receipt: `docs/rollouts/2026-09-03-owner-alert-v3-shadow.md`

**Interfaces:**
- Consumes: completed implementation commits.
- Produces: source-parity, test, deployment, dry-run, table-delta, Telegram, and owner-plan receipts.

- [ ] **Step 1: Run all local verification**

```bash
pytest -q
node --test tests/*.mjs
deno test --allow-env supabase/functions/market-briefing-gateway/_shared/*_test.ts
deno check supabase/functions/market-briefing-gateway/index.ts
deno check supabase/functions/telegram-portfolio/index.ts
git diff --check
```

Expected: zero failures and zero type-check/diff errors.

- [ ] **Step 2: Obtain independent code review**

Review the implementation range from `9837ef4` to HEAD against the design and this plan. Resolve all
Critical and Important findings, then rerun the affected focused tests and the complete verification
set.

- [ ] **Step 3: Deploy only the gateway through the protected project path**

Verify the target project ref is exactly `hlxpxbxhqctwsqizwjjy`, verify owner-only secrets and alert
policy remain unchanged, deploy `market-briefing-gateway`, and record the returned deployed version.
Do not deploy `telegram-portfolio` unless its source changed.

- [ ] **Step 4: Prove deployed source parity and health**

Download the deployed gateway bundle, compare it with the local runtime excluding test files, and
call the protected health operation. Record the exact outputs.

- [ ] **Step 5: Run one protected on-demand dry-run**

Submit VTI plus supported alternative candidates and one evidence-backed companion nomination.
Before and after, count all protected business tables and read the active VTI plan. Require:

```text
status=suppressed
write_counts={}
telegram_message_ids=[]
all protected table deltas=0
VTI plan unchanged
```

Inspect the full rendered preview for message hierarchy, role accuracy, complete scenario labels,
and absence of an action directive or future-profit claim. Do not trigger a saved live Routine.

- [ ] **Step 6: Record and commit receipt evidence**

Add request/run IDs, deployed version, health/source parity, computed horizons/scenario, receipt
fields, table deltas, and VTI plan state to the rollout document. Make only claims supported by the
captured receipts.

```bash
git add docs/rollouts/2026-09-03-owner-alert-v3-shadow.md docs/ROADMAP.md
git commit -m "docs: record long-term companion rollout"
git push origin codex/owner-alert-v3
```

- [ ] **Step 7: Preserve scheduled verification**

Inspect the existing rollout heartbeat and ensure its Friday pre-market, intraday, post-market, and
weekly-audit receipt chain remains active. Do not create a duplicate automation and do not treat the
dry-run as a scheduled-production receipt.
