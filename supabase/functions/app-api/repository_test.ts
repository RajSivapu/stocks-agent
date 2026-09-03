import {
  assertEquals,
  assertRejects,
} from "https://deno.land/std@0.224.0/assert/mod.ts";

import { createAppApiRepository } from "./repository.ts";


Deno.test("public health repository sends only the anon key to the exact bounded RPC", async () => {
  let observed: Request | undefined;
  const repository = createAppApiRepository(
    "https://project.supabase.co",
    "public-anon-key",
    ((input: string | URL | Request, init?: RequestInit) => {
      observed = new Request(input, init);
      return Promise.resolve(Response.json({ status: "ok", schema_version: 1 }));
    }) as typeof fetch,
  );
  assertEquals(await repository.publicHealth(), { status: "ok", schema_version: 1 });
  assertEquals(observed?.url, "https://project.supabase.co/rest/v1/rpc/public_health");
  assertEquals(observed?.headers.get("apikey"), "public-anon-key");
  assertEquals(observed?.headers.has("authorization"), false);
  assertEquals(await observed?.text(), "{}");
});


Deno.test("repository forwards only anon key, original bearer, and bounded dispatch arguments", async () => {
  let observed: Request | undefined;
  const fetcher = (input: string | URL | Request, init?: RequestInit) => {
    observed = new Request(input, init);
    return Promise.resolve(Response.json({ ok: true, data: { status: "previewed" } }));
  };
  const repository = createAppApiRepository(
    "https://project.supabase.co",
    "public-anon-key",
    fetcher as typeof fetch,
  );
  const result = await repository.dispatch({
    route: "POST /portfolio/preview",
    requestId: "11111111-1111-4111-8111-111111111111",
    ipDigest: "a".repeat(64),
    bearerToken: "header.payload.signature",
    body: { idempotency_key: "opaque" },
  });

  assertEquals(result, { ok: true, data: { status: "previewed" } });
  assertEquals(observed?.url, "https://project.supabase.co/rest/v1/rpc/app_dispatch");
  assertEquals(observed?.headers.get("apikey"), "public-anon-key");
  assertEquals(observed?.headers.get("authorization"), "Bearer header.payload.signature");
  assertEquals(observed?.headers.get("content-profile"), "api");
  assertEquals(await observed?.json(), {
    p_route: "POST /portfolio/preview",
    p_request_id: "11111111-1111-4111-8111-111111111111",
    p_ip_digest: "a".repeat(64),
    p_request: { idempotency_key: "opaque" },
  });
});

Deno.test("repository rejects HTTP errors and oversized database responses without exposing bodies", async () => {
  const rejected = createAppApiRepository(
    "https://project.supabase.co",
    "public-anon-key",
    (() => Promise.resolve(new Response("secret SQL details", { status: 400 }))) as typeof fetch,
  );
  await assertRejects(
    () => rejected.dispatch({ bearerToken: "a.b.c" }),
    Error,
    "database request failed",
  );

  const oversized = createAppApiRepository(
    "https://project.supabase.co",
    "public-anon-key",
    (() => Promise.resolve(new Response("x".repeat(65_537)))) as typeof fetch,
  );
  await assertRejects(
    () => oversized.dispatch({ bearerToken: "a.b.c" }),
    Error,
    "too large",
  );
});
