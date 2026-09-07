import { parseGatewayEnvelope } from "./contracts.ts";
import {
  canonicalJson,
  parseDiscoveryContext,
  parseDiscoveryContextRequest,
  parseDiscoveryReferencePayload,
  parseDiscoveryStageCheckpointPayload,
  parseReferenceBeginPayload,
  parseReferenceChunkPayload,
  parseReferenceFinalizePayload,
  parseReferencePage,
  parseReferencePinPayload,
  parseReferenceReadPayload,
  sha256Hex,
} from "./intelligence.ts";

function assertEquals(actual: unknown, expected: unknown): void {
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
    if (error instanceof Error && error.message.includes(message)) return;
    throw error;
  }
  throw new Error(`expected error containing ${message}`);
}

function referenceManifest() {
  return {
    id: "00000000-0000-4000-8000-000000000101",
    reference_version: "sec:2026-09-06:fixture",
    revision: 1,
    capability_version: 1,
    taxonomy_version: 1,
    source_hash: "a".repeat(64),
    valid_from: "2026-09-06T12:00:00.000Z",
    valid_to: null,
    manifest: {
      coverage_status: "scope_not_guaranteed",
      reference_status: "healthy",
      source_url: "https://www.sec.gov/files/company_tickers.json",
      source_retrieved_at: "2026-09-06T12:00:00.000Z",
      source_timestamp: null,
      parser_version: 1,
      security_count: 1,
      conflict_count: 0,
      symbol_directory_status: "disabled_pending_https_and_terms_review",
    },
    content_hash: "b".repeat(64),
  };
}

function referenceEntry() {
  return {
    id: "00000000-0000-4000-8000-000000000102",
    manifest_id: "00000000-0000-4000-8000-000000000101",
    revision: 1,
    security_id: "sec-cik:0000000001:listing-origin:TEST",
    entity_id: "sec-cik:0000000001",
    ticker: "TEST",
    exchange: null,
    instrument_type: "COMMON_STOCK",
    eligible: true,
    exclusion_reasons: [],
    aliases: ["TEST"],
    source_ids: ["sec-company-tickers:0000000001"],
    valid_from: "2026-09-06T00:00:00.000Z",
    valid_to: null,
    content_hash: "c".repeat(64),
  };
}

Deno.test("reference transfer parsers keep every call bounded and exact", () => {
  const manifest = referenceManifest();
  const begin = {
    manifest,
    capability_id: "sec_company_tickers_universe",
    chunk_count: 1,
    security_count: 1,
    root_hash: "d".repeat(64),
    predecessor_manifest_id: null,
  };
  const chunk = {
    manifest_id: manifest.id,
    chunk_index: 0,
    chunk_count: 1,
    entries: [referenceEntry()],
    chunk_hash: "e".repeat(64),
  };
  assertEquals(parseReferenceBeginPayload(begin), begin);
  assertEquals(parseReferenceChunkPayload(chunk), chunk);
  assertEquals(
    parseReferenceFinalizePayload({
      manifest_id: manifest.id,
      root_hash: "d".repeat(64),
    }),
    { manifest_id: manifest.id, root_hash: "d".repeat(64) },
  );
  assertEquals(
    parseReferencePinPayload({
      capability_id: "sec_company_tickers_universe",
      manifest_id: null,
      reference_status: "reference_stale",
      reference_as_of: "2026-09-07T12:00:00.000Z",
    }).reference_status,
    "reference_stale",
  );
  assertEquals(
    parseReferenceReadPayload({
      capability_id: "sec_company_tickers_universe",
      after_security_id: null,
      limit: 500,
    }).limit,
    500,
  );

  assertThrows(
    () =>
      parseReferenceChunkPayload({
        ...chunk,
        entries: Array(201).fill(referenceEntry()),
      }),
    "at most 200 items",
  );
  assertThrows(
    () =>
      parseReferenceReadPayload({
        capability_id: "sec_company_tickers_universe",
        after_security_id: null,
        limit: 501,
      }),
    "must be a bounded integer",
  );
  assertThrows(
    () => parseReferenceBeginPayload({ ...begin, execution_allowed: false }),
    "unexpected key",
  );
});

