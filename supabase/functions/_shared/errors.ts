export class HttpError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.code = code;
  }
}

const BASE_HEADERS = {
  "Cache-Control": "no-store",
  "Content-Type": "application/json; charset=utf-8",
  "X-Content-Type-Options": "nosniff",
} as const;

export function jsonResponse(
  status: number,
  body: Record<string, unknown>,
  headers: HeadersInit = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...BASE_HEADERS, ...Object.fromEntries(new Headers(headers)) },
  });
}

export function jsonError(error: unknown, headers: HeadersInit = {}): Response {
  if (error instanceof HttpError) {
    return jsonResponse(error.status, { error: { code: error.code } }, headers);
  }
  return jsonResponse(500, { error: { code: "INTERNAL_ERROR" } }, headers);
}

export function requireMethod(request: Request, allowed: readonly string[]): void {
  if (!allowed.includes(request.method)) {
    throw new HttpError(405, "METHOD_NOT_ALLOWED", "method not allowed");
  }
}

export function requireJsonContentType(request: Request): void {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") {
    throw new HttpError(415, "JSON_REQUIRED", "application/json is required");
  }
}
