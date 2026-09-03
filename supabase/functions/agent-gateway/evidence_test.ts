import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { validateEvidence, type EvidenceRun, type ServerEvidenceFact } from "./evidence.ts";

const RUN: EvidenceRun = {
  runId: "11111111-1111-4111-8111-111111111111",
  phase: "intraday",
  marketDate: "2026-09-02",
  startedAt: "2026-09-02T17:00:00.000Z",
};

const DIMENSIONS = [
  "fundamentals", "valuation", "catalyst", "technical", "portfolio_fit", "downside",
  "bear_case", "invalidation", "decisive_factor",
] as const;

function analysisSubmission(): Record<string, any> {
  return {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "Independent intraday review",
    suggestion_only: true,
    provider: "claude",
    model: "configured-by-owner",
    analyst: { completed: true, action: "watch", confidence: "medium", thesis: "Evidence is mixed." },
    checker: { completed: true, verdict: "approve", reason: "No unsupported conclusion found." },
    dimensions: Object.fromEntries(DIMENSIONS.map((name) => [name, {
      status: "supported", summary: `${name} was reviewed.`,
      evidence_ids: ["quote-current", "search-current"],
    }])) as Record<typeof DIMENSIONS[number], { status: string; summary: string; evidence_ids: string[] }>,
    evidence_refs: [
      { evidence_id: "quote-current", run_id: RUN.runId, content_hash: "a".repeat(64) },
      { evidence_id: "search-current", run_id: RUN.runId, content_hash: "b".repeat(64) },
    ],
    prior_suggestion_ids: [],
    candidates: [],
  };
}

function facts(): ServerEvidenceFact[] {
  return [
    {
      evidenceId: "quote-current",
      runId: RUN.runId,
      sourceRunId: null,
      category: "market_snapshot",
      source: "yahoo-chart",
      reference: "AAPL",
      observedAt: "2026-09-02T17:04:00.000Z",
      retrievedAt: "2026-09-02T17:05:00.000Z",
      revalidatedAt: null,
      contentHash: "a".repeat(64),
      claims: ["Server quote snapshot"],
      status: "fresh",
    },
    {
      evidenceId: "search-current",
      runId: RUN.runId,
      sourceRunId: null,
      category: "source_search",
      source: "allowed-search",
      reference: "news and filings",
      observedAt: null,
      retrievedAt: "2026-09-02T17:06:00.000Z",
      revalidatedAt: null,
      contentHash: "b".repeat(64),
      claims: [],
      status: "no_new_material_evidence",
    },
  ];
}

Deno.test("current-run server snapshot and explicit source search satisfy the evidence gate", () => {
  assertEquals(validateEvidence(RUN, analysisSubmission(), facts(), "2026-09-02T17:10:00.000Z"), {
    ok: true,
    acceptedEvidenceIds: ["quote-current", "search-current"],
  });
});

Deno.test("copied morning packets and old current-run searches are stale intraday", () => {
  const morning = facts().map((fact) => ({
    ...fact,
    retrievedAt: "2026-09-02T13:00:00.000Z",
  }));
  assertEquals(validateEvidence(RUN, analysisSubmission(), morning, "2026-09-02T17:10:00.000Z"), {
    ok: false, code: "evidence_stale",
  });

  const oldSearch = facts();
  oldSearch[1] = { ...oldSearch[1], retrievedAt: "2026-09-02T16:00:00.000Z" };
  assertEquals(validateEvidence(RUN, analysisSubmission(), oldSearch, "2026-09-02T17:10:00.000Z"), {
    ok: false, code: "evidence_stale",
  });
});

Deno.test("stable facts require a current-run revalidation event within their category TTL", () => {
  const submission = analysisSubmission();
  submission.evidence_refs.push({
    evidence_id: "filing-old", run_id: RUN.runId, content_hash: "c".repeat(64),
  });
  submission.dimensions.fundamentals.evidence_ids.push("filing-old");
  const oldFiling: ServerEvidenceFact = {
    evidenceId: "filing-old", runId: RUN.runId, sourceRunId: "22222222-2222-4222-8222-222222222222",
    category: "filing", source: "sec", reference: "accession", observedAt: "2026-01-01T12:00:00.000Z",
    retrievedAt: "2026-01-01T12:01:00.000Z", revalidatedAt: null,
    contentHash: "c".repeat(64), claims: ["Filed fact"], status: "fresh",
  };
  assertEquals(validateEvidence(RUN, submission, [...facts(), oldFiling], "2026-09-02T17:10:00.000Z"), {
    ok: false, code: "evidence_stale",
  });
  oldFiling.revalidatedAt = "2026-09-02T17:07:00.000Z";
  assertEquals(validateEvidence(RUN, submission, [...facts(), oldFiling], "2026-09-02T17:10:00.000Z").ok, true);
});

Deno.test("missing reviews or analytical dimensions fail closed", () => {
  const missingReview = analysisSubmission();
  missingReview.checker.completed = false;
  assertEquals(validateEvidence(RUN, missingReview, facts(), "2026-09-02T17:10:00.000Z"), {
    ok: false, code: "evidence_missing",
  });
  const missingDimension = analysisSubmission();
  missingDimension.dimensions.downside.status = "missing";
  assertEquals(validateEvidence(RUN, missingDimension, facts(), "2026-09-02T17:10:00.000Z"), {
    ok: false, code: "evidence_missing",
  });
});

Deno.test("cross-run references and reused evidence IDs with changed content conflict", () => {
  const crossRun = analysisSubmission();
  crossRun.evidence_refs[0].run_id = "22222222-2222-4222-8222-222222222222";
  assertEquals(validateEvidence(RUN, crossRun, facts(), "2026-09-02T17:10:00.000Z"), {
    ok: false, code: "evidence_conflicting",
  });
  const duplicate = [...facts(), { ...facts()[0], contentHash: "f".repeat(64) }];
  assertEquals(validateEvidence(RUN, analysisSubmission(), duplicate, "2026-09-02T17:10:00.000Z"), {
    ok: false, code: "evidence_conflicting",
  });
});

Deno.test("uncertain corporate action has its own fail-closed code", () => {
  const corporate = facts();
  corporate.push({
    ...corporate[0], evidenceId: "corp", category: "corporate_action",
    contentHash: "d".repeat(64), status: "needs_review",
  });
  assertEquals(validateEvidence(RUN, analysisSubmission(), corporate, "2026-09-02T17:10:00.000Z"), {
    ok: false, code: "corporate_action_pending",
  });
});
