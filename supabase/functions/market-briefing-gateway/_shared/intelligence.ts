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
const DISCOVERY_STATUSES = [
  "qualified",
  "no_event",
  "insufficient_coverage",
] as const;
const PROVIDER_HOSTS: Readonly<Record<string, readonly string[]>> = {
  gdelt: ["api.gdeltproject.org"],
  alpha_vantage: ["www.alphavantage.co"],
  finnhub: ["finnhub.io"],
  yahoo: ["query1.finance.yahoo.com"],
  sec_edgar: ["www.sec.gov", "data.sec.gov"],
  federal_register: ["www.federalregister.gov"],
  white_house: ["www.whitehouse.gov"],
  doe: ["www.energy.gov"],
  dod: ["www.defense.gov"],
  eia: ["api.eia.gov", "www.eia.gov"],
  fred: ["api.stlouisfed.org", "fred.stlouisfed.org"],
  bls: ["api.bls.gov", "www.bls.gov"],
  bea: ["apps.bea.gov", "www.bea.gov"],
  social: ["www.reddit.com", "oauth.reddit.com"],
};

type JsonObject = Record<string, unknown>;

export interface StartIntelligencePayload {
  phase: typeof PHASES[number];
  market_date: string;
  policy_version: number;
  reservation_plan: { reservations: JsonObject[] };
  request_window: JsonObject;
}

export interface CheckpointIntelligencePayload {
  cache_key: string;
  receipt: JsonObject;
  items: JsonObject[];
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
  request_window: JsonObject;
  duplicate: boolean;
  reservation_usage?: Record<string, number>;
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

export type DiscoveryQueryKind =
  | "feed"
  | "theme_search"
  | "issuer_submissions"
  | "filing_document"
  | "series"
  | "screener"
  | "quote"
  | "universe";

export interface DiscoveryStageTask {
  id: string;
  stage: "reference" | "signals" | "resolve" | "enrich" | "screen" | "quote";
  provider: typeof INTELLIGENCE_PROVIDERS[number];
  capability_id: string;
  query_kind: DiscoveryQueryKind;
  query_hash: string;
  dependency_ids: string[];
  requested_window: Record<string, string>;
  state:
    | "planned"
    | "attempting"
    | "succeeded"
    | "failed"
    | "deferred"
    | "uncertain";
  attempt_count: number;
  request_budget: number;
  result: JsonObject;
}

export interface DiscoveryReferencePayload {
  manifest: JsonObject;
  security_revisions: JsonObject[];
}

export interface ReferenceBeginPayload {
  manifest: JsonObject;
  capability_id: string;
  chunk_count: number;
  security_count: number;
  root_hash: string;
  predecessor_manifest_id: string | null;
}

export interface ReferenceChunkPayload {
  manifest_id: string;
  chunk_index: number;
  chunk_count: number;
  entries: JsonObject[];
  chunk_hash: string;
}

export interface ReferenceFinalizePayload {
  manifest_id: string;
  root_hash: string;
}

export interface ReferencePinPayload {
  capability_id: string;
  manifest_id: string | null;
  reference_status: "healthy" | "reference_stale" | "reference_unavailable";
  reference_as_of: string;
}

export interface ReferenceReadPayload {
  capability_id: string;
  after_security_id: string | null;
  limit: number;
}

export interface ReferencePage {
  binding: JsonObject;
  manifest: JsonObject | null;
  securities: JsonObject[];
  next_after_security_id: string | null;
  complete: boolean;
}

export interface DiscoveryStageCheckpointPayload {
  task: DiscoveryStageTask;
  exposure_facts: JsonObject[];
  theme_episode_revisions: JsonObject[];
  research_nominations: JsonObject[];
}

export interface DiscoveryContext {
  manifests: JsonObject[];
  security_revisions: JsonObject[];
  tasks: DiscoveryStageTask[];
  theme_episodes: JsonObject[];
  exposure_facts: JsonObject[];
  research_nominations: JsonObject[];
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

function percentEncodePath(value: string): string {
  const safe = new Set(
    "/%:@!$&'()*+,;=-._~".split(""),
  );
  let encoded = "";
  for (const character of value) {
    if (/^[A-Za-z0-9]$/.test(character) || safe.has(character)) {
      encoded += character;
    } else {
      encoded += encodeURIComponent(character);
    }
  }
  return encoded;
}

function percentEncodeQuery(value: string): string {
  return encodeURIComponent(value)
    .replace(
      /[!'()*]/g,
      (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`,
    )
    .replace(/%20/g, "+");
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function canonicalizeUrl(value: string, path: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${path} must be HTTPS`);
  }
  if (
    parsed.protocol.toLowerCase() !== "https:" || !parsed.hostname ||
    parsed.username || parsed.password || (parsed.port && parsed.port !== "443")
  ) {
    throw new Error(`${path} must be HTTPS`);
  }
  let decodedPath: string;
  try {
    decodedPath = decodeURIComponent(parsed.pathname || "/");
  } catch {
    throw new Error(`${path} must contain valid URL encoding`);
  }
  const queryPairs = [...parsed.searchParams.entries()].sort((left, right) =>
    compareText(left[0], right[0]) || compareText(left[1], right[1])
  );
  const query = queryPairs.map(([key, item]) =>
    `${percentEncodeQuery(key)}=${percentEncodeQuery(item)}`
  ).join("&");
  const canonical = `https://${
    parsed.hostname.toLowerCase().replace(/\.$/, "")
  }${percentEncodePath(decodedPath)}${query ? `?${query}` : ""}`;
  if (canonical.length > 2_048) throw new Error(`${path} exceeds URL limit`);
  return canonical;
}

function providerRequestUrl(
  value: unknown,
  provider: unknown,
  path: string,
): string {
  const url = canonicalizeUrl(stringValue(value, path, 2_048), path);
  const parsed = new URL(url);
  if (
    /(?:api[_-]?key|token|secret|password)=/i.test(
      `${parsed.pathname}?${parsed.search}`,
    )
  ) {
    throw new Error(`${path} contains a secret-bearing query or path`);
  }
  const host = parsed.hostname;
  if (!PROVIDER_HOSTS[String(provider)]?.includes(host)) {
    throw new Error(`${path} host is not approved for provider`);
  }
  return url;
}

function identifierArray(
  value: unknown,
  path: string,
  maxLength: number,
): string[] {
  const rows = arrayValue(value, path, 32);
  const values = rows.map((entry, index) =>
    stringValue(entry, `${path}[${index}]`, maxLength)
  );
  if (new Set(values).size !== values.length) {
    throw new Error(`${path} is duplicated`);
  }
  return values;
}

const DISCOVERY_FORBIDDEN_FIELDS = new Set([
  "price",
  "valuation",
  "portfoliooverlap",
  "action",
  "authority",
  "execution",
  "executionallowed",
  "broker",
  "brokerage",
  "order",
  "orderid",
  "orderdetails",
]);

function discoveryFieldSemantic(key: string): string {
  return key.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function rejectDiscoveryAuthority(value: unknown, path: string): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) =>
      rejectDiscoveryAuthority(item, `${path}[${index}]`)
    );
  } else if (typeof value === "object" && value !== null) {
    for (const [key, child] of Object.entries(value as JsonObject)) {
      if (DISCOVERY_FORBIDDEN_FIELDS.has(discoveryFieldSemantic(key))) {
        throw new Error(`${path} has forbidden field: ${key}`);
      }
      rejectDiscoveryAuthority(child, `${path}.${key}`);
    }
  }
}

