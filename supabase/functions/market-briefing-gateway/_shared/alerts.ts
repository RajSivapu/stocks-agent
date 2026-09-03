import type {
  AlertCondition,
  AlertConditionEvidence,
  AlertEvaluation,
  AlertRecentEvent,
  AlertRuleKind,
  AlertRuleSnapshot,
  AlertSession,
} from "./contracts.ts";
import { parseFixed } from "./fixed-point.ts";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TICKER = /^[A-Z][A-Z0-9.-]{0,9}$/;
const DECIMAL = /^(?:0|[1-9]\d*)(?:\.\d{1,6})?$/;
const RULE_KEYS = [
  "rule_id", "version", "state", "ticker", "profile", "severity", "session",
  "confirmation", "conditions", "cooldown_seconds", "fire_limit", "valid_until",
  "owner_note",
] as const;
const CONDITION_KEYS = ["kind", "operator", "left", "right", "timeframe"] as const;
const STATES = ["draft", "active", "paused", "snoozed", "dismissed", "expired"] as const;
const PROFILES = ["long_term", "balanced", "active"] as const;
const SEVERITIES = ["critical", "review", "update", "watch", "system"] as const;
const SESSIONS = ["regular", "pre_market", "post_market", "all"] as const;
const CONFIRMATIONS = ["bar_close", "two_quote"] as const;
const KINDS = [
  "price_cross", "price_zone", "sma_cross", "rsi_range", "volume_multiple",
  "recorded_stop", "recorded_target", "screen_entry", "event_window",
] as const;
const OPERATORS = ["above", "below", "inside", "outside"] as const;
const TIMEFRAMES = ["quote", "15m", "1h", "1d"] as const;
const TWO_QUOTE_KINDS = new Set<AlertRuleKind>([
  "price_cross", "price_zone", "recorded_stop", "recorded_target",
]);

function object(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${path} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  row: Record<string, unknown>,
  allowed: readonly string[],
  path: string,
): void {
  const allowedSet = new Set(allowed);
  for (const key of Object.keys(row)) {
    if (!allowedSet.has(key)) throw new Error(`${path} has unknown field ${key}`);
  }
  for (const key of allowed) {
    if (!(key in row)) throw new Error(`${path} is missing field ${key}`);
  }
}

function enumValue<T extends string>(
  value: unknown,
  allowed: readonly T[],
  path: string,
): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    throw new Error(`${path} is invalid`);
  }
  return value as T;
}

function boundedInteger(value: unknown, minimum: number, maximum: number, path: string): number {
  if (!Number.isInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    throw new Error(`${path} must be an integer between ${minimum} and ${maximum}`);
  }
  return value as number;
}

function boundedString(value: unknown, maximum: number, path: string): string {
  if (typeof value !== "string" || value.length > maximum) {
    throw new Error(`${path} must be a string no longer than ${maximum} characters`);
  }
  return value;
}

function decimal(value: unknown, path: string): string {
  if (typeof value !== "string" || !DECIMAL.test(value)) {
    throw new Error(`${path} must be a canonical decimal string`);
  }
  parseFixed(value, 6);
  return value;
}

function timestamp(value: unknown, path: string): string {
  if (typeof value !== "string") throw new Error(`${path} must be an ISO timestamp`);
  const instant = new Date(value);
  if (Number.isNaN(instant.valueOf()) || instant.toISOString() !== value) {
    throw new Error(`${path} must be a canonical ISO timestamp`);
  }
  return value;
}

function parseCondition(value: unknown, index: number): AlertCondition {
  const path = `rule.conditions[${index}]`;
  const row = object(value, path);
  exactKeys(row, CONDITION_KEYS, path);
  const kind = enumValue(row.kind, KINDS, `${path}.kind`);
  const operator = enumValue(row.operator, OPERATORS, `${path}.operator`);
  const left = decimal(row.left, `${path}.left`);
  const right = row.right === null ? null : decimal(row.right, `${path}.right`);
  const timeframe = enumValue(row.timeframe, TIMEFRAMES, `${path}.timeframe`);

  const rangeKind = kind === "price_zone" || kind === "rsi_range" || kind === "event_window";
  if (rangeKind !== (operator === "inside" || operator === "outside") || rangeKind !== (right !== null)) {
    throw new Error(`${path} has invalid operator/range shape`);
  }
  if (!rangeKind && right !== null) throw new Error(`${path}.right must be null`);
  if ((kind === "recorded_stop" && operator !== "below") ||
    (kind === "recorded_target" && operator !== "above")) {
    throw new Error(`${path} has invalid recorded-level operator`);
  }
  if (kind === "sma_cross" && !["20", "50", "200"].includes(left)) {
    throw new Error(`${path}.left must be an approved SMA period`);
  }
  if (kind === "rsi_range") {
    const low = parseFixed(left, 6);
    const high = parseFixed(right as string, 6);
    if (high > parseFixed("100", 6) || low > high) throw new Error(`${path} has invalid range`);
  } else if (right !== null && parseFixed(left, 6) >= parseFixed(right, 6)) {
    throw new Error(`${path} has invalid range`);
  }
  if ((kind === "recorded_stop" || kind === "recorded_target") && timeframe !== "quote") {
    throw new Error(`${path}.timeframe must be quote`);
  }
  return { kind, operator, left, right, timeframe };
}

