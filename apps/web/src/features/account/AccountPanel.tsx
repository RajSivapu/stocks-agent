import { useEffect, useState } from "react";
import type {
  AccountClient,
  AccountStatus,
  DeletionPreview,
  StepUpChallenge,
} from "../../lib/app-api";
import { AppApiError } from "../../lib/app-api";
import type { SessionService, ViewerState } from "../../lib/session";


type Identity = {
  email: string;
  displayName: string;
};

function lifecycleError(error: unknown): string {
  if (error instanceof AppApiError && error.code === "STEP_UP_REQUIRED") {
    return "That secure code is no longer fresh. Start again for a new code.";
  }
  return "The account request was not completed. No success is being assumed.";
}

async function saveExport(accountClient: AccountClient, kind: "account" | "ledger"): Promise<void> {
  const download = await accountClient.downloadExport(kind);
  const url = URL.createObjectURL(download.blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = download.filename;
    anchor.rel = "noopener";
    anchor.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

function ExportButtons({ accountClient }: { accountClient: AccountClient }) {
  const [busy, setBusy] = useState<"account" | "ledger" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const download = async (kind: "account" | "ledger") => {
    setBusy(kind); setError(null);
    try { await saveExport(accountClient, kind); }
    catch (caught) { setError(lifecycleError(caught)); }
    finally { setBusy(null); }
  };
  return <div>
    <div className="account-actions">
      <button type="button" className="secondary-button" disabled={busy !== null} onClick={() => { void download("ledger"); }}>
        {busy === "ledger" ? "Preparing ledger…" : "Download ledger CSV"}
      </button>
      <button type="button" className="secondary-button" disabled={busy !== null} onClick={() => { void download("account"); }}>
        {busy === "account" ? "Preparing account…" : "Download account JSON"}
      </button>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}

export function StepUpCode({
  identity, challenge, session, accountClient, onVerified, onCancel,
}: {
  identity: Identity;
  challenge: StepUpChallenge;
  session: SessionService;
  accountClient: AccountClient;
  onVerified: (receiptId: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [otp, setOtp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const verify = async () => {
    setBusy(true); setError(null);
    try {
      await session.verifyOtp(identity.email, otp);
      const receipt = await accountClient.completeStepUp(challenge.challengeId);
      await onVerified(receipt.receiptId);
    } catch (caught) { setError(lifecycleError(caught)); }
    finally { setBusy(false); }
  };
  return <div className="step-up-box">
    <p>A fresh six-digit code was sent to <strong>{identity.email}</strong>. Verifying it creates a new, five-minute security receipt bound to this browser session.</p>
    <label>Fresh six-digit code
      <input aria-label="Fresh six-digit code" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
        value={otp} onChange={(event) => { setOtp(event.target.value.replace(/\D/g, "").slice(0, 6)); }} />
    </label>
    <div className="account-actions">
      <button type="button" className="primary-button compact-button" disabled={busy || !/^\d{6}$/.test(otp)} onClick={() => { void verify(); }}>
        {busy ? "Verifying…" : "Verify fresh code"}
      </button>
      <button type="button" className="secondary-button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}

export function AccountPanel({ viewer, session, accountClient }: {
  viewer: Extract<ViewerState, { kind: "ready" }>;
  session: SessionService;
  accountClient: AccountClient;
}) {
  const [challenge, setChallenge] = useState<StepUpChallenge | null>(null);
  const [preview, setPreview] = useState<(DeletionPreview & { receiptId: string }) | null>(null);
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const start = async () => {
    setBusy(true); setError(null);
    try {
      const next = await accountClient.beginStepUp();
      await session.requestOtp(viewer.email);
      setChallenge(next);
    } catch (caught) { setError(lifecycleError(caught)); }
    finally { setBusy(false); }
  };
  const prepare = async (receiptId: string) => {
    const next = await accountClient.requestDeletion(receiptId);
    setChallenge(null);
    setPreview({ ...next, receiptId });
  };
  const confirm = async () => {
    if (!preview) return;
    setBusy(true); setError(null);
    try {
      await accountClient.confirmDeletion(preview.deletionRequestId, preview.receiptId, phrase);
      await session.signOut();
    } catch (caught) { setError(lifecycleError(caught)); }
    finally { setBusy(false); }
  };

  return <section className="settings-block account-panel">
    <h2>Account and data</h2>
    <p>Export your records at any time. The JSON export excludes reusable credentials and raw Telegram identifiers; the CSV is the immutable transaction ledger with correction links.</p>
    <ExportButtons accountClient={accountClient} />
    <div className="disclosure-links">
      <a href="/privacy.html" target="_blank" rel="noopener noreferrer">Privacy and data lifecycle</a>
      <a href="/risk-disclosure.html" target="_blank" rel="noopener noreferrer">Investment risk disclosure</a>
    </div>
    <div className="danger-zone">
      <h3>Delete account</h3>
      <p>Deletion immediately disconnects analysis and Telegram, cancels unconfirmed commands, and starts a 72-hour cancellation window. Data is then deleted no later than seven days.</p>
      {!challenge && !preview && <button type="button" className="danger-button" disabled={busy} onClick={() => { void start(); }}>
        {busy ? "Starting secure check…" : "Start account deletion"}
      </button>}
      {challenge && <StepUpCode identity={viewer} challenge={challenge} session={session}
        accountClient={accountClient} onVerified={prepare} onCancel={() => { setChallenge(null); }} />}
      {preview && <div className="step-up-box">
        <p>This action disconnects provider triggers and Telegram. It does not place or cancel any brokerage order. Type the exact phrase shown below.</p>
        <code>{preview.confirmationPhrase}</code>
        <label>Type DELETE MY ACCOUNT
          <input aria-label="Type DELETE MY ACCOUNT" autoComplete="off" value={phrase}
            onChange={(event) => { setPhrase(event.target.value); }} />
        </label>
        <div className="account-actions">
          <button type="button" className="danger-button" disabled={busy || phrase !== preview.confirmationPhrase}
            onClick={() => { void confirm(); }}>Confirm account deletion</button>
          <button type="button" className="secondary-button" disabled={busy} onClick={() => { setPreview(null); setPhrase(""); }}>Keep account</button>
        </div>
      </div>}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  </section>;
}

export function DeletionPendingGate({ viewer, session, accountClient, onCancelled }: {
  viewer: Extract<ViewerState, { kind: "deletion-pending" }>;
  session: SessionService;
  accountClient: AccountClient;
  onCancelled: () => Promise<void> | void;
}) {
  const [status, setStatus] = useState<AccountStatus | null>(null);
  const [challenge, setChallenge] = useState<StepUpChallenge | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    accountClient.loadAccountStatus().then((value) => { if (active) setStatus(value); })
      .catch((caught) => { if (active) setError(lifecycleError(caught)); });
    return () => { active = false; };
  }, [accountClient]);
  const startCancel = async () => {
    setError(null);
    try {
      const next = await accountClient.beginStepUp();
      await session.requestOtp(viewer.email);
      setChallenge(next);
    } catch (caught) { setError(lifecycleError(caught)); }
  };
  const cancel = async (receiptId: string) => {
    await accountClient.cancelDeletion(receiptId);
    await onCancelled();
  };
  return <main className="gate-layout">
    <section className="gate-card deletion-gate">
      <p className="eyebrow">Restricted account</p>
      <h1>Account deletion is pending</h1>
      <p>Your portfolio workspace, provider triggers, scheduled runs, and Telegram link are disabled. The 72-hour cancellation window does not restore old credentials automatically.</p>
      {status?.cancelUntil && <p><strong>Cancellation available until:</strong> {new Date(status.cancelUntil).toLocaleString()}</p>}
      {status?.deleteBy && <p><strong>Active-data deletion deadline:</strong> {new Date(status.deleteBy).toLocaleString()}</p>}
      <p>Recent bot messages are eligible for best-effort cleanup. Older Telegram messages may need to be removed manually from your chat history.</p>
      <ExportButtons accountClient={accountClient} />
      {!challenge && <button type="button" className="primary-button compact-button" onClick={() => { void startCancel(); }}>
        Cancel deletion with email code
      </button>}
      {challenge && <StepUpCode identity={viewer} challenge={challenge} session={session}
        accountClient={accountClient} onVerified={cancel} onCancel={() => { setChallenge(null); }} />}
      <button type="button" className="text-button" onClick={() => { void session.signOut(); }}>Sign out</button>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  </main>;
}
