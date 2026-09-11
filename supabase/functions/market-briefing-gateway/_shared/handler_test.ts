import type {
  AlertRuleSnapshot,
  EvidencePacket,
  GatewayEnvelope,
  GatewayReadContext,
  Phase,
  PolicyConfig,
  TrustedEvidenceFact,
  VerifiedQuote,
} from "./contracts.ts";
import { createGatewayHandler } from "./handler.ts";
import type {
  AlertEventReceipt,
  AlertWork,
  ArtifactReceipt,
  GatewayRepository,
  GatewayRequestClaim,
  PersistableArtifactMutationBatch,
  PersistedBundle,
  PublicationClaim,
  PublicationReceipt,
  RunReceipt,
} from "./repository.ts";
import { GatewayRepositoryError } from "./repository.ts";
import { TelegramDeliveryError } from "./telegram.ts";
import type { DueDecision, OutcomeGrade } from "./outcomes.ts";
import type { AdjustedBar, IntradayQuoteEvidence } from "./market-data.ts";
import { canonicalJson, sha256Hex } from "./intelligence.ts";
import {
  reportIdFromKey,
  type ReportKind,
  type ReportPolicyDecision,
} from "./reports.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
  }
}

const SECRET = "test-market-secret-with-enough-entropy";
const NOW = new Date("2026-09-02T17:00:00.000Z");
const RUN_ID = "00000000-0000-4000-8000-000000000002";

function reportFixture(kind: ReportKind = "morning") {
  const report = {
    title: "Caller title",
    summary: "BUY CENX 999999 shares immediately",
    full_markdown: "Caller BUY 999999",
    source_ids: ["00000000-0000-4000-8000-000000000031"],
    policy_decision_ids: ["00000000-0000-4000-8000-000000000032"],
    comparison_ids: [],
    actionable_risk: true,
    material_thesis_change: false,
    intraday_triggered: true,
    suggestion_only: true,
  };
  const report_hash = sha256Hex(canonicalJson(report));
  const key = sha256Hex(`v2:${kind}:2026-09-02:${PACKET_HASH}:${report_hash}`);
  return {
    id: reportIdFromKey(key),
    idempotency_key: key,
    packet_id: PACKET_ID,
    market_date: "2026-09-02",
    kind,
    report,
    report_hash,
    rendered_text: report.full_markdown,
    rendered_hash: sha256Hex(report.full_markdown),
  };
}

function approvedReportDecision(
  payload: ReturnType<typeof reportFixture>,
): ReportPolicyDecision {
  return {
    evaluation_id: payload.report.policy_decision_ids[0],
    candidate_id: "00000000-0000-4000-8000-000000000033",
    run_id: RUN_ID,
    packet_id: PACKET_ID,
    packet_hash: PACKET_HASH,
    ticker: "CENX",
    status: "approved",
    final_action: "buy",
    final_alert_urgency: null,
    approved_terms: {
      quantity: "10",
      entry_low: "45",
      entry_high: "47.02",
      stop: "42",
      target: "58",
      urgency: "routine",
    },
  };
}

Deno.test("suppressed report retains the typed policy reason without a Telegram send", async () => {
  const repo = new FakeRepository();
  const payload = reportFixture("intraday");
  repo.reportDecisions = [{
    ...approvedReportDecision(payload),
    status: "downgraded",
    final_action: "watch",
    approved_terms: null,
  }];
  const setup = makeHandler(repo);
  const result = await setup.handler(request("record_report", payload));
  assertEquals(result.status, 200);
  const body = await result.json();
  assertEquals(body.publication_receipt.suppression_reason, "no_trigger");
  assertEquals(repo.suppressionReasons, ["no_trigger"]);
  assertEquals(setup.sent.length, 0);
  assert(
    repo.storedReport !== null &&
      !JSON.stringify(repo.storedReport).includes("999999") &&
      JSON.stringify(repo.storedReport).includes("WATCH"),
    "dashboard-visible suppressed report retained caller advice",
  );
});

Deno.test("report handler loads exact persisted decisions before generating delivery and stored prose", async () => {
  const repo = new FakeRepository();
  const payload = reportFixture();
  repo.reportDecisions = [{
    evaluation_id: payload.report.policy_decision_ids[0],
    candidate_id: "00000000-0000-4000-8000-000000000033",
    run_id: RUN_ID,
    packet_id: PACKET_ID,
    packet_hash: PACKET_HASH,
    ticker: "CENX",
    status: "approved",
    final_action: "buy",
    final_alert_urgency: null,
    approved_terms: {
      quantity: "10",
      entry_low: "45",
      entry_high: "47.02",
      stop: "42",
      target: "58",
      urgency: "routine",
    },
  }];
  const setup = makeHandler(repo);
  const response = await setup.handler(request("record_report", payload));
  assertEquals(response.status, 200);
  assertEquals(repo.reportDecisionReads, [{
    runId: RUN_ID,
    packetId: PACKET_ID,
    ids: payload.report.policy_decision_ids,
  }]);
  assertEquals(setup.sent.length, 1);
  assert(
    setup.sent[0][0].includes("47.02") && !setup.sent[0][0].includes("999999"),
    "delivery ignored stored terms",
  );
  assert(
    !JSON.stringify(repo.storedReport).includes("999999"),
    "raw prose retained in report",
  );
});

Deno.test("scheduled Friday research persists a report and delivers an owner status without a fake evaluation", async () => {
  const packet = researchOnlyPacket();
  class ResearchOnlyRepository extends FakeRepository {
    override loadIntelligencePacket() {
      this.packetReadCalls += 1;
      return Promise.resolve({
        id: PACKET_ID,
        run_id: RUN_ID,
        content_hash: sha256Hex(canonicalJson(packet)),
        packet,
        evidence_facts: [],
        exposure_facts: [],
      });
    }
  }
  const repo = new ResearchOnlyRepository();
  repo.scheduledReportPhase = "post-market";
  const payload = researchOnlyReportFixture(packet, "weekly", "2026-09-04");
  const setup = makeHandler(repo, {
    dashboardBaseUrl: "https://stocks.example.test",
    dashboardAllowedOrigins: ["https://stocks.example.test"],
  });

  const response = await setup.handler(request("record_report", payload));

  assertEquals(response.status, 200);
  const result = await json(response);
  assertEquals(result.publication_receipt, {
    status: "delivered",
    telegram_message_ids: [77],
    retry_allowed: false,
  });
  assertEquals(repo.reportDecisionReads, []);
  assertEquals(repo.packetReadCalls, 1);
  assertEquals(setup.sent.length, 1);
  assert(
    setup.sent[0][0].includes("🌙 <b>FRIDAY EOD — Sep 4</b>") &&
      setup.sent[0][0].includes("1 research candidate(s) reviewed") &&
      setup.sent[0][0].includes("No policy-approved action") &&
      setup.sent[0][0].includes(">View full audit</a>") &&
      !setup.sent[0][0].includes("unresolved:magnet-supplier") &&
      !setup.sent[0][0].includes("fabricated ticker"),
    "Friday Telegram status leaked unresolved research or omitted the no-action result",
  );
  assertEquals(repo.reportOrigins.length, 1);
  assert(
    JSON.stringify(repo.storedReport).includes("unresolved:magnet-supplier") &&
      !JSON.stringify(repo.storedReport).includes("fabricated ticker") &&
      !JSON.stringify(repo.storedReport).includes("999999"),
    "research report did not retain canonical packet state",
  );
});

Deno.test("scheduled pre-market research sends a morning no-action Telegram receipt", async () => {
  const packet = researchOnlyPacket();
  class ResearchOnlyRepository extends FakeRepository {
    override loadIntelligencePacket() {
      this.packetReadCalls += 1;
      return Promise.resolve({
        id: PACKET_ID,
        run_id: RUN_ID,
        content_hash: sha256Hex(canonicalJson(packet)),
        packet,
        evidence_facts: [],
        exposure_facts: [],
      });
    }
  }
  const repo = new ResearchOnlyRepository();
  repo.scheduledReportPhase = "pre-market";
  const payload = researchOnlyReportFixture(packet, "morning");
  const setup = makeHandler(repo);

  const response = await setup.handler(request("record_report", payload));

  assertEquals(response.status, 200);
  const result = await json(response);
  assertEquals(result.publication_receipt, {
    status: "delivered",
    telegram_message_ids: [77],
    retry_allowed: false,
  });
  assertEquals(result.telegram_message_ids, [77]);
  assertEquals(setup.sent.length, 1);
  assert(
    setup.sent[0][0].includes("🌅 <b>MORNING CHECK — Sep 2</b>") &&
      setup.sent[0][0].includes("No policy-approved action") &&
      setup.sent[0][0].includes("Suggestion only") &&
      !setup.sent[0][0].includes("fabricated ticker"),
    "morning Telegram did not use the canonical no-action copy",
  );
  const stored = repo.storedReport as {
    rendered_text: string;
    rendered_hash: string;
  };
  assertEquals(stored.rendered_text, setup.sent[0][0]);
  assertEquals(stored.rendered_hash, sha256Hex(setup.sent[0][0]));
});

Deno.test("unscheduled pre-market-shaped research cannot send a morning status", async () => {
  const packet = researchOnlyPacket();
  class ResearchOnlyRepository extends FakeRepository {
    override loadIntelligencePacket() {
      this.packetReadCalls += 1;
      return Promise.resolve({
        id: PACKET_ID,
        run_id: RUN_ID,
        content_hash: sha256Hex(canonicalJson(packet)),
        packet,
        evidence_facts: [],
        exposure_facts: [],
      });
    }
  }
  const repo = new ResearchOnlyRepository();
  const payload = researchOnlyReportFixture(packet, "morning");
  const setup = makeHandler(repo);

  const response = await setup.handler(request("record_report", payload));

  assertEquals(response.status, 200);
  const result = await json(response);
  const publication = result.publication_receipt as Record<string, unknown>;
  assertEquals(publication.status, "suppressed");
  assertEquals(publication.suppression_reason, "not_actionable");
  assertEquals(setup.sent, []);
});

Deno.test("v2 research evaluation receipt returns packet sources without manufacturing decisions", async () => {
  const packet = researchOnlyPacket();
  const packetHash = sha256Hex(canonicalJson(packet));
  class ResearchOnlyRepository extends FakeRepository {
    override loadIntelligencePacket() {
      this.packetReadCalls += 1;
      return Promise.resolve({
        id: PACKET_ID,
        run_id: RUN_ID,
        content_hash: packetHash,
        packet,
        evidence_facts: [],
        exposure_facts: [],
      });
    }
  }
  const repo = new ResearchOnlyRepository();
  const setup = makeHandler(repo);

  const response = await setup.handler(request("evaluate_and_publish", {
    phase: "post-market",
    market_date: "2026-09-02",
    title: "Bounded research",
    candidates: [],
    intelligence_packet: {
      id: PACKET_ID,
      content_hash: packetHash,
      coverage: "partial",
    },
  }));

  assertEquals(response.status, 200);
  const result = await json(response);
  assertEquals(result.policy_decision_ids, []);
  assertEquals(result.source_ids, [
    "00000000-0000-4000-8000-000000000031",
  ]);
  assertEquals(result.telegram_message_ids, []);
  assertEquals(setup.sent, []);
});

Deno.test("stored report delivery becomes uncertain after a send crash and same report key is never resent", async () => {
  const repo = new FakeRepository();
  const payload = reportFixture();
  repo.reportDecisions = [approvedReportDecision(payload)];
  const first = makeHandler(repo, {
    sendTelegram: () =>
      Promise.reject(new TelegramDeliveryError("ambiguous", [77])),
  });
  const firstResponse = await first.handler(request("record_report", payload));
  assertEquals(firstResponse.status, 502);
  assertEquals((await json(firstResponse)).publication_receipt, {
    status: "uncertain",
    telegram_message_ids: [],
    retry_allowed: false,
  });

  const retry = makeHandler(repo);
  const retryResponse = await retry.handler(request("record_report", payload));
  assertEquals(retryResponse.status, 502);
  assertEquals((await json(retryResponse)).publication_receipt, {
    status: "uncertain",
    telegram_message_ids: [],
    retry_allowed: false,
  });
  assertEquals(retry.sent, []);
});

Deno.test("active report delivery leaves its gateway request retryable until its lease becomes uncertain", async () => {
  const repo = new FakeRepository();
  const payload = reportFixture();
  const requestId = "00000000-0000-4000-8000-000000000054";
  repo.reportDecisions = [approvedReportDecision(payload)];
  repo.reportPublicationClaimable = false;
  const first = makeHandler(repo);

  const pending = await first.handler(
    request("record_report", payload, { requestId }),
  );
  assertEquals(pending.status, 409);
  assertEquals(repo.claims.has(requestId), false);
  assertEquals(first.sent, []);

  repo.reportPublicationLeaseExpired = true;
  const retry = makeHandler(repo);
  const uncertain = await retry.handler(
    request("record_report", payload, { requestId }),
  );
  assertEquals(uncertain.status, 502);
  assertEquals((await json(uncertain)).publication_receipt, {
    status: "uncertain",
    telegram_message_ids: [],
    retry_allowed: false,
  });
  assertEquals(retry.sent, []);
});

Deno.test("report handler publishes an approved sizing-free urgent HOLD alert without caller trade prose", async () => {
  const repo = new FakeRepository();
  const payload = reportFixture();
  repo.reportDecisions = [{
    evaluation_id: payload.report.policy_decision_ids[0],
    candidate_id: "00000000-0000-4000-8000-000000000033",
    run_id: RUN_ID,
    packet_id: PACKET_ID,
    packet_hash: PACKET_HASH,
    ticker: "CENX",
    status: "approved",
    final_action: "hold",
    approved_terms: null,
    final_alert_urgency: "urgent",
  }];
  const setup = makeHandler(repo);
  const response = await setup.handler(request("record_report", payload));
  assertEquals(response.status, 200);
  assertEquals(setup.sent.length, 1);
  assert(
    setup.sent[0][0].includes("URGENT RESEARCH REVIEW") &&
      !setup.sent[0][0].includes("BUY") &&
      !setup.sent[0][0].includes("999999") &&
      !setup.sent[0][0].includes("shares"),
    "pure alert retained caller trade action or quantity prose",
  );
  assert(
    !JSON.stringify(repo.storedReport).includes("999999"),
    "stored canonical report retained caller trade prose",
  );
  assertEquals(
    (repo.storedReport as { kind: unknown }).kind,
    "urgent",
  );
});

