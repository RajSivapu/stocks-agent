import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { BROWSER_AUTH_FLOW, AuthProvider, useAuth, type AuthClient, type AuthSession } from "./AuthProvider";
import { ResetPasswordPage } from "./ResetPasswordPage";
import { SignInPage } from "./SignInPage";

const ownerSession = {
  access_token: "owner-token",
  user: { id: "owner-id", email: "owner@example.com" },
} as AuthSession;

function client(session: AuthSession | null = null): AuthClient & {
  signInWithPassword: ReturnType<typeof vi.fn>;
  signInWithOtp: ReturnType<typeof vi.fn>;
  resetPasswordForEmail: ReturnType<typeof vi.fn>;
  updateUser: ReturnType<typeof vi.fn>;
  signOut: ReturnType<typeof vi.fn>;
  emitAuth(event: string, nextSession: AuthSession | null): void;
} {
  let authStateChange: ((event: string, nextSession: AuthSession | null) => void) | undefined;
  const signInWithPassword = vi.fn().mockResolvedValue({ data: { session }, error: null });
  const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
  const resetPasswordForEmail = vi.fn().mockResolvedValue({ error: null });
  const updateUser = vi.fn().mockResolvedValue({ error: null });
  const signOut = vi.fn().mockResolvedValue({ error: null });
  return {
    signInWithPassword,
    signInWithOtp,
    resetPasswordForEmail,
    updateUser,
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
  if (auth.recovering && auth.session) return <ResetPasswordPage />;
  return auth.session && !auth.locked
    ? <button onClick={() => void auth.signOut()}>Sign out</button>
    : <SignInPage />;
}

it("enables signed email callbacks without changing session-only storage", () => {
  expect(BROWSER_AUTH_FLOW).toEqual({
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
    flowType: "implicit",
  });
});

it("uses email and password as the primary sign-in", async () => {
  const authClient = client();
  authClient.signInWithPassword.mockResolvedValue({ data: { session: ownerSession }, error: null });
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);

  await user.type(await screen.findByLabelText(/^email$/i), " owner@example.com ");
  await user.type(screen.getByLabelText(/^password$/i), "owner-password");
  await user.click(screen.getByRole("button", { name: /^sign in$/i }));

  expect(authClient.signInWithPassword).toHaveBeenCalledWith({
    email: "owner@example.com",
    password: "owner-password",
  });
  expect(await screen.findByRole("button", { name: /sign out/i })).toBeVisible();
});