export function parseAlertDraft(value: unknown): AlertRuleSnapshot {
  const row = object(value, "rule");
  exactKeys(row, RULE_KEYS, "rule");
  if (typeof row.rule_id !== "string" || !UUID.test(row.rule_id)) {
    throw new Error("rule.rule_id must be a UUID");
  }
  if (typeof row.ticker !== "string" || !TICKER.test(row.ticker)) {
    throw new Error("rule.ticker must be an uppercase ticker");
  }
  if (!Array.isArray(row.conditions) || row.conditions.length < 1 || row.conditions.length > 5) {
    throw new Error("rule.conditions must contain one to five conditions");
  }
  const confirmation = enumValue(row.confirmation, CONFIRMATIONS, "rule.confirmation");
  const conditions = row.conditions.map(parseCondition);
  if (confirmation === "two_quote" && conditions.some((item) => !TWO_QUOTE_KINDS.has(item.kind))) {
    throw new Error("rule.confirmation two_quote supports raw-price conditions only");
  }
  return {
    rule_id: row.rule_id,
    version: boundedInteger(row.version, 1, 1_000_000, "rule.version"),
    state: enumValue(row.state, STATES, "rule.state"),
    ticker: row.ticker,
    profile: enumValue(row.profile, PROFILES, "rule.profile"),
    severity: enumValue(row.severity, SEVERITIES, "rule.severity"),
    session: enumValue(row.session, SESSIONS, "rule.session"),
    confirmation,
    conditions,
    cooldown_seconds: boundedInteger(row.cooldown_seconds, 60, 604_800, "rule.cooldown_seconds"),
    fire_limit: boundedInteger(row.fire_limit, 1, 100, "rule.fire_limit"),
    valid_until: timestamp(row.valid_until, "rule.valid_until"),
    owner_note: boundedString(row.owner_note, 500, "rule.owner_note"),
  };
}

function compare(left: string, right: string): number {
  const a = parseFixed(left, 6);
  const b = parseFixed(right, 6);
  return a < b ? -1 : a > b ? 1 : 0;
}

function pointPasses(
  condition: AlertCondition,
  point: AlertConditionEvidence["points"][number],
): boolean | null {
  try {
    if (condition.kind === "sma_cross") {
      if (point.comparison_value === null) return null;
      const relation = compare(point.value, point.comparison_value);
      return condition.operator === "above" ? relation > 0 : relation < 0;
    }
    const relation = compare(point.value, condition.left);
    if (condition.operator === "above") return relation > 0;
    if (condition.operator === "below") return relation < 0;
    if (condition.right === null) return null;
    const inside = relation >= 0 && compare(point.value, condition.right) <= 0;
    return condition.operator === "inside" ? inside : !inside;
  } catch {
    return null;
  }
}

function statusReason(status: AlertConditionEvidence["status"]): string | null {
  if (status === "fresh") return null;
  if (status === "stale") return "EVIDENCE_STALE";
  if (status === "conflicting") return "EVIDENCE_CONFLICTING";
  if (status === "missing") return "EVIDENCE_MISSING";
  return "EVIDENCE_UNSAFE";
}

