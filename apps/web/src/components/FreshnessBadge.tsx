import type { Freshness } from "@stocks-agent/dashboard-contracts";

export function FreshnessBadge({ freshness }: { freshness: Freshness }) {
  const label = freshness === "fresh" ? "Receipt current" : freshness === "partial" ? "Partial receipt" : freshness === "stale" ? "Stale evidence" : "Unavailable";
  return <span className={`badge badge-${freshness}`}><span aria-hidden="true">●</span> {label}</span>;
}
