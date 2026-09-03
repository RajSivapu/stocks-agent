# Complete Core Product: Multi-User, Multi-Provider Stock Agent

**Date:** 2026-09-02

**Status:** Draft for Rajrupesh and external Claude review

**Release target:** One complete core-product release, built through internal security gates

**Implementation status:** Not approved; this document authorizes no code or production change

## 1. Executive decision

Evolve the current single-owner stock agent into an invite-only, hosted, multi-user product without
turning it into a brokerage or autonomous trading system.

The product will have four durable layers:

1. **Product layer:** a responsive React/TypeScript progressive web application (PWA) hosted as
   static assets on Cloudflare Workers.
2. **State and control layer:** Supabase Auth, Postgres, Row-Level Security (RLS), authenticated Edge
   Functions, database RPCs, scheduling metadata, and audit records.
3. **Provider-neutral analysis layer:** Claude, ChatGPT, and future model runtimes communicate through
   one versioned, narrowly scoped analysis protocol. Claude is no longer embedded as an architectural
   assumption.
4. **Deterministic safety layer:** the existing market gateway remains authoritative for quotes,
   freshness, policy, portfolio state transitions, persistence, rendering, and delivery.

The first release is complete for its intended use: accounts, tenant isolation, portfolio
recordkeeping, recommendations, run health, provider connection, Telegram pairing, notifications,
export/recovery, and invite-only onboarding. It excludes brokerage connectivity, order execution,
autonomous trading, a strategy marketplace, social features, and unvalidated research laboratories.

## 2. Product principles

These are release invariants, not optional preferences:

- The system provides research, suggestions, and recordkeeping. It never places, modifies, or cancels
  a brokerage order.
- Every trade record represents an action the user says they already completed elsewhere.
- No model can write holdings, bypass deterministic policy, render a delivery claim, or call Telegram
  directly.
- Missing, stale, malformed, or conflicting evidence cannot produce a new actionable conclusion.
- Every user sees only their own portfolio, analysis, notifications, commands, and provider
  connections.
- Portfolio context is sent to a model provider only after explicit connection consent. Each run
  sends one owner's minimum bounded packet; users are never combined in one prompt or batch.
- Adding or changing a model provider cannot weaken the safety gateway.
- A provider subscription is used only through that provider's supported product surface. Consumer
  authentication is never repurposed as an undocumented inference API.
- Free infrastructure is the launch preference, but reliability and recovery limitations must remain
  visible. The product must not describe a free-tier service as guaranteed production infrastructure.
- No secret, auth token, portfolio payload, or model prompt containing user data is written to Git,
  browser logs, analytics, or ordinary application logs.
- The product identifies itself as decision support and recordkeeping, not a broker, fiduciary, or
  promise of returns. It visibly labels delayed/incomplete data and keeps the user's final investment
  decision outside the system.

## 3. Current system and migration constraints

The repository currently has a working single-owner production system:

- Three Claude Cloud Routines run pre-market, intraday, and post-market analysis.
- A Supabase Edge Function authenticates a scoped routine secret, refreshes quotes, applies fixed risk
  policy, writes an audit trail, renders the message, and controls Telegram delivery.
- A second Edge Function accepts Telegram portfolio commands from one configured owner, previews each
  mutation, and applies it only after confirmation.
- Postgres RLS is enabled, but ordinary browser access has no policies; privileged server components
  use the service-role identity.
- The schema uses single-owner keys such as `holdings.ticker`, `radar.ticker`,
  `daily_snapshots(date, ticker)`, and one owner-wide investment-plan row per ticker.
- The existing RPCs and Edge Functions do not carry an owner identity through every query and state
  transition.

Consequently, adding a frontend alone would be unsafe. Tenant identity must be introduced into table
keys, indexes, RPCs, gateway requests, publications, outcome grading, Telegram mappings, and all
read-context queries before friend accounts are enabled.

## 4. Release-one scope

### 4.1 Included

#### Accounts and onboarding

- Invite-only Supabase Auth accounts.
- Email magic-link or one-time-password sign-in.
- A default profile, timezone, base currency, and notification preferences per user.
- Required acknowledgement of the privacy notice, model-provider data flow, record-only trade
  semantics, and decision-support risk disclosure before connecting a provider.
- Owner/admin invitation management without portfolio impersonation.
- A guided provider-connection and Telegram-pairing checklist.

#### Portfolio recordkeeping

- Holdings, cost basis, current value, allocation, unrealized gain/loss, stops, targets, and freshness.
- Append-only transaction ledger with historical execution dates and source attribution.
- Record Buy, Sell, Sell All, Stop, recurring Plan, and Cancel Plan operations.
- Preview, explicit confirmation, expiry, idempotency, stale-state detection, and atomic application.
- Correction workflow that voids and replaces an erroneous transaction; no silent row editing or
  deletion. The preview shows every resulting holding and cost-basis change.
- Arithmetic reconciliation between entered amount, quantity, and fill price. Material mismatches,
  implausible share counts, unknown symbols, and unsupported decimal precision block confirmation and
  ask the user to correct the source record; the system never silently guesses which value is right.
- Canonical portfolio buckets with an explicit `unclassified` state. A model may suggest a bucket,
  but only the user can confirm or change it through the audited command workflow.
- Recurring plans remain reminders only. They never assume a fill or initiate an order.

#### Analysis and decision support

- Pre-market, intraday, post-market, and user-initiated runs submitted from a connected provider.
  The web app does not claim it can wake a consumer subscription unless that provider exposes a
  documented, verified capability.
- Independent fresh evidence for every intraday run; morning levels are context, not instructions.
- Recommendation inbox and history with action, confidence, levels, evidence time, provider, model,
  policy decision, notification status, and outcome grades.
