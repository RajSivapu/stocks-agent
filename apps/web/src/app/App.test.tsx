import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { DashboardApiError, type DashboardClient } from "../api/client";
import { PASSWORD_RECOVERY_STORAGE_KEY, type AuthClient, type AuthSession } from "../auth/AuthProvider";
import type { ResourceState } from "../api/useDashboardResource";
import { App, bannerState } from "./App";

afterEach(() => window.sessionStorage.clear());

function authClient(initialSession?: AuthSession | null) {
  let authStateChange: ((event: string, session: AuthSession | null) => void) | undefined;
  const signOut = vi.fn().mockResolvedValue({ error: null });
  const ownerSession = {
    access_token: "owner-token",
    user: { id: "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22" },
    expires_at: 1_800_000_000,
  } as AuthSession;
  const session = initialSession === undefined ? ownerSession : initialSession;
  const client: AuthClient = {
    getSession: vi.fn().mockResolvedValue({ data: { session }, error: null }),
    onAuthStateChange: vi.fn((callback) => {
      authStateChange = callback;
      return { data: { subscription: { unsubscribe: vi.fn() } } };
    }),
    signInWithPassword: vi.fn().mockResolvedValue({ data: { session }, error: null }),
    signInWithOtp: vi.fn().mockResolvedValue({ error: null }),
    resetPasswordForEmail: vi.fn().mockResolvedValue({ error: null }),
    updateUser: vi.fn().mockResolvedValue({ error: null }),
    signOut,
  };
  return { client, signOut, ownerSession, emitAuth: (event: string, nextSession: AuthSession | null) => authStateChange?.(event, nextSession) };
}

it("routes a password-recovery session to password setup before loading private data", async () => {
  const auth = authClient(null);
  const dashboard = { get: vi.fn() } as unknown as DashboardClient;
  render(<App authClient={auth.client} dashboardClient={dashboard} />);
  await screen.findByRole("button", { name: /^sign in$/i });

  act(() => auth.emitAuth("PASSWORD_RECOVERY", auth.ownerSession));

  expect(await screen.findByRole("heading", { name: /choose a new password/i })).toBeVisible();
  expect(dashboard.get).not.toHaveBeenCalled();
});

it("keeps a reloaded recovery session out of private routes", async () => {
  window.sessionStorage.setItem(PASSWORD_RECOVERY_STORAGE_KEY, "pending");
  const auth = authClient();
  const dashboard = { get: vi.fn() } as unknown as DashboardClient;
  render(<App authClient={auth.client} dashboardClient={dashboard} />);

  expect(await screen.findByRole("heading", { name: /choose a new password/i })).toBeVisible();
  act(() => auth.emitAuth("SIGNED_IN", auth.ownerSession));
  expect(screen.getByRole("heading", { name: /choose a new password/i })).toBeVisible();
  expect(dashboard.get).not.toHaveBeenCalled();
});

it("clears the authenticated shell and returns to sign-in on a 401", async () => {
  const auth = authClient();
  const dashboard = {
    get: vi.fn().mockRejectedValue(new DashboardApiError(401, "unauthorized", "Your session is invalid or expired.")),
  } as unknown as DashboardClient;
  render(<App authClient={auth.client} dashboardClient={dashboard} />);
  await waitFor(() => expect(auth.signOut).toHaveBeenCalledWith({ scope: "global" }));
  expect(await screen.findByRole("button", { name: /^sign in$/i })).toBeVisible();
  expect(screen.queryByText(/temporarily unavailable/i)).not.toBeInTheDocument();
});

it("shows a bounded owner-only denial and clears private views on a 403", async () => {
  const auth = authClient();
  const dashboard = {
    get: vi.fn().mockRejectedValue(new DashboardApiError(403, "owner_only", "This dashboard is restricted to its owner.")),
  } as unknown as DashboardClient;
  render(<App authClient={auth.client} dashboardClient={dashboard} />);
  expect(await screen.findByRole("heading", { name: /owner-only access/i })).toBeVisible();
  expect(screen.getByText(/restricted to its owner/i)).toBeVisible();
  expect(screen.queryByText(/portfolio at a glance/i)).not.toBeInTheDocument();
});

it("keeps a combined view loading until every displayed child receipt settles", () => {
  const ready = {
    status: "ready",
    envelope: {
      contract_version: 1,
      request_id: "request",
      generated_at: "2026-09-06T18:00:00.000Z",
      data_as_of: "2026-09-06T17:59:00.000Z",
      freshness: "fresh",
      market_state: "regular",
      data: {},
    },
    error: null,
  } satisfies ResourceState<unknown>;
  const loading = { status: "loading", envelope: null, error: null } satisfies ResourceState<unknown>;

  expect(bannerState(ready, [ready, ready, loading])).toMatchObject({ viewStatus: "loading" });
});
