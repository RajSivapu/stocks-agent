import { readBoundedJson } from "../_shared/bounded-json.ts";
import { assertAllowedOrigin, corsHeaders, preflightResponse } from "../_shared/cors.ts";
import { HttpError, jsonResponse } from "../_shared/errors.ts";
import { resolveRoute, validateRouteBody } from "./routes.ts";


const MAX_BODY_BYTES = 64 * 1024;
const TOKEN_RE = /^Bearer ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)$/;

export type AppApiRepository = {
  dispatch(input: Record<string, unknown>): Promise<Record<string, unknown>>;
};

export type AppApiHandlerDependencies = {
  allowedOrigins: readonly string[];
  ipHashPepper: string;
  repository: AppApiRepository;
  authenticate(request: Request): Promise<{ sub: string; role: "authenticated" }>;
  newId?: () => string;
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
      let claims: { sub: string; role: "authenticated" };
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
      const validated = validateRouteBody(route, body);
      const result = await dependencies.repository.dispatch({
        route: `${route.method} ${route.path}`,
        requestId: newId(),
        ipDigest: await clientDigest(request, dependencies.ipHashPepper),
        bearerToken: bearerToken(request),
        body: validated,
      });
      return jsonResponse(
        responseStatus(result),
        result,
        corsHeaders(request, dependencies.allowedOrigins),
      );
    } catch (error) {
      return errorResponse(error, request, dependencies.allowedOrigins);
    }
  };
}
