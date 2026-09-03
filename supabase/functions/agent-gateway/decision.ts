import type { ProviderAnalysisSubmissionV2 } from "../../../packages/contracts/src/provider.ts";
import type {
  DecisionCandidate,
  GatewayReadContext,
  PolicyConfig,
  VerifiedQuote,
} from "../market-briefing-gateway/_shared/contracts.ts";
import { evaluateCandidate, type PolicyEvaluation } from "./policy.ts";
import {
  applyCorporateActionQuarantine,
  outcomeDoesNotUpgrade,
} from "./policy.ts";
import { renderPublication } from "./renderer.ts";
import {
  type EvidenceFailureCode,
  type ServerEvidenceFact,
  validateEvidence,
} from "./evidence.ts";
import type {
  PacketEvidenceFact,
  PacketSearchReceipt,
} from "./evidence-packet.ts";

export class DecisionError extends Error {
  readonly code: EvidenceFailureCode | "POLICY_REJECTED";
  constructor(code: DecisionError["code"]) {
    super(code);
    this.code = code;
  }
}

export type ServerDecisionContext = {
  run_id: string;
  phase: "pre-market" | "intraday" | "post-market" | "on-demand";
  market_date: string;
  started_at: string;
  policy: PolicyConfig;
  context: GatewayReadContext;
  corporate_actions: Array<
    { ticker: string; state: "clear" | "suspected" | "needs_review" }
  >;
};

export type PreparedDecision = {
  provider_submission: Omit<ProviderAnalysisSubmissionV2, "evidence_packets">;
  evidence: PacketEvidenceFact[];
  search_receipts: PacketSearchReceipt[];
  policy_quotes: Array<VerifiedQuote & { retrieved_at: string }>;
  policy_version: number;
  evaluations: Array<Record<string, unknown>>;
  suggestions: Array<Record<string, unknown>>;
  holding_state: Array<Record<string, unknown>>;
  publication: {
    market_date: string;
    phase: string;
    kind: string;
    template_version: number;
    rendered_body: string;
    rendered_parts: string[];
    rendered_hash: string;
    status: "ready" | "suppressed";
  };
  parts: string[];
};

function evidenceKind(
  category: PacketEvidenceFact["category"],
): DecisionCandidate["evidence"][number]["kind"] {
  if (category === "market_snapshot") return "quote";
  if (category === "filing" || category === "fundamentals") {
    return "fundamentals";
  }
  if (category === "technicals") return "technicals";
  if (category === "news" || category === "source_search") return "news";
  return "event";
}

function evidenceStatus(
  status: PacketEvidenceFact["status"],
): DecisionCandidate["evidence"][number]["status"] {
  if (
    status === "fresh" || status === "clear" ||
    status === "no_new_material_evidence"
  ) return "fresh";
  if (status === "stale") return "stale";
  if (status === "conflicting") return "conflicting";
  if (status === "missing") return "missing";
  return "unsupported";
}

function validationFacts(
  runId: string,
  facts: PacketEvidenceFact[],
): ServerEvidenceFact[] {
  return facts.map((fact) => ({
    evidenceId: fact.evidence_id,
    runId,
    sourceRunId: fact.source_run_id,
    category: fact.category,
    source: fact.source_identifier,
    reference: fact.reference_identifier,
    observedAt: fact.observed_at,
    retrievedAt: fact.retrieved_at,
    revalidatedAt: fact.revalidated_at,
    contentHash: fact.content_hash,
    claims: fact.claims,
    status: fact.status,
  }));
}

function verifiedCandidate(
  candidate: DecisionCandidate,
  facts: Map<string, PacketEvidenceFact>,
): DecisionCandidate {
  const evidence = [...facts.values()].map((fact) => ({
    id: fact.evidence_id,
    kind: evidenceKind(fact.category),
    source: fact.source_identifier,
    status: evidenceStatus(fact.status),
    observed_at: fact.observed_at,
    retrieved_at: fact.retrieved_at,
    reference: fact.reference_identifier,
    claims: fact.claims,
  }));
  const known = new Set(evidence.map((item) => item.id));
  if (
    candidate.factors.some((factor) =>
      factor.evidence_ids.some((id) => !known.has(id))
    )
  ) {
    throw new DecisionError("evidence_missing");
  }
  return { ...structuredClone(candidate), evidence };
}

