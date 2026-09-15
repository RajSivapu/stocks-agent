# Reliable Telegram Alert Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore dependable pre-market and post-market Telegram briefs by completing a bounded alert lane before resuming market-wide research in deadline-safe slices.

**Architecture:** Scheduled analysis runs remain the sole owners of Telegram and use an explicit `alert` intelligence lane with a small deterministic plan. Separate `on-demand` analysis runs use a `research` lane, persist progress at existing task checkpoints, and may pause without creating a packet or publication. The gateway and database bind lane identity to every intelligence run and reject report, evaluation, or delivery mutations from research runs.

**Tech Stack:** Python 3.11+, pytest, TypeScript, Deno, PostgreSQL/PLpgSQL, Supabase Edge Functions, GitHub Actions, Telegram Bot API

**Spec:** `docs/superpowers/specs/2026-09-15-telegram-fast-lane-design.md`

## Global Constraints

- Keep the application owner-only and suggestion-only; no order execution or brokerage connection.
- Use only existing zero-cost sources; add no paid, premium, trial, or metered API dependency.
- Pre-market and post-market must produce one expected-publication outcome on each open US trading day.
- Intraday remains silent unless a configured trigger fires.
- The alert lane has a four-minute application budget, an eight-request live-source ceiling, and a three-minute collection ceiling.
- Stop beginning optional alert requests when fewer than 30 seconds remain.
- A missing actionable quote must yield a status-only brief with `No new action — data check incomplete` and zero new actionable suggestions.
- A research lane may never publish to Telegram or mutate an alert publication.
- Preserve exact-run idempotency and the existing uncertain-delivery barrier; never create a second Telegram attempt to inspect or repair output.
- Keep the approved concise Telegram renderer structure and keep raw audit details on the owner-only web surface.
- Preserve all existing additive migrations and receipts; do not rewrite or delete historical rows.
- Do not republish the owner-only Site unless its built bytes change.
- Production closeout must use a normal scheduled receipt, never a duplicate manual live run.

---

### Task 1: Model lane identity and a monotonic collection budget

**Files:**
- Modify: `lib/intelligence/pipeline.py`
- Modify: `lib/intelligence/planner.py`
- Modify: `tests/test_intelligence_pipeline.py`
- Modify: `tests/test_intelligence_planner.py`

**Interfaces:**
- Produces: `Lane = Literal["alert", "research"]` and `PipelineRequest.lane: Lane`.
- Produces: `CollectionBudget(deadline: float | None, request_limit: int | None, stop_margin_seconds: float = 30.0)` with `can_start(required_requests: int = 1) -> bool` and `record(requests: int) -> None`.
- Produces: `build_alert_discovery_plan(..., priority_theme_ids: Sequence[str]) -> DiscoveryPlan`, containing only Yahoo quote work, one general GDELT task, and at most two deterministic priority-theme GDELT tasks, with no SEC reference task and at most eight requests.
- Consumes: existing `DiscoveryPlan`, `DiscoveryTask`, capability registry, policy budgets, source cursors, and protected reference version.

- [ ] **Step 1: Write failing request and budget tests**

Add tests that construct both valid lanes, reject any other lane, advance a fake monotonic clock into the 30-second stop margin, and prove `can_start()` becomes false before an optional request begins. Add a test proving the counter rejects a ninth alert request.

```python
request = PipelineRequest(
    phase="pre-market", market_date=date(2026, 9, 15), now=NOW,
    request_id=RUN_ID, lane="alert",
)
assert request.lane == "alert"
budget = CollectionBudget(deadline=210.0, request_limit=8, monotonic=lambda: 181.0)
assert budget.can_start() is False
```

- [ ] **Step 2: Run the focused tests and confirm the new types are absent**

Run: `.venv/bin/python -m pytest tests/test_intelligence_pipeline.py tests/test_intelligence_planner.py -q`

Expected: FAIL because `PipelineRequest` has no `lane`, `CollectionBudget` is missing, and `build_alert_discovery_plan` is missing.

- [ ] **Step 3: Add the lane and budget types**

Add a validated `lane` field defaulting to `research` for backward-compatible direct callers. Implement `CollectionBudget` with an injected monotonic clock, nonnegative counters, exact request ceilings, and the 30-second stop margin. Thread the lane through `PipelineRequest.collection_plan()` as a top-level `lane` field.

