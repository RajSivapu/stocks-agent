# Gate A-G release acceptance

No release, owner cutover, or friend invitation may treat a partial test run as product acceptance.
Every criterion below must have fresh evidence for the exact 40-character commit. `skipped`, `unknown`,
`pending`, and `failed` are failures. Mock browser results never count as live staging evidence.

## Evidence handling

Keep raw logs, screenshots, restore records, provider sessions, and manual sign-offs in the private
operator evidence location. Do not upload them as public-repository Actions artifacts. Hash each exact
evidence file with SHA-256 and put only the digest and UTC check time in the acceptance source. The
source and generated report must contain no tokens, email addresses, owner IDs, holdings, tickers, or
model content.

An automated entry uses `method: "automated"`. A manual entry uses `method: "signed_manual"` only after
the operator has signed the underlying timestamped record or the protected GitHub environment has
captured the named reviewer approval. The script validates freshness, completeness, methods, commit
identity, and hash shape; the release reviewer remains responsible for validating the signature and
that the hashed private artifact is the evidence described here.

The exact source shape is:

```json
{
  "version": 1,
  "commit": "0000000000000000000000000000000000000000",
  "evidence": [
    {
      "criterion": "migration_parity",
      "status": "passed",
      "checked_at": "2026-09-03T18:00:00Z",
      "evidence_hash": "0000000000000000000000000000000000000000000000000000000000000000",
      "method": "automated"
    }
  ]
}
```

Include exactly one entry for every criterion below. Build the private, mode-0600, create-only report:

```bash
python scripts/release_acceptance.py \
  --evidence /private/path/release-evidence.json \
  --report /private/path/release-acceptance.json
```

The command fails closed on missing, duplicate, malformed, non-passing, stale, or future-dated
evidence. Standard evidence expires after 24 hours. Phone step-up, rollback, and friend-cycle evidence
expires after seven days; encrypted restore evidence expires after 30 days.

## Gate A - tenancy and runtime boundary

- `migration_parity` — run migration-from-current, fresh-schema, rollback, and canonical-schema tests
  against disposable PostgreSQL 17. Hash the complete successful output from
  `python scripts/verify_multitenancy_migration.py --rollback-only` and
  `python -m pytest tests/security/test_multitenancy_schema.py tests/security/test_rls_catalog.py -q`.
- `anonymous_private_denial` — the staging live-browser test must receive an empty private API view as
  an anonymous caller. A unit or mocked PostgREST result is insufficient.
- `two_owner_isolation` — run `E2E_LIVE=1 npm --workspace apps/web run test:e2e` with the temporary
  two-owner bundle. It must prove each direction through browser-origin PostgREST and reject a forged
  cross-owner Edge mutation. Delete both temporary identities in the always-run cleanup step.
- `runtime_secret_boundary` — hash passing secret scan, exact machine-role tests, Edge auth tests, and
  static build scan output. The browser bundle must contain no service-role, provider, Telegram,
  database, or signing credential.

## Gate B - money records and analyst safety

- `preview_atomic_receipts` — run the portfolio command RPC, app API, idempotency, expiry, correction,
  and concurrent-confirmation tests. A UI state is never evidence without the server receipt.
- `financial_input_validation` — run contract decimal/portfolio/command-preview tests and the Python
  migration fixtures for quantity, fill price, fees, broker cash reconciliation, and sell-all.
- `ledger_projection` — run `tests/security/test_ledger_projection.py`; holdings must be reproducible
  from the immutable ledger, with projection failures pausing affected writes.
- `run_slot_exclusivity` — run scheduler RPC and handler tests proving one active run per owner, market
  date, and phase, including scheduled/Run Now overlap and duplicate ticks.
- `model_authority_boundary` — run provider contract and deterministic policy suites. Model output may
  propose research but cannot select owner identity, persist directly, publish directly, alter risk
  limits, or execute a brokerage action.
- `fresh_intraday_evidence` — run gateway evidence-packet, source-fetch, market-data, corporate-action,
  and policy tests. They must reject copied morning packets and stale/conflicting/action-pending data.
- `telegram_security` — run Telegram parser, pairing, webhook RPC, replay, sender/chat binding,
  confirmation, and delivery-receipt suites. Quiet intraday must send nothing; unknown delivery must
  never be represented as success.

The full `npm run test:all` command covers these suites and the local desktop/iOS/Android browser
flows. Preserve the full command output as the Gate B automated artifact.

## Gate C - real provider

- `claude_real_handshake` — from the deployed staging Connections screen, create a disabled connection,
  save the one-time gateway credential, enter the real Routine fire URL and one-time token after phone
  step-up, and let the application fire the no-write handshake. The signed record must show the inbound
  callback and verified session for this connection and release window. Curl, Claude's Run now button,
  an old green Routine, or a mocked test does not pass.

Also exercise trigger rejection, timeout/outcome-unknown, Routine allowance exhaustion, stale callback,
and revoked credential fixtures before signing. None may create a phantom run or delivery receipt.

## Gate D - owner access and disclosure

- `phone_otp_and_step_up` — on an actual phone mail client, complete invite-only email OTP, sign out and
  back in, request a fresh code, and complete a destructive-action step-up. A desktop magic-link-only
  test does not pass. Sign the record without retaining the address or code.
- `consent_disclosures` — browser tests must deny every private screen until the current consent version
  is accepted, expose privacy/risk documents, preserve the suggestion-only copy, and confirm that no
  service worker stores private portfolio responses.

## Gate E - recoverability

- `encrypted_recovery` — follow `docs/runbooks/backup-restore.md` using the newest private R2 `age`
  archive. Restore only to staging; verify identity rebinding, ledger counts/digests, projections,
  relationships, tombstones, cancelled in-flight work, rotated secrets/webhook, and fresh provider
  reconnection. A backup upload without a completed restore is not evidence.

## Gate F - delivery and rollback

- `deployment_rollback_drill` — with triggers paused, deploy the exact candidate to staging, run health
  and isolation, then deploy the reviewed previous application reference without down-migrating. Run
  health/isolation again and return to the candidate. Sign the commit references and sanitized hashes;
  keep raw URLs, identities, and logs private.

## Gate G - complete-product boundary

- `no_brokerage_surface` — scan source/build output and exercise all owner and Telegram recordkeeping
  routes. There must be no broker credential field, order endpoint, execute/modify/cancel trade action,
  or invented execution confirmation. Alpaca, IBKR, Webull, and similar connectors remain out of scope.
- `friend_onboarding_cycle` — only after the owner soak, onboard one trusted friend through custom SMTP,
  phone OTP, consent, one-time Claude kit, real handshake, optional private Telegram pairing, one web
  preview/cancel, one confirmed recordkeeping event, and a complete pre/intraday/post scheduled cycle.
  The friend performs the connection kit without operator account access. Sign the sanitized cycle
  record; support notes are failures to improve before wider invitations.

## Final decision

Compare the report commit to the candidate, verify every manual signature against its private artifact,
and independently recompute representative hashes. A complete report authorizes review; it does not
resume paused triggers by itself. Any mismatch keeps mutation paths and friend invitations disabled.