- Provider-neutral primary analyst connection.
- Optional explicit second-opinion run. No automatic provider voting or hidden fallback.
- Run-health timeline showing incomplete, vetoed, suppressed, delivered, failed, and unknown-delivery
  states accurately.

#### Notifications and integrations

- One shared Telegram bot with account-specific pairing.
- Web and Telegram portfolio commands producing identical previews and using the same database RPCs.
- Per-user phase and alert preferences.
- Exactly-once publication claims and truthful delivery receipts.
- Operational alerts for missed scheduled runs and disconnected providers, separate from market
  recommendations.

#### User control and recovery

- Downloadable CSV transaction ledger and JSON account export.
- Owner-initiated account deactivation and deletion, with export offered but not required.
  Deactivation immediately revokes provider connections, Telegram delivery, and sign-in. A 72-hour
  cancellation window precedes deletion, and the deletion runbook removes active owner data no later
  than seven days after the original request.
- Encrypted off-site application-data backups, documented restore steps, and a tested restore drill.
- No advertising, tracking pixels, or third-party behavioral analytics.

### 4.2 Explicitly excluded

- Brokerage login, account aggregation, broker read/write APIs, and order execution.
- Automatic or model-triggered trading.
- Options, futures, forex, crypto, prediction-market, and short-selling execution workflows.
- Public registration, paid subscriptions, billing, referrals, or an app marketplace.
- A social feed, strategy copying, leaderboards, or claims of expected returns.
- An embedded open-ended AI chat surface.
- Automatic risk-policy tuning or thresholds changed from model output.
- A production backtesting/Monte Carlo laboratory. That remains a separately isolated future project.
- Puter, Claude Artifacts, or another prototype runtime as the production host.

## 5. Proposed architecture

```text
Phone / browser
  |
  | HTTPS: static application shell
  v
Cloudflare Workers Static Assets
  |
  | Supabase publishable key + authenticated user JWT only
  v
Supabase
  |-- Auth: invite-only identity
  |-- Postgres: owner-scoped data + RLS
  |-- Quote cache: server-fetched market facts with source/as-of metadata
  |-- Authenticated Edge Functions: previews, confirmations, pairing, export, invitations
  |-- Provider Gateway: scoped external-runner protocol
  |-- Deterministic Market Gateway: evidence validation, quote refresh, policy, persistence
  |-- Cron: maintenance, expected-run monitoring, expiry, bounded reminders
  |
  +-----------------------------> Telegram Bot API
  ^
  |
  | versioned, owner-scoped analysis protocol
  |
  +-- Claude Cloud Routine
  +-- ChatGPT web scheduled task + stock-agent plugin
  +-- Future verified Grok/provider adapter
  +-- Optional future BYOK API runner
```

There is no dedicated VM, VPS, Docker host, Cloud Run service, or always-on Mac in the production
request path. Local development, deployment, and the optional existing Friday Codex audit can still
use the Mac without making the live product dependent on it.

The initial frontend should be a static React/TypeScript/Vite SPA. Cloudflare handles only static
assets in release one; business logic remains in Supabase. Static application assets may be cached,
but authentication responses and portfolio/analysis data must use `no-store` and must never enter the
PWA service-worker cache.

## 6. Trust boundaries

### 6.1 Browser

The browser receives only:

- the public application bundle;
- the Supabase URL and publishable key;
- the signed-in user's session; and
- rows authorized by that user's RLS policies.

It never receives a service-role/secret key, Telegram bot token, gateway master secret, database
password, provider API key, or another user's identifier as an authority claim.

Read-only screens may query narrowly defined owner-scoped views directly through Supabase. Those
views use invoker security or have browser grants revoked; a default security-definer view must not
silently bypass RLS. User-specific base tables are not browser-writable. Every mutation uses an
authenticated Edge Function and a purpose-specific RPC. There is no generic CRUD proxy.

### 6.2 Authenticated application functions

Each authenticated Edge Function must:

1. validate method, content type, body size, schema, origin, and user JWT;
2. derive the owner from the verified JWT, never from a request body;
3. apply rate and replay limits;
4. use a user-context client for ordinary RLS-protected operations;
5. use privileged access only for a fixed RPC after authorization;
6. return `Cache-Control: no-store` and sanitized error codes; and
7. avoid logging request bodies or financial values.

### 6.3 External model runtimes

Each provider connection gets one high-entropy random, revocable credential in the form
`connection_id.secret`. Store only the public lookup identifier and a keyed digest of the secret using
a server-held pepper; compare digests in constant time and show the plaintext once during setup. Send
it only in an authorization header, never a URL. The credential resolves to one owner and a capability
list. A caller cannot choose an owner in its request.

Allowed capabilities are versioned operations such as:

- `start_run`
- `read_bounded_context`
- `submit_analysis`
- `record_permitted_artifacts`
- `grade_due_decisions`
- `finish_run`

The connection cannot query tables, mutate holdings, manage users, read another owner, obtain
Telegram credentials, change policy, or invoke a broker.

### 6.4 Service-role use

The service role remains server-only and is treated as a bypass of RLS, not as an authorization
mechanism. Code using it must resolve owner identity first and call fixed, owner-aware RPCs. Every RPC
uses a fixed `search_path`, validates ownership internally, and has execution revoked from `anon` and
`authenticated` unless intentionally exposed through a wrapper.

## 7. Identity and tenancy model

Use one Supabase project and one shared schema. A separate database per friend is rejected because it
multiplies deployments, credentials, backup processes, migrations, and free-tier projects.

### 7.1 Identity tables

- `profiles`
  - `id UUID PRIMARY KEY REFERENCES auth.users(id)`
  - display name, timezone, base currency, status, onboarding state, timestamps
