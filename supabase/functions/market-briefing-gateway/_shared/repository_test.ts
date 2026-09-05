import type { PolicyConfig } from "./contracts.ts";
import {
  consecutiveRecommendationLosses,
  createSupabaseGatewayRepository,
  GatewayRepositoryError,
  mergeRelevantSuggestions,
  validatePolicy,
} from "./repository.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
  }
}

function policy(): PolicyConfig {
  return {
    version: 2,
    allocation_bps: { core: 7000, growth: 2000, speculative: 1000 },
    max_position_bps_of_bucket: { core: 2500, growth: 2000, speculative: 1000 },
    max_trade_risk_bps: { core: 100, growth: 100, speculative: 50 },
    min_reward_risk_milli: 2000,
    max_actionable_quote_age_minutes: 20,
    alert_near_bps: 400,
    daily_loss_limit_bps: 300,
    circuit_breaker_consecutive_losses: 3,
    speculative_go_live_bucket_micros: "500000000",
    monthly_investment_micros: "500000000",
    broad_core_etfs: ["SCHD", "VOO", "VTI", "VXUS"],
    self_tuning_enabled: false,
    market_calendar_year: 2026,
    nyse_holidays: [],
    request_limits: {
      max_body_bytes: 262144,
      max_candidates: {
        "pre-market": 80,
        intraday: 20,
        "post-market": 80,
        "on-demand": 10,
      },
      max_requests_per_run: 20,
      max_authenticated_requests_per_hour: 100,
    },
    alerts_v3: {
      enabled: false,
      shadow: true,
      enabled_classes: [],
      profile: "balanced",
      draft_ttl_hours: 24,
      drafts_per_hour: 5,
    },
  };
}

Deno.test("report suppression RPC persists the typed reason and rejects an error alias", async () => {
  const calls: unknown[] = [];
  let legacy = false;
  const repository = createSupabaseGatewayRepository({
    rpc(name: string, parameters?: Record<string, unknown>) {
      calls.push({ name, parameters });
      return Promise.resolve({
        data: {
          report_id: "00000000-0000-4000-8000-000000000001",
          idempotency_key: "a".repeat(64),
          status: "suppressed",
          ...(legacy
            ? { error: "no_trigger" }
            : { suppression_reason: "no_trigger" }),
        },
        error: null,
      });
    },
  });
  const result = await repository.suppressReportPublication!(
    "a".repeat(64),
    "no_trigger",
  );
  assertEquals(result.suppression_reason, "no_trigger");
  assertEquals(calls, [{
    name: "suppress_market_report_publication",
    parameters: { p_idempotency_key: "a".repeat(64), p_reason: "no_trigger" },
  }]);
  legacy = true;
  let rejected = false;
  try {
    await repository.suppressReportPublication!("a".repeat(64), "no_trigger");
  } catch {
    rejected = true;
  }
  assert(rejected, "generic error field became suppression authority");
});

Deno.test("start run preserves the authoritative duplicate flag from SQL", async () => {
  const calls: unknown[] = [];
  const repository = createSupabaseGatewayRepository({
    rpc(name: string, parameters?: Record<string, unknown>) {
      calls.push({ name, parameters });
      return Promise.resolve({
        data: {
          run_id: "00000000-0000-4000-8000-000000000001",
          duplicate: true,
        },
        error: null,
      });
    },
  });
  assertEquals(
    await repository.startRun(
      "00000000-0000-4000-8000-000000000002",
      "00000000-0000-4000-8000-000000000003",
      "intraday",
      "2026-09-02",
    ),
    {
      run_id: "00000000-0000-4000-8000-000000000001",
      duplicate: true,
    },
  );
  assertEquals(calls.length, 1);
});

