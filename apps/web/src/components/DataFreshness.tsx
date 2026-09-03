import type { MarketQuote } from "../lib/dashboard";
import { dateTime } from "../lib/format";
import { StatusBadge } from "./StatusBadge";

export function DataFreshness({ quote }: { quote: MarketQuote | null }) {
  if (!quote) return <span className="freshness"><StatusBadge status="unavailable" /> No quote</span>;
  return (
    <span className="freshness">
      <StatusBadge status={quote.status} />
      <span>As of <time dateTime={quote.asOf}>{dateTime(quote.asOf)}</time></span>
      <span className="sr-only">Source {quote.provider}</span>
    </span>
  );
}
