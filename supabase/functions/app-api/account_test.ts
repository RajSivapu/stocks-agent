import {
  assertEquals,
  assertRejects,
} from "https://deno.land/std@0.224.0/assert/mod.ts";

import {
  accountRequestAttestation,
  attachCleanupTokenDigest,
  deleteRecentTelegramMessages,
  exportResponse,
  sanitizeDeletionResponse,
} from "./account.ts";


const SESSION_A = "11111111-1111-4111-8111-111111111111";
const PEPPER = "step-up-test-pepper-that-is-at-least-32-bytes";

Deno.test("step-up challenge injects an HMAC session digest, never the session id", async () => {
  const value = await accountRequestAttestation(
    "account_step_up_challenge",
    {},
    { sub: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", role: "authenticated", session_id: SESSION_A },
    PEPPER,
    1_788_400_000,
  );
  assertEquals(typeof value.session_digest, "string");
  assertEquals(String(value.session_digest).length, 64);
  assertEquals(JSON.stringify(value).includes(SESSION_A), false);
});

Deno.test("Telegram deletion is bounded, truthful, and strips raw identifiers from the client", async () => {
  const prepared = await attachCleanupTokenDigest({}, () => "C".repeat(43));
  assertEquals(String(prepared.request.cleanup_token_digest).length, 64);
  assertEquals(JSON.stringify(prepared.request).includes("C".repeat(43)), false);
  const calls: Array<Record<string, unknown>> = [];
  const persisted = {
    ok: true,
    data: {
      status: "pending",
      deletion_request_id: SESSION_A,
      _telegram_cleanup: {
        record_required: true, previous_status: null,
        attempted: 0, deleted: 0, failed: 0,
        chat_id: "123456", message_ids: ["41", "42"],
      },
    },
  };
  const cleanup = await deleteRecentTelegramMessages(
    persisted,
    "123456:" + "T".repeat(24),
    (_url, options) => {
      calls.push(JSON.parse(String(options?.body)) as Record<string, unknown>);
      return Promise.resolve(new Response(calls.length === 1 ? '{"ok":true}' : '{"ok":false}', {
        status: 200,
      }));
    },
  );
  assertEquals(cleanup, {
    status: "partial", attempted: 2, deleted: 1, failed: 1, recordRequired: true,
  });
  assertEquals(calls, [
    { chat_id: "123456", message_id: "41" },
    { chat_id: "123456", message_id: "42" },
  ]);
  const publicResult = sanitizeDeletionResponse(persisted, cleanup);
  assertEquals(JSON.stringify(publicResult).includes("123456"), false);
  assertEquals(JSON.stringify(publicResult).includes("message_ids"), false);
  assertEquals(JSON.stringify(publicResult).includes("recordRequired"), false);
});

Deno.test("repeated deletion confirmation reuses the recorded Telegram outcome", async () => {
  let calls = 0;
  const persisted = {
    ok: true,
    data: {
      status: "pending",
      deletion_request_id: SESSION_A,
      _telegram_cleanup: {
        record_required: false, previous_status: "completed",
        attempted: 2, deleted: 2, failed: 0,
        chat_id: null, message_ids: [],
      },
    },
  };
  const cleanup = await deleteRecentTelegramMessages(
    persisted,
    "123456:" + "T".repeat(24),
    () => { calls += 1; return Promise.reject(new Error("must not call Telegram")); },
  );
  assertEquals(cleanup, {
    status: "completed", attempted: 2, deleted: 2, failed: 0, recordRequired: false,
  });
  assertEquals(calls, 0);
});

Deno.test("step-up completion requires a current otp AMR entry", async () => {
  const base = { sub: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", role: "authenticated" as const, session_id: SESSION_A };
  await assertRejects(() => accountRequestAttestation(
    "account_step_up_complete", { challenge_id: SESSION_A },
    { ...base, amr: [{ method: "token_refresh", timestamp: 1_788_399_990 }] },
    PEPPER, 1_788_400_000,
  ));
  await assertRejects(() => accountRequestAttestation(
    "account_step_up_complete", { challenge_id: SESSION_A },
    { ...base, amr: [{ method: "otp", timestamp: 1_788_399_699 }] },
    PEPPER, 1_788_400_000,
  ));
  const value = await accountRequestAttestation(
    "account_step_up_complete", { challenge_id: SESSION_A },
    { ...base, amr: [{ method: "otp", timestamp: 1_788_399_990 }] },
    PEPPER, 1_788_400_000,
  );
  assertEquals(value.auth_method, "otp");
  assertEquals(value.authenticated_at, "2026-09-03T01:46:30.000Z");
});

Deno.test("account export becomes a bounded attachment with no-store", () => {
  const response = exportResponse({
    ok: true,
    data: {
      status: "ready",
      format: "json",
      filename: "stock-agent-account.json",
      media_type: "application/json; charset=utf-8",
      body: "{\"schema_version\":1}\n",
    },
  }, { "access-control-allow-origin": "https://app.example.test" });
  assertEquals(response.status, 200);
  assertEquals(response.headers.get("content-disposition"), 'attachment; filename="stock-agent-account.json"');
  assertEquals(response.headers.get("cache-control"), "no-store");
  assertEquals(response.headers.get("x-content-type-options"), "nosniff");
});

Deno.test("account export rejects unsafe metadata and oversized bodies", () => {
  for (const data of [
    { status: "ready", format: "json", filename: "bad\r\nname.json", media_type: "application/json; charset=utf-8", body: "{}" },
    { status: "ready", format: "json", filename: "stock-agent-account.json", media_type: "text/html", body: "{}" },
    { status: "ready", format: "json", filename: "stock-agent-account.json", media_type: "application/json; charset=utf-8", body: "x".repeat(5 * 1024 * 1024 + 1) },
  ]) {
    try {
      exportResponse({ ok: true, data }, {});
      throw new Error("expected export rejection");
    } catch (error) {
      assertEquals((error as Error).message, "invalid export response");
    }
  }
});
