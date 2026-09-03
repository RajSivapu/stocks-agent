import axe from "axe-core";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ResearchScreen } from "./ResearchScreen";
import type { DashboardRepository, ResearchSnapshot } from "../../lib/dashboard";

const xss = '<img src=x onerror="window.__xss=1">';
const research: ResearchSnapshot = {
  items: [
    {
      id: "101", runId: "11111111-1111-4111-8111-111111111111",
      marketDate: "2026-09-03", phase: "intraday", runStatus: "completed",
      provider: "claude", model: "configured", createdAt: "2026-09-03T15:00:00Z",
      ticker: "CENX", action: "watch", rawAction: "buy", policyStatus: "downgraded",
      policyReasonCodes: ["STALE_SOURCE"], policyExplanations: [xss],
      analyst: { completed: true, action: "buy", thesis: xss },
      checker: { completed: true, verdict: "approve", reason: "Risk bounded." },
      confidence: "medium", verifiedPrice: "47.50", evidenceAsOf: "2026-09-03T14:58:00Z",
      entryZoneLow: "46.00", entryZoneHigh: "47.00", stop: "41.50", target: "58.00",
      invalidationPrice: "41.50", validUntil: "2026-09-04", horizon: "swing",
      bucket: "unclassified", riskVerdict: "Position cap applies.",
      decisiveFactor: "Fresh filing needed.", reason: xss, bullCase: "Capacity expansion.",
      bearCase: "Aluminum cycle weakens.", evidenceStatus: "stale",
      evidence: [{
        evidenceId: "quote-current", category: "market_snapshot", source: "yahoo-chart",
        reference: "https://finance.yahoo.com/quote/CENX", observedAt: "2026-09-03T14:58:00Z",
        retrievedAt: "2026-09-03T15:00:00Z", revalidatedAt: null,
        claims: ["Verified quote"], status: "stale",
      }],
      publicationKind: "brief", notificationStatus: "suppressed", deliveredAt: null,
      telegramMessageIds: [], deliveryErrorCode: null,
      outcomes: [
        { horizonSessions: 5, coverageStatus: "complete", stockReturnPct: "2.50", benchmark: "VOO", benchmarkReturnPct: "1.00", excessReturnPct: "1.50", mfePct: "4", maePct: "-2", entryHitAt: null, stopHitAt: null, targetHitAt: null, invalidationHitAt: null, directionSuccess: true, gradedAt: "2026-09-10T12:00:00Z" },
        { horizonSessions: 21, coverageStatus: "incomplete", stockReturnPct: null, benchmark: "VOO", benchmarkReturnPct: null, excessReturnPct: null, mfePct: null, maePct: null, entryHitAt: null, stopHitAt: null, targetHitAt: null, invalidationHitAt: null, directionSuccess: null, gradedAt: "2026-09-10T12:00:00Z" },
        { horizonSessions: 63, coverageStatus: "corporate_action_review", stockReturnPct: null, benchmark: "VOO", benchmarkReturnPct: null, excessReturnPct: null, mfePct: null, maePct: null, entryHitAt: null, stopHitAt: null, targetHitAt: null, invalidationHitAt: null, directionSuccess: null, gradedAt: "2026-09-10T12:00:00Z" },
      ],
    },
    {
      id: "102", runId: "22222222-2222-4222-8222-222222222222",
      marketDate: "2026-09-02", phase: "post-market", runStatus: "completed",
      provider: "claude", model: "configured", createdAt: "2026-09-02T21:00:00Z",
      ticker: "VTI", action: "avoid", rawAction: "buy", policyStatus: "vetoed",
      policyReasonCodes: ["SOURCE_CONFLICT"], policyExplanations: [], analyst: {}, checker: {},
      confidence: "low", verifiedPrice: "380.16", evidenceAsOf: "2026-09-02T20:58:00Z",
      entryZoneLow: null, entryZoneHigh: null, stop: null, target: null,
      invalidationPrice: null, validUntil: null, horizon: "long", bucket: "core",
      riskVerdict: null, decisiveFactor: null, reason: "Conflicting source values.",
      bullCase: null, bearCase: null, evidenceStatus: "source_conflict", evidence: [],
      publicationKind: "data_warning", notificationStatus: "delivery_unknown", deliveredAt: null,
      telegramMessageIds: [], deliveryErrorCode: "TELEGRAM_OUTCOME_UNKNOWN", outcomes: [],
    },
    {
      id: "103", runId: "33333333-3333-4333-8333-333333333333",
      marketDate: "2026-09-01", phase: "pre-market", runStatus: "completed",
      provider: "claude", model: "configured", createdAt: "2026-09-01T12:00:00Z",
      ticker: "MP", action: "watch", rawAction: "watch", policyStatus: "approved",
      policyReasonCodes: [], policyExplanations: [], analyst: {}, checker: {}, confidence: "medium",
      verifiedPrice: "62.00", evidenceAsOf: "2026-09-01T11:55:00Z", entryZoneLow: null,
      entryZoneHigh: null, stop: null, target: null, invalidationPrice: null, validUntil: null,
      horizon: "study", bucket: "speculative", riskVerdict: null, decisiveFactor: null,
      reason: "Awaiting split review.", bullCase: null, bearCase: null,
      evidenceStatus: "corporate_action_pending", evidence: [], publicationKind: "brief",
      notificationStatus: "delivered", deliveredAt: "2026-09-01T12:01:00Z",
      telegramMessageIds: ["701"], deliveryErrorCode: null, outcomes: [],
    },
  ],
};

