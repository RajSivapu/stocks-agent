import {
  parseAnalysisSubmissionV2,
  type Phase,
  type ProviderAnalysisSubmissionV2,
} from "../../../packages/contracts/src/provider.ts";

export type EvidenceFailureCode = "evidence_stale" | "evidence_missing" |
  "evidence_conflicting" | "corporate_action_pending";

export type EvidenceRun = {
  runId: string;
  phase: Phase;
  marketDate: string;
  startedAt: string;
};

export type ServerEvidenceCategory = "market_snapshot" | "source_search" | "filing" |
  "fundamentals" | "news" | "technicals" | "corporate_action" | "issuer" | "exchange";

export type ServerEvidenceFact = {
  evidenceId: string;
  runId: string;
  sourceRunId: string | null;
  category: ServerEvidenceCategory;
  source: string;
  reference: string | null;
  observedAt: string | null;
  retrievedAt: string;
  revalidatedAt: string | null;
  contentHash: string;
  claims: string[];
  status: "fresh" | "stale" | "conflicting" | "missing" |
    "no_new_material_evidence" | "suspected" | "needs_review" | "clear";
};

export type EvidenceValidation =
  | { ok: true; acceptedEvidenceIds: string[] }
  | { ok: false; code: EvidenceFailureCode };

const CURRENT_RUN_CATEGORIES = new Set<ServerEvidenceCategory>([
  "market_snapshot", "source_search", "news", "technicals", "corporate_action", "issuer", "exchange",
]);
const STABLE_CATEGORIES = new Set<ServerEvidenceCategory>(["filing", "fundamentals"]);

function instant(value: string | null): number | null {
  if (value === null) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function failure(code: EvidenceFailureCode): EvidenceValidation {
  return { ok: false, code };
}

function parsedSubmission(value: unknown): ProviderAnalysisSubmissionV2 | EvidenceValidation {
  try {
    return parseAnalysisSubmissionV2(value);
  } catch (error) {
    const message = error instanceof Error ? error.message : "";
    return failure(
      message.includes("server authority") || message.includes("duplicate evidence")
        ? "evidence_conflicting"
        : "evidence_missing",
    );
  }
}

function factSignature(fact: ServerEvidenceFact): string {
  return JSON.stringify({
    runId: fact.runId,
    sourceRunId: fact.sourceRunId,
    category: fact.category,
    source: fact.source,
    reference: fact.reference,
    observedAt: fact.observedAt,
    retrievedAt: fact.retrievedAt,
    revalidatedAt: fact.revalidatedAt,
    contentHash: fact.contentHash,
    claims: fact.claims,
    status: fact.status,
  });
}

export function validateEvidence(
  run: EvidenceRun,
  rawSubmission: unknown,
  serverFacts: readonly ServerEvidenceFact[],
  nowValue = new Date().toISOString(),
): EvidenceValidation {
  const startedAt = instant(run.startedAt);
  const now = instant(nowValue);
  if (startedAt === null || now === null || now < startedAt) return failure("evidence_stale");
  const submission = parsedSubmission(rawSubmission);
  if ("ok" in submission) return submission;
  if (submission.phase !== run.phase || submission.market_date !== run.marketDate) {
    return failure("evidence_stale");
  }
  if (!submission.analyst.completed || !submission.checker.completed) {
    return failure("evidence_missing");
  }
  for (const dimension of Object.values(submission.dimensions)) {
    if (dimension.status === "missing" || dimension.evidence_ids.length === 0) {
      return failure("evidence_missing");
    }
    if (dimension.status === "conflicting") return failure("evidence_conflicting");
  }

  const facts = new Map<string, ServerEvidenceFact>();
  const signatures = new Map<string, string>();
  for (const fact of serverFacts) {
    if (fact.status === "suspected" || fact.status === "needs_review") {
      return failure("corporate_action_pending");
    }
    const signature = factSignature(fact);
    if (signatures.has(fact.evidenceId) && signatures.get(fact.evidenceId) !== signature) {
      return failure("evidence_conflicting");
    }
    signatures.set(fact.evidenceId, signature);
    facts.set(fact.evidenceId, fact);
  }

  const referenced = new Set(submission.evidence_refs.map((item) => item.evidence_id));
  for (const dimension of Object.values(submission.dimensions)) {
    if (dimension.evidence_ids.some((id) => !referenced.has(id))) {
      return failure("evidence_missing");
    }
  }

  let hasMarketSnapshot = false;
  let hasSourceSearch = false;
  for (const reference of submission.evidence_refs) {
    if (reference.run_id !== run.runId) return failure("evidence_conflicting");
    const fact = facts.get(reference.evidence_id);
    if (!fact) return failure("evidence_missing");
    if (fact.runId !== run.runId || fact.contentHash !== reference.content_hash) {
      return failure("evidence_conflicting");
    }
    if (fact.status === "conflicting") return failure("evidence_conflicting");
    if (fact.status === "missing") return failure("evidence_missing");
    if (fact.status === "stale") return failure("evidence_stale");
    const retrievedAt = instant(fact.retrievedAt);
    const observedAt = instant(fact.observedAt);
    const revalidatedAt = instant(fact.revalidatedAt);
    if (retrievedAt === null || (fact.observedAt !== null && observedAt === null)) {
      return failure("evidence_stale");
    }
    if (CURRENT_RUN_CATEGORIES.has(fact.category) && retrievedAt < startedAt) {
      return failure("evidence_stale");
    }
    if (STABLE_CATEGORIES.has(fact.category) && fact.sourceRunId !== null) {
      if (revalidatedAt === null || revalidatedAt < startedAt || revalidatedAt > now) {
        return failure("evidence_stale");
      }
    }
    if (fact.category === "market_snapshot") {
      hasMarketSnapshot = true;
      const maxAgeMs = run.phase === "pre-market" || run.phase === "post-market"
        ? 18 * 60 * 60 * 1_000
        : 15 * 60 * 1_000;
      if (fact.status !== "fresh" || now - retrievedAt > maxAgeMs) return failure("evidence_stale");
    }
    if (fact.category === "source_search") {
      hasSourceSearch = true;
      if (!["fresh", "no_new_material_evidence"].includes(fact.status) || now - retrievedAt > 30 * 60 * 1_000) {
        return failure("evidence_stale");
      }
    }
    if (fact.category === "technicals" && now - retrievedAt > 30 * 60 * 1_000) {
      return failure("evidence_stale");
    }
    if (fact.category === "news" && observedAt !== null && now - observedAt > 24 * 60 * 60 * 1_000) {
      return failure("evidence_stale");
    }
  }
  if (!hasMarketSnapshot || !hasSourceSearch) return failure("evidence_missing");
  return { ok: true, acceptedEvidenceIds: submission.evidence_refs.map((item) => item.evidence_id) };
}
