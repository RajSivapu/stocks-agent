import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./app";
import type { AppApiClient } from "./lib/app-api";
import type { DashboardRepository } from "./lib/dashboard";
import type { SessionService, ViewerState } from "./lib/session";

class FakeSession implements SessionService {
  viewer: ViewerState = { kind: "signed-out" };
  requested: string[] = [];
  desktopLinks: Array<{ email: string; redirectTo: string }> = [];
  verified: Array<{ email: string; token: string }> = [];
  listeners = new Set<() => void>();

  loadViewer() {
    return Promise.resolve(this.viewer);
  }

  requestOtp(email: string) {
    this.requested.push(email);
    return Promise.resolve();
  }

  requestDesktopLink(email: string, redirectTo: string) {
    this.desktopLinks.push({ email, redirectTo });
    return Promise.resolve();
  }

  verifyOtp(email: string, token: string) {
    this.verified.push({ email, token });
    this.viewer = {
      kind: "ready",
      userId: "11111111-1111-4111-8111-111111111111",
      email,
      displayName: "Raj",
    };
    return Promise.resolve();
  }

  signOut() {
    this.viewer = { kind: "signed-out" };
    return Promise.resolve();
  }

  subscribe(listener: () => void) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}

const repository: DashboardRepository = {
  loadToday: () => Promise.resolve({ holdings: [], quotes: [], plans: [], runs: [], recommendations: [] }),
  loadPortfolio: () => Promise.resolve({ holdings: [], quotes: [], plans: [] }),
  loadActivity: () => Promise.resolve({ transactions: [], plans: [], commands: [] }),
  loadResearch: () => Promise.resolve({ items: [] }),
  loadRuns: () => Promise.resolve({ runs: [] }),
  lookupCommand: () => Promise.resolve(null),
};

const commands: AppApiClient = {
  preview: () => Promise.reject(new Error("not used")),
  confirm: () => Promise.reject(new Error("not used")),
  lookup: () => Promise.resolve(null),
  requestRun: () => Promise.reject(new Error("not used")),
};

function app(session: SessionService) {
  return <App session={session} repository={repository} commands={commands} />;
}

beforeEach(() => {
  window.history.replaceState({}, "", "/");
});

describe("secure session shell", () => {
  it("keeps a protected route private while signed out", async () => {
    window.history.replaceState({}, "", "/portfolio");
    render(app(new FakeSession()));

    expect(await screen.findByRole("heading", { name: /sign in/i })).toBeVisible();
    expect(screen.queryByText("Portfolio workspace")).not.toBeInTheDocument();
  });

  it("requests and verifies only a six-digit email OTP", async () => {
    const session = new FakeSession();
    const user = userEvent.setup();
    render(app(session));

    await user.type(await screen.findByLabelText(/email/i), " Raj@Example.COM ");
    await user.click(screen.getByRole("button", { name: /send secure code/i }));
    expect(session.requested).toEqual(["raj@example.com"]);

    const code = await screen.findByLabelText(/six-digit code/i);
    await user.type(code, "12345");
    expect(screen.getByRole("button", { name: /verify/i })).toBeDisabled();
    await user.type(code, "6");
    await user.click(screen.getByRole("button", { name: /verify/i }));

    expect(session.verified).toEqual([{ email: "raj@example.com", token: "123456" }]);
    expect(await screen.findByRole("heading", { name: /^today$/i })).toBeVisible();
  });

  it("gates every private route until current consent is confirmed", async () => {
    const session = new FakeSession();
    session.viewer = {
      kind: "consent-required",
      userId: "11111111-1111-4111-8111-111111111111",
      email: "friend@example.com",
      displayName: "Friend",
    };
    window.history.replaceState({}, "", "/runs");
    render(app(session));

    expect(await screen.findByRole("heading", { name: /review before continuing/i })).toBeVisible();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByText("Runs workspace")).not.toBeInTheDocument();
  });

  it("offers a desktop PKCE link without creating an account", async () => {
    const session = new FakeSession();
    const user = userEvent.setup();
    render(app(session));

    await user.type(await screen.findByLabelText(/email/i), "friend@example.com");
    await user.click(screen.getByRole("button", { name: /desktop sign-in link/i }));

    expect(session.desktopLinks).toEqual([{
      email: "friend@example.com",
      redirectTo: `${window.location.origin}/auth/callback`,
    }]);
  });

  it("never registers a service worker", async () => {
    const register = vi.fn();
    Object.defineProperty(window.navigator, "serviceWorker", {
      configurable: true,
      value: { register },
    });
    render(app(new FakeSession()));
    await screen.findByRole("heading", { name: /sign in/i });
    expect(register).not.toHaveBeenCalled();
  });

  it("returns to sign-in when an authenticated session is revoked", async () => {
    const session = new FakeSession();
    session.viewer = {
      kind: "ready",
      userId: "11111111-1111-4111-8111-111111111111",
      email: "raj@example.com",
      displayName: "Raj",
    };
    render(app(session));
    expect(await screen.findByRole("heading", { name: /^today$/i })).toBeVisible();

    session.viewer = { kind: "signed-out", reason: "SESSION_REVOKED" };
    session.listeners.forEach((listener) => { listener(); });
    await waitFor(() => expect(screen.getByRole("heading", { name: /sign in/i })).toBeVisible());
  });
});
