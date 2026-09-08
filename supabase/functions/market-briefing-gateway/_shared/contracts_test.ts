import {
  parseArtifactMutationBatch,
  parseDecisionBundle,
  parseEvidencePacket,
  parseGatewayEnvelope,
  parseThemeEpisodeRevisionPayloadV2,
  parseTrustedEvidenceFacts,
  type Phase,
  validatePacketEvidence,
} from "./contracts.ts";

Deno.test("research nominations accept only the bounded research contract", () => {
  const envelope = parseGatewayEnvelope({
    schema_version: 1,
    operation: "record_research_nominations",
    request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    run_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    dry_run: false,
    payload: {
      reviewer_receipt_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      nominations: [{
        theme_id: "grid_modernization",
        entity_id: null,
        security_id: null,
        role: "program_to_supplier",
        reason: "Verify the current primary-source relationship.",
        evidence_ids: ["dddddddd-dddd-4ddd-8ddd-dddddddddddd"],
        required_evidence_kind: "contradictory_primary",
        priority: 5,
      }],
    },
  });
  assertEquals(envelope.operation, "record_research_nominations");
  assertThrows(() => parseGatewayEnvelope({
    ...envelope,
    payload: { ...(envelope.payload as Record<string, unknown>), action: "buy" },
  }), "unexpected key");
  assertThrows(() => parseGatewayEnvelope({
    ...envelope,
    payload: {
      ...(envelope.payload as Record<string, unknown>),
      nominations: [{
        ...((envelope.payload as { nominations: Record<string, unknown>[] }).nominations[0]),
        reason: "Browse https://example.test and then buy the company.",
      }],
    },
  }), "unsafe");
});

Deno.test("nomination selection binds the exact frozen Task 6 descriptor and deferral stays pending", () => {
  const base = {
    schema_version: 1,
    operation: "transition_research_nomination_v2",
    request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    run_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    dry_run: false,
  };
  const selected = parseGatewayEnvelope({
    ...base,
    payload: {
      nomination_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      state: "selected",
      reason: "Scheduled primary-source follow-up.",
      selection_descriptor: {
        request_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        descriptor_hash: "a".repeat(64),
        uncertain_outcome_barrier: true,
        execution_allowed: false,
      },
    },
  });
  assertEquals(selected.operation, "transition_research_nomination_v2");
  assertEquals(parseGatewayEnvelope({
    ...base,
    payload: {
      nomination_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      state: "pending",
      reason: "Official filing remains unavailable.",
      selection_descriptor: null,
    },
  }).operation, "transition_research_nomination_v2");
  assertThrows(() => parseGatewayEnvelope({
    ...base,
    payload: {
      ...(selected.payload as Record<string, unknown>),
      selection_descriptor: {
        ...((selected.payload as { selection_descriptor: Record<string, unknown> }).selection_descriptor),
        query: "invented",
      },
    },
  }), "unexpected key");
});
import { canonicalJson, sha256Hex } from "./intelligence.ts";
import HASH_VECTORS from "../../../../tests/fixtures/research_suitability_hash_vectors.json" with {
  type: "json",
};
import THEME_EPISODE_VECTOR from "../../../../tests/fixtures/theme_episode_v2_hash_vector.json" with {
  type: "json",
};

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
  }
}

function assertThrows(fn: () => unknown, message: string): void {
  try {
    fn();
  } catch (error) {
    if (!(error instanceof Error) || !error.message.includes(message)) {
      throw new Error(
        `expected error containing ${message}, got ${String(error)}`,
      );
    }
    return;
  }
  throw new Error(`expected error containing ${message}`);
}

