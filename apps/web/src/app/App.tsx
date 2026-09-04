import { lazy, type ComponentType, Suspense, useCallback, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";

import type {
  AlertsView,
  CompanionView,
  Freshness,
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
import { useDashboardResource, type ResourceState } from "../api/useDashboardResource";
import { AuthProvider, type AuthClient, useAuth } from "../auth/AuthProvider";
import { SignInPage } from "../auth/SignInPage";
import { AsyncView } from "../components/AsyncView";
import { ThemeProvider } from "../theme/theme";
import { AppShell, type ViewStatus } from "./AppShell";
import { ErrorBoundary } from "./ErrorBoundary";

const PortfolioPage = lazy(() => import("../features/portfolio/PortfolioPage").then((module) => ({ default: module.PortfolioPage })));
const IdeasPage = lazy(() => import("../features/ideas/IdeasPage").then((module) => ({ default: module.IdeasPage })));
const IntelligencePage = lazy(() => import("../features/intelligence/IntelligencePage").then((module) => ({ default: module.IntelligencePage })));
const ReportsPage = lazy(() => import("../features/reports/ReportsPage").then((module) => ({ default: module.ReportsPage })));
const ReportDetailPage = lazy(() => import("../features/reports/ReportDetailPage").then((module) => ({ default: module.ReportDetailPage })));
const RunDetailPage = lazy(() => import("../features/runs/RunDetailPage").then((module) => ({ default: module.RunDetailPage })));
const SystemPage = lazy(() => import("../features/system/SystemPage").then((module) => ({ default: module.SystemPage })));

interface BannerState {
  dataTime: string | null;
  freshness: Freshness;
  viewStatus: ViewStatus;
}

const freshnessRank: Record<Freshness, number> = { fresh: 0, stale: 1, partial: 2, unavailable: 3 };

function bannerState<T>(primary: ResourceState<T>, children: ResourceState<unknown>[] = []): BannerState {
  if (primary.status === "loading") return { dataTime: null, freshness: "unavailable", viewStatus: "loading" };
  if (primary.status === "error") return { dataTime: null, freshness: "unavailable", viewStatus: "error" };
  if (children.some((child) => child.status === "loading")) {
    return { dataTime: primary.envelope.data_as_of, freshness: primary.envelope.freshness, viewStatus: "loading" };
  }
  if (children.some((child) => child.status === "error")) {
    return { dataTime: primary.envelope.data_as_of, freshness: primary.envelope.freshness === "unavailable" ? "unavailable" : "partial", viewStatus: "partial-error" };
  }
  const freshness = children.reduce<Freshness>((current, child) => {
    if (child.status !== "ready") return current;
    return freshnessRank[child.envelope.freshness] > freshnessRank[current] ? child.envelope.freshness : current;
  }, primary.envelope.freshness);
  return { dataTime: primary.envelope.data_as_of, freshness, viewStatus: "ready" };
}

function ResourceRoute<T>({
  client,
  token,
  path,
  component: Component,
  onError,
  onSignOut,
}: {
  client: DashboardClient;
  token: string;
  path: string;
  component: ComponentType<{ data: T }>;
  onError(error: Error): void;
  onSignOut(): void;
}) {
  const state = useDashboardResource<T>(client, path, token, onError);
  const banner = bannerState(state);
  return <AppShell {...banner} onSignOut={onSignOut}><AsyncView state={state}>{(data) => <Component data={data} />}</AsyncView></AppShell>;
}

function RunRoute({ client, token, onError, onSignOut }: { client: DashboardClient; token: string; onError(error: Error): void; onSignOut(): void }) {
  const { id } = useParams();
  return <ResourceRoute<RunDetailView> client={client} token={token} path={`/v1/runs/${id ?? "invalid"}`} component={RunDetailPage} onError={onError} onSignOut={onSignOut} />;
}

function ReportRoute({ client, token, onError, onSignOut }: { client: DashboardClient; token: string; onError(error: Error): void; onSignOut(): void }) {
  const { id } = useParams();
  return <ResourceRoute<ReportDetailView> client={client} token={token} path={`/v1/reports/${id ?? "invalid"}`} component={ReportDetailPage} onError={onError} onSignOut={onSignOut} />;
}

function PortfolioRoute({ client, token, onError, onSignOut }: { client: DashboardClient; token: string; onError(error: Error): void; onSignOut(): void }) {
  const portfolio = useDashboardResource<PortfolioView>(client, "/v1/portfolio", token, onError);
  const overview = useDashboardResource<TodayView>(client, "/v1/today", token, onError);
  const companion = useDashboardResource<CompanionView>(client, "/v1/companion", token, onError);
  const banner = bannerState(portfolio, [overview, companion]);
  return <AppShell {...banner} onSignOut={onSignOut}><AsyncView state={portfolio}>{(data) => <PortfolioPage data={data} overviewState={overview} companionState={companion} />}</AsyncView></AppShell>;
}

function SystemRoute({ client, token, onError, onSignOut }: { client: DashboardClient; token: string; onError(error: Error): void; onSignOut(): void }) {
  const system = useDashboardResource<SystemView>(client, "/v1/system", token, onError);
  const runs = useDashboardResource<RunsView>(client, "/v1/runs", token, onError);
  const alerts = useDashboardResource<AlertsView>(client, "/v1/alerts", token, onError);
  const banner = bannerState(system, [runs, alerts]);
  return <AppShell {...banner} onSignOut={onSignOut}><AsyncView state={system}>{(data) => <SystemPage data={data} runsState={runs} alertsState={alerts} />}</AsyncView></AppShell>;
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
  if (ownerDenied) {
    return <main className="auth-layout"><section className="auth-card"><p className="eyebrow">Private workspace</p><h1>Owner-only access</h1><p>This dashboard is restricted to its owner.</p><button className="text-button" onClick={() => void onSignOut()}>Sign out</button></section></main>;
  }
  const signOut = () => void onSignOut();
  return (
      <Suspense fallback={<main className="initial-shell"><p>Loading owner view…</p></main>}>
        <Routes>
          <Route path="/" element={<Navigate replace to="/portfolio" />} />
          <Route path="/portfolio" element={<PortfolioRoute client={dashboardClient} token={token} onError={onResourceError} onSignOut={signOut} />} />
          <Route path="/ideas" element={<ResourceRoute<IdeasView> client={dashboardClient} token={token} path="/v1/ideas" component={IdeasPage} onError={onResourceError} onSignOut={signOut} />} />
          <Route path="/intelligence" element={<ResourceRoute<IntelligenceView> client={dashboardClient} token={token} path="/v1/intelligence" component={IntelligencePage} onError={onResourceError} onSignOut={signOut} />} />
          <Route path="/reports" element={<ResourceRoute<ReportsView> client={dashboardClient} token={token} path="/v1/reports" component={ReportsPage} onError={onResourceError} onSignOut={signOut} />} />
          <Route path="/reports/:id" element={<ReportRoute client={dashboardClient} token={token} onError={onResourceError} onSignOut={signOut} />} />
          <Route path="/companion" element={<Navigate replace to="/portfolio" />} />
          <Route path="/alerts" element={<Navigate replace to="/system" />} />
          <Route path="/runs" element={<Navigate replace to="/system" />} />
          <Route path="/runs/:id" element={<RunRoute client={dashboardClient} token={token} onError={onResourceError} onSignOut={signOut} />} />
          <Route path="/system" element={<SystemRoute client={dashboardClient} token={token} onError={onResourceError} onSignOut={signOut} />} />
          <Route path="*" element={<section className="state-card"><h1>Page not found</h1><p>This route is not part of the owner dashboard.</p></section>} />
        </Routes>
      </Suspense>
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