it("shows a generic error when password sign-in fails", async () => {
  const authClient = client();
  authClient.signInWithPassword.mockResolvedValue({ data: { session: null }, error: new Error("user not found") });
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);

  await user.type(await screen.findByLabelText(/^email$/i), "unknown@example.com");
  await user.type(screen.getByLabelText(/^password$/i), "wrong-password");
  await user.click(screen.getByRole("button", { name: /^sign in$/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect");
  expect(screen.queryByText(/user not found/i)).not.toBeInTheDocument();
});

it("accepts a signed email-link session from the auth state callback", async () => {
  const authClient = client();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await screen.findByRole("button", { name: /^sign in$/i });

  act(() => authClient.emitAuth("SIGNED_IN", ownerSession));

  expect(await screen.findByRole("button", { name: /sign out/i })).toBeVisible();
});

it("requests a secure sign-in link with account creation disabled", async () => {
  const authClient = client();
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await user.click(await screen.findByRole("button", { name: /use an email sign-in link/i }));
  await user.type(screen.getByLabelText(/^email$/i), "owner@example.com");
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
  await user.click(await screen.findByRole("button", { name: /use an email sign-in link/i }));
  await user.type(screen.getByLabelText(/^email$/i), "unknown@example.com");
  await user.click(screen.getByRole("button", { name: /send secure sign-in link/i }));
  expect(await screen.findByText(/if this is the owner account, look for a secure sign-in link from supabase/i)).toBeVisible();
  expect(screen.queryByText(/user not found|could not be sent/i)).not.toBeInTheDocument();
});

it("requests a password setup link without revealing whether the email exists", async () => {
  const authClient = client();
  authClient.resetPasswordForEmail.mockResolvedValue({ error: new Error("user not found") });
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await user.click(await screen.findByRole("button", { name: /set up or reset password/i }));
  await user.type(screen.getByLabelText(/^email$/i), "unknown@example.com");
  await user.click(screen.getByRole("button", { name: /email password setup link/i }));

  expect(authClient.resetPasswordForEmail).toHaveBeenCalledWith("unknown@example.com", {
    redirectTo: window.location.origin,
  });
  expect(await screen.findByText(/if this is the owner account, look for a password setup link from supabase/i)).toBeVisible();
  expect(screen.queryByText(/user not found|could not be sent/i)).not.toBeInTheDocument();
});

it("validates a new password before updating the recovery session", async () => {
  const authClient = client();
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await screen.findByRole("button", { name: /^sign in$/i });
  act(() => authClient.emitAuth("PASSWORD_RECOVERY", ownerSession));

  expect(await screen.findByRole("heading", { name: /choose a new password/i })).toBeVisible();
  await user.type(screen.getByLabelText(/^new password$/i), "too-short");
  await user.type(screen.getByLabelText(/confirm new password/i), "different-password");
  await user.click(screen.getByRole("button", { name: /save password/i }));

  expect(screen.getByRole("alert")).toHaveTextContent(/at least 12 characters/i);
  expect(authClient.updateUser).not.toHaveBeenCalled();
});

it("updates the password and continues into the owner workspace", async () => {
  const authClient = client();
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await screen.findByRole("button", { name: /^sign in$/i });
  act(() => authClient.emitAuth("PASSWORD_RECOVERY", ownerSession));

  await user.type(await screen.findByLabelText(/^new password$/i), "a-secure-owner-password");
  await user.type(screen.getByLabelText(/confirm new password/i), "a-secure-owner-password");
  await user.click(screen.getByRole("button", { name: /save password/i }));

  expect(authClient.updateUser).toHaveBeenCalledWith({ password: "a-secure-owner-password" });
  expect(await screen.findByRole("button", { name: /sign out/i })).toBeVisible();
});

it("signs out globally and returns to password sign-in", async () => {
  const authClient = client(ownerSession);
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await screen.findByRole("button", { name: /sign out/i });
  await user.click(screen.getByRole("button", { name: /sign out/i }));
  await waitFor(() => expect(authClient.signOut).toHaveBeenCalledWith({ scope: "global" }));
  expect(screen.getByRole("button", { name: /^sign in$/i })).toBeVisible();
});

it("does not persist financial data when authentication is absent", async () => {
  const authClient = client();
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  expect(await screen.findByRole("button", { name: /^sign in$/i })).toBeVisible();
  expect(fetchSpy).not.toHaveBeenCalled();
  expect(window.localStorage).toHaveLength(0);
});

it("does not extend the privacy deadline when the auth token refreshes", async () => {
  vi.useFakeTimers();
  try {
    const refreshedSession = { ...ownerSession, access_token: "refreshed-owner-token" };
    const authClient = client(ownerSession);
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
  await user.click(await screen.findByRole("button", { name: /use an email sign-in link/i }));
  await user.type(screen.getByLabelText(/^email$/i), "owner@example.com");
  await user.click(screen.getByRole("button", { name: /send secure sign-in link/i }));
  expect(await screen.findByLabelText(/^email$/i)).toBeDisabled();

  await user.click(screen.getByRole("button", { name: /use a different email/i }));

  expect(screen.getByLabelText(/^email$/i)).toBeEnabled();
  expect(screen.getByRole("button", { name: /send secure sign-in link/i })).toBeVisible();
  expect(screen.queryByText(/look for a secure sign-in link from supabase/i)).not.toBeInTheDocument();
});
