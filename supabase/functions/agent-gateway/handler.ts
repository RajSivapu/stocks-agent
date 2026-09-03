import { parseAnalysisSubmissionV2, parseProviderEnvelopeV2, type ProviderOperation } from "../../../packages/contracts/src/provider.ts";
import { readBoundedJson } from "../_shared/bounded-json.ts";
import { HttpError, jsonResponse } from "../_shared/errors.ts";
import {
  parseArtifactMutationBatch,
  parseDecisionBundle,
  type DecisionCandidate,
  type GatewayReadContext,
  type PolicyConfig,
  type VerifiedQuote,
} from "../market-briefing-gateway/_shared/contracts.ts";
import { fetchVerifiedQuote } from "./market-data.ts";
import {
  createEvidencePacket,
  type PacketEvidenceFact,
} from "./evidence-packet.ts";
import { fetchResearchSources, parseEvidenceReadPayload } from "./source-fetch.ts";
import { DecisionError, prepareDecision, type PreparedDecision, type ServerDecisionContext } from "./decision.ts";
import { verifyEvidencePackets } from "./evidence-packet.ts";
import { sendTelegramParts, TelegramDeliveryError } from "./telegram.ts";

const MAX_BODY_BYTES = 64 * 1024;
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
const BEARER = new RegExp(`^Bearer (${UUID})\\.([A-Za-z0-9_-]{43})$`, "i");

export interface AgentGatewayRepository {
  invoke(operation: ProviderOperation, request: Record<string, unknown>): Promise<Record<string, unknown>>;
  applyAnalysis(request: Record<string, unknown>): Promise<Record<string, unknown>>;
  finishAnalysisDelivery(request: Record<string, unknown>): Promise<Record<string, unknown>>;
}

export type AgentGatewayDependencies = {
  repository: AgentGatewayRepository;
  evidenceSigningKey: Uint8Array;
  now?: () => Date;
  fetchQuote?: typeof fetchVerifiedQuote;
  fetchSource?: typeof fetch;
  telegramToken: string;
  sendTelegram?: typeof sendTelegramParts;
  newId?: () => string;
};

function errorResponse(status: number, code: string): Response {
  return jsonResponse(status, { ok: false, error: { code } });
}

function exactObject(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid payload");
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== keys.length || keys.some((key) => !Object.hasOwn(row, key))) {
    throw new Error("invalid payload");
  }
  return row;
}

function uuidOrNull(value: unknown): boolean {
  return value === null || (typeof value === "string" && new RegExp(`^${UUID}$`, "i").test(value));
}

function handshakeFinishPayload(value: unknown): Record<string, unknown> {
  const row = exactObject(value, ["contract_version", "challenge", "source_checks"]);
  if (row.contract_version !== 2 || typeof row.challenge !== "string" ||
    !/^[0-9a-f]{64}$/.test(row.challenge) || !Array.isArray(row.source_checks) ||
    row.source_checks.length !== 3) throw new Error("invalid handshake receipt");
  const allowedHosts = new Set(["query1.finance.yahoo.com", "www.sec.gov", "finnhub.io"]);
  const observedHosts = new Set<string>();
  const checks = row.source_checks.map((value) => {
    const check = exactObject(value, ["host", "status", "content_hash", "observed_at"]);
    if (typeof check.host !== "string" || !allowedHosts.has(check.host) || observedHosts.has(check.host) ||
      !["reachable", "unreachable"].includes(String(check.status)) ||
      typeof check.observed_at !== "string" || check.observed_at.length > 40 ||
      Number.isNaN(Date.parse(check.observed_at)) ||
      (check.status === "reachable" && (typeof check.content_hash !== "string" ||
        !/^[0-9a-f]{64}$/.test(check.content_hash))) ||
      (check.status === "unreachable" && check.content_hash !== null)) {
      throw new Error("invalid handshake receipt");
    }
    observedHosts.add(check.host);
    return check;
  });
  return { contract_version: 2, challenge: row.challenge, source_checks: checks };
}

function preparePayload(operation: ProviderOperation, payload: unknown): unknown {
  if (operation === "start_run") {
    const row = exactObject(payload, ["trigger_request_id"]);
    if (!uuidOrNull(row.trigger_request_id)) throw new Error("invalid payload");
    return row;
  }
  if (operation === "read_bounded_context") {
    const parsed = parseEvidenceReadPayload(payload);
    return parsed.research ? { research: parsed.research } : {};
  }
  if (operation === "finish_run") {
    if (payload && typeof payload === "object" && !Array.isArray(payload) &&
      Object.keys(payload as Record<string, unknown>).length === 0) return payload;
    return handshakeFinishPayload(payload);
  }
  if (operation === "submit_analysis") {
    const submission = parseAnalysisSubmissionV2(payload);
    const bundle = parseDecisionBundle({
      phase: submission.phase,
      market_date: submission.market_date,
      title: submission.title,
      candidates: submission.candidates,
    }, submission.phase);
    return { ...submission, candidates: bundle.candidates };
  }
  if (operation === "record_permitted_artifacts") return parseArtifactMutationBatch(payload);
  const row = exactObject(payload, ["limit"]);
  if (!Number.isInteger(row.limit) || Number(row.limit) < 1 || Number(row.limit) > 50) {
    throw new Error("invalid payload");
  }
  return row;
}

