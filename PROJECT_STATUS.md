# Personal Stock Agent Project Status

Last updated: 2026-09-05
Canonical release: Personal Stock Agent V1 safety remediation
Audit baseline: `432d647ef911ff63da427097f02a852e18038b62` on `origin/main`
Consolidated local-gate candidate: `883d521728b1b3c2700a78dab1d65208105d7a2f`
Current state: the Astra remediation is merged, the exact-main CI gate passed, all three reviewed
Edge functions are deployed with runtime-byte readback, and signed-link-compatible private Site v5
is live. Remaining release evidence includes the protected workflow/restore, owner email-click and
formal non-owner login canaries, and the next existing scheduled receipts.

This file is the version-controlled source of truth for the Personal Stock Agent V1 rollout.
`docs/ROADMAP.md` records the implementation sequence and remaining release gates.
`docs/HANDOFF.md` is ignored and is not authoritative.

## Release boundary

Final fix wave Track C restored every audited migration's exact bytes and moved the required
intelligence/report changes to `20261001_immutable_history_closure.sql`. The native-ledger fixture
retains the documented `20260907` file hash `79aeb682eba5ddaa2832d72c8ffea24caa2b7904e19c15704605bd64953367e0`;
an actual disposable PostgreSQL upgrade and idempotent retry passed.

The protected CLI now uses one encrypted, per-component capture/mutation/readback/recovery engine
for the runtime role, managed secrets, gateway, dashboard API, Telegram function, and owner Site.
The final verifier requires downloaded artifact bytes and exact identities for all four deployed
artifacts. The configured private Site manifest is unchanged. The repository-native adapter now
implements transactional runtime-role restoration, recoverable managed-secret rotation, exact
downloaded Edge bytes/configuration, and committed encrypted recovery journals. Recovery reads
current state first and never restores/deletes/unsets an attempted component that did not change.
Unknown drift is not treated as release-owned: writes require exact retained candidate state or
explicit native attestation, including per-key secret value/presence proof. Unknown state keeps the
recovery journal unresolved without overwriting another actor's change.
Supabase restoration preserves prior bytes/configuration and records both the original captured
identity and the newly allocated restoration version; the existing function ID must stay exact.
It does not resurrect the old version number or accept a foreign ID with matching bytes.
Final Astra findings 3 and 4 are locally implemented. For finding 5, GPT-6 Astra accepted the
pre-mutation Sites block as the correct local safety boundary because the native owner-scoped Sites
connector has no callable GitHub Actions management transport. Owner-operated Sites publication is
now complete; the GitHub protected-workflow receipt remains a separate gate.

The exact Track C range received independent approval after both Important recovery findings were
fixed. The final consolidated gate, GPT-6 Astra scoped re-review, exact-main CI, owner-operated Site
publication, database migration readback, Edge deployment/readback, live Auth configuration readback,
and owner/anonymous API canaries are complete. The GitHub protected workflow, isolated live restore,
formal non-owner login canary, and next existing scheduled receipts remain pending. No scheduled run
was triggered merely to collect evidence.

The product remains owner-only, suggestion-only, brokerage-free, and constrained to zero
incremental cost. It cannot place, modify, or cancel a trade. The production work did not change the
free-tier Auth template, send Telegram output, call a brokerage, place a trade, or trigger an extra
scheduled run.

The GPT-6 Astra audit of `432d647` returned a no-go verdict for trusted portfolio decision support.
The findings are implemented locally but the no-go is not lifted by local code or fixture tests.
Trusted use requires the exact-candidate local, independent-review, CI, protected deployment, Auth,
recovery, and scheduled-receipt gates below.

## GPT-6 Astra findings

Implementation dispositions record code closure. Production evidence varies by finding and is stated
separately; deployment never substitutes for the protected-workflow, restore, or scheduled receipts.

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
| F9 — history growth and incomplete scheduled success | Implemented | Task 9 bounds relevant history without losing pending state, enforces one slot per market date/phase, required terminal stages, suppression, and overdue detection. Closed through `9ba3bc0`; remote ledger is current and scheduled proof is pending. |
| F10 — database/Telegram ambiguity | Implemented | Task 6 adds durable pending/delivered/failed/uncertain delivery and acknowledgement states, original-receipt recovery, and one publication authority. Closed through `c498177`; Telegram v20 is deployed but no extra send was triggered. |
| F11 — backdated trades corrupt accounting | Implemented | Task 5 rejects unsafe chronology changes, preserves the ledger, and returns replay-stable receipts until explicit chronological reconciliation. Closed through `c5de624`; remote ledger is current and production observation is pending. |
| F12 — verifier accepts stale/wrong evidence | Implemented; operational proof pending | Task 10 binds current-main review/CI/deployment identity, recomputed candidate bytes and hashes, stored scheduled stages, original delivery or suppression receipts, and durable recovery. Closed through `6da3e84`; exact-main CI and owner deployment completed, protected-workflow receipt pending. |
| F13 — retry/cache/quota accounting | Implemented | Task 8 persists per-attempt quota, checkpoints, immutable request costs, cache/predecessor lineage, failure receipts, and restart recovery. Closed through `eff4612`; live provider/database proof pending. |
| F14 — ranking placeholders/disconnected V1 inputs | Implemented | Task 8 supplies protected server-owned holdings, valuation, liquidity, overlap, discovery strength, comparison, and learning inputs; unknown values fail closed. Closed through `eff4612`; scheduled production output pending. |
| F15 — misleading outcome/weekly calculations | Implemented | Task 11 groups losses per eligible recommendation, aligns benchmark windows, uses session highs/lows, distinguishes fills, and retains veto-only weeks. Closed through `f7af10f`; production observation pending. |
| F16 — recovery unproven | Local mechanism and disposable drill implemented; live drill pending | Task 10 creates authenticated encrypted exports, exact schema/receipt reconciliation, durable rollback artifacts, and an isolated disposable-runtime restore drill. A protected live isolated restore has not been performed. |
| F17 — excessive read privilege/unpinned Python | Implemented | Task 11 uses a restricted read-only weekly role with verified TLS and installs a complete hash-locked binary dependency set, including recovery cryptography. Closed through `f7af10f`; live runtime is deployed and protected-workflow receipt pending. |
| F18 — token refresh defeats idle lock | Implemented | Task 2 moves the privacy deadline only on explicit owner activity, never token refresh. Closed through `ce6d9a7`; signed-link-compatible Site v5 is live and the owner email-click canary is pending. |
| F19 — early close/quote identity gaps | Implemented | Task 11 models maintained 2026 early closes, fails closed outside coverage, and rejects wrong symbol/currency or unknown halt/spread/liquidity. Closed through `f7af10f`; deployed, scheduled evidence pending. |