```python
Lane = Literal["alert", "research"]

@dataclass(slots=True)
class CollectionBudget:
    deadline: float | None
    request_limit: int | None
    stop_margin_seconds: float = 30.0
    monotonic: Callable[[], float] = time.monotonic
    requests_started: int = 0

    def can_start(self, required_requests: int = 1) -> bool:
        within_requests = self.request_limit is None or self.requests_started + required_requests <= self.request_limit
        within_time = self.deadline is None or self.monotonic() < self.deadline - self.stop_margin_seconds
        return within_requests and within_time
```

- [ ] **Step 4: Add the bounded alert planner**

Build the alert plan from enabled Yahoo and GDELT capabilities only. Sort priority themes by normalized theme ID, take two, reserve no adaptive enrichment, reserve only the quote count needed by the protected context, and raise if the resulting request count exceeds eight. The research planner remains `build_discovery_plan()` with its existing behavior.

- [ ] **Step 5: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_intelligence_pipeline.py tests/test_intelligence_planner.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the domain boundary**

```bash
git add lib/intelligence/pipeline.py lib/intelligence/planner.py tests/test_intelligence_pipeline.py tests/test_intelligence_planner.py
git commit -m "feat: model bounded intelligence lanes"
```

### Task 2: Persist immutable alert and research lane identity

**Files:**
- Create: `sql/migrations/20261031_telegram_fast_lane.sql`
- Modify: `sql/schema.sql`
- Create: `tests/test_telegram_fast_lane_sql.py`
- Modify: `tests/test_managed_isolated_restore.py`
- Modify: `tests/test_recovery_bundle.py`

**Interfaces:**
- Produces: `market_intelligence_runs.lane TEXT NOT NULL CHECK (lane IN ('alert','research'))`.
- Produces: `start_market_intelligence_run(UUID,TEXT,DATE,INT,JSONB,JSONB,TEXT) -> JSONB`; duplicate calls must match lane.
- Produces: `read_latest_terminal_research_packet(UUID,DATE,TIMESTAMPTZ) -> JSONB`, returning either `null` or exact `packet_id`, `packet_hash`, `market_date`, `created_at`, and bounded `age_days` from the most recent prior open session within five calendar days.
- Produces: SQL guards that require scheduled analysis runs to use `alert`, require research to use an `on-demand` analysis run, and prevent research runs from recording reports or report publications.
- Consumes: the current six-argument `start_market_intelligence_run`, packet hash contract, NYSE session table, report functions, and additive migration/restore conventions.

- [ ] **Step 1: Write the failing SQL integration tests**

Create PostgreSQL-backed tests that apply the full schema and assert:

```sql
SELECT start_market_intelligence_run(
  :scheduled_run, 'pre-market', DATE '2026-09-15', 4,
  :plan, :window, 'alert'
);
SELECT start_market_intelligence_run(
  :research_run, 'on-demand', DATE '2026-09-15', 4,
  :plan, :window, 'research'
);
```

The tests must reject a scheduled `research` lane, reject an `on-demand` `alert` lane, reject a duplicate with a different lane, reject `record_market_report` for research, return only a completed prior-session research packet, omit packets older than five calendar days, and preserve the same lane and packet after a recovery export/import.

- [ ] **Step 2: Run the SQL test and confirm the seven-argument function is absent**

Run: `.venv/bin/python -m pytest tests/test_telegram_fast_lane_sql.py -q`

Expected: FAIL because the lane column, overload, reader, and guards do not exist.

- [ ] **Step 3: Implement the additive migration**

Add the lane column with an explicit backfill: existing scheduled runs become `alert`; existing on-demand runs become `research`. Replace the current authoritative start function with a seven-argument function and retain a six-argument compatibility wrapper that derives the same lane rule. Add the read function as `SECURITY DEFINER SET search_path=pg_catalog`, revoke it from public/dashboard roles, and grant only `service_role`. Replace the authoritative report mutation functions with early lane checks.

- [ ] **Step 4: Mirror the migration exactly into the consolidated schema**

Append the migration under a `-- Consolidated from sql/migrations/20261031_telegram_fast_lane.sql` marker. Keep the standalone migration byte-for-byte represented after that marker so fresh installs and upgrades expose the same functions and grants.

- [ ] **Step 5: Extend recovery coverage**

