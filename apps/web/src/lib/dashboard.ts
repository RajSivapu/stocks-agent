import type { SupabaseClient } from "@supabase/supabase-js";
import type { CommandInput } from "@stocks-agent/contracts";
import type { CommandReceipt } from "./app-api";

export type QuoteStatus = "fresh" | "delayed" | "stale" | "conflicting" | "unavailable";

export type Holding = {
  ticker: string;
  shares: string;
  avgCost: string;
  bucket: "core" | "growth" | "speculative" | "unclassified";
  openedAt: string | null;
  stop: string | null;
  target: string | null;
  highWaterPrice: string | null;
  holdOverrideUntil: string | null;
  projectionSequence: string;
};

export type MarketQuote = {
  ticker: string;
  price: string;
  previousClose: string | null;
  provider: string;
  asOf: string;
  retrievedAt: string;
  session: "PRE" | "REGULAR" | "POST" | "CLOSED";
  adjustmentStatus: "raw" | "adjusted" | "corporate_action_pending";
  status: QuoteStatus;
  conflictBasisPoints: number | null;
  corporateActionState: "clear" | "suspected" | "needs_review";
  alertsSuppressed: boolean;
};

export type InvestmentPlan = {
  id: string;
  ticker: string;
  bucket: "core";
  amount: string;
  cadence: "monthly";
  nextDueOn: string;
  dueDay: number;
  active: boolean;
  createdAt: string;
  updatedAt: string;
};

export type Transaction = {
  id: string;
  createdAt: string;
  ticker: string;
  eventType: "opening" | "trade" | "void";
  side: "buy" | "sell" | null;
  qty: string | null;
  price: string | null;
  fees: string;
  executedOn: string;
  ledgerSequence: string;
  bucket: "core" | "growth" | "speculative" | "unclassified" | null;
  sourceChannel: "web" | "telegram" | "operator" | "migration";
  correctsTransactionId: string | null;
};

export type AnalysisRun = {
  runId: string;
  kind: string;
  startedAt: string;
  finishedAt: string | null;
  status: string;
  dataAsOf: string | null;
  sourceStatus: Record<string, unknown>;
  symbols: string[];
  writeCounts: Record<string, unknown>;
  summary: string | null;
};

export type Recommendation = {
  id: string;
  runId: string | null;
  ticker: string;
  action: string;
  confidence: string | null;
  validUntil: string | null;
  evidenceAsOf: string | null;
};

export type PortfolioSnapshot = {
  holdings: Holding[];
  quotes: MarketQuote[];
  plans: InvestmentPlan[];
};

export type ActivitySnapshot = {
  transactions: Transaction[];
  plans: InvestmentPlan[];
  commands: CommandReceipt[];
};

export type TodaySnapshot = PortfolioSnapshot & {
  runs: AnalysisRun[];
  recommendations: Recommendation[];
};

export type EvidenceItem = {
  evidenceId: string;
  category: string;
  source: string;
  reference: string | null;
  observedAt: string | null;
  retrievedAt: string;
  revalidatedAt: string | null;
  claims: string[];
  status: string;
};

export type Outcome = {
  horizonSessions: 5 | 21 | 63;
  coverageStatus: string;
  stockReturnPct: string | null;
  benchmark: string | null;
  benchmarkReturnPct: string | null;
  excessReturnPct: string | null;
  mfePct: string | null;
  maePct: string | null;
  entryHitAt: string | null;
  stopHitAt: string | null;
  targetHitAt: string | null;
  invalidationHitAt: string | null;
  directionSuccess: boolean | null;
  gradedAt: string | null;
};

export type ResearchItem = {
  id: string;
  runId: string | null;
  marketDate: string | null;
  phase: string | null;
  runStatus: string | null;
  provider: string | null;
  model: string | null;
  createdAt: string;
  ticker: string;
  action: string;
  rawAction: string | null;
  policyStatus: string | null;
  policyReasonCodes: string[];
  policyExplanations: string[];
  analyst: Record<string, unknown>;
  checker: Record<string, unknown>;
  confidence: string | null;
  verifiedPrice: string | null;
  evidenceAsOf: string | null;
  entryZoneLow: string | null;
  entryZoneHigh: string | null;
  stop: string | null;
  target: string | null;
  invalidationPrice: string | null;
  validUntil: string | null;
  horizon: string | null;
  bucket: string | null;
  riskVerdict: string | null;
  decisiveFactor: string | null;
  reason: string | null;
  bullCase: string | null;
  bearCase: string | null;
  evidenceStatus: "fresh" | "stale" | "source_conflict" | "corporate_action_pending" | "missing";
  evidence: EvidenceItem[];
  publicationKind: string | null;
  notificationStatus: string | null;
  deliveredAt: string | null;
  telegramMessageIds: string[];
  deliveryErrorCode: string | null;
  outcomes: Outcome[];
};

