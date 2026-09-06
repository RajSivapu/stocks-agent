import type { IdeaView } from "@stocks-agent/dashboard-contracts";

function evidenceTime(idea: IdeaView) {
  if (!idea.evidence_as_of) return Number.NEGATIVE_INFINITY;
  const value = Date.parse(idea.evidence_as_of);
  return Number.isNaN(value) ? Number.NEGATIVE_INFINITY : value;
}

export function splitIdeaVersions(ideas: IdeaView[]) {
  const ordered = ideas
    .map((idea, index) => ({ idea, index }))
    .sort((left, right) => evidenceTime(right.idea) - evidenceTime(left.idea) || left.index - right.index)
    .map(({ idea }) => idea);
  const tickers = new Set<string>();
  const current: IdeaView[] = [];
  const history: IdeaView[] = [];

  for (const idea of ordered) {
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
  return Boolean(action && action !== "watch" && action !== "hold" && action !== "no action");
}
