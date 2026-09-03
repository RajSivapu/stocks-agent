import type {
  AlertRuleSnapshot,
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
    }],
    factors: [{
      kind: "risk",
      stance: "neutral",
      text: "Margins remain stable.",
      evidence_ids: ["q"],
    }],
    analyst: {
      completed: true,
      action: "buy",
      confidence: "medium",
      reason: "Pass.",
    },
    checker: {
      completed: true,
      verdict: "approve",
      reason_codes: [],
      reason: "Pass.",
    },
    decisive_factor: "Risk-adjusted setup.",
    invalidation: "Support fails.",
    prior_suggestion_ids: [],
  };
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
        payload,
      }),
    },
  );
}

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
  repository.policyValue.version = 3;
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
