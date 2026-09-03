import type {
  Action,
  AlertCondition,
  AlertRuleSnapshot,
  DecisionCandidate,
  HoldingState,
  PolicyConfig,
  PolicyContext,
  VerifiedQuote,
} from "./contracts.ts";
import { formatFixed, multiplyFixed, parseFixed } from "./fixed-point.ts";
import {
  isRegularSession,
  ownerLocalDate,
  quoteAllowedForPhase,
} from "./market-calendar.ts";

export type PolicyReasonCode =
  | "INVALID_SCHEMA" | "QUOTE_MISSING" | "QUOTE_STALE" | "QUOTE_SESSION_MISMATCH"
  | "PRICE_RELATION_INVALID" | "AMOUNT_SHARES_MISMATCH" | "CURRENT_EVIDENCE_MISSING"
  | "ANALYST_INCOMPLETE" | "CHECKER_INCOMPLETE" | "CHECKER_DOWNGRADE" | "CHECKER_VETO"
  | "LOW_CONFIDENCE" | "ACTION_HOLDING_MISMATCH" | "SELL_EXCEEDS_HOLDING"
  | "POSITION_CAP_EXCEEDED" | "STOP_REQUIRED" | "TRADE_RISK_EXCEEDED"
  | "REWARD_RISK_TOO_LOW" | "PORTFOLIO_VALUE_INCOMPLETE" | "DAILY_LOSS_LOCKOUT"
  | "CONSECUTIVE_LOSS_LOCKOUT" | "SPECULATIVE_LEARNING_ONLY" | "OWNER_PLAN_MISMATCH"
  | "ALERT_ALREADY_ACTIVE" | "NARRATIVE_REJECTED" | "OUTSIDE_SESSION_CONDITIONAL"
  | "CALENDAR_COVERAGE_MISSING";

export interface PolicyEvaluation {
  evaluation_id: string;
  candidate_id: string;
  raw_action: Action;
  final_action: Action | null;
  status: "approved" | "downgraded" | "vetoed";
  reason_codes: PolicyReasonCode[];
  explanations: string[];
  normalized: {
    ticker: string;
    verified_price: string;
    quote_as_of: string;
    quote_source: string;
    position_value_after: string | null;
    total_investable_value: string | null;
    dollars_at_risk: string | null;
    reward_risk_milli: string | null;
  };
  holding_state_change: {
    ticker: string;
    high_water_price: string;
    stop_alert_active: boolean;
    stop_near_alert_active: boolean;
    target_near_alert_active: boolean;
    target_alert_active: boolean;
  } | null;
  candidate: DecisionCandidate;
}

const ACTIONABLE = new Set<Action>(["buy", "add", "reduce", "sell"]);
const BUY_SIDE = new Set<Action>(["buy", "add"]);
const SELL_SIDE = new Set<Action>(["reduce", "sell"]);
const PURE_ALERTS = new Set([
  "stop_near", "stop_breach", "target_near", "target_hit", "thesis_break",
]);
const VETO_CODES = new Set<PolicyReasonCode>([
  "INVALID_SCHEMA", "PRICE_RELATION_INVALID", "ACTION_HOLDING_MISMATCH",
  "SELL_EXCEEDS_HOLDING", "CHECKER_VETO", "OWNER_PLAN_MISMATCH",
]);
const INFORMATIONAL_CODES = new Set<PolicyReasonCode>(["OUTSIDE_SESSION_CONDITIONAL"]);

function alertSession(phase: DecisionCandidate["phase"]): AlertRuleSnapshot["session"] {
  if (phase === "pre-market") return "pre_market";
  if (phase === "post-market") return "post_market";
  return "regular";
}

function draftValidity(candidate: DecisionCandidate, now: Date): string {
  if (candidate.valid_until !== null) {
    return new Date(`${candidate.valid_until}T21:00:00.000Z`).toISOString();
  }
  return new Date(now.valueOf() + 24 * 60 * 60 * 1000).toISOString();
}

