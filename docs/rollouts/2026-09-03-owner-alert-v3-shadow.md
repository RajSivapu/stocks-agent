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
- Explicit canary-class allowlist: `3eb5837`.
- Entry-level policy-validation correction: `fba1575`.
- Focused review after the receipt-hardening changes found no Critical or Important issue.
- Final coherent local gate at commit `af0c7e6`:
  - 128 Python tests passed;
  - 63 Node tests passed;
  - 110 Deno tests passed, including the additional mixed-evidence regression case;
  - both Edge Function entrypoints passed Deno type-check; and
  - `git diff --check` passed with a clean worktree before this receipt-only update.
- The final gate after both canary corrections passed 131 Python tests, 63 Node tests, 115 Deno
  tests, both Edge Function type-checks, and `git diff --check`.

## Production deployment checkpoint

- Migration `20260905_owner_alert_lifecycle.sql` applied.
- Active market policy: version 2, activated at `2026-09-03T18:50:16.300978Z`.
- Alert flags: `enabled=false`, `shadow=true`, profile `balanced`, 24-hour draft TTL, maximum five
  projected drafts per hour.
- `market-briefing-gateway`: version 9, active, deployed at epoch milliseconds `1788462308074`.
- `telegram-portfolio`: version 12, active, deployed at epoch milliseconds `1788461409606`.
- Live healthcheck after gateway v9: gateway, standalone alerts, Finnhub, and Yahoo all returned
  `ok`.

After the scheduled post-market baseline below was reconciled, the backward-compatible gateway was
deployed in two protected steps. Gateway v10 first passed all four health probes while policy v2
remained active. Policy v3 was then activated at `2026-09-03T20:22:36.480034Z` with
`enabled=false`, `shadow=true`, `enabled_classes=[]`, profile `balanced`, a 24-hour draft TTL, and a
five-draft hourly cap. The entry-level correction was deployed as gateway v11 at epoch milliseconds
`1788467115992`; Telegram remained unchanged at v12. The final healthcheck again returned `ok` for
gateway, standalone alerts, Finnhub, and Yahoo.

The rollback-only production verifier returned all required booleans true, including duplicate
suppression, owner mismatch rejection, stale-version rejection, expired-draft rejection,
event/publication-bound acknowledgement, Telegram acceptance storage, versioned expiry, and hourly
cap rejection. It finished with `remaining_test_rows=0`.

A later management-plane parity check used the live project referenced by the configured Supabase
URL and downloaded both active function bundles without deploying them. Every downloaded runtime
file matched the corresponding file on `codex/owner-alert-v3` byte-for-byte: 12 gateway files and
five Telegram files. The management receipts independently reported gateway v11 and Telegram v12
as active. The deployed secret-name inventory contained only the gateway secret, Supabase-provided
keys, and the Telegram bot, webhook, and two owner-identity secrets; it contained no brokerage,
friend-invitation, multi-owner, or LLM credential. Telegram's read-only `getWebhookInfo` receipt
matched the expected HTTPS Edge Function URL, allowed only `message` and `callback_query` updates,
reported zero pending updates, and exposed no last-error condition. No webhook was reset and no
Telegram message was sent during these checks.

## Superseded formatting-only shadow preview

The earlier protected `on-demand` dry-run used a synthetic `Watch` packet solely to inspect the
renderer. The gateway independently refreshed the quote, returned `dry_run=true`, kept the normal
publication `suppressed`, and reported one would-be inert alert draft.

- Preview receipt: `AL-C511`.
- Preview draft ID: `c511d386-cf76-4a9e-8a48-3a544f42afc5`.
- Rendered hash: `524491bcb1610001fbf0ab4cc2e5191a7e3fe7a43b20f8407c2fc624f0b7fd01`.
- Displayed fields included condition, quote/evaluation times, age, session, reason, invalidation,
  stop, target, confidence, `evidence 1/1`, validity date, expiry, and receipt.
- The preview was labeled `DRAFT`, `suggestion only`, and `inert until you arm it`.
- Its local deterministic button fixture contained only `Arm` and `Dismiss`; no execution button.
- After the dry-run, live counts remained zero for alert rules, events, drafts, actions, and
  template-v3 publications. No Telegram send was attempted.

Receipt `AL-C511` is formatting evidence only and is superseded for level-validation purposes. A
later review proved that a `Watch` evaluation did not pass through Buy/Add stop, target, and
reward-risk validation even though the renderer labeled its levels policy-approved. Shadow mode
prevented any lifecycle write or Telegram send. Gateway v11 now rejects Watch-only entry-draft
projection, and regression tests cover both the policy function and full handler response.

## Corrected policy-v3 protected preview

After gateway v11 and shadow-only policy v3 were active, a protected post-market dry-run used a
tiny synthetic `Buy` fixture solely to exercise deterministic sizing, entry, stop, target,
reward-risk, and renderer checks. It was labeled test-only in the candidate evidence and was not
treated as investment research or a recommendation.

