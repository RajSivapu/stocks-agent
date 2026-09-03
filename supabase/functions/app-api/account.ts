import type { AuthenticatedClaims } from "../_shared/jwt.ts";


const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_EXPORT_BYTES = 5 * 1024 * 1024;
const MAX_TELEGRAM_RESPONSE_BYTES = 4 * 1024;

type AmrEntry = { method: string; timestamp: number };

function checkedSessionId(claims: AuthenticatedClaims): string {
  const sessionId = claims.session_id;
  if (typeof sessionId !== "string" || !UUID_RE.test(sessionId)) {
    throw new Error("fresh session identity is unavailable");
  }
  return sessionId.toLowerCase();
}

async function sessionDigest(sessionId: string, pepper: string): Promise<string> {
  if (new TextEncoder().encode(pepper).byteLength < 32) {
    throw new Error("step-up pepper is too short");
  }
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(pepper),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const bytes = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(`stock-agent-session-v1:${sessionId}`),
  );
  return [...new Uint8Array(bytes)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

function recentOtp(claims: AuthenticatedClaims, nowSeconds: number): AmrEntry {
  if (!Array.isArray(claims.amr) || claims.amr.length < 1 || claims.amr.length > 16) {
    throw new Error("fresh OTP authentication is required");
  }
  const candidates = claims.amr.filter((entry): entry is AmrEntry => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) return false;
    const value = entry as Record<string, unknown>;
    return value.method === "otp" && Number.isSafeInteger(value.timestamp);
  });
  const newest = candidates.sort((left, right) => right.timestamp - left.timestamp)[0];
  if (!newest || newest.timestamp > nowSeconds + 30 || newest.timestamp < nowSeconds - 300) {
    throw new Error("fresh OTP authentication is required");
  }
  return newest;
}

const SESSION_BOUND_ROUTES = new Set([
  "account_step_up_challenge",
  "account_step_up_complete",
  "account_delete_request",
  "account_delete_confirm",
  "account_delete_cancel",
  "telegram_pairing",
  "connection_handshake",
]);

/** Add server-only session proof after public request validation. */
export async function accountRequestAttestation(
  routeKey: string,
  validated: Record<string, unknown>,
  claims: AuthenticatedClaims,
  pepper: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<Record<string, unknown>> {
  if (!SESSION_BOUND_ROUTES.has(routeKey)) return validated;
  const digest = await sessionDigest(checkedSessionId(claims), pepper);
  if (routeKey !== "account_step_up_complete") {
    return { ...validated, session_digest: digest };
  }
  const otp = recentOtp(claims, nowSeconds);
  return {
    ...validated,
    session_digest: digest,
    auth_method: "otp",
    authenticated_at: new Date(otp.timestamp * 1000).toISOString(),
  };
}

function downloadData(result: Record<string, unknown>): Record<string, unknown> {
  const data = result.data;
  if (result.ok !== true || !data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("invalid export response");
  }
  return data as Record<string, unknown>;
}

export function exportResponse(
  result: Record<string, unknown>,
  inheritedHeaders: HeadersInit,
): Response {
  const data = downloadData(result);
  const format = data.format;
  const filename = data.filename;
  const mediaType = data.media_type;
  const body = data.body;
  const expected = format === "json"
    ? ["stock-agent-account.json", "application/json; charset=utf-8"]
    : format === "csv"
    ? ["stock-agent-ledger.csv", "text/csv; charset=utf-8"]
    : null;
  if (
    data.status !== "ready" || !expected || filename !== expected[0] ||
    mediaType !== expected[1] || typeof body !== "string" ||
    new TextEncoder().encode(body).byteLength > MAX_EXPORT_BYTES
  ) {
    throw new Error("invalid export response");
  }
  const headers = new Headers(inheritedHeaders);
  headers.set("content-type", mediaType);
  headers.set("content-disposition", `attachment; filename="${filename}"`);
  headers.set("access-control-expose-headers", "Content-Disposition");
  headers.set("cache-control", "no-store");
  headers.set("pragma", "no-cache");
  headers.set("x-content-type-options", "nosniff");
  headers.set("content-security-policy", "default-src 'none'; sandbox");
  return new Response(body, { status: 200, headers });
}

export function generateCleanupToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

export async function attachCleanupTokenDigest(
  request: Record<string, unknown>,
  createToken: () => string = generateCleanupToken,
): Promise<{ request: Record<string, unknown>; token: string }> {
  const token = createToken();
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error("invalid cleanup token entropy");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return {
    request: {
      ...request,
      cleanup_token_digest: [...new Uint8Array(digest)]
        .map((value) => value.toString(16).padStart(2, "0")).join(""),
    },
    token,
  };
}

export type TelegramCleanupResult = {
  status: "nothing_recent" | "completed" | "partial" | "failed" | "unavailable";
  attempted: number;
  deleted: number;
  failed: number;
  recordRequired: boolean;
};

type CleanupTarget = {
  chatId: string;
  messageIds: string[];
  recordRequired: boolean;
  previous: Omit<TelegramCleanupResult, "recordRequired"> | null;
};

function boundedCleanupCount(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0 || Number(value) > 100) {
    throw new Error("invalid Telegram cleanup count");
  }
  return Number(value);
}