Deno.test("completion recovery reads the immutable completion by run and stable identity", async () => {
  const calls: unknown[] = [];
  const saved = {
    receipt: { completion_id: "00000000-0000-4000-8000-000000000002" },
    payload: {},
    providers: {},
  };
  const repository = createSupabaseGatewayRepository({
    rpc(name: string, parameters?: Record<string, unknown>) {
      calls.push({ name, parameters });
      return Promise.resolve({ data: saved, error: null });
    },
  });
  const result = await repository.readIntelligenceCompletion!(
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
  );
  assertEquals(result, saved);
  assertEquals(calls, [{
    name: "read_market_intelligence_completion",
    parameters: {
      p_run_id: "00000000-0000-4000-8000-000000000001",
      p_completion_id: "00000000-0000-4000-8000-000000000002",
    },
  }]);
});

Deno.test("relevant suggestion context keeps unresolved work when old completed history exceeds the bound", () => {
  const unresolved = [{
    id: 999,
    date: "2026-09-02",
    ticker: "OPEN",
    valid_until: "2026-09-09",
  }];
  const completed = Array.from({ length: 101 }, (_, index) => ({
    id: index,
    date: `2026-08-${String(31 - (index % 28)).padStart(2, "0")}`,
    ticker: `OLD${index}`,
    valid_until: "2026-08-31",
  }));
  const selected = mergeRelevantSuggestions(unresolved, completed, 100);
  assertEquals(selected.length, 100);
  assertEquals(selected[0].ticker, "OPEN");
  assertEquals(selected.filter((row) => row.ticker === "OPEN").length, 1);
  assertEquals(
    selected.filter((row) => row.ticker.startsWith("OLD")).length,
    99,
  );
  assertEquals(new Set(selected.map((row) => row.id)).size, 100);
});

Deno.test("relevant suggestion history resolves equal dates by descending durable id", () => {
  const selected = mergeRelevantSuggestions(
    [],
    [
      { id: 2, date: "2026-09-01", ticker: "SECOND" },
      { id: 9, date: "2026-09-01", ticker: "FIRST" },
    ],
    2,
  );
  assertEquals(selected.map((row) => row.id), [9, 2]);
});

Deno.test("newer completed history cannot displace unresolved suggestion context", () => {
  const selected = mergeRelevantSuggestions(
    [{ id: 1, date: "2026-01-01", ticker: "PENDING" }],
    [{ id: 99, date: "2099-01-01", ticker: "HISTORY" }],
    1,
  );
  assertEquals(selected.map((row) => row.ticker), ["PENDING"]);
});

Deno.test("three losing horizons for one recommendation count as one loss", () => {
  assertEquals(
    consecutiveRecommendationLosses([
      {
        suggestion_id: 9,
        horizon_days: 63,
        coverage_status: "complete",
        direction_success: false,
        recommendation: { ts: "2026-09-03T14:00:00Z" },
      },
      {
        suggestion_id: 9,
        horizon_days: 21,
        coverage_status: "complete",
        direction_success: false,
        recommendation: { ts: "2026-09-03T14:00:00Z" },
      },
      {
        suggestion_id: 9,
        horizon_days: 5,
        coverage_status: "complete",
        direction_success: false,
        recommendation: { ts: "2026-09-03T14:00:00Z" },
      },
    ]),
    1,
  );
});

Deno.test("recommendation streak uses the longest completed horizon once", () => {
  assertEquals(
    consecutiveRecommendationLosses([
      {
        suggestion_id: 10,
        horizon_days: 5,
        coverage_status: "complete",
        direction_success: true,
        recommendation: { ts: "2026-09-03T14:00:00Z" },
      },
      {
        suggestion_id: 10,
        horizon_days: 21,
        coverage_status: "complete",
        direction_success: false,
        recommendation: { ts: "2026-09-03T14:00:00Z" },
      },
      {
        suggestion_id: 9,
        horizon_days: 63,
        coverage_status: "complete",
        direction_success: false,
        recommendation: { ts: "2026-09-02T14:00:00Z" },
      },
      {
        suggestion_id: 8,
        horizon_days: 63,
        coverage_status: "complete",
        direction_success: true,
        recommendation: { ts: "2026-09-01T14:00:00Z" },
      },
    ]),
    2,
  );
});

