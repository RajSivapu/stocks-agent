import type {
  Action,
  AlertConditionResult,
  AlertRecentEvent,
  AlertRuleSnapshot,
  AlertSession,
  AlertSourceSummary,
  ArtifactMutation,
  DiscoveryCompletedScan,
  DiscoverySourceCursor,
  EvidencePacket,
  GatewayEnvelope,
  GatewayReadContext,
  NotificationKind,
  Phase,
  PolicyConfig,
  RecordResearchNominationsPayloadV2,
  TrustedEvidenceFact,
} from "./contracts.ts";
import { parseEvidencePacket, parseTrustedEvidenceFacts } from "./contracts.ts";
import { parseAlertDraft } from "./alerts.ts";
import type { PolicyEvaluation } from "./policy.ts";
import type {
  DueDecision,
  OutcomeGrade,
  RecordLearningPayload,
} from "./outcomes.ts";
import { formatFixed, parseFixed } from "./fixed-point.ts";
import {
  type DiscoveryContext,
  type DiscoveryReferencePayload,
  type DiscoveryStageCheckpointPayload,
  type DiscoveryStageTask,
  type IntelligenceRecordReceipt,
  type IntelligenceStartReceipt,
  parseDiscoveryContext,
  parseDiscoveryStageTask,
  parseIntelligenceRecordReceipt,
  parseIntelligenceStartReceipt,
  parseReferencePage,
  type RecordIntelligencePayload,
  type ReferenceBeginPayload,
  type ReferenceChunkPayload,
  type ReferenceFinalizePayload,
  type ReferencePage,
  type ReferencePinPayload,
  type ReferenceReadPayload,
  type StartIntelligencePayload,
} from "./intelligence.ts";
import {
  parseReportDecisions,
  type RecordReportPayload,
  type ReportPolicyDecision,
  type ReportSuppressionReason,
} from "./reports.ts";

export interface ReportRecordReceipt {
  report_id: string;
  report_hash: string;
  rendered_hash: string;
  duplicate: boolean;
}

export interface LearningRecordReceipt {
  observation_id: string;
  content_hash: string;
  duplicate: boolean;
}

export function consecutiveRecommendationLosses(
  rows: readonly Record<string, unknown>[],
): number {
  const longestOutcome = new Map<number, {
    horizon: number;
    directionSuccess: boolean;
    recommendationTime: number;
  }>();
  for (const row of rows) {
    if (
      row.coverage_status !== "complete" ||
      typeof row.direction_success !== "boolean"
    ) continue;
    const recommendation = row.recommendation;
    const recommendationAt =
      typeof recommendation === "object" && recommendation !== null &&
        !Array.isArray(recommendation)
        ? (recommendation as Record<string, unknown>).ts
        : null;
    const recommendationTime = typeof recommendationAt === "string"
      ? Date.parse(recommendationAt)
      : Number.NaN;
    if (
      !Number.isSafeInteger(row.suggestion_id) ||
      !Number.isSafeInteger(row.horizon_days) ||
      !Number.isFinite(recommendationTime)
    ) {
      throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
    }
    const suggestionId = row.suggestion_id as number;
    const horizon = row.horizon_days as number;
    const prior = longestOutcome.get(suggestionId);
    if (prior && prior.recommendationTime !== recommendationTime) {
      throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
    }
    if (!prior || horizon > prior.horizon) {
      longestOutcome.set(suggestionId, {
        horizon,
        directionSuccess: row.direction_success,
        recommendationTime,
      });
    }
  }
  const orderedRecommendationIds = [...longestOutcome.entries()]
    .sort(([leftId, left], [rightId, right]) =>
      right.recommendationTime - left.recommendationTime || rightId - leftId
    )
    .map(([suggestionId]) => suggestionId);
  let losses = 0;
  for (const suggestionId of orderedRecommendationIds) {
    if (longestOutcome.get(suggestionId)!.directionSuccess) break;
    losses += 1;
  }
  return losses;
}

function themeMemoryContext(value: unknown, snapshotHash: unknown): NonNullable<NonNullable<GatewayReadContext["intelligence_collection_context"]>["theme_memory"]> | undefined {
  if (value === null || value === undefined) return undefined;
  const row = oneObject({ data: value, error: null });
  if (row.memory_version !== 2 || row.research_only !== true || row.execution_allowed !== false) throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  const bounded = (candidate: unknown, maximum: number) => {
    if (!Array.isArray(candidate) || candidate.length > maximum || candidate.some((item) => !item || typeof item !== "object" || Array.isArray(item))) throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
    return candidate as Record<string, unknown>[];
  };
  if (new TextEncoder().encode(JSON.stringify(row)).byteLength > 65_536) throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
  return {
    memory_version: 2,
    as_of: text(row.as_of, 40),
    reference_manifest_id: row.reference_manifest_id === null ? null : text(row.reference_manifest_id, 36),
    reference_hash: row.reference_hash === null ? null : text(row.reference_hash, 64),
    snapshot_hash: text(snapshotHash, 64),
    active_theme_heads: bounded(row.active_theme_heads, 25),
    due_nominations: bounded(row.due_nominations, 12),
    urgent_events: bounded(row.urgent_events, 10),
    high_materiality_themes: bounded(row.high_materiality_themes, 10),
    radar: bounded(row.radar, 20),
    source_cursors: bounded(row.source_cursors, 100),
    available_counts: oneObject({ data: row.available_counts, error: null }),
    returned_counts: oneObject({ data: row.returned_counts, error: null }),
    deferred_counts: oneObject({ data: row.deferred_counts, error: null }),
    byte_truncated: boole(row.byte_truncated),
    research_only: true,
    execution_allowed: false,
  };
}

export interface PersistedBundle {
  request_id: string;
  request_lease_token: string;
  run_id: string | null;
  policy_version: number;
  evaluations: PolicyEvaluation[];
  suggestions: Record<string, unknown>[];
  holding_state_changes: NonNullable<
    PolicyEvaluation["holding_state_change"]
  >[];
  cash_snapshot: {
    snapshot_id: string;
    ledger_watermark: string;
  } | null;
  publication: {
    id: string;
    idempotency_key: string;
    market_date: string;
    phase: Phase;
    kind: NotificationKind;
    template_version: number;
    rendered_body: string;
    rendered_hash: string;
    status: "ready" | "suppressed";
  };
}

export interface PublicationReceipt {
  suppression_reason?: ReportSuppressionReason | null;
  id: string;
  idempotency_key: string;
  status:
    | "ready"
    | "sending"
    | "delivered"
    | "delivery_failed"
    | "delivery_unknown"
    | "suppressed"
    | "pending"
    | "failed"
    | "uncertain";
  telegram_message_ids: number[];
  telegram_accepted_at: string | null;
  lease_token: string | null;
}

export interface PublicationClaim {
  claimed: boolean;
  lease_token: string | null;
  receipt: PublicationReceipt;
}

export interface ArtifactReceipt {
  counts: Partial<Record<ArtifactMutation["kind"], number>>;
  created_paper_watch_ids: number[];
}

export interface PersistableAlertDraft {
  id: string;
  source_evaluation_id: string;
  rule_snapshot: AlertRuleSnapshot;
  fingerprint: string;
}

export interface AlertDraftReceipt {
  created_count: number;
  draft_ids: string[];
}

export interface AlertWorkItem {
  rule: AlertRuleSnapshot;
  recent_events: AlertRecentEvent[];
  source_summary: AlertSourceSummary | null;
}

export interface AlertWork {
  rules: AlertWorkItem[];
  drafts: AlertWorkItem[];
}

export interface PersistableAlertEvent {
  id: string;
  rule_id: string;
  rule_version: number;
  fingerprint: string;
  status: "triggered" | "unsafe_to_evaluate";
  reason_codes: string[];
  observed_at: string | null;
  evaluated_at: string;
  market_session: Exclude<AlertSession, "all">;
  condition_results: AlertConditionResult[];
  evidence_ids: string[];
}

export interface AlertEventReceipt {
  event_count: number;
  event_ids: string[];
}

export interface PersistableAlertPublication {
  id: string;
  market_date: string;
  kind: NotificationKind;
  rendered_body: string;
  rendered_hash: string;
  event_ids: string[];
  draft_id: string | null;
}

export type PersistableArtifactMutation =
  | Exclude<
    ArtifactMutation,
    { kind: "paper_watch_create" | "paper_watch_close" }
  >
  | (Extract<ArtifactMutation, { kind: "paper_watch_create" }> & {
    created: string;
    agent_view_at_open: Action | "no prior view";
    agent_score_at_open: number | null;
  })
  | (Extract<ArtifactMutation, { kind: "paper_watch_close" }> & {
    closed_date: string;
    close_price: string;
  });

export interface PersistableArtifactMutationBatch {
  mutations: PersistableArtifactMutation[];
}

export interface RunReceipt {
  run_id: string;
  status: "completed" | "suppressed" | "partial" | "failed";
  write_counts: Record<string, number>;
  publication_statuses: string[];
  telegram_message_ids: number[];
}

export interface GatewayRequestClaim {
  duplicate: boolean;
  in_progress: boolean;
  lease_token: string | null;
  response?: unknown;
}

export interface PersistedIntelligencePacket {
  id: string;
  run_id: string;
  content_hash: string;
  packet: EvidencePacket;
  exposure_facts: PersistedExposureFact[];
  evidence_facts: TrustedEvidenceFact[];
}

export interface PersistedExposureFact {
  candidate_key: string;
  evidence_id: string;
  exposure_kind:
    | "filing"
    | "contract"
    | "backlog"
    | "revenue"
    | "capacity"
    | "official_fund";
  status: "fresh" | "stale";
  observed_at: string | null;
  retrieved_at: string;
}

