import type { Freshness, MarketState } from "../../../packages/dashboard-contracts/src/index.ts";

import { corsHeaders, preflightResponse, requireAllowedOrigin } from "./cors.ts";
import { DashboardHttpError, errorEnvelope } from "./errors.ts";
import { resolveDashboardRoute, type DashboardRoute } from "./routes.ts";

export interface DashboardReadResult {
  data: object;
  dataAsOf: string | null;
  freshness: Freshness;
  marketState: MarketState;
  nextCursor?: string | null;
}

export interface DashboardReader {
  read(route: DashboardRoute): Promise<DashboardReadResult>;
}

export interface DashboardHandlerDependencies {
  ownerUserId: string;
  projectUrl: string;
  allowedOrigins: readonly string[];
  verifyOwner(request: Request): Promise<{ subject: string }>;
  repository: DashboardReader;
  now?: () => Date;
  requestId?: () => string;
}

const OWNER_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function baseHeaders(origin: string | null): Headers {
  const headers = origin ? corsHeaders(origin) : new Headers();
  headers.set("content-type", "application/json; charset=utf-8");
  headers.set("cache-control", "no-store");
  headers.set("x-content-type-options", "nosniff");
  return headers;
}

function jsonResponse(body: unknown, status: number, origin: string | null): Response {
  return new Response(JSON.stringify(body), { status, headers: baseHeaders(origin) });
}

export function createOwnerDashboardHandler(
  dependencies: DashboardHandlerDependencies,
): (request: Request) => Promise<Response> {
  const now = dependencies.now ?? (() => new Date());
  const requestId = dependencies.requestId ?? crypto.randomUUID;

  return async (request: Request): Promise<Response> => {
    const id = requestId();
    let origin: string | null = null;
    try {
      if (request.method === "OPTIONS") {
        return preflightResponse(request, dependencies.allowedOrigins);
      }
      origin = requireAllowedOrigin(request, dependencies.allowedOrigins);
      if (!OWNER_PATTERN.test(dependencies.ownerUserId) || dependencies.allowedOrigins.length === 0) {
        throw new DashboardHttpError(
          503,
          "temporarily_unavailable",
          "Dashboard access is unavailable.",
        );
      }
      try {
        await dependencies.verifyOwner(request);
      } catch (cause) {
        if (cause instanceof DashboardHttpError) throw cause;
        throw new DashboardHttpError(401, "unauthorized", "Your session is invalid or expired.");
      }
      const url = new URL(request.url);
      const route = resolveDashboardRoute(request.method, url.pathname, url.searchParams);
      const result = await dependencies.repository.read(route);
      const envelope = {
        contract_version: 1 as const,
        request_id: id,
        generated_at: now().toISOString(),
        data_as_of: result.dataAsOf,
        freshness: result.freshness,
        market_state: result.marketState,
        data: result.data,
        ...(result.nextCursor !== undefined ? { next_cursor: result.nextCursor } : {}),
      };
      return jsonResponse(envelope, 200, origin);
    } catch (cause) {
      const error = cause instanceof DashboardHttpError
        ? cause
        : new DashboardHttpError(503, "temporarily_unavailable", "Dashboard data is temporarily unavailable.");
      return jsonResponse(errorEnvelope(id, error), error.status, origin);
    }
  };
}
