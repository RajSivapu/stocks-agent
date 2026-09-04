import { Link } from "react-router-dom";

import type { AlertsView, RunsView, SystemView } from "@stocks-agent/dashboard-contracts";

const statusLabel = (value: string) => value.replaceAll("_", " ");

export function SystemPage({ data, runs, alerts }: { data: SystemView; runs?: RunsView; alerts?: AlertsView }) {
  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Receipt-derived state</p><h1>System / Receipts</h1><p>Runs, alerts, policies, and authority boundaries. Page load is not treated as system health.</p></header>
      <section className="section-block"><h2>Permanent boundaries</h2><ul className="boundary-list"><li>Owner only</li><li>Suggestion only</li><li>Friend invitations disabled</li><li>Brokerage authority none</li></ul></section>
      <section className="metric-grid"><div><span>Product</span><strong>{data.product_version}</strong></div><div><span>API</span><strong>{data.api_version}</strong></div><div><span>Policy receipt</span><strong>{data.policy_version ?? "unavailable"}</strong></div><div><span>Alert mode</span><strong>{data.alert_mode}</strong></div></section>
      <section className="section-block"><h2>Runs and write receipts</h2>{runs && runs.runs.length > 0 ? <div className="card-grid">{runs.runs.map((run) => <article className="card" key={run.id}><p className="card-kicker">{run.kind}</p><h3><Link to={`/runs/${run.id}`}>{run.status}</Link></h3><p>{run.evaluation_count} evaluations · {run.suggestion_count} suggestions · publication {run.publication_status ?? "none"}</p></article>)}</div> : Object.keys(data.latest_by_kind).length === 0 ? <p className="empty-copy">No run receipt summary is available.</p> : <ul>{Object.entries(data.latest_by_kind).map(([kind, run]) => <li key={kind}>{kind}: {run?.status ?? "missing"}</li>)}</ul>}<p>Latest intelligence run: {data.latest_intelligence_run_id ?? "unavailable"}</p></section>
      <section className="section-block"><h2>Alert and send receipts</h2>{alerts && alerts.alerts.length > 0 ? <div className="card-grid">{alerts.alerts.map((alert) => <article className="card" key={alert.id}><p className="card-kicker">{alert.kind} · {alert.phase}</p><h3>{statusLabel(alert.state)}</h3><p>{alert.attempt_count} send attempt{alert.attempt_count === 1 ? "" : "s"} · Telegram message IDs {alert.telegram_message_ids.length > 0 ? alert.telegram_message_ids.join(", ") : "none"}</p>{alert.suppression_reason && <p>Suppression: {alert.suppression_reason}</p>}</article>)}</div> : <p className="empty-copy">No bounded alert receipt details are available.</p>}<p>Latest publication: {data.latest_publication_status ? statusLabel(data.latest_publication_status) : "unavailable"}</p></section>
      <section className="section-block"><h2>Source collection status</h2>{data.source_coverage.length === 0 ? <p className="empty-copy">No bounded source coverage receipt is available.</p> : <ul>{data.source_coverage.map((source) => <li key={source.provider}>{source.provider}: {source.status}; {source.accepted_count} accepted, {source.dropped_count} dropped</li>)}</ul>}</section>
      <section className="section-block"><h2>Write, send, and deploy boundaries</h2><ul><li>No browser write route or financial mutation authority.</li><li>Send status is reported only from persisted publication receipts.</li><li>Deployment status is unavailable in this read model; this page makes no deployment claim.</li></ul><p>Latest report: {data.latest_report?.title ?? "unavailable"}</p></section>
    </div>
  );
}