function validCandidate(ticker = "CENX", phase: Phase = "on-demand") {
  return {
    candidate_id: "00000000-0000-4000-8000-000000000010",
    ticker,
    phase,
    action: "buy",
    notification_kind: "brief",
    decision_mode: "discretionary",
    bucket: "growth",
    depth: "full",
    confidence: "medium",
    confidence_reason: "Evidence is current but cyclical risk remains.",
    health_score: "72",
    observed_price: "47.02",
    observed_quote_as_of: "2026-09-02T17:00:00.000Z",
    proposed_amount: "2057.04",
    proposed_shares: "43.748192",
    entry_zone_low: "45",
    entry_zone_high: "47.02",
    stop: "42",
    target: "58",
    invalidation_price: "41.5",
    valid_until: "2026-09-09",
    evidence: [{
      id: "quote-1",
      kind: "quote",
      source: "yahoo",
      status: "fresh",
      observed_at: "2026-09-02T17:00:00.000Z",
      retrieved_at: "2026-09-02T17:00:01.000Z",
      reference: "https://example.invalid/untrusted",
      claims: ["Price observed at 47.02."],
      exposure_kind: "filing",
    }],
    factors: [{
      kind: "risk",
      stance: "bear",
      text: "Cyclical aluminum pricing remains the main risk.",
      evidence_ids: ["quote-1"],
    }],
    analyst: {
      id: "00000000-0000-4000-8000-000000000020",
      packet_id: "00000000-0000-4000-8000-000000000030",
      completed: true,
      action: "buy",
      confidence: "medium",
      reason: "Valuation and demand support the thesis.",
    },
    checker: {
      id: "00000000-0000-4000-8000-000000000021",
      analyst_id: "00000000-0000-4000-8000-000000000020",
      completed: true,
      verdict: "approve",
      reason_codes: [],
      reason: "Required evidence is present.",
    },
    relationship_type: "direct",
    decisive_factor: "Demand durability.",
    invalidation: "Demand or margins weaken materially.",
    prior_suggestion_ids: [],
  };
}

function fixturePacket() {
  return {
    candidates: [{ candidate_key: "CENX", evidence_ids: ["quote-1"] }],
    evidence: [{
      item_id: "quote-1",
      normalized_text: "Price observed at 47.02.",
    }],
    coverage: { mode: "fixture_dry_run", complete_market_coverage: false },
    limitations: ["fixture_only_no_external_coverage"],
    policy_version: 1,
  };
}