function discoveryStringArray(
  value: unknown,
  path: string,
  maxItems: number,
  maxLength: number,
  requireOne = false,
): string[] {
  const rows = arrayValue(value, path, maxItems);
  if (requireOne && rows.length === 0) {
    throw new Error(`${path} must not be empty`);
  }
  const result = rows.map((item, index) =>
    stringValue(item, `${path}[${index}]`, maxLength)
  );
  if (new Set(result).size !== result.length) {
    throw new Error(`${path} is duplicated`);
  }
  return result;
}

function nullableUuid(value: unknown, path: string): string | null {
  return value === null ? null : uuidValue(value, path);
}

function rejectDuplicateDiscoveryRowIds(
  rows: JsonObject[],
  path: string,
): void {
  const ids = rows.map((row) => row.id);
  if (new Set(ids).size !== ids.length) {
    throw new Error(`${path} has duplicate id`);
  }
}

export function parseDiscoveryStageTask(value: unknown): DiscoveryStageTask {
  const row = objectValue(value, "discovery task");
  exactKeys(row, [
    "id",
    "stage",
    "provider",
    "capability_id",
    "query_kind",
    "query_hash",
    "dependency_ids",
    "requested_window",
    "state",
    "attempt_count",
    "request_budget",
    "result",
  ], "discovery task");
  const capabilityId = stringValue(
    row.capability_id,
    "discovery task.capability_id",
    80,
  );
  if (!/^[a-z][a-z0-9_]{2,79}$/.test(capabilityId)) {
    throw new Error("discovery task.capability_id is invalid");
  }
  const window = objectValue(
    row.requested_window,
    "discovery task.requested_window",
  );
  exactKeys(window, ["start", "end"], "discovery task.requested_window");
  const start = timestamp(
    window.start,
    "discovery task.requested_window.start",
  ) as string;
  const end = timestamp(
    window.end,
    "discovery task.requested_window.end",
  ) as string;
  if (Date.parse(end) <= Date.parse(start)) {
    throw new Error("discovery task.requested_window is invalid");
  }
  const result = boundedObject(row.result, "discovery task.result", 65_536);
  rejectDiscoveryAuthority(result, "discovery task.result");
  const dependencyIds = arrayValue(
    row.dependency_ids,
    "discovery task.dependency_ids",
    32,
  ).map(
    (item, index) => uuidValue(item, `discovery task.dependency_ids[${index}]`),
  );
  if (new Set(dependencyIds).size !== dependencyIds.length) {
    throw new Error("discovery task.dependency_ids is duplicated");
  }
  return {
    id: uuidValue(row.id, "discovery task.id"),
    stage: enumValue(
      row.stage,
      ["reference", "signals", "resolve", "enrich", "screen", "quote"] as const,
      "discovery task.stage",
    ),
    provider: enumValue(
      row.provider,
      INTELLIGENCE_PROVIDERS,
      "discovery task.provider",
    ),
    capability_id: capabilityId,
    query_kind: enumValue(
      row.query_kind,
      [
        "feed",
        "theme_search",
        "issuer_submissions",
        "filing_document",
        "series",
        "screener",
        "quote",
        "universe",
      ] as const,
      "discovery task.query_kind",
    ),
    query_hash: hashValue(row.query_hash, "discovery task.query_hash"),
    dependency_ids: dependencyIds,
    requested_window: { start, end },
    state: enumValue(
      row.state,
      [
        "planned",
        "attempting",
        "succeeded",
        "failed",
        "deferred",
        "uncertain",
      ] as const,
      "discovery task.state",
    ),
    attempt_count: integer(
      row.attempt_count,
      "discovery task.attempt_count",
      0,
      10,
    ),
    request_budget: integer(
      row.request_budget,
      "discovery task.request_budget",
      0,
      100,
    ),
    result,
  };
}

