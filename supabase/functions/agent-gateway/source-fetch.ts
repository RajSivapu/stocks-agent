import type { PacketEvidenceFact, PacketSearchReceipt } from "./evidence-packet.ts";

export type ResearchCategory = "filing" | "fundamentals" | "news" | "issuer" | "exchange" | "sector" | "macro";
export type ResearchSourceRequest = { evidence_id: string; category: Exclude<ResearchCategory, "sector" | "macro">; url: string };
export type ResearchRequest = {
  categories: ResearchCategory[];
  result_status: "material_evidence_found" | "no_new_material_evidence";
  sources: ResearchSourceRequest[];
};

const EVIDENCE_ID = /^[a-z0-9][a-z0-9._:-]{0,99}$/;
const TICKER = /^[A-Z][A-Z0-9.-]{0,14}$/;
const CATEGORY = new Set(["filing", "fundamentals", "news", "issuer", "exchange", "sector", "macro"]);
const FACT_CATEGORY = new Set(["filing", "fundamentals", "news", "issuer", "exchange"]);
const MAX_SOURCE_BYTES = 256 * 1024;

function exactObject(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid research request");
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== keys.length || keys.some((key) => !Object.hasOwn(row, key))) {
    throw new Error("invalid research request");
  }
  return row;
}

function allowedUrl(raw: unknown): string {
  if (typeof raw !== "string" || raw.length < 1 || raw.length > 500) throw new Error("source URL is invalid");
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("source URL is invalid");
  }
  if (url.protocol !== "https:" || url.username || url.password || url.port || url.hash) {
    throw new Error("source URL is invalid");
  }
  if (url.hostname === "www.sec.gov") {
    if (!/^\/(?:Archives|files|submissions)\//.test(url.pathname) || url.search) throw new Error("source URL is invalid");
  } else if (url.hostname === "query1.finance.yahoo.com") {
    const match = /^\/v8\/finance\/chart\/([^/]+)$/.exec(url.pathname);
    if (!match || !TICKER.test(decodeURIComponent(match[1]))) throw new Error("source URL is invalid");
    const allowed = new Set(["range", "interval", "events"]);
    for (const [key, value] of url.searchParams) {
      if (!allowed.has(key) || value.length > 30 || /token|key|secret/i.test(key)) throw new Error("source URL is invalid");
    }
  } else if (url.hostname === "finnhub.io") {
    if (url.pathname !== "/api/v1/quote" || [...url.searchParams.keys()].some((key) => key !== "symbol") ||
      !TICKER.test(url.searchParams.get("symbol") ?? "")) throw new Error("source URL is invalid");
  } else {
    throw new Error("source URL host is not allowed");
  }
  return url.toString();
}

export function parseEvidenceReadPayload(value: unknown): { research: ResearchRequest | null } {
  const row = exactObject(value, value && typeof value === "object" && !Array.isArray(value) && Object.hasOwn(value, "research")
    ? ["research"] : []);
  if (!Object.hasOwn(row, "research")) return { research: null };
  const research = exactObject(row.research, ["categories", "result_status", "sources"]);
  if (!Array.isArray(research.categories) || research.categories.length < 1 || research.categories.length > 8 ||
    research.categories.some((item) => typeof item !== "string" || !CATEGORY.has(item)) ||
    new Set(research.categories).size !== research.categories.length ||
    !["material_evidence_found", "no_new_material_evidence"].includes(String(research.result_status)) ||
    !Array.isArray(research.sources) || research.sources.length < 1 || research.sources.length > 12) {
    throw new Error("invalid research request");
  }
  const sources = research.sources.map((value) => {
    const source = exactObject(value, ["evidence_id", "category", "url"]);
    if (typeof source.evidence_id !== "string" || !EVIDENCE_ID.test(source.evidence_id) ||
      typeof source.category !== "string" || !FACT_CATEGORY.has(source.category)) {
      throw new Error("invalid research request");
    }
    return {
      evidence_id: source.evidence_id,
      category: source.category as ResearchSourceRequest["category"],
      url: allowedUrl(source.url),
    };
  });
  if (new Set(sources.map((source) => source.evidence_id)).size !== sources.length) {
    throw new Error("invalid research request");
  }
  return {
    research: {
      categories: research.categories as ResearchCategory[],
      result_status: research.result_status as ResearchRequest["result_status"],
      sources,
    },
  };
}

