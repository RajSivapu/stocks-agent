import {
  assertEquals,
} from "https://deno.land/std@0.224.0/assert/mod.ts";

import type { RpcClient } from "../_shared/postgres.ts";
import { createTelegramPortfolioRepository } from "./repository.ts";


Deno.test("Telegram repository exposes only its ten allow-listed machine RPCs", async () => {
  const calls: Array<{ name: string; value: Record<string, unknown> }> = [];
  const runner = async <T>(
    _databaseUrl: string,
    callback: (client: RpcClient) => Promise<T>,
  ): Promise<T> => await callback({
    callJsonRpc(name, value) {
      calls.push({ name, value });
      return Promise.resolve({ ok: true });
    },
  });
  const repository = createTelegramPortfolioRepository("opaque-database-url", runner);
  const input = { chat_id: 123, user_id: 123 };
  await repository.resolveLink(input);
  await repository.claimUpdate(input);
  await repository.consumePairing(input);
  await repository.prepareCommand(input);
  await repository.applyCallback(input);
  await repository.unlink(input);
  await repository.portfolio(input);
  await repository.plans(input);
  await repository.recordDelivery(input);
  await repository.recordPairingDelivery(input);
  assertEquals(calls.map((call) => call.name), [
    "telegram_resolve_link",
    "telegram_claim_update",
    "telegram_consume_pairing",
    "telegram_prepare_command",
    "telegram_apply_callback",
    "telegram_unlink",
    "telegram_portfolio",
    "telegram_plans",
    "telegram_record_delivery",
    "telegram_record_pairing_delivery",
  ]);
  assertEquals(JSON.stringify(calls).includes("bot"), false);
  assertEquals(JSON.stringify(calls).includes("owner_id"), false);
});