function parseDiscoveryManifest(value: unknown): JsonObject {
  const row = objectValue(value, "discovery reference.manifest");
  exactKeys(row, [
    "id",
    "reference_version",
    "revision",
    "capability_version",
    "taxonomy_version",
    "source_hash",
    "valid_from",
    "valid_to",
    "manifest",
    "content_hash",
  ], "discovery reference.manifest");
  const manifest = boundedObject(
    row.manifest,
    "discovery reference.manifest.manifest",
    65_536,
  );
  rejectDiscoveryAuthority(manifest, "discovery reference.manifest.manifest");
  return {
    id: uuidValue(row.id, "discovery reference.manifest.id"),
    reference_version: stringValue(
      row.reference_version,
      "discovery reference.manifest.reference_version",
      128,
    ),
    revision: integer(
      row.revision,
      "discovery reference.manifest.revision",
      1,
      10_000,
    ),
    capability_version: integer(
      row.capability_version,
      "discovery reference.manifest.capability_version",
      1,
      10_000,
    ),
    taxonomy_version: integer(
      row.taxonomy_version,
      "discovery reference.manifest.taxonomy_version",
      1,
      10_000,
    ),
    source_hash: hashValue(
      row.source_hash,
      "discovery reference.manifest.source_hash",
    ),
    valid_from: timestamp(
      row.valid_from,
      "discovery reference.manifest.valid_from",
    ) as string,
    valid_to: timestamp(
      row.valid_to,
      "discovery reference.manifest.valid_to",
      true,
    ),
    manifest,
    content_hash: hashValue(
      row.content_hash,
      "discovery reference.manifest.content_hash",
    ),
  };
}

function parseSecurityRevision(value: unknown, index: number): JsonObject {
  const path = `discovery reference.security_revisions[${index}]`;
  const row = objectValue(value, path);
  exactKeys(row, [
    "id",
    "manifest_id",
    "revision",
    "security_id",
    "entity_id",
    "ticker",
    "exchange",
    "instrument_type",
    "eligible",
    "exclusion_reasons",
    "aliases",
    "source_ids",
    "valid_from",
    "valid_to",
    "content_hash",
  ], path);
  if (typeof row.eligible !== "boolean") {
    throw new Error(`${path}.eligible must be boolean`);
  }
  const result = {
    id: uuidValue(row.id, `${path}.id`),
    manifest_id: uuidValue(row.manifest_id, `${path}.manifest_id`),
    revision: integer(row.revision, `${path}.revision`, 1, 10_000),
    security_id: stringValue(row.security_id, `${path}.security_id`, 128),
    entity_id: stringValue(row.entity_id, `${path}.entity_id`, 128),
    ticker: stringValue(row.ticker, `${path}.ticker`, 15),
    exchange: nullableString(row.exchange, `${path}.exchange`, 32),
    instrument_type: enumValue(
      row.instrument_type,
      [
        "COMMON_STOCK",
        "ADR",
        "ETF",
        "PREFERRED",
        "WARRANT",
        "OTC_COMMON",
        "OTHER",
      ] as const,
      `${path}.instrument_type`,
    ),
    eligible: row.eligible,
    exclusion_reasons: discoveryStringArray(
      row.exclusion_reasons,
      `${path}.exclusion_reasons`,
      16,
      128,
    ),
    aliases: discoveryStringArray(row.aliases, `${path}.aliases`, 32, 32),
    source_ids: discoveryStringArray(
      row.source_ids,
      `${path}.source_ids`,
      16,
      256,
      true,
    ),
    valid_from: timestamp(row.valid_from, `${path}.valid_from`) as string,
    valid_to: timestamp(row.valid_to, `${path}.valid_to`, true),
    content_hash: hashValue(row.content_hash, `${path}.content_hash`),
  };
  rejectDiscoveryAuthority(result, path);
  return result;
}