- `app_admins`
  - user ID and narrow administrative role
  - managed only through trusted SQL/admin tooling
- `telegram_links`
  - `owner_id`, Telegram chat/user IDs, paired/revoked timestamps
- `telegram_pairing_codes`
  - `owner_id`, token digest, expiry, attempts, consumed timestamp
- `notification_preferences`
  - `owner_id`, phase/channel settings, operational-alert settings
- `analysis_schedules`
  - `owner_id`, primary connection, timezone, enabled phases, expected run windows
- `agent_connections`
  - `owner_id`, provider, connection mode, capability set, token digest or Vault reference,
    lifecycle state, last-seen timestamp

Release one supports exactly one portfolio per user. Use `owner_id` directly rather than introducing
organizations, teams, households, or shared portfolios before a real requirement exists.

### 7.2 Owner-scoped data

Add immutable, non-null `owner_id` foreign keys to every user-specific table, including:

- holdings and transactions;
- portfolio commands and investment plans;
- analysis runs and gateway requests;
- decision evaluations, suggestions, publications, and grades;
- observations, snapshots, radar, lessons, and paper watches;
- notification and operational receipts.

Convert single-owner uniqueness to composite uniqueness, for example:

- holdings: `(owner_id, ticker)`
- radar: `(owner_id, ticker)`
- snapshots: `(owner_id, snap_date, ticker)`
- active investment plans: `(owner_id, ticker)`
- publication idempotency: includes `owner_id`, market date, phase, and kind

Foreign keys must preserve ownership across relationships. Where Postgres cannot express the full
invariant with one existing key, add composite unique constraints and composite foreign keys rather
than trusting application code.

### 7.3 Shared versus private records

Shared tables may contain only non-user-specific system facts, such as supported provider metadata,
canonical market sessions, global hard safety ceilings, and schema/prompt contract versions.

Provider output, watchlists, recommendations, observations, summaries, and inferred preferences are
private even when they concern a public ticker. They remain owner-scoped because their selection and
context reveal portfolio information.

### 7.4 RLS policy shape

For authenticated reads and any intentionally exposed owner-scoped operation:

```sql
owner_id = auth.uid()
```

`USING` and `WITH CHECK` policies are both required. Grants and RLS policies are tested together.
There are no permissive catch-all policies, no owner ID sourced from mutable user metadata, and no
browser-writable user data, admin, or provider-credential base tables. All exposed views use invoker
security; otherwise their grants are revoked and access is wrapped in an owner-aware function.

## 8. Existing-data migration and cutover

The production migration is additive and fail-closed:

1. Create the owner's Supabase Auth user and record the immutable UUID through a trusted workflow.
2. Pause all Claude Routines and Telegram mutation handling.
3. Capture encrypted logical backup, table counts, relationship checks, and deterministic row
   digests. Verify that the backup can be decrypted before changing schema.
4. Add nullable `owner_id` columns and new identity/connection tables.
5. Backfill every current user-specific row to the owner's UUID inside a transaction.
6. Reject the migration if any current row remains unowned, any relationship crosses owners, or any
   canonical label/precondition is unknown.
7. Add composite keys, foreign keys, indexes, and owner-aware RPC versions.
8. Deploy tenant-aware gateway and Telegram functions while old entry points remain paused.
9. Replace the owner's broad legacy routine secret with a connection-scoped token.
10. Add and test RLS policies with two synthetic users and the anonymous role.
11. Set owner columns `NOT NULL`, revoke obsolete RPCs, and remove single-owner environment variables.
12. Run non-writing provider dry runs, web mutation previews, Telegram pairing, and before/after data
    parity checks.
13. Resume one controlled pre-market-equivalent run, inspect receipts, and then resume the schedule.

Rollback restores the last reviewed Edge Function code and leaves additive owner columns in place.
If data integrity is uncertain, keep routines and mutations paused; do not attempt an automatic
destructive down-migration.

## 9. Provider-neutral analysis protocol

### 9.1 Connection modes

The architecture supports three modes behind one contract:

1. **Subscription-hosted runtime:** the provider schedules and executes the task, then calls the stock
   agent's scoped gateway. Claude Routines and eligible ChatGPT web scheduled tasks use this mode.
2. **User-funded API runtime:** a future server scheduler calls a provider API using a user-owned key
   stored in Supabase Vault. This mode is disabled in release one unless separately security-reviewed.
3. **Local/self-hosted runtime:** a future connector can submit the same contract from a local model.
   It requires the user's computer or server to be online and is not a release-one dependency.

### 9.2 Launch provider support

- **Claude:** supported through the existing Cloud Routine workflow after converting it to an
  owner-scoped connection.
- **ChatGPT:** targeted through a stock-agent plugin plus a ChatGPT web scheduled task. This is marked
  `experimental` until an end-to-end task proves that the user's eligible plan can run the plugin on
  schedule and return the full receipt without a local machine.
- **Grok/xAI:** the schema and contract are adapter-ready, but release-one production support is not
  promised. The documented xAI inference route requires an API key and paid credits; any
  subscription-backed Grok cloud-agent route requires its own capability, security, and reliability
  spike.
- **Other providers:** unsupported providers cannot be selected merely by typing a model name. Each
  adapter needs contract tests, current official documentation review, and an explicit allow-list.

### 9.3 Per-user connection lifecycle

Each user connects their own eligible provider account; the application does not lend or pool the
owner's Claude/ChatGPT subscription. Setup follows one provider-neutral state machine:

1. The signed-in user chooses an allow-listed provider and acknowledges exactly which bounded
   portfolio/research fields will leave Supabase for that provider.
2. The server creates a disabled connection and shows its endpoint, provider-specific task template,
   and one-time scoped credential. It never shows a database, service-role, or Telegram secret.
