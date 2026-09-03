import {
  assert,
  assertEquals,
} from "https://deno.land/std@0.224.0/assert/mod.ts";

import { createAppApiHandler, type AppApiRepository } from "./handler.ts";
import type { AuthenticatedClaims } from "../_shared/jwt.ts";


const TOKEN = "header.payload.signature";
const OWNER = "11111111-1111-4111-8111-111111111111";

class FakeRepository implements AppApiRepository {
  calls: Array<Record<string, unknown>> = [];
  response: Record<string, unknown> = {
    ok: true,
    data: { command_id: "22222222-2222-4222-8222-222222222222", status: "previewed" },
  };
  cleanupResponse: Record<string, unknown> = { ok: true, data: { status: "recorded" } };

  publicHealth(): Promise<Record<string, unknown>> {
    this.calls.push({ route: "PUBLIC_HEALTH" });
    return Promise.resolve({ status: "ok", schema_version: 1 });
  }

  dispatch(input: Record<string, unknown>): Promise<Record<string, unknown>> {
    this.calls.push(structuredClone(input));
    if (input.route === "POST /connections/create") {
      return Promise.resolve({ ok: true, data: { public_id: OWNER, status: "disabled" } });
    }
    if (input.route === "POST /account/delete/cleanup-result") {
      return Promise.resolve(structuredClone(this.cleanupResponse));
    }
    return Promise.resolve(structuredClone(this.response));
  }
}

