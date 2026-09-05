# GPT-6 Astra Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make Personal Stock Agent V1 safe enough for owner-only shadow use and then trusted suggestion-only decision support by fixing every concrete GPT-6 Astra audit finding.

**Architecture:** Establish safe test and authentication boundaries first, then repair final policy and money semantics, then make intelligence and operations receipt-complete. The final release gate recomputes evidence from authoritative records and permits production rollout only through the existing protected process.

**Tech Stack:** Python 3, pytest, TypeScript, Deno 2.9.6, React/Vitest, PostgreSQL/Supabase RPCs and Edge Functions, Telegram Bot API, GitHub Actions.

**Spec:** docs/superpowers/specs/2026-09-04-astra-audit-remediation-design.md

## Global Constraints

- Zero incremental dollars; do not add paid providers, paid trials, metered runtime model APIs, or premium infrastructure.
- Keep the repository suggestion-only with no brokerage credentials or order execution.
- Keep the dashboard owner-only; signup and friend invitations remain disabled.
- Do not start a duplicate live scheduled run.
- Missing, stale, contradictory, quota-blocked, or unverifiable evidence fails closed.
- No task performs a production mutation, deployment, Auth-template change, Telegram send, or live scheduled run.
- Use one focused red-green test cycle per behavior and defer the consolidated suite to Task 12.

---

### Task 1: Make credentialed database tests production-safe

**Files:**
- Create: tests/integration_guard.py
- Create: tests/test_integration_guard.py
- Modify: tests/test_db.py
- Modify: scripts/test_all.sh
- Modify: scripts/deploy_owner_dashboard_api.py

**Interfaces:**
- Produces: require_isolated_supabase_test_project(environment: Mapping[str, str], secrets_path: Path) -> str
- Produces: pytest mark db_integration, selected only when RUN_DB_INTEGRATION_TESTS=1
- Consumes: SUPABASE_URL, SUPABASE_TEST_PROJECT_REF, RUN_DB_INTEGRATION_TESTS

- [ ] **Step 1: Write the failing guard tests**

~~~python
def test_guard_refuses_production_project_even_when_opted_in(tmp_path):
    env = {
        "RUN_DB_INTEGRATION_TESTS": "1",
        "SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
        "SUPABASE_TEST_PROJECT_REF": "zzzzzzzzzzzzzzzzzzzz",
    }
    with pytest.raises(RuntimeError, match="isolated test project"):
        require_isolated_supabase_test_project(env, tmp_path / "missing.json")

def test_guard_requires_explicit_opt_in(tmp_path):
    with pytest.raises(RuntimeError, match="RUN_DB_INTEGRATION_TESTS"):
        require_isolated_supabase_test_project({}, tmp_path / "missing.json")
~~~

- [ ] **Step 2: Run the focused tests and confirm the missing module/function fails**

Run: .venv/bin/python -m pytest -q tests/test_integration_guard.py

- [ ] **Step 3: Implement the guard and convert mutation tests to an explicit fixture**

The guard must parse the configured Supabase hostname, require an exact 20-character project-ref match to SUPABASE_TEST_PROJECT_REF, reject the production ref declared by SUPABASE_PRODUCTION_PROJECT_REF when present, and reject absent opt-in. tests/test_db.py must register cleanup before mutation and must not load ignored production secrets unless the guard succeeds.

~~~python
@pytest.fixture
def isolated_db():
    require_isolated_supabase_test_project(os.environ, Path("config/test-secrets.local.json"))
    client = create_client(configured_test_url(), configured_test_service_key())
    cleanups = []
    try:
        yield client, cleanups
    finally:
        for cleanup in reversed(cleanups):
            cleanup()
~~~

Remove credential-dependent database tests from the default pytest selection. scripts/test_all.sh and deployment verification run the normal suite without setting the opt-in flag.

- [ ] **Step 4: Verify focused behavior**

Run: .venv/bin/python -m pytest -q tests/test_integration_guard.py tests/test_db.py -m "not db_integration"

- [ ] **Step 5: Commit**

Run: git add tests/integration_guard.py tests/test_integration_guard.py tests/test_db.py scripts/test_all.sh scripts/deploy_owner_dashboard_api.py && git commit -m "fix: isolate credentialed database tests"

---

### Task 2: Align six-digit OTP and make inactivity activity-based

