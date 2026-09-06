import type { IntelligenceView } from "@stocks-agent/dashboard-contracts";

import { SafeSourceLink } from "../../components/SafeSourceLink";

const statusLabel = (value: string) => value.replaceAll("_", " ");

export function IntelligencePage({ data }: { data: IntelligenceView }) {
  const accepted = data.sources.reduce((total, source) => total + source.accepted_count, 0);
  const dropped = data.sources.reduce((total, source) => total + source.dropped_count, 0);
  const unavailable = data.sources.filter((source) => source.status === "unavailable").length;
  const partial = data.sources.some((source) => source.status !== "complete");
  const empty = data.themes.length === 0 && data.events.length === 0 && data.candidates.length === 0 && data.sources.length === 0;
  const qualified = data.candidates.filter((candidate) => candidate.qualified).slice(0, 6);
  const events = [...data.events].sort((left, right) => {
    const leftTime = left.occurred_at ? Date.parse(left.occurred_at) : Number.NEGATIVE_INFINITY;
    const rightTime = right.occurred_at ? Date.parse(right.occurred_at) : Number.NEGATIVE_INFINITY;
    const safeLeft = Number.isNaN(leftTime) ? Number.NEGATIVE_INFINITY : leftTime;
    const safeRight = Number.isNaN(rightTime) ? Number.NEGATIVE_INFINITY : rightTime;
    return safeRight - safeLeft;
  });

  if (empty) return <div className="page-stack"><header className="page-heading"><p className="eyebrow">Advanced research</p><h1>Intelligence</h1><p>Events and relationships discovered from queried approved sources.</p></header><section className="state-card"><h2>No intelligence receipt</h2><p>Source coverage is unavailable for this view. No broader market-coverage claim is made.</p></section></div>;

  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Advanced research</p><h1>Intelligence</h1><p>Events and relationships discovered from queried approved sources.</p></header>

      <section className="section-block">
        <div className="section-heading"><div><p className="eyebrow">Newest first</p><h2>Current research events</h2></div><span className="count-chip">{events.length}</span></div>
        {events.length === 0 ? <p className="empty-copy">No material event was persisted in the latest bounded run.</p> : <div className="card-grid">{events.slice(0, 6).map((event) => <article className="card" key={event.id}><p className="card-kicker">{event.type} · {event.materiality}</p><h3>{event.title}</h3><p>{event.summary}</p><p className="muted">Confidence {event.confidence} · {event.occurred_at ? new Date(event.occurred_at).toLocaleString() : "event time unavailable"}</p>{event.sources.length > 0 && <ul className="source-list">{event.sources.map((source, index) => <li key={`${source.label}-${index}`}><SafeSourceLink source={source} /></li>)}</ul>}</article>)}</div>}
        {events.length > 6 && <p className="muted">Showing the six newest material events from this receipt.</p>}
      </section>

      <section className="section-block">
        <div className="section-heading"><div><p className="eyebrow">Exposure-gated</p><h2>Related companies</h2></div><span className="count-chip">{qualified.length}</span></div>
        {qualified.length === 0 ? <p className="empty-copy">No qualified company relationship was persisted.</p> : <div className="card-grid">{qualified.map((candidate) => <article className="card" key={candidate.id}><p className="ticker">#{candidate.rank} {candidate.ticker ?? candidate.candidate_key}</p><h3>Qualified relationship</h3><p>Relationship score {candidate.total_score}. {candidate.event_id ? "Linked to a persisted event." : "No event link was persisted."}</p>{candidate.sources.length > 0 && <ul className="source-list">{candidate.sources.map((source, index) => <li key={`${source.label}-${index}`}><SafeSourceLink source={source} /></li>)}</ul>}</article>)}</div>}
      </section>

      <details className="section-block disclosure-block research-map-details"><summary><h2>Research map details</h2></summary><div className="disclosure-content"><section><div className="section-heading"><div><p className="eyebrow">Taxonomy</p><h3>Seed and dynamic themes</h3></div><span className="count-chip">{data.themes.length}</span></div>{data.themes.length === 0 ? <p className="empty-copy">No persisted themes in this bounded run.</p> : <div className="card-grid">{data.themes.map((theme) => <article className="card" key={theme.key}><h3>{theme.key}</h3><p>{theme.relationship_count} value-chain relationships · {theme.evidence_count} evidence items</p></article>)}</div>}</section><section className="nested-section"><div className="section-heading"><h3>All candidate relationships</h3><span className="count-chip">{data.candidates.length}</span></div>{data.candidates.length === 0 ? <p className="empty-copy">No persisted candidate relationships in this bounded run.</p> : <div className="card-grid">{data.candidates.map((candidate) => <article className="card" key={candidate.id}><p className="ticker">#{candidate.rank} {candidate.ticker ?? candidate.candidate_key}</p><h3>{candidate.qualified ? "Qualified relationship" : "Not qualified"}</h3><p>Relationship score {candidate.total_score}.</p>{candidate.veto_reasons.length > 0 && <p className="inline-warning">Vetoes: {candidate.veto_reasons.join(" · ")}</p>}</article>)}</div>}</section></div></details>

      <details className="section-block disclosure-block intelligence-details"><summary><h2>Source and coverage details</h2></summary><div className="disclosure-content"><div className="section-heading"><p className="eyebrow">Queried providers</p><span className={`badge badge-${partial ? "partial" : "fresh"}`}>{partial ? "Partial coverage" : "Complete for queried sources"}</span></div><div className="metric-grid compact coverage-metrics"><div><span>Queried sources</span><strong>{data.sources.length}</strong></div><div><span>Accepted items</span><strong>{accepted}</strong></div><div><span>Drops</span><strong>{dropped} dropped items</strong></div><div><span>Failures</span><strong>{unavailable} unavailable {unavailable === 1 ? "source" : "sources"}</strong></div></div><p className="muted">Quota limits are reflected in each source status and recorded limitations. Coverage is bounded and may be partial.</p>{data.sources.length > 0 && <div aria-label="Source coverage table" className="table-scroll" role="region" tabIndex={0}><table><thead><tr><th>Provider</th><th>Status</th><th>Retrieved</th><th>Accepted</th><th>Dropped</th></tr></thead><tbody>{data.sources.map((source) => <tr key={source.provider}><th>{source.provider}</th><td><span className={`badge badge-${source.status}`}>{statusLabel(source.status)}</span></td><td>{source.retrieved_at ? new Date(source.retrieved_at).toLocaleString() : "not available"}</td><td>{source.accepted_count}</td><td>{source.dropped_count}</td></tr>)}</tbody></table></div>}<section className="nested-section"><h3>Coverage limitations</h3>{data.limitations.length === 0 ? <p className="empty-copy">No additional limitation text was persisted; the queried-source boundary still applies.</p> : <ul>{data.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}</ul>}<p className="muted">Run receipt {data.run_id} · Data as of {data.data_as_of ? new Date(data.data_as_of).toLocaleString() : "unavailable"}</p></section></div></details>
    </div>
  );
}