## Owner email sign-in

The owner web app and protected Auth checks support both Supabase email modes: a six-digit numeric
code (`{{ .Token }}`) or a signed email link (`{{ .ConfirmationURL }}`). The browser explicitly
redirects the email flow to its deployed origin, consumes signed-link callbacks, retains the session
only in session storage, and preserves the 30-minute activity-based privacy lock.

Live Auth was read back on 2026-09-05: signup is disabled, JWT lifetime is 900 seconds, OTP length is
6, OTP expiry is 600 seconds, the default free-tier template uses `ConfirmationURL`, the Site URL and
only allowed redirect are the private owner Site, and exactly one confirmed owner exists. The template
was not changed because custom template modification is unavailable with the project's default
free-tier mail provider.

## V1 checkpoints

### V1-C1 — Release control

- [x] Owner approved the Astra remediation design and implementation plan.
- [x] Audit baseline and non-negotiable authority/cost boundaries are recorded.

V1-C1 remains complete.

### V1-C2 — Free intelligence ingestion

- [x] Provider discovery, normalized identities and timestamps, contradiction preservation,
  cache/checkpoint lineage, and quota accounting are implemented and task-reviewed locally.
- [ ] Apply the ordered migrations and deploy the protected collection/gateway candidate.
- [ ] Prove supported free-provider paths and persisted source/quota receipts on an existing
  scheduled run.

V1-C2 is reopened until protected deployment and scheduled receipt evidence pass.

### V1-C3 — Market-discovery brain

- [x] Server-owned ranking inputs, fail-closed unknown values, packet construction, and scheduled
  lifecycle controls are implemented and task-reviewed locally.
- [ ] Complete exact-candidate CI, protected deployment, and one non-duplicated scheduled chain.

V1-C3 is reopened.

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
  support the default signed-link template without adding paid SMTP.
- [x] Deploy the reviewed API candidate and pass owner and anonymous canaries.
- [ ] Complete a formal non-owner login canary and retain its bounded denial receipt.
- [ ] Reconcile an original Telegram delivery ID or explicit persisted suppression from the next
  existing scheduled chain.

V1-C5 is reopened.

### V1-C6 — Independent review and protected rollout

- [x] Candidate-bound release verification, durable recovery orchestration, migration ledgering,
  encrypted export, and local disposable restore drill are implemented and task-reviewed locally.
- [x] Consolidated local `npm run test:all` gate passed for the final code at `883d521`.
- [x] GPT-6 Astra approved the final scoped re-review with no unresolved local Critical or Important finding; the native Sites publication conditional remains a production gate.
- [x] Exact-head CI passed on reviewed `main` at `c9e3140`.
- [x] Owner-operated production database readback, three-function runtime parity, private Site
  publication, and owner/anonymous canaries completed.
- [ ] Run the GitHub protected production workflow once its independent-review and Sites transport
  gates are available; do not weaken its fail-closed preflight.
- [ ] Protected isolated live restore drill and reconciliation.
- [ ] Next existing post-deployment scheduled intelligence/report/publication receipt chain; never
  trigger a duplicate merely to obtain evidence.

V1-C6 is reopened and the release remains no-go for trusted use.

## Immediate next gates

1. Complete the owner email-click and formal non-owner denial canaries.
2. Complete the protected-workflow and isolated live-restore evidence without weakening preflight.
3. Reconcile the next existing scheduled chain without triggering a duplicate.

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

Production contains the reviewed Astra remediation: gateway version 33, owner-dashboard API version
4, Telegram function version 20, an up-to-date remote migration ledger, and private Site version 5
from exact `main` merge `ba3ebc1`. Site deployment `appgdep_6a9cd0def8f0819187aa7d30d99a7ada`
succeeded; the live login bundle hash exactly matches the reviewed build. The owner API returned all
nine bounded projections to an ephemeral owner canary and denied anonymous access. This is not yet
proof of the GitHub protected workflow, an isolated live restore, an owner email-click, a formal
non-owner login denial, or a post-remediation scheduled receipt chain.

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