**Files:**
- Modify: apps/web/src/auth/AuthProvider.tsx
- Modify: apps/web/src/auth/SignInPage.tsx
- Modify: apps/web/src/auth/auth.test.tsx
- Modify: scripts/provision_owner_dashboard_auth.py
- Modify: tests/test_provision_owner_dashboard_auth.py
- Modify: scripts/verify_owner_dashboard_deployment.py
- Modify: tests/test_verify_owner_dashboard_deployment.py
- Modify: README.md

**Interfaces:**
- Produces: user-activity deadline independent of auth refresh events
- Produces: validate_email_otp_configuration(config: Mapping[str, object]) -> dict[str, object]
- Consumes: six-digit Supabase email OTP using the Token template variable

- [ ] **Step 1: Write failing browser tests**

~~~tsx
it("does not extend the privacy deadline when the auth token refreshes", async () => {
  vi.useFakeTimers();
  render(<AuthProvider client={authClient} inactivityMs={30 * 60_000}><Screen /></AuthProvider>);
  emitAuth("TOKEN_REFRESHED", refreshedSession);
  await vi.advanceTimersByTimeAsync(30 * 60_000);
  expect(screen.getByText(/privacy lock activated/i)).toBeVisible();
});

it("accepts exactly six numeric OTP characters", async () => {
  await user.type(screen.getByLabelText(/six-digit code/i), "12345678");
  expect(screen.getByLabelText(/six-digit code/i)).toHaveValue("123456");
});
~~~

Run: npm test --workspace @stocks-agent/web -- --run apps/web/src/auth/auth.test.tsx

- [ ] **Step 2: Implement an activity-only deadline**

Store lastActivityAt in a ref. Only pointerdown, keydown, and focus update it. Auth-state refresh may update the session but cannot reset the deadline. Schedule the remaining duration and lock immediately when the deadline is already past.

- [ ] **Step 3: Write failing Auth configuration tests**

~~~python
def test_email_otp_config_requires_token_template_and_six_digits():
    with pytest.raises(RuntimeError, match="six-digit"):
        validate_email_otp_configuration({
            "mailer_otp_length": 8,
            "mailer_templates_magic_link_content": "{{ .ConfirmationURL }}",
        })
~~~

Run: .venv/bin/python -m pytest -q tests/test_provision_owner_dashboard_auth.py tests/test_verify_owner_dashboard_deployment.py

- [ ] **Step 4: Implement fail-closed configuration verification**

The verifier accepts only otp length 6 and a magic-link email body containing the Supabase Token variable; it rejects ConfirmationURL-only templates. Keep owner enumeration responses neutral in the browser. Document the exact dashboard setting without printing or storing the owner email or secrets.

- [ ] **Step 5: Verify focused behavior and commit**

Run: npm test --workspace @stocks-agent/web -- --run apps/web/src/auth/auth.test.tsx
Run: .venv/bin/python -m pytest -q tests/test_provision_owner_dashboard_auth.py tests/test_verify_owner_dashboard_deployment.py
Run: git add apps/web/src/auth/AuthProvider.tsx apps/web/src/auth/SignInPage.tsx apps/web/src/auth/auth.test.tsx scripts/provision_owner_dashboard_auth.py tests/test_provision_owner_dashboard_auth.py scripts/verify_owner_dashboard_deployment.py tests/test_verify_owner_dashboard_deployment.py README.md && git commit -m "fix: enforce owner email otp contract"

---

### Task 3: Enforce executable-price and portfolio-wide policy

**Files:**
- Modify: supabase/functions/market-briefing-gateway/_shared/policy.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/policy_test.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/contracts.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/contracts_test.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/handler.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/handler_test.ts

**Interfaces:**
- Produces: evaluateCandidate(candidate, context, reservations) with current-price sizing
- Produces: reservePortfolioPlan(evaluations, context) returning approved, alternatives, and reason codes
- Consumes: reconciled cash availability, holdings, allocation targets, verified quote, and earlier reservations

- [ ] **Step 1: Write failing current-price policy tests**

~~~typescript
Deno.test("entry above zone and target is never actionable", () => {
  const result = evaluateCandidate(candidate({
    entryLow: 45, entryHigh: 47.02, stop: 42, target: 58,
    verifiedPrice: 70, proposedShares: 10,
  }), context());
  assertEquals(result.final_action, "watch");
  assert(result.reason_codes.includes("ENTRY_TRIGGER_NOT_MET"));
});
~~~