3. The user installs/configures the task in the provider's supported interface.
4. The provider performs a version/capability handshake. The server records capability claims but
   trusts only operations that pass the allow-list and conformance test.
5. A no-write test run proves authentication, owner isolation, current contract version, receipt
   handling, and provider scheduling behavior.
6. The user explicitly activates the connection as primary or second-opinion-only.
7. Disconnect/revoke invalidates the credential immediately and suppresses future expected-run alerts
   for that connection. Reconnection always creates a new secret.

No shared master provider credential is copied to friends. A provider that cannot safely store/use the
scoped credential or cannot complete the test remains unavailable while the rest of the product works.

### 9.4 Standard analysis submission

Every provider submits the same versioned envelope:

- request, connection, run, contract, prompt, provider, and model identifiers;
- phase, owner-resolved market date, and evidence timestamps;
- citations/source identifiers and explicit missing/conflicting evidence;
- separate Analyst and Checker records;
- proposed action, confidence, thesis, counter-thesis, and decisive factor;
- entry range, invalidation, stop, target, horizon, and bucket when applicable;
- a declaration that the output is suggestion-only.

The gateway rejects extra fields, unknown enum values, oversized inputs, stale timestamps,
duplicate/replayed identities with different payloads, and cross-run references.

### 9.5 Analytical-quality contract

Provider adapters may use different models, but they must produce the same evidence disciplines:

- separate business/fundamental, valuation, catalyst, technical, portfolio-fit, and downside views;
- a clearly stated bear case and the specific facts that would invalidate the thesis;
- fresh market facts for the current phase, with the morning plan treated only as historical context;
- source dates and conflicts, with social-media claims treated as unverified leads rather than facts;
- no conclusion extrapolated from one price chart, one headline, one personality, or past returns;
- an independent Checker response that can challenge or reject the Analyst; and
- calibrated uncertainty when required data is missing instead of invented confidence.

The UI preserves these dimensions rather than collapsing analysis into a single score. Deterministic
policy can veto or downgrade a recommendation, but a passing policy result is not presented as proof
that the investment thesis is correct.

### 9.6 Market-data freshness and integrity

Price-sensitive policy never trusts a model-supplied quote. The server-owned market-data adapter
records symbol, price, source, source timestamp, retrieval timestamp, session, and adjustment status.
Phase-specific freshness limits are versioned policy. If the primary quote is stale, malformed, or
conflicts materially with an independent allowed source, new actionable advice fails closed and the
run records the source disagreement.

Corporate actions are first-class inputs. Split, reverse-split, symbol-change, merger, and delisting
events must be normalized before stop/target comparisons so an unadjusted series cannot create a
false alert. Primary filings and issuer/exchange notices take precedence for company facts; secondary
news and social posts can supply leads but not establish a decisive fact alone.

Free data sources provide no reliability guarantee. The app displays source and `as_of`, tracks source
health, and distinguishes `fresh`, `delayed`, `stale`, `conflicting`, and `unavailable`. A provider run
cannot convert an unavailable server quote into a fresh one by citing its own browsing result.

### 9.7 Provider independence

The provider proposes; the server decides what is eligible to persist or publish. The server:

- independently refreshes price/session data;
- checks evidence freshness and market calendar;
- calculates concentration, bucket capacity, sizing, stop distance, reward/risk, and loss limits;
- may downgrade or veto but never upgrades the model's action;
- owns holding alert transitions and notification suppression;
- renders fixed message templates;
- records actual writes and Telegram message IDs; and
- returns receipts that the model must quote rather than reconstruct.

Changing provider cannot change these rules.

### 9.8 Primary provider and second opinion

Each user chooses one primary scheduled provider. A second provider may be invoked explicitly for a
second opinion, stored separately, and compared in the UI. The system does not average, vote, or
automatically choose between model recommendations.

There is no silent provider failover. A missed or failed run is shown as failed and may generate an
operational alert. Automatic failover could change cost, privacy terms, model behavior, and advice
without informed consent.

## 10. Scheduling and run lifecycle

Subscription-hosted providers own their model schedule. The application stores expected schedules
and observes receipts; it does not pretend it can trigger a consumer subscription through an API.

The canonical exchange calendar and session calculations use `America/New_York`; the user's IANA
timezone is for display and scheduler setup. Default anchors match the current cadence:

- pre-market: 120 minutes before the regular open (normally 07:30 Eastern / 06:30 Central);
- intraday: 210 minutes after the regular open (normally 13:00 Eastern / 12:00 Central); and
- post-market: 10 minutes after the regular close (normally 16:10 Eastern / 15:10 Central).

Anchors are resolved against the actual market session so holidays, early closes, and daylight-saving
changes do not rely on provider prose or a fixed UTC offset. A subscription provider's schedule is
configured in its supported timezone and verified against expected receipt windows. A fresh intraday
run gathers and evaluates current evidence; it never republishes the morning recommendation as the
midday decision.

Each run follows:

```text
provider wakes
  -> start_run
  -> server resolves owner, phase, market calendar, and idempotency
  -> read_bounded_context
  -> provider gathers fresh evidence and creates Analyst + Checker output
  -> submit_analysis
  -> server refreshes market facts and applies deterministic policy
  -> atomic persistence + publication claim
  -> Telegram delivery or explicit suppression/failure/unknown receipt
  -> permitted artifact/outcome work
  -> finish_run
```

Supabase Cron performs deterministic, model-free maintenance:

- expire pending commands and pairing codes;
- detect missed expected run windows;
- clean old operational rows under the retention policy;
- issue due recurring-plan reminders without assuming a purchase;
- trigger bounded backup/health metadata jobs where appropriate.

Cron never generates investment advice.

