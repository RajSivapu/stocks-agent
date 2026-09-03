import { useCallback, useEffect, useMemo, useState } from "react";
import { AppApiError, type SettingsClient, type SettingsUpdate } from "../../lib/app-api";
import type { DashboardRepository, SettingsSnapshot } from "../../lib/dashboard";
import { useRepositoryData } from "../../lib/useRepositoryData";

const TIMEZONES = [
  "America/Chicago", "America/New_York", "America/Denver", "America/Los_Angeles",
  "America/Phoenix", "America/Anchorage", "Pacific/Honolulu",
] as const;

function localTime(iso: string, timezone: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: timezone, hour: "numeric", minute: "2-digit", timeZoneName: "short",
    }).format(new Date(iso));
  } catch {
    return "Unavailable";
  }
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="setting-toggle"><input type="checkbox" checked={checked} onChange={(event) => { onChange(event.target.checked); }} /><span>{label}</span></label>;
}

function SettingsForm({ initial, settingsClient, onSaved }: {
  initial: SettingsSnapshot;
  settingsClient: SettingsClient;
  onSaved: () => Promise<void>;
}) {
  const [value, setValue] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setValue(initial); }, [initial]);
  const update = <K extends keyof SettingsSnapshot>(key: K, next: SettingsSnapshot[K]) => {
    setValue((current) => ({ ...current, [key]: next }));
  };
  const anchors = useMemo(() => ({
    pre: localTime("2026-09-03T11:30:00Z", value.timezone),
    intra: localTime("2026-09-03T17:00:00Z", value.timezone),
    post: localTime("2026-09-03T20:10:00Z", value.timezone),
  }), [value.timezone]);

  const submit = async () => {
    setSaving(true); setNotice(null); setError(null);
    const request: SettingsUpdate = {
      display_name: value.displayName.trim(), timezone: value.timezone,
      notify_pre_market: value.notifyPreMarket, notify_intraday: value.notifyIntraday,
      notify_post_market: value.notifyPostMarket, notify_operational: value.notifyOperational,
      schedule_pre_market: value.schedulePreMarket, schedule_intraday: value.scheduleIntraday,
      schedule_post_market: value.schedulePostMarket,
    };
    try {
      await settingsClient.updateSettings(request);
      setNotice("Settings saved.");
      await onSaved();
    } catch (caught) {
      setError(caught instanceof AppApiError
        ? "Settings were not saved. Review the fields and try again."
        : "Settings status is unavailable; no change is being assumed.");
    } finally {
      setSaving(false);
    }
  };

  return <form className="settings-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
    <section className="settings-block"><h2>Profile</h2><div className="field-grid"><label>Display name<input aria-label="Display name" required maxLength={120} value={value.displayName} onChange={(event) => { update("displayName", event.target.value); }} /></label><label>Display timezone<select aria-label="Display timezone" value={value.timezone} onChange={(event) => { update("timezone", event.target.value); }}>{TIMEZONES.map((timezone) => <option value={timezone} key={timezone}>{timezone}</option>)}</select></label></div></section>
    <section className="settings-block"><h2>Analysis phases</h2><p>Eastern market anchors are server-owned and translated below. Early close sessions move the intraday and post-market anchors automatically.</p><div className="anchor-grid"><div><strong>Pre-market</strong><span>7:30 AM Eastern</span><small>{anchors.pre} · {value.timezone}</small></div><div><strong>Intraday</strong><span>1:00 PM Eastern</span><small>{anchors.intra} · {value.timezone}</small></div><div><strong>Post-market</strong><span>4:10 PM Eastern</span><small>{anchors.post} · {value.timezone}</small></div></div><div className="toggle-grid"><Check label="Run pre-market analysis" checked={value.schedulePreMarket} onChange={(next) => { update("schedulePreMarket", next); }} /><Check label="Run intraday analysis" checked={value.scheduleIntraday} onChange={(next) => { update("scheduleIntraday", next); }} /><Check label="Run post-market analysis" checked={value.schedulePostMarket} onChange={(next) => { update("schedulePostMarket", next); }} /></div></section>
    <section className="settings-block"><h2>Telegram notifications</h2><div className="toggle-grid"><Check label="Send pre-market research" checked={value.notifyPreMarket} onChange={(next) => { update("notifyPreMarket", next); }} /><Check label="Send intraday alerts" checked={value.notifyIntraday} onChange={(next) => { update("notifyIntraday", next); }} /><Check label="Send post-market research" checked={value.notifyPostMarket} onChange={(next) => { update("notifyPostMarket", next); }} /><Check label="Send operational alerts" checked={value.notifyOperational} onChange={(next) => { update("notifyOperational", next); }} /></div></section>
    <div className="policy-lock"><strong>Safety policy cannot be changed here.</strong><span>Evidence freshness, position limits, deterministic vetoes, and the record-only boundary remain server-owned.</span></div>
    <button type="submit" className="primary-button compact-button" disabled={saving || !value.displayName.trim()}>{saving ? "Saving…" : "Save settings"}</button>
    {notice && <p className="success" role="status">{notice}</p>}{error && <p className="error" role="alert">{error}</p>}
  </form>;
}

export function SettingsScreen({ repository, settingsClient }: { repository: DashboardRepository; settingsClient: SettingsClient }) {
  const loader = useCallback(() => repository.loadSettings(), [repository]);
  const { state, reload } = useRepositoryData(loader);
  if (state.kind === "loading") return <section className="workspace-card" aria-busy="true"><div role="status">Loading settings…</div></section>;
  if (state.kind === "error") return <section className="workspace-card"><h1>Settings</h1><p role="alert">Settings are unavailable. No defaults are being assumed.</p><button className="secondary-button" type="button" onClick={() => { void reload(); }}>Try again</button></section>;
  return <div className="feature-stack"><section className="workspace-card settings-intro"><p className="eyebrow">Owner preferences</p><h1>Settings</h1><p>Choose when analysis runs and which server-approved messages reach Telegram. These controls never change market anchors or investment-safety rules.</p></section><section className="workspace-card"><SettingsForm initial={state.data} settingsClient={settingsClient} onSaved={reload} /></section></div>;
}
