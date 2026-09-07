import { createClient } from "npm:@supabase/supabase-js@2.112.4";
import { createRemoteJWKSet } from "npm:jose@6.2.2";

import { verifyOwnerRequest } from "../owner-dashboard-api/auth.ts";
import { createGatewayHandler } from "./_shared/handler.ts";
import { fetchVerifiedQuote } from "./_shared/market-data.ts";
import { createSupabaseGatewayRepository } from "./_shared/repository.ts";
import { sendTelegramParts } from "./_shared/telegram.ts";

function requiredEnvironment(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error(`missing required environment variable: ${name}`);
  return value;
}

const projectUrl = requiredEnvironment("SUPABASE_URL");
const ownerUserId = requiredEnvironment("DASHBOARD_OWNER_USER_ID");
const jwks = createRemoteJWKSet(
  new URL(`${new URL(projectUrl).origin}/auth/v1/.well-known/jwks.json`),
);

const supabase = createClient(
  projectUrl,
  requiredEnvironment("SUPABASE_SERVICE_ROLE_KEY"),
  {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
  },
);

const handler = createGatewayHandler({
  repository: createSupabaseGatewayRepository(supabase),
  marketAgentSecret: requiredEnvironment("MARKET_AGENT_SECRET"),
  telegramToken: requiredEnvironment("TELEGRAM_BOT_TOKEN"),
  telegramChatId: requiredEnvironment("TELEGRAM_OWNER_CHAT_ID"),
  dashboardBaseUrl: requiredEnvironment("OWNER_DASHBOARD_URL"),
  dashboardAllowedOrigins: [requiredEnvironment("OWNER_DASHBOARD_ORIGIN")],
  ownerUserId,
  verifyOwner: (request) =>
    verifyOwnerRequest(
      request,
      jwks,
      ownerUserId,
      projectUrl,
    ),
  fetchQuote: (ticker, now) => fetchVerifiedQuote(ticker, fetch, now),
  sendTelegram: (parts, chatId, token) =>
    sendTelegramParts(parts, chatId, token, fetch),
});

Deno.serve(handler);