Market holidays are checked before external research. Duplicate schedules, delayed provider starts,
and daylight-saving transitions must not produce duplicate publications. The market date and session
come from the server's configured timezone/calendar, not provider prose.

## 11. Portfolio mutation model

Web and Telegram use the same command state machine:

```text
submitted -> previewed -> confirmed -> applied
                    |         |          |
                    |         |          +-- immutable receipt
                    |         +-- stale/expired/rejected/error
                    +-- cancelled/expired
```

Each command stores owner, operation, normalized input, expected holding version, preview digest,
expiry, idempotency key, status, and result. Confirmation must match the owner, channel identity,
command ID, and preview digest.

The atomic RPC locks the owner/ticker pair, rechecks the expected version, updates the holding,
appends the transaction, advances a matching recurring plan at most once, and records the command
receipt in one transaction. A retry with the same idempotency key returns the original receipt.

Corrections do not rewrite history. The correction RPC previews and then records a compensating void
event linked to the original transaction plus a replacement transaction. It recomputes the affected
holding lifecycle deterministically and refuses corrections that would create a negative historical
balance or ambiguous ledger.

## 12. Telegram multi-user design

Use one bot for the invite-only product.

### Pairing

1. A signed-in user requests a pairing code in the web app.
2. Store only its digest, owner, attempt count, and short expiry.
3. The user sends `/start <code>` to the bot.
4. The webhook validates Telegram's webhook secret, rate limits the source, hashes the code, and
   atomically binds that Telegram user/chat to the owner.
5. The code is consumed once. Existing conflicting links require explicit unlink/relink confirmation.

The code is 10 random base32 characters, expires after 10 minutes, and permits at most five failed
attempts before invalidation. Store a keyed digest rather than a plain hash. Rate limits apply per
source and globally; error messages never reveal whether a code or account exists.

### Commands

The existing Buy, Sell, Stop, Portfolio, Plan, Plans, and Cancel Plan commands remain. Add Help,
Status, and Unlink. Every mutation previews first and requires an inline Confirm button. Callback
confirmation must come from the same paired Telegram user and chat before expiry.

`telegram_update_id` remains an idempotency boundary. Commands and messages carry `owner_id`; all
holding and plan reads filter by that resolved owner. The old static owner-ID environment variables
are removed after migration.

### Notifications

The deterministic publication layer resolves the owner's active Telegram link and preferences.
Changing or revoking a link affects only future sends. No portfolio values or advice appear in
operational logs or admin alerts.

## 13. Web application design

### 13.1 Primary screens

1. **Today:** latest portfolio value, data freshness, current actionable/suppressed status, upcoming
   plan reminders, and run health.
2. **Portfolio:** holdings, allocation, cost basis, gains/losses, stops/targets, and record-action
   controls.
3. **Activity:** append-only trades, corrections, plans, commands, and downloadable ledger.
4. **Research:** recommendation history, evidence time, Analyst/Checker views, server policy result,
   notification receipt, and deterministic outcomes.
5. **Runs:** phase timeline, provider/model, source health, vetoes, failures, and receipt counts.
6. **Connections:** provider status, setup instructions, token rotation/revocation, Telegram pairing,
   and last successful handshake.
7. **Settings:** timezone, base currency display, stricter personal risk preferences, schedules, and
   notifications.
8. **Admin:** invitations and service health only; no cross-user portfolio browser.

### 13.2 UX safeguards

- Every value displays its source time and distinguishes market price, owner-recorded fill, and model
  proposal.
- Dashboard prices come from a server-owned quote cache, not from model prose or direct browser calls.
  Each quote carries provider, market-session, and `as_of` metadata; owner-scoped views expose only
  the quotes needed for that user's holdings/watchlist.
- Buy/Sell language says "record" rather than "execute" or "place order."
- Confirmation screens show before/after shares, cost basis, realized P&L estimate, bucket impact,
  execution date, and whether a recurring reminder advances.
- Suppressed, vetoed, failed, and unknown delivery states are visually distinct.
- No green/red color is the only status signal; all statuses have text and icons.
- The application remains useful when no model provider is connected: portfolio recordkeeping,
  plans, history, export, and Telegram continue to work.
- The app shell may work offline, but private data and mutations do not. Offline submissions are not
  queued for later automatic execution.

### 13.3 Browser security

- Strict Content Security Policy with no inline scripts or unapproved third-party origins.
- HSTS, `frame-ancestors 'none'`, no MIME sniffing, strict referrer policy, and restrictive browser
  permissions.
- Exact production CORS allow-list on Edge Functions.
- Supabase PKCE sign-in with callback URLs allow-listed, auth parameters removed from the visible URL
  after exchange, and no session token in logs. Browser mutations attach a bearer JWT explicitly;
  cookie-authenticated mutation endpoints are not introduced.
- Dependency lockfile, automated dependency audit, and no unreviewed remote scripts.
- Sanitize all model and database prose before rendering; render as text by default, never raw HTML.
- Do not put portfolio data in URLs, local analytics, error trackers, or service-worker caches.
- Reauthentication is required for account deletion, provider-token rotation, and Telegram relinking.

## 14. Personal policy and platform safety

Split policy into two layers:

1. **Platform hard limits:** immutable/versioned ceilings controlled by reviewed deployment. Users and
   models cannot weaken them.
2. **Personal preferences:** owner-scoped allocations and limits that may be stricter than the
   platform ceiling.

The effective rule is always the safer value. Personal changes use preview/confirm, create an audit
record, and apply only to future evaluations. Historical decisions retain the policy version used at
the time.

Self-tuning remains disabled. Outcome grades and model commentary may suggest a policy review, but no
automated process activates a new policy.

## 15. Error handling and truthful state