Add `lane` to the protected data export/import column allowlists and assertions. Verify a restored research packet remains linked to its research lane and does not gain publication authority.

- [ ] **Step 6: Run SQL and recovery tests**

Run: `.venv/bin/python -m pytest tests/test_telegram_fast_lane_sql.py tests/test_managed_isolated_restore.py tests/test_recovery_bundle.py -q`

Expected: PASS.

- [ ] **Step 7: Commit persistence changes**

```bash
git add sql/migrations/20261031_telegram_fast_lane.sql sql/schema.sql tests/test_telegram_fast_lane_sql.py tests/test_managed_isolated_restore.py tests/test_recovery_bundle.py
git commit -m "feat: persist protected intelligence lanes"
```

### Task 3: Enforce lane authority in the gateway

**Files:**
- Modify: `supabase/functions/market-briefing-gateway/_shared/intelligence.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/contracts.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/repository.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/repository_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/handler_test.ts`

**Interfaces:**
- Produces: `IntelligenceLane = "alert" | "research"`.
- Produces: `StartIntelligencePayload.lane: IntelligenceLane` and `IntelligenceStartReceipt.lane: IntelligenceLane`.
- Produces: `GatewayReadContext.latest_research_packet` as `null | { packet_id; packet_hash; market_date; created_at; age_days }`.
- Produces: `GatewayRepository.intelligenceLane(runId: string) -> Promise<IntelligenceLane>` and `latestTerminalResearchPacket(runId: string, marketDate: string, now: Date) -> Promise<...>`.
- Consumes: the SQL interfaces from Task 2 and existing exact-key parsers.

- [ ] **Step 1: Write failing contract and handler tests**

Add exact-key parser cases for both valid lanes and an invalid lane. Add handler tests proving `start_intelligence_run` forwards lane, `read_context` returns a validated terminal research packet, `evaluate_and_publish` and `record_report` reject a research run with `INTELLIGENCE_LANE_REJECTED`, and an alert packet cannot claim a mismatched research packet hash.

- [ ] **Step 2: Run the focused Deno tests**

Run: `deno test --allow-env supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts`

Expected: FAIL because lane fields and repository methods are absent.

- [ ] **Step 3: Extend exact request and receipt contracts**

Add `lane` to the exact key set for start payloads and start receipts. Validate `latest_research_packet` as an exact bounded object, lowercase SHA-256 hash, UUID packet ID, ISO date/timestamp, and integer age from zero through five.

- [ ] **Step 4: Wire repository RPCs**

Pass `p_lane` to `start_market_intelligence_run`. Parse the returned lane and implement the lane/read methods using the Task 2 SQL functions. Translate malformed persisted values to `INVALID_PERSISTED_DATA` and database failures to `PERSISTENCE_FAILED`.

- [ ] **Step 5: Add handler fail-closed checks**

Before packet evaluation, report recording, report publication creation, or run finishing, load lane authority. Permit those operations only for `alert`. Keep research collection start/checkpoint/record operations available and prohibit any Telegram call on the rejected path. Validate a prior research packet reference by ID and hash before exposing it to alert context.

- [ ] **Step 6: Run focused Deno tests**

Run: `deno test --allow-env supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts`

Expected: PASS.

- [ ] **Step 7: Commit gateway enforcement**

```bash
git add supabase/functions/market-briefing-gateway/_shared/intelligence.ts supabase/functions/market-briefing-gateway/_shared/contracts.ts supabase/functions/market-briefing-gateway/_shared/repository.ts supabase/functions/market-briefing-gateway/_shared/handler.ts supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts
git commit -m "feat: enforce gateway lane authority"
```

### Task 4: Build the bounded alert collector and resumable research slice

**Files:**
- Modify: `scripts/collect_market_intelligence.py`
- Modify: `scripts/market_gateway.py`
- Modify: `lib/intelligence/pipeline.py`
- Modify: `tests/test_collect_market_intelligence.py`
- Modify: `tests/test_gateway.py`
- Modify: `tests/test_intelligence_pipeline.py`

**Interfaces:**
- Produces CLI: `collect_market_intelligence.py --lane {alert,research} --budget-seconds N`.
- Produces result states: completed `PipelineReceipt` or JSON `{"status":"paused","run_id":...,"lane":"research","planned_remaining":N,"deadline_reached":true}`.
- Produces: `IntelligencePipeline.run_slice(request, budget) -> PipelineReceipt | PausedPipelineReceipt`.
- Consumes: `CollectionBudget`, `build_alert_discovery_plan`, gateway lane contract, persisted discovery task checkpoints, protected holdings/plans/themes/reference context.

