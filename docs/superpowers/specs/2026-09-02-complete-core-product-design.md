# Complete Core Product: Multi-User, Provider-Neutral Stock Agent

**Date:** 2026-09-02

**Status:** Revision 2 approved by Rajrupesh on 2026-09-02

**Release target:** One complete core-product release with one proven launch provider and an additive
provider protocol, built through internal security gates

**Implementation status:** Approved for a test-first implementation plan and gated execution

## 1. Executive decision

Evolve the current single-owner stock agent into an invite-only, hosted, multi-user product without
turning it into a brokerage or autonomous trading system.

The product will have four durable layers:

1. **Product layer:** a responsive, installable React/TypeScript web application with a manifest,
   hosted as static assets on Cloudflare Workers without a service worker.
2. **State and control layer:** Supabase Auth, Postgres, Row-Level Security (RLS), authenticated Edge
   Functions, database RPCs, scheduling metadata, and audit records.
3. **Provider-neutral analysis layer:** model runtimes communicate through one versioned, narrowly
   scoped protocol. Release one ships the verified Claude Routines adapter. A future ChatGPT adapter
   uses a public MCP server and OAuth 2.1; it is additive but not promised until a separate capability
   and security gate passes. Grok remains outside the product until a supported subscription route
   exists.
4. **Deterministic safety layer:** the existing market gateway remains authoritative for quotes,
   freshness, policy, portfolio state transitions, persistence, rendering, and delivery.

The first release is complete for its intended use: accounts, tenant isolation, portfolio
recordkeeping, recommendations, run health, Claude connection, Telegram pairing, notifications,
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

- Invite-only Supabase Auth accounts created through a trusted operator CLI; there is no admin mutation
  screen in release one.
- Six-digit email OTP sign-in as the primary flow. Magic link is a desktop fallback only.
- Custom SMTP must deliver through a verified operator-controlled domain before the first friend is
  invited; the Supabase default mailer is development-only.
- A default profile, IANA timezone, USD base currency, and notification preferences per user. Release
  one supports US-listed equities and ETFs only and does not perform FX conversion.
- Required acknowledgement of the privacy notice, model-provider data flow, record-only trade
  semantics, and decision-support risk disclosure before connecting a provider.
- A guided provider-connection and Telegram-pairing checklist.
- Consent states that provider run transcripts may contain the bounded portfolio packet; the operator
  can access production data and encrypted backups; quote vendors receive requested ticker symbols;
  and users must delete historical Telegram messages themselves when the bot can no longer do so.

#### Portfolio recordkeeping

- Holdings, cost basis, current value, allocation, unrealized gain/loss, stops, targets, and freshness.
- Append-only transaction ledger with historical execution dates and source attribution.
- Record Buy, Sell, Sell All, Stop, recurring Plan, and Cancel Plan operations.
- Preview, explicit confirmation, expiry, idempotency, stale-state detection, and atomic application.
- Correction workflow that voids and replaces an erroneous transaction; no silent row editing or
  deletion. The preview shows every resulting holding and cost-basis change.
- Every Buy/Sell records quantity, fill price, optional non-negative fees, and an optional
  broker-reported cash total. The server computes gross trade value (`quantity * price`) and expected
  net cash (`gross + fees` for a Buy, `gross - fees` for a Sell). A supplied cash total outside
  `max($0.05, 0.1% of the expected total)` blocks confirmation and echoes all inputs. A recurring-plan
  deposit is a separate field and is never treated as the trade's cash total.
- Shares use `NUMERIC(20,8)`, prices `NUMERIC(20,4)`, and money `NUMERIC(20,2)`. More than eight share
  decimals, more than 1,000,000 shares, a price outside `[0.0001, 1,000,000]`, unknown symbols, or
  implausible values block confirmation; the system never silently guesses which value is right.
- Holdings are a deterministic average-cost projection of the append-only transaction ledger. Apply
  and correction RPCs rebuild the affected owner/ticker projection inside the same transaction, and a
  nightly invariant job compares every projection with a fold over its ledger.
- A back-dated transaction is rejected if it would make the historical share balance negative at any
  point.
- Canonical portfolio buckets with an explicit `unclassified` state. A model may suggest a bucket,
  but only the user can confirm or change it through the audited command workflow.
- Recurring plans remain reminders only. They never assume a fill or initiate an order.

#### Analysis and decision support

- Pre-market, intraday, post-market, and user-initiated runs submitted from a connected provider. The
  server derives phase from its exchange calendar and the trigger time; provider prose cannot assert
  a phase.
- Independent fresh evidence for every intraday run; morning levels are context, not instructions.
- Recommendation inbox and history with action, confidence, levels, evidence time, provider, model,
  policy decision, notification status, and outcome grades.
- Provider-neutral primary analyst connection.
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
- Daily encrypted application-data backups to private Cloudflare R2, 14 daily plus 4 weekly copies,
  an independent backup-age alert, documented restore/rotation steps, and a tested restore drill.
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
- ChatGPT/Grok adapters, second-opinion runs, user-editable model risk policy, FX/base-currency
  conversion, an admin mutation UI, and a PWA service worker. These are separate post-launch projects.

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
  |-- Postgres: private app schema + exposed api views + forced RLS
  |-- Quote cache: server-fetched market facts with source/as-of metadata
  |-- Authenticated app-api: user-JWT previews, confirmations, export; no privileged key
  |-- Run Scheduler: claims market slots and calls each user's Routine /fire endpoint
  |-- Provider Gateway: validates the separate inbound Routine credential
  |-- Deterministic Market Gateway: evidence validation, quote refresh, policy, persistence
  |-- Cron: market-aware trigger claims, maintenance, expected-run monitoring, expiry
  |
  +-----------------------------> Telegram Bot API

Supabase Run Scheduler -- scoped /fire bearer --> User-owned Claude Routine
Supabase Agent Gateway  <-- scoped callback ---- User-owned Claude Routine

Private GitHub backup workflow -- backup_reader --> Supabase session pooler
Private GitHub backup workflow -- age ciphertext --> Private Cloudflare R2
Cloudflare scheduled monitor --------------------> R2 age check + Telegram ops alert

