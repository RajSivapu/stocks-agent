import { useCallback } from "react";
import type { CommandClient } from "../../lib/app-api";
import type { DashboardRepository, MarketQuote } from "../../lib/dashboard";
import { decimalNumber, percent, quantity, signedUsd, titleCase, usd } from "../../lib/format";
import { useRepositoryData } from "../../lib/useRepositoryData";
import { DataFreshness } from "../../components/DataFreshness";
import { RecordActionPanel } from "./RecordActionPanel";

function quoteMap(quotes: MarketQuote[]): Map<string, MarketQuote> {
  return new Map(quotes.map((quote) => [quote.ticker, quote]));
}

export function PortfolioScreen({ repository, commands }: {
  repository: DashboardRepository;
  commands: CommandClient;
}) {
  const loader = useCallback(() => repository.loadPortfolio(), [repository]);
  const { state, reload } = useRepositoryData(loader);
  if (state.kind === "loading") {
    return <section className="workspace-card" aria-busy="true"><div role="status" aria-label="Loading portfolio" className="skeleton-stack">Loading portfolio…</div></section>;
  }
  if (state.kind === "error") {
    return <section className="workspace-card"><h1>Portfolio</h1><p role="alert">We couldn’t load your portfolio. No local values were substituted.</p><button type="button" className="secondary-button" onClick={() => { void reload(); }}>Try again</button></section>;
  }

  const quotes = quoteMap(state.data.quotes);
  const positions = state.data.holdings.map((holding) => {
    const quote = quotes.get(holding.ticker) ?? null;
    const shares = decimalNumber(holding.shares);
    const averageCost = decimalNumber(holding.avgCost);
    const price = decimalNumber(quote?.price ?? null);
    const value = shares !== null && price !== null ? shares * price : null;
    const pnl = shares !== null && price !== null && averageCost !== null
      ? shares * (price - averageCost)
      : null;
    return { holding, quote, value, pnl };
  });
  const total = positions.reduce((sum, position) => sum + (position.value ?? 0), 0);
  const unsafeQuotes = positions.filter(({ quote }) => quote?.status !== "fresh" || quote.alertsSuppressed);

  return (
    <div className="feature-stack">
      <section className="workspace-card">
        <div className="section-heading">
          <div><p className="eyebrow">Server-sourced ledger</p><h1>Portfolio</h1></div>
          <div className="metric"><span>Estimated value</span><strong>{usd(total)}</strong></div>
        </div>
        <p className="muted">Values use the latest labeled quote. Your immutable transaction ledger remains the source of shares and cost basis.</p>
        {unsafeQuotes.length > 0 && <div className="safety-banner" role="status">
          <strong>Price caution</strong>
          <span>Delayed, stale, or conflicting prices are labeled below. A price conflict suppresses level-based alerts.</span>
        </div>}
        {positions.length === 0 ? <div className="empty-state"><h2>No holdings recorded</h2><p>Record a completed broker fill below. Stock Agent will not place an order.</p></div> : (
          <div className="table-scroll"><table className="data-table portfolio-table">
            <caption className="sr-only">Current holdings with source freshness and risk levels</caption>
            <thead><tr><th scope="col">Holding</th><th scope="col">Shares</th><th scope="col">Average cost</th><th scope="col">Latest price</th><th scope="col">Value</th><th scope="col">P&amp;L</th><th scope="col">Allocation</th><th scope="col">Risk levels</th></tr></thead>
            <tbody>{positions.map(({ holding, quote, value, pnl }) => (
              <tr key={holding.ticker}>
                <th scope="row"><strong>{holding.ticker}</strong><span className="cell-subtitle">{titleCase(holding.bucket)}</span></th>
                <td data-label="Shares">{quantity(holding.shares)}</td>
                <td data-label="Average cost">{usd(holding.avgCost)}</td>
                <td data-label="Latest price"><strong>{usd(quote?.price ?? null)}</strong><DataFreshness quote={quote} /></td>
                <td data-label="Value">{usd(value)}</td>
                <td data-label="P&L" className={pnl !== null && pnl < 0 ? "negative" : "positive"}>{signedUsd(pnl)}</td>
                <td data-label="Allocation">{percent(value === null || total === 0 ? null : value / total * 100)}</td>
                <td data-label="Risk levels"><span className="level">Stop {usd(holding.stop)}</span><span className="level">Target {usd(holding.target)}</span></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </section>

      <section className="workspace-card" aria-labelledby="plans-title">
        <div className="section-heading"><div><p className="eyebrow">Reminders only</p><h2 id="plans-title">Recurring investments</h2></div></div>
        {state.data.plans.filter((plan) => plan.active).length === 0 ? <p className="empty-copy">No active recurring reminders.</p> : (
          <ul className="plan-list">{state.data.plans.filter((plan) => plan.active).map((plan) => (
            <li key={plan.id}><div><strong>{plan.ticker}</strong><span>{usd(plan.amount)} monthly</span></div><div><span>Next record</span><strong>{plan.nextDueOn}</strong></div></li>
          ))}</ul>
        )}
      </section>
      <RecordActionPanel commands={commands} onApplied={reload} />
    </div>
  );
}
