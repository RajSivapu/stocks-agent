import { clientNetworkSignal, createDashboardRateLimiter } from "./rate-limit.ts";

function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

Deno.test("rate limiter enforces independent bounded windows", () => {
  const limiter = createDashboardRateLimiter({ pre_auth: 2, owner: 3 }, 60_000);
  const now = new Date("2026-09-03T18:00:00.000Z");
  assertEquals(limiter.check("pre_auth", "one", now), null);
  assertEquals(limiter.check("pre_auth", "one", now), null);
  assertEquals(limiter.check("pre_auth", "one", now), 60);
  assertEquals(limiter.check("owner", "one", now), null);
  assertEquals(limiter.check("pre_auth", "one", new Date(now.valueOf() + 60_000)), null);
});

Deno.test("network signal accepts only bounded address-shaped infrastructure headers", () => {
  assertEquals(clientNetworkSignal(new Request("https://api.example", { headers: { "cf-connecting-ip": "203.0.113.8" } })), "203.0.113.8");
  assertEquals(clientNetworkSignal(new Request("https://api.example", { headers: { "x-forwarded-for": "2001:db8::1, 10.0.0.1" } })), "10.0.0.1");
  assertEquals(clientNetworkSignal(new Request("https://api.example", { headers: { "x-forwarded-for": "attacker-controlled" } })), "unknown");
});

Deno.test("rate limiter caps cardinality and evicts expired windows", () => {
  const limiter = createDashboardRateLimiter({ pre_auth: 2, owner: 3 }, 60_000, 2);
  const now = new Date("2026-09-03T18:00:00.000Z");
  assertEquals(limiter.check("pre_auth", "one", now), null);
  assertEquals(limiter.check("pre_auth", "two", now), null);
  assertEquals(limiter.check("pre_auth", "three", now), 60);
  assertEquals(limiter.check("pre_auth", "three", new Date(now.valueOf() + 60_000)), null);
});
