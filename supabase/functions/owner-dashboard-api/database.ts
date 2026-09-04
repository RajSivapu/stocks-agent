import postgres from "postgres";

export interface DashboardDatabase {
  query(text: string, parameters?: readonly unknown[]): Promise<Record<string, unknown>[]>;
}

export type DashboardDatabaseFactory = (databaseUrl: string) => DashboardDatabase;

export function validateDashboardDatabaseUrl(databaseUrl: string): void {
  let parsed: URL;
  try {
    parsed = new URL(databaseUrl);
  } catch {
    throw new Error("DASHBOARD_DATABASE_URL is invalid");
  }
  if (
    !["postgres:", "postgresql:"].includes(parsed.protocol) ||
    !parsed.hostname.endsWith(".pooler.supabase.com") ||
    parsed.port !== "5432" ||
    parsed.pathname !== "/postgres" ||
    !parsed.username.startsWith("stock_agent_dashboard_runtime.") ||
    !parsed.password ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error("DASHBOARD_DATABASE_URL must use the scoped Supavisor session login");
  }
}

export const createDashboardDatabase: DashboardDatabaseFactory = (databaseUrl) => {
  validateDashboardDatabaseUrl(databaseUrl);
  const client = postgres(databaseUrl, {
    ssl: "require",
    max: 1,
    connect_timeout: 5,
    idle_timeout: 5,
    prepare: true,
    connection: {
      application_name: "owner-dashboard-api",
      statement_timeout: 5000,
    },
  });
  return {
    query: async (text, parameters = []) =>
      await client.unsafe(
        text,
        parameters.map((value) => value as never),
        { prepare: true },
      ) as unknown as Record<string, unknown>[],
  };
};
