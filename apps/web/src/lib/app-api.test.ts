import type { SupabaseClient } from "@supabase/supabase-js";
import { describe, expect, it, vi } from "vitest";
import type { CommandInput } from "@stocks-agent/contracts";
import { createCommandClient } from "./app-api";

const command: CommandInput = {
  operation: "buy",
  ticker: "CENX",
  quantity: "1",
  fill_price: "47.5",
  fees: "1",
  cash_total: "48.50",
  executed_on: "2026-09-03",
  bucket: "unclassified",
};

function client(token: string | null = "valid.jwt.value"): SupabaseClient {
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({
        data: { session: token ? { access_token: token } : null },
        error: null,
      }),
    },
  } as unknown as SupabaseClient;
}

function response(data: Record<string, unknown>, status = 200): Response {
  return new Response(JSON.stringify(status < 400
    ? { ok: true, data }
    : { ok: false, error: data }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("browser app API", () => {
  it("sends a bearer-only no-store preview with no owner authority in URL or body", async () => {
    const fetcher = vi.fn().mockResolvedValue(response({
      command_id: "55555555-5555-4555-8555-555555555555",
      status: "previewed",
      preview_digest: "a".repeat(64),
      expires_at: "2026-09-03T16:00:00Z",
      operation: "buy",
      before: {},
      after: {},
      warnings: [],
    }));
    const api = createCommandClient(
      client(),
      "https://test-project.supabase.co",
      () => Promise.resolve(null),
      fetcher,
    );

    await api.preview(command);

    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, rawOptions] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://test-project.supabase.co/functions/v1/app-api/portfolio/preview");
    expect(new URL(url).search).toBe("");
    expect(rawOptions.cache).toBe("no-store");
    expect(rawOptions.credentials).toBe("omit");
    expect(rawOptions.referrerPolicy).toBe("no-referrer");
    expect(rawOptions.headers).toMatchObject({ authorization: "Bearer valid.jwt.value" });
    if (typeof rawOptions.body !== "string") throw new Error("expected a JSON string body");
    const body = JSON.parse(rawOptions.body) as Record<string, unknown>;
    expect(body).toHaveProperty("command");
    expect(body).not.toHaveProperty("owner_id");
    expect(JSON.stringify(body)).not.toContain("raj@example.com");
  });

  it.each([
    ["correct_transaction", "/portfolio/correction/confirm"],
    ["plan", "/plans/confirm"],
    ["sell", "/portfolio/confirm"],
  ] as const)("uses the reviewed confirmation route for %s", async (operation, suffix) => {
    const fetcher = vi.fn().mockResolvedValue(response({
      command_id: "55555555-5555-4555-8555-555555555555",
      status: "applied",
      result: {},
    }));
    const api = createCommandClient(client(), "https://test-project.supabase.co", () => Promise.resolve(null), fetcher);

    await api.confirm("55555555-5555-4555-8555-555555555555", "a".repeat(64), operation);

    expect(fetcher.mock.calls[0]?.[0]).toBe(`https://test-project.supabase.co/functions/v1/app-api${suffix}`);
  });

  it("fails closed before fetch without a current session", async () => {
    const fetcher = vi.fn();
    const api = createCommandClient(client(null), "https://test-project.supabase.co", () => Promise.resolve(null), fetcher);

    await expect(api.preview(command)).rejects.toHaveProperty("code", "SESSION_UNAVAILABLE");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects oversized and malformed success bodies", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("x".repeat(65 * 1024), { status: 200 }));
    const api = createCommandClient(client(), "https://test-project.supabase.co", () => Promise.resolve(null), fetcher);

    await expect(api.preview(command)).rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });

  it("queues an on-demand run with an empty authority-free body", async () => {
    const fetcher = vi.fn().mockResolvedValue(response({
      status: "queued",
      slot_id: "55555555-5555-4555-8555-555555555555",
      phase: "on-demand",
      market_date: "2026-09-03",
      expected_by: "2026-09-03T16:20:00Z",
      telegram: "suppressed",
    }));
    const api = createCommandClient(client(), "https://test-project.supabase.co", () => Promise.resolve(null), fetcher);

    const receipt = await api.requestRun();

    expect(receipt.telegram).toBe("suppressed");
    const [url, rawOptions] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://test-project.supabase.co/functions/v1/app-api/runs/on-demand");
    expect(rawOptions.body).toBe("{}");
    expect(JSON.stringify(rawOptions)).not.toContain("owner_id");
  });

  it("parses one-time connection and pairing credentials without adding owner authority", async () => {
    const publicId = "22222222-2222-4222-8222-222222222222";
    const credential = `${publicId}.${"A".repeat(43)}`;
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response({
        connection_id: "11111111-1111-4111-8111-111111111111",
        public_id: publicId, provider: "claude", status: "disabled", contract_version: 2,
        gateway_credential: credential, credential_display: "once",
      }))
      .mockResolvedValueOnce(response({
        pairing_id: "33333333-3333-4333-8333-333333333333", status: "issued",
        code: "ABCD234567", expires_at: "2026-09-03T16:20:00Z",
      }));
    const api = createCommandClient(client(), "https://test-project.supabase.co", () => Promise.resolve(null), fetcher);

    const connection = await api.createConnection("provider-data-v1");
    const pairing = await api.requestPairingCode();

    expect(connection.gatewayCredential).toBe(credential);
    expect(connection.gatewayUrl).toBe("https://test-project.supabase.co/functions/v1/agent-gateway");
    expect(pairing.code).toBe("ABCD234567");
    const createOptions = fetcher.mock.calls[0]?.[1] as RequestInit;
    const pairingOptions = fetcher.mock.calls[1]?.[1] as RequestInit;
    expect(JSON.stringify(createOptions.body)).not.toContain("owner_id");
    expect(pairingOptions.body).toBe("{}");
  });

  it("uses exact lifecycle routes and PATCH settings without schedule expressions", async () => {
    const connectionId = "11111111-1111-4111-8111-111111111111";
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response({
        connection_id: connectionId, status: "testing",
        handshake_id: "22222222-2222-4222-8222-222222222222",
        trigger_request_id: "33333333-3333-4333-8333-333333333333", duplicate: false,
      }))
      .mockResolvedValueOnce(response({ status: "updated" }))
      .mockResolvedValueOnce(response({ status: "unlinked" }));
    const api = createCommandClient(client(), "https://test-project.supabase.co", () => Promise.resolve(null), fetcher);

    await api.beginConnectionHandshake(
      connectionId,
      "https://api.anthropic.com/v1/claude_code/routines/trig_ABC123/fire",
      "t".repeat(32),
    );
    await api.updateSettings({
      display_name: "Raj", timezone: "America/Chicago",
      notify_pre_market: true, notify_intraday: true, notify_post_market: true,
      notify_operational: true, schedule_pre_market: true,
      schedule_intraday: true, schedule_post_market: true,
    });
    await api.unlinkTelegram();

    const fetchCalls = fetcher.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(fetchCalls.map(([url]) => url)).toEqual([
      "https://test-project.supabase.co/functions/v1/app-api/connections/handshake",
      "https://test-project.supabase.co/functions/v1/app-api/settings",
      "https://test-project.supabase.co/functions/v1/app-api/telegram/unlink",
    ]);
    expect(fetchCalls[1]?.[1].method).toBe("PATCH");
    expect(JSON.stringify(fetchCalls[1]?.[1].body)).not.toContain("cron");
  });
});
