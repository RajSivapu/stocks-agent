import { lazy, type ComponentType, Suspense, useCallback, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";

import type {
  AlertsView,
  CompanionView,
  IntelligenceView,
  IdeasView,
  PortfolioView,
  ReportDetailView,
  ReportsView,
  RunDetailView,
  RunsView,
  SystemView,
  TodayView,
} from "@stocks-agent/dashboard-contracts";

import { DashboardApiError, type DashboardClient } from "../api/client";
import { useDashboardResource } from "../api/useDashboardResource";
import { AuthProvider, type AuthClient, useAuth } from "../auth/AuthProvider";
import { SignInPage } from "../auth/SignInPage";
import { AsyncView } from "../components/AsyncView";
import { ThemeProvider } from "../theme/theme";
import { AppShell } from "./AppShell";
import { ErrorBoundary } from "./ErrorBoundary";

const PortfolioPage = lazy(() => import("../features/portfolio/PortfolioPage").then((module) => ({ default: module.PortfolioPage })));
const IdeasPage = lazy(() => import("../features/ideas/IdeasPage").then((module) => ({ default: module.IdeasPage })));
const IntelligencePage = lazy(() => import("../features/intelligence/IntelligencePage").then((module) => ({ default: module.IntelligencePage })));
const ReportsPage = lazy(() => import("../features/reports/ReportsPage").then((module) => ({ default: module.ReportsPage })));
const ReportDetailPage = lazy(() => import("../features/reports/ReportDetailPage").then((module) => ({ default: module.ReportDetailPage })));
const RunDetailPage = lazy(() => import("../features/runs/RunDetailPage").then((module) => ({ default: module.RunDetailPage })));
const SystemPage = lazy(() => import("../features/system/SystemPage").then((module) => ({ default: module.SystemPage })));

function ResourceRoute<T>({
  client,
  token,
  path,
  component: Component,
  onError,
}: {
  client: DashboardClient;
  token: string;
  path: string;
  component: ComponentType<{ data: T }>;
  onError(error: Error): void;
}) {
  const state = useDashboardResource<T>(client, path, token, onError);
  return <AsyncView state={state}>{(data) => <Component data={data} />}</AsyncView>;
}

function RunRoute({ client, token, onError }: { client: DashboardClient; token: string; onError(error: Error): void }) {
  const { id } = useParams();
  return <ResourceRoute<RunDetailView> client={client} token={token} path={`/v1/runs/${id ?? "invalid"}`} component={RunDetailPage} onError={onError} />;
}

function ReportRoute({ client, token, onError }: { client: DashboardClient; token: string; onError(error: Error): void }) {
  const { id } = useParams();
  return <ResourceRoute<ReportDetailView> client={client} token={token} path={`/v1/reports/${id ?? "invalid"}`} component={ReportDetailPage} onError={onError} />;
}

function PortfolioRoute({ client, token, overview, onError }: { client: DashboardClient; token: string; overview: TodayView | undefined; onError(error: Error): void }) {
  const portfolio = useDashboardResource<PortfolioView>(client, "/v1/portfolio", token, onError);
  const companion = useDashboardResource<CompanionView>(client, "/v1/companion", token, onError);
  return <AsyncView state={portfolio}>{(data) => <PortfolioPage data={data} overview={overview} companion={companion.status === "ready" ? companion.envelope.data : null} />}</AsyncView>;
}

function SystemRoute({ client, token, onError }: { client: DashboardClient; token: string; onError(error: Error): void }) {
  const system = useDashboardResource<SystemView>(client, "/v1/system", token, onError);
  const runs = useDashboardResource<RunsView>(client, "/v1/runs", token, onError);
  const alerts = useDashboardResource<AlertsView>(client, "/v1/alerts", token, onError);
  return <AsyncView state={system}>{(data) => <SystemPage data={data} runs={runs.status === "ready" ? runs.envelope.data : undefined} alerts={alerts.status === "ready" ? alerts.envelope.data : undefined} />}</AsyncView>;
}

function ProtectedApplication({ dashboardClient }: { dashboardClient: DashboardClient }) {
  const auth = useAuth();
  if (auth.loading) return <main className="initial-shell"><p>Opening private workspace…</p></main>;
  if (!auth.session || auth.locked) return <SignInPage />;
  return <AuthenticatedApplication dashboardClient={dashboardClient} token={auth.session.access_token} onSignOut={auth.signOut} />;
}

function AuthenticatedApplication({ dashboardClient, token, onSignOut }: { dashboardClient: DashboardClient; token: string; onSignOut(): Promise<void> }) {
  const [ownerDenied, setOwnerDenied] = useState(false);
  const onResourceError = useCallback((error: Error) => {
    if (!(error instanceof DashboardApiError)) return;
    if (error.status === 401) void onSignOut();
    if (error.status === 403 && error.code === "owner_only") setOwnerDenied(true);
  }, [onSignOut]);
  const today = useDashboardResource<TodayView>(dashboardClient, "/v1/today", token, onResourceError);
  if (ownerDenied) {
    return <main className="auth-layout"><section className="auth-card"><p className="eyebrow">Private workspace</p><h1>Owner-only access</h1><p>This dashboard is restricted to its owner.</p><button className="text-button" onClick={() => void onSignOut()}>Sign out</button></section></main>;
  }
  return (
    <AppShell dataTime={today.envelope?.data_as_of ?? null} freshness={today.envelope?.freshness ?? "unavailable"} onSignOut={() => void onSignOut()}>
      <Suspense fallback={<section className="state-card">Loading view…</section>}>
        <Routes>
          <Route path="/" element={<Navigate replace to="/portfolio" />} />
          <Route path="/portfolio" element={<PortfolioRoute client={dashboardClient} token={token} overview={today.status === "ready" ? today.envelope.data : undefined} onError={onResourceError} />} />
          <Route path="/ideas" element={<ResourceRoute<IdeasView> client={dashboardClient} token={token} path="/v1/ideas" component={IdeasPage} onError={onResourceError} />} />
          <Route path="/intelligence" element={<ResourceRoute<IntelligenceView> client={dashboardClient} token={token} path="/v1/intelligence" component={IntelligencePage} onError={onResourceError} />} />
          <Route path="/reports" element={<ResourceRoute<ReportsView> client={dashboardClient} token={token} path="/v1/reports" component={ReportsPage} onError={onResourceError} />} />
          <Route path="/reports/:id" element={<ReportRoute client={dashboardClient} token={token} onError={onResourceError} />} />
          <Route path="/companion" element={<Navigate replace to="/portfolio" />} />
          <Route path="/alerts" element={<Navigate replace to="/system" />} />
          <Route path="/runs" element={<Navigate replace to="/system" />} />
          <Route path="/runs/:id" element={<RunRoute client={dashboardClient} token={token} onError={onResourceError} />} />
          <Route path="/system" element={<SystemRoute client={dashboardClient} token={token} onError={onResourceError} />} />
          <Route path="*" element={<section className="state-card"><h1>Page not found</h1><p>This route is not part of the owner dashboard.</p></section>} />
        </Routes>
      </Suspense>
    </AppShell>
  );
}

export function App({ authClient, dashboardClient }: { authClient: AuthClient; dashboardClient: DashboardClient }) {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <AuthProvider client={authClient}>
          <BrowserRouter><ProtectedApplication dashboardClient={dashboardClient} /></BrowserRouter>
        </AuthProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}
