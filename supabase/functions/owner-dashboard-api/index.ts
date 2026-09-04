import { createRemoteJWKSet } from "jose";

import { verifyOwnerRequest } from "./auth.ts";
import { parseAllowedOrigins } from "./cors.ts";
import { createOwnerDashboardHandler, type DashboardReader } from "./handler.ts";

function environment(name: string): string {
  return Deno.env.get(name)?.trim() ?? "";
}

const projectUrl = environment("SUPABASE_URL");
const ownerUserId = environment("DASHBOARD_OWNER_USER_ID");
let allowedOrigins: string[] = [];
let jwks: ReturnType<typeof createRemoteJWKSet> | null = null;
try {
  allowedOrigins = parseAllowedOrigins(environment("DASHBOARD_ALLOWED_ORIGINS"));
  jwks = createRemoteJWKSet(new URL(`${projectUrl}/auth/v1/.well-known/jwks.json`));
} catch {
  // The handler's owner-configuration kill switch returns a bounded 503.
}

const unavailableRepository: DashboardReader = {
  read: async () => {
    throw new Error("dashboard repository has not been configured");
  },
};

const handler = createOwnerDashboardHandler({
  ownerUserId,
  projectUrl,
  allowedOrigins,
  verifyOwner: (request) => {
    if (!jwks) throw new Error("owner verifier unavailable");
    return verifyOwnerRequest(request, jwks, ownerUserId, projectUrl);
  },
  repository: unavailableRepository,
});

Deno.serve(handler);