Future public OAuth 2.1 MCP host <---------------> ChatGPT plugin (separate gated project)
```

There is no dedicated VM, VPS, Docker host, Cloud Run service, or always-on Mac in the production
request path. Local development, deployment, and the optional existing Friday Codex audit can still
use the Mac without making the live product dependent on it.

The initial frontend is a static React/TypeScript/Vite SPA with a web-app manifest and reviewed
`_headers` file. Its Cloudflare project handles only static assets; business logic remains in Supabase.
A separate minimal scheduled Worker reads only R2 object metadata and can send an operational Telegram
alert; it never receives user portfolio data or browser traffic. There is no service worker or offline
shell. Static application assets may be cached, but authentication responses and portfolio/analysis
data use `no-store`.

## 6. Trust boundaries

### 6.1 Browser

The browser receives only:

- the public application bundle;
- the Supabase URL and publishable key;
- the signed-in user's session; and
- rows authorized by that user's RLS policies.

It never receives a service-role/secret key, Telegram bot token, gateway master secret, database
password, provider API key, or another user's identifier as an authority claim.

Read-only screens query narrowly defined `security_invoker` views in the exposed `api` schema.
User-specific base tables live in the non-exposed `app` schema with forced RLS. Every browser mutation
calls `app-api`, which uses the user's JWT and `SECURITY INVOKER` RPCs; `app-api` contains no service
role or database password. There is no generic CRUD proxy.

### 6.2 Authenticated application functions

Each authenticated Edge Function must:

1. validate method, content type, body size, schema, origin, and user JWT;
2. derive the owner from the verified JWT, never from a request body;
3. apply rate and replay limits;
4. use a user-context client for every owner operation;
5. derive ownership from `auth.uid()` inside SQL, never from an owner argument;
6. return `Cache-Control: no-store` and sanitized error codes; and
7. avoid logging request bodies or financial values.

### 6.3 External model runtimes

The Claude adapter has two separately scoped credentials:

1. **Outbound Routine trigger:** the user pastes the per-Routine `/fire` URL and bearer token once.
   Validate that the URL is an exact allowed Anthropic Routine endpoint, then store the token encrypted
   in Supabase Vault. Only the scheduler function can read it, and it may use it only to trigger that
   one Routine. The response session URL is recorded for run diagnostics.
2. **Inbound gateway token:** generate `connection_id.secret` with a 256-bit random secret. Store the
   public lookup identifier and `SHA-256(secret)`; high entropy makes an additional pepper unnecessary
   and avoids a recovery-only secret dependency. Compare digests in constant time, show the plaintext
   once, and bind it in Claude as an API credential for the exact Supabase Functions host. It is sent
   only in an authorization header and resolves to one owner and capability set.

The trigger token and gateway token cannot substitute for each other. Both are independently
rotatable/revocable. A caller cannot choose an owner in its request.

Allowed capabilities are versioned operations such as:

- `start_run`
- `read_bounded_context`
- `submit_analysis`
- `record_permitted_artifacts`
- `grade_due_decisions`
- `finish_run`

The connection cannot query tables, mutate holdings, manage users, read another owner, obtain
Telegram credentials, change policy, or invoke a broker.

Per-connection limits are stored in Postgres: at most 12 gateway operations per run, 6 runs per owner
per market day, and 1 user-initiated run per owner per hour. An internal monthly invocation budget
counts this product's own calls and disables non-operator triggers before the configured free-tier
budget is exhausted; it raises an operational alert and never relies on per-isolate memory.

### 6.4 Service-role use

The runtime product does not use Supabase's service-role key. Machine Edge Functions connect through
the session-mode pooler with separate least-privilege database logins whose only grants are `EXECUTE`
on named root RPCs for their boundary (scheduler/gateway or Telegram). They have no direct table access
and no generic PostgREST credential.

Each root RPC receives the credential/update identity rather than `p_owner_id`, resolves exactly one
owner internally, and performs the entire operation in one database transaction. The caller cannot
set or override owner context. Functions use fixed `search_path`, explicit schema qualification,
non-superuser owners, and minimum table grants. Static tests reject generic table queries or unexpected
RPC names in machine functions. Supabase service-role access remains limited to offline operator
administration/migration and is never an application authorization mechanism.

## 7. Identity and tenancy model

Use one Supabase project with a private `app` schema for base tables and an exposed `api` schema for
owner-scoped views/wrappers. A separate database per friend is rejected because it multiplies
deployments, credentials, backup processes, migrations, and free-tier projects.

### 7.1 Identity tables

- `profiles`
  - `id UUID PRIMARY KEY REFERENCES auth.users(id)`
  - display name, timezone, status, onboarding/consent state, timestamps
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
  - `owner_id`, primary connection, enabled phases, expected server-owned run windows
- `agent_connections`
  - `owner_id`, provider, credential type, capability set, inbound token digest, outbound trigger Vault
    reference, lifecycle state, last-seen timestamp

Release one supports exactly one portfolio per user. Use `owner_id` directly rather than introducing
organizations, teams, households, or shared portfolios before a real requirement exists.

### 7.2 Owner-scoped data

Add immutable, non-null `owner_id` foreign keys to every user-specific table, including:

- holdings and transactions;
- portfolio commands and investment plans;
- analysis runs and gateway requests;
- scheduled run slots, trigger attempts, and connection quotas;
- decision evaluations, suggestions, publications, and grades;
- observations, snapshots, radar, lessons, and paper watches;
- notification receipts, auth/webhook events, deletion/reset receipts, and schema-only stricter policy
  overrides.

Convert single-owner uniqueness to composite uniqueness, for example:

- holdings: `(owner_id, ticker)`
- radar: `(owner_id, ticker)`
- snapshots: `(owner_id, snap_date, ticker)`
- active investment plans: `(owner_id, ticker)`
- publication idempotency: includes `owner_id`, market date, phase, and kind
- scheduled run claim: at most one active `(owner_id, market_date, phase)` lease

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

Every user-specific table is in `app`, has RLS enabled and forced, and uses explicit authenticated
policies:

```sql
USING (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()))
WITH CHECK (auth.uid() IS NOT NULL AND owner_id = (SELECT auth.uid()))
```

`USING` and `WITH CHECK` policies are both required. Grants and RLS policies are tested together.
There are no permissive catch-all policies, no owner ID sourced from mutable user metadata, and no
browser-writable base tables, admin tables, or provider credentials. PostgREST exposes `api`, not
`app`; every exposed view uses invoker security. Machine root RPCs are not exposed through PostgREST.
Tests run through PostgREST with real user JWTs, and staging must pass the Supabase security advisor
without an unresolved security-category finding.

## 8. Existing-data migration and cutover

The production migration is additive and fail-closed:

1. Create the owner's Supabase Auth user and record the immutable UUID through a trusted workflow.
2. Pause all Claude Routines and Telegram mutation handling.
3. Capture encrypted logical backup, table counts, relationship checks, and deterministic row
   digests. Verify that the backup can be decrypted before changing schema.
4. Create private `app` and exposed `api` schemas, then add nullable `owner_id` columns and new
   identity/connection tables.
5. Backfill every current user-specific row to the owner's UUID inside a transaction.
6. Reject the migration if any current row remains unowned, any relationship crosses owners, or any
   canonical label/precondition is unknown.
7. Add composite keys, foreign keys, indexes, forced RLS, API views, restricted machine roles, and
   owner-resolving root RPC versions.
8. Deploy tenant-aware scheduler, gateway, app API, and Telegram functions while old entry points
   remain paused.
9. Replace the owner's broad legacy routine secret with separate inbound gateway and outbound Routine
   trigger credentials; revoke the legacy secret.
10. Test every exposed view/RPC through PostgREST with two synthetic users, the anonymous role,
    revoked sessions, and forged tokens; run the security advisor.
11. Set owner columns `NOT NULL`, limit PostgREST to `api`, revoke obsolete RPCs, and remove
    single-owner environment variables and runtime service-role keys.
12. Run non-writing provider dry runs, web mutation previews, Telegram pairing, and before/after data
    parity checks.
13. Resume one controlled pre-market-equivalent run, inspect receipts, and then resume the schedule.

Rollback restores the last reviewed Edge Function code and leaves additive owner columns in place.
If data integrity is uncertain, keep routines and mutations paused; do not attempt an automatic
destructive down-migration.

## 9. Provider-neutral analysis protocol

### 9.1 Connection modes

Two adapter shapes sit behind one analysis contract:

1. **Claude Routine adapter (release one):** the application invokes a user-owned Routine through its
   documented `/fire` trigger, then the Routine calls the scoped gateway. This is not the paid Claude
   inference API; runs consume the user's eligible Claude subscription usage and Routine allowance.
2. **OAuth/MCP adapter (future):** a public streamable-HTTP MCP server authenticates users with OAuth
   2.1. This is the appropriate shape for a future ChatGPT plugin and is a separately reviewed project.

User-funded model APIs and local/self-hosted runtimes remain outside release one.

### 9.2 Launch provider support

- **Claude:** the only release-one allow-listed provider. One API-triggered Routine per user replaces
  three user-managed schedules. The connection kit configures Custom network access, a host-bound
  inbound API credential, the reviewed prompt, and the outbound `/fire` trigger.
- **ChatGPT:** not available in release one. Current OpenAI documentation allows web scheduled tasks to
  use plugins available to the chat, but authenticated plugins require a public MCP server and OAuth
  2.1. A post-launch spike must prove an unattended scheduled plugin run on the user's actual eligible
  plan before this adapter can be promised.
- **Grok/xAI:** not available. No verified subscription-backed scheduled route exists; the documented
  inference route uses separately billed API credits.
- **Other providers:** unsupported providers cannot be selected merely by typing a model name. Each
  adapter needs contract tests, current official documentation review, and an explicit allow-list.

### 9.3 Per-user connection lifecycle

Each user connects their own eligible provider account; the application does not lend or pool the
owner's Claude subscription. Setup follows one provider-neutral state machine:

1. The signed-in user chooses an allow-listed provider and acknowledges exactly which bounded
   portfolio/research fields will leave Supabase for that provider.
2. The server creates a disabled connection and shows a versioned, screenshot-level setup kit. It
   includes the exact Routine prompt, Custom-network domain list, one-time inbound gateway credential,
   and instructions for adding an API trigger. It never shows a database or Telegram secret.
3. The user creates one unscheduled Claude Routine, binds the gateway token as an API credential to the
   Functions host only, enables the API trigger, and pastes the trigger URL/token into the app once.
4. The provider performs a version/capability handshake. The server records capability claims but
   trusts only operations that pass the allow-list and conformance test.
5. A no-write handshake fired by the application—not curl—proves outbound triggering, the returned
   provider session URL, network access to allowed research/market hosts, inbound authentication,
   owner isolation, contract version, and receipt handling. Green provider status alone is not success.
6. The user explicitly activates the connection as primary.
7. Disconnect/revoke invalidates the credential immediately and suppresses future expected-run alerts
   for that connection. Reconnection always creates a new secret.

No shared master provider credential is copied to friends. A provider that cannot safely complete the
full handshake remains unavailable while recordkeeping and Telegram continue to work. The Connections
screen states that Routines are research preview, use the user's subscription allowance, have a daily
run cap, and can fail even when the provider run appears green.

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
duplicate/replayed identities with different payloads, and undeclared cross-run references.

Freshness is enforced through verification events, not by forcing stable facts to acquire fake-new
identifiers. Every actionable run requires a server market snapshot retrieved after `run.started_at`
and a current-run source-search receipt recording which news/filing sources were checked, including an
explicit no-new-material-evidence result. Older filings/fundamentals may be cited only with a
current-run revalidation event and a category-specific freshness window. Previous recommendations are
referenced only through `prior_suggestion_ids`. Reusing an old market snapshot or merely copying the
morning evidence packet into an intraday run is rejected as `evidence_stale`.

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

Until an allowed corporate-action source is verified in a release gate, a held ticker with a detected
or suspected corporate action enters `needs_review`. New stop/target alerts for it are suppressed with
`corporate_action_pending`; the user confirms adjusted quantity and levels through the normal command
workflow. Gate 0 first tests Finnhub's split endpoint under the actual account entitlement. If it is
unavailable, a large discontinuity/adjustment mismatch plus a current issuer/exchange-news check can
only mark `suspected`; it cannot clear the state or adjust the ledger automatically.

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

### 9.8 Primary provider and deferred second opinions

Each user has one primary Claude connection in release one. Second-opinion runs are deferred until the
first month of production outcomes and provider-contract evidence has been reviewed.

There is no silent provider failover. A missed or failed run is shown as failed and may generate an
operational alert. Automatic failover could change cost, privacy terms, model behavior, and advice
without informed consent.

## 10. Scheduling and run lifecycle

The application owns market timing but not model inference. A Supabase Cron scheduler checks due
market-session slots every five minutes and invokes each active user's documented Claude Routine
`/fire` endpoint. It sends only an opaque run-request identifier in the untrusted fire payload; phase,
owner, and market date are derived by the server when the Routine calls `start_run`. This trigger is a
Claude Routine subscription feature, not the separately billed Claude inference API.

The canonical exchange calendar and session calculations use `America/New_York`; the user's IANA
timezone is for display and scheduler setup. Default anchors match the current cadence:

- pre-market: 120 minutes before the regular open (normally 07:30 Eastern / 06:30 Central);
- intraday: 210 minutes after the regular open (normally 13:00 Eastern / 12:00 Central); and
- post-market: 10 minutes after the regular close (normally 16:10 Eastern / 15:10 Central).

Anchors are resolved against the actual market session so holidays, early closes, and daylight-saving
changes do not rely on provider prose or fixed provider schedules. On an early-close day, intraday runs
at open + 120 minutes and post-market at close + 10 minutes. A fresh intraday run gathers and verifies
current evidence; it never republishes the morning recommendation as the midday decision.

Each run follows:

```text
provider wakes
  -> start_run
  -> server resolves connection, run slot, owner, phase, market calendar, and idempotency
  -> read_bounded_context
  -> provider gathers fresh evidence and creates Analyst + Checker output
  -> submit_analysis
  -> server refreshes market facts and applies deterministic policy
  -> atomic persistence + publication claim
  -> Telegram delivery or explicit suppression/failure/unknown receipt
  -> permitted artifact/outcome work
  -> finish_run