function boundedTicker(value: unknown): string | null {
  return typeof value === "string" && /^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$/.test(value) && value.length <= 15
    ? value
    : null;
}

function contextTickers(context: Record<string, unknown>): string[] {
  const values: string[] = ["SPY"];
  for (const key of ["holdings", "plans", "radar"] as const) {
    const rows = context[key];
    if (!Array.isArray(rows)) continue;
    for (const row of rows) {
      const ticker = row && typeof row === "object" && !Array.isArray(row)
        ? boundedTicker((row as Record<string, unknown>).ticker)
        : null;
      if (ticker) values.push(ticker);
    }
  }
  return [...new Set(values)].slice(0, 60);
}

async function quoteFact(
  ticker: string,
  phase: string,
  now: Date,
  fetchQuote: typeof fetchVerifiedQuote,
): Promise<{ fact: PacketEvidenceFact; quote: Record<string, unknown> } | null> {
  try {
    const quote = await fetchQuote(ticker, fetch, now);
    const sourceTime = Date.parse(quote.as_of);
    const maxAge = phase === "intraday" || phase === "on-demand" ? 15 * 60_000 : 18 * 60 * 60_000;
    const status = Number.isFinite(sourceTime) && sourceTime <= now.valueOf() + 5 * 60_000 && now.valueOf() - sourceTime <= maxAge
      ? "fresh"
      : "stale";
    const quoteRecord = {
      ticker: quote.ticker,
      price: quote.price,
      previous_close: quote.previous_close,
      as_of: quote.as_of,
      retrieved_at: now.toISOString(),
      market_state: quote.market_state,
      source: quote.source,
      status,
    };
    const contentHash = Array.from(new Uint8Array(await crypto.subtle.digest(
      "SHA-256", new TextEncoder().encode(JSON.stringify(quoteRecord)),
    )), (byte) => byte.toString(16).padStart(2, "0")).join("");
    return {
      quote: quoteRecord,
      fact: {
        evidence_id: `quote-${ticker.toLowerCase()}`,
        source_run_id: null,
        category: "market_snapshot",
        source_identifier: quote.source,
        reference_identifier: `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}`,
        observed_at: quote.as_of,
        retrieved_at: now.toISOString(),
        revalidated_at: null,
        content_hash: contentHash,
        claims: [`${ticker} server quote ${quote.price}`],
        status,
      },
    };
  } catch {
    return null;
  }
}

async function enrichContext(
  rawContext: Record<string, unknown>,
  parsedPayload: ReturnType<typeof parseEvidenceReadPayload>,
  deps: Required<Pick<AgentGatewayDependencies, "now" | "fetchQuote" | "fetchSource">> & AgentGatewayDependencies,
): Promise<Record<string, unknown>> {
  if (rawContext.handshake === true) return rawContext;
  const runId = typeof rawContext.run_id === "string" ? rawContext.run_id : "";
  const phase = typeof rawContext.phase === "string" ? rawContext.phase : "";
  const marketDate = typeof rawContext.market_date === "string" ? rawContext.market_date : "";
  if (!new RegExp(`^${UUID}$`, "i").test(runId) ||
    !["pre-market", "intraday", "post-market", "on-demand"].includes(phase) ||
    !/^\d{4}-\d{2}-\d{2}$/.test(marketDate)) throw new Error("invalid server context");
  const now = deps.now();
  const quoteResults = await Promise.all(contextTickers(rawContext).map((ticker) =>
    quoteFact(ticker, phase, now, deps.fetchQuote)
  ));
  const facts = quoteResults.flatMap((item) => item ? [item.fact] : []);
  let searchReceipt = null;
  if (parsedPayload.research) {
    const research = await fetchResearchSources(parsedPayload.research, deps.fetchSource, now);
    facts.push(...research.facts);
    searchReceipt = research.search_receipt;
  }
  const evidencePacket = await createEvidencePacket({
    version: 1,
    run_id: runId,
    phase: phase as "pre-market" | "intraday" | "post-market" | "on-demand",
    market_date: marketDate,
    issued_at: now.toISOString(),
    expires_at: new Date(now.valueOf() + 15 * 60_000).toISOString(),
    facts,
    search_receipt: searchReceipt,
  }, deps.evidenceSigningKey);
  return {
    ...rawContext,
    server_quotes: quoteResults.flatMap((item) => item ? [item.quote] : []),
    evidence_packet: evidencePacket,
  };
}

