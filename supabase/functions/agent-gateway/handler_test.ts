import { assert, assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import {
  createAgentGatewayHandler,
  type AgentGatewayDependencies,
  type AgentGatewayRepository,
} from "./handler.ts";
import { createEvidencePacket } from "./evidence-packet.ts";
import { TelegramDeliveryError } from "./telegram.ts";

const PUBLIC_ID = "11111111-1111-4111-8111-111111111111";
const SECRET = "A".repeat(43);
const EVIDENCE_KEY = new Uint8Array(32).fill(7);

class FakeRepository implements AgentGatewayRepository {
  calls: Array<{ operation: string; request: Record<string, unknown> }> = [];
  applyCalls: Record<string, unknown>[] = [];
  finishCalls: Record<string, unknown>[] = [];
  events: string[] = [];
  error: Error | null = null;
  cachedResponse: Record<string, unknown> | null = null;
  applyResult: Record<string, unknown> = {
    delivery_required: false,
    response: { status: "accepted", publication_status: "suppressed", telegram_message_ids: [] },
  };
  finishResult: Record<string, unknown> = { response: { status: "accepted" } };
  invoke(operation: Parameters<AgentGatewayRepository["invoke"]>[0], request: Record<string, unknown>) {
    this.calls.push({ operation, request: structuredClone(request) });
    this.events.push(`invoke:${operation}`);
    if (this.error) return Promise.reject(this.error);
    if (operation === "read_bounded_context") {
      return Promise.resolve({
        run_id: request.run_id,
        phase: "intraday",
        market_date: "2026-09-02",
        holdings: [],
        plans: [],
        radar: [],
      });
    }
    if (operation === "submit_analysis") {
      if (this.cachedResponse) return Promise.resolve(structuredClone(this.cachedResponse));
      return Promise.resolve({
        status: request.dry_run === true ? "dry_run" : "claimed",
        lease_token: "55555555-5555-4555-8555-555555555555",
        run_id: request.run_id,
        phase: "intraday",
        market_date: "2026-09-02",
        started_at: "2026-09-02T16:50:00.000Z",
        policy: policy(),
        context: {
          holdings: [], holding_quotes: {}, realized_pnl_today: "0",
          portfolio_command_coverage_complete: true, consecutive_completed_losses: 0,
          owner_plans: [], recent_suggestions: [], observations: [], lessons: [],
          radar: [], recent_grades: [], dry_powder: [], paper_watches: [],
        },
        corporate_actions: [],
      });
    }
    return Promise.resolve({ operation, accepted: true });
  }
  applyAnalysis(request: Record<string, unknown>) {
    this.events.push("apply");
    this.applyCalls.push(structuredClone(request));
    return Promise.resolve(structuredClone(this.applyResult));
  }
  finishAnalysisDelivery(request: Record<string, unknown>) {
    this.events.push("finish");
    this.finishCalls.push(structuredClone(request));
    return Promise.resolve(structuredClone(this.finishResult));
  }
}

function policy() {
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
    broad_core_etfs: ["VTI", "VOO"],
    self_tuning_enabled: false,
    market_calendar_year: 2026,
    nyse_holidays: [],
    request_limits: {
      max_body_bytes: 262144,
      max_candidates: { "pre-market": 80, intraday: 20, "post-market": 80, "on-demand": 10 },
      max_requests_per_run: 20,
      max_authenticated_requests_per_hour: 100,
    },
  };
}

function gateway(
  repository: AgentGatewayRepository,
  overrides: Partial<Omit<AgentGatewayDependencies, "repository" | "evidenceSigningKey" | "telegramToken">> = {},
) {
  return createAgentGatewayHandler({
    repository,
    evidenceSigningKey: EVIDENCE_KEY,
    telegramToken: "12345:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    now: () => new Date("2026-09-02T17:00:00.000Z"),
    fetchQuote: async (ticker) => ({
      ticker,
      price: "100",
      previous_close: "99",
      as_of: "2026-09-02T16:59:00.000Z",
      market_state: "REGULAR",
      source: "yahoo-chart",
    }),
    ...overrides,
  });
}

