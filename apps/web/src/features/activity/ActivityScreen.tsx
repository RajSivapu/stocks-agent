import { useCallback } from "react";
import type { DashboardRepository, Transaction } from "../../lib/dashboard";
import { dateOnly, dateTime, quantity, titleCase, usd } from "../../lib/format";
import { useRepositoryData } from "../../lib/useRepositoryData";
import { StatusBadge } from "../../components/StatusBadge";

function eventLabel(transaction: Transaction): string {
  if (transaction.eventType === "void") return "Correction void";
  if (transaction.side === "buy") return "Record Buy";
  if (transaction.side === "sell") return "Record Sell";
  return "Opening balance";
}

export function ActivityScreen({ repository }: { repository: DashboardRepository }) {
  const loader = useCallback(() => repository.loadActivity(), [repository]);
  const { state, reload } = useRepositoryData(loader);
  if (state.kind === "loading") return <section className="workspace-card" aria-busy="true"><div role="status" aria-label="Loading activity">Loading activity…</div></section>;
  if (state.kind === "error") return <section className="workspace-card"><h1>Activity</h1><p role="alert">We couldn’t load your activity receipts.</p><button type="button" className="secondary-button" onClick={() => { void reload(); }}>Try again</button></section>;
  return (
    <div className="feature-stack">
      <section className="workspace-card">
        <p className="eyebrow">Audit trail</p><h1>Activity</h1>
        <div className="safety-banner"><strong>Append-only ledger</strong><span>Corrections create linked void and replacement events; history is never silently rewritten.</span></div>
        {state.data.transactions.length === 0 ? <div className="empty-state"><h2>No transactions yet</h2><p>Completed broker activity will appear here after confirmation.</p></div> : <div className="table-scroll"><table className="data-table">
          <caption className="sr-only">Immutable portfolio transaction history</caption>
          <thead><tr><th scope="col">Event</th><th scope="col">Ticker</th><th scope="col">Quantity</th><th scope="col">Fill</th><th scope="col">Fees</th><th scope="col">Executed</th><th scope="col">Source</th></tr></thead>
          <tbody>{state.data.transactions.map((transaction) => <tr key={transaction.id}>
            <th scope="row">{eventLabel(transaction)}{transaction.correctsTransactionId && <span className="cell-subtitle">Corrects {transaction.correctsTransactionId.slice(0, 8)}</span>}</th>
            <td data-label="Ticker">{transaction.ticker}</td><td data-label="Quantity">{quantity(transaction.qty)}</td>
            <td data-label="Fill">{usd(transaction.price)}</td><td data-label="Fees">{usd(transaction.fees)}</td>
            <td data-label="Executed">{dateOnly(transaction.executedOn)}</td><td data-label="Source">{titleCase(transaction.sourceChannel)}</td>
          </tr>)}</tbody>
        </table></div>}
      </section>
      <section className="workspace-card" aria-labelledby="receipts-title">
        <p className="eyebrow">Server confirmations</p><h2 id="receipts-title">Command receipts</h2>
        {state.data.commands.length === 0 ? <p className="empty-copy">No recent command receipts.</p> : <ul className="receipt-list">{state.data.commands.map((command) => <li key={command.id}><div><strong>{titleCase(command.operation)}</strong><span>{dateTime(command.createdAt)}</span></div><StatusBadge status={command.status} />{command.errorCode && <code>{command.errorCode}</code>}</li>)}</ul>}
      </section>
      <section className="workspace-card" aria-labelledby="activity-plans-title">
        <p className="eyebrow">Reminder history</p><h2 id="activity-plans-title">Recurring plans</h2>
        {state.data.plans.length === 0 ? <p className="empty-copy">No recurring plans recorded.</p> : <ul className="plan-list">{state.data.plans.map((plan) => <li key={plan.id}><div><strong>{plan.ticker}</strong><span>{usd(plan.amount)} monthly</span></div><div><StatusBadge status={plan.active ? "active" : "cancelled"} /><span>Next {dateOnly(plan.nextDueOn)}</span></div></li>)}</ul>}
      </section>
    </div>
  );
}
