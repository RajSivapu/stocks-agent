import { createTelegramPortfolioHandler } from "./handler.ts";
import { createTelegramPortfolioRepository } from "./repository.ts";
import { createTelegramSender } from "./telegram-client.ts";


function requiredEnvironment(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error(`missing required environment variable: ${name}`);
  return value;
}

const handler = createTelegramPortfolioHandler({
  webhookSecret: requiredEnvironment("TELEGRAM_WEBHOOK_SECRET"),
  pairingHashPepper: requiredEnvironment("TELEGRAM_PAIRING_PEPPER"),
  repository: createTelegramPortfolioRepository(requiredEnvironment("TELEGRAM_DATABASE_URL")),
  sendTelegram: createTelegramSender(requiredEnvironment("TELEGRAM_BOT_TOKEN")),
});

Deno.serve(handler);
