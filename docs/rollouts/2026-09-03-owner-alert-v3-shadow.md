# Owner Alert v3 Shadow Rollout Receipt

Observed on 2026-09-03 in `America/Chicago`. This receipt records only checks supported by the
repository, Supabase, Telegram publication ledger, or protected gateway responses. It contains no
secret values and makes no investment recommendation.

## Scope boundaries

- Owner-only personal deployment.
- `access.mode` remains `owner_only`; friend invitations remain disabled.
- `guardrails.execution_allowed` remains false.
- No brokerage, order, invitation, or tenant credential name was present in the live Supabase
  secret inventory.
- Alert actions change monitoring state only. They cannot place, modify, or cancel a trade.

## Branch-lineage boundary

Alert v3 intentionally follows the owner-only production line rather than merging the deferred
multi-user/web product branch. Git inspection on 2026-09-03 established:

- renderer-v2 commit `386da6f` exists on `codex/stock-agent-reliability`, where it also touched the
  separate multi-user/web stack;
- the exact `renderer.ts` v2 patch is present on owner-only `main` as `1bdb490` (identical SHA-256
  for the file patch); and
- `codex/owner-alert-v3` descends from `1bdb490`.

Therefore the absence of the multi-user web application on this branch is not an alert-v3 deletion.
It preserves the owner's later decision to keep this a personal agent, leave friend invitations and
multi-tenant identity disabled, and design a new read-first owner dashboard only after the Telegram
alert lifecycle is stable. Do not merge `codex/stock-agent-reliability` wholesale into this rollout.

## Code and review checkpoint

- Branch: `codex/owner-alert-v3`.
- Shadow deployment documentation: `6764d41`.
- Draft evidence-coverage correction: `a54945a`.
- Corrected shadow receipt documentation: `c0e377f`.
- Focused review after the receipt-hardening changes found no Critical or Important issue.
- Final coherent local gate at commit `af0c7e6`:
  - 128 Python tests passed;
  - 63 Node tests passed;
  - 110 Deno tests passed, including the additional mixed-evidence regression case;
  - both Edge Function entrypoints passed Deno type-check; and
  - `git diff --check` passed with a clean worktree before this receipt-only update.

## Production deployment checkpoint

- Migration `20260905_owner_alert_lifecycle.sql` applied.
- Active market policy: version 2, activated at `2026-09-03T18:50:16.300978Z`.
- Alert flags: `enabled=false`, `shadow=true`, profile `balanced`, 24-hour draft TTL, maximum five
  projected drafts per hour.
- `market-briefing-gateway`: version 9, active, deployed at epoch milliseconds `1788462308074`.
- `telegram-portfolio`: version 12, active, deployed at epoch milliseconds `1788461409606`.
- Live healthcheck after gateway v9: gateway, standalone alerts, Finnhub, and Yahoo all returned
  `ok`.

The rollback-only production verifier returned all required booleans true, including duplicate
suppression, owner mismatch rejection, stale-version rejection, expired-draft rejection,
event/publication-bound acknowledgement, Telegram acceptance storage, versioned expiry, and hourly
cap rejection. It finished with `remaining_test_rows=0`.

## Protected shadow preview

The corrected protected `on-demand` dry-run used a synthetic `Watch` packet solely to inspect the
renderer. The gateway independently refreshed the quote, returned `dry_run=true`, kept the normal
publication `suppressed`, and reported one would-be inert alert draft.

- Preview receipt: `AL-C511`.
- Preview draft ID: `c511d386-cf76-4a9e-8a48-3a544f42afc5`.
- Rendered hash: `524491bcb1610001fbf0ab4cc2e5191a7e3fe7a43b20f8407c2fc624f0b7fd01`.
- Displayed fields included condition, quote/evaluation times, age, session, reason, invalidation,
  policy-approved stop, target, confidence, `evidence 1/1`, validity date, expiry, and receipt.
- The preview was labeled `DRAFT`, `suggestion only`, and `inert until you arm it`.
- Its local deterministic button fixture contained only `Arm` and `Dismiss`; no execution button.
- After the dry-run, live counts remained zero for alert rules, events, drafts, actions, and
  template-v3 publications. No Telegram send was attempted.

## Live security checkpoint

Direct Postgres inspection confirmed RLS enabled on all five alert lifecycle tables:

- `market_alert_drafts`;
- `market_alert_rules`;
- `market_alert_rule_versions`;
- `market_alert_events`; and
- `market_alert_actions`.

Direct grant inspection confirmed the six alert mutation RPCs were executable only by `postgres`
and `service_role`, not `PUBLIC`, `anon`, or `authenticated`:

- `create_market_alert_drafts`;
- `apply_market_alert_action`;
- `record_market_alert_evaluations`;
- `create_market_alert_publication`;
- `expire_market_alert_rules`; and
- `finish_market_alert_publication`.

The focused owner-only, no-brokerage, RPC, RLS, and migration security suite passed 46 tests.