export type ResearchSnapshot = { items: ResearchItem[] };

export type RunTimelineItem = {
  slotId: string | null;
  marketDate: string | null;
  phase: string;
  purpose: string;
  expectedAt: string | null;
  windowEndsAt: string | null;
  holiday: boolean;
  slotStatus: string;
  triggerStatus: string | null;
  triggerResponseStatus: number | null;
  providerSessionUrl: string | null;
  triggerStartedAt: string | null;
  triggerFinishedAt: string | null;
  runId: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  runStatus: string | null;
  dataAsOf: string | null;
  sourceStatus: Record<string, unknown>;
  symbols: string[];
  writeCounts: Record<string, unknown>;
  summary: string | null;
  provider: string | null;
  model: string | null;
  submissionStatus: string | null;
  policyStates: string[];
  evidenceStatus: "fresh" | "stale" | "source_conflict" | "missing" | "not_started";
  publicationKind: string | null;
  publicationStatus: string | null;
  deliveredAt: string | null;
  telegramMessageIds: string[];
  errorCode: string | null;
};

export type RunSnapshot = { runs: RunTimelineItem[] };

export type AgentConnection = {
  id: string;
  publicId: string;
  provider: "claude";
  credentialType: "claude_routine_v1";
  capabilities: Record<string, unknown>;
  contractVersion: number;
  status: "disabled" | "testing" | "ready" | "active" | "revoked";
  lastHandshakeAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type TelegramLinkStatus = {
  status: "active" | "revoked";
  linkedAt: string;
  revokedAt: string | null;
};

export type ConnectionsSnapshot = {
  connections: AgentConnection[];
  telegram: TelegramLinkStatus | null;
  handshakeRuns: RunTimelineItem[];
};

export type SettingsSnapshot = {
  displayName: string;
  timezone: string;
  notifyPreMarket: boolean;
  notifyIntraday: boolean;
  notifyPostMarket: boolean;
  notifyOperational: boolean;
  primaryConnectionId: string | null;
  scheduleTimezone: string;
  schedulePreMarket: boolean;
  scheduleIntraday: boolean;
  schedulePostMarket: boolean;
};

export interface DashboardRepository {
  loadToday(): Promise<TodaySnapshot>;
  loadPortfolio(): Promise<PortfolioSnapshot>;
  loadActivity(): Promise<ActivitySnapshot>;
  loadResearch(): Promise<ResearchSnapshot>;
  loadRuns(): Promise<RunSnapshot>;
  loadConnections(): Promise<ConnectionsSnapshot>;
  loadSettings(): Promise<SettingsSnapshot>;
  lookupCommand(commandId: string): Promise<CommandReceipt | null>;
}

type QueryResponse = { data: unknown; error: unknown };
type Query = PromiseLike<QueryResponse> & {
  select(columns: string): Query;
  order(column: string, options: { ascending: boolean }): Query;
  limit(count: number): Query;
  eq(column: string, value: string): Query;
  maybeSingle(): PromiseLike<QueryResponse>;
};
type ApiReader = { schema(name: "api"): { from(name: string): Query } };

function row(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("INVALID_DATA");
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  if (typeof value !== "string") throw new Error("INVALID_DATA");
  return value;
}

function nullableText(value: unknown): string | null {
  return value === null ? null : text(value);
}

function decimal(value: unknown, nullable = false): string | null {
  if (nullable && value === null) return null;
  const result = text(value);
  if (!/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(result)) throw new Error("INVALID_DATA");
  return result;
}

function integerText(value: unknown): string {
  const result = text(value);
  if (!/^\d+$/.test(result)) throw new Error("INVALID_DATA");
  return result;
}

function rows(value: QueryResponse): Record<string, unknown>[] {
  if (value.error || !Array.isArray(value.data)) throw new Error("DATA_UNAVAILABLE");
  return value.data.map(row);
}

function object(value: unknown): Record<string, unknown> {
  return row(value);
}

function objectOrEmpty(value: unknown): Record<string, unknown> {
  return value === null ? {} : object(value);
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error("INVALID_DATA");
  const result: string[] = [];
  for (let index = 0; index < value.length; index += 1) {
    const item: unknown = value[index];
    if (typeof item !== "string") throw new Error("INVALID_DATA");
    result.push(item);
  }
  return result;
}

function nullableBoolean(value: unknown): boolean | null {
  if (value === null || typeof value === "boolean") return value;
  throw new Error("INVALID_DATA");
}

function nullableSafeInteger(value: unknown): number | null {
  if (value === null) return null;
  if (!Number.isSafeInteger(value)) throw new Error("INVALID_DATA");
  return value as number;
}

function boolean(value: unknown): boolean {
  if (typeof value !== "boolean") throw new Error("INVALID_DATA");
  return value;
}

function holding(value: Record<string, unknown>): Holding {
  const bucket = text(value.bucket);
  if (!["core", "growth", "speculative", "unclassified"].includes(bucket)) throw new Error("INVALID_DATA");
  return {
    ticker: text(value.ticker), shares: decimal(value.shares) ?? "0",
    avgCost: decimal(value.avg_cost) ?? "0",
    bucket: bucket as Holding["bucket"], openedAt: nullableText(value.opened_at),
    stop: decimal(value.stop, true), target: decimal(value.target, true),
    highWaterPrice: decimal(value.high_water_price, true),
    holdOverrideUntil: nullableText(value.hold_override_until),
    projectionSequence: integerText(value.projection_sequence),
  };
}

function quote(value: Record<string, unknown>): MarketQuote {
  const status = text(value.status) as QuoteStatus;
  const session = text(value.session) as MarketQuote["session"];
  const adjustment = text(value.adjustment_status) as MarketQuote["adjustmentStatus"];
  const action = text(value.corporate_action_state) as MarketQuote["corporateActionState"];
  if (!(["fresh", "delayed", "stale", "conflicting", "unavailable"] as string[]).includes(status) ||
      !(["PRE", "REGULAR", "POST", "CLOSED"] as string[]).includes(session) ||
      !(["raw", "adjusted", "corporate_action_pending"] as string[]).includes(adjustment) ||
      !(["clear", "suspected", "needs_review"] as string[]).includes(action) ||
      typeof value.alerts_suppressed !== "boolean") throw new Error("INVALID_DATA");
  const conflict = value.conflict_basis_points;
  if (conflict !== null && (!Number.isSafeInteger(conflict) || (conflict as number) < 0)) throw new Error("INVALID_DATA");
  return {
    ticker: text(value.ticker), price: decimal(value.price) ?? "0",
    previousClose: decimal(value.previous_close, true), provider: text(value.provider),
    asOf: text(value.as_of), retrievedAt: text(value.retrieved_at), session,
    adjustmentStatus: adjustment, status, conflictBasisPoints: conflict as number | null,
    corporateActionState: action, alertsSuppressed: value.alerts_suppressed,
  };
}

function plan(value: Record<string, unknown>): InvestmentPlan {
  if (value.bucket !== "core" || value.cadence !== "monthly" ||
      typeof value.active !== "boolean" || !Number.isSafeInteger(value.due_day)) {
    throw new Error("INVALID_DATA");
  }
  return {
    id: text(value.id), ticker: text(value.ticker), bucket: "core",
    amount: decimal(value.amount) ?? "0", cadence: "monthly",
    nextDueOn: text(value.next_due_on), dueDay: value.due_day as number,
    active: value.active, createdAt: text(value.created_at), updatedAt: text(value.updated_at),
  };
}

function transaction(value: Record<string, unknown>): Transaction {
  const eventType = text(value.event_type) as Transaction["eventType"];
  const side = nullableText(value.side) as Transaction["side"];
  const bucket = nullableText(value.bucket) as Transaction["bucket"];
  const channel = text(value.source_channel) as Transaction["sourceChannel"];
  if (!(["opening", "trade", "void"] as string[]).includes(eventType) ||
      (side !== null && !["buy", "sell"].includes(side)) ||
      (bucket !== null && !["core", "growth", "speculative", "unclassified"].includes(bucket)) ||
      !["web", "telegram", "operator", "migration"].includes(channel)) throw new Error("INVALID_DATA");
  return {
    id: text(value.id), createdAt: text(value.created_at), ticker: text(value.ticker),
    eventType, side, qty: decimal(value.qty, true), price: decimal(value.price, true),
    fees: decimal(value.fees) ?? "0", executedOn: text(value.executed_on),
    ledgerSequence: integerText(value.ledger_sequence), bucket, sourceChannel: channel,
    correctsTransactionId: nullableText(value.corrects_transaction_id),
  };
}

function command(value: Record<string, unknown>): CommandReceipt {
  const operation = text(value.operation) as CommandInput["operation"];
  const status = text(value.status) as CommandReceipt["status"];
  if (!(["buy", "sell", "sell_all", "stop", "plan", "cancel_plan", "correct_transaction"] as string[]).includes(operation) ||
      !(["submitted", "previewed", "confirmed", "applied", "cancelled", "expired", "error"] as string[]).includes(status) ||
      !Array.isArray(value.warnings) || value.warnings.some((item) => typeof item !== "string")) {
    throw new Error("INVALID_DATA");
  }
  return {
    id: text(value.id), operation, status,
    before: value.before === null ? null : object(value.before),
    after: value.after === null ? null : object(value.after),
    warnings: value.warnings as string[],
    result: value.result === null ? null : object(value.result),
    errorCode: nullableText(value.error_code), expiresAt: text(value.expires_at),
    confirmedAt: nullableText(value.confirmed_at), appliedAt: nullableText(value.applied_at),
    createdAt: text(value.created_at),
  };
}

function run(value: Record<string, unknown>): AnalysisRun {
  return {
    runId: text(value.run_id), kind: text(value.kind), startedAt: text(value.started_at),
    finishedAt: nullableText(value.finished_at), status: text(value.status),
    dataAsOf: nullableText(value.data_as_of), sourceStatus: object(value.source_status),
    symbols: stringList(value.symbols), writeCounts: object(value.write_counts),
    summary: nullableText(value.summary),
  };
}

function recommendation(value: Record<string, unknown>): Recommendation {
  return {
    id: text(value.id), runId: nullableText(value.run_id), ticker: text(value.ticker),
    action: text(value.action), confidence: nullableText(value.confidence),
    validUntil: nullableText(value.valid_until), evidenceAsOf: nullableText(value.evidence_as_of),
  };
}

function evidence(value: unknown): EvidenceItem {
  const item = row(value);
  return {
    evidenceId: text(item.evidence_id), category: text(item.category),
    source: text(item.source), reference: nullableText(item.reference),
    observedAt: nullableText(item.observed_at), retrievedAt: text(item.retrieved_at),
    revalidatedAt: nullableText(item.revalidated_at), claims: stringList(item.claims),
    status: text(item.status),
  };
}

function outcome(value: unknown): Outcome {
  const item = row(value);
  const horizon = item.horizon_sessions;
  if (![5, 21, 63].includes(horizon as number)) throw new Error("INVALID_DATA");
  return {
    horizonSessions: horizon as Outcome["horizonSessions"],
    coverageStatus: text(item.coverage_status),
    stockReturnPct: decimal(item.stock_return_pct, true),
    benchmark: nullableText(item.benchmark),
    benchmarkReturnPct: decimal(item.benchmark_return_pct, true),
    excessReturnPct: decimal(item.excess_return_pct, true),
    mfePct: decimal(item.mfe_pct, true), maePct: decimal(item.mae_pct, true),
    entryHitAt: nullableText(item.entry_hit_at), stopHitAt: nullableText(item.stop_hit_at),
    targetHitAt: nullableText(item.target_hit_at), invalidationHitAt: nullableText(item.invalidation_hit_at),
    directionSuccess: nullableBoolean(item.direction_success), gradedAt: nullableText(item.graded_at),
  };
}

function objectList<T>(value: unknown, parser: (item: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new Error("INVALID_DATA");
  return value.map(parser);
}

function researchItem(value: Record<string, unknown>): ResearchItem {
  const evidenceStatus = text(value.evidence_status) as ResearchItem["evidenceStatus"];
  if (!["fresh", "stale", "source_conflict", "corporate_action_pending", "missing"].includes(evidenceStatus)) {
    throw new Error("INVALID_DATA");
  }
  return {
    id: text(value.id), runId: nullableText(value.run_id), marketDate: nullableText(value.market_date),
    phase: nullableText(value.phase), runStatus: nullableText(value.run_status),
    provider: nullableText(value.provider), model: nullableText(value.model), createdAt: text(value.created_at),
    ticker: text(value.ticker), action: text(value.action), rawAction: nullableText(value.raw_action),
    policyStatus: nullableText(value.policy_status), policyReasonCodes: stringList(value.policy_reason_codes),
    policyExplanations: stringList(value.policy_explanations), analyst: object(value.analyst), checker: object(value.checker),
    confidence: nullableText(value.confidence), verifiedPrice: decimal(value.verified_price, true),
    evidenceAsOf: nullableText(value.evidence_as_of), entryZoneLow: decimal(value.entry_zone_low, true),
    entryZoneHigh: decimal(value.entry_zone_high, true), stop: decimal(value.stop, true),
    target: decimal(value.target, true), invalidationPrice: decimal(value.invalidation_price, true),
    validUntil: nullableText(value.valid_until), horizon: nullableText(value.horizon),
    bucket: nullableText(value.bucket), riskVerdict: nullableText(value.risk_verdict),
    decisiveFactor: nullableText(value.decisive_factor), reason: nullableText(value.reason),
    bullCase: nullableText(value.bull_case), bearCase: nullableText(value.bear_case), evidenceStatus,
    evidence: objectList(value.evidence, evidence), publicationKind: nullableText(value.publication_kind),
    notificationStatus: nullableText(value.notification_status), deliveredAt: nullableText(value.delivered_at),
    telegramMessageIds: stringList(value.telegram_message_ids),
    deliveryErrorCode: nullableText(value.delivery_error_code), outcomes: objectList(value.outcomes, outcome),
  };
}

function runTimeline(value: Record<string, unknown>): RunTimelineItem {
  const evidenceStatus = text(value.evidence_status) as RunTimelineItem["evidenceStatus"];
  if (!["fresh", "stale", "source_conflict", "missing", "not_started"].includes(evidenceStatus) ||
      typeof value.holiday !== "boolean") throw new Error("INVALID_DATA");
  return {
    slotId: nullableText(value.slot_id), marketDate: nullableText(value.market_date),
    phase: text(value.phase), purpose: text(value.purpose), expectedAt: nullableText(value.expected_at),
    windowEndsAt: nullableText(value.window_ends_at), holiday: value.holiday,
    slotStatus: text(value.slot_status), triggerStatus: nullableText(value.trigger_status),
    triggerResponseStatus: nullableSafeInteger(value.trigger_response_status),
    providerSessionUrl: nullableText(value.provider_session_url),
    triggerStartedAt: nullableText(value.trigger_started_at), triggerFinishedAt: nullableText(value.trigger_finished_at),
    runId: nullableText(value.run_id), startedAt: nullableText(value.started_at), finishedAt: nullableText(value.finished_at),
    runStatus: nullableText(value.run_status), dataAsOf: nullableText(value.data_as_of),
    sourceStatus: objectOrEmpty(value.source_status), symbols: value.symbols === null ? [] : stringList(value.symbols),
    writeCounts: objectOrEmpty(value.write_counts), summary: nullableText(value.summary),
    provider: nullableText(value.provider), model: nullableText(value.model),
    submissionStatus: nullableText(value.submission_status), policyStates: stringList(value.policy_states),
    evidenceStatus, publicationKind: nullableText(value.publication_kind),
    publicationStatus: nullableText(value.publication_status), deliveredAt: nullableText(value.delivered_at),
    telegramMessageIds: stringList(value.telegram_message_ids), errorCode: nullableText(value.error_code),
  };
}

function connection(value: Record<string, unknown>): AgentConnection {
  const provider = text(value.provider);
  const credentialType = text(value.credential_type);
  const status = text(value.status);
  const contractVersion = value.contract_version;
  if (provider !== "claude" || credentialType !== "claude_routine_v1" ||
      !["disabled", "testing", "ready", "active", "revoked"].includes(status) ||
      !Number.isSafeInteger(contractVersion) || (contractVersion as number) < 1) {
    throw new Error("INVALID_DATA");
  }
  return {
    id: text(value.id), publicId: text(value.public_id), provider: "claude",
    credentialType: "claude_routine_v1", capabilities: object(value.capabilities),
    contractVersion: contractVersion as number,
    status: status as AgentConnection["status"],
    lastHandshakeAt: nullableText(value.last_handshake_at),
    createdAt: text(value.created_at), updatedAt: text(value.updated_at),
  };
}

function telegramStatus(value: Record<string, unknown>): TelegramLinkStatus {
  const status = text(value.status);
  if (!['active', 'revoked'].includes(status)) throw new Error("INVALID_DATA");
  return {
    status: status as TelegramLinkStatus["status"],
    linkedAt: text(value.linked_at), revokedAt: nullableText(value.revoked_at),
  };
}

function settings(value: Record<string, unknown>): SettingsSnapshot {
  return {
    displayName: nullableText(value.display_name) ?? "Stock Agent owner",
    timezone: text(value.timezone),
    notifyPreMarket: boolean(value.notify_pre_market),
    notifyIntraday: boolean(value.notify_intraday),
    notifyPostMarket: boolean(value.notify_post_market),
    notifyOperational: boolean(value.notify_operational),
    primaryConnectionId: nullableText(value.primary_connection_id),
    scheduleTimezone: text(value.schedule_timezone),
    schedulePreMarket: boolean(value.schedule_pre_market),
    scheduleIntraday: boolean(value.schedule_intraday),
    schedulePostMarket: boolean(value.schedule_post_market),
  };
}

export function createDashboardRepository(client: SupabaseClient): DashboardRepository {
  const api = (client as unknown as ApiReader).schema("api");
  const holdings = () => api.from("holdings").select("*").order("ticker", { ascending: true }).limit(100);
  const quotes = () => api.from("market_quotes").select("*").order("ticker", { ascending: true }).limit(200);
  const plans = () => api.from("plans").select("*").order("next_due_on", { ascending: true }).limit(100);
  return {
    async loadToday() {
      const [holdingRows, quoteRows, planRows, runRows, recommendationRows] = await Promise.all([
        holdings(), quotes(), plans(),
        api.from("today").select("*").order("started_at", { ascending: false }).limit(20),
        api.from("recommendations").select("id,run_id,ticker,action,confidence,valid_until,evidence_as_of")
          .order("ts", { ascending: false }).limit(50),
      ]);
      return {
        holdings: rows(holdingRows).map(holding), quotes: rows(quoteRows).map(quote),
        plans: rows(planRows).map(plan), runs: rows(runRows).map(run),
        recommendations: rows(recommendationRows).map(recommendation),
      };
    },
    async loadPortfolio() {
      const [holdingRows, quoteRows, planRows] = await Promise.all([holdings(), quotes(), plans()]);
      return {
        holdings: rows(holdingRows).map(holding), quotes: rows(quoteRows).map(quote),
        plans: rows(planRows).map(plan),
      };
    },
    async loadActivity() {
      const [transactionRows, planRows, commandRows] = await Promise.all([
        api.from("transactions").select("*").order("ledger_sequence", { ascending: false }).limit(500),
        plans(), api.from("commands").select("*").order("created_at", { ascending: false }).limit(200),
      ]);
      return {
        transactions: rows(transactionRows).map(transaction), plans: rows(planRows).map(plan),
        commands: rows(commandRows).map(command),
      };
    },
    async loadResearch() {
      const result = await api.from("research").select("*")
        .order("created_at", { ascending: false }).limit(200);
      return { items: rows(result).map(researchItem) };
    },
    async loadRuns() {
      const result = await api.from("run_timeline").select("*")
        .order("market_date", { ascending: false }).limit(200);
      return { runs: rows(result).map(runTimeline) };
    },
    async loadConnections() {
      const [connectionRows, telegramRows, runRows] = await Promise.all([
        api.from("connections").select("*").order("created_at", { ascending: false }).limit(20),
        api.from("telegram_status").select("*").order("linked_at", { ascending: false }).limit(1),
        api.from("run_timeline").select("*").order("expected_at", { ascending: false }).limit(50),
      ]);
      const telegramValues = rows(telegramRows);
      const telegramValue = telegramValues.at(0);
      return {
        connections: rows(connectionRows).map(connection),
        telegram: telegramValue ? telegramStatus(telegramValue) : null,
        handshakeRuns: rows(runRows).map(runTimeline).filter((value) => value.purpose === "handshake"),
      };
    },
    async loadSettings() {
      const result = rows(await api.from("settings").select("*").limit(1));
      const value = result.at(0);
      if (result.length !== 1 || !value) throw new Error("DATA_UNAVAILABLE");
      return settings(value);
    },
    async lookupCommand(commandId) {
      const response = await api.from("commands").select("*").eq("id", commandId).maybeSingle();
      if (response.error) throw new Error("DATA_UNAVAILABLE");
      return response.data === null ? null : command(row(response.data));
    },
  };
}
