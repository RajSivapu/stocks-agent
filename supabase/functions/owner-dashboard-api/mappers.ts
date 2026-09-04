import type {
  AlertView,
  CompanionHorizonView,
  CompanionView,
  HoldingView,
  IdeaView,
  InvestmentPlanView,
  PortfolioView,
  ReceiptStatus,
  RunSummaryView,
  SourceLink,
  TransactionView,
} from "../../../packages/dashboard-contracts/src/index.ts";

type Row = Record<string, unknown>;

function record(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Row
    : {};
}

function text(value: unknown, maximum = 2_000): string | null {
  if (value instanceof Date && !Number.isNaN(value.valueOf())) return value.toISOString();
  return typeof value === "string" && value.length > 0
    ? value.slice(0, maximum)
    : typeof value === "number" && Number.isFinite(value)
    ? String(value)
    : null;
}

function integer(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function textArray(value: unknown, maximum = 20): string[] {
  return Array.isArray(value)
    ? value.slice(0, maximum).flatMap((item) => {
      const parsed = text(item, 160);
      return parsed === null ? [] : [parsed];
    })
    : [];
}

function sourceLinks(value: unknown): SourceLink[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 20).flatMap((item) => {
    const row = record(item);
    const label = text(row.title ?? row.label ?? row.source ?? row.kind, 120);
    if (!label) return [];
    const rawUrl = text(row.url, 1_024);
    let url: string | null = null;
    if (rawUrl) {
      try {
        const parsed = new URL(rawUrl);
        if (parsed.protocol === "https:") url = parsed.href;
      } catch {
        url = null;
      }
    }
    return [{ label, url }];
  });
}

const SCALE = 1_000_000n;

function fixed(value: unknown): bigint | null {
  const raw = text(value, 80);
  const match = raw && /^(-?)(\d{1,18})(?:\.(\d{1,6}))?$/.exec(raw);
  if (!match) return null;
  const whole = match[2];
  if (!whole) return null;
  const fraction = (match[3] ?? "").padEnd(6, "0");
  const result = BigInt(whole) * SCALE + BigInt(fraction || "0");
  return match[1] === "-" ? -result : result;
}

