import { useCallback } from "react";
import type { DashboardRepository, MarketQuote } from "../../lib/dashboard";
import { currentMarketDate, dateOnly, dateTime, decimalNumber, usd } from "../../lib/format";
import { useRepositoryData } from "../../lib/useRepositoryData";
import { StatusBadge } from "../../components/StatusBadge";

function portfolioValue(holdings: { ticker: string; shares: string }[], quotes: MarketQuote[]) {
  const quoteByTicker = new Map(quotes.map((quote) => [quote.ticker, quote]));
  return holdings.reduce((total, holding) => {
    const shares = decimalNumber(holding.shares);
    const price = decimalNumber(quoteByTicker.get(holding.ticker)?.price ?? null);
    return total + (shares === null || price === null ? 0 : shares * price);
  }, 0);
}

export function TodayScreen({ repository }: { repository: DashboardRepository }) {
  const loader = useCallback(() => repository.loadToday(), [repository]);
  const { state, reload } = useRepositoryData(loader);
  if (state.kind === "loading") return <section className="workspace-card" aria-busy="true"><div role="status" aria-label="Loading today">Loading today…</div></section>;
  if (state.kind === "error") return <section className="workspace-card"><h1>Today</h1><p role="alert">Today’s verified data is unavailable. No prior advice is being shown as current.</p><button type="button" className="secondary-button" onClick={() => { void reload(); }}>Try again</button></section>;

  const latest = state.data.runs[0] ?? null;
  const currentRecommendations = latest?.status === "completed"
    ? state.data.recommendations.filter((item) => item.runId === latest.runId &&
      (item.validUntil === null || item.validUntil >= currentMarketDate()))
    : [];
  const unsafe = state.data.quotes.filter((quote) => quote.status !== "fresh" || quote.alertsSuppressed);
  const upcoming = state.data.plans.filter((plan) => plan.active).slice(0, 3);

  return (
    <div className="feature-stack">
      <section className="today-hero">
        <div><p className="eyebrow">Current market-day view</p><h1>Today</h1><p>Decision support, never trade execution.</p></div>
        <div className="hero-value"><span>Estimated portfolio value</span><strong>{usd(portfolioValue(state.data.holdings, state.data.quotes))}</strong><small>{state.data.holdings.length} recorded holdings</small></div>
      </section>
      <div className="today-grid">
        <section className="workspace-card" aria-labelledby="advice-title">
          <div className="section-heading"><div><p className="eyebrow">Actionability gate</p><h2 id="advice-title">Current analysis</h2></div>{latest && <StatusBadge status={latest.status} />}</div>
          {!latest ? <div className="empty-state"><h3>No run recorded today</h3><p>Your portfolio remains available; no recommendation is inferred.</p></div> : latest.status !== "completed" ? (
            <div className="safety-banner"><strong>Latest run is {latest.status}</strong><span>No failed or partial run is treated as current advice. There is no current actionable recommendation.</span></div>
          ) : currentRecommendations.length === 0 ? <div className="empty-state"><h3>No current actionable recommendation</h3><p>The completed run produced no still-valid action for this view.</p></div> : (
            <ul className="recommendation-list">{currentRecommendations.map((item) => <li key={item.id}><strong>{item.ticker}</strong><span>{item.action}</span><small>Evidence {dateTime(item.evidenceAsOf)}</small></li>)}</ul>
          )}
          {latest && <p className="receipt-meta">Run started {dateTime(latest.startedAt)} · Data {dateTime(latest.dataAsOf)}</p>}
        </section>
        <section className="workspace-card" aria-labelledby="health-title">
          <p className="eyebrow">Source health</p><h2 id="health-title">Quote safety</h2>
          {unsafe.length === 0 ? <p className="good-state">All available holding quotes are fresh and unsuppressed.</p> : <ul className="health-list">{unsafe.map((quote) => <li key={quote.ticker}><strong>{quote.ticker}</strong><StatusBadge status={quote.status} /><span>{quote.alertsSuppressed ? "Alerts suppressed" : "Use with freshness caution"}</span></li>)}</ul>}
        </section>
        <section className="workspace-card" aria-labelledby="reminders-title">
          <p className="eyebrow">Owner-entered plans</p><h2 id="reminders-title">Upcoming reminders</h2>
          {upcoming.length === 0 ? <p className="empty-copy">No active recurring reminders.</p> : <ul className="reminder-list">{upcoming.map((plan) => <li key={plan.id}><strong>{plan.ticker}</strong><span>{usd(plan.amount)}</span><time dateTime={plan.nextDueOn}>{dateOnly(plan.nextDueOn)}</time></li>)}</ul>}
        </section>
      </div>
    </div>
  );
}
