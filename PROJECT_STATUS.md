# Personal Stock Agent Project Status

Last updated: 2026-09-09
Canonical release: Personal Stock Agent V1 market-wide discovery candidate
Audit baseline: `432d647ef911ff63da427097f02a852e18038b62` on `origin/main`
Market-wide implementation boundary: `18386b5c76c7a7aa8b3eeda870b12d3aba2399e1`
Current state: the market-wide V1 candidate is deployed from protected main
`7408b4f874afeb489ea8cd7cf7e7c487eeea3de1`. Exact-main CI `34364808638` and protected backend
release `34365299574` passed. Production deployment `6352398867` read back Edge versions 58/29/37,
owner-only API canaries 401/403/200, canonical hashes, and 11 reconciled run/report claims. Native
owner-only Site v10 is live from the same main SHA; all 13 immutable served assets match the protected
build byte-for-byte and normalized HTML matches after removing one request-specific Cloudflare
challenge script. Site v9 remains the provider-retained rollback version. The release and Site
receipts are `docs/receipts/2026-09-09-protected-owner-dashboard-release.json` and
`docs/receipts/2026-09-09-native-site-v10.json`.

Trusted V1 use remains **no-go** only until the first normal post-release scheduled chain proves the
V1-C2 through V1-C6 operational/capability path and records either the original Telegram delivery
receipt or an explicit persisted suppression. No duplicate market run or Telegram test may be used
to close that gate.

## Market-wide V1 candidate — protected production release complete

Tasks 1–10 of the approved market-wide plan are implemented and independently reviewed through
`18386b5c76c7a7aa8b3eeda870b12d3aba2399e1`. The latest full local gate at that boundary passed
Python 1,469 passed/3 skipped/4 credentialed tests deselected, Node 71, Deno 332, package tests
7 + 53, and Playwright 24 passed/1 skipped. The Task 11 candidate gate then passed Python 1,480
passed/3 skipped/4 credentialed tests deselected, Node 71, Deno 333, package tests 7 + 53, and
Playwright 24 passed/1 skipped, with typecheck, lint, license, build, and bundle checks green. This is
local evidence only. The post-review runtime-correction gate passed Python 1,500 passed/3 skipped/4
credentialed tests deselected, Node 71, Deno 333, package tests 7 + 53, and Playwright 24 passed/1
skipped, with typecheck, lint, license, build, and bundle checks green. The final release-trust gate
passed Python 1,509 passed/3 skipped/4 credentialed tests deselected, Node 71, Deno 333, package
tests 7 + 53, and Playwright 24 passed/1 skipped; typecheck, lint, license, build, and bundle checks
also passed. That gate contains 1,997 passing checks. The final release-candidate
hardening gate then passed Python 1,541 passed/3 skipped/4 credentialed tests deselected, Node 71,
Deno 334, package tests 7 + 53, and Playwright 24 passed/1 skipped; typecheck, lint, license,
production build, and bundle checks also passed. That gate contains **2,030 passing checks** and
covers optional official-source lineage, honest empty reports, quiet intraday terminal outcomes,
credential-isolated child processes, pre-mutation recovery state, and exact local Site package/build parity while native deployment, access, live-byte, and retained rollback-target proof stay separate.
PR #35 CI run `34267466157` then exposed one hermetic-packaging omission: the SQL parser used by six
test modules was present locally but absent from `requirements.lock`. Commit `b789e0e` adds `pglast
8.4` to the declared test inputs and regenerated hash-locked binary-only requirements. A fresh Python
3.14 environment installed the lock and passed all 72 tests in the six affected modules; the focused
V1 verifier passed 96/96. Runtime code is unchanged. That historical gate is now superseded by PR
#59: exact-head CI `34364309798`, exact-main CI `34364808638`, protected release `34365299574`, and
native owner-only Site v10 all passed on the exact reviewed tree. Only the first normal post-release
scheduled receipt remains open.

The protected release workflow also assumed a separate GitHub collaborator could submit an
`APPROVED` review. This owner-only repository has one collaborator and GitHub forbids a PR author
from approving the same PR. Commit `248366e` preserves the independent-review path and adds a
solo-owner authorization that must name the exact reviewed head and successful PR CI run in an
owner-authored PR comment created after that CI and before merge. The release re-reads the PR CI,
repository-owner identity, exact comment, merge, exact-main CI, and candidate tree before checkout or
production-secret exposure; the immutable release record retains the authorization kind and IDs.