```

`scheduled_run_slots` has a unique `(owner_id, market_date, phase)` key. The scheduler first claims the
slot and a trigger-attempt idempotency key, then calls Claude. An uncertain `/fire` response is recorded
as `trigger_unknown` and is not blindly retried. `start_run` leases the matching slot; a concurrent or
duplicate start receives the canonical run ID and cannot create another analysis or alert-state
transition. A lease expires at the phase window end so a crashed run can be retried explicitly. Runs
outside a scheduled window are `on-demand`, limited to one per owner per hour.

Supabase Cron performs deterministic, model-free maintenance:

- expire pending commands and pairing codes;
- detect missed expected run windows;
- clean old operational rows under the retention policy;
- invoke due Routine trigger slots and record trigger receipts;
- verify nightly holding projections; and
- trigger bounded backup/health metadata jobs where appropriate.

Cron never generates investment advice.

Market holidays are checked before any Routine trigger. The scheduler emits the one deterministic
holiday publication itself and performs no model research. Duplicate scheduler ticks, delayed provider
starts, manual runs, and daylight-saving transitions cannot produce duplicate analysis or publications.
The market date and phase come from the server's exchange calendar, not provider prose.

## 11. Portfolio mutation model

Web and Telegram use the same command state machine:

```text
submitted -> previewed -> confirmed -> applied
                    |         |          |
                    |         |          +-- immutable receipt
                    |         +-- stale/expired/rejected/error
                    +-- cancelled/expired