function suggestion(
  evaluation: PolicyEvaluation,
  marketDate: string,
): Record<string, unknown> | null {
  if (evaluation.final_action === null || evaluation.status === "vetoed") {
    return null;
  }
  const candidate = evaluation.candidate;
  const joined = (stance: "bull" | "bear") => {
    const text = candidate.factors.filter((factor) => factor.stance === stance)
      .map((factor) => factor.text).join(" ");
    return text || null;
  };
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
    bull: joined("bull"),
    bear: joined("bear"),
    decisive_factor: candidate.decisive_factor,
    risk_verdict: evaluation.status,
    reason: evaluation.reason_codes.join(","),
    score: candidate.health_score === null
      ? null
      : Math.round(Number(candidate.health_score)),
    price_at_suggestion: evaluation.normalized.verified_price,
    evidence_as_of: evaluation.normalized.quote_as_of,
    invalidation_price: candidate.invalidation_price,
  };
}

async function sha256(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  return Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function prepareDecision(
  submission: ProviderAnalysisSubmissionV2,
  strictCandidates: DecisionCandidate[],
  facts: PacketEvidenceFact[],
  searchReceipts: PacketSearchReceipt[],
  server: ServerDecisionContext,
  quotes: VerifiedQuote[],
  now: Date,
  newId: () => string = () => crypto.randomUUID(),
): Promise<PreparedDecision> {
  if (
    submission.phase !== server.phase ||
    submission.market_date !== server.market_date
  ) {
    throw new DecisionError("evidence_stale");
  }
  const validation = validateEvidence(
    {
      runId: server.run_id,
      phase: server.phase,
      marketDate: server.market_date,
      startedAt: server.started_at,
    },
    submission,
    validationFacts(server.run_id, facts),
    now.toISOString(),
  );
  if (!validation.ok) throw new DecisionError(validation.code);
  if (
    !searchReceipts.some((receipt) =>
      receipt.result_status !== "source_unavailable" &&
      Date.parse(receipt.searched_at) >= Date.parse(server.started_at)
    )
  ) {
    throw new DecisionError("evidence_missing");
  }
  const factMap = new Map(facts.map((fact) => [fact.evidence_id, fact]));
  const quoteMap = new Map(quotes.map((quote) => [quote.ticker, quote]));
  const corporateActions = new Map(
    server.corporate_actions.map((item) => [item.ticker, item.state]),
  );
  const policyContext = structuredClone(server.context);
  policyContext.holding_quotes = Object.fromEntries(
    policyContext.holdings.flatMap((holding) => {
      const quote = quoteMap.get(holding.ticker);
      return quote ? [[holding.ticker, quote]] : [];
    }),
  );
  const evaluations = strictCandidates.map((candidate) => {
    let checked = verifiedCandidate(candidate, factMap);
    checked = applyCorporateActionQuarantine(
      checked,
      corporateActions.get(checked.ticker) ?? "clear",
    );
    const evaluated = evaluateCandidate(
      checked,
      policyContext,
      server.policy,
      quoteMap.get(checked.ticker) ?? null,
      now,
      newId,
    );
    if (
      evaluated.final_action !== null &&
      !outcomeDoesNotUpgrade(candidate.action, evaluated.final_action)
    ) {
      throw new DecisionError("POLICY_REJECTED");
    }
    return evaluated;
  });
  const suggestions = evaluations.map((item) =>
    suggestion(item, server.market_date)
  )
    .filter((item): item is Record<string, unknown> => item !== null);
  const rendered = await renderPublication({
    phase: server.phase,
    market_date: server.market_date,
    evaluations,
    context: policyContext,
  });
  const persistedEvaluations = await Promise.all(
    evaluations.map(async (evaluation) => ({
      id: evaluation.evaluation_id,
      candidate_id: evaluation.candidate_id,
      input_digest: await sha256(evaluation.candidate),
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
  const { evidence_packets: _packets, ...providerSubmission } = submission;
  return {
    provider_submission: providerSubmission,
    evidence: facts,
    search_receipts: searchReceipts,
    policy_quotes: quotes.map((quote) => ({
      ...quote,
      retrieved_at: now.toISOString(),
    })),
    policy_version: server.policy.version,
    evaluations: persistedEvaluations,
    suggestions,
    holding_state: evaluations.flatMap((item) =>
      item.holding_state_change ? [item.holding_state_change] : []
    ),
    publication: {
      market_date: server.market_date,
      phase: server.phase,
      kind: rendered.kind,
      template_version: rendered.template_version,
      rendered_body: rendered.body,
      rendered_parts: rendered.parts,
      rendered_hash: rendered.hash,
      status: rendered.status,
    },
    parts: rendered.parts,
  };
}