| Task | Reviewed closing commit | Local result |
|---|---|---|
| 1 — capability planner | `cffb47d` | Required free baseline and bounded phase budgets fail closed. |
| 2 — protected discovery ledgers | `e0411ae` | Additive persistence, ACL, export, restore, and recovery contracts pass. |
| 3 — dated security reference | `2e6b8ff` | SEC reference transfer/restart/capacity pass; scope remains `scope_not_guaranteed`. |
| 4 — official feeds and cursors | `899b69d` | DOE, EIA, Defense, White House, Federal Register, and SEC adapters retain truthful coverage. |
| 5 — ticker-free resolution | `284ad02` | Event-first entity resolution, value chains, syndication, and lineage pass. |
| 6 — primary exposure enrichment | `157a826` | Bounded primary-document enrichment and explicit revenue-share materiality pass. |
| 7 — broad screens | `e2f734f` | Feasibility states pass; all screen transports remain zero-request. |
| 8 — research/action split | `9f71e61` | Research survives missing suitability; action remains fail closed. |
| 9 — theme memory | `95ffbfb` | The normal collector persists canonical V2 episode revisions and consumes due research nominations within the three-task adaptive cap; projection and recovery pass. |
| 10 — acceptance and calendar | `18386b5` | Thematic/held-out/adversarial/capacity gates and NYSE 2026–2028 calendar pass. |

The protected migration boundary is the immutable additive chain
`20261005_market_wide_discovery.sql` through
`20261015_release_reader_source_tables.sql`. The `20261004` artifact is the separate immutable
production-schema reconciliation baseline; there is no `20261004_market_wide_discovery.sql` and the
release must never invent or apply one. The eleven discovery/runtime and release-security migration
file SHA-256 values are
recorded in `docs/rollouts/2026-09-06-market-wide-thematic-discovery-v1.md`.

Known V1 limits remain visible: `complete_market_coverage` is always false; free sources and request
budgets are bounded; six Yahoo screens are disabled; SEC Form 4 transport is unsupported and only
offline parser/cluster semantics exist; and the V2 action lane remains empty because no protected
issuer-valuation ledger exists. Research candidates remain suggestion-only. The NYSE calendar is
maintained through 2028 and fails closed outside that reviewed range.

Any September 8 receipt from the earlier runtime is historical operational evidence only and cannot
prove this candidate's V1-C3 discovery capability. The exact reviewed candidate has now reached
protected main, backend, and owner-only Site. V1-C2 through V1-C6 remain open only for a normal
post-release scheduled run that produces separate operational and capability receipts, including the
original Telegram delivery receipt or explicit persisted suppression. Alert V3 remains disabled and
shadow-only.

This file is the version-controlled source of truth for the Personal Stock Agent V1 rollout.
`docs/ROADMAP.md` records the implementation sequence and remaining release gates.
`docs/HANDOFF.md` is ignored and is not authoritative.

## Verified live checkpoint — 2026-09-06

- [x] PR #10 fixed the Management API `User-Agent` and changed the production reference binding to
  a masked environment secret. Exact-main `4003437` CI passed.
- [x] Managed isolated restore run `34012010196` reached the production snapshot and failed closed
  before temporary-project creation because `public.portfolio_command_acknowledgements` is absent.
  Same-job and independent cleanup receipts both report `deleted: true` and `retained: null`; the
  post-run read-only inventory found exactly one healthy production project and no temporary restore
  projects.
- [x] PR #11 merged at `774584e`. Exact-head CI `34013930731` and exact-main CI `34014003786`
  passed. The full release is manual-only and secret-backed; failed-release trust, run-attempt,
  encrypted-journal, and durable-lease ordering are hardened. No automatic release or recovery ran
  after that merge.
- [x] PR #17 merged as `8497635`; exact-main CI `34028945226` and protected read-only inventory run
  `34029103876` passed. Receipt `f1f08d635d59bb2e429c57deb1a2a9948d1ce631faa746bc8a19ea51372afb46`
  is bound to that main SHA and covers 35 protected public base-table roots without exposing rows.
- [x] Approved production schema reconciliation succeeded exactly once in run `34039011879`
  on `18c25e9b0f40d93a3b65198823b7a7c19105cdfb`. This supersedes the inventory's missing-schema
  blocker. Do not rerun reconciliation or fabricate historical migration receipts.
- [x] PR #23 fixed hosted non-superuser role restoration and was reviewed CLEAN before merge.
  Exact-main CI `34042021993` passed on `bd1cee2317a8689b8ac5a55fb38b853e7320bbfb`.
- [x] Managed isolated restore `34042155368`, attempt 1, succeeded on that exact main.
  Downloaded artifacts were validated once: all four embedded artifact SHA-256 values match;
  production before/after roots are identical; 26 record sets restored and verified; migration
  retry applied nothing. The restore receipt confirms temporary-project deletion, and both
  same-job and independent cleanup receipts confirm `deleted: true`, no retained project.
  Do not rerun this completed restore gate.
  Recovery archive SHA-256: `97b15b0200a8011302368abb1d7dd8f6cccab269a03a92a8000bdf9ebf558378`.
  Cleanup archive SHA-256: `70ef10347da57aecfce1d33eaba66caad1d7b1eedede36b3c32385d565c4a6d2`.
  Both match GitHub artifact metadata; encrypted contents were not decrypted locally.
