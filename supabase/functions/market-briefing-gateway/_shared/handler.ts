import {
  parseArtifactMutationBatch,
  parseDecisionBundle,
  parseGatewayEnvelope,
  type ArtifactMutation,
  type GatewayEnvelope,
  type Phase,
  type VerifiedQuote,
} from "./contracts.ts";
import { isNyseHoliday } from "./market-calendar.ts";
import { fetchAdjustedHistory, fetchVerifiedQuote, type AdjustedBar } from "./market-data.ts";
import { gradeDecision, type DueDecision } from "./outcomes.ts";
import { evaluateCandidate, type PolicyEvaluation } from "./policy.ts";
import { renderPublication } from "./renderer.ts";
import {
  GatewayRepositoryError,
  type GatewayRepository,
  type PersistableArtifactMutation,
  type PersistableArtifactMutationBatch,
  type PersistedBundle,
  type PublicationReceipt,
} from "./repository.ts";
import { sendTelegramParts, TelegramDeliveryError } from "./telegram.ts";

export interface GatewayDependencies {
  repository: GatewayRepository;
  marketAgentSecret: string;
  telegramToken: string;
  telegramChatId: string;
  now?: () => Date;
  newId?: () => string;
  fetchQuote?: (ticker: string, now: Date) => Promise<VerifiedQuote>;
  fetchHistory?: (ticker: string) => Promise<AdjustedBar[]>;
  sendTelegram?: (parts: string[], chatId: string, token: string) => Promise<number[]>;
}

const MAX_BODY_BYTES = 262_144;

