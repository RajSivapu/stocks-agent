import { describe, expect, it } from "vitest";

import {
  parseDashboardEnvelope,
  parseDashboardErrorEnvelope,
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
});
