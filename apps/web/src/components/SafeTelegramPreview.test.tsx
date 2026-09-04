import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { SafeTelegramPreview } from "./SafeTelegramPreview";

it("renders the tiny Telegram formatting allowlist while keeping hostile markup inert", () => {
  render(<SafeTelegramPreview
    text={'<img src=x onerror="alert(1)"><b>HOOD</b>'}
    links={[
      { label: "bad", url: "javascript:alert(1)" },
      { label: "good", url: "https://www.sec.gov/evidence" },
    ]}
  />);
  expect(screen.getByText(/<img src=x/)).toBeVisible();
  expect(screen.getByText("HOOD").tagName).toBe("STRONG");
  expect(screen.queryByText("<b>HOOD</b>")).not.toBeInTheDocument();
  expect(document.querySelector("img")).toBeNull();
  expect(screen.queryByRole("link", { name: "bad" })).not.toBeInTheDocument();
  expect(screen.getByText("bad").tagName).toBe("SPAN");
  expect(screen.getByRole("link", { name: "good" })).toHaveAttribute("rel", "noreferrer noopener");
});
