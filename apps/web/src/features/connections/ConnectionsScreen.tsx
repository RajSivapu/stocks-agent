import { useCallback, useState } from "react";
import { StatusBadge } from "../../components/StatusBadge";
import {
  AppApiError,
  type ConnectionClient,
  type PairingCodeReceipt,
} from "../../lib/app-api";
import type { AgentConnection, DashboardRepository } from "../../lib/dashboard";
import { dateTime } from "../../lib/format";
import { useRepositoryData } from "../../lib/useRepositoryData";
import claudeKit from "../../../../../docs/connection-kits/claude-routine-v1.md?raw";

const CONSENT_VERSION = "provider-data-v1";

function safeSessionUrl(value: string | null): string | null {
  if (!value) return null;
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

function downloadConnectionKit() {
  const url = URL.createObjectURL(new Blob([claudeKit], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "stock-agent-claude-routine-v1.md";
  link.click();
  URL.revokeObjectURL(url);
}

function ConnectionCard({ connection, sessionUrl, busy, onActivate, onRevoke }: {
  connection: AgentConnection;
  sessionUrl: string | null;
  busy: boolean;
  onActivate: () => void;
  onRevoke: () => void;
}) {
  return (
    <article className="connection-card">
      <header className="connection-header">
        <div><p className="eyebrow">Contract v{String(connection.contractVersion)}</p><h2>Claude Routines</h2></div>
        <StatusBadge status={connection.status} />
      </header>
      <p>Suggestion-only, bounded context, independent Analyst and Checker, and server-owned policy.</p>
      <dl className="connection-facts">
        <div><dt>Last verified handshake</dt><dd>{dateTime(connection.lastHandshakeAt)}</dd></div>
        <div><dt>Credential</dt><dd>{connection.status === "revoked" ? "Revoked" : "Stored by digest; cannot be read back"}</dd></div>
      </dl>
      {sessionUrl && <a href={sessionUrl} target="_blank" rel="noreferrer noopener">Open handshake session</a>}
      <div className="button-row">
        {connection.status === "ready" && <button className="primary-button compact-button" type="button" disabled={busy} onClick={onActivate}>Activate as primary</button>}
        {connection.status !== "revoked" && <button className="danger-button compact-button" type="button" disabled={busy} onClick={onRevoke}>Revoke connection</button>}
      </div>
    </article>
  );
}

export function ConnectionsScreen({ repository, connectionClient }: {
  repository: DashboardRepository;
  connectionClient: ConnectionClient;
}) {
  const loader = useCallback(() => repository.loadConnections(), [repository]);
  const { state, reload } = useRepositoryData(loader);
  const [created, setCreated] = useState<{ connectionId: string; gatewayUrl: string } | null>(null);
  const [oneTimeCredential, setOneTimeCredential] = useState<string | null>(null);
  const [triggerUrl, setTriggerUrl] = useState("");
  const [triggerToken, setTriggerToken] = useState("");
  const [pairing, setPairing] = useState<PairingCodeReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await operation();
      await reload();
    } catch (caught) {
      setError(caught instanceof AppApiError
        ? "The request was not completed. Review the current connection state before retrying."
        : "Connection status is unavailable. No credential change is being assumed.");
    } finally {
      setBusy(false);
    }
  };

  const createConnection = () => act(async () => {
    const receipt = await connectionClient.createConnection(CONSENT_VERSION);
    setCreated({ connectionId: receipt.connectionId, gatewayUrl: receipt.gatewayUrl });
    setOneTimeCredential(receipt.gatewayCredential);
    setNotice("Disabled connection created. Save the gateway credential before leaving this page.");
  });

  const beginHandshake = (connectionId: string) => act(async () => {
    await connectionClient.beginConnectionHandshake(connectionId, triggerUrl.trim(), triggerToken);
    setTriggerToken("");
    setTriggerUrl("");
    setNotice("Real application-fired handshake queued. Reload after the Claude session finishes.");
  });

  const requestPairing = () => act(async () => {
    setPairing(await connectionClient.requestPairingCode());
    setNotice("Pairing code issued for one private Telegram chat.");
  });

  if (state.kind === "loading") return <section className="workspace-card" aria-busy="true"><div role="status">Loading connections…</div></section>;
  if (state.kind === "error") return <section className="workspace-card"><h1>Connections</h1><p role="alert">Connection status is unavailable. No connection is being treated as active.</p><button type="button" className="secondary-button" onClick={() => { void reload(); }}>Try again</button></section>;

  const current = state.data.connections.find((item) => item.id === created?.connectionId) ??
    state.data.connections.find((item) => item.status !== "revoked") ?? null;
  const configurableConnectionId = current?.id ?? created?.connectionId ?? null;
  const handshakeRun = state.data.handshakeRuns.find((item) => item.slotId !== null) ?? null;
  const sessionUrl = safeSessionUrl(handshakeRun?.providerSessionUrl ?? null);
  const canConfigure = Boolean(configurableConnectionId) &&
    (!current || current.status === "disabled" || current.status === "testing");
  const telegramActive = state.data.telegram?.status === "active";

  return (
    <div className="feature-stack">
      <section className="workspace-card connection-intro">
        <p className="eyebrow">One verified provider</p><h1>Connections</h1>
        <p>Release one supports Claude Routines only. The app owns market timing; Claude supplies bounded research and cannot place trades.</p>
        <div className="provider-warning"><strong>Research preview limitation</strong><span>Requires an eligible Claude plan. Runs use your subscription and daily Routine allowance. This app cannot see the remaining allowance, and a green provider run does not prove the Stock Agent callback succeeded.</span></div>
        <button type="button" className="secondary-button compact-button" onClick={downloadConnectionKit}>Download connection kit</button>
      </section>

      <section className="workspace-card">
        <div className="section-heading"><div><p className="eyebrow">Analysis provider</p><h2>Claude Routines</h2></div>{!current && <button className="primary-button compact-button" type="button" disabled={busy} onClick={() => { void createConnection(); }}>Start Claude setup</button>}</div>
        {current && <ConnectionCard connection={current} sessionUrl={sessionUrl} busy={busy}
          onActivate={() => { void act(async () => { await connectionClient.activateConnection(current.id); setNotice("Connection activated as the primary provider."); }); }}
          onRevoke={() => { void act(async () => { await connectionClient.revokeConnection(current.id); setCreated(null); setOneTimeCredential(null); setNotice("Connection revoked in both directions."); }); }} />}

        {created && oneTimeCredential && <div className="credential-once" role="status">
          <strong>Gateway credential — shown once</strong>
          <code>{oneTimeCredential}</code>
          <p>Store this as a host-bound Claude API credential for <code>{new URL(created.gatewayUrl).hostname}</code>. Never use an environment variable.</p>
          <button type="button" className="secondary-button compact-button" onClick={() => { setOneTimeCredential(null); }}>I saved the credential</button>
        </div>}
        {current && current.status === "disabled" && !oneTimeCredential && !created && <p className="error">The one-time credential is unavailable. Revoke this disabled connection and start again.</p>}

        {canConfigure && configurableConnectionId && <form className="trigger-form" onSubmit={(event) => { event.preventDefault(); void beginHandshake(configurableConnectionId); }}>
          <h3>Test the production path</h3>
          <label>Routine fire URL<input required aria-label="Routine fire URL" type="url" autoComplete="off" value={triggerUrl} onChange={(event) => { setTriggerUrl(event.target.value); }} placeholder="https://api.anthropic.com/v1/claude_code/routines/trig_…/fire" /></label>
          <label>One-time Routine token<input required minLength={24} maxLength={500} aria-label="One-time Routine token" type="password" autoComplete="new-password" value={triggerToken} onChange={(event) => { setTriggerToken(event.target.value); }} /></label>
          <button type="submit" className="primary-button compact-button" disabled={busy}>Test connection</button>
          <p className="muted">This must be the application-fired no-write handshake—not curl or Claude's Run now button.</p>
        </form>}
        <p className="rotation-note">Rotation creates a new credential and a fresh handshake. Revoked credentials can never run again.</p>
      </section>

      <section className="workspace-card">
        <div className="section-heading"><div><p className="eyebrow">Private recordkeeping channel</p><h2>Telegram</h2></div><StatusBadge status={telegramActive ? "active" : "not linked"} /></div>
        <p>Pair one private chat to record portfolio changes through the same preview-and-confirm workflow used by the web app.</p>
        <div className="button-row">
          <button type="button" className="primary-button compact-button" disabled={busy} onClick={() => { void requestPairing(); }}>New pairing code</button>
          {telegramActive && <button type="button" className="danger-button compact-button" disabled={busy} onClick={() => { void act(async () => { await connectionClient.unlinkTelegram(); setPairing(null); setNotice("Telegram unlinked and pending Telegram confirmations cancelled."); }); }}>Unlink Telegram</button>}
        </div>
        {pairing && <div className="pairing-code" role="status"><span>Send <strong>/pair {pairing.code}</strong> to the bot in a private chat.</span><small>Expires {dateTime(pairing.expiresAt)}. A new code invalidates this one.</small></div>}
      </section>
      {notice && <p className="success" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
