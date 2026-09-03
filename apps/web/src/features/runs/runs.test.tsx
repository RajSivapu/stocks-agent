import axe from "axe-core";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppApiError, type RunControlClient } from "../../lib/app-api";
import type { DashboardRepository, RunSnapshot } from "../../lib/dashboard";
import { RunsScreen } from "./RunsScreen";

const runs: RunSnapshot = {
  runs: [
    {
      slotId: "11111111-1111-4111-8111-111111111111", marketDate: "2026-09-03",
      phase: "pre-market", purpose: "scheduled", expectedAt: "2026-09-03T11:30:00Z",
      windowEndsAt: "2026-09-03T12:30:00Z", holiday: false, slotStatus: "completed",
      triggerStatus: "provider_started", triggerResponseStatus: 202,
      providerSessionUrl: "https://claude.ai/code/session_Abc123", triggerStartedAt: "2026-09-03T11:30:00Z",
      triggerFinishedAt: "2026-09-03T11:30:02Z", runId: "22222222-2222-4222-8222-222222222222",
      startedAt: "2026-09-03T11:30:05Z", finishedAt: "2026-09-03T11:32:00Z",
      runStatus: "completed", dataAsOf: "2026-09-03T11:31:00Z", sourceStatus: { evidence: "server_verified" },
      symbols: ["CENX"], writeCounts: { suggestions: 1 }, summary: "One watch was persisted.",
      provider: "claude", model: "configured", submissionStatus: "accepted",
      policyStates: ["approved"], evidenceStatus: "fresh", publicationKind: "brief",
      publicationStatus: "delivered", deliveredAt: "2026-09-03T11:32:01Z",
      telegramMessageIds: ["701"], errorCode: null,
    },
    {
      slotId: "33333333-3333-4333-8333-333333333333", marketDate: "2026-09-03",
      phase: "intraday", purpose: "scheduled", expectedAt: "2026-09-03T17:00:00Z",
      windowEndsAt: "2026-09-03T18:00:00Z", holiday: false, slotStatus: "trigger_unknown",
      triggerStatus: "trigger_unknown", triggerResponseStatus: null, providerSessionUrl: null,
      triggerStartedAt: "2026-09-03T17:00:00Z", triggerFinishedAt: "2026-09-03T17:00:10Z",
      runId: null, startedAt: null, finishedAt: null, runStatus: null, dataAsOf: null,
      sourceStatus: {}, symbols: [], writeCounts: {}, summary: null, provider: null, model: null,
      submissionStatus: null, policyStates: [], evidenceStatus: "not_started",
      publicationKind: null, publicationStatus: null, deliveredAt: null,
      telegramMessageIds: [], errorCode: "TRIGGER_OUTCOME_UNKNOWN",
    },
    {
      slotId: "44444444-4444-4444-8444-444444444444", marketDate: "2026-09-02",
      phase: "post-market", purpose: "scheduled", expectedAt: "2026-09-02T21:10:00Z",
      windowEndsAt: "2026-09-02T22:10:00Z", holiday: false, slotStatus: "missed",
      triggerStatus: "trigger_failed", triggerResponseStatus: 503, providerSessionUrl: null,
      triggerStartedAt: null, triggerFinishedAt: null, runId: null, startedAt: null,
      finishedAt: null, runStatus: null, dataAsOf: null, sourceStatus: {}, symbols: [],
      writeCounts: {}, summary: null, provider: null, model: null, submissionStatus: null,
      policyStates: [], evidenceStatus: "not_started", publicationKind: null,
      publicationStatus: null, deliveredAt: null, telegramMessageIds: [],
      errorCode: "EXPECTED_RUN_MISSED",
    },
    {
      slotId: null, marketDate: "2026-09-01", phase: "intraday", purpose: "provider_direct",
      expectedAt: null, windowEndsAt: null, holiday: false, slotStatus: "provider_started",
      triggerStatus: null, triggerResponseStatus: null, providerSessionUrl: null,
      triggerStartedAt: null, triggerFinishedAt: null, runId: "55555555-5555-4555-8555-555555555555",
      startedAt: "2026-09-01T16:00:00Z", finishedAt: "2026-09-01T16:01:00Z",
      runStatus: "partial", dataAsOf: "2026-09-01T15:59:00Z", sourceStatus: {}, symbols: ["VTI"],
      writeCounts: {}, summary: "Stopped before publication.", provider: "claude", model: "configured",
      submissionStatus: "quarantined", policyStates: ["vetoed"], evidenceStatus: "source_conflict",
      publicationKind: "data_warning", publicationStatus: "delivery_failed", deliveredAt: null,
      telegramMessageIds: [], errorCode: "RUN_PARTIAL",
    },
  ],
};

