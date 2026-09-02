import type { VerifiedQuote } from "./contracts.ts";

export interface AdjustedBar {
  date: string;
  raw_close: string;
  adjusted_close: string;
  raw_high: string;
  raw_low: string;
  split_ratio: string | null;
}

export type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

const MAX_RESPONSE_BYTES = 1_048_576;
const REQUEST_TIMEOUT_MS = 15_000;
const TICKER_PATTERN = /^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$/;
const DECIMAL_PATTERN = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;

class MarketDataError extends Error {}

function canonicalTicker(ticker: string): string {
  if (typeof ticker !== "string" || ticker.length > 15 || !TICKER_PATTERN.test(ticker)) {
    throw new MarketDataError("invalid ticker");
  }
  return ticker;
}

function decimal(value: unknown, { nullable = false }: { nullable?: boolean } = {}): string | null {
  if (value === null && nullable) return null;
  if (typeof value !== "number" && typeof value !== "string") {
    throw new MarketDataError("invalid provider decimal");
  }
  if (typeof value === "number" && (!Number.isFinite(value) || value <= 0)) {
    throw new MarketDataError("invalid provider decimal");
  }
  const text = typeof value === "number" ? value.toString() : value;
  if (!DECIMAL_PATTERN.test(text) || text.length > 40 || Number(text) <= 0) {
    throw new MarketDataError("invalid provider decimal");
  }
  const [whole, fraction = ""] = text.split(".");
  if (BigInt(whole) > 1_000_000_000_000_000n || fraction.length > 12) {
    throw new MarketDataError("invalid provider decimal");
  }
  return text;
}

function objectValue(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new MarketDataError("invalid provider object");
  }
  return value as Record<string, unknown>;
}

function arrayValue(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new MarketDataError("invalid provider array");
  return value;
}

async function boundedJson(response: Response): Promise<unknown> {
  const declared = response.headers.get("content-length");
  if (declared !== null) {
    if (!/^\d+$/.test(declared) || Number(declared) > MAX_RESPONSE_BYTES) {
      throw new MarketDataError("provider response too large");
    }
  }
  if (!response.body) throw new MarketDataError("invalid provider response");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_RESPONSE_BYTES) {
      await reader.cancel();
      throw new MarketDataError("provider response too large");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw new MarketDataError("invalid provider response");
  }
}

async function fetchProviderJson(url: string, fetchImpl: FetchLike): Promise<unknown> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetchImpl(url, {
      signal: controller.signal,
      headers: { "user-agent": "stocks-agent-market-gateway/1" },
    });
    if (!response.ok) throw new MarketDataError("provider request failed");
    return await boundedJson(response);
  } catch (error) {
    if (error instanceof MarketDataError) throw error;
    throw new MarketDataError("provider request failed");
  } finally {
    clearTimeout(timeout);
  }
}

function chartResult(payload: unknown, kind: "quote" | "history"): Record<string, unknown> {
  try {
    const chart = objectValue(objectValue(payload).chart);
    const results = arrayValue(chart.result);
    if (results.length !== 1) throw new Error();
    return objectValue(results[0]);
  } catch {
    throw new MarketDataError(`invalid ${kind} response`);
  }
}

