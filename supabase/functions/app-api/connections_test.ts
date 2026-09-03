import { assertEquals, assertNotEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { attachGatewayCredential, generateInboundConnectionSecret, prepareConnectionCreate } from "./connections.ts";

Deno.test("connection creation sends only a SHA-256 digest to persistence", async () => {
  const secret = "A".repeat(43);
  const prepared = await prepareConnectionCreate(
    { provider: "claude", consent_version: "provider-data-v1" }, () => secret,
  );
  assertEquals(prepared.secret, secret);
  assertEquals(typeof prepared.request.inbound_token_digest, "string");
  assertEquals(String(prepared.request.inbound_token_digest).length, 64);
  assertEquals(JSON.stringify(prepared.request).includes(secret), false);
  const output = attachGatewayCredential({ ok: true, data: {
    public_id: "11111111-1111-4111-8111-111111111111", status: "disabled",
  } }, secret);
  assertEquals((output.data as Record<string, unknown>).gateway_credential,
    `11111111-1111-4111-8111-111111111111.${secret}`);
});

Deno.test("reconnecting generates new 256-bit credentials", () => {
  const first = generateInboundConnectionSecret();
  const second = generateInboundConnectionSecret();
  assertEquals(first.length, 43);
  assertEquals(second.length, 43);
  assertNotEquals(first, second);
});
