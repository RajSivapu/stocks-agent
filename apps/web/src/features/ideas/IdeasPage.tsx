import { useMemo, useState } from "react";

import type { IdeaView, IdeasView } from "@stocks-agent/dashboard-contracts";

import { SafeSourceLink } from "../../components/SafeSourceLink";
import { isActionNow, splitIdeaVersions } from "./ideaVersions";

type IdeaFilter = "current" | "action" | "watch" | "hold" | "history";

function IdeaCard({ idea, historical = false }: { idea: IdeaView; historical?: boolean }) {
  return (
    <article className={`section-block idea-card${historical ? " idea-card-history" : ""}`}>
      <div className="section-heading"><div><p className="ticker">{idea.ticker}</p><h2>{idea.final_action ?? "No action"}</h2></div><span className={`badge badge-${idea.policy_status}`}>{idea.policy_status}</span></div>
      <div className="metric-grid compact idea-levels"><div><span>Entry zone</span><strong>{idea.entry_zone_low ?? "—"}–{idea.entry_zone_high ?? "—"}</strong></div><div><span>Stop</span><strong>{idea.stop ?? "—"}</strong></div><div><span>Target</span><strong>{idea.target ?? "—"}</strong></div><div><span>Valid through</span><strong>{idea.valid_until ?? "—"}</strong></div></div>
      <div className="decision-summary">
        <div><span>Why</span><p>{idea.decisive_factor ?? "No decisive factor recorded."}</p></div>
        <div><span>Invalidation</span><p>{idea.invalidation ?? "No invalidation recorded."}</p></div>
      </div>
      <p className="muted idea-age">Evidence {idea.evidence_as_of ? new Date(idea.evidence_as_of).toLocaleString() : "time unavailable"} · Confidence {idea.confidence ?? "unavailable"}</p>
      <details className="idea-details disclosure-panel">
        <summary>Research and receipt details</summary>
        <div className="review-grid">
          <section><h3>Relationship and exposure evidence</h3><p>Direct and second-order relationships remain bounded by the persisted discovery run and exposure gate.</p><p>Intelligence run {idea.intelligence_run_id ?? "unavailable"}</p>{idea.sources.length > 0 ? <ul>{idea.sources.map((source, index) => <li key={index}><SafeSourceLink source={source} /></li>)}</ul> : <p>No allowlisted exposure source link was persisted.</p>}</section>
          <section><h3>Analyst review</h3><p>{idea.analyst_complete ? "Completed" : "Incomplete"}</p><p>{idea.decisive_factor ?? "No decisive factor recorded."}</p></section>
          <section><h3>Checker review</h3><p>{idea.checker_complete ? "Completed" : "Incomplete"}</p><p>{idea.invalidation ?? "No invalidation text recorded."}</p></section>
          <section><h3>Deterministic policy</h3><p>{idea.policy_status} under policy {idea.policy_version ?? "unverified"}</p>{idea.reason_codes.length > 0 && <p>{idea.reason_codes.join(" · ")}</p>}</section>
          <section><h3>Conditional scenarios</h3><p><strong>Bull:</strong> {idea.bull_case ?? "Not recorded"}</p><p><strong>Base:</strong> {idea.decisive_factor ?? "Not recorded"}</p><p><strong>Bear:</strong> {idea.bear_case ?? "Not recorded"}</p></section>
          <section><h3>Outcome observation</h3>{idea.outcome ? <p>{idea.outcome.horizon_days}-session outcome: {idea.outcome.result}, graded {new Date(idea.outcome.graded_at).toLocaleString()}.</p> : <p>No eligible outcome grade is available.</p>}</section>
        </div>
      </details>
    </article>
  );
}

export function IdeasPage({ data }: { data: IdeasView }) {
  const { current, history } = useMemo(() => splitIdeaVersions(data.ideas), [data.ideas]);
  const [filter, setFilter] = useState<IdeaFilter>("current");
  const actionIdeas = current.filter(isActionNow);
  const watchIdeas = current.filter((idea) => idea.final_action?.toLowerCase() === "watch");
  const holdIdeas = current.filter((idea) => idea.final_action?.toLowerCase() === "hold");
  const filtered = filter === "history" ? history : filter === "action" ? actionIdeas : filter === "watch" ? watchIdeas : filter === "hold" ? holdIdeas : current;
  const filters: Array<[IdeaFilter, string, number]> = [
    ["current", "Current", current.length],
    ["action", "Action now", actionIdeas.length],
    ["watch", "Watch", watchIdeas.length],
    ["hold", "Hold", holdIdeas.length],
    ["history", "History", history.length],
  ];

  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Current suggestions</p><h1>Ideas</h1><p>The newest reviewed setup for each ticker. Every item is suggestion only, and you decide whether to act.</p></header>
      <div className="filter-bar" role="group" aria-label="Idea views">
        {filters.map(([value, label, count]) => <button type="button" className="filter-chip" aria-pressed={filter === value} key={value} onClick={() => setFilter(value)}>{label} <span>{count}</span></button>)}
      </div>
      {filtered.length === 0 ? <section className="state-card"><h2>No ideas in this view</h2><p>{filter === "history" ? "No older versions are available." : "No current idea matches this filter."}</p></section> : filtered.map((idea) => <IdeaCard idea={idea} historical={filter === "history"} key={idea.id} />)}
    </div>
  );
}
