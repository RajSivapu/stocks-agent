import {
  type AlertConditionEvidence,
  type AlertEvaluation,
  type AlertRuleSnapshot,
  type AlertV3Class,
  type ArtifactMutation,
  type GatewayEnvelope,
  parseArtifactMutationBatch,
  parseDecisionBundle,
  parseGatewayEnvelope,
  type Phase,
  type PolicyContext,
  type VerifiedQuote,
} from "./contracts.ts";
import { isNyseHoliday } from "./market-calendar.ts";
import {
  type AdjustedBar,
  fetchAdjustedHistory,
  fetchIntradayQuoteEvidence,
  fetchVerifiedQuote,
  type IntradayQuoteEvidence,
} from "./market-data.ts";
import { type DueDecision, gradeDecision } from "./outcomes.ts";
import { evaluateCandidate, type PolicyEvaluation } from "./policy.ts";
import { draftFromEvaluation } from "./policy.ts";
import {
  alertFingerprint,
  alertRuleFingerprint,
  evaluateAlertRule,
  shouldPublishAlert,
} from "./alerts.ts";
import {
  renderAlertV3,
  type RenderedAlert,
  renderPublication,
} from "./renderer.ts";
import {
  comparePortfolioAlternative,
  type PortfolioAlternativeComparison,
} from "./alternatives.ts";
import {
  type GatewayRepository,
  GatewayRepositoryError,
  type PersistableAlertDraft,
  type PersistableAlertEvent,
  type PersistableAlertPublication,
  type PersistableArtifactMutation,
  type PersistableArtifactMutationBatch,
  type PersistedBundle,
  type PublicationReceipt,
} from "./repository.ts";
import {
  sendTelegramAlert,
  sendTelegramParts,
  type TelegramAlertInput,
  type TelegramAlertReceipt,
  TelegramDeliveryError,
} from "./telegram.ts";

export interface GatewayDependencies {
  repository: GatewayRepository;
  marketAgentSecret: string;
  telegramToken: string;
  telegramChatId: string;
  now?: () => Date;
  newId?: () => string;
  fetchQuote?: (ticker: string, now: Date) => Promise<VerifiedQuote>;
  fetchHistory?: (ticker: string) => Promise<AdjustedBar[]>;
  fetchAlertEvidence?: (
    ticker: string,
    now: Date,
  ) => Promise<IntradayQuoteEvidence>;
  sendTelegram?: (
    parts: string[],
    chatId: string,
    token: string,
  ) => Promise<number[]>;
  sendTelegramAlert?: (
    alert: TelegramAlertInput,
    chatId: string,
    token: string,
  ) => Promise<TelegramAlertReceipt>;
}

const MAX_BODY_BYTES = 262_144;

function response(status: number, body: Record<string, unknown>): Response {
  const rendered = body.ok === true && !("data" in body)
    ? {
      ...body,
      data: Object.fromEntries(
        Object.entries(body).filter(([key]) => key !== "ok"),
      ),
    }
    : body;
  return Response.json(rendered, {
    status,
    headers: {
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

async function secureEqual(left: string, right: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [leftHash, rightHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(left)),
    crypto.subtle.digest("SHA-256", encoder.encode(right)),
  ]);
  const leftBytes = new Uint8Array(leftHash);
  const rightBytes = new Uint8Array(rightHash);
  let mismatch = 0;
  for (let index = 0; index < leftBytes.length; index += 1) {
    mismatch |= leftBytes[index] ^ rightBytes[index];
  }
  return mismatch === 0 && left.length === right.length;
}

async function readBody(request: Request): Promise<unknown> {
  const declared = request.headers.get("content-length");
  if (
    declared !== null &&
    (!/^\d+$/.test(declared) || Number(declared) > MAX_BODY_BYTES)
  ) {
    throw new GatewayHttpError(413, "REQUEST_TOO_LARGE");
  }
  if (!request.body) throw new GatewayHttpError(400, "INVALID_REQUEST");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > MAX_BODY_BYTES) {
      await reader.cancel();
      throw new GatewayHttpError(413, "REQUEST_TOO_LARGE");
    }
    chunks.push(value);
  }
  const body = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
  } catch {
    throw new GatewayHttpError(400, "INVALID_REQUEST");
  }
}

class GatewayHttpError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

function objectValue(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new GatewayHttpError(400, "INVALID_REQUEST");
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  row: Record<string, unknown>,
  required: readonly string[],
): void {
  if (
    Object.keys(row).some((key) => !required.includes(key)) ||
    required.some((key) => !(key in row))
  ) {
    throw new GatewayHttpError(400, "INVALID_REQUEST");
  }
}

function parseGradePayload(value: unknown): { limit: number } {
  const row = objectValue(value);
  exactKeys(row, ["limit"]);
  if (
    typeof row.limit !== "number" || !Number.isSafeInteger(row.limit) ||
    row.limit < 1 || row.limit > 50
  ) {
    throw new GatewayHttpError(400, "INVALID_REQUEST");
  }
  return { limit: row.limit };
}

function parseAlertPayload(value: unknown): { limit: number } {
  const row = objectValue(value);
  if (Object.keys(row).some((key) => key !== "limit")) {
    throw new GatewayHttpError(400, "INVALID_REQUEST");
  }
  if (row.limit === undefined) return { limit: 20 };
  if (
    typeof row.limit !== "number" || !Number.isSafeInteger(row.limit) ||
    row.limit < 1 || row.limit > 20
  ) {
    throw new GatewayHttpError(400, "INVALID_REQUEST");
  }
  return { limit: row.limit };
}

