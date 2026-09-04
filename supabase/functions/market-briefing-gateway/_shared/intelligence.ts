import { createHash } from "node:crypto";

export const INTELLIGENCE_PROVIDERS = [
  "gdelt",
  "alpha_vantage",
  "finnhub",
  "yahoo",
  "sec_edgar",
  "federal_register",
  "white_house",
  "doe",
  "dod",
  "eia",
  "fred",
  "bls",
  "bea",
  "social",
] as const;

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const HASH = /^[0-9a-f]{64}$/;
const PHASES = ["pre-market", "intraday", "post-market", "on-demand"] as const;
const RECEIPT_STATUSES = [
  "succeeded",
  "failed",
  "cache_hit",
  "quota_blocked",
  "configuration_missing",
] as const;
const DISPOSITIONS = [
  "accepted",
  "duplicate",
  "near_duplicate",
  "dropped",
] as const;

type JsonObject = Record<string, unknown>;

export interface StartIntelligencePayload {
  phase: typeof PHASES[number];
  market_date: string;
  policy_version: number;
  reservation_plan: { reservations: JsonObject[] };
}

export interface RecordIntelligencePayload {
  status: "completed" | "failed";
  coverage: JsonObject;
  receipts: JsonObject[];
  items: JsonObject[];
  events: JsonObject[];
  relationships: JsonObject[];
  rankings: JsonObject[];
  packet: JsonObject | null;
  error: JsonObject | null;
}

export interface IntelligenceStartReceipt {
  run_id: string;
  reservation_ids: string[];
  cache_entries: JsonObject[];
  duplicate: boolean;
}

export interface IntelligenceRecordReceipt {
  run_id: string;
  completion_id: string;
  status: "completed" | "failed";
  counts: Record<string, number>;
  packet_id: string | null;
  packet_hash: string | null;
  duplicate: boolean;
}

