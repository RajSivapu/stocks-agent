import type { Phase } from "../../../packages/contracts/src/provider.ts";

export type PacketEvidenceCategory = "market_snapshot" | "source_search" | "filing" | "fundamentals" |
  "news" | "technicals" | "corporate_action" | "issuer" | "exchange";

export type PacketEvidenceFact = {
  evidence_id: string;
  source_run_id: string | null;
  category: PacketEvidenceCategory;
  source_identifier: string;
  reference_identifier: string | null;
  observed_at: string | null;
  retrieved_at: string;
  revalidated_at: string | null;
  content_hash: string;
  claims: string[];
  status: "fresh" | "stale" | "conflicting" | "missing" |
    "no_new_material_evidence" | "suspected" | "needs_review" | "clear";
};

export type PacketSearchReceipt = {
  searched_at: string;
  categories: string[];
  sources: Array<{ url: string; status: "fetched" | "unavailable"; content_hash: string | null }>;
  result_status: "material_evidence_found" | "no_new_material_evidence" | "source_unavailable";
  content_hash: string;
};

export type EvidencePacketPayload = {
  version: 1;
  run_id: string;
  phase: Phase;
  market_date: string;
  issued_at: string;
  expires_at: string;
  facts: PacketEvidenceFact[];
  search_receipt: PacketSearchReceipt | null;
};