function fixturePacketV2(actionEligible = false) {
  const itemId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const opposingId = "abababab-abab-4bab-8bab-abababababab";
  const suitabilityBody = {
    component_scores: {
      concentration_penalty: "0.000000",
      duplication_penalty: "0.000000",
      liquidity: "0.500000",
      portfolio_relevance: "0.000000",
    },
    lineage: {
      cash_revision: "cash:9",
      evidence_receipt_ids: {
        [itemId]: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        [opposingId]: "bcbcbcbc-bcbc-4bcb-8bcb-bcbcbcbcbcbc",
      },
      observed_at: "2026-09-04T12:00:00.000Z",
      policy_version: 1,
      portfolio_revision: "portfolio:7",
      quote_as_of: "2026-09-04T11:59:00.000Z",
      quote_expires_at: "2026-09-04T12:05:00.000Z",
      quote_receipt_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      reference_expires_at: "2026-09-05T12:00:00.000Z",
      reference_manifest_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      reference_revision: 2,
      run_id: "11111111-1111-4111-8111-111111111111",
      security_revision_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    },
    missing_reasons: actionEligible ? [] : ["valuation_missing"],
    state: actionEligible ? "eligible" : "unknown",
    veto_reasons: [],
  };
  const suitability = {
    ...suitabilityBody,
    evaluation_hash: sha256Hex(canonicalJson(suitabilityBody)),
  };
  const candidateBody = {
    adverse_paths: ["demand-downside"],
    candidate_key: "sec-cik:0000000001:listing-origin:CENX",
    entity_id: "sec-cik:0000000001",
    event_ids: ["12121212-1212-4121-8121-121212121212"],
    evidence: [
      {
        claim_type: "issuer_exposure",
        item_id: itemId,
        relationship_eligible: true,
        role: "supporting",
      },
      {
        claim_type: "event",
        item_id: opposingId,
        relationship_eligible: false,
        role: "opposing",
      },
    ],
    exposure_fact_ids: ["13131313-1313-4131-8131-131313131313"],
    limitations: [],
    priority_components: {
      authority_corroboration: "0.500000",
      exposure: "1.000000",
      materiality: "0.500000",
      recency: "0.900000",
    },
    priority_score: "2.900000",
    research_state: "analysis_ready",
    roles: ["supplier"],
    security_id: "sec-cik:0000000001:listing-origin:CENX",
    suitability,
    theme_ids: ["magnets"],
    ticker: "CENX",
  };
  const candidate = {
    ...candidateBody,
    candidate_hash: sha256Hex(canonicalJson(candidateBody)),
  };
  return {
    action_candidates: actionEligible
      ? [{
        candidate_hash: candidate.candidate_hash,
        candidate_key: candidate.candidate_key,
        suitability_hash: suitability.evaluation_hash,
      }]
      : [],
    contract_version: 2,
    coverage: { complete_market_coverage: false, mode: "bounded" },
    evidence: [
      {
        authority: "official",
        canonical_url: "https://www.sec.gov/source",
        claim_type: "issuer_exposure",
        content_hash: "f".repeat(64),
        effective_at: null,
        item_id: itemId,
        normalized_text: "Primary exposure statement.",
        published_at: "2026-09-04T10:00:00.000Z",
        reporting_at: null,
        retrieved_at: "2026-09-04T10:01:00.000Z",
        source_identity: {
          provider: "sec_edgar",
          receipt_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          upstream_item_id: "filing-1",
        },
      },
      {
        authority: "corroborating",
        canonical_url: "https://example.com/adverse",
        claim_type: "event",
        content_hash: "e".repeat(64),
        effective_at: null,
        item_id: opposingId,
        normalized_text: "Opposing demand evidence.",
        published_at: "2026-09-04T09:00:00.000Z",
        reporting_at: null,
        retrieved_at: "2026-09-04T09:01:00.000Z",
        source_identity: {
          provider: "gdelt",
          receipt_id: "bcbcbcbc-bcbc-4bcb-8bcb-bcbcbcbcbcbc",
          upstream_item_id: "story-2",
        },
      },
    ],
    execution_allowed: false,
    limitations: [],
    observed_at: "2026-09-04T12:00:00.000Z",
    omissions: [],
    policy_version: 1,
    research_candidates: [candidate],
    run_id: "11111111-1111-4111-8111-111111111111",
  };
}

Deno.test("v2 packet keeps research but rejects caller-rehashed action promotion", () => {
  const parsed = parseEvidencePacket(fixturePacketV2());
  if (!("contract_version" in parsed)) throw new Error("v2 packet was not selected");
  assertEquals(parsed.research_candidates.length, 1);
  assertEquals(parsed.action_candidates.length, 0);

  assertThrows(
    () => parseEvidencePacket(fixturePacketV2(true)),
    "protected issuer valuation",
  );

  const forged = structuredClone(fixturePacketV2());
  forged.research_candidates[0].ticker = "SWAP";
  assertThrows(() => parseEvidencePacket(forged), "candidate_hash");

  const identitySwap = structuredClone(fixturePacketV2(false));
  identitySwap.research_candidates[0].candidate_key = "sec:substituted";
  const identityBody = { ...identitySwap.research_candidates[0] } as Record<string, unknown>;
  delete identityBody.candidate_hash;
  identitySwap.research_candidates[0].candidate_hash = sha256Hex(canonicalJson(identityBody));
  assertThrows(() => parseEvidencePacket(identitySwap), "resolved security identity");

  const insecureUrl = structuredClone(fixturePacketV2());
  insecureUrl.evidence[0].canonical_url = "http://www.sec.gov/source";
  assertThrows(() => parseEvidencePacket(insecureUrl), "canonical HTTPS");

  const missingReceipt = structuredClone(fixturePacketV2());
  missingReceipt.evidence[0].source_identity.receipt_id = null as never;
  assertThrows(() => parseEvidencePacket(missingReceipt), "receipt_id");
});

