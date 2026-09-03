# Gate 0 Capability Proofs

Gate 0 proves the external services required by the complete core product. It never uses production
portfolio data. A green provider dashboard is not evidence that the application handshake succeeded.

Copy `config/capabilities.local.json.example` to the ignored
`config/capabilities.local.json`. Store secret values only in the ignored copy or provider secret
stores. Evidence files and screenshots stay under ignored `artifacts/capabilities/`.

## Credential incident prerequisite

The ignored local Codex configuration contained live-looking Finnhub and Alpha Vantage credentials,
and an inspection surfaced them in a task transcript. Before Gate 0 can pass:

1. Revoke/regenerate both credentials in their provider dashboards.
2. Replace values only in ignored local/provider configuration.
3. Do not paste either old or new value into chat, Git, screenshots, or command output.
4. Run `git grep -n -E '(FINNHUB_API_KEY|alphavantage|api[_-]?key)'` and inspect matches without
   printing ignored files.
5. Run a repository-history secret scanner and retain only its redacted pass/fail report.

## Required checks

1. `smtp_phone_otp`: configure custom SMTP on a verified operator-controlled domain. Request and enter
   a six-digit OTP from a non-team address using an iOS or Android mail client. Supabase's default
   mailer is not accepted.
2. `claude_fire`: use one unscheduled cloud Routine with an API trigger. The probe sends only an opaque
   UUID in the documented `text` field to the exact
   `https://api.anthropic.com/v1/claude_code/routines/trig_.../fire` URL. It uses the
   `experimental-cc-routine-2026-04-01` beta header and stores no token/session URL in evidence.
3. `claude_gateway_callback`: the fired Routine must call the staging gateway with the matching probe
   UUID and finish a zero-write handshake. A provider green state alone does not pass.
4. `supabase_cron_pg_net`: a staging Cron tick invokes a synthetic no-write Edge endpoint and records
   exactly one receipt.
5. `supabase_asymmetric_jwt`: staging accepts its own current user JWT and rejects forged,
   wrong-project, expired, and wrong-algorithm tokens.
6. `supavisor_machine_login`: a restricted test login reaches its one RPC over the IPv4 session-mode
   pooler and cannot select a base table.
7. `supabase_pause_policy`: record the current official free-project pause policy and prove the
   independent monitor reports unavailability without generating keep-alive traffic. `pg_cron`
   activity is not assumed to prevent pausing.
8. `r2_age_roundtrip`: encrypt random bytes with the offline `age` public key, upload ciphertext to a
   private R2 probe key, download/decrypt/compare SHA-256, then delete that exact key. Never upload
   plaintext or a GitHub artifact.
9. `independent_backup_alert`: make the probe object older than 36 hours in a test prefix and observe
   the independent Cloudflare monitor's fixed Telegram operational alert.
10. `corporate_action_source`: test Finnhub's split endpoint using the actual account entitlement. If
    unavailable, mark this check failed; the product must quarantine suspected corporate actions.

## Running the harness

Validate configuration without making network calls:

```bash
.venv/bin/python scripts/probe_release_capabilities.py \
  --config config/capabilities.local.json --check-config
```

Trigger only the synthetic Claude probe:

```bash
.venv/bin/python scripts/probe_release_capabilities.py \
  --config config/capabilities.local.json --probe-claude-fire
```

Record a manual result by hashing, but not copying, its evidence file:

```bash
.venv/bin/python scripts/probe_release_capabilities.py \
  --config config/capabilities.local.json \
  --record smtp_phone_otp --status passed --code OTP_PHONE_VERIFIED \
  --evidence artifacts/capabilities/manual/smtp-phone.txt
```

Assemble the report:

```bash
.venv/bin/python scripts/probe_release_capabilities.py \
  --config config/capabilities.local.json --assemble-report
```

Gate 0 passes only when `report.json` says `passed: true` and all ten checks are current. Missing,
failed, incomplete, or stale evidence blocks production activation and friend onboarding. It does not
authorize substituting a paid model API, public backup, broad database credential, or always-on Mac.

## Primary references

- [Anthropic Routines and API trigger](https://code.claude.com/docs/en/routines)
- [Supabase custom SMTP](https://supabase.com/docs/guides/auth/auth-smtp)
- [Supabase scheduled Edge Functions](https://supabase.com/docs/guides/functions/schedule-functions)
- [Supabase free-project pausing](https://supabase.com/docs/guides/platform/free-project-pausing)
- [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/)
