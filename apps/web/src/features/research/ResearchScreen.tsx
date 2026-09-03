import { useCallback } from "react";
import { StatusBadge } from "../../components/StatusBadge";
import type { DashboardRepository, EvidenceItem, ResearchItem } from "../../lib/dashboard";
import { dateOnly, dateTime, titleCase, usd } from "../../lib/format";
import { useRepositoryData } from "../../lib/useRepositoryData";

function textField(value: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    if (typeof value[key] === "string" && value[key].trim()) return value[key];
  }
  return "No provider summary recorded.";
}

function evidenceHref(item: EvidenceItem): string | null {
  if (!item.reference || item.reference.length > 500) return null;
  try {
    const url = new URL(item.reference);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

function Level({ label, value }: { label: string; value: string | null }) {
  return <div><dt>{label}</dt><dd>{usd(value)}</dd></div>;
}

function ResearchCard({ item }: { item: ResearchItem }) {
  const primaryEvidence = item.evidence[0] ?? null;
  return (
    <article className="research-card" aria-labelledby={`research-${item.id}`}>
      <header className="research-header">
        <div><p className="eyebrow">{item.phase ? titleCase(item.phase) : "Historical record"}</p><h2 id={`research-${item.id}`}>{item.ticker}</h2></div>
        <div className="status-cluster"><StatusBadge status={item.action} />{item.policyStatus && <StatusBadge status={item.policyStatus} />}<StatusBadge status={item.evidenceStatus} /></div>
      </header>
      <div className="research-summary">
        <div><span>Verified price</span><strong>{usd(item.verifiedPrice)}</strong><small>{primaryEvidence?.source ?? "Source unavailable"} · {dateTime(item.evidenceAsOf)}</small></div>
        <div><span>Confidence</span><strong>{item.confidence ? titleCase(item.confidence) : "Unavailable"}</strong><small>{item.provider ? `${titleCase(item.provider)} · ${item.model ?? "model unavailable"}` : "Provider unavailable"}</small></div>
        <div><span>Horizon</span><strong>{item.horizon ? titleCase(item.horizon) : "Unavailable"}</strong><small>Valid until {dateOnly(item.validUntil)}</small></div>
      </div>
      <dl className="level-grid">
        <Level label="Entry low" value={item.entryZoneLow} /><Level label="Entry high" value={item.entryZoneHigh} />
        <Level label="Stop" value={item.stop} /><Level label="Target" value={item.target} />
        <Level label="Invalidation" value={item.invalidationPrice} />
      </dl>
      <div className="review-grid">
        <div><h3>Analyst</h3><p>{textField(item.analyst, ["thesis", "summary", "reason"])}</p></div>
        <div><h3>Checker</h3><p>{textField(item.checker, ["reason", "summary", "verdict"])}</p></div>
        <div><h3>Deterministic policy</h3><p>{item.policyStatus ? titleCase(item.policyStatus) : "Legacy unverified"}{item.rawAction && item.rawAction !== item.action ? ` from ${titleCase(item.rawAction)}` : ""}</p></div>
      </div>
      {(item.reason || item.decisiveFactor || item.riskVerdict || item.policyExplanations.length > 0) && <div className="research-prose">
        {item.reason && <p><strong>Decision:</strong> {item.reason}</p>}
        {item.decisiveFactor && <p><strong>Decisive factor:</strong> {item.decisiveFactor}</p>}
        {item.riskVerdict && <p><strong>Risk:</strong> {item.riskVerdict}</p>}
        {item.policyExplanations.map((explanation, index) => <p key={`${item.id}-explanation-${String(index)}`}><strong>Policy:</strong> {explanation}</p>)}
      </div>}
      <div className="research-detail-grid">
        <div><h3>Evidence</h3>
          {item.evidence.length === 0 ? <p>No evidence metadata available.</p> : <ul className="evidence-list">{item.evidence.map((evidence) => {
            const href = evidenceHref(evidence);
            return <li key={evidence.evidenceId}><div>{href ? <a href={href} target="_blank" rel="noreferrer noopener">{evidence.source}</a> : <strong>{evidence.source}</strong>}<span>{titleCase(evidence.category)}</span></div><time dateTime={evidence.retrievedAt}>{dateTime(evidence.retrievedAt)}</time></li>;
          })}</ul>}</div>
        <div><h3>Notification receipt</h3><p>{item.notificationStatus ? titleCase(item.notificationStatus) : "No publication"}</p>{item.deliveryErrorCode && <p><StatusBadge status={item.deliveryErrorCode} /></p>}{item.telegramMessageIds.map((id) => <span className="receipt-chip" key={id}>Message {id}</span>)}</div>
      </div>
      <div><h3>Measured outcomes</h3>
        {item.outcomes.length === 0 ? <p className="empty-copy">No due outcome has complete coverage yet.</p> : <ul className="outcome-list">{item.outcomes.map((outcome) => <li key={outcome.horizonSessions}><strong>{String(outcome.horizonSessions)} sessions</strong><StatusBadge status={outcome.coverageStatus} /><span>{outcome.stockReturnPct === null ? "Return pending" : `${outcome.stockReturnPct}% stock return`}</span></li>)}</ul>}
      </div>
      <footer className="receipt-meta">Research record {item.id} · {dateTime(item.createdAt)} · historical, not automatically current advice</footer>
    </article>
  );
}

export function ResearchScreen({ repository }: { repository: DashboardRepository }) {
  const loader = useCallback(() => repository.loadResearch(), [repository]);
  const { state, reload } = useRepositoryData(loader);
  if (state.kind === "loading") return <section className="workspace-card" aria-busy="true"><div role="status" aria-label="Loading research">Loading research…</div></section>;
  if (state.kind === "error") return <section className="workspace-card"><h1>Research</h1><p role="alert">Research history is unavailable. No prior result is being presented as current.</p><button type="button" className="secondary-button" onClick={() => { void reload(); }}>Try again</button></section>;
  return (
    <div className="feature-stack">
      <section className="workspace-card research-intro"><p className="eyebrow">Auditable decision history</p><h1>Research</h1><p>Every card preserves the provider proposal, independent checker, deterministic policy result, evidence freshness, and delivery receipt. Historical records are not current advice.</p></section>
      {state.data.items.length === 0 ? <section className="workspace-card empty-state"><h2>No research records yet</h2><p>A completed provider run with a persisted decision will appear here.</p></section> : state.data.items.map((item) => <ResearchCard item={item} key={item.id} />)}
    </div>
  );
}
