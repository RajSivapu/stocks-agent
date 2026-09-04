import type { Phase, VerifiedQuote } from "./contracts.ts";

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function localParts(
  instant: Date,
): { date: string; hour: number; minute: number; weekday: string } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    weekday: "short",
  }).formatToParts(instant);
  const get = (kind: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === kind)?.value ?? "";
  return {
    date: `${get("year")}-${get("month")}-${get("day")}`,
    hour: Number(get("hour")),
    minute: Number(get("minute")),
    weekday: get("weekday"),
  };
}

function addDays(date: string, days: number): string {
  const instant = new Date(`${date}T12:00:00.000Z`);
  instant.setUTCDate(instant.getUTCDate() + days);
  return instant.toISOString().slice(0, 10);
}

function isWeekend(date: string): boolean {
  const day = new Date(`${date}T12:00:00.000Z`).getUTCDay();
  return day === 0 || day === 6;
}

function previousSession(date: string, holidays: readonly string[]): string {
  let cursor = addDays(date, -1);
  for (let count = 0; count < 10; count += 1) {
    if (!isWeekend(cursor) && !isNyseHoliday(cursor, holidays)) return cursor;
    cursor = addDays(cursor, -1);
  }
  throw new Error("calendar has no prior session in bounded window");
}

function quoteLocalDate(quote: VerifiedQuote): string | null {
  const instant = new Date(quote.as_of);
  return Number.isNaN(instant.valueOf()) ? null : localParts(instant).date;
}

export function isNyseHoliday(
  localDate: string,
  holidays: readonly string[],
): boolean {
  if (!DATE_PATTERN.test(localDate) || isWeekend(localDate)) return false;
  return holidays.includes(localDate);
}

export function isFirstNyseSessionOfMonth(
  localDate: string,
  holidays: readonly string[],
): boolean {
  if (!DATE_PATTERN.test(localDate)) return false;
  const instant = new Date(`${localDate}T12:00:00.000Z`);
  if (
    Number.isNaN(instant.valueOf()) ||
    instant.toISOString().slice(0, 10) !== localDate ||
    isWeekend(localDate) ||
    isNyseHoliday(localDate, holidays)
  ) return false;
  const month = localDate.slice(0, 7);
  let cursor = `${month}-01`;
  for (let count = 0; count < 31 && cursor.slice(0, 7) === month; count += 1) {
    if (!isWeekend(cursor) && !isNyseHoliday(cursor, holidays)) {
      return cursor === localDate;
    }
    cursor = addDays(cursor, 1);
  }
  return false;
}

export function isRegularSession(
  now: Date,
  holidays: readonly string[],
): boolean {
  const local = localParts(now);
  const minutes = local.hour * 60 + local.minute;
  return !isWeekend(local.date) && !isNyseHoliday(local.date, holidays) &&
    minutes >= 9 * 60 + 30 && minutes < 16 * 60;
}

export function ownerLocalDate(now: Date): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function quoteAllowedForPhase(
  phase: Phase,
  quote: VerifiedQuote,
  now: Date,
  holidays: readonly string[],
  maxAgeMinutes: number,
): boolean {
  if (!Number.isInteger(maxAgeMinutes) || maxAgeMinutes <= 0) return false;
  if (Number.isNaN(now.valueOf())) return false;
  const quoteInstant = new Date(quote.as_of);
  if (Number.isNaN(quoteInstant.valueOf()) || quoteInstant > now) return false;
  const local = localParts(now);
  const quoteDate = quoteLocalDate(quote);
  if (quoteDate === null) return false;

  if (
    phase !== "on-demand" &&
    (isWeekend(local.date) || isNyseHoliday(local.date, holidays))
  ) return false;

  const regular = isRegularSession(now, holidays);
  if (phase === "intraday" || (phase === "on-demand" && regular)) {
    const ageMinutes = (now.valueOf() - quoteInstant.valueOf()) / 60_000;
    return quote.market_state === "REGULAR" && ageMinutes >= 0 &&
      ageMinutes <= maxAgeMinutes;
  }

  if (phase === "pre-market") {
    return quoteDate === previousSession(local.date, holidays);
  }

  const minutes = local.hour * 60 + local.minute;
  const latestCompleted =
    !isWeekend(local.date) && !isNyseHoliday(local.date, holidays) &&
      minutes >= 16 * 60
      ? local.date
      : previousSession(local.date, holidays);
  return quote.market_state !== "REGULAR" && quoteDate === latestCompleted;
}
