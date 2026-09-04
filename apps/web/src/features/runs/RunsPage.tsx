import { Link } from "react-router-dom";

import type { RunsView } from "@stocks-agent/dashboard-contracts";

export function RunsPage({ data }: { data: RunsView }) {
  return (
    <div className="page-stack"><header className="page-heading"><p className="eyebrow">Scheduled lifecycle</p><h1>Runs</h1><p>Each row summarizes persisted stages; it does not trigger a run.</p></header><section className="section-block">{data.runs.length === 0 ? <p className="empty-copy">No run receipts are available.</p> : <div aria-label="Scheduled runs table" className="table-scroll" role="region" tabIndex={0}><table><thead><tr><th>Run</th><th>Status</th><th>Evidence time</th><th>Artifacts</th><th>Publication</th></tr></thead><tbody>{data.runs.map((run) => <tr key={run.id}><th><Link to={`/runs/${run.id}`}>{run.kind}</Link><small>{new Date(run.started_at).toLocaleString()}</small></th><td>{run.status}</td><td>{run.data_as_of ? new Date(run.data_as_of).toLocaleString() : "not recorded"}</td><td>{run.evaluation_count} evaluations · {run.suggestion_count} suggestions</td><td>{run.publication_status ?? "none"}</td></tr>)}</tbody></table></div>}</section></div>
  );
}
