import { Link } from "react-router-dom";

import type { CompanionView, IdeasView, PortfolioView, ReportsView, TodayView } from "@stocks-agent/dashboard-contracts";
import type { ResourceState } from "../../api/useDashboardResource";
import { AsyncView } from "../../components/AsyncView";
import { splitIdeaVersions } from "../ideas/ideaVersions";
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
  return <details className="section-block disclosure-block companion-disclosure"><summary><h2>Companion review</h2></summary><div className="disclosure-content"><div className="section-heading"><p className="eyebrow">Long-term research</p><span className="badge">{data.status}</span></div><p className="large-copy">{data.baseline_ticker ?? "Core"} + {data.companion_ticker ?? "research candidate"}: {data.thesis ?? "No qualified structured companion proposal is available."}</p><p><strong>Risk:</strong> {data.risk_note ?? "No structured risk note was persisted."}</p><p className="inline-warning">Current plan remains unchanged. Any recurring-plan change requires your explicit confirmation.</p><p className="muted">{data.disclaimer}</p></div></details>;
}

function LatestIdeasSummary({ data }: { data: IdeasView }) {
  const ideas = splitIdeaVersions(data.ideas).current.slice(0, 3);
  return <section className="section-block"><div className="section-heading"><div><p className="eyebrow">Newest reviewed setups</p><h2>Latest ideas</h2></div><Link className="section-link" to="/ideas">View all ideas</Link></div>{ideas.length === 0 ? <p className="empty-copy">No current ideas are available.</p> : <div className="card-grid overview-idea-grid">{ideas.map((idea) => <article className="card" key={idea.id}><div className="card-heading"><p className="ticker">{idea.ticker}</p><span className={`badge badge-${idea.policy_status}`}>{idea.final_action ?? "No action"}</span></div><p>{idea.decisive_factor ?? "No decisive factor recorded."}</p><p className="muted">Invalidation: {idea.invalidation ?? "not recorded"}</p></article>)}</div>}</section>;
}

function LatestReportSummary({ data }: { data: ReportsView }) {
  const latest = [...data.reports].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))[0];
  return <section className="section-block"><div className="section-heading"><div><p className="eyebrow">Latest published record</p><h2>Latest report</h2></div><Link className="section-link" to="/reports">View all reports</Link></div>{latest ? <article className="report-highlight"><p className="card-kicker">{latest.kind} · {latest.market_date}</p><h3><Link to={`/reports/${latest.id}`}>{latest.title}</Link></h3><p>{latest.summary}</p></article> : <p className="empty-copy">No scheduled report receipt is available yet.</p>}</section>;
}

export function PortfolioPage({ data, overview, companion, ideas, reports, overviewState, companionState, ideasState, reportsState }: { data: PortfolioView; overview?: TodayView; companion?: CompanionView | null; ideas?: IdeasView | null; reports?: ReportsView | null; overviewState?: ResourceState<TodayView>; companionState?: ResourceState<CompanionView>; ideasState?: ResourceState<IdeasView>; reportsState?: ResourceState<ReportsView> }) {
  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Today and recorded positions</p><h1>Overview</h1><p>Your attention items, portfolio, current suggestions, and newest report in one place.</p></header>
      {overviewState ? <AsyncView state={overviewState}>{(value) => <TodaySummary data={value} />}</AsyncView> : overview ? <TodaySummary data={overview} /> : null}
      <section className="metric-grid"><div><span>Cost basis</span><strong>{dollars(data.totals.cost_basis)}</strong></div><div><span>Supported market value</span><strong>{dollars(data.totals.value)}</strong></div><div><span>Unrealized</span><strong>{dollars(data.totals.unrealized_amount)}</strong></div></section>
      {data.totals.value === null && <p className="inline-warning">Market value withheld because one or more required price receipts are missing or stale.</p>}
      <section className="section-block"><h2>Holdings</h2>{data.holdings.length === 0 ? <p className="empty-copy">No recorded holdings.</p> : <div aria-label="Holdings table" className="table-scroll" role="region" tabIndex={0}><table><thead><tr><th>Ticker</th><th>Shares</th><th>Average</th><th>Price</th><th>Value</th><th>Risk levels</th></tr></thead><tbody>{data.holdings.map((item) => <tr key={item.ticker}><th>{item.ticker}<small>{item.bucket}</small></th><td>{item.shares}</td><td>{dollars(item.average_cost)}</td><td>{dollars(item.price)}<small>{priceReceiptContext(item.market_state, item.price_as_of, item.price_source ? [item.price_source] : [])}</small></td><td>{dollars(item.value)}</td><td>Stop {dollars(item.stop)}<br />Target {dollars(item.target)}</td></tr>)}</tbody></table></div>}</section>
      <section className="section-block"><div className="section-heading"><div><p className="eyebrow">Owner-confirmed</p><h2>Recurring investment reminders</h2></div></div><p className="muted">Reminder only. The agent cannot place or schedule brokerage orders.</p>{data.plans.length === 0 ? <p className="empty-copy">No active recurring reminders.</p> : <div className="card-grid">{data.plans.map((plan) => <article className="card" key={plan.id}><p className="ticker">{plan.ticker}</p><strong>{dollars(plan.amount)} monthly</strong><p>Next due {plan.next_due_on}</p></article>)}</div>}</section>
      {ideasState ? <AsyncView state={ideasState}>{(value) => <LatestIdeasSummary data={value} />}</AsyncView> : ideas ? <LatestIdeasSummary data={ideas} /> : null}
      {reportsState ? <AsyncView state={reportsState}>{(value) => <LatestReportSummary data={value} />}</AsyncView> : reports ? <LatestReportSummary data={reports} /> : null}
      <details className="section-block disclosure-block transaction-disclosure"><summary><h2>Recent transactions</h2></summary><div className="disclosure-content">{data.transactions.length === 0 ? <p className="empty-copy">No recorded transactions in this view.</p> : <div aria-label="Recent transactions table" className="table-scroll" role="region" tabIndex={0}><table><thead><tr><th>Date</th><th>Ticker</th><th>Side</th><th>Quantity</th><th>Price</th></tr></thead><tbody>{data.transactions.map((item) => <tr key={item.id}><td>{item.executed_on ?? item.timestamp}</td><th>{item.ticker}</th><td>{item.side}</td><td>{item.quantity}</td><td>{dollars(item.price)}</td></tr>)}</tbody></table></div>}</div></details>
      {companionState ? <AsyncView state={companionState}>{(value) => <CompanionSummary data={value} />}</AsyncView> : companion ? <CompanionSummary data={companion} /> : <section className="section-block"><div className="section-heading"><div><p className="eyebrow">Long-term research</p><h2>Companion review</h2></div></div><p className="empty-copy">Structured companion context is unavailable for this view.</p></section>}
      <details className="metadata-disclosure"><summary>Evidence metadata</summary><p className="muted">Wider portfolio comparisons require a reviewed structured research receipt. Latest intelligence run: {data.latest_intelligence_run_id ?? "unavailable"}.</p></details>
    </div>
  );
}