export interface GatewayRepository {
  loadReportDecisions(
    runId: string,
    packetId: string,
    decisionIds: string[],
  ): Promise<ReportPolicyDecision[]>;
  recordLearning?(
    runId: string,
    payload: RecordLearningPayload,
  ): Promise<LearningRecordReceipt>;
  recordReport?(
    runId: string,
    payload: RecordReportPayload,
  ): Promise<ReportRecordReceipt>;
  recordReportOrigin?(
    requestId: string,
    leaseToken: string,
    runId: string,
    payload: Pick<
      RecordReportPayload,
      | "id"
      | "idempotency_key"
      | "packet_id"
      | "market_date"
      | "kind"
      | "report_hash"
    >,
  ): Promise<{ scheduled: boolean }>;
  createReportPublication?(
    runId: string,
    payload: RecordReportPayload,
  ): Promise<PublicationReceipt>;
  claimReportPublication?(idempotencyKey: string): Promise<PublicationClaim>;
  finishReportPublication?(
    idempotencyKey: string,
    leaseToken: string,
    status: "delivered" | "failed" | "uncertain",
    messageIds: number[],
    error: string | null,
  ): Promise<PublicationReceipt>;
  suppressReportPublication?(
    idempotencyKey: string,
    reason: ReportSuppressionReason,
  ): Promise<PublicationReceipt>;
  startIntelligenceRun?(
    runId: string,
    payload: StartIntelligencePayload,
  ): Promise<IntelligenceStartReceipt>;
  checkpointIntelligenceCollection?(
    runId: string,
    payload: {
      cache_key: string;
      receipt: Record<string, unknown>;
      items: Record<string, unknown>[];
    },
  ): Promise<{ run_id: string; cache_key: string }>;
  recordDiscoveryReference?(
    runId: string,
    payload: DiscoveryReferencePayload,
  ): Promise<
    { manifest_id: string; security_revision_count: number; duplicate: boolean }
  >;
  checkpointDiscoveryStage?(
    runId: string,
    payload: DiscoveryStageCheckpointPayload,
  ): Promise<{ task: DiscoveryStageTask; duplicate: boolean }>;
  recordThemeEpisodeRevisionV2?(
    runId: string,
    payload: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;
  recordResearchReviewIdentityV2?(
    runId: string,
    receiptId: string,
    payload: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;
  recordResearchNominations?(
    runId: string,
    requestId: string,
    payload: RecordResearchNominationsPayloadV2,
  ): Promise<Record<string, unknown>>;
  transitionResearchNominationV2?(
    runId: string,
    nominationId: string,
    payload: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;
  sealEnrichmentSelection?(
    runId: string,
    payload: Record<string, unknown>,
  ): Promise<{ manifest_id: string; request_count: number; duplicate: boolean }>;
  readDiscoveryContext?(
    runId: string,
    limit: number,
  ): Promise<DiscoveryContext>;
  beginDiscoveryReference?(
    runId: string,
    payload: ReferenceBeginPayload,
    claim: ReferenceTransferClaim,
  ): Promise<
    {
      manifest_id: string;
      predecessor_manifest_id: string | null;
      duplicate: boolean;
    }
  >;
  recordDiscoveryReferenceChunk?(
    runId: string,
    payload: ReferenceChunkPayload,
    claim: ReferenceTransferClaim,
  ): Promise<{ manifest_id: string; chunk_index: number; duplicate: boolean }>;
  finalizeDiscoveryReference?(
    runId: string,
    payload: ReferenceFinalizePayload,
    claim: ReferenceTransferClaim,
  ): Promise<
    { manifest_id: string; security_count: number; duplicate: boolean }
  >;
  pinDiscoveryReference?(
    runId: string,
    payload: ReferencePinPayload,
    claim: ReferenceTransferClaim,
  ): Promise<Record<string, unknown>>;
  readDiscoveryReference?(
    runId: string,
    payload: ReferenceReadPayload,
    claim: ReferenceTransferClaim,
  ): Promise<ReferencePage>;
  readIntelligenceCompletion?(
    runId: string,
    completionId: string,
  ): Promise<Record<string, unknown> | null>;
  claimIntelligenceQuote?(
    runId: string,
    input: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;
  recordIntelligenceQuote?(
    runId: string,
    receiptId: string,
    quote: unknown,
    checkpoint: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;
  recordIntelligence?(
    runId: string,
    completionId: string,
    payload: RecordIntelligencePayload,
  ): Promise<IntelligenceRecordReceipt>;
  claimRequest(envelope: GatewayEnvelope): Promise<GatewayRequestClaim>;
  completeRequest(
    requestId: string,
    leaseToken: string,
    response: unknown,
  ): Promise<void>;
  failRequest(
    requestId: string,
    leaseToken: string,
    code: string,
  ): Promise<void>;
  startRun(
    requestId: string,
    leaseToken: string,
    phase: Phase,
    marketDate: string,
  ): Promise<{ run_id: string; duplicate: boolean }>;
  recordRunOutcome(
    requestId: string,
    leaseToken: string,
    runId: string,
    outcome: "no_trigger" | "not_actionable",
  ): Promise<
    {
      run_id: string;
      outcome: "no_trigger" | "not_actionable";
      duplicate: boolean;
    }
  >;
  readContext(runId: string | null): Promise<GatewayReadContext>;
  loadIntelligencePacket(
    packetId: string,
    runId: string,
  ): Promise<PersistedIntelligencePacket>;
  recordArtifacts(
    requestId: string,
    runId: string,
    leaseToken: string,
    payload: PersistableArtifactMutationBatch,
  ): Promise<ArtifactReceipt>;
  activePolicy(): Promise<PolicyConfig>;
  createAlertDrafts(
    requestId: string,
    drafts: PersistableAlertDraft[],
  ): Promise<AlertDraftReceipt>;
  expireAlertRules(): Promise<number>;
  readAlertWork(limit: number): Promise<AlertWork>;
  recordAlertEvaluations(
    requestId: string,
    events: PersistableAlertEvent[],
  ): Promise<AlertEventReceipt>;
  createAlertPublication(
    requestId: string,
    publication: PersistableAlertPublication,
  ): Promise<PublicationReceipt>;
  applyDecisionBundle(input: PersistedBundle): Promise<PublicationReceipt>;
  claimPublication(idempotencyKey: string): Promise<PublicationClaim>;
  finishPublication(
    idempotencyKey: string,
    leaseToken: string,
    status: "delivered" | "delivery_failed" | "delivery_unknown",
    messageIds: number[],
    error: string | null,
  ): Promise<PublicationReceipt>;
  finishAlertPublication(
    idempotencyKey: string,
    leaseToken: string,
    status: "delivered" | "delivery_failed" | "delivery_unknown",
    messageIds: number[],
    error: string | null,
    acceptedAt: string | null,
  ): Promise<PublicationReceipt>;
  finishRun(runId: string): Promise<RunReceipt>;
  dueDecisions(limit: number): Promise<DueDecision[]>;
  upsertGrades(grades: OutcomeGrade[]): Promise<{
    inserted: number;
    updated: number;
    incomplete: number;
  }>;
}

export interface ReferenceTransferClaim {
  request_id: string;
  encoded_bytes: number;
  request_hash: string;
}

export class GatewayRepositoryError extends Error {
  readonly code: string;
  constructor(code: string) {
    super(code);
    this.name = "GatewayRepositoryError";
    this.code = code;
  }
}

type DbError = { code?: string; message?: string };
type DbResult = { data: unknown; error: DbError | null };
interface QueryBuilder extends PromiseLike<DbResult> {
  select(columns: string): QueryBuilder;
  eq(column: string, value: unknown): QueryBuilder;
  is(column: string, value: null): QueryBuilder;
  gte(column: string, value: unknown): QueryBuilder;
  lt(column: string, value: unknown): QueryBuilder;
  or(filters: string): QueryBuilder;
  in(column: string, values: unknown[]): QueryBuilder;
  order(column: string, options?: { ascending?: boolean }): QueryBuilder;
  limit(count: number): QueryBuilder;
  update(values: Record<string, unknown>): QueryBuilder;
  single(): QueryBuilder;
}
interface SupabaseLike {
  from(table: string): QueryBuilder;
  rpc(name: string, parameters?: Record<string, unknown>): QueryBuilder;
}

const CONTEXT_LIMIT_BYTES = 900_000;

export function assertContextFitsTransport(value: unknown): void {
  if (
    new TextEncoder().encode(JSON.stringify(value)).byteLength >
      CONTEXT_LIMIT_BYTES
  ) {
    throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
  }
}
const CONTEXT_QUERY_BATCH_SIZE = 6;

async function runContextQueries(
  queries: Array<() => PromiseLike<DbResult>>,
): Promise<DbResult[]> {
  const results: DbResult[] = [];
  for (let offset = 0; offset < queries.length; offset += CONTEXT_QUERY_BATCH_SIZE) {
    results.push(...await Promise.all(
      queries.slice(offset, offset + CONTEXT_QUERY_BATCH_SIZE).map((query) =>
        query()
      ),
    ));
  }
  return results;
}

function rows(
  result: DbResult,
  code = "PERSISTENCE_FAILED",
): Record<string, unknown>[] {
  if (result.error || !Array.isArray(result.data)) {
    throw new GatewayRepositoryError(code);
  }
  return result.data as Record<string, unknown>[];
}

function oneObject(
  result: DbResult,
  code = "PERSISTENCE_FAILED",
): Record<string, unknown> {
  if (
    result.error || typeof result.data !== "object" || result.data === null ||
    Array.isArray(result.data)
  ) {
    throw new GatewayRepositoryError(code);
  }
  return result.data as Record<string, unknown>;
}

async function recentRecommendationGrades(
  client: SupabaseLike,
): Promise<DbResult> {
  const recommendationRows = rows(
    await client.from("suggestions").select(
      "id,ts,eligible_grades:suggestion_grades!inner(suggestion_id,horizon_days,coverage_status,excess_return_pct,direction_success,graded_at)",
    )
      .eq("decision_source", "gateway")
      .eq("eligible_grades.coverage_status", "complete")
      .in("eligible_grades.horizon_days", [5, 21, 63])
      .in("eligible_grades.direction_success", [true, false])
      .order("ts", { ascending: false })
      .order("id", { ascending: false }).limit(150),
    "CONTEXT_TOO_LARGE",
  );
  const gradeRows: Record<string, unknown>[] = [];
  for (const row of recommendationRows) {
    const suggestionId = integer(row.id);
    const recommendationAt = text(row.ts, 40);
    if (!Number.isFinite(Date.parse(recommendationAt))) {
      throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
    }
    const eligibleGrades = rows(
      { data: row.eligible_grades, error: null },
      "INVALID_PERSISTED_DATA",
    );
    if (eligibleGrades.length === 0 || eligibleGrades.length > 3) {
      throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
    }
    for (const grade of eligibleGrades) {
      const horizonDays = integer(grade.horizon_days);
      const directionSuccess = boole(grade.direction_success);
      if (
        integer(grade.suggestion_id) !== suggestionId ||
        grade.coverage_status !== "complete" ||
        ![5, 21, 63].includes(horizonDays)
      ) {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
      gradeRows.push({
        ...grade,
        suggestion_id: suggestionId,
        horizon_days: horizonDays,
        coverage_status: "complete",
        direction_success: directionSuccess,
        recommendation: { ts: recommendationAt },
      });
    }
  }
  return {
    data: gradeRows,
    error: null,
  };
}

function text(value: unknown, max = 1000): string {
  if (typeof value !== "string") {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  return value.slice(0, max);
}

function nullableText(value: unknown, max = 1000): string | null {
  return value === null || value === undefined
    ? null
    : text(String(value), max);
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const CAPABILITY_PATTERN = /^[a-z][a-z0-9_]{2,79}$/;
const PROVIDER_PATTERN = /^[a-z][a-z0-9_]{1,79}$/;
const THEME_PATTERN = /^(?:default|[a-z][a-z0-9_]{2,79})$/;

function exactKeys(row: Record<string, unknown>, expected: string[]): boolean {
  const actual = Object.keys(row).sort();
  return actual.length === expected.length &&
    actual.every((key, index) => key === [...expected].sort()[index]);
}

function strictPattern(value: unknown, pattern: RegExp): string {
  if (typeof value !== "string" || !pattern.test(value)) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  return value;
}

function protectedTimestamp(
  value: unknown,
  current: Date,
  nullable = false,
): string | null {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || value.length > 80) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed) || parsed > current.getTime()) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  return value;
}

function discoveryCursorContext(
  value: unknown,
  current: Date,
): {
  source_cursors: DiscoverySourceCursor[];
  last_completed_scans: DiscoveryCompletedScan[];
} {
  const root = oneObject(
    { data: value, error: null },
    "INVALID_PERSISTED_DATA",
  );
  if (!exactKeys(root, ["source_cursors", "last_completed_scans"])) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  const cursorRows = rows(
    { data: root.source_cursors, error: null },
    "INVALID_PERSISTED_DATA",
  );
  const scanRows = rows(
    { data: root.last_completed_scans, error: null },
    "INVALID_PERSISTED_DATA",
  );
  if (cursorRows.length > 100 || scanRows.length > 100) {
    throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
  }
  const sourceCursors = cursorRows.map((row): DiscoverySourceCursor => {
    const legacyCursorKeys = [
        "task_key",
        "provider",
        "capability_id",
        "completed_through",
        "active_window_start",
        "active_window_end",
        "backlog_token",
        "page",
        "accepted_item_ids",
        "next_retry_phase",
        "source_run_id",
        "source_task_id",
        "source_updated_at",
    ];
    if (
      !exactKeys(row, legacyCursorKeys) &&
      !exactKeys(row, [...legacyCursorKeys, "continuation_token_history"])
    ) throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
    const provider = strictPattern(row.provider, PROVIDER_PATTERN);
    const capabilityId = strictPattern(row.capability_id, CAPABILITY_PATTERN);
    const taskKey = strictPattern(
      row.task_key,
      new RegExp(`^${capabilityId}:(?:default|[a-z][a-z0-9_]{2,79})$`),
    );
    const activeStart = protectedTimestamp(
      row.active_window_start,
      current,
      true,
    );
    const activeEnd = protectedTimestamp(row.active_window_end, current, true);
    const completed = protectedTimestamp(row.completed_through, current, true);
    const page = integer(row.page);
    const ids = Array.isArray(row.accepted_item_ids)
      ? row.accepted_item_ids.map((id) => {
        if (
          typeof id !== "string" || id.length < 1 || id.length > 512 ||
          /[\x00-\x1f]/.test(id)
        ) {
          throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
        }
        return id;
      })
      : null;
    const token = row.backlog_token === null ? null : row.backlog_token;
    const history = row.continuation_token_history === undefined
      ? []
      : Array.isArray(row.continuation_token_history)
      ? row.continuation_token_history.map((identity) => {
        if (typeof identity !== "string" || !/^[0-9a-f]{64}$/.test(identity)) {
          throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
        }
        return identity;
      })
      : null;
    const retry = row.next_retry_phase === null ? null : row.next_retry_phase;
    if (
      (activeStart === null) !== (activeEnd === null) ||
      (activeStart !== null && activeEnd !== null &&
        Date.parse(activeStart) > Date.parse(activeEnd)) ||
      (completed !== null && activeStart !== null &&
        Date.parse(activeStart) > Date.parse(completed)) ||
      (completed !== null && activeEnd !== null &&
        Date.parse(completed) > Date.parse(activeEnd)) ||
      page < 1 || page > (capabilityId === "white_house_sitemap" ? 2001 : 10) ||
      ids === null || ids.length > 500 ||
      new Set(ids).size !== ids.length ||
      history === null || history.length > 64 ||
      new Set(history).size !== history.length ||
      (token !== null &&
        (typeof token !== "string" || token.length < 1 || token.length > 2048 ||
          /[\x00-\x1f]/.test(token) || activeStart === null || page < 2)) ||
      (retry !== null &&
        !["pre-market", "intraday", "post-market", "on-demand"].includes(
          String(retry),
        ))
    ) {
      throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
    }
    return {
      task_key: taskKey,
      provider,
      capability_id: capabilityId,
      completed_through: completed,
      active_window_start: activeStart,
      active_window_end: activeEnd,
      backlog_token: token as string | null,
      page,
      accepted_item_ids: ids,
      next_retry_phase: retry as DiscoverySourceCursor["next_retry_phase"],
      continuation_token_history: history,
      source_run_id: strictPattern(row.source_run_id, UUID_PATTERN),
      source_task_id: strictPattern(row.source_task_id, UUID_PATTERN),
      source_updated_at: protectedTimestamp(row.source_updated_at, current)!,
    };
  });
  if (
    new Set(sourceCursors.map((cursor) => cursor.task_key)).size !==
      sourceCursors.length
  ) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  const byKey = new Map(
    sourceCursors.map((cursor) => [cursor.task_key, cursor]),
  );
  const completedScans = scanRows.map((row): DiscoveryCompletedScan => {
    if (
      !exactKeys(row, [
        "capability_id",
        "theme_id",
        "completed_through",
        "source_run_id",
        "source_task_id",
      ])
    ) throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
    const capabilityId = strictPattern(row.capability_id, CAPABILITY_PATTERN);
    const themeId = strictPattern(row.theme_id, THEME_PATTERN);
    const sourceRunId = strictPattern(row.source_run_id, UUID_PATTERN);
    const sourceTaskId = strictPattern(row.source_task_id, UUID_PATTERN);
    const completed = protectedTimestamp(row.completed_through, current)!;
    const cursor = byKey.get(`${capabilityId}:${themeId}`);
    if (
      cursor === undefined || cursor.completed_through !== completed ||
      cursor.source_run_id !== sourceRunId ||
      cursor.source_task_id !== sourceTaskId
    ) {
      throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
    }
    return {
      capability_id: capabilityId,
      theme_id: themeId,
      completed_through: completed,
      source_run_id: sourceRunId,
      source_task_id: sourceTaskId,
    };
  });
  if (
    new Set(
      completedScans.map((scan) => `${scan.capability_id}:${scan.theme_id}`),
    ).size !== completedScans.length
  ) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  return {
    source_cursors: sourceCursors,
    last_completed_scans: completedScans,
  };
}

function decimalMap(value: unknown): Record<string, string> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length > 100) {
    throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
  }
  return Object.fromEntries(entries.map(([ticker, amount]) => [
    text(ticker.toUpperCase(), 15),
    decimal(amount),
  ]));
}

type GateState = "passed" | "failed" | "missing" | "stale" | "ambiguous" |
  "unverified" | "unavailable";

function exactEnum<T extends string>(value: unknown, allowed: readonly T[]): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  return value as T;
}

function gateStateMap(value: unknown): Record<string, GateState> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length > 100) throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
  return Object.fromEntries(entries.map(([ticker, state]) => {
    if (!["passed", "failed", "missing", "stale", "ambiguous", "unverified", "unavailable"].includes(String(state))) {
      throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
    }
    return [text(ticker.toUpperCase(), 15), state as GateState];
  }));
}

