import postgres from "npm:postgres@3.4.9";


export const RPC_ALLOWLIST = [
  "agent_start_run",
  "agent_read_bounded_context",
  "agent_submit_analysis",
  "agent_record_permitted_artifacts",
  "agent_grade_due_decisions",
  "agent_finish_run",
  "scheduler_claim_due_slots",
  "scheduler_read_trigger_secret",
  "scheduler_record_trigger_result",
  "scheduler_publish_holiday",
  "telegram_claim_update",
  "telegram_resolve_link",
  "telegram_preview_command",
  "telegram_confirm_command",
  "telegram_cancel_command",
  "telegram_portfolio",
  "telegram_plans",
  "telegram_record_delivery",
  "backup_export_bundle",
] as const;

export type RpcName = typeof RPC_ALLOWLIST[number];
export type RpcClient = {
  callJsonRpc(name: RpcName, args: Record<string, unknown>): Promise<Record<string, unknown>>;
};

type TaggedSql = {
  (strings: TemplateStringsArray, ...values: unknown[]): Promise<Array<Record<string, unknown>>>;
  begin<T>(callback: (transaction: TaggedSql) => Promise<T>): Promise<T>;
  end(options?: { timeout?: number }): Promise<void>;
  json(value: unknown): unknown;
};

type PostgresFactory = (url: string, options: Record<string, unknown>) => TaggedSql;

export function validateDatabaseUrl(databaseUrl: string): string {
  let url: URL;
  try {
    url = new URL(databaseUrl);
  } catch (_error) {
    throw new Error("database URL must use the Supavisor session endpoint");
  }
  if (
    !["postgres:", "postgresql:"].includes(url.protocol) ||
    !/^[a-z0-9-]+\.pooler\.supabase\.com$/.test(url.hostname) ||
    url.port !== "5432" || !url.username.includes(".") || !url.password ||
    url.pathname !== "/postgres" || url.search || url.hash
  ) {
    throw new Error("database URL must use the IPv4 Supavisor session endpoint on port 5432");
  }
  return databaseUrl;
}

function validateRpcArgs(args: Record<string, unknown>): void {
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    throw new Error("RPC arguments must be an object");
  }
  if (new TextEncoder().encode(JSON.stringify(args)).byteLength > 64 * 1024) {
    throw new Error("RPC arguments exceed the size limit");
  }
}

async function executeRpc(
  transaction: TaggedSql,
  name: RpcName,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  if (!(RPC_ALLOWLIST as readonly string[]).includes(name)) {
    throw new Error("database RPC is not allow-listed");
  }
  validateRpcArgs(args);
  let rows: Array<Record<string, unknown>>;
  const value = transaction.json(args);
  switch (name) {
    case "agent_start_run": rows = await transaction`SELECT machine.agent_start_run(${value}::jsonb) AS result`; break;
    case "agent_read_bounded_context": rows = await transaction`SELECT machine.agent_read_bounded_context(${value}::jsonb) AS result`; break;
    case "agent_submit_analysis": rows = await transaction`SELECT machine.agent_submit_analysis(${value}::jsonb) AS result`; break;
    case "agent_record_permitted_artifacts": rows = await transaction`SELECT machine.agent_record_permitted_artifacts(${value}::jsonb) AS result`; break;
    case "agent_grade_due_decisions": rows = await transaction`SELECT machine.agent_grade_due_decisions(${value}::jsonb) AS result`; break;
    case "agent_finish_run": rows = await transaction`SELECT machine.agent_finish_run(${value}::jsonb) AS result`; break;
    case "scheduler_claim_due_slots": rows = await transaction`SELECT machine.scheduler_claim_due_slots(${value}::jsonb) AS result`; break;
    case "scheduler_read_trigger_secret": rows = await transaction`SELECT machine.scheduler_read_trigger_secret(${value}::jsonb) AS result`; break;
    case "scheduler_record_trigger_result": rows = await transaction`SELECT machine.scheduler_record_trigger_result(${value}::jsonb) AS result`; break;
    case "scheduler_publish_holiday": rows = await transaction`SELECT machine.scheduler_publish_holiday(${value}::jsonb) AS result`; break;
    case "telegram_claim_update": rows = await transaction`SELECT machine.telegram_claim_update(${value}::jsonb) AS result`; break;
    case "telegram_resolve_link": rows = await transaction`SELECT machine.telegram_resolve_link(${value}::jsonb) AS result`; break;
    case "telegram_preview_command": rows = await transaction`SELECT machine.telegram_preview_command(${value}::jsonb) AS result`; break;
    case "telegram_confirm_command": rows = await transaction`SELECT machine.telegram_confirm_command(${value}::jsonb) AS result`; break;
    case "telegram_cancel_command": rows = await transaction`SELECT machine.telegram_cancel_command(${value}::jsonb) AS result`; break;
    case "telegram_portfolio": rows = await transaction`SELECT machine.telegram_portfolio(${value}::jsonb) AS result`; break;
    case "telegram_plans": rows = await transaction`SELECT machine.telegram_plans(${value}::jsonb) AS result`; break;
    case "telegram_record_delivery": rows = await transaction`SELECT machine.telegram_record_delivery(${value}::jsonb) AS result`; break;
    case "backup_export_bundle": rows = await transaction`SELECT machine.backup_export_bundle(${value}::jsonb) AS result`; break;
  }
  const result = rows[0]?.result;
  if (rows.length !== 1 || result === null || typeof result !== "object" || Array.isArray(result)) {
    throw new Error("database RPC returned an unexpected result");
  }
  return result as Record<string, unknown>;
}

export async function withDatabase<T>(
  databaseUrl: string,
  callback: (client: RpcClient) => Promise<T>,
  factory: PostgresFactory = postgres as unknown as PostgresFactory,
): Promise<T> {
  const sql = factory(validateDatabaseUrl(databaseUrl), {
    max: 1,
    idle_timeout: 2,
    connect_timeout: 5,
    max_lifetime: 60,
    prepare: true,
    ssl: "require",
  });
  try {
    return await sql.begin(async (transaction) => {
      await transaction`SET LOCAL statement_timeout = '5s'`;
      return await callback({
        callJsonRpc: (name, args) => executeRpc(transaction, name, args),
      });
    });
  } finally {
    await sql.end({ timeout: 1 });
  }
}
