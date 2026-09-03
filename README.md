# Stock Agent

Stock Agent is an invite-only, provider-neutral US-stock research and portfolio-recordkeeping
product. It can suggest, explain, and record an owner-reported fill after explicit preview and
confirmation. It **cannot place, modify, or cancel** a brokerage order, and it has no brokerage
connector or credential surface.

## Release status

The complete multi-user candidate is implemented on `codex/stock-agent-reliability` but is **not
deployed to staging or production**. Local automated tests are not substitutes for custom SMTP,
live two-owner isolation, a real provider handshake, encrypted R2 restore, protected rollback, or the
owner market-cycle soak. **Friend invitations remain disabled** until those external gates pass.

See [the roadmap](docs/ROADMAP.md), [release acceptance](docs/runbooks/release-acceptance.md), and
[owner cutover](docs/runbooks/owner-cutover.md) before changing rollout status.

## Architecture

```text
Cloudflare static web app
  -> Supabase email OTP + current consent
  -> owner-scoped API views / Edge command boundary
  -> preview -> explicit Confirm -> immutable ledger -> derived holdings

Telegram private chat
  -> secret webhook + paired chat/user digest
  -> same preview/confirm command state machine
  -> recordkeeping only

Supabase five-minute scheduler
  -> DST/holiday-aware owner run slot
  -> owner connection in Vault
  -> one unscheduled provider Routine
  -> scoped agent-gateway contract
  -> fresh evidence + Analyst + same-model Checker
  -> server quote refresh + deterministic policy
  -> atomic decision/publication receipts -> eligible Telegram notification

Supabase Cron backup
  -> age encryption before upload -> private Cloudflare R2
  -> independent backup-age monitor
```

The application owns the schedule, run identity, market calendar, financial state, quote authority,
policy result, message rendering, and delivery receipts. Model output can only propose bounded
analysis. A downgrade or veto cannot be upgraded by prose, and a send/write is never claimed without
the server receipt that proves it.

The hosted runtime uses Supabase, Cloudflare static assets/R2, Telegram, and each owner's provider
subscription. **Your Mac can be off.** There is no Cloud Run requirement and no model API key in the
product runtime. Free-tier and subscription allowances still apply; availability is not guaranteed.

## Provider support

The V2 contract is provider-neutral, but **Claude Routines is the only release-one provider adapter**.
Each owner connects a separate Claude account and **one unscheduled Routine per owner**. The
application owns the schedule and fires the provider's API trigger. The owner-scoped inbound
credential is stored in Claude's host-bound API-credential proxy; the outbound trigger secret is
stored in Supabase Vault. Neither is a model API key.

ChatGPT, Grok, and bring-your-own-key connectors are not release-one features. Adding one requires a
separate adapter, real handshake proof, capability/usage semantics, threat review, and the same
server-owned safety boundaries. See the [Claude connection kit](docs/connection-kits/claude-routine-v1.md)
and [Routine setup summary](routines/README.md).

## Analysis lifecycle

For every market date and phase, the scheduler permits one active slot per owner. Weekends have no
slots. A holiday pre-market slot publishes fixed market-closed copy without invoking a model;
later phases remain silent. Early closes and daylight-saving transitions are computed from the New
York market calendar rather than fixed UTC cron times.

A non-holiday run:

1. claims a server-generated slot and bounded owner context;
2. creates a signed evidence packet with current quote/source receipts;
3. requires fresh Analyst and same-model Checker submissions;
4. refetches authoritative quotes and applies deterministic policy;
5. persists analysis before any notification attempt;
6. classifies delivery as delivered, failed, unknown, or suppressed; and
7. finishes with server-derived write counts and message IDs.

Intraday never treats the morning plan as current evidence. It independently refreshes the phase and
stays completely silent when no eligible edge fires. On-demand research uses the same safety path but
is always session-only and suppressed from Telegram.

## Portfolio recordkeeping

Web and Telegram accept strict string decimals—never JSON floating-point financial values—and share
one server-side state machine. Supported operations include buy, sell/sell-all, stop update,
recurring-plan create/cancel, and transaction correction. Each mutation is:

```text
normalized input -> private preview receipt -> explicit confirmation -> immutable ledger event
                 -> locked deterministic projection -> result receipt
```