Deno.test("report handler derives routine pure-HOLD alert kind and text without caller labels", async () => {
  const results: Array<{
    sent: string[];
    stored:
      | { kind: unknown; report: { title: unknown; summary: unknown } }
      | null;
  }> = [];
  for (const kind of ["morning", "urgent"] as const) {
    const repo = new FakeRepository();
    const payload = reportFixture(kind);
    repo.reportDecisions = [{
      evaluation_id: payload.report.policy_decision_ids[0],
      candidate_id: "00000000-0000-4000-8000-000000000033",
      run_id: RUN_ID,
      packet_id: PACKET_ID,
      packet_hash: PACKET_HASH,
      ticker: "CENX",
      status: "approved",
      final_action: "hold",
      approved_terms: null,
      final_alert_urgency: "routine",
    }];
    const setup = makeHandler(repo);
    const response = await setup.handler(request("record_report", payload));
    assertEquals(response.status, 200);
    results.push({
      sent: setup.sent.map(([body]) => body),
      stored: repo.storedReport as {
        kind: unknown;
        report: { title: unknown; summary: unknown };
      } | null,
    });
  }
  assertEquals(results.map((result) => result.sent.length), [1, 1]);
  for (const result of results) {
    assert(result.stored !== null, "routine alert was not persisted");
    assertEquals(result.stored!.kind, "intraday");
    assertEquals(result.stored!.report.title, "INTRADAY RESEARCH — 2026-09-02");
    assert(
      result.sent[0].includes("INTRADAY RESEARCH") &&
        result.sent[0].includes("POLICY-APPROVED ROUTINE ALERT") &&
        !result.sent[0].includes("BUY") &&
        !result.sent[0].includes("999999") &&
        !result.sent[0].includes("shares"),
      "routine alert retained caller labels or trade prose",
    );
  }
});

Deno.test("scheduled report origins retain requested kind before pre-market delivery derives urgent or intraday", async () => {
  const cases: Array<{ urgency: "urgent" | "routine"; finalKind: ReportKind }> =
    [
      { urgency: "urgent", finalKind: "urgent" },
      { urgency: "routine", finalKind: "intraday" },
    ];
  for (const expected of cases) {
    const repo = new FakeRepository();
    repo.scheduledReportPhase = "pre-market";
    const payload = reportFixture("morning");
    repo.reportDecisions = [{
      evaluation_id: payload.report.policy_decision_ids[0],
      candidate_id: "00000000-0000-4000-8000-000000000033",
      run_id: RUN_ID,
      packet_id: PACKET_ID,
      packet_hash: PACKET_HASH,
      ticker: "CENX",
      status: "approved",
      final_action: "hold",
      approved_terms: null,
      final_alert_urgency: expected.urgency,
    }];
    const setup = makeHandler(repo);
    assertEquals(
      (await setup.handler(request("record_report", payload))).status,
      200,
    );
    assertEquals(repo.reportOrigins.length, 1);
    assertEquals(repo.reportOrigins[0].runId, RUN_ID);
    assertEquals(repo.reportOrigins[0].marketDate, "2026-09-02");
    assertEquals(repo.reportOrigins[0].kind, "morning");
    assertEquals(repo.reportOrigins[0].phase, "pre-market");
    assertEquals(
      (repo.storedReport as { kind: unknown }).kind,
      expected.finalKind,
    );
  }
});

Deno.test("scheduled report origin permits post-market routine delivery to finish as intraday", async () => {
  const repo = new FakeRepository();
  repo.scheduledReportPhase = "post-market";
  const payload = reportFixture("weekly");
  repo.reportDecisions = [{
    evaluation_id: payload.report.policy_decision_ids[0],
    candidate_id: "00000000-0000-4000-8000-000000000033",
    run_id: RUN_ID,
    packet_id: PACKET_ID,
    packet_hash: PACKET_HASH,
    ticker: "CENX",
    status: "approved",
    final_action: "hold",
    approved_terms: null,
    final_alert_urgency: "routine",
  }];
  const setup = makeHandler(repo);
  assertEquals(
    (await setup.handler(request("record_report", payload))).status,
    200,
  );
  assertEquals(repo.reportOrigins.length, 1);
  assertEquals(repo.reportOrigins[0].kind, "weekly");
  assertEquals(repo.reportOrigins[0].phase, "post-market");
  assertEquals((repo.storedReport as { kind: unknown }).kind, "intraday");
});

Deno.test("report handler rejects missing or wrong-packet policy decisions without a write or send", async () => {
  for (const wrong of ["missing", "packet", "run"]) {
    const repo = new FakeRepository();
    const payload = reportFixture();
    repo.reportDecisions = wrong === "missing" ? [] : [{
      evaluation_id: payload.report.policy_decision_ids[0],
      candidate_id: "00000000-0000-4000-8000-000000000033",
      run_id: wrong === "run" ? payload.report.source_ids[0] : RUN_ID,
      packet_id: wrong === "packet" ? payload.report.source_ids[0] : PACKET_ID,
      packet_hash: PACKET_HASH,
      ticker: "CENX",
      status: "downgraded",
      final_action: "watch",
      final_alert_urgency: null,
      approved_terms: null,
    }];
    const setup = makeHandler(repo);
    await setup.handler(request("record_report", payload));
    assertEquals(repo.reportDecisionReads.length, 1);
    assertEquals(repo.storedReport, null);
    assertEquals(setup.sent.length, 0);
  }
});
let requestCounter = 10;

function policy(): PolicyConfig {
  return {
    version: 1,
    allocation_bps: { core: 7000, growth: 2000, speculative: 1000 },
    max_position_bps_of_bucket: { core: 2500, growth: 2000, speculative: 1000 },
    max_trade_risk_bps: { core: 100, growth: 100, speculative: 50 },
    min_reward_risk_milli: 2000,
    max_actionable_quote_age_minutes: 20,
    alert_near_bps: 400,
    daily_loss_limit_bps: 300,
    circuit_breaker_consecutive_losses: 3,
    speculative_go_live_bucket_micros: "500000000",
    monthly_investment_micros: "500000000",
    broad_core_etfs: ["SCHD", "VOO", "VTI", "VXUS"],
    self_tuning_enabled: false,
    market_calendar_year: 2026,
    nyse_holidays: ["2026-09-07"],
    request_limits: {
      max_body_bytes: 262144,
      max_candidates: {
        "pre-market": 80,
        intraday: 20,
        "post-market": 80,
        "on-demand": 10,
      },
      max_requests_per_run: 20,
      max_authenticated_requests_per_hour: 100,
    },
  };
}

function verifiedQuote(
  ticker: string,
  price = ticker === "VTI" ? "400" : "47.02",
): VerifiedQuote {
  return {
    ticker,
    price,
    previous_close: price,
    as_of: "2026-09-02T16:55:00.000Z",
    market_state: "REGULAR",
    source: "yahoo-chart",
    actionable_price_status: "available",
    actionable_price_reasons: [],
  };
}

