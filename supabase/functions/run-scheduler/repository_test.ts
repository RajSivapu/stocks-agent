import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import type { RpcClient } from "../_shared/postgres.ts";
import { createSchedulerRepository } from "./repository.ts";

Deno.test("scheduler repository exposes only eleven reviewed scheduler RPCs", async () => {
  const calls: Array<{ name: string; args: Record<string, unknown> }> = [];
  const runner = async <T>(
    _url: string,
    callback: (client: RpcClient) => Promise<T>,
  ): Promise<T> =>
    await callback({
      callJsonRpc(name, args) {
        calls.push({ name, args });
        if (name === "scheduler_claim_due_slots") {
          return Promise.resolve({ slots: [] });
        }
        return Promise.resolve({
          endpoint:
            "https://api.anthropic.com/v1/claude_code/routines/trig_ABCDEF/fire",
          token: "opaque",
          trigger_request_id: crypto.randomUUID(),
        });
      },
    });
  const repository = createSchedulerRepository("opaque-database-url", runner);
  await repository.claimDueSlots("2026-09-03T11:31:00.000Z", 10);
  await repository.readTriggerSecret("11111111-1111-4111-8111-111111111111");
  await repository.recordTriggerResult("11111111-1111-4111-8111-111111111111", {
    status: "trigger_unknown",
    responseStatus: null,
    sessionUrl: null,
    responseDigest: "a".repeat(64),
  });
  await repository.publishHoliday("22222222-2222-4222-8222-222222222222");
  await repository.runMaintenance("2026-09-03T12:00:00.000Z");
  await repository.claimPublications("2026-09-03T12:00:00.000Z", 10);
  await repository.finishPublication(
    "33333333-3333-4333-8333-333333333333",
    "44444444-4444-4444-8444-444444444444",
    "delivered",
    [1],
  );
  await repository.claimOperationalAlerts("2026-09-03T12:00:00.000Z", 10);
  await repository.finishOperationalAlert(
    "55555555-5555-4555-8555-555555555555",
    "66666666-6666-4666-8666-666666666666",
    "delivery_unknown",
    [],
  );
  await repository.readDueDecisions("2026-09-03T12:00:00.000Z", 10);
  await repository.applyOutcomeGrades([]);
  assertEquals(calls.map((call) => call.name), [
    "scheduler_claim_due_slots",
    "scheduler_read_trigger_secret",
    "scheduler_record_trigger_result",
    "scheduler_publish_holiday",
    "scheduler_run_maintenance",
    "scheduler_claim_publications",
    "scheduler_finish_publication",
    "scheduler_claim_operational_alerts",
    "scheduler_finish_operational_alert",
    "scheduler_read_due_decisions",
    "scheduler_apply_outcome_grades",
  ]);
});
