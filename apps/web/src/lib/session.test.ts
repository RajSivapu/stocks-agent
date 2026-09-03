import type { SupabaseClient } from "@supabase/supabase-js";
import { describe, expect, it, vi } from "vitest";
import {
  consumeAuthCallback,
  createSessionService,
  loadVerifiedViewer,
} from "./session";

function client(overrides: Record<string, unknown> = {}): SupabaseClient {
  return overrides as unknown as SupabaseClient;
}

describe("session verification", () => {
  it.each(["expired", "revoked"])("rejects a %s local session before profile reads", async () => {
    const profile = vi.fn();
    const signOut = vi.fn().mockResolvedValue({ error: null });
    const value = client({
      auth: {
        getUser: vi.fn().mockResolvedValue({ data: { user: null }, error: new Error("invalid") }),
        signOut,
      },
      schema: profile,
    });

    await expect(loadVerifiedViewer(value)).resolves.toEqual({
      kind: "signed-out",
      reason: "SESSION_REVOKED",
    });
    expect(signOut).toHaveBeenCalledWith({ scope: "local" });
    expect(profile).not.toHaveBeenCalled();
  });

  it("loads profile and current consent only after getUser confirms the session", async () => {
    const events: string[] = [];
    const query = (table: string) => ({
      select: () => table === "profile"
        ? {
          maybeSingle: () => {
            events.push("profile");
            return Promise.resolve({ data: { display_name: "Raj", status: "active" }, error: null });
          },
        }
        : {
          order: () => {
            events.push("consents");
            return Promise.resolve({
              data: [{ document_version: "provider-data-v1", accepted_at: "2026-09-03T00:00:00Z" }],
              error: null,
            });
          },
        },
    });
    const value = client({
      auth: {
        getUser: () => {
          events.push("verified");
          return Promise.resolve({ data: { user: { id: "owner", email: "raj@example.com" } }, error: null });
        },
      },
      schema: () => ({ from: query }),
    });

    await expect(loadVerifiedViewer(value)).resolves.toMatchObject({ kind: "ready", userId: "owner" });
    expect(events).toEqual(["verified", "profile", "consents"]);
  });

  it("routes a deletion-pending identity to the restricted lifecycle gate", async () => {
    const value = client({
      auth: {
        getUser: vi.fn().mockResolvedValue({
          data: { user: { id: "owner", email: "owner@example.com" } }, error: null,
        }),
      },
      schema: () => ({
        from: (table: string) => ({
          select: () => table === "profile"
            ? { maybeSingle: () => Promise.resolve({
              data: { display_name: "Owner", status: "deletion_pending" }, error: null,
            }) }
            : { order: () => Promise.resolve({ data: [], error: null }) },
        }),
      }),
    });

    await expect(loadVerifiedViewer(value)).resolves.toMatchObject({
      kind: "deletion-pending", userId: "owner", email: "owner@example.com",
    });
  });

  it("clears auth parameters before exchanging a captured PKCE code and logs no token", async () => {
    const events: string[] = [];
    const replaceState = vi.fn(() => events.push("cleared"));
    const exchangeCodeForSession = vi.fn((code: string) => {
      events.push(`exchange:${code}`);
      return Promise.resolve({ data: {}, error: null });
    });
    const logger = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const value = client({ auth: { exchangeCodeForSession } });
    const location = new URL(
      "https://stocks.example/auth/callback?code=private-code&access_token=never#refresh_token=never",
    );

    await consumeAuthCallback(value, location, { replaceState });

    expect(events).toEqual(["cleared", "exchange:private-code"]);
    expect(replaceState).toHaveBeenCalledWith({}, "", "/");
    expect(logger).not.toHaveBeenCalled();
  });

  it("requests invite-only OTP and desktop fallback with PKCE redirect", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
    const verifyOtp = vi.fn().mockResolvedValue({ error: null });
    const value = client({
      auth: {
        signInWithOtp,
        verifyOtp,
        onAuthStateChange: () => ({ data: { subscription: { unsubscribe: vi.fn() } } }),
        signOut: vi.fn().mockResolvedValue({ error: null }),
      },
    });
    const service = createSessionService(value, "https://stocks.example");

    await service.requestOtp("friend@example.com");
    await service.requestDesktopLink("friend@example.com", "https://stocks.example/auth/callback");
    await service.verifyOtp("friend@example.com", "123456");

    expect(signInWithOtp).toHaveBeenNthCalledWith(1, {
      email: "friend@example.com",
      options: { shouldCreateUser: false },
    });
    expect(signInWithOtp).toHaveBeenNthCalledWith(2, {
      email: "friend@example.com",
      options: {
        shouldCreateUser: false,
        emailRedirectTo: "https://stocks.example/auth/callback",
      },
    });
    expect(verifyOtp).toHaveBeenCalledWith({
      email: "friend@example.com",
      token: "123456",
      type: "email",
    });
  });

  it("defers auth listeners until Supabase releases its auth callback lock", async () => {
    vi.useFakeTimers();
    const listener = vi.fn();
    const unsubscribe = vi.fn();
    let authCallback: (() => void) | undefined;
    const value = client({
      auth: {
        onAuthStateChange: (callback: () => void) => {
          authCallback = callback;
          return { data: { subscription: { unsubscribe } } };
        },
      },
    });
    const service = createSessionService(value, "https://stocks.example");

    const stop = service.subscribe(listener);
    authCallback?.();

    expect(listener).not.toHaveBeenCalled();
    await vi.runAllTimersAsync();
    expect(listener).toHaveBeenCalledOnce();
    stop();
    expect(unsubscribe).toHaveBeenCalledOnce();
    vi.useRealTimers();
  });
});
