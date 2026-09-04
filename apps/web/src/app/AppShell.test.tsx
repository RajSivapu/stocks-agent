import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";

import { ThemeProvider } from "../theme/theme";
import { AppShell } from "./AppShell";

it("exposes all seven pages and an explicit freshness bar", () => {
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
  for (const label of ["Today", "Portfolio", "Ideas", "Companion", "Alerts", "Runs", "System"]) {
    expect(screen.getByRole("link", { name: label })).toBeVisible();
  }
  expect(screen.getByRole("status")).toHaveTextContent(/data through/i);
  expect(screen.getByText(/suggestion only/i)).toBeVisible();
});
