/// <reference lib="deno.ns" />

import { parseCommandPreviewReceipt, parseCommandRequest } from "./command-preview.ts";


function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertThrows(callback: () => unknown, message: string): void {
  try {
    callback();
  } catch (error) {
    if (error instanceof Error && error.message.includes(message)) return;
    throw error;
  }
  throw new Error(`expected ${message}`);
}

Deno.test("command request binds an idempotency key to normalized command input", () => {
  const parsed = parseCommandRequest({
    idempotency_key: "11111111-1111-4111-8111-111111111111",
    command: {
      operation: "buy",
      ticker: "VTI",
      quantity: "0.789142",
      fill_price: "376.63",
      fees: "0.00",
      cash_total: "297.21",
      executed_on: "2026-08-19",
      bucket: "core",
      plan_deposit_amount: "300.00",
    },
  });

  assertEquals(parsed.command.operation, "buy");
  assertEquals(
    parsed.command.operation === "buy" ? parsed.command.plan_deposit_amount : null,
    "300",
  );

  const inheritedBucket = parseCommandRequest({
    idempotency_key: "33333333-3333-4333-8333-333333333333",
    command: {
      operation: "buy",
      ticker: "VTI",
      quantity: "0.1",
      fill_price: "380.00",
      fees: "0.00",
      cash_total: "38.00",
      executed_on: "2026-09-02",
      plan_deposit_amount: "300.00",
    },
  });
  assertEquals(
    inheritedBucket.command.operation === "buy"
      ? inheritedBucket.command.plan_deposit_amount
      : null,
    "300",
  );
});

Deno.test("command request rejects caller-owned authority and unknown fields", () => {
  assertThrows(() => parseCommandRequest({
    idempotency_key: "11111111-1111-4111-8111-111111111111",
    owner_id: "22222222-2222-4222-8222-222222222222",
    command: { operation: "cancel_plan", ticker: "VTI" },
  }), "request fields");
  assertThrows(() => parseCommandRequest({
    idempotency_key: "11111111-1111-4111-8111-111111111111",
    command: { operation: "sell", ticker: "VTI", quantity: 1, fill_price: "1", fees: "0", cash_total: null, executed_on: "2026-09-02" },
  }), "shares");
  assertThrows(() => parseCommandRequest({
    idempotency_key: "11111111-1111-4111-8111-111111111111",
    command: {
      operation: "correct_transaction",
      transaction_id: "22222222-2222-4222-8222-222222222222",
      replacement: { operation: "stop", ticker: "VTI", stop: "300" },
    },
  }), "replacement must be a buy or sell");
});

Deno.test("preview receipt is exact and contains no owner or delivery authority", () => {
  const value = {
    command_id: "22222222-2222-4222-8222-222222222222",
    status: "previewed",
    preview_digest: "a".repeat(64),
    expires_at: "2026-09-02T20:15:00Z",
    operation: "buy",
    before: { shares: "0" },
    after: { shares: "1" },
    warnings: ["UNCLASSIFIED_BUCKET"],
  };
  assertEquals(parseCommandPreviewReceipt(value), value);
  assertThrows(() => parseCommandPreviewReceipt({ ...value, owner_id: "hidden" }), "request fields");
});
