import { withDatabase, type RpcClient } from "../_shared/postgres.ts";
import type { ProviderOperation } from "../../../packages/contracts/src/provider.ts";
import type { AgentGatewayRepository } from "./handler.ts";

type DatabaseRunner = <T>(databaseUrl: string, callback: (client: RpcClient) => Promise<T>) => Promise<T>;

const RPC_BY_OPERATION = {
  start_run: "agent_start_run",
  read_bounded_context: "agent_read_bounded_context",
  submit_analysis: "agent_submit_analysis",
  record_permitted_artifacts: "agent_record_permitted_artifacts",
  grade_due_decisions: "agent_grade_due_decisions",
  finish_run: "agent_finish_run",
} as const;

export function createAgentGatewayRepository(
  databaseUrl: string,
  database: DatabaseRunner = withDatabase,
): AgentGatewayRepository {
  return {
    invoke(operation: ProviderOperation, request: Record<string, unknown>) {
      return database(databaseUrl, (client) => client.callJsonRpc(RPC_BY_OPERATION[operation], request));
    },
    applyAnalysis(request: Record<string, unknown>) {
      return database(databaseUrl, (client) => client.callJsonRpc("agent_apply_analysis", request));
    },
    finishAnalysisDelivery(request: Record<string, unknown>) {
      return database(databaseUrl, (client) => client.callJsonRpc("agent_finish_analysis_delivery", request));
    },
  };
}
