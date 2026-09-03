import { createRunSchedulerHandler } from "./handler.ts";
import { createSchedulerRepository } from "./repository.ts";

function requiredEnvironment(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error(`missing required environment variable: ${name}`);
  return value;
}

Deno.serve(createRunSchedulerHandler({
  repository: createSchedulerRepository(
    requiredEnvironment("SCHEDULER_DATABASE_URL"),
  ),
  schedulerSecret: requiredEnvironment("SCHEDULER_WEBHOOK_SECRET"),
  telegramToken: requiredEnvironment("TELEGRAM_BOT_TOKEN"),
}));
