# stocks-agent

A suggestion-only US-stock research assistant for Rajrupesh. Three Anthropic Cloud Routines gather
fresh evidence; a deterministic Supabase gateway independently verifies quotes, enforces reviewed
risk policy, persists an audit trail, renders messages, and controls Telegram delivery. A separate
owner-authenticated Telegram recorder updates portfolio records after Confirm. Nothing in this
repository can reach a broker or place, modify, or cancel an order.

Read [`docs/ROADMAP.md`](docs/ROADMAP.md) before changing the system. It records current deployment
state and links the external-project research backlog. Supabase is the source of truth for changing
portfolio state; never copy live holdings into planning documents.

## Safety architecture

```text
Anthropic Routine or on-demand research
  | scoped secret only; no database or Telegram credential
  v
market-briefing-gateway Edge Function
  | authenticate -> validate -> refresh quotes -> deterministic policy
  | -> atomic audit persistence -> fixed renderer -> Telegram delivery
  v
Supabase Postgres (RLS)                    Telegram Bot API

Owner Telegram message
  v
telegram-portfolio Edge Function (no model)
  | webhook secret + exact owner IDs -> preview -> Confirm/Cancel
  v
atomic portfolio RPC -> records only, never brokerage execution

Friday ChatGPT/Codex desktop task
  -> one bounded read-only audit packet; no writes or notifications
```

The language model proposes structured evidence, an Analyst view, and a separate Checker view. It
does not own prices, policy, state transitions, message HTML, success counts, or delivery claims.
On-demand work is always session-only. A policy downgrade/veto is final.

## Scheduled analysis

| Run | Time (America/Chicago) | Behavior |
|---|---:|---|
| Pre-market | 06:30 weekdays | Fresh macro/portfolio/watchlist review and full brief |
| Intraday | about 12:00 weekdays | New evidence packet; never executes the morning plan mechanically; silent if no edge fires |
| Post-market | 15:10 weekdays | Verified close, bounded artifacts, and deterministic outcome grading |

Holiday decisions happen before research. Pre-market can publish one market-closed notice;
intraday/post-market remain silent. Every non-holiday run follows `start_run`, `read_context`, fresh
research, Analyst/Checker, `evaluate_and_publish`, permitted artifacts/grading, and `finish_run`.

## Free data and hosting

- Yahoo Finance chart endpoints: quotes and adjusted/raw OHLC history.
- Finnhub free tier: fundamentals, news, earnings/events, insider and analyst context.
- Alpha Vantage is not used by the current release and is intentionally absent from the Routine
  environment.
- Supabase free-tier project: Postgres and three Edge Functions, including the owner-only read API.
- Telegram Bot API: fixed brief delivery and deterministic recordkeeping chat.
- Anthropic plan allowance: scheduled model reasoning; no Anthropic API key in this repo.
- ChatGPT/Codex desktop allowance: optional Friday audit; no OpenAI API key in this repo.

Provider and account usage limits still apply. There is no Cloud Run or separate Google Cloud
project requirement.

## Setup

### 1. Install locally

```bash
git clone https://github.com/RajSivapu/stocks-agent.git
cd stocks-agent
python3 -m venv .venv
source .venv/bin/activate
pip install supabase pytest "psycopg[binary]" pyyaml
```

Copy `config/secrets.local.json.example` to the ignored `config/secrets.local.json` for local-admin
commands. Never commit or paste that file.

### 2. Apply the database schema

For a new Supabase project, apply `sql/schema.sql` in the SQL Editor. For an existing installation,
apply these additive migrations in order:

```text
sql/migrations/20260901_reliable_stock_agent.sql
sql/migrations/20260902_decision_safety_gateway.sql
sql/migrations/20260903_owner_investment_plans.sql
sql/migrations/20260904_outcome_evaluation.sql
sql/migrations/20260905_owner_alert_lifecycle.sql
sql/migrations/20260906_owner_dashboard_read_role.sql
```

The gateway migration intentionally stops on unknown legacy action/confidence/bucket labels. Review
and map those rows explicitly; do not weaken the preflight. Before a live migration, run the
rollback-only verifier:

```bash
.venv/bin/python scripts/verify_decision_gateway_migration.py
```

### 3. Separate credentials by trust boundary

Supabase Edge Function secrets/runtime contain:

