import {
  canonicalJson,
  type DiscoveryReferencePayload,
  type DiscoveryStageCheckpointPayload,
  parseCheckpointIntelligencePayload,
  parseDiscoveryContextRequest,
  parseDiscoveryReferencePayload,
  parseDiscoveryStageCheckpointPayload,
  parseRecordIntelligencePayload,
  parseReferenceBeginPayload,
  parseReferenceChunkPayload,
  parseReferenceFinalizePayload,
  parseReferencePinPayload,
  parseReferenceReadPayload,
  parseStartIntelligencePayload,
  type RecordIntelligencePayload,
  type ReferenceBeginPayload,
  type ReferenceChunkPayload,
  type ReferenceFinalizePayload,
  type ReferencePinPayload,
  type ReferenceReadPayload,
  sha256Hex,
  type StartIntelligencePayload,
} from "./intelligence.ts";
import {
  parseRecordReportPayload,
  type RecordReportPayload,
} from "./reports.ts";
import type { RecordLearningPayload } from "./outcomes.ts";

export type Operation =
  | "start_run"
  | "read_context"
  | "record_artifacts"
  | "grade_due_decisions"
  | "evaluate_and_publish"
  | "evaluate_alert_rules"
  | "finish_run"
  | "start_intelligence_run"
  | "checkpoint_intelligence_collection"
  | "record_intelligence"
  | "read_intelligence_completion"
  | "read_intelligence_context"
  | "collect_intelligence_quote"
  | "seal_enrichment_selection"
  | "record_report"
  | "record_learning"
  | "record_discovery_reference"
  | "checkpoint_discovery_stage"
  | "read_discovery_context"
  | "begin_discovery_reference"
  | "record_discovery_reference_chunk"
  | "finalize_discovery_reference"
  | "pin_discovery_reference"
  | "read_discovery_reference";
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
export type ExposureKind =
  | "filing"
  | "contract"
  | "backlog"
  | "revenue"
  | "capacity"
  | "official_fund";
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
  payload:
    | unknown
    | StartIntelligencePayload
    | RecordIntelligencePayload
    | RecordReportPayload
    | RecordLearningPayload;
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
  exposure_kind?: ExposureKind | null;
}

export interface EvidencePacketV1 {
  candidates: Array<{ candidate_key: string; evidence_ids: string[] }>;
  evidence: Array<{ item_id: string; normalized_text: string }>;
  coverage: Record<string, unknown>;
  limitations: string[];
  policy_version: number;
  facts?: TrustedEvidenceFact[];
}

export interface CandidateLineageV2 {
  run_id: string;
  observed_at: string;
  policy_version: number;
  reference_manifest_id: string | null;
  reference_revision: number | null;
  reference_expires_at: string | null;
  security_revision_id: string | null;
  quote_receipt_id: string | null;
  quote_as_of: string | null;
  quote_expires_at: string | null;
  evidence_receipt_ids: Record<string, string>;
  portfolio_revision: string | null;
  cash_revision: string | null;
}

export interface SuitabilityEvaluationV2 {
  component_scores: Record<string, string>;
  evaluation_hash: string;
  lineage: CandidateLineageV2 | null;
  missing_reasons: string[];
  state: "unknown" | "eligible" | "vetoed";
  veto_reasons: string[];
}

export interface ResearchCandidateV2 {
  adverse_paths: string[];
  candidate_hash: string;
  candidate_key: string;
  entity_id: string | null;
  event_ids: string[];
  evidence: Array<{
    claim_type: string;
    item_id: string;
    relationship_eligible: boolean;
    role: "supporting" | "opposing";
  }>;
  exposure_fact_ids: string[];
  limitations: string[];
  priority_components: Record<string, string>;
  priority_score: string;
  research_state:
    | "unresolved"
    | "resolved"
    | "exposure_supported"
    | "analysis_ready";
  roles: string[];
  security_id: string | null;
  suitability: SuitabilityEvaluationV2;
  theme_ids: string[];
  ticker: string | null;
}

export interface EvidencePacketV2 {
  action_candidates: Array<{
    candidate_hash: string;
    candidate_key: string;
    suitability_hash: string;
  }>;
  contract_version: 2;
  coverage: Record<string, unknown>;
  evidence: Array<{
    authority: string;
    canonical_url: string;
    claim_type: string;
    content_hash: string;
    effective_at: string | null;
    item_id: string;
    normalized_text: string;
    published_at: string | null;
    reporting_at: string | null;
    retrieved_at: string;
    source_identity: {
      provider: string;
      receipt_id: string;
      upstream_item_id: string;
    };
  }>;
  execution_allowed: false;
  limitations: string[];
  observed_at: string;
  omissions: Array<{
    candidate_key: string;
    item_id: string | null;
    kind: string;
    reason: string;
    stage: string;
  }>;
  policy_version: number;
  research_candidates: ResearchCandidateV2[];
  run_id: string;
}

export type EvidencePacket = EvidencePacketV1 | EvidencePacketV2;

export function isEvidencePacketV2(
  packet: EvidencePacket,
): packet is EvidencePacketV2 {
  return "contract_version" in packet && packet.contract_version === 2;
}

/** Only populated by the persisted packet read (or an explicit dry-run fixture). */
export interface TrustedEvidenceFact {
  candidate_key: string;
  evidence_id: string;
  category: EvidenceBlock["kind"] | "unknown";
  source: string;
  source_status: "succeeded" | "cache_hit" | "failed";
  authority: "official" | "market_data" | "reported" | "unverified";
  published_at: string | null;
  retrieved_at: string;
  expires_at: string | null;
  reference: string | null;
  normalized_text: string;
  exposure_kind: ExposureKind | null;
  relationship_eligible: boolean;
  claim_key: string | null;
  claim_polarity: "affirmed" | "denied" | null;
}

