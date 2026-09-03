import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConnectionsScreen } from "./ConnectionsScreen";
import type {
  AppApiClient,
  ConnectionCreateReceipt,
  PairingCodeReceipt,
} from "../../lib/app-api";
import type { ConnectionsSnapshot, DashboardRepository } from "../../lib/dashboard";
import type { SessionService, ViewerState } from "../../lib/session";

const connectionId = "11111111-1111-4111-8111-111111111111";
const receiptId = "66666666-6666-4666-8666-666666666666";
const viewer: Extract<ViewerState, { kind: "ready" }> = {
  kind: "ready", userId: "77777777-7777-4777-8777-777777777777",
  email: "owner@example.test", displayName: "Owner",
};

function snapshot(overrides: Partial<ConnectionsSnapshot> = {}): ConnectionsSnapshot {
  return {
    connections: [],
    telegram: null,
    handshakeRuns: [],
    ...overrides,
  };
}

function harness(initial = snapshot()) {
  let current = initial;
  const repository = {
    loadConnections: vi.fn(() => Promise.resolve(current)),
  } as unknown as DashboardRepository;
  const created: ConnectionCreateReceipt = {
    connectionId,
    publicId: "22222222-2222-4222-8222-222222222222",
    provider: "claude",
    status: "disabled",
    contractVersion: 2,
    gatewayCredential: `22222222-2222-4222-8222-222222222222.${"A".repeat(43)}`,
    gatewayUrl: "https://project.supabase.co/functions/v1/agent-gateway",
    credentialDisplay: "once",
  };
  const paired: PairingCodeReceipt = {
    pairingId: "33333333-3333-4333-8333-333333333333",
    status: "issued",
    code: "ABCD234567",
    expiresAt: "2026-09-03T18:10:00Z",
  };
  const client = {
    createConnection: vi.fn(() => Promise.resolve(created)),
    beginConnectionHandshake: vi.fn(() => Promise.resolve({
      connectionId,
      status: "testing" as const,
      handshakeId: "44444444-4444-4444-8444-444444444444",
      triggerRequestId: "55555555-5555-4555-8555-555555555555",
      duplicate: false,
    })),
    activateConnection: vi.fn(() => Promise.resolve({ connectionId, status: "active" as const })),
    revokeConnection: vi.fn(() => Promise.resolve({ connectionId, status: "revoked" as const })),
    requestPairingCode: vi.fn(() => Promise.resolve(paired)),
    unlinkTelegram: vi.fn(() => Promise.resolve({ status: "unlinked" as const })),
    beginStepUp: vi.fn(() => Promise.resolve({
      challengeId: "88888888-8888-4888-8888-888888888888",
      expiresAt: "2026-09-03T18:10:00Z",
    })),
    completeStepUp: vi.fn(() => Promise.resolve({
      receiptId, expiresAt: "2026-09-03T18:05:00Z",
    })),
  } as unknown as AppApiClient;
  const session = {
    requestOtp: vi.fn(() => Promise.resolve()),
    verifyOtp: vi.fn(() => Promise.resolve()),
  } as unknown as SessionService;
  return { repository, client, session, created, paired, setCurrent(value: ConnectionsSnapshot) { current = value; } };
}