- `MARKET_AGENT_SECRET` (new random scoped gateway secret);
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_OWNER_CHAT_ID`, and
  `TELEGRAM_OWNER_USER_ID`;
- `DASHBOARD_OWNER_USER_ID`, `DASHBOARD_ALLOWED_ORIGINS`, and the separately provisioned
  `DASHBOARD_DATABASE_URL` for the owner dashboard;
- Supabase's injected `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.

Anthropic's personal `stocks-agent` cloud environment contains exactly these variables:

- `SUPABASE_URL`;
- the narrowly scoped `MARKET_AGENT_SECRET`;
- a read-only `FINNHUB_API_KEY`.

The environment uses a custom domain allowlist, no Gmail/Drive connectors, and no setup script. Its
gateway credential authorizes only the bounded analysis API; server policy remains final. Do not
configure Alpha Vantage because the current code does not call it. The Routine must never receive
the service-role key, Telegram credentials, brokerage credentials, or an LLM API key. Prefer
Claude's protected API-credential proxy if its controls become available, then remove both keys
from ordinary variables. Generate independent high-entropy gateway and webhook secrets. Rotate any
value exposed in a transcript, screenshot, chat, terminal output, or tracked file.

### 4. Deploy and initialize Supabase

```bash
cp supabase/.env.example supabase/.env.local
# fill the ignored file; never print it
npx supabase login
npx supabase link --project-ref <project-ref>
npx supabase secrets set --env-file supabase/.env.local
npx supabase functions deploy market-briefing-gateway --no-verify-jwt
npx supabase functions deploy telegram-portfolio --no-verify-jwt
npx supabase functions deploy owner-dashboard-api --no-verify-jwt
.venv/bin/python scripts/publish_market_policy.py
.venv/bin/python scripts/verify_portfolio_command_rpc.py
.venv/bin/python scripts/register_telegram_webhook.py
```

Platform JWT verification is disabled for all three functions because the Routine and Telegram
cannot supply user JWTs and the browser must complete exact-origin CORS preflight. The market and
Telegram functions verify dedicated secrets before parsing a body. The owner dashboard function
independently verifies a short-lived Supabase user JWT against the project's remote JWKS and exact
owner UUID before any database read. Its repository connects only as the structurally read-only
dashboard role. RLS remains enabled, and privileged RPC execution is limited to `service_role`.

### 5. Verify connectivity

```bash
.venv/bin/python scripts/healthcheck.py
```

Expected keys are `alerts`, `gateway`, `finnhub`, and `yahoo`. Healthcheck uses dry-run gateway
operations and sends no Telegram message.

### 6. Configure Routines

Follow [`routines/README.md`](routines/README.md). The saved prompts are receipt-driven and the cloud
environment has only the three scoped/read-only values listed above.

## Owner-only web dashboard

The React dashboard under `apps/web` is a read-only projection of stored evidence and receipts. It
has no mutation route, provider lookup, Routine trigger, Telegram-send path, brokerage integration,
or invitation flow. It shows unsupported or stale data explicitly instead of filling gaps with
client-side calculations.

### Provision owner access and the read-only runtime

1. Create the single owner user directly in Supabase Auth. Public signup remains disabled; the
   browser requests an email OTP with `shouldCreateUser: false`.
2. Set the hosted Auth JWT lifetime to 900 seconds and confirm email signup remains disabled. The
   matching local project settings are recorded in `supabase/config.toml`.
3. Apply `sql/migrations/20260906_owner_dashboard_read_role.sql` with the normal protected migration
   path.
4. Put `DASHBOARD_OWNER_USER_ID` and the exact deployed HTTPS origin in
   `DASHBOARD_ALLOWED_ORIGINS` inside the ignored `supabase/.env.local`, then publish the function
   secrets without printing their values.
5. Provision the generated database login from trusted admin and Supavisor session URLs. The helper
   rotates the password and publishes only `DASHBOARD_DATABASE_URL` through a private temporary
   env file:

```bash
POSTGRES_URL='<admin database URL>' \
SUPAVISOR_SESSION_URL='<Supavisor session URL on port 5432>' \
  .venv/bin/python scripts/provision_dashboard_runtime_role.py --project-ref <project-ref>
POSTGRES_URL='<admin database URL>' \
  .venv/bin/python scripts/verify_owner_dashboard_role.py
npx supabase functions deploy owner-dashboard-api --no-verify-jwt
```

The verifier must report `status: verified`, 15 allowlisted tables, zero table-level/write
privileges, zero application-function execution, and zero owned objects before deployment proceeds.