export function draftFromEvaluation(
  evaluation: PolicyEvaluation,
  context: PolicyContext,
  config: PolicyConfig,
  now: Date,
  newId: () => string = () => crypto.randomUUID(),
): AlertRuleSnapshot | null {
  if (!config.alerts_v3 || (!config.alerts_v3.enabled && !config.alerts_v3.shadow) ||
    evaluation.status !== "approved" || evaluation.final_action === null ||
    !evaluation.candidate.analyst.completed || !evaluation.candidate.checker.completed ||
    evaluation.candidate.checker.verdict !== "approve" ||
    !evaluation.candidate.evidence.some((item) =>
      item.status === "fresh" && item.observed_at !== null
    )) return null;

  const candidate = evaluation.candidate;
  let conditions: AlertCondition[] | null = null;
  let severity: AlertRuleSnapshot["severity"] = "review";
  let confirmation: AlertRuleSnapshot["confirmation"] = "bar_close";
  if (candidate.notification_kind === "entry_trigger" &&
    candidate.entry_zone_low !== null && candidate.entry_zone_high !== null) {
    conditions = [{
      kind: "price_zone", operator: "inside", left: candidate.entry_zone_low,
      right: candidate.entry_zone_high, timeframe: "quote",
    }];
    confirmation = "two_quote";
  } else if (candidate.notification_kind === "stop_breach") {
    const holding = context.holdings.find((item) => item.ticker === candidate.ticker);
    if (holding?.stop !== null && holding?.stop !== undefined) {
      conditions = [{
        kind: "recorded_stop", operator: "below", left: holding.stop, right: null,
        timeframe: "quote",
      }];
      severity = "critical";
      confirmation = "two_quote";
    }
  } else if (candidate.notification_kind === "target_hit") {
    const holding = context.holdings.find((item) => item.ticker === candidate.ticker);
    if (holding?.target !== null && holding?.target !== undefined) {
      conditions = [{
        kind: "recorded_target", operator: "above", left: holding.target, right: null,
        timeframe: "quote",
      }];
      severity = "update";
      confirmation = "two_quote";
    }
  }
  if (conditions === null) return null;
  const ruleId = newId();
  return {
    rule_id: ruleId,
    version: 1,
    state: "draft",
    ticker: candidate.ticker,
    profile: config.alerts_v3.profile,
    severity,
    session: alertSession(candidate.phase),
    confirmation,
    conditions,
    cooldown_seconds: severity === "critical" ? 1_200 : 14_400,
    fire_limit: 3,
    valid_until: draftValidity(candidate, now),
    owner_note: "",
  };
}

function signedFixed(value: string, scale: number): bigint {
  if (value.startsWith("-")) return -parseFixed(value.slice(1), scale);
  return parseFixed(value, scale);
}

function money(priceMicros: bigint, shares: string): bigint {
  return multiplyFixed(priceMicros, parseFixed(shares, 8), 8);
}

function max(left: bigint, right: bigint): bigint {
  return left > right ? left : right;
}

function validPositive(value: string | null, scale: number): bigint | null {
  if (value === null) return null;
  const parsed = parseFixed(value, scale);
  return parsed > 0n ? parsed : null;
}

function parseHoldingValue(
  holding: HoldingState,
  quote: VerifiedQuote,
): bigint {
  return money(parseFixed(quote.price, 6), holding.shares);
}

function deriveHoldingState(
  holding: HoldingState | undefined,
  verifiedQuote: VerifiedQuote | null,
  alertNearBps: number,
): PolicyEvaluation["holding_state_change"] {
  if (!holding || !verifiedQuote) return null;
  try {
    const price = parseFixed(verifiedQuote.price, 6);
    const priorHigh = holding.high_water_price === null
      ? 0n
      : parseFixed(holding.high_water_price, 6);
    const stop = holding.stop === null ? null : validPositive(holding.stop, 6);
    const target = holding.target === null ? null : validPositive(holding.target, 6);
    const stopBreach = stop !== null && price <= stop;
    const stopNear = stop !== null && price >= stop &&
      (price - stop) * 10_000n < stop * BigInt(alertNearBps);
    const targetHit = target !== null && price >= target;
    const targetNear = target !== null && target >= price &&
      (target - price) * 10_000n < price * BigInt(alertNearBps);
    return {
      ticker: holding.ticker,
      high_water_price: formatFixed(max(priorHigh, price), 6),
      stop_alert_active: stopBreach,
      stop_near_alert_active: stopNear,
      target_near_alert_active: targetNear,
      target_alert_active: targetHit,
    };
  } catch {
    return null;
  }
}

