import type { Action, Confidence } from "./contracts.ts";
import { formatFixed, parseFixed } from "./fixed-point.ts";
import type { AdjustedBar } from "./market-data.ts";

export interface DueDecision {
  suggestion_id: number;
  decision_date: string;
  ticker: string;
  bucket: "core" | "growth" | "speculative";
  final_action: Action;
  confidence: Confidence;
  policy_version: number;
  decision_price: string;
  entry_zone_low: string | null;
  entry_zone_high: string | null;
  stop: string | null;
  target: string | null;
  invalidation_price: string | null;
  completed_horizons: number[];
}

export interface OutcomeGrade {
  suggestion_id: number;
  horizon_days: 5 | 21 | 63;
  horizon_sessions: number;
  coverage_status: "incomplete" | "complete" | "missing_history" |
    "missing_benchmark" | "corporate_action_review";
  benchmark_ticker: "VOO" | "VXUS";
  stock_return_pct: string | null;
  benchmark_return_pct: string | null;
  excess_return_pct: string | null;
  mfe_pct: string | null;
  mae_pct: string | null;
  entry_hit_at: string | null;
  stop_hit_at: string | null;
  target_hit_at: string | null;
  invalidation_hit_at: string | null;
  policy_version: number;
  final_action: Action;
  direction_success: boolean | null;
}

const SCALE = 12;
const HORIZONS = new Set([5, 21, 63]);

function divideRounded(numerator: bigint, denominator: bigint): bigint {
  if (denominator <= 0n) throw new Error("invalid denominator");
  const negative = numerator < 0n;
  const magnitude = negative ? -numerator : numerator;
  const rounded = (magnitude + denominator / 2n) / denominator;
  return negative ? -rounded : rounded;
}

function percent(end: bigint, start: bigint): bigint {
  return divideRounded((end - start) * 1_000_000n, start);
}

function pctText(value: bigint): string {
  return formatFixed(value, 4);
}

function fixed(value: string): bigint {
  return parseFixed(value, SCALE);
}

function empty(
  decision: DueDecision,
  horizon: 5 | 21 | 63,
  benchmarkTicker: "VOO" | "VXUS",
  coverageStatus: OutcomeGrade["coverage_status"],
  sessions = 0,
): OutcomeGrade {
  return {
    suggestion_id: decision.suggestion_id,
    horizon_days: horizon,
    horizon_sessions: sessions,
    coverage_status: coverageStatus,
    benchmark_ticker: benchmarkTicker,
    stock_return_pct: null,
    benchmark_return_pct: null,
    excess_return_pct: null,
    mfe_pct: null,
    mae_pct: null,
    entry_hit_at: null,
    stop_hit_at: null,
    target_hit_at: null,
    invalidation_hit_at: null,
    policy_version: decision.policy_version,
    final_action: decision.final_action,
    direction_success: null,
  };
}

function byDate(bars: AdjustedBar[]): Map<string, AdjustedBar> | null {
  const result = new Map<string, AdjustedBar>();
  for (const bar of bars) {
    if (result.has(bar.date)) return null;
    result.set(bar.date, bar);
  }
  return result;
}

function firstHit(
  bars: AdjustedBar[],
  predicate: (low: bigint, high: bigint) => boolean,
): string | null {
  for (const bar of bars) {
    if (predicate(fixed(bar.raw_low), fixed(bar.raw_high))) return bar.date;
  }
  return null;
}