- [ ] **Step 1: Write failing CLI and interruption tests**

Use fake clocks/adapters to prove a 24-task research plan pauses before the deadline, leaves unstarted tasks planned, never repeats completed or uncertain attempts on resume, and does not call `record_intelligence` until completion rules pass. Prove alert mode builds no SEC reference transfer, starts at most eight requests, persists a partial completed packet when optional GDELT is slow, and returns before the four-minute budget.

- [ ] **Step 2: Run focused Python tests**

Run: `.venv/bin/python -m pytest tests/test_collect_market_intelligence.py tests/test_gateway.py tests/test_intelligence_pipeline.py -q`

Expected: FAIL because the CLI lane/budget arguments and paused receipt do not exist.

- [ ] **Step 3: Add CLI validation and shared deadline construction**

Require `--lane`; default `--budget-seconds` to `240` for alert and research. Reject alert budgets above 240 and research budgets above 240. Construct one monotonic deadline and pass it through reference, provider, checkpoint, and completion paths; cap every outbound HTTP timeout by remaining time.

- [ ] **Step 4: Implement alert collection**

Read protected quote targets and due priority themes from context, build the alert plan, skip SEC refresh, attach the latest eligible research packet metadata, and complete a packet from terminal tasks when optional tasks are unavailable or the stop margin is reached. Set `coverage.actionable_data_complete` only when every ticker used in a new action has a fresh, USD, symbol-bound quote.

- [ ] **Step 5: Implement research slicing**

Execute persisted tasks in deterministic `(stage priority, provider priority, task_id)` order. Check `CollectionBudget.can_start()` before every provider attempt and multi-call reference chunk. On budget exhaustion, write no fabricated terminal task, return the paused receipt, and leave the analysis/intelligence run running for the next normal phase. Existing completed and `attempting` task states remain authoritative on resume.

- [ ] **Step 6: Run focused Python tests**

Run: `.venv/bin/python -m pytest tests/test_collect_market_intelligence.py tests/test_gateway.py tests/test_intelligence_pipeline.py -q`

Expected: PASS.

- [ ] **Step 7: Commit collector changes**

```bash
git add scripts/collect_market_intelligence.py scripts/market_gateway.py lib/intelligence/pipeline.py tests/test_collect_market_intelligence.py tests/test_gateway.py tests/test_intelligence_pipeline.py
git commit -m "feat: collect alerts before resumable research"
```

### Task 5: Guarantee useful daily briefs when action data is incomplete

**Files:**
- Modify: `supabase/functions/market-briefing-gateway/_shared/policy.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/reports.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/renderer.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/policy_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/reports_test.ts`
- Modify: `supabase/functions/market-briefing-gateway/_shared/renderer_test.ts`

**Interfaces:**
- Produces: gateway policy veto reason `ACTION_DATA_INCOMPLETE` for every new `buy`, `add`, `reduce`, or `sell` derived from an alert packet whose `actionable_data_complete` is false.
- Produces: expected-publication status-only body for pre-market and post-market with the approved portfolio, market, open-zone/risk, next-session, and optional coverage-note sections.
- Consumes: alert packet coverage, current protected holdings/plans, server-side quote fetches, canonical periodic renderer, and existing report idempotency.

- [ ] **Step 1: Write failing policy and renderer tests**

Add tests with missing/stale actionable quotes that assert zero approved new actionable suggestions, a ready pre-market/post-market report, the exact line `No new action — data check incomplete`, no invented price/return values, and concise coverage categories. Keep the existing intraday no-trigger suppression assertion and source-link safety cases.

- [ ] **Step 2: Run focused Deno tests**

Run: `deno test --allow-env supabase/functions/market-briefing-gateway/_shared/policy_test.ts supabase/functions/market-briefing-gateway/_shared/reports_test.ts supabase/functions/market-briefing-gateway/_shared/renderer_test.ts`

Expected: FAIL because incomplete alert coverage does not yet veto action or force a useful periodic status report.

- [ ] **Step 3: Add the deterministic policy veto**

