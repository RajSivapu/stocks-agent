import type { NotificationKind, Phase } from "./contracts.ts";
import type { PolicyEvaluation, PolicyReasonCode } from "./policy.ts";

export const FORBIDDEN_DECISION_TEXT =
  /\b(buy|purchase|accumulate|sell|dump|unload|liquidate|add|reduce|trim|exit|enter|short|cover)\b|\b\d+(?:\.\d+)?\s+shares?\b|\$\s*\d|\b(entry|stop|target)\s*(?:at|=|:)/i;

export interface RenderPublicationInput {
  phase: Phase;
  market_date: string;
  evaluations: PolicyEvaluation[];
  holiday?: boolean;
}

export interface RenderedPublication {
  status: "ready" | "suppressed";
  kind: NotificationKind;
  body: string;
  parts: string[];
  hash: string;
  template_version: 1;
}

const MAX_PARTS = 4;
// Four joined parts plus three separators remain below the 14,000-byte DB body cap.
const MAX_PART_LENGTH = 3498;
const ELIGIBLE_FACTOR_LIMIT = 3;
const METADATA_FACTOR_LIMIT = 5;
const TRIGGER_PRIORITY: NotificationKind[] = [
  "data_warning",
  "thesis_break",
  "stop_breach",
  "target_hit",
  "entry_trigger",
  "new_idea",
  "stop_near",
  "target_near",
];
const TRIGGERS = new Set(TRIGGER_PRIORITY);

const REASON_LABELS: Record<PolicyReasonCode, string> = {
  INVALID_SCHEMA: "Invalid structured proposal",
  QUOTE_MISSING: "Verified quote is missing",
  QUOTE_STALE: "Quote is stale",
  QUOTE_SESSION_MISMATCH: "Quote does not match the required market session",
  PRICE_RELATION_INVALID: "Price or threshold relationship is invalid",
  AMOUNT_SHARES_MISMATCH: "Amount and shares do not reconcile",
  CURRENT_EVIDENCE_MISSING: "Current evidence is missing",
  ANALYST_INCOMPLETE: "Analyst review is incomplete",
  CHECKER_INCOMPLETE: "Checker review is incomplete",
  CHECKER_DOWNGRADE: "Checker downgraded the proposal",
  CHECKER_VETO: "Checker vetoed the proposal",
  LOW_CONFIDENCE: "Confidence is below the action threshold",
  ACTION_HOLDING_MISMATCH: "Action does not match recorded ownership",
  SELL_EXCEEDS_HOLDING: "Sell quantity exceeds recorded shares",
  POSITION_CAP_EXCEEDED: "Position cap would be exceeded",
  STOP_REQUIRED: "Authoritative stop is required",
  TRADE_RISK_EXCEEDED: "Trade risk limit would be exceeded",
  REWARD_RISK_TOO_LOW: "Reward-to-risk is below policy",
  PORTFOLIO_VALUE_INCOMPLETE: "Portfolio valuation is incomplete",
  DAILY_LOSS_LOCKOUT: "Daily loss lockout is active",
  CONSECUTIVE_LOSS_LOCKOUT: "Consecutive-loss lockout is active",
  SPECULATIVE_LEARNING_ONLY: "Speculative bucket remains learning-only",
  OWNER_PLAN_MISMATCH: "Proposal does not match a due owner plan",
  ALERT_ALREADY_ACTIVE: "Alert is already active or owner-suppressed",
  NARRATIVE_REJECTED: "Narrative failed safety validation",
  OUTSIDE_SESSION_CONDITIONAL: "Based on the latest official close; conditional until the next session",
  CALENDAR_COVERAGE_MISSING: "Reviewed market-calendar coverage is missing",
};