export function gradeDecision(
  decision: DueDecision,
  stockBars: AdjustedBar[],
  benchmarkBars: AdjustedBar[],
  horizon: 5 | 21 | 63,
): OutcomeGrade {
  if (!HORIZONS.has(horizon)) throw new Error("unsupported outcome horizon");
  const benchmarkTicker = decision.ticker === "VXUS" ? "VXUS" : "VOO";
  const stockByDate = byDate(stockBars);
  const benchmarkByDate = byDate(benchmarkBars);
  const decisionBar = stockByDate?.get(decision.decision_date);
  if (!stockByDate || !decisionBar) {
    return empty(decision, horizon, benchmarkTicker, "missing_history");
  }

  const horizonBars = [...stockByDate.values()]
    .filter((bar) => bar.date > decision.decision_date)
    .sort((left, right) => left.date.localeCompare(right.date))
    .slice(0, horizon);
  const sessions = horizonBars.length;
  const benchmarkDecisionBar = benchmarkByDate?.get(decision.decision_date);
  if (!benchmarkByDate || !benchmarkDecisionBar ||
      horizonBars.some((bar) => !benchmarkByDate.has(bar.date))) {
    return empty(decision, horizon, benchmarkTicker, "missing_benchmark", sessions);
  }

  let adjustedStart: bigint;
  let benchmarkStart: bigint;
  let stockReturns: bigint[];
  let stockReturn: bigint | null;
  let benchmarkReturn: bigint | null;
  try {
    adjustedStart = divideRounded(
      fixed(decision.decision_price) * fixed(decisionBar.adjusted_close),
      fixed(decisionBar.raw_close),
    );
    benchmarkStart = fixed(benchmarkDecisionBar.adjusted_close);
    stockReturns = horizonBars.map((bar) => percent(fixed(bar.adjusted_close), adjustedStart));
    stockReturn = sessions ? stockReturns[stockReturns.length - 1] : null;
    const benchmarkEnd = sessions
      ? fixed(benchmarkByDate.get(horizonBars[horizonBars.length - 1].date)!.adjusted_close)
      : null;
    benchmarkReturn = benchmarkEnd === null ? null : percent(benchmarkEnd, benchmarkStart);
  } catch {
    return empty(decision, horizon, benchmarkTicker, "missing_history", sessions);
  }

  const split = horizonBars.some((bar) => bar.split_ratio !== null);
  const complete = sessions === horizon;
  const coverageStatus: OutcomeGrade["coverage_status"] = split
    ? "corporate_action_review"
    : complete ? "complete" : "incomplete";
  const zeroAndReturns = [0n, ...stockReturns];
  const result = empty(decision, horizon, benchmarkTicker, coverageStatus, sessions);
  result.stock_return_pct = stockReturn === null ? null : pctText(stockReturn);
  result.benchmark_return_pct = benchmarkReturn === null ? null : pctText(benchmarkReturn);
  result.excess_return_pct = stockReturn === null || benchmarkReturn === null
    ? null
    : pctText(stockReturn - benchmarkReturn);
  result.mfe_pct = sessions ? pctText(zeroAndReturns.reduce((left, right) => left > right ? left : right)) : null;
  result.mae_pct = sessions ? pctText(zeroAndReturns.reduce((left, right) => left < right ? left : right)) : null;

  if (!split) {
    const entryLow = decision.entry_zone_low === null ? null : fixed(decision.entry_zone_low);
    const entryHigh = decision.entry_zone_high === null ? null : fixed(decision.entry_zone_high);
    result.entry_hit_at = entryLow === null || entryHigh === null
      ? null
      : firstHit(horizonBars, (low, high) => high >= entryLow && low <= entryHigh);
    result.stop_hit_at = decision.stop === null
      ? null
      : firstHit(horizonBars, (low) => low <= fixed(decision.stop!));
    result.target_hit_at = decision.target === null
      ? null
      : firstHit(horizonBars, (_low, high) => high >= fixed(decision.target!));
    result.invalidation_hit_at = decision.invalidation_price === null
      ? null
      : firstHit(horizonBars, (low) => low <= fixed(decision.invalidation_price!));
  }

  if (coverageStatus === "complete" && result.excess_return_pct !== null) {
    const excess = result.excess_return_pct.startsWith("-")
      ? -parseFixed(result.excess_return_pct.slice(1), 4)
      : parseFixed(result.excess_return_pct, 4);
    if (decision.final_action === "buy" || decision.final_action === "add") {
      result.direction_success = excess > 0n && result.stop_hit_at === null;
    } else if (decision.final_action === "reduce" || decision.final_action === "sell") {
      result.direction_success = excess < 0n;
    }
  }
  return result;
}

export function eligibleLearningOutcomes(
  outcomes: readonly OutcomeGrade[],
): OutcomeGrade[] {
  return outcomes.filter((outcome) =>
    HORIZONS.has(outcome.horizon_days) &&
    outcome.horizon_sessions === outcome.horizon_days &&
    outcome.coverage_status === "complete" &&
    outcome.stock_return_pct !== null &&
    outcome.benchmark_return_pct !== null &&
    outcome.excess_return_pct !== null &&
    typeof outcome.direction_success === "boolean"
  );
}

export interface LearningOutcomeSummary {
  policy_version: number;
  horizon_sessions: 5 | 21 | 63;
  benchmark: "VOO" | "VXUS";
  sample_size: number;
  false_positive_count: number;
  false_positive_rate: string;
  limitations: string[];
}

export interface RecordLearningPayload {
  id: string;
  policy_version: number;
  observation_type: "outcome" | "missed-event" | "source-failure" | "noise";
  horizon_days: 0 | 5 | 21 | 63;
  sample_size: number;
  benchmark: string | null;
  observation: {
    status: "observation" | "owner_review";
    evidence_ids: string[];
    limitations: string[];
    metrics: Record<string, unknown>;
    proposed_change: {
      area: string;
      recommendation: string;
      false_positive_rate: string;
    } | null;
  };
  content_hash: string;
}

export function summarizeLearningOutcomes(
  outcomes: readonly OutcomeGrade[],
): LearningOutcomeSummary {
  const eligible = eligibleLearningOutcomes(outcomes);
  if (eligible.length === 0) throw new Error("no eligible learning outcomes");
  const first = eligible[0];
  if (
    eligible.some((outcome) => outcome.policy_version !== first.policy_version)
  ) throw new Error("learning outcomes must share a policy version");
  if (
    eligible.some((outcome) =>
      outcome.benchmark_ticker !== first.benchmark_ticker
    )
  ) throw new Error("learning outcomes must share a benchmark");
  if (
    eligible.some((outcome) => outcome.horizon_days !== first.horizon_days)
  ) throw new Error("learning outcomes must share a horizon");
  const falsePositiveCount = eligible.filter((outcome) =>
    outcome.direction_success === false
  ).length;
  return {
    policy_version: first.policy_version,
    horizon_sessions: first.horizon_days,
    benchmark: first.benchmark_ticker,
    sample_size: eligible.length,
    false_positive_count: falsePositiveCount,
    false_positive_rate: (falsePositiveCount / eligible.length).toFixed(4),
    limitations: ["historical outcomes do not prove future performance"],
  };
}
