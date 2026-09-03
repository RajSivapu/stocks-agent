/// <reference lib="deno.ns" />

import { parseCommandInput } from "./portfolio.ts";

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

Deno.test("buy command is canonical and keeps cash total separate from recurring deposit", () => {
  assertEquals(parseCommandInput({
    operation: "buy",
    ticker: "CENX",
    quantity: "43.74819200",
    fill_price: "47.0200",
    fees: "1.25",
    cash_total: "2058.29",
    executed_on: "2026-08-17",
    bucket: "speculative",
  }), {
    operation: "buy",
    ticker: "CENX",
    quantity: "43.748192",
    fill_price: "47.02",
    fees: "1.25",
    cash_total: "2058.29",
    executed_on: "2026-08-17",
    bucket: "speculative",
  });
});

Deno.test("command parser rejects extra authority and unknown fields", () => {
  const valid = {
    operation: "sell",
    ticker: "CENX",
    quantity: "1",
    fill_price: "50",
    fees: "0",
    cash_total: null,
    executed_on: "2026-09-02",
  };
  assertThrows(() => parseCommandInput({ ...valid, owner_id: crypto.randomUUID() }), "extra field");
  assertThrows(() => parseCommandInput({ ...valid, delivery_status: "delivered" }), "extra field");
});

Deno.test("new buy may be explicitly unclassified but never gets an invented bucket", () => {
  const parsed = parseCommandInput({
    operation: "buy",
    ticker: "AAPL",
    quantity: "1",
    fill_price: "200",
    fees: "0",
    cash_total: null,
    executed_on: "2026-09-02",
    bucket: "unclassified",
  });
  if (parsed.operation !== "buy") throw new Error("expected a buy command");
  assertEquals(parsed.bucket, "unclassified");
  assertThrows(() => parseCommandInput({ ...parsed, bucket: "retirement" }), "bucket");
});

Deno.test("monthly plan deposit is not parsed as a trade cash total", () => {
  assertEquals(parseCommandInput({
    operation: "plan",
    ticker: "VTI",
    deposit_amount: "300.00",
    cadence: "monthly",
    next_due_on: "2026-09-21",
    bucket: "core",
  }), {
    operation: "plan",
    ticker: "VTI",
    deposit_amount: "300",
    cadence: "monthly",
    next_due_on: "2026-09-21",
    bucket: "core",
  });
});

Deno.test("ticker and calendar dates use canonical forms", () => {
  const command = {
    operation: "stop",
    ticker: "brk.b",
    stop: "390.5",
  };
  assertThrows(() => parseCommandInput(command), "ticker");
  assertThrows(() => parseCommandInput({
    operation: "sell_all",
    ticker: "CENX",
    fill_price: "50",
    fees: "0",
    cash_total: null,
    executed_on: "2026-02-30",
  }), "executed_on");
});
