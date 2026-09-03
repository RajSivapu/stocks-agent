import { readBoundedJson } from "../_shared/bounded-json.ts";
import { assertAllowedOrigin, corsHeaders, preflightResponse } from "../_shared/cors.ts";
import { HttpError, jsonResponse } from "../_shared/errors.ts";
import { resolveRoute, validateRouteBody } from "./routes.ts";
import {
  digestPairingValue,
  generatePairingCode,
} from "../telegram-portfolio/pairing.mjs";
import { attachGatewayCredential, generateInboundConnectionSecret, prepareConnectionCreate } from "./connections.ts";
import {
  accountRequestAttestation,
  attachCleanupTokenDigest,
  deleteRecentTelegramMessages,
  exportResponse,
  generateCleanupToken,
  sanitizeDeletionResponse,
} from "./account.ts";
import type { AuthenticatedClaims } from "../_shared/jwt.ts";
import { parseOperatorHealth, parsePublicHealth } from "./health.ts";


const MAX_BODY_BYTES = 64 * 1024;
const TOKEN_RE = /^Bearer ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)$/;

export type AppApiRepository = {
  publicHealth(): Promise<Record<string, unknown>>;
  dispatch(input: Record<string, unknown>): Promise<Record<string, unknown>>;
};

export type AppApiHandlerDependencies = {
  allowedOrigins: readonly string[];
  ipHashPepper: string;
  pairingHashPepper: string;
  stepUpHashPepper: string;
  repository: AppApiRepository;
  authenticate(request: Request): Promise<AuthenticatedClaims>;
  revokeAllSessions(bearerToken: string): Promise<void>;
  telegramBotToken: string;
  telegramFetch?: typeof fetch;
  newId?: () => string;
  newPairingCode?: () => string;
  newConnectionSecret?: () => string;
  newCleanupToken?: () => string;
};

function errorResponse(error: unknown, request: Request, allowedOrigins: readonly string[]): Response {
  const headers = corsHeaders(request, allowedOrigins);
  if (error instanceof HttpError) {
    return jsonResponse(error.status, { ok: false, error: { code: error.code } }, headers);
  }
  return jsonResponse(500, { ok: false, error: { code: "INTERNAL_ERROR" } }, headers);
}

function bearerToken(request: Request): string {
  const match = TOKEN_RE.exec(request.headers.get("authorization") ?? "");
  if (!match || match[1].length > 16_384) {
    throw new HttpError(401, "UNAUTHORIZED", "unauthorized");
  }
  return match[1];
}

async function clientDigest(request: Request, pepper: string): Promise<string> {
  if (new TextEncoder().encode(pepper).byteLength < 32) {
    throw new Error("IP hash pepper is too short");
  }
  const raw = request.headers.get("x-forwarded-for")?.split(",", 1)[0].trim()
    || request.headers.get("x-real-ip")?.trim()
    || "unknown";
  const bounded = /^[0-9a-fA-F:.]{1,64}$/.test(raw) ? raw : "unknown";
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(pepper),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const digest = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(bounded));
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

function responseStatus(value: Record<string, unknown>): number {
  if (value.ok === true) return 200;
  const error = value.error;
  const code = error && typeof error === "object" && !Array.isArray(error)
    ? (error as Record<string, unknown>).code
    : null;
  if (code === "RATE_LIMITED") return 429;
  if (code === "UNAUTHORIZED") return 401;
  if (code === "NOT_FOUND") return 404;
  return 400;
}

