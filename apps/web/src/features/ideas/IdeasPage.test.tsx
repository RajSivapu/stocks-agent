import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import type { IdeaView, IdeasView } from "@stocks-agent/dashboard-contracts";

import { IdeasPage } from "./IdeasPage";

function idea(overrides: Partial<IdeaView>): IdeaView {
  return {
    id: "idea",
    ticker: "MSFT",
    profile: "balanced",
    final_action: "watch",
    policy_status: "approved",
    policy_version: 17,
    confidence: "medium",
    entry_zone_low: "410",
    entry_zone_high: "420",
    stop: "395",
    target: "455",
    valid_until: "2026-09-10",
    bull_case: "Revenue held up.",
    bear_case: "Valuation remains high.",
    decisive_factor: "Fresh evidence",
    invalidation: "Close below 395",
    reason_codes: [],
    analyst_complete: true,
    checker_complete: true,
    sources: [],
    intelligence_run_id: "7d834dbd-75bb-4313-931f-09732f003932",
    evidence_as_of: "2026-09-04T13:00:00.000Z",
    outcome: null,
    ...overrides,
  };
}

it("preserves API persistence order when choosing the current idea per ticker", async () => {
  const user = userEvent.setup();
  const data: IdeasView = {
    ideas: [
      idea({ id: "msft-current", final_action: "watch", decisive_factor: "MSFT current", evidence_as_of: null }),
      idea({ id: "nvda-current", ticker: "NVDA", final_action: "hold", decisive_factor: "NVDA current", evidence_as_of: "2026-09-03T13:00:00.000Z" }),
      idea({ id: "msft-old", final_action: "buy", decisive_factor: "Older setup with newer evidence", evidence_as_of: "2026-09-04T13:00:00.000Z" }),
    ],
  };

  const { container } = render(<IdeasPage data={data} />);

  const summaries = container.querySelectorAll(".decision-summary");
  expect(within(summaries[0] as HTMLElement).getByText("MSFT current")).toBeVisible();
  expect(within(summaries[1] as HTMLElement).getByText("NVDA current")).toBeVisible();
  expect(screen.queryByText("Older setup with newer evidence")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /current.*2/i })).toHaveAttribute("aria-pressed", "true");

  await user.click(screen.getByRole("button", { name: /history.*1/i }));
  expect(within(container.querySelector(".decision-summary") as HTMLElement).getByText("Older setup with newer evidence")).toBeVisible();
  expect(screen.queryByText("MSFT current")).not.toBeInTheDocument();
});

it("shows only approved, allowlisted trade actions under Action now", async () => {
  const user = userEvent.setup();
  const data: IdeasView = {
    ideas: [
      idea({ id: "approved-buy", ticker: "BUY", final_action: "buy", policy_status: "approved" }),
      idea({ id: "avoid", ticker: "AVOID", final_action: "avoid", policy_status: "approved" }),
      idea({ id: "legacy-buy", ticker: "LEGACY", final_action: "buy", policy_status: "legacy_unverified" }),
      idea({ id: "vetoed-sell", ticker: "VETO", final_action: "sell", policy_status: "vetoed" }),
    ],
  };

  render(<IdeasPage data={data} />);
  await user.click(screen.getByRole("button", { name: /action now.*1/i }));
  expect(screen.getByText("BUY")).toBeVisible();
  expect(screen.queryByText("AVOID")).not.toBeInTheDocument();
  expect(screen.queryByText("LEGACY")).not.toBeInTheDocument();
  expect(screen.queryByText("VETO")).not.toBeInTheDocument();
});

it("filters current ideas and keeps research evidence collapsed until requested", async () => {
  const user = userEvent.setup();
  const data: IdeasView = {
    ideas: [
      idea({ id: "msft-current", final_action: "watch", decisive_factor: "MSFT current" }),
      idea({ id: "nvda-current", ticker: "NVDA", final_action: "hold", decisive_factor: "NVDA current", evidence_as_of: "2026-09-03T13:00:00.000Z" }),
    ],
  };

  const { container } = render(<IdeasPage data={data} />);
  await user.click(screen.getByRole("button", { name: /watch.*1/i }));
  expect(within(container.querySelector(".decision-summary") as HTMLElement).getByText("MSFT current")).toBeVisible();
  expect(screen.queryByText("NVDA current")).not.toBeInTheDocument();

  const details = container.querySelector("details.idea-details");
  expect(details).not.toHaveAttribute("open");
  await user.click(screen.getByText(/research and receipt details/i));
  expect(details).toHaveAttribute("open");
  expect(screen.getByRole("heading", { name: /analyst review/i })).toBeVisible();
});
