import {
  assert,
  assertEquals,
  assertRejects,
} from "https://deno.land/std@0.224.0/assert/mod.ts";
import {
  exportJWK,
  generateKeyPair,
  SignJWT,
  type JWTHeaderParameters,
} from "npm:jose@6.2.10";

import { readBoundedJson } from "./bounded-json.ts";
import { assertAllowedOrigin, corsHeaders } from "./cors.ts";
import { HttpError, jsonError, jsonResponse } from "./errors.ts";
import { ProjectJwksCache, verifyUserJwt } from "./jwt.ts";
import { validateDatabaseUrl, withDatabase } from "./postgres.ts";


Deno.test("bounded JSON rejects content types and streamed overflow before parsing", async () => {
  await assertRejects(
    () => readBoundedJson(new Request("https://example.test", { method: "POST", body: "{}" }), 10),
    HttpError,
    "application/json",
  );
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('{"a":"'));
      controller.enqueue(new TextEncoder().encode("1234567890"));
      controller.enqueue(new TextEncoder().encode('"}'));
      controller.close();
    },
  });
  await assertRejects(
    () => readBoundedJson(new Request("https://example.test", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: stream,
      // Required by the Fetch implementation for streamed request bodies.
      duplex: "half",
    } as RequestInit), 12),
    HttpError,
    "too large",
  );
});

Deno.test("bounded JSON accepts one object within the byte ceiling", async () => {
  const parsed = await readBoundedJson(
    new Request("https://example.test", {
      method: "POST",
      headers: { "content-type": "application/json; charset=utf-8" },
      body: '{"ok":true}',
    }),
    64,
  );
  assertEquals(parsed, { ok: true });
});

Deno.test("CORS accepts only configured exact origins", () => {
  const request = new Request("https://api.example.test", {
    headers: { origin: "https://app.example.test" },
  });
  assertAllowedOrigin(request, ["https://app.example.test"]);
  assertEquals(corsHeaders(request, ["https://app.example.test"]), {
    "Access-Control-Allow-Origin": "https://app.example.test",
    "Access-Control-Allow-Credentials": "true",
    Vary: "Origin",
  });
  assertRejects(
    async () => assertAllowedOrigin(
      new Request("https://api.example.test", { headers: { origin: "https://app.example.test.evil" } }),
      ["https://app.example.test"],
    ),
    HttpError,
    "origin",
  );
});

Deno.test("stable JSON responses are no-store and errors omit internals", async () => {
  const success = jsonResponse(200, { ok: true });
  const failure = jsonError(new Error("database password must not escape"));

  assertEquals(success.headers.get("cache-control"), "no-store");
  assertEquals(failure.status, 500);
  assertEquals(failure.headers.get("cache-control"), "no-store");
  assertEquals(await failure.json(), { error: { code: "INTERNAL_ERROR" } });
});

async function signingFixture() {
  const { publicKey, privateKey } = await generateKeyPair("ES256");
  const jwk = await exportJWK(publicKey);
  jwk.kid = "test-key";
  jwk.alg = "ES256";
  jwk.use = "sig";
  return { privateKey, jwks: { keys: [jwk] } };
}

async function token(
  privateKey: CryptoKey,
  overrides: Record<string, unknown> = {},
  header: JWTHeaderParameters = { alg: "ES256", kid: "test-key", typ: "JWT" },
) {
  const issuer = typeof overrides.iss === "string"
    ? overrides.iss
    : "https://project.supabase.co/auth/v1";
  const audience = typeof overrides.aud === "string" ? overrides.aud : "authenticated";
  const { iss: _issuer, aud: _audience, ...payloadOverrides } = overrides;
  return await new SignJWT({
    role: "authenticated",
    ...payloadOverrides,
  })
    .setProtectedHeader(header)
    .setSubject("11111111-1111-4111-8111-111111111111")
    .setIssuer(issuer)
    .setAudience(audience)
    .setIssuedAt(1_788_400_000)
    .setExpirationTime(1_788_400_300)
    .sign(privateKey);
}

function authenticatedRequest(value: string) {
  return new Request("https://edge.example.test", {
    headers: { authorization: `Bearer ${value}` },
  });
}

Deno.test("asymmetric JWT verification binds signature issuer audience role and subject", async () => {
  const fixture = await signingFixture();
  const jwt = await token(fixture.privateKey);
  const claims = await verifyUserJwt(authenticatedRequest(jwt), fixture.jwks, {
    projectUrl: "https://project.supabase.co",
    now: new Date(1_788_400_100_000),
  });

  assertEquals(claims.sub, "11111111-1111-4111-8111-111111111111");
  assertEquals(claims.role, "authenticated");
});