- Preview receipt: `AL-9B78`.
- Preview draft ID: `9b78332b-8c27-4a29-bfdb-a5236655f424`.
- Rendered hash: `72d8cde944e0f17564b550f495c5a6ac141028ce054702d5f1246995f9a3a0b4`.
- The body displayed the `$326.57–$329.85` test condition, `$311.80` policy-approved stop,
  `$367.60` target, quote/evaluation time, age, post-market session, confidence, `evidence 1/1`,
  validity, expiry, and the inert-until-armed boundary.
- The gateway receipt reported `dry_run=true`, one evaluation, one would-be suggestion, one
  would-be draft, publication `ready`, and an empty Telegram-message-ID list.
- Afterward, all alert lifecycle counts and template-v3 publication count remained zero. There were
  also zero gateway-request, suggestion, or market-publication rows created after policy-v3
  activation, proving the post-cutover dry-run and healthchecks were write-free.

## Owner alternatives protected release

The owner-approved current-versus-alternatives review was released without changing alert-v3
activation or the Telegram function. The implementation commits were `7e9cbd2`, `3c2ff7f`,
`8f637bf`, `f9c1e60`, and `cafd044`. The last three fixes were found by inspecting protected previews: Yahoo
daily-history values required bounded six-decimal normalization, and a sub-display-precision lead
could not truthfully be described as “ahead by 0.0 points,” while VOO required an explicit `tilt`
relationship instead of being mislabeled as a total-market substitute or diversifier.

- `market-briefing-gateway` version 16 is active at deployment epoch milliseconds `1788471499180`;
  `telegram-portfolio` remained version 12.
- The active policy remained version 3 with `enabled=false`, `shadow=true`, and
  `enabled_classes=[]`.
- Post-deployment health returned `ok` for the gateway, standalone alert path, Finnhub, and Yahoo.
- A management-plane download of gateway v16 compared equal to every local runtime file; test
  files were intentionally excluded because Supabase does not deploy them.
- The post-deployment secret-name inventory remained owner/gateway/Supabase/Telegram only. It
  contained no brokerage or friend-invitation credential.
- The final local gate passed 132 Python tests, 63 Node/Telegram tests, 132 Deno gateway tests, both
  Edge Function type-checks, and `git diff --check`.

Protected on-demand dry-run `c2c2ff90-3676-403b-94f2-f143379c8d04` read the live owner context and
confirmed the existing `$300` monthly VTI plan. Its evaluation receipt reported `dry_run=true`, two
evaluations, one comparison with `complete` history coverage, publication `suppressed`, two
would-be suggestions, and zero alert drafts. The gateway-computed preview showed:

- equal-monthly one-year results of `+10.0%` for both VTI and ITOT, with the difference below `0.1`
  percentage point;
- max drawdown of `8.9%` for both over the synchronized window;
- a qualitative `SIMILAR` forward view, official Vanguard and iShares profile links, and an explicit
  statement that the VTI plan was unchanged; and
- the required warning that hypothetical history is not a forecast.

The pair dry-run finish receipt reported `status=completed`, `write_counts={}`,
`publication_statuses=[]`, and `telegram_message_ids=[]`. Independent before/after counts had zero
deltas for all twelve inspected tables: runs, gateway requests, evaluations, suggestions,
publications, five alert-lifecycle tables, holdings, and owner plans. This was not a live Routine or
a Telegram test send.

The earlier protected preview returned `missing_history` and also had zero writes and sends. That
fail-closed receipt led to the provider-decimal fix; the final receipt above supersedes it for
comparison-coverage evidence.

A broader protected on-demand dry-run, `b809f384-354f-45a3-9649-f543a13f2035`, then evaluated the
full initial role set: ITOT and SCHB as like-for-like funds, VOO as a large-cap tilt, and VT and VXUS
as diversification changes. Its receipt reported six evaluations, five comparisons, `complete`
coverage for all five, publication `suppressed`, six would-be suggestions, zero alert drafts,
`write_counts={}`, and `telegram_message_ids=[]`. All twelve before/after table deltas again remained
zero. The gateway-computed equal-monthly one-year results were:

- VTI `+10.0%`, max drawdown `8.9%`;
- ITOT `+10.0%`, max drawdown `8.9%`, difference from VTI below `0.1` point;
- SCHB `+9.9%`, max drawdown `8.9%`, difference from VTI below `0.1` point;
- VOO `+10.0%`, max drawdown `8.9%`, difference from VTI below `0.1` point;
- VT `+10.2%`, max drawdown `9.7%`, ahead of VTI by `0.2` points; and
- VXUS `+10.6%`, max drawdown `11.3%`, ahead of VTI by `0.7` points.

