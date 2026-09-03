/// <reference lib="deno.ns" />

import { legacyEnvelopeToV2, parseProviderEnvelopeV2 } from "./provider.ts";

function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertThrows(callback: () => unknown, expected: string): void {
  try {
    callback();
  } catch (error) {
    if (error instanceof Error && error.message.includes(expected)) return;
    throw error;
  }
  throw new Error(`expected error containing ${expected}`);
}

function envelope() {
  return {
    contract_version: 2,
    operation: "submit_analysis",
    request_id: "c5ef7765-07e4-47ad-9b46-223585d3048a",
    run_id: "aeb2a10d-dac3-4a21-bf99-7777cf9e2eed",
    dry_run: false,
    payload: { candidates: [] },
  };
}

Deno.test("provider V2 envelope accepts only the reviewed top-level contract", () => {
  assertEquals(parseProviderEnvelopeV2(envelope()), envelope());
  assertThrows(() => parseProviderEnvelopeV2({ ...envelope(), owner_id: crypto.randomUUID() }), "extra field");
  assertThrows(() => parseProviderEnvelopeV2({ ...envelope(), contract_version: 3 }), "contract_version");
});

Deno.test("provider input cannot claim server authority at any nested depth", () => {
  for (const forbidden of [
    { owner_id: crypto.randomUUID() },
    { result: { verified_price: "47.02" } },
    { result: { policy_status: "approved" } },
    { result: { delivery_status: "delivered", telegram_message_id: 5 } },
  ]) {
    assertThrows(
      () => parseProviderEnvelopeV2({ ...envelope(), payload: forbidden }),
      "server authority",
    );
  }
});

Deno.test("provider envelope bounds candidates and serialized bytes", () => {
  const candidates = Array.from({ length: 21 }, (_, index) => ({ ticker: `T${index}` }));
  assertThrows(
    () => parseProviderEnvelopeV2({ ...envelope(), payload: { candidates } }),
    "20 candidates",
  );
  assertThrows(
    () => parseProviderEnvelopeV2({ ...envelope(), payload: { text: "x".repeat(65_536) } }),
    "64 KiB",
  );
});

Deno.test("run identity is required except when starting a run", () => {
  assertThrows(() => parseProviderEnvelopeV2({ ...envelope(), run_id: null }), "run_id");
  const start = {
    ...envelope(),
    operation: "start_run",
    run_id: null,
    payload: { trigger_request_id: "bc21db4a-bba4-445e-bd38-99c38b3d9222" },
  };
  assertEquals(parseProviderEnvelopeV2(start), start);
});

Deno.test("legacy conversion is explicit and maps only known operations", () => {
  const legacy = {
    schema_version: 1,
    operation: "read_context",
    request_id: "c5ef7765-07e4-47ad-9b46-223585d3048a",
    run_id: "aeb2a10d-dac3-4a21-bf99-7777cf9e2eed",
    dry_run: true,
    payload: {},
  };
  assertEquals(legacyEnvelopeToV2(legacy), {
    contract_version: 2,
    operation: "read_bounded_context",
    request_id: legacy.request_id,
    run_id: legacy.run_id,
    dry_run: true,
    payload: {},
  });
  assertThrows(() => legacyEnvelopeToV2({ ...legacy, operation: "send_telegram" }), "legacy operation");
  assertThrows(() => legacyEnvelopeToV2({ ...legacy, extra: true }), "extra field");
});
