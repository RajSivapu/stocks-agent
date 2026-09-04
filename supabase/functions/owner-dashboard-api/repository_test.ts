import { createDashboardRepository } from "./repository.ts";

const TEST_DATABASE_URL =
  "postgresql://stock_agent_dashboard_runtime.projectref:dashboard-password-longer-than-24@aws-0-us-east-1.pooler.supabase.com:5432/postgres";

function assert(condition: boolean, message = "assertion failed"): void {
  if (!condition) throw new Error(message);
}

function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function recordingDatabase() {
  const statements: Array<{ text: string; parameters: readonly unknown[] }> = [];
  return {
    statements,
    factory: () => ({
      query: (text: string, parameters: readonly unknown[] = []) => {
        statements.push({ text, parameters });
        return Promise.resolve([]);
      },
    }),
  };
}

Deno.test("repository issues only fixed parameterized SELECT statements", async () => {
  const recorder = recordingDatabase();
  const repository = createDashboardRepository(TEST_DATABASE_URL, recorder.factory);
  await repository.read({ name: "portfolio" });
  await repository.read({ name: "meta" });
  await repository.read({ name: "today" });
  await repository.read({ name: "companion" });
  await repository.read({ name: "system" });
  await repository.read({ name: "transactions", cursor: "25" });
  await repository.read({ name: "ideas", status: "approved", cursor: "25" });
  await repository.read({ name: "alerts", state: "delivered", cursor: "25" });
  await repository.read({ name: "runs", kind: "intraday", cursor: "25" });
  try {
    await repository.read({
      name: "runDetail",
      id: "7d834dbd-75bb-4313-931f-09732f003932",
    });
  } catch {
    // The empty recording database has no matching run; its three queries are still inspected.
  }
  assert(recorder.statements.length >= 10);
  for (const statement of recorder.statements) {
    assert(/^SELECT\b/i.test(statement.text.trim()), statement.text);
    assert(!/\b(CALL|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|GRANT|REVOKE|RPC)\b/i.test(statement.text));
    assert(!statement.text.includes("25"), "cursor values must be parameters");
  }
});

Deno.test("repository applies fixed caps and returns opaque cursors", async () => {
  const statements: string[] = [];
  const repository = createDashboardRepository(TEST_DATABASE_URL, () => ({
    query: (text: string) => {
      statements.push(text);
      return Promise.resolve(Array.from({ length: 51 }, (_, index) => ({
        id: String(100 - index),
        ts: "2026-09-03T18:00:00.000Z",
        ticker: "VTI",
        side: "buy",
        qty: "1",
        price: "100",
        source: "owner",
        executed_on: "2026-09-03",
      })));
    },
  }));
  const result = await repository.read({ name: "transactions" });
  assertEquals(((result.data as { transactions: unknown[] }).transactions).length, 50);
  assertEquals(result.nextCursor, "50");
  assert((statements[0] ?? "").includes("LIMIT 51"));
});

Deno.test("database URL must be the scoped Supavisor session login", () => {
  for (const value of [
    "postgresql://postgres.projectref:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
    "postgresql://stock_agent_dashboard_runtime.projectref:secret@db.example.com:5432/postgres",
    "postgresql://stock_agent_dashboard_runtime.projectref:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
  ]) {
    let failed = false;
    try {
      createDashboardRepository(value, recordingDatabase().factory);
    } catch {
      failed = true;
    }
    assertEquals(failed, true);
  }
});
