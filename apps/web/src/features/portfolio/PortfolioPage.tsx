import type { PortfolioView } from "@stocks-agent/dashboard-contracts";
import { priceReceiptContext } from "../../lib/price-context";

const dollars = (value: string | null) => value === null ? "—" : `$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

export function PortfolioPage({ data }: { data: PortfolioView }) {
  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Recorded positions</p><h1>Portfolio</h1><p>Holdings and reminders—not a brokerage account.</p></header>
      <section className="metric-grid"><div><span>Cost basis</span><strong>{dollars(data.totals.cost_basis)}</strong></div><div><span>Supported market value</span><strong>{dollars(data.totals.value)}</strong></div><div><span>Unrealized</span><strong>{dollars(data.totals.unrealized_amount)}</strong></div></section>
      {data.totals.value === null && <p className="inline-warning">Market value withheld because one or more required price receipts are missing or stale.</p>}
      <section className="section-block"><h2>Holdings</h2>{data.holdings.length === 0 ? <p className="empty-copy">No recorded holdings.</p> : <div className="table-scroll"><table><thead><tr><th>Ticker</th><th>Shares</th><th>Average</th><th>Price</th><th>Value</th><th>Risk levels</th></tr></thead><tbody>{data.holdings.map((item) => <tr key={item.ticker}><th>{item.ticker}<small>{item.bucket}</small></th><td>{item.shares}</td><td>{dollars(item.average_cost)}</td><td>{dollars(item.price)}<small>{priceReceiptContext(item.market_state, item.price_as_of, item.price_source ? [item.price_source] : [])}</small></td><td>{dollars(item.value)}</td><td>Stop {dollars(item.stop)}<br />Target {dollars(item.target)}</td></tr>)}</tbody></table></div>}</section>
      <section className="section-block"><div className="section-heading"><div><p className="eyebrow">Owner-confirmed</p><h2>Recurring investment reminders</h2></div></div><p className="muted">Reminder only. The agent cannot place or schedule brokerage orders.</p>{data.plans.length === 0 ? <p className="empty-copy">No active recurring reminders.</p> : <div className="card-grid">{data.plans.map((plan) => <article className="card" key={plan.id}><p className="ticker">{plan.ticker}</p><strong>{dollars(plan.amount)} monthly</strong><p>Next due {plan.next_due_on}</p></article>)}</div>}</section>
      <section className="section-block"><h2>Recent transactions</h2>{data.transactions.length === 0 ? <p className="empty-copy">No recorded transactions in this view.</p> : <div className="table-scroll"><table><thead><tr><th>Date</th><th>Ticker</th><th>Side</th><th>Quantity</th><th>Price</th></tr></thead><tbody>{data.transactions.map((item) => <tr key={item.id}><td>{item.executed_on ?? item.timestamp}</td><th>{item.ticker}</th><td>{item.side}</td><td>{item.quantity}</td><td>{dollars(item.price)}</td></tr>)}</tbody></table></div>}</section>
      <p className="muted">Wider portfolio comparisons are unavailable unless a reviewed structured research receipt was persisted.</p>
    </div>
  );
}
