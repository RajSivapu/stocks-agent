import {
  parseDashboardEnvelope,
  parseDashboardErrorEnvelope,
  type DashboardEnvelope,
} from "@stocks-agent/dashboard-contracts";

type Fetcher = (input: string, init: RequestInit) => Promise<Response>;

const PATH = /^\/v1\/(?:meta|today|portfolio|transactions|ideas|companion|alerts|runs(?:\/[0-9a-f-]{36})?|system)(?:\?[A-Za-z0-9_%=&-]{0,600})?$/;

export class DashboardApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
    this.name = "DashboardApiError";
  }
}

export function createDashboardClient(
  baseUrl: string,
  fetcher: Fetcher = fetch,
  timeoutMs = 8_000,
) {
  const base = new URL(baseUrl);
  if (base.protocol !== "https:" || base.pathname !== "/functions/v1/owner-dashboard-api" || base.search || base.hash) {
    throw new Error("invalid dashboard API URL");
  }
  return {
    async get<T>(path: string, token: string): Promise<DashboardEnvelope<T>> {
      if (!PATH.test(path) || path.includes("..")) throw new Error("invalid dashboard API path");
      if (!token || token.length > 4_096) throw new Error("invalid session token");
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), timeoutMs);
      let response: Response;
      try {
        response = await fetcher(`${base.origin}${base.pathname}${path}`, {
          method: "GET",
          headers: { authorization: `Bearer ${token}`, accept: "application/json" },
          cache: "no-store",
          credentials: "omit",
          redirect: "error",
          signal: controller.signal,
        });
      } catch (error) {
        if (controller.signal.aborted) throw new Error("Dashboard request timed out.", { cause: error });
        throw error;
      } finally {
        clearTimeout(timeout);
      }
      const declared = Number(response.headers.get("content-length") ?? "0");
      if (declared > 1_048_576) throw new Error("Dashboard response is too large.");
      const raw = await response.text();
      if (raw.length > 1_048_576) throw new Error("Dashboard response is too large.");
      let value: unknown;
      try {
        value = JSON.parse(raw);
      } catch {
        throw new Error("Dashboard returned an invalid response.");
      }
      if (!response.ok) {
        const parsed = parseDashboardErrorEnvelope(value);
        throw new DashboardApiError(response.status, parsed.error.code, parsed.error.message);
      }
      return parseDashboardEnvelope<T>(value);
    },
  };
}

export function createBrowserDashboardClient() {
  const url = import.meta.env.VITE_DASHBOARD_API_URL?.trim();
  if (!url) throw new Error("Dashboard API configuration is unavailable.");
  return createDashboardClient(url);
}