## Scheduled v2 receipt observed before v3 deployment

The 2026-09-03 intraday run started at `2026-09-03T17:02:43.216398Z` and finished at
`2026-09-03T17:09:21.808Z` with status `completed`.

- Run ID: `1a89f988-fede-49d3-b4d2-7beefe73ebcd`.
- Four gateway operations completed once: `start_run`, `read_context`, `evaluate_and_publish`, and
  `finish_run`.
- Five new policy-v1 Analyst and Checker evaluations were stored with fresh same-run quote/history
  evidence.
- Five suggestions linked to those evaluation IDs.
- One renderer-v2 intraday `new_idea` publication was delivered.
- Publication ID: `045a290c-6362-4f24-9d6d-3cfbf16bfca8`.
- Rendered hash: `b7d54d409d90926970b20a2bf07faea9cd0496a646a6365d48de2d87ab5ef459`.
- Telegram message ID: `17`.

The corresponding Claude Routine session was inspected directly at
`session_01Gucsv6Xk63FCaAF6gn8aXP` after the Mac was unlocked. The Routine detail page identified it
as the scheduled `Intraday` cloud run at 12:01 PM CDT and showed a completed session that cloned only
the `stocks-agent` repository. The transcript then reconciled with the receipts above:

- it attempted the repository's `market-briefing` skill, found the committed skill file, and read it
  directly after Claude's skill registry did not resolve it;
- it called `start_run`, then `read_context`, and treated the morning plan only as historical context;
- it fetched new quotes and one-year history/indicators for holdings, morning candidates, indices,
  and VIX, with provider observations around `17:03Z`;
- it independently checked current news and history-based day moves, detected stale Yahoo
  `previousClose` values, and used the history-derived moves instead;
- it built five new candidates whose payloads each contained completed Analyst and Checker records;
- it submitted exactly one `evaluate_and_publish` request for run
  `1a89f988-fede-49d3-b4d2-7beefe73ebcd`, whose gateway response reported five evaluations, five
  suggestions, publication `045a290c-6362-4f24-9d6d-3cfbf16bfca8`, status `delivered`, and Telegram
  message ID `17`; and
- it then called `finish_run`, whose response reported `completed`, the same write counts,
  publication status, and Telegram message ID.

Its final response limited write/send claims to that `finish_run` receipt and said that no trade was
executed and no repository change was made. This transcript verification does not endorse the v2
message wording; the owner's dissatisfaction with that compact message is the reason v3 remains in
shadow review.

The saved Routine configuration was also opened read-only and closed without saving. It contained
one repository (`RajSivapu/stocks-agent`), the `stocks-agent` cloud environment, no included
connectors, and disabled auto-fix behavior. Its instructions restrict the run to
`python scripts/market_gateway.py`, require independently refreshed evidence plus rebuilt Analyst and
Checker records, require a suppressed outcome to stay silent, allow only server-receipt claims, and
prohibit trade execution and repository edits.

## Canary-class safety fix staged after review

A pre-canary review found that the original `alerts_v3.enabled` switch would have enabled every
eligible v3 draft class together. No canary was enabled and no production setting was changed.
The staged policy-v3 contract adds a server-validated `enabled_classes` allowlist limited to
`entry_trigger`, `stop_breach`, and `target_hit`. Shadow mode may still preview all supported
classes, while enabled mode creates or evaluates only explicitly listed classes. A live policy is
rejected when the list is empty, duplicated, or contains an unsupported value. Gateway code remains
backward-compatible with the deployed disabled/shadow policy v2 so it can be upgraded safely before
policy v3 is activated.

The focused red/green tests cover policy projection, backward-compatible repository validation,
draft suppression, and active-rule suppression. The full local gate then passed 131 Python tests,
63 Node tests, 113 Deno tests, both Edge Function type-checks, and `git diff --check`. This code is
staged locally and must not be deployed until the scheduled 2026-09-03 post-market run has been
reconciled against the unchanged gateway v9 and policy v2 baseline.

## Remaining gates

1. Reconcile the first scheduled post-market run after the v3 shadow deployment, including its
   Routine transcript, policy-v2 evaluations, normal renderer-v2 Telegram outcome, shadow preview
   response, and zero v3 lifecycle writes/sends.
2. Reconcile the first Friday process-audit receipt and confirm it remains bounded and read-only.
3. Present at least one real scheduled v3 shadow example to the owner. Do not infer approval from
   silence or from the synthetic preview.
4. After explicit owner approval of a real example, enable only the owner-recorded `stop_breach`
   canary class. Do not enable entry suggestions, target alerts, screening alerts, or the optional
   15-minute monitor in the same change.
5. Verify one canary end to end: rule version, provider observation time, evaluation/event IDs,
   publication hash, Telegram API acceptance/message ID, and any acknowledgement receipt.
6. Keep the optional faster monitor and owner-only read-first dashboard behind their later evidence
   and security-design gates.
