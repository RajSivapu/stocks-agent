import type { PortfolioAlternativeRequest } from "./contracts.ts";
export type { PortfolioAlternativeRequest } from "./contracts.ts";
import { formatFixed, parseFixed } from "./fixed-point.ts";
import type { AdjustedBar } from "./market-data.ts";
import {
  personalComparisonReasonCodes,
  type PersonalFactorStatus,
  type PolicyReasonCode,
} from "./policy.ts";

export type AlternativeCoverage =
  | "complete"
  | "insufficient_history"
  | "missing_history";

export interface PortfolioAlternativeComparison
  extends PortfolioAlternativeRequest {
  coverage_status: AlternativeCoverage;
  period_start: string | null;
  period_end: string | null;
  common_sessions: number;
  contribution_count: number;
  baseline_lump_sum_return_pct: string | null;
  alternative_lump_sum_return_pct: string | null;
  lump_sum_excess_pct: string | null;
  baseline_monthly_return_pct: string | null;
  alternative_monthly_return_pct: string | null;
  monthly_excess_pct: string | null;
  baseline_max_drawdown_pct: string | null;
  alternative_max_drawdown_pct: string | null;
  daily_return_correlation: string | null;
}

export interface PersonalAlternativeGate {
  overlap_status: PersonalFactorStatus;
  concentration_status: PersonalFactorStatus;
}

export interface PersonalAlternativeDecision {
  status: "eligible" | "vetoed" | "insufficient";
  reason_codes: PolicyReasonCode[];
}

const SCALE = 12;
const FIXED = 10n ** BigInt(SCALE);
const PERCENT_SCALE = 10_000n;
const MIN_COMMON_SESSIONS = 240;

function divideRounded(numerator: bigint, denominator: bigint): bigint {
  if (denominator <= 0n) throw new Error("invalid denominator");
  const negative = numerator < 0n;
  const magnitude = negative ? -numerator : numerator;
  const rounded = (magnitude + denominator / 2n) / denominator;
  return negative ? -rounded : rounded;
}

function percent(end: bigint, start: bigint): bigint {
  return divideRounded((end - start) * 100n * PERCENT_SCALE, start);
}

function pct(value: bigint): string {
  return formatFixed(value, 4);
}

function empty(
  request: PortfolioAlternativeRequest,
  coverageStatus: AlternativeCoverage,
  commonSessions = 0,
): PortfolioAlternativeComparison {
  return {
    ...request,
    coverage_status: coverageStatus,
    period_start: null,
    period_end: null,
    common_sessions: commonSessions,
    contribution_count: 0,
    baseline_lump_sum_return_pct: null,
    alternative_lump_sum_return_pct: null,
    lump_sum_excess_pct: null,
    baseline_monthly_return_pct: null,
    alternative_monthly_return_pct: null,
    monthly_excess_pct: null,
    baseline_max_drawdown_pct: null,
    alternative_max_drawdown_pct: null,
    daily_return_correlation: null,
  };
}

function uniqueByDate(bars: AdjustedBar[]): Map<string, bigint> | null {
  const values = new Map<string, bigint>();
  try {
    for (const bar of bars) {
      if (values.has(bar.date)) return null;
      values.set(bar.date, parseFixed(bar.adjusted_close, SCALE));
    }
  } catch {
    return null;
  }
  return values;
}

function maxDrawdown(prices: readonly bigint[]): bigint {
  let peak = prices[0];
  let drawdown = 0n;
  for (const price of prices) {
    if (price > peak) peak = price;
    const current = divideRounded((peak - price) * 100n * PERCENT_SCALE, peak);
    if (current > drawdown) drawdown = current;
  }
  return drawdown;
}

function correlation(
  leftPrices: readonly bigint[],
  rightPrices: readonly bigint[],
): string | null {
  const left: number[] = [];
  const right: number[] = [];
  for (let index = 1; index < leftPrices.length; index += 1) {
    left.push(Number(leftPrices[index]) / Number(leftPrices[index - 1]) - 1);
    right.push(
      Number(rightPrices[index]) / Number(rightPrices[index - 1]) - 1,
    );
  }
  if (left.length < 2) return null;
  const leftMean = left.reduce((sum, value) => sum + value, 0) / left.length;
  const rightMean = right.reduce((sum, value) => sum + value, 0) /
    right.length;
  let covariance = 0;
  let leftVariance = 0;
  let rightVariance = 0;
  for (let index = 0; index < left.length; index += 1) {
    const leftDelta = left[index] - leftMean;
    const rightDelta = right[index] - rightMean;
    covariance += leftDelta * rightDelta;
    leftVariance += leftDelta * leftDelta;
    rightVariance += rightDelta * rightDelta;
  }
  const denominator = Math.sqrt(leftVariance * rightVariance);
  if (!Number.isFinite(denominator) || denominator <= Number.EPSILON) {
    return null;
  }
  const value = Math.max(-1, Math.min(1, covariance / denominator));
  if (!Number.isFinite(value)) return null;
  return value.toFixed(4).replace(/\.?0+$/, "");
}