async function boundedBody(response: Response): Promise<Uint8Array> {
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_SOURCE_BYTES)) {
    throw new Error("source response is too large");
  }
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > MAX_SOURCE_BYTES) {
      await reader.cancel();
      throw new Error("source response is too large");
    }
    chunks.push(value);
  }
  const body = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.length;
  }
  return body;
}

async function digest(value: Uint8Array): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", value.slice().buffer as ArrayBuffer)),
    (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function fetchOne(
  source: ResearchSourceRequest,
  fetcher: typeof fetch,
  now: Date,
): Promise<{ fact: PacketEvidenceFact | null; receipt: PacketSearchReceipt["sources"][number] }> {
  let current = source.url;
  for (let redirects = 0; redirects <= 2; redirects += 1) {
    let response: Response;
    try {
      response = await fetcher(current, {
        method: "GET",
        redirect: "manual",
        headers: { "accept": "application/json,text/plain,text/html,application/xml", "user-agent": "stock-agent-evidence/1.0 security@invalid.example" },
        signal: AbortSignal.timeout(8_000),
      });
    } catch {
      return { fact: null, receipt: { url: source.url, status: "unavailable", content_hash: null } };
    }
    if (response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location");
      if (!location || redirects === 2) throw new Error("source redirect is invalid");
      current = allowedUrl(new URL(location, current).toString());
      continue;
    }
    if (!response.ok) return { fact: null, receipt: { url: source.url, status: "unavailable", content_hash: null } };
    const contentType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() ?? "";
    if (contentType && !["application/json", "text/plain", "text/html", "application/xml", "text/xml"].includes(contentType)) {
      return { fact: null, receipt: { url: source.url, status: "unavailable", content_hash: null } };
    }
    const body = await boundedBody(response);
    const contentHash = await digest(body);
    const lastModified = response.headers.get("last-modified");
    const observedAt = lastModified && !Number.isNaN(Date.parse(lastModified)) && Date.parse(lastModified) <= now.valueOf() + 5 * 60_000
      ? new Date(lastModified).toISOString()
      : null;
    return {
      fact: {
        evidence_id: source.evidence_id,
        source_run_id: null,
        category: source.category,
        source_identifier: new URL(current).hostname,
        reference_identifier: current,
        observed_at: observedAt,
        retrieved_at: now.toISOString(),
        revalidated_at: null,
        content_hash: contentHash,
        claims: [],
        status: "fresh",
      },
      receipt: { url: source.url, status: "fetched", content_hash: contentHash },
    };
  }
  throw new Error("source redirect is invalid");
}

export async function fetchResearchSources(
  request: ResearchRequest,
  fetcher: typeof fetch = fetch,
  now: Date = new Date(),
): Promise<{ facts: PacketEvidenceFact[]; search_receipt: PacketSearchReceipt }> {
  const results = await Promise.all(request.sources.map((source) => fetchOne(source, fetcher, now)));
  const facts = results.flatMap((result) => result.fact ? [result.fact] : []);
  const sources = results.map((result) => result.receipt);
  const allFetched = facts.length === request.sources.length;
  const resultStatus = allFetched ? request.result_status : "source_unavailable";
  const contentHash = await digest(new TextEncoder().encode(JSON.stringify({
    categories: request.categories,
    sources,
    result_status: resultStatus,
  })));
  if (allFetched) {
    facts.push({
      evidence_id: `source-search-${contentHash.slice(0, 16)}`,
      source_run_id: null,
      category: "source_search",
      source_identifier: "server-source-retrieval",
      reference_identifier: null,
      observed_at: null,
      retrieved_at: now.toISOString(),
      revalidated_at: null,
      content_hash: contentHash,
      claims: [],
      status: resultStatus === "no_new_material_evidence" ? "no_new_material_evidence" : "fresh",
    });
  }
  return {
    facts: allFetched ? facts : [],
    search_receipt: {
      searched_at: now.toISOString(),
      categories: request.categories,
      sources,
      result_status: resultStatus,
      content_hash: contentHash,
    },
  };
}