function chicagoDate(now: Date): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function parseStartPayload(
  value: unknown,
  currentDate: string,
): { phase: Phase; market_date: string } {
  const row = objectValue(value);
  exactKeys(row, ["phase", "market_date"]);
  if (
    !["pre-market", "intraday", "post-market", "on-demand"].includes(
      String(row.phase),
    ) ||
    row.market_date !== currentDate
  ) {
    throw new GatewayHttpError(400, "INVALID_REQUEST");
  }
  return { phase: row.phase as Phase, market_date: currentDate };
}

function requireRun(envelope: GatewayEnvelope): string {
  if (envelope.run_id === null) {
    throw new GatewayHttpError(400, "INVALID_REQUEST");
  }
  return envelope.run_id;
}

function errorStatus(code: string): number {
  if (code === "RATE_LIMITED") return 429;
  if (code === "CONTEXT_TOO_LARGE") return 413;
  if (code === "POLICY_REJECTED" || code === "CALENDAR_COVERAGE_MISSING") {
    return 409;
  }
  return 500;
}

function stableScore(value: string | null): number | null {
  if (value === null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number) : null;
}

function suggestionFromEvaluation(
  evaluation: PolicyEvaluation,
  marketDate: string,
): Record<string, unknown> | null {
  if (evaluation.final_action === null || evaluation.status === "vetoed") {
    return null;
  }
  const candidate = evaluation.candidate;
  return {
    evaluation_id: evaluation.evaluation_id,
    candidate_id: evaluation.candidate_id,
    date: marketDate,
    ticker: candidate.ticker,
    action: evaluation.final_action,
    decision_mode: candidate.decision_mode,
    bucket: candidate.bucket,
    depth: candidate.depth,
    entry_zone_low: candidate.entry_zone_low,
    entry_zone_high: candidate.entry_zone_high,
    valid_until: candidate.valid_until,
    stop: candidate.stop,
    target: candidate.target,
    confidence: candidate.confidence,
    bull: null,
    bear: null,
    decisive_factor: null,
    risk_verdict: evaluation.status,
    reason: evaluation.reason_codes.join(","),
    score: stableScore(candidate.health_score),
    price_at_suggestion: evaluation.normalized.verified_price,
    evidence_as_of: evaluation.normalized.quote_as_of,
    invalidation_price: candidate.invalidation_price,
  };
}

async function normalizeArtifacts(
  mutations: ArtifactMutation[],
  runId: string,
  deps:
    & Required<Pick<GatewayDependencies, "now" | "fetchQuote">>
    & Pick<GatewayDependencies, "repository">,
): Promise<PersistableArtifactMutationBatch> {
  const currentDate = chicagoDate(deps.now());
  let context: Awaited<ReturnType<GatewayRepository["readContext"]>> | null =
    null;
  const output: PersistableArtifactMutation[] = [];
  for (const mutation of mutations) {
    if (mutation.kind === "paper_watch_create") {
      context ??= await deps.repository.readContext(runId);
      const quote = await deps.fetchQuote(mutation.ticker, deps.now());
      const latest = context.recent_suggestions.find((item) =>
        item.ticker === mutation.ticker
      );
      output.push({
        ...mutation,
        entry_ref_price: quote.price,
        created: currentDate,
        agent_view_at_open: latest?.action ?? "no prior view",
        agent_score_at_open: latest?.score ?? null,
      });
    } else if (mutation.kind === "paper_watch_close") {
      const quote = await deps.fetchQuote(mutation.ticker, deps.now());
      output.push({
        ...mutation,
        closed_date: currentDate,
        close_price: quote.price,
      });
    } else {
      output.push(mutation);
    }
  }
  return { mutations: output };
}

