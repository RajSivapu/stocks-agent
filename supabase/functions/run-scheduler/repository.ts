import { type RpcClient, withDatabase } from "../_shared/postgres.ts";
import type { FireReceipt } from "./claude-fire.ts";
import type {
  DueDecision,
  OutcomeGrade,
} from "../market-briefing-gateway/_shared/outcomes.ts";

export type ClaimedSlot = {
  slot_id: string;
  trigger_request_id: string;
  phase: "pre-market" | "intraday" | "post-market" | "on-demand";
  market_date: string;
  holiday: boolean;
  attempt_id: string | null;
};

export type TriggerSecret = {
  endpoint: string;
  token: string;
  trigger_request_id: string;
};
export type ClaimedPublication = {
  publication_id: string;
  lease_token: string;
  chat_id: string;
  parts: string[];
};
export type ClaimedOperationalAlert = {
  alert_id: string;
  lease_token: string;
  chat_id: string;
  code: string;
};
export type DeliveryStatus =
  | "delivered"
  | "delivery_failed"
  | "delivery_unknown";
export type OwnerDueDecision = DueDecision & { owner_id: string };
export type OwnerOutcomeGrade = OutcomeGrade & { owner_id: string };

export interface SchedulerRepository {
  claimDueSlots(now: string, limit: number): Promise<ClaimedSlot[]>;
  readTriggerSecret(attemptId: string): Promise<TriggerSecret>;
  recordTriggerResult(
    attemptId: string,
    receipt: FireReceipt,
  ): Promise<Record<string, unknown>>;
  publishHoliday(slotId: string): Promise<Record<string, unknown>>;
  runMaintenance(now: string): Promise<Record<string, unknown>>;
  claimPublications(now: string, limit: number): Promise<ClaimedPublication[]>;
  finishPublication(
    publicationId: string,
    leaseToken: string,
    status: DeliveryStatus,
    messageIds: number[],
  ): Promise<Record<string, unknown>>;
  claimOperationalAlerts(
    now: string,
    limit: number,
  ): Promise<ClaimedOperationalAlert[]>;
  finishOperationalAlert(
    alertId: string,
    leaseToken: string,
    status: DeliveryStatus,
    messageIds: number[],
  ): Promise<Record<string, unknown>>;
  readDueDecisions(now: string, limit: number): Promise<OwnerDueDecision[]>;
  applyOutcomeGrades(
    grades: OwnerOutcomeGrade[],
  ): Promise<Record<string, unknown>>;
}

type DatabaseRunner = <T>(
  url: string,
  callback: (client: RpcClient) => Promise<T>,
) => Promise<T>;

function rows(value: Record<string, unknown>): ClaimedSlot[] {
  return Array.isArray(value.slots) ? value.slots as ClaimedSlot[] : [];
}

function publications(value: Record<string, unknown>): ClaimedPublication[] {
  return Array.isArray(value.publications)
    ? value.publications as ClaimedPublication[]
    : [];
}

function alerts(value: Record<string, unknown>): ClaimedOperationalAlert[] {
  return Array.isArray(value.alerts)
    ? value.alerts as ClaimedOperationalAlert[]
    : [];
}

function dueDecisions(value: Record<string, unknown>): OwnerDueDecision[] {
  return Array.isArray(value.due) ? value.due as OwnerDueDecision[] : [];
}

export function createSchedulerRepository(
  databaseUrl: string,
  database: DatabaseRunner = withDatabase,
): SchedulerRepository {
  return {
    claimDueSlots(now, limit) {
      return database(
        databaseUrl,
        async (client) =>
          rows(
            await client.callJsonRpc(
              "scheduler_claim_due_slots",
              { now, limit },
            ),
          ),
      );
    },
    readTriggerSecret(attemptId) {
      return database(databaseUrl, (client) =>
        client.callJsonRpc(
          "scheduler_read_trigger_secret",
          { attempt_id: attemptId },
        ) as Promise<TriggerSecret>);
    },
    recordTriggerResult(attemptId, receipt) {
      return database(
        databaseUrl,
        (client) =>
          client.callJsonRpc("scheduler_record_trigger_result", {
            attempt_id: attemptId,
            status: receipt.status,
            response_status: receipt.responseStatus,
            session_url: receipt.sessionUrl,
            response_digest: receipt.responseDigest,
          }),
      );
    },
    publishHoliday(slotId) {
      return database(databaseUrl, (client) =>
        client.callJsonRpc(
          "scheduler_publish_holiday",
          { slot_id: slotId },
        ));
    },
    runMaintenance(now) {
      return database(databaseUrl, (client) =>
        client.callJsonRpc(
          "scheduler_run_maintenance",
          { now },
        ));
    },
    claimPublications(now, limit) {
      return database(
        databaseUrl,
        async (client) =>
          publications(
            await client.callJsonRpc(
              "scheduler_claim_publications",
              { now, limit },
            ),
          ),
      );
    },
    finishPublication(publicationId, leaseToken, status, messageIds) {
      return database(databaseUrl, (client) =>
        client.callJsonRpc(
          "scheduler_finish_publication",
          {
            publication_id: publicationId,
            lease_token: leaseToken,
            status,
            message_ids: messageIds,
          },
        ));
    },
    claimOperationalAlerts(now, limit) {
      return database(
        databaseUrl,
        async (client) =>
          alerts(
            await client.callJsonRpc(
              "scheduler_claim_operational_alerts",
              { now, limit },
            ),
          ),
      );
    },
    finishOperationalAlert(alertId, leaseToken, status, messageIds) {
      return database(databaseUrl, (client) =>
        client.callJsonRpc(
          "scheduler_finish_operational_alert",
          {
            alert_id: alertId,
            lease_token: leaseToken,
            status,
            message_ids: messageIds,
          },
        ));
    },
    readDueDecisions(now, limit) {
      return database(
        databaseUrl,
        async (client) =>
          dueDecisions(
            await client.callJsonRpc(
              "scheduler_read_due_decisions",
              { now, limit },
            ),
          ),
      );
    },
    applyOutcomeGrades(grades) {
      return database(databaseUrl, (client) =>
        client.callJsonRpc(
          "scheduler_apply_outcome_grades",
          { grades },
        ));
    },
  };
}