- [x] PR #25 merged as `eb240bf`; exact-main CI `34052341671` passed. The deployed Site and three
  Edge functions are byte-identical to this documentation-only descendant, so no production
  component needs republishing or credential rotation.
- [x] PR #26 merged as `b3f7d70`; exact-main CI `34055295512` passed. The one-time protected
  existing-runtime attestation `34055419086` then passed. Artifact `9995809768` has GitHub archive
  SHA-256 `df065c0d090deb6b33014159574761dcffba6350a8b8cc5fd9adbc036bb4c7aa`;
  receipt SHA-256 `88bedc1a0488e11e2a0c75bc981087f5eeacc45ec8c9d42dbf5c937104155baf`
  was independently recomputed after one download. It binds Site v5, Edge versions 33/4/20, all 31
  required relations and 52 protected roots, exactly one owner, anonymous HTTP 401, and an owner
  HTTP 200 database-backed GET. It records zero database writes, deployments, secret rotations, or
  scheduled starts; its one canary session was revoked with local scope.
- [x] PR #28 simplified the owner dashboard around Overview, Ideas, and Reports, moved Intelligence
  and Receipts under Advanced, and used progressive disclosure for supporting evidence. It merged as
  `6ef5fcc`; exact-main CI `34066235515` passed.
- [x] PR #29 applied the UI/UX audit follow-up: keyboard skip navigation plus theme-aware contrast
  for the skip link and sign-in button. The focused control passes axe in both themes, the Sol
  re-review returned CLEAN, and exact-main CI `34066973967` passed on `a4c8031`.
- [x] Owner-only Site v7 is live from that exact main. Native deployment
  `appgdep_6a9df7eee2cc819193fefc13aa39d8bb` succeeded with one allowed owner, no groups, and zero
  external visitors. Its live script hashes match the verified build; Site v6 remains the immediate
  rollback. Receipt: `docs/receipts/2026-09-06-native-site-v7.json`.
- [x] PR #31 aligned owner sign-in with the live Supabase signed-link template and removed the
  misleading numeric-code step. Sol review approved exact head `7c6e09f`; protected main
  `b433a5b` passed exact-main CI `34068491038`. Owner-only Site v8 deployment
  `appgdep_6a9dff357ee88191bf08d54af6f4240f` succeeded with one allowed owner, no groups, and zero
  external visitors. The live entry scripts match the verified build; Site v7 is the immediate
  rollback. Receipt: `docs/receipts/2026-09-06-native-site-v8.json`.
- [x] PR #33 made email-and-password the primary owner login, added signed-link password setup and
  recovery, preserved signed magic-link fallback, and prevented dashboard reads until recovery is
  completed. Sol xhigh review approved exact head `13551e5`; protected main `e2f9d74` passed
  exact-main CI `34073314211`. Owner-only Site v9 deployment
  `appgdep_6a9e15085f048191a80497d9e75fd253` succeeded with one allowed owner, no groups, and zero
  external visitors. All application assets match the verified build after excluding Cloudflare's
  request-specific HTML challenge injection; Site v8 is the immediate rollback. Receipt:
  `docs/receipts/2026-09-06-native-site-v9.json`.

## Product interface direction

Telegram is the primary timely decision surface. The web app remains intentionally small and
owner-only. Overview contains attention items, portfolio state, current suggestions, and the latest
report; Ideas and Reports hold the daily decision history; Intelligence and Receipts preserve deeper
research and audit evidence under Advanced. It is not presented as a live trading terminal, and the
absence of continuous live data is surfaced rather than hidden.

## Approved V1-C3 market-wide discovery scope and local implementation — 2026-09-06

The owner approved keeping cross-sector thematic discovery inside V1-C3. Two GPT-6 Astra reviews
confirmed that the current runtime can preserve and rank known security evidence but does not yet
prove broad discovery beyond the portfolio and watchlist. In particular, ticker-free stories can be
dropped, unsupported provider/theme pairs can silently narrow the plan, filing metadata does not
prove business exposure, and missing portfolio suitability can remove otherwise valid research
candidates.

The implemented design and its protected-release boundary are summarized in
`docs/rollouts/2026-09-06-market-wide-thematic-discovery-v1.md`; Git history retains the superseded
task-by-task working documents. The work adds capability-aware source planning, a dated U.S.-listed
security reference, ticker-independent
event and entity resolution, bounded official-source enrichment, bounded role/theme reverse
discovery for previously unseen issuers, primary exposure facts, broad
screens with explicit feasibility states, durable theme memory, and separate research, portfolio
suitability, and action-authorization states.

The post-review runtime integration closes the local producer-to-ledger gap: the normal collector
records the exact V2 evidence packet, persists canonical theme revisions, schedules eligible due
nominations as bounded research-only follow-ups, and proves actual nonempty, honestly empty,
overlapping-query duplicate, restart, and cross-run item-reuse shapes through a disposable
PostgreSQL database and the read-only capability verifier. The action lane remains empty while the
protected issuer-valuation ledger is unavailable.