Holdings are projections of the append-only ledger, not an independent mutable source of truth.
Fees are recorded explicitly. Concurrent first buys are serialized, stale previews fail, duplicate
idempotency keys reuse the original receipt, and corrections preserve the original event.
Recurring investments are reminders only; they never schedule a brokerage purchase.

## Identity, isolation, and lifecycle

- Email OTP and current disclosure consent protect every private screen.
- PostgreSQL RLS is forced on every owner table; API views are security-invoker allow-lists.
- Edge runtimes receive separate least-privilege database roles and fixed RPC surfaces.
- Provider connections, Telegram pairing, schedules, evidence, ledger events, and exports are
  owner-scoped.
- Sensitive lifecycle actions require a fresh OTP step-up.
- Owners can export account JSON and ledger CSV, request deletion, cancel during a 72-hour window,
  and receive truthful Telegram-cleanup status.
- Deletion tombstones are restored before owner data so backups cannot resurrect a deleted account.

Read [privacy](docs/privacy.md), [risk disclosure](docs/risk-disclosure.md), and the
[account lifecycle runbook](docs/runbooks/account-lifecycle.md).

## Fresh install and migration

Install local test dependencies from the pinned project manifests, then use a disposable PostgreSQL
17 instance for migration work. Never point a local test command at production.

`sql/schema.sql` is the generated canonical fresh-install schema. It contains the reviewed legacy
baseline, migrations through 20260910, and a data-free bootstrap at the multitenancy boundary. Do not
edit it directly. Regenerate and prove catalog equivalence with:

```bash
.venv/bin/python scripts/verify_schema_parity.py --write
.venv/bin/python scripts/verify_schema_parity.py --verify
```

`sql/legacy_schema.sql` exists only to test the real upgrade path. Existing single-owner data must go
through `scripts/migrate_single_owner_to_tenant.py`; never run the fresh bootstrap over non-empty
tables. Migration filenames use unique sortable versions in dependency order.

## Deployment and operations

Pull requests run without environment secrets. CI performs secret/dependency/SQL checks, legacy and
fresh migration parity, RLS/surface attacks, all language/UI/browser tests, a production build, and a
build-output scan. Successful `main` CI may deploy isolated staging. Production remains a manually
approved workflow requiring a fresh encrypted backup, recent restore, passing staging evidence,
paused triggers, a distinct rollback commit, and post-deploy owner smoke.

Start here:

- [Gate 0 capabilities](docs/runbooks/gate-0-capabilities.md)
- [Deployment](docs/runbooks/deployment.md)
- [Backup and restore](docs/runbooks/backup-restore.md)
- [Release acceptance](docs/runbooks/release-acceptance.md)
- [Credential rotation](docs/runbooks/credential-rotation.md)
- [Incident response](docs/runbooks/incident-response.md)
- [Rollback](docs/runbooks/rollback.md)
- [Friend onboarding](docs/runbooks/friend-onboarding.md)

No test, workflow, report, or screenshot may contain reusable credentials, owner identity, holdings,
tickers, model content, or Telegram identifiers. Release reports contain fixed statuses and evidence
hashes only.

## Verification

Run the complete local gate:

```bash
npm run test:all
.venv/bin/python scripts/verify_schema_parity.py --verify
.venv/bin/python scripts/verify_multitenancy_migration.py --rollback-only
.venv/bin/python scripts/ci_policy_checks.py scan-secrets
git diff --check
```

Live acceptance additionally requires the two-owner staging browser test, real Claude handshake,
phone-mail OTP, R2 backup/restore, deployment rollback, owner soak, and first-friend review. Mock
browser tests prove UI behavior only and never count as live evidence.

## Unchanging guardrails

- No brokerage credentials, broker endpoints, or autonomous real-money execution.
- Every owner personally decides and places every trade outside Stock Agent.
- Missing, stale, conflicting, or implausible evidence cannot produce new actionable advice.
- Social-media claims and external repositories are untrusted research leads, never automatic
  signals or dependencies.
- Telegram portfolio changes require private pairing, preview, and explicit confirmation.
- Delivery-unknown notifications are never blindly retried or described as delivered.
- Policy changes are reviewed code/config changes, never model self-tuning.
- Owners are never batched into a provider prompt, credential, support screenshot, or run.

## License

MIT
