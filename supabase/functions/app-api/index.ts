import { createAppApiHandler } from "./handler.ts";
import { ProjectJwksCache, verifyUserJwt } from "../_shared/jwt.ts";
import { createAppApiRepository } from "./repository.ts";


function requiredEnvironment(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error(`missing required environment variable: ${name}`);
  return value;
}

const projectUrl = requiredEnvironment("SUPABASE_URL");
const jwks = new ProjectJwksCache(projectUrl);
const handler = createAppApiHandler({
  allowedOrigins: requiredEnvironment("APP_ALLOWED_ORIGINS").split(",").map((value) => value.trim()),
  ipHashPepper: requiredEnvironment("APP_RATE_LIMIT_PEPPER"),
  pairingHashPepper: requiredEnvironment("TELEGRAM_PAIRING_PEPPER"),
  repository: createAppApiRepository(projectUrl, requiredEnvironment("SUPABASE_ANON_KEY")),
  authenticate: async (request) => verifyUserJwt(request, await jwks.get(), { projectUrl }),
});

Deno.serve(handler);
