import type {
  Action,
  AlertConditionResult,
  AlertRecentEvent,
  AlertRuleSnapshot,
  AlertSession,
  AlertSourceSummary,
  ArtifactMutation,
  EvidencePacket,
  GatewayEnvelope,
  GatewayReadContext,
  NotificationKind,
  Phase,
  PolicyConfig,
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
  type IntelligenceRecordReceipt,
  type IntelligenceStartReceipt,
  parseIntelligenceRecordReceipt,
  parseIntelligenceStartReceipt,
  type RecordIntelligencePayload,
  type StartIntelligencePayload,
} from "./intelligence.ts";
import {
  parseReportDecisions,
  type RecordReportPayload,
  type ReportPolicyDecision,
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
  suppressReportPublication?(idempotencyKey: string): Promise<PublicationReceipt>;
  startIntelligenceRun?(
    runId: string,
    payload: StartIntelligencePayload,
  ): Promise<IntelligenceStartReceipt>;
  checkpointIntelligenceCollection?(runId: string, payload: { cache_key: string; receipt: Record<string, unknown>; items: Record<string, unknown>[] }): Promise<{ run_id: string; cache_key: string }>;
  readIntelligenceCompletion?(runId: string, completionId: string): Promise<Record<string, unknown> | null>;
  claimIntelligenceQuote?(runId: string, input: Record<string, unknown>): Promise<Record<string, unknown>>;
  recordIntelligenceQuote?(runId: string, receiptId: string, quote: unknown, checkpoint: Record<string, unknown>): Promise<Record<string, unknown>>;
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
  ): Promise<string>;
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

const CONTEXT_LIMIT_BYTES = 524_288;

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

