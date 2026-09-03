export type Operation =
  | "start_run"
  | "read_context"
  | "record_artifacts"
  | "grade_due_decisions"
  | "evaluate_and_publish"
  | "evaluate_alert_rules"
  | "finish_run";
export type Phase = "pre-market" | "intraday" | "post-market" | "on-demand";
export type Action =
  | "buy"
  | "add"
  | "hold"
  | "reduce"
  | "sell"
  | "watch"
  | "avoid";
export type NotificationKind =
  | "brief"
  | "new_idea"
  | "entry_trigger"
  | "stop_near"
  | "stop_breach"
  | "target_near"
  | "target_hit"
  | "thesis_break"
  | "data_warning"
  | "holiday";
export type DecisionMode = "discretionary" | "owner_plan";
export type Confidence = "low" | "medium" | "high";
export type Bucket = "core" | "growth" | "speculative";
export type EvidenceStatus =
  | "fresh"
  | "stale"
  | "fallback"
  | "missing"
  | "failed"
  | "conflicting"
  | "unsupported";
export type PolicyStatus =
  | "approved"
  | "downgraded"
  | "vetoed"
  | "legacy_unverified";
export type AlertProfile = "long_term" | "balanced" | "active";
export type AlertV3Class = "entry_trigger" | "stop_breach" | "target_hit";
export type AlertRuleKind =
  | "price_cross"
  | "price_zone"
  | "sma_cross"
  | "rsi_range"
  | "volume_multiple"
  | "recorded_stop"
  | "recorded_target"
  | "screen_entry"
  | "event_window";
export type AlertRuleState =
  | "draft"
  | "active"
  | "paused"
  | "snoozed"
  | "dismissed"
  | "expired";
export type AlertSeverity =
  | "critical"
  | "review"
  | "update"
  | "watch"
  | "system";
export type AlertSession = "regular" | "pre_market" | "post_market" | "all";
export type ConfirmationMode = "bar_close" | "two_quote";
export type AlertTimeframe = "quote" | "15m" | "1h" | "1d";

export interface AlertCondition {
  kind: AlertRuleKind;
  operator: "above" | "below" | "inside" | "outside";
  left: string;
  right: string | null;
  timeframe: AlertTimeframe;
}

export interface AlertRuleSnapshot {
  rule_id: string;
  version: number;
  state: AlertRuleState;
  ticker: string;
  profile: AlertProfile;
  severity: AlertSeverity;
  session: AlertSession;
  confirmation: ConfirmationMode;
  conditions: AlertCondition[];
  cooldown_seconds: number;
  fire_limit: number;
  valid_until: string;
  owner_note: string;
}

export interface AlertEvidencePoint {
  value: string;
  comparison_value: string | null;
  observed_at: string;
  bar_complete: boolean;
}

export interface AlertConditionEvidence {
  condition_index: number;
  status: EvidenceStatus;
  market_session: AlertSession;
  evidence_ids: string[];
  points: AlertEvidencePoint[];
}

export interface AlertConditionResult {
  condition: AlertCondition;
  passed: boolean | null;
  observed_value: string | null;
  evidence_ids: string[];
}

export interface AlertEvaluation {
  rule: AlertRuleSnapshot;
  status: "triggered" | "not_triggered" | "unsafe_to_evaluate";
  reason_codes: string[];
  observed_at: string | null;
  evaluated_at: string;
  market_session: AlertSession;
  condition_results: AlertConditionResult[];
}

export interface AlertRecentEvent {
  fingerprint: string;
  status: "triggered" | "unsafe_to_evaluate";
  evaluated_at: string;
  severity: AlertSeverity;
}

export interface AlertSourceSummary {
  ticker: string;
  confidence: Confidence;
  valid_until: string | null;
  invalidation_price: string | null;
  stop: string | null;
  target: string | null;
  position_value_after: string | null;
  total_investable_value: string | null;
  evidence: Array<{ id: string; status: EvidenceStatus }>;
  reasons: string[];
}

export interface GatewayEnvelope {
  schema_version: 1;
  operation: Operation;
  request_id: string;
  run_id: string | null;
  dry_run: boolean;
  payload: unknown;
}

export interface EvidenceBlock {
  id: string;
  kind:
    | "quote"
    | "fundamentals"
    | "technicals"
    | "news"
    | "event"
    | "macro"
    | "sector";
  source: string;
  status: EvidenceStatus;
  observed_at: string | null;
  retrieved_at: string;
  reference: string | null;
  claims: string[];
}