The September 8 morning receipt remains necessary operational evidence for the currently deployed
chain. It cannot close V1-C3 by itself. Local implementation and acceptance are complete through
Task 10; final exact-candidate review, protected backend release, native Sites publication, and normal scheduled
production receipts for the new discovery capability remain. A valid quiet run may contain no
positive suggestion, but it must prove the complete planned source/task lineage and distinguish no
event from failure, truncation, quota exhaustion, and unsupported capability.

## Release boundary

Final fix wave Track C restored every audited migration's exact bytes and moved the required
intelligence/report changes to `20261001_immutable_history_closure.sql`. The native-ledger fixture
retains the documented `20260907` file hash `79aeb682eba5ddaa2832d72c8ffea24caa2b7904e19c15704605bd64953367e0`;
an actual disposable PostgreSQL upgrade and idempotent retry passed.

The protected CLI now uses one encrypted, per-component capture/mutation/readback/recovery engine
for the runtime role, managed secrets, gateway, dashboard API, and Telegram function. The final
backend verifier requires downloaded artifact bytes and exact identities for all three deployed
Edge functions plus exact GitHub repository/workflow/event/branch/run/artifact identities and
archive digests. The configured private Site manifest is unchanged. The repository-native adapter now
implements transactional runtime-role restoration, recoverable managed-secret rotation, exact
downloaded Edge bytes/configuration, and committed encrypted recovery journals. Recovery reads
current state first and never restores/deletes/unsets an attempted component that did not change.
Unknown drift is not treated as release-owned: writes require exact retained candidate state or
explicit native attestation, including per-key secret value/presence proof. Unknown state keeps the
recovery journal unresolved without overwriting another actor's change.
Supabase restoration preserves prior bytes/configuration and records both the original captured
identity and the newly allocated restoration version; the existing function ID must stay exact.
It does not resurrect the old version number or accept a foreign ID with matching bytes.
GitHub Actions has no native Sites connector, so it receives no Sites write credential. The protected
workflow releases and recovers only the backend. The owner then publishes the exact same candidate
through the native owner-scoped Sites connector. `scripts/verify_native_site_release.py` compares the
local package with the protected build but deliberately leaves `owner_site` pending; only fresh direct
connector access, version, deployment, and retained-prior-version observations plus authenticated live
asset readback can close it. Historical Site v9 and protected run `34055419086` remain
evidence for the earlier runtime; they cannot be relabeled as receipts for this market-wide candidate.

The exact Track C range received independent approval after both Important recovery findings were
fixed. The final consolidated gate, GPT-6 Astra scoped re-review, historical exact-main CI,
owner-operated Site publication, Edge deployment/readback, live Auth configuration readback,
owner/anonymous API canaries, owner email-click canary, and formal non-owner denial canary are
complete. The successful reconciliation and isolated restore receipts above supersede the earlier missing-schema
inventory. The one-time protected existing-runtime attestation is complete; the next existing
scheduled receipts remain pending. The local candidate now includes a manual-only Management-API isolated restore
workflow: it proves a one-project free slot, uses only the Management read-only SQL endpoint for
production, restores only a run-created project, compares two encrypted production root hashes,
and fail-closes if the exact temporary project cannot be deleted. The candidate persists a
keyed, run-bound cleanup identity before project creation and has an `always()` workflow cleanup
step for ordinary cancellation/failure paths; runner loss still requires owner follow-up from the
bounded receipt. No scheduled run was triggered merely to collect evidence.

The product remains owner-only, suggestion-only, brokerage-free, and constrained to zero
incremental cost. It cannot place, modify, or cancel a trade. The Site-only UI releases did not
change the database, Auth configuration, Edge functions, managed secrets, Telegram output, or
schedules, and did not trigger an extra scheduled run.

The GPT-6 Astra audit of `432d647` returned a no-go verdict for trusted portfolio decision support.
The findings are implemented locally but the no-go is not lifted by local code or fixture tests.
Trusted use requires the exact-candidate local, independent-review, CI, protected runtime, Auth,
recovery, and scheduled-receipt gates below.

## GPT-6 Astra findings

Implementation dispositions record code closure. Production evidence varies by finding and is stated
separately; deployment never substitutes for the protected attestation, restore, or scheduled receipts.

