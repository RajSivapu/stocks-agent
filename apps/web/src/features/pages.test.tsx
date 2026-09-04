import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";

import type {
  AlertsView,
  CompanionView,
  IdeasView,
  PortfolioView,
  RunsView,
  SystemView,
  TodayView,
} from "@stocks-agent/dashboard-contracts";

import { AlertsPage } from "./alerts/AlertsPage";
import { CompanionPage } from "./companion/CompanionPage";
import { IdeasPage } from "./ideas/IdeasPage";
import { PortfolioPage } from "./portfolio/PortfolioPage";
import { RunsPage } from "./runs/RunsPage";
import { SystemPage } from "./system/SystemPage";
import { TodayPage } from "./today/TodayPage";

const boundaries = {
  owner_only: true as const,
  suggestion_only: true as const,
  friend_invitations: "disabled" as const,
  brokerage_authority: "none" as const,
};

const today: TodayView = {
  boundaries,
  attention: [{ id: "risk", severity: "review", title: "VTI stop distance", detail: "Review persisted risk level.", data_as_of: "2026-09-03T18:00:00.000Z", destination: "/portfolio" }],
  latest_run: null,
  portfolio: { value: "2200", cost_basis: "2000", unrealized_amount: "200", holdings: [], data_as_of: null, market_state: "unknown", price_sources: [] },
  market_summary: "Mixed close; small caps lagged.",
  entry_zones: [],
  companion: null,
};

it("renders attention first and collapses empty optional blocks", () => {
  const { container } = render(<MemoryRouter><TodayPage data={today} /></MemoryRouter>);
  expect(screen.getByRole("heading", { name: /needs attention/i })).toBeVisible();
  expect(screen.queryByRole("heading", { name: /entry zones/i })).not.toBeInTheDocument();
  const headings = [...container.querySelectorAll("h2")].map((heading) => heading.textContent);
  expect(headings[0]).toMatch(/needs attention/i);
});

it("portfolio omits unsupported market value and labels the recurring reminder", () => {
  const data: PortfolioView = {
    holdings: [{ ticker: "VTI", shares: "2", average_cost: "100", bucket: "core", opened_at: null, stop: "95", target: null, price: null, price_as_of: "2026-09-02T20:00:00.000Z", price_source: "yahoo-chart", market_state: "as_of_close", value: null, unrealized_amount: null, unrealized_percent: null, weight_percent: null, freshness: "stale" }],
    plans: [{ id: "plan", ticker: "VTI", amount: "100", cadence: "monthly", next_due_on: "2026-10-01", due_day: 1, active: true }],
    transactions: [],
    totals: { cost_basis: "200", value: null, unrealized_amount: null },
    comparison_availability: "structured_companion",
    latest_intelligence_run_id: null,
  };
  render(<PortfolioPage data={data} />);
  expect(screen.getByText(/market value withheld/i)).toBeVisible();
  expect(screen.getByText(/reminder only/i)).toBeVisible();
});

it("labels closed-session prices with their receipt time and source", () => {
  const holding = {
    ticker: "VTI", shares: "2", average_cost: "100", bucket: "core", opened_at: null,
    stop: "95", target: null, price: "110", price_as_of: "2026-09-03T20:00:00.000Z",
    price_source: "yahoo-chart", market_state: "as_of_close", value: "220",
    unrealized_amount: "20", unrealized_percent: "10", weight_percent: "100", freshness: "fresh",
  } as unknown as PortfolioView["holdings"][number];
  const portfolio = {
    holdings: [holding], plans: [], transactions: [],
    totals: { cost_basis: "200", value: "220", unrealized_amount: "20" },
    comparison_availability: "structured_companion",
    latest_intelligence_run_id: null,
  } as PortfolioView;
  const todayWithClose = {
    ...today,
    portfolio: {
      ...today.portfolio,
      value: "220",
      holdings: [holding],
      data_as_of: "2026-09-03T20:00:00.000Z",
      market_state: "as_of_close",
      price_sources: ["yahoo-chart"],
    },
  } as unknown as TodayView;

  const { rerender } = render(<PortfolioPage data={portfolio} />);
  expect(screen.getByText(/as of close/i)).toBeVisible();
  expect(screen.getByText(/sep 3, 2026/i)).toBeVisible();
  expect(screen.getByText(/yahoo finance/i)).toBeVisible();

  rerender(<MemoryRouter><TodayPage data={todayWithClose} /></MemoryRouter>);
  expect(screen.getByText(/portfolio prices as of close/i)).toBeVisible();
  expect(screen.getByText(/yahoo finance/i)).toBeVisible();
});