Deno.test("v2 packet cannot bind one ticker to two security identities", () => {
  const duplicate = structuredClone(fixturePacketV2(false));
  const second = structuredClone(duplicate.research_candidates[0]);
  second.candidate_key = "sec-cik:0000000002:listing-origin:CENX";
  second.security_id = second.candidate_key;
  const body = { ...second } as Record<string, unknown>;
  delete body.candidate_hash;
  second.candidate_hash = sha256Hex(canonicalJson(body));
  duplicate.research_candidates.push(second);
  assertThrows(() => parseEvidencePacket(duplicate), "duplicate ticker identities");
});

Deno.test("v2 research-only candidate cannot be promoted by analyst or checker", () => {
  const packet = parseEvidencePacket(fixturePacketV2(false));
  const candidate = validCandidate();
  candidate.evidence[0].id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  candidate.analyst.action = "buy";
  candidate.checker.verdict = "approve";
  assertEquals(validatePacketEvidence(candidate as never, packet), [
    "RESEARCH_ONLY_CANDIDATE",
  ]);
});

Deno.test("research suitability canonical hashes match shared golden vectors", () => {
  for (const vector of HASH_VECTORS.vectors) {
    assertEquals(canonicalJson(vector.value), vector.canonical_json);
    assertEquals(sha256Hex(vector.canonical_json), vector.sha256);
  }
  if (HASH_VECTORS.vectors[1].sha256 === HASH_VECTORS.vectors[2].sha256) {
    throw new Error("absent and explicit null keys must remain distinct");
  }
});

Deno.test("theme episode v2 parser verifies the shared canonical persistence vector", () => {
  assertEquals(
    canonicalJson(parseThemeEpisodeRevisionPayloadV2(THEME_EPISODE_VECTOR.persistence_row)),
    canonicalJson(THEME_EPISODE_VECTOR.persistence_row),
  );
  const changed = structuredClone(THEME_EPISODE_VECTOR.persistence_row);
  changed.subject_identity = "entity:substituted";
  assertThrows(
    () => parseThemeEpisodeRevisionPayloadV2(changed),
    "hash mismatch",
  );
  const noncanonicalStory = structuredClone(THEME_EPISODE_VECTOR.persistence_row);
  noncanonicalStory.source_membership[0].story_identity = "  substituted\tstory  ";
  assertThrows(
    () => parseThemeEpisodeRevisionPayloadV2(noncanonicalStory),
    "not canonical",
  );
});

Deno.test("persisted facts reject duplicate IDs and missing authority fields", () => {
  const fact = {
    candidate_key: "CENX",
    evidence_id: "quote-1",
    category: "quote",
    source: "yahoo",
    source_status: "succeeded",
    authority: "market_data",
    published_at: "2026-09-02T16:55:00Z",
    retrieved_at: "2026-09-02T16:56:00Z",
    expires_at: "2026-09-03T16:55:00Z",
    reference: null,
    normalized_text: "Stored source",
    exposure_kind: null,
    relationship_eligible: false,
    claim_key: null,
    claim_polarity: null,
  };
  assertEquals(parseTrustedEvidenceFacts([fact])[0].source, "yahoo");
  assertThrows(() => parseTrustedEvidenceFacts([fact, fact]), "duplicate");
  const missing = { ...fact } as Record<string, unknown>;
  delete missing.authority;
  assertThrows(() => parseTrustedEvidenceFacts([missing]), "authority");
});

function validBundle(phase: Phase = "on-demand") {
  return {
    phase,
    market_date: "2026-09-02",
    title: "Owner research request",
    candidates: [validCandidate("CENX", phase)],
  };
}

function validEnvelope() {
  return {
    schema_version: 1,
    operation: "evaluate_and_publish",
    request_id: "00000000-0000-4000-8000-000000000001",
    run_id: "00000000-0000-4000-8000-000000000002",
    dry_run: false,
    payload: validBundle(),
  };
}

Deno.test("gateway envelope accepts one complete decision bundle", () => {
  const parsed = parseGatewayEnvelope(validEnvelope());
  assertEquals(parsed.operation, "evaluate_and_publish");
  assertEquals(
    parseDecisionBundle(parsed.payload, "on-demand").candidates[0].ticker,
    "CENX",
  );
});