| Finding | Disposition | Evidence and remaining boundary |
|---|---|---|
| F1 — entry above zone/target | Implemented | Task 3 requires an executable current price inside the entry range and below target; sizing and reward/risk use that price. Closed through `01374a9`; deployed, scheduled evidence pending. |
| F2 — report prose bypasses policy | Implemented | Task 4 renders actionable content only from persisted final decisions and derives publication authority. Closed through `4504044`; deployed, scheduled evidence pending. |
| F3 — caller-labelled or unrelated evidence | Implemented | Tasks 4 and 7 bind timestamps, categories, relationships, membership, conflicts, and report provenance to persisted records. Closed through `4504044` and `84f0839`; scheduled receipt proof pending. |
| F4 — individually valid but unfunded portfolio plan | Implemented | Task 3 reserves reconciled cash, allocation, risk, holdings, existing stop exposure, alternatives, and available shares across the complete proposal set. Closed through `01374a9`; deployed, scheduled evidence pending. |
| F5 — decimal parse becomes zero basis | Implemented | Task 5 preserves fixed-point values and makes invalid basis/profit visibly unavailable instead of zero. Closed through `c5de624`; deployed, production observation pending. |
| F6 — adapters cannot reach discovery | Implemented | Task 7 implements provider-native queries, normalized identifiers/timestamps, discoverable securities, and attributable failures. Closed through `84f0839`; live free-provider health remains a protected gate. |
| F7 — dedupe/timestamps discard corrections or conflict | Implemented | Task 7 separates request provenance from item identity and retains corrections, distinct claims, contradictions, and publication/retrieval/effective/reporting times. Closed through `84f0839`; scheduled evidence pending. |
| F8 — ordinary tests can mutate production | Implemented | Task 1 deselects credentialed tests by default and requires explicit opt-in plus an exact allowlisted non-production project before credentials are loaded. Closed through `d798129`. |
| F9 — history growth and incomplete scheduled success | Implemented; schema gate complete | Task 9 bounds relevant history without losing pending state, enforces one slot per market date/phase, required terminal stages, suppression, and overdue detection. Required recovery schema is reconciled; scheduled proof remains pending. |
| F10 — database/Telegram ambiguity | Implemented; schema gate complete | Task 6 adds durable pending/delivered/failed/uncertain delivery and acknowledgement states, original-receipt recovery, and one publication authority. Telegram v20 is deployed and the schema is reconciled; original scheduled delivery evidence remains pending. |
| F11 — backdated trades corrupt accounting | Implemented; schema gate complete | Task 5 rejects unsafe chronology changes, preserves the ledger, and returns replay-stable receipts until explicit chronological reconciliation. Production schema reconciliation is complete; production observation is pending. |
| F12 — verifier accepts stale/wrong evidence | Implemented; scheduled proof pending | Task 10 binds current-main review/CI/deployment identity, recomputed candidate bytes and hashes, stored scheduled stages, original delivery or suppression receipts, and durable recovery. Protected attestation `34055419086` binds the unchanged runtime; scheduled-stage and delivery evidence remain pending. |
| F13 — retry/cache/quota accounting | Implemented | Task 8 persists per-attempt quota, checkpoints, immutable request costs, cache/predecessor lineage, failure receipts, and restart recovery. Closed through `eff4612`; live provider/database proof pending. |
| F14 — ranking placeholders/disconnected V1 inputs | Implemented | Task 8 supplies protected server-owned holdings, valuation, liquidity, overlap, discovery strength, comparison, and learning inputs; unknown values fail closed. Closed through `eff4612`; scheduled production output pending. |
| F15 — misleading outcome/weekly calculations | Implemented | Task 11 groups losses per eligible recommendation, aligns benchmark windows, uses session highs/lows, distinguishes fills, and retains veto-only weeks. Closed through `f7af10f`; production observation pending. |
| F16 — recovery unproven | Implemented; protected isolated live drill complete | Task 10 creates authenticated encrypted exports, exact schema/receipt reconciliation, durable rollback artifacts, and an isolated disposable-runtime restore drill. Protected live isolated restore `34042155368` verified 26 record sets, unchanged production roots, and deletion of the temporary project; both cleanup receipts passed. |
| F17 — excessive read privilege/unpinned Python | Complete | Task 11 uses a restricted read-only weekly role with verified TLS and installs a complete hash-locked binary dependency set, including recovery cryptography. The live runtime and protected attestation `34055419086` passed. |
| F18 — token refresh defeats idle lock | Implemented | Task 2 moves the privacy deadline only on explicit owner activity, never token refresh. Closed through `ce6d9a7`; signed-link-compatible Site v5 is live and the owner confirmed that its signed email link opened the portfolio dashboard. |
| F19 — early close/quote identity gaps | Implemented | Task 11 models maintained 2026 early closes, fails closed outside coverage, and rejects wrong symbol/currency or unknown halt/spread/liquidity. Closed through `f7af10f`; deployed, scheduled evidence pending. |

## Owner email sign-in

Email-and-password is the primary owner login. Initial password setup and password recovery use
Supabase's signed recovery link, and signed magic-link login remains available as a fallback. The
browser does not present a numeric-code field. It redirects both link flows to the exact deployed
origin, consumes their signed callbacks, retains the session only in session storage, and preserves
the 30-minute activity-based privacy lock. A tab-scoped recovery marker keeps the password-reset
screen ahead of private dashboard reads until the new password is saved or the owner signs out.

