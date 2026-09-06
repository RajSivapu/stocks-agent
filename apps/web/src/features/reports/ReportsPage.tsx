import { Link } from "react-router-dom";

import type { ReportsView } from "@stocks-agent/dashboard-contracts";

export function ReportsPage({ data }: { data: ReportsView }) {
  const reports = [...data.reports].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at));
  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Published research</p><h1>Reports</h1><p>Morning, weekly, monthly, and theme reports, newest first.</p></header>
      {reports.length === 0 ? <section className="state-card"><h2>No report versions</h2><p>No scheduled report receipt is available yet. New reports will appear here after publication.</p></section> : <section className="section-block"><div className="section-heading"><div><p className="eyebrow">Newest first</p><h2>Report archive</h2></div><span className="count-chip">{reports.length}</span></div><div className="report-list">{reports.map((report, index) => <article className={`report-row${index === 0 ? " report-row-latest" : ""}`} key={report.id}><div><p className="card-kicker">{index === 0 ? "Latest · " : ""}{report.kind} · {report.market_date}</p><h3><Link to={`/reports/${report.id}`}>{report.title}</Link></h3><p>{report.summary}</p><p className="muted">Published {new Date(report.created_at).toLocaleString()}</p></div><details className="report-receipt"><summary>Receipt details</summary><dl className="receipt-meta"><div><dt>Created</dt><dd>{new Date(report.created_at).toLocaleString()}</dd></div><div><dt>Immutable version hash</dt><dd>{report.report_hash}</dd></div></dl></details></article>)}</div></section>}
      {data.next_cursor && <p className="muted">Additional report versions are available through bounded pagination.</p>}
    </div>
  );
}