Deno.test("decision bundle parses an exact intelligence packet reference and bounded fixture", () => {
  const live = Object.assign(validBundle("intraday"), {
    intelligence_packet: {
      id: "00000000-0000-4000-8000-000000000030",
      content_hash: "a".repeat(64),
      coverage: "complete_for_plan",
    },
  });
  assertEquals(
    parseDecisionBundle(live, "intraday").intelligence_packet!.coverage,
    "complete_for_plan",
  );

  const dry = Object.assign(validBundle("intraday"), {
    intelligence_packet: {
      id: "00000000-0000-4000-8000-000000000030",
      content_hash: "a".repeat(64),
      coverage: "fixture_dry_run",
      packet: fixturePacket(),
    },
  });
  const parsedPacket = parseDecisionBundle(dry, "intraday")
    .intelligence_packet!.packet!;
  if ("contract_version" in parsedPacket) throw new Error("expected v1 fixture");
  assertEquals(parsedPacket.candidates.length, 1);
});

Deno.test("inline intelligence packet enforces candidate, evidence, and byte bounds", () => {
  const bundle = Object.assign(validBundle("intraday"), {
    intelligence_packet: {
      id: "00000000-0000-4000-8000-000000000030",
      content_hash: "a".repeat(64),
      coverage: "fixture_dry_run",
      packet: fixturePacket(),
    },
  });
  bundle.intelligence_packet.packet.candidates[0].evidence_ids = Array.from({
    length: 9,
  }, (_, index) => `e-${index}`);
  assertThrows(() => parseDecisionBundle(bundle, "intraday"), "at most 8");

  const oversized = structuredClone(bundle);
  oversized.intelligence_packet.packet.candidates[0].evidence_ids = ["quote-1"];
  (oversized.intelligence_packet.packet as {
    coverage: Record<string, unknown>;
  }).coverage = { padding: "x".repeat(98_304) };
  assertThrows(() => parseDecisionBundle(oversized, "intraday"), "96 KiB");
});

Deno.test("candidate cannot omit contradictory packet evidence", () => {
  const candidate = validCandidate();
  const packet = fixturePacket();
  packet.evidence.push({
    item_id: "conflict-1",
    normalized_text: "Contradicts the thesis.",
  });
  packet.candidates[0].evidence_ids.push("conflict-1");
  assertEquals(validatePacketEvidence(candidate as never, packet), [
    "EVIDENCE_NOT_IN_PACKET",
  ]);
});

Deno.test("gateway envelope accepts the bounded standalone alert evaluation operation", () => {
  const envelope = {
    ...validEnvelope(),
    operation: "evaluate_alert_rules",
    run_id: null,
    payload: {},
  };
  assertEquals(
    parseGatewayEnvelope(envelope).operation,
    "evaluate_alert_rules",
  );
});

Deno.test("gateway envelope accepts scoped intelligence controller operations", () => {
  const start = {
    ...validEnvelope(),
    operation: "start_intelligence_run",
    run_id: null,
    payload: {
      phase: "on-demand",
      market_date: "2026-09-02",
      policy_version: 1,
      reservation_plan: { reservations: [] },
      request_window: {
        start: "2026-09-04T11:00:00.000Z",
        end: "2026-09-04T12:00:00.000Z",
        timezone: "America/Chicago",
        market_date: "2026-09-04",
        phase: "pre-market",
      },
    },
  };
  assertEquals(parseGatewayEnvelope(start).operation, "start_intelligence_run");
  const checkpoint = {
    ...validEnvelope(),
    operation: "checkpoint_intelligence_collection",
    payload: { cache_key: "a".repeat(64), receipt: {}, items: [] },
  };
  assertEquals(
    parseGatewayEnvelope(checkpoint).operation,
    "checkpoint_intelligence_collection",
  );
});

Deno.test("gateway envelope rejects unknown and extra authority fields", () => {
  const unknownOperation = validEnvelope();
  unknownOperation.operation = "send_telegram";
  assertThrows(() => parseGatewayEnvelope(unknownOperation), "operation");

  const withTable = { ...validEnvelope(), table: "holdings" };
  assertThrows(() => parseGatewayEnvelope(withTable), "unexpected key");
});

