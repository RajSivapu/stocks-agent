import { fetchAdjustedHistory, fetchIntradayQuoteEvidence, fetchVerifiedQuote } from "./market-data.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
  }
}

async function assertRejects(
  callback: () => Promise<unknown>,
  message: string,
): Promise<void> {
  try {
    await callback();
  } catch (error) {
    if (error instanceof Error && error.message.includes(message)) return;
    throw new Error(
      `expected rejection containing ${message}, got ${String(error)}`,
    );
  }
  throw new Error(`expected rejection containing ${message}`);
}

function fixtureFetch(
  body: unknown,
  options: { status?: number; headers?: HeadersInit } = {},
) {
  return (_input: RequestInfo | URL, _init?: RequestInit) =>
    Promise.resolve(
      new Response(
        JSON.stringify(body),
        { status: options.status ?? 200, headers: options.headers },
      ),
    );
}

const quoteFixture = {
  chart: {
    result: [{
      meta: {
        regularMarketPrice: 380.16,
        previousClose: 377.89,
        regularMarketTime: Date.parse("2026-09-02T17:00:00.000Z") / 1000,
        marketState: "REGULAR",
      },
    }],
    error: null,
  },
};

Deno.test("verified quote uses provider timestamp and decimal strings", async () => {
  const result = await fetchVerifiedQuote(
    "VTI",
    fixtureFetch(quoteFixture),
    new Date("2026-09-02T17:01:00.000Z"),
  );
  assertEquals(result, {
    ticker: "VTI",
    price: "380.16",
    previous_close: "377.89",
    as_of: "2026-09-02T17:00:00.000Z",
    market_state: "REGULAR",
    source: "yahoo-chart",
  });
});

Deno.test("verified quote derives the session from provider trading windows when marketState is omitted", async () => {
  const withoutMarketState = structuredClone(quoteFixture);
  const meta = withoutMarketState.chart.result[0].meta as Record<
    string,
    unknown
  >;
  delete meta.marketState;
  meta.currentTradingPeriod = {
    pre: {
      start: Date.parse("2026-09-02T08:00:00.000Z") / 1000,
      end: Date.parse("2026-09-02T13:30:00.000Z") / 1000,
    },
    regular: {
      start: Date.parse("2026-09-02T13:30:00.000Z") / 1000,
      end: Date.parse("2026-09-02T20:00:00.000Z") / 1000,
    },
    post: {
      start: Date.parse("2026-09-02T20:00:00.000Z") / 1000,
      end: Date.parse("2026-09-03T00:00:00.000Z") / 1000,
    },
  };

  const regular = await fetchVerifiedQuote(
    "VTI",
    fixtureFetch(withoutMarketState),
    new Date("2026-09-02T17:01:00.000Z"),
  );
  assertEquals(regular.market_state, "REGULAR");

  const post = await fetchVerifiedQuote(
    "VTI",
    fixtureFetch(withoutMarketState),
    new Date("2026-09-02T20:05:00.000Z"),
  );
  assertEquals(post.market_state, "POST");
});

Deno.test("quote parser fails closed when both marketState and trading windows are unusable", async () => {
  const missingSession = structuredClone(quoteFixture);
  const meta = missingSession.chart.result[0].meta as Record<string, unknown>;
  delete meta.marketState;
  await assertRejects(
    () =>
      fetchVerifiedQuote(
        "VTI",
        fixtureFetch(missingSession),
        new Date("2026-09-02T17:01:00.000Z"),
      ),
    "invalid quote response",
  );
});

Deno.test("quote request uses one encoded canonical ticker path", async () => {
  let requested = "";
  await fetchVerifiedQuote("BRK.B", (input: RequestInfo | URL) => {
    requested = String(input);
    return Promise.resolve(new Response(JSON.stringify(quoteFixture)));
  }, new Date("2026-09-02T17:01:00.000Z"));
  assert(
    requested ===
      "https://query1.finance.yahoo.com/v8/finance/chart/BRK.B?range=5d&interval=1d",
    "unexpected provider URL",
  );
  await assertRejects(
    () =>
      fetchVerifiedQuote(
        "VTI?period=MAX",
        fixtureFetch(quoteFixture),
        new Date(),
      ),
    "invalid ticker",
  );
});