it("renders policy evidence separately from analyst and checker", () => {
  const data: IdeasView = { ideas: [{ id: "idea", ticker: "MSFT", profile: "balanced", final_action: "watch", policy_status: "approved", policy_version: 17, confidence: "medium", entry_zone_low: "410", entry_zone_high: "420", stop: "395", target: "455", valid_until: "2026-09-10", bull_case: "Revenue held up.", bear_case: "Valuation remains high.", decisive_factor: "Fresh evidence", invalidation: "Close below 395", reason_codes: [], analyst_complete: true, checker_complete: true, sources: [], intelligence_run_id: "7d834dbd-75bb-4313-931f-09732f003932", evidence_as_of: "2026-09-04T13:00:00.000Z", outcome: { result: "open", horizon_days: 21, graded_at: "2026-09-04T13:30:00.000Z" } }] };
  render(<IdeasPage data={data} />);
  expect(screen.getByRole("heading", { name: /analyst/i })).toBeVisible();
  expect(screen.getByRole("heading", { name: /checker/i })).toBeVisible();
  expect(screen.getByRole("heading", { name: /deterministic policy/i })).toBeVisible();
  expect(screen.getByRole("heading", { name: /relationship and exposure evidence/i })).toBeVisible();
  expect(screen.getByRole("heading", { name: /conditional scenarios/i })).toBeVisible();
  expect(screen.getByText(/21-session outcome/i)).toBeVisible();
  expect(screen.getByText(/suggestion only/i)).toBeVisible();
});

it("portfolio absorbs today's owner summary and companion context", () => {
  const portfolio: PortfolioView = {
    holdings: [], plans: [], transactions: [], totals: { cost_basis: "2000", value: "2200", unrealized_amount: "200" },
    comparison_availability: "structured_companion", latest_intelligence_run_id: "7d834dbd-75bb-4313-931f-09732f003932",
  };
  const companion: CompanionView = {
    status: "qualified", baseline_ticker: "VTI", companion_ticker: "VXUS", role: "diversifier",
    thesis: "Adds non-U.S. exposure.", risk_note: "Currency risk.", plan_unchanged: true,
    recurring_plan_review_eligible: true, horizons: [], contribution_history: null, evidence: [],
    disclaimer: "Historical scenarios are not forecasts.",
  };
  render(<PortfolioPage data={portfolio} overview={today} companion={companion} />);
  expect(screen.getByRole("heading", { name: /needs attention/i })).toBeVisible();
  expect(screen.getByRole("heading", { name: /companion review/i })).toBeVisible();
  expect(screen.getByText(/current plan remains unchanged/i)).toBeVisible();
});

it("companion uses historical scenario language and preserves the current plan", () => {
  const data: CompanionView = {
    status: "qualified", baseline_ticker: "VTI", companion_ticker: "VXUS", role: "diversifier",
    thesis: "Adds non-U.S. exposure.", risk_note: "Currency risk can lag for long periods.",
    plan_unchanged: true, recurring_plan_review_eligible: true,
    horizons: [{ years: 3, baseline_annualized_percent: "7", companion_annualized_percent: "5", baseline_max_drawdown_percent: "20", companion_max_drawdown_percent: "25", correlation: "0.7" }],
    contribution_history: { contributed: "1200", lower_ending_value: "980", median_ending_value: "1240", higher_ending_value: "1410", sample_count: 50 },
    evidence: [{ label: "Gateway evidence", url: "https://www.sec.gov/evidence" }], disclaimer: "Historical scenarios are not forecasts.",
  };
  render(<CompanionPage data={data} />);
  expect(screen.getByText(/current plan remains unchanged/i)).toBeVisible();
  expect(screen.getByText(/historical scenarios are not forecasts/i)).toBeVisible();
  expect(screen.getByRole("link", { name: /gateway evidence/i })).toHaveAttribute("href", "https://www.sec.gov/evidence");
  expect(screen.queryByText(/winner|best stock|guaranteed/i)).not.toBeInTheDocument();
});

it("alerts, runs, and system expose receipt and immutable-boundary language", () => {
  const alerts: AlertsView = { alerts: [{ id: "a", kind: "brief", phase: "intraday", state: "suppressed", rendered_text: "No trigger", rendered_hash: "f".repeat(64), template_version: "3", telegram_message_ids: [], attempt_count: 0, created_at: "2026-09-03T18:00:00.000Z", delivered_at: null, suppression_reason: "no_trigger", rule_ticker: null, rule_state: null, event_status: null, owner_action: null, sources: [] }] };
  const runs: RunsView = { runs: [{ id: "7d834dbd-75bb-4313-931f-09732f003932", kind: "intraday", status: "completed", started_at: "2026-09-03T17:00:00.000Z", finished_at: "2026-09-03T17:02:00.000Z", data_as_of: "2026-09-03T17:01:00.000Z", policy_version: 17, evaluation_count: 2, suggestion_count: 0, publication_status: "suppressed" }] };
  const system: SystemView = { product_version: "v1", api_version: "v1", policy_version: 17, alert_mode: "shadow", latest_by_kind: {}, latest_publication_status: "suppressed", boundaries, source_coverage: [{ provider: "gdelt", status: "partial", retrieved_at: null, accepted_count: 2, dropped_count: 1 }], latest_report: null, latest_intelligence_run_id: null };
  const { rerender } = render(<AlertsPage data={alerts} />);
  expect(screen.getByText(/no telegram message was sent/i)).toBeVisible();
  rerender(<MemoryRouter><RunsPage data={runs} /></MemoryRouter>);
  expect(screen.getByText(/2 evaluations/i)).toBeVisible();
  rerender(<SystemPage data={system} />);
  expect(screen.getByText(/friend invitations disabled/i)).toBeVisible();
  expect(screen.getByText(/brokerage authority none/i)).toBeVisible();
  expect(screen.getByRole("heading", { name: /write, send, and deploy boundaries/i })).toBeVisible();
  expect(screen.getByText(/no browser write route/i)).toBeVisible();
});