Use stable error codes and user-safe messages. Preserve technical details only in bounded server logs
without payloads.

- Authentication failure: no existence or owner information disclosed.
- Authorization failure: generic unavailable response; audit the attempted boundary crossing.
- Stale command: reject and require a new preview.
- Duplicate request: return the original receipt when payload identity matches; reject mismatches.
- Provider timeout/missed run: mark failed or missed; never reuse the previous run as current.
- Market-data failure: fail closed for new actionable advice.
- Telegram definite failure: mark `delivery_failed`; do not claim delivery.
- Telegram uncertain acceptance: mark `delivery_unknown`; do not automatically retry.
- Database failure before atomic commit: no publication claim.
- Partial provider run: finish as partial/failed with actual persisted counts.
- Frontend network loss: show unknown client state and refresh the server receipt before allowing
  another confirmation.

## 16. Data retention, export, backup, and recovery

### Retention proposal

- Holdings, transactions, policy decisions, evaluations, suggestions, and outcome grades: retained
  while the account is active.
- Analysis-run summaries and publication receipts: retained for auditability.
- Full bounded model evidence: retain 12 months, then compact to citations, hashes, and decision
  records unless a legal/product requirement changes this.
- Expired pairing codes: delete after 24 hours.
- Expired/cancelled portfolio commands: retain 90 days, then preserve only non-sensitive audit facts.
- Telegram update deduplication records: retain enough IDs/timestamps for reliable replay protection,
  without storing unnecessary message text.
- Account deletion removes active data under the deletion runbook. Existing encrypted backups expire
  under the 14-day rotation; a minimal non-financial deletion tombstone ensures an emergency restore
  does not resurrect a deleted account before those archives age out.

### Free-tier backup plan

Supabase Free does not include downloadable automatic backups. Release one therefore requires an
encrypted logical backup outside Supabase before friend onboarding.

Proposed no-model workflow:

1. Versioned migrations reconstruct the schema. A scheduled GitHub Action exports application data
   with a least-privilege backup credential that can read only the schemas/tables being backed up and
   cannot mutate production or read Vault plaintext.
2. The runner encrypts the archive to an offline-held public key before upload.
3. Only ciphertext is retained as a private GitHub Actions artifact. Run daily and retain the latest
   14 daily archives for 14 days; fail the launch gate if current account limits cannot support this
   at zero additional cost.
4. Provider secrets and Vault plaintext are excluded; their rotation/recreation is documented.
5. Backup success metadata, not data, is reported to the admin health view.
6. The encrypted backup includes a minimal identity-recovery map needed to rebind old owner UUIDs to
   newly invited Auth users without exporting passwords or provider secrets.
7. A restore into the second Supabase project applies migrations, recreates invited identities,
   explicitly remaps old-to-new owner UUIDs, restores data, and verifies relationship/row digests.
   It is exercised before launch and quarterly thereafter; staging returns to synthetic data after
   the drill.

Friend onboarding remains disabled until a restore has succeeded. If current quotas cannot retain 14
encrypted daily backups at zero additional cost, this specification must be amended and re-approved
with a different explicit retention target or disclosed paid storage; implementation must not silently
omit backups.

Target launch objectives:

- Recovery point objective: 24 hours.
- Recovery time objective: one business day for an invite-only pilot.

## 17. Deployment and environments

Use the two Supabase Free projects deliberately:

- **Production:** real owner and invited-user data.
- **Test/staging:** synthetic users and data only; used for migrations, RLS attacks, Edge Function
  integration tests, provider contract tests, and restore drills.

Cloudflare deploys the static frontend from Git after tests pass. Preview deployments use staging or
mock data and never receive production secrets. The frontend build contains only public Supabase
configuration.

GitHub Actions performs deterministic CI and deployment only; no model API is used in CI. Production
database migrations and Edge Function deployments require a protected manual gate until the process
has enough evidence to automate safely.

Suggested repository additions after design approval:

```text
apps/web/                         React/TypeScript/Vite PWA
supabase/functions/app-api/      authenticated product mutations and export
supabase/functions/agent-gateway/provider-neutral model protocol
supabase/functions/telegram-portfolio/
sql/migrations/                  additive tenancy and product migrations
packages/contracts/              shared schemas and generated types
tests/security/                  RLS and cross-tenant attack tests
tests/contracts/                 provider conformance fixtures
docs/runbooks/                   onboarding, incident, backup, restore, rollback
```

Exact file boundaries may be refined in the implementation plan, but security domains must not be
collapsed into one large Edge Function.

## 18. Testing strategy

### Database and RLS

- Anonymous, authenticated owner A, authenticated owner B, revoked user, and service-role cases.
- Cross-user reads, inserts, updates, deletes, RPC calls, foreign-key substitutions, and guessed IDs.
- Composite ownership constraints and attempts to create cross-owner relationships.
- Security-definer search paths, grants, function ownership, and RLS on every exposed table/view.
- Security-definer-view bypass attempts; every browser-readable view must prove invoker-context RLS.
- Migration tests from a realistic copy of the current single-owner schema.

### Portfolio invariants

- Concurrent Buy/Sell/Stop/Plan commands on the same owner/ticker.
- Duplicate confirmation, callback replay, expiry, stale holding version, sell-all, fractional shares,
  historical dates, correction chains, negative-balance attempts, and exact decimal behavior.
- Amount/quantity/price mismatch, implausible share count, unsupported precision, symbol ambiguity,
  and explicit unclassified-bucket cases.
- Web and Telegram produce equivalent previews and receipts.

### Provider contracts

- One conformance suite runs against Claude, ChatGPT, and future adapters.
- Unknown fields, bad enum values, oversized payloads, stale evidence, replayed request IDs, switched
  owners, missing Analyst/Checker records, and fabricated receipts are rejected.
