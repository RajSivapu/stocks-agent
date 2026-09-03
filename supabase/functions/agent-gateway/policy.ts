import type { DecisionCandidate } from "../market-briefing-gateway/_shared/contracts.ts";
import type { CorporateActionState } from "./corporate-actions.ts";

export { evaluateCandidate } from "../market-briefing-gateway/_shared/policy.ts";
export type { PolicyEvaluation } from "../market-briefing-gateway/_shared/policy.ts";

const SAFE_RESULTS: Record<DecisionCandidate["action"], ReadonlySet<DecisionCandidate["action"]>> = {
  buy: new Set(["buy", "watch", "avoid"]),
  add: new Set(["add", "hold", "watch", "avoid"]),
  hold: new Set(["hold", "watch", "avoid"]),
  reduce: new Set(["reduce", "sell", "hold", "watch", "avoid"]),
  sell: new Set(["sell", "hold", "watch", "avoid"]),
  watch: new Set(["watch", "avoid"]),
  avoid: new Set(["avoid"]),
};

export function outcomeDoesNotUpgrade(
  proposed: DecisionCandidate["action"],
  finalAction: DecisionCandidate["action"],
): boolean {
  return SAFE_RESULTS[proposed].has(finalAction);
}

export function applyCorporateActionQuarantine(
  candidate: DecisionCandidate,
  state: CorporateActionState,
): DecisionCandidate {
  if (state === "clear" || !["entry_trigger", "stop_near", "stop_breach", "target_near", "target_hit"].includes(candidate.notification_kind)) {
    return structuredClone(candidate);
  }
  return {
    ...structuredClone(candidate),
    action: candidate.action === "sell" || candidate.action === "reduce" ? "hold" : "watch",
    notification_kind: "data_warning",
    confidence: "low",
    confidence_reason: "Corporate-action review is pending; price-level alerts are suppressed.",
  };
}