export function parseDiscoveryReferencePayload(
  value: unknown,
): DiscoveryReferencePayload {
  const row = objectValue(value, "discovery reference");
  exactKeys(row, ["manifest", "security_revisions"], "discovery reference");
  if (byteLength(row) > 1_048_576) {
    throw new Error("discovery reference exceeds byte limit");
  }
  const manifest = parseDiscoveryManifest(row.manifest);
  const securityRevisions = arrayValue(
    row.security_revisions,
    "discovery reference.security_revisions",
    15_000,
  )
    .map(parseSecurityRevision);
  rejectDuplicateDiscoveryRowIds(
    securityRevisions,
    "discovery reference.security_revisions",
  );
  if (securityRevisions.some((item) => item.manifest_id !== manifest.id)) {
    throw new Error("discovery reference manifest identity mismatch");
  }
  return { manifest, security_revisions: securityRevisions };
}

function referenceCapability(value: unknown, path: string): string {
  const capability = stringValue(value, path, 80);
  if (!/^[a-z][a-z0-9_]{2,79}$/.test(capability)) {
    throw new Error(`${path} is invalid`);
  }
  return capability;
}

function boundedReferenceCall(value: unknown, path: string): JsonObject {
  const row = objectValue(value, path);
  if (byteLength(row) > 196_608) throw new Error(`${path} exceeds byte limit`);
  return row;
}

export function parseReferenceBeginPayload(
  value: unknown,
): ReferenceBeginPayload {
  const row = boundedReferenceCall(value, "reference begin");
  exactKeys(row, [
    "manifest",
    "capability_id",
    "chunk_count",
    "security_count",
    "root_hash",
    "predecessor_manifest_id",
  ], "reference begin");
  const manifest = parseDiscoveryManifest(row.manifest);
  const metadata = objectValue(
    manifest.manifest,
    "reference begin.manifest.manifest",
  );
  if (
    metadata.coverage_status !== "scope_not_guaranteed" ||
    metadata.reference_status !== "healthy"
  ) throw new Error("reference begin manifest status is invalid");
  const securityCount = integer(
    row.security_count,
    "reference begin.security_count",
    1,
    15_000,
  );
  if (metadata.security_count !== securityCount) {
    throw new Error("reference begin security count mismatch");
  }
  return {
    manifest,
    capability_id: referenceCapability(
      row.capability_id,
      "reference begin.capability_id",
    ),
    chunk_count: integer(
      row.chunk_count,
      "reference begin.chunk_count",
      1,
      512,
    ),
    security_count: securityCount,
    root_hash: hashValue(row.root_hash, "reference begin.root_hash"),
    predecessor_manifest_id: row.predecessor_manifest_id === null
      ? null
      : uuidValue(
        row.predecessor_manifest_id,
        "reference begin.predecessor_manifest_id",
      ),
  };
}

export function parseReferenceChunkPayload(
  value: unknown,
): ReferenceChunkPayload {
  const row = boundedReferenceCall(value, "reference chunk");
  exactKeys(row, [
    "manifest_id",
    "chunk_index",
    "chunk_count",
    "entries",
    "chunk_hash",
  ], "reference chunk");
  const entries = arrayValue(row.entries, "reference chunk.entries", 200)
    .map(parseSecurityRevision);
  if (entries.length === 0) {
    throw new Error("reference chunk.entries must not be empty");
  }
  rejectDuplicateDiscoveryRowIds(entries, "reference chunk.entries");
  const manifestId = uuidValue(row.manifest_id, "reference chunk.manifest_id");
  if (entries.some((entry) => entry.manifest_id !== manifestId)) {
    throw new Error("reference chunk manifest identity mismatch");
  }
  return {
    manifest_id: manifestId,
    chunk_index: integer(
      row.chunk_index,
      "reference chunk.chunk_index",
      0,
      511,
    ),
    chunk_count: integer(
      row.chunk_count,
      "reference chunk.chunk_count",
      1,
      512,
    ),
    entries,
    chunk_hash: hashValue(row.chunk_hash, "reference chunk.chunk_hash"),
  };
}

export function parseReferenceFinalizePayload(
  value: unknown,
): ReferenceFinalizePayload {
  const row = boundedReferenceCall(value, "reference finalize");
  exactKeys(row, ["manifest_id", "root_hash"], "reference finalize");
  return {
    manifest_id: uuidValue(row.manifest_id, "reference finalize.manifest_id"),
    root_hash: hashValue(row.root_hash, "reference finalize.root_hash"),
  };
}