function gateProvenanceMap(value: unknown): Record<string, Record<string, unknown>> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  if (new TextEncoder().encode(JSON.stringify(value)).byteLength > 32_768) {
    throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length > 100) throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
  return Object.fromEntries(entries.map(([ticker, provenance]) => [
    text(ticker.toUpperCase(), 15), oneObject({ data: provenance, error: null }),
  ]));
}

function integer(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isSafeInteger(parsed)) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  return parsed;
}

function decimal(value: unknown): string {
  const rendered = String(value);
  if (!/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(rendered)) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  return rendered;
}

function nullableDecimal(value: unknown): string | null {
  return value === null || value === undefined ? null : decimal(value);
}

function signedMicros(value: string): bigint {
  return value.startsWith("-")
    ? -parseFixed(value.slice(1), 6)
    : parseFixed(value, 6);
}

function boole(value: unknown): boolean {
  if (typeof value !== "boolean") {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  return value;
}

function alertSourceSummary(
  row: Record<string, unknown>,
): AlertSourceSummary | null {
  const confidence = nullableText(row.confidence, 10);
  if (
    confidence !== "low" && confidence !== "medium" && confidence !== "high"
  ) return null;
  return {
    ticker: text(row.ticker, 15),
    confidence,
    valid_until: nullableText(row.valid_until, 10),
    invalidation_price: nullableDecimal(row.invalidation_price),
    stop: nullableDecimal(row.stop),
    target: nullableDecimal(row.target),
    position_value_after: null,
    total_investable_value: null,
    evidence: [],
    reasons: [],
  };
}

function alertRuleFromRow(row: Record<string, unknown>): AlertRuleSnapshot {
  return parseAlertDraft({
    rule_id: text(row.id, 36),
    version: integer(row.current_version),
    state: text(row.state, 20),
    ticker: text(row.ticker, 15),
    profile: text(row.profile, 20),
    severity: text(row.severity, 20),
    session: text(row.session, 20),
    confirmation: text(row.confirmation, 20),
    conditions: row.conditions,
    cooldown_seconds: integer(row.cooldown_seconds),
    fire_limit: integer(row.fire_limit),
    valid_until: text(row.valid_until, 40),
    owner_note: text(row.owner_note, 500),
  });
}

function ownerDate(now: Date): string {
  const values = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    values.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function mergeRelevantSuggestions<T extends Record<string, unknown>>(
  unresolved: T[],
  completed: T[],
  limit: number,
): T[] {
  if (!Number.isSafeInteger(limit) || limit < 1) {
    throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
  }
  if (unresolved.length > limit) {
    throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
  }
  const output: T[] = [];
  const seen = new Set<string>();
  const orderByNewestDateAndId = (left: T, right: T): number => {
    const byDate = String(right.date ?? "").localeCompare(
      String(left.date ?? ""),
    );
    if (byDate !== 0) return byDate;
    const leftId = Number(left.id);
    const rightId = Number(right.id);
    if (Number.isSafeInteger(leftId) && Number.isSafeInteger(rightId)) {
      return rightId - leftId;
    }
    return String(right.id).localeCompare(String(left.id));
  };
  // Unresolved rows are actionably pending. History is only allowed to use
  // capacity left after those bounded rows, regardless of a newer history date.
  const ordered = [...unresolved].sort(orderByNewestDateAndId).concat(
    [...completed].sort(orderByNewestDateAndId),
  );
  for (const row of ordered) {
    const identity = String(row.id);
    if (seen.has(identity) || output.length >= limit) continue;
    seen.add(identity);
    output.push(row);
  }
  return output;
}

export function validatePolicy(value: unknown): PolicyConfig {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new GatewayRepositoryError("POLICY_REJECTED");
  }
  const policy = value as Partial<PolicyConfig>;
  if (
    (policy.version !== 1 && policy.version !== 2 && policy.version !== 3 &&
      policy.version !== 4) ||
    policy.self_tuning_enabled !== false ||
    !policy.allocation_bps || !policy.max_position_bps_of_bucket ||
    !policy.max_trade_risk_bps || !policy.request_limits ||
    !Array.isArray(policy.nyse_holidays) ||
    !Array.isArray(policy.broad_core_etfs)
  ) {
    throw new GatewayRepositoryError("POLICY_REJECTED");
  }
  if (
    policy.version === 4 &&
    (typeof policy.intelligence !== "object" || policy.intelligence === null ||
      Array.isArray(policy.intelligence))
  ) {
    throw new GatewayRepositoryError("POLICY_REJECTED");
  }
  if (policy.alerts_v3 !== undefined) {
    const alerts = policy.alerts_v3 as unknown;
    if (
      typeof alerts !== "object" || alerts === null || Array.isArray(alerts)
    ) {
      throw new GatewayRepositoryError("POLICY_REJECTED");
    }
    const row = alerts as Record<string, unknown>;
    const legacyExpected = [
      "enabled",
      "shadow",
      "profile",
      "draft_ttl_hours",
      "drafts_per_hour",
    ];
    const expected = policy.version === 3 || policy.version === 4
      ? [...legacyExpected, "enabled_classes"]
      : legacyExpected;
    const enabledClasses = policy.version === 3 || policy.version === 4
      ? row.enabled_classes
      : [];
    const supportedClasses = new Set([
      "entry_trigger",
      "stop_breach",
      "target_hit",
    ]);
    if (
      Object.keys(row).length !== expected.length || expected.some((key) =>
        !(key in row)
      ) ||
      typeof row.enabled !== "boolean" || typeof row.shadow !== "boolean" ||
      (row.enabled === true && row.shadow === true) ||
      !Array.isArray(enabledClasses) ||
      enabledClasses.some((value) =>
        typeof value !== "string" || !supportedClasses.has(value)
      ) ||
      new Set(enabledClasses).size !== enabledClasses.length ||
      (row.enabled === true && enabledClasses.length === 0) ||
      !["long_term", "balanced", "active"].includes(String(row.profile)) ||
      row.draft_ttl_hours !== 24 || row.drafts_per_hour !== 5
    ) {
      throw new GatewayRepositoryError("POLICY_REJECTED");
    }
    return {
      ...policy,
      alerts_v3: { ...row, enabled_classes: enabledClasses },
    } as PolicyConfig;
  }
  return policy as PolicyConfig;
}

async function digestCandidate(value: unknown): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(value)),
  );
  return Array.from(new Uint8Array(digest)).map((byte) =>
    byte.toString(16).padStart(2, "0")
  ).join("");
}

