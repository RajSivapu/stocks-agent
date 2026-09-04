import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { ThemeControl } from "./ThemeControl";
import { ThemeProvider } from "./theme";

it("defaults to system and stores only a valid manual theme name", async () => {
  const user = userEvent.setup();
  render(<ThemeProvider><ThemeControl /></ThemeProvider>);
  expect(screen.getByRole("radio", { name: "System" })).toBeChecked();
  expect(document.documentElement.dataset.theme).toBe("system");
  await user.click(screen.getByRole("radio", { name: "Dark" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(window.localStorage.getItem("personal-stock-agent-theme")).toBe("dark");
  expect(window.localStorage).toHaveLength(1);
});

it("ignores an invalid stored theme", () => {
  window.localStorage.setItem("personal-stock-agent-theme", "javascript:bad");
  render(<ThemeProvider><ThemeControl /></ThemeProvider>);
  expect(screen.getByRole("radio", { name: "System" })).toBeChecked();
});
