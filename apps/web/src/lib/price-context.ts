import type { MarketState } from "@stocks-agent/dashboard-contracts";

const sourceLabels: Record<string, string> = {
  finnhub: "Finnhub",
  "yahoo-chart": "Yahoo Finance",
  yahoo: "Yahoo Finance",
  "alpha-vantage": "Alpha Vantage",
};

export function priceSourceLabel(source: string | null): string {
  if (!source) return "Source unavailable";
  return sourceLabels[source.toLowerCase()] ?? source;
}

export function priceReceiptContext(
  marketState: MarketState,
  asOf: string | null,
  sources: readonly string[],
): string {
  const state = marketState === "as_of_close" ? "as of close" : marketState.replaceAll("_", " ");
  const parsedTimestamp = asOf ? new Date(asOf) : null;
  const timestamp = parsedTimestamp && !Number.isNaN(parsedTimestamp.valueOf())
    ? new Intl.DateTimeFormat("en-US", {
      dateStyle: "medium", timeStyle: "short", timeZone: "America/New_York",
    }).format(parsedTimestamp)
    : "time unavailable";
  const labels = [...new Set(sources.map((source) => priceSourceLabel(source)))];
  return `${state} · ${timestamp} ET · ${labels.join(", ") || "Source unavailable"}`;
}
