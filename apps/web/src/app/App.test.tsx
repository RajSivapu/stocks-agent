import { render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { DashboardApiError, type DashboardClient } from "../api/client";
import type { AuthClient } from "../auth/AuthProvider";
import { App } from "./App";

function authClient() {
  const signOut = vi.fn().mockResolvedValue({ error: null });
  const session = {
    access_token: "owner-token",
    user: { id: "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22" },
    expires_at: 1_800_000_000,
  };
  const client: AuthClient = {
    getSession: vi.fn().mockResolvedValue({ data: { session }, error: null }),
    onAuthStateChange: vi.fn(() => ({ data: { subscription: { unsubscribe: vi.fn() } } })),
    signInWithOtp: vi.fn().mockResolvedValue({ error: null }),
    verifyOtp: vi.fn().mockResolvedValue({ data: { session }, error: null }),
    signOut,
  };
  return { client, signOut };
}

it("clears the authenticated shell and returns to sign-in on a 401", async () => {
  const auth = authClient();
  const dashboard = {
    get: vi.fn().mockRejectedValue(new DashboardApiError(401, "unauthorized", "Your session is invalid or expired.")),
  } as unknown as DashboardClient;
  render(<App authClient={auth.client} dashboardClient={dashboard} />);
  await waitFor(() => expect(auth.signOut).toHaveBeenCalledWith({ scope: "global" }));
  expect(await screen.findByRole("button", { name: /send code/i })).toBeVisible();
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