- Provider outputs cannot affect deterministic quote, policy, persistence, or delivery fields.
- Prompt-injection strings in news, database prose, and user notes remain inert data.
- Each provider receives only one owner's bounded packet after consent; cross-owner batch prompts and
  undeclared context fields fail contract tests.

### Publication and scheduling

- Holiday gate, daylight-saving boundaries, delayed runs, duplicate schedules, missed windows,
  silent intraday behavior, Telegram failure/unknown states, and exactly-once claims.
- No prior recommendation is shown as current after a failed fresh run.

### Frontend

- Authentication lifecycle, invite acceptance, route protection, session expiry, reauthentication,
  loading/error/empty states, mobile layout, keyboard use, screen readers, and no private-data caching.
- Model prose XSS payloads and content-security-policy regression tests.
- Two-browser end-to-end cross-tenant tests using synthetic users.

### Recovery and operations

- Backup encryption/decryption, staged restore, row-count/digest parity, user-identity rebinding,
  credential rotation, provider revocation, Telegram relinking, and rollback while routines are
  paused.

## 19. Threat model summary

| Threat | Primary controls |
|---|---|
| User A requests User B's row ID | RLS, owner-derived identity, composite ownership FKs, attack tests |
| Browser bundle or XSS steals privileged credentials | No privileged client secrets, CSP, text rendering, dependency controls |
| View bypasses RLS despite safe-looking SQL | Invoker-security views, revoked grants, owner A/B attack tests |
| External model asks for another owner | Owner resolved from token; no owner selector; bounded context operation |
| Model provider receives excess or mixed-user context | Explicit connection consent, minimum packet, one owner per run, field allow-list |
| Provider token leaks | Digest-only storage, least capability, rate limits, rotation/revocation, no DB access |
| Service role bypass causes IDOR | Resolve owner before privileged call; fixed owner-aware RPCs; code/tests prohibit generic queries |
| Telegram account hijack or guessed code | Webhook secret, short single-use digest, attempt limit, same-user callback checks, relink reauth |
| Duplicate webhook/request | Update ID and idempotency claims; atomic original-receipt return |
| Model prompt injection | Treat all external/stored prose as data; fixed contract; no direct tools for side effects |
| Model fabricates price or send claim | Server-refetched facts, fixed renderer, server receipts only |
| Split or stale/free quote creates a false alert | Corporate-action normalization, source/as-of state, conflict gate, fail closed |
| Free provider misses a run | Expected-run monitor, explicit missed state, no stale-plan reuse |
| Backup exposes portfolios or API keys | Encrypt before upload, offline private key, exclude Vault plaintext, restore test |
| Admin UI becomes surveillance surface | Invitations and health only; no impersonation/cross-user portfolio browser |
| Product drifts toward execution | No broker dependency or endpoint; security tests and copy invariants enforce record-only language |

## 20. Observability without leaking financial data

Operational records include request ID, owner pseudonymous ID/hash where needed, component, operation,
status code, duration, provider/model identifier, contract version, and bounded error code.

Logs exclude holdings, quantities, cost basis, recommendations, chat text, tokens, full URLs containing
codes, and request/response bodies. User-facing run receipts in Postgres retain the necessary private
details under RLS.

The admin health view shows aggregate component health, missed-run counts, backup age, deployed
versions, and provider adapter status without exposing which ticker or position belongs to a user.

## 21. Cost and scaling boundaries

Expected pilot infrastructure:

- Cloudflare Workers Static Assets: free static delivery within current platform terms.
- Supabase Free: database, Auth, Edge Functions, and Cron within current quotas.
- Telegram Bot API: no separate application hosting cost.
- Claude/ChatGPT subscription-hosted runs: charged against each participating user's eligible plan
  allowance, subject to provider availability and limits.
- No model API charge in the default launch path.

Potential future costs must be visible before activation:

- Supabase Pro for guaranteed non-pausing behavior, downloadable automatic backups, greater limits,
  logs, and support.
- Custom domain registration.
- Custom SMTP for reliable invitation delivery at wider scale.
- OpenAI, Anthropic, xAI, or another API when a user explicitly enables a BYOK connector.
- Object storage if encrypted backup retention exceeds included quotas.

The product must show provider connection health and last-run time; it must not promise unlimited or
guaranteed analysis based on a consumer subscription.

## 22. Internal delivery gates for the single complete release

The release is built incrementally but launched only after all gates pass.

### Gate A: tenancy foundation

- New identity tables, owner columns, composite constraints, RLS, and migration verifier.
- Existing owner data backfills with exact parity.
- Two-user cross-tenant attack suite passes.

### Gate B: owner-aware control plane

- Portfolio RPCs, gateway, grading, publications, policy, and Telegram are owner-aware.
- Legacy single-owner secrets and assumptions are removed.
- Existing owner workflows pass regression and dry-run tests.

### Gate C: provider-neutral bridge

- Versioned contract, scoped connection lifecycle, Claude adapter, provider conformance suite.
- ChatGPT connector spike either passes and ships as experimental/supported, or is visibly unavailable;
  failure does not block Claude-backed core-product operation.
- Grok remains adapter-ready unless its separate spike passes.

### Gate D: complete web product

- All release-one screens, recordkeeping workflows, provider/Telegram setup, export, and settings.
- Mobile/accessibility/security verification passes.

### Gate E: operations and recovery

- Staging environment, deployment runbooks, monitoring, encrypted backup, successful restore drill,
  incident response, and rollback drill.

### Gate F: controlled production cutover

