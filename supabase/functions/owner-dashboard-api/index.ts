import { createRemoteJWKSet } from "jose";

import { verifyOwnerRequest } from "./auth.ts";
import { parseAllowedOrigins } from "./cors.ts";
import { createOwnerDashboardHandler, type DashboardReader } from "./handler.ts";
import { createDashboardRepository } from "./repository.ts";

function environment(name: string): string {
  return Deno.env.get(name)?.trim() ?? "";
}

const projectUrl = environment("SUPABASE_URL");
const ownerUserId = environment("DASHBOARD_OWNER_USER_ID");
const databaseUrl = environment("DASHBOARD_DATABASE_URL");
const ownerPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
let allowedOrigins: string[] = [];
let jwks: ReturnType<typeof createRemoteJWKSet> | null = null;
let repository: DashboardReader | null = null;
try {
  allowedOrigins = parseAllowedOrigins(environment("DASHBOARD_ALLOWED_ORIGINS"));
  const parsedProject = new URL(projectUrl);
  if (parsedProject.protocol !== "https:" || parsedProject.origin !== projectUrl || !parsedProject.hostname.endsWith(".supabase.co")) {
    throw new Error("invalid Supabase project URL");
  }
  jwks = createRemoteJWKSet(new URL(`${parsedProject.origin}/auth/v1/.well-known/jwks.json`));
  if (ownerPattern.test(ownerUserId)) repository = createDashboardRepository(databaseUrl);
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
  repository: repository ?? unavailableRepository,
});

Deno.serve(handler);
