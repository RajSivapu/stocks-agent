import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AppApiClient } from "../../lib/app-api";
import type { SessionService } from "../../lib/session";
import { AccountPanel, DeletionPendingGate } from "./AccountPanel";


const viewer = {
  kind: "ready" as const,
  userId: "11111111-1111-4111-8111-111111111111",
  email: "owner@example.com",
  displayName: "Owner",
};

function harness() {
  const session = {
    requestOtp: vi.fn(() => Promise.resolve()),
    verifyOtp: vi.fn(() => Promise.resolve()),
    signOut: vi.fn(() => Promise.resolve()),
  } as unknown as SessionService;
  const client = {
    beginStepUp: vi.fn(() => Promise.resolve({
      challengeId: "22222222-2222-4222-8222-222222222222",
      expiresAt: "2026-09-03T16:10:00Z",
    })),
    completeStepUp: vi.fn(() => Promise.resolve({
      receiptId: "33333333-3333-4333-8333-333333333333",
      expiresAt: "2026-09-03T16:05:00Z",
    })),
    requestDeletion: vi.fn(() => Promise.resolve({
      deletionRequestId: "44444444-4444-4444-8444-444444444444",
      confirmationPhrase: "DELETE MY ACCOUNT" as const,
      confirmationExpiresAt: "2026-09-03T16:05:00Z",
    })),
    confirmDeletion: vi.fn(() => Promise.resolve({
      deletionRequestId: "44444444-4444-4444-8444-444444444444",
      status: "pending" as const,
      cancelUntil: "2026-09-06T16:00:00Z",
      deleteBy: "2026-09-10T16:00:00Z",
    })),
    cancelDeletion: vi.fn(() => Promise.resolve({
      deletionRequestId: "44444444-4444-4444-8444-444444444444",
      status: "cancelled" as const,
    })),
    loadAccountStatus: vi.fn(() => Promise.resolve({
      accountStatus: "deletion_pending" as const,
      deletionStatus: "pending" as const,
      requestedAt: "2026-09-03T16:00:00Z",
      cancelUntil: "2026-09-06T16:00:00Z",
      deleteBy: "2026-09-10T16:00:00Z",
      olderTelegramHistoryRequiresManualRemoval: true,
    })),
    downloadExport: vi.fn(() => Promise.resolve({
      filename: "stock-agent-ledger.csv", mediaType: "text/csv; charset=utf-8",
      blob: new Blob(["ledger"]),
    })),
  } as unknown as AppApiClient;
  return { session, client };
}

describe("private account lifecycle", () => {
  it("requires a new OTP session and exact typed phrase before deletion", async () => {
    const { session, client } = harness();
    const user = userEvent.setup();
    render(<AccountPanel viewer={viewer} session={session} accountClient={client} />);

    await user.click(screen.getByRole("button", { name: /start account deletion/i }));
    expect(client.beginStepUp).toHaveBeenCalledOnce();
    expect(session.requestOtp).toHaveBeenCalledWith("owner@example.com");
    const otp = await screen.findByLabelText(/fresh six-digit code/i);
    await user.type(otp, "123456");
    await user.click(screen.getByRole("button", { name: /verify fresh code/i }));
    await waitFor(() => expect(session.verifyOtp).toHaveBeenCalledWith("owner@example.com", "123456"));
    expect(client.completeStepUp).toHaveBeenCalledWith("22222222-2222-4222-8222-222222222222");
    expect(client.requestDeletion).toHaveBeenCalledWith("33333333-3333-4333-8333-333333333333");

    const phrase = await screen.findByLabelText(/type delete my account/i);
    await user.type(phrase, "delete my account");
    expect(screen.getByRole("button", { name: /confirm account deletion/i })).toBeDisabled();
    await user.clear(phrase);
    await user.type(phrase, "DELETE MY ACCOUNT");
    await user.click(screen.getByRole("button", { name: /confirm account deletion/i }));
    await waitFor(() => expect(client.confirmDeletion).toHaveBeenCalledWith(
      "44444444-4444-4444-8444-444444444444",
      "33333333-3333-4333-8333-333333333333",
      "DELETE MY ACCOUNT",
    ));
    expect(session.signOut).toHaveBeenCalledOnce();
  });

  it("offers both secret-free exports without treating a download as deletion consent", async () => {
    const { session, client } = harness();
    const user = userEvent.setup();
    const createObjectURL = vi.fn(() => "blob:test");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    render(<AccountPanel viewer={viewer} session={session} accountClient={client} />);

    await user.click(screen.getByRole("button", { name: /download ledger csv/i }));
    await waitFor(() => expect(client.downloadExport).toHaveBeenCalledWith("ledger"));
    expect(client.beginStepUp).not.toHaveBeenCalled();
  });

  it("deletion-pending gate exposes only export, fresh-OTP cancellation, and manual Telegram cleanup", async () => {
    const { session, client } = harness();
    render(<DeletionPendingGate
      viewer={{ ...viewer, kind: "deletion-pending" }}
      session={session}
      accountClient={client}
      onCancelled={vi.fn()}
    />);

    expect(await screen.findByRole("heading", { name: /account deletion is pending/i })).toBeVisible();
    expect(screen.getByText(/72-hour cancellation window/i)).toBeVisible();
    expect(screen.getByText(/older telegram messages/i)).toBeVisible();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /cancel deletion with email code/i })).toBeVisible();
    expect(screen.getByRole("button", { name: /download account json/i })).toBeVisible();
  });
});
