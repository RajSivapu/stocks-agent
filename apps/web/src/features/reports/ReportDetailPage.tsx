import { Link } from "react-router-dom";

import type { ReportDetailView } from "@stocks-agent/dashboard-contracts";

import { ReceiptTimeline } from "../../components/ReceiptTimeline";
import { SafeSourceLink } from "../../components/SafeSourceLink";

const statusLabel = (value: string) => value.replaceAll("_", " ");

export function ReportDetailPage({ data }: { data: ReportDetailView }) {
  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">{data.kind} · {data.market_date}</p><h1>{data.title}</h1><p>{data.summary}</p><Link to="/reports">Back to report versions</Link></header>
      <section className="section-block"><h2>Immutable report</h2><dl className="receipt-meta"><div><dt>Report ID</dt><dd>{data.id}</dd></div><div><dt>Created</dt><dd>{new Date(data.created_at).toLocaleString()}</dd></div><div><dt>Report hash</dt><dd>{data.report_hash}</dd></div></dl></section>
      {data.sections.length === 0 ? <section className="state-card"><h2>No report sections</h2><p>The immutable version contains no displayable structured sections.</p></section> : data.sections.map((section, index) => <section className="section-block report-copy" key={`${section.heading}-${index}`}><h2>{section.heading}</h2><p>{section.body}</p></section>)}
      <section className="section-block"><h2>Source references</h2>{data.sources.length === 0 ? <p className="empty-copy">No allowlisted source links were persisted.</p> : <ul className="source-list">{data.sources.map((source, index) => <li key={`${source.label}-${index}`}><SafeSourceLink source={source} /></li>)}</ul>}</section>
      <section className="section-block"><h2>Exact publication receipt timeline</h2>{data.publication.length === 0 ? <p className="empty-copy">No publication receipt is linked to this report version.</p> : <ReceiptTimeline steps={data.publication.map((item) => ({ label: new Date(item.at).toLocaleString(), status: statusLabel(item.status), detail: `Gateway ${item.gateway_status}; ${item.attempt_count} attempt${item.attempt_count === 1 ? "" : "s"}; Telegram message IDs ${item.telegram_message_ids.length > 0 ? item.telegram_message_ids.join(", ") : "none"}; request ${item.request_id}.` }))} />}</section>
    </div>
  );
}
