import { Link } from "react-router-dom";

import type { ReportsView } from "@stocks-agent/dashboard-contracts";

export function ReportsPage({ data }: { data: ReportsView }) {
  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Append-only record</p><h1>Reports</h1><p>Morning, weekly, monthly, and theme reports are stored once and linked to their exact receipts.</p></header>
      {data.reports.length === 0 ? <section className="state-card"><h2>No report versions</h2><p>No immutable report is available for this bounded view.</p></section> : <section className="section-block"><div className="section-heading"><h2>Report archive</h2><span className="count-chip">{data.reports.length}</span></div><div className="report-list">{data.reports.map((report) => <article className="report-row" key={report.id}><div><p className="card-kicker">{report.kind} · {report.market_date}</p><h3><Link to={`/reports/${report.id}`}>{report.title}</Link></h3><p>{report.summary}</p></div><dl className="receipt-meta"><div><dt>Created</dt><dd>{new Date(report.created_at).toLocaleString()}</dd></div><div><dt>Immutable version hash</dt><dd>{report.report_hash}</dd></div></dl></article>)}</div></section>}
      {data.next_cursor && <p className="muted">Additional report versions are available through bounded pagination.</p>}
    </div>
  );
}
