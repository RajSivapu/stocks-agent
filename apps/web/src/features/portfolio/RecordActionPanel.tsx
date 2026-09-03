import { useState, type SyntheticEvent } from "react";
import { parseCommandInput, type CommandInput, type CommandPreviewReceipt } from "@stocks-agent/contracts";
import type { CommandClient, CommandReceipt } from "../../lib/app-api";
import { dateTime, titleCase, usd } from "../../lib/format";

const ACTIONS = [
  ["buy", "Record Buy"],
  ["sell", "Record Sell"],
  ["sell_all", "Record Sell All"],
  ["stop", "Update Stop"],
  ["correct_transaction", "Record Correction"],
  ["plan", "Record Recurring Plan"],
  ["cancel_plan", "Cancel Recurring Plan"],
] as const satisfies ReadonlyArray<readonly [CommandInput["operation"], string]>;

type Action = (typeof ACTIONS)[number][0];
type ConfirmState = "idle" | "sending" | "checking" | "retry" | "expired" | "locked" | "applied";

function actionLabel(operation: Action): string {
  return ACTIONS.find(([value]) => value === operation)?.[1] ?? "Record Change";
}

function value(form: HTMLFormElement, name: string): string {
  const field = new FormData(form).get(name);
  return typeof field === "string" ? field.trim() : "";
}

function tradeInput(form: HTMLFormElement, operation: "buy" | "sell"): CommandInput {
  const cash = value(form, "cash_total");
  const planDeposit = value(form, "plan_deposit_amount");
  return parseCommandInput({
    operation,
    ticker: value(form, "ticker").toUpperCase(),
    quantity: value(form, "quantity"),
    fill_price: value(form, "fill_price"),
    fees: value(form, "fees"),
    cash_total: cash || null,
    executed_on: value(form, "executed_on"),
    ...(operation === "buy" ? { bucket: value(form, "bucket") || "unclassified" } : {}),
    ...(operation === "buy" && planDeposit ? { plan_deposit_amount: planDeposit } : {}),
  });
}

function commandFromForm(form: HTMLFormElement, operation: Action): CommandInput {
  if (operation === "buy" || operation === "sell") return tradeInput(form, operation);
  if (operation === "sell_all") {
    const cash = value(form, "cash_total");
    return parseCommandInput({
      operation, ticker: value(form, "ticker").toUpperCase(),
      fill_price: value(form, "fill_price"), fees: value(form, "fees"),
      cash_total: cash || null, executed_on: value(form, "executed_on"),
    });
  }
  if (operation === "stop") {
    return parseCommandInput({
      operation, ticker: value(form, "ticker").toUpperCase(), stop: value(form, "stop"),
    });
  }
  if (operation === "plan") {
    return parseCommandInput({
      operation, ticker: value(form, "ticker").toUpperCase(),
      deposit_amount: value(form, "deposit_amount"), cadence: "monthly",
      next_due_on: value(form, "next_due_on"), bucket: "core",
    });
  }
  if (operation === "cancel_plan") {
    return parseCommandInput({ operation, ticker: value(form, "ticker").toUpperCase() });
  }
  const replacementSide = value(form, "replacement_side") as "buy" | "sell";
  return parseCommandInput({
    operation,
    transaction_id: value(form, "transaction_id"),
    replacement: tradeInput(form, replacementSide),
  });
}