Deno.test("recommendation streak orders equal-time grades by recommendation chronology", () => {
  const gradedAt = "2026-12-31T21:00:00Z";
  assertEquals(
    consecutiveRecommendationLosses([
      {
        suggestion_id: 7,
        horizon_days: 63,
        coverage_status: "complete",
        direction_success: true,
        graded_at: gradedAt,
        recommendation: { ts: "2026-09-01T14:00:00Z" },
      },
      {
        suggestion_id: 9,
        horizon_days: 5,
        coverage_status: "complete",
        direction_success: false,
        graded_at: gradedAt,
        recommendation: { ts: "2026-09-03T14:00:00Z" },
      },
      {
        suggestion_id: 8,
        horizon_days: 21,
        coverage_status: "complete",
        direction_success: false,
        graded_at: gradedAt,
        recommendation: { ts: "2026-09-02T14:00:00Z" },
      },
    ]),
    2,
  );
});

Deno.test("context bounds eligible completed recommendations before newer ineligible rows", async () => {
  const newerIneligibleRecommendations = Array.from(
    { length: 151 },
    (_, index) => ({
      id: 2000 + index,
      ts: "2026-10-01T14:00:00Z",
      decision_source: "gateway",
    }),
  );
  const oldRecommendations = Array.from({ length: 151 }, (_, index) => ({
    id: index + 1,
    ts: "2026-08-01T14:00:00Z",
    decision_source: "gateway",
  }));
  const recommendations = [
    ...newerIneligibleRecommendations,
    { id: 1001, ts: "2026-09-03T14:00:00Z", decision_source: "gateway" },
    { id: 1000, ts: "2026-09-02T14:00:00Z", decision_source: "gateway" },
    { id: 999, ts: "2026-09-01T14:00:00Z", decision_source: "gateway" },
    ...oldRecommendations,
  ];
  const grades = [
    ...newerIneligibleRecommendations.slice(0, 50).map((recommendation) => ({
      suggestion_id: recommendation.id,
      horizon_days: 5,
      coverage_status: "incomplete",
      excess_return_pct: null,
      direction_success: false,
      graded_at: "2026-11-01T21:00:00Z",
    })),
    ...newerIneligibleRecommendations.slice(50, 100).map((recommendation) => ({
      suggestion_id: recommendation.id,
      horizon_days: 5,
      coverage_status: "complete",
      excess_return_pct: null,
      direction_success: null,
      graded_at: "2026-11-01T21:00:00Z",
    })),
    ...oldRecommendations.map((recommendation) => ({
      suggestion_id: recommendation.id,
      horizon_days: 5,
      coverage_status: "incomplete",
      excess_return_pct: null,
      direction_success: null,
      graded_at: "2026-12-31T21:00:00Z",
    })),
    {
      suggestion_id: 1001,
      horizon_days: 5,
      coverage_status: "complete",
      excess_return_pct: "-2",
      direction_success: false,
      graded_at: "2026-09-10T21:00:00Z",
    },
    {
      suggestion_id: 1000,
      horizon_days: 21,
      coverage_status: "complete",
      excess_return_pct: "-3",
      direction_success: false,
      graded_at: "2026-09-09T21:00:00Z",
    },
    {
      suggestion_id: 999,
      horizon_days: 63,
      coverage_status: "complete",
      excess_return_pct: "2",
      direction_success: true,
      graded_at: "2026-09-08T21:00:00Z",
    },
  ];

  class ReadQuery {
    private selected = "";
    private equals: Array<[string, unknown]> = [];
    private included: Array<[string, unknown[]]> = [];
    private orders: Array<[string, boolean]> = [];
    private rowLimit: number | null = null;

    constructor(private table: string) {}

    select(columns: string): ReadQuery {
      this.selected = columns;
      return this;
    }
    eq(column: string, value: unknown): ReadQuery {
      this.equals.push([column, value]);
      return this;
    }
    in(column: string, values: unknown[]): ReadQuery {
      this.included.push([column, values]);
      return this;
    }
    order(column: string, options: { ascending?: boolean } = {}): ReadQuery {
      this.orders.push([column, options.ascending !== false]);
      return this;
    }
    limit(count: number): ReadQuery {
      this.rowLimit = count;
      return this;
    }
    or(): ReadQuery {
      return this;
    }
    gte(): ReadQuery {
      return this;
    }
    lt(): ReadQuery {
      return this;
    }

    private result(): { data: Record<string, unknown>[]; error: null } {
      const joinedGrades = this.table === "suggestions" &&
        this.selected.startsWith(
          "id,ts,eligible_grades:suggestion_grades!inner(",
        );
      let data: Record<string, unknown>[] = joinedGrades
        ? structuredClone(recommendations).map((recommendation) => {
          let eligibleGrades = structuredClone(grades).filter((grade) =>
            grade.suggestion_id === recommendation.id
          );
          for (const [column, value] of this.equals) {
            if (column.startsWith("eligible_grades.")) {
              const nestedColumn = column.slice("eligible_grades.".length);
              eligibleGrades = eligibleGrades.filter((grade) =>
                (grade as Record<string, unknown>)[nestedColumn] === value
              );
            }
          }
          for (const [column, values] of this.included) {
            if (column.startsWith("eligible_grades.")) {
              const nestedColumn = column.slice("eligible_grades.".length);
              eligibleGrades = eligibleGrades.filter((grade) =>
                values.includes(
                  (grade as Record<string, unknown>)[nestedColumn],
                )
              );
            }
          }
          return { ...recommendation, eligible_grades: eligibleGrades };
        }).filter((recommendation) =>
          (recommendation.eligible_grades as unknown[]).length > 0
        )
        : this.table === "suggestions" && this.selected === "id,ts"
        ? structuredClone(recommendations)
        : this.table === "suggestion_grades"
        ? structuredClone(grades)
        : [];
      for (const [column, value] of this.equals) {
        if (column.startsWith("eligible_grades.")) continue;
        data = data.filter((row) => row[column] === value);
      }
      for (const [column, values] of this.included) {
        if (column.startsWith("eligible_grades.")) continue;
        data = data.filter((row) => values.includes(row[column]));
      }
      data.sort((left, right) => {
        for (const [column, ascending] of this.orders) {
          const compared = String(left[column]).localeCompare(
            String(right[column]),
            "en",
            { numeric: true },
          );
          if (compared !== 0) return ascending ? compared : -compared;
        }
        return 0;
      });
      return {
        data: this.rowLimit === null ? data : data.slice(0, this.rowLimit),
        error: null,
      };
    }

    then(
      onfulfilled?: (
        value: { data: Record<string, unknown>[]; error: null },
      ) => unknown,
      onrejected?: (reason: unknown) => unknown,
    ): Promise<unknown> {
      return Promise.resolve(this.result()).then(onfulfilled, onrejected);
    }
  }

  const repository = createSupabaseGatewayRepository({
    from(table: string) {
      return new ReadQuery(table);
    },
    rpc(name: string) {
      if (name === "read_reconciled_cash_snapshot") {
        return Promise.resolve({ data: null, error: null });
      }
      throw new Error(`unexpected RPC ${name}`);
    },
  });
  const context = await repository.readContext(null);
  assertEquals(context.consecutive_completed_losses, 2);
});

