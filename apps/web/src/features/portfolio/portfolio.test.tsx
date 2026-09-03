import axe from "axe-core";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ActivityScreen } from "../activity/ActivityScreen";
import { TodayScreen } from "../today/TodayScreen";
import { PortfolioScreen } from "./PortfolioScreen";
import type {
  ActivitySnapshot,
  DashboardRepository,
  PortfolioSnapshot,
  TodaySnapshot,
} from "../../lib/dashboard";
import type { CommandClient, CommandReceipt } from "../../lib/app-api";

const portfolio: PortfolioSnapshot = {
  holdings: [
    {
      ticker: "CENX", shares: "43.74819200", avgCost: "47.0200",
      bucket: "unclassified", openedAt: "2026-08-17", stop: null, target: null,
      highWaterPrice: "49.1000", holdOverrideUntil: null, projectionSequence: "2",
    },
    {
      ticker: "VTI", shares: "0.78914200", avgCost: "380.1600",
      bucket: "core", openedAt: "2026-08-19", stop: null, target: null,
      highWaterPrice: null, holdOverrideUntil: null, projectionSequence: "1",
    },
  ],
  quotes: [
    {
      ticker: "CENX", price: "47.50000000", previousClose: "47.00000000",
      provider: "yahoo-chart", asOf: "2026-09-03T15:30:00Z",
      retrievedAt: "2026-09-03T15:30:04Z", session: "REGULAR", status: "delayed",
      adjustmentStatus: "raw", conflictBasisPoints: null,
      corporateActionState: "clear", alertsSuppressed: false,
    },
    {
      ticker: "VTI", price: "376.62000000", previousClose: "379.00000000",
      provider: "yahoo-chart", asOf: "2026-09-03T14:00:00Z",
      retrievedAt: "2026-09-03T15:30:04Z", session: "REGULAR", status: "conflicting",
      adjustmentStatus: "raw", conflictBasisPoints: 175,
      corporateActionState: "clear", alertsSuppressed: true,
    },
  ],
  plans: [{
    id: "33333333-3333-4333-8333-333333333333", ticker: "VTI", bucket: "core",
    amount: "300", cadence: "monthly", nextDueOn: "2026-09-21", dueDay: 21,
    active: true, createdAt: "2026-08-19T12:00:00Z", updatedAt: "2026-08-19T12:00:00Z",
  }],
};

const activity: ActivitySnapshot = {
  transactions: [{
    id: "44444444-4444-4444-8444-444444444444", createdAt: "2026-08-19T12:00:00Z",
    ticker: "VTI", eventType: "trade", side: "buy", qty: "0.78914200",
    price: "380.1600", fees: "0", executedOn: "2026-08-19", ledgerSequence: "1",
    bucket: "core", sourceChannel: "web", correctsTransactionId: null,
  }],
  plans: portfolio.plans,
  commands: [],
};

function repository(overrides: Partial<DashboardRepository> = {}): DashboardRepository {
  return {
    loadToday: () => Promise.resolve({
      runs: [], recommendations: [], ...portfolio,
    } satisfies TodaySnapshot),
    loadPortfolio: () => Promise.resolve(portfolio),
    loadActivity: () => Promise.resolve(activity),
    loadResearch: () => Promise.resolve({ items: [] }),
    loadRuns: () => Promise.resolve({ runs: [] }),
    lookupCommand: () => Promise.resolve(null),
    ...overrides,
  };
}

function commands(overrides: Partial<CommandClient> = {}): CommandClient {
  return {
    preview: vi.fn().mockResolvedValue({
      command_id: "55555555-5555-4555-8555-555555555555",
      status: "previewed",
      preview_digest: "a".repeat(64),
      expires_at: "2099-09-03T16:00:00Z",
      operation: "buy",
      before: { shares: "43.74819200", avg_cost: "47.0200" },
      after: {
        shares: "44.74819200", avg_cost: "47.0310", fees: "1.00",
        cash_total: "48.50", estimated_realized_pnl: "0", bucket: "unclassified",
        plan_impact: "none",
      },
      warnings: ["Quote is delayed; this only records your broker fill."],
    }),
    confirm: vi.fn().mockResolvedValue({
      command_id: "55555555-5555-4555-8555-555555555555",
      status: "applied",
      result: { operation: "buy", ticker: "CENX" },
    }),
    lookup: vi.fn().mockResolvedValue(null),
    ...overrides,
  };
}

