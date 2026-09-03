import { type Money, type Price, type Shares, parseMoney, parsePrice, parseShares } from "./decimal.ts";

export type Bucket = "core" | "growth" | "speculative" | "unclassified";
export type CommandOperation = "buy" | "sell" | "sell_all" | "stop" | "plan" |
  "cancel_plan" | "correct_transaction";

export type CommandInput =
  | {
    operation: "buy" | "sell";
    ticker: string;
    quantity: Shares;
    fill_price: Price;
    fees: Money;
    cash_total: Money | null;
    executed_on: string;
    bucket?: Bucket;
  }
  | {
    operation: "sell_all";
    ticker: string;
    fill_price: Price;
    fees: Money;
    cash_total: Money | null;
    executed_on: string;
  }
  | { operation: "stop"; ticker: string; stop: Price }
  | {
    operation: "plan";
    ticker: string;
    deposit_amount: Money;
    cadence: "monthly";
    next_due_on: string;
    bucket: "core";
  }
  | { operation: "cancel_plan"; ticker: string }
  | { operation: "correct_transaction"; transaction_id: string; replacement: CommandInput };

function record(value: unknown, field = "command"): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${field} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  row: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
): void {
  const allowed = new Set([...required, ...optional]);
  for (const key of Object.keys(row)) {
    if (!allowed.has(key)) throw new Error(`extra field: ${key}`);
  }
  for (const key of required) {
    if (!Object.hasOwn(row, key)) throw new Error(`missing field: ${key}`);
  }
}

function ticker(value: unknown): string {
  if (typeof value !== "string" || value.length > 15 ||
    !/^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?$/.test(value)) {
    throw new Error("ticker must be canonical uppercase text");
  }
  return value;
}

function calendarDate(value: unknown, field: string): string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new Error(`${field} must be YYYY-MM-DD`);
  }
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.valueOf()) || date.toISOString().slice(0, 10) !== value) {
    throw new Error(`${field} must be a real calendar date`);
  }
  return value;
}

function bucket(value: unknown): Bucket {
  if (value !== "core" && value !== "growth" && value !== "speculative" &&
    value !== "unclassified") {
    throw new Error("bucket is invalid");
  }
  return value;
}

function nullableMoney(value: unknown): Money | null {
  return value === null ? null : parseMoney(value);
}

export function parseCommandInput(value: unknown): CommandInput {
  const row = record(value);
  const operation = row.operation;
  if (operation === "buy" || operation === "sell") {
    exactKeys(
      row,
      ["operation", "ticker", "quantity", "fill_price", "fees", "cash_total", "executed_on"],
      operation === "buy" ? ["bucket"] : [],
    );
    const parsed: Extract<CommandInput, { operation: "buy" | "sell" }> = {
      operation,
      ticker: ticker(row.ticker),
      quantity: parseShares(row.quantity),
      fill_price: parsePrice(row.fill_price),
      fees: parseMoney(row.fees),
      cash_total: nullableMoney(row.cash_total),
      executed_on: calendarDate(row.executed_on, "executed_on"),
    };
    if (operation === "buy" && row.bucket !== undefined) {
      return { ...parsed, bucket: bucket(row.bucket) };
    }
    return parsed;
  }
  if (operation === "sell_all") {
    exactKeys(row, ["operation", "ticker", "fill_price", "fees", "cash_total", "executed_on"]);
    return {
      operation,
      ticker: ticker(row.ticker),
      fill_price: parsePrice(row.fill_price),
      fees: parseMoney(row.fees),
      cash_total: nullableMoney(row.cash_total),
      executed_on: calendarDate(row.executed_on, "executed_on"),
    };
  }
  if (operation === "stop") {
    exactKeys(row, ["operation", "ticker", "stop"]);
    return { operation, ticker: ticker(row.ticker), stop: parsePrice(row.stop) };
  }
  if (operation === "plan") {
    exactKeys(row, ["operation", "ticker", "deposit_amount", "cadence", "next_due_on", "bucket"]);
    if (row.cadence !== "monthly") throw new Error("cadence must be monthly");
    if (row.bucket !== "core") throw new Error("bucket must be core for recurring plans");
    return {
      operation,
      ticker: ticker(row.ticker),
      deposit_amount: parseMoney(row.deposit_amount),
      cadence: "monthly",
      next_due_on: calendarDate(row.next_due_on, "next_due_on"),
      bucket: "core",
    };
  }
  if (operation === "cancel_plan") {
    exactKeys(row, ["operation", "ticker"]);
    return { operation, ticker: ticker(row.ticker) };
  }
  if (operation === "correct_transaction") {
    exactKeys(row, ["operation", "transaction_id", "replacement"]);
    if (typeof row.transaction_id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(row.transaction_id)) {
      throw new Error("transaction_id must be a UUID");
    }
    const replacement = parseCommandInput(row.replacement);
    if (replacement.operation === "correct_transaction") {
      throw new Error("replacement cannot be another correction");
    }
    return { operation, transaction_id: row.transaction_id, replacement };
  }
  throw new Error("operation is invalid");
}
