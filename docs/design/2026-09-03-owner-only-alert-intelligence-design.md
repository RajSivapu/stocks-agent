# Owner-Only Alert Intelligence Design

**Date:** 2026-09-03
**Status:** Approved by the owner on 2026-09-03 for implementation and receipt-gated rollout.
**Owner:** Rajrupesh

## Decision

Keep Telegram renderer v2 live and unchanged while building a native owner-only alert layer behind
a disabled feature flag. Do not purchase or integrate a competitor merely to copy its interface.
Borrow the strongest patterns from the reviewed products, validate them with the existing protected
gateway, and expose an owner-only web history/dashboard only after the Telegram alert lifecycle is
stable.

The agent remains suggestion-only. It may propose an alert rule. Only the owner may arm a proposed
rule. No alert, callback, webhook, dashboard, model, or scheduler may place, modify, or cancel a
brokerage order. Friend invitations and multi-owner access remain disabled.

## Research boundary

The review used public product pages, public help documentation, and public interface text for the
seven products supplied by the owner. Exact private-account push cards were not available for every
product. Where the vendor does not publish a message layout, this design records the documented
behavior only and does not invent a screenshot or field order.

Community reports were used only to discover failure modes. They are anecdotes, not proof of a
vendor-wide defect. The controls derived from them are independently justified by official product
documentation and by this repository's existing receipt model.

## Competitive findings

| Product | Documented strength | Pattern to adopt | Pattern not to adopt |
|---|---|---|---|
| Wayvia | Continuous change/anomaly monitoring, competitor context, trend views, and screenshot verification for some pricing evidence | Treat an alert as a detected state change; preserve the evidence snapshot and compare with a benchmark or prior state | Retail catalog, seller-enforcement, and bulk-notice workflows |
| Ragic | Configurable approval messages, variables, action buttons, reminders, locked records, and one notification when conditions overlap | Explicit draft/decision state, owner action, versioned details, deduplication, and bounded reminders | Departments, joint approvals, and multi-user routing |
| Bloomberg | Need-to-know bullet digests, security-list personalization, morning briefings, linked data/analytics, and event monitoring | Put the new fact first, then thesis/portfolio impact, risk, and evidence; keep routine briefs separate from urgent alerts | Enterprise order, execution, compliance, and terminal scope |
| Stock Alarm | Typed alerts, several delivery channels, delivery history, quote age/session context, and an agent boundary where drafts remain inert until the owner arms them | Agent proposes; owner arms; drafts expire; no model tool may arm, pause, or delete; retain actual delivery receipts | A second paid alert system as the portfolio source of truth |
| TradingView | Explicit operator, timeframe, trigger frequency, expiration, message variables, status/log, and export | Store the exact rule version and runtime values; default technical rules to confirmed-bar evaluation; expose active/triggered/expired states | Broker/webhook execution and arbitrary scripts in the protected agent |
| TrendSpider | Multi-factor conditions, ALL/ANY/NONE groups, confirmation candles, session controls, notes in notifications, and trigger-count expiry | Start with bounded ALL conditions, a confirmation policy, profile/horizon label, owner note, expiry, and trigger count | Strategy-bot or order-routing payloads |
| Finviz | Ticker, screener-entry, portfolio, and calendar alert classes; cooldowns; centralized management | Separate alerts by purpose and use cooldowns/digests to prevent fatigue; add “new candidate entered screen” as a research alert | Treating screener membership as a buy signal |

Sources:

