import { analyzeLongTermCompanion, qualifyCompanionRole } from "./companion.ts";
import type {
  AlternativeRelationship,
  LongTermCompanionRequest,
} from "./contracts.ts";
import type { AdjustedBar } from "./market-data.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
  }
}

function request(
  overrides: Partial<LongTermCompanionRequest> = {},
): LongTermCompanionRequest {
  return {
    baseline_ticker: "VTI",
    companion_ticker: "VXUS",
    role: "diversifier",
    thesis: "Non-U.S. exposure adds a distinct geographic role.",
    risk_note:
      "Currency and foreign-market risks can cause long periods of lagging U.S. stocks.",
    evidence_ids: ["vxus-profile"],
    ...overrides,
  };
}

function bars(
  start: string,
  end: string,
  priceForMonth: (monthIndex: number) => number,
): AdjustedBar[] {
  const cursor = new Date(`${start}T12:00:00.000Z`);
  const last = new Date(`${end}T12:00:00.000Z`);
  const startYear = cursor.getUTCFullYear();
  const startMonth = cursor.getUTCMonth();
  const output: AdjustedBar[] = [];
  while (cursor <= last) {
    const day = cursor.getUTCDay();
    if (day !== 0 && day !== 6) {
      const monthIndex = (cursor.getUTCFullYear() - startYear) * 12 +
        cursor.getUTCMonth() - startMonth;
      const value = String(priceForMonth(monthIndex));
      output.push({
        date: cursor.toISOString().slice(0, 10),
        raw_close: value,
        adjusted_close: value,
        raw_high: value,
        raw_low: value,
        split_ratio: null,
      });
    }
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return output;
}

Deno.test("gateway-owned companion roles reject duplicate and mislabeled exposure", () => {
  const cases: Array<{
    ticker: string;
    role: LongTermCompanionRequest["role"];
    relationship: AlternativeRelationship;
    allowed: boolean;
    recurring: boolean;
  }> = [
    {
      ticker: "ITOT",
      role: "tilt",
      relationship: "tilt",
      allowed: false,
      recurring: false,
    },
    {
      ticker: "SCHB",
      role: "satellite",
      relationship: "satellite",
      allowed: false,
      recurring: false,
    },
    {
      ticker: "VT",
      role: "diversifier",
      relationship: "diversifier",
      allowed: false,
      recurring: false,
    },
    {
      ticker: "VOO",
      role: "tilt",
      relationship: "tilt",
      allowed: true,
      recurring: false,
    },
    {
      ticker: "SCHD",
      role: "tilt",
      relationship: "tilt",
      allowed: true,
      recurring: false,
    },
    {
      ticker: "VXUS",
      role: "diversifier",
      relationship: "diversifier",
      allowed: true,
      recurring: true,
    },
    {
      ticker: "MSFT",
      role: "satellite",
      relationship: "satellite",
      allowed: true,
      recurring: false,
    },
    {
      ticker: "MSFT",
      role: "diversifier",
      relationship: "diversifier",
      allowed: false,
      recurring: false,
    },
  ];
  for (const item of cases) {
    const result = qualifyCompanionRole(
      request({ companion_ticker: item.ticker, role: item.role }),
      item.relationship,
    );
    assertEquals(result.allowed, item.allowed);
    assertEquals(result.recurring_plan_review_eligible, item.recurring);
  }
});

Deno.test("long-term companion computes complete synchronized horizons and rolling contribution history", () => {
  const history = bars(
    "2016-09-01",
    "2026-09-01",
    (monthIndex) => monthIndex % 2 === 0 ? 100 : 200,
  );
  const policy = qualifyCompanionRole(request(), "diversifier");
  const result = analyzeLongTermCompanion(
    request(),
    history,
    history,
    policy,
  );

  assertEquals(result.qualification_status, "qualified");
  assertEquals(result.recurring_plan_review_eligible, true);
  assertEquals(result.horizons.map((row) => row.years), [3, 5, 10]);
  for (const row of result.horizons) {
    assertEquals(row.baseline_annualized_return_pct, "0");
    assertEquals(row.companion_annualized_return_pct, "0");
    assertEquals(row.baseline_max_drawdown_pct, "50");
    assertEquals(row.companion_max_drawdown_pct, "50");
    assertEquals(row.daily_return_correlation, "1");
  }
  assertEquals(result.rolling_one_year, {
    monthly_contribution_usd: "100",
    total_contributed_usd: "1200",
    sample_windows: 110,
    weak_ending_value_usd: "900",
    middle_ending_value_usd: "900",
    strong_ending_value_usd: "1800",
  });
});

Deno.test("rolling contribution windows skip a missing calendar month", () => {
  const complete = bars(
    "2016-09-01",
    "2026-09-01",
    (monthIndex) => monthIndex % 2 === 0 ? 100 : 200,
  );
  const missingJune2022 = complete.filter((bar) =>
    !bar.date.startsWith("2022-06")
  );
  const result = analyzeLongTermCompanion(
    request(),
    missingJune2022,
    missingJune2022,
    qualifyCompanionRole(request(), "diversifier"),
  );

  assertEquals(result.qualification_status, "qualified");
  assertEquals(result.rolling_one_year?.sample_windows, 98);
});

Deno.test("partial history emits only supported horizons with nonzero paired metrics", () => {
  const baseline = bars(
    "2021-09-01",
    "2026-09-01",
    (monthIndex) => 100 + monthIndex,
  );
  const companion = bars(
    "2021-09-01",
    "2026-09-01",
    (monthIndex) => 200 - monthIndex,
  );
  const result = analyzeLongTermCompanion(
    request(),
    baseline,
    companion,
    qualifyCompanionRole(request(), "diversifier"),
  );

  assertEquals(result.qualification_status, "qualified");
  assertEquals(result.horizons.map((row) => row.years), [3, 5]);
  for (const row of result.horizons) {
    assert(
      Number(row.baseline_annualized_return_pct) > 0,
      "rising baseline should have positive annualized history",
    );
    assert(
      Number(row.companion_annualized_return_pct) < 0,
      "falling companion should have negative annualized history",
    );
    assert(
      Number(row.daily_return_correlation) < 0,
      "opposed monthly movements should not be reported as perfectly correlated",
    );
  }
});

Deno.test("long-term companion fails closed without three years or valid policy", () => {
  const short = bars("2025-01-01", "2026-09-01", () => 100);
  const insufficient = analyzeLongTermCompanion(
    request(),
    short,
    short,
    qualifyCompanionRole(request(), "diversifier"),
  );
  assertEquals(insufficient.qualification_status, "insufficient");
  assertEquals(insufficient.horizons, []);
  assertEquals(insufficient.rolling_one_year, null);

  const duplicate = request({ companion_ticker: "ITOT", role: "tilt" });
  const rejected = analyzeLongTermCompanion(
    duplicate,
    short,
    short,
    qualifyCompanionRole(duplicate, "tilt"),
  );
  assertEquals(rejected.qualification_status, "insufficient");
  assert(
    rejected.qualification_reason.includes("substitute"),
    "duplicate-core rejection reason was lost",
  );
});
