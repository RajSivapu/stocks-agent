import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";

import type {
  AlertsView,
  CompanionView,
  IdeasView,
  PortfolioView,
  RunDetailView,
  RunsView,
  SystemView,
  TodayView,
} from "@stocks-agent/dashboard-contracts";

import { AppShell } from "../app/AppShell";
import { AlertsPage } from "../features/alerts/AlertsPage";
import { CompanionPage } from "../features/companion/CompanionPage";
import { IdeasPage } from "../features/ideas/IdeasPage";
import { PortfolioPage } from "../features/portfolio/PortfolioPage";
import { RunDetailPage } from "../features/runs/RunDetailPage";
import { RunsPage } from "../features/runs/RunsPage";
import { SystemPage } from "../features/system/SystemPage";
import { TodayPage } from "../features/today/TodayPage";
import { ThemeProvider } from "../theme/theme";

const RUN_ID = "7d834dbd-75bb-4313-931f-09732f003932";
const DATA_TIME = "2026-09-03T18:00:00.000Z";
const boundaries = {
  owner_only: true as const,
  suggestion_only: true as const,
  friend_invitations: "disabled" as const,
  brokerage_authority: "none" as const,
};
const run = {
  id: RUN_ID,
  kind: "intraday" as const,
  status: "completed" as const,
  started_at: "2026-09-03T17:58:00.000Z",
  finished_at: DATA_TIME,
  data_as_of: "2026-09-03T17:59:00.000Z",
  policy_version: 17,
  evaluation_count: 2,
  suggestion_count: 1,
  publication_status: "suppressed" as const,
};
const idea = {
  id: "fixture-idea",
  ticker: "FIXTURE_ONLY_TICKER",
  profile: "balanced",
  final_action: "watch",
  policy_status: "approved" as const,
  policy_version: 17,
  confidence: "medium",
  entry_zone_low: "100",
  entry_zone_high: "104",
  stop: "96",
  target: "114",
  valid_until: "2026-09-10",
  bull_case: "The recorded evidence supported a monitored setup.",
  bear_case: "The persisted risk case remained material.",
  decisive_factor: "Current evidence stayed inside the reviewed boundary.",
  invalidation: "A close below the recorded stop invalidated the setup.",
  reason_codes: [],
  analyst_complete: true,
  checker_complete: true,
  sources: [{ label: "Evidence source", url: "https://example.com/research" }],
};
const holdings = [{
  ticker: "FIXTURE_ONLY_TICKER",
  shares: "10",
  average_cost: "98",
  bucket: "core",
  opened_at: "2026-08-01",
  stop: "96",
  target: "114",
  price: "102",
  price_as_of: DATA_TIME,
  value: "1020",
  unrealized_amount: "40",
  unrealized_percent: "4.08",
  weight_percent: "100",
  freshness: "fresh" as const,
}];

const portfolio: PortfolioView = {
  holdings,
  plans: [{ id: "fixture-plan", ticker: "FIXTURE_ONLY_TICKER", amount: "100", cadence: "monthly", next_due_on: "2026-10-01", due_day: 1, active: true }],
  transactions: [{ id: "fixture-transaction", timestamp: "2026-08-01T15:00:00.000Z", executed_on: "2026-08-01", ticker: "FIXTURE_ONLY_TICKER", side: "buy", quantity: "10", price: "98", source: "owner" }],
  totals: { cost_basis: "980", value: "1020", unrealized_amount: "40" },
  comparison_availability: "structured_companion",
};
const companion: CompanionView = {
  status: "qualified",
  baseline_ticker: "VTI",
  companion_ticker: "VXUS",
  role: "diversifier",
  thesis: "The recorded review found a distinct geographic role beside the U.S. core baseline.",
  risk_note: "Currency and foreign-market risks historically produced long periods of lagging.",
  plan_unchanged: true,
  recurring_plan_review_eligible: true,
  horizons: [{ years: 3, baseline_annualized_percent: "7.2", companion_annualized_percent: "5.1", baseline_max_drawdown_percent: "20.2", companion_max_drawdown_percent: "24.8", correlation: "0.71" }],
  contribution_history: { contributed: "1200", lower_ending_value: "970", median_ending_value: "1230", higher_ending_value: "1420", sample_count: 50 },
  evidence: [{ label: "Fund profile", url: "https://example.com/fund" }],
  disclaimer: "Historical scenarios are not forecasts. This is suggestion only.",
};
const normalAlert = {
  id: "fixture-alert",
  kind: "entry review",
  phase: "intraday",
  state: "suppressed" as const,
  rendered_text: "⚠️ INTRADAY REVIEW\n\nFIXTURE_ONLY_TICKER · Watch $100–$104\nStop $96 · Target $114\n\nNo trigger met policy today.",
  rendered_hash: "f".repeat(64),
  template_version: "3",
  telegram_message_ids: [],
  attempt_count: 0,
  created_at: DATA_TIME,
  delivered_at: null,
  suppression_reason: "no_trigger",
  rule_ticker: null,
  rule_state: null,
  event_status: "not_triggered",
  owner_action: null,
  sources: [{ label: "Evidence source", url: "https://example.com/evidence" }],
};

