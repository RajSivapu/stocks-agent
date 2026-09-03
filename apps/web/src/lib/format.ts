const DECIMAL_RE = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;

export function decimalNumber(value: string | null): number | null {
  if (value === null || !DECIMAL_RE.test(value)) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && Math.abs(parsed) <= Number.MAX_SAFE_INTEGER
    ? parsed
    : null;
}

const usdFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function usd(value: number | string | null): string {
  const parsed = typeof value === "number" ? value : decimalNumber(value);
  return parsed === null || !Number.isFinite(parsed) ? "Unavailable" : usdFormatter.format(parsed);
}

export function signedUsd(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "Unavailable";
  return `${value >= 0 ? "+" : "−"}${usdFormatter.format(Math.abs(value))}`;
}

export function quantity(value: string | null): string {
  if (value === null || !DECIMAL_RE.test(value)) return "Unavailable";
  const [whole, fractional = ""] = value.split(".");
  const compact = fractional.replace(/0+$/, "");
  const canonicalWhole = whole ?? "0";
  return compact ? `${canonicalWhole}.${compact}` : canonicalWhole;
}

export function percent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "Unavailable";
  return `${value.toFixed(1)}%`;
}

export function dateTime(value: string | null): string {
  if (!value) return "Unavailable";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return "Unavailable";
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

export function dateOnly(value: string | null): string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return "Unavailable";
  const parsed = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(parsed.valueOf())) return "Unavailable";
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeZone: "UTC" }).format(parsed);
}

export function currentMarketDate(now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

export function titleCase(value: string): string {
  return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