- Pause routines and Telegram mutations.
- Back up, migrate, verify, deploy, pair owner, connect Claude, and run no-write tests.
- Exercise one owner web mutation and one Telegram mutation with explicit confirmation.
- Observe one complete scheduled market cycle.
- Keep friend invitations disabled during an owner-only soak period.

### Gate G: invite-only launch

- Invite one synthetic/test account and then one trusted friend.
- Re-run isolation and deletion/export checks.
- Enable additional invitations only after reviewing errors, provider usage, notification noise, and
  support burden.

## 23. Release acceptance criteria

The complete core product is ready only when all are true:

- Existing owner records, plans, suggestions, and outcome links survive migration with verified
  parity.
- Anonymous access returns no private rows.
- Two authenticated test users cannot infer, read, mutate, link to, notify, or analyze each other's
  records through any table, view, RPC, Edge Function, Telegram callback, or provider operation.
- No client artifact contains a secret/service-role key.
- Every mutation requires a matching unexpired preview and returns an idempotent atomic receipt.
- Material amount/quantity/price mismatches and ambiguous symbols cannot reach confirmation.
- Every provider passes the same contract tests; unsupported providers cannot be selected.
- A model cannot override server prices, freshness, policy, notification suppression, or send status.
- Failed/missed runs do not reuse stale advice as current.
- Telegram pairing, unlinking, replay defense, and same-user confirmation pass.
- The web application works on a phone and does not cache private API data offline.
- Encrypted backup and staging restore succeed within the stated recovery objectives.
- Deployment and rollback instructions have been exercised.
- There is still no brokerage credential, dependency, endpoint, or executable trade action anywhere
  in the system.

## 24. Decisions to validate during external review

The proposed default decisions are:

1. One shared Supabase project with `owner_id` and RLS, not one database per user.
2. Exactly one portfolio per user in release one.
3. Invite-only onboarding.
4. Claude and ChatGPT as targeted subscription-hosted connectors; Grok adapter-ready but not promised.
5. One primary scheduled provider and explicit second opinions; no hidden failover or model voting.
6. Static Cloudflare frontend with Supabase as the only business-logic backend.
7. Web and Telegram share the same deterministic command/RPC layer.
8. Platform hard safety ceilings plus user preferences that may only become stricter.
9. One complete launch after internal gates and an owner-only soak period.
10. Friend onboarding blocked until cross-tenant attacks and a real restore drill pass.

These are concrete recommendations, not unresolved placeholders. Reviewer objections should include a
specific threat, failure mode, operational constraint, or simpler alternative.

## 25. External-review checklist for Claude

Ask the reviewer to challenge the design rather than summarize it:

1. Identify every path where service-role access could cause an owner-ID authorization bypass.
2. Check whether all current tables, RPCs, unique keys, foreign keys, gateway operations, grades, and
   publications are covered by the tenancy migration.
3. Find cross-user leaks through timing, errors, IDs, Telegram callbacks, provider context, exports,
   logs, caches, or admin screens.
4. Challenge the provider-authentication assumptions, especially subscription-backed ChatGPT tasks
   and any future Grok route.
5. Check replay, duplicate delivery, stale-command, concurrent mutation, and uncertain-delivery
   handling.
6. Challenge the backup plan, Vault handling, identity restoration, recovery objectives, and free-tier
   assumptions.
7. Look for ways model-controlled prose could become code, HTML, SQL, a tool instruction, or a policy
   input.
8. Check whether the release is too broad to verify safely and recommend scope cuts only when tied to
   a concrete risk or dependency.
9. Identify claims that need a capability spike or current provider documentation before they become
   acceptance criteria.
10. Propose missing tests and release gates; do not propose brokerage execution or autonomous trading.

Suggested review prompt:

> Perform an adversarial architecture, security, privacy, reliability, and operability review of this
> design for an invite-only multi-user stock decision-support application. Treat financial data as
> sensitive and model output as untrusted. Verify that tenant isolation survives service-role use,
> Telegram callbacks, provider connectors, exports, and backups. Separate blocking findings from
> optional improvements. Flag any unsupported assumptions about Claude, ChatGPT, Grok, Supabase,
> Cloudflare, or free-tier behavior. Do not expand the product into brokerage execution.

## 26. Research references

- [Cloudflare Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/)
- [Cloudflare static-assets billing and limitations](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/)
- [Supabase Auth](https://supabase.com/docs/guides/auth)
- [Supabase user invitations](https://supabase.com/docs/guides/auth/users)
- [Supabase Row-Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase data security](https://supabase.com/docs/guides/database/secure-data)
- [Supabase scheduling Edge Functions](https://supabase.com/docs/guides/functions/schedule-functions)
- [Supabase Vault](https://supabase.com/docs/guides/database/vault)
- [Supabase database backups](https://supabase.com/docs/guides/platform/backups)
- [Telegram Bot API webhooks](https://core.telegram.org/bots/api#setwebhook)
- [OpenAI Docs: scheduled tasks](https://learn.chatgpt.com/docs/automations)
- [OpenAI Docs: authentication](https://learn.chatgpt.com/docs/auth)
- [OpenAI plugin architecture](https://developers.openai.com/plugins)
- [Anthropic: subscription and API billing are separate](https://support.anthropic.com/en/articles/9876003-i-subscribe-to-a-paid-claude-ai-plan-why-do-i-have-to-pay-separately-for-api-usage-on-console)
- [xAI inference API](https://docs.x.ai/developers/rest-api-reference/inference)
- [xAI API billing](https://docs.x.ai/console/billing)

## 27. Next step after review

No implementation starts from this draft. Rajrupesh reviews it with Claude and returns the complete
findings. We reconcile accepted changes into this specification, run the placeholder/consistency/
scope/ambiguity self-review again, and request final design approval. Only then do we create a
task-level implementation plan and begin test-driven implementation through the internal release
gates.