Deno.test("gateway envelope accepts only a bounded review-only learning record", () => {
  const observation = {
    status: "owner_review",
    evidence_ids: ["00000000-0000-4000-8000-000000000010"],
    limitations: ["Historical outcomes do not prove future performance."],
    metrics: { false_positive_rate: "0.1667" },
    proposed_change: {
      area: "candidate_ranking_review",
      recommendation: "review_false_positive_rate",
      false_positive_rate: "0.1667",
    },
  };
  const envelope = {
    ...validEnvelope(),
    operation: "record_learning",
    payload: {
      id: "00000000-0000-4000-8000-000000000040",
      policy_version: 1,
      observation_type: "outcome",
      horizon_days: 21,
      sample_size: 6,
      benchmark: "VOO",
      observation,
      content_hash: "a".repeat(64),
    },
  };

  assertEquals(parseGatewayEnvelope(envelope).operation, "record_learning");

  const executable = structuredClone(envelope);
  (executable.payload.observation as Record<string, unknown>).apply = true;
  assertThrows(() => parseGatewayEnvelope(executable), "unexpected key");

  const mutationTarget = structuredClone(envelope);
  (mutationTarget.payload.observation.proposed_change as Record<
    string,
    unknown
  >)
    .operation = "update_policy";
  assertThrows(() => parseGatewayEnvelope(mutationTarget), "unexpected key");
});

Deno.test("learning record rejects unsupported horizons, kinds, and unbounded evidence", () => {
  const base = {
    ...validEnvelope(),
    operation: "record_learning",
    payload: {
      id: "00000000-0000-4000-8000-000000000040",
      policy_version: 1,
      observation_type: "noise",
      horizon_days: 5,
      sample_size: 6,
      benchmark: "VOO",
      observation: {
        status: "observation",
        evidence_ids: [],
        limitations: [],
        metrics: { false_positive_rate: "0.1667" },
        proposed_change: null,
      },
      content_hash: "a".repeat(64),
    },
  };
  const horizon = structuredClone(base);
  horizon.payload.horizon_days = 10;
  assertThrows(() => parseGatewayEnvelope(horizon), "horizon");

  const kind = structuredClone(base);
  kind.payload.observation_type = "policy-update";
  assertThrows(() => parseGatewayEnvelope(kind), "observation_type");

  const evidence = structuredClone(base);
  (evidence.payload.observation.evidence_ids as string[]) = Array.from(
    { length: 97 },
    () => "00000000-0000-4000-8000-000000000010",
  );
  assertThrows(() => parseGatewayEnvelope(evidence), "at most 96");
});

Deno.test("gateway envelope rejects invalid identifiers and decimal JSON numbers", () => {
  const invalidId = validEnvelope();
  invalidId.request_id = "not-a-uuid";
  assertThrows(() => parseGatewayEnvelope(invalidId), "request_id");

  const numericPrice = validEnvelope();
  (numericPrice.payload.candidates[0] as Record<string, unknown>)
    .entry_zone_high = 47.02;
  const parsed = parseGatewayEnvelope(numericPrice);
  assertThrows(
    () => parseDecisionBundle(parsed.payload, "on-demand"),
    "decimal string",
  );
});

Deno.test("decision bundle rejects lowercase tickers and duplicate evidence", () => {
  const lowercase = validBundle();
  lowercase.candidates[0].ticker = "cenx";
  assertThrows(() => parseDecisionBundle(lowercase, "on-demand"), "ticker");

  const duplicateEvidence = validBundle();
  duplicateEvidence.candidates[0].evidence.push({
    ...duplicateEvidence.candidates[0].evidence[0],
  });
  assertThrows(
    () => parseDecisionBundle(duplicateEvidence, "on-demand"),
    "duplicate evidence id",
  );
});

Deno.test("decision bundle enforces phase and per-phase candidate limits", () => {
  assertThrows(
    () => parseDecisionBundle(validBundle("pre-market"), "on-demand"),
    "phase",
  );

  const oversized = validBundle();
  oversized.candidates = Array.from({ length: 11 }, (_, index) => ({
    ...validCandidate(`T${index}`, "on-demand"),
    candidate_id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
  }));
  assertThrows(
    () => parseDecisionBundle(oversized, "on-demand"),
    "candidate limit",
  );
});

