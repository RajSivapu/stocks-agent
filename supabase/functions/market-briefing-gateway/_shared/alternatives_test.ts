import {
  comparePortfolioAlternative,
  type PortfolioAlternativeRequest,
  qualifyPersonalAlternative,
} from "./alternatives.ts";
import type { AdjustedBar } from "./market-data.ts";

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

function businessBars(
  start: string,
  sessions: number,
  startPrice: number,
  endPrice: number,
): AdjustedBar[] {
  const date = new Date(`${start}T12:00:00.000Z`);
  const bars: AdjustedBar[] = [];
  while (bars.length < sessions) {
    const day = date.getUTCDay();
    if (day !== 0 && day !== 6) {
      const progress = bars.length / (sessions - 1);
      const price = (startPrice + (endPrice - startPrice) * progress).toFixed(
        6,
      );
      bars.push({
        date: date.toISOString().slice(0, 10),
        raw_close: price,
        adjusted_close: price,
        raw_high: price,
        raw_low: price,
        split_ratio: null,
      });
    }
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return bars;
}

function request(
  overrides: Partial<PortfolioAlternativeRequest> = {},
): PortfolioAlternativeRequest {
  return {
    baseline_ticker: "VTI",
    alternative_ticker: "ITOT",
    relationship: "like_for_like",
    prospective_view: "similar",
    reason:
      "Both cover the broad U.S. equity market; the evidence does not support a meaningful forward advantage.",
    evidence_ids: ["itot-profile"],
    ...overrides,
  };
}

Deno.test("portfolio comparison uses synchronized adjusted history and equal monthly contributions", () => {
  const baseline = businessBars("2025-09-02", 260, 100, 120);
  const alternative = businessBars("2025-09-02", 260, 100, 130);
  const result = comparePortfolioAlternative(request(), baseline, alternative);

  assertEquals(result.coverage_status, "complete");
  assertEquals(result.period_start, "2025-09-02");
  assertEquals(result.period_end, baseline.at(-1)!.date);
  assertEquals(result.common_sessions, 260);
  assertEquals(result.contribution_count, 12);
  assertEquals(result.baseline_lump_sum_return_pct, "20");
  assertEquals(result.alternative_lump_sum_return_pct, "30");
  assertEquals(result.lump_sum_excess_pct, "10");
  assert(
    Number(result.alternative_monthly_return_pct) >
      Number(result.baseline_monthly_return_pct),
    "higher adjusted-price path did not produce the stronger matched monthly result",
  );
  assertEquals(result.baseline_max_drawdown_pct, "0");
  assertEquals(result.alternative_max_drawdown_pct, "0");
});

Deno.test("portfolio comparison fails closed when synchronized history is too short", () => {
  const result = comparePortfolioAlternative(
    request(),
    businessBars("2026-07-01", 40, 100, 103),
    businessBars("2026-07-01", 40, 100, 105),
  );
  assertEquals(result.coverage_status, "insufficient_history");
  assertEquals(result.baseline_lump_sum_return_pct, null);
  assertEquals(result.alternative_monthly_return_pct, null);
});

Deno.test("portfolio comparison requires approximately one full year, not a lucky half-year", () => {
  const result = comparePortfolioAlternative(
    request(),
    businessBars("2026-01-02", 200, 100, 140),
    businessBars("2026-01-02", 200, 100, 160),
  );
  assertEquals(result.coverage_status, "insufficient_history");
  assertEquals(result.lump_sum_excess_pct, null);
});

Deno.test("portfolio comparison reports missing or unsynchronized history without a verdict", () => {
  const result = comparePortfolioAlternative(
    request({
      relationship: "peer",
      baseline_ticker: "CENX",
      alternative_ticker: "AA",
    }),
    [],
    businessBars("2025-09-02", 260, 100, 110),
  );
  assertEquals(result.coverage_status, "missing_history");
  assertEquals(result.period_start, null);
  assertEquals(result.lump_sum_excess_pct, null);
});

Deno.test("portfolio comparison has exact equal-monthly fixed-point math", () => {
  const baseline = businessBars("2025-09-02", 260, 100, 100);
  baseline.at(-1)!.adjusted_close = "120";
  baseline.at(-1)!.raw_close = "120";
  baseline.at(-1)!.raw_high = "120";
  baseline.at(-1)!.raw_low = "120";
  const alternative = businessBars("2025-09-02", 260, 100, 100);
  const result = comparePortfolioAlternative(request(), baseline, alternative);
  assertEquals(result.baseline_lump_sum_return_pct, "20");
  assertEquals(result.baseline_monthly_return_pct, "20");
  assertEquals(result.alternative_monthly_return_pct, "0");
  assertEquals(result.monthly_excess_pct, "-20");
});

Deno.test("portfolio comparison measures negative return and non-zero drawdown", () => {
  const baseline = businessBars("2025-09-02", 260, 100, 100);
  baseline.forEach((bar, index) => {
    const price = index <= 50
      ? 100 + (20 * index) / 50
      : 120 - (40 * (index - 50)) / 209;
    const value = price.toFixed(6);
    bar.adjusted_close = value;
    bar.raw_close = value;
    bar.raw_high = value;
    bar.raw_low = value;
  });
  const alternative = businessBars("2025-09-02", 260, 100, 100);
  const result = comparePortfolioAlternative(request(), baseline, alternative);
  assertEquals(result.baseline_lump_sum_return_pct, "-20");
  assertEquals(result.baseline_max_drawdown_pct, "33.3333");
  assert(
    Number(result.baseline_monthly_return_pct) < 0,
    "negative monthly result absent",
  );
});

Deno.test("portfolio comparison synchronizes mismatched calendars by date", () => {
  const baseline = businessBars("2025-09-02", 260, 100, 120);
  const alternative = businessBars("2025-09-02", 260, 100, 125).slice(5);
  const result = comparePortfolioAlternative(request(), baseline, alternative);
  assertEquals(result.coverage_status, "complete");
  assertEquals(result.common_sessions, 255);
  assertEquals(result.period_start, alternative[0].date);
  assertEquals(result.period_end, baseline.at(-1)!.date);
});

Deno.test("portfolio comparison reports correlation from synchronized adjusted returns", () => {
  const baseline = businessBars("2025-09-02", 260, 100, 120);
  const alternative = baseline.map((bar) => ({ ...bar }));
  const result = comparePortfolioAlternative(request(), baseline, alternative);

  assertEquals(result.coverage_status, "complete");
  assertEquals(result.daily_return_correlation, "1");
});

Deno.test("personal overlap or concentration evidence can veto an attractive case", () => {
  const overlap = qualifyPersonalAlternative({
    overlap_status: "veto",
    concentration_status: "supported",
  });
  const concentration = qualifyPersonalAlternative({
    overlap_status: "supported",
    concentration_status: "veto",
  });

  assertEquals(overlap.status, "vetoed");
  assertEquals(overlap.reason_codes, ["PERSONAL_OVERLAP_VETO"]);
  assertEquals(concentration.status, "vetoed");
  assertEquals(concentration.reason_codes, ["PERSONAL_CONCENTRATION_VETO"]);
});
