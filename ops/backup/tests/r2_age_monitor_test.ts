import {
  assertEquals,
} from "https://deno.land/std@0.224.0/assert/mod.ts";
import { evaluateBackupState } from "../r2-age-monitor/src/index.ts";

const limits = {
  maxAgeHours: 36,
  storageAlertBytes: 8 * 1024 * 1024 * 1024,
  plannedMonthlyClassAOps: 250,
  plannedMonthlyClassBOps: 100,
  classAAlertOps: 800_000,
  classBAlertOps: 8_000_000,
};

Deno.test("backup monitor reports a fresh encrypted archive without private metadata", () => {
  const now = Date.parse("2026-09-03T12:00:00Z");
  const state = evaluateBackupState([
    {
      key: "stock-agent/daily/2026/09/2026-09-03T08-23-00Z.age",
      size: 2048,
      uploaded: new Date("2026-09-03T08:23:00Z"),
    },
  ], now, limits);
  assertEquals(state, {
    stale: false,
    capacityWarning: false,
    newestAgeHours: 3.6166666666666667,
    objectCount: 1,
    totalBytes: 2048,
  });
});

Deno.test("backup monitor fails stale when no archive exists", () => {
  const state = evaluateBackupState([], Date.parse("2026-09-03T12:00:00Z"), limits);
  assertEquals(state.stale, true);
  assertEquals(state.newestAgeHours, null);
});

Deno.test("backup monitor warns before configured storage or operation allowance", () => {
  const state = evaluateBackupState([
    {
      key: "stock-agent/daily/test.age",
      size: limits.storageAlertBytes,
      uploaded: new Date("2026-09-03T12:00:00Z"),
    },
  ], Date.parse("2026-09-03T12:00:00Z"), limits);
  assertEquals(state.capacityWarning, true);

  const operationState = evaluateBackupState([], Date.now(), {
    ...limits,
    plannedMonthlyClassAOps: limits.classAAlertOps,
  });
  assertEquals(operationState.capacityWarning, true);
});
