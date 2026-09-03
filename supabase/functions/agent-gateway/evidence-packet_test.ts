import { assertEquals, assertRejects, assertThrows } from "https://deno.land/std@0.224.0/assert/mod.ts";
import {
  createEvidencePacket,
  decodeSigningKey,
  verifyEvidencePackets,
  type EvidencePacketPayload,
} from "./evidence-packet.ts";

const KEY = "A".repeat(43);
const RUN_ID = "11111111-1111-4111-8111-111111111111";

function payload(): EvidencePacketPayload {
  return {
    version: 1,
    run_id: RUN_ID,
    phase: "intraday",
    market_date: "2026-09-03",
    issued_at: "2026-09-03T17:00:00.000Z",
    expires_at: "2026-09-03T17:15:00.000Z",
    facts: [{
      evidence_id: "quote-vti",
      source_run_id: null,
      category: "market_snapshot",
      source_identifier: "yahoo-chart",
      reference_identifier: "https://query1.finance.yahoo.com/v8/finance/chart/VTI",
      observed_at: "2026-09-03T16:59:00.000Z",
      retrieved_at: "2026-09-03T17:00:00.000Z",
      revalidated_at: null,
      content_hash: "b".repeat(64),
      claims: [],
      status: "fresh",
    }],
    search_receipt: null,
  };
}

Deno.test("evidence packet is canonical, signed by a server-only key, and run bound", async () => {
  const packet = await createEvidencePacket(payload(), decodeSigningKey(KEY));
  const verified = await verifyEvidencePackets(
    [packet], decodeSigningKey(KEY), RUN_ID, new Date("2026-09-03T17:05:00.000Z"),
  );
  assertEquals(verified.facts[0].evidence_id, "quote-vti");
  assertEquals(verified.search_receipts, []);
  await assertRejects(
    () => verifyEvidencePackets([packet], decodeSigningKey(KEY), crypto.randomUUID(), new Date("2026-09-03T17:05:00.000Z")),
    Error,
    "run",
  );
});

Deno.test("tampered, expired, duplicate, and malformed packets fail closed", async () => {
  const packet = await createEvidencePacket(payload(), decodeSigningKey(KEY));
  const tampered = structuredClone(packet);
  tampered.payload.facts[0].content_hash = "c".repeat(64);
  await assertRejects(
    () => verifyEvidencePackets([tampered], decodeSigningKey(KEY), RUN_ID, new Date("2026-09-03T17:05:00.000Z")),
    Error,
    "signature",
  );
  await assertRejects(
    () => verifyEvidencePackets([packet], decodeSigningKey(KEY), RUN_ID, new Date("2026-09-03T17:16:00.000Z")),
    Error,
    "expired",
  );
  await assertRejects(
    () => verifyEvidencePackets([packet, packet], decodeSigningKey(KEY), RUN_ID, new Date("2026-09-03T17:05:00.000Z")),
    Error,
    "duplicate",
  );
  assertThrows(() => decodeSigningKey("short"), Error, "256-bit");
});
