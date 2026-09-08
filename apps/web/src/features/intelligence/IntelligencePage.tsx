import type { IntelligenceEvidenceViewV2, IntelligenceView } from "@stocks-agent/dashboard-contracts";

import { SafeSourceLink } from "../../components/SafeSourceLink";

const label = (value: string) => value.replaceAll("_", " ");

function EvidencePassage({ evidence }: { evidence: IntelligenceEvidenceViewV2 }) {
  return (
    <blockquote className={evidence.role === "opposing" ? "evidence-adverse" : undefined}>
      <p>{evidence.passage || "No bounded passage was persisted."}</p>
      <footer>
        <span>{evidence.role === "opposing" ? "Adverse evidence" : "Supporting evidence"}: </span>
        <SafeSourceLink source={{ label: evidence.label, url: evidence.url }} />
      </footer>
    </blockquote>
  );
}

export function IntelligencePage({ data }: { data: IntelligenceView }) {
  const evidence = new Map(data.evidence.map((item) => [item.evidence_id, item]));
  const active = data.themes.filter((theme) => theme.state === "active");
  const revised = active.filter((theme) => theme.revision > 1);
  const outside = data.companies.filter((company) => company.outside_watchlist === true);
  const empty = data.themes.length === 0 && data.companies.length === 0;

  return (
    <div className="page-stack intelligence-view">
      <header className="page-heading">
        <p className="eyebrow">Advanced research · Version {data.intelligence_version}</p>
        <h1>Intelligence</h1>
        <p>Market-wide research memory for investigation. Research only; execution is disabled and valuation is unavailable.</p>
      </header>

      {empty ? (
        <section className="state-card" aria-labelledby="empty-intelligence-title">
          <h2 id="empty-intelligence-title">No validated research memory</h2>
          <p>No completed version-two research catalog is available. This view makes no coverage or qualification claim.</p>
        </section>
      ) : (
        <>
          <section className="section-block" aria-labelledby="changes-title">
            <div className="section-heading"><div><p className="eyebrow">Active and revised</p><h2 id="changes-title">What changed</h2></div><span className="count-chip">{active.length} active</span></div>
            {active.length === 0 ? <p className="empty-copy">No active episode is supported at this reference time.</p> : (
              <div className="card-grid intelligence-grid">{active.map((theme) => (
                <article className="card" key={theme.revision_id}>
                  <p className="card-kicker">{theme.revision > 1 ? `Revised · revision ${theme.revision}` : "New active episode"}</p>
                  <h3>{theme.mechanism}</h3>
                  <p>{theme.subject} · {theme.jurisdiction}</p>
                  <p><strong>{theme.adverse_evidence_count}</strong> adverse evidence {theme.adverse_evidence_count === 1 ? "item" : "items"}.</p>
                  <p className="muted">Next review {new Date(theme.next_review_at).toLocaleString()}</p>
                  <details className="nested-section"><summary>History, questions, and invalidation</summary><div className="disclosure-content"><dl className="receipt-meta"><div><dt>First seen</dt><dd>{new Date(theme.first_seen).toLocaleString()}</dd></div><div><dt>Last seen</dt><dd>{new Date(theme.last_seen).toLocaleString()}</dd></div><div><dt>Expires</dt><dd>{new Date(theme.expires_at).toLocaleString()}</dd></div></dl><h4>Missing inputs</h4>{theme.missing_questions.length ? <ul>{theme.missing_questions.map((question) => <li key={question}>{question}</li>)}</ul> : <p>None recorded.</p>}<h4>Invalidation</h4>{theme.invalidation_conditions.length ? <ul>{theme.invalidation_conditions.map((condition) => <li key={condition}>{condition}</li>)}</ul> : <p>None recorded.</p>}</div></details>
                </article>
              ))}</div>
            )}
            {revised.length > 0 && <p className="muted">{revised.length} active {revised.length === 1 ? "episode has" : "episodes have"} new source-backed revisions.</p>}
          </section>

          <section className="section-block" aria-labelledby="companies-title">
            <div className="section-heading"><div><p className="eyebrow">Outside the current watchlist</p><h2 id="companies-title">Companies to investigate</h2></div><span className="count-chip">{outside.length}</span></div>
            {outside.length === 0 ? <p className="empty-copy">No outside-watchlist company relationship is in this bounded projection.</p> : <div className="card-grid intelligence-grid">{outside.map((company) => (
              <article className="card" key={company.company_id}>
                <p className="ticker">{company.ticker ?? "Ticker unresolved"}</p><h3>{company.name}</h3>
                <p>Research relationship; no qualification or buy signal.</p>
                <h4>Relationship paths</h4><ul>{company.relationship_paths.map((path) => <li key={path}>{path}</li>)}</ul>
                {company.evidence_ids.map((id) => evidence.get(id)).filter((item): item is IntelligenceEvidenceViewV2 => Boolean(item)).slice(0, 2).map((item) => <EvidencePassage evidence={item} key={item.evidence_id} />)}
                {company.missing_inputs.length > 0 && <details><summary>Missing inputs</summary><ul>{company.missing_inputs.map((input) => <li key={input}>{input}</li>)}</ul></details>}
              </article>
            ))}</div>}
          </section>
        </>
      )}

      <details className="section-block disclosure-block"><summary><h2>Evidence and diagnostics</h2></summary><div className="disclosure-content">
        <section><h3>All primary passages</h3>{data.evidence.length ? data.evidence.map((item) => <EvidencePassage evidence={item} key={item.evidence_id} />) : <p className="empty-copy">No shareable evidence passage is available.</p>}</section>
        <section className="nested-section"><h3>Source health</h3>{data.source_health.length ? <ul>{data.source_health.map((source) => <li key={source.provider}><strong>{source.provider}</strong>: {label(source.status)}; {source.accepted_count} accepted, {source.dropped_count} dropped.</li>)}</ul> : <p>Unavailable.</p>}</section>
        <section className="nested-section"><h3>Reference, coverage, and backlog</h3><p>Reference {label(data.reference.state)}{data.reference.manifest_id ? ` · ${data.reference.manifest_id}` : ""}.</p><p>Coverage is {data.coverage.mode}; complete market coverage is not claimed.</p><p>{data.backlog.returned} of {data.backlog.available} themes returned; {data.backlog.deferred} deferred. {data.backlog.byte_truncated ? "Byte limit required deterministic thinning." : "No byte truncation."}</p><p>Data as of {data.data_as_of ? new Date(data.data_as_of).toLocaleString() : "unavailable"}.</p></section>
        {data.omissions.length > 0 && <section className="nested-section"><h3>Omissions</h3><ul>{data.omissions.map((omission) => <li key={omission}>{omission}</li>)}</ul></section>}
      </div></details>
    </div>
  );
}
