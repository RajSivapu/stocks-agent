import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";

import { ThemeProvider } from "../theme/theme";
import { AppShell } from "./AppShell";

it("keeps three daily destinations primary and places audit views under Advanced", () => {
  render(
    <MemoryRouter initialEntries={["/system"]}>
      <ThemeProvider>
        <AppShell dataTime="2026-09-03T18:00:00.000Z" freshness="fresh">
          <h2>Page content</h2>
        </AppShell>
      </ThemeProvider>
    </MemoryRouter>,
  );
  expect(screen.getByRole("navigation", { name: /primary/i })).toBeVisible();
  const primary = screen.getByRole("navigation", { name: /primary/i });
  const primaryLinks = within(primary).getAllByRole("link");
  expect(primaryLinks).toHaveLength(3);
  for (const label of ["Overview", "Ideas", "Reports"]) {
    expect(within(primary).getByRole("link", { name: label })).toBeVisible();
  }
  const advanced = screen.getByRole("navigation", { name: /advanced/i });
  expect(within(advanced).getByRole("link", { name: "Intelligence" })).toBeInTheDocument();
  expect(within(advanced).getByRole("link", { name: "Receipts" })).toBeInTheDocument();
  for (const retiredLabel of ["Today", "Companion", "Alerts", "Runs", "System / Receipts"]) {
    expect(screen.queryByRole("link", { name: retiredLabel })).not.toBeInTheDocument();
  }
  expect(screen.getByRole("status")).toHaveTextContent(/data through/i);
  expect(screen.getByText(/suggestion only/i)).toBeVisible();
});
