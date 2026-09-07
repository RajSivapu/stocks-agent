import { type FormEvent, useEffect, useRef, useState } from "react";

import { useAuth } from "./AuthProvider";

type SignInMode = "password" | "link" | "recovery";

const sentMessages: Record<Exclude<SignInMode, "password">, string> = {
  link: "If this is the owner account, look for a secure sign-in link from Supabase. Open it to continue.",
  recovery: "If this is the owner account, look for a password setup link from Supabase. Open it to choose a new password.",
};

export function SignInPage() {
  const auth = useAuth();
  const emailInput = useRef<HTMLInputElement>(null);
  const passwordInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<SignInMode>("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [emailSent, setEmailSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retrySeconds, setRetrySeconds] = useState(0);

  useEffect(() => {
    if (retrySeconds <= 0) return;
    const timer = window.setTimeout(() => setRetrySeconds((value) => Math.max(0, value - 1)), 1_000);
    return () => window.clearTimeout(timer);
  }, [retrySeconds]);

  function focusEmail() {
    window.setTimeout(() => {
      emailInput.current?.focus();
      emailInput.current?.select();
    }, 0);
  }

  function chooseMode(nextMode: SignInMode) {
    setMode(nextMode);
    setEmailSent(false);
    setRetrySeconds(0);
    setMessage(null);
    setError(null);
    setPassword("");
    if (nextMode === "password" && email.trim()) {
      window.setTimeout(() => passwordInput.current?.focus(), 0);
    } else {
      focusEmail();
    }
  }

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      await auth.signInWithPassword(email.trim(), password);
    } catch {
      setError("Email or password is incorrect. Try again or set up a new password.");
    } finally {
      setBusy(false);
    }
  }

  async function requestEmail() {
    if (mode === "password") return;
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      if (mode === "link") {
        await auth.sendSignInLink(email.trim());
      } else {
        await auth.sendPasswordReset(email.trim());
      }
    } catch {
      // Keep both responses neutral so the page never reveals whether an account exists.
    } finally {
      setEmailSent(true);
      setRetrySeconds(30);
      setMessage(sentMessages[mode]);
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || emailSent) return;
    if (mode === "password") void signIn();
    else void requestEmail();
  }

  function changeEmail() {
    setEmailSent(false);
    setRetrySeconds(0);
    setMessage(null);
    setError(null);
    focusEmail();
  }

  const emailActionLabel = mode === "recovery" ? "Email password setup link" : "Send secure sign-in link";

  return (
    <main className="auth-layout">
      <section className="auth-card" aria-labelledby="sign-in-title">
        <p className="eyebrow">Private workspace</p>
        <h1 id="sign-in-title">Personal Stock Agent</h1>
        <p className="lede">Owner-only research, portfolio context, and receipt history.</p>
        {auth.locked && <p className="notice" role="status">Screen privacy lock activated. Sign in again to continue.</p>}

        {mode === "password" && (
          <>
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
                disabled={busy}
              />
              <label htmlFor="owner-password">Password</label>
              <div className="password-field">
                <input
                  ref={passwordInput}
                  id="owner-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={busy}
                  aria-describedby={error ? "sign-in-error" : undefined}
                />
                <button
                  className="password-toggle"
                  type="button"
                  aria-pressed={showPassword}
                  onClick={() => setShowPassword((shown) => !shown)}
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>
              {error && <p id="sign-in-error" className="form-error" role="alert">{error}</p>}
              <button className="primary-button" disabled={busy} type="submit">
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </form>
            <div className="auth-options" aria-label="Other sign-in options">
              <button className="text-button auth-text-button" type="button" onClick={() => chooseMode("recovery")}>
                Set up or reset password
              </button>
              <button className="text-button auth-text-button" type="button" onClick={() => chooseMode("link")}>
                Use an email sign-in link
              </button>
            </div>
          </>
        )}

        {mode !== "password" && (
          <>
            <div className="auth-mode-heading">
              <h2>{mode === "recovery" ? "Set up or reset your password" : "Sign in with an email link"}</h2>
              <p>
                {mode === "recovery"
                  ? "Supabase will email one secure link. Open it to choose a password on this page."
                  : "Supabase will email a secure link that signs you in when you open it."}
              </p>
            </div>
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
                  {busy ? "Sending…" : emailActionLabel}
                </button>
              )}
              {emailSent && (
                <>
                  <button
                    className="text-button auth-text-button"
                    disabled={busy || retrySeconds > 0}
                    type="button"
                    onClick={() => void requestEmail()}
                  >
                    {busy ? "Sending…" : retrySeconds > 0 ? `Send another email in ${retrySeconds}s` : "Send another email"}
                  </button>
                  <button className="text-button auth-text-button" disabled={busy} type="button" onClick={changeEmail}>
                    Use a different email
                  </button>
                </>
              )}
            </form>
            <button className="text-button auth-text-button auth-back-button" type="button" onClick={() => chooseMode("password")}>
              Back to email and password
            </button>
          </>
        )}

        <p className="boundary-note">No public registration · No brokerage access · Suggestions only</p>
      </section>
    </main>
  );
}