- [Wayvia retail intelligence](https://wayvia.com/retail-intelligence) and
  [Wayvia screenshot-verification update](https://wayvia.com/blog/the-optimize-innovate-win-debrief-how-pricespider-is-starting-2025-with-a-bang)
- [Ragic approval flow](https://www.ragic.com/intl/en/doc/30/approval-flow-configuration),
  [Ragic notification channels](https://www.ragic.com/intl/en/doc-user/12/notifications), and
  [Ragic reminders](https://www.ragic.com/intl/en/doc/40/reminders)
- [Bloomberg News workflow](https://professional.bloomberg.com/products/bloomberg-terminal/news/) and
  [Bloomberg equity analyst workflow](https://professional.bloomberg.com/institutions/equity-analyst/)
- [Stock Alarm alert guide](https://pro.stockalarm.io/stock-price-alerts),
  [alert types](https://pro.stockalarm.io/alert-types), and
  [owner-armed MCP boundary](https://pro.stockalarm.io/mcp)
- [TradingView alert configuration](https://www.tradingview.com/support/solutions/43000763312-learn-how-to-configure-alerts/),
  [message variables](https://www.tradingview.com/support/solutions/43000531021-how-to-use-a-variable-value-in-alert/),
  [alert management/log](https://www.tradingview.com/support/solutions/43000595311-manage-alerts/), and
  [webhook delivery status](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/)
- [TrendSpider multi-factor alerts](https://help.trendspider.com/kb/alerts/create-a-new-multi-factor-alert-from-scratch),
  [alert management](https://help.trendspider.com/kb/alerts/manage-existing-price-alerts), and
  [webhook variables](https://help.trendspider.com/articles/webhooks)
- [Finviz custom alerts](https://finviz.com/knowledge-base/portfolio-alerts/alerts/custom-alerts-setup) and
  [Finviz screener alerts](https://finviz.com/knowledge-base/screener/alerts-signals/screener-alerts)

## Current v2 baseline to preserve

The current code already provides:

- server-refetched prices and explicit quote timestamps;
- stale/session/conflicting-data vetoes;
- deterministic portfolio, sizing, stop, target, and reward/risk checks;
- fixed intraday alert priority and edge-triggered holding alert suppression;
- concise intraday messages and bounded Telegram parts;
- HTML escaping and rejection of directive language in model narrative;
- immutable evaluation evidence plus rendered message text and SHA-256 hash;
- publication states for ready, sending, delivered, failed, unknown, and suppressed;
- Telegram message IDs, send-attempt count, delivery timestamps, and bounded errors; and
- explicit owner confirmation for portfolio-record changes.

The v2 renderer, policy, and Telegram delivery tests passed 36/36 on 2026-09-03 with the repository's
pinned `deno@2.9.6` command. That verifies local behavior only; it is not a claim about a scheduled
cloud run or delivery to the owner's device.

## Gaps to close

1. Alerts have no explicit draft, armed, paused, snoozed, acknowledged, dismissed, or expired
   lifecycle.
2. A Telegram message does not show the alert rule version, evaluation/trigger time, delivery time,
   session, horizon/profile, or which conditions passed.
3. The current renderer gives price/levels but little concise “why now” and thesis-impact context in
   an intraday alert.
4. Existing publication receipts prove that Telegram accepted message IDs; they do not prove that
   the owner viewed a notification. An owner acknowledgement must be a separate receipt.
5. The system cannot measure trigger-to-evaluation and evaluation-to-Telegram latency separately.
6. Alert cooldowns and quiet-hour/digest policy are implicit rather than a first-class, testable
   contract.
7. The current three analysis runs are not continuous monitoring. With the configured delayed data,
   the system must not describe itself as real-time or day-trading grade.
8. There is no owner-only web surface for rule status, evidence, delivery history, or review of why
   an alert fired.

## Recommended architecture

### 1. Two distinct notification products

- **Briefs:** Keep renderer v2 for pre-market and post-market portfolio summaries. Quiet intraday
  remains suppressed.
- **Alerts:** Add a separate renderer v3 for one material state change or a small coalesced group.
  It is trigger-first, receipt-linked, and action-oriented without becoming an order interface.

The first v3 deployment is shadow-only: the protected gateway renders and returns a preview, but it
does not persist an alert lifecycle change or call Telegram.

### 2. Deterministic trigger, bounded explanation

The deterministic server decides whether an armed rule fired. The model may supply a concise thesis
and evidence-linked explanation during an existing analysis run, but model prose cannot make an
unmet condition true. If explanation data is missing or unsafe, send the server-owned trigger facts
without model prose.

Initial condition vocabulary is deliberately small:

- price crosses above/below a level or enters/leaves a zone;
- price closes above/below SMA 20, 50, or 200;
- RSI 14 enters a configured range;
- volume is at least a configured multiple of its 20-session average;
- an owner-recorded stop/target near/breach edge changes state;
- an approved screener candidate enters or leaves a saved screen; and
- a dated earnings or macro event enters a configured reminder window.

Version 1 supports one to five conditions joined by **ALL**. ANY/NONE and nested groups are deferred
until the simple evaluator and receipts are proven. Technical conditions default to the completed
bar. A raw price/recorded-stop rule may use two separately observed quotes as confirmation. Each rule
declares regular, pre-market, post-market, or all-session scope.

### 3. Owner-only lifecycle

```text
agent proposal -> inert draft -> owner Arm -> active rule -> triggered event
                                      |             |             |
                                owner Dismiss   Pause/Expire   Ack/Snooze/Dismiss
```

- An agent-proposed draft expires after 24 hours and is limited to five drafts per hour.
- Only an authenticated owner Telegram callback may arm, pause, resume, or dismiss a rule.
- Recording a stop/target through the existing confirmed portfolio command counts as the owner's
  authorization to monitor that recorded level; it is not authorization to place an order.
- A rule edit creates a new version. Past events keep the version and condition snapshot that fired.
- No message includes Buy, Sell, Place order, or broker-link buttons.

### 4. Alert classes and routing

| Class | Examples | Telegram behavior |
|---|---|---|
| Critical risk | Recorded stop breach, thesis break, conflicting/stale data on a due decision | Immediate, one ticker per message, explicit review language, acknowledgement button |
| Action review | Armed entry zone plus all required confirmations | Immediate during declared session; Review, Snooze, Dismiss buttons |
| Material update | Earnings/filing/catalyst materially changes a held/watch thesis | Immediate for holdings; otherwise next digest |
| Watch/research | New candidate enters a saved screen, technical setup becomes interesting | Coalesced digest; never called a buy signal |
| Routine | Pre-market and closing briefs | Existing renderer v2 schedule |
| System | Source stale, publication failed/unknown, scheduler receipt missing | Separate operational notice; no investment action language |

Cooldowns are rule-specific and tested. Initial defaults proposed for owner approval are 20 minutes
for repeated critical risk states, four hours for action-review states, 15 minutes for coalescing
watch/research events, once per scheduled routine, and escalation only when the state becomes more
severe or materially changes. Exact defaults remain configuration, not model decisions.

### 5. Telegram message contract

Every v3 alert has this order:

1. severity, ticker, owner-selected profile, and alert class;
2. plain-language trigger and an explicit “suggestion only” label;
3. trigger/evaluation time, quote as-of time, age, and market session;
4. conditions passed and any condition unavailable;
5. one-sentence thesis impact and up to three evidence-linked reasons;
6. recorded or policy-approved risk levels and portfolio exposure context;
7. confidence label and evidence coverage, not an uncalibrated probability;
8. owner actions that affect only the alert lifecycle; and
9. short receipt ID, rule version, and expiry.

Example with fictional values:

```text
🟠 REVIEW • ABC • BALANCED
Entry setup confirmed — suggestion only; no order was placed

Triggered 12:15:04 CT • quote 12:14:51 CT • age 13s • REGULAR
Conditions 3/3: inside $41.80–$42.30; volume 1.7x 20d; RSI 58 in 50–65

Why now: price and participation confirm the previously reviewed setup.
Risk: invalidation below $39.90 • recorded/proposed stop $39.75 • target $47.20
Confidence MEDIUM • evidence 3/3 • horizon 5–21 sessions

[Review] [Snooze 1d] [Dismiss]
Receipt AL-7F2C • rule v3 • expires Sep 9
```

Critical holding-risk example:

```text
🔴 RISK REVIEW • ABC • RECORDED HOLDING
Verified price is at/below your recorded stop — review manually

Triggered 10:42:18 CT • quote 10:42:01 CT • age 17s • REGULAR
Price $39.68 • recorded stop $39.75 • position exposure 4.2%
The bot did not sell and cannot access a brokerage.

[Acknowledge] [Snooze 20m] [Open evidence]
Receipt AL-91D0 • stop rule v2
```

The first screen stays compact. “Review” or the future owner-only dashboard opens the complete
evidence, condition snapshot, analyst/checker result, and publication/delivery timeline.

### 6. Receipt semantics

Store these timestamps separately:

- market observation time supplied by the provider;
- deterministic evaluation time;
- event persistence time;
- Telegram send-start time;
- Telegram API acceptance time and message ID; and
- owner acknowledgement/action time, if any.

Claims must use the narrowest receipt-supported wording:

- A Telegram message ID means **accepted by Telegram**, not viewed on the owner's device.
- An owner callback means **owner action received**, not that a brokerage trade occurred.
- A quiet run means **no rule passed the evaluated conditions**, not that no market opportunity
  existed.
- A data failure means **not evaluated safely**, never an inferred Hold or Buy.

### 7. Monitoring cadence and data limits

Do not market the first release as instant. The current configuration allows delayed quotes and
requires actionable quote age no greater than 20 minutes. Initial monitoring therefore uses the
existing scheduled runs. A separate 15-minute deterministic market-hours monitor may be enabled
only after shadow receipts prove the provider timestamps and quota are adequate.

If the owner later needs second/minute-level active-trading alerts, evaluate a licensed real-time
feed as a separate cost/data-quality decision. A faster scheduler on delayed data is not a substitute
for a real-time feed.

### 8. Owner-only web dashboard

The dashboard is read-first and follows, rather than blocks, the Telegram alert layer. It shows:

- active, draft, paused, snoozed, expired, and triggered rules;
- condition/rule version and owner profile/horizon;
- event evidence and trigger/delivery/acknowledgement timeline;
- scheduled-run receipt health;
- notification-noise and stale-data metrics; and
- 5/21/63-session outcome grades when eligible.

Initial dashboard actions are limited to alert lifecycle changes with the same owner-confirmation
and audit semantics. It has no brokerage connector, order ticket, friend invitation, or cross-owner
data model. Authentication/RLS receives a separate security design before implementation.

### 9. Learning and prediction boundary

Do not label the current health score or model confidence as a probability of future profit. The
agent may present bull/base/bear scenarios and a confidence label. Numerical probabilities can be
introduced only after enough out-of-sample, benchmark-relative, time-stamped outcomes exist to
measure calibration and sample size. Learning may propose reviewed policy changes; it never silently
changes weights, thresholds, sizing, cooldowns, or risk limits.

## Rollout gates

1. Observe and reconcile the already-scheduled v2 pre-market, intraday, post-market, and Friday
   receipts. This does not block local design/test work; it blocks only new production claims.
2. Approve this message/lifecycle design. **Completed 2026-09-03.**
3. Implement pure alert contracts/evaluator and migration tests with the feature disabled.
4. Render deterministic v3 fixtures and protected dry-run previews with zero writes and zero sends.
5. Run a shadow comparison against v2 and review wording/noise with the owner.
6. Enable owner draft/arm callbacks; verify unauthorized, replayed, expired, and stale actions fail
   closed.
7. Enable one low-risk alert class, inspect database and Telegram receipts, then expand one class at
   a time.
8. Consider the 15-minute deterministic monitor only after latency/quota evidence exists.
9. Design and build the owner-only dashboard after the alert lifecycle is stable.
10. Evaluate calibration and validation-lab results only after sufficient outcome history exists.

## Explicit non-goals

- No brokerage, exchange, order, cancel, rebalance, or autonomous execution authority.
- No friend invitations, shared portfolios, multi-tenant identity, or per-friend API keys.
- No blind integration with Stock Alarm, TradingView webhooks, TrendSpider bots, or Finviz.
- No claim that price alerts predict the future or guarantee profit.
- No social-sentiment-only trigger.
- No silent policy self-tuning.
- No production cutover from renderer v2 without owner-reviewed shadow previews and receipts.