function serverDecisionContext(value: unknown): ServerDecisionContext {
  const row = exactObject(value, [
    "status", "lease_token", "run_id", "phase", "market_date", "started_at",
    "policy", "context", "corporate_actions",
  ]);
  if (!new RegExp(`^${UUID}$`, "i").test(String(row.run_id)) ||
    !new RegExp(`^${UUID}$`, "i").test(String(row.lease_token)) ||
    !["claimed", "dry_run"].includes(String(row.status)) ||
    !["pre-market", "intraday", "post-market", "on-demand"].includes(String(row.phase)) ||
    typeof row.market_date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(row.market_date) ||
    typeof row.started_at !== "string" || Number.isNaN(Date.parse(row.started_at)) ||
    !row.policy || typeof row.policy !== "object" || Array.isArray(row.policy) ||
    !row.context || typeof row.context !== "object" || Array.isArray(row.context) ||
    !Array.isArray(row.corporate_actions)) throw new Error("invalid server context");
  return {
    run_id: row.run_id as string,
    phase: row.phase as ServerDecisionContext["phase"],
    market_date: row.market_date,
    started_at: row.started_at,
    policy: structuredClone(row.policy) as PolicyConfig,
    context: structuredClone(row.context) as GatewayReadContext,
    corporate_actions: structuredClone(row.corporate_actions) as ServerDecisionContext["corporate_actions"],
  };
}

function candidateTickers(submission: { candidates: DecisionCandidate[] }, server: ServerDecisionContext): string[] {
  const candidates = submission.candidates;
  return [...new Set([
    ...candidates.map((candidate) => candidate.ticker),
    ...server.context.holdings.map((holding) => holding.ticker),
  ])].slice(0, 60);
}

function safeAnalysisResponse(value: unknown): Record<string, unknown> {
  const row = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
  const response = row.response;
  if (!response || typeof response !== "object" || Array.isArray(response)) throw new Error("invalid analysis receipt");
  return response as Record<string, unknown>;
}

