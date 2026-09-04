import type {
  AlertRuleSnapshot,
  EvidencePacket,
  GatewayEnvelope,
  GatewayReadContext,
  PolicyConfig,
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
    recent_suggestions: [],
    observations: [],
    lessons: [],
    radar: [],
    recent_grades: [],
    dry_powder: [],
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
    coverage: { mode: "bounded", complete_market_coverage: false },
    limitations: [],
    policy_version: 1,
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
  startRun(): Promise<string> {
    this.mutationCalls += 1;
    this.startCalls += 1;
    return Promise.resolve(RUN_ID);
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
    runId?: string | null;
    requestId?: string;
    secret?: string;
    method?: string;
  } = {},
) {
  const payloadValue = structuredClone(payload);
  if (
    operation === "evaluate_and_publish" && typeof payloadValue === "object" &&
    payloadValue !== null
  ) {
    const row = payloadValue as Record<string, unknown>;
    const phase = row.phase;
    if (phase !== "on-demand" && !("intelligence_packet" in row)) {
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
    if (phase !== "on-demand" && Array.isArray(row.candidates)) {
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
      },
      body: options.method === "GET" ? undefined : JSON.stringify({
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

Deno.test("scheduled discovery requires the persisted packet hash before market work", async () => {
  class MismatchedPacketRepository extends FakeRepository {
    override loadIntelligencePacket(): Promise<
      {
        id: string;
        run_id: string;
        content_hash: string;
        packet: EvidencePacket;
        exposure_facts: [];
      }
    > {
      this.packetReadCalls += 1;
      return Promise.resolve({
        id: PACKET_ID,
        run_id: RUN_ID,
        content_hash: "b".repeat(64),
        packet: evidencePacket(),
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

  const body = await json(await fixture.handler(request("record_learning", payload)));

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
  assertEquals(repository.startCalls, 1);
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

Deno.test("evaluation refetches every quote, persists before sending, and ignores claimed prices", async () => {
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
  assertEquals(repository.events, ["persist", "claim-publication", "send"]);
  assertEquals(
    repository.lastBundle!.evaluations[0].normalized.verified_price,
    "47.02",
  );
  assertEquals(
    repository.lastBundle!.evaluations[0].normalized.total_investable_value,
    "40500",
  );
  assertEquals(repository.lastBundle!.publication.template_version, 2);
  assertEquals(sent.length, 1);
  assertEquals(repository.finishPublicationCalls, [{
    status: "delivered",
    ids: [77],
  }]);
});

Deno.test("persistence failure prevents Telegram and delivery outcomes are classified", async () => {
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
    candidates: [candidate()],
  };
  assertEquals(
    (await failed.handler(request("evaluate_and_publish", bundle))).status,
    500,
  );
  assertEquals(failed.sent, []);

  const definitiveRepo = new FakeRepository();
  const definitive = makeHandler(definitiveRepo, {
    sendTelegram: () =>
      Promise.reject(new TelegramDeliveryError("definitive", [])),
  });
  assertEquals(
    (await definitive.handler(request("evaluate_and_publish", bundle))).status,
    502,
  );
  assertEquals(definitiveRepo.finishPublicationCalls, [{
    status: "delivery_failed",
    ids: [],
  }]);

  const ambiguousRepo = new FakeRepository();
  const ambiguous = makeHandler(ambiguousRepo, {
    sendTelegram: () =>
      Promise.reject(new TelegramDeliveryError("ambiguous", [88])),
  });
  const response = await ambiguous.handler(
    request("evaluate_and_publish", bundle),
  );
  assertEquals(response.status, 502);
  assertEquals((await json(response)).code, "DELIVERY_UNKNOWN");
  assertEquals(ambiguousRepo.finishPublicationCalls, [{
    status: "delivery_unknown",
    ids: [88],
  }]);
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

Deno.test("delivered and ambiguous duplicate requests never resend", async () => {
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
  assertEquals(delivered.sent.length, 1);

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
  assertEquals(attempts, 1);
});

Deno.test("a second request for an evaluated run reuses the publication without sending", async () => {
  class OnePublicationRepository extends FakeRepository {
    override applyDecisionBundle(
      input: PersistedBundle,
    ): Promise<PublicationReceipt> {
      if (this.applyCalls > 0) {
        this.applyCalls += 1;
        return Promise.resolve({
          id: "00000000-0000-4000-8000-000000000050",
          idempotency_key: "00000000-0000-4000-8000-000000000090",
          status: "delivered",
          telegram_message_ids: [77],
          telegram_accepted_at: "2026-09-02T17:00:00.000Z",
          lease_token: null,
        });
      }
      return super.applyDecisionBundle(input);
    }
  }
  const setup = makeHandler(new OnePublicationRepository());
  const bundle = {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "x",
    candidates: [candidate()],
  };
  await setup.handler(request("evaluate_and_publish", bundle));
  await setup.handler(request("evaluate_and_publish", bundle));
  assertEquals(setup.sent.length, 1);
  assertEquals(setup.repository.applyCalls, 2);
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
