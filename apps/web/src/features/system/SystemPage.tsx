import type { SystemView } from "@stocks-agent/dashboard-contracts";

export function SystemPage({ data }: { data: SystemView }) {
  return (
    <div className="page-stack"><header className="page-heading"><p className="eyebrow">Receipt-derived state</p><h1>System</h1><p>Page load is not treated as system health.</p></header><section className="section-block"><h2>Immutable boundaries</h2><ul className="boundary-list"><li>Owner only</li><li>Suggestion only</li><li>Friend invitations disabled</li><li>Brokerage authority none</li></ul></section><section className="metric-grid"><div><span>Product</span><strong>{data.product_version}</strong></div><div><span>API</span><strong>{data.api_version}</strong></div><div><span>Policy receipt</span><strong>{data.policy_version ?? "unavailable"}</strong></div><div><span>Alert mode</span><strong>{data.alert_mode}</strong></div></section><section className="section-block"><h2>Latest scheduled receipts</h2>{Object.keys(data.latest_by_kind).length === 0 ? <p className="empty-copy">No receipt summary is available.</p> : <ul>{Object.entries(data.latest_by_kind).map(([kind, run]) => <li key={kind}>{kind}: {run?.status ?? "missing"}</li>)}</ul>}<p>Latest publication: {data.latest_publication_status ?? "unavailable"}</p></section></div>
  );
}