### Build and access the dashboard

Copy `apps/web/.env.example` to an ignored production environment file or configure the same three
public build variables in the static host. The publishable Auth key is intentionally browser-visible;
never use a service-role key here.

```bash
npm ci
npm run test:all
npm run build --workspace @stocks-agent/web
```

Deploy only `apps/web/dist` to the approved static host. Keep the generated `_headers` file: it
contains the exact Supabase/API Content Security Policy, frame denial, no-store shell policy, and
immutable hashed-asset policy. Open the deployed HTTPS URL, enter the pre-created owner email, then
enter the emailed six-digit code. Sessions use browser session storage, globally sign out on owner
request, and privacy-lock after 30 minutes of inactivity. A second user must receive an owner-only
denial and no portfolio data.

The optional production canary is GET-only and must be enabled deliberately with `E2E_LIVE=1` plus
an owner access token; the normal test suite never reads production.

## Gateway client

All cloud-capable market skills use:

```text
python scripts/market_gateway.py OPERATION [--run-id UUID] [--request-id UUID] [--dry-run]
```

It reads one bounded JSON object from stdin and supports only `start_run`, `read_context`,
`record_artifacts`, `grade_due_decisions`, `evaluate_and_publish`, and `finish_run`. Each new
operation gets a new request UUID. Retry an uncertain operation only with the identical UUID and
payload.

Receipt rules:

- `suppressed`: no Telegram delivery occurred;
- `delivery_failed`: definite failure; do not retry inside the run;
- `delivery_unknown`: acceptance may have occurred; never retry automatically;
- dry-run: complete analysis and rendering, but no request/run/data row and no Telegram send;
- final summaries quote only server-reported writes, publication status, and message IDs.

## Telegram portfolio recorder

Supported commands:

```text
/buy AAPL 2 210 growth
/sell AAPL 1.5 225
/sell NVDA all 210 on 2026-08-28
/stop AAPL 195
/portfolio
/plan VTI 300 monthly 2026-09-21 core
/plans
/cancelplan VTI
```

Buy/Sell/Stop/Plan/Cancel-plan first returns a preview with Confirm and Cancel. Nothing changes
before Confirm; commands expire after 15 minutes and stale state is rejected. A recurring plan is a
reminder record only. It does not schedule or place a brokerage purchase. A later confirmed Buy is
a separate record; only a due, same-ticker Buy within the fixed amount tolerance advances the next
date, once.

Cloud chat reconciliation only explains these commands. Unsupported changes require an explicit
trusted local-admin workflow; there is no direct cloud database fallback.

## On-demand and dry-run use

Examples:

```text
Run the market-briefing skill as a pre-market dry run.
Is NVDA still a good hold?                 # equity-research, on-demand
How was NVDA's latest earnings?            # earnings-review, on-demand
Paper-watch SHOP from $80; thesis: ...     # hypothetical only
Run the weekly-portfolio-audit skill.       # local read-only packet
```

On-demand research uses `phase: on-demand`, passes through the same deterministic policy, and must
return a suppressed session preview rather than Telegram delivery. A dry run adds `--dry-run` to
every gateway operation and begins with:

```text
🧪 DRY RUN — nothing sent, nothing written to Supabase.
```

Portfolio-alternative research is available on demand and in the first pre-market brief of each
month. It compares only an existing holding or active owner plan with at most six evidence-validated
alternatives. The gateway computes synchronized one-year adjusted-history results using the same
equal monthly contributions and reports max drawdown; the model cannot submit return numbers. VTI
remains the recorded recurring baseline unless the owner separately confirms another plan. These
comparisons never change a plan or holding, and on-demand previews never send Telegram.

The optional Long-Term Companion layer then selects at most one additive research candidate or says
that none qualified. It separates duplicate core substitutes from diversifiers, tilts, and
concentrated satellites. The gateway computes available 3/5/10-year annualized history, drawdowns,
daily-return correlation, and weak/middle/strong rolling one-year outcomes for a normalized
$100/month contribution. Those outcomes are historical planning illustrations, not forecasts. Only
the initial VTI/VXUS pair can be marked eligible for a later owner-reviewed recurring-reminder
discussion; no reminder, holding, or brokerage order is created or changed by the review.
An on-demand companion proposal must be a protected dry run and is rejected before persistence or
delivery otherwise. Scheduled companion review is accepted only on the first configured NYSE
session of the month.