describe("owner portfolio screens", () => {
  it("renders exact money, textual freshness, allocation, P&L, and unclassified risk", async () => {
    render(<PortfolioScreen repository={repository()} commands={commands()} />);

    const row = await screen.findByRole("row", { name: /CENX/i });
    expect(within(row).getByText("Unclassified")).toBeVisible();
    expect(within(row).getByText("Delayed")).toBeVisible();
    expect(within(row).getByText("$47.50")).toBeVisible();
    expect(within(row).getByText(/\$21\.00/)).toBeVisible();
    expect(screen.getByText("Conflicting")).toBeVisible();
    expect(screen.getByText(/price conflict/i)).toBeVisible();
    expect(screen.getByText("$300.00 monthly")).toBeVisible();
  });

  it("has explicit loading, empty, and error states", async () => {
    let resolvePortfolio: ((value: PortfolioSnapshot) => void) | undefined;
    const pending = new Promise<PortfolioSnapshot>((resolve) => { resolvePortfolio = resolve; });
    const first = render(<PortfolioScreen repository={repository({ loadPortfolio: () => pending })} commands={commands()} />);
    expect(screen.getByRole("status", { name: /loading portfolio/i })).toBeVisible();
    resolvePortfolio?.({ holdings: [], quotes: [], plans: [] });
    expect(await screen.findByText(/no holdings recorded/i)).toBeVisible();
    first.unmount();

    render(<PortfolioScreen repository={repository({ loadPortfolio: () => Promise.reject(new Error("private")) })} commands={commands()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn’t load your portfolio/i);
    expect(screen.queryByText("private")).not.toBeInTheDocument();
  });

  it("previews a record-only buy before confirmation without browser persistence", async () => {
    const commandClient = commands();
    const localWrite = vi.spyOn(Storage.prototype, "setItem");
    const user = userEvent.setup();
    render(<PortfolioScreen repository={repository()} commands={commandClient} />);
    await screen.findByRole("heading", { name: /^portfolio$/i });

    await user.click(screen.getByRole("button", { name: "Record Buy" }));
    await user.type(screen.getByLabelText("Ticker"), "cenx");
    await user.type(screen.getByLabelText("Filled quantity"), "1");
    await user.type(screen.getByLabelText("Fill price"), "47.50");
    await user.type(screen.getByLabelText("Fees"), "1.00");
    await user.type(screen.getByLabelText("Broker cash total"), "48.50");
    await user.type(screen.getByLabelText("Execution date"), "2026-09-03");
    await user.selectOptions(screen.getByLabelText("Risk bucket"), "unclassified");
    await user.click(screen.getByRole("button", { name: /preview record/i }));

    const preview = await screen.findByRole("dialog", { name: /review record buy/i });
    expect(within(preview).getByText("44.74819200 shares")).toBeVisible();
    expect(within(preview).getByText("$48.50")).toBeVisible();
    expect(within(preview).getByText(/expires/i)).toBeVisible();
    expect(within(preview).getByText(/digest aaaaaaaa/i)).toBeVisible();
    expect(within(preview).getByText("Plan impact")).toBeVisible();
    expect(within(preview).getByText("none")).toBeVisible();
    expect(localWrite).not.toHaveBeenCalled();
  });

  it("checks the server receipt after an ambiguous confirmation before allowing retry", async () => {
    const receipt: CommandReceipt = {
      id: "55555555-5555-4555-8555-555555555555", operation: "buy",
      status: "previewed", before: {}, after: {}, warnings: [], result: null,
      errorCode: null, expiresAt: "2099-09-03T16:00:00Z",
      confirmedAt: null, appliedAt: null, createdAt: "2026-09-03T15:45:00Z",
    };
    const lookup = vi.fn().mockResolvedValue(receipt);
    const commandClient = commands({
      confirm: vi.fn().mockRejectedValue(new Error("network")),
      lookup,
    });
    const user = userEvent.setup();
    render(<PortfolioScreen repository={repository()} commands={commandClient} />);
    await screen.findByRole("heading", { name: /^portfolio$/i });
    await user.click(screen.getByRole("button", { name: "Record Buy" }));
    const fields: Array<[string, string]> = [
      ["Ticker", "CENX"], ["Filled quantity", "1"], ["Fill price", "47.50"],
      ["Fees", "0"], ["Execution date", "2026-09-03"],
    ];
    for (const [label, fieldValue] of fields) await user.type(screen.getByLabelText(label), fieldValue);
    await user.selectOptions(screen.getByLabelText("Risk bucket"), "growth");
    await user.click(screen.getByRole("button", { name: /preview record/i }));
    await user.click(await screen.findByRole("button", { name: /confirm record/i }));

    expect(await screen.findByText(/server still shows this preview as unapplied/i)).toBeVisible();
    expect(lookup).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /retry confirmation/i })).toBeEnabled();
  });

  it("refuses to confirm a preview after its server expiry", async () => {
    const confirm = vi.fn().mockResolvedValue({
      command_id: "55555555-5555-4555-8555-555555555555",
      status: "applied",
      result: {},
    });
    const commandClient = commands({
      confirm,
      preview: vi.fn().mockResolvedValue({
        command_id: "55555555-5555-4555-8555-555555555555",
        status: "previewed",
        preview_digest: "a".repeat(64),
        expires_at: "2026-09-03T16:00:00Z",
        operation: "buy",
        before: { shares: "43.74819200", avg_cost: "47.0200" },
        after: { shares: "44.74819200", avg_cost: "47.0310" },
        warnings: [],
      }),
    });
    const clock = vi.spyOn(Date, "now");
    clock.mockReturnValue(Date.parse("2026-09-03T15:59:59Z"));
    const user = userEvent.setup();
    render(<PortfolioScreen repository={repository()} commands={commandClient} />);
    await screen.findByRole("heading", { name: /^portfolio$/i });
    await user.click(screen.getByRole("button", { name: "Record Buy" }));
    for (const [label, fieldValue] of [
      ["Ticker", "CENX"], ["Filled quantity", "1"], ["Fill price", "47.50"],
      ["Fees", "0"], ["Execution date", "2026-09-03"],
    ] as const) await user.type(screen.getByLabelText(label), fieldValue);
    await user.click(screen.getByRole("button", { name: /preview record/i }));
    await screen.findByRole("dialog", { name: /review record buy/i });

    clock.mockReturnValue(Date.parse("2026-09-03T16:00:01Z"));
    await user.click(screen.getByRole("button", { name: /confirm record/i }));

    expect(confirm).not.toHaveBeenCalled();
    expect(await screen.findByText(/preview expired/i)).toBeVisible();
    expect(screen.getByRole("button", { name: /create fresh preview/i })).toBeEnabled();
    clock.mockRestore();
  });

  it("renders Today and Activity without treating failed advice as current", async () => {
    const today: TodaySnapshot = {
      ...portfolio,
      runs: [{
        runId: "66666666-6666-4666-8666-666666666666", kind: "intraday",
        status: "partial", startedAt: "2026-09-03T15:00:00Z",
        finishedAt: "2026-09-03T15:01:00Z", dataAsOf: "2026-09-03T15:00:30Z",
        sourceStatus: {}, symbols: ["CENX"], writeCounts: {},
        summary: "Prior run failed after evidence collection.",
      }],
      recommendations: [],
    };
    const source = repository({
      loadToday: () => Promise.resolve(today),
      loadActivity: () => Promise.resolve(activity),
    });
    const rendered = render(<TodayScreen repository={source} />);
    expect(await screen.findByText(/latest run is partial/i)).toBeVisible();
    expect(screen.getByText(/no current actionable recommendation/i)).toBeVisible();
    rendered.unmount();

    render(<ActivityScreen repository={source} />);
    expect(await screen.findByRole("row", { name: /VTI/i })).toHaveTextContent("Record Buy");
    expect(screen.getByText(/append-only/i)).toBeVisible();
  });

  it("has no automatically detectable accessibility violations", async () => {
    const { container } = render(<PortfolioScreen repository={repository()} commands={commands()} />);
    await screen.findByRole("heading", { name: /^portfolio$/i });
    const result = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(result.violations).toEqual([]);
  });
});