export type SignedEvidencePacket = {
  payload: EvidencePacketPayload;
  signature: string;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const HASH = /^[0-9a-f]{64}$/;
const EVIDENCE_ID = /^[a-z0-9][a-z0-9._:-]{0,99}$/;
const PHASES = new Set(["pre-market", "intraday", "post-market", "on-demand"]);
const CATEGORIES = new Set([
  "market_snapshot", "source_search", "filing", "fundamentals", "news", "technicals",
  "corporate_action", "issuer", "exchange",
]);
const STATUSES = new Set(["fresh", "stale", "conflicting", "missing", "no_new_material_evidence", "suspected", "needs_review", "clear"]);

function exactObject(value: unknown, keys: readonly string[], label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} is invalid`);
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== keys.length || keys.some((key) => !Object.hasOwn(row, key))) {
    throw new Error(`${label} is invalid`);
  }
  return row;
}

function validInstant(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length > 40 || Number.isNaN(Date.parse(value))) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function canonical(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("packet contains a non-finite number");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object") {
    const row = value as Record<string, unknown>;
    return `{${Object.keys(row).sort().map((key) => `${JSON.stringify(key)}:${canonical(row[key])}`).join(",")}}`;
  }
  throw new Error("packet contains an unsupported value");
}

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
  let different = left.length ^ right.length;
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    different |= (left[index] ?? 0) ^ (right[index] ?? 0);
  }
  return different === 0;
}

function hex(bytes: Uint8Array): string {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function unhex(value: string): Uint8Array {
  if (!HASH.test(value)) throw new Error("evidence packet signature is invalid");
  return Uint8Array.from(value.match(/../g)!, (part) => Number.parseInt(part, 16));
}

export function decodeSigningKey(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]{43}$/.test(value)) throw new Error("evidence signing key must be 256-bit base64url");
  try {
    const decoded = atob(value.replaceAll("-", "+").replaceAll("_", "/") + "=");
    if (decoded.length !== 32) throw new Error();
    return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
  } catch {
    throw new Error("evidence signing key must be 256-bit base64url");
  }
}

async function signature(payload: EvidencePacketPayload, keyBytes: Uint8Array): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw", keyBytes.slice().buffer as ArrayBuffer, { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(canonical(payload))));
}

function validateFact(value: unknown): PacketEvidenceFact {
  const row = exactObject(value, [
    "evidence_id", "source_run_id", "category", "source_identifier", "reference_identifier",
    "observed_at", "retrieved_at", "revalidated_at", "content_hash", "claims", "status",
  ], "evidence fact");
  if (typeof row.evidence_id !== "string" || !EVIDENCE_ID.test(row.evidence_id) ||
    (row.source_run_id !== null && (typeof row.source_run_id !== "string" || !UUID.test(row.source_run_id))) ||
    typeof row.category !== "string" || !CATEGORIES.has(row.category) ||
    typeof row.source_identifier !== "string" || row.source_identifier.length < 1 || row.source_identifier.length > 200 ||
    (row.reference_identifier !== null && (typeof row.reference_identifier !== "string" || row.reference_identifier.length > 500)) ||
    (row.observed_at !== null && (typeof row.observed_at !== "string" || Number.isNaN(Date.parse(row.observed_at)))) ||
    (row.revalidated_at !== null && (typeof row.revalidated_at !== "string" || Number.isNaN(Date.parse(row.revalidated_at)))) ||
    typeof row.content_hash !== "string" || !HASH.test(row.content_hash) ||
    !Array.isArray(row.claims) || row.claims.length > 10 || row.claims.some((claim) => typeof claim !== "string" || claim.length > 500) ||
    typeof row.status !== "string" || !STATUSES.has(row.status)) {
    throw new Error("evidence fact is invalid");
  }
  validInstant(row.retrieved_at, "retrieved_at");
  return structuredClone(row) as PacketEvidenceFact;
}

function validateSearchReceipt(value: unknown): PacketSearchReceipt | null {
  if (value === null) return null;
  const row = exactObject(value, ["searched_at", "categories", "sources", "result_status", "content_hash"], "search receipt");
  validInstant(row.searched_at, "searched_at");
  if (!Array.isArray(row.categories) || row.categories.length < 1 || row.categories.length > 8 ||
    row.categories.some((item) => typeof item !== "string" || item.length > 30) ||
    !Array.isArray(row.sources) || row.sources.length < 1 || row.sources.length > 20 ||
    typeof row.result_status !== "string" || !["material_evidence_found", "no_new_material_evidence", "source_unavailable"].includes(row.result_status) ||
    typeof row.content_hash !== "string" || !HASH.test(row.content_hash)) {
    throw new Error("search receipt is invalid");
  }
  for (const source of row.sources) {
    const item = exactObject(source, ["url", "status", "content_hash"], "search source");
    if (typeof item.url !== "string" || item.url.length > 500 ||
      !["fetched", "unavailable"].includes(String(item.status)) ||
      (item.content_hash !== null && (typeof item.content_hash !== "string" || !HASH.test(item.content_hash)))) {
      throw new Error("search source is invalid");
    }
  }
  return structuredClone(row) as PacketSearchReceipt;
}

function validatePayload(value: unknown): EvidencePacketPayload {
  const row = exactObject(value, [
    "version", "run_id", "phase", "market_date", "issued_at", "expires_at", "facts", "search_receipt",
  ], "evidence packet payload");
  const issued = Date.parse(validInstant(row.issued_at, "issued_at"));
  const expires = Date.parse(validInstant(row.expires_at, "expires_at"));
  if (row.version !== 1 || typeof row.run_id !== "string" || !UUID.test(row.run_id) ||
    typeof row.phase !== "string" || !PHASES.has(row.phase) ||
    typeof row.market_date !== "string" || !DATE.test(row.market_date) ||
    expires <= issued || expires - issued > 15 * 60_000 ||
    !Array.isArray(row.facts) || row.facts.length > 100) {
    throw new Error("evidence packet payload is invalid");
  }
  const facts = row.facts.map(validateFact);
  if (new Set(facts.map((fact) => fact.evidence_id)).size !== facts.length) {
    throw new Error("evidence packet has duplicate evidence");
  }
  return {
    version: 1,
    run_id: row.run_id,
    phase: row.phase as Phase,
    market_date: row.market_date,
    issued_at: row.issued_at as string,
    expires_at: row.expires_at as string,
    facts,
    search_receipt: validateSearchReceipt(row.search_receipt),
  };
}

export async function createEvidencePacket(
  rawPayload: EvidencePacketPayload,
  keyBytes: Uint8Array,
): Promise<SignedEvidencePacket> {
  const payload = validatePayload(rawPayload);
  return { payload, signature: hex(await signature(payload, keyBytes)) };
}

export async function verifyEvidencePackets(
  values: unknown,
  keyBytes: Uint8Array,
  expectedRunId: string,
  now: Date = new Date(),
): Promise<{ facts: PacketEvidenceFact[]; search_receipts: PacketSearchReceipt[]; phase: Phase; market_date: string }> {
  if (!Array.isArray(values) || values.length < 1 || values.length > 4) throw new Error("evidence packets are invalid");
  const facts = new Map<string, PacketEvidenceFact>();
  const searchReceipts: PacketSearchReceipt[] = [];
  let phase: Phase | null = null;
  let marketDate: string | null = null;
  const packetSignatures = new Set<string>();
  for (const value of values) {
    const row = exactObject(value, ["payload", "signature"], "evidence packet");
    if (typeof row.signature !== "string" || !HASH.test(row.signature)) throw new Error("evidence packet signature is invalid");
    if (packetSignatures.has(row.signature)) throw new Error("duplicate evidence packet");
    packetSignatures.add(row.signature);
    const payload = validatePayload(row.payload);
    const actual = await signature(payload, keyBytes);
    if (!equalBytes(actual, unhex(row.signature))) throw new Error("evidence packet signature is invalid");
    if (payload.run_id !== expectedRunId) throw new Error("evidence packet run does not match");
    if (Date.parse(payload.issued_at) > now.valueOf() + 5 * 60_000 || Date.parse(payload.expires_at) < now.valueOf()) {
      throw new Error("evidence packet expired");
    }
    if ((phase !== null && phase !== payload.phase) || (marketDate !== null && marketDate !== payload.market_date)) {
      throw new Error("evidence packet run metadata conflicts");
    }
    phase = payload.phase;
    marketDate = payload.market_date;
    for (const fact of payload.facts) {
      const prior = facts.get(fact.evidence_id);
      if (prior && canonical(prior) !== canonical(fact)) throw new Error("duplicate evidence conflicts");
      facts.set(fact.evidence_id, fact);
    }
    if (payload.search_receipt) searchReceipts.push(payload.search_receipt);
  }
  if (phase === null || marketDate === null) throw new Error("evidence packets are invalid");
  return { facts: [...facts.values()], search_receipts: searchReceipts, phase, market_date: marketDate };
}
