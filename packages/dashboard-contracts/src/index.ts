export const CONTRACT_VERSION = 1 as const;

const SAFE_SOURCE_PATHS: Readonly<Record<string, readonly string[]>> = {
  "api.gdeltproject.org": ["/api/v2/"],
  "www.alphavantage.co": ["/query"],
  "finnhub.io": ["/api/v1/"],
  "query1.finance.yahoo.com": ["/v8/finance/chart/", "/v10/finance/quoteSummary/"],
  "www.sec.gov": ["/Archives/", "/files/", "/ixviewer/"],
  "data.sec.gov": ["/submissions/", "/api/xbrl/"],
  "www.federalregister.gov": ["/documents/", "/api/v1/documents/"],
  "www.whitehouse.gov": ["/briefing-room/"],
  "www.energy.gov": ["/articles/", "/gdo/", "/ceser/", "/oe/"],
  "www.defense.gov": ["/News/", "/Contracts/"],
  "api.eia.gov": ["/v2/"], "www.eia.gov": ["/todayinenergy/", "/electricity/"],
  "api.stlouisfed.org": ["/fred/"], "fred.stlouisfed.org": ["/series/"],
  "api.bls.gov": ["/publicAPI/"], "www.bls.gov": ["/news.release/", "/regions/"],
  "apps.bea.gov": ["/api/"], "www.bea.gov": ["/news/", "/data/"],
};

