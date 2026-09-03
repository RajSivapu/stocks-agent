export type CorporateActionState = "clear" | "suspected" | "needs_review";
export type CorporateActionEvent = {
  type: "split" | "reverse_split" | "symbol_change" | "merger" | "delisting";
  effectiveDate: string;
  ratio: string | null;
  reference: string;
};
export type CorporateActionInput = {
  ticker: string;
  held: boolean;
  allowedSourceVerified: boolean;
  allowedSourceEvent: CorporateActionEvent | null;
  historySplitRatios: string[];
  largestRawAdjustedGapBps: number;
  issuerOrExchangeCheckedAt: string | null;
};
export type CorporateActionAssessment = {
  state: CorporateActionState;
  eventType: CorporateActionEvent["type"] | null;
  ratio: string | null;
  reason: "confirmed_and_normalized" | "unverified_corporate_action" |
    "corporate_action_conflict" | "no_corporate_action_signal";
};

const RATIO = /^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,8})?$/;

function quarantined(input: CorporateActionInput, reason: CorporateActionAssessment["reason"]): CorporateActionAssessment {
  return {
    state: input.held ? "needs_review" : "suspected",
    eventType: input.allowedSourceEvent?.type ?? (input.historySplitRatios.length ? "split" : null),
    ratio: input.allowedSourceEvent?.ratio ?? input.historySplitRatios[0] ?? null,
    reason,
  };
}

export function assessCorporateAction(input: CorporateActionInput): CorporateActionAssessment {
  if (!/^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$/.test(input.ticker) ||
    input.historySplitRatios.length > 20 || input.historySplitRatios.some((ratio) => !RATIO.test(ratio)) ||
    !Number.isInteger(input.largestRawAdjustedGapBps) || input.largestRawAdjustedGapBps < 0) {
    return quarantined(input, "corporate_action_conflict");
  }
  const event = input.allowedSourceEvent;
  if (input.allowedSourceVerified && event) {
    const historyConflict = event.ratio !== null && input.historySplitRatios.length > 0 &&
      !input.historySplitRatios.includes(event.ratio);
    if (historyConflict) return quarantined(input, "corporate_action_conflict");
    return {
      state: "clear",
      eventType: event.type,
      ratio: event.ratio,
      reason: "confirmed_and_normalized",
    };
  }
  const issuerCheck = input.issuerOrExchangeCheckedAt === null
    ? null
    : Date.parse(input.issuerOrExchangeCheckedAt);
  const discontinuity = input.largestRawAdjustedGapBps >= 2_500;
  if (input.historySplitRatios.length > 0 || discontinuity ||
    (issuerCheck !== null && !Number.isFinite(issuerCheck))) {
    return quarantined(input, "unverified_corporate_action");
  }
  return { state: "clear", eventType: null, ratio: null, reason: "no_corporate_action_signal" };
}

export function suppressPriceAlerts(assessment: CorporateActionAssessment): boolean {
  return assessment.state !== "clear";
}