function repository(loadResearch = () => Promise.resolve(research)): DashboardRepository {
  return {
    loadToday: () => Promise.reject(new Error("unused")),
    loadPortfolio: () => Promise.reject(new Error("unused")),
    loadActivity: () => Promise.reject(new Error("unused")),
    loadResearch,
    loadRuns: () => Promise.reject(new Error("unused")),
    lookupCommand: () => Promise.resolve(null),
  };
}

describe("research history", () => {
  it("renders policy, evidence, separate reviews, delivery truth, and 5/21/63 outcomes", async () => {
    const { container } = render(<ResearchScreen repository={repository()} />);
    const cenx = await screen.findByRole("article", { name: /CENX/i });
    expect(within(cenx).getByText("Downgraded")).toBeVisible();
    expect(within(cenx).getByText("Stale")).toBeVisible();
    expect(within(cenx).getByText("$47.50")).toBeVisible();
    expect(within(cenx).getByRole("heading", { name: "Analyst" })).toBeVisible();
    expect(within(cenx).getByRole("heading", { name: "Checker" })).toBeVisible();
    expect(within(cenx).getByRole("link", { name: /yahoo-chart/i })).toHaveAttribute("rel", "noreferrer noopener");
    expect(within(cenx).getByText("5 sessions")).toBeVisible();
    expect(within(cenx).getByText("21 sessions")).toBeVisible();
    expect(within(cenx).getByText("63 sessions")).toBeVisible();
    expect(screen.getByText("Source Conflict")).toBeVisible();
    expect(screen.getByText("Corporate Action Pending")).toBeVisible();
    expect(screen.getByText("Telegram Outcome Unknown")).toBeVisible();
    expect(screen.getByText("Message 701")).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getAllByText(xss).length).toBeGreaterThan(0);
    expect((window as Window & { __xss?: number }).__xss).toBeUndefined();
  });

  it("has explicit loading, empty, error, and accessible states", async () => {
    let resolveResearch: ((value: ResearchSnapshot) => void) | undefined;
    const pending = new Promise<ResearchSnapshot>((resolve) => { resolveResearch = resolve; });
    const first = render(<ResearchScreen repository={repository(() => pending)} />);
    expect(screen.getByRole("status", { name: /loading research/i })).toBeVisible();
    resolveResearch?.({ items: [] });
    expect(await screen.findByText(/no research records yet/i)).toBeVisible();
    first.unmount();

    const rendered = render(<ResearchScreen repository={repository(() => Promise.reject(new Error("raw secret")))} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/research history is unavailable/i);
    expect(screen.queryByText("raw secret")).not.toBeInTheDocument();
    rendered.unmount();

    const { container } = render(<ResearchScreen repository={repository()} />);
    await screen.findByRole("heading", { name: /^research$/i });
    expect((await axe.run(container, { rules: { "color-contrast": { enabled: false } } })).violations).toEqual([]);
  });
});
