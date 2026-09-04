import { Link } from "react-router-dom";

import type { TodayView } from "@stocks-agent/dashboard-contracts";

function money(value: string | null) {
  return value === null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(Number(value));
}

export function TodayPage({ data }: { data: TodayView }) {
  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Daily command center</p><h1>Today</h1><p>What deserves your attention, backed by persisted receipts.</p></header>
      <section className="section-block attention-section">
        <div className="section-heading"><div><p className="eyebrow">Priority</p><h2>Needs attention</h2></div><span className="count-chip">{data.attention.length}</span></div>
        {data.attention.length === 0
          ? <p className="empty-copy">No receipt-backed attention items right now.</p>
          : <div className="card-grid">{data.attention.map((item) => <article className={`card severity-${item.severity}`} key={item.id}><p className="card-kicker">{item.severity}</p><h3>{item.title}</h3><p>{item.detail}</p><Link to={item.destination}>Review details</Link></article>)}</div>}
      </section>
      <section className="section-block"><div className="section-heading"><h2>Portfolio at a glance</h2><Link to="/portfolio">Open portfolio</Link></div><div className="metric-grid"><div><span>Supported value</span><strong>{money(data.portfolio.value)}</strong></div><div><span>Cost basis</span><strong>{money(data.portfolio.cost_basis)}</strong></div><div><span>Unrealized</span><strong>{money(data.portfolio.unrealized_amount)}</strong></div></div>{data.portfolio.value === null && <p className="inline-warning">Value is withheld until every required price has current receipt support.</p>}</section>
      {data.market_summary && <section className="section-block"><p className="eyebrow">Recorded context</p><h2>Market</h2><p className="large-copy">{data.market_summary}</p></section>}
      {data.entry_zones.length > 0 && <section className="section-block"><div className="section-heading"><h2>Open entry zones</h2><Link to="/ideas">Review all ideas</Link></div><div className="card-grid">{data.entry_zones.map((idea) => <article className="card" key={idea.id}><p className="ticker">{idea.ticker}</p><p>{money(idea.entry_zone_low)}–{money(idea.entry_zone_high)}</p><p>Stop {money(idea.stop)} · Target {money(idea.target)}</p><p>Valid through {idea.valid_until ?? "not recorded"}</p></article>)}</div></section>}
      {data.companion && <section className="section-block"><p className="eyebrow">Long-term research</p><h2>Companion review</h2><p>{data.companion.thesis ?? "A structured review is available."}</p><Link to="/companion">Open Companion</Link></section>}
    </div>
  );
}
