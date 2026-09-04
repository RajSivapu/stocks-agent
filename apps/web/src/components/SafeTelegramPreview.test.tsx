import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { SafeTelegramPreview } from "./SafeTelegramPreview";

it("renders stored markup as text and disables unsafe links", () => {
  render(<SafeTelegramPreview
    text={'<img src=x onerror="alert(1)"><b>HOOD</b>'}
    links={[
      { label: "bad", url: "javascript:alert(1)" },
      { label: "good", url: "https://example.com/evidence" },
    ]}
  />);
  expect(screen.getByText(/<img src=x/)).toBeVisible();
  expect(document.querySelector("img")).toBeNull();
  expect(screen.queryByRole("link", { name: "bad" })).not.toBeInTheDocument();
  expect(screen.getByText("bad").tagName).toBe("SPAN");
  expect(screen.getByRole("link", { name: "good" })).toHaveAttribute("rel", "noreferrer noopener");
});