function response(status: number, body: Record<string, unknown>): Response {
  const rendered = body.ok === true && !("data" in body)
    ? {
      ...body,
      data: Object.fromEntries(Object.entries(body).filter(([key]) => key !== "ok")),
    }
    : body;
  return Response.json(rendered, {
    status,
    headers: { "cache-control": "no-store", "x-content-type-options": "nosniff" },
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
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_BODY_BYTES)) {
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

function exactKeys(row: Record<string, unknown>, required: readonly string[]): void {
  if (Object.keys(row).some((key) => !required.includes(key)) ||
    required.some((key) => !(key in row))) {
    throw new GatewayHttpError(400, "INVALID_REQUEST");
  }
}

function parseGradePayload(value: unknown): { limit: number } {
  const row = objectValue(value);
  exactKeys(row, ["limit"]);
  if (typeof row.limit !== "number" || !Number.isSafeInteger(row.limit) ||
      row.limit < 1 || row.limit > 50) {
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

function parseStartPayload(value: unknown, currentDate: string): { phase: Phase; market_date: string } {
  const row = objectValue(value);
  exactKeys(row, ["phase", "market_date"]);
  if (!["pre-market", "intraday", "post-market", "on-demand"].includes(String(row.phase)) ||
    row.market_date !== currentDate) {
    throw new GatewayHttpError(400, "INVALID_REQUEST");
  }
  return { phase: row.phase as Phase, market_date: currentDate };
}

function requireRun(envelope: GatewayEnvelope): string {
  if (envelope.run_id === null) throw new GatewayHttpError(400, "INVALID_REQUEST");
  return envelope.run_id;
}

function errorStatus(code: string): number {
  if (code === "RATE_LIMITED") return 429;
  if (code === "CONTEXT_TOO_LARGE") return 413;
  if (code === "POLICY_REJECTED" || code === "CALENDAR_COVERAGE_MISSING") return 409;
  return 500;
}

function stableScore(value: string | null): number | null {
  if (value === null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number) : null;
}

function suggestionFromEvaluation(evaluation: PolicyEvaluation, marketDate: string): Record<string, unknown> | null {
  if (evaluation.final_action === null || evaluation.status === "vetoed") return null;
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
  deps: Required<Pick<GatewayDependencies, "now" | "fetchQuote">> & Pick<GatewayDependencies, "repository">,
): Promise<PersistableArtifactMutationBatch> {
  const currentDate = chicagoDate(deps.now());
  let context: Awaited<ReturnType<GatewayRepository["readContext"]>> | null = null;
  const output: PersistableArtifactMutation[] = [];
  for (const mutation of mutations) {
    if (mutation.kind === "paper_watch_create") {
      context ??= await deps.repository.readContext(runId);
      const quote = await deps.fetchQuote(mutation.ticker, deps.now());
      const latest = context.recent_suggestions.find((item) => item.ticker === mutation.ticker);
      output.push({
        ...mutation,
        entry_ref_price: quote.price,
        created: currentDate,
        agent_view_at_open: latest?.action ?? "no prior view",
        agent_score_at_open: latest?.score ?? null,
      });
    } else if (mutation.kind === "paper_watch_close") {
      const quote = await deps.fetchQuote(mutation.ticker, deps.now());
      output.push({ ...mutation, closed_date: currentDate, close_price: quote.price });
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
    fetchQuote: dependencies.fetchQuote ?? ((ticker: string, now: Date) => fetchVerifiedQuote(ticker, fetch, now)),
    fetchHistory: dependencies.fetchHistory ?? ((ticker: string) => fetchAdjustedHistory(ticker, "1y", fetch)),
    sendTelegram: dependencies.sendTelegram ?? sendTelegramParts,
  };

  return async (request: Request): Promise<Response> => {
    if (request.method !== "POST") return response(405, { ok: false, code: "METHOD_NOT_ALLOWED" });
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
        if (row.market_date !== currentDate) throw new GatewayHttpError(400, "INVALID_REQUEST");
        prepared = parseDecisionBundle(row, row.phase as Phase);
      } else if (envelope.operation === "grade_due_decisions") {
        requireRun(envelope);
        prepared = parseGradePayload(envelope.payload);
      } else if (envelope.operation === "read_context" || envelope.operation === "finish_run") {
        requireRun(envelope);
        const row = objectValue(envelope.payload);
        if (envelope.operation !== "finish_run") exactKeys(row, []);
        prepared = envelope.payload;
      }
    } catch (error) {
      if (error instanceof GatewayHttpError) return response(error.status, { ok: false, code: error.code });
      return response(400, { ok: false, code: "INVALID_REQUEST" });
    }

    if (envelope.dry_run) {
      try {
        if (envelope.operation === "start_run") {
          return response(200, { ok: true, dry_run: true, run_id: deps.newId(), status: "ephemeral" });
        }
        if (envelope.operation === "read_context") {
          return response(200, { ok: true, dry_run: true, context: await deps.repository.readContext(envelope.run_id) });
        }
        if (envelope.operation === "record_artifacts") {
          const normalized = await normalizeArtifacts(
            (prepared as ReturnType<typeof parseArtifactMutationBatch>).mutations,
            requireRun(envelope),
            deps,
          );
          const counts: Record<string, number> = {};
          for (const mutation of normalized.mutations) counts[mutation.kind] = (counts[mutation.kind] ?? 0) + 1;
          return response(200, { ok: true, dry_run: true, receipt: { would_write: normalized.mutations.length, counts } });
        }
        if (envelope.operation === "finish_run") {
          return response(200, { ok: true, dry_run: true, status: "completed", write_counts: {}, publication_statuses: [], telegram_message_ids: [] });
        }
        if (envelope.operation === "grade_due_decisions") {
          const receipt = await gradeDueDecisions(
            (prepared as { limit: number }).limit,
            false,
            deps,
          );
          return response(200, { ok: true, dry_run: true, ...receipt });
        }
        return await evaluateAndPublish(envelope, prepared as ReturnType<typeof parseDecisionBundle>, null, deps);
      } catch (error) {
        const code = error instanceof GatewayRepositoryError ? error.code : "POLICY_REJECTED";
        return response(errorStatus(code), { ok: false, code });
      }
    }

    let leaseToken: string | null = null;
    try {
      const claim = await deps.repository.claimRequest(envelope);
      if (claim.in_progress) return response(409, { ok: false, code: "REQUEST_IN_PROGRESS" });
      if (claim.duplicate) return response(200, objectValue(claim.response));
      leaseToken = claim.lease_token;
      if (!leaseToken) throw new GatewayRepositoryError("PERSISTENCE_FAILED");

      if (envelope.operation === "start_run") {
        const start = prepared as { phase: Phase; market_date: string };
        const activePolicy = await deps.repository.activePolicy();
        if (currentDate.slice(0, 4) !== String(activePolicy.market_calendar_year)) {
          throw new GatewayRepositoryError("CALENDAR_COVERAGE_MISSING");
        }
        if (start.phase !== "on-demand" && isNyseHoliday(currentDate, activePolicy.nyse_holidays)) {
          if (start.phase !== "pre-market") {
            const result = { ok: true, status: "suppressed", holiday: true, run_id: null };
            await deps.repository.completeRequest(envelope.request_id, leaseToken, result);
            return response(200, result);
          }
          const rendered = await renderPublication({ phase: start.phase, market_date: currentDate, evaluations: [], holiday: true });
          const persisted = await deps.repository.applyDecisionBundle({
            request_id: envelope.request_id,
            request_lease_token: leaseToken,
            run_id: null,
            policy_version: activePolicy.version,
            evaluations: [],
            suggestions: [],
            holding_state_changes: [],
            publication: {
              id: deps.newId(), idempotency_key: envelope.request_id, market_date: currentDate,
              phase: start.phase, kind: rendered.kind, template_version: rendered.template_version,
              rendered_body: rendered.body, rendered_hash: rendered.hash, status: rendered.status,
            },
          });
          const delivered = await deliverReadyPublication(persisted, rendered.parts, deps);
          const result = { ok: true, holiday: true, run_id: null, publication_status: delivered.status, telegram_message_ids: delivered.telegram_message_ids };
          if (delivered.status === "delivery_failed" || delivered.status === "delivery_unknown") {
            const code = delivered.status === "delivery_unknown" ? "DELIVERY_UNKNOWN" : "DELIVERY_FAILED";
            await deps.repository.failRequest(envelope.request_id, leaseToken, code);
            return response(502, { ...result, ok: false, code });
          }
          await deps.repository.completeRequest(envelope.request_id, leaseToken, result);
          return response(200, result);
        }
        const runId = await deps.repository.startRun(envelope.request_id, leaseToken, start.phase);
        const result = { ok: true, run_id: runId, phase: start.phase, market_date: currentDate };
        await deps.repository.completeRequest(envelope.request_id, leaseToken, result);
        return response(200, result);
      }
      if (envelope.operation === "read_context") {
        const result = { ok: true, context: await deps.repository.readContext(envelope.run_id) };
        await deps.repository.completeRequest(envelope.request_id, leaseToken, result);
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
          ...await gradeDueDecisions((prepared as { limit: number }).limit, true, deps),
        };
        await deps.repository.completeRequest(envelope.request_id, leaseToken, result);
        return response(200, result);
      }
      if (envelope.operation === "finish_run") {
        const receipt = await deps.repository.finishRun(requireRun(envelope));
        const result = { ok: true, ...receipt };
        await deps.repository.completeRequest(envelope.request_id, leaseToken, result);
        return response(200, result);
      }
      return await evaluateAndPublish(
        envelope,
        prepared as ReturnType<typeof parseDecisionBundle>,
        leaseToken,
        deps,
      );
    } catch (error) {
      const code = error instanceof GatewayRepositoryError ? error.code : "PERSISTENCE_FAILED";
      if (leaseToken) {
        try {
          await deps.repository.failRequest(envelope.request_id, leaseToken, code);
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
  sendTelegram: (parts: string[], chatId: string, token: string) => Promise<number[]>;
};

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
        incomplete: grades.filter((grade) => grade.coverage_status !== "complete").length,
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
  if (persisted.status === "suppressed" || persisted.status === "delivered" ||
    persisted.status === "delivery_unknown" || persisted.status === "sending") return persisted;
  const claim = await deps.repository.claimPublication(persisted.idempotency_key);
  if (!claim.claimed || !claim.lease_token) return claim.receipt;
  try {
    const ids = await deps.sendTelegram(parts, deps.telegramChatId, deps.telegramToken);
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
      delivery.kind === "definitive" ? "TELEGRAM_REJECTED" : "TELEGRAM_OUTCOME_UNKNOWN",
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
  const tickers = [...new Set([
    ...bundle.candidates.map((candidate) => candidate.ticker),
    ...context.holdings.map((holding) => holding.ticker),
  ])];
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
    evaluateCandidate(candidate, context, activePolicy, quotes.get(candidate.ticker) ?? null, deps.now(), deps.newId)
  );
  const suggestions = evaluations.map((evaluation) => suggestionFromEvaluation(evaluation, bundle.market_date))
    .filter((item): item is Record<string, unknown> => item !== null);
  let rendered;
  try {
    rendered = await renderPublication({
      phase: bundle.phase,
      market_date: bundle.market_date,
      evaluations,
      context,
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
  const delivered = await deliverReadyPublication(persisted, rendered.parts, deps);
  const result = {
    ok: delivered.status !== "delivery_failed" && delivered.status !== "delivery_unknown",
    publication_id: delivered.id,
    publication_status: delivered.status,
    telegram_message_ids: delivered.telegram_message_ids,
    evaluation_count: evaluations.length,
    suggestion_count: suggestions.length,
    preview: bundle.phase === "on-demand" ? rendered.body : undefined,
    ...(delivered.status === "delivery_unknown" ? { code: "DELIVERY_UNKNOWN" } : {}),
    ...(delivered.status === "delivery_failed" ? { code: "DELIVERY_FAILED" } : {}),
  };
  if (delivered.status === "delivery_failed" || delivered.status === "delivery_unknown") {
    await deps.repository.failRequest(envelope.request_id, leaseToken, String(result.code));
    return response(502, result);
  }
  await deps.repository.completeRequest(envelope.request_id, leaseToken, result);
  return response(200, result);
}