async function submitAnalysis(
  authority: { connectionId: string; secretDigest: string },
  envelope: ReturnType<typeof parseProviderEnvelopeV2>,
  submission: ReturnType<typeof parseAnalysisSubmissionV2> & { candidates: DecisionCandidate[] },
  deps: Required<Pick<AgentGatewayDependencies, "now" | "fetchQuote" | "sendTelegram" | "newId">> & AgentGatewayDependencies,
): Promise<Record<string, unknown>> {
  const baseRequest = {
    connection_id: authority.connectionId,
    secret_digest: authority.secretDigest,
    contract_version: envelope.contract_version,
    operation: envelope.operation,
    request_id: envelope.request_id,
    run_id: envelope.run_id,
    dry_run: envelope.dry_run,
    payload: submission,
  };
  const claim = await deps.repository.invoke("submit_analysis", baseRequest);
  if (claim.status !== "claimed" && claim.status !== "dry_run") {
    return claim;
  }
  const server = serverDecisionContext(claim);
  let verified;
  try {
    verified = await verifyEvidencePackets(
      submission.evidence_packets,
      deps.evidenceSigningKey,
      server.run_id,
      deps.now(),
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "";
    throw new DecisionError(
      message.includes("expired") || message.includes("run does not match") ||
        message.includes("metadata conflicts")
        ? "evidence_stale"
        : "evidence_conflicting",
    );
  }
  if (verified.phase !== server.phase || verified.market_date !== server.market_date) {
    throw new DecisionError("evidence_stale");
  }
  const quotePairs = await Promise.all(candidateTickers(submission, server).map(async (ticker) => {
    try {
      return await deps.fetchQuote(ticker, fetch, deps.now());
    } catch {
      return null;
    }
  }));
  const quotes = quotePairs.filter((quote): quote is VerifiedQuote => quote !== null);
  const prepared = await prepareDecision(
    submission,
    submission.candidates,
    verified.facts,
    verified.search_receipts,
    server,
    quotes,
    deps.now(),
    deps.newId,
  );
  if (envelope.dry_run) {
    return {
      status: "dry_run",
      writes: 0,
      telegram: { status: "not_sent", message_ids: [] },
      evaluation_count: prepared.evaluations.length,
      would_write_suggestions: prepared.suggestions.length,
      publication_status: prepared.publication.status,
      preview: prepared.publication.rendered_body,
    };
  }
  const { parts: _parts, ...decision } = prepared;
  const applied = await deps.repository.applyAnalysis({
    connection_id: authority.connectionId,
    secret_digest: authority.secretDigest,
    request_id: envelope.request_id,
    run_id: server.run_id,
    lease_token: claim.lease_token,
    decision,
  });
  if (applied.delivery_required !== true) return safeAnalysisResponse(applied);
  if (typeof applied.chat_id !== "string" || !/^[1-9][0-9]{0,15}$/.test(applied.chat_id) ||
    typeof applied.delivery_lease !== "string" || !new RegExp(`^${UUID}$`, "i").test(applied.delivery_lease)) {
    throw new Error("invalid delivery claim");
  }
  let status: "delivered" | "delivery_failed" | "delivery_unknown" = "delivered";
  let messageIds: number[] = [];
  try {
    messageIds = await deps.sendTelegram(prepared.parts, applied.chat_id, deps.telegramToken);
  } catch (error) {
    const delivery = error instanceof TelegramDeliveryError ? error : new TelegramDeliveryError("ambiguous", []);
    status = delivery.kind === "definitive" ? "delivery_failed" : "delivery_unknown";
    messageIds = delivery.partialMessageIds;
  }
  const finished = await deps.repository.finishAnalysisDelivery({
    connection_id: authority.connectionId,
    secret_digest: authority.secretDigest,
    request_id: envelope.request_id,
    run_id: server.run_id,
    delivery_lease: applied.delivery_lease,
    status,
    message_ids: messageIds,
  });
  return safeAnalysisResponse(finished);
}

function decodedSecret(value: string): Uint8Array | null {
  try {
    const padded = value.replaceAll("-", "+").replaceAll("_", "/") + "=";
    const decoded = atob(padded);
    if (decoded.length !== 32) return null;
    return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
  } catch {
    return null;
  }
}

async function credential(request: Request): Promise<{ connectionId: string; secretDigest: string } | null> {
  const match = BEARER.exec(request.headers.get("authorization") ?? "");
  if (!match) return null;
  const secret = decodedSecret(match[2]);
  if (!secret) return null;
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", secret.slice().buffer as ArrayBuffer));
  return {
    connectionId: match[1].toLowerCase(),
    secretDigest: Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join(""),
  };
}

export function createAgentGatewayHandler(dependencies: AgentGatewayDependencies) {
  const deps = {
    ...dependencies,
    now: dependencies.now ?? (() => new Date()),
    fetchQuote: dependencies.fetchQuote ?? fetchVerifiedQuote,
    fetchSource: dependencies.fetchSource ?? fetch,
    sendTelegram: dependencies.sendTelegram ?? sendTelegramParts,
    newId: dependencies.newId ?? (() => crypto.randomUUID()),
  };
  return async (request: Request): Promise<Response> => {
    if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
    const authority = await credential(request);
    if (!authority) return errorResponse(401, "UNAUTHORIZED");
    let envelope;
    let payload: unknown;
    try {
      envelope = parseProviderEnvelopeV2(await readBoundedJson(request, MAX_BODY_BYTES));
      payload = preparePayload(envelope.operation, envelope.payload);
    } catch (error) {
      return error instanceof HttpError && error.status === 413
        ? errorResponse(413, "REQUEST_TOO_LARGE")
        : errorResponse(400, "INVALID_REQUEST");
    }
    try {
      if (envelope.operation === "submit_analysis") {
        const result = await submitAnalysis(
          authority,
          envelope,
          payload as ReturnType<typeof parseAnalysisSubmissionV2> & { candidates: DecisionCandidate[] },
          deps,
        );
        return jsonResponse(200, { ok: true, data: result });
      }
      let result = await deps.repository.invoke(envelope.operation, {
        connection_id: authority.connectionId,
        secret_digest: authority.secretDigest,
        contract_version: envelope.contract_version,
        operation: envelope.operation,
        request_id: envelope.request_id,
        run_id: envelope.run_id,
        dry_run: envelope.dry_run,
        payload,
      });
      if (envelope.operation === "read_bounded_context") {
        result = await enrichContext(
          result,
          parseEvidenceReadPayload(payload),
          deps,
        );
      }
      return jsonResponse(200, { ok: true, data: result });
    } catch (error) {
      const code = error && typeof error === "object" && "code" in error
        ? String((error as { code: unknown }).code)
        : "";
      if (code === "UNAUTHORIZED" || code === "42501") return errorResponse(401, "UNAUTHORIZED");
      if (code === "RATE_LIMITED" || code === "54000") return errorResponse(429, "RATE_LIMITED");
      if (code === "REQUEST_CONFLICT" || code === "23505") return errorResponse(409, "REQUEST_CONFLICT");
      if (["evidence_stale", "evidence_missing", "evidence_conflicting", "corporate_action_pending", "POLICY_REJECTED"].includes(code)) {
        return errorResponse(409, code.toUpperCase());
      }
      return errorResponse(500, "GATEWAY_UNAVAILABLE");
    }
  };
}