Deno.test("decision bundle permits only one candidate per ticker", () => {
  const duplicateTicker = validBundle();
  duplicateTicker.candidates.push({
    ...validCandidate(),
    candidate_id: "00000000-0000-4000-8000-000000000011",
  });
  assertThrows(
    () => parseDecisionBundle(duplicateTicker, "on-demand"),
    "duplicate ticker",
  );
});

Deno.test("decision bundle accepts an explicit bounded reservation group", () => {
  const bundle = validBundle();
  Object.assign(bundle.candidates[0], {
    reservation_group: "aluminum-growth-idea",
  });
  assertEquals(
    parseDecisionBundle(bundle, "on-demand").candidates[0].reservation_group,
    "aluminum-growth-idea",
  );
});

Deno.test("decision bundle accepts bounded evidence-linked portfolio comparisons", () => {
  const bundle = validBundle("on-demand");
  bundle.candidates.push({
    ...validCandidate("ITOT", "on-demand"),
    candidate_id: "00000000-0000-4000-8000-000000000011",
    evidence: [{
      ...validCandidate().evidence[0],
      id: "itot-profile",
      status: "fresh",
    }],
    factors: [{
      kind: "fundamentals",
      stance: "neutral",
      text: "The fund covers the broad U.S. equity market.",
      evidence_ids: ["itot-profile"],
    }],
  });
  Object.assign(bundle, {
    comparisons: [{
      baseline_ticker: "CENX",
      alternative_ticker: "ITOT",
      relationship: "tilt",
      prospective_view: "stronger",
      reason:
        "The broader fund would reduce single-company concentration risk.",
      evidence_ids: ["itot-profile"],
    }],
  });
  const parsed = parseDecisionBundle(bundle, "on-demand");
  assertEquals(parsed.comparisons?.[0].alternative_ticker, "ITOT");
  assertEquals(parsed.comparisons?.[0].relationship, "tilt");
});

Deno.test("portfolio comparisons reject unknown tickers, evidence, duplicates, and scheduled noise", () => {
  const base = validBundle("on-demand") as ReturnType<typeof validBundle> & {
    comparisons?: unknown[];
  };
  base.comparisons = [{
    baseline_ticker: "CENX",
    alternative_ticker: "ITOT",
    relationship: "peer",
    prospective_view: "similar",
    reason: "Evidence-linked comparison.",
    evidence_ids: ["missing"],
  }];
  assertThrows(
    () => parseDecisionBundle(base, "on-demand"),
    "comparison ticker",
  );

  const noisy = validBundle("intraday") as ReturnType<typeof validBundle> & {
    comparisons?: unknown[];
  };
  noisy.comparisons = [];
  assertThrows(
    () => parseDecisionBundle(noisy, "intraday"),
    "comparisons are limited",
  );
});

function companionBundle(phase: Phase = "on-demand") {
  const bundle = validBundle(phase);
  bundle.candidates[0].ticker = "VTI";
  bundle.candidates.push({
    ...validCandidate("VXUS", phase),
    candidate_id: "00000000-0000-4000-8000-000000000012",
    evidence: [{
      ...validCandidate().evidence[0],
      id: "vxus-profile",
      kind: "fundamentals",
      status: "fresh",
    }],
    factors: [{
      kind: "fundamentals",
      stance: "neutral",
      text: "The fund covers developed and emerging non-U.S. equity markets.",
      evidence_ids: ["vxus-profile"],
    }],
  });
  return Object.assign(bundle, {
    comparisons: [{
      baseline_ticker: "VTI",
      alternative_ticker: "VXUS",
      relationship: "diversifier",
      prospective_view: "similar",
      reason: "The candidate adds a distinct geographic role.",
      evidence_ids: ["vxus-profile"],
    }],
    companion_proposal: {
      baseline_ticker: "VTI",
      companion_ticker: "VXUS",
      role: "diversifier",
      thesis: "Non-U.S. exposure adds a distinct geographic role.",
      risk_note:
        "Currency and foreign-market risks can cause long periods of lagging U.S. stocks.",
      evidence_ids: ["vxus-profile"],
    },
  });
}

