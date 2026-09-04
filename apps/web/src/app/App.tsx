import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AuthProvider, type AuthClient, useAuth } from "../auth/AuthProvider";
import { SignInPage } from "../auth/SignInPage";
import { ThemeProvider } from "../theme/theme";
import { AppShell } from "./AppShell";
import { ErrorBoundary } from "./ErrorBoundary";

function ProtectedApplication() {
  const auth = useAuth();
  if (auth.loading) return <main className="initial-shell"><p>Opening private workspace…</p></main>;
  if (!auth.session || auth.locked) return <SignInPage />;
  return (
    <AppShell dataTime={null} freshness="unavailable" onSignOut={() => void auth.signOut()}>
      <Routes>
        <Route path="*" element={(
          <section className="page-heading">
            <p className="eyebrow">Secure foundation</p>
            <h1>Dashboard views are connecting</h1>
            <p>Only persisted, receipt-backed information will appear here.</p>
          </section>
        )} />
      </Routes>
    </AppShell>
  );
}

export function App({ authClient }: { authClient: AuthClient }) {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <AuthProvider client={authClient}>
          <BrowserRouter><ProtectedApplication /></BrowserRouter>
        </AuthProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}
