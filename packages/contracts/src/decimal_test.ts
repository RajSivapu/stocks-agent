/// <reference lib="deno.ns" />

import { parseMoney, parsePrice, parseShares } from "./decimal.ts";

function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertThrows(callback: () => unknown, expected: string): void {
  try {
    callback();
  } catch (error) {
    if (error instanceof Error && error.message.includes(expected)) return;
    throw error;
  }
  throw new Error(`expected error containing ${expected}`);
}

Deno.test("financial parsers accept strings and return canonical decimal text", () => {
  assertEquals(parseShares("00043.74819200"), "43.748192");
  assertEquals(parsePrice("047.0200"), "47.02");
  assertEquals(parseMoney("02057.04"), "2057.04");
  assertEquals(parseMoney("0"), "0");
});

Deno.test("share parser enforces positivity precision and one million cap", () => {
  assertEquals(parseShares("1000000.00000000"), "1000000");
  for (const invalid of [0, "0", "-1", "+1", "1e3", ".5", "1.000000001", "1000000.00000001"]) {
    assertThrows(() => parseShares(invalid), "shares");
  }
});

Deno.test("price parser enforces four decimals and reviewed bounds", () => {
  assertEquals(parsePrice("0.0001"), "0.0001");
  assertEquals(parsePrice("1000000"), "1000000");
  for (const invalid of [47.02, "0", "0.00001", "1000000.0001", "Infinity", "NaN"]) {
    assertThrows(() => parsePrice(invalid), "price");
  }
});

Deno.test("money parser accepts two decimals only and rejects JSON numbers", () => {
  for (const invalid of [2057.04, "-1", "1.001", "1e2", " 1", "1 "]) {
    assertThrows(() => parseMoney(invalid), "money");
  }
});