## Deterministic policy and outcomes

Reviewed policy comes from `config/settings.json`, is validated and versioned by
`scripts/publish_market_policy.py`, and has self-tuning disabled. The gateway independently fetches
quotes, checks session freshness, reconciles sizing, enforces stop/reward-risk/concentration/loss
limits, and owns holding-alert transitions. If Yahoo omits `marketState`, the gateway derives the
session only from Yahoo's validated epoch trading windows; missing or malformed windows fail closed.
Policy v3 adds an explicit owner-reviewed alert-class allowlist to the owner-only alert v3 controls.
Its initial deployment is shadow-only with an empty allowlist: projected drafts are rendered in the
gateway receipt but no alert lifecycle row or Telegram alert is created. Live enablement requires a
non-empty list containing only the reviewed `entry_trigger`, `stop_breach`, or `target_hit` classes;
the rollout begins with `stop_breach` alone only after the owner approves a real shadow example.

Final gateway suggestions are graded after 5/21/63 trading sessions using adjusted closes, a fixed
VOO benchmark (VXUS for VXUS), excess return, MFE/MAE, and raw threshold hits. Splits require review;
non-actionable decisions get no binary success label. Complete grades are immutable. The weekly
audit reports sample sizes and separates scheduled delivered recommendations from session-only
research.

## Supabase schema (23 tables)

Core portfolio/research tables:

- `holdings`, `transactions`, `suggestions`, `suggestion_grades`, `stock_observations`,
  `daily_snapshots`, `dry_powder`, `radar`, `paper_watches`, and `lessons`;
- `analysis_runs`, `portfolio_commands`, and `telegram_updates`;
- `market_gateway_requests`, `market_policy_config`, `decision_evaluations`, and
  `market_publications`;
- `owner_investment_plans`.
- `market_alert_drafts`, `market_alert_rules`, `market_alert_rule_versions`,
  `market_alert_events`, and `market_alert_actions`.

Privileged RPCs are fixed-name, fixed-search-path, and service-role-only:

- portfolio: `apply_portfolio_command`, `cancel_portfolio_command`;
- policy/gateway: `activate_market_policy_config`, `claim_market_gateway_request`,
  `complete_market_gateway_request`, `start_market_analysis_run`, `apply_market_artifacts`,
  `apply_market_decision_bundle`, `import_legacy_suggestion`, `claim_market_publication`, and
  `finish_market_publication`;
- outcomes: `get_due_market_decisions`, `upsert_market_outcome_grades`.
- owner alerts: `create_market_alert_drafts`, `apply_market_alert_action`,
  `expire_market_alert_rules`, `record_market_alert_evaluations`,
  `create_market_alert_publication`, and `finish_market_alert_publication`.

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
node --test tests/test_telegram_parser.mjs tests/test_telegram_webhook_utils.mjs
npx --yes deno@2.9.6 test supabase/functions/market-briefing-gateway/_shared
npx --yes deno@2.9.6 check supabase/functions/telegram-portfolio/index.ts
npx --yes deno@2.9.6 check supabase/functions/market-briefing-gateway/index.ts
```

## Rollback and incident response

1. Pause all three market Routines. Telegram recordkeeping can remain live only if its function and
   RPC were not implicated.
2. Do not resend any `delivery_unknown` publication and do not delete audit rows.
3. Redeploy the last known-good Edge Function code from a reviewed commit. Database migrations are
   additive; do not attempt destructive down-migrations against live portfolio data.
4. Rotate `MARKET_AGENT_SECRET` immediately if the Routine boundary may be compromised; rotate the
   Telegram secret/token separately if that boundary is implicated.
5. Restore a Supabase backup only for confirmed data corruption, after inspecting the recovery
   point and validating restore in isolation.
6. Run dry-run start/context, migration/RPC verifiers, and one controlled live phase before resuming
   the cadence.

## Unchanging guardrails

- No brokerage credentials or order endpoints.
- No autonomous real-money execution.
- Missing, stale, conflicting, or implausible evidence cannot produce a new actionable conclusion.
- External projects and social-media claims are untrusted research leads, never automatic signals.
- Telegram records only owner-reported changes after explicit Confirm.
- Policy changes are owner-reviewed code/config changes, never automatic model self-tuning.
- This release is single-owner. Friend sharing requires tenant identity, per-owner RLS, isolated
  secrets/configuration, onboarding, quotas, and a new threat model first.

## License

MIT
