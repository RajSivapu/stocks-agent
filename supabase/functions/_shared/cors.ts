import { HttpError } from "./errors.ts";


function configuredOrigins(values: readonly string[]): Set<string> {
  const result = new Set<string>();
  for (const value of values) {
    let parsed: URL;
    try {
      parsed = new URL(value);
    } catch (_error) {
      throw new Error("configured CORS origin is malformed");
    }
    if (parsed.origin !== value || !["https:", "http:"].includes(parsed.protocol)) {
      throw new Error("configured CORS origin must be exact");
    }
    result.add(value);
  }
  return result;
}

export function assertAllowedOrigin(request: Request, allowedOrigins: readonly string[]): void {
  const origin = request.headers.get("origin");
  if (origin === null) return;
  if (origin === "null" || !configuredOrigins(allowedOrigins).has(origin)) {
    throw new HttpError(403, "ORIGIN_DENIED", "origin is not allowed");
  }
}

export function corsHeaders(
  request: Request,
  allowedOrigins: readonly string[],
): Record<string, string> {
  const origin = request.headers.get("origin");
  if (origin === null || !configuredOrigins(allowedOrigins).has(origin)) return {};
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    Vary: "Origin",
  };
}

export function preflightResponse(
  request: Request,
  allowedOrigins: readonly string[],
  allowedMethods: readonly string[],
): Response {
  assertAllowedOrigin(request, allowedOrigins);
  const requestedMethod = request.headers.get("access-control-request-method");
  if (request.method !== "OPTIONS" || !requestedMethod || !allowedMethods.includes(requestedMethod)) {
    throw new HttpError(405, "PREFLIGHT_DENIED", "preflight method is not allowed");
  }
  return new Response(null, {
    status: 204,
    headers: {
      ...corsHeaders(request, allowedOrigins),
      "Access-Control-Allow-Methods": allowedMethods.join(", "),
      "Access-Control-Allow-Headers": "authorization, content-type",
      "Access-Control-Max-Age": "600",
      "Cache-Control": "no-store",
    },
  });
}