export function parseReferencePinPayload(value: unknown): ReferencePinPayload {
  const row = boundedReferenceCall(value, "reference pin");
  exactKeys(row, [
    "capability_id",
    "manifest_id",
    "reference_status",
    "reference_as_of",
  ], "reference pin");
  const status = enumValue(
    row.reference_status,
    [
      "healthy",
      "reference_stale",
      "reference_unavailable",
    ] as const,
    "reference pin.reference_status",
  );
  const manifestId = row.manifest_id === null
    ? null
    : uuidValue(row.manifest_id, "reference pin.manifest_id");
  if (
    (status === "healthy" && manifestId === null) ||
    (status === "reference_unavailable" && manifestId !== null)
  ) {
    throw new Error("reference pin status does not match manifest");
  }
  return {
    capability_id: referenceCapability(
      row.capability_id,
      "reference pin.capability_id",
    ),
    manifest_id: manifestId,
    reference_status: status,
    reference_as_of: timestamp(
      row.reference_as_of,
      "reference pin.reference_as_of",
    ) as string,
  };
}

export function parseReferenceReadPayload(
  value: unknown,
): ReferenceReadPayload {
  const row = boundedReferenceCall(value, "reference read");
  exactKeys(
    row,
    ["capability_id", "after_security_id", "limit"],
    "reference read",
  );
  return {
    capability_id: referenceCapability(
      row.capability_id,
      "reference read.capability_id",
    ),
    after_security_id: nullableString(
      row.after_security_id,
      "reference read.after_security_id",
      128,
    ),
    limit: integer(row.limit, "reference read.limit", 1, 500),
  };
}

export function parseReferencePage(value: unknown): ReferencePage {
  const row = boundedReferenceCall(value, "reference page");
  exactKeys(row, [
    "binding",
    "manifest",
    "securities",
    "next_after_security_id",
    "complete",
  ], "reference page");
  const binding = boundedObject(row.binding, "reference page.binding", 2_048);
  exactKeys(binding, [
    "manifest_id",
    "reference_status",
    "source_retrieved_at",
    "reference_age_seconds",
  ], "reference page.binding");
  const status = enumValue(
    binding.reference_status,
    [
      "healthy",
      "reference_stale",
      "reference_unavailable",
    ] as const,
    "reference page.binding.reference_status",
  );
  const manifestId = binding.manifest_id === null
    ? null
    : uuidValue(binding.manifest_id, "reference page.binding.manifest_id");
  const sourceRetrievedAt = timestamp(
    binding.source_retrieved_at,
    "reference page.binding.source_retrieved_at",
    true,
  );
  const age = binding.reference_age_seconds === null ? null : integer(
    binding.reference_age_seconds,
    "reference page.binding.reference_age_seconds",
    0,
    Number.MAX_SAFE_INTEGER,
  );
  if ((status === "reference_unavailable") !== (manifestId === null)) {
    throw new Error("reference page binding is inconsistent");
  }
  if (
    status === "reference_unavailable"
      ? sourceRetrievedAt !== null || age !== null
      : sourceRetrievedAt === null || age === null
  ) {
    throw new Error("reference page binding is inconsistent");
  }
  const manifest = row.manifest === null
    ? null
    : parseDiscoveryManifest(row.manifest);
  const securities = arrayValue(
    row.securities,
    "reference page.securities",
    500,
  )
    .map(parseSecurityRevision);
  rejectDuplicateDiscoveryRowIds(securities, "reference page.securities");
  if (
    (manifest === null) !== (manifestId === null) ||
    (manifest !== null && manifest.id !== manifestId) ||
    securities.some((entry) => entry.manifest_id !== manifestId)
  ) {
    throw new Error("reference page manifest identity mismatch");
  }
  if (typeof row.complete !== "boolean") {
    throw new Error("reference page.complete must be boolean");
  }
  const next = nullableString(
    row.next_after_security_id,
    "reference page.next_after_security_id",
    128,
  );
  if (
    (row.complete && next !== null) ||
    (!row.complete && (securities.length === 0 ||
      next !== securities[securities.length - 1].security_id)) ||
    (status === "reference_unavailable" &&
      (!row.complete || securities.length !== 0))
  ) {
    throw new Error("reference page pagination is inconsistent");
  }
  return {
    binding: {
      manifest_id: manifestId,
      reference_status: status,
      source_retrieved_at: sourceRetrievedAt,
      reference_age_seconds: age,
    },
    manifest,
    securities,
    next_after_security_id: next,
    complete: row.complete,
  };
}

function parseDiscoveryResultRow(
  value: unknown,
  path: string,
  keys: readonly string[],
): JsonObject {
  const row = objectValue(value, path);
  exactKeys(row, keys, path);
  rejectDiscoveryAuthority(row, path);
  return row;
}