Deno.test("JWT verification rejects wrong project, audience, expiry, signature, and algorithm", async () => {
  const fixture = await signingFixture();
  const other = await signingFixture();
  const valid = await token(fixture.privateKey);
  const wrongAudience = await token(fixture.privateKey, { aud: "other" });
  const wrongIssuer = await token(fixture.privateKey, { iss: "https://other.supabase.co/auth/v1" });
  const wrongSignature = await token(other.privateKey);
  const missingExpiry = await new SignJWT({ role: "authenticated" })
    .setProtectedHeader({ alg: "ES256", kid: "test-key", typ: "JWT" })
    .setSubject("11111111-1111-4111-8111-111111111111")
    .setIssuer("https://project.supabase.co/auth/v1")
    .setAudience("authenticated")
    .setIssuedAt(1_788_400_000)
    .sign(fixture.privateKey);
  const symmetric = await new SignJWT({
    role: "authenticated",
    sub: "11111111-1111-4111-8111-111111111111",
    iss: "https://project.supabase.co/auth/v1",
    aud: "authenticated",
    exp: 1_788_400_300,
  }).setProtectedHeader({ alg: "HS256", kid: "test-key" })
    .sign(new TextEncoder().encode("a-secret-that-is-long-enough-for-hmac"));

  for (const candidate of [wrongAudience, wrongIssuer, wrongSignature, missingExpiry, symmetric]) {
    await assertRejects(
      () => verifyUserJwt(authenticatedRequest(candidate), fixture.jwks, {
        projectUrl: "https://project.supabase.co",
        now: new Date(1_788_400_100_000),
      }),
      HttpError,
      "unauthorized",
    );
  }
  await assertRejects(
    () => verifyUserJwt(authenticatedRequest(valid), fixture.jwks, {
      projectUrl: "https://project.supabase.co",
      now: new Date(1_788_401_000_000),
    }),
    HttpError,
    "unauthorized",
  );
});

Deno.test("project JWKS cache is bounded and refreshes after its short TTL", async () => {
  let calls = 0;
  let now = 1_000;
  const fixture = await signingFixture();
  const fetcher = () => {
    calls += 1;
    return Promise.resolve(Response.json(fixture.jwks));
  };
  const cache = new ProjectJwksCache("https://project.supabase.co", {
    ttlMs: 60_000,
    fetcher: fetcher as typeof fetch,
    now: () => now,
  });

  assertEquals(await cache.get(), fixture.jwks);
  assertEquals(await cache.get(), fixture.jwks);
  assertEquals(calls, 1);
  now += 60_001;
  assertEquals(await cache.get(), fixture.jwks);
  assertEquals(calls, 2);
});

Deno.test("database URL requires the IPv4 Supavisor session endpoint", () => {
  const valid = "postgres://runtime.project:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres";
  assertEquals(validateDatabaseUrl(valid), valid);
  for (const candidate of [
    "postgres://runtime.project:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
    "postgres://runtime.project:secret@db.project.supabase.co:5432/postgres",
    "http://runtime.project:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
    "postgres://runtime.project:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=disable",
  ]) {
    assertRejects(
      async () => validateDatabaseUrl(candidate),
      Error,
      "Supavisor session",
    );
  }
});

Deno.test("database helper uses one transaction, an allow-listed RPC, and always closes", async () => {
  const events: string[] = [];
  const transaction = Object.assign(
    async (strings: TemplateStringsArray, ..._values: unknown[]) => {
      const statement = strings.join("?").replace(/\s+/g, " ").trim();
      events.push(statement);
      return statement.startsWith("SET LOCAL") ? [] : [{ result: { ok: true } }];
    },
    {
      json: (value: unknown) => value,
      begin: async <T>(_callback: (sql: unknown) => Promise<T>) => {
        throw new Error("nested begin is unavailable");
      },
      end: async () => undefined,
    },
  );
  const sql = Object.assign(transaction, {
    begin: async <T>(callback: (value: typeof transaction) => Promise<T>) => {
      events.push("BEGIN");
      return await callback(transaction);
    },
    end: async () => {
      events.push("END");
    },
  });
  const factory = (_url: string, options: Record<string, unknown>) => {
    assertEquals(options.max, 1);
    assertEquals(options.prepare, true);
    return sql;
  };

  const result = await withDatabase(
    "postgres://runtime.project:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
    (client) => client.callJsonRpc("agent_start_run", { request_id: "opaque" }),
    factory as Parameters<typeof withDatabase>[2],
  );

  assertEquals(result, { ok: true });
  assertEquals(events[0], "BEGIN");
  assert(events[1].startsWith("SET LOCAL statement_timeout"));
  assert(events[2].includes("machine.agent_start_run"));
  assertEquals(events.at(-1), "END");
});