function envelope(operation = "read_bounded_context", payload: unknown = {}) {
  return {
    contract_version: 2,
    operation,
    request_id: "22222222-2222-4222-8222-222222222222",
    run_id: operation === "start_run" ? null : "33333333-3333-4333-8333-333333333333",
    dry_run: false,
    payload,
  };
}

function request(value: unknown, authorization = `Bearer ${PUBLIC_ID}.${SECRET}`) {
  return new Request("https://example.test/agent-gateway", {
    method: "POST",
    headers: { authorization, "content-type": "application/json" },
    body: JSON.stringify(value),
  });
}

Deno.test("connection bearer is validated before body parsing and raw secret never reaches SQL", async () => {
  const repository = new FakeRepository();
  const handler = gateway(repository);
  const invalid = await handler(new Request("https://example.test/agent-gateway", {
    method: "POST", headers: { authorization: "Bearer wrong", "content-type": "application/json" }, body: "{broken",
  }));
  assertEquals(invalid.status, 401);
  assertEquals(repository.calls, []);

  const response = await handler(request(envelope()));
  assertEquals(response.status, 200);
  const machineRequest = repository.calls[0].request;
  assertEquals(machineRequest.connection_id, PUBLIC_ID);
  assertEquals(typeof machineRequest.secret_digest, "string");
  assertEquals(String(machineRequest.secret_digest).length, 64);
  assertEquals(JSON.stringify(machineRequest).includes(SECRET), false);
  assertEquals(Object.hasOwn(machineRequest, "owner_id"), false);
});

Deno.test("all six V2 operations dispatch only to their named machine RPC", async () => {
  const repository = new FakeRepository();
  const handler = gateway(repository);
  const cases: Array<[string, unknown]> = [
    ["start_run", { trigger_request_id: null }],
    ["read_bounded_context", {}],
    ["submit_analysis", await validSignedSubmission()],
    ["record_permitted_artifacts", { mutations: [{
      kind: "lesson", entry_date: "2026-09-02", category: "process", content: "Use fresh evidence.",
    }] }],
    ["grade_due_decisions", { limit: 10 }],
    ["finish_run", {}],
  ];
  for (const [operation, payload] of cases) {
    const response = await handler(request(envelope(operation, payload)));
    assertEquals(response.status, 200);
  }
  assertEquals(repository.calls.map((call) => call.operation), cases.map(([operation]) => operation));
});

Deno.test("start_run accepts only server-resolved opaque trigger authority", async () => {
  const repository = new FakeRepository();
  const handler = gateway(repository);
  const accepted = await handler(request(envelope("start_run", {
    trigger_request_id: "44444444-4444-4444-8444-444444444444",
  })));
  assertEquals(accepted.status, 200);
  for (const body of [
    { phase: "intraday", market_date: "2026-09-03", trigger_request_id: null },
    { trigger_request_id: null, phase: "on-demand" },
  ]) {
    assertEquals((await handler(request(envelope("start_run", body)))).status, 400);
  }
  assertEquals(repository.calls.length, 1);
});

Deno.test("handshake finish accepts only exact bounded source-check receipts", async () => {
  const repository = new FakeRepository();
  const handler = gateway(repository);
  const valid = {
    contract_version: 2,
    challenge: "a".repeat(64),
    source_checks: [
      { host: "query1.finance.yahoo.com", status: "reachable", content_hash: "b".repeat(64), observed_at: "2026-09-03T12:00:00Z" },
      { host: "www.sec.gov", status: "reachable", content_hash: "c".repeat(64), observed_at: "2026-09-03T12:00:01Z" },
      { host: "finnhub.io", status: "reachable", content_hash: "d".repeat(64), observed_at: "2026-09-03T12:00:02Z" },
    ],
  };
  assertEquals((await handler(request(envelope("finish_run", valid)))).status, 200);
  for (const malformed of [
    { ...valid, challenge: "not-a-hash" },
    { ...valid, source_checks: valid.source_checks.slice(0, 2) },
    { ...valid, source_checks: [{ ...valid.source_checks[0], extra: true }, ...valid.source_checks.slice(1)] },
    { ...valid, source_checks: [{ ...valid.source_checks[0], status: "invented" }, ...valid.source_checks.slice(1)] },
  ]) {
    assertEquals((await handler(request(envelope("finish_run", malformed)))).status, 400);
  }
  assertEquals(repository.calls.length, 1);
});