export function parseDiscoveryStageCheckpointPayload(
  value: unknown,
): DiscoveryStageCheckpointPayload {
  const row = objectValue(value, "discovery checkpoint");
  exactKeys(row, [
    "task",
    "exposure_facts",
    "theme_episode_revisions",
    "research_nominations",
  ], "discovery checkpoint");
  if (byteLength(row) > 262_144) {
    throw new Error("discovery checkpoint exceeds byte limit");
  }
  const task = parseDiscoveryStageTask(row.task);
  const exposureFacts = arrayValue(
    row.exposure_facts,
    "discovery checkpoint.exposure_facts",
    100,
  ).map((item, index) => {
    const path = `discovery checkpoint.exposure_facts[${index}]`;
    const parsed = parseDiscoveryResultRow(item, path, [
      "id",
      "security_revision_id",
      "theme_episode_revision_id",
      "exposure_kind",
      "fact",
      "source_ids",
      "valid_from",
      "valid_to",
      "content_hash",
    ]);
    return {
      ...parsed,
      id: uuidValue(parsed.id, `${path}.id`),
      security_revision_id: uuidValue(
        parsed.security_revision_id,
        `${path}.security_revision_id`,
      ),
      theme_episode_revision_id: nullableUuid(
        parsed.theme_episode_revision_id,
        `${path}.theme_episode_revision_id`,
      ),
      exposure_kind: enumValue(
        parsed.exposure_kind,
        [
          "filing",
          "contract",
          "backlog",
          "revenue",
          "capacity",
          "official_fund",
          "supply_chain",
          "customer",
          "segment",
        ] as const,
        `${path}.exposure_kind`,
      ),
      fact: boundedObject(parsed.fact, `${path}.fact`, 32_768),
      source_ids: discoveryStringArray(
        parsed.source_ids,
        `${path}.source_ids`,
        64,
        256,
        true,
      ),
      valid_from: timestamp(parsed.valid_from, `${path}.valid_from`),
      valid_to: timestamp(parsed.valid_to, `${path}.valid_to`, true),
      content_hash: hashValue(parsed.content_hash, `${path}.content_hash`),
    };
  });
  const episodes = arrayValue(
    row.theme_episode_revisions,
    "discovery checkpoint.theme_episode_revisions",
    50,
  ).map((item, index) => {
    const path = `discovery checkpoint.theme_episode_revisions[${index}]`;
    const parsed = parseDiscoveryResultRow(item, path, [
      "id",
      "theme_id",
      "revision",
      "episode",
      "source_ids",
      "valid_from",
      "valid_to",
      "content_hash",
    ]);
    return {
      ...parsed,
      id: uuidValue(parsed.id, `${path}.id`),
      theme_id: stringValue(parsed.theme_id, `${path}.theme_id`, 80),
      revision: integer(parsed.revision, `${path}.revision`, 1, 10_000),
      episode: boundedObject(parsed.episode, `${path}.episode`, 32_768),
      source_ids: discoveryStringArray(
        parsed.source_ids,
        `${path}.source_ids`,
        64,
        256,
        true,
      ),
      valid_from: timestamp(parsed.valid_from, `${path}.valid_from`),
      valid_to: timestamp(parsed.valid_to, `${path}.valid_to`, true),
      content_hash: hashValue(parsed.content_hash, `${path}.content_hash`),
    };
  });
  const nominations = arrayValue(
    row.research_nominations,
    "discovery checkpoint.research_nominations",
    50,
  ).map((item, index) => {
    const path = `discovery checkpoint.research_nominations[${index}]`;
    const parsed = parseDiscoveryResultRow(item, path, [
      "id",
      "security_revision_id",
      "theme_episode_revision_id",
      "exposure_fact_ids",
      "state",
      "rationale",
    ]);
    const exposureFactIds = arrayValue(
      parsed.exposure_fact_ids,
      `${path}.exposure_fact_ids`,
      32,
    ).map((id, position) =>
      uuidValue(id, `${path}.exposure_fact_ids[${position}]`)
    );
    if (new Set(exposureFactIds).size !== exposureFactIds.length) {
      throw new Error(`${path}.exposure_fact_ids is duplicated`);
    }
    return {
      ...parsed,
      id: uuidValue(parsed.id, `${path}.id`),
      security_revision_id: uuidValue(
        parsed.security_revision_id,
        `${path}.security_revision_id`,
      ),
      theme_episode_revision_id: nullableUuid(
        parsed.theme_episode_revision_id,
        `${path}.theme_episode_revision_id`,
      ),
      exposure_fact_ids: exposureFactIds,
      state: enumValue(parsed.state, ["nominated"] as const, `${path}.state`),
      rationale: boundedObject(parsed.rationale, `${path}.rationale`, 16_384),
    };
  });
  rejectDuplicateDiscoveryRowIds(
    exposureFacts,
    "discovery checkpoint.exposure_facts",
  );
  rejectDuplicateDiscoveryRowIds(
    episodes,
    "discovery checkpoint.theme_episode_revisions",
  );
  rejectDuplicateDiscoveryRowIds(
    nominations,
    "discovery checkpoint.research_nominations",
  );
  if (
    (episodes.length > 0 && task.stage !== "signals") ||
    (exposureFacts.length > 0 && task.stage !== "enrich") ||
    (nominations.length > 0 && task.stage !== "screen")
  ) {
    throw new Error("discovery result does not match stage");
  }
  if (
    task.state !== "succeeded" &&
    (episodes.length > 0 || exposureFacts.length > 0 || nominations.length > 0)
  ) {
    throw new Error("non-success discovery checkpoint has result rows");
  }
  return {
    task,
    exposure_facts: exposureFacts,
    theme_episode_revisions: episodes,
    research_nominations: nominations,
  };
}