export function createGatewayHandler(dependencies: GatewayDependencies) {
  const deps = {
    ...dependencies,
    now: dependencies.now ?? (() => new Date()),
    newId: dependencies.newId ?? (() => crypto.randomUUID()),
    fetchQuote: dependencies.fetchQuote ??
      ((ticker: string, now: Date) => fetchVerifiedQuote(ticker, fetch, now)),
    fetchHistory: dependencies.fetchHistory ??
      ((ticker: string) => fetchAdjustedHistory(ticker, "1y", fetch)),
    fetchAlertEvidence: dependencies.fetchAlertEvidence ??
      ((ticker: string, now: Date) =>
        fetchIntradayQuoteEvidence(ticker, fetch, now)),
    sendTelegram: dependencies.sendTelegram ?? sendTelegramParts,
    sendTelegramAlert: dependencies.sendTelegramAlert ?? sendTelegramAlert,
  };

  return async (request: Request): Promise<Response> => {
    if (request.method !== "POST") {
      return response(405, { ok: false, code: "METHOD_NOT_ALLOWED" });
    }
    const supplied = request.headers.get("x-market-agent-secret") ?? "";
    if (!(await secureEqual(supplied, deps.marketAgentSecret))) {
      return response(401, { ok: false, code: "UNAUTHORIZED" });
    }

    let envelope: GatewayEnvelope;
    let prepared: unknown;
    const currentDate = chicagoDate(deps.now());
    try {
      envelope = parseGatewayEnvelope(await readBody(request));
      if (envelope.operation === "start_run") {
        prepared = parseStartPayload(envelope.payload, currentDate);
      } else if (envelope.operation === "record_artifacts") {
        requireRun(envelope);
        prepared = parseArtifactMutationBatch(envelope.payload);
      } else if (envelope.operation === "evaluate_and_publish") {
        requireRun(envelope);
        const row = objectValue(envelope.payload);
        if (row.market_date !== currentDate) {
          throw new GatewayHttpError(400, "INVALID_REQUEST");
        }
        prepared = parseDecisionBundle(row, row.phase as Phase);
      } else if (envelope.operation === "grade_due_decisions") {
        requireRun(envelope);
        prepared = parseGradePayload(envelope.payload);
      } else if (envelope.operation === "evaluate_alert_rules") {
        if (envelope.run_id !== null) {
          throw new GatewayHttpError(400, "INVALID_REQUEST");
        }
        prepared = parseAlertPayload(envelope.payload);
      } else if (
        envelope.operation === "read_context" ||
        envelope.operation === "finish_run"
      ) {
        requireRun(envelope);
        const row = objectValue(envelope.payload);
        if (envelope.operation !== "finish_run") exactKeys(row, []);
        prepared = envelope.payload;
      }
    } catch (error) {
      if (error instanceof GatewayHttpError) {
        return response(error.status, { ok: false, code: error.code });
      }
      return response(400, { ok: false, code: "INVALID_REQUEST" });
    }

    if (envelope.dry_run) {
      try {
        if (envelope.operation === "start_run") {
          return response(200, {
            ok: true,
            dry_run: true,
            run_id: deps.newId(),
            status: "ephemeral",
          });
        }
        if (envelope.operation === "read_context") {
          return response(200, {
            ok: true,
            dry_run: true,
            context: await deps.repository.readContext(envelope.run_id),
          });
        }
        if (envelope.operation === "record_artifacts") {
          const normalized = await normalizeArtifacts(
            (prepared as ReturnType<typeof parseArtifactMutationBatch>)
              .mutations,
            requireRun(envelope),
            deps,
          );
          const counts: Record<string, number> = {};
          for (const mutation of normalized.mutations) {
            counts[mutation.kind] = (counts[mutation.kind] ?? 0) + 1;
          }
          return response(200, {
            ok: true,
            dry_run: true,
            receipt: { would_write: normalized.mutations.length, counts },
          });
        }
        if (envelope.operation === "finish_run") {
          return response(200, {
            ok: true,
            dry_run: true,
            status: "completed",
            write_counts: {},
            publication_statuses: [],
            telegram_message_ids: [],
          });
        }
        if (envelope.operation === "grade_due_decisions") {
          const receipt = await gradeDueDecisions(
            (prepared as { limit: number }).limit,
            false,
            deps,
          );
          return response(200, { ok: true, dry_run: true, ...receipt });
        }
        if (envelope.operation === "evaluate_alert_rules") {
          return response(200, {
            ok: true,
            dry_run: true,
            ...await evaluateAlertRules(
              envelope,
              (prepared as { limit: number }).limit,
              null,
              deps,
            ),
          });
        }
        return await evaluateAndPublish(
          envelope,
          prepared as ReturnType<typeof parseDecisionBundle>,
          null,
          deps,
        );
      } catch (error) {
        const code = error instanceof GatewayRepositoryError
          ? error.code
          : "POLICY_REJECTED";
        return response(errorStatus(code), { ok: false, code });
      }
    }

    let leaseToken: string | null = null;
    try {
      const claim = await deps.repository.claimRequest(envelope);
      if (claim.in_progress) {
        return response(409, { ok: false, code: "REQUEST_IN_PROGRESS" });
      }
      if (claim.duplicate) return response(200, objectValue(claim.response));
      leaseToken = claim.lease_token;
      if (!leaseToken) throw new GatewayRepositoryError("PERSISTENCE_FAILED");

      if (envelope.operation === "start_run") {
        const start = prepared as { phase: Phase; market_date: string };
        const activePolicy = await deps.repository.activePolicy();
        if (
          currentDate.slice(0, 4) !== String(activePolicy.market_calendar_year)
        ) {
          throw new GatewayRepositoryError("CALENDAR_COVERAGE_MISSING");
        }
        if (
          start.phase !== "on-demand" &&
          isNyseHoliday(currentDate, activePolicy.nyse_holidays)
        ) {
          if (start.phase !== "pre-market") {
            const result = {
              ok: true,
              status: "suppressed",
              holiday: true,
              run_id: null,
            };
            await deps.repository.completeRequest(
              envelope.request_id,
              leaseToken,
              result,
            );
            return response(200, result);
          }
          const rendered = await renderPublication({
            phase: start.phase,
            market_date: currentDate,
            evaluations: [],
            holiday: true,
          });
          const persisted = await deps.repository.applyDecisionBundle({
            request_id: envelope.request_id,
            request_lease_token: leaseToken,
            run_id: null,
            policy_version: activePolicy.version,
            evaluations: [],
            suggestions: [],
            holding_state_changes: [],
            publication: {
              id: deps.newId(),
              idempotency_key: envelope.request_id,
              market_date: currentDate,
              phase: start.phase,
              kind: rendered.kind,
              template_version: rendered.template_version,
              rendered_body: rendered.body,
              rendered_hash: rendered.hash,
              status: rendered.status,
            },
          });
          const delivered = await deliverReadyPublication(
            persisted,
            rendered.parts,
            deps,
          );
          const result = {
            ok: true,
            holiday: true,
            run_id: null,
            publication_status: delivered.status,
            telegram_message_ids: delivered.telegram_message_ids,
          };
          if (
            delivered.status === "delivery_failed" ||
            delivered.status === "delivery_unknown"
          ) {
            const code = delivered.status === "delivery_unknown"
              ? "DELIVERY_UNKNOWN"
              : "DELIVERY_FAILED";
            await deps.repository.failRequest(
              envelope.request_id,
              leaseToken,
              code,
            );
            return response(502, { ...result, ok: false, code });
          }
          await deps.repository.completeRequest(
            envelope.request_id,
            leaseToken,
            result,
          );
          return response(200, result);
        }
        const runId = await deps.repository.startRun(
          envelope.request_id,
          leaseToken,
          start.phase,
        );
        const result = {
          ok: true,
          run_id: runId,
          phase: start.phase,
          market_date: currentDate,
        };
        await deps.repository.completeRequest(
          envelope.request_id,
          leaseToken,
          result,
        );
        return response(200, result);
      }
      if (envelope.operation === "read_context") {
        const result = {
          ok: true,
          context: await deps.repository.readContext(envelope.run_id),
        };
        await deps.repository.completeRequest(
          envelope.request_id,
          leaseToken,
          result,
        );
        return response(200, result);
      }
      if (envelope.operation === "record_artifacts") {
        const normalized = await normalizeArtifacts(
          (prepared as ReturnType<typeof parseArtifactMutationBatch>).mutations,
          requireRun(envelope),
          deps,
        );
        const receipt = await deps.repository.recordArtifacts(
          envelope.request_id,
          requireRun(envelope),
          leaseToken,
          normalized,
        );
        return response(200, { ok: true, receipt });
      }
      if (envelope.operation === "grade_due_decisions") {
        const result = {
          ok: true,
          ...await gradeDueDecisions(
            (prepared as { limit: number }).limit,
            true,
            deps,
          ),
        };
        await deps.repository.completeRequest(
          envelope.request_id,
          leaseToken,
          result,
        );
        return response(200, result);
      }
      if (envelope.operation === "finish_run") {
        const receipt = await deps.repository.finishRun(requireRun(envelope));
        const result = { ok: true, ...receipt };
        await deps.repository.completeRequest(
          envelope.request_id,
          leaseToken,
          result,
        );
        return response(200, result);
      }
      if (envelope.operation === "evaluate_alert_rules") {
        const result = await evaluateAlertRules(
          envelope,
          (prepared as { limit: number }).limit,
          leaseToken,
          deps,
        );
        if (
          result.publication_status === "delivery_failed" ||
          result.publication_status === "delivery_unknown"
        ) {
          const code = result.publication_status === "delivery_unknown"
            ? "DELIVERY_UNKNOWN"
            : "DELIVERY_FAILED";
          await deps.repository.failRequest(
            envelope.request_id,
            leaseToken,
            code,
          );
          return response(502, { ok: false, ...result, code });
        }
        const completed = { ok: true, ...result };
        await deps.repository.completeRequest(
          envelope.request_id,
          leaseToken,
          completed,
        );
        return response(200, completed);
      }
      return await evaluateAndPublish(
        envelope,
        prepared as ReturnType<typeof parseDecisionBundle>,
        leaseToken,
        deps,
      );
    } catch (error) {
      const code = error instanceof GatewayRepositoryError
        ? error.code
        : "PERSISTENCE_FAILED";
      if (leaseToken) {
        try {
          await deps.repository.failRequest(
            envelope.request_id,
            leaseToken,
            code,
          );
        } catch {
          // The original stable error remains authoritative.
        }
      }
      return response(errorStatus(code), { ok: false, code });
    }
  };
}

