import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";

import { ThemeProvider } from "../theme/theme";
import { AppShell } from "./AppShell";

it("shows exactly five primary owner surfaces and an explicit freshness bar", () => {
  render(
    <MemoryRouter>
      <ThemeProvider>
        <AppShell dataTime="2026-09-03T18:00:00.000Z" freshness="fresh">
          <h2>Page content</h2>
        </AppShell>
      </ThemeProvider>
    </MemoryRouter>,
  );
  expect(screen.getByRole("navigation", { name: /primary/i })).toBeVisible();
  const primaryLinks = screen.getAllByRole("link");
  expect(primaryLinks).toHaveLength(5);
  for (const label of ["Portfolio", "Ideas", "Intelligence", "Reports", "System / Receipts"]) {
    expect(screen.getByRole("link", { name: label })).toBeVisible();
  }
  for (const retiredLabel of ["Today", "Companion", "Alerts", "Runs"]) {
    expect(screen.queryByRole("link", { name: retiredLabel })).not.toBeInTheDocument();
  }
  expect(screen.getByRole("status")).toHaveTextContent(/data through/i);
  expect(screen.getByText(/suggestion only/i)).toBeVisible();
});
