import { createOwnerDashboardHandler } from "./handler.ts";
import { resolveDashboardRoute } from "./routes.ts";

const OWNER = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22";
const ORIGIN = "https://dashboard.example";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function assert(condition: boolean, message = "assertion failed"): void {
  if (!condition) throw new Error(message);
}

function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function dependencies(overrides: Record<string, unknown> = {}) {
  return {
    ownerUserId: OWNER,
    projectUrl: "https://test-project.supabase.co",
    allowedOrigins: [ORIGIN],
    verifyOwner: async () => ({ subject: OWNER }),
    repository: {
      read: async (route: { name: string }) => ({
        data: { route: route.name },
        dataAsOf: null,
        freshness: "unavailable" as const,
        marketState: "unknown" as const,
      }),
    },
    now: () => new Date("2026-09-03T18:00:00.000Z"),
    requestId: () => "7d834dbd-75bb-4313-931f-09732f003932",
    ...overrides,
  };
}

function request(path: string, init: RequestInit = {}): Request {
  const headers = new Headers(init.headers);
  headers.set("origin", ORIGIN);
  headers.set("authorization", "Bearer test-owner-token");
  return new Request(`https://api.example${path}`, { ...init, headers });
}

Deno.test("missing owner configuration fails before repository access", async () => {
  let accessed = false;
  const handler = createOwnerDashboardHandler(dependencies({
    ownerUserId: "",
    repository: {
      read: async () => {
        accessed = true;
        return { data: {}, dataAsOf: null, freshness: "unavailable", marketState: "unknown" };
      },
    },
  }));
  const response = await handler(request("/v1/meta"));
  assertEquals(response.status, 503);
  assertEquals(accessed, false);
});

Deno.test("an allowlisted browser receives CORS on a configuration kill-switch response", async () => {
  const handler = createOwnerDashboardHandler(dependencies({ configurationReady: false }));
  const response = await handler(request("/v1/meta"));
  assertEquals(response.status, 503);
  assertEquals(response.headers.get("access-control-allow-origin"), ORIGIN);
  assertEquals((await response.json()).error.code, "temporarily_unavailable");
});

Deno.test("missing owner configuration fails closed before origin disclosure", async () => {
  const handler = createOwnerDashboardHandler(dependencies({ ownerUserId: "" }));
  const response = await handler(new Request("https://api.example/v1/meta", {
    headers: { origin: "https://attacker.example", authorization: "Bearer invalid" },
  }));
  assertEquals(response.status, 503);
  assertEquals((await response.json()).error.code, "temporarily_unavailable");
});

Deno.test("exact-origin OPTIONS succeeds without authorization", async () => {
  let verified = false;
  const handler = createOwnerDashboardHandler(dependencies({
    verifyOwner: async () => {
      verified = true;
      return { subject: OWNER };
    },
  }));
  const response = await handler(new Request("https://api.example/v1/meta", {
    method: "OPTIONS",
    headers: {
      origin: ORIGIN,
      "access-control-request-method": "GET",
      "access-control-request-headers": "authorization",
    },
  }));
  assertEquals(response.status, 204);
  assertEquals(response.headers.get("access-control-allow-origin"), ORIGIN);
  assertEquals(response.headers.get("access-control-allow-methods"), "GET");
  assertEquals(verified, false);
});

Deno.test("unknown origins never receive CORS permission", async () => {
  const handler = createOwnerDashboardHandler(dependencies());
  const response = await handler(new Request("https://api.example/v1/meta", {
    method: "OPTIONS",
    headers: { origin: "https://attacker.example" },
  }));
  assertEquals(response.status, 403);
  assertEquals(response.headers.get("access-control-allow-origin"), null);
});

Deno.test("POST is unavailable after owner authentication", async () => {
  const handler = createOwnerDashboardHandler(dependencies());
  assertEquals((await handler(request("/v1/portfolio", { method: "POST" }))).status, 405);
});

Deno.test("every GET route including meta authenticates before reading", async () => {
  const observed: string[] = [];
  const handler = createOwnerDashboardHandler(dependencies({
    verifyOwner: async () => {
      observed.push("auth");
      return { subject: OWNER };
    },
    repository: {
      read: async () => {
        observed.push("read");
        return { data: {}, dataAsOf: null, freshness: "unavailable", marketState: "unknown" };
      },
    },
  }));
  const response = await handler(request("/v1/meta"));
  assertEquals(response.status, 200);
  assertEquals(observed, ["auth", "read"]);
  assertEquals(response.headers.get("cache-control"), "no-store");
  assertEquals((await response.json()).contract_version, 1);
});

Deno.test("the default request id generator remains bound to Web Crypto", async () => {
  const { requestId: _requestId, ...defaults } = dependencies();
  const response = await createOwnerDashboardHandler(defaults)(request("/v1/meta"));
  const body = await response.json();
  assertEquals(response.status, 200);
  assert(UUID.test(body.request_id));
});

Deno.test("authentication and internal errors use bounded public envelopes", async () => {
  const handler = createOwnerDashboardHandler(dependencies({
    verifyOwner: async () => {
      throw new Error("secret raw token and database detail");
    },
  }));
  const response = await handler(request("/v1/today"));
  const body = await response.json();
  assertEquals(response.status, 401);
  assertEquals(body.error.code, "unauthorized");
  assert(!JSON.stringify(body).includes("database detail"));
});

Deno.test("rate limits stop work before authentication and emit retry timing", async () => {
  let verified = false;
  const handler = createOwnerDashboardHandler(dependencies({
    verifyOwner: async () => {
      verified = true;
      return { subject: OWNER };
    },
    rateLimiter: {
      check: (stage: string) => stage === "pre_auth" ? 12 : null,
    },
  }));
  const response = await handler(request("/v1/today"));
  assertEquals(response.status, 429);
  assertEquals(response.headers.get("retry-after"), "12");
  assertEquals(verified, false);
});

Deno.test("route resolver exposes only documented bounded GET routes", () => {
  const routes = [
    "/v1/meta",
    "/v1/today",
    "/v1/portfolio",
    "/v1/transactions?cursor=eyJ2IjoxLCJpZCI6IjUwIn0",
    "/v1/ideas?status=approved&cursor=eyJ2IjoxLCJpZCI6IjUwIn0",
    "/v1/companion",
    "/v1/alerts?state=delivered&cursor=eyJ2IjoxLCJpZCI6IjUwIn0",
    "/v1/runs?kind=intraday&cursor=eyJ2IjoxLCJpZCI6IjUwIn0",
    "/v1/runs/7d834dbd-75bb-4313-931f-09732f003932",
    "/v1/system",
    "/v1/intelligence",
    "/v1/reports?cursor=eyJ2IjoxLCJpZCI6IjUwIn0",
    "/v1/reports/7d834dbd-75bb-4313-931f-09732f003932",
  ];
  for (const value of routes) {
    const url = new URL(`https://api.example${value}`);
    assert(resolveDashboardRoute("GET", url.pathname, url.searchParams).name.length > 0);
  }
  let rejected = false;
  try {
    resolveDashboardRoute("GET", "/v1/admin", new URLSearchParams());
  } catch {
    rejected = true;
  }
  assertEquals(rejected, true);
});
