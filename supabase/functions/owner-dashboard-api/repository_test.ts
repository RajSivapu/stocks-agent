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
  await repository.read({ name: "transactions" });
  await repository.read({ name: "ideas", status: "approved" });
  await repository.read({ name: "alerts", state: "delivered" });
  await repository.read({ name: "runs", kind: "intraday" });
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
  assert(typeof result.nextCursor === "string" && !/^\d+$/.test(result.nextCursor), "cursor must be opaque");
  assert((statements[0] ?? "").includes("LIMIT 51"));
  assert(!(statements[0] ?? "").includes("OFFSET"), "offset pagination can skip concurrent receipts");
});

Deno.test("an empty portfolio is unavailable rather than fresh", async () => {
  const repository = createDashboardRepository(TEST_DATABASE_URL, () => ({
    query: () => Promise.resolve([]),
  }));
  const result = await repository.read({ name: "portfolio" });
  assertEquals(result.freshness, "unavailable");
  assertEquals(result.dataAsOf, null);
});

Deno.test("a quote without persisted market state cannot support portfolio value", async () => {
  const repository = createDashboardRepository(TEST_DATABASE_URL, () => ({
    query: (text: string) => Promise.resolve(text.includes("FROM public.holdings h") ? [{
      ticker: "VTI",
      shares: "1",
      avg_cost: "100",
      bucket: "core",
      price: "110",
      price_as_of: "2026-09-03T15:00:00.000Z",
      price_source: "yahoo-chart",
      price_market_state: null,
    }] : []),
  }), () => new Date("2026-09-03T21:00:00.000Z"));
  const result = await repository.read({ name: "portfolio" });
  assertEquals(result.freshness, "partial");
  assertEquals((result.data as { totals: { value: string | null } }).totals.value, null);
});

Deno.test("today reports the worst supporting freshness and a conservative timestamp", async () => {
  const old = "2026-09-02T17:00:00.000Z";
  const repository = createDashboardRepository(TEST_DATABASE_URL, () => ({
    query: (text: string) => {
      if (text.includes("FROM public.holdings h")) return Promise.resolve([{
        ticker: "VTI", shares: "1", avg_cost: "100", bucket: "core", price: "110",
        price_as_of: "2026-09-03T17:55:00.000Z", price_source: "finnhub",
        price_market_state: "REGULAR",
      }]);
      if (text.includes("FROM public.analysis_runs r")) return Promise.resolve([{
        id: "7d834dbd-75bb-4313-931f-09732f003932", kind: "intraday", status: "completed",
        started_at: old, finished_at: old, data_as_of: old,
      }]);
      if (text.includes("FROM public.suggestions s")) return Promise.resolve([{
        id: "1", ticker: "MSFT", action: "watch", created_at: old,
      }]);
      if (text.includes("FROM public.market_gateway_requests")) return Promise.resolve([]);
      if (text.includes("FROM public.market_publications p")) return Promise.resolve([{
        id: "a", kind: "brief", phase: "intraday", status: "suppressed",
        rendered_body: "No trigger.", rendered_hash: "a".repeat(64), template_version: "3",
        telegram_message_ids: [], attempt_count: 0, created_at: old,
      }]);
      return Promise.resolve([]);
    },
  }), () => new Date("2026-09-03T18:00:00.000Z"));
  const result = await repository.read({ name: "today" });
  assertEquals(result.freshness, "stale");
  assertEquals(result.dataAsOf, old);
});

Deno.test("alerts query carries structured source evidence", async () => {
  const recorder = recordingDatabase();
  const repository = createDashboardRepository(TEST_DATABASE_URL, recorder.factory);
  await repository.read({ name: "alerts" });
  const statement = recorder.statements.find((item) => item.text.includes("FROM public.market_publications p"));
  assert(Boolean(statement?.text.includes("AS sources")), "alerts query omitted structured evidence sources");
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