```

Each command stores owner, operation, normalized input, expected ledger sequence, preview digest,
expiry, idempotency key, status, and result. Confirmation must match the owner, channel identity,
command ID, and preview digest.

The atomic RPC takes a transaction-scoped advisory lock derived from `(owner_id, ticker)` before
checking for a holding, so concurrent first buys are serialized. It rechecks the owner's ledger
sequence, appends the transaction, rebuilds the owner/ticker average-cost projection from the full
ordered ledger, advances a matching recurring plan at most once, and records the receipt in one
transaction. A retry with the same idempotency key returns the original receipt.

Corrections do not rewrite history. The correction RPC previews and then records a compensating void
event linked to the original transaction plus a replacement transaction. It recomputes the affected
projection deterministically and refuses corrections or back-dated ordinary commands that create a
negative historical balance or ambiguous ledger. Realized P&L is derived by the projection, not copied
from command prose.

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
attempts before invalidation. Store an HMAC digest under a rotation-capable pairing secret; this
short-lived secret is not part of disaster recovery because all pending codes are invalidated after a
restore. Pairing is accepted only in a private chat where `chat.id = from.id`. Rate limits apply per
source and globally; error messages never reveal whether a code or account exists.

### Commands

The existing Buy, Sell, Stop, Portfolio, Plan, Plans, and Cancel Plan commands remain. Add Help,
Status, and Unlink. Every mutation previews first and requires an inline Confirm button. Callback
confirmation must come from the same paired Telegram user and chat before expiry.

`telegram_updates(update_id PRIMARY KEY, received_at)` is a 30-day set-based idempotency boundary;
ordering is never assumed because Telegram may choose a random next ID after a quiet week. Group and
channel messages receive a fixed private-chat-only refusal and never create commands. Callback data is
an opaque token no longer than 64 bytes. The handler answers the callback promptly, then idempotently
applies `(command_id, action)` only if the link is still active at callback time. `/unlink` cancels all
pending commands. Commands and messages carry the internally resolved owner; the old static owner-ID
environment variables are removed after migration.

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
6. **Connections:** Claude setup kit, Routine/session status, token rotation/revocation, Telegram
   pairing, and last successful handshake.
7. **Settings:** timezone, notification preferences, and expected phases. Provider timing is
   server-owned and is not presented as a user-editable cron schedule.

Operator invitations use a trusted CLI. A separate aggregate health view contains no per-user ticker,
position, recommendation, or identifiable missed-run table; there is no admin mutation UI.

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
- The app deliberately has no offline shell or queued mutation behavior.

### 13.3 Browser security

- Strict Content Security Policy with no inline scripts or unapproved third-party origins.
- HSTS, `frame-ancestors 'none'`, no MIME sniffing, strict referrer policy, and restrictive browser
  permissions.
- Exact production CORS allow-list on Edge Functions.
- Email OTP is primary. Desktop magic-link fallback uses PKCE and allow-listed callbacks. Access-token
  lifetime is 15 minutes with refresh-token rotation; browser mutations attach the bearer JWT
  explicitly, and no token is written to URLs or logs.
- Dependency lockfile, automated dependency audit, and no unreviewed remote scripts.
- Sanitize all model and database prose before rendering; render as text by default, never raw HTML.
- Do not put portfolio data in URLs, local analytics, error trackers, or browser caches.
- Account deletion, Routine-token rotation, and Telegram relinking require a fresh email OTP. The
  server records a session-bound step-up receipt with a five-minute expiry; destructive RPCs check it.
  Account deletion revokes every active session.

## 14. Personal policy and platform safety

Release one applies immutable, versioned platform hard limits. Users and models cannot weaken them.
The schema reserves owner-scoped overrides that may only be stricter, but release one exposes no UI or
command that writes them. Historical decisions retain the policy version used at the time.

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
- Claude `/fire` definite rejection: mark `trigger_failed`; timeout/ambiguous acceptance:
  `trigger_unknown`, do not automatically retry, and wait for an inbound `start_run` before offering a
  manual retry.
- Claude run that never completes the inbound handshake: mark `provider_incomplete` and link its
  provider session when available; provider green status is not treated as application success.
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
- Full bounded model evidence: retain 12 months. The compaction job may ship later but must exist before
  the first record reaches 12 months; it preserves citations, hashes, and decision records.
- Expired pairing codes: delete after 24 hours.
- Expired/cancelled portfolio commands: retain 90 days, then preserve only non-sensitive audit facts.
- Telegram update deduplication records: retain enough IDs/timestamps for reliable replay protection,
  without storing unnecessary message text.
- Account deletion removes active data under the deletion runbook. Daily/weekly encrypted backups may
  retain deleted data for at most 35 days; a minimal non-financial deletion tombstone ensures a restore
  does not resurrect a deleted account before those archives age out.

Deletion first disables sign-in and trigger claims, revokes both Claude credentials, unlinks Telegram,
cancels pending commands, and offers an export. After the 72-hour cancellation window it removes
owner-scoped rows—including rendered publication bodies—in a documented dependency order, deletes the
Auth identity last, records only the non-financial tombstone, and confirms completion. The bot attempts
to delete recent messages where Telegram permits it and instructs the user to clear older chat history.

A full-ledger reset is an operator runbook, not an ordinary UI button: fresh OTP, mandatory export,
previewed row counts, explicit owner confirmation, owner-only purge, projection verification, and an
immutable non-financial reset receipt.

### Free-tier backup plan

Supabase Free does not include downloadable automatic backups. Release one therefore requires an
encrypted logical backup outside Supabase before friend onboarding.

The backup controller is a scheduled workflow in a separate private GitHub repository, not this public
source repository. Private-repository schedules avoid the public-repository 60-day inactivity disable,
and no backup is stored as a GitHub artifact.

1. Versioned migrations reconstruct the schema. The workflow connects through the Supavisor
   session-mode pooler over IPv4 with a dedicated `backup_reader` database login.
2. `backup_reader` has no direct table grants and no `BYPASSRLS`. It may execute only purpose-built,
   fixed-`search_path` backup export RPCs owned by a non-superuser role. Those RPCs expose an
   enumerated set of `app` data plus an encrypted identity-recovery map and cannot mutate data or read
   Auth secrets, Vault, or unrelated schemas.
3. The runner exports schema-version metadata and application data, computes row/relationship digests,
   and encrypts the archive with `age` to an offline-held public key before any upload.
4. Ciphertext is uploaded to a private Cloudflare R2 bucket. Retain 14 daily and 4 weekly archives;
   enforce lifecycle cleanup and alert before stored bytes approach the free allowance.
5. The workflow records success metadata in the application and reports failures through the
   operational channel. A small independent Cloudflare scheduled monitor checks the newest R2 object
   and alerts if backup age exceeds 36 hours, so a Supabase outage cannot hide backup failure.
6. Vault plaintext, Telegram/SMTP/R2 tokens, database passwords, and other reusable secrets are never
   placed in the data backup. Their inventory and rotation/reconnection procedure is versioned without
   values; recoverable values are held in the operator's offline password manager.
7. The restored high-entropy inbound gateway-token digests remain valid without a pepper. Claude
   outbound Routine trigger tokens stored in Vault are deliberately reconnected after a full disaster.
   Pending Telegram pairing codes are invalidated; existing Telegram links restore, and the webhook is
   re-registered with rotated server secrets.
8. Restore into staging applies migrations, recreates invited identities, explicitly remaps old-to-new
   owner UUIDs, applies deletion tombstones, restores data, and verifies digests and ledger projections.
   It is exercised before launch and quarterly; staging returns to synthetic data afterward.

Friend onboarding remains disabled until a restore has succeeded from only the R2 archive, offline
`age` private key, and documented operator secret store. If current GitHub/R2 limits cannot support the
workflow at zero additional usage cost, this specification must be amended and re-approved; backups
must not silently move to the public repository or disappear.

Target launch objectives:

- Recovery point objective: 24 hours.
- Recovery time objective: three business days for a complete project loss, including identity remap,
  provider reconnection, Telegram webhook registration, and user verification.

## 17. Deployment and environments

Use the two Supabase Free projects deliberately:

- **Production:** real owner and invited-user data.
- **Test/staging:** synthetic users and data only; used for migrations, RLS attacks, Edge Function
  integration tests, provider contract tests, and restore drills. A disaster restore may temporarily
  sacrifice staging because the free plan provides no third project.

Before the web gate closes, both projects expose only the `api` schema through PostgREST, use current
asymmetric JWT signing keys, and deliver OTP through custom SMTP. Authenticated Edge Functions deploy
without legacy platform JWT verification and validate access tokens in code against the project JWKS;
forged or wrong-project tokens are rejected. Availability of Cron/`pg_net` on the actual free projects
is proven rather than assumed.

Cloudflare deploys the static frontend from Git after tests pass. Preview deployments use staging or
mock data and never receive production secrets. The frontend build contains only public Supabase
configuration.

Supabase Free may pause an insufficiently active project. The external Cloudflare health/backup-age
monitor reports unavailability but does not generate fake traffic merely to defeat plan limits. One
observed production pause or sustained quota pressure triggers an explicit Supabase Pro decision before
additional friends are invited.

GitHub Actions performs deterministic CI and deployment only; no model API is used in CI. Production
database migrations and Edge Function deployments require a protected manual gate until the process
has enough evidence to automate safely.

Suggested repository additions after design approval:

```text
apps/web/                         React/TypeScript/Vite SPA + web manifest
supabase/functions/app-api/      authenticated product mutations and export
supabase/functions/run-scheduler/market-aware Claude Routine triggers
supabase/functions/agent-gateway/owner-scoped analysis protocol
supabase/functions/telegram-portfolio/
supabase/migrations/                  additive tenancy and product migrations
packages/contracts/              shared schemas and generated types
tests/security/                  RLS and cross-tenant attack tests
tests/contracts/                 provider conformance fixtures
docs/runbooks/                   onboarding, incident, backup, restore, rollback
docs/connection-kits/             versioned Claude setup instructions and screenshots
```

Exact file boundaries may be refined in the implementation plan, but security domains must not be
collapsed into one large Edge Function.

Runbooks cover rotation of each separate credential (Claude trigger, inbound connection, Telegram bot,
SMTP, R2, database roles), provider-account loss, wrong-tenant disclosure, trigger pause, ledger reset,
backup/restore, and rollback. This is a single-operator pilot: if Rajrupesh is unavailable, no hidden
administrator can pause providers or restore service. That limitation is disclosed before friend
onboarding.

## 18. Testing strategy

### Database and RLS

- Anonymous, authenticated owner A, authenticated owner B, revoked user, restricted machine-role,
  and offline-operator misuse cases.
- Cross-user reads, inserts, updates, deletes, RPC calls, foreign-key substitutions, and guessed IDs.
- Composite ownership constraints and attempts to create cross-owner relationships.
- Security-definer search paths, grants, function ownership, and RLS on every exposed table/view.
- Security-definer-view bypass attempts; every browser-readable view must prove invoker-context RLS.
- PostgREST tests with real owner A/B JWTs, revoked sessions, and forged/wrong-project JWTs for every
  exposed view/RPC. Staging's security advisor must be clean.
- Machine database roles can execute only their named root RPCs, cannot select base tables, and have
  no service-role/PostgREST credential.
- The backup role has no direct table or `BYPASSRLS` grant; its export RPC allow-list cannot reach
  Vault, Auth secrets, or a non-enumerated schema/table.
- Migration tests from a realistic copy of the current single-owner schema.

### Portfolio invariants

- Concurrent Buy/Sell/Stop/Plan commands on the same owner/ticker.
- Duplicate confirmation, callback replay, expiry, stale holding version, sell-all, fractional shares,
  historical dates, correction chains, negative-balance attempts, and exact decimal behavior.
- Amount/quantity/price mismatch, implausible share count, unsupported precision, symbol ambiguity,
  and explicit unclassified-bucket cases.
- Two simultaneous first buys yield one applied idempotency receipt per distinct command without lost
  ledger entries. Property tests compare every admitted buy/sell/correction interleaving with a pure
  average-cost fold over the final ledger.
- Buy/sell fee reconciliation, separate recurring deposits, eight-decimal fractional shares, and
  back-dated negative historical balance rejection.
- Web and Telegram produce equivalent previews and receipts.

### Provider contracts

- The conformance suite runs against Claude at launch and is mandatory for every future adapter.
- Unknown fields, bad enum values, oversized payloads, stale evidence, replayed request IDs, switched
  owners, missing Analyst/Checker records, and fabricated receipts are rejected.
- Provider outputs cannot affect deterministic quote, policy, persistence, or delivery fields.
- Prompt-injection strings in news, database prose, and user notes remain inert data.
- Each provider receives only one owner's bounded packet after consent; cross-owner batch prompts and
  undeclared context fields fail contract tests.
- A morning packet copied into an intraday run fails; a stable filing with a new current-run
  revalidation receipt passes; a fresh search reporting no material news passes without invented news.
- Edge CPU/wall-time budgets are tested with 40 holdings and 20 submitted candidates.

### Publication and scheduling

- Holiday gate, daylight-saving boundaries, delayed runs, duplicate schedules, missed windows,
  silent intraday behavior, Telegram failure/unknown states, and exactly-once claims.
- Concurrent scheduler ticks, concurrent `start_run`, uncertain Routine-fire acceptance, lease expiry,
  early-close windows, and a manual run overlapping a scheduled slot.
- No prior recommendation is shown as current after a failed fresh run.
- Split-day fixtures suppress stop/target alerts and create `corporate_action_pending`.

### Frontend

- OTP authentication on iOS/Android phone mail clients, desktop magic-link fallback, route protection,
  session expiry, step-up expiry,
  loading/error/empty states, mobile layout, keyboard use, screen readers, and no private-data caching.
- Model prose XSS payloads and content-security-policy regression tests.
- Two-browser end-to-end cross-tenant tests using synthetic users.
- No service worker is registered and static security headers match the reviewed policy.

### Recovery and operations

- Backup encryption/decryption, R2 lifecycle, disabled-job backup-age alert, staged restore from R2 and
  the offline key, row/digest/projection parity, user-identity rebinding, Routine reconnection,
  credential rotation, Telegram webhook restoration, and rollback while triggers are paused.
- Telegram tests cover random lower `update_id` after inactivity, group chats, callback after unlink,
  callback replay, and the 64-byte callback-data limit.

## 19. Threat model summary

| Threat | Primary controls |
|---|---|
| User A requests User B's row ID | RLS, owner-derived identity, composite ownership FKs, attack tests |
| Browser bundle or XSS steals privileged credentials | No privileged client secrets, CSP, text rendering, dependency controls |
| View bypasses RLS despite safe-looking SQL | Invoker-security views, revoked grants, owner A/B attack tests |
| External model asks for another owner | Owner resolved from token; no owner selector; bounded context operation |
| Model provider receives excess or mixed-user context | Explicit connection consent, minimum packet, one owner per run, field allow-list |
| Inbound provider token leaks | High-entropy digest, one-owner capability, quotas, rotation/revocation, no DB access |
| Claude trigger token leaks | Vault encryption, one-Routine scope, no portfolio access, trigger quotas, provider-side revocation |
| Machine function chooses wrong owner | Separate execute-only DB roles; root RPC resolves owner from credential/link; no owner parameter |
| Telegram account hijack or guessed code | Webhook secret, short single-use digest, attempt limit, same-user callback checks, relink reauth |
| Duplicate webhook/request | Update ID and idempotency claims; atomic original-receipt return |
| Model prompt injection | Treat all external/stored prose as data; fixed contract; no direct tools for side effects |
| Model fabricates price or send claim | Server-refetched facts, fixed renderer, server receipts only |
| Split or stale/free quote creates a false alert | Corporate-action normalization, source/as-of state, conflict gate, fail closed |
| Free provider misses a run | Expected-run monitor, explicit missed state, no stale-plan reuse |
| Backup exposes portfolios or API keys | Encrypt before upload, offline private key, exclude Vault plaintext, restore test |
| Admin UI becomes surveillance surface | Invitations and health only; no impersonation/cross-user portfolio browser |
| Friend's Claude account is compromised | Blast radius limited to one owner; bounded context; revoke connection and Routine tokens |
| Telegram bot token leaks | Rotate webhook/bot token, cancel pending commands, audit sends, notify affected users |
| Operator laptop/account is compromised | No live backup key in repo; scoped credentials; rotation runbook; audit and incident notification |
| Wrong-tenant disclosure occurs | Contain/rotate, preserve audit facts, identify affected users, notify within 72 hours |
| Product drifts toward execution | No broker dependency or endpoint; security tests and copy invariants enforce record-only language |

## 20. Observability without leaking financial data

Operational records include request ID, owner pseudonymous ID/hash where needed, component, operation,
status code, duration, provider/model identifier, contract version, and bounded error code.

Platform logs are a one-day debugging convenience on the free tier. Incident-relevant bounded facts
live under retention in owner-scoped Postgres records such as gateway requests, auth events, webhook
events, trigger attempts, and backup status. They exclude holdings, quantities, cost basis,
recommendations, chat text, tokens, full URLs containing codes, and request/response bodies.

The admin health view shows aggregate component health, missed-run counts, backup age, deployed
versions, and provider adapter status without exposing which ticker or position belongs to a user.

## 21. Cost and scaling boundaries

Expected pilot infrastructure:

- Cloudflare Workers Static Assets: free static delivery within current platform terms.
- Supabase Free: database, Auth, Edge Functions, and Cron within current quotas.
- Telegram Bot API: no separate application hosting cost.
- Claude Routine runs: charged against each participating user's eligible Claude plan allowance and
  daily Routine cap, subject to research-preview availability and limits.
- No model API charge in the default launch path.

Known launch cost and free-tier conditions:

- An operator-controlled domain for authenticated email is the likely unavoidable non-zero launch
  cost. Custom SMTP may use a transactional provider's free allowance.
- R2 requires enabling its usage-based subscription. The pilot must remain below its included storage
  and operation allowances, with lifecycle enforcement and usage alerts; “free” is not a hard cap.

Pre-agreed future escalations must be visible before activation:

- Supabase Pro for guaranteed non-pausing behavior, downloadable automatic backups, greater limits,
  logs, and support.
- A paid transactional-email tier if invitations exceed the free allowance.
- A separately approved OpenAI, Anthropic, xAI, or other model API; none exists in release one.
- Object storage if encrypted backup retention exceeds included quotas.

The product must show provider connection health and last-run time; it must not promise unlimited or
guaranteed analysis based on a consumer subscription.

## 22. Internal delivery gates for the single complete release

The release is built incrementally but launched only after all gates pass.

### Gate 0: external capability proofs

- Staging sends an OTP through custom SMTP to a non-team phone email client.
- A second Claude account completes the setup kit, accepts an application-fired `/fire` trigger, and
  calls the staging gateway without a model API key or running computer.
- The actual free Supabase projects prove Cron/`pg_net`, asymmetric JWT validation, session-pooler
  access for custom database roles, and documented pause behavior.
- A private backup workflow writes and reads an `age`-encrypted R2 test object; the independent age
  monitor alerts when it becomes stale.
- At least one allowed corporate-action source is tested. Until it passes, suspected events always
  enter `needs_review` and cannot produce price-level alerts.

### Gate A: tenancy foundation

- Private `app`/exposed `api` schemas, identity tables, owner columns, composite constraints, forced
  RLS, restricted machine roles, and migration verifier.
- Existing owner data backfills with exact parity.
- PostgREST two-user, forged-token, RPC-grant, and security-advisor suites pass.

### Gate B: owner-aware control plane

- Ledger-projection RPCs, advisory locking, fees, back-dated checks, run-slot exclusivity,
  server-derived phase, evidence verification, quotas, gateway, publications, and Telegram are
  owner-aware.
- Telegram private-chat, set-based dedupe, callback-after-unlink, and replay tests pass.
- Legacy single-owner secrets and assumptions are removed.
- Existing owner workflows pass regression and dry-run tests.

### Gate C: Claude connection lifecycle

- Versioned contract, one-Routine connection kit, separate trigger/gateway credentials, real scheduler
  handshake, returned provider session URL, source access, and provider conformance suite.
- A non-Raj Claude account completes setup without live technical intervention.
- ChatGPT, Grok, second-opinion, and BYOK paths are rejected server-side and absent from the UI.

### Gate D: complete web product

- Seven release-one screens, OTP/custom-SMTP auth, recordkeeping workflows, Claude/Telegram setup,
  consent, export, deletion request, step-up, and settings.
- Mobile/accessibility/CSP/no-cache/no-service-worker verification passes on iOS and Android.

### Gate E: operations and recovery

- Staging environment, deployment runbooks, source/quota/backup monitoring, private R2 retention,
  backup-age failure alert, successful restore from R2 plus offline key/operator secret store, incident
  response, secret rotation, and rollback drill.

### Gate F: controlled production cutover

- Pause Routine triggers and Telegram mutations.
- Back up, migrate, verify, deploy, pair owner, connect Claude, and run no-write tests.
- Exercise one owner web mutation and one Telegram mutation with explicit confirmation.
- Observe one complete scheduled market cycle.
- Keep friend invitations disabled during an owner-only soak period.

### Gate G: invite-only launch

- Reconfirm backup age under 36 hours and a successful restore within the last 30 days.
- Invite one synthetic/test account and then one trusted friend only after the unassisted Claude kit,
  phone OTP, consent, isolation, deletion/export, and ledger-projection checks pass.
- Enable additional invitations only after reviewing errors, provider usage, notification noise, and
  support burden.

## 23. Release acceptance criteria

The complete core product is ready only when all are true:

- Existing owner records, plans, suggestions, and outcome links survive migration with verified
  parity.
- Anonymous access returns no private rows.
- Two authenticated test users cannot infer, read, mutate, link to, notify, or analyze each other's
  records through any table, view, RPC, Edge Function, Telegram callback, or provider operation.
- No client artifact or runtime product function contains a service-role key; machine roles can invoke
  only their named root RPCs.
- Every mutation requires a matching unexpired preview and returns an idempotent atomic receipt.
- Material amount/quantity/price mismatches and ambiguous symbols cannot reach confirmation.
- Holdings exactly match the average-cost fold over the ledger after concurrent first buys, sells,
  fees, back-dated operations, and corrections.
- One scheduled run slot can produce at most one canonical analysis/alert transition per owner, market
  date, and phase.
- Claude passes the contract and real `/fire` handshake; every unsupported provider is rejected.
- A model cannot override server prices, freshness, policy, notification suppression, or send status.
- Failed/missed runs do not reuse stale advice as current, and intraday requires a fresh market snapshot
  plus a current-run source-check receipt.
- Telegram pairing, private-chat enforcement, set-based dedupe, unlink cancellation, replay defense,
  and same-user confirmation pass.
- OTP/custom-SMTP authentication and five-minute step-up work on a phone; the app registers no service
  worker and does not cache private API data.
- Encrypted R2 backup, backup-age alert, and staging restore succeed within the stated recovery
  objectives using only the documented recovery inputs.
- Consent discloses provider transcript retention, operator access, quote-vendor ticker disclosure, and
  Telegram deletion limits.
- Deployment and rollback instructions have been exercised.
- There is still no brokerage credential, dependency, endpoint, or executable trade action anywhere
  in the system.

## 24. Resolved product decisions

1. One shared Supabase project with a private `app` schema, exposed `api` schema, owner IDs, forced RLS,
   and PostgREST attack tests—not one database per user.
2. Exactly one USD portfolio per user for US-listed equities/ETFs in release one.
3. Invite-only onboarding through operator CLI, custom SMTP, and OTP-first authentication.
4. Claude Routines are the sole release-one model adapter. One API-triggered Routine per user is
   scheduled by the application; no inference API or always-on Mac is involved.
5. ChatGPT is a future public MCP/OAuth 2.1 adapter and Grok is unavailable. Neither is selectable in
   release one; there is no second-opinion, failover, voting, or BYOK path.
6. The frontend is a static Cloudflare-hosted SPA with no service worker; Supabase owns business logic.
7. Browser, Claude, and Telegram use separate least-privilege trust paths but converge on the same
   deterministic ledger and policy invariants.
8. Release one exposes platform hard safety limits only. Stricter personal overrides are schema-only.
9. Backups run from a private automation repository to private R2; reusable secrets are excluded and
   full-disaster recovery deliberately reconnects outbound provider triggers.
10. The product launches once after internal gates and an owner soak. Friend onboarding is blocked by
    any isolation failure, stale backup, failed restore, failed phone OTP, failed unassisted Claude
    setup, ledger mismatch, missing consent, or brokerage-capable path.

## 25. External-review reconciliation

The complete external review is preserved at
[`docs/reviews/2026-09-03-complete-core-product-adversarial-review.md`](../../reviews/2026-09-03-complete-core-product-adversarial-review.md).

Accepted findings:

- custom SMTP and OTP-first authentication;
- a real, unassisted Claude onboarding/handshake test;
- run-slot exclusivity, average-cost ledger projection, first-buy advisory lock, fee-aware inputs, and
  back-dated balance validation;
- private/exposed schema split, PostgREST-level RLS tests, no privileged browser mutation path;
- Telegram set-based dedupe, private-chat-only commands, callback/link revalidation, and unlink cancel;
- explicit rate limits, operational tables, corporate-action suppression, privacy disclosures, phone
  step-up, free-tier limitations, and a three-business-day full-loss RTO;
- scope cuts for second opinions, Grok, admin mutation UI, FX, service worker, and editable schedules.

Adjusted findings:

- **ChatGPT:** the OAuth/MCP requirement is correct, but current official OpenAI documentation says web
  scheduled tasks can use plugins available to the chat. The adapter is therefore not declared
  impossible; it is deferred until an OAuth/MCP unattended capability spike passes on the target plan.
- **Recovery secrets:** backing reusable peppers and service tokens up beside portfolio archives would
  enlarge the compromise blast radius. High-entropy inbound tokens use an unkeyed SHA-256 digest so
  they have no pepper dependency; low-entropy pairing codes expire; Vault trigger tokens are
  reconnected after full loss; live secrets remain in platform stores plus the operator's offline
  password manager.
- **Evidence freshness:** stable filings and fundamentals do not need fake-new evidence IDs. The server
  instead requires a current-run retrieval/revalidation event, a fresh server market snapshot, and a
  current source-search receipt. Copying a prior run without current verification is rejected.
- **Machine authorization:** a service-role session setting is not a meaningful RLS boundary because
  the service role bypasses RLS, and separate PostgREST calls would not share one transaction. Runtime
  machine functions instead use distinct execute-only database roles and root RPCs that resolve owner
  internally without accepting `p_owner_id`.
- **Claude scheduling:** official Anthropic documentation now exposes a per-Routine `/fire` trigger.
  One application-triggered Routine per user replaces three provider schedules and lets the server
  handle DST, holidays, and early closes. This beta capability is a Gate 0 proof, not an assumption.

## 26. Research references

- [Cloudflare Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/)
- [Cloudflare static-assets billing and limitations](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/)
- [Cloudflare static-asset headers](https://developers.cloudflare.com/workers/static-assets/headers/)
- [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/)
- [Supabase Auth](https://supabase.com/docs/guides/auth)
- [Supabase user invitations](https://supabase.com/docs/guides/auth/users)
- [Supabase custom SMTP](https://supabase.com/docs/guides/auth/auth-smtp)
- [Supabase passwordless email](https://supabase.com/docs/guides/auth/auth-email-passwordless)
- [Supabase OAuth 2.1 server](https://supabase.com/docs/guides/auth/oauth-server)
- [Supabase Row-Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase data security](https://supabase.com/docs/guides/database/secure-data)
- [Supabase database advisors](https://supabase.com/docs/guides/database/database-advisors)
- [Supabase database connections](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [Supabase API keys](https://supabase.com/docs/guides/api/api-keys)
- [Supabase Edge Function limits](https://supabase.com/docs/guides/functions/limits)
- [Supabase scheduling Edge Functions](https://supabase.com/docs/guides/functions/schedule-functions)
- [Supabase Vault](https://supabase.com/docs/guides/database/vault)
- [Supabase database backups](https://supabase.com/docs/guides/platform/backups)
- [Supabase free-project pausing](https://supabase.com/docs/guides/platform/free-project-pausing)
- [Telegram Bot API webhooks](https://core.telegram.org/bots/api#setwebhook)
- [OpenAI Docs: scheduled tasks](https://learn.chatgpt.com/docs/automations)
- [OpenAI Docs: authentication](https://learn.chatgpt.com/docs/auth)
- [OpenAI plugin architecture](https://developers.openai.com/plugins)
- [OpenAI: build a plugin MCP server](https://developers.openai.com/plugins/build/mcp-server)
- [OpenAI plugin OAuth authentication](https://developers.openai.com/plugins/build/auth)
- [Anthropic Claude Routines](https://code.claude.com/docs/en/routines)
- [Anthropic Claude cloud environments](https://code.claude.com/docs/en/cloud-environments)
- [Anthropic: subscription and API billing are separate](https://support.anthropic.com/en/articles/9876003-i-subscribe-to-a-paid-claude-ai-plan-why-do-i-have-to-pay-separately-for-api-usage-on-console)
- [GitHub scheduled workflow behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [GitHub artifact access](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)
- [xAI inference API](https://docs.x.ai/developers/rest-api-reference/inference)
- [xAI API billing](https://docs.x.ai/console/billing)

## 27. Approval and implementation plan

Rajrupesh approved this revision on 2026-09-02. The executable task plan is
[`docs/superpowers/plans/2026-09-02-complete-core-product-implementation.md`](../plans/2026-09-02-complete-core-product-implementation.md).
It begins with Gate 0 capability proofs and uses test-driven implementation through Gates A-G. Any
failed Gate 0 assumption returns the design to review instead of being worked around silently.
