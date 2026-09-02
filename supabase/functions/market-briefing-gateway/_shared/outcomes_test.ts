import type { AdjustedBar } from "./market-data.ts";
import { gradeDecision, type DueDecision } from "./outcomes.ts";

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function decision(overrides: Partial<DueDecision> = {}): DueDecision {
  return {
    suggestion_id: 7,
    decision_date: "2026-09-02",
    ticker: "AAPL",
    bucket: "growth",
    final_action: "buy",
    confidence: "medium",
    policy_version: 1,
    decision_price: "100",
    entry_zone_low: "98",
    entry_zone_high: "101",
    stop: "95",
    target: "109",
    invalidation_price: "94",
    completed_horizons: [],
    ...overrides,
  };
}

function bar(date: string, close: string, low = close, high = close, split: string | null = null): AdjustedBar {
  return { date, raw_close: close, adjusted_close: close, raw_low: low, raw_high: high, split_ratio: split };
}

const dates = ["2026-09-02", "2026-09-03", "2026-09-04", "2026-09-08", "2026-09-09", "2026-09-10"];
const stock = [
  bar(dates[0], "100", "99", "101"),
  bar(dates[1], "101", "99", "102"),
  bar(dates[2], "99", "98", "101"),
  bar(dates[3], "104", "102", "105"),
  bar(dates[4], "103", "101", "105"),
  bar(dates[5], "110", "108", "111"),
];
const benchmark = dates.map((date, index) => bar(date, String(100 + index * 0.8)));

Deno.test("five-session buy grade counts market sessions and uses adjusted closes", () => {
  const grade = gradeDecision(decision(), stock, benchmark, 5);
  assertEquals(grade.coverage_status, "complete");
  assertEquals(grade.benchmark_ticker, "VOO");
  assertEquals(grade.horizon_sessions, 5);
  assertEquals(grade.stock_return_pct, "10");
  assertEquals(grade.benchmark_return_pct, "4");
  assertEquals(grade.excess_return_pct, "6");
  assertEquals(grade.mfe_pct, "10");
  assertEquals(grade.mae_pct, "-1");
  assertEquals(grade.entry_hit_at, "2026-09-03");
  assertEquals(grade.target_hit_at, "2026-09-10");
  assertEquals(grade.direction_success, true);
});

Deno.test("decision price is converted to adjusted basis", () => {
  const adjustedStock = stock.map((item, index) => ({
    ...item,
    adjusted_close: index === 0 ? "50" : String(Number(item.adjusted_close) / 2),
  }));
  const grade = gradeDecision(decision(), adjustedStock, benchmark, 5);
  assertEquals(grade.stock_return_pct, "10");
  assertEquals(grade.mfe_pct, "10");
});

Deno.test("first raw level hits and an earlier stop make a Buy unsuccessful", () => {
  const losing = [
    stock[0],
    bar("2026-09-03", "97", "93", "100"),
    bar("2026-09-04", "96", "92", "110"),
    ...stock.slice(3),
  ];
  const grade = gradeDecision(decision(), losing, benchmark, 5);
  assertEquals(grade.entry_hit_at, "2026-09-03");
  assertEquals(grade.stop_hit_at, "2026-09-03");
  assertEquals(grade.invalidation_hit_at, "2026-09-03");
  assertEquals(grade.target_hit_at, "2026-09-04");
  assertEquals(grade.direction_success, false);
});

Deno.test("incomplete and missing synchronized history fail closed", () => {
  assertEquals(gradeDecision(decision(), stock.slice(0, 4), benchmark.slice(0, 4), 5).coverage_status, "incomplete");
  assertEquals(gradeDecision(decision(), [], benchmark, 5).coverage_status, "missing_history");
  assertEquals(gradeDecision(decision(), stock, benchmark.filter((item) => item.date !== "2026-09-08"), 5).coverage_status, "missing_benchmark");
});

Deno.test("a split requires review and suppresses raw threshold outcomes", () => {
  const splitStock = stock.map((item) => item.date === "2026-09-04" ? { ...item, split_ratio: "2" } : item);
  const grade = gradeDecision(decision(), splitStock, benchmark, 5);
  assertEquals(grade.coverage_status, "corporate_action_review");
  assertEquals(grade.entry_hit_at, null);
  assertEquals(grade.stop_hit_at, null);
  assertEquals(grade.target_hit_at, null);
  assertEquals(grade.invalidation_hit_at, null);
  assertEquals(grade.direction_success, null);
});

Deno.test("benchmark and directional success are deterministic", () => {
  assertEquals(gradeDecision(decision({ ticker: "VXUS" }), stock, benchmark, 5).benchmark_ticker, "VXUS");
  assertEquals(gradeDecision(decision({ final_action: "reduce" }), stock, benchmark, 5).direction_success, false);
  const falling = stock.map((item, index) => ({ ...item, adjusted_close: String(100 - index * 2) }));
  assertEquals(gradeDecision(decision({ final_action: "sell" }), falling, benchmark, 5).direction_success, true);
});

for (const final_action of ["watch", "hold", "avoid"] as const) {
  Deno.test(`${final_action} has no binary directional success`, () => {
    assertEquals(gradeDecision(decision({ final_action }), stock, benchmark, 5).direction_success, null);
  });
}
