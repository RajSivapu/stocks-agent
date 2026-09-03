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

function calendarDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === value;
}

function uuidOrNull(value: unknown): boolean {
  return value === null || (typeof value === "string" && new RegExp(`^${UUID}$`, "i").test(value));
}

function preparePayload(operation: ProviderOperation, payload: unknown): unknown {
  if (operation === "start_run") {
    const row = exactObject(payload, ["phase", "market_date", "trigger_request_id"]);
    if (!["pre-market", "intraday", "post-market", "on-demand"].includes(String(row.phase)) ||
      !calendarDate(row.market_date) || !uuidOrNull(row.trigger_request_id)) throw new Error("invalid payload");
    return row;
  }
  if (operation === "read_bounded_context" || operation === "finish_run") {
    return exactObject(payload, []);
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
