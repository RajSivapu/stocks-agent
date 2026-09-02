import { createClient } from "npm:@supabase/supabase-js@2.112.4";

import { createGatewayHandler } from "./_shared/handler.ts";
import { fetchVerifiedQuote } from "./_shared/market-data.ts";
import { createSupabaseGatewayRepository } from "./_shared/repository.ts";
import { sendTelegramParts } from "./_shared/telegram.ts";

function requiredEnvironment(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error(`missing required environment variable: ${name}`);
  return value;
}

const supabase = createClient(
  requiredEnvironment("SUPABASE_URL"),
  requiredEnvironment("SUPABASE_SERVICE_ROLE_KEY"),
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } },
);

const handler = createGatewayHandler({
  repository: createSupabaseGatewayRepository(supabase),
  marketAgentSecret: requiredEnvironment("MARKET_AGENT_SECRET"),
  telegramToken: requiredEnvironment("TELEGRAM_BOT_TOKEN"),
  telegramChatId: requiredEnvironment("TELEGRAM_OWNER_CHAT_ID"),
  fetchQuote: (ticker, now) => fetchVerifiedQuote(ticker, fetch, now),
  sendTelegram: (parts, chatId, token) => sendTelegramParts(parts, chatId, token, fetch),
});

Deno.serve(handler);
