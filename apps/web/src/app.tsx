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
import type { AppApiClient } from "./lib/app-api";
import type { DashboardRepository } from "./lib/dashboard";
import type { SessionService, ViewerState } from "./lib/session";
import { ActivityScreen } from "./features/activity/ActivityScreen";
import { PortfolioScreen } from "./features/portfolio/PortfolioScreen";
import { TodayScreen } from "./features/today/TodayScreen";
import { ResearchScreen } from "./features/research/ResearchScreen";
import { RunsScreen } from "./features/runs/RunsScreen";
import { ConnectionsScreen } from "./features/connections/ConnectionsScreen";
import { SettingsScreen } from "./features/settings/SettingsScreen";
import "./styles.css";

type AppProps = {
  session: SessionService;
  repository: DashboardRepository;
  commands: AppApiClient;
};

const ROUTES = [
  ["/", "Today"],
  ["/portfolio", "Portfolio"],
  ["/activity", "Activity"],
  ["/research", "Research"],
  ["/runs", "Runs"],
  ["/connections", "Connections"],
  ["/settings", "Settings"],
] as const;

function Workspace({ viewer, repository, commands }: {
  viewer: Extract<ViewerState, { kind: "ready" }>;
  repository: DashboardRepository;
  commands: AppApiClient;
}) {
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
          <Route path="/" element={<TodayScreen repository={repository} />} />
          <Route path="/portfolio" element={<PortfolioScreen repository={repository} commands={commands} />} />
          <Route path="/activity" element={<ActivityScreen repository={repository} />} />
          <Route path="/research" element={<ResearchScreen repository={repository} />} />
          <Route path="/runs" element={<RunsScreen repository={repository} runClient={commands} />} />
          <Route path="/connections" element={<ConnectionsScreen repository={repository} connectionClient={commands} />} />
          <Route path="/settings" element={<SettingsScreen repository={repository} settingsClient={commands} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function SessionBoundary({ session, repository, commands }: AppProps) {
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
  return <Workspace viewer={viewer} repository={repository} commands={commands} />;
}

export function App({ session, repository, commands }: AppProps) {
  return <BrowserRouter><SessionBoundary session={session} repository={repository} commands={commands} /></BrowserRouter>;
}
