import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { createBrowserDashboardClient } from "./api/client";
import { createBrowserAuthClient } from "./auth/AuthProvider";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("application root is missing");
const reactRoot = createRoot(root);

if (import.meta.env.DEV && import.meta.env.VITE_E2E_FIXTURES === "1") {
  void import("./test/FixtureApp").then(({ FixtureApp }) => {
    reactRoot.render(<StrictMode><FixtureApp /></StrictMode>);
  });
} else {
  reactRoot.render(
    <StrictMode><App authClient={createBrowserAuthClient()} dashboardClient={createBrowserDashboardClient()} /></StrictMode>,
  );
}