function text(value: unknown, max = 1000): string {
  if (typeof value !== "string") {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  return value.slice(0, max);
}

function nullableText(value: unknown, max = 1000): string | null {
  return value === null || value === undefined ? null : text(String(value), max);
}

function decimalMap(value: unknown): Record<string, string> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new GatewayRepositoryError("INVALID_PERSISTED_DATA");
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length > 100) throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
  return Object.fromEntries(entries.map(([ticker, amount]) => [
    text(ticker.toUpperCase(), 15), decimal(amount),
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
  return value.startsWith("-") ? -parseFixed(value.slice(1), 6) : parseFixed(value, 6);
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
  const get = (type: Intl.DateTimeFormatPartTypes) => values.find((part) => part.type === type)?.value ?? "";
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
  const output: T[] = [];
  const seen = new Set<string>();
  const orderByNewestDateAndId = (left: T, right: T): number => {
    const byDate = String(right.date ?? "").localeCompare(String(left.date ?? ""));
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
    (policy.version !== 1 && policy.version !== 2 && policy.version !== 3) ||
    policy.self_tuning_enabled !== false ||
    !policy.allocation_bps || !policy.max_position_bps_of_bucket ||
    !policy.max_trade_risk_bps || !policy.request_limits ||
    !Array.isArray(policy.nyse_holidays) ||
    !Array.isArray(policy.broad_core_etfs)
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
    const expected = policy.version === 3 ? [...legacyExpected, "enabled_classes"] : legacyExpected;
    const enabledClasses = policy.version === 3 ? row.enabled_classes : [];
    const supportedClasses = new Set([
      "entry_trigger",
      "stop_breach",
      "target_hit",
    ]);
    if (
      Object.keys(row).length !== expected.length || expected.some((key) => !(key in row)) ||
      typeof row.enabled !== "boolean" || typeof row.shadow !== "boolean" ||
      (row.enabled === true && row.shadow === true) ||
      !Array.isArray(enabledClasses) ||
      enabledClasses.some((value) => typeof value !== "string" || !supportedClasses.has(value)) ||
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
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
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
    const ids = Array.isArray(row.telegram_message_ids) ? row.telegram_message_ids.map(integer) : [];
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

    async finishReportPublication(idempotencyKey, leaseToken, status, messageIds, error) {
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

    async suppressReportPublication(idempotencyKey) {
      const result = await client.rpc("suppress_market_report_publication", {
        p_idempotency_key: idempotencyKey,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      return {
        id: text(row.report_id, 36), idempotency_key: text(row.idempotency_key, 64),
        status: text(row.status, 20) as PublicationReceipt["status"],
        telegram_message_ids: [], telegram_accepted_at: null, lease_token: null,
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
      const result = await client.rpc("checkpoint_market_intelligence_collection", { p_run_id: runId, p_payload: payload });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const row = oneObject(result);
      return { run_id: text(row.run_id, 36), cache_key: text(row.cache_key, 512) };
    },

    async readIntelligenceCompletion(runId, completionId) {
      const result = await client.rpc("read_market_intelligence_completion", {
        p_run_id: runId, p_completion_id: completionId,
      });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      return result.data === null ? null : oneObject(result);
    },

    async claimIntelligenceQuote(runId, input) {
      const result = await client.rpc("claim_market_intelligence_quote", { p_run_id: runId, p_input: input });
      if (result.error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      return oneObject(result);
    },

    async recordIntelligenceQuote(runId, receiptId, quote, checkpoint) {
      const result = await client.rpc("record_market_intelligence_quote", {
        p_run_id: runId, p_receipt_id: receiptId, p_quote: quote, p_checkpoint: checkpoint,
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
      return text(oneObject(result).run_id, 36);
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
        draft_ids: Array.isArray(row.draft_ids) ? row.draft_ids.map((id) => text(id, 36)) : [],
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
          [...allDrafts.values()].map((row) => text(row.source_evaluation_id, 36)),
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
        recent_events: eventRows.filter((event) => event.rule_id === alertRule.rule_id).map((event) => ({
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
        drafts: pendingDrafts.map((draft) => workItem(parseAlertDraft(draft.rule_snapshot), draft)),
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
        event_ids: Array.isArray(row.event_ids) ? row.event_ids.map((id) => text(id, 36)) : [],
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
      const today = ownerDate(now());
      const results = await Promise.all([
        client.from("holdings").select(
          "ticker,shares,avg_cost,bucket,stop,target,high_water_price,hold_override_until,stop_alert_active,stop_near_alert_active,target_near_alert_active,target_alert_active",
        ).order("ticker").limit(101),
        client.from("suggestions").select(
          "id,date,ticker,action,bucket,confidence,score,stop,target,invalidation_price,valid_until,evidence_as_of",
        ).eq("decision_source", "gateway")
          .or(`valid_until.is.null,valid_until.gte.${today}`)
          .order("date", { ascending: false }).order("id", { ascending: false })
          .limit(100),
        client.from("suggestions").select(
          "id,date,ticker,action,bucket,confidence,score,stop,target,invalidation_price,valid_until,evidence_as_of",
        ).eq("decision_source", "gateway").lt("valid_until", today)
          .order("date", { ascending: false }).order("id", { ascending: false })
          .limit(100),
        client.from("stock_observations").select(
          "id,ticker,obs_date,event_type,summary,price_reaction,confidence,source",
        ).order("obs_date", { ascending: false }).limit(100),
        client.from("lessons").select("id,entry_date,category,content").order(
          "entry_date",
          { ascending: false },
        ).limit(40),
        client.from("radar").select(
          "ticker,added,last_seen,days_relevant,reason,bucket_guess,promoted,promoted_on",
        ).order("last_seen", { ascending: false }).limit(20),
        client.from("suggestion_grades").select(
          "suggestion_id,horizon_days,coverage_status,excess_return_pct,direction_success,graded_at",
        ).order("graded_at", { ascending: false }).limit(150),
        client.from("owner_investment_plans").select(
          "id,ticker,bucket,amount,cadence,next_due_on,active,updated_at",
        ).eq("active", true).limit(21),
        client.from("paper_watches").select(
          "id,ticker,created,entry_ref_price,target_price,hypothetical_amount,thesis,horizon,agent_view_at_open,agent_score_at_open",
        ).eq("status", "active").order("created", { ascending: false }).limit(
          51,
        ),
        client.from("dry_powder").select(
          "month,growth_available,spec_available,rolled_months",
        ).order("month", { ascending: false }).limit(12),
        client.from("transactions").select("id,side,source,executed_on").eq(
          "executed_on",
          today,
        ).eq("side", "sell").limit(501),
        client.from("portfolio_commands").select(
          "id,operation,status,executed_on,realized_pnl",
        ).eq("executed_on", today).eq("operation", "sell").eq(
          "status",
          "applied",
        ).limit(501),
        _runId
          ? client.rpc("refresh_market_intelligence_context", { p_run_id: _runId })
          : Promise.resolve({ data: null, error: null }),
      ]);
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
      if (results[12].error) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
      const intelligenceInputs = results[12].data === null ? [] : [oneObject(results[12])];
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
      let consecutiveLosses = 0;
      for (const row of gradeRows) {
        if (
          row.coverage_status !== "complete" ||
          typeof row.direction_success !== "boolean"
        ) continue;
        if (row.direction_success) break;
        consecutiveLosses += 1;
      }
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
            holding_market_values: decimalMap(intelligenceInputs[0].holding_market_values),
            liquidity_by_ticker: decimalMap(intelligenceInputs[0].liquidity_by_ticker),
            overlap_by_ticker: decimalMap(intelligenceInputs[0].overlap_by_ticker),
            current_quotes: Object.fromEntries(Object.entries(oneObject({ data: intelligenceInputs[0].current_quotes ?? {}, error: null })).map(([ticker, raw]) => {
              const quote = oneObject({ data: raw, error: null });
              return [ticker, { price: decimal(quote.price), as_of: text(quote.as_of, 40) }];
            })),
            quote_receipt_ids: Array.isArray(intelligenceInputs[0].quote_receipt_ids)
              ? intelligenceInputs[0].quote_receipt_ids.map((id) => text(id, 36)) : [],
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
          days_relevant: row.days_relevant === null ? null : integer(row.days_relevant),
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
          direction_success: row.direction_success === null ? null : boole(row.direction_success),
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
          agent_score_at_open: row.agent_score_at_open === null ? null : integer(row.agent_score_at_open),
        })),
      };
      if (
        new TextEncoder().encode(JSON.stringify(context)).byteLength >
          CONTEXT_LIMIT_BYTES
      ) {
        throw new GatewayRepositoryError("CONTEXT_TOO_LARGE");
      }
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
        created_paper_watch_ids: Array.isArray(row.paper_watch_ids) ? row.paper_watch_ids.map(integer) : [],
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
        completed_horizons: Array.isArray(row.completed_horizons) ? row.completed_horizons.map(integer) : [],
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
      const result = await client.rpc("apply_market_decision_bundle", {
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
      });
      const row = oneObject(result);
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
      const result = await client.rpc("finish_market_analysis_run", { p_run_id: runId });
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
        write_counts: Object.fromEntries(Object.entries(writeCounts).map(([key, value]) => [key, integer(value)])),
        publication_statuses: Array.isArray(row.publication_statuses) ? row.publication_statuses.map((value) => text(value, 30)) : [],
        telegram_message_ids: Array.isArray(row.telegram_message_ids) ? row.telegram_message_ids.map(integer) : [],
      };
    },
  };
}