Run: npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/policy_test.ts

- [ ] **Step 2: Implement current-price validation**

For actionable entry, require entryLow <= verifiedPrice <= entryHigh and verifiedPrice < target. Calculate position cost, price-to-stop loss, and reward/risk from verifiedPrice. Invalid conditions downgrade to watch or insufficient; they never render ENTRY TRIGGER.

- [ ] **Step 3: Write failing aggregate-plan tests**

~~~typescript
Deno.test("six individually valid purchases cannot exceed the growth allocation", async () => {
  const response = await evaluateBundle(sixPurchases(1500), portfolio({
    totalValue: 40000, monthlyContribution: 500, growthTarget: 8100,
  }));
  assertLessOrEqual(totalApprovedCost(response), 8100);
  assert(response.evaluations.some((item) => item.reason_codes.includes("PORTFOLIO_BUDGET_EXCEEDED")));
});
~~~

Run: npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/handler_test.ts

- [ ] **Step 4: Implement aggregate reservations**

Evaluate candidates deterministically in ranked order, reserving spend and risk after each approval. When cash is unavailable, no purchase is actionable. Label mutually exclusive proposals as alternatives. Apply available-share caps to reduce and sell.

- [ ] **Step 5: Verify focused behavior and commit**

Run: npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/policy_test.ts supabase/functions/market-briefing-gateway/_shared/contracts_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts
Run: git add supabase/functions/market-briefing-gateway/_shared/policy.ts supabase/functions/market-briefing-gateway/_shared/policy_test.ts supabase/functions/market-briefing-gateway/_shared/contracts.ts supabase/functions/market-briefing-gateway/_shared/contracts_test.ts supabase/functions/market-briefing-gateway/_shared/handler.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts && git commit -m "fix: enforce executable portfolio policy"

---

### Task 4: Bind evidence and reports to final policy

**Files:**
- Modify: supabase/functions/market-briefing-gateway/_shared/contracts.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/contracts_test.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/policy.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/policy_test.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/reports.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/reports_test.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/handler.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/handler_test.ts
- Modify: sql/migrations/20260907_market_intelligence.sql
- Modify: sql/schema.sql

**Interfaces:**
- Produces: trusted evidence facts loaded by ID from the persisted packet
- Produces: renderReportDelivery(report, finalEvaluations) where actionable fields are derived
- Consumes: packet item timestamps/categories/relationships and persisted final decisions

- [ ] **Step 1: Write failing evidence tests**

~~~typescript
Deno.test("caller fresh label cannot make 2020 evidence current", () => {
  const result = evaluateCandidate(staleCandidateMarkedFresh(), currentContext());
  assertEquals(result.final_action, "insufficient");
  assert(result.reason_codes.includes("EVIDENCE_STALE"));
});
~~~

Run: npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/contracts_test.ts supabase/functions/market-briefing-gateway/_shared/policy_test.ts

- [ ] **Step 2: Derive evidence attributes from packet records**

Ignore caller freshness/status claims. Validate claim-required evidence categories, authoritative source, publication time, and relationship eligibility from stored packet facts. Keep contradictory facts in the policy input and surface a conflict reason instead of dropping them.

- [ ] **Step 3: Write failing report-authority and idempotency tests**

~~~typescript
Deno.test("report cannot publish a buy contradicted by watch decision", () => {
  const delivery = renderReportDelivery(contradictoryBuyReport(), [watchEvaluation()]);
  assertEquals(delivery.ready, false);
  assertEquals(delivery.reason, "REPORT_POLICY_MISMATCH");
});
~~~

Add a database test proving that same report kind, market date, and packet hash cannot be inserted with a different report UUID or arbitrary idempotency key.

- [ ] **Step 4: Implement derived report action and database idempotency**

Generate action, quantity, entry, stop, target, and urgency only from final evaluations. Derive the idempotency key from report kind, market date, packet hash, and canonical body hash. Validate all source, decision, and comparison IDs against persisted rows inside the RPC.

- [ ] **Step 5: Verify focused behavior and commit**