function FixtureSurface() {
  const location = useLocation();
  const mode = new URLSearchParams(location.search).get("fixture") ?? "complete";
  if (mode === "owner-denied") return <main className="auth-layout"><section className="auth-card"><p className="eyebrow">Private workspace</p><h1>Owner only</h1><p>This dashboard is restricted to its owner.</p></section></main>;
  if (mode === "expired") return <main className="auth-layout"><section className="auth-card"><p className="eyebrow">Protected view paused</p><h1>Session expired</h1><p>Sign in again to continue.</p></section></main>;
  const stale = mode === "stale";
  const hostile = mode === "hostile";
  const alerts: AlertsView = { alerts: [{
    ...normalAlert,
    ...(hostile ? {
      rendered_text: '<img src=x onerror="alert(1)"><b>FIXTURE_ONLY_TICKER</b>',
      sources: [{ label: "unsafe.example", url: "javascript:alert(1)" }],
    } : {}),
  }] };
  const today: TodayView = {
    boundaries,
    attention: [{ id: "fixture-attention", severity: "review", title: "Review the recorded stop distance", detail: "The current receipt places price near the owner-recorded risk level.", data_as_of: DATA_TIME, destination: "/portfolio" }],
    latest_run: run,
    portfolio: { value: "1020", cost_basis: "980", unrealized_amount: "40", holdings },
    market_summary: "The persisted market note described a mixed, narrow session.",
    entry_zones: [idea],
    companion,
  };
  const ideas: IdeasView = { ideas: [idea] };
  const runs: RunsView = { runs: [run] };
  const detail: RunDetailView = {
    run,
    request_receipts: [{ request_id: RUN_ID, operation: "evaluate_and_publish", status: "completed", response_digest: "d".repeat(64), attempt_count: 1, finished_at: DATA_TIME }],
    evaluations: [idea],
    write_counts: { evaluations: 2, suggestions: 1, publications: 1 },
    telegram_message_ids: [],
    incomplete_stages: [],
  };
  const system: SystemView = {
    product_version: "personal-stock-agent-web-v1",
    api_version: "v1",
    policy_version: 17,
    alert_mode: "shadow",
    latest_by_kind: { intraday: run },
    latest_publication_status: "suppressed",
    boundaries,
  };
  return (
    <AppShell dataTime={DATA_TIME} freshness={stale ? "stale" : "fresh"}>
      <Routes>
        <Route path="/" element={<TodayPage data={today} />} />
        <Route path="/portfolio" element={<PortfolioPage data={stale ? { ...portfolio, totals: { ...portfolio.totals, value: null, unrealized_amount: null }, holdings: holdings.map((item) => ({ ...item, price: null, value: null, unrealized_amount: null, unrealized_percent: null, freshness: "stale" as const })) } : portfolio} />} />
        <Route path="/ideas" element={<IdeasPage data={ideas} />} />
        <Route path="/companion" element={<CompanionPage data={companion} />} />
        <Route path="/alerts" element={<AlertsPage data={alerts} />} />
        <Route path="/runs" element={<RunsPage data={runs} />} />
        <Route path="/runs/:id" element={<RunDetailPage data={detail} />} />
        <Route path="/system" element={<SystemPage data={system} />} />
      </Routes>
    </AppShell>
  );
}

export function FixtureApp() {
  return <ThemeProvider><BrowserRouter><FixtureSurface /></BrowserRouter></ThemeProvider>;
}
