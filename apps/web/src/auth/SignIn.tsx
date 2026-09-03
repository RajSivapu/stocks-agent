import { useState, type SyntheticEvent } from "react";
import { canonicalEmail, type SessionService } from "../lib/session";

function friendlyError(): string {
  return "That didn’t work. Check the details and try again without reusing an old code.";
}

export function SignIn({ session, onVerified }: {
  session: SessionService;
  onVerified: () => Promise<void>;
}) {
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const normalizedEmail = () => canonicalEmail(email);
  const run = async (action: () => Promise<void>, success: string) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      setNotice(success);
    } catch {
      setError(friendlyError());
    } finally {
      setBusy(false);
    }
  };

  const requestCode = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    await run(async () => {
      const value = normalizedEmail();
      await session.requestOtp(value);
      setEmail(value);
      setSent(true);
    }, "If this address has an invitation, a six-digit code is on its way.");
  };

  const requestDesktopLink = async () => {
    await run(
      () => session.requestDesktopLink(
        normalizedEmail(),
        `${window.location.origin}/auth/callback`,
      ),
      "If this address has an invitation, check the email for a desktop sign-in link.",
    );
  };

  const verify = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    await run(async () => {
      await session.verifyOtp(normalizedEmail(), token);
      await onVerified();
    }, "Signed in securely.");
  };

  return (
    <main className="auth-layout">
      <section className="brand-panel" aria-label="Product introduction">
        <div className="brand-mark" aria-hidden="true">SA</div>
        <p className="eyebrow">Private decision support</p>
        <h1>Stock Agent</h1>
        <p className="brand-copy">
          Fresh research, truthful alerts, and a portfolio ledger you control.
          No brokerage access. No automatic trades.
        </p>
        <div className="trust-note">
          <span aria-hidden="true">●</span>
          Invite-only pilot · data isolated per account
        </div>
      </section>

      <section className="auth-card" aria-labelledby="sign-in-title">
        <p className="eyebrow">Welcome back</p>
        <h2 id="sign-in-title">Sign in to your account</h2>
        <p className="muted">Use the email address that received your invitation.</p>

        <form onSubmit={(event) => { void requestCode(event); }} className="auth-form">
          <label htmlFor="email">Email address</label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            maxLength={254}
            value={email}
            onChange={(event) => { setEmail(event.target.value); }}
            required
          />
          <button className="primary-button" type="submit" disabled={busy}>
            Send secure code
          </button>
        </form>

        {sent && (
          <form onSubmit={(event) => { void verify(event); }} className="auth-form code-form">
            <label htmlFor="otp">Six-digit code</label>
            <input
              id="otp"
              name="otp"
              className="otp-input"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              value={token}
              onChange={(event) => { setToken(event.target.value.replace(/\D/g, "").slice(0, 6)); }}
              required
            />
            <button className="primary-button" type="submit" disabled={busy || !/^\d{6}$/.test(token)}>
              Verify and continue
            </button>
          </form>
        )}

        <button className="text-button" type="button" disabled={busy || email.trim().length === 0} onClick={() => { void requestDesktopLink(); }}>
          Email a desktop sign-in link instead
        </button>
        {notice && <p className="notice" role="status">{notice}</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <p className="security-copy">Codes expire in 10 minutes. Stock Agent never asks for a password.</p>
      </section>
    </main>
  );
}
