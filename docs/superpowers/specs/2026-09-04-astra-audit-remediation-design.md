# GPT-6 Astra Audit Remediation Design

**Date:** 2026-09-04
**Status:** Approved for implementation by the owner on 2026-09-04
**Release:** Personal Stock Agent V1 safety remediation
**Audit baseline:** `432d647ef911ff63da427097f02a852e18038b62`
**Release control:** `PROJECT_STATUS.md`

## 1. Outcome

Repair the concrete correctness, accounting, intelligence, delivery, recovery, and release-evidence
defects found by the GPT-6 Astra repository audit before the system is treated as trusted portfolio
decision support. Until every immediate gate passes, the system remains owner-only research and
shadow decision support. This remediation does not create brokerage authority or autonomous trading.

## 2. Non-negotiable boundaries

- Zero incremental dollars: no paid provider, premium endpoint, trial, metered model API, or paid
  infrastructure is introduced.
- No brokerage credentials, order API, order placement, modification, cancellation, or autonomous
  portfolio mutation is added.
- One pre-created owner account remains the only web user; signup and invitations stay disabled.
- No duplicate live scheduled run may be started to obtain evidence.
- Missing, stale, contradictory, quota-blocked, or unverifiable evidence fails closed.
- A database write and an external Telegram send are separate states with separately retained
  receipts; ambiguity must never be reported as success or as "nothing changed."
- Ordinary local tests cannot use a production Supabase project, even when production credentials
  exist in the environment or ignored local files.
- Learning remains advisory and cannot change thresholds, source priority, sizing, routing, or
  authority.

## 3. Remediation architecture

The work is divided into three dependency-ordered layers.

1. **Containment and trusted inputs.** Isolate integration tests, align the owner OTP flow, prevent
   token refresh from resetting inactivity, validate current actionable price, and derive freshness
   and evidence categories from persisted records.
2. **Money and decision integrity.** Evaluate the complete proposed portfolio plan against cash,
   allocation, concentration, and aggregate exposure; use one decimal contract; reject or quarantine
   out-of-order transactions; and route every owner-visible actionable statement through the final
   policy result.
3. **Operational and release integrity.** Make provider discovery real, preserve contradictory
   evidence, persist quota/cache consumption, enforce scheduled stage completion, use an outbox for
   Telegram uncertainty, demonstrate recovery, and bind release verification to one current deployed
   candidate and its original receipts.

Each slice uses a focused red-green regression test. The consolidated suite runs once at the final
integration gate, not after every slice.

## 4. Required behavior

### 4.1 Test isolation

Credentialed database tests require an explicit opt-in flag and an exact allowlisted non-production
project reference. The guard rejects the configured production project and any URL that cannot be
matched to the allowlist. Cleanup is registered before the first mutation and runs from `finally` or
a fixture finalizer. Deployment verification must never invoke the opt-in mutation suite.

### 4.2 Owner authentication

The product uses an emailed six-digit OTP, not a magic link. Repository configuration, the Supabase
email template, the browser input, and verification type must agree. Provisioning and deployment
verification fail closed when the live Auth configuration is not six digits or the template does not
contain the token variable. Token refresh does not count as owner activity; the 30-minute privacy
deadline moves only on explicit pointer, keyboard, or focus activity.

### 4.3 Policy and publication authority

An executable entry is valid only when the verified current price is inside the approved entry
trigger interval and below the target. Sizing and reward/risk use that executable price. Conditional
setups remain watches and are labeled conditional.

Evidence timestamps, source status, relationship type, and candidate membership are loaded from
persisted packet records. Caller labels such as `fresh` cannot make old evidence current. Required
evidence categories vary by claim, and contradictory evidence remains visible.

Reports are rendered from persisted final policy decisions. Caller-written prose cannot introduce
an action, quantity, entry, stop, target, or urgency that the final decision did not authorize. One
briefing has one publication authority.

### 4.4 Portfolio and ledger integrity

The gateway evaluates the complete set of proposed actions as a plan. It uses reconciled holdings,
spendable cash or an explicit unavailable state, allocation targets, existing stop exposure, and
reserved exposure from earlier approved candidates in the same run. Mutually exclusive ideas are
explicit alternatives. Every sale-like action is capped by available shares.

Database numeric values use a consistent fixed-point boundary. Invalid or unavailable cost basis
makes profit unavailable and visibly incomplete; it never contributes zero. Backdated transactions
that precede a later transaction for the same security are quarantined or rejected until a
chronological replay creates explicit correction records.

### 4.5 Intelligence correctness

Every declared adapter must produce provider-specific request parameters, stable item identity,
published/retrieved/effective/reporting-period timestamps, entity/security identifiers, and bounded
failure receipts. Distinct or contradictory items cannot collapse solely because they came from the
same query URL or have similar wording. Ranking uses actual recency, liquidity, portfolio relevance,
and holding concentration, with unknown values represented as unknown rather than neutral defaults.

Quota reservations and actual requests are persisted. A retry resumes completed work through a real
cache and cannot repeat requests without accounting. A run distinguishes no qualifying event from
insufficient collection coverage.

### 4.6 Lifecycle, delivery, recovery, and release evidence

There is one scheduled run per market date and phase. Completion requires the phase-specific
collection, packet, evaluation, report, and publication-or-suppression receipts. Relevant history is
bounded without discarding pending state, and overdue phases become visible failures.

Telegram delivery uses durable pending, delivered, failed, and uncertain states. Retries recover the
original receipt and never claim a committed database change did not occur. Restore procedures use
encrypted zero-cost exports and an isolated restore drill that reconciles holdings, commands,
reports, and external delivery receipts.

The release verifier independently checks freshness, scheduled phase, post-merge execution, exact
candidate SHA, recomputed source and artifact hashes, required stage receipts, original Telegram
message IDs or explicit suppression, and rollback evidence. Labels such as `verified` are not proof.

### 4.7 Outcomes and least privilege

Outcome calculation keys loss streaks by recommendation rather than horizon, aligns stock and
benchmark exposure windows, uses session highs/lows for excursion metrics, and retains veto-only
evaluations in weekly summaries. Read-only workflows use restricted database roles. Python
dependencies are reproducibly pinned. Market calendars represent early closes and fail closed beyond
their maintained coverage; actionable quotes must match requested symbol and currency and surface
unavailable halt/spread/liquidity conditions.

## 5. Acceptance

Immediate acceptance requires focused evidence for every Astra F1-F12 defect plus the F16 recovery
gate and the owner OTP mismatch. Full remediation additionally requires F13-F15 and F17-F19. After
task-level reviews, run one consolidated local suite, exact-head CI, a protected deployment, owner and
denial canaries, an isolated restore drill, and the next existing scheduled receipt reconciliation.
No production mutation, deployment, auth-template update, or scheduled-run trigger occurs without the
existing protected process and the authority required for that external change.
