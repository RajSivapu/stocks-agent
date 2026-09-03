import type { ViewerState } from "../lib/session";

export function ConsentGate({ viewer }: {
  viewer: Extract<ViewerState, { kind: "consent-required" }>;
}) {
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
        <p className="muted">Consent controls arrive in the Account release gate. Private screens remain locked until acceptance is recorded.</p>
      </section>
    </main>
  );
}