export function safeSourceUrl(value: string | null): string | null {
  if (!value || value.length > 2048 || /[\u0000-\u001f\u007f]/u.test(value) || /^https:\/\/[^/?#]+:\d+(?:[/?#]|$)/iu.test(value)) return null;
  try {
    const parsed = new URL(value);
    const prefixes = SAFE_SOURCE_PATHS[parsed.hostname];
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.port || parsed.hash || !prefixes?.some((prefix) => parsed.pathname.startsWith(prefix))) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

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
  | "accepted_by_telegram"
  | "delivered"
  | "delivery_failed"
  | "delivery_unknown"
  | "suppressed"
  | "duplicate"
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
    cost_basis: string | null;
    unrealized_amount: string | null;
    incomplete: boolean;
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
    cost_basis: string | null;
    value: string | null;
    unrealized_amount: string | null;
  };
  summary: {
    costBasis: number | null;
    unrealizedProfit: number | null;
    incomplete: boolean;
  };
  comparison_availability: "structured_companion" | "coverage_only" | "unavailable";
  latest_intelligence_run_id: string | null;
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
  intelligence_run_id: string | null;
  evidence_as_of: string | null;
  outcome: {
    result: string;
    horizon_days: number;
    graded_at: string;
  } | null;
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

export interface ThemeView {
  key: string;
  relationship_count: number;
  evidence_count: number;
}

export interface MarketEventView {
  id: string;
  type: string;
  title: string;
  summary: string;
  occurred_at: string | null;
  effective_at: string | null;
  materiality: string;
  confidence: string;
  sources: SourceLink[];
}

export interface CandidateRelationshipView {
  id: string;
  event_id: string | null;
  candidate_key: string;
  ticker: string | null;
  rank: number;
  total_score: string;
  qualified: boolean;
  veto_reasons: string[];
  sources: SourceLink[];
}

export interface SourceCoverageView {
  provider: string;
  status: "complete" | "partial" | "unavailable";
  retrieved_at: string | null;
  accepted_count: number;
  dropped_count: number;
}

export interface LearningObservationView {
  id: string;
  kind: "outcome" | "missed-event" | "source-failure" | "noise";
  status: "observation" | "owner_review";
  policy_version: number;
  horizon_days: 0 | 5 | 21 | 63;
  sample_size: number;
  benchmark: string | null;
  false_positive_rate: string | null;
  evidence_count: number;
  limitations: string[];
  proposal_available: boolean;
  created_at: string;
}

export interface IntelligenceThemeViewV2 {
  theme_id: string;
  episode_id: string;
  revision_id: string;
  revision: number;
  mechanism: string;
  subject: string;
  jurisdiction: string;
  state: "active" | "expired" | "closed";
  first_seen: string;
  last_seen: string;
  next_review_at: string;
  expires_at: string;
  adverse_evidence_count: number;
  missing_questions: string[];
  invalidation_conditions: string[];
}

export interface IntelligenceEvidenceViewV2 {
  evidence_id: string;
  label: string;
  url: string | null;
  passage: string;
  role: "supporting" | "opposing";
  retrieved_at: string;
}

export interface IntelligenceCompanyViewV2 {
  company_id: string;
  name: string;
  ticker: string | null;
  outside_watchlist: boolean | null;
  relationship_paths: string[];
  evidence_ids: string[];
  missing_inputs: string[];
}

export interface IntelligenceView {
  intelligence_version: 2;
  run_id: string | null;
  data_as_of: string | null;
  themes: IntelligenceThemeViewV2[];
  companies: IntelligenceCompanyViewV2[];
  evidence: IntelligenceEvidenceViewV2[];
  source_health: SourceCoverageView[];
  coverage: { mode: "bounded"; complete_market_coverage: false };
  reference: { state: "healthy" | "reference_stale" | "reference_unavailable" | "unavailable"; manifest_id?: string; as_of?: string; age_seconds?: number };
  scope: { research_only: true; market_wide: true };
  backlog: { available: number; returned: number; deferred: number; byte_truncated: boolean };
  omissions: string[];
  boundaries: { research_only: true; execution_disabled: true; valuation_unavailable: true };
}

export interface ReportSummaryView {
  id: string;
  market_date: string;
  kind: "morning" | "urgent" | "weekly" | "monthly" | "theme" | "on-demand" | "intraday" | "unknown";
  title: string;
  summary: string;
  report_hash: string;
  created_at: string;
}

export interface ReportsView {
  reports: ReportSummaryView[];
  next_cursor: string | null;
}

export interface ReportSectionView {
  heading: string;
  body: string;
}

export interface ReceiptTimelineItem {
  request_id: string;
  status: ReceiptStatus;
  gateway_status: "completed";
  attempt_count: number;
  at: string;
  telegram_message_ids: number[];
}

export interface ReportDetailView extends ReportSummaryView {
  sections: ReportSectionView[];
  sources: SourceLink[];
  publication: ReceiptTimelineItem[];
}

export interface SystemView {
  product_version: string;
  api_version: string;
  policy_version: number | null;
  alert_mode: "shadow" | "canary" | "enabled" | "unavailable";
  latest_by_kind: Record<string, RunSummaryView | null>;
  latest_publication_status: ReceiptStatus | null;
  boundaries: TodayView["boundaries"];
  source_coverage: SourceCoverageView[];
  latest_report: ReportSummaryView | null;
  latest_intelligence_run_id: string | null;
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

export function parseIntelligenceView(value: unknown): IntelligenceView {
  const row = object(value, "intelligence");
  exactObjectKeys(row, [
    "intelligence_version", "run_id", "data_as_of", "themes", "companies", "evidence",
    "source_health", "coverage", "reference", "scope", "backlog", "omissions", "boundaries",
  ], "intelligence");
  if (row.intelligence_version !== 2) throw new Error("unsupported intelligence_version");
  const runId = row.run_id === null ? null : uuid(row.run_id, "intelligence.run_id");
  const dataAsOf = boundedTimestamp(row.data_as_of, "intelligence.data_as_of", true);
  const themes = boundedArray(row.themes, "intelligence.themes", 25).map((value, index) => {
    const theme = object(value, `intelligence.themes[${index}]`);
    exactObjectKeys(theme, ["theme_id", "episode_id", "revision_id", "revision", "mechanism", "subject", "jurisdiction", "state", "first_seen", "last_seen", "next_review_at", "expires_at", "adverse_evidence_count", "missing_questions", "invalidation_conditions"], `intelligence.themes[${index}]`);
    const state = boundedEnum(theme.state, ["active", "expired", "closed"] as const, `intelligence.themes[${index}].state`);
    return {
      theme_id: boundedString(theme.theme_id, 80, "theme_id"),
      episode_id: uuid(theme.episode_id, "episode_id"), revision_id: uuid(theme.revision_id, "revision_id"),
      revision: boundedInteger(theme.revision, 1, 10000, "revision"),
      mechanism: boundedString(theme.mechanism, 240, "mechanism"), subject: boundedString(theme.subject, 240, "subject"),
      jurisdiction: boundedString(theme.jurisdiction, 80, "jurisdiction"), state,
      first_seen: boundedTimestamp(theme.first_seen, "first_seen")!, last_seen: boundedTimestamp(theme.last_seen, "last_seen")!,
      next_review_at: boundedTimestamp(theme.next_review_at, "next_review_at")!, expires_at: boundedTimestamp(theme.expires_at, "expires_at")!,
      adverse_evidence_count: boundedInteger(theme.adverse_evidence_count, 0, 64, "adverse_evidence_count"),
      missing_questions: boundedStrings(theme.missing_questions, 24, 500, "missing_questions"),
      invalidation_conditions: boundedStrings(theme.invalidation_conditions, 24, 500, "invalidation_conditions"),
    };
  });
  const companies = boundedArray(row.companies, "intelligence.companies", 12).map((value, index) => {
    const company = object(value, `intelligence.companies[${index}]`);
    exactObjectKeys(company, ["company_id", "name", "ticker", "outside_watchlist", "relationship_paths", "evidence_ids", "missing_inputs"], `intelligence.companies[${index}]`);
    if (company.outside_watchlist !== null && typeof company.outside_watchlist !== "boolean") throw new Error("invalid outside_watchlist");
    return {
      company_id: boundedString(company.company_id, 128, "company_id"), name: boundedString(company.name, 240, "name"),
      ticker: company.ticker === null ? null : boundedString(company.ticker, 24, "ticker"),
      outside_watchlist: company.outside_watchlist as boolean | null,
      relationship_paths: boundedStrings(company.relationship_paths, 4, 500, "relationship_paths"),
      evidence_ids: boundedStrings(company.evidence_ids, 8, 64, "evidence_ids"),
      missing_inputs: boundedStrings(company.missing_inputs, 16, 500, "missing_inputs"),
    };
  });
  const evidence = boundedArray(row.evidence, "intelligence.evidence", 96).map((value, index) => {
    const item = object(value, `intelligence.evidence[${index}]`);
    exactObjectKeys(item, ["evidence_id", "label", "url", "passage", "role", "retrieved_at"], `intelligence.evidence[${index}]`);
    return {
      evidence_id: uuid(item.evidence_id, "evidence_id"), label: boundedString(item.label, 256, "label"),
      url: item.url === null ? null : safeSourceUrl(boundedString(item.url, 2048, "url")), passage: boundedString(item.passage, 2000, "passage", true),
      role: boundedEnum(item.role, ["supporting", "opposing"] as const, "role"), retrieved_at: boundedTimestamp(item.retrieved_at, "retrieved_at")!,
    };
  });
  const sourceHealth = boundedArray(row.source_health, "intelligence.source_health", 50).map((value, index) => {
    const source = object(value, `intelligence.source_health[${index}]`);
    exactObjectKeys(source, ["provider", "status", "retrieved_at", "accepted_count", "dropped_count"], `intelligence.source_health[${index}]`);
    const persisted = source.status;
    const status = persisted === "complete" || persisted === "partial" || persisted === "unavailable"
      ? persisted
      : persisted === "succeeded" || persisted === "cache_hit"
      ? "complete"
      : persisted === "failed" && Number(source.accepted_count) > 0
      ? "partial"
      : "unavailable";
    return {
      provider: boundedString(source.provider, 80, "provider"),
      status: boundedEnum(status, ["complete", "partial", "unavailable"] as const, "status"),
      retrieved_at: boundedTimestamp(source.retrieved_at, "retrieved_at", true),
      accepted_count: boundedInteger(source.accepted_count, 0, 10000, "accepted_count"),
      dropped_count: boundedInteger(source.dropped_count ?? 0, 0, 10000, "dropped_count"),
    };
  });
  const coverage = object(row.coverage, "intelligence.coverage");
  exactObjectKeys(coverage, ["mode", "complete_market_coverage"], "intelligence.coverage");
  if (coverage.mode !== "bounded" || coverage.complete_market_coverage !== false) {
    throw new Error("intelligence coverage boundary mismatch");
  }
  const reference = object(row.reference, "intelligence.reference");
  const referenceAllowed = ["state", ...(Object.hasOwn(reference, "manifest_id") ? ["manifest_id"] : []), ...(Object.hasOwn(reference, "as_of") ? ["as_of"] : []), ...(Object.hasOwn(reference, "age_seconds") ? ["age_seconds"] : [])];
  exactObjectKeys(reference, referenceAllowed, "intelligence.reference");
  const scope = object(row.scope, "intelligence.scope"); exactObjectKeys(scope, ["research_only", "market_wide"], "intelligence.scope");
  const backlog = object(row.backlog, "intelligence.backlog"); exactObjectKeys(backlog, ["available", "returned", "deferred", "byte_truncated"], "intelligence.backlog");
  const boundaries = object(row.boundaries, "intelligence.boundaries"); exactObjectKeys(boundaries, ["research_only", "execution_disabled", "valuation_unavailable"], "intelligence.boundaries");
  if (scope.research_only !== true || scope.market_wide !== true || boundaries.research_only !== true || boundaries.execution_disabled !== true || boundaries.valuation_unavailable !== true || typeof backlog.byte_truncated !== "boolean") throw new Error("intelligence authority boundary mismatch");
  const evidenceIds = new Set(evidence.map((item) => item.evidence_id));
  if (companies.some((company) => company.evidence_ids.some((id) => !evidenceIds.has(id)))) throw new Error("intelligence contains dangling evidence reference");
  return {
    intelligence_version: 2, run_id: runId, data_as_of: dataAsOf, themes, companies, evidence,
    source_health: sourceHealth, coverage: { mode: "bounded", complete_market_coverage: false },
    reference: {
      state: boundedEnum(reference.state, ["healthy", "reference_stale", "reference_unavailable", "unavailable"] as const, "reference.state"),
      ...(reference.manifest_id !== undefined ? { manifest_id: uuid(reference.manifest_id, "reference.manifest_id") } : {}),
      ...(reference.as_of !== undefined ? { as_of: boundedTimestamp(reference.as_of, "reference.as_of")! } : {}),
      ...(reference.age_seconds !== undefined ? { age_seconds: boundedInteger(reference.age_seconds, 0, Number.MAX_SAFE_INTEGER, "reference.age_seconds") } : {}),
    },
    scope: { research_only: true, market_wide: true },
    backlog: { available: boundedInteger(backlog.available, 0, 1000000, "backlog.available"), returned: boundedInteger(backlog.returned, 0, 25, "backlog.returned"), deferred: boundedInteger(backlog.deferred, 0, 1000000, "backlog.deferred"), byte_truncated: backlog.byte_truncated },
    omissions: boundedStrings(row.omissions, 50, 1000, "omissions"),
    boundaries: { research_only: true, execution_disabled: true, valuation_unavailable: true },
  };
}

function exactObjectKeys(row: Record<string, unknown>, expected: readonly string[], name: string): void {
  const actual = Object.keys(row).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) throw new Error(`${name} has unexpected fields`);
}

function boundedArray(value: unknown, name: string, max: number): unknown[] {
  if (!Array.isArray(value) || value.length > max) throw new Error(`${name} must be a bounded array`);
  return value;
}

function boundedString(value: unknown, max: number, name: string, empty = false): string {
  if (typeof value !== "string" || value.length > max || (!empty && value.trim().length === 0)) throw new Error(`${name} must be a bounded string`);
  return value;
}

function boundedStrings(value: unknown, max: number, itemMax: number, name: string): string[] {
  const items = boundedArray(value, name, max).map((item) => boundedString(item, itemMax, name));
  if (new Set(items).size !== items.length) throw new Error(`${name} contains duplicates`);
  return items;
}

function boundedInteger(value: unknown, min: number, max: number, name: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) throw new Error(`${name} must be a bounded integer`);
  return value;
}

function boundedEnum<T extends string>(value: unknown, allowed: readonly T[], name: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) throw new Error(`${name} is invalid`);
  return value as T;
}

function boundedTimestamp(value: unknown, name: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || value.length > 40 || Number.isNaN(Date.parse(value))) throw new Error(`${name} must be an ISO timestamp`);
  return value;
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