Deno.test("reference pages reject inconsistent bindings, pagination, and identities", () => {
  const page = {
    binding: {
      manifest_id: referenceManifest().id,
      reference_status: "healthy",
      source_retrieved_at: "2026-09-06T12:00:00.000Z",
      reference_age_seconds: 0,
    },
    manifest: referenceManifest(),
    securities: [referenceEntry()],
    next_after_security_id: null,
    complete: true,
  };
  assertEquals(parseReferencePage(page).complete, true);
  assertThrows(
    () =>
      parseReferencePage({
        ...page,
        binding: {
          manifest_id: null,
          reference_status: "reference_unavailable",
          source_retrieved_at: "2026-09-06T12:00:00.000Z",
          reference_age_seconds: 0,
        },
        manifest: null,
        securities: [],
      }),
    "binding is inconsistent",
  );
  assertThrows(
    () => parseReferencePage({ ...page, complete: false }),
    "pagination is inconsistent",
  );
  assertThrows(
    () =>
      parseReferencePage({
        ...page,
        securities: [referenceEntry(), referenceEntry()],
      }),
    "duplicate",
  );
});

function validRecordIntelligenceEnvelope() {
  const canonicalContent = canonicalJson({
    title: "Bounded item",
    summary: "Evidence.",
  });
  const packet = {
    candidates: [],
    evidence: [],
    coverage: {},
    limitations: [],
    policy_version: 1,
  };
  return {
    schema_version: 1,
    operation: "record_intelligence",
    request_id: "00000000-0000-4000-8000-000000000003",
    run_id: "00000000-0000-4000-8000-000000000002",
    dry_run: false,
    payload: {
      status: "completed",
      coverage: {},
      receipts: [],
      items: [{
        id: "00000000-0000-4000-8000-000000000010",
        run_item_id: "00000000-0000-4000-8000-000000000011",
        receipt_id: "00000000-0000-4000-8000-000000000012",
        provider: "gdelt",
        upstream_item_id: "story-1",
        canonical_url: "https://api.gdeltproject.org/api/v2/doc/doc",
        request_url: "https://api.gdeltproject.org/api/v2/doc/doc?query=grid",
        published_at: "2026-09-04T12:00:00.000Z",
        retrieved_at: "2026-09-04T12:01:00.000Z",
        effective_at: null,
        reporting_at: null,
        entity_ids: [],
        security_ids: [],
        discovery_status: "no_event",
        title: "Bounded item",
        normalized_text: "Evidence.",
        canonical_content: canonicalContent,
        content_hash: sha256Hex(canonicalContent),
        metadata: {},
        disposition: "accepted",
        drop_reason: null,
      }],
      events: [],
      relationships: [],
      rankings: [],
      packet: {
        id: "00000000-0000-4000-8000-000000000020",
        candidate_count: 0,
        evidence_count: 0,
        packet,
        packet_hash: sha256Hex(canonicalJson(packet)),
      },
      error: null,
    },
  };
}

Deno.test("record_intelligence rejects bad hashes and extra authority fields", () => {
  const badHash = validRecordIntelligenceEnvelope();
  badHash.payload.items[0].content_hash = "0".repeat(64);
  assertThrows(() => parseGatewayEnvelope(badHash), "content_hash");

  const extraAuthority = validRecordIntelligenceEnvelope() as Record<
    string,
    unknown
  >;
  (extraAuthority.payload as Record<string, unknown>).send_telegram = true;
  assertThrows(() => parseGatewayEnvelope(extraAuthority), "unexpected key");
});

