import type { RunDetailView } from "@stocks-agent/dashboard-contracts";

import { ReceiptTimeline } from "../../components/ReceiptTimeline";
import { IdeasPage } from "../ideas/IdeasPage";

export function RunDetailPage({ data }: { data: RunDetailView }) {
  return (
    <div className="page-stack"><header className="page-heading"><p className="eyebrow">Receipt chain</p><h1>{data.run.kind} run</h1><p>{data.run.id}</p></header>{data.incomplete_stages.length > 0 && <p className="inline-warning">Incomplete stages: {data.incomplete_stages.join(", ")}</p>}<section className="section-block"><h2>Gateway requests</h2><ReceiptTimeline steps={data.request_receipts.map((receipt) => ({ label: receipt.operation, status: receipt.status, detail: receipt.response_digest ? `Digest ${receipt.response_digest}` : "No response digest" }))} /></section><section className="section-block"><h2>Recorded effects</h2><dl className="receipt-meta">{Object.entries(data.write_counts).map(([name, count]) => <div key={name}><dt>{name}</dt><dd>{count}</dd></div>)}<div><dt>Telegram message IDs</dt><dd>{data.telegram_message_ids.join(", ") || "none"}</dd></div></dl></section>{data.evaluations.length > 0 && <IdeasPage data={{ ideas: data.evaluations }} />}</div>
  );
}