The forward result remained `SIMILAR` for the two like-for-like funds and `INSUFFICIENT` for VOO,
VT, and VXUS because their different portfolio roles do not establish a durable forward-return
edge. The preview explicitly left the `$300` monthly VTI plan unchanged and labeled hypothetical
history as not a forecast. Gateway v16 health and source parity passed after this receipt.

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

## Scheduled post-market baseline

The 2026-09-03 scheduled post-market Routine completed under Claude session
`session_01EKUSqXrSGW38x68VsMsKKZ` and production run
`667c89db-7d12-4c04-9a0d-8e230d561b8f`. The Routine UI marked it Scheduled, running at 15:10
America/Chicago, then Completed; no manual run was triggered.

- The run started at `2026-09-03T20:12:44.337804Z` and finished at
  `2026-09-03T20:20:24.913Z` with status `completed` and no recorded error.
- Six distinct gateway requests completed with one attempt each: `start_run`, `read_context`, one
  `evaluate_and_publish`, one `record_artifacts`, `grade_due_decisions` with limit 50, and
  `finish_run`.
- Two new policy-v2 evaluations stored completed Analyst and Checker records. Their official-close
  quote evidence was observed at `20:00:01Z` for CENX and `19:59:57Z` for VTI, with same-run
  retrieval at `20:15:10Z`; both final actions were Hold.
- Two linked suggestions, two daily snapshots, one observation, and one lesson were stored. The
  grading receipt reported 33 inserted rows, all incomplete rather than claimed successes.
- Publication `dc0d1aeb-0b42-44cb-9d26-d406665f3e99` used renderer template v2, hash
  `84e943a23ea2ba123a7b95ee15da8849eeaf62a9d0706b8f16a88d8d0eee0fca`, status `delivered`, and
  Telegram message ID `18`.
- The evaluation receipt reported `alert_draft_status=not_applicable`, zero drafts created, and no
  shadow preview because neither recorded stop was breached. All four alert lifecycle tables and
  template-v3 publications remained at zero.
- The transcript's final write/send claims reconcile with `finish_run`; it explicitly described the
  run as suggestion-only with no repository changes and made no trade-execution claim.

## Canary-class safety controls

A pre-canary review found that the original `alerts_v3.enabled` switch would have enabled every
eligible v3 draft class together. No canary was enabled. The policy-v3 contract adds a
server-validated `enabled_classes` allowlist limited to
`entry_trigger`, `stop_breach`, and `target_hit`. Shadow mode may still preview all supported
classes, while enabled mode creates or evaluates only explicitly listed classes. A live policy is
rejected when the list is empty, duplicated, or contains an unsupported value. Gateway code remains
backward-compatible with disabled/shadow policy v2; that compatibility was verified in production
before policy v3 was activated.

The focused red/green tests cover policy projection, backward-compatible repository validation,
draft suppression, active-rule suppression, and Watch-level rejection. The full local gate passed
131 Python tests, 63 Node tests, 115 Deno tests, both Edge Function type-checks, and
`git diff --check`. Gateway v11 and shadow-only policy v3 are now active, but the live allowlist is
still empty.

## Friday audit preflight

The scheduled `weekly-portfolio-process-audit` automation was re-inspected without running it. It is
active for Fridays at 16:30 America/Chicago, uses `gpt-5.6-terra` with high reasoning, and targets
the saved Git project `stocks-agent` at `/Users/rajrupesh/Documents/Raj/stocks-agent` (project ID
`81e05586-ea52-46ac-97f9-ffd2b4ad2413`).

The saved prompt requires exactly one bounded packet and forbids Supabase writes, Telegram sends,
file edits, brokerage capabilities, and fresh trade recommendations. The referenced skill repeats
that read-only boundary, requires schema version 2, separates scheduled-delivered from session-only
samples, treats incomplete grades as data gaps, and forbids automatic threshold changes. Static
inspection confirmed the packet calls only the eight bounded read helpers for holdings,
transactions, suggestions, grades, lessons, snapshots, evaluations, and publications. SHA-256
comparison confirmed the packet script, audit library, database helper, and skill are identical in
the rollout worktree and the saved project used by the automation. The audit was not run early.

## Remaining gates

1. Reconcile the first Friday process-audit receipt and confirm it remains bounded and read-only.
2. Present at least one real scheduled v3 shadow example to the owner. Do not infer approval from
   silence or from the synthetic preview.
3. After explicit owner approval of a real example, enable only the owner-recorded `stop_breach`
   canary class. Do not enable entry suggestions, target alerts, screening alerts, or the optional
   15-minute monitor in the same change.
4. Verify one canary end to end: rule version, provider observation time, evaluation/event IDs,
   publication hash, Telegram API acceptance/message ID, and any acknowledgement receipt.
5. Keep the optional faster monitor and owner-only read-first dashboard behind their later evidence
   and security-design gates.