Deno.test("quote parser rejects missing, null, future, and provider errors", async () => {
  const now = new Date("2026-09-02T17:01:00.000Z");
  await assertRejects(
    () =>
      fetchVerifiedQuote("VTI", fixtureFetch({ chart: { result: null } }), now),
    "invalid quote response",
  );
  await assertRejects(
    () =>
      fetchVerifiedQuote(
        "VTI",
        fixtureFetch({
          chart: {
            result: [{
              meta: {
                ...quoteFixture.chart.result[0].meta,
                regularMarketPrice: null,
              },
            }],
          },
        }),
        now,
      ),
    "invalid quote response",
  );
  await assertRejects(
    () =>
      fetchVerifiedQuote(
        "VTI",
        fixtureFetch({
          chart: {
            result: [{
              meta: {
                ...quoteFixture.chart.result[0].meta,
                regularMarketTime: "bad",
              },
            }],
          },
        }),
        now,
      ),
    "invalid quote response",
  );
  await assertRejects(
    () =>
      fetchVerifiedQuote(
        "VTI",
        fixtureFetch(quoteFixture, { status: 503 }),
        now,
      ),
    "provider request failed",
  );
});

Deno.test("response reader rejects declared and streamed overlong bodies", async () => {
  const oversizedHeaders = fixtureFetch({}, {
    headers: { "content-length": "1048577" },
  });
  await assertRejects(
    () => fetchVerifiedQuote("VTI", oversizedHeaders, new Date()),
    "provider response too large",
  );

  const bytes = new Uint8Array(1_048_577);
  const streamed = () =>
    Promise.resolve(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(bytes);
            controller.close();
          },
        }),
      ),
    );
  await assertRejects(
    () => fetchVerifiedQuote("VTI", streamed, new Date()),
    "provider response too large",
  );
});

const historyFixture = {
  chart: {
    result: [{
      timestamp: [
        Date.parse("2026-09-02T20:00:00.000Z") / 1000,
        Date.parse("2026-09-01T20:00:00.000Z") / 1000,
        Date.parse("2026-09-02T20:01:00.000Z") / 1000,
      ],
      indicators: {
        quote: [{
          close: [101, 50, 102],
          high: [103, 52, 104],
          low: [99, 48, 100],
        }],
        adjclose: [{ adjclose: [101, 100, 102] }],
      },
      events: {
        splits: {
          split: {
            date: Date.parse("2026-09-01T13:30:00.000Z") / 1000,
            numerator: 2,
            denominator: 1,
            splitRatio: "2:1",
          },
        },
      },
    }],
    error: null,
  },
};

Deno.test("adjusted history keeps raw ranges and split ratios, sorted and deduplicated", async () => {
  const bars = await fetchAdjustedHistory(
    "VTI",
    "1y",
    fixtureFetch(historyFixture),
  );
  assertEquals(bars, [
    {
      date: "2026-09-01",
      raw_close: "50",
      adjusted_close: "100",
      raw_high: "52",
      raw_low: "48",
      split_ratio: "2",
    },
    {
      date: "2026-09-02",
      raw_close: "102",
      adjusted_close: "102",
      raw_high: "104",
      raw_low: "100",
      split_ratio: null,
    },
  ]);
});

Deno.test("history normalizes provider floating-point noise to six decimals", async () => {
  const noisy = structuredClone(historyFixture);
  noisy.chart.result[0].indicators.quote[0].close[1] = 320.1400146484375;
  noisy.chart.result[0].indicators.quote[0].high[1] = 320.2300109863281;
  noisy.chart.result[0].indicators.quote[0].low[1] = 317.32000732421875;
  noisy.chart.result[0].indicators.adjclose[0].adjclose[1] =
    316.4450378417969;

  const bars = await fetchAdjustedHistory("VTI", "1y", fixtureFetch(noisy));
  assertEquals(bars[0], {
    date: "2026-09-01",
    raw_close: "320.140015",
    adjusted_close: "316.445038",
    raw_high: "320.230011",
    raw_low: "317.320007",
    split_ratio: "2",
  });
});

Deno.test("history rejects missing adjusted values and arbitrary ranges", async () => {
  const missing = structuredClone(historyFixture);
  missing.chart.result[0].indicators.adjclose[0].adjclose[1] =
    null as unknown as number;
  await assertRejects(
    () => fetchAdjustedHistory("VTI", "1y", fixtureFetch(missing)),
    "invalid history response",
  );
  await assertRejects(
    () =>
      fetchAdjustedHistory("VTI", "max" as "1y", fixtureFetch(historyFixture)),
    "invalid history range",
  );
});

