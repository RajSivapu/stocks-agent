import { createAgentGatewayHandler } from "./handler.ts";
import { createAgentGatewayRepository } from "./repository.ts";

function requiredEnvironment(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error(`missing required environment variable: ${name}`);
  return value;
}

Deno.serve(createAgentGatewayHandler({
  repository: createAgentGatewayRepository(requiredEnvironment("AGENT_DATABASE_URL")),
}));