function makeHandler(claims: AuthenticatedClaims = {
  sub: OWNER,
  role: "authenticated",
  session_id: "44444444-4444-4444-8444-444444444444",
}) {
  const repository = new FakeRepository();
  const events: string[] = [];
  const revoked: string[] = [];
  const handler = createAppApiHandler({
    allowedOrigins: ["https://app.example.test"],
    ipHashPepper: "a-secure-test-pepper-with-32-bytes",
    pairingHashPepper: "a-pairing-test-pepper-with-at-least-32-bytes",
    stepUpHashPepper: "a-step-up-test-pepper-with-at-least-32-bytes",
    repository,
    authenticate: (request) => {
      events.push("authenticate");
      if (request.headers.get("authorization") !== `Bearer ${TOKEN}`) {
        throw new Error("unauthorized");
      }
      return Promise.resolve(claims);
    },
    newId: () => "33333333-3333-4333-8333-333333333333",
    newPairingCode: () => "ABCD234567",
    newConnectionSecret: () => "A".repeat(43),
    newCleanupToken: () => "C".repeat(43),
    revokeAllSessions: (token) => { revoked.push(token); return Promise.resolve(); },
    telegramBotToken: "123456:" + "T".repeat(24),
    telegramFetch: () => Promise.resolve(new Response('{"ok":true}', {
      status: 200,
      headers: { "content-type": "application/json" },
    })),
  });
  return { handler, repository, events, revoked };
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

Deno.test("public health is exact and bypasses authentication without accepting private data", async () => {
  const { handler, repository, events } = makeHandler();
  const response = await handler(new Request("https://edge.example.test/healthz", {
    method: "GET",
  }));
  assertEquals(response.status, 200);
  assertEquals(await response.json(), {
    ok: true,
    data: { status: "ok", schema_version: 1 },
  });
  assertEquals(events, []);
  assertEquals(repository.calls, [{ route: "PUBLIC_HEALTH" }]);
});

Deno.test("operator health is authenticated and response-validated", async () => {
  const { handler, repository } = makeHandler();
  repository.response = {
    ok: true,
    data: {
      status: "degraded",
      component_status: {
        database: "ok", scheduler: "ok", provider_adapter: "ok",
        backup: "missing", restore: "missing", projections: "ok",
      },
      deployed_versions: { database_schema: 20260910, provider_contract: 2 },
      provider_adapter: { provider: "claude", active: 1, unavailable: 0 },
      missed_runs: { last_24_hours: 0, last_7_days: 0 },
      quota_pressure: { month_invocations: 0, configured_limit: 1000 },
      backup: { age_hours: null, last_success_at: null },
      restore: { age_days: null, last_verified_at: null },
      projection: { checked: 0, failed: 0, paused: 0 },
    },
  };
  const response = await handler(request("/health/operator", {}, { method: "GET" }));
  assertEquals(response.status, 200);
  assertEquals(repository.calls[0].route, "GET /health/operator");
  assertEquals((await response.json()).data.status, "degraded");

  repository.response = { ok: true, data: { ...repository.response.data as object, ticker: "NVDA" } };
  assertEquals((await handler(request("/health/operator", {}, { method: "GET" }))).status, 500);
});

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

Deno.test("on-demand run accepts only an empty authority-free request", async () => {
  const { handler, repository } = makeHandler();
  repository.response = {
    ok: true,
    data: {
      status: "queued",
      slot_id: "22222222-2222-4222-8222-222222222222",
      phase: "on-demand",
      telegram: "suppressed",
    },
  };

  const accepted = await handler(request("/runs/on-demand", {}));
  assertEquals(accepted.status, 200);
  assertEquals(repository.calls[0].route, "POST /runs/on-demand");
  assertEquals(repository.calls[0].body, {});
  assertEquals((await accepted.json()).data.telegram, "suppressed");

  const rejected = await handler(request("/runs/on-demand", { owner_id: OWNER }));
  assertEquals(rejected.status, 400);
  assertEquals(repository.calls.length, 1);
});

Deno.test("future lifecycle routes reject unreviewed fields before dispatch", async () => {
  const { handler, repository } = makeHandler();
  const created = await handler(request("/connections/create", {
    provider: "claude", consent_version: "provider-data-v1",
  }));
  assertEquals(created.status, 200);
  assertEquals((await created.json()).data.gateway_credential, `${OWNER}.${"A".repeat(43)}`);
  assertEquals((await handler(request("/telegram/pairing-code", {
    step_up_receipt_id: "55555555-5555-4555-8555-555555555555",
  }))).status, 200);
  assertEquals((await handler(request("/connections/create", {
    provider: "grok", consent_version: "provider-data-v1",
  }))).status, 400);
  assertEquals((await handler(request("/connections/handshake", {
    connection_id: OWNER,
    trigger_url: "https://api.anthropic.com/v1/claude_code/routines/trig_ABCDEF/fire",
    trigger_token: "t".repeat(32),
    step_up_receipt_id: "55555555-5555-4555-8555-555555555555",
  }))).status, 200);
  assertEquals((await handler(request("/connections/activate", { connection_id: OWNER }))).status, 200);
  assertEquals((await handler(request("/telegram/unlink", {}))).status, 200);
  assertEquals((await handler(request("/settings", {
    display_name: "Raj", timezone: "America/Chicago",
    notify_pre_market: true, notify_intraday: true, notify_post_market: true,
    notify_operational: true, schedule_pre_market: true,
    schedule_intraday: true, schedule_post_market: true,
  }, { method: "PATCH" }))).status, 200);
  assertEquals((await handler(request("/settings", {
    cron: "* * * * *",
  }, { method: "PATCH" }))).status, 400);
  assertEquals((await handler(request("/telegram/pairing-code", { owner_id: OWNER }))).status, 400);
  assertEquals(repository.calls.length, 6);
  const connectionBody = repository.calls[0].body as Record<string, unknown>;
  assertEquals(String(connectionBody.inbound_token_digest).length, 64);
  assertEquals(JSON.stringify(connectionBody).includes("A".repeat(43)), false);
  const pairingBody = repository.calls[1].body as Record<string, unknown>;
  assertEquals(pairingBody.step_up_receipt_id, "55555555-5555-4555-8555-555555555555");
  assertEquals(String(pairingBody.session_digest).length, 64);
  assertEquals(pairingBody.code_digest, "a43255350a50f61e0b614e953291560250a8d824c7a919ea1dab9504cfa35d7b");
  assertEquals(repository.calls[4].route, "POST /telegram/unlink");
  assertEquals(repository.calls[4].body, {});
});

Deno.test("pairing code is returned once and only its HMAC reaches persistence", async () => {
  const { handler, repository } = makeHandler();
  repository.response = { ok: true, data: { status: "issued", expires_at: "2026-09-02T20:10:00Z" } };
  const response = await handler(request("/telegram/pairing-code", {
    step_up_receipt_id: "55555555-5555-4555-8555-555555555555",
  }));
  const value = await response.json();
  assertEquals(value.data.code, "ABCD234567");
  assertEquals(Object.hasOwn(repository.calls[0].body as object, "code"), false);
  assertEquals(
    (repository.calls[0].body as Record<string, unknown>).code_digest,
    "a43255350a50f61e0b614e953291560250a8d824c7a919ea1dab9504cfa35d7b",
  );
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

Deno.test("lifecycle routes inject server-only session proof and revoke all sessions", async () => {
  const claims: AuthenticatedClaims = {
    sub: OWNER,
    role: "authenticated",
    session_id: "44444444-4444-4444-8444-444444444444",
    amr: [{ method: "otp", timestamp: Math.floor(Date.now() / 1000) }],
  };
  const { handler, repository, revoked } = makeHandler(claims);
  repository.response = {
    ok: true,
    data: {
      status: "challenge_created",
      challenge_id: "55555555-5555-4555-8555-555555555555",
      expires_at: "2026-09-03T16:10:00Z",
    },
  };
  assertEquals((await handler(request("/account/step-up/challenge", {}))).status, 200);
  const challengeBody = repository.calls[0].body as Record<string, unknown>;
  assertEquals(String(challengeBody.session_digest).length, 64);
  assertEquals(JSON.stringify(challengeBody).includes(String(claims.session_id)), false);

  repository.response = {
    ok: true,
    data: {
      status: "pending",
      deletion_request_id: "55555555-5555-4555-8555-555555555555",
      _telegram_cleanup: {
        record_required: true, previous_status: null,
        attempted: 0, deleted: 0, failed: 0,
        chat_id: "123456", message_ids: ["42"],
      },
    },
  };
  const deletion = await handler(request("/account/delete/confirm", {
    deletion_request_id: "55555555-5555-4555-8555-555555555555",
    step_up_receipt_id: "66666666-6666-4666-8666-666666666666",
    confirmation_phrase: "DELETE MY ACCOUNT",
  }));
  assertEquals(deletion.status, 200);
  assertEquals(revoked, [TOKEN]);
  const deletionBody = repository.calls[1].body as Record<string, unknown>;
  assertEquals(String(deletionBody.session_digest).length, 64);
  assertEquals(String(deletionBody.cleanup_token_digest).length, 64);
  assertEquals(Object.hasOwn(deletionBody, "otp"), false);
  const cleanupBody = repository.calls[2].body as Record<string, unknown>;
  assertEquals(cleanupBody.cleanup_token, "C".repeat(43));
  assertEquals(cleanupBody.status, "completed");
  assertEquals((await deletion.json()).data._telegram_cleanup, undefined);
});

Deno.test("deletion reports unavailable when its Telegram cleanup receipt cannot be recorded", async () => {
  const claims: AuthenticatedClaims = {
    sub: OWNER,
    role: "authenticated",
    session_id: "44444444-4444-4444-8444-444444444444",
    amr: [{ method: "otp", timestamp: Math.floor(Date.now() / 1000) }],
  };
  const { handler, repository, revoked } = makeHandler(claims);
  repository.response = {
    ok: true,
    data: {
      status: "pending",
      deletion_request_id: "55555555-5555-4555-8555-555555555555",
      _telegram_cleanup: {
        record_required: true, previous_status: null,
        attempted: 0, deleted: 0, failed: 0,
        chat_id: "123456", message_ids: ["42"],
      },
    },
  };
  repository.cleanupResponse = { ok: false, error: { code: "INTERNAL_ERROR" } };

  const response = await handler(request("/account/delete/confirm", {
    deletion_request_id: "55555555-5555-4555-8555-555555555555",
    step_up_receipt_id: "66666666-6666-4666-8666-666666666666",
    confirmation_phrase: "DELETE MY ACCOUNT",
  }));

  assertEquals(response.status, 200);
  assertEquals((await response.json()).data.telegram_cleanup.status, "unavailable");
  assertEquals(revoked, [TOKEN]);
});

Deno.test("exports return raw bounded attachments instead of JSON envelopes", async () => {
  const { handler, repository } = makeHandler();
  repository.response = {
    ok: true,
    data: {
      status: "ready",
      format: "csv",
      filename: "stock-agent-ledger.csv",
      media_type: "text/csv; charset=utf-8",
      body: "transaction_id\n",
    },
  };
  const response = await handler(request("/export/ledger.csv", {}, { method: "GET" }));
  assertEquals(response.status, 200);
  assertEquals(response.headers.get("content-disposition"), 'attachment; filename="stock-agent-ledger.csv"');
  assertEquals(response.headers.get("cache-control"), "no-store");
  assertEquals(await response.text(), "transaction_id\n");
});