Deno.test("intraday evidence returns bounded same-session quote points with provider timestamps", async () => {
  const timestamps = [
    "2026-09-02T13:29:00.000Z",
    "2026-09-02T16:58:00.000Z",
    "2026-09-02T16:59:00.000Z",
    "2026-09-02T17:00:00.000Z",
  ].map((value) => Date.parse(value) / 1000);
  const payload = {
    chart: { result: [{
      meta: {
        marketState: "REGULAR",
        currentTradingPeriod: {
          pre: { start: Date.parse("2026-09-02T08:00:00.000Z") / 1000, end: Date.parse("2026-09-02T13:30:00.000Z") / 1000 },
          regular: { start: Date.parse("2026-09-02T13:30:00.000Z") / 1000, end: Date.parse("2026-09-02T20:00:00.000Z") / 1000 },
          post: { start: Date.parse("2026-09-02T20:00:00.000Z") / 1000, end: Date.parse("2026-09-03T00:00:00.000Z") / 1000 },
        },
      },
      timestamp: timestamps,
      indicators: { quote: [{ close: [40, 41.9, 42.05, 42.1] }] },
    }], error: null },
  };
  let requested = "";
  const evidence = await fetchIntradayQuoteEvidence("ABC", (input) => {
    requested = String(input);
    return Promise.resolve(new Response(JSON.stringify(payload)));
  }, new Date("2026-09-02T17:01:00.000Z"));
  assertEquals(evidence, {
    ticker: "ABC",
    market_session: "regular",
    source: "yahoo-chart",
    points: [
      { value: "41.9", comparison_value: null, observed_at: "2026-09-02T16:58:00.000Z", bar_complete: true },
      { value: "42.05", comparison_value: null, observed_at: "2026-09-02T16:59:00.000Z", bar_complete: true },
      { value: "42.1", comparison_value: null, observed_at: "2026-09-02T17:00:00.000Z", bar_complete: true },
    ],
  });
  assert(requested.endsWith("/ABC?range=1d&interval=1m&includePrePost=true"), "unexpected intraday URL");
});

Deno.test("intraday evidence fails closed on mixed, missing, or future latest data", async () => {
  const now = new Date("2026-09-02T17:01:00.000Z");
  for (const result of [
    { meta: { marketState: "REGULAR" }, timestamp: [], indicators: { quote: [{ close: [] }] } },
    { meta: { marketState: "REGULAR" }, timestamp: [Date.parse("2026-09-02T17:07:00.000Z") / 1000], indicators: { quote: [{ close: [42] }] } },
    { meta: { marketState: "CLOSED" }, timestamp: [Date.parse("2026-09-02T17:00:00.000Z") / 1000], indicators: { quote: [{ close: [42] }] } },
  ]) {
    await assertRejects(
      () => fetchIntradayQuoteEvidence("ABC", fixtureFetch({ chart: { result: [result] } }), now),
      "invalid intraday response",
    );
  }
});

Deno.test("intraday evidence rejects nonincreasing timestamps and excludes a forming minute", async () => {
  const periods = {
    pre: { start: Date.parse("2026-09-02T08:00:00.000Z") / 1000, end: Date.parse("2026-09-02T13:30:00.000Z") / 1000 },
    regular: { start: Date.parse("2026-09-02T13:30:00.000Z") / 1000, end: Date.parse("2026-09-02T20:00:00.000Z") / 1000 },
    post: { start: Date.parse("2026-09-02T20:00:00.000Z") / 1000, end: Date.parse("2026-09-03T00:00:00.000Z") / 1000 },
  };
  const make = (timestamps: number[]) => ({
    chart: { result: [{
      meta: { marketState: "REGULAR", currentTradingPeriod: periods },
      timestamp: timestamps,
      indicators: { quote: [{ close: timestamps.map((_, index) => 41 + index) }] },
    }], error: null },
  });
  const at = (value: string) => Date.parse(value) / 1000;
  await assertRejects(
    () => fetchIntradayQuoteEvidence("ABC", fixtureFetch(make([
      at("2026-09-02T16:59:00.000Z"), at("2026-09-02T16:58:00.000Z"),
    ])), new Date("2026-09-02T17:01:00.000Z")),
    "invalid intraday response",
  );
  const evidence = await fetchIntradayQuoteEvidence("ABC", fixtureFetch(make([
    at("2026-09-02T16:58:00.000Z"), at("2026-09-02T16:59:00.000Z"),
    at("2026-09-02T17:00:00.000Z"),
  ])), new Date("2026-09-02T17:00:30.000Z"));
  assertEquals(evidence.points.map((point) => point.observed_at), [
    "2026-09-02T16:58:00.000Z", "2026-09-02T16:59:00.000Z",
  ]);
});
