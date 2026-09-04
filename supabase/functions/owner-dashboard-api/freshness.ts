import type { Freshness, MarketState } from "../../../packages/dashboard-contracts/src/index.ts";

export interface MarketCalendar {
  holidays: readonly string[];
}

export interface FreshnessInput {
  kind: "price" | "brief" | "run";
  dataAsOf: string | null;
  sourceMarketState?: string | null;
  phase?: string | null;
  status?: string | null;
}

export interface FreshnessResult {
  freshness: Freshness;
  marketState: MarketState;
  dataAsOf: string | null;
}

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function localParts(instant: Date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(instant);
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return {
    date: `${value("year")}-${value("month")}-${value("day")}`,
    minutes: Number(value("hour")) * 60 + Number(value("minute")),
  };
}

function addDays(date: string, amount: number): string {
  const instant = new Date(`${date}T12:00:00.000Z`);
  instant.setUTCDate(instant.getUTCDate() + amount);
  return instant.toISOString().slice(0, 10);
}

function weekend(date: string): boolean {
  const day = new Date(`${date}T12:00:00.000Z`).getUTCDay();
  return day === 0 || day === 6;
}

function tradingDay(date: string, calendar: MarketCalendar): boolean {
  return DATE_PATTERN.test(date) && !weekend(date) && !calendar.holidays.includes(date);
}

const SCHEDULED_PHASES = ["pre-market", "intraday", "post-market"] as const;

function phaseRank(phase: string | null | undefined): number {
  return SCHEDULED_PHASES.indexOf(phase as typeof SCHEDULED_PHASES[number]);
}

function expectedPhase(now: Date, calendar: MarketCalendar): { date: string; minimumRank: number } {
  const local = localParts(now);
  if (!tradingDay(local.date, calendar)) {
    return { date: previousTradingDay(local.date, calendar), minimumRank: 2 };
  }
  if (local.minutes < 8 * 60) {
    return { date: previousTradingDay(local.date, calendar), minimumRank: 2 };
  }
  if (local.minutes < 13 * 60 + 30) return { date: local.date, minimumRank: 0 };
  if (local.minutes < 16 * 60 + 40) return { date: local.date, minimumRank: 1 };
  return { date: local.date, minimumRank: 2 };
}

function previousTradingDay(date: string, calendar: MarketCalendar): string {
  let cursor = addDays(date, -1);
  for (let count = 0; count < 12; count += 1) {
    if (tradingDay(cursor, calendar)) return cursor;
    cursor = addDays(cursor, -1);
  }
  return "";
}

function currentMarketState(now: Date, calendar: MarketCalendar): MarketState {
  const local = localParts(now);
  if (weekend(local.date)) return "closed";
  if (calendar.holidays.includes(local.date)) return "holiday";
  if (local.minutes < 9 * 60 + 30) return "pre_market";
  if (local.minutes < 16 * 60) return "regular";
  return "post_market";
}

function latestCompletedSession(now: Date, calendar: MarketCalendar): string {
  const local = localParts(now);
  if (tradingDay(local.date, calendar) && local.minutes >= 16 * 60) return local.date;
  return previousTradingDay(local.date, calendar);
}

function unavailable(): FreshnessResult {
  return { freshness: "unavailable", marketState: "unknown", dataAsOf: null };
}

export function classifyFreshness(
  input: FreshnessInput,
  now: Date,
  calendar: MarketCalendar,
): FreshnessResult {
  if (!input.dataAsOf || Number.isNaN(now.valueOf())) return unavailable();
  const instant = new Date(input.dataAsOf);
  if (Number.isNaN(instant.valueOf()) || instant > now) return unavailable();
  const canonical = instant.toISOString();
  const state = currentMarketState(now, calendar);

  if (input.kind === "price") {
    const sourceState = (input.sourceMarketState ?? "").toUpperCase();
    const sourceDate = localParts(instant).date;
    if (sourceState === "REGULAR") {
      const age = (now.valueOf() - instant.valueOf()) / 60_000;
      const sameSession = sourceDate === localParts(now).date;
      return {
        freshness: state === "regular" && sameSession && age <= 20 ? "fresh" : "stale",
        marketState: state,
        dataAsOf: canonical,
      };
    }
    if (sourceState !== "CLOSED") {
      return { freshness: "unavailable", marketState: state, dataAsOf: canonical };
    }
    const expectedClose = latestCompletedSession(now, calendar);
    const stillBeforeNextOpen = sourceDate === expectedClose && state !== "regular";
    return {
      freshness: stillBeforeNextOpen ? "fresh" : "stale",
      marketState: stillBeforeNextOpen ? "as_of_close" : state,
      dataAsOf: canonical,
    };
  }

  if (input.status && !["completed", "suppressed", "delivered"].includes(input.status)) {
    return { freshness: "partial", marketState: state, dataAsOf: canonical };
  }
  if (input.phase === "on-demand") {
    const ageDays = (now.valueOf() - instant.valueOf()) / 86_400_000;
    return { freshness: ageDays <= 30 ? "fresh" : "stale", marketState: state, dataAsOf: canonical };
  }
  if (input.phase === "weekly-audit") {
    const ageDays = (now.valueOf() - instant.valueOf()) / 86_400_000;
    return { freshness: ageDays <= 8 ? "fresh" : "stale", marketState: state, dataAsOf: canonical };
  }
  const expected = expectedPhase(now, calendar);
  const receiptDate = localParts(instant).date;
  const receiptRank = phaseRank(input.phase);
  return {
    freshness: receiptDate === expected.date && receiptRank >= expected.minimumRank ? "fresh" : "stale",
    marketState: state,
    dataAsOf: canonical,
  };
}

export const NYSE_HOLIDAYS_2026 = [
  "2026-01-01",
  "2026-01-19",
  "2026-02-16",
  "2026-04-03",
  "2026-05-25",
  "2026-06-19",
  "2026-07-03",
  "2026-09-07",
  "2026-11-26",
  "2026-12-25",
] as const;
