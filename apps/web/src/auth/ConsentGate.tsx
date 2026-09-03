import type { ViewerState } from "../lib/session";
import type { AccountClient } from "../lib/app-api";
import { useState } from "react";
import { CURRENT_CONSENT_VERSION } from "../lib/session";

export function ConsentGate({ viewer, accountClient, onAccepted }: {
  viewer: Extract<ViewerState, { kind: "consent-required" }>;
  accountClient: AccountClient;
  onAccepted: () => Promise<void> | void;
}) {
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = async () => {
    setBusy(true); setError(null);
    try {
      await accountClient.acceptConsent(CURRENT_CONSENT_VERSION);
      await onAccepted();
    } catch {
      setError("Consent was not recorded. Your private workspace remains locked.");
    } finally { setBusy(false); }
  };
  return (
    <main className="gate-layout">
      <section className="gate-card">
        <p className="eyebrow">One step before your dashboard</p>
        <h1>Review before continuing</h1>
        <p>Hello {viewer.displayName}. Your account is private, but using an analysis provider moves a bounded data packet outside Stock Agent.</p>
        <ul>
          <li>Your provider stores its own run transcript under your provider account.</li>
          <li>The operator can access production records and encrypted recovery backups.</li>
          <li>Market-data vendors receive the ticker symbols needed for current quotes.</li>
          <li>Old Telegram messages may need to be removed by you.</li>
        </ul>
        <label className="consent-check"><input type="checkbox" checked={accepted} onChange={(event) => { setAccepted(event.target.checked); }} />
          <span>I understand these disclosures and consent to the bounded provider and market-data processing described above.</span>
        </label>
        <button type="button" className="primary-button compact-button" disabled={!accepted || busy} onClick={() => { void confirm(); }}>
          {busy ? "Recording consent…" : "Accept and continue"}
        </button>
        {error && <p className="error" role="alert">{error}</p>}
      </section>
    </main>
  );
}