export async function fetchVerifiedQuote(
  ticker: string,
  fetchImpl: FetchLike = fetch,
  now: Date = new Date(),
): Promise<VerifiedQuote> {
  const symbol = canonicalTicker(ticker);
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=5d&interval=1d`;
  const payload = await fetchProviderJson(url, fetchImpl);
  try {
    const meta = objectValue(chartResult(payload, "quote").meta);
    const price = decimal(meta.regularMarketPrice);
    const previousClose = decimal(meta.previousClose ?? meta.chartPreviousClose ?? null, { nullable: true });
    if (typeof meta.regularMarketTime !== "number" ||
      !Number.isSafeInteger(meta.regularMarketTime) || meta.regularMarketTime <= 0) {
      throw new Error();
    }
    const asOf = new Date(meta.regularMarketTime * 1000);
    if (Number.isNaN(asOf.valueOf()) || asOf.valueOf() > now.valueOf() + 5 * 60_000) {
      throw new Error();
    }
    if (typeof meta.marketState !== "string" || !/^[A-Z_]{2,24}$/.test(meta.marketState)) {
      throw new Error();
    }
    return {
      ticker: symbol,
      price: price!,
      previous_close: previousClose,
      as_of: asOf.toISOString(),
      market_state: meta.marketState,
      source: "yahoo-chart",
    };
  } catch {
    throw new MarketDataError("invalid quote response");
  }
}

function exchangeDate(epochSeconds: number): string {
  if (!Number.isSafeInteger(epochSeconds) || epochSeconds <= 0) {
    throw new MarketDataError("invalid history response");
  }
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.valueOf())) throw new MarketDataError("invalid history response");
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function splitRatio(value: unknown): string {
  const split = objectValue(value);
  const numerator = decimal(split.numerator);
  const denominator = decimal(split.denominator);
  const numeratorNumber = Number(numerator);
  const denominatorNumber = Number(denominator);
  if (!Number.isSafeInteger(numeratorNumber) || !Number.isSafeInteger(denominatorNumber)) {
    throw new MarketDataError("invalid history response");
  }
  const ratio = numeratorNumber / denominatorNumber;
  if (!Number.isFinite(ratio) || ratio <= 0) throw new MarketDataError("invalid history response");
  return ratio.toFixed(8).replace(/\.?0+$/, "");
}

export async function fetchAdjustedHistory(
  ticker: string,
  range: "1y",
  fetchImpl: FetchLike = fetch,
): Promise<AdjustedBar[]> {
  const symbol = canonicalTicker(ticker);
  if (range !== "1y") throw new MarketDataError("invalid history range");
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=1y&interval=1d&events=div%2Csplits`;
  const payload = await fetchProviderJson(url, fetchImpl);
  try {
    const result = chartResult(payload, "history");
    const timestamps = arrayValue(result.timestamp);
    if (timestamps.length === 0 || timestamps.length > 400) throw new Error();
    const indicators = objectValue(result.indicators);
    const quotes = arrayValue(indicators.quote);
    const adjusted = arrayValue(indicators.adjclose);
    if (quotes.length !== 1 || adjusted.length !== 1) throw new Error();
    const quote = objectValue(quotes[0]);
    const adjustment = objectValue(adjusted[0]);
    const closes = arrayValue(quote.close);
    const highs = arrayValue(quote.high);
    const lows = arrayValue(quote.low);
    const adjustedCloses = arrayValue(adjustment.adjclose);
    if ([closes, highs, lows, adjustedCloses].some((values) => values.length !== timestamps.length)) {
      throw new Error();
    }

    const splitByDate = new Map<string, string>();
    const events = result.events === undefined ? {} : objectValue(result.events);
    const splits = events.splits === undefined ? {} : objectValue(events.splits);
    if (Object.keys(splits).length > 20) throw new Error();
    for (const event of Object.values(splits)) {
      const row = objectValue(event);
      if (typeof row.date !== "number") throw new Error();
      splitByDate.set(exchangeDate(row.date), splitRatio(row));
    }

    const bars = new Map<string, AdjustedBar>();
    for (let index = 0; index < timestamps.length; index += 1) {
      if (typeof timestamps[index] !== "number") throw new Error();
      const date = exchangeDate(timestamps[index] as number);
      const rawClose = decimal(closes[index]);
      const adjustedClose = decimal(adjustedCloses[index]);
      const rawHigh = decimal(highs[index]);
      const rawLow = decimal(lows[index]);
      if (Number(rawLow) > Number(rawClose) || Number(rawClose) > Number(rawHigh)) throw new Error();
      bars.set(date, {
        date,
        raw_close: rawClose!,
        adjusted_close: adjustedClose!,
        raw_high: rawHigh!,
        raw_low: rawLow!,
        split_ratio: splitByDate.get(date) ?? null,
      });
    }
    return [...bars.values()].sort((left, right) => left.date.localeCompare(right.date));
  } catch {
    throw new MarketDataError("invalid history response");
  }
}
