import { parseAnalysisSubmissionV2, parseProviderEnvelopeV2, type ProviderOperation } from "../../../packages/contracts/src/provider.ts";
import { readBoundedJson } from "../_shared/bounded-json.ts";
import { HttpError, jsonResponse } from "../_shared/errors.ts";
import { parseArtifactMutationBatch } from "../market-briefing-gateway/_shared/contracts.ts";

const MAX_BODY_BYTES = 64 * 1024;
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
const BEARER = new RegExp(`^Bearer (${UUID})\\.([A-Za-z0-9_-]{43})$`, "i");

export interface AgentGatewayRepository {
  invoke(operation: ProviderOperation, request: Record<string, unknown>): Promise<Record<string, unknown>>;
}

export type AgentGatewayDependencies = { repository: AgentGatewayRepository };

function errorResponse(status: number, code: string): Response {
  return jsonResponse(status, { ok: false, error: { code } });
}

function exactObject(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid payload");
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== keys.length || keys.some((key) => !Object.hasOwn(row, key))) {
    throw new Error("invalid payload");
  }
  return row;
}

function uuidOrNull(value: unknown): boolean {
  return value === null || (typeof value === "string" && new RegExp(`^${UUID}$`, "i").test(value));
}

function handshakeFinishPayload(value: unknown): Record<string, unknown> {
  const row = exactObject(value, ["contract_version", "challenge", "source_checks"]);
  if (row.contract_version !== 2 || typeof row.challenge !== "string" ||
    !/^[0-9a-f]{64}$/.test(row.challenge) || !Array.isArray(row.source_checks) ||
    row.source_checks.length !== 3) throw new Error("invalid handshake receipt");
  const allowedHosts = new Set(["query1.finance.yahoo.com", "www.sec.gov", "finnhub.io"]);
  const observedHosts = new Set<string>();
  const checks = row.source_checks.map((value) => {
    const check = exactObject(value, ["host", "status", "content_hash", "observed_at"]);
    if (typeof check.host !== "string" || !allowedHosts.has(check.host) || observedHosts.has(check.host) ||
      !["reachable", "unreachable"].includes(String(check.status)) ||
      typeof check.observed_at !== "string" || check.observed_at.length > 40 ||
      Number.isNaN(Date.parse(check.observed_at)) ||
      (check.status === "reachable" && (typeof check.content_hash !== "string" ||
        !/^[0-9a-f]{64}$/.test(check.content_hash))) ||
      (check.status === "unreachable" && check.content_hash !== null)) {
      throw new Error("invalid handshake receipt");
    }
    observedHosts.add(check.host);
    return check;
  });
  return { contract_version: 2, challenge: row.challenge, source_checks: checks };
}

function preparePayload(operation: ProviderOperation, payload: unknown): unknown {
  if (operation === "start_run") {
    const row = exactObject(payload, ["trigger_request_id"]);
    if (!uuidOrNull(row.trigger_request_id)) throw new Error("invalid payload");
    return row;
  }
  if (operation === "read_bounded_context") {
    return exactObject(payload, []);
  }
  if (operation === "finish_run") {
    if (payload && typeof payload === "object" && !Array.isArray(payload) &&
      Object.keys(payload as Record<string, unknown>).length === 0) return payload;
    return handshakeFinishPayload(payload);
  }
  if (operation === "submit_analysis") return parseAnalysisSubmissionV2(payload);
  if (operation === "record_permitted_artifacts") return parseArtifactMutationBatch(payload);
  const row = exactObject(payload, ["limit"]);
  if (!Number.isInteger(row.limit) || Number(row.limit) < 1 || Number(row.limit) > 50) {
    throw new Error("invalid payload");
  }
  return row;
}

function decodedSecret(value: string): Uint8Array | null {
  try {
    const padded = value.replaceAll("-", "+").replaceAll("_", "/") + "=";
    const decoded = atob(padded);
    if (decoded.length !== 32) return null;
    return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
  } catch {
    return null;
  }
}

async function credential(request: Request): Promise<{ connectionId: string; secretDigest: string } | null> {
  const match = BEARER.exec(request.headers.get("authorization") ?? "");
  if (!match) return null;
  const secret = decodedSecret(match[2]);
  if (!secret) return null;
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", secret.slice().buffer as ArrayBuffer));
  return {
    connectionId: match[1].toLowerCase(),
    secretDigest: Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join(""),
  };
}

export function createAgentGatewayHandler(dependencies: AgentGatewayDependencies) {
  return async (request: Request): Promise<Response> => {
    if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
    const authority = await credential(request);
    if (!authority) return errorResponse(401, "UNAUTHORIZED");
    let envelope;
    let payload: unknown;
    try {
      envelope = parseProviderEnvelopeV2(await readBoundedJson(request, MAX_BODY_BYTES));
      payload = preparePayload(envelope.operation, envelope.payload);
    } catch (error) {
      return error instanceof HttpError && error.status === 413
        ? errorResponse(413, "REQUEST_TOO_LARGE")
        : errorResponse(400, "INVALID_REQUEST");
    }
    try {
      const result = await dependencies.repository.invoke(envelope.operation, {
        connection_id: authority.connectionId,
        secret_digest: authority.secretDigest,
        contract_version: envelope.contract_version,
        operation: envelope.operation,
        request_id: envelope.request_id,
        run_id: envelope.run_id,
        dry_run: envelope.dry_run,
        payload,
      });
      return jsonResponse(200, { ok: true, data: result });
    } catch (error) {
      const code = error && typeof error === "object" && "code" in error
        ? String((error as { code: unknown }).code)
        : "";
      if (code === "UNAUTHORIZED" || code === "42501") return errorResponse(401, "UNAUTHORIZED");
      if (code === "RATE_LIMITED" || code === "54000") return errorResponse(429, "RATE_LIMITED");
      if (code === "REQUEST_CONFLICT" || code === "23505") return errorResponse(409, "REQUEST_CONFLICT");
      return errorResponse(500, "GATEWAY_UNAVAILABLE");
    }
  };
}
