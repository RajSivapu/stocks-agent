import { withDatabase, type RpcClient } from "../_shared/postgres.ts";
import type { FireReceipt } from "./claude-fire.ts";

export type ClaimedSlot = {
  slot_id: string;
  trigger_request_id: string;
  phase: "pre-market" | "intraday" | "post-market";
  market_date: string;
  holiday: boolean;
  attempt_id: string | null;
};

export type TriggerSecret = { endpoint: string; token: string; trigger_request_id: string };

export interface SchedulerRepository {
  claimDueSlots(now: string, limit: number): Promise<ClaimedSlot[]>;
  readTriggerSecret(attemptId: string): Promise<TriggerSecret>;
  recordTriggerResult(attemptId: string, receipt: FireReceipt): Promise<Record<string, unknown>>;
  publishHoliday(slotId: string): Promise<Record<string, unknown>>;
}

type DatabaseRunner = <T>(url: string, callback: (client: RpcClient) => Promise<T>) => Promise<T>;

function rows(value: Record<string, unknown>): ClaimedSlot[] {
  return Array.isArray(value.slots) ? value.slots as ClaimedSlot[] : [];
}

export function createSchedulerRepository(
  databaseUrl: string,
  database: DatabaseRunner = withDatabase,
): SchedulerRepository {
  return {
    claimDueSlots(now, limit) {
      return database(databaseUrl, async (client) => rows(await client.callJsonRpc(
        "scheduler_claim_due_slots", { now, limit },
      )));
    },
    readTriggerSecret(attemptId) {
      return database(databaseUrl, (client) => client.callJsonRpc(
        "scheduler_read_trigger_secret", { attempt_id: attemptId },
      ) as Promise<TriggerSecret>);
    },
    recordTriggerResult(attemptId, receipt) {
      return database(databaseUrl, (client) => client.callJsonRpc("scheduler_record_trigger_result", {
        attempt_id: attemptId,
        status: receipt.status,
        response_status: receipt.responseStatus,
        session_url: receipt.sessionUrl,
        response_digest: receipt.responseDigest,
      }));
    },
    publishHoliday(slotId) {
      return database(databaseUrl, (client) => client.callJsonRpc(
        "scheduler_publish_holiday", { slot_id: slotId },
      ));
    },
  };
}