type ResolvedDependencies = GatewayDependencies & {
  now: () => Date;
  newId: () => string;
  fetchQuote: (ticker: string, now: Date) => Promise<VerifiedQuote>;
  fetchHistory: (ticker: string) => Promise<AdjustedBar[]>;
  sendTelegram: (
    parts: string[],
    chatId: string,
    token: string,
  ) => Promise<number[]>;
  fetchAlertEvidence: (
    ticker: string,
    now: Date,
  ) => Promise<IntradayQuoteEvidence>;
  sendTelegramAlert: (
    alert: TelegramAlertInput,
    chatId: string,
    token: string,
  ) => Promise<TelegramAlertReceipt>;
};

type AlertOperationPublicationStatus =
  | PublicationReceipt["status"]
  | "not_created";

interface AlertOperationResult {
  status: "disabled" | "shadow" | "evaluated";
  evaluated_rules: number;
  unsafe_evaluations: number;
  would_write_events: number;
  would_publish: number;
  shadow_publish_candidates: number;
  alert_events_recorded: number;
  publication_status: AlertOperationPublicationStatus;
  telegram_message_ids: number[];
  telegram_accepted_at: string | null;
  draft_previews: Array<{ draft_id: string; body: string; hash: string }>;
  alert_previews: Array<
    {
      event_id: string;
      status: AlertEvaluation["status"];
      body: string;
      hash: string;
    }
  >;
  suppression_reason?: string;
}

const ALERT_SEVERITY_PRIORITY: Record<AlertRuleSnapshot["severity"], number> = {
  critical: 0,
  system: 1,
  review: 2,
  update: 3,
  watch: 4,
};

function evidenceForAlertRule(
  rule: AlertRuleSnapshot,
  intraday: IntradayQuoteEvidence | null,
): AlertConditionEvidence[] {
  const quoteKinds = new Set([
    "price_cross",
    "price_zone",
    "recorded_stop",
    "recorded_target",
  ]);
  return rule.conditions.map((condition, conditionIndex) => {
    const supported = condition.timeframe === "quote" &&
      quoteKinds.has(condition.kind);
    const marketSession = intraday?.market_session ??
      (rule.session === "all" ? "regular" : rule.session);
    return {
      condition_index: conditionIndex,
      status: supported && intraday
        ? "fresh"
        : supported
        ? "missing"
        : "unsupported",
      market_session: marketSession,
      evidence_ids: supported && intraday
        ? intraday.points.map((point) =>
          `${intraday.source}:${point.observed_at}`
        )
        : [],
      points: supported && intraday ? intraday.points : [],
    };
  });
}

