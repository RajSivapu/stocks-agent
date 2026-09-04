import type {
  AlternativeRelationship,
  LongTermCompanionRequest,
  PolicyContext,
} from "./contracts.ts";
import type { AdjustedBar } from "./market-data.ts";

export interface CompanionRoleDecision {
  allowed: boolean;
  reason: string;
  recurring_plan_review_eligible: boolean;
}

export interface CompanionQualification {
  status: "qualified" | "insufficient";
  reason:
    | "qualified"
    | "baseline_not_current"
    | "relationship_mismatch"
    | "substitute_is_not_companion"
    | "replacement_is_not_companion"
    | "role_not_allowed";
  policy: CompanionRoleDecision;
}

export interface CompanionHorizon {
  years: 3 | 5 | 10;
  period_start: string;
  period_end: string;
  common_sessions: number;
  baseline_annualized_return_pct: string;
  companion_annualized_return_pct: string;
  baseline_max_drawdown_pct: string;
  companion_max_drawdown_pct: string;
  daily_return_correlation: string;
}

export interface RollingContributionScenario {
  monthly_contribution_usd: "100";
  total_contributed_usd: "1200";
  sample_windows: number;
  weak_ending_value_usd: string;
  middle_ending_value_usd: string;
  strong_ending_value_usd: string;
}

export interface LongTermCompanionAnalysis extends LongTermCompanionRequest {
  qualification_status: "qualified" | "insufficient";
  qualification_reason: string;
  recurring_plan_review_eligible: boolean;
  horizons: CompanionHorizon[];
  rolling_one_year: RollingContributionScenario | null;
}

interface SynchronizedRow {
  date: string;
  baseline: number;
  companion: number;
}

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const MIN_SESSIONS_PER_YEAR = 240;
const HORIZONS = [3, 5, 10] as const;

export function qualifyCompanion(
  request: LongTermCompanionRequest,
  relationship: AlternativeRelationship,
  context: PolicyContext,
): CompanionQualification {
  const currentBaseline =
    context.holdings.some((holding) =>
      holding.ticker === request.baseline_ticker
    ) || context.owner_plans.some((plan) =>
      plan.active && plan.ticker === request.baseline_ticker
    );
  const policy = qualifyCompanionRole(request, relationship);
  if (!currentBaseline) {
    return { status: "insufficient", reason: "baseline_not_current", policy };
  }
  if (
    request.companion_ticker === "ITOT" || request.companion_ticker === "SCHB"
  ) {
    return {
      status: "insufficient",
      reason: "substitute_is_not_companion",
      policy,
    };
  }
  if (request.companion_ticker === "VT") {
    return {
      status: "insufficient",
      reason: "replacement_is_not_companion",
      policy,
    };
  }
  if (relationship !== request.role) {
    return { status: "insufficient", reason: "relationship_mismatch", policy };
  }
  if (!policy.allowed) {
    return { status: "insufficient", reason: "role_not_allowed", policy };
  }
  return { status: "qualified", reason: "qualified", policy };
}

export function qualifyCompanionRole(
  request: LongTermCompanionRequest,
  relationship: AlternativeRelationship,
): CompanionRoleDecision {
  const deny = (reason: string): CompanionRoleDecision => ({
    allowed: false,
    reason,
    recurring_plan_review_eligible: false,
  });
  if (relationship !== request.role) {
    return deny(
      "The nominated role does not match the validated comparison relationship.",
    );
  }
  if (
    request.companion_ticker === "ITOT" || request.companion_ticker === "SCHB"
  ) {
    return deny(
      "This is a like-for-like U.S. core substitute, not an additive companion.",
    );
  }
  if (request.companion_ticker === "VT") {
    return deny(
      "VT is a global-core replacement with overlapping U.S. exposure, not a companion beside VTI.",
    );
  }
  const expectedRole = request.companion_ticker === "VXUS"
    ? "diversifier"
    : request.companion_ticker === "VOO" || request.companion_ticker === "SCHD"
    ? "tilt"
    : "satellite";
  if (request.role !== expectedRole) {
    return deny(
      expectedRole === "satellite"
        ? "An individual company or unsupported fund can qualify only as a concentrated satellite."
        : `Gateway fund policy classifies ${request.companion_ticker} as a ${expectedRole}.`,
    );
  }
  return {
    allowed: true,
    reason:
      "Gateway role policy accepted the candidate for long-term research.",
    recurring_plan_review_eligible: request.baseline_ticker === "VTI" &&
      request.companion_ticker === "VXUS" && request.role === "diversifier",
  };
}