Deno.test("unresolved suggestion overflow fails closed instead of dropping pending state", () => {
  let error: unknown = null;
  try {
    mergeRelevantSuggestions(
      Array.from({ length: 101 }, (_, id) => ({
        id,
        date: "2026-09-01",
        ticker: `PENDING${id}`,
      })),
      [],
      100,
    );
  } catch (caught) {
    error = caught;
  }
  assert(
    error instanceof GatewayRepositoryError,
    "overflow must reject context",
  );
  assertEquals((error as GatewayRepositoryError).code, "CONTEXT_TOO_LARGE");
});

function rejects(value: unknown): boolean {
  try {
    validatePolicy(value);
  } catch (error) {
    return error instanceof GatewayRepositoryError &&
      error.code === "POLICY_REJECTED";
  }
  return false;
}

Deno.test("repository accepts only the reviewed alert v3 policy shape", () => {
  const legacy = structuredClone(policy()) as unknown as Record<
    string,
    unknown
  >;
  legacy.version = 2;
  delete (legacy.alerts_v3 as Record<string, unknown>).enabled_classes;
  assertEquals(validatePolicy(legacy).alerts_v3?.enabled_classes, []);

  const reviewed = policy();
  reviewed.version = 3;
  reviewed.alerts_v3!.enabled = true;
  reviewed.alerts_v3!.shadow = false;
  reviewed.alerts_v3!.enabled_classes = ["stop_breach"];
  assert(
    validatePolicy(reviewed).alerts_v3?.enabled === true,
    "reviewed policy rejected",
  );
  for (
    const alerts of [
      { ...policy().alerts_v3, enabled: true, shadow: true },
      {
        ...policy().alerts_v3,
        enabled: true,
        shadow: false,
        enabled_classes: [],
      },
      {
        ...policy().alerts_v3,
        enabled_classes: ["stop_breach", "stop_breach"],
      },
      { ...policy().alerts_v3, enabled_classes: ["stop_near"] },
      { ...policy().alerts_v3, profile: "aggressive" },
      { ...policy().alerts_v3, drafts_per_hour: 50 },
      { ...policy().alerts_v3, broker_execution: true },
    ]
  ) {
    assert(
      rejects({ ...policy(), version: 3, alerts_v3: alerts }),
      "unreviewed alert policy accepted",
    );
  }
});

