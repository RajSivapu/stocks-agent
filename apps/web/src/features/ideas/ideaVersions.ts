import type { IdeaView } from "@stocks-agent/dashboard-contracts";

export function splitIdeaVersions(ideas: IdeaView[]) {
  const tickers = new Set<string>();
  const current: IdeaView[] = [];
  const history: IdeaView[] = [];

  // The API returns persisted suggestion rows in descending ID order. Preserve
  // that ordering: evidence_as_of describes source age, not version creation.
  for (const idea of ideas) {
    const ticker = idea.ticker.trim().toUpperCase();
    if (tickers.has(ticker)) history.push(idea);
    else {
      tickers.add(ticker);
      current.push(idea);
    }
  }

  return { current, history };
}

export function isActionNow(idea: IdeaView) {
  const action = idea.final_action?.trim().toLowerCase();
  return idea.policy_status === "approved" && Boolean(action && ["buy", "add", "reduce", "sell"].includes(action));
}