Run: npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/contracts_test.ts supabase/functions/market-briefing-gateway/_shared/policy_test.ts supabase/functions/market-briefing-gateway/_shared/reports_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts
Run: .venv/bin/python -m pytest -q tests/test_verify_market_intelligence_migration.py
Run: git add supabase/functions/market-briefing-gateway/_shared sql/migrations/20260907_market_intelligence.sql sql/schema.sql tests/test_verify_market_intelligence_migration.py && git commit -m "fix: bind reports to trusted policy evidence"

---

### Task 5: Repair portfolio decimal and backdated transaction semantics

**Files:**
- Modify: supabase/functions/owner-dashboard-api/mappers.ts
- Modify: supabase/functions/owner-dashboard-api/mappers_test.ts
- Create: sql/migrations/20260909_transaction_chronology.sql
- Modify: sql/schema.sql
- Modify: supabase/functions/telegram-portfolio/index.ts
- Modify: tests/test_verify_market_intelligence_migration.py
- Modify: tests/test_telegram_webhook_utils.mjs

**Interfaces:**
- Produces: decimal parsing that accepts PostgreSQL numeric precision and rounds at display boundaries
- Produces: record_portfolio_transaction rejection receipt TRANSACTION_OUT_OF_ORDER
- Consumes: latest transaction date for the same ticker

- [ ] **Step 1: Write failing mapper test**

~~~typescript
Deno.test("repeating decimal cost basis remains available", () => {
  const view = mapPortfolio([{ ticker: "ABC", shares: "3", avg_cost: "100.6666666666666667", current_price: "105" }]);
  assertEquals(view.summary.costBasis, 302);
  assertEquals(view.summary.unrealizedProfit, 13);
  assertEquals(view.summary.incomplete, false);
});
~~~

Run: npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/owner-dashboard-api/mappers_test.ts

- [ ] **Step 2: Implement consistent fixed-point parsing**

Accept bounded PostgreSQL numeric strings, convert through the existing fixed-point utility, and round only at the API display contract. Invalid basis sets costBasis and unrealizedProfit unavailable and marks the summary incomplete.

- [ ] **Step 3: Write failing chronology tests**

Add a migration/RPC fixture for buy 10 at 100 on September 1, sell 5 at 110 on September 3, then late buy 10 at 200 on September 2. The third write must return TRANSACTION_OUT_OF_ORDER and leave the prior +50 realized result unchanged.

- [ ] **Step 4: Implement quarantine/rejection**

The transaction RPC checks the latest existing transaction date for the ticker under the same lock. Earlier transactions are recorded only in a non-authoritative correction request or rejected with a deterministic receipt; they cannot update holdings. Telegram must tell the owner that reconciliation is required.

- [ ] **Step 5: Verify focused behavior and commit**

Run: npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/owner-dashboard-api/mappers_test.ts
Run: node --test tests/test_telegram_webhook_utils.mjs
Run: .venv/bin/python -m pytest -q tests/test_verify_market_intelligence_migration.py
Run: git add supabase/functions/owner-dashboard-api/mappers.ts supabase/functions/owner-dashboard-api/mappers_test.ts sql/migrations/20260909_transaction_chronology.sql sql/schema.sql supabase/functions/telegram-portfolio/index.ts tests/test_verify_market_intelligence_migration.py tests/test_telegram_webhook_utils.mjs && git commit -m "fix: preserve portfolio accounting integrity"

---

### Task 6: Add durable publication and command delivery states

**Files:**
- Create: sql/migrations/20260910_delivery_outbox.sql
- Modify: sql/schema.sql
- Modify: supabase/functions/market-briefing-gateway/_shared/repository.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/repository_test.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/handler.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/handler_test.ts
- Modify: supabase/functions/telegram-portfolio/index.ts
- Modify: tests/test_telegram_webhook_utils.mjs

**Interfaces:**
- Produces: durable states pending, delivered, failed, uncertain, suppressed
- Produces: claim/recover publication receipt by deterministic idempotency key
- Consumes: original Telegram message IDs and committed portfolio command ID

- [ ] **Step 1: Write failing crash-boundary tests**

~~~typescript
Deno.test("retry after stored report recovers original delivery receipt", async () => {
  const first = await publishWithCrashAfterSend();
  const retry = await retrySameRequest(first.requestId);
  assertEquals(retry.status, "uncertain");
  assertEquals(retry.retry_allowed, false);
});
~~~