Deno.test("report delivery outbox is created from the deterministic report receipt before Telegram work", async () => {
  const calls: Array<
    { name: string; parameters: Record<string, unknown> | undefined }
  > = [];
  const repository = createSupabaseGatewayRepository({
    rpc(name: string, parameters?: Record<string, unknown>) {
      calls.push({ name, parameters });
      return Promise.resolve({
        data: {
          report_id: "00000000-0000-4000-8000-000000000001",
          idempotency_key: "a".repeat(64),
          status: "pending",
          telegram_message_ids: [],
          telegram_accepted_at: null,
          lease_token: null,
        },
        error: null,
      });
    },
  });
  const receipt = await repository.createReportPublication!(
    "00000000-0000-4000-8000-000000000002",
    {
      id: "00000000-0000-4000-8000-000000000001",
      idempotency_key: "a".repeat(64),
      packet_id: "00000000-0000-4000-8000-000000000003",
      market_date: "2026-09-02",
      kind: "morning",
      report: {
        title: "x",
        summary: "x",
        full_markdown: "x",
        source_ids: [],
        policy_decision_ids: [],
        comparison_ids: [],
        actionable_risk: false,
        material_thesis_change: false,
        intraday_triggered: false,
        suggestion_only: true,
      },
      report_hash: "b".repeat(64),
      rendered_text: "Delivery body",
      rendered_hash: "c".repeat(64),
    },
  );
  assertEquals(receipt, {
    id: "00000000-0000-4000-8000-000000000001",
    idempotency_key: "a".repeat(64),
    status: "pending",
    telegram_message_ids: [],
    telegram_accepted_at: null,
    lease_token: null,
  });
  assertEquals(calls, [{
    name: "create_market_report_publication",
    parameters: {
      p_run_id: "00000000-0000-4000-8000-000000000002",
      p_report_id: "00000000-0000-4000-8000-000000000001",
      p_idempotency_key: "a".repeat(64),
      p_market_date: "2026-09-02",
      p_kind: "morning",
      p_rendered_body: "Delivery body",
      p_rendered_hash: "c".repeat(64),
    },
  }]);
});
