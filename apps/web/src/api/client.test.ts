import { expect, it, vi } from "vitest";

import { createDashboardClient } from "./client";

const envelope = {
  contract_version: 1,
  request_id: "7d834dbd-75bb-4313-931f-09732f003932",
  generated_at: "2026-09-03T18:00:00.000Z",
  data_as_of: null,
  freshness: "unavailable",
  market_state: "unknown",
  data: {},
};

it("sends only GET with bearer auth and no-store caching", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(envelope), {
    status: 200,
    headers: { "content-type": "application/json" },
  }));
  const client = createDashboardClient("https://test-project.supabase.co/functions/v1/owner-dashboard-api", fetcher);
  await expect(client.get("/v1/meta", "owner-token")).resolves.toMatchObject(envelope);
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe("https://test-project.supabase.co/functions/v1/owner-dashboard-api/v1/meta");
  expect(init).toMatchObject({ method: "GET", cache: "no-store" });
  expect(new Headers(init.headers).get("authorization")).toBe("Bearer owner-token");
});

it("rejects unknown contracts and non-allowlisted paths", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ ...envelope, contract_version: 2 }));
  const client = createDashboardClient("https://test-project.supabase.co/functions/v1/owner-dashboard-api", fetcher);
  await expect(client.get("/v1/meta", "owner-token")).rejects.toThrow(/contract_version/i);
  await expect(client.get("https://attacker.example", "owner-token")).rejects.toThrow(/path/i);
});

it("does not include credentials and aborts at the configured timeout", async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  }));
  const client = createDashboardClient("https://test-project.supabase.co/functions/v1/owner-dashboard-api", fetcher, 50);
  const pending = client.get("/v1/today", "owner-token");
  const rejected = expect(pending).rejects.toThrow(/timed out/i);
  await vi.advanceTimersByTimeAsync(51);
  await rejected;
  expect(fetcher.mock.calls[0][1].credentials).toBe("omit");
  vi.useRealTimers();
});