function objectValue(value: unknown, path: string): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${path} must be an object`);
  }
  return value as JsonObject;
}

function exactKeys(
  row: JsonObject,
  keys: readonly string[],
  path: string,
): void {
  const allowed = new Set(keys);
  for (const key of Object.keys(row)) {
    if (!allowed.has(key)) {
      throw new Error(`${path} has unexpected key: ${key}`);
    }
  }
  for (const key of keys) {
    if (!(key in row)) throw new Error(`${path} is missing key: ${key}`);
  }
}

function byteLength(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

function boundedObject(
  value: unknown,
  path: string,
  bytes: number,
): JsonObject {
  const row = objectValue(value, path);
  if (byteLength(row) > bytes) throw new Error(`${path} exceeds byte limit`);
  canonicalJson(row);
  return row;
}

function stringValue(
  value: unknown,
  path: string,
  max: number,
  empty = false,
): string {
  if (
    typeof value !== "string" || value.length > max ||
    (!empty && value.length === 0)
  ) {
    throw new Error(`${path} must be a bounded string`);
  }
  return value;
}

function nullableString(
  value: unknown,
  path: string,
  max: number,
): string | null {
  return value === null ? null : stringValue(value, path, max);
}

function uuidValue(value: unknown, path: string): string {
  const result = stringValue(value, path, 36);
  if (!UUID.test(result)) throw new Error(`${path} must be a UUID`);
  return result.toLowerCase();
}

function hashValue(value: unknown, path: string): string {
  const result = stringValue(value, path, 64);
  if (!HASH.test(result)) throw new Error(`${path} must be a SHA-256 hash`);
  return result;
}

function integer(
  value: unknown,
  path: string,
  min: number,
  max: number,
): number {
  if (
    typeof value !== "number" || !Number.isSafeInteger(value) || value < min ||
    value > max
  ) {
    throw new Error(`${path} must be a bounded integer`);
  }
  return value;
}

function timestamp(
  value: unknown,
  path: string,
  nullable = false,
): string | null {
  if (value === null && nullable) return null;
  const result = stringValue(value, path, 40);
  if (
    !/^\d{4}-\d{2}-\d{2}T/.test(result) || !Number.isFinite(Date.parse(result))
  ) {
    throw new Error(`${path} must be an ISO timestamp`);
  }
  return result;
}

function dateValue(value: unknown, path: string): string {
  const result = stringValue(value, path, 10);
  const parsed = new Date(`${result}T00:00:00.000Z`);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(result) ||
    parsed.toISOString().slice(0, 10) !== result
  ) {
    throw new Error(`${path} must be an ISO date`);
  }
  return result;
}

function arrayValue(value: unknown, path: string, max: number): unknown[] {
  if (!Array.isArray(value) || value.length > max) {
    throw new Error(`${path} must contain at most ${max} items`);
  }
  return value;
}

function enumValue<T extends string>(
  value: unknown,
  allowed: readonly T[],
  path: string,
): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    throw new Error(`${path} is invalid`);
  }
  return value as T;
}

function assertHash(actual: unknown, canonical: string, path: string): string {
  const supplied = hashValue(actual, path);
  const calculated = sha256Hex(canonical);
  if (supplied !== calculated) {
    throw new Error(`${path} does not match canonical content`);
  }
  return calculated;
}

function canonicalValue(value: unknown): unknown {
  if (
    value === null || typeof value === "string" || typeof value === "boolean"
  ) return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("canonical JSON contains a non-finite number");
    }
    return value;
  }
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (typeof value === "object") {
    const input = value as JsonObject;
    const result: JsonObject = {};
    const encoder = new TextEncoder();
    const compareUtf8 = (left: string, right: string): number => {
      const leftBytes = encoder.encode(left);
      const rightBytes = encoder.encode(right);
      for (
        let index = 0;
        index < Math.min(leftBytes.length, rightBytes.length);
        index += 1
      ) {
        if (leftBytes[index] !== rightBytes[index]) {
          return leftBytes[index] - rightBytes[index];
        }
      }
      return leftBytes.length - rightBytes.length;
    };
    for (const key of Object.keys(input).sort(compareUtf8)) {
      result[key] = canonicalValue(input[key]);
    }
    return result;
  }
  throw new Error("canonical JSON contains an unsupported value");
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalValue(value));
}

export function sha256Hex(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

export function parseStartIntelligencePayload(
  value: unknown,
): StartIntelligencePayload {
  const row = objectValue(value, "payload");
  exactKeys(
    row,
    ["phase", "market_date", "policy_version", "reservation_plan"],
    "payload",
  );
  const plan = objectValue(row.reservation_plan, "payload.reservation_plan");
  exactKeys(plan, ["reservations"], "payload.reservation_plan");
  if (byteLength(plan) > 32_768) {
    throw new Error("payload.reservation_plan exceeds byte limit");
  }
  const reservations = arrayValue(
    plan.reservations,
    "payload.reservation_plan.reservations",
    13,
  );
  const providers = new Set<string>();
  const parsedReservations = reservations.map((value, index) => {
    const path = `payload.reservation_plan.reservations[${index}]`;
    const reservation = objectValue(value, path);
    exactKeys(reservation, ["id", "provider", "requests", "cache_keys"], path);
    const provider = enumValue(
      reservation.provider,
      INTELLIGENCE_PROVIDERS,
      `${path}.provider`,
    );
    if (providers.has(provider)) {
      throw new Error(`${path}.provider is duplicated`);
    }
    providers.add(provider);
    const cacheKeys = arrayValue(
      reservation.cache_keys,
      `${path}.cache_keys`,
      20,
    ).map((key, keyIndex) =>
      stringValue(key, `${path}.cache_keys[${keyIndex}]`, 512)
    );
    return {
      id: uuidValue(reservation.id, `${path}.id`),
      provider,
      requests: integer(reservation.requests, `${path}.requests`, 1, 100),
      cache_keys: cacheKeys,
    };
  });
  return {
    phase: enumValue(row.phase, PHASES, "payload.phase"),
    market_date: dateValue(row.market_date, "payload.market_date"),
    policy_version: integer(
      row.policy_version,
      "payload.policy_version",
      1,
      2_147_483_647,
    ),
    reservation_plan: { reservations: parsedReservations },
  };
}

function parseReceipt(value: unknown, index: number): JsonObject {
  const path = `payload.receipts[${index}]`;
  const row = objectValue(value, path);
  const keys = [
    "id",
    "reservation_id",
    "status",
    "cache_key",
    "requested_window",
    "retrieved_at",
    "expires_at",
    "request_cost",
    "upstream_remaining",
    "returned_count",
    "accepted_count",
    "duplicate_count",
    "dropped_count",
    "error",
    "response_hash",
  ];
  exactKeys(row, keys, path);
  const status = enumValue(row.status, RECEIPT_STATUSES, `${path}.status`);
  const success = status === "succeeded" || status === "cache_hit";
  if (success && row.expires_at === null) {
    throw new Error(`${path}.expires_at is required`);
  }
  if (!success && row.expires_at !== null) {
    throw new Error(`${path}.expires_at must be null`);
  }
  if (success && row.response_hash === null) {
    throw new Error(`${path}.response_hash is required`);
  }
  return {
    id: uuidValue(row.id, `${path}.id`),
    reservation_id: uuidValue(row.reservation_id, `${path}.reservation_id`),
    status,
    cache_key: stringValue(row.cache_key, `${path}.cache_key`, 512),
    requested_window: boundedObject(
      row.requested_window,
      `${path}.requested_window`,
      8_192,
    ),
    retrieved_at: timestamp(row.retrieved_at, `${path}.retrieved_at`),
    expires_at: timestamp(row.expires_at, `${path}.expires_at`, true),
    request_cost: integer(row.request_cost, `${path}.request_cost`, 0, 100),
    upstream_remaining: row.upstream_remaining === null ? null : integer(
      row.upstream_remaining,
      `${path}.upstream_remaining`,
      0,
      1_000_000_000,
    ),
    returned_count: integer(
      row.returned_count,
      `${path}.returned_count`,
      0,
      10_000,
    ),
    accepted_count: integer(
      row.accepted_count,
      `${path}.accepted_count`,
      0,
      10_000,
    ),
    duplicate_count: integer(
      row.duplicate_count,
      `${path}.duplicate_count`,
      0,
      10_000,
    ),
    dropped_count: integer(
      row.dropped_count,
      `${path}.dropped_count`,
      0,
      10_000,
    ),
    error: row.error === null
      ? null
      : boundedObject(row.error, `${path}.error`, 4_096),
    response_hash: row.response_hash === null
      ? null
      : hashValue(row.response_hash, `${path}.response_hash`),
  };
}

function parseItem(value: unknown, index: number): JsonObject {
  const path = `payload.items[${index}]`;
  const row = objectValue(value, path);
  const keys = [
    "id",
    "run_item_id",
    "receipt_id",
    "upstream_item_id",
    "canonical_url",
    "published_at",
    "effective_at",
    "title",
    "normalized_text",
    "canonical_content",
    "content_hash",
    "metadata",
    "disposition",
    "drop_reason",
  ];
  exactKeys(row, keys, path);
  const disposition = enumValue(
    row.disposition,
    DISPOSITIONS,
    `${path}.disposition`,
  );
  const dropReason = nullableString(
    row.drop_reason,
    `${path}.drop_reason`,
    200,
  );
  if ((disposition === "accepted") !== (dropReason === null)) {
    throw new Error(`${path}.drop_reason does not match disposition`);
  }
  const canonicalContent = stringValue(
    row.canonical_content,
    `${path}.canonical_content`,
    4_096,
    true,
  );
  const canonicalUrl = nullableString(
    row.canonical_url,
    `${path}.canonical_url`,
    2_048,
  );
  if (canonicalUrl !== null) {
    let parsed: URL;
    try {
      parsed = new URL(canonicalUrl);
    } catch {
      throw new Error(`${path}.canonical_url must be HTTPS`);
    }
    if (
      parsed.protocol !== "https:" || parsed.username || parsed.password ||
      (parsed.port && parsed.port !== "443")
    ) {
      throw new Error(`${path}.canonical_url must be HTTPS`);
    }
  }
  return {
    id: uuidValue(row.id, `${path}.id`),
    run_item_id: uuidValue(row.run_item_id, `${path}.run_item_id`),
    receipt_id: uuidValue(row.receipt_id, `${path}.receipt_id`),
    upstream_item_id: nullableString(
      row.upstream_item_id,
      `${path}.upstream_item_id`,
      512,
    ),
    canonical_url: canonicalUrl,
    published_at: timestamp(row.published_at, `${path}.published_at`, true),
    effective_at: timestamp(row.effective_at, `${path}.effective_at`, true),
    title: stringValue(row.title, `${path}.title`, 500),
    normalized_text: stringValue(
      row.normalized_text,
      `${path}.normalized_text`,
      2_000,
      true,
    ),
    canonical_content: canonicalContent,
    content_hash: assertHash(
      row.content_hash,
      canonicalContent,
      `${path}.content_hash`,
    ),
    metadata: boundedObject(row.metadata, `${path}.metadata`, 8_192),
    disposition,
    drop_reason: dropReason,
  };
}

function parseSemanticRow(
  value: unknown,
  index: number,
  collection: "events" | "relationships" | "rankings",
  keys: readonly string[],
): JsonObject {
  const path = `payload.${collection}[${index}]`;
  const row = objectValue(value, path);
  exactKeys(row, keys, path);
  uuidValue(row.id, `${path}.id`);
  const canonicalRow = { ...row };
  delete canonicalRow.id;
  delete canonicalRow.content_hash;
  assertHash(
    row.content_hash,
    canonicalJson(canonicalRow),
    `${path}.content_hash`,
  );
  return row;
}

function decimalNumber(
  value: unknown,
  path: string,
  min: number,
  max: number,
): number {
  if (
    (typeof value !== "number" && typeof value !== "string") ||
    (typeof value === "string" && !/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value))
  ) {
    throw new Error(`${path} must be a canonical decimal`);
  }
  const result = Number(value);
  if (!Number.isFinite(result) || result < min || result > max) {
    throw new Error(`${path} is outside bounds`);
  }
  return result;
}

function uuidArray(
  value: unknown,
  path: string,
  min: number,
  max: number,
): string[] {
  const rows = arrayValue(value, path, max);
  if (rows.length < min) throw new Error(`${path} has too few items`);
  return rows.map((id, index) => uuidValue(id, `${path}[${index}]`));
}

function validateEvent(row: JsonObject, index: number): void {
  const path = `payload.events[${index}]`;
  stringValue(row.event_type, `${path}.event_type`, 80);
  stringValue(row.title, `${path}.title`, 500);
  stringValue(row.summary, `${path}.summary`, 4_000, true);
  timestamp(row.occurred_at, `${path}.occurred_at`, true);
  timestamp(row.effective_at, `${path}.effective_at`, true);
  decimalNumber(row.materiality, `${path}.materiality`, 0, 1);
  decimalNumber(row.confidence, `${path}.confidence`, 0, 1);
  uuidArray(row.evidence_item_ids, `${path}.evidence_item_ids`, 1, 96);
}

function validateRelationship(row: JsonObject, index: number): void {
  const path = `payload.relationships[${index}]`;
  uuidValue(row.event_id, `${path}.event_id`);
  enumValue(
    row.source_kind,
    ["event", "theme", "value_chain", "entity", "security"] as const,
    `${path}.source_kind`,
  );
  enumValue(
    row.target_kind,
    ["theme", "value_chain", "entity", "security", "etf"] as const,
    `${path}.target_kind`,
  );
  stringValue(row.source_key, `${path}.source_key`, 256);
  stringValue(row.target_key, `${path}.target_key`, 256);
  stringValue(row.relationship_type, `${path}.relationship_type`, 80);
  if (typeof row.hypothesis !== "boolean") {
    throw new Error(`${path}.hypothesis must be boolean`);
  }
  uuidArray(row.evidence_item_ids, `${path}.evidence_item_ids`, 1, 8);
}

function validateRanking(row: JsonObject, index: number): void {
  const path = `payload.rankings[${index}]`;
  if (row.event_id !== null) uuidValue(row.event_id, `${path}.event_id`);
  stringValue(row.candidate_key, `${path}.candidate_key`, 256);
  if (
    row.ticker !== null &&
    (typeof row.ticker !== "string" ||
      !/^[A-Z][A-Z0-9.-]{0,14}$/.test(row.ticker))
  ) {
    throw new Error(`${path}.ticker must be canonical`);
  }
  integer(row.rank, `${path}.rank`, 1, 100);
  boundedObject(row.component_scores, `${path}.component_scores`, 8_192);
  decimalNumber(row.total_score, `${path}.total_score`, -100_000, 100_000);
  if (typeof row.qualified !== "boolean") {
    throw new Error(`${path}.qualified must be boolean`);
  }
  const vetoes = arrayValue(row.veto_reasons, `${path}.veto_reasons`, 20);
  vetoes.forEach((reason, reasonIndex) =>
    stringValue(reason, `${path}.veto_reasons[${reasonIndex}]`, 200)
  );
  const exposures = uuidArray(
    row.exposure_item_ids,
    `${path}.exposure_item_ids`,
    row.qualified ? 1 : 0,
    8,
  );
  if (row.qualified && exposures.length === 0) {
    throw new Error(`${path}.exposure_item_ids is required`);
  }
}

function parsePacket(value: unknown, policyVersionHint?: number): JsonObject {
  const row = objectValue(value, "payload.packet");
  exactKeys(row, [
    "id",
    "candidate_count",
    "evidence_count",
    "packet",
    "packet_hash",
  ], "payload.packet");
  const packet = boundedObject(row.packet, "payload.packet.packet", 98_304);
  exactKeys(packet, [
    "candidates",
    "evidence",
    "coverage",
    "limitations",
    "policy_version",
  ], "payload.packet.packet");
  const candidates = arrayValue(
    packet.candidates,
    "payload.packet.packet.candidates",
    12,
  );
  const evidence = arrayValue(
    packet.evidence,
    "payload.packet.packet.evidence",
    96,
  );
  const candidateCount = integer(
    row.candidate_count,
    "payload.packet.candidate_count",
    0,
    12,
  );
  const evidenceCount = integer(
    row.evidence_count,
    "payload.packet.evidence_count",
    0,
    96,
  );
  if (
    candidateCount !== candidates.length || evidenceCount !== evidence.length
  ) {
    throw new Error("payload.packet counts do not match packet arrays");
  }
  integer(
    packet.policy_version,
    "payload.packet.packet.policy_version",
    1,
    2_147_483_647,
  );
  if (
    policyVersionHint !== undefined &&
    packet.policy_version !== policyVersionHint
  ) {
    throw new Error("payload.packet policy_version mismatch");
  }
  candidates.forEach((candidate, index) => {
    const candidateRow = objectValue(
      candidate,
      `payload.packet.packet.candidates[${index}]`,
    );
    arrayValue(
      candidateRow.evidence_ids,
      `payload.packet.packet.candidates[${index}].evidence_ids`,
      8,
    );
  });
  evidence.forEach((item, index) => {
    const evidenceRow = objectValue(
      item,
      `payload.packet.packet.evidence[${index}]`,
    );
    uuidValue(
      evidenceRow.item_id,
      `payload.packet.packet.evidence[${index}].item_id`,
    );
  });
  return {
    id: uuidValue(row.id, "payload.packet.id"),
    candidate_count: candidateCount,
    evidence_count: evidenceCount,
    packet,
    packet_hash: assertHash(
      row.packet_hash,
      canonicalJson(packet),
      "payload.packet.packet_hash",
    ),
  };
}

export function parseRecordIntelligencePayload(
  value: unknown,
): RecordIntelligencePayload {
  const row = objectValue(value, "payload");
  if (byteLength(row) > 1_048_576) {
    throw new Error("payload exceeds byte limit");
  }
  exactKeys(row, [
    "status",
    "coverage",
    "receipts",
    "items",
    "events",
    "relationships",
    "rankings",
    "packet",
    "error",
  ], "payload");
  const status = enumValue(
    row.status,
    ["completed", "failed"] as const,
    "payload.status",
  );
  const coverage = boundedObject(row.coverage, "payload.coverage", 32_768);
  const receipts = arrayValue(row.receipts, "payload.receipts", 100).map(
    parseReceipt,
  );
  const items = arrayValue(row.items, "payload.items", 500).map(parseItem);
  const eventKeys = [
    "id",
    "event_type",
    "title",
    "summary",
    "occurred_at",
    "effective_at",
    "materiality",
    "confidence",
    "evidence_item_ids",
    "content_hash",
  ];
  const relationshipKeys = [
    "id",
    "event_id",
    "source_kind",
    "source_key",
    "target_kind",
    "target_key",
    "relationship_type",
    "hypothesis",
    "evidence_item_ids",
    "content_hash",
  ];
  const rankingKeys = [
    "id",
    "event_id",
    "candidate_key",
    "ticker",
    "rank",
    "component_scores",
    "total_score",
    "qualified",
    "veto_reasons",
    "exposure_item_ids",
    "content_hash",
  ];
  const events = arrayValue(row.events, "payload.events", 100).map(
    (item, index) => {
      const parsed = parseSemanticRow(item, index, "events", eventKeys);
      validateEvent(parsed, index);
      return parsed;
    },
  );
  const relationships = arrayValue(
    row.relationships,
    "payload.relationships",
    500,
  ).map((item, index) => {
    const parsed = parseSemanticRow(
      item,
      index,
      "relationships",
      relationshipKeys,
    );
    validateRelationship(parsed, index);
    return parsed;
  });
  const rankings = arrayValue(row.rankings, "payload.rankings", 100).map(
    (item, index) => {
      const parsed = parseSemanticRow(item, index, "rankings", rankingKeys);
      validateRanking(parsed, index);
      return parsed;
    },
  );
  const packet = status === "completed" ? parsePacket(row.packet) : null;
  const error = status === "failed"
    ? boundedObject(row.error, "payload.error", 4_096)
    : null;
  if (
    (status === "completed" && row.error !== null) ||
    (status === "failed" && row.packet !== null)
  ) {
    throw new Error("payload terminal fields do not match status");
  }
  return {
    status,
    coverage,
    receipts,
    items,
    events,
    relationships,
    rankings,
    packet,
    error,
  };
}

export function summarizeIntelligencePayload(
  payload: RecordIntelligencePayload,
) {
  return {
    failure_count:
      payload.receipts.filter((receipt) =>
        !["succeeded", "cache_hit"].includes(String(receipt.status))
      ).length,
    duplicate_count:
      payload.items.filter((item) => item.disposition === "duplicate").length,
    near_duplicate_count:
      payload.items.filter((item) => item.disposition === "near_duplicate")
        .length,
    drop_count:
      payload.items.filter((item) => item.disposition === "dropped").length,
  };
}

export function parseIntelligenceStartReceipt(
  value: unknown,
): IntelligenceStartReceipt {
  const row = objectValue(value, "start intelligence receipt");
  exactKeys(
    row,
    ["run_id", "reservation_ids", "cache_entries", "duplicate"],
    "start intelligence receipt",
  );
  const cacheEntries = arrayValue(
    row.cache_entries,
    "start intelligence receipt.cache_entries",
    50,
  )
    .map((entry, index) =>
      boundedObject(
        entry,
        `start intelligence receipt.cache_entries[${index}]`,
        16_384,
      )
    );
  return {
    run_id: uuidValue(row.run_id, "start intelligence receipt.run_id"),
    reservation_ids: arrayValue(
      row.reservation_ids,
      "start intelligence receipt.reservation_ids",
      13,
    )
      .map((id, index) =>
        uuidValue(id, `start intelligence receipt.reservation_ids[${index}]`)
      ),
    cache_entries: cacheEntries,
    duplicate: typeof row.duplicate === "boolean" ? row.duplicate : (() => {
      throw new Error("start intelligence receipt.duplicate must be boolean");
    })(),
  };
}

export function parseIntelligenceRecordReceipt(
  value: unknown,
): IntelligenceRecordReceipt {
  const row = objectValue(value, "record intelligence receipt");
  exactKeys(row, [
    "run_id",
    "completion_id",
    "status",
    "counts",
    "packet_id",
    "packet_hash",
    "duplicate",
  ], "record intelligence receipt");
  const countRow = objectValue(
    row.counts,
    "record intelligence receipt.counts",
  );
  const countKeys = [
    "source_receipts",
    "source_items",
    "events",
    "relationships",
    "rankings",
    "packets",
  ];
  exactKeys(countRow, countKeys, "record intelligence receipt.counts");
  const counts = Object.fromEntries(countKeys.map((key) => [
    key,
    integer(
      countRow[key],
      `record intelligence receipt.counts.${key}`,
      0,
      10_000,
    ),
  ]));
  if (typeof row.duplicate !== "boolean") {
    throw new Error("record intelligence receipt.duplicate must be boolean");
  }
  return {
    run_id: uuidValue(row.run_id, "record intelligence receipt.run_id"),
    completion_id: uuidValue(
      row.completion_id,
      "record intelligence receipt.completion_id",
    ),
    status: enumValue(
      row.status,
      ["completed", "failed"] as const,
      "record intelligence receipt.status",
    ),
    counts,
    packet_id: row.packet_id === null
      ? null
      : uuidValue(row.packet_id, "record intelligence receipt.packet_id"),
    packet_hash: row.packet_hash === null
      ? null
      : hashValue(row.packet_hash, "record intelligence receipt.packet_hash"),
    duplicate: row.duplicate,
  };
}