function persistableAlertEvent(
  id: string,
  evaluation: AlertEvaluation,
  fingerprint: string,
): PersistableAlertEvent {
  const session = evaluation.market_session === "all"
    ? "regular"
    : evaluation.market_session;
  return {
    id,
    rule_id: evaluation.rule.rule_id,
    rule_version: evaluation.rule.version,
    fingerprint,
    status: evaluation.status as PersistableAlertEvent["status"],
    reason_codes: [...evaluation.reason_codes],
    observed_at: evaluation.observed_at,
    evaluated_at: evaluation.evaluated_at,
    market_session: session,
    condition_results: evaluation.condition_results,
    evidence_ids: [
      ...new Set(
        evaluation.condition_results.flatMap((result) => result.evidence_ids),
      ),
    ],
  };
}

function alertKind(
  rule: AlertRuleSnapshot,
  evaluation?: AlertEvaluation,
): PersistableAlertPublication["kind"] {
  if (
    evaluation?.status === "unsafe_to_evaluate" || rule.severity === "system"
  ) return "data_warning";
  if (rule.conditions.some((condition) => condition.kind === "recorded_stop")) {
    return "stop_breach";
  }
  if (
    rule.conditions.some((condition) => condition.kind === "recorded_target")
  ) return "target_hit";
  if (rule.severity === "watch") return "new_idea";
  return "entry_trigger";
}

function alertRuleClass(rule: AlertRuleSnapshot): AlertV3Class | null {
  if (rule.conditions.length === 0) return null;
  if (
    rule.conditions.every((condition) => condition.kind === "recorded_stop")
  ) return "stop_breach";
  if (
    rule.conditions.every((condition) => condition.kind === "recorded_target")
  ) return "target_hit";
  if (rule.conditions.every((condition) => condition.kind === "price_zone")) {
    return "entry_trigger";
  }
  return null;
}

function alertRuleAllowed(
  rule: AlertRuleSnapshot,
  policy: NonNullable<import("./contracts.ts").PolicyConfig["alerts_v3"]>,
): boolean {
  if (policy.shadow) return true;
  const alertClass = alertRuleClass(rule);
  return policy.enabled && alertClass !== null &&
    policy.enabled_classes.includes(alertClass);
}

function unsafeOutsideCooldown(
  work: Awaited<
    ReturnType<GatewayRepository["readAlertWork"]>
  >["rules"][number],
  evaluation: AlertEvaluation,
): boolean {
  if (evaluation.status !== "unsafe_to_evaluate") return false;
  const evaluated = Date.parse(evaluation.evaluated_at);
  return !work.recent_events.some((event) =>
    event.status === "unsafe_to_evaluate" &&
    evaluated - Date.parse(event.evaluated_at) >= 0 &&
    evaluated - Date.parse(event.evaluated_at) <
      work.rule.cooldown_seconds * 1_000
  );
}

async function deliverReadyAlert(
  persisted: PublicationReceipt,
  alert: RenderedAlert,
  deps: ResolvedDependencies,
): Promise<PublicationReceipt> {
  if (
    persisted.status === "suppressed" || persisted.status === "delivered" ||
    persisted.status === "delivery_unknown" || persisted.status === "sending"
  ) return persisted;
  const claim = await deps.repository.claimPublication(
    persisted.idempotency_key,
  );
  if (!claim.claimed || !claim.lease_token) return claim.receipt;
  try {
    const accepted = await deps.sendTelegramAlert(
      { body: alert.body, reply_markup: alert.reply_markup },
      deps.telegramChatId,
      deps.telegramToken,
    );
    return await deps.repository.finishAlertPublication(
      persisted.idempotency_key,
      claim.lease_token,
      "delivered",
      [accepted.message_id],
      null,
      accepted.accepted_at,
    );
  } catch (error) {
    const delivery = error instanceof TelegramDeliveryError
      ? error
      : new TelegramDeliveryError("ambiguous", []);
    return await deps.repository.finishAlertPublication(
      persisted.idempotency_key,
      claim.lease_token,
      delivery.kind === "definitive" ? "delivery_failed" : "delivery_unknown",
      delivery.partialMessageIds,
      delivery.kind === "definitive"
        ? "TELEGRAM_REJECTED"
        : "TELEGRAM_OUTCOME_UNKNOWN",
      null,
    );
  }
}

