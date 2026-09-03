export type ProviderOperation = "start_run" | "read_bounded_context" | "submit_analysis" |
  "record_permitted_artifacts" | "grade_due_decisions" | "finish_run";

export type Phase = "pre-market" | "intraday" | "post-market" | "on-demand";

export interface ProviderEnvelopeV2 {
  contract_version: 2;
  operation: ProviderOperation;
  request_id: string;
  run_id: string | null;
  dry_run: boolean;
  payload: unknown;
}

const OPERATIONS: readonly ProviderOperation[] = [
  "start_run",
  "read_bounded_context",
  "submit_analysis",
  "record_permitted_artifacts",
  "grade_due_decisions",
  "finish_run",
];
const FORBIDDEN_AUTHORITY = new Set([
  "owner_id",
  "telegram_message_id",
  "telegram_message_ids",
  "policy_status",
  "verified_price",
  "delivery_status",
]);
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function record(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${field} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(row: Record<string, unknown>, keys: readonly string[]): void {
  const expected = new Set(keys);
  for (const key of Object.keys(row)) {
    if (!expected.has(key)) throw new Error(`extra field: ${key}`);
  }
  for (const key of keys) {
    if (!Object.hasOwn(row, key)) throw new Error(`missing field: ${key}`);
  }
}

function uuid(value: unknown, field: string): string {
  if (typeof value !== "string" || !UUID_RE.test(value)) {
    throw new Error(`${field} must be a UUID`);
  }
  return value;
}

function rejectServerAuthority(value: unknown, seen = new WeakSet<object>()): void {
  if (typeof value !== "object" || value === null) return;
  if (seen.has(value)) throw new Error("payload must not contain cycles");
  seen.add(value);
  if (Array.isArray(value)) {
    for (const item of value) rejectServerAuthority(item, seen);
    return;
  }
  for (const [key, nested] of Object.entries(value)) {
    if (FORBIDDEN_AUTHORITY.has(key)) throw new Error(`payload claims server authority: ${key}`);
    rejectServerAuthority(nested, seen);
  }
}

export function parseProviderEnvelopeV2(value: unknown): ProviderEnvelopeV2 {
  const row = record(value, "envelope");
  exactKeys(row, ["contract_version", "operation", "request_id", "run_id", "dry_run", "payload"]);
  let encoded: string;
  try {
    encoded = JSON.stringify(value);
  } catch {
    throw new Error("provider envelope must be serializable");
  }
  if (new TextEncoder().encode(encoded).byteLength > 64 * 1024) {
    throw new Error("provider envelope exceeds 64 KiB");
  }
  if (row.contract_version !== 2) throw new Error("contract_version must be 2");
  if (typeof row.operation !== "string" || !OPERATIONS.includes(row.operation as ProviderOperation)) {
    throw new Error("operation is invalid");
  }
  const operation = row.operation as ProviderOperation;
  const requestId = uuid(row.request_id, "request_id");
  const runId = row.run_id === null ? null : uuid(row.run_id, "run_id");
  if (operation === "start_run" ? runId !== null : runId === null) {
    throw new Error("run_id must be null only for start_run");
  }
  if (typeof row.dry_run !== "boolean") throw new Error("dry_run must be boolean");
  rejectServerAuthority(row.payload);
  if (typeof row.payload === "object" && row.payload !== null && !Array.isArray(row.payload)) {
    const candidates = (row.payload as Record<string, unknown>).candidates;
    if (Array.isArray(candidates) && candidates.length > 20) {
      throw new Error("provider payload supports at most 20 candidates");
    }
  }
  return {
    contract_version: 2,
    operation,
    request_id: requestId,
    run_id: runId,
    dry_run: row.dry_run,
    payload: row.payload,
  };
}

const LEGACY_OPERATIONS: Record<string, ProviderOperation> = {
  start_run: "start_run",
  read_context: "read_bounded_context",
  record_artifacts: "record_permitted_artifacts",
  grade_due_decisions: "grade_due_decisions",
  evaluate_and_publish: "submit_analysis",
  finish_run: "finish_run",
};

export function legacyEnvelopeToV2(value: unknown): ProviderEnvelopeV2 {
  const row = record(value, "legacy envelope");
  exactKeys(row, ["schema_version", "operation", "request_id", "run_id", "dry_run", "payload"]);
  if (row.schema_version !== 1) throw new Error("legacy schema_version must be 1");
  if (typeof row.operation !== "string" || !LEGACY_OPERATIONS[row.operation]) {
    throw new Error("legacy operation is unsupported");
  }
  return parseProviderEnvelopeV2({
    contract_version: 2,
    operation: LEGACY_OPERATIONS[row.operation],
    request_id: row.request_id,
    run_id: row.run_id,
    dry_run: row.dry_run,
    payload: row.payload,
  });
}