function escapeHtml(value: string): string {
  return value.replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function actionLabel(evaluation: PolicyEvaluation): string {
  if (evaluation.final_action === null) return "VETOED";
  return evaluation.final_action.toUpperCase();
}

function triggerLabel(kind: NotificationKind): string {
  return kind.replaceAll("_", " ").toUpperCase();
}

function evidenceLabel(evaluation: PolicyEvaluation, ids: readonly string[]): string {
  return ids.map((id) => {
    const evidence = evaluation.candidate.evidence.find((item) => item.id === id);
    return evidence ? `${escapeHtml(id)} (${escapeHtml(evidence.source)})` : escapeHtml(id);
  }).join(", ");
}

function renderCandidate(evaluation: PolicyEvaluation): string {
  const candidate = evaluation.candidate;
  const lines = [
    `<b>${escapeHtml(candidate.ticker)} — ${actionLabel(evaluation)}</b>`,
    `Status: ${evaluation.status.toUpperCase()} · ${triggerLabel(candidate.notification_kind)}`,
    `Confidence: ${candidate.confidence.toUpperCase()} · Bucket: ${candidate.bucket.toUpperCase()}`,
    `Verified: $${escapeHtml(evaluation.normalized.verified_price)} as of ${escapeHtml(evaluation.normalized.quote_as_of)}`,
  ];
  const actionableApproved = evaluation.status === "approved" &&
    (evaluation.final_action === "buy" || evaluation.final_action === "add" ||
      evaluation.final_action === "reduce" || evaluation.final_action === "sell");

  if (actionableApproved) {
    if (candidate.proposed_amount !== null) {
      lines.push(`Size: $${escapeHtml(candidate.proposed_amount)} · ${escapeHtml(candidate.proposed_shares!)} shares`);
    } else if (candidate.proposed_shares !== null) {
      lines.push(`Shares: ${escapeHtml(candidate.proposed_shares)}`);
    }
    if (candidate.entry_zone_low !== null && candidate.entry_zone_high !== null) {
      lines.push(`Entry zone: $${escapeHtml(candidate.entry_zone_low)}–$${escapeHtml(candidate.entry_zone_high)}`);
    }
    if (candidate.stop !== null) lines.push(`Stop: $${escapeHtml(candidate.stop)}`);
    if (candidate.target !== null) lines.push(`Target: $${escapeHtml(candidate.target)}`);
    if (evaluation.normalized.dollars_at_risk !== null) {
      lines.push(`Policy dollars at risk: $${escapeHtml(evaluation.normalized.dollars_at_risk)}`);
    }
    for (const factor of candidate.factors.slice(0, ELIGIBLE_FACTOR_LIMIT)) {
      if (FORBIDDEN_DECISION_TEXT.test(factor.text)) {
        throw new Error("forbidden decision directive in factor summary");
      }
      lines.push(
        `• ${factor.kind}/${factor.stance}: ${escapeHtml(factor.text)} ` +
          `[evidence: ${evidenceLabel(evaluation, factor.evidence_ids)}]`,
      );
    }
  } else {
    if (evaluation.reason_codes.length > 0) {
      lines.push(`Reasons: ${evaluation.reason_codes.map((code) => REASON_LABELS[code]).join("; ")}`);
    }
    for (const factor of candidate.factors.slice(0, METADATA_FACTOR_LIMIT)) {
      lines.push(
        `• ${factor.kind}/${factor.stance} [evidence: ${evidenceLabel(evaluation, factor.evidence_ids)}]`,
      );
    }
  }
  const block = lines.join("\n");
  if (block.length > MAX_PART_LENGTH) throw new Error("candidate render block exceeds limit");
  return block;
}

function heading(input: RenderPublicationInput): string {
  if (input.phase === "pre-market") return `<b>📈 Pre-market brief — ${input.market_date}</b>`;
  if (input.phase === "post-market") return `<b>📊 Post-market brief — ${input.market_date}</b>`;
  if (input.phase === "intraday") return `<b>⚠️ Intraday alerts — ${input.market_date}</b>`;
  const conditional = input.evaluations.some((item) =>
    item.reason_codes.includes("OUTSIDE_SESSION_CONDITIONAL")
  );
  return `<b>🔎 On-demand analysis — ${input.market_date} — ${
    conditional ? "CONDITIONAL — LATEST OFFICIAL CLOSE" : "LIVE REGULAR SESSION"
  }</b>`;
}

function splitBlocks(title: string, blocks: string[]): string[] {
  const parts: string[] = [];
  let current = title;
  let omitted = false;
  for (const block of blocks) {
    const combined = `${current}\n\n${block}`;
    if (combined.length <= MAX_PART_LENGTH) {
      current = combined;
      continue;
    }
    parts.push(current);
    if (parts.length >= MAX_PARTS) {
      omitted = true;
      current = "";
      break;
    }
    current = block;
  }
  if (current && parts.length < MAX_PARTS) parts.push(current);
  if (omitted && parts.length > 0) {
    const note = "\n\n<i>Additional evaluated names omitted by the message-size limit.</i>";
    const last = parts.length - 1;
    if (parts[last].length + note.length <= MAX_PART_LENGTH) parts[last] += note;
  }
  return parts;
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export async function renderPublication(input: RenderPublicationInput): Promise<RenderedPublication> {
  if (input.holiday) {
    const body = "🏛 Market closed today — US public holiday. No brief.";
    return { status: "ready", kind: "holiday", body, parts: [body], hash: await sha256(body), template_version: 1 };
  }

  let evaluations = [...input.evaluations];
  let kind: NotificationKind = "brief";
  if (input.phase === "intraday") {
    evaluations = evaluations.filter((item) =>
      item.status === "approved" && item.final_action !== null && TRIGGERS.has(item.candidate.notification_kind)
    );
    if (evaluations.length === 0) {
      return { status: "suppressed", kind: "brief", body: "", parts: [], hash: await sha256(""), template_version: 1 };
    }
    evaluations.sort((left, right) => {
      const priority = TRIGGER_PRIORITY.indexOf(left.candidate.notification_kind) -
        TRIGGER_PRIORITY.indexOf(right.candidate.notification_kind);
      return priority || left.candidate.ticker.localeCompare(right.candidate.ticker) ||
        left.candidate_id.localeCompare(right.candidate_id);
    });
    kind = evaluations[0].candidate.notification_kind;
  } else {
    evaluations.sort((left, right) =>
      left.candidate.ticker.localeCompare(right.candidate.ticker) ||
      left.candidate_id.localeCompare(right.candidate_id)
    );
  }

  const parts = splitBlocks(heading(input), evaluations.map(renderCandidate));
  const body = parts.join("\n\n");
  const sessionOnly = input.phase === "on-demand";
  return {
    status: sessionOnly ? "suppressed" : "ready",
    kind,
    body,
    parts: sessionOnly ? [] : parts,
    hash: await sha256(body),
    template_version: 1,
  };
}
