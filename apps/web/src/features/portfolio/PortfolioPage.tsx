import type { CompanionView, PortfolioView, TodayView } from "@stocks-agent/dashboard-contracts";
import type { ResourceState } from "../../api/useDashboardResource";
import { AsyncView } from "../../components/AsyncView";
import { priceReceiptContext } from "../../lib/price-context";

const dollars = (value: string | null) => value === null ? "—" : `$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

function primaryDestination(destination: string) {
  if (destination === "/companion" || destination === "/") return "/portfolio";
  if (destination === "/alerts" || destination.startsWith("/runs")) return "/system";
  return destination;
}

function TodaySummary({ data }: { data: TodayView }) {
  return <section className="section-block attention-section"><div className="section-heading"><div><p className="eyebrow">Today</p><h2>Needs attention</h2></div><span className="count-chip">{data.attention.length}</span></div>{data.attention.length === 0 ? <p className="empty-copy">No receipt-backed attention items right now.</p> : <div className="card-grid">{data.attention.map((item) => <article className={`card severity-${item.severity}`} key={item.id}><p className="card-kicker">{item.severity}</p><h3>{item.title}</h3><p>{item.detail}</p><a href={primaryDestination(item.destination)}>Review details</a></article>)}</div>}{data.market_summary && <div className="summary-strip"><strong>Recorded market context</strong><p>{data.market_summary}</p></div>}</section>;
}

function CompanionSummary({ data }: { data: CompanionView }) {
  return <section className="section-block"><div className="section-heading"><div><p className="eyebrow">Long-term research</p><h2>Companion review</h2></div><span className="badge">{data.status}</span></div><p className="large-copy">{data.baseline_ticker ?? "Core"} + {data.companion_ticker ?? "research candidate"}: {data.thesis ?? "No qualified structured companion proposal is available."}</p><p><strong>Risk:</strong> {data.risk_note ?? "No structured risk note was persisted."}</p><p className="inline-warning">Current plan remains unchanged. Any recurring-plan change requires your explicit confirmation.</p><p className="muted">{data.disclaimer}</p></section>;
}

export function PortfolioPage({ data, overview, companion, overviewState, companionState }: { data: PortfolioView; overview?: TodayView; companion?: CompanionView | null; overviewState?: ResourceState<TodayView>; companionState?: ResourceState<CompanionView> }) {
  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Today and recorded positions</p><h1>Portfolio</h1><p>Your daily owner overview, holdings, reminders, and long-term companion context—not a brokerage account.</p></header>
      {overviewState ? <AsyncView state={overviewState}>{(value) => <TodaySummary data={value} />}</AsyncView> : overview ? <TodaySummary data={overview} /> : null}
      <section className="metric-grid"><div><span>Cost basis</span><strong>{dollars(data.totals.cost_basis)}</strong></div><div><span>Supported market value</span><strong>{dollars(data.totals.value)}</strong></div><div><span>Unrealized</span><strong>{dollars(data.totals.unrealized_amount)}</strong></div></section>
      {data.totals.value === null && <p className="inline-warning">Market value withheld because one or more required price receipts are missing or stale.</p>}
      <section className="section-block"><h2>Holdings</h2>{data.holdings.length === 0 ? <p className="empty-copy">No recorded holdings.</p> : <div aria-label="Holdings table" className="table-scroll" role="region" tabIndex={0}><table><thead><tr><th>Ticker</th><th>Shares</th><th>Average</th><th>Price</th><th>Value</th><th>Risk levels</th></tr></thead><tbody>{data.holdings.map((item) => <tr key={item.ticker}><th>{item.ticker}<small>{item.bucket}</small></th><td>{item.shares}</td><td>{dollars(item.average_cost)}</td><td>{dollars(item.price)}<small>{priceReceiptContext(item.market_state, item.price_as_of, item.price_source ? [item.price_source] : [])}</small></td><td>{dollars(item.value)}</td><td>Stop {dollars(item.stop)}<br />Target {dollars(item.target)}</td></tr>)}</tbody></table></div>}</section>
      <section className="section-block"><div className="section-heading"><div><p className="eyebrow">Owner-confirmed</p><h2>Recurring investment reminders</h2></div></div><p className="muted">Reminder only. The agent cannot place or schedule brokerage orders.</p>{data.plans.length === 0 ? <p className="empty-copy">No active recurring reminders.</p> : <div className="card-grid">{data.plans.map((plan) => <article className="card" key={plan.id}><p className="ticker">{plan.ticker}</p><strong>{dollars(plan.amount)} monthly</strong><p>Next due {plan.next_due_on}</p></article>)}</div>}</section>
      <section className="section-block"><h2>Recent transactions</h2>{data.transactions.length === 0 ? <p className="empty-copy">No recorded transactions in this view.</p> : <div aria-label="Recent transactions table" className="table-scroll" role="region" tabIndex={0}><table><thead><tr><th>Date</th><th>Ticker</th><th>Side</th><th>Quantity</th><th>Price</th></tr></thead><tbody>{data.transactions.map((item) => <tr key={item.id}><td>{item.executed_on ?? item.timestamp}</td><th>{item.ticker}</th><td>{item.side}</td><td>{item.quantity}</td><td>{dollars(item.price)}</td></tr>)}</tbody></table></div>}</section>
      {companionState ? <AsyncView state={companionState}>{(value) => <CompanionSummary data={value} />}</AsyncView> : companion ? <CompanionSummary data={companion} /> : <section className="section-block"><div className="section-heading"><div><p className="eyebrow">Long-term research</p><h2>Companion review</h2></div></div><p className="empty-copy">Structured companion context is unavailable for this view.</p></section>}
      <p className="muted">Wider portfolio comparisons are unavailable unless a reviewed structured research receipt was persisted. Latest intelligence run: {data.latest_intelligence_run_id ?? "unavailable"}.</p>
    </div>
  );
}
