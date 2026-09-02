import {
  formatFixed,
  multiplyFixed,
  parseFixed,
} from "./fixed-point.ts";

function assertEquals<T>(actual: T, expected: T): void {
  if (actual !== expected) {
    throw new Error(`expected ${String(expected)}, got ${String(actual)}`);
  }
}

function assertThrows(fn: () => unknown, message: string): void {
  try {
    fn();
  } catch (error) {
    if (!(error instanceof Error) || !error.message.includes(message)) {
      throw new Error(`expected error containing ${message}, got ${String(error)}`);
    }
    return;
  }
  throw new Error(`expected error containing ${message}`);
}

Deno.test("fixed point computes fractional-share value without Number", () => {
  const priceMicros = parseFixed("47.02", 6);
  const shareUnits = parseFixed("43.748192", 8);
  assertEquals(
    formatFixed(multiplyFixed(priceMicros, shareUnits, 8), 6),
    "2057.039987",
  );
});

Deno.test("fixed point preserves and trims decimal scale deterministically", () => {
  assertEquals(parseFixed("0", 6), 0n);
  assertEquals(parseFixed("12.3400", 6), 12_340_000n);
  assertEquals(formatFixed(12_340_000n, 6), "12.34");
  assertEquals(formatFixed(12_000_000n, 6), "12");
});

Deno.test("fixed point rejects signs, exponent notation, and excess precision", () => {
  for (const value of ["-1", "+1", "1e3", "01", "1.", ".5"]) {
    assertThrows(() => parseFixed(value, 6), "decimal string");
  }
  assertThrows(() => parseFixed("1.0000001", 6), "fractional digits");
});

Deno.test("fixed point rejects unsafe whole-unit magnitudes", () => {
  assertThrows(() => parseFixed("1000000000000001", 6), "maximum");
});