async function evaluateAlertRules(
  envelope: GatewayEnvelope,
  limit: number,
  leaseToken: string | null,
  deps: ResolvedDependencies,
): Promise<AlertOperationResult> {
  const activePolicy = await deps.repository.activePolicy();
  const alertPolicy = activePolicy.alerts_v3;
  if (!alertPolicy || (!alertPolicy.enabled && !alertPolicy.shadow)) {
    return {
      status: "disabled",
      evaluated_rules: 0,
      unsafe_evaluations: 0,
      would_write_events: 0,
      would_publish: 0,
      shadow_publish_candidates: 0,
      alert_events_recorded: 0,
      publication_status: "not_created",
      telegram_message_ids: [],
      telegram_accepted_at: null,
      draft_previews: [],
      alert_previews: [],
    };
  }

  if (!envelope.dry_run && alertPolicy.enabled) {
    if (!leaseToken) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
    await deps.repository.expireAlertRules();
  }

  const [work, context] = await Promise.all([
    deps.repository.readAlertWork(limit),
    deps.repository.readContext(null),
  ]);
  const now = deps.now();
  const evidenceByTicker = new Map<
    string,
    Promise<IntradayQuoteEvidence | null>
  >();
  const evidence = (ticker: string) => {
    if (!evidenceByTicker.has(ticker)) {
      evidenceByTicker.set(
        ticker,
        deps.fetchAlertEvidence(ticker, now).catch(() => null),
      );
    }
    return evidenceByTicker.get(ticker)!;
  };

  const allowedRules = work.rules.filter((item) =>
    alertRuleAllowed(item.rule, alertPolicy)
  );
  const allowedDrafts = work.drafts.filter((item) =>
    alertRuleAllowed(item.rule, alertPolicy)
  );
  const evaluated = await Promise.all(allowedRules.map(async (item) => {
    const providerEvidence = await evidence(item.rule.ticker);
    const evaluation = evaluateAlertRule(
      item.rule,
      evidenceForAlertRule(item.rule, providerEvidence),
      now,
      activePolicy.max_actionable_quote_age_minutes,
    );
    const fingerprint = await alertFingerprint(item.rule, evaluation);
    const publishable = shouldPublishAlert(
      item.rule,
      evaluation,
      item.recent_events,
    );
    const recordable = publishable || unsafeOutsideCooldown(item, evaluation);
    const eventId = deps.newId();
    const rendered = evaluation.status === "not_triggered"
      ? null
      : await renderAlertV3({
        event_id: eventId,
        evaluation,
        source_evaluation: null,
        source_summary: item.source_summary,
        context,
      });
    return {
      item,
      evaluation,
      fingerprint,
      publishable,
      recordable,
      eventId,
      rendered,
    };
  }));

  const draftPreviews = await Promise.all(allowedDrafts.map(async (item) => {
    const draftEvaluation: AlertEvaluation = {
      rule: item.rule,
      status: "not_triggered",
      reason_codes: [],
      observed_at: null,
      evaluated_at: now.toISOString(),
      market_session: item.rule.session === "all"
        ? "regular"
        : item.rule.session,
      condition_results: item.rule.conditions.map((condition) => ({
        condition,
        passed: null,
        observed_value: null,
        evidence_ids: [],
      })),
    };
    const rendered = await renderAlertV3({
      event_id: item.rule.rule_id,
      evaluation: draftEvaluation,
      source_evaluation: null,
      source_summary: item.source_summary,
      context,
      mode: "draft",
    });
    return { item, rendered };
  }));

  const publishable = evaluated
    .filter((item) => item.publishable && item.rendered?.status === "ready")
    .sort((left, right) =>
      ALERT_SEVERITY_PRIORITY[left.item.rule.severity] -
        ALERT_SEVERITY_PRIORITY[right.item.rule.severity] ||
      left.item.rule.ticker.localeCompare(right.item.rule.ticker) ||
      left.item.rule.rule_id.localeCompare(right.item.rule.rule_id)
    );
  const chosenEvent = publishable[0] ?? null;
  const persistables = evaluated
    .filter((item) =>
      item.recordable &&
      (item.evaluation.status === "unsafe_to_evaluate" || item === chosenEvent)
    )
    .map((item) =>
      persistableAlertEvent(item.eventId, item.evaluation, item.fingerprint)
    );
  const alertPreviews = evaluated.flatMap((item) =>
    item.rendered
      ? [{
        event_id: item.eventId,
        status: item.evaluation.status,
        body: item.rendered.body,
        hash: item.rendered.hash,
      }]
      : []
  );
  const base = {
    evaluated_rules: evaluated.length,
    unsafe_evaluations:
      evaluated.filter((item) =>
        item.evaluation.status === "unsafe_to_evaluate"
      ).length,
    would_write_events: persistables.length,
    would_publish:
      alertPolicy.enabled && (chosenEvent !== null || draftPreviews.length > 0)
        ? 1
        : 0,
    shadow_publish_candidates: publishable.length,
    alert_events_recorded: 0,
    publication_status: "not_created" as AlertOperationPublicationStatus,
    telegram_message_ids: [] as number[],
    telegram_accepted_at: null as string | null,
    draft_previews: draftPreviews.map(({ item, rendered }) => ({
      draft_id: item.rule.rule_id,
      body: rendered.body,
      hash: rendered.hash,
    })),
    alert_previews: alertPreviews,
  };
  if (envelope.dry_run || !alertPolicy.enabled) {
    return { status: alertPolicy.enabled ? "evaluated" : "shadow", ...base };
  }
  if (!leaseToken) throw new GatewayRepositoryError("PERSISTENCE_FAILED");

  const eventReceipt = persistables.length > 0
    ? await deps.repository.recordAlertEvaluations(
      envelope.request_id,
      persistables,
    )
    : { event_count: 0, event_ids: [] };
  const chosenDraft = chosenEvent ? null : draftPreviews[0] ?? null;
  const rendered = chosenEvent?.rendered ?? chosenDraft?.rendered ?? null;
  if (!rendered || rendered.status !== "ready") {
    return {
      status: "evaluated",
      ...base,
      alert_events_recorded: eventReceipt.event_count,
    };
  }
  const publicationId = deps.newId();
  const persisted = await deps.repository.createAlertPublication(
    envelope.request_id,
    {
      id: publicationId,
      market_date: chicagoDate(now),
      kind: chosenEvent
        ? alertKind(chosenEvent.item.rule, chosenEvent.evaluation)
        : alertKind(chosenDraft!.item.rule),
      rendered_body: rendered.body,
      rendered_hash: rendered.hash,
      event_ids: chosenEvent ? [chosenEvent.eventId] : [],
      draft_id: chosenDraft?.item.rule.rule_id ?? null,
    },
  );
  const delivered = await deliverReadyAlert(persisted, rendered, deps);
  return {
    status: "evaluated",
    ...base,
    alert_events_recorded: eventReceipt.event_count,
    publication_status: delivered.status,
    telegram_message_ids: delivered.telegram_message_ids,
    telegram_accepted_at: delivered.telegram_accepted_at,
  };
}

