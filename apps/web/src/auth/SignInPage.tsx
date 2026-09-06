import { type FormEvent, useEffect, useState } from "react";

import { useAuth } from "./AuthProvider";

const OTP_LENGTH = 6;
const OTP_PATTERN = `[0-9]{${OTP_LENGTH}}`;

export function SignInPage() {
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [retrySeconds, setRetrySeconds] = useState(0);

  useEffect(() => {
    if (retrySeconds <= 0) return;
    const timer = window.setTimeout(() => setRetrySeconds((value) => Math.max(0, value - 1)), 1_000);
    return () => window.clearTimeout(timer);
  }, [retrySeconds]);

  async function requestCode() {
    try {
      await auth.sendOtp(email.trim());
    } catch {
      // Keep the browser response neutral; project-level signup disablement is authoritative.
    }
    setCodeSent(true);
    setRetrySeconds(30);
    setMessage("If this is the owner account, use the sign-in link in the email or enter its six-digit code.");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      if (!codeSent) {
        await requestCode();
      } else {
        await auth.verifyOtp(email.trim(), code.trim());
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Sign-in is unavailable.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-card" aria-labelledby="sign-in-title">
        <p className="eyebrow">Private workspace</p>
        <h1 id="sign-in-title">Personal Stock Agent</h1>
        <p className="lede">Owner-only research, portfolio context, and receipt history.</p>
        {auth.locked && <p className="notice" role="status">Screen privacy lock activated. Sign in again to continue.</p>}
        <form onSubmit={(event) => void submit(event)}>
          <label htmlFor="owner-email">Email</label>
          <input
            id="owner-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={codeSent || busy}
          />
          {codeSent && (
            <>
              <label htmlFor="owner-code">Six-digit code (if shown)</label>
              <input
                id="owner-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern={OTP_PATTERN}
                maxLength={OTP_LENGTH}
                required
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, OTP_LENGTH))}
              />
            </>
          )}
          <button className="primary-button" disabled={busy} type="submit">
            {busy ? "Please wait…" : codeSent ? "Verify code" : "Send code or link"}
          </button>
          {codeSent && (
            <button
              className="text-button"
              disabled={busy || retrySeconds > 0}
              type="button"
              onClick={() => void requestCode()}
            >
              {retrySeconds > 0 ? `Send another email in ${retrySeconds}s` : "Send another email"}
            </button>
          )}
        </form>
        {message && <p className="form-message" role="status">{message}</p>}
        <p className="boundary-note">No public registration · No brokerage access · Suggestions only</p>
      </section>
    </main>
  );
}