Add a Telegram command test where the database commits and message editing fails. The response must say the change was recorded and delivery acknowledgement is uncertain; it must never say nothing changed.

- [ ] **Step 2: Add outbox persistence**

Persist the operation and pending state before the send. Store message IDs only on delivered. Store definite API rejection as failed and network/timeout ambiguity as uncertain. Same-key retry returns the existing state and never resends uncertain or delivered work.

- [ ] **Step 3: Route one publication authority**

Prevent both standard evaluation and report publication routes from sending the same briefing. The deterministic report key owns periodic report delivery; alert-only events retain their existing separate key space.

- [ ] **Step 4: Verify focused behavior and commit**

Run: npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts
Run: node --test tests/test_telegram_webhook_utils.mjs
Run: .venv/bin/python -m pytest -q tests/test_verify_market_intelligence_migration.py
Run: git add sql/migrations/20260910_delivery_outbox.sql sql/schema.sql supabase/functions/market-briefing-gateway/_shared/repository.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts supabase/functions/telegram-portfolio/index.ts tests/test_telegram_webhook_utils.mjs tests/test_verify_market_intelligence_migration.py && git commit -m "fix: persist delivery uncertainty"

---

### Task 7: Make provider evidence discoverable without losing contradictions

**Files:**
- Modify: lib/intelligence/providers/__init__.py
- Modify: lib/intelligence/providers/alpha_vantage.py
- Modify: lib/intelligence/providers/finnhub.py
- Modify: lib/intelligence/providers/gdelt.py
- Modify: lib/intelligence/providers/official.py
- Modify: lib/intelligence/pipeline.py
- Modify: lib/intelligence/dedupe.py
- Modify: tests/test_intelligence_providers.py
- Modify: tests/test_intelligence_pipeline.py
- Modify: tests/test_intelligence_dedupe.py

**Interfaces:**
- Produces: normalized evidence with ticker/entity IDs, item URL, request URL, published/retrieved/effective/reporting timestamps
- Produces: discovery_status values qualified, no_event, insufficient_coverage
- Consumes: provider-specific query identifiers rather than generic theme strings

- [ ] **Step 1: Write failing adapter-to-discovery tests**

For every declared provider, use a realistic complete response fixture and assert at least one normalized evidence item reaches entity/security discovery or returns an attributable unsupported-query failure. Tests must not inject ticker metadata after parsing.

Run: .venv/bin/python -m pytest -q tests/test_intelligence_providers.py tests/test_intelligence_pipeline.py

- [ ] **Step 2: Implement provider-specific query and identity mapping**

Separate request URL from item canonical URL. Populate stable upstream ID, entity/security identifiers, and all distinct time fields. SEC and macro queries use CIK or series IDs supplied by versioned configuration; unsupported abstract queries fail before HTTP admission.

- [ ] **Step 3: Write failing contradiction and time tests**

~~~python
def test_affirmation_and_negated_correction_are_not_near_duplicates():
    kept = deduplicate([affirmed_contract(), denied_contract()])
    assert [item.claim_polarity for item in kept] == ["affirmed", "denied"]

def test_new_filing_about_prior_period_is_retained():
    assert accept(newly_published_prior_period_filing(), current_window()) is True
~~~

- [ ] **Step 4: Implement semantic-preserving deduplication and time filtering**

Exact item identity may deduplicate; near-text clustering keeps contradictory polarity and all source references. Filter collection by publication/retrieval time, not effective or reporting-period time. Future-effective and prior-period facts stay labeled.

- [ ] **Step 5: Verify focused behavior and commit**

Run: .venv/bin/python -m pytest -q tests/test_intelligence_providers.py tests/test_intelligence_pipeline.py tests/test_intelligence_dedupe.py
Run: git add lib/intelligence/providers lib/intelligence/pipeline.py lib/intelligence/dedupe.py tests/test_intelligence_providers.py tests/test_intelligence_pipeline.py tests/test_intelligence_dedupe.py && git commit -m "fix: preserve discoverable provider evidence"

---

### Task 8: Persist cache/quota usage and remove ranking placeholders