export function evaluateCandidate(
  candidate: DecisionCandidate,
  context: PolicyContext,
  config: PolicyConfig,
  verifiedQuote: VerifiedQuote | null,
  now: Date,
  newId: () => string = () => crypto.randomUUID(),
): PolicyEvaluation {
  const reasons: PolicyReasonCode[] = [];
  const explanations: string[] = [];
  const add = (code: PolicyReasonCode, explanation: string) => {
    if (!reasons.includes(code)) {
      reasons.push(code);
      explanations.push(explanation);
    }
  };
  const marketDate = ownerLocalDate(now);
  const holding = context.holdings.find((item) => item.ticker === candidate.ticker);
  let candidateQuoteAllowed = false;
  let positionValueAfter: bigint | null = null;
  let totalInvestable: bigint | null = null;
  let dollarsAtRisk: bigint | null = null;
  let rewardRiskMilli: bigint | null = null;
  let schemaInvalid = false;

  if (marketDate.slice(0, 4) !== String(config.market_calendar_year)) {
    add("CALENDAR_COVERAGE_MISSING", "The owner-local market date is outside the reviewed calendar year.");
  }
  if (!verifiedQuote || verifiedQuote.ticker !== candidate.ticker) {
    add("QUOTE_MISSING", "An independent quote for the candidate is unavailable.");
  } else if (!(candidateQuoteAllowed = quoteAllowedForPhase(
    candidate.phase, verifiedQuote, now, config.nyse_holidays,
    config.max_actionable_quote_age_minutes,
  ))) {
    if ((candidate.phase === "intraday" || candidate.phase === "on-demand") &&
      verifiedQuote.market_state === "REGULAR") {
      add("QUOTE_STALE", "The regular-session quote exceeds the reviewed freshness limit.");
    } else {
      add("QUOTE_SESSION_MISMATCH", "The quote does not belong to the phase's authoritative session.");
    }
  } else if (candidate.phase === "on-demand" && !isRegularSession(now, config.nyse_holidays)) {
    add("OUTSIDE_SESSION_CONDITIONAL", "Outside-session conclusions are conditional on the latest official close.");
    if (candidate.notification_kind === "entry_trigger") {
      add("QUOTE_SESSION_MISMATCH", "Entry triggers require a live regular session.");
    }
  }
  const holdingState = deriveHoldingState(
    holding,
    candidateQuoteAllowed ? verifiedQuote : null,
    config.alert_near_bps,
  );
  if (candidate.phase === "pre-market" && candidate.notification_kind === "entry_trigger") {
    add("QUOTE_SESSION_MISMATCH", "Pre-market data cannot authorize an entry trigger.");
  }

  const hasCurrentEvidence = candidate.evidence.some((item) => item.status === "fresh" && item.observed_at !== null);
  if ((ACTIONABLE.has(candidate.action) || candidate.prior_suggestion_ids.length > 0 || PURE_ALERTS.has(candidate.notification_kind)) &&
    !hasCurrentEvidence) {
    add("CURRENT_EVIDENCE_MISSING", "The candidate has no current, timestamped evidence.");
  }
  if (!candidate.analyst.completed || candidate.analyst.action !== candidate.action) {
    add("ANALYST_INCOMPLETE", "The Analyst pass is incomplete or does not match the proposed action.");
  }
  if (!candidate.checker.completed) {
    add("CHECKER_INCOMPLETE", "The independent Checker pass is incomplete.");
  } else if (candidate.checker.verdict === "downgrade") {
    add("CHECKER_DOWNGRADE", "The Checker downgraded the proposal.");
  } else if (candidate.checker.verdict === "veto") {
    add("CHECKER_VETO", "The Checker vetoed the proposal.");
  }
  if (BUY_SIDE.has(candidate.action) && candidate.confidence === "low") {
    add("LOW_CONFIDENCE", "Buy-side actions require at least medium confidence.");
  }

  if ((candidate.action === "buy" && holding) ||
    ((candidate.action === "add" || SELL_SIDE.has(candidate.action)) && !holding) ||
    (candidate.action === "add" && holding?.bucket !== candidate.bucket)) {
    add("ACTION_HOLDING_MISMATCH", "The action does not match authoritative ownership state.");
  }

  const isPureAlert = PURE_ALERTS.has(candidate.notification_kind);
  try {
    const amount = candidate.proposed_amount === null ? null : validPositive(candidate.proposed_amount, 6);
    const shares = candidate.proposed_shares === null ? null : validPositive(candidate.proposed_shares, 8);
    if (BUY_SIDE.has(candidate.action) && !isPureAlert && (amount === null || shares === null)) {
      add("AMOUNT_SHARES_MISMATCH", "Buy-side proposals require positive amount and share values.");
    }
    if (SELL_SIDE.has(candidate.action) && !isPureAlert && (candidate.proposed_amount !== null || shares === null)) {
      add("AMOUNT_SHARES_MISMATCH", "Sell-side proposals require shares and forbid a proposed amount.");
    }
    if ((!ACTIONABLE.has(candidate.action) || isPureAlert) &&
      (candidate.proposed_amount !== null || candidate.proposed_shares !== null)) {
      add("INVALID_SCHEMA", "This action or alert cannot carry trade sizing fields.");
      schemaInvalid = true;
    }
    if (candidate.action === "sell" && holding && shares !== null &&
      shares > parseFixed(holding.shares, 8)) {
      add("SELL_EXCEEDS_HOLDING", "Sell shares exceed the authoritative holding.");
    }
    if (BUY_SIDE.has(candidate.action) && amount !== null && shares !== null && verifiedQuote) {
      const calculated = money(parseFixed(verifiedQuote.price, 6), candidate.proposed_shares!);
      const difference = calculated > amount ? calculated - amount : amount - calculated;
      if (difference > 10_000n) {
        add("AMOUNT_SHARES_MISMATCH", "Amount and shares differ by more than one cent at the verified quote.");
      }
    }
  } catch {
    add("INVALID_SCHEMA", "A decimal field is invalid.");
    schemaInvalid = true;
  }

  let ownerPlanMatched = false;
  if (candidate.decision_mode === "owner_plan") {
    const plans = context.owner_plans.filter((plan) => plan.active && plan.ticker === candidate.ticker);
    try {
      const plan = plans.length === 1 ? plans[0] : undefined;
      const expectedAction = holding ? "add" : "buy";
      ownerPlanMatched = Boolean(plan && plan.cadence === "monthly" && plan.bucket === "core" &&
        candidate.bucket === "core" && candidate.action === expectedAction &&
        config.broad_core_etfs.includes(candidate.ticker) && marketDate >= plan.next_due_on &&
        candidate.proposed_amount !== null &&
        parseFixed(candidate.proposed_amount, 6) === parseFixed(plan.amount, 6));
    } catch {
      ownerPlanMatched = false;
    }
    if (!ownerPlanMatched) add("OWNER_PLAN_MISMATCH", "The proposal does not match one unique due owner plan.");
  }

  let entryHigh: bigint | null = null;
  let candidateStop: bigint | null = null;
  let candidateTarget: bigint | null = null;
  if (BUY_SIDE.has(candidate.action) && !ownerPlanMatched) {
    if (candidate.entry_zone_low === null || candidate.entry_zone_high === null ||
      candidate.stop === null || candidate.target === null) {
      add("STOP_REQUIRED", "Discretionary Buy/Add requires entry, stop, and target levels.");
    } else {
      try {
        const low = validPositive(candidate.entry_zone_low, 6);
        entryHigh = validPositive(candidate.entry_zone_high, 6);
        candidateStop = validPositive(candidate.stop, 6);
        candidateTarget = validPositive(candidate.target, 6);
        if (low === null || entryHigh === null || candidateStop === null || candidateTarget === null ||
          !(candidateStop < low && low <= entryHigh && entryHigh < candidateTarget)) {
          add("PRICE_RELATION_INVALID", "Required long levels must satisfy stop < low <= high < target.");
        }
      } catch {
        add("PRICE_RELATION_INVALID", "Required long price levels are invalid.");
      }
    }
  }

  if (BUY_SIDE.has(candidate.action)) {
    let portfolioComplete = context.portfolio_command_coverage_complete && context.realized_pnl_today !== null;
    let holdingsValue = 0n;
    const holdingValues = new Map<string, bigint>();
    for (const item of context.holdings) {
      const itemQuote = item.ticker === candidate.ticker && verifiedQuote
        ? verifiedQuote
        : context.holding_quotes[item.ticker];
      try {
        if (!itemQuote || itemQuote.ticker !== item.ticker ||
          !quoteAllowedForPhase(candidate.phase, itemQuote, now, config.nyse_holidays, config.max_actionable_quote_age_minutes)) {
          portfolioComplete = false;
          continue;
        }
        const value = parseHoldingValue(item, itemQuote);
        holdingsValue += value;
        holdingValues.set(item.ticker, value);
      } catch {
        portfolioComplete = false;
      }
    }
    if (!portfolioComplete) {
      add("PORTFOLIO_VALUE_INCOMPLETE", "Portfolio value or realized-loss coverage is incomplete.");
    } else {
      try {
        totalInvestable = holdingsValue + BigInt(config.monthly_investment_micros);
        const bucketBudget = totalInvestable * BigInt(config.allocation_bps[candidate.bucket]) / 10_000n;
        const positionCap = bucketBudget * BigInt(config.max_position_bps_of_bucket[candidate.bucket]) / 10_000n;
        const quotePrice = parseFixed(verifiedQuote!.price, 6);
        const sizingPrice = entryHigh === null ? quotePrice : max(quotePrice, entryHigh);
        const newTradeValue = money(sizingPrice, candidate.proposed_shares!);
        positionValueAfter = (holdingValues.get(candidate.ticker) ?? 0n) + newTradeValue;
        if (positionValueAfter > positionCap) {
          add("POSITION_CAP_EXCEEDED", "The resulting position exceeds the reviewed bucket cap.");
        }
        if (!ownerPlanMatched && entryHigh !== null && candidateStop !== null && candidateTarget !== null) {
          let existingRisk = 0n;
          if (candidate.action === "add" && holding) {
            const recordedStop = holding.stop === null ? null : validPositive(holding.stop, 6);
            if (recordedStop === null) {
              add("STOP_REQUIRED", "An Add requires a positive recorded holding stop.");
            } else {
              const average = parseFixed(holding.avg_cost, 6);
              existingRisk = money(average > recordedStop ? average - recordedStop : 0n, holding.shares);
            }
          }
          const newRisk = money(entryHigh - candidateStop, candidate.proposed_shares!);
          dollarsAtRisk = existingRisk + newRisk;
          if (dollarsAtRisk * 10_000n > totalInvestable * BigInt(config.max_trade_risk_bps[candidate.bucket])) {
            add("TRADE_RISK_EXCEEDED", "Conservative dollars at risk exceed the reviewed portfolio limit.");
          }
          rewardRiskMilli = (candidateTarget - entryHigh) * 1_000n / (entryHigh - candidateStop);
          if (rewardRiskMilli < BigInt(config.min_reward_risk_milli)) {
            add("REWARD_RISK_TOO_LOW", "Reward-to-risk is below the reviewed minimum.");
          }
        }
        if (context.realized_pnl_today !== null) {
          const pnl = signedFixed(context.realized_pnl_today, 6);
          if (pnl < 0n && -pnl * 10_000n >= totalInvestable * BigInt(config.daily_loss_limit_bps)) {
            add("DAILY_LOSS_LOCKOUT", "The confirmed daily loss limit is active.");
          }
        }
        if (candidate.bucket === "speculative") {
          let speculativeValue = 0n;
          for (const item of context.holdings) {
            if (item.bucket === "speculative") speculativeValue += holdingValues.get(item.ticker) ?? 0n;
          }
          if (speculativeValue < BigInt(config.speculative_go_live_bucket_micros)) {
            add("SPECULATIVE_LEARNING_ONLY", "The speculative bucket remains paper-only.");
          }
        }
      } catch {
        add("PORTFOLIO_VALUE_INCOMPLETE", "Required portfolio arithmetic could not be completed.");
      }
    }
    if (context.consecutive_completed_losses >= config.circuit_breaker_consecutive_losses) {
      add("CONSECUTIVE_LOSS_LOCKOUT", "The confirmed-loss circuit breaker is active.");
    }
  }

  let alertSuppressed = false;
  if (PURE_ALERTS.has(candidate.notification_kind)) {
    if (!holding || !holdingState || !verifiedQuote || reasons.includes("QUOTE_STALE") ||
      reasons.includes("QUOTE_MISSING") || reasons.includes("QUOTE_SESSION_MISMATCH")) {
      add("ACTION_HOLDING_MISMATCH", "A holding alert requires a holding and authoritative quote.");
      alertSuppressed = true;
    } else {
      const flags = {
        stop_near: [holding.stop, holdingState.stop_near_alert_active, holding.stop_near_alert_active],
        stop_breach: [holding.stop, holdingState.stop_alert_active, holding.stop_alert_active],
        target_near: [holding.target, holdingState.target_near_alert_active, holding.target_near_alert_active],
        target_hit: [holding.target, holdingState.target_alert_active, holding.target_alert_active],
      } as const;
      if (candidate.notification_kind === "thesis_break") {
        const independentlyEvidenced = candidate.evidence.some((item) =>
          item.status === "fresh" && ["fundamentals", "news", "event"].includes(item.kind)
        );
        if (!independentlyEvidenced) {
          add("CURRENT_EVIDENCE_MISSING", "Thesis breaks require fresh non-price evidence.");
          alertSuppressed = true;
        }
      } else {
        const mechanicalKind = candidate.notification_kind as keyof typeof flags;
        const [threshold, condition, active] = flags[mechanicalKind];
        if (threshold === null || !condition) {
          add("PRICE_RELATION_INVALID", "The authoritative alert threshold has not fired.");
          alertSuppressed = true;
        } else if (active) {
          add("ALERT_ALREADY_ACTIVE", "This alert edge is already active.");
          alertSuppressed = true;
        }
        if ((candidate.notification_kind === "stop_near" || candidate.notification_kind === "stop_breach") &&
          holding.hold_override_until !== null && holding.hold_override_until >= marketDate) {
          add("ALERT_ALREADY_ACTIVE", "The owner's unexpired hold override suppresses mechanical stop alerts.");
          alertSuppressed = true;
        }
      }
    }
  }

  const blockingReasons = reasons.filter((reason) => !INFORMATIONAL_CODES.has(reason));
  const vetoed = schemaInvalid || blockingReasons.some((reason) => VETO_CODES.has(reason)) ||
    (candidate.notification_kind === "entry_trigger" && reasons.includes("QUOTE_SESSION_MISMATCH"));
  let finalAction: Action | null = candidate.action;
  if (vetoed) {
    finalAction = null;
  } else if (alertSuppressed) {
    finalAction = null;
  } else if (blockingReasons.length > 0) {
    if (BUY_SIDE.has(candidate.action)) finalAction = "watch";
    else if (SELL_SIDE.has(candidate.action)) finalAction = "hold";
  }
  const status = finalAction === null
    ? "vetoed"
    : (finalAction !== candidate.action || blockingReasons.length > 0 ? "downgraded" : "approved");

  return {
    evaluation_id: newId(),
    candidate_id: candidate.candidate_id,
    raw_action: candidate.action,
    final_action: finalAction,
    status,
    reason_codes: reasons,
    explanations,
    normalized: {
      ticker: candidate.ticker,
      verified_price: verifiedQuote?.price ?? "0",
      quote_as_of: verifiedQuote?.as_of ?? "",
      quote_source: verifiedQuote?.source ?? "",
      position_value_after: positionValueAfter === null ? null : formatFixed(positionValueAfter, 6),
      total_investable_value: totalInvestable === null ? null : formatFixed(totalInvestable, 6),
      dollars_at_risk: dollarsAtRisk === null ? null : formatFixed(dollarsAtRisk, 6),
      reward_risk_milli: rewardRiskMilli === null ? null : rewardRiskMilli.toString(),
    },
    holding_state_change: holdingState,
    candidate,
  };
}