function format(value: number, decimals = 4): string {
  if (!Number.isFinite(value)) throw new Error("invalid computed value");
  const rounded = Math.abs(value) < 0.5 * 10 ** -decimals ? 0 : value;
  return rounded.toFixed(decimals).replace(/\.?0+$/, "");
}

function pricesByDate(
  bars: readonly AdjustedBar[],
): Map<string, number> | null {
  const output = new Map<string, number>();
  for (const bar of bars) {
    const price = Number(bar.adjusted_close);
    const parsed = new Date(`${bar.date}T00:00:00.000Z`);
    if (
      !DATE_PATTERN.test(bar.date) ||
      Number.isNaN(parsed.valueOf()) ||
      parsed.toISOString().slice(0, 10) !== bar.date ||
      !Number.isFinite(price) || price <= 0 || output.has(bar.date)
    ) {
      return null;
    }
    output.set(bar.date, price);
  }
  return output;
}

function synchronize(
  baselineBars: readonly AdjustedBar[],
  companionBars: readonly AdjustedBar[],
): SynchronizedRow[] | null {
  const baseline = pricesByDate(baselineBars);
  const companion = pricesByDate(companionBars);
  if (!baseline || !companion || baseline.size === 0 || companion.size === 0) {
    return null;
  }
  return [...baseline.entries()].flatMap(([date, baselinePrice]) => {
    const companionPrice = companion.get(date);
    return companionPrice === undefined
      ? []
      : [{ date, baseline: baselinePrice, companion: companionPrice }];
  }).sort((left, right) => left.date.localeCompare(right.date));
}

function cutoffDate(endDate: string, years: number): string {
  const cutoff = new Date(`${endDate}T00:00:00.000Z`);
  cutoff.setUTCFullYear(cutoff.getUTCFullYear() - years);
  return cutoff.toISOString().slice(0, 10);
}

function addCalendarDays(date: string, days: number): string {
  const instant = new Date(`${date}T00:00:00.000Z`);
  instant.setUTCDate(instant.getUTCDate() + days);
  return instant.toISOString().slice(0, 10);
}

function annualizedReturn(
  prices: readonly number[],
  start: string,
  end: string,
): number {
  const elapsedDays = (Date.parse(`${end}T00:00:00.000Z`) -
    Date.parse(`${start}T00:00:00.000Z`)) /
    86_400_000;
  if (elapsedDays <= 0) throw new Error("invalid history period");
  return (Math.pow(prices.at(-1)! / prices[0], 365.2425 / elapsedDays) - 1) *
    100;
}

function maxDrawdown(prices: readonly number[]): number {
  let peak = prices[0];
  let largest = 0;
  for (const price of prices) {
    if (price > peak) peak = price;
    largest = Math.max(largest, (peak - price) / peak);
  }
  return largest * 100;
}

function correlation(
  leftPrices: readonly number[],
  rightPrices: readonly number[],
): number | null {
  const left: number[] = [];
  const right: number[] = [];
  for (let index = 1; index < leftPrices.length; index += 1) {
    left.push(leftPrices[index] / leftPrices[index - 1] - 1);
    right.push(rightPrices[index] / rightPrices[index - 1] - 1);
  }
  if (left.length < 2) return null;
  const leftMean = left.reduce((sum, value) => sum + value, 0) / left.length;
  const rightMean = right.reduce((sum, value) => sum + value, 0) / right.length;
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
  const value = covariance / denominator;
  return Number.isFinite(value) ? Math.max(-1, Math.min(1, value)) : null;
}