Deno.test("record_intelligence accepts persisted provider provenance but rejects a provider-host mismatch", () => {
  const valid = validRecordIntelligenceEnvelope();
  Object.assign(valid.payload.items[0], {
    request_url: "https://api.gdeltproject.org/api/v2/doc/doc?query=grid",
    retrieved_at: "2026-09-04T12:01:00.000Z",
    reporting_at: "2025-12-31T00:00:00.000Z",
    entity_ids: ["cik:0000000001"],
    security_ids: ["TEST"],
    discovery_status: "qualified",
  });
  const parsed = parseGatewayEnvelope(valid);
  const item =
    (parsed.payload as { items: Array<Record<string, unknown>> }).items[0];
  assertEquals(
    item.request_url,
    "https://api.gdeltproject.org/api/v2/doc/doc?query=grid",
  );

  const invalid = validRecordIntelligenceEnvelope();
  Object.assign(invalid.payload.items[0], {
    request_url: "https://www.sec.gov/submissions/CIK0000000001.json",
    retrieved_at: "2026-09-04T12:01:00.000Z",
    reporting_at: null,
    entity_ids: [],
    security_ids: [],
    discovery_status: "no_event",
  });
  assertThrows(() => parseGatewayEnvelope(invalid), "request_url");

  const secretBearing = validRecordIntelligenceEnvelope();
  secretBearing.payload.items[0].request_url =
    "https://api.gdeltproject.org/api/v2/doc/doc?api_key=not-persistable";
  assertThrows(() => parseGatewayEnvelope(secretBearing), "secret-bearing");

  const corroborating = validRecordIntelligenceEnvelope();
  Object.assign(corroborating.payload.items[0], {
    disposition: "near_duplicate",
    drop_reason: "similar_normalized_content",
  });
  assertEquals(
    (parseGatewayEnvelope(corroborating).payload as {
      items: Array<Record<string, unknown>>;
    }).items[0]
      .disposition,
    "near_duplicate",
  );
});