Protected Auth configuration checks now require both the magic-link and recovery templates to use
`{{ .ConfirmationURL }}`. They do not accept `{{ .Token }}` because this browser intentionally has no
numeric-code entry step.

Live Auth was read back again on 2026-09-06: signup is disabled, the Email provider is enabled, JWT
lifetime is 900 seconds, OTP length is 6, OTP expiry is 600 seconds, both the default magic-link and
recovery templates use `ConfirmationURL`, the Site URL and only allowed redirect are the private
owner Site, and exactly one confirmed owner exists. The templates were not changed because custom
template modification is unavailable with the project's default free-tier mail provider.

The owner confirmed on 2026-09-05 that the signed email link opened the live portfolio dashboard.
A separate bounded canary created one temporary confirmed non-owner without sending email, verified
that the live owner API returned HTTP 403 with `owner_only` and no portfolio data, revoked the
temporary session, deleted the user, and confirmed that Auth inventory returned to exactly one owner.

## V1 checkpoints

### V1-C1 — Release control

- [x] Owner approved the Astra remediation design and implementation plan.
- [x] Audit baseline and non-negotiable authority/cost boundaries are recorded.

V1-C1 remains complete.

### V1-C2 — Free intelligence ingestion

- [x] Provider discovery, normalized identities and timestamps, contradiction preservation,
  cache/checkpoint lineage, and quota accounting are implemented and task-reviewed locally.
- [x] Apply the receipt-bound final-state reconciliation (`34039011879`).
- [x] Retain the protected collection/gateway runtime-attestation receipt (`34055419086`).
- [ ] Prove supported free-provider paths and persisted source/quota receipts on an existing
  scheduled run.

V1-C2 remains reopened until scheduled receipt evidence passes.

### V1-C3 — Market-discovery brain

- [x] Server-owned ranking inputs, fail-closed unknown values, packet construction, and scheduled
  lifecycle controls are implemented and task-reviewed locally.
- [x] Complete exact-candidate CI and protected runtime attestation (`34055295512`, `34055419086`).
- [x] Approve the GPT-6 Astra-reviewed market-wide thematic discovery design and implementation plan.
- [ ] Observe the current deployed chain on its normal schedule; treat this as operational evidence,
  not proof of the new discovery capability.
- [x] Implement a capability-aware fair planner, official-source cursors, and a dated U.S.-listed
  security reference without adding paid or metered providers.
- [x] Resolve ticker-independent events, public/private entities, securities, value-chain paths, and
  primary business-exposure evidence outside the owner's portfolio and watchlist.
- [x] Preserve research candidates when portfolio suitability is unavailable while keeping action
  authorization fail-closed.
- [x] Pass the magnet, aluminum, data-center energy, uranium, robotics, adversarial, capacity,
  recovery, and held-out-sector acceptance scenarios without hardcoded company lists.
- [x] Replace the separate 2026-only calendar copies with one reviewed NYSE source synchronized
  through the official published 2028 holiday and early-close schedule; fail closed afterward.
- [x] Merge the exact reviewed candidate through protected main, complete protected backend release
  `34365299574`, and publish/read back owner-only Site v10 from exact main `7408b4f`.
- [ ] Retain a normal post-release scheduled capability receipt without a duplicate run.

V1-C3 remains open only for normal scheduled production capability evidence.

### V1-C4 — Personal comparison brain

- [x] Comparison and advisory-learning inputs are constrained to persisted, typed, server-owned
  evidence; missing concentration, liquidity, or overlap fails closed.
- [ ] Verify the deployed read/write authority and one production scheduled comparison chain.

V1-C4 is reopened.

### V1-C5 — Reports, dashboard, and delivery

- [x] Final-policy report authority, durable report/command delivery, fixed-point accounting,
  outcome corrections, owner inactivity, and read-only weekly audit controls are implemented and
  task-reviewed locally.
- [x] Read back the live signup-disabled, 900-second JWT, six-digit/600-second email settings and
  support the default signed magic-link and recovery templates without adding paid SMTP.
- [x] Deploy the reviewed API candidate and pass owner and anonymous canaries.
- [x] Complete the owner email-click canary; the owner confirmed the live dashboard appeared.
- [x] Complete a formal non-owner login canary and retain its bounded denial receipt: HTTP 403
  `owner_only`, no portfolio data, temporary user deleted, and exactly one owner afterward.
- [x] Publish the signed-link-only owner login correction as owner-only Site v8 from exact main
  `b433a5b`; retain Site v7 as rollback.
- [x] Publish password-primary login with signed-link setup/recovery and fallback as owner-only Site
  v9 from exact main `e2f9d74`; retain Site v8 as rollback.
- [x] Publish the complete market-wide candidate as owner-only Site v10 from exact main `7408b4f`;
  verify one-owner access, anonymous 401, authenticated live-file parity, and retained Site v9.