export function createAppApiHandler(dependencies: AppApiHandlerDependencies) {
  const newId = dependencies.newId ?? crypto.randomUUID;
  const newPairingCode = dependencies.newPairingCode ?? generatePairingCode;
  const newConnectionSecret = dependencies.newConnectionSecret ?? generateInboundConnectionSecret;
  const newCleanupToken = dependencies.newCleanupToken ?? generateCleanupToken;
  return async (request: Request): Promise<Response> => {
    try {
      const url = new URL(request.url);
      if (url.search || url.hash) throw new HttpError(404, "NOT_FOUND", "route not found");
      if (request.method === "OPTIONS") {
        return preflightResponse(
          request,
          dependencies.allowedOrigins,
          ["GET", "PATCH", "POST"],
        );
      }
      const route = resolveRoute(request.method, url.pathname);
      assertAllowedOrigin(request, dependencies.allowedOrigins);
      if (route.key === "public_health") {
        const health = parsePublicHealth(await dependencies.repository.publicHealth());
        return jsonResponse(200, { ok: true, data: health }, corsHeaders(request, dependencies.allowedOrigins));
      }
      let claims: AuthenticatedClaims;
      try {
        claims = await dependencies.authenticate(request);
      } catch (error) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(401, "UNAUTHORIZED", "unauthorized");
      }
      if (!claims.sub || claims.role !== "authenticated") {
        throw new HttpError(401, "UNAUTHORIZED", "unauthorized");
      }

      const body = route.body === "none"
        ? {}
        : await readBoundedJson(request, MAX_BODY_BYTES);
      let validated = validateRouteBody(route, body);
      try {
        validated = await accountRequestAttestation(
          route.key, validated, claims, dependencies.stepUpHashPepper,
        );
      } catch (_error) {
        throw new HttpError(401, "STEP_UP_REQUIRED", "fresh OTP step up is required");
      }
      let pairingCode: string | undefined;
      if (route.key === "telegram_pairing") {
        pairingCode = newPairingCode();
        validated = {
          ...validated,
          code_digest: await digestPairingValue(pairingCode, dependencies.pairingHashPepper),
        };
      }
      let inboundConnectionSecret: string | undefined;
      let cleanupToken: string | undefined;
      if (route.key === "connection_create") {
        const connection = await prepareConnectionCreate(validated, newConnectionSecret);
        validated = connection.request;
        inboundConnectionSecret = connection.secret;
      }
      if (route.key === "account_delete_confirm") {
        const cleanup = await attachCleanupTokenDigest(validated, newCleanupToken);
        validated = cleanup.request;
        cleanupToken = cleanup.token;
      }
      const result = await dependencies.repository.dispatch({
        route: `${route.method} ${route.path}`,
        requestId: newId(),
        ipDigest: await clientDigest(request, dependencies.ipHashPepper),
        bearerToken: bearerToken(request),
        body: validated,
      });
      let responseValue = pairingCode && result.ok === true && result.data &&
          typeof result.data === "object" && !Array.isArray(result.data)
        ? {
          ...result,
          data: { ...(result.data as Record<string, unknown>), code: pairingCode },
        }
        : result;
      if (route.key === "operator_health" && responseValue.ok === true) {
        responseValue = {
          ...responseValue,
          data: parseOperatorHealth(responseValue.data),
        };
      }
      if (inboundConnectionSecret) {
        responseValue = attachGatewayCredential(responseValue, inboundConnectionSecret);
      }
      if (route.key === "account_delete_confirm" && responseValue.ok === true) {
        const cleanup = await deleteRecentTelegramMessages(
          responseValue,
          dependencies.telegramBotToken,
          dependencies.telegramFetch,
        );
        if (cleanupToken && cleanup.recordRequired) {
          const data = responseValue.data as Record<string, unknown>;
          const cleanupReceipt = await dependencies.repository.dispatch({
            route: "POST /account/delete/cleanup-result",
            requestId: newId(),
            ipDigest: await clientDigest(request, dependencies.ipHashPepper),
            bearerToken: bearerToken(request),
            body: {
              deletion_request_id: data.deletion_request_id,
              cleanup_token: cleanupToken,
              attempted: cleanup.attempted,
              deleted: cleanup.deleted,
              failed: cleanup.failed,
              status: cleanup.status,
            },
          });
          if (cleanupReceipt.ok !== true ||
              !cleanupReceipt.data || typeof cleanupReceipt.data !== "object" ||
              Array.isArray(cleanupReceipt.data) ||
              (cleanupReceipt.data as Record<string, unknown>).status !== "recorded") {
            cleanup.status = "unavailable";
          }
        }
        responseValue = sanitizeDeletionResponse(responseValue, cleanup);
        try {
          await dependencies.revokeAllSessions(bearerToken(request));
        } catch (_error) {
          throw new HttpError(503, "SESSION_REVOCATION_FAILED", "session revocation failed");
        }
      }
      const responseHeaders = corsHeaders(request, dependencies.allowedOrigins);
      if (route.key === "export_account" || route.key === "export_ledger") {
        return exportResponse(responseValue, responseHeaders);
      }
      return jsonResponse(
        responseStatus(responseValue),
        responseValue,
        responseHeaders,
      );
    } catch (error) {
      return errorResponse(error, request, dependencies.allowedOrigins);
    }
  };
}
