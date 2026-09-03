import type {
  AlertCondition,
  AlertEvaluation,
  AlertRuleSnapshot,
  AlertSourceSummary,
  EvidenceBlock,
  HoldingState,
  NotificationKind,
  Phase,
  PolicyContext,
} from "./contracts.ts";
import { parseAlertDraft } from "./alerts.ts";
import { parseFixed } from "./fixed-point.ts";
import type { PolicyEvaluation, PolicyReasonCode } from "./policy.ts";
import type { PortfolioAlternativeComparison } from "./alternatives.ts";
import type { LongTermCompanionAnalysis } from "./companion.ts";

export const FORBIDDEN_DECISION_TEXT =
  /\b(buy|purchase|accumulate|sell|dump|unload|liquidate|add|reduce|trim|exit|enter|short|cover)\b|\b\d+(?:\.\d+)?\s+shares?\b|\$\s*\d|\b(entry|stop|target)\s*(?:at|=|:)/i;
const FORBIDDEN_COMPARISON_TEXT =
  /\b(?:buy|buying|sell|selling|switch|switching|replace|replacing|move|moving|redirect|redirecting|reallocate|reallocated|reallocating|recommend|recommends|recommended|recommending|consider|should|must|cancel|trade|order)\b/i;
const FORBIDDEN_COMPANION_PROMISE =
  /\b(?:allocate|allocating|guarantee|guaranteed|guarantees|guaranteeing|sure profits?|assured (?:profits?|returns?|gains?)|(?:profits?|returns?|gains?) (?:are|is) (?:assured|guaranteed|certain|expected)|(?:certain|certainly|sure) to (?:beat|rise|gain|outperform|make|earn|return|profit|win)|(?:cannot|can not|can't|will not|won't) (?:lose|decline|fall)(?: money| value)?|risk[- ]free|will (?:beat|rise|gain|outperform|make|earn|return|profit|win)|outperform(?:s|ed|ing)?|predict(?:ed|s|ing)? returns?)\b/i;

export interface RenderPublicationInput {
  phase: Phase;
  market_date: string;
  evaluations: PolicyEvaluation[];
  context?: PolicyContext;
  comparisons?: PortfolioAlternativeComparison[];
  companion?: LongTermCompanionAnalysis;
  holiday?: boolean;
}

export interface RenderedPublication {
  status: "ready" | "suppressed";
  kind: NotificationKind;
  body: string;
  parts: string[];
  hash: string;
  template_version: 2;
}

const MAX_PARTS = 4;
// Four joined parts plus three separators remain below the 14,000-byte DB body cap.
const MAX_PART_LENGTH = 3498;
const PORTFOLIO_LIMIT = 20;
const MARKET_LINE_LIMIT = 3;
const ZONE_LIMIT = 8;
const RISK_LIMIT = 8;
const WATCH_LIMIT = 5;
const SOURCE_LIMIT = 3;
const TRIGGER_PRIORITY: NotificationKind[] = [
  "data_warning",
  "thesis_break",
  "stop_breach",
  "target_hit",
  "entry_trigger",
  "new_idea",
  "stop_near",
  "target_near",
];
const TRIGGERS = new Set(TRIGGER_PRIORITY);

const REASON_LABELS: Record<PolicyReasonCode, string> = {
  INVALID_SCHEMA: "Invalid structured proposal",
  QUOTE_MISSING: "Verified quote is missing",
  QUOTE_STALE: "Quote is stale",
  QUOTE_SESSION_MISMATCH: "Quote does not match the required market session",
  PRICE_RELATION_INVALID: "Price or threshold relationship is invalid",
  AMOUNT_SHARES_MISMATCH: "Amount and shares do not reconcile",
  CURRENT_EVIDENCE_MISSING: "Current evidence is missing",
  ANALYST_INCOMPLETE: "Analyst review is incomplete",
  CHECKER_INCOMPLETE: "Checker review is incomplete",
  CHECKER_DOWNGRADE: "Checker downgraded the proposal",
  CHECKER_VETO: "Checker vetoed the proposal",
  LOW_CONFIDENCE: "Confidence is below the action threshold",
  ACTION_HOLDING_MISMATCH: "Action does not match recorded ownership",
  SELL_EXCEEDS_HOLDING: "Sell quantity exceeds recorded shares",
  POSITION_CAP_EXCEEDED: "Position cap would be exceeded",
  STOP_REQUIRED: "Authoritative stop is required",
  TRADE_RISK_EXCEEDED: "Trade risk limit would be exceeded",
  REWARD_RISK_TOO_LOW: "Reward-to-risk is below policy",
  PORTFOLIO_VALUE_INCOMPLETE: "Portfolio valuation is incomplete",
  DAILY_LOSS_LOCKOUT: "Daily loss lockout is active",
  CONSECUTIVE_LOSS_LOCKOUT: "Consecutive-loss lockout is active",
  SPECULATIVE_LEARNING_ONLY: "Speculative bucket remains learning-only",
  OWNER_PLAN_MISMATCH: "Proposal does not match a due owner plan",
  ALERT_ALREADY_ACTIVE: "Alert is already active or owner-suppressed",
  NARRATIVE_REJECTED: "Narrative failed safety validation",
  OUTSIDE_SESSION_CONDITIONAL:
    "Based on the latest official close; conditional until the next session",
  CALENDAR_COVERAGE_MISSING: "Reviewed market-calendar coverage is missing",
};

const SOURCE_HOSTS: Array<{ domain: string; label: string }> = [
  { domain: "sec.gov", label: "SEC filings" },
  { domain: "finance.yahoo.com", label: "Yahoo Finance" },
  { domain: "finnhub.io", label: "Finnhub" },
  { domain: "investor.vanguard.com", label: "Vanguard fund profile" },
  { domain: "ishares.com", label: "iShares fund profile" },
  {
    domain: "schwabassetmanagement.com",
    label: "Schwab Asset Management",
  },
  { domain: "federalreserve.gov", label: "Federal Reserve" },
  { domain: "bls.gov", label: "U.S. BLS" },
  { domain: "bea.gov", label: "U.S. BEA" },
];