function repository(loadRuns = () => Promise.resolve(runs)): DashboardRepository {
  return {
    loadToday: () => Promise.reject(new Error("unused")),
    loadPortfolio: () => Promise.reject(new Error("unused")),
    loadActivity: () => Promise.reject(new Error("unused")),
    loadResearch: () => Promise.reject(new Error("unused")),
    loadRuns,
    loadConnections: () => Promise.reject(new Error("unused")),
    loadSettings: () => Promise.reject(new Error("unused")),
    lookupCommand: () => Promise.resolve(null),
  };
}

function runClient(requestRun = vi.fn().mockResolvedValue({
  status: "queued", slotId: "66666666-6666-4666-8666-666666666666",
  phase: "on-demand", marketDate: "2026-09-03", expectedBy: "2026-09-03T18:20:00Z",
  telegram: "suppressed",
})): RunControlClient {
  return { requestRun };
}

describe("run transparency", () => {
  it("renders every phase boundary and truthful delivery/error state without credentials", async () => {
    const { container } = render(<RunsScreen repository={repository()} runClient={runClient()} />);
    const delivered = await screen.findByRole("article", { name: /pre-market/i });
    expect(within(delivered).getByText("Expected")).toBeVisible();
    expect(within(delivered).getByText("Provider Started")).toBeVisible();
    expect(within(delivered).getByText("Fresh")).toBeVisible();
    expect(within(delivered).getByText("Delivered")).toBeVisible();
    expect(within(delivered).getByText("Message 701")).toBeVisible();
    expect(within(delivered).getByRole("link", { name: /provider session/i })).toHaveAttribute("href", "https://claude.ai/code/session_Abc123");
    expect(screen.getByText("Trigger Outcome Unknown")).toBeVisible();
    expect(screen.getByText("Expected Run Missed")).toBeVisible();
    expect(screen.getByText("Run Partial")).toBeVisible();
    expect(screen.getByText("Source Conflict")).toBeVisible();
    expect(container.textContent).not.toMatch(/token|credential|raw prompt/i);
  });

  it("queues a fresh on-demand slot and labels Telegram as suppressed", async () => {
    const requestRun = vi.fn().mockResolvedValue({
      status: "queued", slotId: "66666666-6666-4666-8666-666666666666",
      phase: "on-demand", marketDate: "2026-09-03", expectedBy: "2026-09-03T18:20:00Z",
      telegram: "suppressed",
    });
    const user = userEvent.setup();
    render(<RunsScreen repository={repository()} runClient={runClient(requestRun)} />);
    await screen.findByRole("heading", { name: /^runs$/i });

    await user.click(screen.getByRole("button", { name: /run analysis now/i }));

    expect(requestRun).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/queued as a fresh on-demand run/i)).toBeVisible();
    expect(screen.getByText(/no Telegram notification will be sent/i)).toBeVisible();
  });

  it("fails closed on rate limiting and has loading, empty, error, and accessible states", async () => {
    const user = userEvent.setup();
    const limited = runClient(vi.fn().mockRejectedValue(new AppApiError("RATE_LIMITED")));
    const first = render(<RunsScreen repository={repository()} runClient={limited} />);
    await screen.findByRole("heading", { name: /^runs$/i });
    await user.click(screen.getByRole("button", { name: /run analysis now/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/one on-demand run per hour/i);
    first.unmount();

    let resolveRuns: ((value: RunSnapshot) => void) | undefined;
    const pending = new Promise<RunSnapshot>((resolve) => { resolveRuns = resolve; });
    const loading = render(<RunsScreen repository={repository(() => pending)} runClient={runClient()} />);
    expect(screen.getByRole("status", { name: /loading runs/i })).toBeVisible();
    resolveRuns?.({ runs: [] });
    expect(await screen.findByText(/no run history yet/i)).toBeVisible();
    loading.unmount();

    const failure = render(<RunsScreen repository={repository(() => Promise.reject(new Error("secret")))} runClient={runClient()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/run history is unavailable/i);
    failure.unmount();

    const { container } = render(<RunsScreen repository={repository()} runClient={runClient()} />);
    await screen.findByRole("heading", { name: /^runs$/i });
    expect((await axe.run(container, { rules: { "color-contrast": { enabled: false } } })).violations).toEqual([]);
  });
});
