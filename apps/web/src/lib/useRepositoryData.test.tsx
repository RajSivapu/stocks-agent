import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { useRepositoryData } from "./useRepositoryData";

function Harness({ loader }: { loader: () => Promise<string> }) {
  const { state, reload } = useRepositoryData(loader);
  return <div>
    <span>{state.kind === "ready" ? state.data : state.kind}</span>
    <button type="button" onClick={() => { void reload(); }}>Reload</button>
  </div>;
}

it("keeps verified server data visible during a background refresh", async () => {
  let finishRefresh: ((value: string) => void) | undefined;
  const loader = vi.fn()
    .mockResolvedValueOnce("first receipt")
    .mockImplementationOnce(() => new Promise<string>((resolve) => { finishRefresh = resolve; }));
  render(<Harness loader={loader} />);
  await screen.findByText("first receipt");

  await userEvent.click(screen.getByRole("button", { name: "Reload" }));

  expect(screen.getByText("first receipt")).toBeInTheDocument();
  finishRefresh?.("fresh receipt");
  await waitFor(() => { expect(screen.getByText("fresh receipt")).toBeInTheDocument(); });
});
