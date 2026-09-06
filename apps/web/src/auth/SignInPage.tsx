import { type FormEvent, useEffect, useRef, useState } from "react";

import { useAuth } from "./AuthProvider";

export function SignInPage() {
  const auth = useAuth();
  const emailInput = useRef<HTMLInputElement>(null);
  const [email, setEmail] = useState("");
  const [emailSent, setEmailSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [retrySeconds, setRetrySeconds] = useState(0);

  useEffect(() => {
    if (retrySeconds <= 0) return;
    const timer = window.setTimeout(() => setRetrySeconds((value) => Math.max(0, value - 1)), 1_000);
    return () => window.clearTimeout(timer);
  }, [retrySeconds]);

  async function requestLink() {
    setBusy(true);
    setMessage(null);
    try {
      await auth.sendOtp(email.trim());
    } catch {
      // Keep the browser response neutral; project-level signup disablement is authoritative.
    } finally {
      setEmailSent(true);
      setRetrySeconds(30);
      setMessage("If this is the owner account, look for a secure sign-in link from Supabase. Open it to continue.");
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!emailSent && !busy) void requestLink();
  }

  function changeEmail() {
    setEmailSent(false);
    setRetrySeconds(0);
    setMessage(null);
    window.setTimeout(() => {
      emailInput.current?.focus();
      emailInput.current?.select();
    }, 0);
  }

  return (
    <main className="auth-layout">
      <section className="auth-card" aria-labelledby="sign-in-title">
        <p className="eyebrow">Private workspace</p>
        <h1 id="sign-in-title">Personal Stock Agent</h1>
        <p className="lede">Owner-only research, portfolio context, and receipt history.</p>
        {auth.locked && <p className="notice" role="status">Screen privacy lock activated. Sign in again to continue.</p>}
        <form onSubmit={submit}>
          <label htmlFor="owner-email">Email</label>
          <input
            ref={emailInput}
            id="owner-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={emailSent || busy}
          />
          {message && <p className="form-message" role="status">{message}</p>}
          {!emailSent && (
            <button className="primary-button" disabled={busy} type="submit">
              {busy ? "Sending…" : "Send secure sign-in link"}
            </button>
          )}
          {emailSent && (
            <>
              <button
                className="text-button"
                disabled={busy || retrySeconds > 0}
                type="button"
                onClick={() => void requestLink()}
              >
                {busy ? "Sending…" : retrySeconds > 0 ? `Send another email in ${retrySeconds}s` : "Send another email"}
              </button>
              <button className="text-button" disabled={busy} type="button" onClick={changeEmail}>
                Use a different email
              </button>
            </>
          )}
        </form>
        <p className="boundary-note">No public registration · No brokerage access · Suggestions only</p>
      </section>
    </main>
  );
}
