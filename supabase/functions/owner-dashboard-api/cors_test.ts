import { parseAllowedOrigins } from "./cors.ts";

function assertThrows(fn: () => unknown): void {
  let failed = false;
  try {
    fn();
  } catch {
    failed = true;
  }
  if (!failed) throw new Error("expected the origin parser to reject the value");
}

Deno.test("dashboard origins reject noncanonical URL serializations", () => {
  for (const origin of [
    "https://stocks.example.com:443",
    "https://STOCKS.example.com",
    "https://st\u00f6cks.example.com",
    "https://%73tocks.example.com",
  ]) {
    assertThrows(() => parseAllowedOrigins(origin));
  }
});
