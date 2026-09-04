import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { AuthProvider, useAuth, type AuthClient } from "./AuthProvider";
import { SignInPage } from "./SignInPage";

function client(session: unknown = null): AuthClient & {
  signInWithOtp: ReturnType<typeof vi.fn>;
  verifyOtp: ReturnType<typeof vi.fn>;
  signOut: ReturnType<typeof vi.fn>;
} {
  const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
  const verifyOtp = vi.fn().mockResolvedValue({ data: { session }, error: null });
  const signOut = vi.fn().mockResolvedValue({ error: null });
  return {
    signInWithOtp,
    verifyOtp,
    signOut,
    getSession: vi.fn().mockResolvedValue({ data: { session }, error: null }),
    onAuthStateChange: vi.fn(() => ({ data: { subscription: { unsubscribe: vi.fn() } } })),
  };
}

function Screen() {
  const auth = useAuth();
  return auth.session && !auth.locked
    ? <button onClick={() => void auth.signOut()}>Sign out</button>
    : <SignInPage />;
}

it("requests OTP with account creation disabled", async () => {
  const authClient = client();
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await user.type(screen.getByLabelText(/email/i), "owner@example.com");
  await user.click(screen.getByRole("button", { name: /send code/i }));
  expect(authClient.signInWithOtp).toHaveBeenCalledWith({
    email: "owner@example.com",
    options: { shouldCreateUser: false },
  });
  expect(await screen.findByLabelText(/six-digit code/i)).toBeVisible();
});

it("uses the same neutral code step when the OTP request fails", async () => {
  const authClient = client();
  authClient.signInWithOtp.mockResolvedValue({ error: new Error("user not found") });
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await user.type(screen.getByLabelText(/email/i), "unknown@example.com");
  await user.click(screen.getByRole("button", { name: /send code/i }));
  expect(await screen.findByLabelText(/six-digit code/i)).toBeVisible();
  expect(screen.getByText(/if this is the owner account/i)).toBeVisible();
  expect(screen.queryByText(/user not found|could not be sent/i)).not.toBeInTheDocument();
});

it("verifies the emailed code and signs out globally", async () => {
  const session = { access_token: "owner-token", user: { id: "owner-id" } };
  const authClient = client(session);
  const user = userEvent.setup();
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  await screen.findByRole("button", { name: /sign out/i });
  await user.click(screen.getByRole("button", { name: /sign out/i }));
  await waitFor(() => expect(authClient.signOut).toHaveBeenCalledWith({ scope: "global" }));
  expect(screen.getByRole("button", { name: /send code/i })).toBeVisible();
});

it("does not persist financial data when authentication is absent", async () => {
  const authClient = client();
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  render(<AuthProvider client={authClient}><Screen /></AuthProvider>);
  expect(await screen.findByRole("button", { name: /send code/i })).toBeVisible();
  expect(fetchSpy).not.toHaveBeenCalled();
  expect(window.localStorage).toHaveLength(0);
});
