import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";

import type { ReportDetailView, ReportsView } from "@stocks-agent/dashboard-contracts";

import { ReportDetailPage } from "./ReportDetailPage";
import { ReportsPage } from "./ReportsPage";

const reportId = "7d834dbd-75bb-4313-931f-09732f003932";
const reports: ReportsView = {
  reports: [{ id: reportId, market_date: "2026-09-04", kind: "weekly", title: "Weekly owner report", summary: "Receipt-backed weekly summary.", report_hash: "a".repeat(64), created_at: "2026-09-04T13:30:00.000Z" }],
  next_cursor: null,
};

it("links to exact immutable report versions", () => {
  render(<MemoryRouter><ReportsPage data={reports} /></MemoryRouter>);
  expect(screen.getByRole("link", { name: /weekly owner report/i })).toHaveAttribute("href", `/reports/${reportId}`);
  expect(screen.getByText(/immutable version/i)).toBeVisible();
  expect(screen.getByText("a".repeat(64))).toBeVisible();
});

it("renders an explicit empty report state", () => {
  render(<MemoryRouter><ReportsPage data={{ reports: [], next_cursor: null }} /></MemoryRouter>);
  expect(screen.getByRole("heading", { name: /no report versions/i })).toBeVisible();
});

it("shows exact publication receipt states without upgrading Telegram acceptance", () => {
  const detail: ReportDetailView = {
    ...reports.reports[0],
    sections: [{ heading: "Market", body: "<script>alert(1)</script>" }],
    sources: [{ label: "Unsafe source", url: "data:text/html,unsafe" }],
    publication: [{ request_id: "request-1", status: "accepted_by_telegram", gateway_status: "completed", attempt_count: 1, at: "2026-09-04T13:31:00.000Z", telegram_message_ids: [42] }],
  };
  const { container } = render(<MemoryRouter><ReportDetailPage data={detail} /></MemoryRouter>);
  expect(screen.getByText(/accepted by telegram/i)).toBeVisible();
  expect(screen.queryByText(/^delivered$/i)).not.toBeInTheDocument();
  expect(screen.getByText("<script>alert(1)</script>")).toBeVisible();
  expect(container.querySelector("script")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Unsafe source" })).not.toBeInTheDocument();
});
