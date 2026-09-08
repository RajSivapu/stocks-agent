import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import type { IntelligenceView } from "@stocks-agent/dashboard-contracts";

import { IntelligencePage } from "./IntelligencePage";

const fixture: IntelligenceView = {
  intelligence_version: 2,
  run_id: "7d834dbd-75bb-4313-931f-09732f003932",
  data_as_of: "2026-09-04T13:30:00.000Z",
  themes: [{ theme_id: "grid_modernization", episode_id: "11111111-1111-4111-8111-111111111111", revision_id: "22222222-2222-4222-8222-222222222222", revision: 2, mechanism: "Grid modernization awards", subject: "United States transmission buildout", jurisdiction: "US", state: "active", first_seen: "2026-09-01T12:00:00.000Z", last_seen: "2026-09-04T12:00:00.000Z", next_review_at: "2026-09-08T12:00:00.000Z", expires_at: "2026-09-30T12:00:00.000Z", adverse_evidence_count: 1, missing_questions: ["Is the contract funded?"], invalidation_conditions: ["Program cancellation"] }],
  companies: [{ company_id: "CIK:0000000001", name: "<img src=x onerror=alert(1)>", ticker: "ACME", outside_watchlist: true, relationship_paths: ["Program to supplier"], evidence_ids: ["33333333-3333-4333-8333-333333333333"], missing_inputs: ["Current filing"] }],
  evidence: [{ evidence_id: "33333333-3333-4333-8333-333333333333", label: "Unsafe source", url: "javascript:alert(1)", passage: "A bounded adverse primary passage.", role: "opposing", retrieved_at: "2026-09-04T13:00:00.000Z" }],
  source_health: [{ provider: "sec_edgar", status: "partial", retrieved_at: "2026-09-04T13:00:00.000Z", accepted_count: 2, dropped_count: 1 }],
  coverage: { mode: "bounded", complete_market_coverage: false }, reference: { state: "healthy", manifest_id: "44444444-4444-4444-8444-444444444444" },
  scope: { research_only: true, market_wide: true }, backlog: { available: 4, returned: 1, deferred: 3, byte_truncated: true },
  omissions: ["Current validation is required."], boundaries: { research_only: true, execution_disabled: true, valuation_unavailable: true },
};

it("leads with revisions and outside-watchlist research without qualification language", () => {
  render(<IntelligencePage data={fixture} />);
  expect(screen.getByRole("heading", { name: "What changed" })).toBeVisible();
  expect(screen.getByText(/revised · revision 2/i)).toBeVisible();
  expect(screen.getByRole("heading", { name: "Companies to investigate" })).toBeVisible();
  expect(screen.getByText(/no qualification or buy signal/i)).toBeVisible();
  expect(screen.queryByText(/qualified relationship/i)).not.toBeInTheDocument();
});

it("keeps diagnostics in keyboard-operable native details and exposes textual adverse state", () => {
  render(<IntelligencePage data={fixture} />);
  const summary = screen.getByText("Evidence and diagnostics").closest("summary")!;
  expect(screen.getByText("All primary passages")).not.toBeVisible();
  fireEvent.click(summary);
  expect(screen.getByText("All primary passages")).toBeVisible();
  expect(screen.getAllByText(/adverse evidence/i).length).toBeGreaterThan(0);
  expect(screen.getByText(/3 deferred/i)).toBeVisible();
});

it("renders injection text inert and unsafe URLs without links", () => {
  const { container } = render(<IntelligencePage data={fixture} />);
  expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeVisible();
  expect(container.querySelector("img")).not.toBeInTheDocument();
  expect(screen.getAllByText("Unsafe source").length).toBeGreaterThan(0);
  expect(screen.queryByRole("link", { name: "Unsafe source" })).not.toBeInTheDocument();
});

it("states the bounded v2 empty state", () => {
  render(<IntelligencePage data={{ ...fixture, themes: [], companies: [], evidence: [], source_health: [] }} />);
  expect(screen.getByRole("heading", { name: /no validated research memory/i })).toBeVisible();
  expect(screen.getByText(/no coverage or qualification claim/i)).toBeVisible();
});
