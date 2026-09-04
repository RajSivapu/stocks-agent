import { describe, expect, it } from "vitest";

import {
  type IntelligenceView,
  parseDashboardEnvelope,
  parseDashboardErrorEnvelope,
  type ReportDetailView,
  type ReportsView,
} from "./index";

const base = {
  contract_version: 1,
  request_id: "11111111-1111-4111-8111-111111111111",
  generated_at: "2026-09-03T20:00:00.000Z",
  data_as_of: "2026-09-03T19:55:00.000Z",
  freshness: "fresh",
  market_state: "regular",
  data: { owner_only: true },
};

describe("dashboard response contracts", () => {
  it("accepts a complete version-one success envelope", () => {
    expect(parseDashboardEnvelope(base)).toEqual(base);
  });

  it("rejects unknown versions and missing freshness", () => {
    expect(() => parseDashboardEnvelope({ ...base, contract_version: 2 })).toThrow(
      "contract_version",
    );
    const { freshness: _freshness, ...missingFreshness } = base;
    expect(() => parseDashboardEnvelope(missingFreshness)).toThrow("freshness");
  });

  it("rejects malformed timestamps and unbounded request identifiers", () => {
    expect(() => parseDashboardEnvelope({ ...base, generated_at: "today" })).toThrow(
      "generated_at",
    );
    expect(() => parseDashboardEnvelope({ ...base, request_id: "x".repeat(200) })).toThrow(
      "request_id",
    );
  });

  it("accepts only the bounded public error vocabulary", () => {
    const error = {
      contract_version: 1,
      request_id: "22222222-2222-4222-8222-222222222222",
      error: {
        code: "owner_only",
        message: "This dashboard is restricted to its owner.",
      },
    };
    expect(parseDashboardErrorEnvelope(error)).toEqual(error);
    expect(() => parseDashboardErrorEnvelope({
      ...error,
      error: { code: "postgres_error", message: "relation missing" },
    })).toThrow("error.code");
  });

  it("parses intelligence coverage without raw provider payloads", () => {
    const fixture = {
      ...base,
      data: {
        run_id: "7d834dbd-75bb-4313-931f-09732f003932",
        data_as_of: "2026-09-03T19:55:00.000Z",
        themes: [], events: [], candidates: [], limitations: [],
        sources: [{ provider: "gdelt", status: "complete", retrieved_at: "2026-09-03T19:55:00.000Z", accepted_count: 3, dropped_count: 0 }],
      },
    };
    const view: IntelligenceView = parseDashboardEnvelope<IntelligenceView>(fixture).data;
    expect(view.sources[0]).toEqual(expect.objectContaining({ provider: "gdelt", status: "complete" }));
    expect(JSON.stringify(view)).not.toContain("raw_payload");
  });

  it("exports bounded report list and detail contracts", () => {
    const reports: ReportsView = { reports: [], next_cursor: null };
    const detail: ReportDetailView = {
      id: "7d834dbd-75bb-4313-931f-09732f003932", market_date: "2026-09-03",
      kind: "weekly", title: "Weekly review", summary: "Bounded summary.",
      report_hash: "a".repeat(64), created_at: "2026-09-03T20:00:00.000Z",
      sections: [], sources: [], publication: [],
    };
    expect(reports.next_cursor).toBeNull();
    expect(detail.sections).toEqual([]);
  });
});
