import { DashboardHttpError } from "./errors.ts";

export type DashboardRouteName =
  | "meta"
  | "today"
  | "portfolio"
  | "transactions"
  | "ideas"
  | "companion"
  | "alerts"
  | "runs"
  | "runDetail"
  | "system";

export interface DashboardRoute {
  name: DashboardRouteName;
  cursor?: string;
  status?: string;
  state?: string;
  kind?: string;
  id?: string;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const IDEA_STATUSES = new Set(["approved", "downgraded", "vetoed", "legacy_unverified"]);
const ALERT_STATES = new Set(["ready", "sending", "delivered", "delivery_failed", "delivery_unknown", "suppressed", "incomplete"]);
const RUN_KINDS = new Set(["pre-market", "intraday", "post-market", "on-demand", "weekly-audit"]);

function optionalBounded(
  parameters: URLSearchParams,
  name: string,
  allowed?: ReadonlySet<string>,
): string | undefined {
  const values = parameters.getAll(name);
  if (values.length > 1) {
    throw new DashboardHttpError(400, "invalid_request", `Invalid ${name}.`);
  }
  const value = values[0];
  if (value === undefined) return undefined;
  if (
    value.length < 1 || value.length > 512 ||
    (name === "cursor" && !/^[A-Za-z0-9_-]{8,512}$/.test(value)) ||
    (allowed && !allowed.has(value))
  ) {
    throw new DashboardHttpError(400, "invalid_request", `Invalid ${name}.`);
  }
  return value;
}

function rejectUnknownParameters(parameters: URLSearchParams, allowed: readonly string[]): void {
  for (const key of parameters.keys()) {
    if (!allowed.includes(key)) {
      throw new DashboardHttpError(400, "invalid_request", "Unsupported query parameter.");
    }
  }
}

export function resolveDashboardRoute(
  method: string,
  pathname: string,
  parameters: URLSearchParams,
): DashboardRoute {
  if (method !== "GET") {
    throw new DashboardHttpError(405, "invalid_request", "Only GET requests are available.");
  }
  const marker = pathname.indexOf("/v1/");
  const path = marker >= 0 ? pathname.slice(marker) : pathname;
  const simple = new Map<string, DashboardRouteName>([
    ["/v1/meta", "meta"],
    ["/v1/today", "today"],
    ["/v1/portfolio", "portfolio"],
    ["/v1/companion", "companion"],
    ["/v1/system", "system"],
  ]);
  const name = simple.get(path);
  if (name) {
    rejectUnknownParameters(parameters, []);
    return { name };
  }
  if (path === "/v1/transactions") {
    rejectUnknownParameters(parameters, ["cursor"]);
    return { name: "transactions", cursor: optionalBounded(parameters, "cursor") };
  }
  if (path === "/v1/ideas") {
    rejectUnknownParameters(parameters, ["status", "cursor"]);
    return {
      name: "ideas",
      status: optionalBounded(parameters, "status", IDEA_STATUSES),
      cursor: optionalBounded(parameters, "cursor"),
    };
  }
  if (path === "/v1/alerts") {
    rejectUnknownParameters(parameters, ["state", "cursor"]);
    return {
      name: "alerts",
      state: optionalBounded(parameters, "state", ALERT_STATES),
      cursor: optionalBounded(parameters, "cursor"),
    };
  }
  if (path === "/v1/runs") {
    rejectUnknownParameters(parameters, ["kind", "cursor"]);
    return {
      name: "runs",
      kind: optionalBounded(parameters, "kind", RUN_KINDS),
      cursor: optionalBounded(parameters, "cursor"),
    };
  }
  const runMatch = /^\/v1\/runs\/([^/]+)$/.exec(path);
  if (runMatch) {
    rejectUnknownParameters(parameters, []);
    if (!UUID_PATTERN.test(runMatch[1] ?? "")) {
      throw new DashboardHttpError(400, "invalid_request", "Invalid run identifier.");
    }
    return { name: "runDetail", id: runMatch[1] };
  }
  throw new DashboardHttpError(404, "not_found", "Dashboard route not found.");
}