async function gradeDueDecisions(
  limit: number,
  persist: boolean,
  deps: ResolvedDependencies,
): Promise<Record<string, unknown>> {
  const decisions = await deps.repository.dueDecisions(limit);
  const histories = new Map<string, Promise<AdjustedBar[]>>();
  const history = (ticker: string): Promise<AdjustedBar[]> => {
    if (!histories.has(ticker)) {
      histories.set(ticker, deps.fetchHistory(ticker).catch(() => []));
    }
    return histories.get(ticker)!;
  };
  const grades = [];
  for (const decision of decisions as DueDecision[]) {
    const stockBars = await history(decision.ticker);
    const benchmarkTicker = decision.ticker === "VXUS" ? "VXUS" : "VOO";
    const benchmarkBars = await history(benchmarkTicker);
    for (const horizon of [5, 21, 63] as const) {
      if (!decision.completed_horizons.includes(horizon)) {
        grades.push(gradeDecision(decision, stockBars, benchmarkBars, horizon));
      }
    }
  }
  if (!persist) {
    return {
      counts: {
        inserted: 0,
        updated: 0,
        incomplete: grades.filter((grade) =>
          grade.coverage_status !== "complete"
        ).length,
      },
      would_grade: grades.length,
    };
  }
  return { counts: await deps.repository.upsertGrades(grades) };
}

async function deliverReadyPublication(
  persisted: PublicationReceipt,
  parts: string[],
  deps: ResolvedDependencies,
): Promise<PublicationReceipt> {
  if (
    persisted.status === "suppressed" || persisted.status === "delivered" ||
    persisted.status === "delivery_unknown" || persisted.status === "sending"
  ) return persisted;
  const claim = await deps.repository.claimPublication(
    persisted.idempotency_key,
  );
  if (!claim.claimed || !claim.lease_token) return claim.receipt;
  try {
    const ids = await deps.sendTelegram(
      parts,
      deps.telegramChatId,
      deps.telegramToken,
    );
    return await deps.repository.finishPublication(
      persisted.idempotency_key,
      claim.lease_token,
      "delivered",
      ids,
      null,
    );
  } catch (error) {
    const delivery = error instanceof TelegramDeliveryError
      ? error
      : new TelegramDeliveryError("ambiguous", []);
    return await deps.repository.finishPublication(
      persisted.idempotency_key,
      claim.lease_token,
      delivery.kind === "definitive" ? "delivery_failed" : "delivery_unknown",
      delivery.partialMessageIds,
      delivery.kind === "definitive"
        ? "TELEGRAM_REJECTED"
        : "TELEGRAM_OUTCOME_UNKNOWN",
    );
  }
}