Deno.test("model authority fields and arbitrary artifact kinds fail before repository access", async () => {
  const repository = new FakeRepository();
  const handler = gateway(repository);
  const submission = validSubmission();
  submission.candidates = [{ server_quote: { price: "1" } }];
  assertEquals((await handler(request(envelope("submit_analysis", submission)))).status, 400);
  assertEquals((await handler(request(envelope("record_permitted_artifacts", {
    mutations: [{ kind: "write_file", path: "config/watchlist.json" }],
  })))).status, 400);
  assertEquals(repository.calls, []);
});

Deno.test("unknown and revoked credentials share one stable unauthorized response", async () => {
  const repository = new FakeRepository();
  repository.error = Object.assign(new Error("private database detail"), { code: "UNAUTHORIZED" });
  const response = await gateway(repository)(request(envelope()));
  assertEquals(response.status, 401);
  assertEquals(await response.json(), { ok: false, error: { code: "UNAUTHORIZED" } });
});

Deno.test("repository failures and oversized bodies are redacted and bounded", async () => {
  const repository = new FakeRepository();
  repository.error = new Error("SQL included a secret");
  const handler = gateway(repository);
  const failed = await handler(request(envelope()));
  assertEquals(failed.status, 500);
  assertEquals(JSON.stringify(await failed.json()).includes("SQL"), false);
  const oversized = await handler(request({ ...envelope(), payload: { text: "x".repeat(70_000) } }));
  assertEquals(oversized.status, 413);
});

Deno.test("tampered evidence is rejected before policy persistence", async () => {
  const repository = new FakeRepository();
  const submission = await validSignedSubmission();
  const packet = submission.evidence_packets[0] as { signature: string };
  packet.signature = `${packet.signature.slice(0, -1)}${packet.signature.endsWith("0") ? "1" : "0"}`;
  const response = await gateway(repository)(request(envelope("submit_analysis", submission)));
  assertEquals(response.status, 409);
  assertEquals(await response.json(), { ok: false, error: { code: "EVIDENCE_CONFLICTING" } });
  assertEquals(repository.applyCalls.length, 0);
  assertEquals(repository.finishCalls.length, 0);
});

Deno.test("duplicate submit returns the server receipt without repeating effects", async () => {
  const repository = new FakeRepository();
  repository.cachedResponse = {
    status: "accepted",
    publication_status: "delivered",
    telegram_message_ids: [88],
  };
  let sends = 0;
  const response = await gateway(repository, {
    sendTelegram: async () => {
      sends += 1;
      return [99];
    },
  })(request(envelope("submit_analysis", await validSignedSubmission())));
  assertEquals(response.status, 200);
  assertEquals((await response.json()).data.telegram_message_ids, [88]);
  assertEquals(repository.applyCalls.length, 0);
  assertEquals(repository.finishCalls.length, 0);
  assertEquals(sends, 0);
});

Deno.test("analysis is persisted before Telegram and actual delivery is finalized", async () => {
  const repository = new FakeRepository();
  repository.applyResult = {
    delivery_required: true,
    chat_id: "123456",
    delivery_lease: "66666666-6666-4666-8666-666666666666",
    response: { status: "accepted", publication_status: "sending" },
  };
  repository.finishResult = {
    response: { status: "accepted", publication_status: "delivered", telegram_message_ids: [321] },
  };
  const response = await gateway(repository, {
    sendTelegram: async () => {
      repository.events.push("send");
      return [321];
    },
  })(request(envelope("submit_analysis", await validSignedSubmission())));
  assertEquals(response.status, 200);
  assertEquals(repository.events.slice(-4), ["invoke:submit_analysis", "apply", "send", "finish"]);
  assertEquals(repository.finishCalls[0].status, "delivered");
  assertEquals(repository.finishCalls[0].message_ids, [321]);
});