function escapeHtml(value: string): string {
  return value.replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function compactText(value: string, limit: number): string {
  const compact = value.replace(/\s+/g, " ").trim();
  if (compact.length <= limit) return compact;
  return `${compact.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
}

function formatMoney(value: string): string {
  const micros = parseFixed(value, 6);
  return formatMoneyMicros(micros);
}

function formatMoneyMicros(value: bigint, showPlus = false): string {
  const negative = value < 0n;
  const magnitude = negative ? -value : value;
  const cents = (magnitude + 5_000n) / 10_000n;
  const sign = negative ? "-" : showPlus ? "+" : "";
  return `${sign}$${cents / 100n}.${
    (cents % 100n).toString().padStart(2, "0")
  }`;
}

function holdingValueMicros(shares: string, price: string): bigint {
  const sharesMicros = parseFixed(shares, 8);
  const priceMicros = parseFixed(price, 6);
  return (sharesMicros * priceMicros + 50_000_000n) / 100_000_000n;
}

function formatShares(value: string): string {
  const micros = parseFixed(value, 8);
  const tenThousandths = (micros + 5_000n) / 10_000n;
  const whole = tenThousandths / 10_000n;
  const decimal = (tenThousandths % 10_000n).toString().padStart(4, "0")
    .replace(/0+$/, "");
  return decimal.length > 0 ? `${whole}.${decimal}` : whole.toString();
}

function roundedRatioTenths(numerator: bigint, denominator: bigint): bigint {
  if (denominator <= 0n) throw new Error("ratio denominator must be positive");
  const magnitude = numerator < 0n ? -numerator : numerator;
  const rounded = (magnitude * 1_000n + denominator / 2n) / denominator;
  return numerator < 0n ? -rounded : rounded;
}

function formatTenthsPercent(value: bigint, showPlus = false): string {
  const negative = value < 0n;
  const magnitude = negative ? -value : value;
  const sign = negative ? "-" : showPlus ? "+" : "";
  return `${sign}${magnitude / 10n}.${magnitude % 10n}%`;
}

function comparisonPercent(value: string, showPlus = false): string {
  const negative = value.startsWith("-");
  const magnitude = parseFixed(negative ? value.slice(1) : value, 4);
  const tenths = (magnitude + 500n) / 1_000n;
  const sign = negative ? "-" : showPlus ? "+" : "";
  return `${sign}${tenths / 10n}.${tenths % 10n}%`;
}

function performance(price: string, averageCost: string): string {
  const priceMicros = parseFixed(price, 6);
  const averageMicros = parseFixed(averageCost, 6);
  return formatTenthsPercent(
    roundedRatioTenths(priceMicros - averageMicros, averageMicros),
    true,
  );
}

function stopDistance(price: string, stop: string): string {
  const priceMicros = parseFixed(price, 6);
  const stopMicros = parseFixed(stop, 6);
  if (priceMicros >= stopMicros) {
    return `${
      formatTenthsPercent(
        roundedRatioTenths(priceMicros - stopMicros, priceMicros),
      )
    } below`;
  }
  return `BREACHED by ${
    formatTenthsPercent(
      roundedRatioTenths(stopMicros - priceMicros, stopMicros),
    )
  }`;
}

function latestTimestamp(
  context: PolicyContext,
  evaluations: readonly PolicyEvaluation[],
): string | null {
  const values = [
    ...Object.values(context.holding_quotes).map((quote) => quote.as_of),
    ...evaluations.map((evaluation) => evaluation.normalized.quote_as_of),
  ].filter((value) => Number.isFinite(Date.parse(value)));
  return values.sort().at(-1) ?? null;
}

function evaluationForHolding(
  holding: HoldingState,
  evaluations: readonly PolicyEvaluation[],
): PolicyEvaluation | undefined {
  return evaluations.find((evaluation) =>
    evaluation.candidate.ticker === holding.ticker &&
    evaluation.status === "approved"
  );
}

function portfolioRows(
  context: PolicyContext,
  evaluations: readonly PolicyEvaluation[],
  visual = false,
): string[] {
  const holdings = [...context.holdings].sort((left, right) =>
    left.ticker.localeCompare(right.ticker)
  );
  const rows = holdings.slice(0, PORTFOLIO_LIMIT).map((holding) => {
    const quote = context.holding_quotes[holding.ticker];
    const action = evaluationForHolding(holding, evaluations)?.final_action;
    const actionSuffix = action === "hold" || action === "watch"
      ? ` · ${action.toUpperCase()}`
      : "";
    const stop = holding.stop === null
      ? "No recorded stop"
      : quote
      ? `Stop ${formatMoney(holding.stop)} (${
        stopDistance(quote.price, holding.stop)
      })`
      : `Stop ${formatMoney(holding.stop)}`;
    if (!quote || !visual) {
      if (!quote) {
        return `${
          escapeHtml(holding.ticker)
        } · verified price unavailable · Avg ${
          formatMoney(holding.avg_cost)
        } · ${stop}${actionSuffix}`;
      }
      return `${escapeHtml(holding.ticker)} · ${formatMoney(quote.price)} · ${
        performance(quote.price, holding.avg_cost)
      } since avg ${formatMoney(holding.avg_cost)} · ${stop}${actionSuffix}`;
    }

    const priceMicros = parseFixed(quote.price, 6);
    const averageMicros = parseFixed(holding.avg_cost, 6);
    const invested = holdingValueMicros(holding.shares, holding.avg_cost);
    const current = holdingValueMicros(holding.shares, quote.price);
    const pnl = current - invested;
    const positive = priceMicros >= averageMicros;
    const direction = positive ? "📈" : "📉";
    const status = positive ? "🟢" : "🔴";
    const lines = [
      `<b>${status} ${escapeHtml(holding.ticker)}</b> ${
        formatMoney(quote.price)
      } · avg ${formatMoney(holding.avg_cost)} · ${
        formatShares(holding.shares)
      } shares`,
      `${direction} ${formatMoneyMicros(pnl, pnl >= 0n)} (${
        performance(quote.price, holding.avg_cost)
      }) · invested ${formatMoneyMicros(invested)} → now ${
        formatMoneyMicros(current)
      }`,
    ];
    if (holding.stop !== null) {
      const stopMicros = parseFixed(holding.stop, 6);
      const gap = priceMicros - stopMicros;
      const state = effectiveAlertState(holding, evaluations);
      const warning = state.stop_alert_active || state.stop_near_alert_active;
      const gapPercent = roundedRatioTenths(gap, priceMicros);
      lines.push(
        `${warning ? "⚠️ " : ""}<b>Stop ${formatMoney(holding.stop)}</b> · ${
          formatMoneyMicros(gap < 0n ? -gap : gap)
        } gap (${
          formatTenthsPercent(gapPercent < 0n ? -gapPercent : gapPercent)
        })${warning ? " — watch open" : ""}`,
      );
    } else {
      lines.push("No recorded stop.");
    }
    if (holding.target !== null) {
      lines.push(`<b>Target ${formatMoney(holding.target)}</b>`);
    }
    return lines.join("\n");
  });
  if (holdings.length > PORTFOLIO_LIMIT) {
    rows.push(
      `<i>Portfolio list limited to ${PORTFOLIO_LIMIT} of ${holdings.length} holdings.</i>`,
    );
  }
  return rows.length > 0 ? rows : ["No recorded holdings."];
}

function factorHasVerifiedEvidence(
  evaluation: PolicyEvaluation,
  evidenceIds: readonly string[],
): boolean {
  if (evidenceIds.length === 0) return false;
  const byId = new Map(
    evaluation.candidate.evidence.map((evidence) => [evidence.id, evidence]),
  );
  return evidenceIds.every((id) => {
    const evidence = byId.get(id);
    return evidence?.status === "fresh" || evidence?.status === "fallback";
  });
}

function validateApprovedNarratives(
  evaluations: readonly PolicyEvaluation[],
): void {
  for (const evaluation of evaluations) {
    if (evaluation.status !== "approved") continue;
    for (const factor of evaluation.candidate.factors) {
      if (FORBIDDEN_DECISION_TEXT.test(factor.text)) {
        throw new Error("forbidden decision directive in factor summary");
      }
    }
  }
}

function marketRows(
  evaluations: readonly PolicyEvaluation[],
  limit = MARKET_LINE_LIMIT,
): string[] {
  const rows: string[] = [];
  const seen = new Set<string>();
  for (
    const evaluation of [...evaluations].sort((left, right) =>
      left.candidate.ticker.localeCompare(right.candidate.ticker)
    )
  ) {
    if (evaluation.status !== "approved") continue;
    for (const factor of evaluation.candidate.factors) {
      if (
        factor.kind !== "macro" && factor.kind !== "sector" ||
        !factorHasVerifiedEvidence(evaluation, factor.evidence_ids)
      ) continue;
      const text = compactText(factor.text, 220);
      const key = text.toLocaleLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      rows.push(`• ${escapeHtml(text)}`);
      if (rows.length >= limit) return rows;
    }
  }
  return rows.length > 0 ? rows : ["No material verified market update."];
}

function entryZoneRows(evaluations: readonly PolicyEvaluation[]): string[] {
  const rows = [...evaluations]
    .filter((evaluation) => {
      const candidate = evaluation.candidate;
      return evaluation.status === "approved" &&
        (evaluation.final_action === "buy" ||
          evaluation.final_action === "add") &&
        candidate.entry_zone_low !== null &&
        candidate.entry_zone_high !== null &&
        candidate.stop !== null &&
        candidate.target !== null &&
        candidate.valid_until !== null;
    })
    .sort((left, right) =>
      left.candidate.ticker.localeCompare(right.candidate.ticker) ||
      left.candidate_id.localeCompare(right.candidate_id)
    )
    .slice(0, ZONE_LIMIT)
    .map((evaluation) => {
      const candidate = evaluation.candidate;
      return `${escapeHtml(candidate.ticker)} · ${
        formatMoney(candidate.entry_zone_low!)
      }–${formatMoney(candidate.entry_zone_high!)} · Stop ${
        formatMoney(candidate.stop!)
      } · Target ${formatMoney(candidate.target!)} · Valid through ${
        escapeHtml(candidate.valid_until!)
      } · ${candidate.confidence.toUpperCase()}`;
    });
  return rows.length > 0
    ? rows
    : ["No active policy-approved entry zones today."];
}

function effectiveAlertState(
  holding: HoldingState,
  evaluations: readonly PolicyEvaluation[],
): Pick<
  HoldingState,
  | "stop_alert_active"
  | "stop_near_alert_active"
  | "target_near_alert_active"
  | "target_alert_active"
> {
  const current = evaluations
    .map((evaluation) => evaluation.holding_state_change)
    .find((change) => change?.ticker === holding.ticker);
  return current ?? holding;
}

function riskRows(
  context: PolicyContext,
  evaluations: readonly PolicyEvaluation[],
): string[] {
  const rows: string[] = [];
  if (!context.portfolio_command_coverage_complete) {
    rows.push(
      "Portfolio history coverage is incomplete; performance may be understated.",
    );
  }
  for (
    const holding of [...context.holdings].sort((left, right) =>
      left.ticker.localeCompare(right.ticker)
    )
  ) {
    const policyAction = evaluations.find((evaluation) =>
      evaluation.candidate.ticker === holding.ticker &&
      evaluation.status === "approved" &&
      (evaluation.final_action === "reduce" ||
        evaluation.final_action === "sell")
    )?.final_action;
    if (policyAction) {
      rows.push(
        `${
          escapeHtml(holding.ticker)
        }: policy-approved ${policyAction.toUpperCase()} review; open the full analysis before acting.`,
      );
    }
    const state = effectiveAlertState(holding, evaluations);
    if (state.stop_alert_active) {
      rows.push(
        `${
          escapeHtml(holding.ticker)
        }: verified price is at or below its recorded stop.`,
      );
    } else if (state.stop_near_alert_active) {
      rows.push(
        `${
          escapeHtml(holding.ticker)
        }: verified price is near its recorded stop.`,
      );
    }
    if (state.target_alert_active) {
      rows.push(
        `${
          escapeHtml(holding.ticker)
        }: verified price has reached its recorded target.`,
      );
    } else if (state.target_near_alert_active) {
      rows.push(
        `${
          escapeHtml(holding.ticker)
        }: verified price is near its recorded target.`,
      );
    }
    if (rows.length >= RISK_LIMIT) break;
  }
  return rows.length > 0
    ? rows.slice(0, RISK_LIMIT)
    : ["No material recorded-stop or target alerts."];
}

function watchReason(evaluation: PolicyEvaluation): string {
  if (evaluation.status !== "approved") {
    const labels = evaluation.reason_codes.slice(0, 2).map((code) =>
      REASON_LABELS[code]
    );
    return labels.length > 0 ? labels.join("; ") : "No policy-approved action";
  }
  const factor = evaluation.candidate.factors.find((item) =>
    item.kind !== "macro" && item.kind !== "sector" &&
    factorHasVerifiedEvidence(evaluation, item.evidence_ids) &&
    !FORBIDDEN_DECISION_TEXT.test(item.text)
  );
  return factor ? compactText(factor.text, 160) : "No policy-approved action";
}

function watchingRows(
  context: PolicyContext,
  evaluations: readonly PolicyEvaluation[],
  includeEmpty = true,
): string[] {
  const held = new Set(context.holdings.map((holding) => holding.ticker));
  const rows = [...evaluations]
    .filter((evaluation) => {
      if (held.has(evaluation.candidate.ticker)) return false;
      if (
        evaluation.status === "approved" &&
        (evaluation.final_action === "buy" || evaluation.final_action === "add")
      ) return false;
      return true;
    })
    .sort((left, right) =>
      left.candidate.ticker.localeCompare(right.candidate.ticker) ||
      left.candidate_id.localeCompare(right.candidate_id)
    )
    .slice(0, WATCH_LIMIT)
    .map((evaluation) => {
      const action = evaluation.status === "approved" && evaluation.final_action
        ? evaluation.final_action.toUpperCase()
        : "WATCH";
      return `${escapeHtml(evaluation.candidate.ticker)} · ${action} · ${
        escapeHtml(watchReason(evaluation))
      }`;
    });
  return rows.length > 0
    ? rows
    : includeEmpty
    ? ["No additional watch names from this run."]
    : [];
}

function canonicalSourceLabel(source: string): string | null {
  const normalized = source.toLocaleLowerCase();
  if (normalized.includes("yahoo")) return "Yahoo Finance";
  if (normalized.includes("finnhub")) return "Finnhub";
  if (normalized === "sec" || normalized.includes("sec-edgar")) {
    return "SEC filings";
  }
  if (normalized.includes("federal reserve") || normalized.includes("fred")) {
    return "Federal Reserve";
  }
  if (normalized.includes("bls")) return "U.S. BLS";
  if (normalized.includes("bea")) return "U.S. BEA";
  return null;
}

function approvedEvidenceLink(
  evidence: EvidenceBlock,
): { label: string; href: string } | null {
  if (!evidence.reference || evidence.reference.length > 500) return null;
  try {
    const url = new URL(evidence.reference);
    if (
      url.protocol !== "https:" || url.username !== "" || url.password !== ""
    ) return null;
    const hostname = url.hostname.toLocaleLowerCase();
    const source = SOURCE_HOSTS.find(({ domain }) =>
      hostname === domain || hostname.endsWith(`.${domain}`)
    );
    return source ? { label: source.label, href: url.toString() } : null;
  } catch {
    return null;
  }
}

function readMoreRows(
  context: PolicyContext,
  evaluations: readonly PolicyEvaluation[],
): string[] {
  const sources = new Map<string, string>();
  for (const quote of Object.values(context.holding_quotes)) {
    const label = canonicalSourceLabel(quote.source);
    if (label) sources.set(label, escapeHtml(label));
  }
  for (const evaluation of evaluations) {
    const normalized = canonicalSourceLabel(evaluation.normalized.quote_source);
    if (normalized) sources.set(normalized, escapeHtml(normalized));
    const referencedEvidence = new Set(
      evaluation.candidate.factors.flatMap((factor) => factor.evidence_ids),
    );
    for (const evidence of evaluation.candidate.evidence) {
      if (!referencedEvidence.has(evidence.id)) continue;
      if (evidence.status !== "fresh" && evidence.status !== "fallback") {
        continue;
      }
      const link = approvedEvidenceLink(evidence);
      if (link) {
        sources.set(
          link.label,
          `<a href="${escapeHtml(link.href)}">${escapeHtml(link.label)}</a>`,
        );
        continue;
      }
      const label = canonicalSourceLabel(evidence.source);
      if (label && !sources.has(label)) sources.set(label, escapeHtml(label));
    }
  }
  const displayed = [...sources.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .slice(0, SOURCE_LIMIT)
    .map(([, value]) => value);
  return [
    ...dataGapRows(evaluations),
    displayed.length > 0
      ? `Sources: ${displayed.join(" · ")}`
      : "No external source link was approved for display.",
    "<i>Suggestion only — you decide and place every trade.</i>",
  ];
}

function dataGapRows(evaluations: readonly PolicyEvaluation[]): string[] {
  const rows: string[] = [];
  const seen = new Set<string>();
  for (
    const evaluation of [...evaluations].sort((left, right) =>
      left.candidate.ticker.localeCompare(right.candidate.ticker)
    )
  ) {
    for (const evidence of evaluation.candidate.evidence) {
      if (
        evidence.status !== "missing" && evidence.status !== "failed" &&
        evidence.status !== "stale" && evidence.status !== "conflicting" &&
        evidence.status !== "unsupported"
      ) continue;
      const source = canonicalSourceLabel(evidence.source) ??
        "the configured provider";
      const state = evidence.status === "stale"
        ? "was stale from"
        : evidence.status === "conflicting"
        ? "conflicted across"
        : "unavailable from";
      const row = `Data gap: ${escapeHtml(evaluation.candidate.ticker)} · ${
        escapeHtml(evidence.kind)
      } evidence ${state} ${escapeHtml(source)}.`;
      if (seen.has(row)) continue;
      seen.add(row);
      rows.push(row);
      if (rows.length >= 4) return rows;
    }
  }
  return rows;
}

function nextCheckRows(
  context: PolicyContext,
  evaluations: readonly PolicyEvaluation[],
): string[] {
  const rows: string[] = [];
  if (!context.portfolio_command_coverage_complete) {
    rows.push(
      "Portfolio history coverage is incomplete; performance may be understated.",
    );
  }
  for (
    const holding of [...context.holdings].sort((left, right) =>
      left.ticker.localeCompare(right.ticker)
    )
  ) {
    const policyAction = evaluations.find((evaluation) =>
      evaluation.candidate.ticker === holding.ticker &&
      evaluation.status === "approved" &&
      (evaluation.final_action === "reduce" ||
        evaluation.final_action === "sell")
    )?.final_action;
    if (policyAction) {
      rows.push(
        `${
          escapeHtml(holding.ticker)
        }: policy-approved ${policyAction.toUpperCase()} review; open the full analysis before acting.`,
      );
    }
    const state = effectiveAlertState(holding, evaluations);
    if (
      holding.stop !== null &&
      (state.stop_alert_active || state.stop_near_alert_active)
    ) {
      rows.push(
        `${escapeHtml(holding.ticker)}: recorded stop ${
          formatMoney(holding.stop)
        } remains flagged for follow-up.`,
      );
    }
    if (
      holding.target !== null &&
      (state.target_alert_active || state.target_near_alert_active)
    ) {
      rows.push(
        `${escapeHtml(holding.ticker)}: recorded target ${
          formatMoney(holding.target)
        } remains flagged for follow-up.`,
      );
    }
    if (rows.length >= RISK_LIMIT) break;
  }
  return rows.length > 0 ? rows.slice(0, RISK_LIMIT) : [
    "No recorded stop or target follow-up is flagged in this brief.",
  ];
}

function comparisonRows(
  context: PolicyContext,
  comparisons: readonly PortfolioAlternativeComparison[],
): string[] {
  const rows: string[] = [];
  for (
    const comparison of [...comparisons].sort((left, right) =>
      left.baseline_ticker.localeCompare(right.baseline_ticker) ||
      left.alternative_ticker.localeCompare(right.alternative_ticker)
    )
  ) {
    if (FORBIDDEN_COMPARISON_TEXT.test(comparison.reason)) {
      throw new Error("forbidden decision directive in comparison summary");
    }
    const relationship = comparison.relationship === "like_for_like"
      ? "LIKE-FOR-LIKE"
      : comparison.relationship.toUpperCase();
    const lines = [
      `<b>${escapeHtml(comparison.baseline_ticker)} ↔ ${
        escapeHtml(comparison.alternative_ticker)
      } · ${relationship}</b>`,
    ];
    if (
      comparison.coverage_status === "complete" &&
      comparison.baseline_monthly_return_pct !== null &&
      comparison.alternative_monthly_return_pct !== null &&
      comparison.monthly_excess_pct !== null &&
      comparison.baseline_max_drawdown_pct !== null &&
      comparison.alternative_max_drawdown_pct !== null
    ) {
      const excess = comparison.monthly_excess_pct.startsWith("-")
        ? -parseFixed(comparison.monthly_excess_pct.slice(1), 4)
        : parseFixed(comparison.monthly_excess_pct, 4);
      const excessMagnitude = excess < 0n ? -excess : excess;
      const roundedExcessTenths = (excessMagnitude + 500n) / 1_000n;
      const leader = excess === 0n
        ? "matched to 0.0 points"
        : roundedExcessTenths === 0n
        ? "difference under 0.1 point"
        : excess > 0n
        ? `${comparison.alternative_ticker} ahead by ${
          comparisonPercent(comparison.monthly_excess_pct).replace(
            "%",
            " points",
          )
        }`
        : excess < 0n
        ? `${comparison.baseline_ticker} ahead by ${
          comparisonPercent(comparison.monthly_excess_pct.slice(1)).replace(
            "%",
            " points",
          )
        }`
        : "matched to 0.0 points";
      lines.push(
        `Past year, equal monthly contributions: ${
          escapeHtml(comparison.baseline_ticker)
        } ${
          comparisonPercent(comparison.baseline_monthly_return_pct, true)
        } · ${escapeHtml(comparison.alternative_ticker)} ${
          comparisonPercent(comparison.alternative_monthly_return_pct, true)
        } · ${escapeHtml(leader)}.`,
      );
      lines.push(
        `Max drawdown: ${escapeHtml(comparison.baseline_ticker)} ${
          comparisonPercent(comparison.baseline_max_drawdown_pct)
        } · ${escapeHtml(comparison.alternative_ticker)} ${
          comparisonPercent(comparison.alternative_max_drawdown_pct)
        }.`,
      );
    } else {
      lines.push(
        "Historical comparison unavailable — synchronized adjusted history was insufficient.",
      );
    }
    lines.push(
      `Forward evidence: ${comparison.prospective_view.toUpperCase()} — ${
        escapeHtml(comparison.reason)
      }`,
    );
    const plan = context.owner_plans.find((item) =>
      item.active && item.ticker === comparison.baseline_ticker
    );
    lines.push(
      plan
        ? `Your recorded ${escapeHtml(plan.ticker)} ${
          escapeHtml(plan.cadence)
        } plan is unchanged.`
        : "No holding or recurring plan was changed.",
    );
    rows.push(lines.join("\n"));
  }
  if (rows.length > 0) {
    rows.push("<i>Hypothetical history is not a forecast.</i>");
  }
  return rows;
}

function formatCorrelation(value: string): string {
  const negative = value.startsWith("-");
  const magnitude = parseFixed(negative ? value.slice(1) : value, 4);
  const hundredths = (magnitude + 50n) / 100n;
  return `${negative ? "-" : ""}${hundredths / 100n}.${
    (hundredths % 100n).toString().padStart(2, "0")
  }`;
}

function companionRows(
  context: PolicyContext,
  companion?: LongTermCompanionAnalysis,
): string[] {
  if (!companion) {
    return [
      "No additive companion cleared current evidence. Your recorded plan is unchanged.",
    ];
  }
  for (const text of [companion.thesis, companion.risk_note]) {
    if (
      FORBIDDEN_COMPARISON_TEXT.test(text) ||
      FORBIDDEN_DECISION_TEXT.test(text) ||
      FORBIDDEN_COMPANION_PROMISE.test(text)
    ) {
      throw new Error("forbidden decision directive in companion summary");
    }
  }
  const role = companion.qualification_status !== "qualified"
    ? "REVIEW NOT QUALIFIED"
    : companion.role === "satellite"
    ? "CONCENTRATED SATELLITE"
    : companion.role.toUpperCase();
  const plan = context.owner_plans.find((item) =>
    item.active && item.ticker === companion.baseline_ticker
  );
  const rows = [
    plan
      ? `Core stays: <b>${escapeHtml(companion.baseline_ticker)}</b> · ${
        formatMoney(plan.amount)
      }/month reminder`
      : `Core reference: <b>${
        escapeHtml(companion.baseline_ticker)
      }</b> · recorded holding/plan unchanged`,
    `Research candidate: <b>${
      escapeHtml(companion.companion_ticker)
    }</b> · ${role}`,
  ];
  if (companion.qualification_status !== "qualified") {
    rows.push(
      `Did not qualify: ${escapeHtml(companion.qualification_reason)}`,
      "No reminder was added or changed.",
    );
    return rows;
  }
  rows.push(
    `Why it adds something: ${escapeHtml(companion.thesis)}`,
    `Main risk: ${escapeHtml(companion.risk_note)}`,
  );
  for (
    const horizon of [...companion.horizons].sort((left, right) =>
      left.years - right.years
    )
  ) {
    rows.push(
      `${horizon.years}Y annualized: ${escapeHtml(companion.baseline_ticker)} ${
        comparisonPercent(horizon.baseline_annualized_return_pct, true)
      } · ${escapeHtml(companion.companion_ticker)} ${
        comparisonPercent(horizon.companion_annualized_return_pct, true)
      } · corr ${
        formatCorrelation(horizon.daily_return_correlation)
      } · drawdown ${comparisonPercent(horizon.baseline_max_drawdown_pct)} / ${
        comparisonPercent(horizon.companion_max_drawdown_pct)
      }.`,
    );
  }
  if (companion.rolling_one_year) {
    const scenario = companion.rolling_one_year;
    rows.push(
      `Per ${
        formatMoney(scenario.monthly_contribution_usd)
      }/month, rolling 1Y history: ${
        formatMoney(scenario.total_contributed_usd)
      } contributed → weak ${
        formatMoney(scenario.weak_ending_value_usd)
      } · middle ${formatMoney(scenario.middle_ending_value_usd)} · strong ${
        formatMoney(scenario.strong_ending_value_usd)
      } (${scenario.sample_windows} windows).`,
    );
  }
  rows.push(
    companion.recurring_plan_review_eligible
      ? "Plan status: eligible for owner review; no reminder was added or changed."
      : "Plan status: research-only; not eligible for a recurring core reminder.",
    "<i>Historical scenarios are not forecasts. Future loss is possible.</i>",
  );
  return rows;
}

function section(
  title: string,
  rows: readonly string[],
  separator = "\n",
): string {
  return `<b>${title}</b>\n${rows.join(separator)}`;
}

function shortMarketDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return escapeHtml(value);
  const month = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ][Number(match[2]) - 1];
  return month ? `${month} ${match[3]}` : escapeHtml(value);
}

function fullBriefHeading(input: RenderPublicationInput): string {
  if (input.phase === "pre-market") {
    return `<b>🌅 MORNING BRIEF — ${input.market_date}</b>`;
  }
  if (input.phase === "post-market") {
    return `<b>🌙 EOD — ${shortMarketDate(input.market_date)}</b>`;
  }
  const conditional = input.evaluations.some((item) =>
    item.reason_codes.includes("OUTSIDE_SESSION_CONDITIONAL")
  );
  return `<b>🔎 ON-DEMAND ANALYSIS — ${input.market_date} — ${
    conditional ? "CONDITIONAL — LATEST OFFICIAL CLOSE" : "LIVE REGULAR SESSION"
  }</b>`;
}

function splitBlocks(title: string, blocks: string[]): string[] {
  const parts: string[] = [];
  let current = title;
  let omitted = false;
  for (const block of blocks) {
    if (block.length > MAX_PART_LENGTH) {
      throw new Error("render block exceeds Telegram part limit");
    }
    const combined = `${current}\n\n${block}`;
    if (combined.length <= MAX_PART_LENGTH) {
      current = combined;
      continue;
    }
    parts.push(current);
    if (parts.length >= MAX_PARTS) {
      omitted = true;
      current = "";
      break;
    }
    current = block;
  }
  if (current && parts.length < MAX_PARTS) parts.push(current);
  if (omitted && parts.length > 0) {
    const note =
      "\n\n<i>Additional brief sections omitted by the message-size limit.</i>";
    const last = parts.length - 1;
    if (parts[last].length + note.length <= MAX_PART_LENGTH) {
      parts[last] += note;
    }
  }
  return parts;
}

function renderFullBrief(
  input: RenderPublicationInput,
  context: PolicyContext,
): string[] {
  const timestamp = latestTimestamp(context, input.evaluations);
  const heading = `${fullBriefHeading(input)}\n<i>${
    timestamp
      ? input.phase === "post-market"
        ? `Data through ${escapeHtml(centralTime(timestamp))}`
        : `Data through ${escapeHtml(timestamp)}`
      : "Data timestamp unavailable"
  }</i>`;
  if (input.phase === "post-market") {
    const watching = watchingRows(context, input.evaluations, false);
    return splitBlocks(heading, [
      section(
        "📊 YOUR PORTFOLIO",
        portfolioRows(context, input.evaluations, true),
        "\n\n",
      ),
      section("🌎 MARKET", marketRows(input.evaluations, 1)),
      section("🎯 OPEN ENTRY ZONES", entryZoneRows(input.evaluations)),
      section("➡️ NEXT REVIEW", nextCheckRows(context, input.evaluations)),
      ...(watching.length > 0 ? [section("👀 WATCHING", watching)] : []),
      section("📚 READ MORE", readMoreRows(context, input.evaluations)),
    ]);
  }
  return splitBlocks(heading, [
    section("📊 YOUR PORTFOLIO", portfolioRows(context, input.evaluations)),
    section("🌎 MARKET", marketRows(input.evaluations)),
    section("🎯 OPEN ENTRY ZONES", entryZoneRows(input.evaluations)),
    ...(input.comparisons && input.comparisons.length > 0
      ? [
        section(
          "🔄 CURRENT VS ALTERNATIVES",
          comparisonRows(context, input.comparisons),
          "\n\n",
        ),
        section(
          "🧠 LONG-TERM COMPANION",
          companionRows(context, input.companion),
        ),
      ]
      : []),
    section("⚠️ PORTFOLIO RISKS", riskRows(context, input.evaluations)),
    section("👀 WATCHING", watchingRows(context, input.evaluations)),
    section("📚 READ MORE", readMoreRows(context, input.evaluations)),
  ]);
}

function triggerLabel(kind: NotificationKind): string {
  return kind.replaceAll("_", " ").toUpperCase();
}

function intradayBlock(
  evaluation: PolicyEvaluation,
  context: PolicyContext,
): string {
  const candidate = evaluation.candidate;
  const isWatchIdea = candidate.notification_kind === "new_idea" &&
    evaluation.final_action === "watch";
  const label = isWatchIdea
    ? `👀 ${candidate.ticker} — WATCH IDEA`
    : `${candidate.ticker} — ${triggerLabel(candidate.notification_kind)}`;
  const lines = [
    `<b>${escapeHtml(label)}</b>`,
    `${formatMoney(evaluation.normalized.verified_price)} · ${
      escapeHtml(centralTime(evaluation.normalized.quote_as_of))
    }`,
  ];
  const holding = context.holdings.find((item) =>
    item.ticker === candidate.ticker
  );
  if (
    candidate.notification_kind === "entry_trigger" ||
    candidate.notification_kind === "new_idea"
  ) {
    const details: string[] = [];
    if (
      candidate.entry_zone_low !== null && candidate.entry_zone_high !== null
    ) {
      details.push(
        `Entry zone ${formatMoney(candidate.entry_zone_low)}–${
          formatMoney(candidate.entry_zone_high)
        }`,
      );
    }
    if (candidate.stop !== null) {
      details.push(`Stop ${formatMoney(candidate.stop)}`);
    }
    if (candidate.target !== null) {
      details.push(`Target ${formatMoney(candidate.target)}`);
    }
    if (candidate.valid_until !== null) {
      details.push(`Valid through ${escapeHtml(candidate.valid_until)}`);
    }
    if (details.length > 0) {
      details.push(`Confidence ${candidate.confidence.toUpperCase()}`);
      lines.push(details.join(" · "));
    } else {
      lines.push("No policy-approved entry, stop, or target — watch only.");
      lines.push(`Why now: ${escapeHtml(watchReason(evaluation))}`);
      lines.push(`Confidence ${candidate.confidence.toUpperCase()}`);
    }
  } else if (holding?.stop !== null && holding?.stop !== undefined) {
    lines.push(`Recorded stop ${formatMoney(holding.stop)}`);
  } else if (candidate.stop !== null) {
    lines.push(`Recorded stop ${formatMoney(candidate.stop)}`);
  }
  return lines.join("\n");
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export interface AlertButton {
  text: string;
  callback_data: string;
}

export interface RenderAlertV3Input {
  event_id: string;
  evaluation: AlertEvaluation;
  source_evaluation: PolicyEvaluation | null;
  source_summary?: AlertSourceSummary | null;
  context: PolicyContext | null;
  mode?: "draft" | "event";
}

export interface RenderedAlert {
  status: "ready" | "suppressed";
  body: string;
  parts: string[];
  hash: string;
  template_version: 3;
  reply_markup: { inline_keyboard: AlertButton[][] };
}

const ALERT_EVENT_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function centralTime(value: string): string {
  const instant = new Date(value);
  if (Number.isNaN(instant.valueOf())) return "unavailable";
  return `${
    new Intl.DateTimeFormat("en-US", {
      timeZone: "America/Chicago",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    }).format(instant)
  } CT`;
}

function alertExpiry(value: string): string {
  const instant = new Date(value);
  if (Number.isNaN(instant.valueOf())) return "unavailable";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago",
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(instant);
}

function alertHeading(
  rule: AlertRuleSnapshot,
  mode: "draft" | "event",
  unsafe: boolean,
): string {
  const profile = rule.profile.replaceAll("_", " ").toUpperCase();
  if (mode === "draft") {
    return `🔵 DRAFT • ${escapeHtml(rule.ticker)} • ${profile}`;
  }
  if (unsafe || rule.severity === "system") {
    return `⚪ DATA CHECK • ${escapeHtml(rule.ticker)} • ${profile}`;
  }
  if (rule.severity === "critical") {
    return `🔴 RISK REVIEW • ${escapeHtml(rule.ticker)} • ${profile}`;
  }
  if (rule.severity === "review") {
    return `🟠 REVIEW • ${escapeHtml(rule.ticker)} • ${profile}`;
  }
  if (rule.severity === "update") {
    return `🟡 UPDATE • ${escapeHtml(rule.ticker)} • ${profile}`;
  }
  return `🔵 WATCH • ${escapeHtml(rule.ticker)} • ${profile}`;
}

function triggerSummary(
  rule: AlertRuleSnapshot,
  status: AlertEvaluation["status"],
  mode: "draft" | "event",
): string {
  if (mode === "draft") {
    return "Proposed monitoring rule — suggestion only; inert until you arm it";
  }
  if (status === "unsafe_to_evaluate") {
    return "This rule was not evaluated safely — no investment conclusion was made";
  }
  const kinds = new Set(rule.conditions.map((condition) => condition.kind));
  if (kinds.has("recorded_stop")) {
    return "Verified price is at/below your recorded stop — review manually";
  }
  if (kinds.has("recorded_target")) {
    return "Verified price reached your recorded target — review manually";
  }
  if (kinds.has("screen_entry")) {
    return "A reviewed screen condition changed — research watch only; not a buy signal";
  }
  return "Entry setup confirmed — suggestion only; no order was placed";
}

function alertConditionText(condition: AlertCondition): string {
  if (condition.kind === "price_zone") {
    return `${condition.operator} ${formatMoney(condition.left)}–${
      formatMoney(condition.right!)
    }`;
  }
  if (condition.kind === "recorded_stop") {
    return `price at/below recorded stop ${formatMoney(condition.left)}`;
  }
  if (condition.kind === "recorded_target") {
    return `price at/above recorded target ${formatMoney(condition.left)}`;
  }
  if (condition.kind === "price_cross") {
    return `price crossed ${condition.operator} ${formatMoney(condition.left)}`;
  }
  if (condition.kind === "sma_cross") {
    return `close crossed ${condition.operator} SMA ${
      escapeHtml(condition.left)
    }`;
  }
  if (condition.kind === "rsi_range") {
    return `RSI 14 ${condition.operator} ${escapeHtml(condition.left)}–${
      escapeHtml(condition.right!)
    }`;
  }
  if (condition.kind === "volume_multiple") {
    return `volume ${condition.operator} ${
      escapeHtml(condition.left)
    }x its 20-session average`;
  }
  if (condition.kind === "screen_entry") {
    return "approved screen-entry condition met";
  }
  return `event window ${condition.operator} ${escapeHtml(condition.left)}–${
    escapeHtml(condition.right!)
  }`;
}

function conditionLine(
  evaluation: AlertEvaluation,
  mode: "draft" | "event",
): string {
  if (mode === "draft") {
    return `Proposed conditions ${evaluation.condition_results.length}: ${
      evaluation.condition_results.map((result) =>
        alertConditionText(result.condition)
      ).join("; ")
    }`;
  }
  const passed =
    evaluation.condition_results.filter((result) => result.passed === true)
      .length;
  const details = evaluation.condition_results.map((result) => {
    const status = result.passed === true
      ? "✅"
      : result.passed === false
      ? "❌"
      : "⚪ Unavailable —";
    return `${status} ${alertConditionText(result.condition)}`;
  });
  return `Conditions ${passed}/${evaluation.condition_results.length}: ${
    details.join("; ")
  }`;
}

function sourceSummaryFromEvaluation(
  source: PolicyEvaluation,
): AlertSourceSummary {
  return {
    ticker: source.candidate.ticker,
    confidence: source.candidate.confidence,
    valid_until: source.candidate.valid_until,
    invalidation_price: source.candidate.invalidation_price,
    stop: source.candidate.stop,
    target: source.candidate.target,
    position_value_after: source.normalized.position_value_after,
    total_investable_value: source.normalized.total_investable_value,
    evidence: source.candidate.evidence.map((item) => ({
      id: item.id,
      status: item.status,
    })),
    reasons: source.candidate.factors
      .filter((factor) =>
        factorHasVerifiedEvidence(source, factor.evidence_ids)
      )
      .map((factor) => factor.text),
  };
}

function trustedAlertSource(
  input: RenderAlertV3Input,
): AlertSourceSummary | null {
  if (input.source_summary) {
    return input.source_summary.ticker === input.evaluation.rule.ticker
      ? input.source_summary
      : null;
  }
  const source = input.source_evaluation;
  if (
    !source || source.status !== "approved" ||
    source.candidate.ticker !== input.evaluation.rule.ticker ||
    !source.candidate.analyst.completed ||
    !source.candidate.checker.completed ||
    source.candidate.checker.verdict !== "approve"
  ) return null;
  return sourceSummaryFromEvaluation(source);
}

function alertNarratives(source: AlertSourceSummary | null): string[] {
  if (!source) return [];
  return source.reasons
    .map((text) => compactText(text, 220))
    .filter((text) => text.length > 0 && !FORBIDDEN_DECISION_TEXT.test(text))
    .slice(0, 3);
}

function alertEvidenceCoverage(
  evaluation: AlertEvaluation,
  source: AlertSourceSummary | null,
  mode: "draft" | "event",
): string {
  if (!source) return "0/0";
  const ids = mode === "draft"
    ? new Set(source.evidence.map((item) => item.id))
    : new Set(
      evaluation.condition_results.flatMap((result) => result.evidence_ids),
    );
  if (ids.size === 0) return "0/0";
  const evidence = new Map(
    source.evidence.map((item) => [item.id, item.status]),
  );
  const available = [...ids].filter((id) => {
    const status = evidence.get(id);
    return status === "fresh" || status === "fallback";
  }).length;
  return `${available}/${ids.size}`;
}

function alertRiskLine(
  rule: AlertRuleSnapshot,
  source: AlertSourceSummary | null,
  context: PolicyContext | null,
): string {
  const holding =
    context?.holdings.find((item) => item.ticker === rule.ticker) ?? null;
  const hasRecordedStop = rule.conditions.some((item) =>
    item.kind === "recorded_stop"
  );
  const hasRecordedTarget = rule.conditions.some((item) =>
    item.kind === "recorded_target"
  );
  const parts: string[] = [];
  if (hasRecordedStop) {
    const stop = rule.conditions.find((item) => item.kind === "recorded_stop")
      ?.left;
    if (stop) parts.push(`recorded stop ${formatMoney(stop)}`);
  } else if (source?.invalidation_price) {
    parts.push(`invalidation below ${formatMoney(source.invalidation_price)}`);
  }
  if (hasRecordedTarget) {
    const target = rule.conditions.find((item) =>
      item.kind === "recorded_target"
    )?.left;
    if (target) parts.push(`recorded target ${formatMoney(target)}`);
  } else if (hasRecordedStop && holding?.target) {
    parts.push(`current portfolio target ${formatMoney(holding.target)}`);
  }
  if (!hasRecordedStop && source?.stop) {
    parts.push(`policy-approved stop ${formatMoney(source.stop)}`);
  }
  if (!hasRecordedTarget && !hasRecordedStop && source?.target) {
    parts.push(`target ${formatMoney(source.target)}`);
  }
  return parts.length > 0
    ? `Risk: ${parts.join(" • ")}`
    : "Risk: no recorded or policy-approved stop/target was available.";
}

function alertExposure(source: AlertSourceSummary | null): string {
  if (
    !source || source.position_value_after === null ||
    source.total_investable_value === null
  ) {
    return "Portfolio exposure unavailable from this alert receipt.";
  }
  try {
    const position = parseFixed(source.position_value_after, 6);
    const total = parseFixed(source.total_investable_value, 6);
    if (total <= 0n) throw new Error("invalid total");
    return `Portfolio exposure ${
      formatTenthsPercent(roundedRatioTenths(position, total))
    }`;
  } catch {
    return "Portfolio exposure unavailable from this alert receipt.";
  }
}

function alertButtons(
  rule: AlertRuleSnapshot,
  mode: "draft" | "event",
  eventId: string,
): AlertButton[][] {
  const prefix = (action: string) =>
    `al:${action}:${
      action === "ack" && mode === "event" ? eventId : rule.rule_id
    }:${rule.version}`;
  const buttons = mode === "draft"
    ? [{ text: "Arm", callback_data: prefix("arm") }, {
      text: "Dismiss",
      callback_data: prefix("dismiss"),
    }]
    : [
      { text: "Acknowledge", callback_data: prefix("ack") },
      {
        text: rule.severity === "critical" ? "Snooze 20m" : "Snooze 1d",
        callback_data: prefix(
          rule.severity === "critical" ? "snooze20m" : "snooze1d",
        ),
      },
      { text: "Dismiss", callback_data: prefix("dismiss") },
    ];
  if (buttons.some((button) => button.callback_data.length > 64)) {
    throw new Error("alert callback exceeds Telegram limit");
  }
  return [buttons];
}

export async function renderAlertV3(
  input: RenderAlertV3Input,
): Promise<RenderedAlert> {
  if (!ALERT_EVENT_ID.test(input.event_id)) {
    throw new Error("alert event id must be a UUID");
  }
  const rule = parseAlertDraft(input.evaluation.rule);
  const mode = input.mode ?? "event";
  if (mode === "draft" && rule.state !== "draft") {
    throw new Error("draft alert requires draft rule state");
  }
  if (mode === "event" && input.evaluation.status === "not_triggered") {
    return {
      status: "suppressed",
      body: "",
      parts: [],
      hash: await sha256(""),
      template_version: 3,
      reply_markup: { inline_keyboard: [] },
    };
  }
  if (mode === "event" && rule.state !== "active") {
    throw new Error("event alert requires active rule state");
  }

  const source = trustedAlertSource(input);
  const narratives = alertNarratives(source);
  const unsafe = input.evaluation.status === "unsafe_to_evaluate";
  const ageSeconds = input.evaluation.observed_at
    ? Math.max(
      0,
      Math.floor(
        (Date.parse(input.evaluation.evaluated_at) -
          Date.parse(input.evaluation.observed_at)) / 1000,
      ),
    )
    : null;
  const timing = input.evaluation.observed_at
    ? mode === "draft"
      ? `Proposed from quote ${
        centralTime(input.evaluation.observed_at)
      } • evaluated ${
        centralTime(input.evaluation.evaluated_at)
      } • age ${ageSeconds}s • ${input.evaluation.market_session.toUpperCase()}`
      : unsafe
      ? `Evaluated ${
        centralTime(input.evaluation.evaluated_at)
      } • latest quote ${
        centralTime(input.evaluation.observed_at)
      } • age ${ageSeconds}s • ${input.evaluation.market_session.toUpperCase()}`
      : `Triggered ${centralTime(input.evaluation.evaluated_at)} • quote ${
        centralTime(input.evaluation.observed_at)
      } • age ${ageSeconds}s • ${input.evaluation.market_session.toUpperCase()}`
    : mode === "draft"
    ? `Proposed at ${
      centralTime(input.evaluation.evaluated_at)
    } • source quote timestamp unavailable • ${input.evaluation.market_session.toUpperCase()}`
    : `Evaluated ${
      centralTime(input.evaluation.evaluated_at)
    } • quote unavailable • ${input.evaluation.market_session.toUpperCase()}`;
  const validThrough = source?.valid_until
    ? `valid through ${escapeHtml(source.valid_until)}`
    : `expires ${escapeHtml(alertExpiry(rule.valid_until))}`;
  const confidence = source ? source.confidence.toUpperCase() : "NOT SCORED";
  const receipt = input.event_id.replaceAll("-", "").slice(0, 4).toUpperCase();
  const why = narratives[0] ??
    (unsafe
      ? "No safe conclusion was produced because required evidence was unavailable."
      : mode === "draft"
      ? "Rule projected from an approved source evaluation; review it before arming."
      : "Deterministic conditions passed; no safe thesis summary was available.");
  const evidenceLines = narratives.slice(1).map((text) =>
    `• ${escapeHtml(text)}`
  );
  const explanationLabel = unsafe
    ? "Why unavailable"
    : mode === "draft"
    ? "Why proposed"
    : "Why now";
  const lines = [
    `<b>${alertHeading(rule, mode, unsafe)}</b>`,
    triggerSummary(rule, input.evaluation.status, mode),
    "",
    timing,
    conditionLine(input.evaluation, mode),
    "",
    `${explanationLabel}: ${escapeHtml(why)}`,
    ...evidenceLines,
    alertRiskLine(rule, source, input.context),
    alertExposure(source),
    `Confidence ${confidence} • evidence ${
      alertEvidenceCoverage(input.evaluation, source, mode)
    } • ${validThrough}`,
    ...(rule.owner_note
      ? [`Owner note: ${escapeHtml(compactText(rule.owner_note, 160))}`]
      : []),
    "",
    ...(rule.severity === "critical"
      ? ["The bot did not sell and cannot access a brokerage."]
      : []),
    `Receipt AL-${receipt} • rule v${rule.version} • expires ${
      escapeHtml(alertExpiry(rule.valid_until))
    }`,
  ];
  const body = lines.join("\n");
  if (body.length > 3_500) {
    throw new Error("rendered alert exceeds Telegram limit");
  }
  return {
    status: "ready",
    body,
    parts: [body],
    hash: await sha256(body),
    template_version: 3,
    reply_markup: { inline_keyboard: alertButtons(rule, mode, input.event_id) },
  };
}

export async function renderPublication(
  input: RenderPublicationInput,
): Promise<RenderedPublication> {
  if (input.holiday) {
    const body = "🏛 Market closed today — US public holiday. No brief.";
    return {
      status: "ready",
      kind: "holiday",
      body,
      parts: [body],
      hash: await sha256(body),
      template_version: 2,
    };
  }
  if (!input.context) throw new Error("render context is required");

  if (input.phase === "intraday") {
    const evaluations = [...input.evaluations]
      .filter((item) =>
        item.status === "approved" && item.final_action !== null &&
        TRIGGERS.has(item.candidate.notification_kind)
      )
      .sort((left, right) => {
        const priority =
          TRIGGER_PRIORITY.indexOf(left.candidate.notification_kind) -
          TRIGGER_PRIORITY.indexOf(right.candidate.notification_kind);
        return priority ||
          left.candidate.ticker.localeCompare(right.candidate.ticker) ||
          left.candidate_id.localeCompare(right.candidate_id);
      });
    if (evaluations.length === 0) {
      return {
        status: "suppressed",
        kind: "brief",
        body: "",
        parts: [],
        hash: await sha256(""),
        template_version: 2,
      };
    }
    const parts = splitBlocks(
      `<b>⚠️ INTRADAY ALERTS — ${input.market_date}</b>`,
      [
        ...evaluations.map((evaluation) =>
          intradayBlock(evaluation, input.context!)
        ),
        "<i>Suggestion only — no order was placed.</i>",
      ],
    );
    const body = parts.join("\n\n");
    return {
      status: "ready",
      kind: evaluations[0].candidate.notification_kind,
      body,
      parts,
      hash: await sha256(body),
      template_version: 2,
    };
  }

  validateApprovedNarratives(input.evaluations);
  const parts = renderFullBrief(input, input.context);
  const body = parts.join("\n\n");
  const sessionOnly = input.phase === "on-demand";
  return {
    status: sessionOnly ? "suppressed" : "ready",
    kind: "brief",
    body,
    parts: sessionOnly ? [] : parts,
    hash: await sha256(body),
    template_version: 2,
  };
}