export function parseTrustedEvidenceFacts(
  value: unknown,
): TrustedEvidenceFact[] {
  const seen = new Set<string>();
  return arrayValue(value, "persisted evidence facts", 96).map(
    (value, index) => {
      const path = `persisted evidence facts[${index}]`;
      const row = objectValue(value, path);
      exactKeys(row, [
        "candidate_key",
        "evidence_id",
        "category",
        "source",
        "source_status",
        "authority",
        "published_at",
        "retrieved_at",
        "expires_at",
        "reference",
        "normalized_text",
        "exposure_kind",
        "relationship_eligible",
        "claim_key",
        "claim_polarity",
      ], path);
      if (typeof row.relationship_eligible !== "boolean") {
        throw new Error(`${path} invalid eligibility`);
      }
      const key = `${row.candidate_key}:${row.evidence_id}`;
      if (seen.has(key)) throw new Error(`${path} duplicate evidence fact`);
      seen.add(key);
      return {
        candidate_key: tickerValue(row.candidate_key, `${path}.candidate_key`),
        evidence_id: stringValue(row.evidence_id, `${path}.evidence_id`, 100),
        category: enumValue(
          row.category,
          [...EVIDENCE_KINDS, "unknown"] as const,
          `${path}.category`,
        ),
        source: stringValue(row.source, `${path}.source`, 200),
        source_status: enumValue(
          row.source_status,
          ["succeeded", "cache_hit", "failed"] as const,
          `${path}.source_status`,
        ),
        authority: enumValue(
          row.authority,
          ["official", "market_data", "reported", "unverified"] as const,
          `${path}.authority`,
        ),
        published_at: nullableTimestamp(
          row.published_at,
          `${path}.published_at`,
        ),
        retrieved_at: timestampValue(row.retrieved_at, `${path}.retrieved_at`),
        expires_at: nullableTimestamp(row.expires_at, `${path}.expires_at`),
        reference: nullableString(row.reference, `${path}.reference`),
        normalized_text: stringValue(
          row.normalized_text,
          `${path}.normalized_text`,
          2000,
          true,
        ),
        exposure_kind: row.exposure_kind === null ? null : enumValue(
          row.exposure_kind,
          EXPOSURE_KINDS,
          `${path}.exposure_kind`,
        ),
        relationship_eligible: row.relationship_eligible,
        claim_key: nullableString(row.claim_key, `${path}.claim_key`, 200),
        claim_polarity: row.claim_polarity === null ? null : enumValue(
          row.claim_polarity,
          ["affirmed", "denied"] as const,
          `${path}.claim_polarity`,
        ),
      };
    },
  );
}

export interface IntelligencePacketRef {
  id: string;
  content_hash: string;
  coverage: "complete_for_plan" | "partial" | "fixture_dry_run";
  packet?: EvidencePacket;
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
  relationship_type?: "direct" | "second_order" | null;
  reservation_group?: string | null;
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
    id?: string | null;
    packet_id?: string | null;
    completed: boolean;
    action: Action;
    confidence: Confidence;
    reason: string;
  };
  checker: {
    id?: string | null;
    analyst_id?: string | null;
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
  intelligence_packet?: IntelligencePacketRef;
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
  actionable_price_status: "available" | "unavailable";
  actionable_price_reasons: Array<
    "halted" | "halt_status_unknown" | "spread_unknown" | "liquidity_unknown"
  >;
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
  reconciled_cash_snapshot?: {
    snapshot_id: string;
    as_of: string;
    fresh_through: string;
    ledger_watermark: string;
    spendable_cash: Record<Bucket, string>;
  };
}