export function parseDiscoveryContextRequest(
  value: unknown,
): { limit: number } {
  const row = objectValue(value, "discovery context request");
  exactKeys(row, ["limit"], "discovery context request");
  return {
    limit: integer(row.limit, "discovery context request.limit", 1, 100),
  };
}

export function parseDiscoveryContext(value: unknown): DiscoveryContext {
  const row = objectValue(value, "discovery context");
  exactKeys(row, [
    "manifests",
    "security_revisions",
    "tasks",
    "theme_episodes",
    "exposure_facts",
    "research_nominations",
  ], "discovery context");
  if (byteLength(row) > 1_048_576) {
    throw new Error("discovery context exceeds byte limit");
  }
  const opaque = (key: string, keys: readonly string[]) =>
    arrayValue(row[key], `discovery context.${key}`, 100).map((item, index) => {
      const parsed = boundedObject(
        item,
        `discovery context.${key}[${index}]`,
        65_536,
      );
      exactKeys(parsed, keys, `discovery context.${key}[${index}]`);
      rejectDiscoveryAuthority(parsed, `discovery context.${key}[${index}]`);
      return parsed;
    });
  return {
    manifests: opaque("manifests", [
      "id",
      "reference_version",
      "revision",
      "capability_version",
      "taxonomy_version",
      "source_hash",
      "valid_from",
      "valid_to",
      "manifest",
      "content_hash",
      "created_at",
    ]),
    security_revisions: opaque("security_revisions", [
      "id",
      "manifest_id",
      "revision",
      "security_id",
      "entity_id",
      "ticker",
      "exchange",
      "instrument_type",
      "eligible",
      "exclusion_reasons",
      "aliases",
      "source_ids",
      "valid_from",
      "valid_to",
      "content_hash",
      "created_at",
    ]),
    tasks: arrayValue(row.tasks, "discovery context.tasks", 100).map(
      parseDiscoveryStageTask,
    ),
    theme_episodes: opaque("theme_episodes", [
      "id",
      "task_id",
      "theme_id",
      "revision",
      "episode",
      "source_ids",
      "valid_from",
      "valid_to",
      "content_hash",
      "created_at",
    ]),
    exposure_facts: opaque("exposure_facts", [
      "id",
      "task_id",
      "security_revision_id",
      "theme_episode_revision_id",
      "exposure_kind",
      "fact",
      "source_ids",
      "valid_from",
      "valid_to",
      "content_hash",
      "created_at",
    ]),
    research_nominations: opaque("research_nominations", [
      "id",
      "task_id",
      "security_revision_id",
      "theme_episode_revision_id",
      "exposure_fact_ids",
      "state",
      "rationale",
      "created_at",
      "updated_at",
    ]),
  };
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
    [
      "phase",
      "market_date",
      "policy_version",
      "reservation_plan",
      "request_window",
    ],
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
    request_window: parseRequestWindow(row.request_window),
  };
}

function parseRequestWindow(value: unknown): JsonObject {
  const row = objectValue(value, "payload.request_window");
  exactKeys(
    row,
    ["start", "end", "timezone", "market_date", "phase"],
    "payload.request_window",
  );
  const start = timestamp(row.start, "payload.request_window.start")!;
  const end = timestamp(row.end, "payload.request_window.end")!;
  if (
    Date.parse(start) >= Date.parse(end) || row.timezone !== "America/Chicago"
  ) {
    throw new Error("payload.request_window is invalid");
  }
  return {
    start,
    end,
    timezone: "America/Chicago",
    market_date: dateValue(
      row.market_date,
      "payload.request_window.market_date",
    ),
    phase: enumValue(row.phase, PHASES, "payload.request_window.phase"),
  };
}