- [ ] Reconcile an original Telegram delivery ID or explicit persisted suppression from the next
  existing scheduled chain.

V1-C5 is reopened.

### V1-C6 — Independent review and protected rollout

- [x] Candidate-bound release verification, durable recovery orchestration, migration ledgering,
  encrypted export, and local disposable restore drill are implemented and task-reviewed locally.
- [x] Consolidated local `npm run test:all` gate passed for the final code at `883d521`.
- [x] GPT-6 Astra approved the final scoped re-review with no unresolved local Critical or Important
  finding; the final split-trust review also returned CLEAN with no Critical/P1/P2 finding.
- [x] Final PR #59 exact-head CI `34364309798` and exact-main CI `34364808638` passed; both Sol and
  Astra returned CLEAN on the exact reviewed head `e4029bb`.
- [x] Historical production runtime readback, three-function parity, private Site publication, and
  owner/anonymous canaries completed; schema reconciliation is now complete above.
- [x] Approved receipt-bound schema reconciliation completed (`34039011879`); do not rerun.
- [x] Managed isolated restore and both cleanup paths completed (`34042155368`); do not rerun.
- [x] Protected one-time existing-runtime attestation `34055419086` verified the unchanged
  reconciled database state before the next routine scheduled write.
- [x] Protected backend release `34365299574` and deployment `6352398867` succeeded on exact main
  `7408b4f`; native owner-only Site v10 publication and live-file readback also succeeded.
- [ ] Next existing post-deployment scheduled intelligence/report/publication receipt chain; never
  trigger a duplicate merely to obtain evidence.

V1-C6 remains open for the normal post-release receipt; trusted use stays no-go until it passes.

## Immediate next gates

1. Preserve the completed schema, isolated recovery, protected release, and Site v10 gates; do not
   rerun them.
2. Wait for the next normal post-release scheduled phase. Do not dispatch a market run or Telegram
   test for evidence.
3. Reconcile the analysis, discovery, packet, report, publication, provider/quota, dashboard, and
   original Telegram delivery or explicit suppression receipts through the active heartbeat.
4. Close V1-C2 through V1-C6 and the active V1 goal only when that receipt passes.
5. Review Alert V3 separately in shadow/canary mode after V1 closes.

## Native Site releases

PRs #28 and #29 produced the simplified private Site v7. PR #31 removed the browser's misleading
numeric-code step and produced Site v8. PR #33 then added password-primary login with signed-link
setup/recovery and magic-link fallback. Current UI main `e2f9d74` passed exact-main CI `34073314211`,
and private Site v9 was published. On 2026-09-09, Site v10 published the complete market-wide
candidate from exact main `7408b4f`. Native readback confirmed custom access with exactly one
allowed owner, no allowed groups, and zero external visitors. All 13 immutable application assets
match the protected build; normalized HTML also matches after excluding Cloudflare's request-specific
challenge injection. Site v9 is retained as the immediate rollback. The current bounded receipt is
`docs/receipts/2026-09-09-native-site-v10.json`; the v7 through v9 receipts remain historical evidence.

The Site v10 publication did not mutate the production database, Supabase Auth, any Edge function,
managed secrets, Telegram, or scheduled workflows. Its backend evidence boundary is protected
release `34365299574`; it does not claim the Site publication itself attests backend bytes. A later
Site-only UI change may use the
owner-scoped native Sites connector when it preserves exact source, verified build, owner-only
access, deployment, live-bundle, and rollback receipts.

## Protected attestation boundary — completed 2026-09-06

Source comparison `git diff ba3ebc1..b3f7d70 -- apps/web .openai supabase/functions` is empty.
The published Site v5 and Edge functions do not need republishing for these recovery changes.
The owner app is https://personal-stock-agent.rupesh-sivapu.chatgpt.site; retain its existing access.

The protected environment contains the pre-existing `RELEASE_RECOVERY_KEY`,
`SUPABASE_ACCESS_TOKEN`, and `SUPABASE_PROJECT_REF`. Existing Supabase keys and the single confirmed
owner identity were recovered through authenticated read-only platform access and added without
rotation as `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_PUBLISHABLE_KEY`,
`DASHBOARD_OWNER_USER_ID`, and `DASHBOARD_OWNER_EMAIL`. The preserved active dashboard database URL
is bound by the protected `DASHBOARD_DATABASE_URL_SHA256` variable without exposing or rotating the
secret. The following original mutation-workflow inputs remain unavailable:

- `RELEASE_READONLY_DATABASE_URL`
- `DASHBOARD_NON_OWNER_ACCESS_TOKEN`
- `POSTGRES_URL`, `SUPAVISOR_SESSION_URL`
- `DASHBOARD_PRIOR_MANAGED_SECRETS_JSON`

