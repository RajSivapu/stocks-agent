export const CONTRACT_VERSION = 1 as const;

export type Freshness = "fresh" | "stale" | "partial" | "unavailable";
export type MarketState =
  | "regular"
  | "pre_market"
  | "post_market"
  | "closed"
  | "holiday"
  | "as_of_close"
  | "unknown";
export type ReceiptStatus =
  | "ready"
  | "sending"
  | "delivered"
  | "delivery_failed"
  | "delivery_unknown"
  | "suppressed"
  | "incomplete";
export type DashboardErrorCode =
  | "unauthorized"
  | "owner_only"
  | "not_found"
  | "rate_limited"
  | "temporarily_unavailable"
  | "invalid_request";

export interface DashboardEnvelope<T> {
  contract_version: 1;
  request_id: string;
  generated_at: string;
  data_as_of: string | null;
  freshness: Freshness;
  market_state: MarketState;
  data: T;
  next_cursor?: string | null;
}

export interface DashboardErrorEnvelope {
  contract_version: 1;
  request_id: string;
  error: {
    code: DashboardErrorCode;
    message: string;
  };
}

export interface SourceLink {
  label: string;
  url: string | null;
}

export interface HoldingView {
  ticker: string;
  shares: string;
  average_cost: string;
  bucket: string | null;
  opened_at: string | null;
  stop: string | null;
  target: string | null;
  price: string | null;
  price_as_of: string | null;
  price_source: string | null;
  market_state: MarketState;
  value: string | null;
  unrealized_amount: string | null;
  unrealized_percent: string | null;
  weight_percent: string | null;
  freshness: Freshness;
}

export interface InvestmentPlanView {
  id: string;
  ticker: string;
  amount: string;
  cadence: "monthly";
  next_due_on: string;
  due_day: number;
  active: boolean;
}

export interface TransactionView {
  id: string;
  timestamp: string;
  executed_on: string | null;
  ticker: string;
  side: "buy" | "sell";
  quantity: string;
  price: string;
  source: string | null;
}

export interface AttentionItemView {
  id: string;
  severity: "critical" | "review" | "update" | "watch" | "system";
  title: string;
  detail: string;
  data_as_of: string | null;
  destination: string;
}

export interface RunSummaryView {
  id: string;
  kind: "pre-market" | "intraday" | "post-market" | "on-demand" | "weekly-audit" | "unknown";
  status: "running" | "completed" | "partial" | "failed" | "unknown";
  started_at: string;
  finished_at: string | null;
  data_as_of: string | null;
  policy_version: number | null;
  evaluation_count: number;
  suggestion_count: number;
  publication_status: ReceiptStatus | null;
}

export interface TodayView {
  boundaries: {
    owner_only: true;
    suggestion_only: true;
    friend_invitations: "disabled";
    brokerage_authority: "none";
  };
  attention: AttentionItemView[];
  latest_run: RunSummaryView | null;
  portfolio: {
    value: string | null;
    cost_basis: string;
    unrealized_amount: string | null;
    holdings: HoldingView[];
    data_as_of: string | null;
    market_state: MarketState;
    price_sources: string[];
  };
  market_summary: string | null;
  entry_zones: IdeaView[];
  companion: CompanionView | null;
}

export interface PortfolioView {
  holdings: HoldingView[];
  plans: InvestmentPlanView[];
  transactions: TransactionView[];
  totals: {
    cost_basis: string;
    value: string | null;
    unrealized_amount: string | null;
  };
  comparison_availability: "structured_companion" | "coverage_only" | "unavailable";
}

export interface IdeaView {
  id: string;
  ticker: string;
  profile: string;
  final_action: string | null;
  policy_status: "approved" | "downgraded" | "vetoed" | "legacy_unverified";
  policy_version: number | null;
  confidence: string | null;
  entry_zone_low: string | null;
  entry_zone_high: string | null;
  stop: string | null;
  target: string | null;
  valid_until: string | null;
  bull_case: string | null;
  bear_case: string | null;
  decisive_factor: string | null;
  invalidation: string | null;
  reason_codes: string[];
  analyst_complete: boolean;
  checker_complete: boolean;
  sources: SourceLink[];
}

export interface IdeasView {
  ideas: IdeaView[];
}

export interface CompanionHorizonView {
  years: 3 | 5 | 10;
  baseline_annualized_percent: string;
  companion_annualized_percent: string;
  baseline_max_drawdown_percent: string;
  companion_max_drawdown_percent: string;
  correlation: string;
}