function Fields({ operation }: { operation: Action }) {
  const trade = ["buy", "sell", "sell_all", "correct_transaction"].includes(operation);
  const quantity = ["buy", "sell", "correct_transaction"].includes(operation);
  return (
    <div className="field-grid">
      {operation === "correct_transaction" && <>
        <label>Original transaction ID<input name="transaction_id" required /></label>
        <label>Replacement side<select name="replacement_side" defaultValue="buy"><option value="buy">Buy</option><option value="sell">Sell</option></select></label>
      </>}
      {operation !== "correct_transaction" || trade ? (
        <label>Ticker<input name="ticker" autoCapitalize="characters" maxLength={15} required /></label>
      ) : null}
      {quantity && <label>Filled quantity<input name="quantity" inputMode="decimal" required /></label>}
      {trade && <>
        <label>Fill price<input name="fill_price" inputMode="decimal" required /></label>
        <label>Fees<input name="fees" inputMode="decimal" defaultValue="0" required /></label>
        <label>Broker cash total<input name="cash_total" inputMode="decimal" aria-describedby="cash-help" /></label>
        <span id="cash-help" className="field-help">Optional reconciliation check from your broker.</span>
        <label>Execution date<input name="executed_on" type="date" required /></label>
      </>}
      {(operation === "buy" || operation === "correct_transaction") && <>
        <label>Risk bucket<select name="bucket" defaultValue="unclassified">
          <option value="unclassified">Unclassified</option><option value="core">Core</option>
          <option value="growth">Growth</option><option value="speculative">Speculative</option>
        </select></label>
        <label>Recurring deposit matched<input name="plan_deposit_amount" inputMode="decimal" aria-describedby="plan-help" /></label>
        <span id="plan-help" className="field-help">Optional. Advances a matching active plan only after confirmation.</span>
      </>}
      {operation === "stop" && <label>New stop<input name="stop" inputMode="decimal" required /></label>}
      {operation === "plan" && <>
        <label>Monthly deposit<input name="deposit_amount" inputMode="decimal" required /></label>
        <label>Next investment date<input name="next_due_on" type="date" required /></label>
        <p className="field-help">Recurring plans are core reminders only; Stock Agent never places the trade.</p>
      </>}
    </div>
  );
}

function projectionValue(label: string, projection: Record<string, unknown>, key: string) {
  const raw = projection[key];
  if (typeof raw !== "string" && typeof raw !== "number") return null;
  const text = String(raw);
  const money = ["avg_cost", "fill_price", "cash_total", "expected_cash_total", "fees", "estimated_realized_pnl", "stop", "target"].includes(key);
  return <div><dt>{label}</dt><dd>{key === "shares" ? `${text} shares` : money ? usd(text) : text}</dd></div>;
}