All five referenced environment variables are now configured from verified live/repository state:
the allowed and Site origins are the live private Site URL, the current gateway is version 33, and
its rollback source is bound to `dceb76c9a32a94a16e0cdc0f9dab602f465ef186` with source SHA-256
`955f98817ad5e90a9d33b2a537282209d9d8af466ec757c96719dd73aadd1063`.

The protected mutation workflow now has a concrete backend-only transport for migrations, the
runtime role, managed secrets, and all three Edge functions. It authenticates the exact reviewed
candidate before checkout or production-secret exposure and retains encrypted recovery state before
mutation. Native Sites publication remains a separate owner-scoped step and receipt because GitHub
Actions has no native Sites connector. The unchanged-runtime attestation and Site-only UI releases
remain historical evidence for their own candidates.

The approved one-time workflow used two independent trust domains: a fresh, bounded native Sites
receipt captured through the owner-scoped connector, and protected GitHub readback of current main,
the completed reconciliation artifact, database inventory, Auth configuration and sole-owner
inventory, managed-secret digests, exact deployed Edge bytes, anonymous denial, and one authenticated
owner GET. The canary created one temporary owner session and revoked only that current session. It
makes no database, deployment, Site, secret, or scheduled-run mutation. Full database-root equality
is intentionally a one-time pre-schedule baseline; normal later market-data growth is verified by
the scheduled-chain receipt instead of rerunning this bridge. Protected run `34055419086` passed and
its artifact and receipt hashes are recorded in the verified checkpoint above. Do not rerun it.

The formal scheduled reader in `scripts/protected_evidence.py` requires the missing restricted
`RELEASE_READONLY_DATABASE_URL`; `scripts/verify_personal_stock_agent_v1.py` requires the protected
backend artifact, then validates the persisted chain,
original Telegram delivery or explicit suppression, and quota lineage. Never send another
report to manufacture evidence. The existing one-shot heartbeat will inspect the next normal market
session after the protected attestation; it must not dispatch a duplicate run.

## Consolidated local evidence

The first `npm run test:all` run exposed six stale integration checks: two packet fixtures did not
supply newly required ranking context; one repository allowlist still expected the removed direct
grade query; two SQL tests scanned unrelated later migrations for dynamic SQL; and one schema mirror
test assumed no additive migrations followed `20260908`. The generated fresh schema was also behind
the final ordered migrations. No product behavior was relaxed.

After those exact six checks were corrected and `scripts/sync_intelligence_schema.py` regenerated
the fresh schema, the focused reproduction passed 6/6. The one permitted consolidated rerun passed:

- 589 Python tests passed, 3 skipped, and 4 credentialed database-integration tests deselected;
- 71 Node tests passed;
- 289 Deno tests passed, followed by Deno checks for all three Edge entrypoints;
- 6 dashboard-contract and 34 web-unit tests passed;
- dashboard-contract and web TypeScript checks, web lint, 242-package license verification,
  production build, and the 14-file bundle scan passed;
- 17 Playwright tests passed and the opt-in live read-only canary was skipped.

That historical Task 12 gate was 1,006 passed tests, 4 skipped tests, and 4 explicitly deselected
credentialed integration tests at `a0f8158`.

After the final Astra fix wave and its two scoped corrections, `npm run test:all` passed again at
`883d521`: 779 Python, 71 Node, 290 Deno, 6 dashboard-contract, 34 web-unit, and 17 Playwright tests.
That is **1,197 passed**, 4 skipped, and 4 credentialed database integrations deliberately deselected.
Typechecks, Edge checks, web lint, the 242-package license gate, production build, bundle scan, schema
sync, focused PostgreSQL restore/retry evidence, and `git diff --check` also passed.

## Production truth

Production contains owner-only Site v9 and the reviewed backend runtime. Approved schema reconciliation
`34039011879`, isolated live restore `34042155368`, and protected existing-runtime attestation
`34055419086` passed. These do not substitute for a post-remediation scheduled receipt chain. The owner email-click and
formal non-owner denial canaries remain complete. Native Site v9 readback confirms one allowed owner,
no groups, and zero external visitors. Its receipt explicitly separates the UI-only publication from
the unchanged backend and the still-pending scheduled chain.

## Decisions and guardrails

- One complete V1; no silent V1.1 deferral for approved scope.
- Zero incremental dollars; no paid/premium/trial providers or metered runtime model API.
- Approved free-source boundary only: GDELT, existing free Alpha Vantage, existing free Finnhub,
  Yahoo, and free official sources. Social material is hypothesis discovery only.
- Owner-only, friend invitations disabled, no brokerage credentials or execution, and every trade
  remains a manual owner decision.
- Missing, stale, contradictory, quota-blocked, or unverifiable evidence fails closed.
- Learning is advisory and cannot silently change policy, thresholds, source priority, sizing,
  routing, or authority.
- A local test, green CI run, stored report, or quiet bot is not production correctness evidence.