export function qualifyPersonalAlternative(
  gate: PersonalAlternativeGate,
): PersonalAlternativeDecision {
  const reasonCodes = personalComparisonReasonCodes(
    gate.overlap_status,
    gate.concentration_status,
  );
  return {
    status: reasonCodes.some((reason) => reason.endsWith("_VETO"))
      ? "vetoed"
      : reasonCodes.length > 0
      ? "insufficient"
      : "eligible",
    reason_codes: reasonCodes,
  };
}

function monthlyReturn(
  rows: readonly { date: string; baseline: bigint; alternative: bigint }[],
  key: "baseline" | "alternative",
): { value: bigint; contributions: number } {
  const firstByMonth = new Map<string, bigint>();
  for (const row of rows) {
    const month = row.date.slice(0, 7);
    if (!firstByMonth.has(month)) firstByMonth.set(month, row[key]);
  }
  const contributionPrices = [...firstByMonth.values()].slice(-12);
  const contribution = 100n * FIXED;
  let shares = 0n;
  for (const price of contributionPrices) {
    shares += divideRounded(contribution * FIXED, price);
  }
  const terminal = divideRounded(shares * rows.at(-1)![key], FIXED);
  const invested = contribution * BigInt(contributionPrices.length);
  return {
    value: percent(terminal, invested),
    contributions: contributionPrices.length,
  };
}

export function comparePortfolioAlternative(
  request: PortfolioAlternativeRequest,
  baselineBars: AdjustedBar[],
  alternativeBars: AdjustedBar[],
): PortfolioAlternativeComparison {
  const baseline = uniqueByDate(baselineBars);
  const alternative = uniqueByDate(alternativeBars);
  if (
    !baseline || !alternative || baseline.size === 0 || alternative.size === 0
  ) {
    return empty(request, "missing_history");
  }
  const rows = [...baseline.entries()]
    .flatMap(([date, baselinePrice]) => {
      const alternativePrice = alternative.get(date);
      return alternativePrice === undefined
        ? []
        : [{ date, baseline: baselinePrice, alternative: alternativePrice }];
    })
    .sort((left, right) => left.date.localeCompare(right.date));
  if (rows.length < MIN_COMMON_SESSIONS) {
    return empty(request, "insufficient_history", rows.length);
  }
  try {
    const first = rows[0];
    const last = rows.at(-1)!;
    const baselineLump = percent(last.baseline, first.baseline);
    const alternativeLump = percent(last.alternative, first.alternative);
    const baselineMonthly = monthlyReturn(rows, "baseline");
    const alternativeMonthly = monthlyReturn(rows, "alternative");
    if (
      baselineMonthly.contributions < 11 ||
      alternativeMonthly.contributions !== baselineMonthly.contributions
    ) {
      return empty(request, "insufficient_history", rows.length);
    }
    return {
      ...request,
      coverage_status: "complete",
      period_start: first.date,
      period_end: last.date,
      common_sessions: rows.length,
      contribution_count: baselineMonthly.contributions,
      baseline_lump_sum_return_pct: pct(baselineLump),
      alternative_lump_sum_return_pct: pct(alternativeLump),
      lump_sum_excess_pct: pct(alternativeLump - baselineLump),
      baseline_monthly_return_pct: pct(baselineMonthly.value),
      alternative_monthly_return_pct: pct(alternativeMonthly.value),
      monthly_excess_pct: pct(alternativeMonthly.value - baselineMonthly.value),
      baseline_max_drawdown_pct: pct(
        maxDrawdown(rows.map((row) => row.baseline)),
      ),
      alternative_max_drawdown_pct: pct(
        maxDrawdown(rows.map((row) => row.alternative)),
      ),
      daily_return_correlation: correlation(
        rows.map((row) => row.baseline),
        rows.map((row) => row.alternative),
      ),
    };
  } catch {
    return empty(request, "missing_history", rows.length);
  }
}