Read the packet coverage flag only from the validated persisted packet. Downgrade all affected actionable decisions with `ACTION_DATA_INCOMPLETE`; retain safe `hold`, `watch`, `avoid`, portfolio-status, and risk-monitoring conclusions that have their required evidence.

- [ ] **Step 4: Add the status-only periodic rendering path**

Render factual protected state and available fresh quotes. Include the incomplete-data line and bounded coverage note; omit any unavailable number instead of rendering a zero, placeholder, or estimate. Keep the canonical periodic heading and section contract so Telegram splitting/idempotency remain unchanged.

- [ ] **Step 5: Run focused Deno tests**

Run: `deno test --allow-env supabase/functions/market-briefing-gateway/_shared/policy_test.ts supabase/functions/market-briefing-gateway/_shared/reports_test.ts supabase/functions/market-briefing-gateway/_shared/renderer_test.ts`

Expected: PASS.

- [ ] **Step 6: Commit report behavior**

```bash
git add supabase/functions/market-briefing-gateway/_shared/policy.ts supabase/functions/market-briefing-gateway/_shared/reports.ts supabase/functions/market-briefing-gateway/_shared/renderer.ts supabase/functions/market-briefing-gateway/_shared/policy_test.ts supabase/functions/market-briefing-gateway/_shared/reports_test.ts supabase/functions/market-briefing-gateway/_shared/renderer_test.ts
git commit -m "fix: publish useful status-only daily briefs"
```

### Task 6: Switch scheduled routines to alert-first execution

**Files:**
- Modify: `skills/market-briefing/SKILL.md`
- Modify: `routines/README.md`
- Modify: `tests/test_owner_intelligence_contract.py`
- Modify: `tests/test_wait_market_intelligence.py`

**Interfaces:**
- Produces routine sequence: reconcile/start scheduled alert run; collect alert lane; evaluate; record canonical report; deliver/reconcile; finish alert run; start/resume one on-demand research slice; exit.
- Consumes: collector CLI from Task 4, current gateway operations, publication receipt states, and scheduled run IDs.

- [ ] **Step 1: Write failing routine contract tests**

Assert each normal phase prompt places alert collection and its terminal publication before research, never calls the old full-collector wait/restart path, uses `--lane alert --budget-seconds 240`, uses `--lane research --budget-seconds 240` only after alert terminal state, skips research on holidays, and treats research `paused` as a clean bounded result.

- [ ] **Step 2: Run the focused routine tests**

Run: `.venv/bin/python -m pytest tests/test_owner_intelligence_contract.py tests/test_wait_market_intelligence.py -q`

Expected: FAIL because the current routine still starts/waits/restarts the full collector before Telegram.

- [ ] **Step 3: Rewrite the routine contract**

Remove the six-minute blocking wait and same-run full-collector restart from normal execution. Specify exact receipt checks for each alert step, explicit terminal transport handling, no duplicate resend, and a best-effort research slice that cannot change the completed alert outcome.

- [ ] **Step 4: Update operator documentation**

Document the two lanes, budgets, pause semantics, manual diagnostic procedure, and normal-schedule-only production acceptance. State that `planned` research work is resumable backlog and is not an alert-delivery failure.

- [ ] **Step 5: Run the focused routine tests**

