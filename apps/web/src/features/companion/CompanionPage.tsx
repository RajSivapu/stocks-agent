import type { CompanionView } from "@stocks-agent/dashboard-contracts";

import { SafeSourceLink } from "../../components/SafeSourceLink";

export function CompanionPage({ data }: { data: CompanionView }) {
  return (
    <div className="page-stack">
      <header className="page-heading"><p className="eyebrow">Long-term companion</p><h1>{data.baseline_ticker ?? "Core"} + {data.companion_ticker ?? "research candidate"}</h1><p>Role-aware historical analysis, not a prediction or automatic plan change.</p></header>
      <section className="section-block"><div className="section-heading"><h2>Role review</h2><span className="badge">{data.status}</span></div><p className="large-copy">{data.thesis ?? "No qualified structured companion proposal is available."}</p><p><strong>Risk:</strong> {data.risk_note ?? "No structured risk note was persisted."}</p><p className="inline-warning">Current plan remains unchanged. Any recurring-plan change requires your explicit confirmation.</p></section>
      {data.horizons.length > 0 && <section className="section-block"><h2>Historical horizons</h2><div aria-label="Historical horizons table" className="table-scroll" role="region" tabIndex={0}><table><thead><tr><th>Period</th><th>{data.baseline_ticker} annualized</th><th>{data.companion_ticker} annualized</th><th>{data.baseline_ticker} drawdown</th><th>{data.companion_ticker} drawdown</th><th>Correlation</th></tr></thead><tbody>{data.horizons.map((row) => <tr key={row.years}><th>{row.years} years</th><td>{row.baseline_annualized_percent}%</td><td>{row.companion_annualized_percent}%</td><td>{row.baseline_max_drawdown_percent}%</td><td>{row.companion_max_drawdown_percent}%</td><td>{row.correlation}</td></tr>)}</tbody></table></div></section>}
      {data.contribution_history && <section className="section-block"><h2>Rolling one-year contribution history</h2><p>Across {data.contribution_history.sample_count} historical windows, {data.contribution_history.contributed} contributed ended between {data.contribution_history.lower_ending_value} and {data.contribution_history.higher_ending_value}; the middle observed result was {data.contribution_history.median_ending_value}.</p></section>}
      {data.evidence.length > 0 && <section className="section-block"><h2>Evidence</h2><ul className="source-list">{data.evidence.map((source, index) => <li key={`${source.label}-${index}`}><SafeSourceLink source={source} /></li>)}</ul></section>}
      <section className="section-block muted"><h2>Comparison boundary</h2><p>Detailed wider alternatives are unavailable until they are persisted as reviewed structured receipts.</p><p>{data.disclaimer}</p></section>
    </div>
  );
}