export interface CompanionView {
  status: "qualified" | "insufficient" | "not_nominated" | "not_reviewed";
  baseline_ticker: string | null;
  companion_ticker: string | null;
  role: "substitute" | "tilt" | "diversifier" | "replacement" | "satellite" | null;
  thesis: string | null;
  risk_note: string | null;
  plan_unchanged: true;
  recurring_plan_review_eligible: boolean;
  horizons: CompanionHorizonView[];
  contribution_history: {
    contributed: string;
    lower_ending_value: string;
    median_ending_value: string;
    higher_ending_value: string;
    sample_count: number;
  } | null;
  evidence: SourceLink[];
  disclaimer: string;
}

export interface AlertView {
  id: string;
  kind: string;
  phase: string;
  state: ReceiptStatus;
  rendered_text: string;
  rendered_hash: string;
  template_version: string;
  telegram_message_ids: number[];
  attempt_count: number;
  created_at: string;
  delivered_at: string | null;
  suppression_reason: string | null;
  rule_ticker: string | null;
  rule_state: string | null;
  event_status: string | null;
  owner_action: string | null;
  sources: SourceLink[];
}

export interface AlertsView {
  alerts: AlertView[];
}

export interface RunDetailView {
  run: RunSummaryView;
  request_receipts: Array<{
    request_id: string;
    operation: string;
    status: string;
    response_digest: string | null;
    attempt_count: number;
    finished_at: string | null;
  }>;
  evaluations: IdeaView[];
  write_counts: Record<string, number>;
  telegram_message_ids: number[];
  incomplete_stages: string[];
}

export interface RunsView {
  runs: RunSummaryView[];
}

export interface SystemView {
  product_version: string;
  api_version: string;
  policy_version: number | null;
  alert_mode: "shadow" | "canary" | "enabled" | "unavailable";
  latest_by_kind: Record<string, RunSummaryView | null>;
  latest_publication_status: ReceiptStatus | null;
  boundaries: TodayView["boundaries"];
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ISO_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const FRESHNESS = new Set<Freshness>(["fresh", "stale", "partial", "unavailable"]);
const MARKET_STATES = new Set<MarketState>([
  "regular", "pre_market", "post_market", "closed", "holiday", "as_of_close", "unknown",
]);
const ERROR_CODES = new Set<DashboardErrorCode>([
  "unauthorized", "owner_only", "not_found", "rate_limited", "temporarily_unavailable",
  "invalid_request",
]);

function object(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} must be an object`);
  }
  return value as Record<string, unknown>;
}

function uuid(value: unknown, name: string): string {
  if (typeof value !== "string" || value.length > 64 || !UUID_PATTERN.test(value)) {
    throw new Error(`${name} must be a UUID`);
  }
  return value;
}

function timestamp(value: unknown, name: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || !ISO_PATTERN.test(value) || Number.isNaN(Date.parse(value))) {
    throw new Error(`${name} must be an ISO timestamp`);
  }
  return value;
}

export function parseDashboardEnvelope<T = unknown>(value: unknown): DashboardEnvelope<T> {
  const row = object(value, "dashboard envelope");
  if (row.contract_version !== CONTRACT_VERSION) throw new Error("unsupported contract_version");
  uuid(row.request_id, "request_id");
  timestamp(row.generated_at, "generated_at");
  timestamp(row.data_as_of, "data_as_of", true);
  if (!FRESHNESS.has(row.freshness as Freshness)) throw new Error("invalid freshness");
  if (!MARKET_STATES.has(row.market_state as MarketState)) throw new Error("invalid market_state");
  object(row.data, "data");
  if (row.next_cursor !== undefined && row.next_cursor !== null &&
      (typeof row.next_cursor !== "string" || row.next_cursor.length > 512)) {
    throw new Error("invalid next_cursor");
  }
  return row as unknown as DashboardEnvelope<T>;
}

export function parseDashboardErrorEnvelope(value: unknown): DashboardErrorEnvelope {
  const row = object(value, "dashboard error envelope");
  if (row.contract_version !== CONTRACT_VERSION) throw new Error("unsupported contract_version");
  uuid(row.request_id, "request_id");
  const error = object(row.error, "error");
  if (!ERROR_CODES.has(error.code as DashboardErrorCode)) throw new Error("invalid error.code");
  if (typeof error.message !== "string" || error.message.length < 1 || error.message.length > 160) {
    throw new Error("invalid error.message");
  }
  return row as unknown as DashboardErrorEnvelope;
}