Run: `.venv/bin/python -m pytest tests/test_owner_intelligence_contract.py tests/test_wait_market_intelligence.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the routine switch**

```bash
git add skills/market-briefing/SKILL.md routines/README.md tests/test_owner_intelligence_contract.py tests/test_wait_market_intelligence.py
git commit -m "fix: run scheduled alerts before research"
```

### Task 7: Verify release boundaries and close the V1 checklist

**Files:**
- Modify: `scripts/verify_personal_stock_agent_v1.py`
- Modify: `tests/test_verify_personal_stock_agent_v1.py`
- Modify: `tests/test_production_v1_verification_workflow.py`
- Modify: `PROJECT_STATUS.md`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Produces verifier evidence for lane identity, alert terminal chain, original Telegram message IDs or explicit terminal transport state, and later nonblocking research progress.
- Produces canonical V1 checklist language that remains pending until a normal scheduled receipt proves the deployed behavior.
- Consumes: protected release manifest, live read-only gateway, exact-main workflow SHA, report/publication chain, and research task checkpoints.

- [ ] **Step 1: Write failing verifier tests**

Add fixtures for: a delivered alert with one original Telegram message ID and later research progress; a status-only delivered alert; a research run that attempts publication; a duplicate Telegram attempt; an alert with no packet; and a completed alert followed by paused research. Assert only the first, second, and last shapes pass their applicable checks.

- [ ] **Step 2: Run focused verifier tests**

Run: `.venv/bin/python -m pytest tests/test_verify_personal_stock_agent_v1.py tests/test_production_v1_verification_workflow.py -q`

Expected: FAIL because verifier output does not yet expose lane and nonblocking research evidence.

- [ ] **Step 3: Add verifier checks and compact diagnostics**

Report `alert_lane_terminal`, `alert_publication_status`, `telegram_message_ids`, `telegram_attempt_count`, `research_lane_progressed_after_alert`, and `research_remaining_tasks`. Fail closed on lane mismatch, publication from research, duplicate attempts, packet/report hash mismatch, or an absent expected-publication outcome.

- [ ] **Step 4: Update status and roadmap**

Record the final architecture, completed local checks, release SHA fields, and the one remaining production acceptance item: a normal scheduled pre-market/post-market receipt plus later research progress. Do not mark V1 complete in the document until those receipts exist.

- [ ] **Step 5: Run focused verifier tests**

Run: `.venv/bin/python -m pytest tests/test_verify_personal_stock_agent_v1.py tests/test_production_v1_verification_workflow.py -q`

Expected: PASS.

- [ ] **Step 6: Run complete local verification**

Run: `npm run test:all`

Expected: every Python, Deno, Node, Playwright, typecheck, lint, license, runtime-bundle, and build check passes.

- [ ] **Step 7: Commit release verification**

```bash
git add scripts/verify_personal_stock_agent_v1.py tests/test_verify_personal_stock_agent_v1.py tests/test_production_v1_verification_workflow.py PROJECT_STATUS.md docs/ROADMAP.md
git commit -m "docs: close telegram fast-lane implementation checklist"
```

### Task 8: Protected integration, backend release, and normal receipt proof

**Files:**
- Review: `.github/workflows/ci.yml`
- Review: `.github/workflows/production-v1-scheduled-verification.yml`
- Review: `scripts/native_release_adapter.py`
- Review: `scripts/verify_personal_stock_agent_v1.py`

**Interfaces:**
- Produces: protected main commit, exact-main green CI, deployed backend runtime SHA, read-only attestation, and a later normal scheduled receipt.
- Consumes: all commits from Tasks 1-7 and the repository's existing protected release process.

- [ ] **Step 1: Rebase on current origin/main and rerun full verification**

Run: `git fetch origin && git rebase origin/main && npm run test:all`

Expected: clean rebase and full PASS.

- [ ] **Step 2: Review the complete branch diff**

Run: `git diff --check && git diff --stat origin/main...HEAD && git log --oneline origin/main..HEAD`

Expected: no whitespace errors; only the planned lane, collector, routine, verifier, tests, spec, plan, and status changes.

- [ ] **Step 3: Push the feature branch and open the protected PR**

Push `codex/telegram-fast-lane`. Create a PR whose description leads with the scheduled full-collector blockage and the alert-first behavior, then includes migration, idempotency, and validation evidence.

- [ ] **Step 4: Require exact-head PR CI and merge through protection**

Read the PR head SHA, wait for all required checks at that SHA, merge without bypassing protection, and record the resulting main SHA.

- [ ] **Step 5: Require exact-main CI and deploy the backend**

Wait for the required main workflow at the merged SHA. Use the existing release adapter to apply the additive migration and deploy the Edge Function/runtime bundle. Read back the protected runtime SHA and migration state.

- [ ] **Step 6: Run read-only deployment verification**

Run the production verifier in diagnostic/read-only mode. Confirm the deployed runtime SHA equals the merged main SHA, lane columns/functions are present, the previous stuck run remains immutable history, and no duplicate Telegram attempt was created.

- [ ] **Step 7: Observe the next normal scheduled receipt**

Wait for the next normal pre-market or post-market run. Read only its alert run, intelligence packet, report, publication, and Telegram receipt; then read a later research checkpoint. Do not manually trigger a duplicate live phase.

- [ ] **Step 8: Close the V1 goal only after receipt proof**

Update `PROJECT_STATUS.md` and `docs/ROADMAP.md` with the exact production receipt IDs and final V1 state, commit through the protected path, and mark the tracked Codex goal complete only after no required work remains.