async function evaluateAndPublish(
  envelope: GatewayEnvelope,
  bundle: ReturnType<typeof parseDecisionBundle>,
  leaseToken: string | null,
  deps: ResolvedDependencies,
): Promise<Response> {
  const [context, activePolicy] = await Promise.all([
    deps.repository.readContext(envelope.run_id),
    deps.repository.activePolicy(),
  ]);
  const tickers = [
    ...new Set([
      ...bundle.candidates.map((candidate) => candidate.ticker),
      ...context.holdings.map((holding) => holding.ticker),
    ]),
  ];
  const quotePairs = await Promise.all(tickers.map(async (ticker) => {
    try {
      return [ticker, await deps.fetchQuote(ticker, deps.now())] as const;
    } catch {
      return [ticker, null] as const;
    }
  }));
  const quotes = new Map(quotePairs);
  context.holding_quotes = Object.fromEntries(
    context.holdings.flatMap((holding) => {
      const quote = quotes.get(holding.ticker);
      return quote ? [[holding.ticker, quote]] : [];
    }),
  );
  const evaluations = bundle.candidates.map((candidate) =>
    evaluateCandidate(
      candidate,
      context,
      activePolicy,
      quotes.get(candidate.ticker) ?? null,
      deps.now(),
      deps.newId,
    )
  );
  const comparisons = await buildPortfolioComparisons(
    bundle.comparisons ?? [],
    evaluations,
    context,
    deps,
  );
  const suggestions = evaluations.map((evaluation) =>
    suggestionFromEvaluation(evaluation, bundle.market_date)
  )
    .filter((item): item is Record<string, unknown> => item !== null);
  const alertDrafts: PersistableAlertDraft[] = [];
  for (const evaluation of evaluations) {
    if (alertDrafts.length >= (activePolicy.alerts_v3?.drafts_per_hour ?? 0)) {
      break;
    }
    const snapshot = draftFromEvaluation(
      evaluation,
      context,
      activePolicy,
      deps.now(),
      deps.newId,
    );
    if (snapshot) {
      alertDrafts.push({
        id: snapshot.rule_id,
        source_evaluation_id: evaluation.evaluation_id,
        rule_snapshot: snapshot,
        fingerprint: await alertRuleFingerprint(snapshot),
      });
    }
  }
  const alertDraftPreviews = await Promise.all(
    alertDrafts.map(async (draft) => {
      const source = evaluations.find((evaluation) =>
        evaluation.evaluation_id === draft.source_evaluation_id
      );
      if (!source) throw new GatewayRepositoryError("POLICY_REJECTED");
      const previewEvaluation: AlertEvaluation = {
        rule: draft.rule_snapshot,
        status: "not_triggered",
        reason_codes: [],
        observed_at: source.normalized.quote_as_of,
        evaluated_at: deps.now().toISOString(),
        market_session: draft.rule_snapshot.session === "all"
          ? "regular"
          : draft.rule_snapshot.session,
        condition_results: draft.rule_snapshot.conditions.map((condition) => ({
          condition,
          passed: null,
          observed_value: null,
          evidence_ids: [],
        })),
      };
      const preview = await renderAlertV3({
        event_id: draft.id,
        evaluation: previewEvaluation,
        source_evaluation: source,
        context,
        mode: "draft",
      });
      return { draft_id: draft.id, body: preview.body, hash: preview.hash };
    }),
  );
  let rendered;
  try {
    rendered = await renderPublication({
      phase: bundle.phase,
      market_date: bundle.market_date,
      evaluations,
      context,
      comparisons,
    });
  } catch {
    throw new GatewayRepositoryError("POLICY_REJECTED");
  }
  if (envelope.dry_run) {
    return response(200, {
      ok: true,
      dry_run: true,
      evaluation_count: evaluations.length,
      would_write_suggestions: suggestions.length,
      would_create_alert_drafts: alertDrafts.length,
      alert_draft_previews: alertDraftPreviews,
      comparison_count: comparisons.length,
      comparison_coverage: comparisonCoverage(comparisons),
      publication_status: rendered.status,
      preview: rendered.body,
    });
  }
  if (!leaseToken) throw new GatewayRepositoryError("PERSISTENCE_FAILED");
  const persistedInput: PersistedBundle = {
    request_id: envelope.request_id,
    request_lease_token: leaseToken,
    run_id: requireRun(envelope),
    policy_version: activePolicy.version,
    evaluations,
    suggestions,
    holding_state_changes: evaluations.flatMap((evaluation) =>
      evaluation.holding_state_change ? [evaluation.holding_state_change] : []
    ),
    publication: {
      id: deps.newId(),
      idempotency_key: envelope.request_id,
      market_date: bundle.market_date,
      phase: bundle.phase,
      kind: rendered.kind,
      template_version: rendered.template_version,
      rendered_body: rendered.body,
      rendered_hash: rendered.hash,
      status: rendered.status,
    },
  };
  const persisted = await deps.repository.applyDecisionBundle(persistedInput);
  let alertDraftsCreated = 0;
  let alertDraftStatus = alertDrafts.length === 0
    ? "not_applicable"
    : activePolicy.alerts_v3?.enabled
    ? "created"
    : "shadow_preview";
  if (alertDrafts.length > 0 && activePolicy.alerts_v3?.enabled) {
    try {
      const draftReceipt = await deps.repository.createAlertDrafts(
        envelope.request_id,
        alertDrafts,
      );
      alertDraftsCreated = draftReceipt.created_count;
    } catch {
      alertDraftStatus = "failed";
    }
  }
  const delivered = await deliverReadyPublication(
    persisted,
    rendered.parts,
    deps,
  );
  const result = {
    ok: delivered.status !== "delivery_failed" &&
      delivered.status !== "delivery_unknown",
    publication_id: delivered.id,
    publication_status: delivered.status,
    telegram_message_ids: delivered.telegram_message_ids,
    evaluation_count: evaluations.length,
    suggestion_count: suggestions.length,
    alert_drafts_created: alertDraftsCreated,
    alert_draft_status: alertDraftStatus,
    alert_draft_previews: activePolicy.alerts_v3?.shadow
      ? alertDraftPreviews
      : [],
    comparison_count: comparisons.length,
    comparison_coverage: comparisonCoverage(comparisons),
    preview: bundle.phase === "on-demand" ? rendered.body : undefined,
    ...(delivered.status === "delivery_unknown"
      ? { code: "DELIVERY_UNKNOWN" }
      : {}),
    ...(delivered.status === "delivery_failed"
      ? { code: "DELIVERY_FAILED" }
      : {}),
  };
  if (
    delivered.status === "delivery_failed" ||
    delivered.status === "delivery_unknown"
  ) {
    await deps.repository.failRequest(
      envelope.request_id,
      leaseToken,
      String(result.code),
    );
    return response(502, result);
  }
  await deps.repository.completeRequest(
    envelope.request_id,
    leaseToken,
    result,
  );
  return response(200, result);
}

async function buildPortfolioComparisons(
  requests: NonNullable<ReturnType<typeof parseDecisionBundle>["comparisons"]>,
  evaluations: PolicyEvaluation[],
  context: PolicyContext,
  deps: ResolvedDependencies,
): Promise<PortfolioAlternativeComparison[]> {
  if (requests.length === 0) return [];
  const ownerTickers = new Set([
    ...context.holdings.map((holding) => holding.ticker),
    ...context.owner_plans.filter((plan) => plan.active).map((plan) =>
      plan.ticker
    ),
  ]);
  if (requests.some((request) => !ownerTickers.has(request.baseline_ticker))) {
    throw new GatewayRepositoryError("POLICY_REJECTED");
  }
  const tickers = [
    ...new Set(requests.flatMap((request) => [
      request.baseline_ticker,
      request.alternative_ticker,
    ])),
  ];
  const histories = new Map(
    await Promise.all(tickers.map(async (ticker) => {
      try {
        return [ticker, await deps.fetchHistory(ticker)] as const;
      } catch {
        return [ticker, [] as AdjustedBar[]] as const;
      }
    })),
  );
  return requests.map((request) => {
    const alternative = evaluations.find((evaluation) =>
      evaluation.candidate.ticker === request.alternative_ticker
    );
    const evidenceAvailable = alternative !== undefined &&
      request.evidence_ids.every((id) => {
        const evidence = alternative.candidate.evidence.find((item) =>
          item.id === id
        );
        return evidence?.status === "fresh" || evidence?.status === "fallback";
      });
    const checkedRequest =
      alternative?.status === "approved" && evidenceAvailable ? request : {
        ...request,
        prospective_view: "insufficient" as const,
        reason:
          "Gateway policy or current evidence did not support a forward comparison conclusion.",
      };
    return comparePortfolioAlternative(
      checkedRequest,
      histories.get(request.baseline_ticker) ?? [],
      histories.get(request.alternative_ticker) ?? [],
    );
  });
}

function comparisonCoverage(
  comparisons: readonly PortfolioAlternativeComparison[],
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const comparison of comparisons) {
    counts[comparison.coverage_status] =
      (counts[comparison.coverage_status] ?? 0) + 1;
  }
  return counts;
}
