import { lazy, type ComponentType, Suspense } from "react";
import { BrowserRouter, Route, Routes, useParams } from "react-router-dom";

import type {
  AlertsView,
  CompanionView,
  IdeasView,
  PortfolioView,
  RunDetailView,
  RunsView,
  SystemView,
  TodayView,
} from "@stocks-agent/dashboard-contracts";

import type { DashboardClient } from "../api/client";
import { useDashboardResource } from "../api/useDashboardResource";
import { AuthProvider, type AuthClient, useAuth } from "../auth/AuthProvider";
import { SignInPage } from "../auth/SignInPage";
import { AsyncView } from "../components/AsyncView";
import { ThemeProvider } from "../theme/theme";
import { AppShell } from "./AppShell";
import { ErrorBoundary } from "./ErrorBoundary";

const TodayPage = lazy(() => import("../features/today/TodayPage").then((module) => ({ default: module.TodayPage })));
const PortfolioPage = lazy(() => import("../features/portfolio/PortfolioPage").then((module) => ({ default: module.PortfolioPage })));
const IdeasPage = lazy(() => import("../features/ideas/IdeasPage").then((module) => ({ default: module.IdeasPage })));
const CompanionPage = lazy(() => import("../features/companion/CompanionPage").then((module) => ({ default: module.CompanionPage })));
const AlertsPage = lazy(() => import("../features/alerts/AlertsPage").then((module) => ({ default: module.AlertsPage })));
const RunsPage = lazy(() => import("../features/runs/RunsPage").then((module) => ({ default: module.RunsPage })));
const RunDetailPage = lazy(() => import("../features/runs/RunDetailPage").then((module) => ({ default: module.RunDetailPage })));
const SystemPage = lazy(() => import("../features/system/SystemPage").then((module) => ({ default: module.SystemPage })));

function ResourceRoute<T>({
  client,
  token,
  path,
  component: Component,
}: {
  client: DashboardClient;
  token: string;
  path: string;
  component: ComponentType<{ data: T }>;
}) {
  const state = useDashboardResource<T>(client, path, token);
  return <AsyncView state={state}>{(data) => <Component data={data} />}</AsyncView>;
}

function RunRoute({ client, token }: { client: DashboardClient; token: string }) {
  const { id } = useParams();
  return <ResourceRoute<RunDetailView> client={client} token={token} path={`/v1/runs/${id ?? "invalid"}`} component={RunDetailPage} />;
}

function ProtectedApplication({ dashboardClient }: { dashboardClient: DashboardClient }) {
  const auth = useAuth();
  if (auth.loading) return <main className="initial-shell"><p>Opening private workspace…</p></main>;
  if (!auth.session || auth.locked) return <SignInPage />;
  return <AuthenticatedApplication dashboardClient={dashboardClient} token={auth.session.access_token} onSignOut={() => void auth.signOut()} />;
}

function AuthenticatedApplication({ dashboardClient, token, onSignOut }: { dashboardClient: DashboardClient; token: string; onSignOut(): void }) {
  const today = useDashboardResource<TodayView>(dashboardClient, "/v1/today", token);
  return (
    <AppShell dataTime={today.envelope?.data_as_of ?? null} freshness={today.envelope?.freshness ?? "unavailable"} onSignOut={onSignOut}>
      <Suspense fallback={<section className="state-card">Loading view…</section>}>
        <Routes>
          <Route path="/" element={<AsyncView state={today}>{(data) => <TodayPage data={data} />}</AsyncView>} />
          <Route path="/portfolio" element={<ResourceRoute<PortfolioView> client={dashboardClient} token={token} path="/v1/portfolio" component={PortfolioPage} />} />
          <Route path="/ideas" element={<ResourceRoute<IdeasView> client={dashboardClient} token={token} path="/v1/ideas" component={IdeasPage} />} />
          <Route path="/companion" element={<ResourceRoute<CompanionView> client={dashboardClient} token={token} path="/v1/companion" component={CompanionPage} />} />
          <Route path="/alerts" element={<ResourceRoute<AlertsView> client={dashboardClient} token={token} path="/v1/alerts" component={AlertsPage} />} />
          <Route path="/runs" element={<ResourceRoute<RunsView> client={dashboardClient} token={token} path="/v1/runs" component={RunsPage} />} />
          <Route path="/runs/:id" element={<RunRoute client={dashboardClient} token={token} />} />
          <Route path="/system" element={<ResourceRoute<SystemView> client={dashboardClient} token={token} path="/v1/system" component={SystemPage} />} />
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
