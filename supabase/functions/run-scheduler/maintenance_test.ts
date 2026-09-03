import {
  assertEquals,
  assertRejects,
} from "https://deno.land/std@0.224.0/assert/mod.ts";
import { TelegramDeliveryError } from "../market-briefing-gateway/_shared/telegram.ts";
import { OPERATIONAL_COPY, runMaintenanceCycle } from "./maintenance.ts";
import type {
  ClaimedOperationalAlert,
  ClaimedPublication,
  DeliveryStatus,
  OwnerDueDecision,
  OwnerOutcomeGrade,
  SchedulerRepository,
} from "./repository.ts";
import type { AdjustedBar } from "../market-briefing-gateway/_shared/market-data.ts";

const NOW = new Date("2026-09-03T18:00:00.000Z");
const ID1 = "11111111-1111-4111-8111-111111111111";
const ID2 = "22222222-2222-4222-8222-222222222222";

class MaintenanceRepository implements SchedulerRepository {
  events: string[] = [];
  publications: ClaimedPublication[] = [];
  alerts: ClaimedOperationalAlert[] = [];
  finishedPublications: Array<{ status: DeliveryStatus; ids: number[] }> = [];
  finishedAlerts: Array<{ status: DeliveryStatus; ids: number[] }> = [];
  appliedGrades: OwnerOutcomeGrade[] = [];
  due: OwnerDueDecision[] = [];
  claimDueSlots() {
    return Promise.resolve([]);
  }
  readTriggerSecret(): Promise<never> {
    return Promise.reject(new Error("unused"));
  }
  recordTriggerResult() {
    return Promise.resolve({});
  }
  publishHoliday() {
    return Promise.resolve({});
  }
  runMaintenance() {
    this.events.push("maintain");
    return Promise.resolve({ missed_slots: 1 });
  }
  claimPublications() {
    this.events.push("claim-publications");
    return Promise.resolve(this.publications);
  }
  finishPublication(
    _id: string,
    _lease: string,
    status: DeliveryStatus,
    ids: number[],
  ) {
    this.events.push("finish-publication");
    this.finishedPublications.push({ status, ids });
    return Promise.resolve({ status });
  }
  claimOperationalAlerts() {
    this.events.push("claim-alerts");
    return Promise.resolve(this.alerts);
  }
  finishOperationalAlert(
    _id: string,
    _lease: string,
    status: DeliveryStatus,
    ids: number[],
  ) {
    this.events.push("finish-alert");
    this.finishedAlerts.push({ status, ids });
    return Promise.resolve({ status });
  }
  readDueDecisions() {
    this.events.push("read-due");
    return Promise.resolve(this.due);
  }
  applyOutcomeGrades(grades: OwnerOutcomeGrade[]) {
    this.events.push("apply-grades");
    this.appliedGrades = structuredClone(grades);
    return Promise.resolve({
      inserted: grades.length,
      updated: 0,
      incomplete: grades.length,
    });
  }
}

function bar(date: string, close: string): AdjustedBar {
  return {
    date,
    raw_close: close,
    adjusted_close: close,
    raw_low: close,
    raw_high: close,
    split_ratio: null,
  };
}

function sessionBars(): AdjustedBar[] {
  const start = Date.UTC(2026, 5, 1);
  return Array.from({ length: 64 }, (_, index) => {
    const date = new Date(start + index * 86_400_000).toISOString().slice(
      0,
      10,
    );
    return bar(date, String(100 + index));
  });
}

Deno.test("maintenance delivers persisted publication parts before finalizing actual IDs", async () => {
  const repository = new MaintenanceRepository();
  repository.publications = [{
    publication_id: ID1,
    lease_token: ID2,
    chat_id: "1001",
    parts: ["server rendered part"],
  }];
  const result = await runMaintenanceCycle(
    repository,
    NOW,
    10,
    "12345:opaque",
    async (parts) => {
      repository.events.push(`send:${parts.join("|")}`);
      return [77];
    },
  );
  assertEquals(repository.events, [
    "maintain",
    "read-due",
    "claim-publications",
    "send:server rendered part",
    "finish-publication",
    "claim-alerts",
  ]);
  assertEquals(repository.finishedPublications, [{
    status: "delivered",
    ids: [77],
  }]);
  assertEquals(result.publications.delivered, 1);
});

