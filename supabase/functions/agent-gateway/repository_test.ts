import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import type { RpcClient } from "../_shared/postgres.ts";
import { createAgentGatewayRepository } from "./repository.ts";

Deno.test("agent repository maps six provider operations and two fixed internal steps to named machine RPCs", async () => {
  const calls: string[] = [];
  const runner = async <T>(_url: string, callback: (client: RpcClient) => Promise<T>): Promise<T> =>
    await callback({ callJsonRpc(name) { calls.push(name); return Promise.resolve({ ok: true }); } });
  const repository = createAgentGatewayRepository("opaque-database-url", runner);
  const input = { connection_id: crypto.randomUUID(), secret_digest: "a".repeat(64) };
  for (const operation of [
    "start_run", "read_bounded_context", "submit_analysis", "record_permitted_artifacts",
    "grade_due_decisions", "finish_run",
  ] as const) await repository.invoke(operation, input);
  await repository.applyAnalysis(input);
  await repository.finishAnalysisDelivery(input);
  assertEquals(calls, [
    "agent_start_run", "agent_read_bounded_context", "agent_submit_analysis",
    "agent_record_permitted_artifacts", "agent_grade_due_decisions", "agent_finish_run",
    "agent_apply_analysis", "agent_finish_analysis_delivery",
  ]);
});
