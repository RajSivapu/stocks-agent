import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import type { IntelligenceView } from "@stocks-agent/dashboard-contracts";

import { IntelligencePage } from "./IntelligencePage";

const partialFixture: IntelligenceView = {
  run_id: "7d834dbd-75bb-4313-931f-09732f003932",
  data_as_of: "2026-09-04T13:30:00.000Z",
  themes: [{ key: "grid-modernization", relationship_count: 2, evidence_count: 3 }],
  events: [{
    id: "event-1",
    type: "official-policy",
    title: "<img src=x onerror=alert(1)>",
    summary: "A bounded official-source event summary.",
    occurred_at: "2026-09-04T12:00:00.000Z",
    effective_at: null,
    materiality: "material",
    confidence: "high",
    sources: [{ label: "Unsafe source", url: "javascript:alert(1)" }],
  }],
  candidates: [{
    id: "relationship-1",
    event_id: "event-1",
    candidate_key: "supplier:fixture",
    ticker: "FIXTURE_ONLY_TICKER",
    rank: 1,
    total_score: "0.82",
    qualified: false,
    veto_reasons: ["missing-current-exposure"],
    sources: [],
  }],
  sources: [
    { provider: "sec", status: "complete", retrieved_at: "2026-09-04T13:00:00.000Z", accepted_count: 4, dropped_count: 1 },
    { provider: "gdelt", status: "partial", retrieved_at: null, accepted_count: 2, dropped_count: 3 },
    { provider: "finnhub", status: "unavailable", retrieved_at: null, accepted_count: 0, dropped_count: 0 },
  ],
  limitations: ["GDELT quota was bounded for this run."],
};

it("labels partial coverage, counts failures and drops, and never claims exhaustive news", () => {
  render(<IntelligencePage data={partialFixture} />);
  expect(screen.getByRole("heading", { name: /bounded source coverage/i })).toBeVisible();
  expect(screen.getByText(/partial coverage/i)).toBeVisible();
  expect(screen.getByText(/1 unavailable source/i)).toBeVisible();
  expect(screen.getByText(/4 dropped items/i)).toBeVisible();
  expect(screen.queryByText(/all news/i)).not.toBeInTheDocument();
});

it("renders hostile event text inert and unsafe source URLs as text", () => {
  const { container } = render(<IntelligencePage data={partialFixture} />);
  expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeVisible();
  expect(container.querySelector("img")).not.toBeInTheDocument();
  expect(screen.getByText("Unsafe source")).toBeVisible();
  expect(screen.queryByRole("link", { name: "Unsafe source" })).not.toBeInTheDocument();
});

it("states an explicit empty bounded-coverage limitation", () => {
  render(<IntelligencePage data={{ ...partialFixture, themes: [], events: [], candidates: [], sources: [], limitations: [] }} />);
  expect(screen.getByRole("heading", { name: /no intelligence receipt/i })).toBeVisible();
  expect(screen.getByText(/coverage is unavailable/i)).toBeVisible();
});