export function parseCheckpointIntelligencePayload(
  value: unknown,
): CheckpointIntelligencePayload {
  const row = objectValue(value, "checkpoint payload");
  exactKeys(row, ["cache_key", "receipt", "items"], "checkpoint payload");
  return {
    cache_key: stringValue(row.cache_key, "checkpoint payload.cache_key", 512),
    receipt: boundedObject(row.receipt, "checkpoint payload.receipt", 16_384),
    items: arrayValue(row.items, "checkpoint payload.items", 50).map((
      item,
      index,
    ) => boundedObject(item, `checkpoint payload.items[${index}]`, 16_384)),
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
    "cache_predecessor_receipt_id",
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
    cache_predecessor_receipt_id: row.cache_predecessor_receipt_id === null
      ? null
      : uuidValue(
        row.cache_predecessor_receipt_id,
        `${path}.cache_predecessor_receipt_id`,
      ),
  };
}

function parseItem(value: unknown, index: number): JsonObject {
  const path = `payload.items[${index}]`;
  const row = objectValue(value, path);
  const keys = [
    "id",
    "run_item_id",
    "receipt_id",
    "provider",
    "upstream_item_id",
    "canonical_url",
    "request_url",
    "published_at",
    "retrieved_at",
    "effective_at",
    "reporting_at",
    "entity_ids",
    "security_ids",
    "discovery_status",
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
  const exactDuplicateReasons = [
    "same_canonical_url",
    "same_upstream_item_id",
    "same_content_hash",
  ];
  if (
    (disposition === "duplicate" &&
      (dropReason === null || !exactDuplicateReasons.includes(dropReason))) ||
    (disposition === "near_duplicate" &&
      dropReason !== "similar_normalized_content")
  ) {
    throw new Error(`${path}.drop_reason is invalid for disposition`);
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
  const normalizedCanonicalUrl = canonicalUrl === null
    ? null
    : canonicalizeUrl(canonicalUrl, `${path}.canonical_url`);
  const provider = enumValue(
    row.provider,
    INTELLIGENCE_PROVIDERS,
    `${path}.provider`,
  );
  const requestUrl = providerRequestUrl(
    row.request_url,
    provider,
    `${path}.request_url`,
  );
  const entityIds = identifierArray(row.entity_ids, `${path}.entity_ids`, 160);
  const securityIds = identifierArray(
    row.security_ids,
    `${path}.security_ids`,
    32,
  );
  const discoveryStatus = enumValue(
    row.discovery_status,
    DISCOVERY_STATUSES,
    `${path}.discovery_status`,
  );
  if (discoveryStatus === "qualified" && securityIds.length === 0) {
    throw new Error(
      `${path}.qualified evidence requires a security identifier`,
    );
  }
  return {
    id: uuidValue(row.id, `${path}.id`),
    run_item_id: uuidValue(row.run_item_id, `${path}.run_item_id`),
    receipt_id: uuidValue(row.receipt_id, `${path}.receipt_id`),
    provider,
    upstream_item_id: nullableString(
      row.upstream_item_id,
      `${path}.upstream_item_id`,
      512,
    ),
    canonical_url: normalizedCanonicalUrl,
    request_url: requestUrl,
    published_at: timestamp(row.published_at, `${path}.published_at`, true),
    retrieved_at: timestamp(row.retrieved_at, `${path}.retrieved_at`),
    effective_at: timestamp(row.effective_at, `${path}.effective_at`, true),
    reporting_at: timestamp(row.reporting_at, `${path}.reporting_at`, true),
    entity_ids: entityIds,
    security_ids: securityIds,
    discovery_status: discoveryStatus,
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
    const path = `payload.packet.packet.candidates[${index}]`;
    const candidateRow = objectValue(
      candidate,
      path,
    );
    exactKeys(candidateRow, ["candidate_key", "evidence_ids"], path);
    stringValue(candidateRow.candidate_key, `${path}.candidate_key`, 256);
    uuidArray(
      candidateRow.evidence_ids,
      `${path}.evidence_ids`,
      0,
      8,
    );
  });
  evidence.forEach((item, index) => {
    const path = `payload.packet.packet.evidence[${index}]`;
    const evidenceRow = objectValue(
      item,
      path,
    );
    exactKeys(evidenceRow, ["item_id", "normalized_text"], path);
    uuidValue(evidenceRow.item_id, `${path}.item_id`);
    stringValue(
      evidenceRow.normalized_text,
      `${path}.normalized_text`,
      2_000,
      true,
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
    [
      "run_id",
      "reservation_ids",
      "cache_entries",
      "request_window",
      "duplicate",
      ...("reservation_usage" in row ? ["reservation_usage"] : []),
    ],
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
        65_536,
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
    request_window: parseRequestWindow(row.request_window),
    ...("reservation_usage" in row
      ? {
        reservation_usage: Object.fromEntries(
          Object.entries(
            objectValue(row.reservation_usage, "reservation usage"),
          ).map((
            [key, value],
          ) => [
            uuidValue(key, "reservation usage id"),
            integer(value, "reservation usage count", 0, 100),
          ]),
        ),
      }
      : {}),
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
