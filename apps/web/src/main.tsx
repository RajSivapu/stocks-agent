import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./styles.css";

function InitialShell() {
  return (
    <main className="initial-shell">
      <p className="eyebrow">Owner-only · Suggestion-only</p>
      <h1>Personal Stock Agent</h1>
      <p>The secure dashboard is loading.</p>
    </main>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("application root is missing");
createRoot(root).render(<StrictMode><InitialShell /></StrictMode>);
