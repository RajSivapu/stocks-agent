import { parseGatewayEnvelope } from "./contracts.ts";
import { canonicalJson, sha256Hex } from "./intelligence.ts";

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
  const item = (parsed.payload as { items: Array<Record<string, unknown>> }).items[0];
  assertEquals(item.request_url, "https://api.gdeltproject.org/api/v2/doc/doc?query=grid");

  const invalid = validRecordIntelligenceEnvelope();
  Object.assign(invalid.payload.items[0], {
    request_url: "https://www.sec.gov/submissions/CIK0000000001.json",
    retrieved_at: "2026-09-04T12:01:00.000Z",
    reporting_at: null,
    entity_ids: [], security_ids: [], discovery_status: "no_event",
  });
  assertThrows(() => parseGatewayEnvelope(invalid), "request_url");

  const secretBearing = validRecordIntelligenceEnvelope();
  secretBearing.payload.items[0].request_url =
    "https://api.gdeltproject.org/api/v2/doc/doc?api_key=not-persistable";
  assertThrows(() => parseGatewayEnvelope(secretBearing), "secret-bearing");

  const corroborating = validRecordIntelligenceEnvelope();
  Object.assign(corroborating.payload.items[0], {
    disposition: "near_duplicate", drop_reason: "similar_normalized_content",
  });
  assertEquals(
    ((parseGatewayEnvelope(corroborating).payload as { items: Array<Record<string, unknown>> }).items[0])
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
