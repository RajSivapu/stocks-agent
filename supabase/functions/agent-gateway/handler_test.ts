import { assert, assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import {
  createAgentGatewayHandler,
  type AgentGatewayRepository,
} from "./handler.ts";

const PUBLIC_ID = "11111111-1111-4111-8111-111111111111";
const SECRET = "A".repeat(43);

class FakeRepository implements AgentGatewayRepository {
  calls: Array<{ operation: string; request: Record<string, unknown> }> = [];
  error: Error | null = null;
  invoke(operation: Parameters<AgentGatewayRepository["invoke"]>[0], request: Record<string, unknown>) {
    this.calls.push({ operation, request: structuredClone(request) });
    if (this.error) return Promise.reject(this.error);
    return Promise.resolve({ operation, accepted: true });
  }
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
  const handler = createAgentGatewayHandler({ repository });
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
  const handler = createAgentGatewayHandler({ repository });
  const cases: Array<[string, unknown]> = [
    ["start_run", { phase: "intraday", market_date: "2026-09-02", trigger_request_id: null }],
    ["read_bounded_context", {}],
    ["submit_analysis", validSubmission()],
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

Deno.test("model authority fields and arbitrary artifact kinds fail before repository access", async () => {
  const repository = new FakeRepository();
  const handler = createAgentGatewayHandler({ repository });
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
  const response = await createAgentGatewayHandler({ repository })(request(envelope()));
  assertEquals(response.status, 401);
  assertEquals(await response.json(), { ok: false, error: { code: "UNAUTHORIZED" } });
});

Deno.test("repository failures and oversized bodies are redacted and bounded", async () => {
  const repository = new FakeRepository();
  repository.error = new Error("SQL included a secret");
  const handler = createAgentGatewayHandler({ repository });
  const failed = await handler(request(envelope()));
  assertEquals(failed.status, 500);
  assertEquals(JSON.stringify(await failed.json()).includes("SQL"), false);
  const oversized = await handler(request({ ...envelope(), payload: { text: "x".repeat(70_000) } }));
  assertEquals(oversized.status, 413);
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
    dimensions, evidence_refs: evidence, prior_suggestion_ids: [], candidates: [],
  };
}