**Files:**
- Modify: lib/intelligence/cache.py
- Modify: lib/intelligence/quota.py
- Modify: lib/intelligence/pipeline.py
- Modify: lib/intelligence/ranking.py
- Modify: tests/test_intelligence_http.py
- Modify: tests/test_intelligence_quota.py
- Modify: tests/test_intelligence_pipeline.py
- Modify: tests/test_intelligence_ranking.py
- Modify: scripts/collect_market_intelligence.py
- Modify: tests/test_collect_market_intelligence.py

**Interfaces:**
- Produces: resumable cache keys provider/query/window/schema-version
- Produces: persisted actual-request quota receipts
- Produces: ranking inputs Optional values for recency, liquidity, portfolio relevance, and holding weight
- Consumes: the scheduled analysis run ID supplied by --run-id

- [ ] **Step 1: Write failing retry and run-binding tests**

~~~python
def test_retry_reuses_completed_http_work_and_counts_each_real_request():
    first = pipeline.run(request_id=RUN_ID)
    second = pipeline.run(request_id=RUN_ID)
    assert transport.calls == first.actual_requests
    assert second.cache_hits == first.actual_requests

def test_collector_reuses_supplied_analysis_run_id():
    assert parse_args(["--run-id", RUN_ID]).run_id == RUN_ID
~~~

- [ ] **Step 2: Implement durable cache and exact run binding**

The collector requires --run-id for scheduled phases and uses it for the intelligence run and packet. Cache entries retain source receipts. Quota consumption increments only for actual HTTP calls, and a duplicate request resumes completed work without another reservation or call.

- [ ] **Step 3: Write failing unknown/concentration ranking tests**

~~~python
def test_missing_liquidity_and_high_concentration_cannot_qualify():
    result = rank(candidate(liquidity=None, holding_weight=0.42))
    assert result.status == "insufficient"
~~~

- [ ] **Step 4: Replace neutral placeholders**

Pass actual timestamps, validated liquidity, current portfolio weight, overlap, and concentration into ranking. Unknown required inputs yield insufficient with reason codes. Persist comparison IDs and learning inputs instead of requiring them to remain empty.

- [ ] **Step 5: Verify focused behavior and commit**

Run: .venv/bin/python -m pytest -q tests/test_intelligence_http.py tests/test_intelligence_quota.py tests/test_intelligence_pipeline.py tests/test_intelligence_ranking.py tests/test_collect_market_intelligence.py
Run: git add lib/intelligence/cache.py lib/intelligence/quota.py lib/intelligence/pipeline.py lib/intelligence/ranking.py scripts/collect_market_intelligence.py tests/test_intelligence_http.py tests/test_intelligence_quota.py tests/test_intelligence_pipeline.py tests/test_intelligence_ranking.py tests/test_collect_market_intelligence.py && git commit -m "fix: make intelligence runs resumable"

---

### Task 9: Enforce scheduled lifecycle and bounded relevant history

**Files:**
- Create: sql/migrations/20260911_scheduled_run_lifecycle.sql
- Modify: sql/schema.sql
- Modify: supabase/functions/market-briefing-gateway/_shared/repository.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/repository_test.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/handler.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/handler_test.ts
- Modify: scripts/verify_owner_dashboard_deployment.py
- Modify: tests/test_verify_owner_dashboard_deployment.py

**Interfaces:**
- Produces: unique scheduled slot market_date plus phase
- Produces: finish_run validation against required stage receipts
- Produces: overdue phase query/receipt
- Consumes: bounded unresolved records plus newest relevant completed history

- [ ] **Step 1: Write failing lifecycle tests**

Add tests that more than 100 historical suggestions do not break context, two scheduled starts for one slot return one run, and finish_run rejects a run with no collection/packet/evaluation/report/publication-or-suppression receipts.

Run: npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts

- [ ] **Step 2: Implement bounded relevant queries and slot uniqueness**

Always include unresolved/pending records, then fill the remaining bound with newest relevant completed records. Add a database uniqueness constraint for scheduled market_date and phase while retaining request UUID idempotency.

- [ ] **Step 3: Implement stage-aware completion and overdue visibility**

finish_run checks required receipts by phase and returns a specific missing-stage error. The read-only verification source reports scheduled phases that passed their deadline without a completed or explicitly suppressed run.

- [ ] **Step 4: Verify focused behavior and commit**

