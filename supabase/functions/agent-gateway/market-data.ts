import type { Phase } from "../../../packages/contracts/src/provider.ts";
import { parseFixed } from "../market-briefing-gateway/_shared/fixed-point.ts";

export { fetchAdjustedHistory, fetchVerifiedQuote } from "../market-briefing-gateway/_shared/market-data.ts";
export type { AdjustedBar, FetchLike } from "../market-briefing-gateway/_shared/market-data.ts";

export type QuoteIntegrityStatus = "fresh" | "delayed" | "stale" | "conflicting" | "unavailable";

export type QuoteObservation = {
  ticker: string;
  price: string;
  previousClose: string | null;
  sourceTimestamp: string;
  retrievedAt: string;
  session: string;
  provider: string;
  adjustmentStatus: "raw" | "adjusted" | "corporate_action_pending";
};

export type TradingSession = {
  marketDate: string;
  openAt: string;
  closeAt: string;
};

export type ServerQuote = {
  status: QuoteIntegrityStatus;
  quote: QuoteObservation | null;
  conflictBasisPoints: string | null;
};

function timestamp(value: string): number | null {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function conflictBasisPoints(left: string, right: string): bigint {
  const leftValue = parseFixed(left, 12);
  const rightValue = parseFixed(right, 12);
  const denominator = leftValue < rightValue ? leftValue : rightValue;
  if (denominator <= 0n) throw new Error("quote price must be positive");
  const difference = leftValue > rightValue ? leftValue - rightValue : rightValue - leftValue;
  return difference * 10_000n / denominator;
}

function phaseStatus(
  phase: Phase,
  quote: QuoteObservation,
  now: number,
  session: TradingSession,
  maxAgeMinutes: number,
): "fresh" | "delayed" | "stale" {
  const source = timestamp(quote.sourceTimestamp);
  const retrieved = timestamp(quote.retrievedAt);
  const open = timestamp(session.openAt);
  const close = timestamp(session.closeAt);
  if (source === null || retrieved === null || open === null || close === null ||
    open >= close || source > retrieved || retrieved > now + 5 * 60_000 || source > now) {
    return "stale";
  }
  const age = now - source;
  const freshAge = maxAgeMinutes * 60_000;
  if (phase === "intraday" || phase === "on-demand") {
    if (now < open || now > close || quote.session !== "REGULAR") return "stale";
  } else if (phase === "post-market") {
    if (now < close || source < open || source > close + 5 * 60_000 || quote.session === "REGULAR") {
      return "stale";
    }
  } else if (source >= open || quote.session === "REGULAR") {
    return "stale";
  }
  if (age <= freshAge) return "fresh";
  if (age <= freshAge * 3) return "delayed";
  return "stale";
}

export function assessServerQuote(
  phase: Phase,
  primary: QuoteObservation | null,
  independent: QuoteObservation | null,
  now: Date,
  session: TradingSession,
  maxAgeMinutes = 15,
  maxConflictBasisPoints = 100,
): ServerQuote {
  if (!primary) return { status: "unavailable", quote: null, conflictBasisPoints: null };
  if (!Number.isInteger(maxAgeMinutes) || maxAgeMinutes < 1 || maxAgeMinutes > 60 ||
    !Number.isInteger(maxConflictBasisPoints) || maxConflictBasisPoints < 1 || maxConflictBasisPoints > 1_000 ||
    Number.isNaN(now.valueOf())) {
    return { status: "stale", quote: primary, conflictBasisPoints: null };
  }
  let conflict: bigint | null = null;
  if (independent && independent.ticker === primary.ticker) {
    try {
      const independentAt = timestamp(independent.sourceTimestamp);
      if (independentAt !== null && Math.abs(now.valueOf() - independentAt) <= 30 * 60_000) {
        conflict = conflictBasisPoints(primary.price, independent.price);
        if (conflict > BigInt(maxConflictBasisPoints)) {
          return { status: "conflicting", quote: primary, conflictBasisPoints: conflict.toString() };
        }
      }
    } catch {
      return { status: "conflicting", quote: primary, conflictBasisPoints: null };
    }
  }
  try {
    return {
      status: phaseStatus(phase, primary, now.valueOf(), session, maxAgeMinutes),
      quote: primary,
      conflictBasisPoints: conflict?.toString() ?? null,
    };
  } catch {
    return { status: "stale", quote: primary, conflictBasisPoints: null };
  }
}

export function ownerVisibleQuotes(
  ownerTickers: readonly string[],
  rows: readonly QuoteObservation[],
): QuoteObservation[] {
  const allowed = new Set(ownerTickers.filter((ticker) => /^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$/.test(ticker)).slice(0, 60));
  const visible = new Map<string, QuoteObservation>();
  for (const row of rows) {
    if (allowed.has(row.ticker)) visible.set(row.ticker, structuredClone(row));
  }
  return [...visible.values()].sort((left, right) => left.ticker.localeCompare(right.ticker)).slice(0, 60);
}