export interface GatewayReadContext extends PolicyContext {
  intelligence_collection_context?: {
    holding_market_values: Record<string, string>;
    liquidity_by_ticker: Record<string, string>;
    overlap_by_ticker: Record<string, string>;
    current_quotes?: Record<string, {
      price: string;
      as_of: string;
      expires_at: string;
      receipt_id: string;
    }>;
    quote_receipt_ids?: string[];
    portfolio_revision?: string;
    portfolio_valuation_complete?: boolean;
    cash_revision?: string;
    source_cursors?: DiscoverySourceCursor[];
    last_completed_scans?: DiscoveryCompletedScan[];
  };
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

export interface DiscoverySourceCursor {
  task_key: string;
  provider: string;
  capability_id: string;
  completed_through: string | null;
  active_window_start: string | null;
  active_window_end: string | null;
  backlog_token: string | null;
  page: number;
  accepted_item_ids: string[];
  next_retry_phase: Phase | null;
  continuation_token_history: string[];
  source_run_id: string;
  source_task_id: string;
  source_updated_at: string;
}

export interface DiscoveryCompletedScan {
  capability_id: string;
  theme_id: string;
  completed_through: string;
  source_run_id: string;
  source_task_id: string;
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
  "start_intelligence_run",
  "checkpoint_intelligence_collection",
  "record_intelligence",
  "read_intelligence_completion",
  "read_intelligence_context",
  "collect_intelligence_quote",
  "seal_enrichment_selection",
  "record_report",
  "record_learning",
  "record_discovery_reference",
  "checkpoint_discovery_stage",
  "read_discovery_context",
  "begin_discovery_reference",
  "record_discovery_reference_chunk",
  "finalize_discovery_reference",
  "pin_discovery_reference",
  "read_discovery_reference",
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
const EXPOSURE_KINDS = [
  "filing",
  "contract",
  "backlog",
  "revenue",
  "capacity",
  "official_fund",
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

export function parseRecordLearningPayload(
  value: unknown,
): RecordLearningPayload {
  const row = objectValue(value, "learning");
  exactKeys(row, [
    "id",
    "policy_version",
    "observation_type",
    "horizon_days",
    "sample_size",
    "benchmark",
    "observation",
    "content_hash",
  ], "learning");
  const observation = objectValue(row.observation, "learning.observation");
  exactKeys(observation, [
    "status",
    "evidence_ids",
    "limitations",
    "metrics",
    "proposed_change",
  ], "learning.observation");
  const status = enumValue(
    observation.status,
    ["observation", "owner_review"] as const,
    "learning.observation.status",
  );
  const proposed = observation.proposed_change === null ? null : objectValue(
    observation.proposed_change,
    "learning.observation.proposed_change",
  );
  if (proposed !== null) {
    exactKeys(proposed, [
      "area",
      "recommendation",
      "false_positive_rate",
    ], "learning.observation.proposed_change");
  }
  if (
    (status === "owner_review") !== (proposed !== null) ||
    JSON.stringify(observation).length > 32_768 ||
    typeof row.content_hash !== "string" ||
    !/^[0-9a-f]{64}$/.test(row.content_hash)
  ) throw new Error("learning observation review status is invalid");
  const horizon = integerValue(
    row.horizon_days,
    "learning.horizon_days",
    0,
    63,
  );
  if (![0, 5, 21, 63].includes(horizon)) {
    throw new Error("learning horizon is invalid");
  }
  return {
    id: uuidValue(row.id, "learning.id"),
    policy_version: integerValue(
      row.policy_version,
      "learning.policy_version",
      1,
      1_000_000,
    ),
    observation_type: enumValue(
      row.observation_type,
      [
        "outcome",
        "missed-event",
        "source-failure",
        "noise",
      ] as const,
      "learning.observation_type",
    ),
    horizon_days: horizon as 0 | 5 | 21 | 63,
    sample_size: integerValue(
      row.sample_size,
      "learning.sample_size",
      1,
      1_000_000,
    ),
    benchmark: nullableString(row.benchmark, "learning.benchmark", 100),
    observation: {
      status,
      evidence_ids: arrayValue(
        observation.evidence_ids,
        "learning.observation.evidence_ids",
        96,
      ).map((id, index) =>
        uuidValue(id, `learning.observation.evidence_ids[${index}]`)
      ),
      limitations: arrayValue(
        observation.limitations,
        "learning.observation.limitations",
        20,
      ).map((item, index) =>
        stringValue(item, `learning.observation.limitations[${index}]`, 500)
      ),
      metrics: objectValue(observation.metrics, "learning.observation.metrics"),
      proposed_change: proposed === null ? null : {
        area: stringValue(
          proposed.area,
          "learning.observation.proposed_change.area",
          100,
        ),
        recommendation: stringValue(
          proposed.recommendation,
          "learning.observation.proposed_change.recommendation",
          200,
        ),
        false_positive_rate: stringValue(
          proposed.false_positive_rate,
          "learning.observation.proposed_change.false_positive_rate",
          20,
        ),
      },
    },
    content_hash: stringValue(row.content_hash, "learning.content_hash", 64),
  };
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
  const operation = enumValue(row.operation, OPERATIONS, "operation");
  let payload = row.payload;
  if (operation === "start_intelligence_run") {
    if (row.run_id !== null) {
      throw new Error("run_id must be null for start_intelligence_run");
    }
    payload = parseStartIntelligencePayload(row.payload);
  } else if (operation === "checkpoint_intelligence_collection") {
    if (row.run_id === null) {
      throw new Error(
        "run_id is required for checkpoint_intelligence_collection",
      );
    }
    payload = parseCheckpointIntelligencePayload(row.payload);
  } else if (operation === "record_intelligence") {
    if (row.run_id === null) {
      throw new Error("run_id is required for record_intelligence");
    }
    payload = parseRecordIntelligencePayload(row.payload);
  } else if (operation === "record_report") {
    if (row.run_id === null) {
      throw new Error("run_id is required for record_report");
    }
    payload = parseRecordReportPayload(row.payload);
  } else if (operation === "record_learning") {
    if (row.run_id === null) {
      throw new Error("run_id is required for record_learning");
    }
    payload = parseRecordLearningPayload(row.payload);
  } else if (operation === "record_discovery_reference") {
    if (row.run_id === null) {
      throw new Error("run_id is required for record_discovery_reference");
    }
    payload = parseDiscoveryReferencePayload(row.payload);
  } else if (operation === "checkpoint_discovery_stage") {
    if (row.run_id === null) {
      throw new Error("run_id is required for checkpoint_discovery_stage");
    }
    payload = parseDiscoveryStageCheckpointPayload(row.payload);
  } else if (operation === "read_discovery_context") {
    if (row.run_id === null) {
      throw new Error("run_id is required for read_discovery_context");
    }
    payload = parseDiscoveryContextRequest(row.payload);
  } else if (operation === "begin_discovery_reference") {
    if (row.run_id === null) {
      throw new Error("run_id is required for begin_discovery_reference");
    }
    payload = parseReferenceBeginPayload(row.payload);
  } else if (operation === "record_discovery_reference_chunk") {
    if (row.run_id === null) {
      throw new Error(
        "run_id is required for record_discovery_reference_chunk",
      );
    }
    payload = parseReferenceChunkPayload(row.payload);
  } else if (operation === "finalize_discovery_reference") {
    if (row.run_id === null) {
      throw new Error("run_id is required for finalize_discovery_reference");
    }
    payload = parseReferenceFinalizePayload(row.payload);
  } else if (operation === "pin_discovery_reference") {
    if (row.run_id === null) {
      throw new Error("run_id is required for pin_discovery_reference");
    }
    payload = parseReferencePinPayload(row.payload);
  } else if (operation === "read_discovery_reference") {
    if (row.run_id === null) {
      throw new Error("run_id is required for read_discovery_reference");
    }
    payload = parseReferenceReadPayload(row.payload);
  }
  return {
    schema_version: 1,
    operation,
    request_id: uuidValue(row.request_id, "request_id"),
    run_id: row.run_id === null ? null : uuidValue(row.run_id, "run_id"),
    dry_run: booleanValue(row.dry_run, "dry_run"),
    payload: payload as
      | unknown
      | StartIntelligencePayload
      | RecordIntelligencePayload
      | RecordReportPayload
      | RecordLearningPayload
      | DiscoveryReferencePayload
      | DiscoveryStageCheckpointPayload
      | ReferenceBeginPayload
      | ReferenceChunkPayload
      | ReferenceFinalizePayload
      | ReferencePinPayload
      | ReferenceReadPayload
      | { limit: number },
  };
}

function parseEvidence(value: unknown, path: string): EvidenceBlock {
  const row = objectValue(value, path);
  const hasExposureKind = Object.hasOwn(row, "exposure_kind");
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
      ...(hasExposureKind ? ["exposure_kind"] : []),
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
    exposure_kind: !hasExposureKind || row.exposure_kind === null
      ? null
      : enumValue(row.exposure_kind, EXPOSURE_KINDS, `${path}.exposure_kind`),
  };
}

function parseCandidate(
  value: unknown,
  expectedPhase: Phase,
  path: string,
): DecisionCandidate {
  const row = objectValue(value, path);
  const hasRelationshipType = Object.hasOwn(row, "relationship_type");
  const hasReservationGroup = Object.hasOwn(row, "reservation_group");
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
    ...(hasRelationshipType ? ["relationship_type"] : []),
    ...(hasReservationGroup ? ["reservation_group"] : []),
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
  const hasAnalystReceipt = Object.hasOwn(analystRow, "id") ||
    Object.hasOwn(analystRow, "packet_id");
  exactKeys(
    analystRow,
    hasAnalystReceipt
      ? ["id", "packet_id", "completed", "action", "confidence", "reason"]
      : ["completed", "action", "confidence", "reason"],
    `${path}.analyst`,
  );
  const checkerRow = objectValue(row.checker, `${path}.checker`);
  const hasCheckerReceipt = Object.hasOwn(checkerRow, "id") ||
    Object.hasOwn(checkerRow, "analyst_id");
  exactKeys(
    checkerRow,
    hasCheckerReceipt
      ? ["id", "analyst_id", "completed", "verdict", "reason_codes", "reason"]
      : ["completed", "verdict", "reason_codes", "reason"],
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
    relationship_type: !hasRelationshipType || row.relationship_type === null
      ? null
      : enumValue(
        row.relationship_type,
        ["direct", "second_order"] as const,
        `${path}.relationship_type`,
      ),
    reservation_group: !hasReservationGroup || row.reservation_group === null
      ? null
      : stringValue(row.reservation_group, `${path}.reservation_group`, 100),
    factors,
    analyst: {
      id: !hasAnalystReceipt || analystRow.id === null
        ? null
        : uuidValue(analystRow.id, `${path}.analyst.id`),
      packet_id: !hasAnalystReceipt || analystRow.packet_id === null
        ? null
        : uuidValue(analystRow.packet_id, `${path}.analyst.packet_id`),
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
      id: !hasCheckerReceipt || checkerRow.id === null
        ? null
        : uuidValue(checkerRow.id, `${path}.checker.id`),
      analyst_id: !hasCheckerReceipt || checkerRow.analyst_id === null
        ? null
        : uuidValue(checkerRow.analyst_id, `${path}.checker.analyst_id`),
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

function parseEvidencePacketV1(value: unknown): EvidencePacketV1 {
  const path = "intelligence packet";
  const row = objectValue(value, path);
  if (new TextEncoder().encode(JSON.stringify(row)).byteLength > 98_304) {
    throw new Error(`${path} exceeds 96 KiB`);
  }
  exactKeys(
    row,
    [
      "candidates",
      "evidence",
      "coverage",
      "limitations",
      "policy_version",
      ...(Object.hasOwn(row, "facts") ? ["facts"] : []),
    ],
    path,
  );
  const evidence = arrayValue(row.evidence, `${path}.evidence`, 96).map(
    (value, index) => {
      const evidencePath = `${path}.evidence[${index}]`;
      const item = objectValue(value, evidencePath);
      exactKeys(item, ["item_id", "normalized_text"], evidencePath);
      return {
        item_id: stringValue(item.item_id, `${evidencePath}.item_id`, 100),
        normalized_text: stringValue(
          item.normalized_text,
          `${evidencePath}.normalized_text`,
          2_000,
          true,
        ),
      };
    },
  );
  const knownEvidence = new Set(evidence.map((item) => item.item_id));
  if (knownEvidence.size !== evidence.length) {
    throw new Error(`${path} has duplicate evidence id`);
  }
  const candidates = arrayValue(row.candidates, `${path}.candidates`, 12).map(
    (value, index) => {
      const candidatePath = `${path}.candidates[${index}]`;
      const item = objectValue(value, candidatePath);
      exactKeys(item, ["candidate_key", "evidence_ids"], candidatePath);
      const evidenceIds = arrayValue(
        item.evidence_ids,
        `${candidatePath}.evidence_ids`,
        8,
      ).map((id, evidenceIndex) =>
        stringValue(id, `${candidatePath}.evidence_ids[${evidenceIndex}]`, 100)
      );
      if (
        new Set(evidenceIds).size !== evidenceIds.length ||
        evidenceIds.some((id) => !knownEvidence.has(id))
      ) {
        throw new Error(`${candidatePath} references unknown evidence id`);
      }
      return {
        candidate_key: tickerValue(
          item.candidate_key,
          `${candidatePath}.candidate_key`,
        ),
        evidence_ids: evidenceIds,
      };
    },
  );
  if (
    new Set(candidates.map((item) => item.candidate_key)).size !==
      candidates.length
  ) {
    throw new Error(`${path} has duplicate candidate key`);
  }
  const coverage = objectValue(row.coverage, `${path}.coverage`);
  const limitations = arrayValue(row.limitations, `${path}.limitations`, 100)
    .map((item, index) =>
      stringValue(item, `${path}.limitations[${index}]`, 500)
    );
  return {
    candidates,
    evidence,
    coverage,
    limitations,
    ...(Object.hasOwn(row, "facts")
      ? { facts: parseTrustedEvidenceFacts(row.facts) }
      : {}),
    policy_version: integerValue(
      row.policy_version,
      `${path}.policy_version`,
      1,
    ),
  };
}

function hashValue(value: unknown, path: string): string {
  const result = stringValue(value, path, 64);
  if (!/^[0-9a-f]{64}$/.test(result)) {
    throw new Error(`${path} must be a lowercase SHA-256 hash`);
  }
  return result;
}

function canonicalTimestampValue(value: unknown, path: string): string {
  const result = timestampValue(value, path);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(result)) {
    throw new Error(`${path} must be a millisecond UTC timestamp`);
  }
  if (new Date(result).toISOString() !== result) {
    throw new Error(`${path} must be a canonical UTC timestamp`);
  }
  return result;
}

function nullableCanonicalTimestamp(
  value: unknown,
  path: string,
): string | null {
  return value === null ? null : canonicalTimestampValue(value, path);
}

function fixedPointValue(value: unknown, path: string): string {
  const result = stringValue(value, path, 100);
  if (!/^-?(?:0|[1-9]\d*)\.\d{6}$/.test(result) || result === "-0.000000") {
    throw new Error(`${path} must be a canonical six-place decimal`);
  }
  return result;
}

function utf8Compare(left: string, right: string): number {
  const encoder = new TextEncoder();
  const leftBytes = encoder.encode(left);
  const rightBytes = encoder.encode(right);
  for (
    let index = 0;
    index < Math.min(leftBytes.length, rightBytes.length);
    index += 1
  ) {
    if (leftBytes[index] !== rightBytes[index]) {
      return leftBytes[index] - rightBytes[index];
    }
  }
  return leftBytes.length - rightBytes.length;
}

function sortedUniqueStrings(
  value: unknown,
  path: string,
  maxLength: number,
  itemLimit = 256,
): string[] {
  const result = arrayValue(value, path, maxLength).map((item, index) =>
    stringValue(item, `${path}[${index}]`, itemLimit)
  );
  if (
    new Set(result).size !== result.length ||
    result.some((item, index) =>
      index > 0 && utf8Compare(result[index - 1], item) >= 0
    )
  ) throw new Error(`${path} must be sorted and unique`);
  return result;
}

function nullableBoundedString(
  value: unknown,
  path: string,
  limit: number,
): string | null {
  return value === null ? null : stringValue(value, path, limit);
}

function parseLineageV2(
  value: unknown,
  path: string,
): CandidateLineageV2 | null {
  if (value === null) return null;
  const row = objectValue(value, path);
  exactKeys(row, [
    "cash_revision",
    "evidence_receipt_ids",
    "observed_at",
    "policy_version",
    "portfolio_revision",
    "quote_as_of",
    "quote_expires_at",
    "quote_receipt_id",
    "reference_expires_at",
    "reference_manifest_id",
    "reference_revision",
    "run_id",
    "security_revision_id",
  ], path);
  const receiptRow = objectValue(
    row.evidence_receipt_ids,
    `${path}.evidence_receipt_ids`,
  );
  if (Object.keys(receiptRow).length > 8) {
    throw new Error(`${path}.evidence_receipt_ids exceeds evidence bound`);
  }
  const evidence_receipt_ids: Record<string, string> = {};
  const receiptKeys = Object.keys(receiptRow);
  if (
    receiptKeys.some((key, index) =>
      index > 0 && utf8Compare(receiptKeys[index - 1], key) >= 0
    )
  ) {
    throw new Error(`${path}.evidence_receipt_ids keys must be sorted`);
  }
  for (const itemId of receiptKeys) {
    uuidValue(itemId, `${path}.evidence_receipt_ids key`);
    evidence_receipt_ids[itemId] = uuidValue(
      receiptRow[itemId],
      `${path}.evidence_receipt_ids.${itemId}`,
    );
  }
  return {
    cash_revision: nullableBoundedString(
      row.cash_revision,
      `${path}.cash_revision`,
      256,
    ),
    evidence_receipt_ids,
    observed_at: canonicalTimestampValue(
      row.observed_at,
      `${path}.observed_at`,
    ),
    policy_version: integerValue(
      row.policy_version,
      `${path}.policy_version`,
      1,
    ),
    portfolio_revision: nullableBoundedString(
      row.portfolio_revision,
      `${path}.portfolio_revision`,
      256,
    ),
    quote_as_of: nullableCanonicalTimestamp(
      row.quote_as_of,
      `${path}.quote_as_of`,
    ),
    quote_expires_at: nullableCanonicalTimestamp(
      row.quote_expires_at,
      `${path}.quote_expires_at`,
    ),
    quote_receipt_id: row.quote_receipt_id === null
      ? null
      : uuidValue(row.quote_receipt_id, `${path}.quote_receipt_id`),
    reference_expires_at: nullableCanonicalTimestamp(
      row.reference_expires_at,
      `${path}.reference_expires_at`,
    ),
    reference_manifest_id: row.reference_manifest_id === null
      ? null
      : uuidValue(row.reference_manifest_id, `${path}.reference_manifest_id`),
    reference_revision: row.reference_revision === null
      ? null
      : integerValue(row.reference_revision, `${path}.reference_revision`, 1),
    run_id: uuidValue(row.run_id, `${path}.run_id`),
    security_revision_id: row.security_revision_id === null
      ? null
      : uuidValue(row.security_revision_id, `${path}.security_revision_id`),
  };
}

function componentScoresV2(
  value: unknown,
  path: string,
  keys: readonly string[],
): Record<string, string> {
  const row = objectValue(value, path);
  exactKeys(row, [...keys], path);
  return Object.fromEntries(keys.map((key) => [
    key,
    fixedPointValue(row[key], `${path}.${key}`),
  ]));
}

function parseEvidencePacketV2(value: unknown): EvidencePacketV2 {
  const path = "intelligence packet";
  const row = objectValue(value, path);
  if (new TextEncoder().encode(JSON.stringify(row)).byteLength > 98_304) {
    throw new Error(`${path} exceeds 96 KiB`);
  }
  exactKeys(row, [
    "action_candidates",
    "contract_version",
    "coverage",
    "evidence",
    "execution_allowed",
    "limitations",
    "observed_at",
    "omissions",
    "policy_version",
    "research_candidates",
    "run_id",
  ], path);
  if (row.contract_version !== 2) {
    throw new Error(`${path}.contract_version must be 2`);
  }
  if (row.execution_allowed !== false) {
    throw new Error(`${path}.execution_allowed must be false`);
  }
  const run_id = uuidValue(row.run_id, `${path}.run_id`);
  const observed_at = canonicalTimestampValue(
    row.observed_at,
    `${path}.observed_at`,
  );
  const policy_version = integerValue(
    row.policy_version,
    `${path}.policy_version`,
    1,
  );
  const evidence = arrayValue(row.evidence, `${path}.evidence`, 96).map(
    (value, index) => {
      const evidencePath = `${path}.evidence[${index}]`;
      const item = objectValue(value, evidencePath);
      exactKeys(item, [
        "authority",
        "canonical_url",
        "claim_type",
        "content_hash",
        "effective_at",
        "item_id",
        "normalized_text",
        "published_at",
        "reporting_at",
        "retrieved_at",
        "source_identity",
      ], evidencePath);
      const source = objectValue(
        item.source_identity,
        `${evidencePath}.source_identity`,
      );
      exactKeys(
        source,
        ["provider", "receipt_id", "upstream_item_id"],
        `${evidencePath}.source_identity`,
      );
      const canonical_url = stringValue(
        item.canonical_url,
        `${evidencePath}.canonical_url`,
        2_048,
      );
      const parsedUrl = new URL(canonical_url);
      if (parsedUrl.protocol !== "https:" || parsedUrl.href !== canonical_url) {
        throw new Error(
          `${evidencePath}.canonical_url must be canonical HTTPS`,
        );
      }
      return {
        authority: stringValue(item.authority, `${evidencePath}.authority`, 80),
        canonical_url,
        claim_type: stringValue(
          item.claim_type,
          `${evidencePath}.claim_type`,
          80,
        ),
        content_hash: hashValue(
          item.content_hash,
          `${evidencePath}.content_hash`,
        ),
        effective_at: nullableCanonicalTimestamp(
          item.effective_at,
          `${evidencePath}.effective_at`,
        ),
        item_id: uuidValue(item.item_id, `${evidencePath}.item_id`),
        normalized_text: stringValue(
          item.normalized_text,
          `${evidencePath}.normalized_text`,
          2_000,
          true,
        ),
        published_at: nullableCanonicalTimestamp(
          item.published_at,
          `${evidencePath}.published_at`,
        ),
        reporting_at: nullableCanonicalTimestamp(
          item.reporting_at,
          `${evidencePath}.reporting_at`,
        ),
        retrieved_at: canonicalTimestampValue(
          item.retrieved_at,
          `${evidencePath}.retrieved_at`,
        ),
        source_identity: {
          provider: stringValue(
            source.provider,
            `${evidencePath}.source_identity.provider`,
            120,
          ),
          receipt_id: uuidValue(
            source.receipt_id,
            `${evidencePath}.source_identity.receipt_id`,
          ),
          upstream_item_id: stringValue(
            source.upstream_item_id,
            `${evidencePath}.source_identity.upstream_item_id`,
            512,
          ),
        },
      };
    },
  );
  const evidenceIds = evidence.map((item) => item.item_id);
  if (
    new Set(evidenceIds).size !== evidenceIds.length ||
    evidenceIds.some((item, index) =>
      index > 0 && utf8Compare(evidenceIds[index - 1], item) >= 0
    )
  ) throw new Error(`${path}.evidence must have sorted unique ids`);
  const knownEvidence = new Set(evidenceIds);

  const research_candidates = arrayValue(
    row.research_candidates,
    `${path}.research_candidates`,
    12,
  ).map((value, index) => {
    const candidatePath = `${path}.research_candidates[${index}]`;
    const candidate = objectValue(value, candidatePath);
    exactKeys(candidate, [
      "adverse_paths",
      "candidate_hash",
      "candidate_key",
      "entity_id",
      "event_ids",
      "evidence",
      "exposure_fact_ids",
      "limitations",
      "priority_components",
      "priority_score",
      "research_state",
      "roles",
      "security_id",
      "suitability",
      "theme_ids",
      "ticker",
    ], candidatePath);
    const references = arrayValue(
      candidate.evidence,
      `${candidatePath}.evidence`,
      8,
    ).map((value, evidenceIndex) => {
      const refPath = `${candidatePath}.evidence[${evidenceIndex}]`;
      const ref = objectValue(value, refPath);
      exactKeys(
        ref,
        ["claim_type", "item_id", "relationship_eligible", "role"],
        refPath,
      );
      if (typeof ref.relationship_eligible !== "boolean") {
        throw new Error(`${refPath}.relationship_eligible must be boolean`);
      }
      const item_id = uuidValue(ref.item_id, `${refPath}.item_id`);
      if (!knownEvidence.has(item_id)) {
        throw new Error(`${refPath} references unknown evidence`);
      }
      return {
        claim_type: stringValue(ref.claim_type, `${refPath}.claim_type`, 80),
        item_id,
        relationship_eligible: ref.relationship_eligible,
        role: enumValue(
          ref.role,
          ["supporting", "opposing"] as const,
          `${refPath}.role`,
        ),
      };
    });
    if (
      new Set(references.map((item) => item.item_id)).size !== references.length
    ) {
      throw new Error(`${candidatePath}.evidence has duplicate ids`);
    }
    const suitabilityPath = `${candidatePath}.suitability`;
    const suitabilityRow = objectValue(candidate.suitability, suitabilityPath);
    exactKeys(suitabilityRow, [
      "component_scores",
      "evaluation_hash",
      "lineage",
      "missing_reasons",
      "state",
      "veto_reasons",
    ], suitabilityPath);
    const suitability: SuitabilityEvaluationV2 = {
      component_scores: componentScoresV2(
        suitabilityRow.component_scores,
        `${suitabilityPath}.component_scores`,
        [
          "concentration_penalty",
          "duplication_penalty",
          "liquidity",
          "portfolio_relevance",
        ],
      ),
      evaluation_hash: hashValue(
        suitabilityRow.evaluation_hash,
        `${suitabilityPath}.evaluation_hash`,
      ),
      lineage: parseLineageV2(
        suitabilityRow.lineage,
        `${suitabilityPath}.lineage`,
      ),
      missing_reasons: sortedUniqueStrings(
        suitabilityRow.missing_reasons,
        `${suitabilityPath}.missing_reasons`,
        50,
        200,
      ),
      state: enumValue(
        suitabilityRow.state,
        ["unknown", "eligible", "vetoed"] as const,
        `${suitabilityPath}.state`,
      ),
      veto_reasons: sortedUniqueStrings(
        suitabilityRow.veto_reasons,
        `${suitabilityPath}.veto_reasons`,
        50,
        200,
      ),
    };
    const ticker = candidate.ticker === null
      ? null
      : tickerValue(candidate.ticker, `${candidatePath}.ticker`);
    const result: ResearchCandidateV2 = {
      adverse_paths: sortedUniqueStrings(
        candidate.adverse_paths,
        `${candidatePath}.adverse_paths`,
        50,
        256,
      ),
      candidate_hash: hashValue(
        candidate.candidate_hash,
        `${candidatePath}.candidate_hash`,
      ),
      candidate_key: stringValue(
        candidate.candidate_key,
        `${candidatePath}.candidate_key`,
        256,
      ),
      entity_id: nullableBoundedString(
        candidate.entity_id,
        `${candidatePath}.entity_id`,
        256,
      ),
      event_ids: sortedUniqueStrings(
        candidate.event_ids,
        `${candidatePath}.event_ids`,
        50,
        100,
      ),
      evidence: references,
      exposure_fact_ids: sortedUniqueStrings(
        candidate.exposure_fact_ids,
        `${candidatePath}.exposure_fact_ids`,
        50,
        100,
      ),
      limitations: sortedUniqueStrings(
        candidate.limitations,
        `${candidatePath}.limitations`,
        100,
        500,
      ),
      priority_components: componentScoresV2(
        candidate.priority_components,
        `${candidatePath}.priority_components`,
        [
          "authority_corroboration",
          "exposure",
          "materiality",
          "recency",
        ],
      ),
      priority_score: fixedPointValue(
        candidate.priority_score,
        `${candidatePath}.priority_score`,
      ),
      research_state: enumValue(
        candidate.research_state,
        [
          "unresolved",
          "resolved",
          "exposure_supported",
          "analysis_ready",
        ] as const,
        `${candidatePath}.research_state`,
      ),
      roles: sortedUniqueStrings(
        candidate.roles,
        `${candidatePath}.roles`,
        50,
        80,
      ),
      security_id: nullableBoundedString(
        candidate.security_id,
        `${candidatePath}.security_id`,
        256,
      ),
      suitability,
      theme_ids: sortedUniqueStrings(
        candidate.theme_ids,
        `${candidatePath}.theme_ids`,
        50,
        256,
      ),
      ticker,
    };
    const suitabilityBody = { ...suitability } as Record<string, unknown>;
    delete suitabilityBody.evaluation_hash;
    if (
      sha256Hex(canonicalJson(suitabilityBody)) !== suitability.evaluation_hash
    ) {
      throw new Error(
        `${suitabilityPath}.evaluation_hash does not match canonical content`,
      );
    }
    const candidateBody = { ...result } as Record<string, unknown>;
    delete candidateBody.candidate_hash;
    if (sha256Hex(canonicalJson(candidateBody)) !== result.candidate_hash) {
      throw new Error(
        `${candidatePath}.candidate_hash does not match canonical content`,
      );
    }
    if (
      (result.security_id === null || result.ticker === null) &&
      result.research_state !== "unresolved"
    ) {
      throw new Error(
        `${candidatePath} unresolved identity has invalid research state`,
      );
    }
    if (
      result.research_state === "unresolved" && (
        result.security_id !== null || result.ticker !== null ||
        result.entity_id === null || !result.entity_id.startsWith("unresolved:")
      )
    ) {
      throw new Error(
        `${candidatePath} explicit unresolved identity is invalid`,
      );
    }
    if (
      result.security_id !== null && result.candidate_key !== result.security_id
    ) {
      throw new Error(
        `${candidatePath}.candidate_key must equal the resolved security identity`,
      );
    }
    if (
      result.research_state === "analysis_ready" && (
        result.security_id === null || result.ticker === null ||
        result.exposure_fact_ids.length === 0 ||
        !result.evidence.some((item) => item.role === "supporting") ||
        !result.evidence.some((item) => item.role === "opposing") ||
        !result.evidence.some((item) =>
          item.relationship_eligible && item.claim_type === "issuer_exposure"
        ) || suitability.lineage === null
      )
    ) throw new Error(`${candidatePath} analysis_ready evidence is incomplete`);
    if (
      suitability.state === "eligible" && (
        result.research_state !== "analysis_ready" ||
        suitability.missing_reasons.length !== 0 ||
        suitability.veto_reasons.length !== 0 ||
        suitability.lineage === null ||
        Object.values(suitability.lineage).some((value) =>
          value === null || value === ""
        )
      )
    ) {
      throw new Error(
        `${suitabilityPath} eligible protected lineage is incomplete`,
      );
    }
    if (
      suitability.state === "unknown" &&
      suitability.missing_reasons.length === 0
    ) {
      throw new Error(`${suitabilityPath} unknown requires missing reasons`);
    }
    if (
      suitability.state === "vetoed" && suitability.veto_reasons.length === 0
    ) {
      throw new Error(`${suitabilityPath} vetoed requires veto reasons`);
    }
    if (
      suitability.lineage !== null && (
        suitability.lineage.run_id !== run_id ||
        suitability.lineage.observed_at !== observed_at ||
        suitability.lineage.policy_version !== policy_version
      )
    ) throw new Error(`${suitabilityPath}.lineage packet binding mismatch`);
    return result;
  });
  if (
    new Set(research_candidates.map((item) => item.candidate_key)).size !==
      research_candidates.length
  ) {
    throw new Error(`${path}.research_candidates has duplicate identities`);
  }
  const resolvedTickers = research_candidates.flatMap((item) =>
    item.ticker === null ? [] : [item.ticker]
  );
  if (new Set(resolvedTickers).size !== resolvedTickers.length) {
    throw new Error(
      `${path}.research_candidates has duplicate ticker identities`,
    );
  }
  const researchByKey = new Map(
    research_candidates.map((item) => [item.candidate_key, item]),
  );
  const action_candidates = arrayValue(
    row.action_candidates,
    `${path}.action_candidates`,
    12,
  ).map((value, index) => {
    const actionPath = `${path}.action_candidates[${index}]`;
    const action = objectValue(value, actionPath);
    exactKeys(
      action,
      ["candidate_hash", "candidate_key", "suitability_hash"],
      actionPath,
    );
    const candidate_key = stringValue(
      action.candidate_key,
      `${actionPath}.candidate_key`,
      256,
    );
    const research = researchByKey.get(candidate_key);
    const result = {
      candidate_hash: hashValue(
        action.candidate_hash,
        `${actionPath}.candidate_hash`,
      ),
      candidate_key,
      suitability_hash: hashValue(
        action.suitability_hash,
        `${actionPath}.suitability_hash`,
      ),
    };
    if (
      !research || research.candidate_hash !== result.candidate_hash ||
      research.suitability.evaluation_hash !== result.suitability_hash ||
      research.research_state !== "analysis_ready" ||
      research.suitability.state !== "eligible"
    ) throw new Error(`${actionPath} is not an exact eligible research subset`);
    return result;
  });
  if (
    new Set(action_candidates.map((item) => item.candidate_key)).size !==
      action_candidates.length
  ) {
    throw new Error(`${path}.action_candidates has duplicate identities`);
  }
  const limitations = sortedUniqueStrings(
    row.limitations,
    `${path}.limitations`,
    100,
    500,
  );
  const omissions = arrayValue(row.omissions, `${path}.omissions`, 1_000).map(
    (value, index) => {
      const omissionPath = `${path}.omissions[${index}]`;
      const omission = objectValue(value, omissionPath);
      exactKeys(omission, [
        "candidate_key",
        "item_id",
        "kind",
        "reason",
        "stage",
      ], omissionPath);
      return {
        candidate_key: stringValue(
          omission.candidate_key,
          `${omissionPath}.candidate_key`,
          256,
          true,
        ),
        item_id: omission.item_id === null
          ? null
          : uuidValue(omission.item_id, `${omissionPath}.item_id`),
        kind: stringValue(omission.kind, `${omissionPath}.kind`, 80),
        reason: stringValue(omission.reason, `${omissionPath}.reason`, 200),
        stage: stringValue(omission.stage, `${omissionPath}.stage`, 80),
      };
    },
  );
  return {
    action_candidates,
    contract_version: 2,
    coverage: objectValue(row.coverage, `${path}.coverage`),
    evidence,
    execution_allowed: false,
    limitations,
    observed_at,
    omissions,
    policy_version,
    research_candidates,
    run_id,
  };
}

export function parseEvidencePacket(value: unknown): EvidencePacket {
  const row = objectValue(value, "intelligence packet");
  return row.contract_version === 2
    ? parseEvidencePacketV2(row)
    : parseEvidencePacketV1(row);
}

function parseIntelligencePacketRef(value: unknown): IntelligencePacketRef {
  const path = "bundle.intelligence_packet";
  const row = objectValue(value, path);
  const hasPacket = Object.hasOwn(row, "packet");
  exactKeys(
    row,
    hasPacket
      ? ["id", "content_hash", "coverage", "packet"]
      : ["id", "content_hash", "coverage"],
    path,
  );
  const contentHash = stringValue(row.content_hash, `${path}.content_hash`, 64);
  if (!/^[0-9a-f]{64}$/.test(contentHash)) {
    throw new Error(`${path}.content_hash must be a SHA-256 hash`);
  }
  const coverage = enumValue(
    row.coverage,
    ["complete_for_plan", "partial", "fixture_dry_run"] as const,
    `${path}.coverage`,
  );
  if (hasPacket !== (coverage === "fixture_dry_run")) {
    throw new Error(`${path} fixture coverage and inline packet must match`);
  }
  return {
    id: uuidValue(row.id, `${path}.id`),
    content_hash: contentHash,
    coverage,
    ...(hasPacket ? { packet: parseEvidencePacket(row.packet) } : {}),
  };
}

export function validatePacketEvidence(
  candidate: DecisionCandidate,
  packet: EvidencePacket,
): string[] {
  if (isEvidencePacketV2(packet)) {
    const research = packet.research_candidates.find((row) =>
      row.ticker === candidate.ticker
    );
    if (!research) return ["EVIDENCE_NOT_IN_PACKET"];
    const action = packet.action_candidates.find((row) =>
      row.candidate_key === research.candidate_key &&
      row.candidate_hash === research.candidate_hash &&
      row.suitability_hash === research.suitability.evaluation_hash
    );
    if (!action) return ["RESEARCH_ONLY_CANDIDATE"];
    const allowed = new Set(research.evidence.map((item) => item.item_id));
    const supplied = new Set(candidate.evidence.map((item) => item.id));
    return supplied.size === allowed.size &&
        [...allowed].every((id) => supplied.has(id))
      ? []
      : ["EVIDENCE_NOT_IN_PACKET"];
  }
  const packetCandidate = packet.candidates.find((row) =>
    row.candidate_key === candidate.ticker
  );
  if (!packetCandidate) return ["EVIDENCE_NOT_IN_PACKET"];
  const allowed = new Set(packetCandidate.evidence_ids);
  const supplied = new Set(candidate.evidence.map((item) => item.id));
  return supplied.size === allowed.size &&
      [...allowed].every((id) => supplied.has(id))
    ? []
    : ["EVIDENCE_NOT_IN_PACKET"];
}

export function packetEvidenceIds(
  packet: EvidencePacket,
  ticker: string,
): string[] | null {
  if (isEvidencePacketV2(packet)) {
    const research = packet.research_candidates.find((row) =>
      row.ticker === ticker
    );
    if (
      !research ||
      !packet.action_candidates.some((row) =>
        row.candidate_key === research.candidate_key &&
        row.candidate_hash === research.candidate_hash &&
        row.suitability_hash === research.suitability.evaluation_hash
      )
    ) return null;
    return research.evidence.map((item) => item.item_id);
  }
  return packet.candidates.find((row) => row.candidate_key === ticker)
    ?.evidence_ids ?? null;
}

export function parseDecisionBundle(
  value: unknown,
  phase: Phase,
): DecisionBundle {
  const row = objectValue(value, "bundle");
  const hasComparisons = Object.hasOwn(row, "comparisons");
  const hasCompanionProposal = Object.hasOwn(row, "companion_proposal");
  const hasIntelligencePacket = Object.hasOwn(row, "intelligence_packet");
  const bundleKeys = ["phase", "market_date", "title", "candidates"];
  if (hasIntelligencePacket) bundleKeys.push("intelligence_packet");
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
  const intelligencePacket = hasIntelligencePacket
    ? parseIntelligencePacketRef(row.intelligence_packet)
    : undefined;
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
    ...(intelligencePacket ? { intelligence_packet: intelligencePacket } : {}),
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
