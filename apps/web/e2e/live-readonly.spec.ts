import { expect, test } from "@playwright/test";

test("optional production canary uses GET only", async ({ request }) => {
  test.skip(process.env.E2E_LIVE !== "1", "live read-only canary is explicitly opt-in");
  const api = process.env.E2E_DASHBOARD_API_URL;
  const token = process.env.E2E_OWNER_ACCESS_TOKEN;
  expect(api).toMatch(/^https:\/\/[a-z0-9.-]+\/functions\/v1\/owner-dashboard-api$/);
  expect(token?.length).toBeGreaterThan(100);
  for (const path of ["/v1/meta", "/v1/today", "/v1/portfolio", "/v1/companion", "/v1/alerts", "/v1/runs", "/v1/system"]) {
    const response = await request.get(`${api}${path}`, {
      headers: { authorization: `Bearer ${token}`, origin: process.env.E2E_DASHBOARD_ORIGIN ?? "" },
    });
    expect(response.ok()).toBeTruthy();
    expect((await response.json()).contract_version).toBe(1);
  }
});
