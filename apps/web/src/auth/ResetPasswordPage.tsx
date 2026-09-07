import { type FormEvent, useState } from "react";

import { useAuth } from "./AuthProvider";

const MINIMUM_PASSWORD_LENGTH = 12;

export function ResetPasswordPage() {
  const auth = useAuth();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [showPasswords, setShowPasswords] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (password.length < MINIMUM_PASSWORD_LENGTH) {
      setError(`Use at least ${MINIMUM_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (password !== confirmation) {
      setError("The passwords do not match.");
      return;
    }

    setBusy(true);
    try {
      await auth.updatePassword(password);
    } catch {
      setError("The password could not be saved. Request a new setup link and try again.");
      setBusy(false);
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-card" aria-labelledby="reset-password-title">
        <p className="eyebrow">Private workspace</p>
        <h1 id="reset-password-title">Choose a new password</h1>
        <p className="lede">This password will be used for future sign-ins on this page.</p>
        <form onSubmit={submit}>
          <label htmlFor="new-password">New password</label>
          <div className="password-field">
            <input
              id="new-password"
              type={showPasswords ? "text" : "password"}
              autoComplete="new-password"
              minLength={MINIMUM_PASSWORD_LENGTH}
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={busy}
              aria-describedby={error ? "password-requirements reset-password-error" : "password-requirements"}
            />
            <button
              className="password-toggle"
              type="button"
              aria-pressed={showPasswords}
              onClick={() => setShowPasswords((shown) => !shown)}
            >
              {showPasswords ? "Hide" : "Show"}
            </button>
          </div>
          <p id="password-requirements" className="field-help">Use at least 12 characters. A password manager is recommended.</p>

          <label htmlFor="confirm-password">Confirm new password</label>
          <input
            id="confirm-password"
            type={showPasswords ? "text" : "password"}
            autoComplete="new-password"
            minLength={MINIMUM_PASSWORD_LENGTH}
            required
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            disabled={busy}
            aria-describedby={error ? "reset-password-error" : undefined}
          />
          {error && <p id="reset-password-error" className="form-error" role="alert">{error}</p>}
          <button className="primary-button" disabled={busy} type="submit">
            {busy ? "Saving…" : "Save password"}
          </button>
        </form>
        <button
          className="text-button auth-text-button auth-back-button"
          disabled={busy}
          type="button"
          onClick={() => void auth.signOut()}
        >
          Cancel and return to sign in
        </button>
        <p className="boundary-note">No public registration · No brokerage access · Suggestions only</p>
      </section>
    </main>
  );
}