Deno.test("Telegram failures are classified without inventing a successful send", async () => {
  for (const [deliveryError, expectedStatus, expectedIds] of [
    [new TelegramDeliveryError("definitive", []), "delivery_failed", []],
    [new TelegramDeliveryError("ambiguous", [91]), "delivery_unknown", [91]],
  ] as const) {
    const repository = new FakeRepository();
    repository.applyResult = {
      delivery_required: true,
      chat_id: "123456",
      delivery_lease: "66666666-6666-4666-8666-666666666666",
      response: { status: "accepted", publication_status: "sending" },
    };
    await gateway(repository, {
      sendTelegram: () => Promise.reject(deliveryError),
    })(request(envelope("submit_analysis", await validSignedSubmission())));
    assertEquals(repository.finishCalls[0].status, expectedStatus);
    assertEquals(repository.finishCalls[0].message_ids, expectedIds);
  }
});

Deno.test("dry-run computes a preview with no domain persistence or Telegram send", async () => {
  const repository = new FakeRepository();
  let sends = 0;
  const body = envelope("submit_analysis", await validSignedSubmission());
  body.dry_run = true;
  const response = await gateway(repository, {
    sendTelegram: async () => {
      sends += 1;
      return [1];
    },
  })(request(body));
  assertEquals(response.status, 200);
  const data = (await response.json()).data;
  assertEquals(data.status, "dry_run");
  assertEquals(data.writes, 0);
  assertEquals(data.telegram, { status: "not_sent", message_ids: [] });
  assertEquals(repository.applyCalls.length, 0);
  assertEquals(repository.finishCalls.length, 0);
  assertEquals(sends, 0);
});

function validSubmission(): Record<string, any> {
  const evidence = [
    { evidence_id: "quote", run_id: "33333333-3333-4333-8333-333333333333", content_hash: "a".repeat(64) },
    { evidence_id: "search", run_id: "33333333-3333-4333-8333-333333333333", content_hash: "b".repeat(64) },
  ];
  const dimensions = Object.fromEntries([
    "fundamentals", "valuation", "catalyst", "technical", "portfolio_fit", "downside",
    "bear_case", "invalidation", "decisive_factor",
  ].map((name) => [name, { status: "supported", summary: "Reviewed.", evidence_ids: ["quote", "search"] }]));
  return {
    phase: "intraday", market_date: "2026-09-02", title: "Review", suggestion_only: true,
    provider: "claude", model: "configured", analyst: {
      completed: true, action: "watch", confidence: "medium", thesis: "Mixed evidence.",
    }, checker: { completed: true, verdict: "approve", reason: "Bounded." },
    dimensions, evidence_packets: [{ payload: {}, signature: "f".repeat(64) }],
    evidence_refs: evidence, prior_suggestion_ids: [], candidates: [],
  };
}

async function validSignedSubmission(): Promise<Record<string, any>> {
  const submission = validSubmission();
  const packet = await createEvidencePacket({
    version: 1,
    run_id: "33333333-3333-4333-8333-333333333333",
    phase: "intraday",
    market_date: "2026-09-02",
    issued_at: "2026-09-02T16:55:00.000Z",
    expires_at: "2026-09-02T17:10:00.000Z",
    facts: [
      {
        evidence_id: "quote", source_run_id: null, category: "market_snapshot",
        source_identifier: "yahoo-chart", reference_identifier: null,
        observed_at: "2026-09-02T16:54:00.000Z", retrieved_at: "2026-09-02T16:55:00.000Z",
        revalidated_at: null, content_hash: "a".repeat(64), claims: [], status: "fresh",
      },
      {
        evidence_id: "search", source_run_id: null, category: "source_search",
        source_identifier: "server-source-retrieval", reference_identifier: null,
        observed_at: null, retrieved_at: "2026-09-02T16:56:00.000Z",
        revalidated_at: null, content_hash: "b".repeat(64), claims: [], status: "no_new_material_evidence",
      },
    ],
    search_receipt: {
      searched_at: "2026-09-02T16:56:00.000Z",
      categories: ["news"],
      sources: [{ url: "https://www.sec.gov/files/company_tickers.json", status: "fetched", content_hash: "c".repeat(64) }],
      result_status: "no_new_material_evidence",
      content_hash: "b".repeat(64),
    },
  }, EVIDENCE_KEY);
  submission.evidence_packets = [packet];
  return submission;
}