function cleanupTarget(result: Record<string, unknown>): CleanupTarget | null {
  const data = result.data;
  if (result.ok !== true || !data || typeof data !== "object" || Array.isArray(data)) return null;
  const target = (data as Record<string, unknown>)._telegram_cleanup;
  if (!target || typeof target !== "object" || Array.isArray(target)) return null;
  const row = target as Record<string, unknown>;
  if (typeof row.record_required !== "boolean") throw new Error("invalid Telegram cleanup target");
  const attempted = boundedCleanupCount(row.attempted);
  const deleted = boundedCleanupCount(row.deleted);
  const failed = boundedCleanupCount(row.failed);
  const allowedStatus = ["nothing_recent", "completed", "partial", "failed", "unavailable"];
  if (deleted + failed !== attempted ||
      (row.previous_status !== null && !allowedStatus.includes(String(row.previous_status)))) {
    throw new Error("invalid Telegram cleanup target");
  }
  const previous = row.previous_status === null ? null : {
    status: row.previous_status as Omit<TelegramCleanupResult, "recordRequired">["status"],
    attempted,
    deleted,
    failed,
  };
  if (row.chat_id === null && Array.isArray(row.message_ids) && row.message_ids.length === 0) {
    return { chatId: "", messageIds: [], recordRequired: row.record_required, previous };
  }
  if (typeof row.chat_id !== "string" || !/^-?[1-9][0-9]{0,15}$/.test(row.chat_id) ||
      !Array.isArray(row.message_ids) || row.message_ids.length > 100 ||
      row.message_ids.some((value) => typeof value !== "string" || !/^[1-9][0-9]{0,18}$/.test(value))) {
    throw new Error("invalid Telegram cleanup target");
  }
  return {
    chatId: row.chat_id,
    messageIds: row.message_ids as string[],
    recordRequired: row.record_required,
    previous,
  };
}

export function sanitizeDeletionResponse(
  result: Record<string, unknown>,
  cleanup: TelegramCleanupResult,
): Record<string, unknown> {
  const data = downloadData(result);
  const { _telegram_cleanup: _discarded, ...publicData } = data;
  const { recordRequired: _private, ...publicCleanup } = cleanup;
  return { ...result, data: { ...publicData, telegram_cleanup: publicCleanup } };
}

async function telegramAccepted(response: Response): Promise<boolean> {
  if (response.body === null) return false;
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_TELEGRAM_RESPONSE_BYTES)) {
    return false;
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > MAX_TELEGRAM_RESPONSE_BYTES) return false;
  try {
    const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    return response.ok && !!value && typeof value === "object" && !Array.isArray(value) &&
      (value as Record<string, unknown>).ok === true;
  } catch {
    return false;
  }
}

export async function deleteRecentTelegramMessages(
  result: Record<string, unknown>,
  botToken: string,
  fetcher: typeof fetch = fetch,
): Promise<TelegramCleanupResult> {
  const target = cleanupTarget(result);
  if (!target) {
    return { status: "unavailable", attempted: 0, deleted: 0, failed: 0, recordRequired: false };
  }
  if (!target.recordRequired) {
    if (!target.previous) throw new Error("missing recorded Telegram cleanup result");
    return { ...target.previous, recordRequired: false };
  }
  if (target.messageIds.length === 0) {
    return { status: "nothing_recent", attempted: 0, deleted: 0, failed: 0, recordRequired: true };
  }
  if (!/^[0-9]{6,20}:[A-Za-z0-9_-]{20,200}$/.test(botToken)) {
    return { status: "unavailable", attempted: 0, deleted: 0, failed: 0, recordRequired: true };
  }
  let deleted = 0;
  for (const messageId of target.messageIds) {
    try {
      const response = await fetcher(`https://api.telegram.org/bot${botToken}/deleteMessage`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ chat_id: target.chatId, message_id: messageId }),
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal: AbortSignal.timeout(5_000),
      });
      if (await telegramAccepted(response)) deleted += 1;
    } catch {
      // Best effort is recorded below; never claim deletion from an uncertain response.
    }
  }
  const attempted = target.messageIds.length;
  const failed = attempted - deleted;
  return {
    status: deleted === attempted ? "completed" : deleted === 0 ? "failed" : "partial",
    attempted,
    deleted,
    failed,
    recordRequired: true,
  };
}
