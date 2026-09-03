export type ScheduledPhase = "pre-market" | "intraday" | "post-market";

export type MarketDay = {
  marketDate: string;
  status: "open" | "holiday";
  earlyClose: boolean;
};

export type RunSlot = {
  marketDate: string;
  phase: ScheduledPhase;
  dueAt: string;
  windowEndsAt: string;
  holiday: boolean;
};

const DATE = /^\d{4}-\d{2}-\d{2}$/;

function validDate(value: string): boolean {
  if (!DATE.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function localParts(instant: Date, timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(instant);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value ?? "0");
  return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour"), minute: get("minute"), second: get("second") };
}

export function zonedDateTime(
  calendarDate: string,
  hour: number,
  minute: number,
  timeZone = "America/New_York",
): Date {
  if (!validDate(calendarDate) || !Number.isInteger(hour) || hour < 0 || hour > 23 ||
    !Number.isInteger(minute) || minute < 0 || minute > 59) throw new Error("invalid market wall time");
  const [year, month, day] = calendarDate.split("-").map(Number);
  const target = Date.UTC(year, month - 1, day, hour, minute, 0);
  let guess = target;
  for (let pass = 0; pass < 4; pass += 1) {
    const local = localParts(new Date(guess), timeZone);
    const observed = Date.UTC(local.year, local.month - 1, local.day, local.hour, local.minute, local.second);
    guess += target - observed;
  }
  const result = new Date(guess);
  const final = localParts(result, timeZone);
  if (final.year !== year || final.month !== month || final.day !== day || final.hour !== hour || final.minute !== minute) {
    throw new Error("market wall time is not representable");
  }
  return result;
}

function addMinutes(value: Date, minutes: number): Date {
  return new Date(value.valueOf() + minutes * 60_000);
}

function slot(marketDate: string, phase: ScheduledPhase, due: Date, holiday: boolean): RunSlot {
  return {
    marketDate,
    phase,
    dueAt: due.toISOString(),
    windowEndsAt: addMinutes(due, 60).toISOString(),
    holiday,
  };
}

export function runSlotsForMarketDay(day: MarketDay): RunSlot[] {
  if (!validDate(day.marketDate)) throw new Error("invalid market date");
  const weekday = new Date(`${day.marketDate}T12:00:00Z`).getUTCDay();
  if (weekday === 0 || weekday === 6) return [];
  const open = zonedDateTime(day.marketDate, 9, 30);
  const pre = slot(day.marketDate, "pre-market", addMinutes(open, -120), day.status === "holiday");
  if (day.status === "holiday") return [pre];
  const close = zonedDateTime(day.marketDate, day.earlyClose ? 13 : 16, 0);
  return [
    pre,
    slot(day.marketDate, "intraday", addMinutes(open, day.earlyClose ? 120 : 210), false),
    slot(day.marketDate, "post-market", addMinutes(close, 10), false),
  ];
}