export interface DecisionCandidate {
  candidate_id: string;
  ticker: string;
  phase: Phase;
  action: Action;
  notification_kind: NotificationKind;
  decision_mode: DecisionMode;
  bucket: Bucket;
  depth: "full" | "compact";
  confidence: Confidence;
  confidence_reason: string;
  health_score: string | null;
  observed_price: string | null;
  observed_quote_as_of: string | null;
  proposed_amount: string | null;
  proposed_shares: string | null;
  entry_zone_low: string | null;
  entry_zone_high: string | null;
  stop: string | null;
  target: string | null;
  invalidation_price: string | null;
  valid_until: string | null;
  evidence: EvidenceBlock[];
  factors: Array<{
    kind:
      | "fundamentals"
      | "valuation"
      | "technicals"
      | "news"
      | "event"
      | "macro"
      | "sector"
      | "risk";
    stance: "bull" | "bear" | "neutral";
    text: string;
    evidence_ids: string[];
  }>;
  analyst: {
    completed: boolean;
    action: Action;
    confidence: Confidence;
    reason: string;
  };
  checker: {
    completed: boolean;
    verdict: "approve" | "downgrade" | "veto";
    reason_codes: string[];
    reason: string;
  };
  decisive_factor: string;
  invalidation: string;
  prior_suggestion_ids: string[];
}

export interface DecisionBundle {
  phase: Phase;
  market_date: string;
  title: string;
  candidates: DecisionCandidate[];
  comparisons?: PortfolioAlternativeRequest[];
  companion_proposal?: LongTermCompanionRequest;
}

export type AlternativeRelationship =
  | "like_for_like"
  | "tilt"
  | "diversifier"
  | "satellite"
  | "peer";
export type ProspectiveView =
  | "stronger"
  | "similar"
  | "weaker"
  | "insufficient";

export interface PortfolioAlternativeRequest {
  baseline_ticker: string;
  alternative_ticker: string;
  relationship: AlternativeRelationship;
  prospective_view: ProspectiveView;
  reason: string;
  evidence_ids: string[];
}

export type CompanionRole = "diversifier" | "tilt" | "satellite";

export interface LongTermCompanionRequest {
  baseline_ticker: string;
  companion_ticker: string;
  role: CompanionRole;
  thesis: string;
  risk_note: string;
  evidence_ids: string[];
}

export type ArtifactMutation =
  | {
    kind: "observation";
    ticker: string;
    obs_date: string;
    event_type: string;
    summary: string;
    price_reaction: string | null;
    confidence: Confidence;
    source: string;
  }
  | {
    kind: "snapshot";
    snap_date: string;
    ticker: string;
    close: string;
    day_move_pct: string | null;
    rsi14: string | null;
    sma50: string | null;
    sma200: string | null;
    macd_hist: string | null;
  }
  | {
    kind: "lesson";
    entry_date: string;
    category: string;
    content: string;
  }
  | {
    kind: "radar_upsert";
    ticker: string;
    added: string;
    last_seen: string;
    days_relevant: number;
    reason: string;
    bucket_guess: Bucket;
    promoted: boolean;
    promoted_on: string | null;
  }
  | { kind: "radar_delete"; ticker: string }
  | {
    kind: "paper_watch_create";
    ticker: string;
    entry_ref_price: string;
    target_price: string | null;
    hypothetical_amount: string | null;
    thesis: string;
    horizon: string;
  }
  | { kind: "paper_watch_close"; watch_id: number; ticker: string };

export interface ArtifactMutationBatch {
  mutations: ArtifactMutation[];
}

export interface VerifiedQuote {
  ticker: string;
  price: string;
  previous_close: string | null;
  as_of: string;
  market_state: string;
  source: "yahoo-chart";
}

export interface HoldingState {
  ticker: string;
  shares: string;
  avg_cost: string;
  bucket: Bucket | null;
  stop: string | null;
  target: string | null;
  high_water_price: string | null;
  hold_override_until: string | null;
  stop_alert_active: boolean;
  stop_near_alert_active: boolean;
  target_near_alert_active: boolean;
  target_alert_active: boolean;
}

export interface OwnerInvestmentPlan {
  id: string;
  ticker: string;
  bucket: "core";
  amount: string;
  cadence: "monthly";
  next_due_on: string;
  active: boolean;
  updated_at: string;
}

export interface PaperWatchState {
  id: number;
  ticker: string;
  created: string;
  entry_ref_price: string;
  target_price: string | null;
  hypothetical_amount: string | null;
  thesis: string;
  horizon: string;
  agent_view_at_open: Action | "no prior view";
  agent_score_at_open: number | null;
}

export interface ContextSuggestion {
  id: number;
  date: string;
  ticker: string;
  action: Action;
  bucket: Bucket;
  confidence: Confidence;
  score: number | null;
  stop: string | null;
  target: string | null;
  invalidation_price: string | null;
  valid_until: string | null;
  evidence_as_of: string | null;
}

export interface PolicyContext {
  holdings: HoldingState[];
  holding_quotes: Record<string, VerifiedQuote>;
  realized_pnl_today: string | null;
  portfolio_command_coverage_complete: boolean;
  consecutive_completed_losses: number;
  owner_plans: OwnerInvestmentPlan[];
}

