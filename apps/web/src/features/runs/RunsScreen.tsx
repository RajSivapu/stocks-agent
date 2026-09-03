import { useCallback, useState } from "react";
import { StatusBadge } from "../../components/StatusBadge";
import { AppApiError, type RunControlClient, type RunRequestReceipt } from "../../lib/app-api";
import type { DashboardRepository, RunTimelineItem } from "../../lib/dashboard";
import { dateOnly, dateTime, titleCase } from "../../lib/format";
import { useRepositoryData } from "../../lib/useRepositoryData";

function providerSessionHref(value: string | null): string | null {
  if (!value || value.length > 300) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "claude.ai" &&
        /^\/code\/session_[A-Za-z0-9_-]{6,200}$/.test(url.pathname) &&
        !url.search && !url.hash && !url.username && !url.password
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

function WriteCounts({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values).filter(([, value]) => Number.isSafeInteger(value) && (value as number) >= 0);
  return entries.length === 0 ? <span>No writes recorded</span> : <span>{entries.map(([key, value]) => `${titleCase(key)} ${String(value)}`).join(" · ")}</span>;
}

function RunCard({ run }: { run: RunTimelineItem }) {
  const sessionHref = providerSessionHref(run.providerSessionUrl);
  return (
    <article className="run-card" aria-label={`${run.phase} run ${run.marketDate ?? "date unavailable"}`}>
      <header className="run-header"><div><p className="eyebrow">{dateOnly(run.marketDate)}</p><h2>{titleCase(run.phase)}</h2></div><StatusBadge status={run.runStatus ?? run.slotStatus} /></header>
      <ol className="run-steps">
        <li><span>Expected</span><strong>{run.expectedAt ? dateTime(run.expectedAt) : titleCase(run.purpose)}</strong><small>Window ends {dateTime(run.windowEndsAt)}</small></li>
        <li><span>Provider trigger</span><strong>{titleCase(run.triggerStatus ?? run.slotStatus)}</strong><small>{run.triggerResponseStatus === null ? "No response code recorded" : `HTTP ${String(run.triggerResponseStatus)}`}</small>{sessionHref && <a href={sessionHref} target="_blank" rel="noreferrer noopener">Open provider session</a>}</li>
        <li><span>Inbound run</span><strong>{run.runStatus ? titleCase(run.runStatus) : "Not started"}</strong><small>{run.startedAt ? `Started ${dateTime(run.startedAt)}` : "No inbound start recorded"}</small></li>
        <li><span>Evidence</span><strong><StatusBadge status={run.evidenceStatus} /></strong><small>Data as of {dateTime(run.dataAsOf)}</small></li>
        <li><span>Policy and persistence</span><strong>{run.policyStates.length > 0 ? run.policyStates.map(titleCase).join(", ") : "No policy receipt"}</strong><small><WriteCounts values={run.writeCounts} /></small></li>
        <li><span>Publication</span><strong>{run.publicationStatus ? titleCase(run.publicationStatus) : "No publication"}</strong><small>{run.telegramMessageIds.length > 0 ? run.telegramMessageIds.map((id) => `Message ${id}`).join(" · ") : "No delivered message ID"}</small></li>
      </ol>
      {run.errorCode && <p className="run-error"><StatusBadge status={run.errorCode} /></p>}
      {run.summary && <p className="run-summary">{run.summary}</p>}
      <footer className="receipt-meta">{run.provider ? `${titleCase(run.provider)} · ${run.model ?? "model unavailable"}` : "Provider not recorded"}{run.runId ? ` · Run ${run.runId.slice(0, 8)}` : ""}</footer>
    </article>
  );
}

function queuedMessage(receipt: RunRequestReceipt) {
  return <div className="safety-banner" role="status"><strong>Queued as a fresh on-demand run</strong><span>Expected by {dateTime(receipt.expectedBy)}. No Telegram notification will be sent; results stay in this session and Research history.</span></div>;
}

export function RunsScreen({ repository, runClient }: {
  repository: DashboardRepository;
  runClient: RunControlClient;
}) {
  const loader = useCallback(() => repository.loadRuns(), [repository]);
  const { state, reload } = useRepositoryData(loader);
  const [requesting, setRequesting] = useState(false);
  const [receipt, setReceipt] = useState<RunRequestReceipt | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);

  const requestRun = async () => {
    setRequesting(true);
    setReceipt(null);
    setRequestError(null);
    try {
      const next = await runClient.requestRun();
      setReceipt(next);
      await reload();
    } catch (error) {
      setRequestError(error instanceof AppApiError && error.code === "RATE_LIMITED"
        ? "Only one on-demand run per hour is allowed. Check the timeline for the run already queued."
        : "The run was not queued. Check connection health before trying again.");
    } finally {
      setRequesting(false);
    }
  };

  if (state.kind === "loading") return <section className="workspace-card" aria-busy="true"><div role="status" aria-label="Loading runs">Loading runs…</div></section>;
  if (state.kind === "error") return <section className="workspace-card"><h1>Runs</h1><p role="alert">Run history is unavailable. Status is unknown until the server can be read.</p><button type="button" className="secondary-button" onClick={() => { void reload(); }}>Try again</button></section>;
  return (
    <div className="feature-stack">
      <section className="workspace-card run-intro"><div className="section-heading"><div><p className="eyebrow">Provider lifecycle</p><h1>Runs</h1></div><button type="button" className="primary-button compact-button" disabled={requesting} onClick={() => { void requestRun(); }}>{requesting ? "Queueing…" : "Run analysis now"}</button></div><p>Expected slots, provider starts, evidence, policy, persistence, and actual delivery receipts in one timeline.</p><p className="muted">On-demand analysis uses a new rate-limited slot and never sends Telegram.</p>{receipt && queuedMessage(receipt)}{requestError && <p className="error" role="alert">{requestError}</p>}</section>
      {state.data.runs.length === 0 ? <section className="workspace-card empty-state"><h2>No run history yet</h2><p>Connect and activate the supported provider, or request an on-demand analysis.</p></section> : state.data.runs.map((run) => <RunCard run={run} key={run.slotId ?? run.runId} />)}
    </div>
  );
}
