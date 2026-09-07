import { fetchCollectionQuote, quoteCheckpoint } from "./collection-quotes.ts";

function assert(value: unknown): asserts value { if (!value) throw new Error("assertion failed"); }
Deno.test("the server quote producer validates identity, freshness, and preserves only bounded liquidity samples", async () => {
  const now = new Date("2026-09-04T15:00:00Z");
  const meta = { symbol: "TEST", currency: "USD", instrumentType: "EQUITY", regularMarketPrice: 100, regularMarketTime: now.valueOf()/1000 };
  const payload = { chart: { result: [{ meta, timestamp: [now.valueOf()/1000], indicators: { quote: [{ close: [100], volume: [50000] }] } }] } };
  const fetcher = (_url: unknown, init?: RequestInit) => {
    assert(init?.redirect === "error");
    return Promise.resolve(Response.json(payload));
  };
  const quote = await fetchCollectionQuote("TEST", now, "COMMON_STOCK", fetcher);
  assert(quote.price === "100" && quote.average_daily_dollar_volume === null);
  assert(quote.liquidity_sample_count === 1);
  assert(quote.liquidity_window?.start === now.toISOString());
  assert(quote.response_hash.length === 64);
  for (const mutation of [
    { symbol: "WRONG" }, { currency: "EUR" }, { regularMarketPrice: 0 },
    { regularMarketTime: now.valueOf() / 1000 - 3600 },
    { regularMarketTime: now.valueOf() / 1000 + 60 },
  ]) {
    let rejected = false;
    try { await fetchCollectionQuote("TEST", now, "COMMON_STOCK", () => Promise.resolve(Response.json({ chart: { result: [{ ...payload.chart.result[0], meta: { ...meta, ...mutation } }] } }))); }
    catch { rejected = true; }
    assert(rejected);
  }
  let incompatible = false;
  try { await fetchCollectionQuote("TEST", now, "ETF", fetcher); } catch { incompatible = true; }
  assert(incompatible);
  const malformedLiquidity = await fetchCollectionQuote("TEST", now, "COMMON_STOCK", () =>
    Promise.resolve(Response.json({
      chart: { result: [{
        ...payload.chart.result[0],
        indicators: { quote: [{ close: [100], volume: [0] }] },
      }] },
    }))
  );
  assert(malformedLiquidity.average_daily_dollar_volume === null);
  assert(malformedLiquidity.liquidity_sample_count === 0);
  assert(malformedLiquidity.liquidity_window === null);
  const checkpoint = quoteCheckpoint({ ticker: "TEST", cache_key: "key", source_receipt_id: "original", reservation_id: "r" }, { start: "start", end: "end" }, quote, now);
  assert((checkpoint.receipt as Record<string, unknown>).request_cost === 1);
});