Deno.test("operational alerts use only fixed non-financial copy", async () => {
  const repository = new MaintenanceRepository();
  repository.alerts = [{
    alert_id: ID1,
    lease_token: ID2,
    chat_id: "1001",
    code: "EXPECTED_RUN_MISSED",
  }];
  let sent = "";
  await runMaintenanceCycle(
    repository,
    NOW,
    10,
    "12345:opaque",
    async (parts) => {
      sent = parts[0];
      return [78];
    },
  );
  assertEquals(sent, OPERATIONAL_COPY.EXPECTED_RUN_MISSED);
  assertEquals(/\$|\bshares?\b|\b[A-Z]{2,5}\b/.test(sent), false);
  assertEquals(repository.finishedAlerts, [{ status: "delivered", ids: [78] }]);
});

Deno.test("ambiguous publication delivery is finalized unknown and never retried in-cycle", async () => {
  const repository = new MaintenanceRepository();
  repository.publications = [{
    publication_id: ID1,
    lease_token: ID2,
    chat_id: "1001",
    parts: ["one", "two"],
  }];
  let sends = 0;
  await runMaintenanceCycle(repository, NOW, 10, "12345:opaque", async () => {
    sends += 1;
    throw new TelegramDeliveryError("ambiguous", [90]);
  });
  assertEquals(sends, 1);
  assertEquals(repository.finishedPublications, [{
    status: "delivery_unknown",
    ids: [90],
  }]);
});

Deno.test("unknown operational codes fail closed before a message is sent", async () => {
  const repository = new MaintenanceRepository();
  repository.alerts = [{
    alert_id: ID1,
    lease_token: ID2,
    chat_id: "1001",
    code: "OWNER_PRIVATE_DATA",
  }];
  let sends = 0;
  await assertRejects(
    () =>
      runMaintenanceCycle(repository, NOW, 10, "12345:opaque", async () => {
        sends += 1;
        return [1];
      }),
    Error,
    "invalid operational alert claim",
  );
  assertEquals(sends, 0);
  assertEquals(repository.finishedAlerts, []);
});

Deno.test("maintenance grades due decisions deterministically with owner provenance", async () => {
  const repository = new MaintenanceRepository();
  repository.due = [{
    owner_id: ID1,
    suggestion_id: 7,
    decision_date: "2026-06-01",
    ticker: "AAPL",
    bucket: "growth",
    final_action: "buy",
    confidence: "medium",
    policy_version: 1,
    decision_price: "100",
    entry_zone_low: "99",
    entry_zone_high: "101",
    stop: "95",
    target: "110",
    invalidation_price: "94",
    completed_horizons: [],
  }];
  const fetched: string[] = [];
  const result = await runMaintenanceCycle(
    repository,
    NOW,
    10,
    "12345:opaque",
    async () => [],
    (ticker) => {
      fetched.push(ticker);
      return Promise.resolve(sessionBars());
    },
  );
  assertEquals(fetched.sort(), ["AAPL", "VOO"]);
  assertEquals(
    repository.appliedGrades.map((grade) => ({
      owner_id: grade.owner_id,
      horizon_days: grade.horizon_days,
      coverage_status: grade.coverage_status,
    })),
    [
      { owner_id: ID1, horizon_days: 5, coverage_status: "complete" },
      { owner_id: ID1, horizon_days: 21, coverage_status: "complete" },
      { owner_id: ID1, horizon_days: 63, coverage_status: "complete" },
    ],
  );
  assertEquals(result.outcome_grades, {
    inserted: 3,
    updated: 0,
    incomplete: 3,
  });
  assertEquals(repository.events, [
    "maintain",
    "read-due",
    "apply-grades",
    "claim-publications",
    "claim-alerts",
  ]);
});