Run: npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts
Run: .venv/bin/python -m pytest -q tests/test_verify_owner_dashboard_deployment.py tests/test_verify_market_intelligence_migration.py
Run: git add sql/migrations/20260911_scheduled_run_lifecycle.sql sql/schema.sql supabase/functions/market-briefing-gateway/_shared/repository.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts scripts/verify_owner_dashboard_deployment.py tests/test_verify_owner_dashboard_deployment.py tests/test_verify_market_intelligence_migration.py && git commit -m "fix: enforce scheduled run lifecycle"

---

### Task 10: Rebuild release verification and zero-cost recovery evidence

**Files:**
- Modify: scripts/verify_personal_stock_agent_v1.py
- Modify: tests/test_verify_personal_stock_agent_v1.py
- Modify: scripts/verify_owner_dashboard_deployment.py
- Modify: tests/test_verify_owner_dashboard_deployment.py
- Modify: scripts/deploy_owner_dashboard_api.py
- Modify: tests/test_deploy_owner_dashboard_api.py
- Create: scripts/export_recovery_bundle.py
- Create: scripts/verify_recovery_bundle.py
- Create: tests/test_recovery_bundle.py
- Modify: README.md

**Interfaces:**
- Produces: verifier that recomputes source/artifact hashes and binds exact candidate receipts
- Produces: encrypted-export manifest without secrets or plaintext portfolio data
- Consumes: scheduled post-merge run, original publication receipt, CI/deploy SHA, rollback receipt

- [ ] **Step 1: Write failing false-receipt tests**

Reuse the complete receipt fixture but make it old, on-demand, dry-run, duplicate, and without original Telegram IDs. verify_release must reject each mutation. Replace a stored source body while keeping its claimed hash; source reconciliation must reject it.

Run: .venv/bin/python -m pytest -q tests/test_verify_personal_stock_agent_v1.py tests/test_verify_owner_dashboard_deployment.py

- [ ] **Step 2: Implement independently derived verification**

Require normally scheduled phase, freshness after merge, exact CI/deployed/source SHA, required stored stages, recomputed hashes from retained canonical content, and original delivery IDs or explicit suppression. Typed gate objects must contain evidence fields; status labels alone are insufficient.

- [ ] **Step 3: Write failing rollback and recovery tests**

Test that a dashboard deployment failure after gateway deployment restores the captured prior gateway version. Test that a recovery export manifest covers holdings, transactions, commands, runs, packets, reports, publications, roles, and schema version while excluding secret values.

- [ ] **Step 4: Implement rollback and recovery tooling**

Use the existing protected rollback artifact for every failure after the gateway changes. Export canonical newline-delimited data, hash each file, encrypt only through a caller-supplied zero-cost local command boundary, and verify an isolated restored database against the manifest. Never add cloud storage or paid backup dependencies.

- [ ] **Step 5: Verify focused behavior and commit**

Run: .venv/bin/python -m pytest -q tests/test_verify_personal_stock_agent_v1.py tests/test_verify_owner_dashboard_deployment.py tests/test_deploy_owner_dashboard_api.py tests/test_recovery_bundle.py
Run: git add scripts/verify_personal_stock_agent_v1.py tests/test_verify_personal_stock_agent_v1.py scripts/verify_owner_dashboard_deployment.py tests/test_verify_owner_dashboard_deployment.py scripts/deploy_owner_dashboard_api.py tests/test_deploy_owner_dashboard_api.py scripts/export_recovery_bundle.py scripts/verify_recovery_bundle.py tests/test_recovery_bundle.py README.md && git commit -m "fix: require receipt-backed release recovery"

---

### Task 11: Correct outcomes, privileges, dependencies, and market sessions

**Files:**
- Modify: supabase/functions/market-briefing-gateway/_shared/outcomes.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/outcomes_test.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/repository.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/repository_test.ts
- Modify: lib/weekly_audit.py
- Modify: tests/test_weekly_audit.py
- Modify: scripts/weekly_audit_packet.py
- Modify: tests/test_security_invariants.py
- Create: requirements.lock
- Modify: .github/workflows/owner-dashboard-ci.yml
- Modify: supabase/functions/market-briefing-gateway/_shared/market-calendar.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/market-calendar_test.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/market-data.ts
- Modify: supabase/functions/market-briefing-gateway/_shared/market-data_test.ts

