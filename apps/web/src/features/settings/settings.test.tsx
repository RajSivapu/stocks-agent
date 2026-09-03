import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SettingsScreen } from "./SettingsScreen";
import type { AppApiClient } from "../../lib/app-api";
import type { DashboardRepository, SettingsSnapshot } from "../../lib/dashboard";

const initial: SettingsSnapshot = {
  displayName: "Raj",
  timezone: "America/Chicago",
  notifyPreMarket: true,
  notifyIntraday: true,
  notifyPostMarket: false,
  notifyOperational: true,
  primaryConnectionId: "11111111-1111-4111-8111-111111111111",
  scheduleTimezone: "America/Chicago",
  schedulePreMarket: true,
  scheduleIntraday: true,
  schedulePostMarket: true,
};

function harness() {
  const repository = {
    loadSettings: vi.fn(() => Promise.resolve(initial)),
  } as unknown as DashboardRepository;
  const updateSettings = vi.fn((value: Record<string, unknown>) => Promise.resolve({
    ...initial,
    ...value,
    status: "updated" as const,
  }));
  const client = { updateSettings } as unknown as AppApiClient;
  return { repository, client, updateSettings };
}

describe("owner settings", () => {
  it("shows server-owned Eastern anchors converted for display without editable cron", async () => {
    const { repository, client } = harness();
    render(<SettingsScreen repository={repository} settingsClient={client} />);

    expect(await screen.findByRole("heading", { name: /^settings$/i })).toBeVisible();
    expect(screen.getByText(/7:30 AM Eastern/i)).toBeVisible();
    expect(screen.getByText(/6:30 AM.*Chicago/i)).toBeVisible();
    expect(screen.getByText(/early close/i)).toBeVisible();
    expect(screen.queryByLabelText(/cron|schedule expression/i)).not.toBeInTheDocument();
  });

  it("updates only display, phase, and notification preferences", async () => {
    const { repository, client, updateSettings } = harness();
    const user = userEvent.setup();
    render(<SettingsScreen repository={repository} settingsClient={client} />);

    await screen.findByDisplayValue("Raj");
    await user.clear(screen.getByLabelText(/display name/i));
    await user.type(screen.getByLabelText(/display name/i), "Rupesh");
    await user.selectOptions(screen.getByLabelText(/display timezone/i), "America/New_York");
    await user.click(screen.getByLabelText(/run intraday analysis/i));
    await user.click(screen.getByLabelText(/send post-market research/i));
    await user.click(screen.getByRole("button", { name: /save settings/i }));

    await waitFor(() => {
      expect(updateSettings).toHaveBeenCalledWith({
        display_name: "Rupesh",
        timezone: "America/New_York",
        notify_pre_market: true,
        notify_intraday: true,
        notify_post_market: true,
        notify_operational: true,
        schedule_pre_market: true,
        schedule_intraday: false,
        schedule_post_market: true,
      });
    });
    expect(await screen.findByText(/settings saved/i)).toBeVisible();
  });

  it("does not expose a control that can alter safety policy or brokerage behavior", async () => {
    const { repository, client } = harness();
    render(<SettingsScreen repository={repository} settingsClient={client} />);
    await screen.findByRole("heading", { name: /^settings$/i });
    expect(screen.queryByText(/broker|auto.?trade|risk ceiling|policy version|model prompt/i)).not.toBeInTheDocument();
    expect(screen.getByText(/safety policy cannot be changed here/i)).toBeVisible();
  });
});
