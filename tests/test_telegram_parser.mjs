import assert from "node:assert/strict";
import test from "node:test";

import { parsePortfolioCommand } from "../supabase/functions/telegram-portfolio/parser.mjs";

const okCases = [
  ["/buy aapl 2 210 growth", { operation: "buy", ticker: "AAPL", qty: 2, price: 210, bucket: "growth" }],
  ["bought 2.5 aapl at $1,210.50 speculative", { operation: "buy", ticker: "AAPL", qty: 2.5, price: 1210.5, bucket: "speculative" }],
  ["  bought   1   brk.b   @   450.25   core  ", { operation: "buy", ticker: "BRK.B", qty: 1, price: 450.25, bucket: "core" }],
  ["/buy BF-B 3 80", { operation: "buy", ticker: "BF-B", qty: 3, price: 80, bucket: null }],
  ["/sell AAPL 1.5 225", { operation: "sell", ticker: "AAPL", qty: 1.5, price: 225 }],
  ["sold all nvda at 210", { operation: "sell", ticker: "NVDA", qty: "all", price: 210 }],
  ["sold 2 BF-B @ $81.50", { operation: "sell", ticker: "BF-B", qty: 2, price: 81.5 }],
  ["/stop aapl 195", { operation: "stop", ticker: "AAPL", stop: 195 }],
  ["move brk.b stop to $390.50", { operation: "stop", ticker: "BRK.B", stop: 390.5 }],
  ["/portfolio", { operation: "portfolio" }],
  ["/help", { operation: "help" }],
];

for (const [input, expected] of okCases) {
  test(`parses ${input.trim()}`, () => {
    assert.deepEqual(parsePortfolioCommand(input), { ok: true, command: expected });
  });
}

const rejected = [
  "",
  "/buy AAPL 0 210 growth",
  "/buy AAPL -2 210 growth",
  "/buy AAPL 2 -210 growth",
  "/buy AAPL NaN 210 growth",
  "/buy AAPL 2 Infinity growth",
  "/buy AAPL 2 210 retirement",
  "/buy AAPL 2",
  "/sell AAPL 0 225",
  "/sell AAPL -1 225",
  "/sell AAPL all",
  "/stop AAPL 0",
  "move AAPL stop to -5",
  "AAPL is a buy",
  "/buy AAPL;DROP TABLE holdings 1 210 growth",
  "/sell AAPL 1 225; DROP TABLE holdings",
];

for (const input of rejected) {
  test(`rejects unsupported or unsafe input: ${input || "empty"}`, () => {
    const parsed = parsePortfolioCommand(input);
    assert.equal(parsed.ok, false);
    assert.match(parsed.error, /Try \/buy, \/sell, \/stop, \/portfolio, or \/help\./);
    assert.doesNotMatch(parsed.error, /DROP TABLE|Infinity|NaN/);
  });
}

test("rejects non-string input without throwing", () => {
  assert.equal(parsePortfolioCommand(null).ok, false);
  assert.equal(parsePortfolioCommand({ text: "/portfolio" }).ok, false);
});