Deno.test("canonical JSON and hashes match Task 2 semantic ordering", () => {
  assertEquals(
    canonicalJson({ z: 1, a: { y: true, b: null } }),
    '{"a":{"b":null,"y":true},"z":1}',
  );
  assertEquals(
    sha256Hex("abc"),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});

const DISCOVERY_TASK_ID = "00000000-0000-4000-8000-000000000041";
const OTHER_RUN_TASK_ID = "00000000-0000-4000-8000-000000000099";
const DISCOVERY_MANIFEST_ID = "00000000-0000-4000-8000-000000000042";
const DISCOVERY_SECURITY_ID = "00000000-0000-4000-8000-000000000043";
const DISCOVERY_EXPOSURE_ID = "00000000-0000-4000-8000-000000000044";

function discoveryTask() {
  return {
    id: DISCOVERY_TASK_ID,
    stage: "signals",
    provider: "gdelt",
    capability_id: "gdelt_theme_search",
    query_kind: "theme_search",
    query_hash: "a".repeat(64),
    dependency_ids: [],
    requested_window: {
      start: "2026-09-05T00:00:00.000Z",
      end: "2026-09-06T00:00:00.000Z",
    },
    state: "planned",
    attempt_count: 0,
    request_budget: 1,
    result: {},
  };
}

function discoveryManifest() {
  return {
    id: DISCOVERY_MANIFEST_ID,
    reference_version: "sec:2026-09-06",
    revision: 1,
    capability_version: 1,
    taxonomy_version: 1,
    source_hash: "b".repeat(64),
    valid_from: "2026-09-06T00:00:00.000Z",
    valid_to: null,
    manifest: { coverage_status: "scope_not_guaranteed" },
    content_hash: "c".repeat(64),
  };
}

function discoverySecurityRevision() {
  return {
    id: DISCOVERY_SECURITY_ID,
    manifest_id: DISCOVERY_MANIFEST_ID,
    revision: 1,
    security_id: "NASDAQ:TEST",
    entity_id: "CIK:0000000001",
    ticker: "TEST",
    exchange: "NASDAQ",
    instrument_type: "COMMON_STOCK",
    eligible: true,
    exclusion_reasons: [],
    aliases: ["Test Corp"],
    source_ids: ["nasdaq-listed:TEST"],
    valid_from: "2026-09-06T00:00:00.000Z",
    valid_to: null,
    content_hash: "d".repeat(64),
  };
}

function succeededDiscoveryCheckpoint(stage: string): Record<string, unknown> {
  return {
    task: {
      ...discoveryTask(),
      stage,
      state: "succeeded",
      attempt_count: 1,
    },
    exposure_facts: [],
    theme_episode_revisions: [],
    research_nominations: [],
  };
}

function discoveryExposureFact() {
  return {
    id: DISCOVERY_EXPOSURE_ID,
    security_revision_id: DISCOVERY_SECURITY_ID,
    theme_episode_revision_id: null,
    exposure_kind: "filing",
    fact: { basis: "10-K" },
    source_ids: ["sec:fixture"],
    valid_from: "2026-09-06T00:00:00.000Z",
    valid_to: null,
    content_hash: "e".repeat(64),
  };
}

Deno.test("discovery task parser preserves the planner identity and rejects reselection fields", () => {
  const payload = {
    task: discoveryTask(),
    exposure_facts: [],
    theme_episode_revisions: [],
    research_nominations: [],
  };
  assertEquals(
    parseDiscoveryStageCheckpointPayload(payload).task,
    discoveryTask(),
  );

  const changed = structuredClone(payload) as Record<string, unknown>;
  (changed.task as Record<string, unknown>).query = { search: "changed" };
  assertThrows(
    () => parseDiscoveryStageCheckpointPayload(changed),
    "unexpected key",
  );

  const badDependency = structuredClone(payload) as Record<string, unknown>;
  (badDependency.task as Record<string, unknown>).dependency_ids = [
    OTHER_RUN_TASK_ID,
  ];
  assertEquals(
    parseDiscoveryStageCheckpointPayload(badDependency).task.dependency_ids,
    [OTHER_RUN_TASK_ID],
  );

  const duplicateDependency = structuredClone(payload) as Record<
    string,
    unknown
  >;
  (duplicateDependency.task as Record<string, unknown>).dependency_ids = [
    DISCOVERY_TASK_ID,
    DISCOVERY_TASK_ID,
  ];
  assertThrows(
    () => parseDiscoveryStageCheckpointPayload(duplicateDependency),
    "duplicated",
  );
});

Deno.test("discovery reference parser rejects duplicate security revision identifiers", () => {
  const security = discoverySecurityRevision();
  assertThrows(
    () =>
      parseDiscoveryReferencePayload({
        manifest: discoveryManifest(),
        security_revisions: [security, structuredClone(security)],
      }),
    "security_revisions has duplicate id",
  );
});

Deno.test("discovery checkpoint parser rejects duplicate result child identifiers", () => {
  const cases = [
    {
      stage: "signals",
      collection: "theme_episode_revisions",
      child: {
        id: "00000000-0000-4000-8000-000000000045",
        theme_id: "power_grid",
        revision: 1,
        episode: { summary: "grid investment" },
        source_ids: ["gdelt:fixture"],
        valid_from: "2026-09-06T00:00:00.000Z",
        valid_to: null,
        content_hash: "f".repeat(64),
      },
    },
    {
      stage: "enrich",
      collection: "exposure_facts",
      child: discoveryExposureFact(),
    },
    {
      stage: "screen",
      collection: "research_nominations",
      child: {
        id: "00000000-0000-4000-8000-000000000046",
        security_revision_id: DISCOVERY_SECURITY_ID,
        theme_episode_revision_id: null,
        exposure_fact_ids: [DISCOVERY_EXPOSURE_ID],
        state: "nominated",
        rationale: { basis: "research" },
      },
    },
  ];
  for (const { stage, collection, child } of cases) {
    const payload = succeededDiscoveryCheckpoint(stage);
    payload[collection] = [child, structuredClone(child)];
    assertThrows(
      () => parseDiscoveryStageCheckpointPayload(payload),
      `${collection} has duplicate id`,
    );
  }
});

Deno.test("discovery checkpoint parser rejects duplicate nomination exposure fact identifiers", () => {
  const payload = succeededDiscoveryCheckpoint("screen");
  payload.research_nominations = [{
    id: "00000000-0000-4000-8000-000000000046",
    security_revision_id: DISCOVERY_SECURITY_ID,
    theme_episode_revision_id: null,
    exposure_fact_ids: [DISCOVERY_EXPOSURE_ID, DISCOVERY_EXPOSURE_ID],
    state: "nominated",
    rationale: { basis: "research" },
  }];
  assertThrows(
    () => parseDiscoveryStageCheckpointPayload(payload),
    "exposure_fact_ids is duplicated",
  );
});

Deno.test("discovery parser rejects normalized nested authority and order semantics", () => {
  for (
    const result of [
      { nested: { order: { side: "buy" } } },
      { nested: { order_details: { side: "buy" } } },
      { portfolioOverlap: { ticker: "TEST" } },
      { executionAllowed: false },
      { nested: { "ORDER-ID": "fixture" } },
    ]
  ) {
    const payload = {
      task: { ...discoveryTask(), result },
      exposure_facts: [],
      theme_episode_revisions: [],
      research_nominations: [],
    };
    assertThrows(
      () => parseDiscoveryStageCheckpointPayload(payload),
      "forbidden field",
    );
  }
});

Deno.test("discovery stage parser rejects unapproved providers, authority fields, and oversized results", () => {
  const unapproved = {
    task: { ...discoveryTask(), provider: "premium_feed" },
    exposure_facts: [],
    theme_episode_revisions: [],
    research_nominations: [],
  };
  assertThrows(
    () => parseDiscoveryStageCheckpointPayload(unapproved),
    "provider",
  );

  const authority = {
    task: { ...discoveryTask(), result: { action: "buy" } },
    exposure_facts: [],
    theme_episode_revisions: [],
    research_nominations: [],
  };
  assertThrows(
    () => parseDiscoveryStageCheckpointPayload(authority),
    "forbidden field",
  );

  const oversized = {
    task: { ...discoveryTask(), result: { note: "x".repeat(65_537) } },
    exposure_facts: [],
    theme_episode_revisions: [],
    research_nominations: [],
  };
  assertThrows(
    () => parseDiscoveryStageCheckpointPayload(oversized),
    "byte limit",
  );

  const wrongExposureKind = {
    task: {
      ...discoveryTask(),
      stage: "enrich",
      state: "succeeded",
      attempt_count: 1,
    },
    exposure_facts: [{
      id: "00000000-0000-4000-8000-000000000043",
      security_revision_id: "00000000-0000-4000-8000-000000000044",
      theme_episode_revision_id: null,
      exposure_kind: "analyst_guess",
      fact: { basis: "10-K" },
      source_ids: ["sec:fixture"],
      valid_from: "2026-09-06T00:00:00.000Z",
      valid_to: null,
      content_hash: "d".repeat(64),
    }],
    theme_episode_revisions: [],
    research_nominations: [],
  };
  assertThrows(
    () => parseDiscoveryStageCheckpointPayload(wrongExposureKind),
    "exposure_kind",
  );
});

Deno.test("reference and context parsers are exact, bounded, and research-only", () => {
  const manifest = discoveryManifest();
  assertEquals(
    parseDiscoveryReferencePayload({ manifest, security_revisions: [] })
      .manifest,
    manifest,
  );
  assertEquals(parseDiscoveryContextRequest({ limit: 100 }), { limit: 100 });
  assertThrows(() => parseDiscoveryContextRequest({ limit: 101 }), "limit");
  assertThrows(
    () =>
      parseDiscoveryReferencePayload({
        manifest: { ...manifest, authority: "execute" },
        security_revisions: [],
      }),
    "unexpected key",
  );
  assertThrows(
    () =>
      parseDiscoveryContext({
        manifests: [{ id: manifest.id, rogue: true }],
        security_revisions: [],
        tasks: [],
        theme_episodes: [],
        exposure_facts: [],
        research_nominations: [],
      }),
    "unexpected key",
  );
});