function horizon(
  rows: readonly SynchronizedRow[],
  years: 3 | 5 | 10,
): CompanionHorizon | null {
  const periodEnd = rows.at(-1)!.date;
  const requestedStart = cutoffDate(periodEnd, years);
  const periodRows = rows.filter((row) => row.date >= requestedStart);
  if (
    periodRows.length < years * MIN_SESSIONS_PER_YEAR ||
    periodRows[0].date > addCalendarDays(requestedStart, 10)
  ) return null;
  const baseline = periodRows.map((row) => row.baseline);
  const companion = periodRows.map((row) => row.companion);
  const pairedCorrelation = correlation(baseline, companion);
  if (pairedCorrelation === null) return null;
  try {
    return {
      years,
      period_start: periodRows[0].date,
      period_end: periodEnd,
      common_sessions: periodRows.length,
      baseline_annualized_return_pct: format(
        annualizedReturn(baseline, periodRows[0].date, periodEnd),
      ),
      companion_annualized_return_pct: format(
        annualizedReturn(companion, periodRows[0].date, periodEnd),
      ),
      baseline_max_drawdown_pct: format(maxDrawdown(baseline)),
      companion_max_drawdown_pct: format(maxDrawdown(companion)),
      daily_return_correlation: format(pairedCorrelation),
    };
  } catch {
    return null;
  }
}

function nearestRank(sorted: readonly number[], percentile: number): number {
  const index = Math.max(0, Math.ceil(percentile * sorted.length) - 1);
  return sorted[index];
}

function rollingContribution(
  rows: readonly SynchronizedRow[],
): RollingContributionScenario | null {
  const firstByMonth = new Map<string, number>();
  for (const row of rows) {
    const month = row.date.slice(0, 7);
    if (!firstByMonth.has(month)) firstByMonth.set(month, row.companion);
  }
  const monthly = [...firstByMonth.entries()].map(([month, price]) => ({
    month,
    price,
  }));
  if (monthly.length < 24) return null;
  const endingValues: number[] = [];
  for (let start = 0; start <= monthly.length - 12; start += 1) {
    const window = monthly.slice(start, start + 12);
    const consecutive = window.every((item, index) => {
      if (index === 0) return true;
      const [previousYear, previousMonth] = window[index - 1].month
        .split("-").map(Number);
      const [year, month] = item.month.split("-").map(Number);
      return year * 12 + month === previousYear * 12 + previousMonth + 1;
    });
    if (!consecutive) continue;
    const shares = window.reduce((sum, item) => sum + 100 / item.price, 0);
    const endingValue = shares * window.at(-1)!.price;
    if (!Number.isFinite(endingValue) || endingValue <= 0) return null;
    endingValues.push(endingValue);
  }
  if (endingValues.length === 0) return null;
  endingValues.sort((left, right) => left - right);
  return {
    monthly_contribution_usd: "100",
    total_contributed_usd: "1200",
    sample_windows: endingValues.length,
    weak_ending_value_usd: format(nearestRank(endingValues, 0.10), 2),
    middle_ending_value_usd: format(nearestRank(endingValues, 0.50), 2),
    strong_ending_value_usd: format(nearestRank(endingValues, 0.90), 2),
  };
}

function insufficient(
  request: LongTermCompanionRequest,
  policy: CompanionRoleDecision,
  reason: string,
): LongTermCompanionAnalysis {
  return {
    ...request,
    qualification_status: "insufficient",
    qualification_reason: reason,
    recurring_plan_review_eligible: false,
    horizons: [],
    rolling_one_year: null,
  };
}

export function analyzeLongTermCompanion(
  request: LongTermCompanionRequest,
  baselineBars: readonly AdjustedBar[],
  companionBars: readonly AdjustedBar[],
  policy: CompanionRoleDecision,
): LongTermCompanionAnalysis {
  if (!policy.allowed) return insufficient(request, policy, policy.reason);
  const rows = synchronize(baselineBars, companionBars);
  if (!rows || rows.length === 0) {
    return insufficient(
      request,
      policy,
      "Synchronized adjusted history was missing or malformed.",
    );
  }
  const horizons = HORIZONS.flatMap((years) => {
    const result = horizon(rows, years);
    return result ? [result] : [];
  });
  const rolling = rollingContribution(rows);
  if (!horizons.some((row) => row.years === 3) || !rolling) {
    return insufficient(
      request,
      policy,
      "At least three years of synchronized adjusted history is required.",
    );
  }
  return {
    ...request,
    qualification_status: "qualified",
    qualification_reason: policy.reason,
    recurring_plan_review_eligible: policy.recurring_plan_review_eligible,
    horizons,
    rolling_one_year: rolling,
  };
}