export function evaluateAlertRule(
  inputRule: AlertRuleSnapshot,
  evidence: AlertConditionEvidence[],
  now: Date,
  maxQuoteAgeMinutes = 20,
): AlertEvaluation {
  const rule = parseAlertDraft(inputRule);
  if (Number.isNaN(now.valueOf())) throw new Error("now must be valid");
  if (!Number.isInteger(maxQuoteAgeMinutes) || maxQuoteAgeMinutes <= 0) {
    throw new Error("maxQuoteAgeMinutes must be positive");
  }
  const reasons = new Set<string>();
  const byIndex = new Map<number, AlertConditionEvidence>();
  for (const item of evidence) {
    if (!Number.isInteger(item.condition_index) || item.condition_index < 0 ||
      item.condition_index >= rule.conditions.length || byIndex.has(item.condition_index)) {
      reasons.add("EVIDENCE_INDEX_INVALID");
      continue;
    }
    byIndex.set(item.condition_index, item);
  }
  const results = rule.conditions.map((condition, conditionIndex) => {
    const item = byIndex.get(conditionIndex);
    let passed: boolean | null = null;
    if (!item) {
      reasons.add("EVIDENCE_MISSING");
      return { condition, passed, observed_value: null, evidence_ids: [] };
    }
    const status = statusReason(item.status);
    if (status) reasons.add(status);
    if (rule.session !== "all" && item.market_session !== rule.session) {
      reasons.add("SESSION_MISMATCH");
    }
    if (item.points.length === 0) reasons.add("EVIDENCE_MISSING");
    for (const point of item.points) {
      let instant: Date | null = null;
      try {
        instant = new Date(timestamp(point.observed_at, "evidence.observed_at"));
      } catch {
        reasons.add("EVIDENCE_INVALID");
      }
      if (instant && instant > now) reasons.add("OBSERVATION_IN_FUTURE");
      if (instant && (condition.timeframe === "quote" || rule.confirmation === "two_quote") &&
        now.valueOf() - instant.valueOf() > maxQuoteAgeMinutes * 60_000) {
        reasons.add("EVIDENCE_STALE");
      }
      if (pointPasses(condition, point) === null) reasons.add("EVIDENCE_INVALID");
    }
    if (rule.confirmation === "bar_close") {
      const latest = item.points.at(-1);
      if (latest && !latest.bar_complete) reasons.add("BAR_INCOMPLETE");
      if ((condition.kind === "price_cross" || condition.kind === "sma_cross") &&
        item.points.length < 2) reasons.add("STATE_TRANSITION_MISSING");
      if (latest) {
        const current = pointPasses(condition, latest);
        if (condition.kind === "price_cross" || condition.kind === "sma_cross") {
          const previous = item.points.length >= 2
            ? pointPasses(condition, item.points[item.points.length - 2])
            : null;
          passed = current === true && previous === false;
        } else {
          passed = current;
        }
      }
    } else {
      const lastTwo = item.points.slice(-2);
      if (lastTwo.length < 2 || lastTwo[0].observed_at === lastTwo[1]?.observed_at) {
        reasons.add("QUOTE_CONFIRMATION_MISSING");
      } else {
        passed = lastTwo.every((point) => pointPasses(condition, point) === true);
      }
      if (condition.kind === "price_cross") {
        if (item.points.length < 3) reasons.add("QUOTE_CONFIRMATION_MISSING");
        else passed = passed === true &&
          pointPasses(condition, item.points[item.points.length - 3]) === false;
      }
    }
    return {
      condition,
      passed,
      observed_value: item.points.at(-1)?.value ?? null,
      evidence_ids: [...item.evidence_ids],
    };
  });

  const unsafe = reasons.size > 0;
  if (unsafe) {
    for (const result of results) result.passed = null;
  }
  const observed = evidence.flatMap((item) => item.points.map((point) => point.observed_at))
    .filter((value) => !Number.isNaN(new Date(value).valueOf())).sort().at(-1) ?? null;
  const marketSession: AlertSession = evidence[0]?.market_session ?? rule.session;
  const status = unsafe ? "unsafe_to_evaluate" :
    results.every((result) => result.passed === true) ? "triggered" : "not_triggered";
  if (status === "not_triggered") reasons.add("CONDITIONS_NOT_MET");
  return {
    rule,
    status,
    reason_codes: [...reasons].sort(),
    observed_at: observed,
    evaluated_at: now.toISOString(),
    market_session: marketSession,
    condition_results: results,
  };
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const row = value as Record<string, unknown>;
    return `{${Object.keys(row).sort().map((key) => `${JSON.stringify(key)}:${canonical(row[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function alertFingerprint(
  rule: AlertRuleSnapshot,
  evaluation: AlertEvaluation,
): Promise<string> {
  const input = canonical({
    rule_id: rule.rule_id,
    version: rule.version,
    ticker: rule.ticker,
    status: evaluation.status,
    observed_at: evaluation.observed_at,
    condition_results: evaluation.condition_results,
  });
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(input));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function alertRuleFingerprint(rule: AlertRuleSnapshot): Promise<string> {
  const parsed = parseAlertDraft(rule);
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonical(parsed)),
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function shouldPublishAlert(
  inputRule: AlertRuleSnapshot,
  evaluation: AlertEvaluation,
  recentEvents: AlertRecentEvent[],
): boolean {
  const rule = parseAlertDraft(inputRule);
  if (rule.state !== "active" || evaluation.status !== "triggered") return false;
  const evaluatedAt = new Date(evaluation.evaluated_at);
  if (Number.isNaN(evaluatedAt.valueOf()) || new Date(rule.valid_until) < evaluatedAt) return false;
  const priorTriggers = recentEvents.filter((event) => event.status === "triggered");
  if (priorTriggers.length >= rule.fire_limit) return false;
  return !priorTriggers.some((event) => {
    const prior = new Date(event.evaluated_at);
    return !Number.isNaN(prior.valueOf()) &&
      evaluatedAt.valueOf() - prior.valueOf() >= 0 &&
      evaluatedAt.valueOf() - prior.valueOf() < rule.cooldown_seconds * 1000;
  });
}