export interface GatewayReadContext extends PolicyContext {
  recent_suggestions: ContextSuggestion[];
  observations: Array<{
    id: number;
    ticker: string;
    obs_date: string;
    event_type: string | null;
    summary: string;
    price_reaction: string | null;
    confidence: string | null;
    source: string | null;
  }>;
  lessons: Array<
    { id: number; entry_date: string; category: string; content: string }
  >;
  radar: Array<{
    ticker: string;
    added: string | null;
    last_seen: string | null;
    days_relevant: number | null;
    reason: string | null;
    bucket_guess: Bucket | null;
    promoted: boolean;
    promoted_on: string | null;
  }>;
  recent_grades: Array<{
    suggestion_id: number;
    horizon_days: number;
    coverage_status: string | null;
    excess_return_pct: string | null;
    direction_success: boolean | null;
  }>;
  dry_powder: Array<{
    month: string;
    growth_available: string;
    spec_available: string;
    rolled_months: number;
  }>;
  paper_watches: PaperWatchState[];
}

export interface PolicyConfig {
  version: 1 | 2 | 3;
  allocation_bps: Record<Bucket, number>;
  max_position_bps_of_bucket: Record<Bucket, number>;
  max_trade_risk_bps: Record<Bucket, number>;
  min_reward_risk_milli: number;
  max_actionable_quote_age_minutes: number;
  alert_near_bps: number;
  daily_loss_limit_bps: number;
  circuit_breaker_consecutive_losses: number;
  speculative_go_live_bucket_micros: string;
  monthly_investment_micros: string;
  broad_core_etfs: string[];
  self_tuning_enabled: false;
  market_calendar_year: number;
  nyse_holidays: string[];
  request_limits: {
    max_body_bytes: 262144;
    max_candidates: Record<Phase, number>;
    max_requests_per_run: 20;
    max_authenticated_requests_per_hour: 100;
  };
  alerts_v3?: {
    enabled: boolean;
    shadow: boolean;
    enabled_classes: AlertV3Class[];
    profile: AlertProfile;
    draft_ttl_hours: 24;
    drafts_per_hour: 5;
  };
}

const OPERATIONS: readonly Operation[] = [
  "start_run",
  "read_context",
  "record_artifacts",
  "grade_due_decisions",
  "evaluate_and_publish",
  "evaluate_alert_rules",
  "finish_run",
];
const PHASES: readonly Phase[] = [
  "pre-market",
  "intraday",
  "post-market",
  "on-demand",
];
const ACTIONS: readonly Action[] = [
  "buy",
  "add",
  "hold",
  "reduce",
  "sell",
  "watch",
  "avoid",
];
const NOTIFICATION_KINDS: readonly NotificationKind[] = [
  "brief",
  "new_idea",
  "entry_trigger",
  "stop_near",
  "stop_breach",
  "target_near",
  "target_hit",
  "thesis_break",
  "data_warning",
  "holiday",
];
const DECISION_MODES: readonly DecisionMode[] = ["discretionary", "owner_plan"];
const CONFIDENCES: readonly Confidence[] = ["low", "medium", "high"];
const BUCKETS: readonly Bucket[] = ["core", "growth", "speculative"];
const EVIDENCE_STATUSES: readonly EvidenceStatus[] = [
  "fresh",
  "stale",
  "fallback",
  "missing",
  "failed",
  "conflicting",
  "unsupported",
];
const EVIDENCE_KINDS = [
  "quote",
  "fundamentals",
  "technicals",
  "news",
  "event",
  "macro",
  "sector",
] as const;
const FACTOR_KINDS = [
  "fundamentals",
  "valuation",
  "technicals",
  "news",
  "event",
  "macro",
  "sector",
  "risk",
] as const;
const STANCES = ["bull", "bear", "neutral"] as const;
const CHECKER_VERDICTS = ["approve", "downgrade", "veto"] as const;
const ALTERNATIVE_RELATIONSHIPS = [
  "like_for_like",
  "tilt",
  "diversifier",
  "satellite",
  "peer",
] as const;
const COMPANION_ROLES = ["diversifier", "tilt", "satellite"] as const;
const PROSPECTIVE_VIEWS = [
  "stronger",
  "similar",
  "weaker",
  "insufficient",
] as const;
const CANDIDATE_LIMITS: Record<Phase, number> = {
  "pre-market": 80,
  intraday: 20,
  "post-market": 80,
  "on-demand": 10,
};
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TICKER_PATTERN = /^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$/;
const DECIMAL_PATTERN = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;
const SIGNED_DECIMAL_PATTERN = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;

