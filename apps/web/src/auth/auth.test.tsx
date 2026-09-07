import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { BROWSER_AUTH_FLOW, AuthProvider, useAuth, type AuthClient } from "./AuthProvider";
import { SignInPage } from "./SignInPage";

function client(session: unknown = null): AuthClient & {
  signInWithOtp: ReturnType<typeof vi.fn>;
  verifyOtp: ReturnType<typeof vi.fn>;
  signOut: ReturnType<typeof vi.fn>;
  emitAuth(event: string, nextSession: unknown): void;
} {
  let authStateChange: ((event: string, nextSession: unknown) => void) | undefined;
  const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
  const verifyOtp = vi.fn().mockResolvedValue({ data: { session }, error: null });
  const signOut = vi.fn().mockResolvedValue({ error: null });
  return {
    signInWithOtp,
    verifyOtp,
    signOut,
    getSession: vi.fn().mockResolvedValue({ data: { session }, error: null }),
    onAuthStateChange: vi.fn((callback) => {
      authStateChange = callback;
      return { data: { subscription: { unsubscribe: vi.fn() } } };
    }),
    emitAuth: (event, nextSession) => authStateChange?.(event, nextSession),
  };
}

function Screen() {
  const auth = useAuth();
  return auth.session && !auth.locked
    ? <button onClick={() => void auth.signOut()}>Sign out</button>
    : <SignInPage />;
}

it("enables the signed email-link callback without changing session-only storage", () => {
  expect(BROWSER_AUTH_FLOW).toEqual({
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
    flowType: "implicit",
  });
});

it("accepts a signed email-link session from the auth state callback", async () => {
  const authClient = client();
  const linkSession = { access_token: "owner-link-token", user: { id: "owner-id" } };
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await screen.findByRole("button", { name: /send secure sign-in link/i });

  act(() => authClient.emitAuth("SIGNED_IN", linkSession));

  expect(await screen.findByRole("button", { name: /sign out/i })).toBeVisible();
});

it("requests a secure sign-in link with account creation disabled", async () => {
  const authClient = client();
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await user.type(screen.getByLabelText(/email/i), "owner@example.com");
  await user.click(screen.getByRole("button", { name: /send secure sign-in link/i }));
  expect(authClient.signInWithOtp).toHaveBeenCalledWith({
    email: "owner@example.com",
    options: {
      shouldCreateUser: false,
      emailRedirectTo: window.location.origin,
    },
  });
  expect(await screen.findByText(/if this is the owner account, look for a secure sign-in link from supabase/i)).toBeVisible();
  expect(screen.queryByLabelText(/six-digit code/i)).not.toBeInTheDocument();
});

it("uses the same neutral link-sent state when the request fails", async () => {
  const authClient = client();
  authClient.signInWithOtp.mockResolvedValue({ error: new Error("user not found") });
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await user.type(screen.getByLabelText(/email/i), "unknown@example.com");
  await user.click(screen.getByRole("button", { name: /send secure sign-in link/i }));
  expect(await screen.findByText(/if this is the owner account, look for a secure sign-in link from supabase/i)).toBeVisible();
  expect(screen.queryByLabelText(/six-digit code/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/user not found|could not be sent/i)).not.toBeInTheDocument();
});

it("signs out globally and returns to the link request", async () => {
  const session = { access_token: "owner-token", user: { id: "owner-id" } };
  const authClient = client(session);
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await screen.findByRole("button", { name: /sign out/i });
  await user.click(screen.getByRole("button", { name: /sign out/i }));
  await waitFor(() => expect(authClient.signOut).toHaveBeenCalledWith({ scope: "global" }));
  expect(screen.getByRole("button", { name: /send secure sign-in link/i })).toBeVisible();
});

it("does not persist financial data when authentication is absent", async () => {
  const authClient = client();
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  expect(await screen.findByRole("button", { name: /send secure sign-in link/i })).toBeVisible();
  expect(fetchSpy).not.toHaveBeenCalled();
  expect(window.localStorage).toHaveLength(0);
});

it("does not extend the privacy deadline when the auth token refreshes", async () => {
  vi.useFakeTimers();
  try {
    const initialSession = { access_token: "owner-token", user: { id: "owner-id" } };
    const refreshedSession = { access_token: "refreshed-owner-token", user: { id: "owner-id" } };
    const authClient = client(initialSession);
    render(<AuthProvider client={authClient} inactivityMs={30 * 60_000}><Screen /></AuthProvider>);
    await act(async () => { await Promise.resolve(); });

    await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60_000); });
    await act(async () => { authClient.emitAuth("TOKEN_REFRESHED", refreshedSession); });
    await act(async () => { await vi.advanceTimersByTimeAsync(20 * 60_000); });

    expect(screen.getByText(/privacy lock activated/i)).toBeVisible();
  } finally {
    vi.useRealTimers();
  }
});

it("lets the owner correct the email after requesting a link", async () => {
  const authClient = client();
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await user.type(screen.getByLabelText(/email/i), "owner@example.com");
  await user.click(screen.getByRole("button", { name: /send secure sign-in link/i }));
  expect(await screen.findByLabelText(/email/i)).toBeDisabled();

  await user.click(screen.getByRole("button", { name: /use a different email/i }));

  expect(screen.getByLabelText(/email/i)).toBeEnabled();
  expect(screen.getByRole("button", { name: /send secure sign-in link/i })).toBeVisible();
  expect(screen.queryByText(/look for a secure sign-in link from supabase/i)).not.toBeInTheDocument();
});
