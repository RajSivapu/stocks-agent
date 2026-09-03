import { parseCommandRequest } from "../../../packages/contracts/src/command-preview.ts";

import { HttpError } from "../_shared/errors.ts";


const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA_RE = /^[0-9a-f]{64}$/;

export type AppRoute = {
  method: "GET" | "PATCH" | "POST";
  path: string;
  key: string;
  body: "none" | "command" | "confirmation" | "object";
};

export const APP_ROUTES: readonly AppRoute[] = [
  { method: "POST", path: "/portfolio/preview", key: "portfolio_preview", body: "command" },
  { method: "POST", path: "/portfolio/confirm", key: "portfolio_confirm", body: "confirmation" },
  { method: "POST", path: "/portfolio/correction/preview", key: "correction_preview", body: "command" },
  { method: "POST", path: "/portfolio/correction/confirm", key: "correction_confirm", body: "confirmation" },
  { method: "POST", path: "/plans/preview", key: "plan_preview", body: "command" },
  { method: "POST", path: "/plans/confirm", key: "plan_confirm", body: "confirmation" },
  { method: "POST", path: "/telegram/pairing-code", key: "telegram_pairing", body: "object" },
  { method: "POST", path: "/connections/create", key: "connection_create", body: "object" },
  { method: "POST", path: "/connections/handshake", key: "connection_handshake", body: "object" },
  { method: "POST", path: "/connections/activate", key: "connection_activate", body: "object" },
  { method: "POST", path: "/connections/revoke", key: "connection_revoke", body: "object" },
  { method: "GET", path: "/settings", key: "settings_read", body: "none" },
  { method: "PATCH", path: "/settings", key: "settings_update", body: "object" },
  { method: "GET", path: "/export", key: "export", body: "none" },
  { method: "POST", path: "/account/delete/request", key: "account_delete_request", body: "object" },
  { method: "POST", path: "/account/delete/confirm", key: "account_delete_confirm", body: "object" },
] as const;

function exactKeys(row: Record<string, unknown>, required: readonly string[]): void {
  if (Object.keys(row).length !== required.length || required.some((key) => !Object.hasOwn(row, key))) {
    throw new HttpError(400, "INVALID_REQUEST", "request fields are invalid");
  }
}

function uuidField(row: Record<string, unknown>, field: string): void {
  if (typeof row[field] !== "string" || !UUID_RE.test(row[field])) {
    throw new HttpError(400, "INVALID_REQUEST", `${field} is invalid`);
  }
}

function validateObjectRoute(route: AppRoute, body: Record<string, unknown>): void {
  if (route.key === "telegram_pairing" || route.key === "account_delete_request") {
    exactKeys(body, []);
    return;
  }
  if (route.key === "connection_create") {
    exactKeys(body, ["provider", "consent_version"]);
    if (body.provider !== "claude") throw new HttpError(400, "INVALID_REQUEST", "provider is invalid");
    if (typeof body.consent_version !== "string" || !/^[a-z0-9][a-z0-9.-]{2,99}$/.test(body.consent_version)) {
      throw new HttpError(400, "INVALID_REQUEST", "consent version is invalid");
    }
    return;
  }
  if (route.key === "connection_handshake") {
    exactKeys(body, ["connection_id", "trigger_url", "trigger_token"]);
    uuidField(body, "connection_id");
    if (typeof body.trigger_url !== "string" || body.trigger_url.length > 300 ||
      typeof body.trigger_token !== "string" || body.trigger_token.length < 24 ||
      body.trigger_token.length > 500 || /\s/.test(body.trigger_token)) {
      throw new HttpError(400, "INVALID_REQUEST", "trigger credential is invalid");
    }
    return;
  }
  if (route.key === "connection_activate" || route.key === "connection_revoke") {
    exactKeys(body, ["connection_id"]);
    uuidField(body, "connection_id");
    return;
  }
  if (route.key === "account_delete_confirm") {
    exactKeys(body, ["confirmation_id", "otp"]);
    uuidField(body, "confirmation_id");
    if (typeof body.otp !== "string" || !/^\d{6}$/.test(body.otp)) {
      throw new HttpError(400, "INVALID_REQUEST", "OTP is invalid");
    }
    return;
  }
  if (route.key === "settings_update") {
    const allowed = new Set([
      "display_name", "timezone", "notify_pre_market", "notify_intraday",
      "notify_post_market", "notify_operational", "schedule_pre_market",
      "schedule_intraday", "schedule_post_market",
    ]);
    const keys = Object.keys(body);
    if (keys.length === 0 || keys.some((key) => !allowed.has(key))) {
      throw new HttpError(400, "INVALID_REQUEST", "settings fields are invalid");
    }
    for (const key of keys) {
      const value = body[key];
      if (key === "display_name") {
        if (typeof value !== "string" || value.trim().length < 1 || value.length > 120) {
          throw new HttpError(400, "INVALID_REQUEST", "display name is invalid");
        }
      } else if (key === "timezone") {
        if (typeof value !== "string" || !/^[A-Za-z_]+(?:\/[A-Za-z0-9_+.-]+)+$/.test(value) || value.length > 100) {
          throw new HttpError(400, "INVALID_REQUEST", "timezone is invalid");
        }
      } else if (typeof value !== "boolean") {
        throw new HttpError(400, "INVALID_REQUEST", "notification setting is invalid");
      }
    }
    return;
  }
  throw new HttpError(400, "INVALID_REQUEST", "request is invalid");
}

export function resolveRoute(method: string, pathname: string): AppRoute {
  const pathMatches = APP_ROUTES.filter((candidate) => candidate.path === pathname);
  if (pathMatches.length === 0) throw new HttpError(404, "NOT_FOUND", "route not found");
  const route = pathMatches.find((candidate) => candidate.method === method);
  if (!route) throw new HttpError(405, "METHOD_NOT_ALLOWED", "method not allowed");
  return route;
}

export function validateRouteBody(
  route: AppRoute,
  body: Record<string, unknown>,
): Record<string, unknown> {
  if (route.body === "none") {
    exactKeys(body, []);
    return body;
  }
  if (route.body === "command") {
    let parsed;
    try {
      parsed = parseCommandRequest(body);
    } catch (_error) {
      throw new HttpError(400, "INVALID_REQUEST", "command is invalid");
    }
    const operation = parsed.command.operation;
    if (route.key === "correction_preview" && operation !== "correct_transaction") {
      throw new HttpError(400, "INVALID_REQUEST", "correction command is required");
    }
    if (route.key === "plan_preview" && !["plan", "cancel_plan"].includes(operation)) {
      throw new HttpError(400, "INVALID_REQUEST", "plan command is required");
    }
    if (route.key === "portfolio_preview" && !["buy", "sell", "sell_all", "stop"].includes(operation)) {
      throw new HttpError(400, "INVALID_REQUEST", "portfolio command is required");
    }
    return parsed as unknown as Record<string, unknown>;
  }
  if (route.body === "confirmation") {
    exactKeys(body, ["command_id", "preview_digest"]);
    if (typeof body.command_id !== "string" || !UUID_RE.test(body.command_id) ||
      typeof body.preview_digest !== "string" || !SHA_RE.test(body.preview_digest)) {
      throw new HttpError(400, "INVALID_REQUEST", "confirmation is invalid");
    }
    return {
      command_id: body.command_id.toLowerCase(),
      preview_digest: body.preview_digest,
    };
  }
  validateObjectRoute(route, body);
  return body;
}