Deno.test("decision bundle accepts one evidence-linked long-term companion nomination", () => {
  const parsed = parseDecisionBundle(companionBundle(), "on-demand");
  assertEquals(parsed.companion_proposal, {
    baseline_ticker: "VTI",
    companion_ticker: "VXUS",
    role: "diversifier",
    thesis: "Non-U.S. exposure adds a distinct geographic role.",
    risk_note:
      "Currency and foreign-market risks can cause long periods of lagging U.S. stocks.",
    evidence_ids: ["vxus-profile"],
  });
});

Deno.test("long-term companion requires a valid pair, evidence, phase, and additive role", () => {
  const unknownTicker = companionBundle();
  unknownTicker.companion_proposal.companion_ticker = "UNKNOWN";
  assertThrows(
    () => parseDecisionBundle(unknownTicker, "on-demand"),
    "companion ticker",
  );

  const absentPair = companionBundle();
  absentPair.comparisons = [];
  assertThrows(
    () => parseDecisionBundle(absentPair, "on-demand"),
    "matching portfolio comparison",
  );

  const unknownEvidence = companionBundle();
  unknownEvidence.companion_proposal.evidence_ids = ["missing"];
  assertThrows(
    () => parseDecisionBundle(unknownEvidence, "on-demand"),
    "companion evidence",
  );

  const intraday = companionBundle("intraday");
  assertThrows(
    () => parseDecisionBundle(intraday, "intraday"),
    "comparisons are limited",
  );

  const substitute = companionBundle();
  substitute.companion_proposal.role = "like_for_like";
  assertThrows(
    () => parseDecisionBundle(substitute, "on-demand"),
    "companion role",
  );
});

Deno.test("artifact parser accepts bounded paper-watch input", () => {
  assertEquals(
    parseArtifactMutationBatch({
      mutations: [{
        kind: "paper_watch_create",
        ticker: "MP",
        entry_ref_price: "74.25",
        target_price: null,
        hypothetical_amount: "500",
        thesis: "Domestic magnet capacity may expand.",
        horizon: "three months",
      }],
    }),
    {
      mutations: [{
        kind: "paper_watch_create",
        ticker: "MP",
        entry_ref_price: "74.25",
        target_price: null,
        hypothetical_amount: "500",
        thesis: "Domestic magnet capacity may expand.",
        horizon: "three months",
      }],
    },
  );
});

Deno.test("artifact parser rejects dynamic tables and caller-owned close fields", () => {
  assertThrows(
    () =>
      parseArtifactMutationBatch({
        mutations: [{ kind: "radar_delete", ticker: "MP", table: "holdings" }],
      }),
    "unexpected key",
  );
  assertThrows(
    () =>
      parseArtifactMutationBatch({
        mutations: [{
          kind: "paper_watch_close",
          watch_id: 1,
          ticker: "MP",
          close_price: "99",
        }],
      }),
    "unexpected key",
  );
});

Deno.test("reference snapshot transfer operations require a run and parse service payloads", () => {
  const base = {
    schema_version: 1 as const,
    request_id: "00000000-0000-4000-8000-000000000091",
    run_id: "00000000-0000-4000-8000-000000000092",
    dry_run: false,
  };
  const read = parseGatewayEnvelope({
    ...base,
    operation: "read_discovery_reference",
    payload: {
      capability_id: "sec_company_tickers_universe",
      binding_role: "current",
      after_security_id: null,
      limit: 500,
    },
  });
  assertEquals(read.operation, "read_discovery_reference");
  assertEquals((read.payload as { limit: number }).limit, 500);
  assertThrows(() =>
    parseGatewayEnvelope({
      ...base,
      run_id: null,
      operation: "pin_discovery_reference",
      payload: {
        capability_id: "sec_company_tickers_universe",
        binding_role: "current",
        manifest_id: null,
        reference_status: "reference_unavailable",
        reference_as_of: "2026-09-07T12:00:00.000Z",
      },
    }), "run_id is required");
});