**Interfaces:**
- Produces: recommendation-level loss streaks and aligned outcome windows
- Produces: restricted weekly-audit database client
- Produces: reproducible hashed Python dependency installation
- Produces: session-specific closes and quote identity checks

- [ ] **Step 1: Write failing outcome and veto tests**

One recommendation with losing 5/21/63 horizons counts as one loss, not three. Benchmark and stock start on the same timestamp. Excursions use session high/low. A veto-only week contains its evaluation and nonzero veto summary.

- [ ] **Step 2: Implement outcome semantics**

Key streaks by recommendation ID, align benchmark eligibility, use high/low observations for MFE/MAE, distinguish actual fill status, and preserve every veto in weekly filtering.

- [ ] **Step 3: Write failing privilege, dependency, calendar, and quote tests**

Verify weekly audit cannot mutate tables; CI installs only hashes from requirements.lock; November 27 and December 24, 2026 close at 13:00 America/New_York; wrong symbol or non-USD quote is unavailable.

- [ ] **Step 4: Implement the remaining controls**

Use a restricted read role for weekly audit. Generate a complete locked dependency set with hashes and install it with hash enforcement. Represent early closes and maintained calendar coverage. Validate returned symbol/currency and expose unknown halt/spread/liquidity as actionable-price unavailable.

- [ ] **Step 5: Verify focused behavior and commit**

Run: npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/outcomes_test.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/market-calendar_test.ts supabase/functions/market-briefing-gateway/_shared/market-data_test.ts
Run: .venv/bin/python -m pytest -q tests/test_weekly_audit.py tests/test_security_invariants.py
Run: git add supabase/functions/market-briefing-gateway/_shared/outcomes.ts supabase/functions/market-briefing-gateway/_shared/outcomes_test.ts supabase/functions/market-briefing-gateway/_shared/repository.ts supabase/functions/market-briefing-gateway/_shared/repository_test.ts lib/weekly_audit.py tests/test_weekly_audit.py scripts/weekly_audit_packet.py tests/test_security_invariants.py requirements.lock .github/workflows/owner-dashboard-ci.yml supabase/functions/market-briefing-gateway/_shared/market-calendar.ts supabase/functions/market-briefing-gateway/_shared/market-calendar_test.ts supabase/functions/market-briefing-gateway/_shared/market-data.ts supabase/functions/market-briefing-gateway/_shared/market-data_test.ts && git commit -m "fix: close remaining audit controls"

---

### Task 12: Reconcile documentation and run one final integration gate

**Files:**
- Modify: PROJECT_STATUS.md
- Modify: docs/ROADMAP.md
- Create: docs/reviews/2026-09-04-astra-audit-remediation-review.md
- Create: docs/rollouts/2026-09-04-astra-audit-remediation.md

**Interfaces:**
- Produces: truthful status that reopens C2-C6 until protected rollout and scheduled receipts pass
- Consumes: all task commits and task-review verdicts

- [ ] **Step 1: Update status before verification**

Record each Astra F1-F19 disposition, the pending production Auth-template action, the fact that local implementation does not prove deployment, and the exact remaining protected gates. Remove the false statement that scheduled receipts are the only blocker.

- [ ] **Step 2: Run the consolidated local gate exactly once**

Run: npm run test:all

Capture exact pass/fail counts, skipped tests, commit SHA, and dirty state. If it fails, fix only the demonstrated failure through a focused red-green cycle and re-run the smallest affected command before re-running the consolidated gate once.

- [ ] **Step 3: Run the final whole-branch review**

Review the full diff from origin/main through HEAD against the remediation spec. Critical or Important findings are fixed in one bounded fix wave and one scoped re-review.

- [ ] **Step 4: Write review and rollout records**

The records distinguish local implementation, exact-head CI, production deployment, live Auth configuration, restore drill, and scheduled receipt evidence. Unknown or pending items remain explicitly pending.

- [ ] **Step 5: Commit documentation**

Run: git add PROJECT_STATUS.md docs/ROADMAP.md docs/reviews/2026-09-04-astra-audit-remediation-review.md docs/rollouts/2026-09-04-astra-audit-remediation.md docs/superpowers/specs/2026-09-04-astra-audit-remediation-design.md docs/superpowers/plans/2026-09-04-astra-audit-remediation.md && git commit -m "docs: record astra audit remediation"