function decimal(value: bigint): string {
  const negative = value < 0n;
  const absolute = negative ? -value : value;
  const whole = absolute / SCALE;
  const fraction = (absolute % SCALE).toString().padStart(6, "0").replace(/0+$/, "");
  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

function multiply(left: bigint, right: bigint): bigint {
  return (left * right + SCALE / 2n) / SCALE;
}

function holding(row: Row): HoldingView {
  const freshness = ["fresh", "stale", "partial", "unavailable"].includes(String(row.price_freshness))
    ? row.price_freshness as HoldingView["freshness"]
    : "unavailable";
  const shares = fixed(row.shares);
  const average = fixed(row.avg_cost);
  const price = fixed(row.price);
  const value = shares !== null && price !== null && freshness === "fresh"
    ? multiply(shares, price)
    : null;
  const cost = shares !== null && average !== null ? multiply(shares, average) : null;
  const change = value !== null && cost !== null ? value - cost : null;
  const changePercent = change !== null && cost !== null && cost !== 0n
    ? (Number(change) * 100 / Number(cost)).toFixed(2).replace(/\.00$/, "")
    : null;
  return {
    ticker: text(row.ticker, 24) ?? "UNKNOWN",
    shares: text(row.shares, 80) ?? "0",
    average_cost: text(row.avg_cost, 80) ?? "0",
    bucket: text(row.bucket, 40),
    opened_at: text(row.opened_at, 40),
    stop: text(row.stop, 80),
    target: text(row.target, 80),
    price: freshness === "fresh" ? text(row.price, 80) : null,
    price_as_of: text(row.price_as_of, 40),
    value: value === null ? null : decimal(value),
    unrealized_amount: change === null ? null : decimal(change),
    unrealized_percent: changePercent,
    weight_percent: null,
    freshness,
  };
}

export function mapPortfolio(
  holdingRows: readonly Row[],
  planRows: readonly Row[],
  transactionRows: readonly Row[],
): PortfolioView {
  const holdings = holdingRows.slice(0, 100).map(holding);
  let costBasis = 0n;
  let totalValue = 0n;
  let complete = holdings.length > 0;
  for (const row of holdings) {
    const shares = fixed(row.shares);
    const average = fixed(row.average_cost);
    if (shares !== null && average !== null) costBasis += multiply(shares, average);
    const value = fixed(row.value);
    if (value === null) complete = false;
    else totalValue += value;
  }
  if (complete && totalValue > 0n) {
    for (const row of holdings) {
      const value = fixed(row.value) ?? 0n;
      row.weight_percent = (Number(value) * 100 / Number(totalValue)).toFixed(2).replace(/\.00$/, "");
    }
  }
  const plans: InvestmentPlanView[] = planRows.slice(0, 100).flatMap((row) => {
    const id = text(row.id, 64);
    const ticker = text(row.ticker, 24);
    const amount = text(row.amount, 80);
    const nextDue = text(row.next_due_on, 40);
    const dueDay = integer(row.due_day);
    if (!id || !ticker || !amount || !nextDue || dueDay === null) return [];
    return [{
      id,
      ticker,
      amount,
      cadence: "monthly" as const,
      next_due_on: nextDue,
      due_day: dueDay,
      active: row.active === true,
    }];
  });
  return {
    holdings,
    plans,
    transactions: mapTransactions(transactionRows),
    totals: {
      cost_basis: decimal(costBasis),
      value: complete ? decimal(totalValue) : null,
      unrealized_amount: complete ? decimal(totalValue - costBasis) : null,
    },
    comparison_availability: "structured_companion",
  };
}

export function mapTransactions(rows: readonly Row[]): TransactionView[] {
  return rows.slice(0, 50).flatMap((row) => {
    const id = text(row.id, 64);
    const timestamp = text(row.ts, 40);
    const ticker = text(row.ticker, 24);
    const quantity = text(row.qty, 80);
    const price = text(row.price, 80);
    const side = row.side === "buy" || row.side === "sell" ? row.side : null;
    if (!id || !timestamp || !ticker || !quantity || !price || !side) return [];
    return [{
      id,
      timestamp,
      executed_on: text(row.executed_on, 40),
      ticker,
      side,
      quantity,
      price,
      source: text(row.source, 120),
    }];
  });
}

export function mapIdea(row: Row): IdeaView {
  const evaluationBound = Boolean(text(row.evaluation_id, 64));
  const rawStatus = text(row.policy_status, 40);
  const supportedStatus = ["approved", "downgraded", "vetoed"].includes(rawStatus ?? "")
    ? rawStatus as IdeaView["policy_status"]
    : "legacy_unverified";
  const analyst = record(row.analyst);
  const checker = record(row.checker);
  return {
    id: text(row.id, 64) ?? "unknown",
    ticker: text(row.ticker, 24) ?? "UNKNOWN",
    profile: text(row.profile ?? row.bucket, 40) ?? "unclassified",
    final_action: evaluationBound ? text(row.final_action, 40) : text(row.action, 40),
    policy_status: evaluationBound ? supportedStatus : "legacy_unverified",
    policy_version: evaluationBound ? integer(row.policy_version) : null,
    confidence: text(row.confidence, 40),
    entry_zone_low: text(row.entry_zone_low, 80),
    entry_zone_high: text(row.entry_zone_high, 80),
    stop: text(row.stop, 80),
    target: text(row.target, 80),
    valid_until: text(row.valid_until, 40),
    bull_case: text(row.bull, 2_000),
    bear_case: text(row.bear, 2_000),
    decisive_factor: text(row.decisive_factor, 2_000),
    invalidation: text(row.invalidation_level ?? row.invalidation, 2_000),
    reason_codes: textArray(row.reason_codes),
    analyst_complete: analyst.completed === true || analyst.verdict === "complete",
    checker_complete: checker.completed === true || ["pass", "approve", "downgrade", "veto"].includes(String(checker.verdict)),
    sources: sourceLinks(row.evidence),
  };
}

export function mapCompanionResponse(value: unknown): CompanionView | null {
  const response = record(value);
  const analysis = record(response.companion_analysis);
  if (Object.keys(analysis).length === 0) return null;
  const qualification = analysis.qualification_status === "qualified" ? "qualified" : "insufficient";
  const horizons: CompanionHorizonView[] = Array.isArray(analysis.horizons)
    ? analysis.horizons.slice(0, 3).flatMap((item) => {
      const row = record(item);
      const years = integer(row.years);
      if (years !== 3 && years !== 5 && years !== 10) return [];
      return [{
        years,
        baseline_annualized_percent: text(row.baseline_annualized_return_pct, 80) ?? "",
        companion_annualized_percent: text(row.companion_annualized_return_pct, 80) ?? "",
        baseline_max_drawdown_percent: text(row.baseline_max_drawdown_pct, 80) ?? "",
        companion_max_drawdown_percent: text(row.companion_max_drawdown_pct, 80) ?? "",
        correlation: text(row.daily_return_correlation, 80) ?? "",
      }];
    })
    : [];
  const rolling = record(analysis.rolling_one_year);
  const sampleCount = integer(rolling.sample_windows);
  const contributionHistory = sampleCount === null ? null : {
    contributed: text(rolling.total_contributed_usd, 80) ?? "",
    lower_ending_value: text(rolling.weak_ending_value_usd, 80) ?? "",
    median_ending_value: text(rolling.middle_ending_value_usd, 80) ?? "",
    higher_ending_value: text(rolling.strong_ending_value_usd, 80) ?? "",
    sample_count: sampleCount,
  };
  const role = ["substitute", "tilt", "diversifier", "replacement", "satellite"].includes(String(analysis.role))
    ? analysis.role as CompanionView["role"]
    : null;
  return {
    status: qualification,
    baseline_ticker: text(analysis.baseline_ticker, 24),
    companion_ticker: text(analysis.companion_ticker, 24),
    role,
    thesis: text(analysis.thesis, 2_000),
    risk_note: text(analysis.risk_note, 2_000),
    plan_unchanged: true,
    recurring_plan_review_eligible: analysis.recurring_plan_review_eligible === true,
    horizons,
    contribution_history: contributionHistory,
    evidence: sourceLinks(analysis.evidence ?? analysis.evidence_blocks),
    disclaimer: "Historical scenarios are not forecasts. This is a suggestion only; you decide and place every trade.",
  };
}

function messageIds(value: unknown): number[] {
  return Array.isArray(value)
    ? value.slice(0, 10).flatMap((item) => {
      const parsed = integer(item);
      return parsed !== null && parsed > 0 ? [parsed] : [];
    })
    : [];
}

export function mapPublicationReceipt(row: Row): AlertView {
  const persisted = text(row.status, 40) ?? "incomplete";
  const ids = messageIds(row.telegram_message_ids);
  let state: ReceiptStatus = ["ready", "sending", "delivery_failed", "delivery_unknown"].includes(persisted)
    ? persisted as ReceiptStatus
    : "incomplete";
  if (persisted === "delivered") state = ids.length > 0 ? "delivered" : "incomplete";
  if (persisted === "suppressed") state = "suppressed";
  return {
    id: text(row.id, 64) ?? "unknown",
    kind: text(row.kind, 80) ?? "unknown",
    phase: text(row.phase, 80) ?? "unknown",
    state,
    rendered_text: text(row.rendered_body, 14_000) ?? "",
    rendered_hash: text(row.rendered_hash, 64) ?? "",
    template_version: text(row.template_version, 20) ?? "unknown",
    telegram_message_ids: state === "suppressed" ? [] : ids,
    attempt_count: integer(row.attempt_count) ?? 0,
    created_at: text(row.created_at, 40) ?? "",
    delivered_at: state === "delivered" ? text(row.delivered_at, 40) : null,
    suppression_reason: state === "suppressed" ? text(row.suppression_reason ?? row.error, 500) : null,
    rule_ticker: text(row.rule_ticker, 24),
    rule_state: text(row.rule_state, 40),
    event_status: text(row.event_status, 40),
    owner_action: text(row.owner_action, 40),
    sources: sourceLinks(row.sources),
  };
}

export function mapRun(row: Row): RunSummaryView {
  const allowedKinds = ["pre-market", "intraday", "post-market", "on-demand", "weekly-audit"];
  const allowedStatus = ["running", "completed", "partial", "failed"];
  const publication = text(row.publication_status, 40);
  const publicationIds = messageIds(row.publication_message_ids);
  let receiptStatus = publication && ["ready", "sending", "delivery_failed", "delivery_unknown", "suppressed"].includes(publication)
    ? publication as ReceiptStatus
    : null;
  if (publication === "delivered") receiptStatus = publicationIds.length > 0 ? "delivered" : "incomplete";
  return {
    id: text(row.id, 64) ?? "unknown",
    kind: (allowedKinds.includes(String(row.kind)) ? row.kind : "on-demand") as RunSummaryView["kind"],
    status: (allowedStatus.includes(String(row.status)) ? row.status : "partial") as RunSummaryView["status"],
    started_at: text(row.started_at, 40) ?? "",
    finished_at: text(row.finished_at, 40),
    data_as_of: text(row.data_as_of, 40),
    policy_version: integer(row.policy_version),
    evaluation_count: integer(row.evaluation_count) ?? 0,
    suggestion_count: integer(row.suggestion_count) ?? 0,
    publication_status: receiptStatus,
  };
}
