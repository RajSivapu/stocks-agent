import type { IdeasView } from "@stocks-agent/dashboard-contracts";

import { SafeSourceLink } from "../../components/SafeSourceLink";

export function IdeasPage({ data }: { data: IdeasView }) {
  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Underwritten setups</p><h1>Ideas</h1><p>Observed evidence and review stages remain separate. Every item is suggestion only.</p></header>
      {data.ideas.length === 0 ? <section className="state-card"><h2>No active ideas</h2><p>No persisted idea meets this view’s filter.</p></section> : data.ideas.map((idea) => (
        <article className="section-block idea-card" key={idea.id}>
          <div className="section-heading"><div><p className="ticker">{idea.ticker}</p><h2>{idea.final_action ?? "No action"}</h2></div><span className={`badge badge-${idea.policy_status}`}>{idea.policy_status}</span></div>
          <div className="metric-grid compact"><div><span>Entry zone</span><strong>{idea.entry_zone_low ?? "—"}–{idea.entry_zone_high ?? "—"}</strong></div><div><span>Stop</span><strong>{idea.stop ?? "—"}</strong></div><div><span>Target</span><strong>{idea.target ?? "—"}</strong></div><div><span>Valid through</span><strong>{idea.valid_until ?? "—"}</strong></div></div>
          <div className="review-grid"><section><h3>Observed evidence</h3><p><strong>Bull:</strong> {idea.bull_case ?? "Not recorded"}</p><p><strong>Bear:</strong> {idea.bear_case ?? "Not recorded"}</p>{idea.sources.length > 0 && <ul>{idea.sources.map((source, index) => <li key={index}><SafeSourceLink source={source} /></li>)}</ul>}</section><section><h3>Analyst</h3><p>{idea.analyst_complete ? "Completed" : "Incomplete"}</p><p>{idea.decisive_factor ?? "No decisive factor recorded."}</p></section><section><h3>Checker</h3><p>{idea.checker_complete ? "Completed" : "Incomplete"}</p><p>{idea.invalidation ?? "No invalidation text recorded."}</p></section><section><h3>Deterministic policy</h3><p>{idea.policy_status} under policy {idea.policy_version ?? "unverified"}</p>{idea.reason_codes.length > 0 && <p>{idea.reason_codes.join(" · ")}</p>}</section></div>
        </article>
      ))}
    </div>
  );
}