function objectValue(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${path} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  value: Record<string, unknown>,
  allowed: readonly string[],
  path: string,
): void {
  const allowedSet = new Set(allowed);
  for (const key of Object.keys(value)) {
    if (!allowedSet.has(key)) {
      throw new Error(`${path} has unexpected key: ${key}`);
    }
  }
  for (const key of allowed) {
    if (!(key in value)) throw new Error(`${path} is missing key: ${key}`);
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

function stringValue(
  value: unknown,
  path: string,
  maxLength = 1000,
  allowEmpty = false,
): string {
  if (
    typeof value !== "string" || value.length > maxLength ||
    (!allowEmpty && value.trim().length === 0)
  ) {
    throw new Error(`${path} must be a bounded string`);
  }
  return value;
}

function nullableString(
  value: unknown,
  path: string,
  maxLength = 1000,
): string | null {
  return value === null ? null : stringValue(value, path, maxLength);
}

function booleanValue(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${path} must be a boolean`);
  return value;
}

function integerValue(
  value: unknown,
  path: string,
  min = 0,
  max = Number.MAX_SAFE_INTEGER,
): number {
  if (
    typeof value !== "number" || !Number.isSafeInteger(value) || value < min ||
    value > max
  ) {
    throw new Error(`${path} must be a bounded integer`);
  }
  return value;
}

function uuidValue(value: unknown, path: string): string {
  if (typeof value !== "string" || !UUID_PATTERN.test(value)) {
    throw new Error(`${path} must be a UUID`);
  }
  return value.toLowerCase();
}

function tickerValue(value: unknown, path: string): string {
  if (
    typeof value !== "string" || value.length > 15 ||
    !TICKER_PATTERN.test(value)
  ) {
    throw new Error(`${path} must be a canonical ticker`);
  }
  return value;
}

function dateValue(value: unknown, path: string): string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new Error(`${path} must be an ISO date`);
  }
  const parsed = new Date(`${value}T00:00:00.000Z`);
  if (
    Number.isNaN(parsed.valueOf()) ||
    parsed.toISOString().slice(0, 10) !== value
  ) {
    throw new Error(`${path} must be a valid ISO date`);
  }
  return value;
}

function timestampValue(value: unknown, path: string): string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T/.test(value)) {
    throw new Error(`${path} must be an ISO timestamp`);
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) {
    throw new Error(`${path} must be a valid ISO timestamp`);
  }
  return value;
}

function nullableTimestamp(value: unknown, path: string): string | null {
  return value === null ? null : timestampValue(value, path);
}

function decimalValue(
  value: unknown,
  path: string,
  fractionalDigits: number,
  options: { nullable?: boolean; signed?: boolean; positive?: boolean } = {},
): string | null {
  if (value === null && options.nullable) return null;
  const pattern = options.signed ? SIGNED_DECIMAL_PATTERN : DECIMAL_PATTERN;
  if (typeof value !== "string" || !pattern.test(value)) {
    throw new Error(`${path} must be a canonical decimal string`);
  }
  const unsigned = value.startsWith("-") ? value.slice(1) : value;
  const [whole, fraction = ""] = unsigned.split(".");
  if (fraction.length > fractionalDigits) {
    throw new Error(`${path} exceeds ${fractionalDigits} fractional digits`);
  }
  if (BigInt(whole) > 1_000_000_000_000_000n) {
    throw new Error(`${path} exceeds maximum magnitude`);
  }
  if (options.positive && /^0(?:\.0+)?$/.test(unsigned)) {
    throw new Error(`${path} must be positive`);
  }
  return value;
}

function arrayValue(
  value: unknown,
  path: string,
  maxLength: number,
): unknown[] {
  if (!Array.isArray(value) || value.length > maxLength) {
    throw new Error(`${path} must be an array with at most ${maxLength} items`);
  }
  return value;
}

export function parseGatewayEnvelope(value: unknown): GatewayEnvelope {
  const row = objectValue(value, "envelope");
  exactKeys(
    row,
    [
      "schema_version",
      "operation",
      "request_id",
      "run_id",
      "dry_run",
      "payload",
    ],
    "envelope",
  );
  if (row.schema_version !== 1) throw new Error("schema_version must be 1");
  return {
    schema_version: 1,
    operation: enumValue(row.operation, OPERATIONS, "operation"),
    request_id: uuidValue(row.request_id, "request_id"),
    run_id: row.run_id === null ? null : uuidValue(row.run_id, "run_id"),
    dry_run: booleanValue(row.dry_run, "dry_run"),
    payload: row.payload,
  };
}

function parseEvidence(value: unknown, path: string): EvidenceBlock {
  const row = objectValue(value, path);
  exactKeys(
    row,
    [
      "id",
      "kind",
      "source",
      "status",
      "observed_at",
      "retrieved_at",
      "reference",
      "claims",
    ],
    path,
  );
  return {
    id: stringValue(row.id, `${path}.id`, 100),
    kind: enumValue(row.kind, EVIDENCE_KINDS, `${path}.kind`),
    source: stringValue(row.source, `${path}.source`, 200),
    status: enumValue(row.status, EVIDENCE_STATUSES, `${path}.status`),
    observed_at: nullableTimestamp(row.observed_at, `${path}.observed_at`),
    retrieved_at: timestampValue(row.retrieved_at, `${path}.retrieved_at`),
    reference: nullableString(row.reference, `${path}.reference`),
    claims: arrayValue(row.claims, `${path}.claims`, 10).map((claim, index) =>
      stringValue(claim, `${path}.claims[${index}]`, 500)
    ),
  };
}

function parseCandidate(
  value: unknown,
  expectedPhase: Phase,
  path: string,
): DecisionCandidate {
  const row = objectValue(value, path);
  const keys = [
    "candidate_id",
    "ticker",
    "phase",
    "action",
    "notification_kind",
    "decision_mode",
    "bucket",
    "depth",
    "confidence",
    "confidence_reason",
    "health_score",
    "observed_price",
    "observed_quote_as_of",
    "proposed_amount",
    "proposed_shares",
    "entry_zone_low",
    "entry_zone_high",
    "stop",
    "target",
    "invalidation_price",
    "valid_until",
    "evidence",
    "factors",
    "analyst",
    "checker",
    "decisive_factor",
    "invalidation",
    "prior_suggestion_ids",
  ] as const;
  exactKeys(row, keys, path);

  const phase = enumValue(row.phase, PHASES, `${path}.phase`);
  if (phase !== expectedPhase) {
    throw new Error(`${path}.phase does not match bundle phase`);
  }

  const evidence = arrayValue(row.evidence, `${path}.evidence`, 100).map((
    item,
    index,
  ) => parseEvidence(item, `${path}.evidence[${index}]`));
  const evidenceIds = new Set<string>();
  for (const item of evidence) {
    if (evidenceIds.has(item.id)) {
      throw new Error(`${path} has duplicate evidence id`);
    }
    evidenceIds.add(item.id);
  }

  const factors = arrayValue(row.factors, `${path}.factors`, 20).map(
    (item, index) => {
      const factorPath = `${path}.factors[${index}]`;
      const factor = objectValue(item, factorPath);
      exactKeys(factor, ["kind", "stance", "text", "evidence_ids"], factorPath);
      const factorEvidenceIds = arrayValue(
        factor.evidence_ids,
        `${factorPath}.evidence_ids`,
        20,
      ).map((id, evidenceIndex) =>
        stringValue(id, `${factorPath}.evidence_ids[${evidenceIndex}]`, 100)
      );
      for (const id of factorEvidenceIds) {
        if (!evidenceIds.has(id)) {
          throw new Error(`${factorPath} references unknown evidence id`);
        }
      }
      return {
        kind: enumValue(factor.kind, FACTOR_KINDS, `${factorPath}.kind`),
        stance: enumValue(factor.stance, STANCES, `${factorPath}.stance`),
        text: stringValue(factor.text, `${factorPath}.text`, 500),
        evidence_ids: factorEvidenceIds,
      };
    },
  );

  const analystRow = objectValue(row.analyst, `${path}.analyst`);
  exactKeys(
    analystRow,
    ["completed", "action", "confidence", "reason"],
    `${path}.analyst`,
  );
  const checkerRow = objectValue(row.checker, `${path}.checker`);
  exactKeys(
    checkerRow,
    ["completed", "verdict", "reason_codes", "reason"],
    `${path}.checker`,
  );

  const validUntil = row.valid_until === null
    ? null
    : dateValue(row.valid_until, `${path}.valid_until`);
  return {
    candidate_id: uuidValue(row.candidate_id, `${path}.candidate_id`),
    ticker: tickerValue(row.ticker, `${path}.ticker`),
    phase,
    action: enumValue(row.action, ACTIONS, `${path}.action`),
    notification_kind: enumValue(
      row.notification_kind,
      NOTIFICATION_KINDS,
      `${path}.notification_kind`,
    ),
    decision_mode: enumValue(
      row.decision_mode,
      DECISION_MODES,
      `${path}.decision_mode`,
    ),
    bucket: enumValue(row.bucket, BUCKETS, `${path}.bucket`),
    depth: enumValue(row.depth, ["full", "compact"] as const, `${path}.depth`),
    confidence: enumValue(row.confidence, CONFIDENCES, `${path}.confidence`),
    confidence_reason: stringValue(
      row.confidence_reason,
      `${path}.confidence_reason`,
    ),
    health_score: decimalValue(row.health_score, `${path}.health_score`, 6, {
      nullable: true,
    }),
    observed_price: decimalValue(
      row.observed_price,
      `${path}.observed_price`,
      6,
      {
        nullable: true,
      },
    ),
    observed_quote_as_of: nullableTimestamp(
      row.observed_quote_as_of,
      `${path}.observed_quote_as_of`,
    ),
    proposed_amount: decimalValue(
      row.proposed_amount,
      `${path}.proposed_amount`,
      6,
      {
        nullable: true,
      },
    ),
    proposed_shares: decimalValue(
      row.proposed_shares,
      `${path}.proposed_shares`,
      8,
      {
        nullable: true,
      },
    ),
    entry_zone_low: decimalValue(
      row.entry_zone_low,
      `${path}.entry_zone_low`,
      6,
      {
        nullable: true,
      },
    ),
    entry_zone_high: decimalValue(
      row.entry_zone_high,
      `${path}.entry_zone_high`,
      6,
      {
        nullable: true,
      },
    ),
    stop: decimalValue(row.stop, `${path}.stop`, 6, { nullable: true }),
    target: decimalValue(row.target, `${path}.target`, 6, { nullable: true }),
    invalidation_price: decimalValue(
      row.invalidation_price,
      `${path}.invalidation_price`,
      6,
      { nullable: true },
    ),
    valid_until: validUntil,
    evidence,
    factors,
    analyst: {
      completed: booleanValue(
        analystRow.completed,
        `${path}.analyst.completed`,
      ),
      action: enumValue(analystRow.action, ACTIONS, `${path}.analyst.action`),
      confidence: enumValue(
        analystRow.confidence,
        CONFIDENCES,
        `${path}.analyst.confidence`,
      ),
      reason: stringValue(analystRow.reason, `${path}.analyst.reason`),
    },
    checker: {
      completed: booleanValue(
        checkerRow.completed,
        `${path}.checker.completed`,
      ),
      verdict: enumValue(
        checkerRow.verdict,
        CHECKER_VERDICTS,
        `${path}.checker.verdict`,
      ),
      reason_codes: arrayValue(
        checkerRow.reason_codes,
        `${path}.checker.reason_codes`,
        20,
      ).map((code, index) =>
        stringValue(code, `${path}.checker.reason_codes[${index}]`, 100)
      ),
      reason: stringValue(checkerRow.reason, `${path}.checker.reason`),
    },
    decisive_factor: stringValue(
      row.decisive_factor,
      `${path}.decisive_factor`,
    ),
    invalidation: stringValue(row.invalidation, `${path}.invalidation`),
    prior_suggestion_ids: arrayValue(
      row.prior_suggestion_ids,
      `${path}.prior_suggestion_ids`,
      20,
    ).map((id, index) =>
      stringValue(id, `${path}.prior_suggestion_ids[${index}]`, 100)
    ),
  };
}

export function parseDecisionBundle(
  value: unknown,
  phase: Phase,
): DecisionBundle {
  const row = objectValue(value, "bundle");
  const hasComparisons = Object.hasOwn(row, "comparisons");
  const hasCompanionProposal = Object.hasOwn(row, "companion_proposal");
  const bundleKeys = ["phase", "market_date", "title", "candidates"];
  if (hasComparisons) bundleKeys.push("comparisons");
  if (hasCompanionProposal) bundleKeys.push("companion_proposal");
  exactKeys(
    row,
    bundleKeys,
    "bundle",
  );
  const parsedPhase = enumValue(row.phase, PHASES, "bundle.phase");
  if (parsedPhase !== phase) {
    throw new Error("bundle phase does not match requested phase");
  }
  const marketDate = dateValue(row.market_date, "bundle.market_date");
  if (!Array.isArray(row.candidates)) {
    throw new Error("bundle.candidates must be an array");
  }
  const candidateRows = row.candidates;
  if (candidateRows.length > CANDIDATE_LIMITS[phase]) {
    throw new Error(`bundle exceeds ${phase} candidate limit`);
  }
  const candidates = candidateRows.map((candidate, index) =>
    parseCandidate(candidate, phase, `bundle.candidates[${index}]`)
  );
  const candidateIds = new Set<string>();
  const tickers = new Set<string>();
  let evidenceCount = 0;
  for (const candidate of candidates) {
    if (candidateIds.has(candidate.candidate_id)) {
      throw new Error("bundle has duplicate candidate id");
    }
    if (tickers.has(candidate.ticker)) {
      throw new Error("bundle has duplicate ticker");
    }
    candidateIds.add(candidate.candidate_id);
    tickers.add(candidate.ticker);
    evidenceCount += candidate.evidence.length;
    if (candidate.valid_until !== null && candidate.valid_until < marketDate) {
      throw new Error("candidate valid_until precedes market_date");
    }
  }
  if (evidenceCount > 100) throw new Error("bundle exceeds evidence limit");
  if (hasComparisons && phase !== "pre-market" && phase !== "on-demand") {
    throw new Error(
      "portfolio comparisons are limited to pre-market and on-demand reviews",
    );
  }
  if (
    hasCompanionProposal &&
    (phase !== "pre-market" && phase !== "on-demand")
  ) {
    throw new Error(
      "long-term companion is limited to pre-market and on-demand reviews",
    );
  }
  const comparisons = hasComparisons
    ? arrayValue(row.comparisons, "bundle.comparisons", 6).map(
      (item, index) => {
        const path = `bundle.comparisons[${index}]`;
        const comparison = objectValue(item, path);
        exactKeys(
          comparison,
          [
            "baseline_ticker",
            "alternative_ticker",
            "relationship",
            "prospective_view",
            "reason",
            "evidence_ids",
          ],
          path,
        );
        const baselineTicker = tickerValue(
          comparison.baseline_ticker,
          `${path}.baseline_ticker`,
        );
        const alternativeTicker = tickerValue(
          comparison.alternative_ticker,
          `${path}.alternative_ticker`,
        );
        if (
          baselineTicker === alternativeTicker ||
          !tickers.has(baselineTicker) ||
          !tickers.has(alternativeTicker)
        ) {
          throw new Error(`${path} has an invalid comparison ticker`);
        }
        const alternative = candidates.find((candidate) =>
          candidate.ticker === alternativeTicker
        )!;
        const evidenceIds = arrayValue(
          comparison.evidence_ids,
          `${path}.evidence_ids`,
          10,
        ).map((id, evidenceIndex) =>
          stringValue(id, `${path}.evidence_ids[${evidenceIndex}]`, 100)
        );
        if (
          evidenceIds.length === 0 ||
          evidenceIds.some((id) =>
            !alternative.evidence.some((evidence) => evidence.id === id) ||
            !alternative.factors.some((factor) =>
              factor.evidence_ids.includes(id)
            )
          )
        ) {
          throw new Error(`${path} references unknown comparison evidence`);
        }
        return {
          baseline_ticker: baselineTicker,
          alternative_ticker: alternativeTicker,
          relationship: enumValue(
            comparison.relationship,
            ALTERNATIVE_RELATIONSHIPS,
            `${path}.relationship`,
          ),
          prospective_view: enumValue(
            comparison.prospective_view,
            PROSPECTIVE_VIEWS,
            `${path}.prospective_view`,
          ),
          reason: stringValue(comparison.reason, `${path}.reason`, 300),
          evidence_ids: evidenceIds,
        };
      },
    )
    : undefined;
  if (comparisons) {
    const pairs = new Set<string>();
    for (const comparison of comparisons) {
      const pair =
        `${comparison.baseline_ticker}:${comparison.alternative_ticker}`;
      if (pairs.has(pair)) {
        throw new Error("bundle has duplicate portfolio comparison");
      }
      pairs.add(pair);
    }
  }
  const companionProposal = hasCompanionProposal
    ? (() => {
      if (!comparisons) {
        throw new Error(
          "bundle.companion_proposal requires a matching portfolio comparison",
        );
      }
      const path = "bundle.companion_proposal";
      const proposal = objectValue(row.companion_proposal, path);
      exactKeys(
        proposal,
        [
          "baseline_ticker",
          "companion_ticker",
          "role",
          "thesis",
          "risk_note",
          "evidence_ids",
        ],
        path,
      );
      const baselineTicker = tickerValue(
        proposal.baseline_ticker,
        `${path}.baseline_ticker`,
      );
      const companionTicker = tickerValue(
        proposal.companion_ticker,
        `${path}.companion_ticker`,
      );
      if (
        baselineTicker === companionTicker || !tickers.has(baselineTicker) ||
        !tickers.has(companionTicker)
      ) {
        throw new Error(`${path} has an invalid companion ticker`);
      }
      const role = enumValue(
        proposal.role,
        COMPANION_ROLES,
        `${path}.companion role`,
      );
      const pair = comparisons.find((comparison) =>
        comparison.baseline_ticker === baselineTicker &&
        comparison.alternative_ticker === companionTicker &&
        comparison.relationship === role
      );
      if (!pair) {
        throw new Error(`${path} requires a matching portfolio comparison`);
      }
      const candidate = candidates.find((item) =>
        item.ticker === companionTicker
      )!;
      const evidenceIds = arrayValue(
        proposal.evidence_ids,
        `${path}.evidence_ids`,
        10,
      ).map((id, index) =>
        stringValue(id, `${path}.evidence_ids[${index}]`, 100)
      );
      if (
        evidenceIds.length === 0 ||
        evidenceIds.some((id) =>
          !candidate.evidence.some((evidence) => evidence.id === id) ||
          !candidate.factors.some((factor) => factor.evidence_ids.includes(id))
        )
      ) {
        throw new Error(`${path} references unknown companion evidence`);
      }
      return {
        baseline_ticker: baselineTicker,
        companion_ticker: companionTicker,
        role,
        thesis: stringValue(proposal.thesis, `${path}.thesis`, 500),
        risk_note: stringValue(proposal.risk_note, `${path}.risk_note`, 500),
        evidence_ids: evidenceIds,
      };
    })()
    : undefined;
  return {
    phase,
    market_date: marketDate,
    title: stringValue(row.title, "bundle.title"),
    candidates,
    ...(comparisons ? { comparisons } : {}),
    ...(companionProposal ? { companion_proposal: companionProposal } : {}),
  };
}

function parseArtifact(value: unknown, path: string): ArtifactMutation {
  const row = objectValue(value, path);
  const kind = stringValue(row.kind, `${path}.kind`, 50);
  switch (kind) {
    case "observation":
      exactKeys(
        row,
        [
          "kind",
          "ticker",
          "obs_date",
          "event_type",
          "summary",
          "price_reaction",
          "confidence",
          "source",
        ],
        path,
      );
      return {
        kind,
        ticker: tickerValue(row.ticker, `${path}.ticker`),
        obs_date: dateValue(row.obs_date, `${path}.obs_date`),
        event_type: stringValue(row.event_type, `${path}.event_type`, 100),
        summary: stringValue(row.summary, `${path}.summary`),
        price_reaction: nullableString(
          row.price_reaction,
          `${path}.price_reaction`,
          100,
        ),
        confidence: enumValue(
          row.confidence,
          CONFIDENCES,
          `${path}.confidence`,
        ),
        source: stringValue(row.source, `${path}.source`, 200),
      };
    case "snapshot":
      exactKeys(
        row,
        [
          "kind",
          "snap_date",
          "ticker",
          "close",
          "day_move_pct",
          "rsi14",
          "sma50",
          "sma200",
          "macd_hist",
        ],
        path,
      );
      return {
        kind,
        snap_date: dateValue(row.snap_date, `${path}.snap_date`),
        ticker: tickerValue(row.ticker, `${path}.ticker`),
        close: decimalValue(row.close, `${path}.close`, 6, {
          positive: true,
        }) as string,
        day_move_pct: decimalValue(
          row.day_move_pct,
          `${path}.day_move_pct`,
          6,
          {
            nullable: true,
            signed: true,
          },
        ),
        rsi14: decimalValue(row.rsi14, `${path}.rsi14`, 6, { nullable: true }),
        sma50: decimalValue(row.sma50, `${path}.sma50`, 6, { nullable: true }),
        sma200: decimalValue(row.sma200, `${path}.sma200`, 6, {
          nullable: true,
        }),
        macd_hist: decimalValue(row.macd_hist, `${path}.macd_hist`, 6, {
          nullable: true,
          signed: true,
        }),
      };
    case "lesson":
      exactKeys(row, ["kind", "entry_date", "category", "content"], path);
      return {
        kind,
        entry_date: dateValue(row.entry_date, `${path}.entry_date`),
        category: stringValue(row.category, `${path}.category`, 100),
        content: stringValue(row.content, `${path}.content`),
      };
    case "radar_upsert":
      exactKeys(
        row,
        [
          "kind",
          "ticker",
          "added",
          "last_seen",
          "days_relevant",
          "reason",
          "bucket_guess",
          "promoted",
          "promoted_on",
        ],
        path,
      );
      return {
        kind,
        ticker: tickerValue(row.ticker, `${path}.ticker`),
        added: dateValue(row.added, `${path}.added`),
        last_seen: dateValue(row.last_seen, `${path}.last_seen`),
        days_relevant: integerValue(
          row.days_relevant,
          `${path}.days_relevant`,
          0,
          3650,
        ),
        reason: stringValue(row.reason, `${path}.reason`),
        bucket_guess: enumValue(
          row.bucket_guess,
          BUCKETS,
          `${path}.bucket_guess`,
        ),
        promoted: booleanValue(row.promoted, `${path}.promoted`),
        promoted_on: row.promoted_on === null
          ? null
          : dateValue(row.promoted_on, `${path}.promoted_on`),
      };
    case "radar_delete":
      exactKeys(row, ["kind", "ticker"], path);
      return { kind, ticker: tickerValue(row.ticker, `${path}.ticker`) };
    case "paper_watch_create":
      exactKeys(
        row,
        [
          "kind",
          "ticker",
          "entry_ref_price",
          "target_price",
          "hypothetical_amount",
          "thesis",
          "horizon",
        ],
        path,
      );
      return {
        kind,
        ticker: tickerValue(row.ticker, `${path}.ticker`),
        entry_ref_price: decimalValue(
          row.entry_ref_price,
          `${path}.entry_ref_price`,
          6,
          { positive: true },
        ) as string,
        target_price: decimalValue(
          row.target_price,
          `${path}.target_price`,
          6,
          {
            nullable: true,
            positive: true,
          },
        ),
        hypothetical_amount: decimalValue(
          row.hypothetical_amount,
          `${path}.hypothetical_amount`,
          6,
          { nullable: true, positive: true },
        ),
        thesis: stringValue(row.thesis, `${path}.thesis`),
        horizon: stringValue(row.horizon, `${path}.horizon`, 100),
      };
    case "paper_watch_close":
      exactKeys(row, ["kind", "watch_id", "ticker"], path);
      return {
        kind,
        watch_id: integerValue(row.watch_id, `${path}.watch_id`, 1),
        ticker: tickerValue(row.ticker, `${path}.ticker`),
      };
    default:
      throw new Error(`${path}.kind is invalid`);
  }
}

export function parseArtifactMutationBatch(
  value: unknown,
): ArtifactMutationBatch {
  const row = objectValue(value, "artifact batch");
  exactKeys(row, ["mutations"], "artifact batch");
  const mutations = arrayValue(row.mutations, "artifact batch.mutations", 100);
  if (mutations.length === 0) {
    throw new Error("artifact batch must not be empty");
  }
  return {
    mutations: mutations.map((mutation, index) =>
      parseArtifact(mutation, `artifact batch.mutations[${index}]`)
    ),
  };
}
