import type { PropsWithChildren } from "react";
import { NavLink } from "react-router-dom";

import type { Freshness } from "@stocks-agent/dashboard-contracts";

import { ThemeControl } from "../theme/ThemeControl";

const pages = [
  ["Today", "/"],
  ["Portfolio", "/portfolio"],
  ["Ideas", "/ideas"],
  ["Companion", "/companion"],
  ["Alerts", "/alerts"],
  ["Runs", "/runs"],
  ["System", "/system"],
] as const;

export function AppShell({
  dataTime,
  freshness,
  onSignOut,
  children,
}: PropsWithChildren<{ dataTime: string | null; freshness: Freshness; onSignOut?: () => void }>) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div>
          <p className="eyebrow">MarketPal</p>
          <p className="product-name">Personal Stock Agent</p>
          <p className="owner-chip">Private · Owner only</p>
        </div>
        <nav aria-label="Primary">
          {pages.map(([label, path]) => (
            <NavLink key={path} end={path === "/"} to={path}>{label}</NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <ThemeControl />
          {onSignOut && <button className="text-button" onClick={onSignOut}>Sign out</button>}
          <p>Suggestion only.<br />You decide and place every trade.</p>
        </div>
      </aside>
      <div className="workspace">
        <header className="data-bar" role="status">
          <span className={`freshness-dot freshness-${freshness}`} aria-hidden="true" />
          <strong>{freshness === "fresh" ? "Receipt current" : freshness}</strong>
          <span>{dataTime ? `Data through ${new Date(dataTime).toLocaleString()}` : "No supported data time"}</span>
        </header>
        <main id="main-content" className="page-content">{children}</main>
      </div>
    </div>
  );
}
