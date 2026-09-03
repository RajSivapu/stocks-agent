import { withDatabase, type RpcClient } from "../_shared/postgres.ts";
import type { TelegramPortfolioRepository } from "./handler.ts";


type DatabaseRunner = <T>(
  databaseUrl: string,
  callback: (client: RpcClient) => Promise<T>,
) => Promise<T>;

export function createTelegramPortfolioRepository(
  databaseUrl: string,
  database: DatabaseRunner = withDatabase,
): TelegramPortfolioRepository {
  const call = (name: Parameters<RpcClient["callJsonRpc"]>[0], value: Record<string, unknown>) =>
    database(databaseUrl, (client) => client.callJsonRpc(name, value));
  return {
    resolveLink: (value) => call("telegram_resolve_link", value),
    claimUpdate: (value) => call("telegram_claim_update", value),
    consumePairing: (value) => call("telegram_consume_pairing", value),
    prepareCommand: (value) => call("telegram_prepare_command", value),
    applyCallback: (value) => call("telegram_apply_callback", value),
    unlink: (value) => call("telegram_unlink", value),
    portfolio: (value) => call("telegram_portfolio", value),
    plans: (value) => call("telegram_plans", value),
    recordDelivery: (value) => call("telegram_record_delivery", value),
    recordPairingDelivery: (value) => call("telegram_record_pairing_delivery", value),
  };
}