describe("provider and Telegram setup", () => {
  it("offers only the proven Claude Routine adapter and states its limits", async () => {
    const { repository, client, session } = harness();
    render(<ConnectionsScreen repository={repository} connectionClient={client}
      accountClient={client} session={session} viewer={viewer} />);

    expect(await screen.findByRole("heading", { name: /claude routines/i })).toBeVisible();
    expect(screen.queryByText(/chatgpt|grok|bring your own key|second opinion/i)).not.toBeInTheDocument();
    expect(screen.getByText(/research preview/i)).toBeVisible();
    expect(screen.getByText(/daily routine allowance/i)).toBeVisible();
    expect(screen.getByText(/green.*does not prove/i)).toBeVisible();
    expect(screen.queryByLabelText(/cron|schedule expression/i)).not.toBeInTheDocument();
  });

  it("shows the inbound credential once and never displays the trigger token", async () => {
    const { repository, client, session, created } = harness();
    const user = userEvent.setup();
    render(<ConnectionsScreen repository={repository} connectionClient={client}
      accountClient={client} session={session} viewer={viewer} />);

    await user.click(await screen.findByRole("button", { name: /start claude setup/i }));
    expect(await screen.findByText(created.gatewayCredential)).toBeVisible();
    expect(client.createConnection).toHaveBeenCalledWith("provider-data-v1");
    await user.click(screen.getByRole("button", { name: /i saved the credential/i }));
    expect(screen.queryByText(created.gatewayCredential)).not.toBeInTheDocument();

    const token = "routine-token-value-that-is-long-enough";
    await user.type(screen.getByLabelText(/routine fire url/i), "https://api.anthropic.com/v1/claude_code/routines/trig_ABC123/fire");
    await user.type(screen.getByLabelText(/one-time routine token/i), token);
    expect(screen.getByLabelText(/one-time routine token/i)).toHaveAttribute("type", "password");
    expect(document.body.textContent).not.toContain(token);
    await user.click(screen.getByRole("button", { name: /test connection/i }));
    expect(session.requestOtp).toHaveBeenCalledWith(viewer.email);
    await user.type(await screen.findByLabelText(/fresh six-digit code/i), "123456");
    await user.click(screen.getByRole("button", { name: /verify fresh code/i }));
    await waitFor(() => {
      expect(client.beginConnectionHandshake).toHaveBeenCalledWith(
        connectionId,
        "https://api.anthropic.com/v1/claude_code/routines/trig_ABC123/fire",
        token,
        receiptId,
      );
    });
    expect(screen.getByLabelText(/one-time routine token/i)).toHaveValue("");
  });

  it("activates only a ready connection, links to the handshake session, and supports rotation", async () => {
    const { repository, client, session } = harness(snapshot({
      connections: [{
        id: connectionId,
        publicId: "22222222-2222-4222-8222-222222222222",
        provider: "claude",
        credentialType: "claude_routine_v1",
        capabilities: { suggestion_only: true },
        contractVersion: 2,
        status: "ready",
        lastHandshakeAt: "2026-09-03T18:00:00Z",
        createdAt: "2026-09-03T17:00:00Z",
        updatedAt: "2026-09-03T18:00:00Z",
      }],
      handshakeRuns: [{
        slotId: "44444444-4444-4444-8444-444444444444", marketDate: "2026-09-03",
        phase: "on-demand", purpose: "handshake", expectedAt: "2026-09-03T17:55:00Z",
        windowEndsAt: "2026-09-03T18:55:00Z", holiday: false, slotStatus: "completed",
        triggerStatus: "triggered", triggerResponseStatus: 200,
        providerSessionUrl: "https://claude.ai/code/session_abcdef123", triggerStartedAt: null,
        triggerFinishedAt: null, runId: null, startedAt: null, finishedAt: null, runStatus: null,
        dataAsOf: null, sourceStatus: {}, symbols: [], writeCounts: {}, summary: null,
        provider: null, model: null, submissionStatus: null, policyStates: [],
        evidenceStatus: "not_started", publicationKind: null, publicationStatus: null,
        deliveredAt: null, telegramMessageIds: [], errorCode: null,
      }],
    }));
    const user = userEvent.setup();
    render(<ConnectionsScreen repository={repository} connectionClient={client}
      accountClient={client} session={session} viewer={viewer} />);

    const sessionLink = await screen.findByRole("link", { name: /open handshake session/i });
    expect(sessionLink).toHaveAttribute("href", "https://claude.ai/code/session_abcdef123");
    await user.click(screen.getByRole("button", { name: /activate as primary/i }));
    expect(client.activateConnection).toHaveBeenCalledWith(connectionId);
    await user.click(screen.getByRole("button", { name: /revoke connection/i }));
    expect(client.revokeConnection).toHaveBeenCalledWith(connectionId);
    expect(screen.getByText(/rotation creates a new credential/i)).toBeVisible();
  });

  it("issues an expiring private-chat pairing code and can unlink", async () => {
    const { repository, client, session } = harness(snapshot({
      telegram: { status: "active", linkedAt: "2026-09-03T17:00:00Z", revokedAt: null },
    }));
    const user = userEvent.setup();
    render(<ConnectionsScreen repository={repository} connectionClient={client}
      accountClient={client} session={session} viewer={viewer} />);

    await user.click(await screen.findByRole("button", { name: /unlink telegram/i }));
    expect(client.unlinkTelegram).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: /new pairing code/i }));
    await user.type(await screen.findByLabelText(/fresh six-digit code/i), "123456");
    await user.click(screen.getByRole("button", { name: /verify fresh code/i }));
    expect(await screen.findByText(/\/pair ABCD234567/i)).toBeVisible();
    expect(client.requestPairingCode).toHaveBeenCalledWith(receiptId);
    expect(screen.getByText(/expires/i)).toBeVisible();
  });
});
