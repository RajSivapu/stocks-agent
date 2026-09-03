import { useCallback, useEffect, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  NavLink,
  Route,
  Routes,
} from "react-router-dom";
import { ConsentGate } from "./auth/ConsentGate";
import { SignIn } from "./auth/SignIn";
import type { SessionService, ViewerState } from "./lib/session";
import "./styles.css";

type AppProps = { session: SessionService };

const ROUTES = [
  ["/", "Today"],
  ["/portfolio", "Portfolio"],
  ["/activity", "Activity"],
  ["/research", "Research"],
  ["/runs", "Runs"],
  ["/connections", "Connections"],
  ["/settings", "Settings"],
] as const;

function Workspace({ viewer }: { viewer: Extract<ViewerState, { kind: "ready" }> }) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand"><span>SA</span> Stock Agent</div>
        <nav aria-label="Primary navigation">
          {ROUTES.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="account-chip">
          <strong>{viewer.displayName}</strong>
          <span>{viewer.email}</span>
        </div>
      </aside>
      <main className="workspace">
        <Routes>
          {ROUTES.map(([path, label]) => (
            <Route key={path} path={path} element={
              <section className="workspace-card">
                <p className="eyebrow">Private workspace</p>
                <h1>{label}</h1>
                <p>{label} workspace</p>
              </section>
            } />
          ))}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function SessionBoundary({ session }: AppProps) {
  const [viewer, setViewer] = useState<ViewerState | { kind: "loading" }>({ kind: "loading" });
  const refresh = useCallback(async () => {
    setViewer(await session.loadViewer());
  }, [session]);

  useEffect(() => {
    let active = true;
    const safeRefresh = async () => {
      const next = await session.loadViewer();
      if (active) setViewer(next);
    };
    void safeRefresh();
    const unsubscribe = session.subscribe(() => { void safeRefresh(); });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [session]);

  if (viewer.kind === "loading") {
    return <main className="loading-screen"><p role="status">Confirming your session…</p></main>;
  }
  if (viewer.kind === "signed-out") return <SignIn session={session} onVerified={refresh} />;
  if (viewer.kind === "consent-required") return <ConsentGate viewer={viewer} />;
  if (viewer.kind === "unavailable") {
    return <main className="loading-screen"><p role="alert">Your private workspace is temporarily unavailable.</p></main>;
  }
  return <Workspace viewer={viewer} />;
}

export function App({ session }: AppProps) {
  return <BrowserRouter><SessionBoundary session={session} /></BrowserRouter>;
}
