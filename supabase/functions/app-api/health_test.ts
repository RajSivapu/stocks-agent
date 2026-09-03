import {
  assertEquals,
  assertThrows,
} from "https://deno.land/std@0.224.0/assert/mod.ts";

import {
  parseOperatorHealth,
  parsePublicHealth,
} from "./health.ts";


function operatorHealth() {
  return {
    status: "degraded",
    component_status: {
      database: "ok",
      scheduler: "degraded",
      provider_adapter: "ok",
      backup: "stale",
      restore: "missing",
      projections: "ok",
    },
    deployed_versions: { database_schema: 1, provider_contract: 2 },
    provider_adapter: { provider: "claude", active: 2, unavailable: 1 },
    missed_runs: { last_24_hours: 1, last_7_days: 2 },
    quota_pressure: { month_invocations: 12, configured_limit: 180 },
    backup: { age_hours: 40, last_success_at: "2026-09-01T00:00:00Z" },
    restore: { age_days: null, last_verified_at: null },
    projection: { checked: 2, failed: 0, paused: 0 },
  };
}

Deno.test("public health accepts only the fixed redacted shape", () => {
  assertEquals(parsePublicHealth({ status: "ok", schema_version: 1 }), {
    status: "ok",
    schema_version: 1,
  });
  assertThrows(() => parsePublicHealth({ status: "ok", schema_version: 1, users: 2 }));
});

Deno.test("operator health accepts aggregate component facts", () => {
  assertEquals(parseOperatorHealth(operatorHealth()), operatorHealth());
});

Deno.test("operator health rejects financial, identity, message, prompt, and token fields", () => {
  for (const key of [
    "email",
    "telegram_chat_id",
    "ticker",
    "position",
    "quantity",
    "cost_basis",
    "recommendation_text",
    "rendered_body",
    "prompt",
    "token",
    "owner_id",
    "missed_run_owners",
  ]) {
    assertThrows(() => parseOperatorHealth({ ...operatorHealth(), [key]: "private" }));
  }
});

Deno.test("operator health rejects per-owner arrays and unexpected nesting", () => {
  assertThrows(() => parseOperatorHealth({
    ...operatorHealth(),
    missed_runs: { last_24_hours: 1, last_7_days: 2, owners: ["one"] },
  }));
});
