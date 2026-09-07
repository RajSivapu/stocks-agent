import { canonicalJson, sha256Hex } from "./intelligence.ts";
import type { FetchLike } from "./market-data.ts";

export interface CollectionQuote {
  ticker: string;
  currency: "USD";
  price: string;
  as_of: string;
  instrument_type: "EQUITY" | "ETF";
  average_daily_dollar_volume: string | null;
  liquidity_sample_count: number;
  liquidity_window: { start: string; end: string } | null;
  retrieved_at: string;
  response_hash: string;
}

function decimal(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || value > 1e12) throw new Error("INVALID_QUOTE");
  return value.toFixed(6).replace(/\.?0+$/, "");
}

export async function fetchCollectionQuote(
  ticker: string,
  now: Date,
  expectedInstrumentType: "COMMON_STOCK" | "ADR" | "ETF",
  fetchImpl: FetchLike = fetch,
): Promise<CollectionQuote> {
  if (!/^[A-Z][A-Z0-9.-]{0,14}$/.test(ticker)) throw new Error("INVALID_QUOTE");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);
  try {
    const response = await fetchImpl(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=5d&interval=1d`, {
      redirect: "error", signal: controller.signal,
    });
    if (!response.ok || !response.body) throw new Error("SOURCE_UNAVAILABLE");
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 1_000_000) { await reader.cancel(); throw new Error("INVALID_QUOTE"); }
      chunks.push(value);
    }
    const raw = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.length; }
    const text = new TextDecoder("utf-8", { fatal: true }).decode(raw);
    const payload = JSON.parse(text);
    if (payload.chart?.result?.length !== 1) throw new Error("INVALID_QUOTE");
    const result = payload.chart.result[0];
    const meta = result.meta;
    const expectedYahooType = expectedInstrumentType === "ETF" ? "ETF" : "EQUITY";
    if (meta.symbol !== ticker || meta.currency !== "USD" || meta.instrumentType !== expectedYahooType
      || !Number.isSafeInteger(meta.regularMarketTime)) throw new Error("INVALID_QUOTE");
    const asOf = new Date(meta.regularMarketTime * 1_000);
    if (asOf.valueOf() > now.valueOf() || now.valueOf() - asOf.valueOf() > 20 * 60_000) throw new Error("STALE_QUOTE");
    const observations = result.indicators?.quote?.[0];
    let sampleCount = 0;
    let sampleWindow: { start: string; end: string } | null = null;
    if (Array.isArray(result.timestamp) && result.timestamp.length >= 1 && result.timestamp.length <= 5
      && observations?.close?.length === result.timestamp.length && observations?.volume?.length === result.timestamp.length) {
      try {
        observations.close.forEach((close: unknown, index: number) => {
          decimal(close);
          decimal(observations.volume[index]);
          const timestamp = result.timestamp[index];
          if (!Number.isSafeInteger(timestamp)) throw new Error("INVALID_QUOTE");
          const instant = new Date(timestamp * 1_000);
          if (instant.valueOf() > now.valueOf()) throw new Error("INVALID_QUOTE");
        });
        const timestamps = result.timestamp.map((value: number) => new Date(value * 1_000).toISOString());
        sampleCount = timestamps.length;
        sampleWindow = { start: timestamps[0], end: timestamps[timestamps.length - 1] };
      } catch { /* Incomplete volume is unavailable, never a neutral liquidity score. */ }
    }
    return { ticker, currency: "USD", price: decimal(meta.regularMarketPrice), as_of: asOf.toISOString(),
      instrument_type: meta.instrumentType, average_daily_dollar_volume: null,
      liquidity_sample_count: sampleCount, liquidity_window: sampleWindow,
      retrieved_at: now.toISOString(), response_hash: sha256Hex(text) };
  } finally { clearTimeout(timeout); }
}

export function quoteCheckpoint(input: Record<string, unknown>, window: Record<string, unknown>, quote: CollectionQuote | null, now: Date): Record<string, unknown> {
  const ticker = input.ticker as string;
  const retrieved = quote?.retrieved_at ?? now.toISOString();
  const requestUrl = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=5d&interval=1d`;
  const canonical = canonicalJson({ ticker, quote });
  const receipt = { provider: "yahoo", reservation_id: input.reservation_id, status: quote ? "succeeded" : "failed",
    cache_key: input.cache_key, requested_window: { start: window.start, end: window.end }, requested_limit: 20,
    retrieved_at: retrieved, observed_at: quote?.as_of ?? null,
    expires_at: quote ? new Date(Date.parse(retrieved) + 15 * 60_000).toISOString() : null,
    request_cost: 1, upstream_remaining: null, returned_count: quote ? 1 : 0, accepted_count: quote ? 1 : 0,
    duplicate_count: 0, dropped_count: 0, response_hash: quote?.response_hash ?? null,
    error_code: quote ? null : "SOURCE_UNAVAILABLE", source_receipt_id: input.source_receipt_id, cache_predecessor_receipt_id: null };
  return { cache_key: input.cache_key, receipt, items: quote ? [{ provider: "yahoo", upstream_item_id: `${ticker}:${quote.as_of}`,
    source_url: `https://finance.yahoo.com/quote/${encodeURIComponent(ticker)}`, title: `${ticker} market quote`,
    normalized_text: canonical, canonical_content: canonical, content_hash: sha256Hex(canonical),
    published_at: quote.as_of, effective_at: quote.as_of, retrieved_at: retrieved,
    authority: "market_data", metadata: {}, request_url: requestUrl, reporting_at: null, entity_ids: [], security_ids: [ticker] }] : [] };
}
