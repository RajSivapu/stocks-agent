import type { AlertsView } from "@stocks-agent/dashboard-contracts";

import { SafeTelegramPreview } from "../../components/SafeTelegramPreview";

export function AlertsPage({ data }: { data: AlertsView }) {
  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Telegram reconciliation</p><h1>Alerts</h1><p>Delivery claims come only from persisted publication receipts.</p></header>
      {data.alerts.length === 0 ? <section className="state-card"><h2>No alert receipts</h2><p>No publications are available for this filter.</p></section> : data.alerts.map((alert) => (
        <article className="section-block" key={alert.id}>
          <div className="section-heading"><div><p className="card-kicker">{alert.phase} · template {alert.template_version}</p><h2>{alert.kind}</h2></div><span className={`badge badge-${alert.state}`}>{alert.state}</span></div>
          {alert.state === "suppressed" && <p className="inline-warning">Suppressed: no Telegram message was sent.</p>}
          <SafeTelegramPreview text={alert.rendered_text} links={alert.sources} />
          <dl className="receipt-meta"><div><dt>Hash</dt><dd>{alert.rendered_hash}</dd></div><div><dt>Attempts</dt><dd>{alert.attempt_count}</dd></div><div><dt>Message IDs</dt><dd>{alert.telegram_message_ids.length > 0 ? alert.telegram_message_ids.join(", ") : "none"}</dd></div><div><dt>Rule / event</dt><dd>{[alert.rule_ticker, alert.rule_state, alert.event_status, alert.owner_action].filter(Boolean).join(" · ") || "not linked"}</dd></div></dl>
        </article>
      ))}
    </div>
  );
}
