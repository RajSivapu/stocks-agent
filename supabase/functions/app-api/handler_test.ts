import {
  assert,
  assertEquals,
} from "https://deno.land/std@0.224.0/assert/mod.ts";

import { createAppApiHandler, type AppApiRepository } from "./handler.ts";


const TOKEN = "header.payload.signature";
const OWNER = "11111111-1111-4111-8111-111111111111";

class FakeRepository implements AppApiRepository {
  calls: Array<Record<string, unknown>> = [];
  response: Record<string, unknown> = {
    ok: true,
    data: { command_id: "22222222-2222-4222-8222-222222222222", status: "previewed" },
  };

  dispatch(input: Record<string, unknown>): Promise<Record<string, unknown>> {
    this.calls.push(structuredClone(input));
    return Promise.resolve(structuredClone(this.response));
  }
}

function makeHandler() {
  const repository = new FakeRepository();
  const events: string[] = [];
  const handler = createAppApiHandler({
    allowedOrigins: ["https://app.example.test"],
    ipHashPepper: "a-secure-test-pepper-with-32-bytes",
    repository,
    authenticate: (request) => {
      events.push("authenticate");
      if (request.headers.get("authorization") !== `Bearer ${TOKEN}`) {
        throw new Error("unauthorized");
      }
      return Promise.resolve({ sub: OWNER, role: "authenticated" });
    },
    newId: () => "33333333-3333-4333-8333-333333333333",
  });
  return { handler, repository, events };
}

function request(
  path: string,
  body: unknown,
  options: { method?: string; token?: string; origin?: string; headers?: Record<string, string> } = {},
) {
  return new Request(`https://edge.example.test${path}`, {
    method: options.method ?? "POST",
    headers: {
      authorization: `Bearer ${options.token ?? TOKEN}`,
      "content-type": "application/json",
      origin: options.origin ?? "https://app.example.test",
      "x-forwarded-for": "192.0.2.5",
      ...options.headers,
    },
    body: options.method === "GET" ? undefined : JSON.stringify(body),
  });
}

Deno.test("method and origin are rejected before authentication or body parsing", async () => {
  const { handler, repository, events } = makeHandler();
  const method = await handler(request("/portfolio/preview", {}, { method: "PUT" }));
  const origin = await handler(request("/portfolio/preview", {}, { origin: "https://evil.test" }));

  assertEquals(method.status, 405);
  assertEquals(await method.json(), { ok: false, error: { code: "METHOD_NOT_ALLOWED" } });
  assertEquals(origin.status, 403);
  assertEquals(events, []);
  assertEquals(repository.calls, []);
});

Deno.test("JWT is verified before an unauthenticated body is consumed", async () => {
  const { handler, repository, events } = makeHandler();
  const response = await handler(new Request(
    "https://edge.example.test/portfolio/preview",
    {
      method: "POST",
      headers: {
        authorization: "Bearer wrong.token.value",
        "content-type": "application/json",
        origin: "https://app.example.test",
      },
      body: "{not json",
    },
  ));

  assertEquals(response.status, 401);
  assertEquals(events, ["authenticate"]);
  assertEquals(repository.calls, []);
});

Deno.test("preview route validates exact authority-free command input", async () => {
  const { handler, repository } = makeHandler();
  const body = {
    idempotency_key: "44444444-4444-4444-8444-444444444444",
    command: {
      operation: "buy",
      ticker: "VTI",
      quantity: "0.1",
      fill_price: "380",
      fees: "0",
      cash_total: "38",
      executed_on: "2026-09-02",
      bucket: "core",
    },
  };
  const response = await handler(request("/portfolio/preview", body));

  assertEquals(response.status, 200);
  assertEquals(response.headers.get("cache-control"), "no-store");
  assertEquals(response.headers.get("access-control-allow-origin"), "https://app.example.test");
  assertEquals(repository.calls.length, 1);
  assertEquals(repository.calls[0].route, "POST /portfolio/preview");
  assertEquals(repository.calls[0].body, body);
  assertEquals(repository.calls[0].bearerToken, TOKEN);
  assertEquals(repository.calls[0].requestId, "33333333-3333-4333-8333-333333333333");
  assert(typeof repository.calls[0].ipDigest === "string");
  assertEquals((repository.calls[0].ipDigest as string).length, 64);

  const rejected = await handler(request("/portfolio/preview", { ...body, owner_id: OWNER }));
  assertEquals(rejected.status, 400);
  assertEquals(repository.calls.length, 1);
});

Deno.test("confirmation schema and route table are exact", async () => {
  const { handler, repository } = makeHandler();
  const body = {
    command_id: "22222222-2222-4222-8222-222222222222",
    preview_digest: "a".repeat(64),
  };
  assertEquals((await handler(request("/portfolio/confirm", body))).status, 200);
  assertEquals((await handler(request("/unknown", {}))).status, 404);
  assertEquals((await handler(request("/portfolio/confirm", { ...body, digest: "x" }))).status, 400);
  assertEquals(repository.calls.length, 1);
});

Deno.test("future lifecycle routes reject unreviewed fields before dispatch", async () => {
  const { handler, repository } = makeHandler();
  assertEquals((await handler(request("/connections/create", { provider: "claude" }))).status, 200);
  assertEquals((await handler(request("/telegram/pairing-code", {}))).status, 200);
  assertEquals((await handler(request("/connections/create", { provider: "grok" }))).status, 400);
  assertEquals((await handler(request("/telegram/pairing-code", { owner_id: OWNER }))).status, 400);
  assertEquals(repository.calls.length, 2);
});

Deno.test("oversized bodies and repository failures are bounded and sanitized", async () => {
  const { handler, repository } = makeHandler();
  const oversized = await handler(request(
    "/settings",
    { value: "x".repeat(70_000) },
    { method: "PATCH" },
  ));
  assertEquals(oversized.status, 413);

  repository.response = { ok: false, error: { code: "RATE_LIMITED", retry_after_seconds: 60 } };
  const limited = await handler(request("/portfolio/confirm", {
    command_id: "22222222-2222-4222-8222-222222222222",
    preview_digest: "a".repeat(64),
  }));
  assertEquals(limited.status, 429);
  assertEquals(await limited.json(), repository.response);

  repository.dispatch = () => Promise.reject(new Error("password=secret SQL failed"));
  const failed = await handler(request("/portfolio/confirm", {
    command_id: "22222222-2222-4222-8222-222222222222",
    preview_digest: "a".repeat(64),
  }));
  assertEquals(failed.status, 500);
  assertEquals(await failed.json(), { ok: false, error: { code: "INTERNAL_ERROR" } });
});

Deno.test("preflight is bounded to the reviewed route methods", async () => {
  const { handler } = makeHandler();
  const response = await handler(new Request("https://edge.example.test/portfolio/preview", {
    method: "OPTIONS",
    headers: {
      origin: "https://app.example.test",
      "access-control-request-method": "POST",
    },
  }));
  assertEquals(response.status, 204);
  assertEquals(response.headers.get("access-control-allow-methods"), "GET, PATCH, POST");
});
