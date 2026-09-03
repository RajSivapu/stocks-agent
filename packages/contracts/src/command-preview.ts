import { parseCommandInput, type CommandInput } from "./portfolio.ts";


const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA_RE = /^[0-9a-f]{64}$/;

function record(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${field} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(row: Record<string, unknown>, keys: readonly string[]): void {
  if (Object.keys(row).length !== keys.length || keys.some((key) => !Object.hasOwn(row, key))) {
    throw new Error("request fields are invalid");
  }
}

export type CommandRequest = {
  idempotency_key: string;
  command: CommandInput;
};

export function parseCommandRequest(value: unknown): CommandRequest {
  const row = record(value, "request");
  exactKeys(row, ["idempotency_key", "command"]);
  if (typeof row.idempotency_key !== "string" || !UUID_RE.test(row.idempotency_key)) {
    throw new Error("idempotency_key must be a UUID");
  }
  return {
    idempotency_key: row.idempotency_key.toLowerCase(),
    command: parseCommandInput(row.command),
  };
}

export type CommandPreviewReceipt = {
  command_id: string;
  status: "previewed";
  preview_digest: string;
  expires_at: string;
  operation: CommandInput["operation"];
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  warnings: string[];
};

export function parseCommandPreviewReceipt(value: unknown): CommandPreviewReceipt {
  const row = record(value, "preview");
  exactKeys(row, [
    "command_id",
    "status",
    "preview_digest",
    "expires_at",
    "operation",
    "before",
    "after",
    "warnings",
  ]);
  if (typeof row.command_id !== "string" || !UUID_RE.test(row.command_id)) {
    throw new Error("command_id is invalid");
  }
  if (row.status !== "previewed" || typeof row.preview_digest !== "string" ||
    !SHA_RE.test(row.preview_digest)) {
    throw new Error("preview receipt is invalid");
  }
  if (typeof row.expires_at !== "string" || Number.isNaN(Date.parse(row.expires_at))) {
    throw new Error("expires_at is invalid");
  }
  const operation = row.operation;
  if (!["buy", "sell", "sell_all", "stop", "plan", "cancel_plan", "correct_transaction"].includes(operation as string)) {
    throw new Error("operation is invalid");
  }
  const warnings = row.warnings;
  if (!Array.isArray(warnings) || warnings.some((warning) => typeof warning !== "string")) {
    throw new Error("warnings are invalid");
  }
  return {
    command_id: row.command_id,
    status: "previewed",
    preview_digest: row.preview_digest,
    expires_at: row.expires_at,
    operation: operation as CommandInput["operation"],
    before: record(row.before, "before"),
    after: record(row.after, "after"),
    warnings,
  };
}