function comparisonHistory(ticker: string): AdjustedBar[] {
  const rows: AdjustedBar[] = [];
  const date = new Date("2025-09-02T12:00:00.000Z");
  while (rows.length < 260) {
    if (date.getUTCDay() !== 0 && date.getUTCDay() !== 6) {
      const price: number = ticker === "ITOT"
        ? 100 + rows.length / 10
        : 100 + rows.length / 20;
      rows.push({
        date: date.toISOString().slice(0, 10),
        raw_close: price.toFixed(6),
        adjusted_close: price.toFixed(6),
        raw_high: price.toFixed(6),
        raw_low: price.toFixed(6),
        split_ratio: null,
      });
    }
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return rows;
}

function longTermHistory(ticker: string): AdjustedBar[] {
  const rows: AdjustedBar[] = [];
  const date = new Date("2016-09-01T12:00:00.000Z");
  const end = new Date("2026-09-01T12:00:00.000Z");
  while (date <= end) {
    if (date.getUTCDay() !== 0 && date.getUTCDay() !== 6) {
      const progress = rows.length / 2_610;
      const base = ticker === "VXUS" ? 55 : 100;
      const price = base * (1 + progress) + (rows.length % 20);
      rows.push({
        date: date.toISOString().slice(0, 10),
        raw_close: price.toFixed(6),
        adjusted_close: price.toFixed(6),
        raw_high: price.toFixed(6),
        raw_low: price.toFixed(6),
        split_ratio: null,
      });
    }
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return rows;
}

function readContext(): GatewayReadContext {
  return {
    holdings: [{
      ticker: "VTI",
      shares: "100",
      avg_cost: "380",
      bucket: "core",
      stop: "350",
      target: "450",
      high_water_price: "410",
      hold_override_until: null,
      stop_alert_active: false,
      stop_near_alert_active: false,
      target_near_alert_active: false,
      target_alert_active: false,
    }],
    holding_quotes: { VTI: { ...verifiedQuote("VTI"), price: "1" } },
    realized_pnl_today: "0",
    portfolio_command_coverage_complete: true,
    consecutive_completed_losses: 0,
    owner_plans: [],
    reconciled_cash_snapshot: {
      snapshot_id: "00000000-0000-4000-8000-000000000099",
      as_of: "2026-09-02T16:59:00.000Z",
      fresh_through: "2026-09-02T17:14:00.000Z",
      ledger_watermark: "0",
      spendable_cash: { core: "300", growth: "10000", speculative: "0" },
    },
    recent_suggestions: [],
    observations: [],
    lessons: [],
    radar: [],
    recent_grades: [],
    dry_powder: [{
      month: "2026-09",
      growth_available: "10000",
      spec_available: "0",
      rolled_months: 0,
    }],
    paper_watches: [],
  };
}

function candidate(phase = "intraday", notification = "entry_trigger") {
  return {
    candidate_id: "00000000-0000-4000-8000-000000000010",
    ticker: "CENX",
    phase,
    action: "buy",
    notification_kind: notification,
    decision_mode: "discretionary",
    bucket: "growth",
    depth: "full",
    confidence: "medium",
    confidence_reason: "Current evidence.",
    health_score: "72",
    observed_price: "9999",
    observed_quote_as_of: "2026-09-02T16:55:00.000Z",
    proposed_amount: "470.20",
    proposed_shares: "10",
    entry_zone_low: "45",
    entry_zone_high: "47.02",
    stop: "42",
    target: "58",
    invalidation_price: "42",
    valid_until: "2026-09-09",
    evidence: [{
      id: "q",
      kind: "quote",
      source: "yahoo",
      status: "fresh",
      observed_at: "2026-09-02T16:55:00.000Z",
      retrieved_at: "2026-09-02T16:56:00.000Z",
      reference: null,
      claims: ["Current quote."],
      exposure_kind: "filing",
    }],
    factors: [{
      kind: "risk",
      stance: "neutral",
      text: "Margins remain stable.",
      evidence_ids: ["q"],
    }],
    analyst: {
      id: "00000000-0000-4000-8000-000000000020",
      packet_id: "00000000-0000-4000-8000-000000000030",
      completed: true,
      action: "buy",
      confidence: "medium",
      reason: "Analyst pass.",
    },
    checker: {
      id: "00000000-0000-4000-8000-000000000021",
      analyst_id: "00000000-0000-4000-8000-000000000020",
      completed: true,
      verdict: "approve",
      reason_codes: [],
      reason: "Checker pass.",
    },
    relationship_type: null,
    decisive_factor: "Risk-adjusted setup.",
    invalidation: "Support fails.",
    prior_suggestion_ids: [],
  };
}

const PACKET_ID = "00000000-0000-4000-8000-000000000030";

function evidencePacket(): EvidencePacket {
  return {
    candidates: [{ candidate_key: "CENX", evidence_ids: ["q"] }],
    evidence: [{ item_id: "q", normalized_text: "Current quote." }],
    facts: [storedFact()],
    coverage: { mode: "bounded", complete_market_coverage: false },
    limitations: [],
    policy_version: 1,
  };
}

function researchOnlyPacket(): EvidencePacket {
  const suitabilityBody = {
    component_scores: {
      concentration_penalty: "0.000000",
      duplication_penalty: "0.000000",
      liquidity: "0.000000",
      portfolio_relevance: "0.000000",
    },
    lineage: null,
    missing_reasons: ["security_identity_unresolved"],
    state: "unknown" as const,
    veto_reasons: [],
  };
  const candidateBody = {
    adverse_paths: [],
    candidate_key: "unresolved:magnet-supplier",
    entity_id: "unresolved:magnet-supplier",
    event_ids: ["00000000-0000-4000-8000-000000000041"],
    evidence: [{
      claim_type: "event",
      item_id: "00000000-0000-4000-8000-000000000031",
      relationship_eligible: false,
      role: "supporting" as const,
    }],
    exposure_fact_ids: [],
    limitations: ["security_identity_unresolved"],
    priority_components: {
      authority_corroboration: "1.000000",
      exposure: "0.000000",
      materiality: "0.000000",
      recency: "1.000000",
    },
    priority_score: "2.000000",
    research_state: "unresolved" as const,
    roles: [],
    security_id: null,
    suitability: {
      ...suitabilityBody,
      evaluation_hash: sha256Hex(canonicalJson(suitabilityBody)),
    },
    theme_ids: ["critical-minerals"],
    ticker: null,
  };
  return {
    action_candidates: [],
    contract_version: 2,
    coverage: { complete_market_coverage: false, mode: "bounded" },
    evidence: [{
      authority: "official",
      canonical_url: "https://example.test/item",
      claim_type: "event",
      content_hash: "a".repeat(64),
      effective_at: null,
      item_id: "00000000-0000-4000-8000-000000000031",
      normalized_text: "Official event.",
      published_at: "2026-09-02T16:00:00.000Z",
      reporting_at: null,
      retrieved_at: "2026-09-02T16:01:00.000Z",
      source_identity: {
        provider: "official",
        receipt_id: "00000000-0000-4000-8000-000000000042",
        upstream_item_id: "item-1",
      },
    }],
    execution_allowed: false,
    limitations: [],
    observed_at: "2026-09-02T17:00:00.000Z",
    omissions: [],
    policy_version: 1,
    research_candidates: [{
      ...candidateBody,
      candidate_hash: sha256Hex(canonicalJson(candidateBody)),
    }],
    run_id: RUN_ID,
  };
}

function researchOnlyReportFixture(
  packet: EvidencePacket,
  kind: ReportKind = "weekly",
  marketDate = "2026-09-02",
) {
  const body = {
    title: "Caller title",
    summary: "BUY a fabricated ticker",
    full_markdown: "Caller BUY 999999",
    source_ids: ["00000000-0000-4000-8000-000000000031"],
    policy_decision_ids: [],
    comparison_ids: [],
    actionable_risk: true,
    material_thesis_change: false,
    intraday_triggered: false,
    suggestion_only: true,
  };
  const reportHash = sha256Hex(canonicalJson(body));
  const packetHash = sha256Hex(canonicalJson(packet));
  const key = sha256Hex(
    `v2:${kind}:${marketDate}:${packetHash}:${reportHash}`,
  );
  return {
    id: reportIdFromKey(key),
    idempotency_key: key,
    packet_id: PACKET_ID,
    market_date: marketDate,
    kind,
    report: body,
    report_hash: reportHash,
    rendered_text: body.full_markdown,
    rendered_hash: sha256Hex(body.full_markdown),
  };
}

function storedFact(candidateKey = "CENX", id = "q"): TrustedEvidenceFact {
  return {
    candidate_key: candidateKey,
    evidence_id: id,
    category: "quote",
    source: "sec_edgar",
    source_status: "succeeded",
    authority: "official",
    published_at: "2026-09-02T16:55:00.000Z",
    retrieved_at: "2026-09-02T16:56:00.000Z",
    expires_at: "2026-09-04T17:00:00.000Z",
    reference: null,
    normalized_text: "Stored evidence.",
    exposure_kind: "filing",
    relationship_eligible: true,
    claim_key: null,
    claim_polarity: null,
  };
}

function packetForCandidates(
  values: Array<Record<string, unknown>>,
): EvidencePacket {
  const evidence = new Map<string, string>();
  const candidates = values.map((value) => {
    const items = value.evidence as Array<{ id: string; claims: string[] }>;
    for (const item of items) evidence.set(item.id, item.claims.join(" "));
    return {
      candidate_key: String(value.ticker),
      evidence_ids: items.map((item) => item.id),
    };
  });
  return {
    candidates,
    facts: values.flatMap((value) =>
      (value.evidence as Array<Record<string, unknown>>).map((item) => ({
        ...storedFact(String(value.ticker), String(item.id)),
        category: item.kind as TrustedEvidenceFact["category"],
        published_at: item.observed_at as string | null,
        retrieved_at: item.retrieved_at as string,
        source_status: item.status === "fresh"
          ? "succeeded" as const
          : "failed" as const,
      }))
    ),
    evidence: [...evidence].map(([item_id, normalized_text]) => ({
      item_id,
      normalized_text,
    })),
    coverage: { mode: "fixture_dry_run", complete_market_coverage: false },
    limitations: [],
    policy_version: 1,
  };
}

const PACKET_HASH = sha256Hex(canonicalJson(evidencePacket()));

function packetRef(
  coverage: "complete_for_plan" | "partial" | "fixture_dry_run" =
    "complete_for_plan",
) {
  return { id: PACKET_ID, content_hash: PACKET_HASH, coverage };
}

function alertRule(
  state: AlertRuleSnapshot["state"] = "active",
): AlertRuleSnapshot {
  return {
    rule_id: "7f7f70bf-5cec-4f1e-9de8-ec8823d99fc7",
    version: 1,
    state,
    ticker: "CENX",
    profile: "balanced",
    severity: "review",
    session: "regular",
    confirmation: "two_quote",
    conditions: [{
      kind: "price_zone",
      operator: "inside",
      left: "45",
      right: "48",
      timeframe: "quote",
    }],
    cooldown_seconds: 14_400,
    fire_limit: 3,
    valid_until: "2026-09-09T21:00:00.000Z",
    owner_note: "Review only",
  };
}

function alertWork(
  options: { rule?: boolean; draft?: boolean } = { rule: true },
): AlertWork {
  const source_summary = {
    ticker: "CENX",
    confidence: "medium" as const,
    valid_until: "2026-09-09",
    invalidation_price: "42",
    stop: "42",
    target: "58",
    position_value_after: null,
    total_investable_value: null,
    evidence: [],
    reasons: [],
  };
  return {
    rules: options.rule
      ? [{ rule: alertRule(), recent_events: [], source_summary }]
      : [],
    drafts: options.draft
      ? [{ rule: alertRule("draft"), recent_events: [], source_summary }]
      : [],
  };
}

class FakeRepository implements GatewayRepository {
  reportDecisionReads: Array<
    { runId: string; packetId: string; ids: string[] }
  > = [];
  reportDecisions: ReportPolicyDecision[] = [];
  scheduledReportPhase: "pre-market" | "intraday" | "post-market" | null = null;
  reportOrigins: Array<{
    requestId: string;
    runId: string;
    marketDate: string;
    kind: ReportKind;
    phase: "pre-market" | "intraday" | "post-market" | null;
  }> = [];
  storedReport: unknown = null;
  reportPublication: PublicationReceipt | null = null;
  reportPublicationClaimable = true;
  reportPublicationLeaseExpired = false;
  loadReportDecisions(
    runId: string,
    packetId: string,
    ids: string[],
  ): Promise<ReportPolicyDecision[]> {
    this.reportDecisionReads.push({ runId, packetId, ids });
    return Promise.resolve(structuredClone(this.reportDecisions));
  }
  recordReportOrigin(
    requestId: string,
    _leaseToken: string,
    runId: string,
    payload: { market_date: string; kind: ReportKind },
  ) {
    this.reportOrigins.push({
      requestId,
      runId,
      marketDate: payload.market_date,
      kind: payload.kind,
      phase: this.scheduledReportPhase,
    });
    return Promise.resolve({ scheduled: this.scheduledReportPhase !== null });
  }
  recordReport(
    _runId: string,
    payload: { id: string; report_hash: string; rendered_hash: string },
  ) {
    this.storedReport = structuredClone(payload);
    return Promise.resolve({
      report_id: payload.id,
      report_hash: payload.report_hash,
      rendered_hash: payload.rendered_hash,
      duplicate: false,
    });
  }
  createReportPublication(
    _runId: string,
    payload: { id: string; idempotency_key: string },
  ): Promise<PublicationReceipt> {
    this.events.push("persist-report-publication");
    if (!this.reportPublication) {
      this.reportPublication = {
        id: payload.id,
        idempotency_key: payload.idempotency_key,
        status: "pending",
        telegram_message_ids: [],
        telegram_accepted_at: null,
        lease_token: null,
      };
    }
    return Promise.resolve(structuredClone(this.reportPublication));
  }
  claimReportPublication(idempotencyKey: string): Promise<PublicationClaim> {
    this.events.push("claim-report-publication");
    const receipt = this.reportPublication!;
    if (this.reportPublicationLeaseExpired) {
      this.reportPublication = {
        ...receipt,
        status: "uncertain",
        lease_token: null,
      };
      return Promise.resolve({
        claimed: false,
        lease_token: null,
        receipt: { ...this.reportPublication, idempotency_key: idempotencyKey },
      });
    }
    if (!this.reportPublicationClaimable) {
      return Promise.resolve({
        claimed: false,
        lease_token: null,
        receipt: { ...receipt, idempotency_key: idempotencyKey },
      });
    }
    const claimed = receipt.status === "pending" || receipt.status === "failed";
    return Promise.resolve({
      claimed,
      lease_token: claimed ? "00000000-0000-4000-8000-000000000052" : null,
      receipt: { ...receipt, idempotency_key: idempotencyKey },
    });
  }
  finishReportPublication(
    _key: string,
    _lease: string,
    status: "delivered" | "failed" | "uncertain",
    ids: number[],
  ): Promise<PublicationReceipt> {
    this.reportPublication = {
      ...this.reportPublication!,
      status,
      telegram_message_ids: ids,
      lease_token: null,
    };
    return Promise.resolve(structuredClone(this.reportPublication));
  }
  suppressionReasons: Array<string | undefined> = [];
  suppressReportPublication(
    idempotencyKey: string,
    reason?: string,
  ): Promise<PublicationReceipt> {
    this.suppressionReasons.push(reason);
    this.reportPublication = {
      ...this.reportPublication!,
      idempotency_key: idempotencyKey,
      status: "suppressed",
      telegram_message_ids: [],
      telegram_accepted_at: null,
      lease_token: null,
    };
    return Promise.resolve(
      {
        ...structuredClone(this.reportPublication),
        suppression_reason: reason,
      } as PublicationReceipt,
    );
  }
  mutationCalls = 0;
  startCalls = 0;
  recordCalls = 0;
  applyCalls = 0;
  createDraftCalls = 0;
  readAlertWorkCalls = 0;
  recordAlertEventCalls = 0;
  createAlertPublicationCalls = 0;
  finishPublicationCalls: Array<{ status: string; ids: number[] }> = [];
  finishAlertPublicationCalls: Array<
    { status: string; ids: number[]; acceptedAt: string | null }
  > = [];
  expireAlertRuleCalls = 0;
  finishRunCalls = 0;
  runOutcomes: Array<{ runId: string; outcome: string }> = [];
  scheduledSlots: string[] = [];
  readCalls = 0;
  packetReadCalls = 0;
  events: string[] = [];
  claims = new Map<string, unknown>();
  failCode: string | null = null;
  publicationStatus: PublicationReceipt["status"] = "ready";
  context = readContext();
  lastArtifacts: PersistableArtifactMutationBatch | null = null;
  lastBundle: PersistedBundle | null = null;
  lastDrafts: unknown[] = [];
  alertWorkValue: AlertWork = { rules: [], drafts: [] };
  lastAlertEvents: unknown[] = [];
  due: DueDecision[] = [];
  graded: OutcomeGrade[] = [];
  dueLimit: number | null = null;
  learningCalls = 0;
  lastLearning: unknown = null;

  recordLearning(_runId: string, payload: unknown): Promise<{
    observation_id: string;
    content_hash: string;
    duplicate: boolean;
  }> {
    this.learningCalls += 1;
    this.lastLearning = structuredClone(payload);
    const row = payload as { id: string; content_hash: string };
    return Promise.resolve({
      observation_id: row.id,
      content_hash: row.content_hash,
      duplicate: false,
    });
  }

  claimRequest(envelope: GatewayEnvelope): Promise<GatewayRequestClaim> {
    this.mutationCalls += 1;
    if (this.failCode) throw new GatewayRepositoryError(this.failCode);
    if (this.claims.has(envelope.request_id)) {
      return Promise.resolve({
        duplicate: true,
        in_progress: false,
        lease_token: null,
        response: this.claims.get(envelope.request_id),
      });
    }
    return Promise.resolve({
      duplicate: false,
      in_progress: false,
      lease_token: "00000000-0000-4000-8000-000000000003",
    });
  }
  completeRequest(
    requestId: string,
    _lease: string,
    response: unknown,
  ): Promise<void> {
    this.mutationCalls += 1;
    this.claims.set(requestId, response);
    return Promise.resolve();
  }
  failRequest(requestId: string, _lease: string, code: string): Promise<void> {
    this.mutationCalls += 1;
    this.claims.set(requestId, { ok: false, code });
    return Promise.resolve();
  }
  startRun(
    _requestId: string,
    _leaseToken: string,
    phase: Phase,
    marketDate?: string,
  ): Promise<{ run_id: string; duplicate: boolean }> {
    this.mutationCalls += 1;
    this.startCalls += 1;
    const slot = `${marketDate ?? "missing"}:${phase}`;
    const duplicate = phase !== "on-demand" &&
      this.scheduledSlots.includes(slot);
    this.scheduledSlots.push(slot);
    return Promise.resolve({ run_id: RUN_ID, duplicate });
  }
  recordRunOutcome(
    _requestId: string,
    _leaseToken: string,
    runId: string,
    outcome: "no_trigger" | "not_actionable",
  ) {
    this.runOutcomes.push({ runId, outcome });
    return Promise.resolve({ run_id: runId, outcome, duplicate: false });
  }
  readContext(): Promise<GatewayReadContext> {
    this.readCalls += 1;
    return Promise.resolve(structuredClone(this.context));
  }
  loadIntelligencePacket(): Promise<
    {
      id: string;
      run_id: string;
      content_hash: string;
      packet: EvidencePacket;
      evidence_facts: TrustedEvidenceFact[];
      exposure_facts: Array<{
        candidate_key: string;
        evidence_id: string;
        exposure_kind: "filing";
        status: "fresh";
        observed_at: string;
        retrieved_at: string;
      }>;
    }
  > {
    this.packetReadCalls += 1;
    return Promise.resolve({
      id: PACKET_ID,
      run_id: RUN_ID,
      content_hash: PACKET_HASH,
      packet: evidencePacket(),
      evidence_facts: [storedFact()],
      exposure_facts: [{
        candidate_key: "CENX",
        evidence_id: "q",
        exposure_kind: "filing",
        status: "fresh",
        observed_at: "2026-09-02T16:55:00.000Z",
        retrieved_at: "2026-09-02T16:56:00.000Z",
      }],
    });
  }
  activePolicy(): Promise<PolicyConfig> {
    return Promise.resolve(this.policyValue);
  }
  policyValue = policy();
  createAlertDrafts(
    _requestId: string,
    drafts: unknown[],
  ): Promise<{ created_count: number; draft_ids: string[] }> {
    this.mutationCalls += 1;
    this.createDraftCalls += 1;
    this.lastDrafts = structuredClone(drafts);
    return Promise.resolve({
      created_count: drafts.length,
      draft_ids: drafts.map((item) => (item as { id: string }).id),
    });
  }
  readAlertWork(): Promise<AlertWork> {
    this.readAlertWorkCalls += 1;
    return Promise.resolve(structuredClone(this.alertWorkValue));
  }
  recordAlertEvaluations(
    _requestId: string,
    events: unknown[],
  ): Promise<AlertEventReceipt> {
    this.events.push("persist-alert-events");
    this.mutationCalls += 1;
    this.recordAlertEventCalls += 1;
    this.lastAlertEvents = structuredClone(events);
    return Promise.resolve({
      event_count: events.length,
      event_ids: events.map((event) => (event as { id: string }).id),
    });
  }
  createAlertPublication(requestId: string): Promise<PublicationReceipt> {
    this.events.push("persist-alert-publication");
    this.mutationCalls += 1;
    this.createAlertPublicationCalls += 1;
    return Promise.resolve({
      id: "00000000-0000-4000-8000-000000000050",
      idempotency_key: requestId,
      status: "ready",
      telegram_message_ids: [],
      telegram_accepted_at: null,
      lease_token: null,
    });
  }
  expireAlertRules(): Promise<number> {
    this.mutationCalls += 1;
    this.expireAlertRuleCalls += 1;
    return Promise.resolve(0);
  }
  recordArtifacts(
    _request: string,
    _run: string,
    _lease: string,
    payload: PersistableArtifactMutationBatch,
  ): Promise<ArtifactReceipt> {
    this.mutationCalls += 1;
    this.recordCalls += 1;
    this.lastArtifacts = payload;
    return Promise.resolve({
      counts: { lesson: payload.mutations.length },
      created_paper_watch_ids: [],
    });
  }
  applyDecisionBundle(input: PersistedBundle): Promise<PublicationReceipt> {
    this.events.push("persist");
    this.mutationCalls += 1;
    this.applyCalls += 1;
    this.lastBundle = input;
    return Promise.resolve({
      id: "00000000-0000-4000-8000-000000000050",
      idempotency_key: input.request_id,
      status: input.publication.status,
      telegram_message_ids: [],
      telegram_accepted_at: null,
      lease_token: null,
    });
  }
  claimPublication(idempotencyKey: string): Promise<PublicationClaim> {
    this.events.push("claim-publication");
    return Promise.resolve({
      claimed: this.publicationStatus === "ready" ||
        this.publicationStatus === "delivery_failed",
      lease_token: "00000000-0000-4000-8000-000000000051",
      receipt: {
        id: "00000000-0000-4000-8000-000000000050",
        idempotency_key: idempotencyKey,
        status: this.publicationStatus,
        telegram_message_ids: [],
        telegram_accepted_at: null,
        lease_token: null,
      },
    });
  }
  finishPublication(
    _key: string,
    _lease: string,
    status: "delivered" | "delivery_failed" | "delivery_unknown",
    ids: number[],
  ): Promise<PublicationReceipt> {
    this.mutationCalls += 1;
    this.finishPublicationCalls.push({ status, ids });
    this.publicationStatus = status;
    return Promise.resolve({
      id: "00000000-0000-4000-8000-000000000050",
      idempotency_key: _key,
      status,
      telegram_message_ids: ids,
      telegram_accepted_at: null,
      lease_token: null,
    });
  }
  finishAlertPublication(
    key: string,
    _lease: string,
    status: "delivered" | "delivery_failed" | "delivery_unknown",
    ids: number[],
    _error: string | null,
    acceptedAt: string | null,
  ): Promise<PublicationReceipt> {
    this.mutationCalls += 1;
    this.finishAlertPublicationCalls.push({ status, ids, acceptedAt });
    this.publicationStatus = status;
    return Promise.resolve({
      id: "00000000-0000-4000-8000-000000000050",
      idempotency_key: key,
      status,
      telegram_message_ids: ids,
      lease_token: null,
      telegram_accepted_at: acceptedAt,
    });
  }
  finishRun(): Promise<RunReceipt> {
    this.mutationCalls += 1;
    this.finishRunCalls += 1;
    return Promise.resolve({
      run_id: RUN_ID,
      status: "completed",
      write_counts: { evaluations: 1 },
      publication_statuses: ["delivered"],
      telegram_message_ids: [77],
    });
  }
  dueDecisions(limit: number): Promise<DueDecision[]> {
    this.dueLimit = limit;
    return Promise.resolve(structuredClone(this.due));
  }
  upsertGrades(
    grades: OutcomeGrade[],
  ): Promise<{ inserted: number; updated: number; incomplete: number }> {
    this.mutationCalls += 1;
    this.graded = structuredClone(grades);
    return Promise.resolve({
      inserted: grades.length,
      updated: 0,
      incomplete: grades.filter((grade) =>
        grade.coverage_status !== "complete"
      ).length,
    });
  }
}

function makeHandler(
  repository = new FakeRepository(),
  overrides: Record<string, unknown> = {},
) {
  let ids = 100;
  const sent: string[][] = [];
  const fetched: string[] = [];
  const alertFetches: string[] = [];
  const sentAlerts: unknown[] = [];
  const handler = createGatewayHandler({
    repository,
    marketAgentSecret: SECRET,
    telegramToken: "12345678:token",
    telegramChatId: "123",
    now: () => new Date(NOW),
    newId: () => `00000000-0000-4000-8000-${String(ids++).padStart(12, "0")}`,
    fetchQuote: (ticker: string) => {
      fetched.push(ticker);
      return Promise.resolve(verifiedQuote(ticker));
    },
    fetchAlertEvidence: (ticker: string): Promise<IntradayQuoteEvidence> => {
      alertFetches.push(ticker);
      return Promise.resolve({
        ticker,
        market_session: "regular",
        source: "yahoo-chart",
        points: [
          {
            value: "46",
            comparison_value: null,
            observed_at: "2026-09-02T16:54:00.000Z",
            bar_complete: true,
          },
          {
            value: "47.02",
            comparison_value: null,
            observed_at: "2026-09-02T16:55:00.000Z",
            bar_complete: true,
          },
        ],
      });
    },
    sendTelegram: (parts: string[]) => {
      repository.events.push("send");
      sent.push(parts);
      return Promise.resolve([77]);
    },
    sendTelegramAlert: (alert: unknown) => {
      repository.events.push("send-alert");
      sentAlerts.push(alert);
      return Promise.resolve({
        status: "accepted_by_telegram",
        message_id: 78,
        accepted_at: NOW.toISOString(),
      });
    },
    ...overrides,
  });
  return { handler, repository, sent, fetched, alertFetches, sentAlerts };
}

function nextRequestId(): string {
  return `00000000-0000-4000-8000-${
    String(requestCounter++).padStart(12, "0")
  }`;
}

function request(
  operation: GatewayEnvelope["operation"],
  payload: unknown,
  options: {
    dry?: boolean;
    packet?: boolean;
    runId?: string | null;
    requestId?: string;
    secret?: string;
    method?: string;
    authorization?: string;
  } = {},
) {
  const payloadValue = structuredClone(payload);
  if (
    operation === "evaluate_and_publish" && typeof payloadValue === "object" &&
    payloadValue !== null
  ) {
    const row = payloadValue as Record<string, unknown>;
    const phase = row.phase;
    if (!("intelligence_packet" in row) && options.packet !== false) {
      if (options.dry) {
        const packet = packetForCandidates(
          row.candidates as Array<Record<string, unknown>>,
        );
        row.intelligence_packet = {
          id: PACKET_ID,
          content_hash: sha256Hex(canonicalJson(packet)),
          coverage: "fixture_dry_run",
          packet,
        };
      } else {
        row.intelligence_packet = packetRef();
      }
    }
    if (Array.isArray(row.candidates)) {
      for (const value of row.candidates) {
        const item = value as Record<string, unknown>;
        const analyst = item.analyst as Record<string, unknown>;
        const checker = item.checker as Record<string, unknown>;
        item.relationship_type ??= "direct";
        analyst.id ??= "00000000-0000-4000-8000-000000000020";
        analyst.packet_id ??= PACKET_ID;
        checker.id ??= "00000000-0000-4000-8000-000000000021";
        checker.analyst_id ??= analyst.id;
      }
    }
  }
  return new Request(
    "https://example.invalid/functions/v1/market-briefing-gateway",
    {
      method: options.method ?? "POST",
      headers: {
        "content-type": "application/json",
        "x-market-agent-secret": options.secret ?? SECRET,
        ...(options.authorization
          ? { authorization: options.authorization }
          : {}),
      },
      body: options.method === "GET" ? undefined : canonicalJson({
        schema_version: 1,
        operation,
        request_id: options.requestId ?? nextRequestId(),
        run_id: options.runId === undefined
          ? (operation === "start_run" || operation === "evaluate_alert_rules"
            ? null
            : RUN_ID)
          : options.runId,
        dry_run: options.dry ?? false,
        payload: payloadValue,
      }),
    },
  );
}

Deno.test("every evaluation mode requires a packet before prior suggestions can be considered", async () => {
  for (
    const phase of [
      "on-demand",
      "pre-market",
      "intraday",
      "post-market",
    ] as const
  ) {
    const setup = makeHandler();
    const value = candidate(phase, "brief") as Record<string, unknown>;
    value.prior_suggestion_ids = ["00000000-0000-4000-8000-000000000099"];
    const response = await setup.handler(request("evaluate_and_publish", {
      phase,
      market_date: "2026-09-02",
      title: "packet bypass attempt",
      candidates: [value],
    }, { dry: true, packet: false }));
    assertEquals((await json(response)).code, "INTELLIGENCE_PACKET_INVALID");
    assertEquals(setup.fetched, []);
    assertEquals(setup.repository.applyCalls, 0);
  }
});

Deno.test("protected completion recovery bypasses new request claims and refuses authored payloads", async () => {
  const REQUEST_ID = "00000000-0000-4000-8000-000000000071";
  const repo = Object.assign(new FakeRepository(), {
    readIntelligenceCompletion: (runId: string, completionId: string) =>
      Promise.resolve({ run_id: runId, completion_id: completionId }),
  });
  const setup = makeHandler(repo);
  const recovered = await setup.handler(
    request("read_intelligence_completion", {}, { requestId: REQUEST_ID }),
  );
  assertEquals(recovered.status, 200);
  assertEquals((await json(recovered)).completion, {
    run_id: RUN_ID,
    completion_id: REQUEST_ID,
  });
  assertEquals(repo.claims.size, 0);
  assertEquals(setup.sent, []);
  (repo.policyValue as unknown as Record<string, unknown>).version = 4;
  const context = await setup.handler(
    request("read_intelligence_context", {}),
  );
  assertEquals(context.status, 200);
  assertEquals((await json(context)).context, {
    ...readContext(),
    policy_version: 4,
  });
  const invented = await setup.handler(
    request("read_intelligence_context", {
      liquidity_by_ticker: { TEST: "1" },
    }),
  );
  assertEquals(invented.status, 400);
  const unauthorized = await setup.handler(
    request("read_intelligence_context", {}, { secret: "wrong" }),
  );
  assertEquals(unauthorized.status, 401);
});

Deno.test("decision context exposes the active policy version", async () => {
  const repository = new FakeRepository();
  (repository.policyValue as unknown as Record<string, unknown>).version = 4;
  const setup = makeHandler(repository);
  const result = await setup.handler(request("read_context", {}));
  assertEquals(result.status, 200);
  assertEquals((await json(result)).context, {
    ...readContext(),
    policy_version: 4,
  });
});

Deno.test("protected quote producer reserves before fetching and resumes without another call", async () => {
  const { fetchCollectionQuote } = await import("./collection-quotes.ts");
  const input = {
    ticker: "TEST",
    instrument_type: "COMMON_STOCK",
    security_revision_id: "00000000-0000-4000-8000-000000000083",
    reference_manifest_id: "00000000-0000-4000-8000-000000000084",
    selection_manifest_id: "00000000-0000-4000-8000-000000000085",
    selected_task_id: "00000000-0000-4000-8000-000000000086",
    cache_key: "a".repeat(64),
    source_receipt_id: "00000000-0000-4000-8000-000000000081",
    reservation_id: "00000000-0000-4000-8000-000000000082",
  };
  const window = { start: "2026-09-02T16:00:00.000Z", end: NOW.toISOString() };
  const calls: string[] = [];
  let saved: Record<string, unknown> | null = null;
  let blocked = false;
  const repo = Object.assign(new FakeRepository(), {
    claimIntelligenceQuote: () => {
      calls.push("claim");
      return Promise.resolve(
        saved ? { status: "completed", checkpoint: saved } : {
          status: blocked ? "quota_blocked" : "claimed",
          request_window: window,
        },
      );
    },
    recordIntelligenceQuote: (
      _run: string,
      _id: string,
      quote: unknown,
      checkpoint: Record<string, unknown>,
    ) => {
      calls.push("record");
      assert(quote !== null, "protected fetch supplied the quote");
      saved = checkpoint;
      return Promise.resolve(checkpoint);
    },
  });
  const setup = makeHandler(repo, {
    fetchCollectionQuote: (
      ticker: string,
      now: Date,
      instrumentType: "COMMON_STOCK" | "ADR" | "ETF",
    ) => {
      calls.push("fetch");
      return fetchCollectionQuote(
        ticker,
        now,
        instrumentType,
        () =>
          Promise.resolve(Response.json({
            chart: {
              result: [{
                meta: {
                  symbol: ticker,
                  currency: "USD",
                  instrumentType: "EQUITY",
                  regularMarketPrice: 100,
                  regularMarketTime: Math.floor(now.valueOf() / 1000),
                },
                timestamp: [Math.floor(now.valueOf() / 1000)],
                indicators: { quote: [{ close: [100], volume: [50000] }] },
              }],
            },
          })),
      );
    },
  });
  const first = await setup.handler(
    request("collect_intelligence_quote", input),
  );
  assertEquals(first.status, 200);
  assertEquals(calls, ["claim", "fetch", "record"]);
  const checkpoint = (await json(first)).checkpoint as {
    receipt: { request_cost: number; status: string };
  };
  assertEquals(checkpoint.receipt.request_cost, 1);
  assertEquals(checkpoint.receipt.status, "succeeded");
  const retry = await setup.handler(
    request("collect_intelligence_quote", input),
  );
  assertEquals((await json(retry)).checkpoint, checkpoint);
  assertEquals(calls, ["claim", "fetch", "record", "claim"]);
  saved = null;
  blocked = true;
  const exhausted = await setup.handler(
    request("collect_intelligence_quote", input),
  );
  const blockedReceipt =
    ((await json(exhausted)).checkpoint as { receipt: Record<string, unknown> })
      .receipt;
  assertEquals(blockedReceipt.status, "quota_blocked");
  assertEquals(blockedReceipt.error_code, "QUOTA_BLOCKED");
  assertEquals(blockedReceipt.request_cost, 0);
  assertEquals(calls.filter((value) => value === "fetch").length, 1);
  assertEquals(
    (await setup.handler(
      request("collect_intelligence_quote", { ...input, price: "999999" }),
    )).status,
    400,
  );
  assertEquals(
    (await setup.handler(
      request("collect_intelligence_quote", input, { secret: "wrong" }),
    )).status,
    401,
  );
  assertEquals(repo.claims.size, 0);
  assertEquals(setup.sent, []);
});

Deno.test("enrichment selection is persisted as an exact service-only manifest before transport", async () => {
  const payload = {
    manifest: {
      manifest_id: "00000000-0000-4000-8000-000000000091",
      run_id: RUN_ID,
      phase: "on-demand",
      selection_stage: "initial",
      schema_version: 1,
      execution_allowed: false,
      provider_reservations: {
        sec_issuer_submissions: 1,
        sec_filing_document: 1,
        yahoo_security_quote: 1,
        gdelt_reverse: 1,
      },
      deferred_reasons: {},
      request_descriptors: [],
      semantic_hash: "a".repeat(64),
    },
    requests: [],
  };
  let received: unknown = null;
  const repo = Object.assign(new FakeRepository(), {
    sealEnrichmentSelection: (_run: string, value: unknown) => {
      received = value;
      return Promise.resolve({
        manifest_id: payload.manifest.manifest_id,
        request_count: 0,
        duplicate: false,
      });
    },
  });
  const setup = makeHandler(repo);
  const result = await setup.handler(
    request("seal_enrichment_selection", payload),
  );
  assertEquals(result.status, 200);
  assert(
    (received as typeof payload).manifest.manifest_id ===
      payload.manifest.manifest_id,
    "handler must preserve the sealed manifest identity",
  );
  assert(
    (received as typeof payload).requests.length === 0,
    "handler must preserve an explicitly empty bounded selection",
  );
  assertEquals((await json(result)).request_count, 0);
  assertEquals(
    (await setup.handler(
      request("seal_enrichment_selection", { ...payload, extra: true }),
    )).status,
    400,
  );
});

Deno.test("scheduled discovery requires the persisted packet hash before market work", async () => {
  class MismatchedPacketRepository extends FakeRepository {
    override loadIntelligencePacket(): Promise<
      {
        id: string;
        run_id: string;
        content_hash: string;
        packet: EvidencePacket;
        evidence_facts: TrustedEvidenceFact[];
        exposure_facts: [];
      }
    > {
      this.packetReadCalls += 1;
      return Promise.resolve({
        id: PACKET_ID,
        run_id: RUN_ID,
        content_hash: "b".repeat(64),
        packet: evidencePacket(),
        evidence_facts: [storedFact()],
        exposure_facts: [],
      });
    }
  }
  const setup = makeHandler(new MismatchedPacketRepository());
  const response = await setup.handler(request("evaluate_and_publish", {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "bounded discovery",
    candidates: [candidate()],
    intelligence_packet: packetRef(),
  }));
  assertEquals((await json(response)).code, "INTELLIGENCE_PACKET_MISMATCH");
  assertEquals(setup.fetched, []);
});

Deno.test("gateway binds current caller evidence to stale persisted packet facts", async () => {
  class StalePacketRepository extends FakeRepository {
    override async loadIntelligencePacket() {
      const persisted = await super.loadIntelligencePacket();
      persisted.evidence_facts[0].published_at = "2020-01-01T00:00:00Z";
      return persisted;
    }
  }
  const setup = makeHandler(new StalePacketRepository());
  await setup.handler(
    request("evaluate_and_publish", {
      phase: "intraday",
      market_date: "2026-09-02",
      title: "Caller fresh",
      candidates: [candidate()],
    }),
  );
  const evaluation = setup.repository.lastBundle!.evaluations[0];
  assertEquals(evaluation.final_action, "watch");
  assert(
    evaluation.reason_codes.includes("EVIDENCE_STALE"),
    "handler lost stored timestamps",
  );
  assertEquals(
    evaluation.candidate.evidence[0].observed_at,
    "2020-01-01T00:00:00Z",
  );
});

Deno.test("record_learning persists only the immutable observation RPC payload", async () => {
  const fixture = makeHandler();
  const observation = {
    status: "owner_review",
    evidence_ids: ["00000000-0000-4000-8000-000000000010"],
    limitations: ["Historical outcomes do not prove future performance."],
    metrics: { false_positive_rate: "0.1667" },
    proposed_change: {
      area: "candidate_ranking_review",
      recommendation: "review_false_positive_rate",
      false_positive_rate: "0.1667",
    },
  };
  const payload = {
    id: "00000000-0000-4000-8000-000000000040",
    policy_version: 1,
    observation_type: "outcome",
    horizon_days: 21,
    sample_size: 6,
    benchmark: "VOO",
    observation,
    content_hash: sha256Hex(canonicalJson(observation)),
  };

  const body = await json(
    await fixture.handler(request("record_learning", payload)),
  );

  assertEquals(body.ok, true);
  assertEquals(body.observation_id, payload.id);
  assertEquals(fixture.repository.learningCalls, 1);
  assertEquals(fixture.repository.lastLearning, payload);
  assertEquals(fixture.sent, []);
  assertEquals(fixture.repository.applyCalls, 0);
});

Deno.test("record_learning dry-run is write-free and rejects executable authority", async () => {
  const fixture = makeHandler();
  const observation = {
    status: "observation",
    evidence_ids: [],
    limitations: ["Insufficient sample."],
    metrics: { false_positive_rate: null },
    proposed_change: null,
  };
  const payload = {
    id: "00000000-0000-4000-8000-000000000040",
    policy_version: 1,
    observation_type: "noise",
    horizon_days: 5,
    sample_size: 1,
    benchmark: "VOO",
    observation,
    content_hash: sha256Hex(canonicalJson(observation)),
  };
  const dry = await json(
    await fixture.handler(request("record_learning", payload, { dry: true })),
  );
  assertEquals(dry.dry_run, true);
  assertEquals(dry.write_counts, {});
  assertEquals(fixture.repository.learningCalls, 0);

  const invalid = structuredClone(payload);
  (invalid.observation as Record<string, unknown>).apply = true;
  const denied = await fixture.handler(request("record_learning", invalid));
  assertEquals(denied.status, 400);
  assertEquals(fixture.repository.learningCalls, 0);
});

Deno.test("scheduled discovery rejects evidence outside the immutable packet", async () => {
  const setup = makeHandler();
  const outside = candidate();
  outside.evidence[0].id = "outside";
  outside.factors[0].evidence_ids = ["outside"];
  const response = await setup.handler(request("evaluate_and_publish", {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "bounded discovery",
    candidates: [outside],
    intelligence_packet: packetRef(),
  }));
  assertEquals((await json(response)).code, "EVIDENCE_NOT_IN_PACKET");
  assertEquals(setup.fetched, []);
});

Deno.test("dry-run fixture packet is validated without a repository packet read", async () => {
  const setup = makeHandler();
  const response = await setup.handler(request("evaluate_and_publish", {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "fixture",
    candidates: [candidate()],
    intelligence_packet: {
      ...packetRef("fixture_dry_run"),
      packet: evidencePacket(),
    },
  }, { dry: true }));
  assertEquals(response.status, 200);
  assertEquals(setup.repository.packetReadCalls, 0);
});

Deno.test("accepted discovery persists the packet to Analyst to Checker chain", async () => {
  const setup = makeHandler();
  const response = await setup.handler(request("evaluate_and_publish", {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "bounded discovery",
    candidates: [candidate("intraday", "brief")],
    intelligence_packet: packetRef(),
  }));
  assertEquals(response.status, 200);
  const receipt = await json(response);
  assertEquals(receipt.run_id, RUN_ID);
  assert(
    Array.isArray(receipt.policy_decision_ids) &&
      receipt.policy_decision_ids.length === 1,
    "durable policy decision ID missing",
  );
  assertEquals(receipt.source_ids, ["q"]);
  assertEquals(receipt.intelligence_packet, {
    id: PACKET_ID,
    content_hash: PACKET_HASH,
  });
  const persisted = setup.repository.lastBundle!.evaluations[0].candidate;
  assertEquals(persisted.analyst.packet_id, PACKET_ID);
  assertEquals(persisted.checker.analyst_id, persisted.analyst.id);
});

async function json(response: Response): Promise<Record<string, unknown>> {
  return await response.json();
}

Deno.test("method and secret are rejected before repository or body processing", async () => {
  const { handler, repository } = makeHandler();
  const get = await handler(request("read_context", {}, { method: "GET" }));
  assertEquals(get.status, 405);
  assert(
    get.headers.get("access-control-allow-origin") === null,
    "wildcard CORS present",
  );
  const unauthorized = await handler(
    request("read_context", {}, { secret: "wrong" }),
  );
  assertEquals(unauthorized.status, 401);
  assertEquals(repository.mutationCalls, 0);
  assertEquals(repository.readCalls, 0);
});

const DISCOVERY_OWNER = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22";

function discoveryCheckpoint(stage = "signals") {
  return {
    task: {
      id: "00000000-0000-4000-8000-000000000041",
      stage,
      provider: "gdelt",
      capability_id: "gdelt_theme_search",
      query_kind: "theme_search",
      query_hash: "a".repeat(64),
      dependency_ids: [],
      requested_window: {
        start: "2026-09-05T00:00:00.000Z",
        end: "2026-09-06T00:00:00.000Z",
      },
      state: "planned",
      attempt_count: 0,
      request_budget: 1,
      result: {},
    },
    exposure_facts: [] as Record<string, unknown>[],
    theme_episode_revisions: [] as Record<string, unknown>[],
    research_nominations: [] as Record<string, unknown>[],
  };
}

function discoveryOwnerVerifier(
  request: Request,
): Promise<{ subject: string }> {
  const token = request.headers.get("authorization");
  if (token === "Bearer owner") {
    return Promise.resolve({ subject: DISCOVERY_OWNER });
  }
  if (token === "Bearer other") {
    return Promise.resolve({ subject: "00000000-0000-4000-8000-000000000099" });
  }
  return Promise.reject(new Error("missing owner authentication"));
}

Deno.test("discovery reads accept the owner or scoped collection secret while writes stay service-only", async () => {
  const repository = Object.assign(new FakeRepository(), {
    readDiscoveryContext: () =>
      Promise.resolve({
        manifests: [],
        security_revisions: [],
        tasks: [],
        theme_episodes: [],
        exposure_facts: [],
        research_nominations: [],
        enrichment_selections: [],
      }),
    checkpointDiscoveryStage: (
      _runId: string,
      payload: ReturnType<typeof discoveryCheckpoint>,
    ) => Promise.resolve({ task: payload.task, duplicate: false }),
  });
  const setup = makeHandler(repository, {
    ownerUserId: DISCOVERY_OWNER,
    verifyOwner: discoveryOwnerVerifier,
  });

  const anonymous = await setup.handler(request(
    "read_discovery_context",
    { limit: 100 },
    { secret: "" },
  ));
  assertEquals(anonymous.status, 401);
  const nonOwner = await setup.handler(request(
    "read_discovery_context",
    { limit: 100 },
    { secret: "", authorization: "Bearer other" },
  ));
  assertEquals(nonOwner.status, 403);
  const serviceRead = await setup.handler(request(
    "read_discovery_context",
    { limit: 100 },
  ));
  assertEquals(serviceRead.status, 200);
  const owner = await setup.handler(request(
    "read_discovery_context",
    { limit: 100 },
    { secret: "", authorization: "Bearer owner" },
  ));
  assertEquals(owner.status, 200);

  const ownerWrite = await setup.handler(request(
    "checkpoint_discovery_stage",
    discoveryCheckpoint(),
    { secret: "", authorization: "Bearer owner" },
  ));
  assertEquals(ownerWrite.status, 403);
  const ownerLegacyOperation = await setup.handler(request(
    "read_context",
    {},
    { secret: "", authorization: "Bearer owner" },
  ));
  assertEquals(ownerLegacyOperation.status, 403);
  const serviceWrite = await setup.handler(request(
    "checkpoint_discovery_stage",
    discoveryCheckpoint(),
  ));
  assertEquals(serviceWrite.status, 200);
});

Deno.test("discovery read preserves an authenticated non-owner rejection", async () => {
  const repository = Object.assign(new FakeRepository(), {
    readDiscoveryContext: () =>
      Promise.resolve({
        manifests: [],
        security_revisions: [],
        tasks: [],
        theme_episodes: [],
        exposure_facts: [],
        research_nominations: [],
        enrichment_selections: [],
      }),
  });
  const setup = makeHandler(repository, {
    ownerUserId: DISCOVERY_OWNER,
    verifyOwner: () =>
      Promise.reject(Object.assign(new Error("owner only"), { status: 403 })),
  });

  const response = await setup.handler(request(
    "read_discovery_context",
    { limit: 100 },
    { secret: "", authorization: "Bearer other" },
  ));

  assertEquals(response.status, 403);
  assertEquals((await json(response)).code, "OWNER_ONLY");
});

Deno.test("pinned reference read remains service-only for an owner session", async () => {
  const calls: string[] = [];
  const repository = Object.assign(new FakeRepository(), {
    pinDiscoveryReference: () => {
      calls.push("pin");
      return Promise.resolve({
        binding_role: "current",
        manifest_id: null,
        reference_status: "reference_unavailable",
        source_retrieved_at: null,
        reference_age_seconds: null,
        duplicate: false,
      });
    },
    readDiscoveryReference: () => {
      calls.push("read");
      return Promise.resolve({
        binding: {
          binding_role: "current",
          manifest_id: null,
          reference_status: "reference_unavailable",
          source_retrieved_at: null,
          reference_age_seconds: null,
        },
        manifest: null,
        securities: [],
        next_after_security_id: null,
        complete: true,
      });
    },
  });
  const setup = makeHandler(repository, {
    ownerUserId: DISCOVERY_OWNER,
    verifyOwner: discoveryOwnerVerifier,
  });
  const runId = "00000000-0000-4000-8000-000000000002";
  const servicePin = await setup.handler(request(
    "pin_discovery_reference",
    {
      capability_id: "sec_company_tickers_universe",
      binding_role: "current",
      manifest_id: null,
      reference_status: "reference_unavailable",
      reference_as_of: "2026-09-07T12:00:00.000Z",
    },
    { runId },
  ));
  const serviceRead = await setup.handler(request(
    "read_discovery_reference",
    {
      capability_id: "sec_company_tickers_universe",
      binding_role: "current",
      after_security_id: null,
      limit: 500,
    },
    { runId },
  ));
  assertEquals(servicePin.status, 200);
  assertEquals(serviceRead.status, 200);
  assertEquals(calls, ["pin", "read"]);
  const ownerRead = await setup.handler(request(
    "read_discovery_reference",
    {
      capability_id: "sec_company_tickers_universe",
      binding_role: "current",
      after_security_id: null,
      limit: 500,
    },
    { runId, secret: "", authorization: "Bearer owner" },
  ));
  assertEquals(ownerRead.status, 403);
  assertEquals((await json(ownerRead)).code, "SERVICE_ONLY");
});

Deno.test("dry-run reference read reports the requested binding role", async () => {
  const { handler } = makeHandler();
  const response = await handler(request(
    "read_discovery_reference",
    {
      capability_id: "sec_company_tickers_universe",
      binding_role: "predecessor",
      after_security_id: null,
      limit: 500,
    },
    { dry: true },
  ));

  assertEquals(response.status, 200);
  const body = await json(response);
  assertEquals(
    ((body.reference as Record<string, unknown>).binding as Record<
      string,
      unknown
    >)
      .binding_role,
    "predecessor",
  );
});

Deno.test("discovery checkpoint rejects wrong-stage result rows before persistence", async () => {
  let writes = 0;
  const repository = Object.assign(new FakeRepository(), {
    checkpointDiscoveryStage: (
      _runId: string,
      payload: ReturnType<typeof discoveryCheckpoint>,
    ) => {
      writes += 1;
      return Promise.resolve({ task: payload.task, duplicate: false });
    },
  });
  const setup = makeHandler(repository, {
    ownerUserId: DISCOVERY_OWNER,
    verifyOwner: discoveryOwnerVerifier,
  });
  const payload = discoveryCheckpoint("quote");
  Object.assign(payload.task, {
    state: "succeeded",
    attempt_count: 1,
    result: { count: 1 },
  });
  payload.exposure_facts.push({
    id: "00000000-0000-4000-8000-000000000043",
    security_revision_id: "00000000-0000-4000-8000-000000000044",
    theme_episode_revision_id: null,
    exposure_kind: "filing",
    fact: { basis: "10-K" },
    source_ids: ["sec:fixture"],
    valid_from: "2026-09-06T00:00:00.000Z",
    valid_to: null,
    content_hash: "d".repeat(64),
  });
  const response = await setup.handler(
    request("checkpoint_discovery_stage", payload),
  );
  assertEquals(response.status, 400);
  assertEquals(writes, 0);
});

Deno.test("discovery stage replay returns the durable duplicate receipt", async () => {
  let writes = 0;
  const repository = Object.assign(new FakeRepository(), {
    checkpointDiscoveryStage: (
      _runId: string,
      payload: ReturnType<typeof discoveryCheckpoint>,
    ) => {
      writes += 1;
      return Promise.resolve({ task: payload.task, duplicate: writes > 1 });
    },
  });
  const setup = makeHandler(repository, {
    ownerUserId: DISCOVERY_OWNER,
    verifyOwner: discoveryOwnerVerifier,
  });
  const requestId = "00000000-0000-4000-8000-000000000045";
  const first = await setup.handler(request(
    "checkpoint_discovery_stage",
    discoveryCheckpoint(),
    { requestId },
  ));
  const replay = await setup.handler(request(
    "checkpoint_discovery_stage",
    discoveryCheckpoint(),
    { requestId },
  ));
  assertEquals((await json(first)).duplicate, false);
  assertEquals((await json(replay)).duplicate, true);
  assertEquals(writes, 2);
});

Deno.test("malformed and streamed oversized bodies fail before repository", async () => {
  const { handler, repository } = makeHandler();
  const malformed = await handler(
    new Request("https://example.invalid", {
      method: "POST",
      headers: { "x-market-agent-secret": SECRET },
      body: "{",
    }),
  );
  assertEquals(malformed.status, 400);
  const oversized = await handler(
    new Request("https://example.invalid", {
      method: "POST",
      headers: { "x-market-agent-secret": SECRET },
      body: "x".repeat(262145),
    }),
  );
  assertEquals(oversized.status, 413);
  assertEquals(repository.mutationCalls, 0);
});

Deno.test("repository rate limit prevents market data work", async () => {
  const repository = new FakeRepository();
  repository.failCode = "RATE_LIMITED";
  const { handler, fetched } = makeHandler(repository);
  const response = await handler(
    request("evaluate_and_publish", {
      phase: "intraday",
      market_date: "2026-09-02",
      title: "x",
      candidates: [candidate()],
    }),
  );
  assertEquals(response.status, 429);
  assertEquals(fetched, []);
});

Deno.test("dry-run operations are write-free and report only their own effects", async () => {
  const { handler, repository, sent } = makeHandler();
  const start = await json(
    await handler(
      request("start_run", { phase: "intraday", market_date: "2026-09-02" }, {
        dry: true,
      }),
    ),
  );
  assert(typeof start.run_id === "string", "dry run id absent");
  assertEquals((start.data as Record<string, unknown>).run_id, start.run_id);
  const artifacts = await json(
    await handler(
      request("record_artifacts", {
        mutations: [{
          kind: "lesson",
          entry_date: "2026-09-02",
          category: "test",
          content: "test",
        }],
      }, { dry: true }),
    ),
  );
  assertEquals((artifacts.receipt as Record<string, unknown>).would_write, 1);
  const bundle = {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "ignored",
    candidates: [candidate("intraday", "brief")],
  };
  const evaluated = await json(
    await handler(request("evaluate_and_publish", bundle, { dry: true })),
  );
  assert("preview" in evaluated, "dry preview absent");
  const finished = await json(
    await handler(
      request("finish_run", { invented_counts: 999 }, { dry: true }),
    ),
  );
  assertEquals(finished.write_counts, {});
  assertEquals(repository.mutationCalls, 0);
  assertEquals(sent, []);
});

Deno.test("six individually valid purchases cannot exceed the growth allocation", async () => {
  const setup = makeHandler();
  setup.repository.policyValue.max_trade_risk_bps.growth = 1000;
  const purchases = Array.from({ length: 6 }, (_, index) => ({
    ...candidate("on-demand", "brief"),
    candidate_id: `00000000-0000-4000-8000-${
      String(index + 30).padStart(12, "0")
    }`,
    ticker: `G${index}`,
    proposed_amount: "1500",
    proposed_shares: "31.901318",
  }));
  const response = await setup.handler(request("evaluate_and_publish", {
    phase: "on-demand",
    market_date: "2026-09-02",
    title: "Growth candidates",
    candidates: purchases,
  }, { dry: true }));
  assertEquals(response.status, 200);
  const result = await json(response);
  const evaluations = (result.evaluations ?? []) as Array<{
    final_action: string | null;
    candidate: { proposed_amount: string | null };
    reason_codes: string[];
  }>;
  const approvedCost = evaluations
    .filter((item) => item.final_action === "buy")
    .reduce((total, item) => total + Number(item.candidate.proposed_amount), 0);
  assert(approvedCost <= 8100, "approved purchases exceeded growth allocation");
  assert(
    evaluations.some((item) =>
      new Set<string>(item.reason_codes).has("PORTFOLIO_BUDGET_EXCEEDED")
    ),
    "portfolio-level rejection was absent",
  );
});

Deno.test("unreconciled cash and mutually exclusive purchases fail closed", async () => {
  const unavailable = makeHandler();
  unavailable.repository.context.reconciled_cash_snapshot = undefined;
  unavailable.repository.context.dry_powder = [{
    month: "2026-09",
    growth_available: "999999999",
    spec_available: "999999999",
    rolled_months: 99,
  }];
  const missingCash = await json(
    await unavailable.handler(request(
      "evaluate_and_publish",
      {
        phase: "on-demand",
        market_date: "2026-09-02",
        title: "Cash check",
        candidates: [candidate("on-demand", "brief")],
      },
      { dry: true },
    )),
  );
  const cashEvaluation = (missingCash.evaluations as Array<{
    final_action: string | null;
    reason_codes: string[];
  }>)[0];
  assertEquals(cashEvaluation.final_action, "watch");
  assert(
    new Set(cashEvaluation.reason_codes).has("CASH_UNAVAILABLE"),
    "missing cash passed",
  );

  const stale = makeHandler();
  stale.repository.context.reconciled_cash_snapshot = {
    ...stale.repository.context.reconciled_cash_snapshot!,
    fresh_through: "2026-09-02T16:59:59.000Z",
  };
  const staleResult = await json(
    await stale.handler(request(
      "evaluate_and_publish",
      {
        phase: "on-demand",
        market_date: "2026-09-02",
        title: "Stale cash check",
        candidates: [candidate("on-demand", "brief")],
      },
      { dry: true },
    )),
  );
  const staleEvaluation = (staleResult.evaluations as Array<{
    final_action: string | null;
    reason_codes: string[];
  }>)[0];
  assertEquals(staleEvaluation.final_action, "watch");
  assert(
    staleEvaluation.reason_codes.includes("CASH_UNAVAILABLE"),
    "stale cash snapshot passed",
  );

  const alternatives = makeHandler();
  alternatives.repository.policyValue.max_trade_risk_bps.growth = 1000;
  const proposals = [0, 1].map((index) => ({
    ...candidate("on-demand", "brief"),
    candidate_id: `00000000-0000-4000-8000-${
      String(index + 50).padStart(12, "0")
    }`,
    ticker: `A${index}`,
    reservation_group: "same-idea",
  }));
  const alternativeResult = await json(
    await alternatives.handler(request(
      "evaluate_and_publish",
      {
        phase: "on-demand",
        market_date: "2026-09-02",
        title: "Alternative ideas",
        candidates: proposals,
      },
      { dry: true },
    )),
  );
  const alternativeEvaluations = alternativeResult.evaluations as Array<{
    final_action: string | null;
    reason_codes: string[];
  }>;
  assertEquals(
    alternativeEvaluations.filter((item) => item.final_action === "buy").length,
    1,
  );
  assert(
    alternativeEvaluations.some((item) =>
      new Set(item.reason_codes).has("MUTUALLY_EXCLUSIVE_ALTERNATIVE")
    ),
    "mutually exclusive proposal was not labeled as an alternative",
  );
});

Deno.test("existing stop exposure reserves portfolio risk before a new purchase", async () => {
  const setup = makeHandler();
  setup.repository.policyValue.max_trade_risk_bps.growth = 400;
  setup.repository.context.holdings.push({
    ticker: "GROW",
    shares: "100",
    avg_cost: "60",
    bucket: "growth",
    stop: "42",
    target: null,
    high_water_price: null,
    hold_override_until: null,
    stop_alert_active: false,
    stop_near_alert_active: false,
    target_near_alert_active: false,
    target_alert_active: false,
  });
  const result = await json(
    await setup.handler(request(
      "evaluate_and_publish",
      {
        phase: "on-demand",
        market_date: "2026-09-02",
        title: "Risk reservation",
        candidates: [candidate("on-demand", "brief")],
      },
      { dry: true },
    )),
  );
  const evaluation = (result.evaluations as Array<{
    final_action: string | null;
    reason_codes: string[];
  }>)[0];
  assertEquals(evaluation.final_action, "watch");
  assert(
    new Set(evaluation.reason_codes).has("PORTFOLIO_BUDGET_EXCEEDED"),
    "existing stop exposure did not consume the risk limit",
  );
});

Deno.test("owner-plan Core purchase requires cash but not a stop-derived risk value", async () => {
  const ownerPlanCandidate = {
    ...candidate("on-demand", "brief"),
    ticker: "VTI",
    action: "buy",
    decision_mode: "owner_plan",
    bucket: "core",
    proposed_amount: "300",
    proposed_shares: "0.75",
    entry_zone_low: null,
    entry_zone_high: null,
    stop: null,
    target: null,
    invalidation_price: null,
  };
  const setup = makeHandler();
  setup.repository.policyValue.allocation_bps.core = 10000;
  setup.repository.context.holdings = [{
    ...setup.repository.context.holdings[0],
    ticker: "VOO",
    avg_cost: "480",
    high_water_price: "510",
  }];
  setup.repository.context.owner_plans = [{
    id: "00000000-0000-4000-8000-000000000088",
    ticker: "VTI",
    bucket: "core",
    amount: "300",
    cadence: "monthly",
    next_due_on: "2026-09-01",
    active: true,
    updated_at: "2026-09-02T12:00:00.000Z",
  }];
  setup.repository.context.reconciled_cash_snapshot = {
    snapshot_id: "00000000-0000-4000-8000-000000000099",
    as_of: "2026-09-02T16:59:00.000Z",
    fresh_through: "2026-09-02T17:14:00.000Z",
    ledger_watermark: "0",
    spendable_cash: { core: "300", growth: "10000", speculative: "0" },
  };
  const approved = await json(
    await setup.handler(request(
      "evaluate_and_publish",
      {
        phase: "on-demand",
        market_date: "2026-09-02",
        title: "Core contribution",
        candidates: [ownerPlanCandidate],
      },
      { dry: true },
    )),
  );
  const approvedEvaluation = (approved.evaluations as Array<{
    final_action: string | null;
    reason_codes: string[];
  }>)[0];
  assertEquals(approvedEvaluation.final_action, "buy");

  const unavailable = makeHandler();
  unavailable.repository.context.holdings = structuredClone(
    setup.repository.context.holdings,
  );
  unavailable.repository.context.owner_plans = structuredClone(
    setup.repository.context.owner_plans,
  );
  unavailable.repository.context.reconciled_cash_snapshot = undefined;
  const missingCash = await json(
    await unavailable.handler(request(
      "evaluate_and_publish",
      {
        phase: "on-demand",
        market_date: "2026-09-02",
        title: "Core contribution",
        candidates: [ownerPlanCandidate],
      },
      { dry: true },
    )),
  );
  const missingCashEvaluation = (missingCash.evaluations as Array<{
    final_action: string | null;
    reason_codes: string[];
  }>)[0];
  assertEquals(missingCashEvaluation.final_action, "watch");
  assert(
    new Set(missingCashEvaluation.reason_codes).has("CASH_UNAVAILABLE"),
    "owner-plan cash availability did not fail closed",
  );
});

Deno.test("on-demand alternatives are history-computed by the gateway and remain send-free", async () => {
  const repository = new FakeRepository();
  const setup = makeHandler(repository, {
    fetchHistory: (ticker: string) =>
      Promise.resolve(comparisonHistory(ticker)),
  });
  const vti = {
    ...candidate("on-demand", "brief"),
    ticker: "VTI",
    candidate_id: "00000000-0000-4000-8000-000000000020",
  };
  const itot = {
    ...candidate("on-demand", "brief"),
    ticker: "ITOT",
    candidate_id: "00000000-0000-4000-8000-000000000021",
    action: "watch",
    proposed_amount: null,
    proposed_shares: null,
    entry_zone_low: null,
    entry_zone_high: null,
    stop: null,
    target: null,
    invalidation_price: null,
    valid_until: null,
    evidence: [{
      id: "itot-profile",
      kind: "fundamentals",
      source: "ishares",
      status: "fresh",
      observed_at: "2026-09-02T16:00:00.000Z",
      retrieved_at: "2026-09-02T16:01:00.000Z",
      reference: null,
      claims: ["Broad U.S. market exposure."],
    }],
    factors: [{
      kind: "fundamentals",
      stance: "neutral",
      text: "Broad U.S. market exposure is similar.",
      evidence_ids: ["itot-profile"],
    }],
    analyst: {
      completed: true,
      action: "watch",
      confidence: "medium",
      reason: "Like-for-like research comparison only.",
    },
  };
  const bundle = {
    phase: "on-demand",
    market_date: "2026-09-02",
    title: "VTI alternatives",
    candidates: [vti, itot],
    comparisons: [{
      baseline_ticker: "VTI",
      alternative_ticker: "ITOT",
      relationship: "like_for_like",
      prospective_view: "similar",
      reason: "Both provide broad U.S. market exposure.",
      evidence_ids: ["itot-profile"],
    }],
  };
  const result = await json(
    await setup.handler(request("evaluate_and_publish", bundle, { dry: true })),
  );
  assertEquals(result.comparison_count, 1);
  assertEquals(result.comparison_coverage, { complete: 1 });
  assert(
    typeof result.preview === "string" &&
      result.preview.includes("VTI ↔ ITOT · LIKE-FOR-LIKE"),
    "server-computed comparison preview absent",
  );
  assertEquals(repository.mutationCalls, 0);
  assertEquals(setup.sent, []);
});

Deno.test("long-term companion is range-computed and enforces dry-run and monthly cadence", async () => {
  const repository = new FakeRepository();
  repository.context.owner_plans = [{
    id: "00000000-0000-4000-8000-000000000088",
    ticker: "VTI",
    bucket: "core",
    amount: "300",
    cadence: "monthly",
    next_due_on: "2026-09-21",
    active: true,
    updated_at: "2026-09-02T12:00:00.000Z",
  }];
  const historyRequests: string[] = [];
  const setup = makeHandler(repository, {
    fetchHistory: (ticker: string, range = "1y") => {
      historyRequests.push(`${ticker}:${range}`);
      return Promise.resolve(
        range === "10y" ? longTermHistory(ticker) : comparisonHistory(ticker),
      );
    },
  });
  const vti = {
    ...candidate("on-demand", "brief"),
    ticker: "VTI",
    candidate_id: "00000000-0000-4000-8000-000000000020",
    action: "watch",
    proposed_amount: null,
    proposed_shares: null,
    entry_zone_low: null,
    entry_zone_high: null,
    stop: null,
    target: null,
    invalidation_price: null,
    valid_until: null,
    analyst: {
      completed: true,
      action: "watch",
      confidence: "medium",
      reason: "Recorded baseline research only.",
    },
  };
  const vxus = {
    ...vti,
    ticker: "VXUS",
    candidate_id: "00000000-0000-4000-8000-000000000021",
    evidence: [{
      id: "vxus-profile",
      kind: "fundamentals",
      source: "vanguard",
      status: "fresh",
      observed_at: "2026-09-02T16:00:00.000Z",
      retrieved_at: "2026-09-02T16:01:00.000Z",
      reference:
        "https://investor.vanguard.com/investment-products/etfs/profile/vxus",
      claims: [
        "The fund covers developed and emerging non-U.S. equity markets.",
      ],
    }],
    factors: [{
      kind: "fundamentals",
      stance: "neutral",
      text: "Non-U.S. exposure adds a distinct geographic role.",
      evidence_ids: ["vxus-profile"],
    }],
  };
  const bundle = {
    phase: "on-demand",
    market_date: "2026-09-02",
    title: "Long-term companion review",
    candidates: [vti, vxus],
    comparisons: [{
      baseline_ticker: "VTI",
      alternative_ticker: "VXUS",
      relationship: "diversifier",
      prospective_view: "similar",
      reason: "The candidate adds a distinct geographic role.",
      evidence_ids: ["vxus-profile"],
    }],
    companion_proposal: {
      baseline_ticker: "VTI",
      companion_ticker: "VXUS",
      role: "diversifier",
      thesis: "Non-U.S. exposure adds a distinct geographic role.",
      risk_note:
        "Currency and foreign-market risks can cause long periods of lagging U.S. stocks.",
      evidence_ids: ["vxus-profile"],
    },
  };
  const result = await json(
    await setup.handler(request("evaluate_and_publish", bundle, { dry: true })),
  );

  assertEquals(result.publication_status, "suppressed");
  assertEquals(result.companion_status, "qualified");
  assertEquals(
    (result.companion_analysis as { recurring_plan_review_eligible: boolean })
      .recurring_plan_review_eligible,
    true,
  );
  assertEquals(historyRequests.sort(), [
    "VTI:10y",
    "VTI:1y",
    "VXUS:10y",
    "VXUS:1y",
  ]);
  assertEquals(repository.mutationCalls, 0);
  assertEquals(setup.sent, []);

  const liveRepository = new FakeRepository();
  const live = makeHandler(liveRepository);
  const liveResponse = await live.handler(
    request("evaluate_and_publish", bundle),
  );
  assertEquals(liveResponse.status, 400);
  assertEquals((await json(liveResponse)).code, "INVALID_REQUEST");
  assertEquals(liveRepository.mutationCalls, 0);
  assertEquals(liveRepository.readCalls, 0);
  assertEquals(live.fetched, []);
  assertEquals(live.sent, []);

  const cadenceRepository = new FakeRepository();
  const cadence = makeHandler(cadenceRepository);
  const premarketBundle = structuredClone(bundle);
  premarketBundle.phase = "pre-market";
  for (const item of premarketBundle.candidates) item.phase = "pre-market";
  const cadenceResponse = await cadence.handler(
    request("evaluate_and_publish", premarketBundle, { dry: true }),
  );
  assertEquals(cadenceResponse.status, 409);
  assertEquals((await json(cadenceResponse)).code, "POLICY_REJECTED");
  assertEquals(cadenceRepository.mutationCalls, 0);
  assertEquals(cadence.fetched, []);
  assertEquals(cadence.sent, []);
});

Deno.test("stale companion evidence fails closed without long-term history or side effects", async () => {
  const repository = new FakeRepository();
  let historyCalls = 0;
  const setup = makeHandler(repository, {
    fetchHistory: (ticker: string) => {
      historyCalls += 1;
      return Promise.resolve(comparisonHistory(ticker));
    },
  });
  const vti = {
    ...candidate("on-demand", "brief"),
    ticker: "VTI",
    candidate_id: "00000000-0000-4000-8000-000000000020",
  };
  const vxus = {
    ...candidate("on-demand", "brief"),
    ticker: "VXUS",
    candidate_id: "00000000-0000-4000-8000-000000000021",
    evidence: [{
      ...candidate().evidence[0],
      id: "vxus-stale",
      status: "stale",
    }],
    factors: [{
      kind: "risk",
      stance: "neutral",
      text: "Stale geographic exposure evidence.",
      evidence_ids: ["vxus-stale"],
    }],
  };
  const result = await json(
    await setup.handler(request("evaluate_and_publish", {
      phase: "on-demand",
      market_date: "2026-09-02",
      title: "Long-term companion review",
      candidates: [vti, vxus],
      comparisons: [{
        baseline_ticker: "VTI",
        alternative_ticker: "VXUS",
        relationship: "diversifier",
        prospective_view: "stronger",
        reason: "Stale evidence cannot support this view.",
        evidence_ids: ["vxus-stale"],
      }],
      companion_proposal: {
        baseline_ticker: "VTI",
        companion_ticker: "VXUS",
        role: "diversifier",
        thesis: "Stale evidence cannot support a current conclusion.",
        risk_note: "Current risks are unavailable.",
        evidence_ids: ["vxus-stale"],
      },
    }, { dry: true })),
  );
  assertEquals(result.companion_status, "insufficient");
  assertEquals(historyCalls, 2);
  assertEquals(repository.mutationCalls, 0);
  assertEquals(setup.sent, []);
});

Deno.test("shadow alert drafts are rendered but never persisted", async () => {
  const dryRepository = new FakeRepository();
  dryRepository.policyValue.alerts_v3 = {
    enabled: false,
    shadow: true,
    enabled_classes: [],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  const dry = makeHandler(dryRepository);
  const bundle = {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "ignored",
    candidates: [candidate()],
  };
  const dryReceipt = await json(
    await dry.handler(request("evaluate_and_publish", bundle, { dry: true })),
  );
  assertEquals(dryReceipt.would_create_alert_drafts, 1);
  assertEquals(dryRepository.createDraftCalls, 0);
  assertEquals(dry.sent, []);

  const liveRepository = new FakeRepository();
  liveRepository.policyValue.alerts_v3 = structuredClone(
    dryRepository.policyValue.alerts_v3,
  );
  const live = makeHandler(liveRepository);
  const liveReceipt = await json(
    await live.handler(request("evaluate_and_publish", bundle)),
  );
  assertEquals(liveReceipt.alert_drafts_created, 0);
  assertEquals(liveReceipt.alert_draft_status, "shadow_preview");
  assertEquals((liveReceipt.alert_draft_previews as unknown[]).length, 1);
  assertEquals(liveRepository.createDraftCalls, 0);
});

Deno.test("shadow preview cannot label watch-only entry levels as policy approved", async () => {
  const repository = new FakeRepository();
  repository.policyValue.alerts_v3 = {
    enabled: false,
    shadow: true,
    enabled_classes: [],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  const setup = makeHandler(repository);
  const watch = {
    ...candidate(),
    action: "watch",
    proposed_amount: null,
    proposed_shares: null,
    analyst: {
      completed: true,
      action: "watch",
      confidence: "medium",
      reason: "Watch only.",
    },
  };
  const bundle = {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "ignored",
    candidates: [watch],
  };
  const result = await json(
    await setup.handler(request("evaluate_and_publish", bundle, { dry: true })),
  );
  assertEquals(result.would_create_alert_drafts, 0);
  assertEquals(result.alert_draft_previews, []);
  assertEquals(repository.createDraftCalls, 0);
  assertEquals(setup.sentAlerts, []);
});

Deno.test("enabled canary creates drafts only for an allowlisted alert class", async () => {
  const repository = new FakeRepository();
  repository.policyValue.alerts_v3 = {
    enabled: true,
    shadow: false,
    enabled_classes: ["stop_breach"],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  const setup = makeHandler(repository);
  const bundle = {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "ignored",
    candidates: [candidate()],
  };
  const result = await json(
    await setup.handler(request("evaluate_and_publish", bundle)),
  );
  assertEquals(result.alert_drafts_created, 0);
  assertEquals(result.alert_draft_status, "not_applicable");
  assertEquals(repository.createDraftCalls, 0);
  assertEquals(setup.sentAlerts, []);
});

Deno.test("enabled canary does not evaluate active rules outside its allowlist", async () => {
  const repository = new FakeRepository();
  repository.policyValue.version = 3;
  repository.policyValue.alerts_v3 = {
    enabled: true,
    shadow: false,
    enabled_classes: ["stop_breach"],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  repository.alertWorkValue = alertWork({ rule: true, draft: true });
  const setup = makeHandler(repository);
  const result = await json(
    await setup.handler(request("evaluate_alert_rules", {}, { dry: true })),
  );
  assertEquals(result.evaluated_rules, 0);
  assertEquals(result.would_write_events, 0);
  assertEquals(result.would_publish, 0);
  assertEquals(result.draft_previews, []);
  assertEquals(setup.alertFetches, []);
  assertEquals(setup.sentAlerts, []);
});

Deno.test("alert evaluation dry-run uses server evidence and remains write-free and send-free", async () => {
  const repository = new FakeRepository();
  repository.policyValue.alerts_v3 = {
    enabled: false,
    shadow: true,
    enabled_classes: [],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  repository.alertWorkValue = alertWork({ rule: true, draft: true });
  const setup = makeHandler(repository);
  const result = await json(
    await setup.handler(request("evaluate_alert_rules", {}, { dry: true })),
  );
  assertEquals(result.evaluated_rules, 1);
  assertEquals(result.would_write_events, 1);
  assertEquals(result.would_publish, 0);
  assertEquals(result.shadow_publish_candidates, 1);
  assertEquals((result.draft_previews as unknown[]).length, 1);
  assertEquals((result.alert_previews as unknown[]).length, 1);
  assertEquals(repository.mutationCalls, 0);
  assertEquals(setup.alertFetches, ["CENX"]);
  assertEquals(setup.sentAlerts, []);

  const injected = await setup.handler(
    request("evaluate_alert_rules", { quote: { price: "1" } }, { dry: true }),
  );
  assertEquals(injected.status, 400);
  assertEquals(setup.alertFetches, ["CENX"]);
});

Deno.test("enabled alert evaluation persists receipts before one Telegram alert", async () => {
  const repository = new FakeRepository();
  repository.policyValue.alerts_v3 = {
    enabled: true,
    shadow: false,
    enabled_classes: ["entry_trigger"],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  repository.alertWorkValue = alertWork({ rule: true, draft: false });
  const setup = makeHandler(repository);
  const result = await json(
    await setup.handler(request("evaluate_alert_rules", {})),
  );
  assertEquals(result.alert_events_recorded, 1);
  assertEquals(result.publication_status, "delivered");
  assertEquals(result.telegram_message_ids, [78]);
  assertEquals(repository.events, [
    "persist-alert-events",
    "persist-alert-publication",
    "claim-publication",
    "send-alert",
  ]);
  assertEquals(repository.expireAlertRuleCalls, 1);
  assertEquals(repository.finishAlertPublicationCalls, [{
    status: "delivered",
    ids: [78],
    acceptedAt: NOW.toISOString(),
  }]);
  assertEquals(repository.finishPublicationCalls, []);
  assertEquals(setup.sentAlerts.length, 1);
});

Deno.test("alert evaluation records no-trigger, unsafe, shadow, and cooldown outcomes narrowly", async () => {
  const noRules = new FakeRepository();
  noRules.policyValue.alerts_v3 = {
    enabled: false,
    shadow: true,
    enabled_classes: [],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  const quiet = makeHandler(noRules);
  const quietResult = await json(
    await quiet.handler(request("evaluate_alert_rules", {}, { dry: true })),
  );
  assertEquals(quietResult.evaluated_rules, 0);
  assertEquals(quietResult.would_write_events, 0);
  assertEquals(quietResult.would_publish, 0);
  assertEquals(quiet.alertFetches, []);

  const unsafeRepository = new FakeRepository();
  unsafeRepository.policyValue.alerts_v3 = structuredClone(
    noRules.policyValue.alerts_v3,
  );
  const unsupported = alertRule();
  unsupported.confirmation = "bar_close";
  unsupported.conditions = [{
    kind: "volume_multiple",
    operator: "above",
    left: "1.5",
    right: null,
    timeframe: "1d",
  }];
  unsafeRepository.alertWorkValue = {
    rules: [{ rule: unsupported, recent_events: [], source_summary: null }],
    drafts: [],
  };
  const unsafe = makeHandler(unsafeRepository);
  const unsafeResult = await json(
    await unsafe.handler(request("evaluate_alert_rules", {}, { dry: true })),
  );
  assertEquals(unsafeResult.unsafe_evaluations, 1);
  assertEquals(unsafeResult.would_write_events, 1);
  assertEquals(unsafeResult.shadow_publish_candidates, 0);
  assertEquals(unsafe.sentAlerts, []);

  const shadowRepository = new FakeRepository();
  shadowRepository.policyValue.alerts_v3 = structuredClone(
    noRules.policyValue.alerts_v3,
  );
  shadowRepository.alertWorkValue = alertWork({ rule: true, draft: false });
  const shadow = makeHandler(shadowRepository);
  const shadowResult = await json(
    await shadow.handler(request("evaluate_alert_rules", {})),
  );
  assertEquals(shadowResult.status, "shadow");
  assertEquals(shadowResult.alert_events_recorded, 0);
  assertEquals(shadowRepository.recordAlertEventCalls, 0);
  assertEquals(shadowRepository.createAlertPublicationCalls, 0);
  assertEquals(shadowRepository.expireAlertRuleCalls, 0);
  assertEquals(shadow.sentAlerts, []);

  const cooldownRepository = new FakeRepository();
  cooldownRepository.policyValue.alerts_v3 = structuredClone(
    noRules.policyValue.alerts_v3,
  );
  cooldownRepository.alertWorkValue = alertWork({ rule: true, draft: false });
  cooldownRepository.alertWorkValue.rules[0].recent_events = [{
    fingerprint: "a".repeat(64),
    status: "triggered",
    evaluated_at: "2026-09-02T16:59:00.000Z",
    severity: "review",
  }];
  const cooldown = makeHandler(cooldownRepository);
  const cooldownResult = await json(
    await cooldown.handler(request("evaluate_alert_rules", {}, { dry: true })),
  );
  assertEquals(cooldownResult.would_write_events, 0);
  assertEquals(cooldownResult.shadow_publish_candidates, 0);
});

Deno.test("enabled draft proposal is persisted before Telegram and remains monitoring-only", async () => {
  const repository = new FakeRepository();
  repository.policyValue.alerts_v3 = {
    enabled: true,
    shadow: false,
    enabled_classes: ["entry_trigger"],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  repository.alertWorkValue = alertWork({ rule: false, draft: true });
  const setup = makeHandler(repository);
  const result = await json(
    await setup.handler(request("evaluate_alert_rules", {})),
  );
  assertEquals(result.alert_events_recorded, 0);
  assertEquals(result.publication_status, "delivered");
  assertEquals(repository.events, [
    "persist-alert-publication",
    "claim-publication",
    "send-alert",
  ]);
  const sent = setup.sentAlerts[0] as {
    body: string;
    reply_markup: { inline_keyboard: Array<Array<{ text: string }>> };
  };
  assert(
    sent.body.includes("policy-approved stop $42.00 • target $58.00"),
    "draft levels absent",
  );
  assert(
    sent.body.includes("inert until you arm it"),
    "draft authority boundary absent",
  );
  assert(
    sent.body.includes("Proposed at"),
    "draft without a quote used event timing language",
  );
  assertEquals(
    sent.reply_markup.inline_keyboard[0].map((button) => button.text),
    ["Arm", "Dismiss"],
  );
});

Deno.test("multiple simultaneous triggers publish one deterministically without consuming the rest", async () => {
  const repository = new FakeRepository();
  repository.policyValue.alerts_v3 = {
    enabled: true,
    shadow: false,
    enabled_classes: ["entry_trigger"],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  const first = alertWork({ rule: true, draft: false }).rules[0];
  const second = structuredClone(first);
  second.rule.rule_id = "8f7f70bf-5cec-4f1e-9de8-ec8823d99fc7";
  second.rule.ticker = "XYZ";
  second.source_summary!.ticker = "XYZ";
  repository.alertWorkValue = { rules: [first, second], drafts: [] };
  const setup = makeHandler(repository);
  const result = await json(
    await setup.handler(request("evaluate_alert_rules", {})),
  );
  assertEquals(result.alert_events_recorded, 1);
  assertEquals(result.publication_status, "delivered");
  assertEquals(repository.createAlertPublicationCalls, 1);
  assertEquals(setup.sentAlerts.length, 1);
  assertEquals(
    (repository.lastAlertEvents[0] as { rule_id: string }).rule_id,
    first.rule.rule_id,
  );
});

Deno.test("live start_run is idempotent", async () => {
  const { handler, repository } = makeHandler();
  const id = nextRequestId();
  const first = await json(
    await handler(
      request("start_run", { phase: "intraday", market_date: "2026-09-02" }, {
        requestId: id,
      }),
    ),
  );
  const second = await json(
    await handler(
      request("start_run", { phase: "intraday", market_date: "2026-09-02" }, {
        requestId: id,
      }),
    ),
  );
  assertEquals(first.run_id, RUN_ID);
  assertEquals(second.run_id, RUN_ID);
  assertEquals(first.duplicate, false);
  assertEquals(second.duplicate, false);
  assertEquals(repository.startCalls, 1);
});

Deno.test("different request ids for one scheduled market slot return the same run", async () => {
  const { handler, repository } = makeHandler();
  const first = await json(
    await handler(request(
      "start_run",
      { phase: "intraday", market_date: "2026-09-02" },
      { requestId: nextRequestId() },
    )),
  );
  const second = await json(
    await handler(request(
      "start_run",
      { phase: "intraday", market_date: "2026-09-02" },
      { requestId: nextRequestId() },
    )),
  );
  assertEquals(first.run_id, second.run_id);
  assertEquals(first.duplicate, false);
  assertEquals(second.duplicate, true);
  assertEquals(repository.scheduledSlots, [
    "2026-09-02:intraday",
    "2026-09-02:intraday",
  ]);
});

Deno.test("finish_run returns the specific missing lifecycle stage", async () => {
  class MissingStageRepository extends FakeRepository {
    override finishRun(): Promise<RunReceipt> {
      throw new GatewayRepositoryError("MISSING_COLLECTION_RECEIPT");
    }
  }
  const { handler } = makeHandler(new MissingStageRepository());
  const result = await handler(request("finish_run", {}));
  assertEquals(result.status, 409);
  assertEquals((await json(result)).code, "MISSING_COLLECTION_RECEIPT");
});

Deno.test("record_artifacts derives paper-watch date, quote, and latest gateway view", async () => {
  const repository = new FakeRepository();
  repository.context.recent_suggestions = [{
    id: 4,
    date: "2026-09-01",
    ticker: "CENX",
    action: "watch",
    bucket: "growth",
    confidence: "medium",
    score: 72,
    stop: null,
    target: null,
    invalidation_price: null,
    valid_until: null,
    evidence_as_of: null,
  }];
  const { handler, fetched } = makeHandler(repository);
  const response = await handler(request("record_artifacts", {
    mutations: [{
      kind: "paper_watch_create",
      ticker: "CENX",
      entry_ref_price: "9999",
      target_price: null,
      hypothetical_amount: "500",
      thesis: "Owner hypothesis",
      horizon: "weeks",
    }],
  }));
  assertEquals(response.status, 200);
  const mutation = repository.lastArtifacts!.mutations[0] as Record<
    string,
    unknown
  >;
  assertEquals(mutation.created, "2026-09-02");
  assertEquals(mutation.entry_ref_price, "47.02");
  assertEquals(mutation.agent_view_at_open, "watch");
  assertEquals(mutation.agent_score_at_open, 72);
  assertEquals(fetched, ["CENX"]);
});

Deno.test("intraday evaluation refetches every quote, persists a suppression receipt, and leaves delivery to the report or alert key", async () => {
  const { handler, repository, sent, fetched } = makeHandler();
  const bundle = {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "ignored",
    candidates: [candidate()],
  };
  const response = await handler(request("evaluate_and_publish", bundle));
  assertEquals(response.status, 200);
  assertEquals(fetched.sort(), ["CENX", "VTI"]);
  assertEquals(repository.events, ["persist"]);
  assertEquals(
    repository.lastBundle!.evaluations[0].normalized.verified_price,
    "47.02",
  );
  assertEquals(
    repository.lastBundle!.evaluations[0].normalized.total_investable_value,
    "40500",
  );
  assertEquals(repository.lastBundle!.cash_snapshot, {
    snapshot_id: "00000000-0000-4000-8000-000000000099",
    ledger_watermark: "0",
  });
  assertEquals(repository.lastBundle!.publication.template_version, 2);
  assertEquals(repository.lastBundle!.publication.status, "suppressed");
  assertEquals(sent.length, 0);
  assertEquals(repository.finishPublicationCalls, []);
});

Deno.test("periodic evaluation persists a suppression receipt and leaves report delivery to its deterministic report key", async () => {
  const { handler, repository, sent } = makeHandler();
  const response = await handler(request("evaluate_and_publish", {
    phase: "pre-market",
    market_date: "2026-09-02",
    title: "ignored",
    candidates: [candidate("pre-market")],
  }));
  assertEquals(response.status, 200);
  assertEquals(repository.lastBundle!.publication.status, "suppressed");
  assertEquals(sent, []);
});

Deno.test("persistence failure prevents Telegram and scheduled evaluation never bypasses report delivery authority", async () => {
  class FailingRepository extends FakeRepository {
    override applyDecisionBundle(): Promise<PublicationReceipt> {
      throw new GatewayRepositoryError("PERSISTENCE_FAILED");
    }
  }
  const failed = makeHandler(new FailingRepository());
  const bundle = {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "x",
    candidates: [candidate("intraday", "brief")],
  };
  assertEquals(
    (await failed.handler(request("evaluate_and_publish", bundle))).status,
    500,
  );
  assertEquals(failed.sent, []);

  const scheduled = makeHandler();
  assertEquals(
    (await scheduled.handler(request("evaluate_and_publish", bundle))).status,
    200,
  );
  assertEquals(scheduled.sent, []);
});

Deno.test("suppressed intraday and on-demand outputs never call Telegram", async () => {
  const intraday = makeHandler();
  const quietBundle = {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "x",
    candidates: [candidate("intraday", "brief")],
  };
  assertEquals(
    (await intraday.handler(request("evaluate_and_publish", quietBundle)))
      .status,
    200,
  );
  assertEquals(intraday.sent, []);
  assertEquals(
    intraday.repository.lastBundle!.publication.status,
    "suppressed",
  );
  assertEquals(intraday.repository.runOutcomes, [{
    runId: RUN_ID,
    outcome: "no_trigger",
  }]);

  const onDemand = makeHandler();
  const onDemandBundle = {
    phase: "on-demand",
    market_date: "2026-09-02",
    title: "x",
    candidates: [candidate("on-demand", "brief")],
  };
  const output = await json(
    await onDemand.handler(request("evaluate_and_publish", onDemandBundle)),
  );
  assertEquals(onDemand.sent, []);
  assert(
    typeof output.preview === "string" && output.preview.includes("ON-DEMAND"),
    "session preview absent",
  );
});

Deno.test("duplicate scheduled evaluation requests never send outside the report or alert delivery keys", async () => {
  const bundle = {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "x",
    candidates: [candidate()],
  };
  const delivered = makeHandler();
  const deliveredId = nextRequestId();
  await delivered.handler(
    request("evaluate_and_publish", bundle, { requestId: deliveredId }),
  );
  await delivered.handler(
    request("evaluate_and_publish", bundle, { requestId: deliveredId }),
  );
  assertEquals(delivered.sent.length, 0);

  const ambiguousRepo = new FakeRepository();
  let attempts = 0;
  const ambiguous = makeHandler(ambiguousRepo, {
    sendTelegram: () => {
      attempts += 1;
      return Promise.reject(new TelegramDeliveryError("ambiguous", []));
    },
  });
  const ambiguousId = nextRequestId();
  await ambiguous.handler(
    request("evaluate_and_publish", bundle, { requestId: ambiguousId }),
  );
  await ambiguous.handler(
    request("evaluate_and_publish", bundle, { requestId: ambiguousId }),
  );
  assertEquals(attempts, 0);
});

Deno.test("a second scheduled evaluation cannot reuse another request's suppression receipt", async () => {
  class OnePublicationRepository extends FakeRepository {
    override applyDecisionBundle(
      input: PersistedBundle,
    ): Promise<PublicationReceipt> {
      if (this.applyCalls > 0) {
        this.applyCalls += 1;
        return Promise.reject(
          new GatewayRepositoryError("RUN_ALREADY_EVALUATED"),
        );
      }
      return super.applyDecisionBundle(input);
    }
  }
  const setup = makeHandler(new OnePublicationRepository());
  const bundle = {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "x",
    candidates: [candidate("intraday", "brief")],
  };
  await setup.handler(request("evaluate_and_publish", bundle));
  const replay = await setup.handler(request("evaluate_and_publish", bundle));
  assertEquals(replay.status, 409);
  assertEquals((await json(replay)).code, "RUN_ALREADY_EVALUATED");
  assertEquals(setup.sent.length, 0);
  assertEquals(setup.repository.applyCalls, 2);
  assertEquals(setup.repository.runOutcomes.length, 1);
});

Deno.test("theme-memory review and nomination writes stay service-authenticated and research-only", async () => {
  class ThemeMemoryRepository extends FakeRepository {
    themeMemoryWrites: unknown[] = [];
    recordResearchReviewIdentityV2(runId: string, receiptId: string, payload: Record<string, unknown>) {
      this.themeMemoryWrites.push({ kind: "review", runId, receiptId, payload });
      return Promise.resolve({ receipt_id: receiptId, reviewed_role: payload.reviewed_role });
    }
    recordResearchNominations(runId: string, requestId: string, payload: Record<string, unknown>) {
      this.themeMemoryWrites.push({ kind: "nominations", runId, requestId, payload });
      return Promise.resolve({ accepted_count: 1, duplicate: false, nominations: [{ nomination_id: "00000000-0000-4000-8000-000000000088" }] });
    }
  }
  const repository = new ThemeMemoryRepository();
  const setup = makeHandler(repository);
  const reviewerId = "00000000-0000-4000-8000-000000000087";
  const review = await setup.handler(request("record_research_review_identity_v2", {
    actor_identity: "analyst-fixture", reviewed_role: "analyst", predecessor_receipt_id: null,
  }, { requestId: reviewerId }));
  assertEquals(review.status, 200);
  const nomination = await setup.handler(request("record_research_nominations", {
    reviewer_receipt_id: reviewerId,
    nominations: [{
      theme_id: "grid_buildout", entity_id: "CIK:0000000001", security_id: "NASDAQ:ACME",
      role: "program_to_supplier", reason: "Confirm the current primary source relationship.",
      evidence_ids: ["00000000-0000-4000-8000-000000000031"],
      required_evidence_kind: "primary_exposure", priority: 3,
    }],
  }));
  assertEquals(nomination.status, 200);
  assertEquals(repository.themeMemoryWrites.length, 2);
  assertEquals((await json(nomination)).telegram_message_ids, []);

  const ownerBrowser = await setup.handler(request("record_research_nominations", {
    reviewer_receipt_id: reviewerId,
    nominations: [],
  }, { secret: "", authorization: "Bearer owner-browser-token" }));
  assertEquals(ownerBrowser.status, 401);
  assertEquals(repository.themeMemoryWrites.length, 2);
});

Deno.test("holiday, bounded grading, and server-derived finish behavior", async () => {
  const holidayNow = () => new Date("2026-09-07T11:00:00.000Z");
  const pre = makeHandler(new FakeRepository(), { now: holidayNow });
  const preResponse = await json(
    await pre.handler(
      request("start_run", { phase: "pre-market", market_date: "2026-09-07" }),
    ),
  );
  assertEquals(preResponse.publication_status, "delivered");
  assertEquals(pre.repository.startCalls, 0);
  const midday = makeHandler(new FakeRepository(), { now: holidayNow });
  const midResponse = await json(
    await midday.handler(
      request("start_run", { phase: "intraday", market_date: "2026-09-07" }),
    ),
  );
  assertEquals(midResponse.status, "suppressed");
  assertEquals(midday.sent, []);

  const grading = makeHandler();
  const grade = await grading.handler(
    request("grade_due_decisions", { limit: 10 }),
  );
  assertEquals(grade.status, 200);
  assertEquals((await json(grade)).counts, {
    inserted: 0,
    updated: 0,
    incomplete: 0,
  });
  assertEquals(grading.repository.dueLimit, 10);
  for (
    const invalid of [{}, { limit: 0 }, { limit: 51 }, {
      limit: 1,
      returns: [99],
    }]
  ) {
    const response = await grading.handler(
      request("grade_due_decisions", invalid),
    );
    assertEquals(response.status, 400);
  }

  const finish = makeHandler();
  const receipt = await json(
    await finish.handler(
      request("finish_run", { write_counts: { invented: 999 } }),
    ),
  );
  assertEquals(receipt.write_counts, { evaluations: 1 });
  assertEquals(finish.repository.finishRunCalls, 1);
});