function PreviewDialog({ preview, commands, onClose, onApplied }: {
  preview: CommandPreviewReceipt;
  commands: CommandClient;
  onClose: () => void;
  onApplied: () => Promise<void>;
}) {
  const [state, setState] = useState<ConfirmState>("idle");
  const [message, setMessage] = useState<string | null>(null);

  const applyReceipt = async (receipt: CommandReceipt | null) => {
    if (receipt?.status === "applied") {
      setState("applied");
      setMessage("The server receipt confirms this record was applied.");
      await onApplied();
    } else if (receipt?.status === "previewed" && Date.parse(receipt.expiresAt) > Date.now()) {
      setState("retry");
      setMessage("The server still shows this preview as unapplied. It is safe to retry confirmation.");
    } else if (receipt?.status === "previewed" || receipt?.status === "expired") {
      setState("expired");
      setMessage("This preview expired. Create a fresh preview before recording anything.");
    } else if (receipt) {
      setState("locked");
      setMessage(`The server reports ${titleCase(receipt.status)}. Create a fresh preview before continuing.`);
    } else {
      setState("locked");
      setMessage("The server receipt is unavailable. Confirmation remains locked until it can be checked.");
    }
  };

  const checkReceipt = async () => {
    setState("checking");
    try {
      await applyReceipt(await commands.lookup(preview.command_id));
    } catch {
      setState("locked");
      setMessage("The server receipt could not be checked. No local portfolio change was made.");
    }
  };

  const confirm = async () => {
    if (Date.parse(preview.expires_at) <= Date.now()) {
      setState("expired");
      setMessage("This preview expired. Create a fresh preview before recording anything.");
      return;
    }
    setState("sending");
    setMessage(null);
    try {
      await commands.confirm(preview.command_id, preview.preview_digest, preview.operation);
      setState("applied");
      setMessage("Record applied. The portfolio was refreshed from the server.");
      await onApplied();
    } catch {
      await checkReceipt();
    }
  };

  return (
    <section className="preview-dialog" role="dialog" aria-modal="true" aria-labelledby="preview-title">
      <p className="eyebrow">Record only · no brokerage action</p>
      <h2 id="preview-title">Review {actionLabel(preview.operation)}</h2>
      <div className="preview-columns">
        <div><h3>Before</h3><dl>{projectionValue("Shares", preview.before, "shares")}{projectionValue("Average cost", preview.before, "avg_cost")}</dl></div>
        <div><h3>After</h3><dl>
          {projectionValue("Shares", preview.after, "shares")}
          {projectionValue("Average cost", preview.after, "avg_cost")}
          {projectionValue("Fill price", preview.after, "fill_price")}
          {projectionValue("Fees", preview.after, "fees")}
          {projectionValue("Broker cash total", preview.after, "cash_total")}
          {projectionValue("Expected cash total", preview.after, "expected_cash_total")}
          {projectionValue("Estimated realized P&L", preview.after, "estimated_realized_pnl")}
          {projectionValue("Execution date", preview.after, "executed_on")}
          {typeof preview.after.cash_reconciled === "boolean" && <div><dt>Cash reconciliation</dt><dd>{preview.after.cash_reconciled ? "Matched" : "Not provided"}</dd></div>}
          {typeof preview.after.bucket === "string" && <div><dt>Bucket</dt><dd>{titleCase(preview.after.bucket)}</dd></div>}
          {typeof preview.after.plan_impact === "string" && <div><dt>Plan impact</dt><dd>{preview.after.plan_impact}</dd></div>}
        </dl></div>
      </div>
      {preview.warnings.length > 0 && <ul className="warning-list">{preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
      <p className="receipt-meta">Expires {dateTime(preview.expires_at)} · Digest {preview.preview_digest.slice(0, 8)}</p>
      {message && <p role="status" className={state === "locked" ? "error" : "notice"}>{message}</p>}
      <div className="dialog-actions">
        <button type="button" className="secondary-button" onClick={onClose} disabled={["sending", "checking"].includes(state)}>Cancel</button>
        {state === "expired" ? (
          <button type="button" className="primary-button" onClick={onClose}>Create fresh preview</button>
        ) : state === "locked" ? (
          <button type="button" className="primary-button" onClick={() => { void checkReceipt(); }}>Check server receipt</button>
        ) : state !== "applied" ? (
          <button type="button" className="primary-button" onClick={() => { void confirm(); }} disabled={["sending", "checking"].includes(state)}>
            {state === "retry" ? "Retry confirmation" : state === "checking" ? "Checking receipt…" : "Confirm record"}
          </button>
        ) : <button type="button" className="primary-button" onClick={onClose}>Done</button>}
      </div>
    </section>
  );
}

export function RecordActionPanel({ commands, onApplied }: {
  commands: CommandClient;
  onApplied: () => Promise<void>;
}) {
  const [operation, setOperation] = useState<Action | null>(null);
  const [preview, setPreview] = useState<CommandPreviewReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!operation) return;
    setBusy(true);
    setError(null);
    try {
      setPreview(await commands.preview(commandFromForm(event.currentTarget, operation)));
    } catch {
      setError("The record could not be previewed. Check every field against your broker fill.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="record-panel" aria-labelledby="record-title">
      <div className="section-heading"><div><p className="eyebrow">Your ledger</p><h2 id="record-title">Record a portfolio change</h2></div></div>
      <p className="muted">These controls only record activity you already completed at your broker.</p>
      <div className="action-grid">{ACTIONS.map(([value, label]) => (
        <button key={value} type="button" className={operation === value ? "action-button selected" : "action-button"} onClick={() => { setOperation(value); setPreview(null); setError(null); }}>{label}</button>
      ))}</div>
      {operation && <form className="record-form" onSubmit={(event) => { void submit(event); }}>
        <h3>{actionLabel(operation)}</h3>
        <Fields operation={operation} />
        <button className="primary-button" type="submit" disabled={busy}>{busy ? "Building preview…" : `Preview ${actionLabel(operation)}`}</button>
      </form>}
      {error && <p className="error" role="alert">{error}</p>}
      {preview && <PreviewDialog preview={preview} commands={commands} onApplied={onApplied} onClose={() => { setPreview(null); }} />}
    </section>
  );
}
