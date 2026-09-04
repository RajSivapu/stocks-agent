import { DashboardHttpError } from "./errors.ts";

const ALLOWED_REQUEST_HEADERS = "authorization, content-type";

export function parseAllowedOrigins(value: string): string[] {
  const origins = value.split(",").map((item) => item.trim()).filter(Boolean);
  if (origins.length === 0 || origins.length > 5 || new Set(origins).size !== origins.length) {
    throw new Error("DASHBOARD_ALLOWED_ORIGINS must contain one to five exact origins");
  }
  for (const origin of origins) {
    const parsed = new URL(origin);
    if (parsed.origin !== origin || parsed.protocol !== "https:" || origin.includes("*")) {
      throw new Error("dashboard origins must be exact HTTPS origins");
    }
  }
  return origins;
}

export function requireAllowedOrigin(request: Request, allowedOrigins: readonly string[]): string {
  const origin = request.headers.get("origin") ?? "";
  if (!allowedOrigins.includes(origin)) {
    throw new DashboardHttpError(403, "invalid_request", "Request origin is not allowed.");
  }
  return origin;
}

export function corsHeaders(origin: string): Headers {
  return new Headers({
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET",
    "access-control-allow-headers": ALLOWED_REQUEST_HEADERS,
    "access-control-max-age": "600",
    "vary": "Origin",
  });
}

export function preflightResponse(request: Request, allowedOrigins: readonly string[]): Response {
  const origin = requireAllowedOrigin(request, allowedOrigins);
  if (request.headers.get("access-control-request-method") !== "GET") {
    throw new DashboardHttpError(405, "invalid_request", "Only GET requests are available.");
  }
  const requested = (request.headers.get("access-control-request-headers") ?? "")
    .toLowerCase()
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  if (requested.some((value) => !["authorization", "content-type"].includes(value))) {
    throw new DashboardHttpError(403, "invalid_request", "Requested headers are not allowed.");
  }
  const headers = corsHeaders(origin);
  headers.set("cache-control", "no-store");
  return new Response(null, { status: 204, headers });
}