export function createSupabaseGatewayRepository(
  rawClient: unknown,
  now: () => Date = () => new Date(),
): GatewayRepository {
  const client = rawClient as SupabaseLike;

  async function publication(id: string): Promise<PublicationReceipt> {
    const row = oneObject(
      await client.from("market_publications")
        .select(
          "id,idempotency_key,status,telegram_message_ids,telegram_accepted_at,lease_token",
        )
        .eq("id", id).single(),
    );
    const ids = Array.isArray(row.telegram_message_ids)
      ? row.telegram_message_ids.map(integer)
      : [];
    return {
      id: text(row.id, 36),
      idempotency_key: text(row.idempotency_key, 36),
      status: text(row.status, 30) as PublicationReceipt["status"],
      telegram_message_ids: ids,
      telegram_accepted_at: nullableText(row.telegram_accepted_at, 40),
      lease_token: nullableText(row.lease_token, 36),
    };
  }

  return {
    async recordLearning(runId, payload) {
      const result = await client.rpc("record_market_learning", {
        p_run_id: runId,
        p_observation: payload,
      });
      const row = oneObject(result);
      return {
        observation_id: text(row.observation_id, 36),
        content_hash: text(row.content_hash, 64),
        duplicate: boole(row.duplicate),
      };
    },

    async loadReportDecisions(runId, packetId, decisionIds) {
      const result = await client.rpc("read_market_report_decisions", {
        p_run_id: runId,
        p_packet_id: packetId,
        p_decision_ids: decisionIds,
      });
      if (result.error) {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
      try {
        return parseReportDecisions(result.data, runId, packetId, decisionIds);
      } catch {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
    },

    async recordReport(runId, payload) {
      const result = await client.rpc("record_market_report", {
        p_run_id: runId,
        p_idempotency_key: payload.idempotency_key,
        p_report: {
          id: payload.id,
          packet_id: payload.packet_id,
          market_date: payload.market_date,
          kind: payload.kind,
          report: payload.report,
          report_hash: payload.report_hash,
          rendered_text: payload.rendered_text,
          rendered_hash: payload.rendered_hash,
        },
      });
      const row = oneObject(result);
      return {
        report_id: text(row.report_id, 36),
        report_hash: text(row.report_hash, 64),
        rendered_hash: text(row.rendered_hash, 64),
        duplicate: boole(row.duplicate),
      };
    },

    async recordReportOrigin(requestId, leaseToken, runId, payload) {
      const result = await client.rpc("record_market_report_origin", {
        p_request_id: requestId,
        p_lease_token: leaseToken,
        p_run_id: runId,
        p_market_date: payload.market_date,
        p_requested_kind: payload.kind,
        p_requested_report_id: payload.id,
        p_requested_packet_id: payload.packet_id,
        p_requested_idempotency_key: payload.idempotency_key,
        p_requested_report_hash: payload.report_hash,
      });
      const row = oneObject(result);
      return { scheduled: boole(row.scheduled) };
    },

    async createReportPublication(runId, payload) {
      const result = await client.rpc("create_market_report_publication", {
        p_run_id: runId,
        p_report_id: payload.id,
        p_idempotency_key: payload.idempotency_key,
        p_market_date: payload.market_date,
        p_kind: payload.kind,
        p_rendered_body: payload.rendered_text,
        p_rendered_hash: payload.rendered_hash,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      return {
        id: text(row.report_id, 36),
        idempotency_key: text(row.idempotency_key, 64),
        status: text(row.status, 20) as PublicationReceipt["status"],
        telegram_message_ids: Array.isArray(row.telegram_message_ids)
          ? row.telegram_message_ids.map(integer)
          : [],
        telegram_accepted_at: nullableText(row.telegram_accepted_at, 40),
        lease_token: nullableText(row.lease_token, 36),
      };
    },

    async claimReportPublication(idempotencyKey) {
      const result = await client.rpc("claim_market_report_publication", {
        p_idempotency_key: idempotencyKey,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      const receipt: PublicationReceipt = {
        id: text(row.report_id, 36),
        idempotency_key: text(row.idempotency_key, 64),
        status: text(row.status, 20) as PublicationReceipt["status"],
        telegram_message_ids: Array.isArray(row.telegram_message_ids)
          ? row.telegram_message_ids.map(integer)
          : [],
        telegram_accepted_at: nullableText(row.telegram_accepted_at, 40),
        lease_token: nullableText(row.lease_token, 36),
      };
      return {
        claimed: boole(row.claimed),
        lease_token: nullableText(row.lease_token, 36),
        receipt,
      };
    },

    async finishReportPublication(
      idempotencyKey,
      leaseToken,
      status,
      messageIds,
      error,
    ) {
      const result = await client.rpc("finish_market_report_publication", {
        p_idempotency_key: idempotencyKey,
        p_lease_token: leaseToken,
        p_status: status,
        p_message_ids: messageIds,
        p_error: error,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      return {
        id: text(row.report_id, 36),
        idempotency_key: text(row.idempotency_key, 64),
        status: text(row.status, 20) as PublicationReceipt["status"],
        telegram_message_ids: Array.isArray(row.telegram_message_ids)
          ? row.telegram_message_ids.map(integer)
          : [],
        telegram_accepted_at: nullableText(row.telegram_accepted_at, 40),
        lease_token: null,
      };
    },

    async suppressReportPublication(idempotencyKey, reason) {
      const result = await client.rpc("suppress_market_report_publication", {
        p_idempotency_key: idempotencyKey,
        p_reason: reason,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      if (row.status !== "suppressed" || row.suppression_reason !== reason) {
        throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      }
      return {
        suppression_reason: reason,
        id: text(row.report_id, 36),
        idempotency_key: text(row.idempotency_key, 64),
        status: text(row.status, 20) as PublicationReceipt["status"],
        telegram_message_ids: [],
        telegram_accepted_at: null,
        lease_token: null,
      };
    },

    async startIntelligenceRun(runId, payload) {
      const result = await client.rpc("start_market_intelligence_run", {
        p_run_id: runId,
        p_phase: payload.phase,
        p_market_date: payload.market_date,
        p_policy_version: payload.policy_version,
        p_reservation_plan: payload.reservation_plan,
        p_request_window: payload.request_window,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      try {
        return parseIntelligenceStartReceipt(result.data);
      } catch {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
    },

    async checkpointIntelligenceCollection(runId, payload) {
      const result = await client.rpc(
        "checkpoint_market_intelligence_collection",
        { p_run_id: runId, p_payload: payload },
      );
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      return {
        run_id: text(row.run_id, 36),
        cache_key: text(row.cache_key, 512),
      };
    },

    async recordDiscoveryReference(runId, payload) {
      const result = await client.rpc("record_market_discovery_reference", {
        p_run_id: runId,
        p_payload: payload,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      if (typeof row.duplicate !== "boolean") {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
      return {
        manifest_id: text(row.manifest_id, 36),
        security_revision_count: integer(row.security_revision_count),
        duplicate: row.duplicate,
      };
    },

    async checkpointDiscoveryStage(runId, payload) {
      const result = await client.rpc("checkpoint_market_discovery_stage", {
        p_run_id: runId,
        p_payload: payload,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      if (typeof row.duplicate !== "boolean") {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
      try {
        return {
          task: parseDiscoveryStageTask(row.task),
          duplicate: row.duplicate,
        };
      } catch {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
    },

    async recordThemeEpisodeRevisionV2(runId, payload) {
      const result = await client.rpc("record_theme_episode_revision_v2", {
        p_run_id: runId,
        p_revision: payload,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      return oneObject(result);
    },

    async recordResearchReviewIdentityV2(runId, receiptId, payload) {
      const result = await client.rpc("record_research_review_identity_v2", {
        p_run_id: runId,
        p_receipt_id: receiptId,
        p_review: payload,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      return oneObject(result);
    },

    async recordResearchNominations(runId, requestId, payload) {
      const result = await client.rpc("record_research_nominations", {
        p_run_id: runId,
        p_request_id: requestId,
        p_payload: payload,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      if (
        typeof row.duplicate !== "boolean" ||
        !Number.isSafeInteger(row.accepted_count) ||
        (row.accepted_count as number) < 1 || (row.accepted_count as number) > 3 ||
        !Array.isArray(row.nominations)
      ) throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      return row;
    },

    async transitionResearchNominationV2(runId, nominationId, payload) {
      const result = await client.rpc("transition_research_nomination_v2", {
        p_run_id: runId,
        p_nomination_id: nominationId,
        p_payload: payload,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      return oneObject(result);
    },

    async sealEnrichmentSelection(runId, payload) {
      const result = await client.rpc("seal_market_enrichment_selection", {
        p_run_id: runId,
        p_payload: payload,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      if (typeof row.duplicate !== "boolean") {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
      return {
        manifest_id: text(row.manifest_id, 36),
        request_count: integer(row.request_count),
        duplicate: row.duplicate,
      };
    },

    async readDiscoveryContext(runId, limit) {
      const result = await client.rpc("read_market_discovery_context", {
        p_run_id: runId,
        p_limit: limit,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      try {
        return parseDiscoveryContext(result.data);
      } catch {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
    },

    async beginDiscoveryReference(runId, payload, claim) {
      const result = await client.rpc("begin_market_discovery_reference", {
        p_run_id: runId,
        p_payload: payload,
        p_request_id: claim.request_id,
        p_encoded_bytes: claim.encoded_bytes,
        p_request_hash: claim.request_hash,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      return {
        manifest_id: text(row.manifest_id, 36),
        predecessor_manifest_id: nullableText(row.predecessor_manifest_id, 36),
        duplicate: boole(row.duplicate),
      };
    },

    async recordDiscoveryReferenceChunk(runId, payload, claim) {
      const result = await client.rpc(
        "record_market_discovery_reference_chunk",
        {
          p_run_id: runId,
          p_payload: payload,
          p_request_id: claim.request_id,
          p_encoded_bytes: claim.encoded_bytes,
          p_request_hash: claim.request_hash,
        },
      );
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      return {
        manifest_id: text(row.manifest_id, 36),
        chunk_index: integer(row.chunk_index),
        duplicate: boole(row.duplicate),
      };
    },

    async finalizeDiscoveryReference(runId, payload, claim) {
      const result = await client.rpc("finalize_market_discovery_reference", {
        p_run_id: runId,
        p_payload: payload,
        p_request_id: claim.request_id,
        p_encoded_bytes: claim.encoded_bytes,
        p_request_hash: claim.request_hash,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      return {
        manifest_id: text(row.manifest_id, 36),
        security_count: integer(row.security_count),
        duplicate: boole(row.duplicate),
      };
    },

    async pinDiscoveryReference(runId, payload, claim) {
      const result = await client.rpc("pin_market_discovery_reference", {
        p_run_id: runId,
        p_payload: payload,
        p_request_id: claim.request_id,
        p_encoded_bytes: claim.encoded_bytes,
        p_request_hash: claim.request_hash,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      return {
        binding_role: text(row.binding_role, 11),
        manifest_id: nullableText(row.manifest_id, 36),
        reference_status: text(row.reference_status, 32),
        source_retrieved_at: nullableText(row.source_retrieved_at, 40),
        reference_age_seconds: row.reference_age_seconds === null
          ? null
          : integer(row.reference_age_seconds),
        duplicate: boole(row.duplicate),
      };
    },

    async readDiscoveryReference(runId, payload, claim) {
      const result = await client.rpc("read_market_discovery_reference", {
        p_run_id: runId,
        p_payload: payload,
        p_request_id: claim.request_id,
        p_encoded_bytes: claim.encoded_bytes,
        p_request_hash: claim.request_hash,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      try {
        return parseReferencePage(result.data);
      } catch {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
    },

    async readIntelligenceCompletion(runId, completionId) {
      const result = await client.rpc("read_market_intelligence_completion", {
        p_run_id: runId,
        p_completion_id: completionId,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      return result.data === null ? null : oneObject(result);
    },

    async claimIntelligenceQuote(runId, input) {
      const result = await client.rpc("claim_market_intelligence_quote", {
        p_run_id: runId,
        p_input: input,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      return oneObject(result);
    },

    async recordIntelligenceQuote(runId, receiptId, quote, checkpoint) {
      const result = await client.rpc("record_market_intelligence_quote", {
        p_run_id: runId,
        p_receipt_id: receiptId,
        p_quote: quote,
        p_checkpoint: checkpoint,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      return oneObject(result);
    },

    async recordIntelligence(runId, completionId, payload) {
      const result = await client.rpc("record_market_intelligence", {
        p_run_id: runId,
        p_completion_id: completionId,
        p_payload: payload,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      try {
        return parseIntelligenceRecordReceipt(result.data);
      } catch {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
    },

    async loadIntelligencePacket(packetId, runId) {
      const result = await client.rpc("read_market_evidence_packet", {
        p_packet_id: packetId,
        p_run_id: runId,
      });
      if (result.error || result.data === null) {
        throw new GatewayRepositoryError("INTELLIGENCE_PACKET_MISMATCH");
      }
      try {
        const packetRow = oneObject(result, "INTELLIGENCE_PACKET_MISMATCH");
        if (
          !Array.isArray(packetRow.exposure_facts) ||
          packetRow.exposure_facts.length > 96
        ) {
          throw new Error("invalid exposure facts");
        }
        const exposureKinds = new Set([
          "filing",
          "contract",
          "backlog",
          "revenue",
          "capacity",
          "official_fund",
        ]);
        const exposure_facts = packetRow.exposure_facts.map((value) => {
          if (
            typeof value !== "object" || value === null || Array.isArray(value)
          ) {
            throw new Error("invalid exposure fact");
          }
          const fact = value as Record<string, unknown>;
          if (
            Object.keys(fact).length !== 6 ||
            ![
              "candidate_key",
              "evidence_id",
              "exposure_kind",
              "status",
              "observed_at",
              "retrieved_at",
            ]
              .every((key) => key in fact) ||
            !exposureKinds.has(String(fact.exposure_kind)) ||
            !["fresh", "stale"].includes(String(fact.status))
          ) throw new Error("invalid exposure fact");
          return {
            candidate_key: text(fact.candidate_key, 80),
            evidence_id: text(fact.evidence_id, 36),
            exposure_kind: String(
              fact.exposure_kind,
            ) as PersistedExposureFact["exposure_kind"],
            status: String(fact.status) as PersistedExposureFact["status"],
            observed_at: nullableText(fact.observed_at, 40),
            retrieved_at: text(fact.retrieved_at, 40),
          };
        });
        return {
          id: text(packetRow.id, 36),
          run_id: text(packetRow.run_id, 36),
          content_hash: text(packetRow.packet_hash, 64),
          packet: parseEvidencePacket(packetRow.packet),
          evidence_facts: parseTrustedEvidenceFacts(packetRow.evidence_facts),
          exposure_facts,
        };
      } catch {
        throw new GatewayRepositoryError("INTELLIGENCE_PACKET_MISMATCH");
      }
    },

    async claimRequest(envelope) {
      const result = await client.rpc("claim_market_gateway_request", {
        p_request_id: envelope.request_id,
        p_operation: envelope.operation,
        p_run_id: envelope.run_id,
      });
      if (result.error) {
        if (
          result.error.code === "54000" ||
          result.error.message?.includes("rate limit")
        ) {
          throw new GatewayRepositoryError("RATE_LIMITED");
        }
        throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      }
      const row = oneObject(result);
      if (row.claimed === true) {
        return {
          duplicate: false,
          in_progress: false,
          lease_token: text(row.lease_token, 36),
        };
      }
      if (row.status === "REQUEST_IN_PROGRESS") {
        return { duplicate: false, in_progress: true, lease_token: null };
      }
      return {
        duplicate: true,
        in_progress: false,
        lease_token: null,
        response: row.response,
      };
    },

    async completeRequest(requestId, leaseToken, response) {
      const result = await client.rpc("complete_market_gateway_request", {
        p_request_id: requestId,
        p_lease_token: leaseToken,
        p_status: "completed",
        p_response: response,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
    },

    async failRequest(requestId, leaseToken, code) {
      const result = await client.rpc("complete_market_gateway_request", {
        p_request_id: requestId,
        p_lease_token: leaseToken,
        p_status: "failed",
        p_response: { ok: false, code },
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
    },

    async startRun(requestId, leaseToken, phase, marketDate) {
      const result = await client.rpc("start_market_analysis_run", {
        p_request_id: requestId,
        p_lease_token: leaseToken,
        p_kind: phase,
        p_market_date: marketDate,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      return {
        run_id: text(row.run_id, 36),
        duplicate: boole(row.duplicate),
      };
    },

    async recordRunOutcome(requestId, leaseToken, runId, outcome) {
      const result = await client.rpc("record_market_run_outcome", {
        p_request_id: requestId,
        p_lease_token: leaseToken,
        p_run_id: runId,
        p_outcome: outcome,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      if (row.outcome !== "no_trigger" && row.outcome !== "not_actionable") {
        throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
      }
      return {
        run_id: text(row.run_id, 36),
        outcome: row.outcome,
        duplicate: boole(row.duplicate),
      };
    },

    async activePolicy() {
      const policyRows = rows(
        await client.from("market_policy_config")
          .select("version,config").eq("active", true).limit(2),
        "POLICY_REJECTED",
      );
      if (policyRows.length !== 1) {
        throw new GatewayRepositoryError("POLICY_REJECTED");
      }
      const policy = validatePolicy(policyRows[0].config);
      if (policyRows[0].version !== policy.version) {
        throw new GatewayRepositoryError("POLICY_REJECTED");
      }
      return policy;
    },

    async createAlertDrafts(requestId, drafts) {
      if (drafts.length > 5) {
        throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
      }
      const row = oneObject(
        await client.rpc("create_market_alert_drafts", {
          p_request_id: requestId,
          p_drafts: drafts,
        }),
      );
      return {
        created_count: integer(row.created_count),
        draft_ids: Array.isArray(row.draft_ids)
          ? row.draft_ids.map((id) => text(id, 36))
          : [],
      };
    },

    async expireAlertRules() {
      const row = oneObject(await client.rpc("expire_market_alert_rules"));
      return integer(row.expired_count);
    },

    async readAlertWork(limit) {
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 20) {
        throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
      }
      const current = now();
      const [ruleResult, pendingDraftResult] = await Promise.all([
        client.from("market_alert_rules")
          .select(
            "id,source_draft_id,current_version,state,ticker,profile,severity,session,confirmation,conditions,cooldown_seconds,fire_limit,valid_until,owner_note",
          )
          .eq("state", "active").gte("valid_until", current.toISOString())
          .order("updated_at", { ascending: true }).limit(limit + 1),
        client.from("market_alert_drafts")
          .select(
            "id,source_evaluation_id,rule_snapshot,state,expires_at,publication_id",
          )
          .eq("state", "draft").is("publication_id", null).gte(
            "expires_at",
            current.toISOString(),
          )
          .order("created_at", { ascending: true }).limit(limit + 1),
      ]);
      const ruleRows = rows(ruleResult, "CONTEXT_TOO_LARGE");
      const pendingDrafts = rows(pendingDraftResult, "CONTEXT_TOO_LARGE");
      if (ruleRows.length > limit || pendingDrafts.length > limit) {
        throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
      }
      const ruleDraftIds = ruleRows.map((row) => text(row.source_draft_id, 36));
      const sourceDrafts = ruleDraftIds.length === 0 ? [] : rows(
        await client.from("market_alert_drafts")
          .select(
            "id,source_evaluation_id,rule_snapshot,state,expires_at,publication_id",
          )
          .in("id", ruleDraftIds).limit(limit),
        "CONTEXT_TOO_LARGE",
      );
      const allDrafts = new Map<string, Record<string, unknown>>();
      for (const row of [...sourceDrafts, ...pendingDrafts]) {
        allDrafts.set(text(row.id, 36), row);
      }
      const evaluationIds = [
        ...new Set(
          [...allDrafts.values()].map((row) =>
            text(row.source_evaluation_id, 36)
          ),
        ),
      ];
      const suggestionRows = evaluationIds.length === 0 ? [] : rows(
        await client.from("suggestions")
          .select(
            "evaluation_id,ticker,confidence,valid_until,invalidation_price,stop,target",
          )
          .in("evaluation_id", evaluationIds).order("date", {
            ascending: false,
          })
          .limit(evaluationIds.length + 1),
        "CONTEXT_TOO_LARGE",
      );
      if (suggestionRows.length > evaluationIds.length) {
        throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
      }
      const summaries = new Map<string, AlertSourceSummary>();
      for (const row of suggestionRows) {
        const summary = alertSourceSummary(row);
        if (summary && typeof row.evaluation_id === "string") {
          summaries.set(row.evaluation_id, summary);
        }
      }
      const ruleIds = ruleRows.map((row) => text(row.id, 36));
      const eventRows = ruleIds.length === 0 ? [] : rows(
        await client.from("market_alert_events")
          .select("rule_id,fingerprint,status,evaluated_at")
          .in("rule_id", ruleIds)
          .gte(
            "evaluated_at",
            new Date(current.valueOf() - 7 * 24 * 60 * 60_000).toISOString(),
          )
          .order("evaluated_at", { ascending: false }).limit(201),
        "CONTEXT_TOO_LARGE",
      );
      if (eventRows.length > 200) {
        throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
      }
      const workItem = (
        alertRule: AlertRuleSnapshot,
        draft: Record<string, unknown>,
      ): AlertWorkItem => ({
        rule: alertRule,
        recent_events: eventRows.filter((event) =>
          event.rule_id === alertRule.rule_id
        ).map((event) => ({
          fingerprint: text(event.fingerprint, 64),
          status: text(event.status, 30) as AlertRecentEvent["status"],
          evaluated_at: text(event.evaluated_at, 40),
          severity: alertRule.severity,
        })),
        source_summary: summaries.get(text(draft.source_evaluation_id, 36)) ??
          null,
      });
      return {
        rules: ruleRows.map((row) => {
          const draft = allDrafts.get(text(row.source_draft_id, 36));
          if (!draft) {
            throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
          }
          return workItem(alertRuleFromRow(row), draft);
        }),
        drafts: pendingDrafts.map((draft) =>
          workItem(parseAlertDraft(draft.rule_snapshot), draft)
        ),
      };
    },

    async recordAlertEvaluations(requestId, events) {
      if (events.length > 20) {
        throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
      }
      const row = oneObject(
        await client.rpc("record_market_alert_evaluations", {
          p_request_id: requestId,
          p_evaluations: events,
        }),
      );
      return {
        event_count: integer(row.event_count),
        event_ids: Array.isArray(row.event_ids)
          ? row.event_ids.map((id) => text(id, 36))
          : [],
      };
    },

    async createAlertPublication(requestId, input) {
      const row = oneObject(
        await client.rpc("create_market_alert_publication", {
          p_request_id: requestId,
          p_publication_id: input.id,
          p_market_date: input.market_date,
          p_kind: input.kind,
          p_rendered_body: input.rendered_body,
          p_rendered_hash: input.rendered_hash,
          p_event_ids: input.event_ids,
          p_draft_id: input.draft_id,
        }),
      );
      return await publication(text(row.publication_id, 36));
    },

    async readContext(_runId) {
      const current = now();
      const today = ownerDate(current);
      const intelligenceResult = _runId
        ? await client.rpc("refresh_market_intelligence_context", {
          p_run_id: _runId,
        })
        : { data: null, error: null };
      const passiveResults = await runContextQueries([
        () => client.from("holdings").select(
          "ticker,shares,avg_cost,bucket,stop,target,high_water_price,hold_override_until,stop_alert_active,stop_near_alert_active,target_near_alert_active,target_alert_active",
        ).order("ticker").limit(101),
        () => client.from("suggestions").select(
          "id,date,ticker,action,bucket,confidence,score,stop,target,invalidation_price,valid_until,evidence_as_of",
        ).eq("decision_source", "gateway")
          .or(`valid_until.is.null,valid_until.gte.${today}`)
          .order("date", { ascending: false }).order("id", { ascending: false })
          .limit(101),
        () => client.from("suggestions").select(
          "id,date,ticker,action,bucket,confidence,score,stop,target,invalidation_price,valid_until,evidence_as_of",
        ).eq("decision_source", "gateway").lt("valid_until", today)
          .order("date", { ascending: false }).order("id", { ascending: false })
          .limit(100),
        () => client.from("stock_observations").select(
          "id,ticker,obs_date,event_type,summary,price_reaction,confidence,source",
        ).order("obs_date", { ascending: false }).limit(100),
        () => client.from("lessons").select("id,entry_date,category,content").order(
          "entry_date",
          { ascending: false },
        ).limit(40),
        () => client.from("radar").select(
          "ticker,added,last_seen,days_relevant,reason,bucket_guess,promoted,promoted_on",
        ).order("last_seen", { ascending: false }).limit(20),
        () => recentRecommendationGrades(client),
        () => client.from("owner_investment_plans").select(
          "id,ticker,bucket,amount,cadence,next_due_on,active,updated_at",
        ).eq("active", true).limit(21),
        () => client.from("paper_watches").select(
          "id,ticker,created,entry_ref_price,target_price,hypothetical_amount,thesis,horizon,agent_view_at_open,agent_score_at_open",
        ).eq("status", "active").order("created", { ascending: false }).limit(
          51,
        ),
        () => client.from("dry_powder").select(
          "month,growth_available,spec_available,rolled_months",
        ).order("month", { ascending: false }).limit(12),
        () => client.from("transactions").select("id,side,source,executed_on").eq(
          "executed_on",
          today,
        ).eq("side", "sell").limit(501),
        () => client.from("portfolio_commands").select(
          "id,operation,status,executed_on,realized_pnl",
        ).eq("executed_on", today).eq("operation", "sell").eq(
          "status",
          "applied",
        ).limit(501),
        () => client.rpc("read_reconciled_cash_snapshot", {
          p_now: current.toISOString(),
        }),
        () => _runId
          ? client.rpc("read_market_discovery_cursor_context", {
            p_run_id: _runId,
            p_limit: 100,
          })
          : Promise.resolve({
            data: { source_cursors: [], last_completed_scans: [] },
            error: null,
          }),
      ]);
      const results = [
        ...passiveResults.slice(0, 12),
        intelligenceResult,
        ...passiveResults.slice(12),
      ];
      const holdings = rows(results[0], "CONTEXT_TOO_LARGE");
      const unresolvedSuggestions = rows(results[1], "CONTEXT_TOO_LARGE");
      const completedSuggestions = rows(results[2], "CONTEXT_TOO_LARGE");
      const suggestions = mergeRelevantSuggestions(
        unresolvedSuggestions,
        completedSuggestions,
        100,
      );
      const plans = rows(results[7], "CONTEXT_TOO_LARGE");
      const watches = rows(results[8], "CONTEXT_TOO_LARGE");
      const transactions = rows(results[10], "CONTEXT_TOO_LARGE");
      const commands = rows(results[11], "CONTEXT_TOO_LARGE");
      if (results[12].error) {
        throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      }
      const intelligenceInputs = results[12].data === null
        ? []
        : [oneObject(results[12])];
      const memory = intelligenceInputs.length === 1
        ? themeMemoryContext(
          intelligenceInputs[0].theme_memory,
          intelligenceInputs[0].theme_memory_snapshot_hash,
        )
        : undefined;
      if (results[13].error) {
        throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      }
      const cashSnapshot = results[13].data === null
        ? null
        : oneObject(results[13]);
      if (results[14].error) {
        throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      }
      const cursorContext = discoveryCursorContext(results[14].data, current);
      if (
        holdings.length > 100 || unresolvedSuggestions.length > 100 ||
        plans.length > 20 ||
        watches.length > 50 || transactions.length > 500 ||
        commands.length > 500
      ) {
        throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
      }
      const coverage = transactions.length === commands.length &&
        transactions.every((row) => row.source === "telegram") &&
        commands.every((row) => row.realized_pnl !== null);
      const pnlMicros = commands.reduce((sum, row) => {
        const value = nullableDecimal(row.realized_pnl);
        return value === null ? sum : sum + signedMicros(value);
      }, 0n);
      const gradeRows = rows(results[6], "CONTEXT_TOO_LARGE");
      const consecutiveLosses = consecutiveRecommendationLosses(gradeRows);
      const context: GatewayReadContext = {
        holdings: holdings.map((row) => ({
          ticker: text(row.ticker, 15),
          shares: decimal(row.shares),
          avg_cost: decimal(row.avg_cost),
          bucket: nullableText(
            row.bucket,
            20,
          ) as GatewayReadContext["holdings"][number]["bucket"],
          stop: nullableDecimal(row.stop),
          target: nullableDecimal(row.target),
          high_water_price: nullableDecimal(row.high_water_price),
          hold_override_until: nullableText(row.hold_override_until, 10),
          stop_alert_active: boole(row.stop_alert_active ?? false),
          stop_near_alert_active: boole(row.stop_near_alert_active ?? false),
          target_near_alert_active: boole(
            row.target_near_alert_active ?? false,
          ),
          target_alert_active: boole(row.target_alert_active ?? false),
        })),
        holding_quotes: {},
        intelligence_collection_context: intelligenceInputs.length === 1
          ? {
            holding_market_values: decimalMap(
              intelligenceInputs[0].holding_market_values,
            ),
            liquidity_by_ticker: decimalMap(
              intelligenceInputs[0].liquidity_by_ticker,
            ),
            overlap_by_ticker: decimalMap(
              intelligenceInputs[0].overlap_by_ticker,
            ),
            valuation_status: exactEnum(intelligenceInputs[0].valuation_status, ["unavailable"] as const),
            valuation_state_by_ticker: gateStateMap(intelligenceInputs[0].valuation_state_by_ticker),
            valuation_provenance_by_ticker: gateProvenanceMap(intelligenceInputs[0].valuation_provenance_by_ticker),
            liquidity_state_by_ticker: gateStateMap(intelligenceInputs[0].liquidity_state_by_ticker),
            liquidity_provenance_by_ticker: gateProvenanceMap(intelligenceInputs[0].liquidity_provenance_by_ticker),
            overlap_state_by_ticker: gateStateMap(intelligenceInputs[0].overlap_state_by_ticker),
            overlap_provenance_by_ticker: gateProvenanceMap(intelligenceInputs[0].overlap_provenance_by_ticker),
            current_reference_state: exactEnum(intelligenceInputs[0].current_reference_state, ["current", "stale", "ambiguous", "unavailable"] as const),
            current_reference_provenance: oneObject({ data: intelligenceInputs[0].current_reference_provenance, error: null }),
            current_quotes: Object.fromEntries(
              Object.entries(
                oneObject({
                  data: intelligenceInputs[0].current_quotes ?? {},
                  error: null,
                }),
              ).map(([ticker, raw]) => {
                const quote = oneObject({ data: raw, error: null });
                return [ticker, {
                  price: decimal(quote.price),
                  as_of: text(quote.as_of, 40),
                  expires_at: text(quote.expires_at, 40),
                  receipt_id: text(quote.receipt_id, 36),
                }];
              }),
            ),
            quote_receipt_ids:
              Array.isArray(intelligenceInputs[0].quote_receipt_ids)
                ? intelligenceInputs[0].quote_receipt_ids.map((id) =>
                  text(id, 36)
                )
                : [],
            portfolio_revision: text(
              intelligenceInputs[0].portfolio_revision,
              256,
            ),
            portfolio_valuation_complete: boole(
              intelligenceInputs[0].portfolio_valuation_complete,
            ),
            cash_revision: text(intelligenceInputs[0].cash_revision, 256),
            source_cursors: cursorContext.source_cursors,
            last_completed_scans: cursorContext.last_completed_scans,
            theme_memory: memory,
            urgent_events: memory?.urgent_events,
            high_materiality_themes: memory?.high_materiality_themes,
          }
          : _runId
          ? {
            holding_market_values: {},
            liquidity_by_ticker: {},
            overlap_by_ticker: {},
            valuation_status: "unavailable",
            valuation_state_by_ticker: {},
            valuation_provenance_by_ticker: {},
            liquidity_state_by_ticker: {},
            liquidity_provenance_by_ticker: {},
            overlap_state_by_ticker: {},
            overlap_provenance_by_ticker: {},
            current_reference_state: "unavailable",
            current_reference_provenance: {},
            current_quotes: {},
            quote_receipt_ids: [],
            portfolio_revision: "",
            portfolio_valuation_complete: false,
            cash_revision: "",
            source_cursors: cursorContext.source_cursors,
            last_completed_scans: cursorContext.last_completed_scans,
          }
          : undefined,
        realized_pnl_today: coverage ? formatFixed(pnlMicros, 6) : null,
        portfolio_command_coverage_complete: coverage,
        consecutive_completed_losses: consecutiveLosses,
        owner_plans: plans.map((row) => ({
          id: text(row.id, 36),
          ticker: text(row.ticker, 15),
          bucket: "core",
          amount: decimal(row.amount),
          cadence: "monthly",
          next_due_on: text(row.next_due_on, 10),
          active: boole(row.active),
          updated_at: text(row.updated_at, 40),
        })),
        ...(cashSnapshot
          ? {
            reconciled_cash_snapshot: {
              snapshot_id: text(cashSnapshot.snapshot_id, 36),
              as_of: text(cashSnapshot.as_of, 40),
              fresh_through: text(cashSnapshot.fresh_through, 40),
              ledger_watermark: text(cashSnapshot.ledger_watermark, 30),
              spendable_cash: {
                core: decimal(
                  oneObject({ data: cashSnapshot.spendable_cash, error: null })
                    .core,
                ),
                growth: decimal(
                  oneObject({ data: cashSnapshot.spendable_cash, error: null })
                    .growth,
                ),
                speculative: decimal(
                  oneObject({ data: cashSnapshot.spendable_cash, error: null })
                    .speculative,
                ),
              },
            },
          }
          : {}),
        recent_suggestions: suggestions.map((row) => ({
          id: integer(row.id),
          date: text(row.date, 10),
          ticker: text(row.ticker, 15),
          action: text(
            row.action,
            10,
          ) as GatewayReadContext["recent_suggestions"][number]["action"],
          bucket: text(
            row.bucket,
            20,
          ) as GatewayReadContext["recent_suggestions"][number]["bucket"],
          confidence: text(
            row.confidence,
            10,
          ) as GatewayReadContext["recent_suggestions"][number]["confidence"],
          score: row.score === null ? null : integer(row.score),
          stop: nullableDecimal(row.stop),
          target: nullableDecimal(row.target),
          invalidation_price: nullableDecimal(row.invalidation_price),
          valid_until: nullableText(row.valid_until, 10),
          evidence_as_of: nullableText(row.evidence_as_of, 40),
        })),
        observations: rows(results[3], "CONTEXT_TOO_LARGE").map((row) => ({
          id: integer(row.id),
          ticker: text(row.ticker, 15),
          obs_date: text(row.obs_date, 10),
          event_type: nullableText(row.event_type, 100),
          summary: text(row.summary, 1000),
          price_reaction: nullableText(row.price_reaction, 500),
          confidence: nullableText(row.confidence, 20),
          source: nullableText(row.source, 200),
        })),
        lessons: rows(results[4], "CONTEXT_TOO_LARGE").map((row) => ({
          id: integer(row.id),
          entry_date: text(row.entry_date, 10),
          category: text(row.category, 100),
          content: text(row.content, 1000),
        })),
        radar: rows(results[5], "CONTEXT_TOO_LARGE").map((row) => ({
          ticker: text(row.ticker, 15),
          added: nullableText(row.added, 10),
          last_seen: nullableText(row.last_seen, 10),
          days_relevant: row.days_relevant === null
            ? null
            : integer(row.days_relevant),
          reason: nullableText(row.reason, 1000),
          bucket_guess: nullableText(
            row.bucket_guess,
            20,
          ) as GatewayReadContext["radar"][number]["bucket_guess"],
          promoted: boole(row.promoted ?? false),
          promoted_on: nullableText(row.promoted_on, 10),
        })),
        recent_grades: gradeRows.map((row) => ({
          suggestion_id: integer(row.suggestion_id),
          horizon_days: integer(row.horizon_days),
          coverage_status: nullableText(row.coverage_status, 30),
          excess_return_pct: nullableDecimal(row.excess_return_pct),
          direction_success: row.direction_success === null
            ? null
            : boole(row.direction_success),
        })),
        dry_powder: rows(results[9], "CONTEXT_TOO_LARGE").map((row) => ({
          month: text(row.month, 7),
          growth_available: decimal(row.growth_available),
          spec_available: decimal(row.spec_available),
          rolled_months: integer(row.rolled_months),
        })),
        paper_watches: watches.map((row) => ({
          id: integer(row.id),
          ticker: text(row.ticker, 15),
          created: text(row.created, 10),
          entry_ref_price: decimal(row.entry_ref_price),
          target_price: nullableDecimal(row.target_price),
          hypothetical_amount: nullableDecimal(row.hypothetical_amount),
          thesis: text(row.thesis, 1000),
          horizon: text(row.horizon, 100),
          agent_view_at_open: text(
            row.agent_view_at_open,
            20,
          ) as GatewayReadContext["paper_watches"][number][
            "agent_view_at_open"
          ],
          agent_score_at_open: row.agent_score_at_open === null
            ? null
            : integer(row.agent_score_at_open),
        })),
      };
      assertContextFitsTransport(context);
      return context;
    },

    async recordArtifacts(requestId, runId, leaseToken, payload) {
      const result = await client.rpc("apply_market_artifacts", {
        p_request_id: requestId,
        p_run_id: runId,
        p_lease_token: leaseToken,
        p_mutations: payload.mutations,
      });
      const row = oneObject(result);
      return {
        counts: oneObject({
          data: row.counts,
          error: null,
        }) as ArtifactReceipt["counts"],
        created_paper_watch_ids: Array.isArray(row.paper_watch_ids)
          ? row.paper_watch_ids.map(integer)
          : [],
      };
    },

    async dueDecisions(limit) {
      const result = await client.rpc("get_due_market_decisions", {
        p_limit: limit,
      });
      return rows(result).map((row) => ({
        suggestion_id: integer(row.suggestion_id),
        decision_date: text(row.decision_date, 10),
        ticker: text(row.ticker, 15),
        bucket: text(row.bucket, 20) as DueDecision["bucket"],
        final_action: text(row.final_action, 10) as DueDecision["final_action"],
        confidence: text(row.confidence, 10) as DueDecision["confidence"],
        policy_version: integer(row.policy_version),
        decision_price: decimal(row.decision_price),
        entry_zone_low: nullableDecimal(row.entry_zone_low),
        entry_zone_high: nullableDecimal(row.entry_zone_high),
        stop: nullableDecimal(row.stop),
        target: nullableDecimal(row.target),
        invalidation_price: nullableDecimal(row.invalidation_price),
        completed_horizons: Array.isArray(row.completed_horizons)
          ? row.completed_horizons.map(integer)
          : [],
      }));
    },

    async upsertGrades(grades) {
      if (grades.length > 150) {
        throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
      }
      const row = oneObject(
        await client.rpc("upsert_market_outcome_grades", { p_grades: grades }),
      );
      return {
        inserted: integer(row.inserted),
        updated: integer(row.updated),
        incomplete: integer(row.incomplete),
      };
    },

    async applyDecisionBundle(input) {
      const evaluations = await Promise.all(
        input.evaluations.map(async (evaluation) => ({
          id: evaluation.evaluation_id,
          candidate_id: evaluation.candidate_id,
          input_digest: await digestCandidate(evaluation.candidate),
          raw_action: evaluation.raw_action,
          final_action: evaluation.final_action,
          policy_status: evaluation.status,
          reason_codes: evaluation.reason_codes,
          explanations: evaluation.explanations,
          normalized: evaluation.normalized,
          evidence: evaluation.candidate.evidence,
          analyst: evaluation.candidate.analyst,
          checker: evaluation.candidate.checker,
        })),
      );
      const result = await client.rpc(
        "apply_market_decision_bundle_with_cash_snapshot",
        {
          p_request_id: input.request_id,
          p_run_id: input.run_id,
          p_lease_token: input.request_lease_token,
          p_policy_version: input.policy_version,
          p_evaluations: evaluations,
          p_suggestions: input.suggestions,
          p_publication: {
            market_date: input.publication.market_date,
            phase: input.publication.phase,
            kind: input.publication.kind,
            template_version: input.publication.template_version,
            rendered_body: input.publication.rendered_body,
            rendered_hash: input.publication.rendered_hash,
            status: input.publication.status,
            holding_state: input.holding_state_changes,
          },
          p_cash_snapshot_id: input.cash_snapshot?.snapshot_id ?? null,
          p_cash_ledger_watermark: input.cash_snapshot?.ledger_watermark ??
            null,
        },
      );
      if (result.error) {
        if (result.error.message?.includes("CASH_UNAVAILABLE")) {
          throw new GatewayRepositoryError("CASH_UNAVAILABLE");
        }
        throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      }
      const row = oneObject(result);
      if (row.code === "RUN_ALREADY_EVALUATED") {
        throw new GatewayRepositoryError("RUN_ALREADY_EVALUATED");
      }
      return await publication(text(row.publication_id, 36));
    },

    async claimPublication(idempotencyKey) {
      const row = oneObject(
        await client.rpc("claim_market_publication", {
          p_request_id: idempotencyKey,
        }),
      );
      const receipt = await publication(text(row.publication_id, 36));
      return {
        claimed: row.claimed === true,
        lease_token: nullableText(row.lease_token, 36),
        receipt,
      };
    },

    async finishPublication(
      idempotencyKey,
      leaseToken,
      status,
      messageIds,
      error,
    ) {
      const result = await client.rpc("finish_market_publication", {
        p_request_id: idempotencyKey,
        p_lease_token: leaseToken,
        p_status: status,
        p_message_ids: messageIds,
        p_error: error,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const rowsResult = rows(
        await client.from("market_publications")
          .select("id,idempotency_key,status,telegram_message_ids,lease_token")
          .eq("idempotency_key", idempotencyKey).limit(1),
      );
      if (rowsResult.length !== 1) {
        throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      }
      return await publication(text(rowsResult[0].id, 36));
    },

    async finishAlertPublication(
      idempotencyKey,
      leaseToken,
      status,
      messageIds,
      error,
      acceptedAt,
    ) {
      const result = await client.rpc("finish_market_alert_publication", {
        p_request_id: idempotencyKey,
        p_lease_token: leaseToken,
        p_status: status,
        p_message_ids: messageIds,
        p_error: error,
        p_accepted_at: acceptedAt,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const rowsResult = rows(
        await client.from("market_publications")
          .select("id").eq("idempotency_key", idempotencyKey).limit(1),
      );
      if (rowsResult.length !== 1) {
        throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      }
      return await publication(text(rowsResult[0].id, 36));
    },

    async finishRun(runId) {
      const result = await client.rpc("finish_market_analysis_run", {
        p_run_id: runId,
      });
      if (result.error) {
        const code = result.error.message;
        if (typeof code === "string" && /^MISSING_[A-Z_]+$/.test(code)) {
          throw new GatewayRepositoryError(code);
        }
        throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      }
      const row = oneObject(result);
      const writeCounts = oneObject({ data: row.write_counts, error: null });
      return {
        run_id: text(row.run_id, 36),
        status: text(row.status, 10) as RunReceipt["status"],
        write_counts: Object.fromEntries(
          Object.entries(writeCounts).map((
            [key, value],
          ) => [key, integer(value)]),
        ),
        publication_statuses: Array.isArray(row.publication_statuses)
          ? row.publication_statuses.map((value) => text(value, 30))
          : [],
        telegram_message_ids: Array.isArray(row.telegram_message_ids)
          ? row.telegram_message_ids.map(integer)
          : [],
      };
    },
  };
}
