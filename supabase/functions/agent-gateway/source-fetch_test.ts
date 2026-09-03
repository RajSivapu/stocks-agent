import { assertEquals, assertRejects, assertThrows } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { fetchResearchSources, parseEvidenceReadPayload } from "./source-fetch.ts";

const NOW = new Date("2026-09-03T17:00:00.000Z");

Deno.test("research read accepts exact allow-listed public source requests", async () => {
  const request = parseEvidenceReadPayload({ research: {
    categories: ["filing", "news"],
    result_status: "material_evidence_found",
    sources: [
      { evidence_id: "sec-10q", category: "filing", url: "https://www.sec.gov/Archives/edgar/data/1/report.txt" },
      { evidence_id: "vti-chart", category: "news", url: "https://query1.finance.yahoo.com/v8/finance/chart/VTI?range=5d&interval=1d" },
    ],
  } });
  const result = await fetchResearchSources(request.research!, async (input) =>
    new Response(`source:${String(input)}`, { status: 200, headers: { "content-type": "text/plain" } }), NOW);
  assertEquals(result.facts.length, 3);
  assertEquals(result.search_receipt.result_status, "material_evidence_found");
  assertEquals(result.facts.every((fact) => fact.content_hash.length === 64), true);
});

Deno.test("source retrieval rejects SSRF, credentials, redirects, secret query keys, and overlong bodies", async () => {
  for (const url of [
    "http://www.sec.gov/files/company_tickers.json",
    "https://user:pass@www.sec.gov/files/company_tickers.json",
    "https://www.sec.gov:444/files/company_tickers.json",
    "https://127.0.0.1/data",
    "https://www.sec.gov/files/company_tickers.json?token=secret",
    "https://evil.example/data",
  ]) {
    assertThrows(() => parseEvidenceReadPayload({ research: {
      categories: ["filing"], result_status: "no_new_material_evidence",
      sources: [{ evidence_id: "source-one", category: "filing", url }],
    } }), Error);
  }
  const request = parseEvidenceReadPayload({ research: {
    categories: ["filing"], result_status: "no_new_material_evidence",
    sources: [{ evidence_id: "source-one", category: "filing", url: "https://www.sec.gov/files/company_tickers.json" }],
  } });
  await assertRejects(
    () => fetchResearchSources(request.research!, async () =>
      new Response(null, { status: 302, headers: { location: "https://evil.example/data" } }), NOW),
    Error,
    "not allowed",
  );
  await assertRejects(
    () => fetchResearchSources(request.research!, async () =>
      new Response("x".repeat(262_145), { headers: { "content-type": "text/plain" } }), NOW),
    Error,
    "large",
  );
});

Deno.test("zero successful source fetches produce source_unavailable, never a fresh receipt", async () => {
  const request = parseEvidenceReadPayload({ research: {
    categories: ["filing"], result_status: "no_new_material_evidence",
    sources: [{ evidence_id: "source-one", category: "filing", url: "https://www.sec.gov/files/company_tickers.json" }],
  } });
  const result = await fetchResearchSources(request.research!, async () => new Response("down", { status: 503 }), NOW);
  assertEquals(result.facts, []);
  assertEquals(result.search_receipt.result_status, "source_unavailable");
});
