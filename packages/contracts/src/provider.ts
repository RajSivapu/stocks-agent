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
  "persistence_receipt",
  "publication_receipt",
  "write_counts",
  "server_quote",
  "quote_status",
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

export type AnalysisAction = "buy" | "add" | "hold" | "reduce" | "sell" | "watch" | "avoid";
export type AnalysisConfidence = "low" | "medium" | "high";
export type EvidenceDimensionStatus = "supported" | "mixed" | "missing" | "conflicting";
export type AnalysisDimension = "fundamentals" | "valuation" | "catalyst" | "technical" |
  "portfolio_fit" | "downside" | "bear_case" | "invalidation" | "decisive_factor";

export interface ProviderEvidenceReference {
  evidence_id: string;
  run_id: string;
  content_hash: string;
}

export interface ProviderAnalysisSubmissionV2 {
  phase: Phase;
  market_date: string;
  title: string;
  suggestion_only: true;
  provider: string;
  model: string;
  analyst: {
    completed: boolean;
    action: AnalysisAction;
    confidence: AnalysisConfidence;
    thesis: string;
  };
  checker: {
    completed: boolean;
    verdict: "approve" | "downgrade" | "veto";
    reason: string;
  };
  dimensions: Record<AnalysisDimension, {
    status: EvidenceDimensionStatus;
    summary: string;
    evidence_ids: string[];
  }>;
  evidence_packets: unknown[];
  evidence_refs: ProviderEvidenceReference[];
  prior_suggestion_ids: string[];
  candidates: unknown[];
}

const PHASES: readonly Phase[] = ["pre-market", "intraday", "post-market", "on-demand"];
const ACTIONS: readonly AnalysisAction[] = ["buy", "add", "hold", "reduce", "sell", "watch", "avoid"];
const DIMENSIONS: readonly AnalysisDimension[] = [
  "fundamentals", "valuation", "catalyst", "technical", "portfolio_fit", "downside",
  "bear_case", "invalidation", "decisive_factor",
];
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const HASH_RE = /^[0-9a-f]{64}$/;

function boundedString(value: unknown, field: string, max = 500): string {
  if (typeof value !== "string" || value.length < 1 || value.length > max) {
    throw new Error(`${field} must be a bounded string`);
  }
  return value;
}

function stringArray(value: unknown, field: string, max: number): string[] {
  if (!Array.isArray(value) || value.length > max) throw new Error(`${field} must be a bounded array`);
  return value.map((item, index) => boundedString(item, `${field}[${index}]`, 100));
}

function enumValue<T extends string>(value: unknown, values: readonly T[], field: string): T {
  if (typeof value !== "string" || !values.includes(value as T)) throw new Error(`${field} is invalid`);
  return value as T;
}

export function parseAnalysisSubmissionV2(value: unknown): ProviderAnalysisSubmissionV2 {
  rejectServerAuthority(value);
  const row = record(value, "analysis submission");
  exactKeys(row, [
    "phase", "market_date", "title", "suggestion_only", "provider", "model", "analyst",
    "checker", "dimensions", "evidence_packets", "evidence_refs", "prior_suggestion_ids", "candidates",
  ]);
  const analyst = record(row.analyst, "analyst");
  exactKeys(analyst, ["completed", "action", "confidence", "thesis"]);
  const checker = record(row.checker, "checker");
  exactKeys(checker, ["completed", "verdict", "reason"]);
  const dimensionsRow = record(row.dimensions, "dimensions");
  exactKeys(dimensionsRow, DIMENSIONS);
  const dimensions = {} as ProviderAnalysisSubmissionV2["dimensions"];
  for (const dimension of DIMENSIONS) {
    const item = record(dimensionsRow[dimension], `dimensions.${dimension}`);
    exactKeys(item, ["status", "summary", "evidence_ids"]);
    dimensions[dimension] = {
      status: enumValue(item.status, ["supported", "mixed", "missing", "conflicting"], `dimensions.${dimension}.status`),
      summary: boundedString(item.summary, `dimensions.${dimension}.summary`),
      evidence_ids: stringArray(item.evidence_ids, `dimensions.${dimension}.evidence_ids`, 20),
    };
  }
  if (!Array.isArray(row.evidence_refs) || row.evidence_refs.length > 100) {
    throw new Error("evidence_refs must be a bounded array");
  }
  if (!Array.isArray(row.evidence_packets) || row.evidence_packets.length < 1 || row.evidence_packets.length > 4) {
    throw new Error("evidence_packets must be a bounded array");
  }
  const evidenceRefs = row.evidence_refs.map((item, index) => {
    const reference = record(item, `evidence_refs[${index}]`);
    exactKeys(reference, ["evidence_id", "run_id", "content_hash"]);
    const contentHash = boundedString(reference.content_hash, `evidence_refs[${index}].content_hash`, 64);
    if (!HASH_RE.test(contentHash)) throw new Error("content_hash is invalid");
    return {
      evidence_id: boundedString(reference.evidence_id, `evidence_refs[${index}].evidence_id`, 100),
      run_id: uuid(reference.run_id, `evidence_refs[${index}].run_id`),
      content_hash: contentHash,
    };
  });
  if (new Set(evidenceRefs.map((item) => item.evidence_id)).size !== evidenceRefs.length) {
    throw new Error("duplicate evidence reference");
  }
  if (!Array.isArray(row.candidates) || row.candidates.length > 20) {
    throw new Error("candidates must be a bounded array");
  }
  const marketDate = boundedString(row.market_date, "market_date", 10);
  const parsedMarketDate = new Date(`${marketDate}T00:00:00Z`);
  if (!DATE_RE.test(marketDate) || Number.isNaN(parsedMarketDate.getTime()) ||
    parsedMarketDate.toISOString().slice(0, 10) !== marketDate) {
    throw new Error("market_date is invalid");
  }
  if (row.suggestion_only !== true) throw new Error("suggestion_only must be true");
  if (typeof analyst.completed !== "boolean" || typeof checker.completed !== "boolean") {
    throw new Error("review completion flags must be boolean");
  }
  return {
    phase: enumValue(row.phase, PHASES, "phase"),
    market_date: marketDate,
    title: boundedString(row.title, "title", 200),
    suggestion_only: true,
    provider: boundedString(row.provider, "provider", 50),
    model: boundedString(row.model, "model", 100),
    analyst: {
      completed: analyst.completed,
      action: enumValue(analyst.action, ACTIONS, "analyst.action"),
      confidence: enumValue(analyst.confidence, ["low", "medium", "high"], "analyst.confidence"),
      thesis: boundedString(analyst.thesis, "analyst.thesis", 2_000),
    },
    checker: {
      completed: checker.completed,
      verdict: enumValue(checker.verdict, ["approve", "downgrade", "veto"], "checker.verdict"),
      reason: boundedString(checker.reason, "checker.reason", 2_000),
    },
    dimensions,
    evidence_packets: structuredClone(row.evidence_packets),
    evidence_refs: evidenceRefs,
    prior_suggestion_ids: stringArray(row.prior_suggestion_ids, "prior_suggestion_ids", 20),
    candidates: structuredClone(row.candidates),
  };
}
