import assert from "node:assert/strict";
import test from "node:test";

import { parsePortfolioCommand } from "../supabase/functions/telegram-portfolio/parser.mjs";

const okCases = [
  ["/start ABCD234567", { operation: "pair", code: "ABCD234567" }],
  ["/relink ABCD234567", { operation: "pair", code: "ABCD234567", confirm_relink: true }],
  ["/status", { operation: "status" }],
  ["/unlink", { operation: "unlink" }],
  ["/buy aapl 2 210 growth", { operation: "buy", ticker: "AAPL", quantity: "2", fill_price: "210", fees: "0", cash_total: null, bucket: "growth" }],
  ["bought 2.5 aapl at $1,210.50 speculative", { operation: "buy", ticker: "AAPL", quantity: "2.5", fill_price: "1210.5", fees: "0", cash_total: null, bucket: "speculative" }],
  ["  bought   1   brk.b   @   450.25   core  ", { operation: "buy", ticker: "BRK.B", quantity: "1", fill_price: "450.25", fees: "0", cash_total: null, bucket: "core" }],
  ["/buy BF-B 3 80", { operation: "buy", ticker: "BF-B", quantity: "3", fill_price: "80", fees: "0", cash_total: null }],
  ["/sell AAPL 1.5 225", { operation: "sell", ticker: "AAPL", quantity: "1.5", fill_price: "225", fees: "0", cash_total: null }],
  ["sold all nvda at 210", { operation: "sell_all", ticker: "NVDA", fill_price: "210", fees: "0", cash_total: null }],
  ["sold 2 BF-B @ $81.50", { operation: "sell", ticker: "BF-B", quantity: "2", fill_price: "81.5", fees: "0", cash_total: null }],
  ["/sell NVDA all 210 on 2026-08-28", { operation: "sell_all", ticker: "NVDA", fill_price: "210", fees: "0", cash_total: null, executed_on: "2026-08-28" }],
  ["bought 2 aapl at 210 growth on 2026-08-28", { operation: "buy", ticker: "AAPL", quantity: "2", fill_price: "210", fees: "0", cash_total: null, bucket: "growth", executed_on: "2026-08-28" }],
  ["/buy AAPL 2 210 on 2026-08-28", { operation: "buy", ticker: "AAPL", quantity: "2", fill_price: "210", fees: "0", cash_total: null, executed_on: "2026-08-28" }],
  ["/stop aapl 195", { operation: "stop", ticker: "AAPL", stop: "195" }],
  ["move brk.b stop to $390.50", { operation: "stop", ticker: "BRK.B", stop: "390.5" }],
  ["/portfolio", { operation: "portfolio" }],
  ["/plan VTI 300 monthly 2026-09-21 core", { operation: "plan", ticker: "VTI", deposit_amount: "300", cadence: "monthly", next_due_on: "2026-09-21", bucket: "core" }],
  ["plan VTI $300 monthly next 2026-09-21 core", { operation: "plan", ticker: "VTI", deposit_amount: "300", cadence: "monthly", next_due_on: "2026-09-21", bucket: "core" }],
  ["/cancelplan VTI", { operation: "cancel_plan", ticker: "VTI" }],
  ["cancel plan VTI", { operation: "cancel_plan", ticker: "VTI" }],
  ["/plans", { operation: "plans" }],
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
  "/buy AAPL 1000000.00000001 210 growth",
  "/buy AAPL 2 1000000.0001 growth",
  "/buy AAPL 2 210 retirement",
  "/buy AAPL 2",
  "/sell AAPL 0 225",
  "/sell AAPL -1 225",
  "/sell AAPL all",
  "/sell AAPL all 225 on 2026-02-30",
  "/sell AAPL all 225 on 08/28/2026",
  "/sell AAPL all 225 on 1999-12-31",
  "/stop AAPL 0",
  "/plan VTI 0 monthly 2026-09-21 core",
  "/plan VTI -300 monthly 2026-09-21 core",
  "/plan VTI NaN monthly 2026-09-21 core",
  "/plan VTI Infinity monthly 2026-09-21 core",
  "/plan VTI 300 weekly 2026-09-21 core",
  "/plan VTI 300 monthly 2026-02-30 core",
  "/plan VTI 300 monthly 1999-12-31 core",
  "/plan VTI 300 monthly 2026-09-21 growth",
  "/cancelplan VTI extra",
  "/start ABCD123456",
  "/start ABCD234567 extra",
  "/unlink now",
  "move AAPL stop to -5",
  "AAPL is a buy",
  "/buy AAPL;DROP TABLE holdings 1 210 growth",
  "/sell AAPL 1 225; DROP TABLE holdings",
];

for (const input of rejected) {
  test(`rejects unsupported or unsafe input: ${input || "empty"}`, () => {
    const parsed = parsePortfolioCommand(input);
    assert.equal(parsed.ok, false);
    assert.match(parsed.error, /Try .*\/buy/);
    assert.doesNotMatch(parsed.error, /DROP TABLE|Infinity|NaN/);
  });
}

test("rejects non-string input without throwing", () => {
  assert.equal(parsePortfolioCommand(null).ok, false);
  assert.equal(parsePortfolioCommand({ text: "/portfolio" }).ok, false);
});

test("plan commands addressed to the bot retain the exact grammar", () => {
  assert.deepEqual(parsePortfolioCommand("/plans@my_portfolio_bot"), {
    ok: true,
    command: { operation: "plans" },
  });
  assert.deepEqual(parsePortfolioCommand("/plan@my_portfolio_bot VTI 300 monthly 2026-09-21 core"), {
    ok: true,
    command: { operation: "plan", ticker: "VTI", deposit_amount: "300", cadence: "monthly", next_due_on: "2026-09-21", bucket: "core" },
  });
});
