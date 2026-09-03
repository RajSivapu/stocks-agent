# Credential rotation

Rotate one trust boundary at a time, with triggers and mutations paused. Store credentials only in the
owning platform's secret manager. Never paste a live value into source, CI output, screenshots, support
chat, or a command argument that will be logged. Use generated high-entropy values and record only the
rotation time, credential name, version label, and verification result.

## Rotation matrix

| Credential | Stored/used by | Rotation procedure | Verification |
|---|---|---|---|
| Claude inbound gateway credential | one user's Routine and its hashed `agent_connections` record | revoke the connection, create a replacement credential in the web flow, replace only that user's Routine secret | real no-write handshake; old credential receives a denial |
| Claude outbound trigger URL/token | Supabase Vault and scheduler | disable the connection/schedule, create a new Routine trigger, submit it through step-up-protected handshake, delete the old Vault secret after cutover | one handshake slot, matching trigger request ID, then one enabled phase |
| Telegram bot token | Telegram plus Telegram, scheduler, agent, health-monitor, and backup-monitor senders | pause webhook/senders, revoke and regenerate with BotFather, replace each platform secret, redeploy | `getMe`, webhook registration, private test message, and no delivery from old token |
| Telegram webhook secret | Telegram registration and `telegram-portfolio` | generate a new random value, update Edge secret, register webhook with the new secret, remove old registration | old header denied; synthetic update with new header accepted once |
| Custom SMTP password/API key | Supabase Auth SMTP settings | issue provider credential scoped to sending, update Auth configuration, revoke old credential | OTP delivered to a non-team phone mail client; old credential cannot send |
| R2 access key | private backup workflow | create a token scoped only to the private backup bucket, update private-repo secrets, run backup and HEAD verification, revoke old token | archive and key-material objects present; age monitor can list only its bound bucket |
| Age recovery identity/recipient | offline operator store and private backup workflow | create a new identity offline, back up the private identity in two controlled offline locations, update recipient, produce a new full archive, retain old identity until its last archive expires | decrypt and restore the new archive into staging |
| Runtime database login | one of gateway, scheduler, Telegram, or backup Edge/workflow environments | create a replacement login mapped to exactly one NOLOGIN capability role, update only that runtime secret, restart/deploy, revoke old login | expected RPC succeeds; table reads and all other machine RPCs remain denied |
| Scheduler webhook secret | protected scheduler caller and `run-scheduler` | pause scheduler, generate/update both ends, revoke old value | wrong value denied before body parsing; one duplicate-safe tick succeeds |
| App rate-limit/step-up/pairing peppers | app-api or Telegram runtime plus offline recovery material | rotate only during a planned session invalidation; invalidate outstanding challenges/codes and update encrypted continuity material | old challenges/codes fail; new flows complete |
| Evidence signing key | agent gateway plus offline recovery material | treat as a versioned cryptographic migration; retain verification-only access to prior public/version metadata and never overwrite without a reviewed migration | old evidence remains verifiable and new evidence verifies under the new version |
| Supabase publishable key | static web and app-api's PostgREST client | create replacement public key, deploy staging then production, revoke old key after active sessions are tested | OTP/authenticated API and public health pass; no secret key exists in web output |
| Staging-only Supabase service-role key | invitation/recovery operator jobs only | rotate in the dashboard, update protected staging environment, revoke old key | synthetic invitation or restore identity operation succeeds only in staging |

The production runtime must not contain a Supabase service-role key. User invitations and Auth identity
recreation are operator jobs, never browser or general Edge capabilities.

## Runtime database role procedure

Run the reviewed role-provisioning script against the target environment to create a new login for one
capability role. Put its session-mode Supavisor URL into the corresponding secret (`AGENT_DATABASE_URL`,
`SCHEDULER_DATABASE_URL`, `TELEGRAM_DATABASE_URL`, or `BACKUP_DATABASE_URL`). Deploy and execute the
role's positive and negative privilege tests. Revoke and drop only the old login after verification;
never grant it membership in a second capability role.

## Emergency order

For a credential confirmed public, revoke first even if availability is lost. Otherwise: pause,
capture non-sensitive evidence, issue replacement, deploy, verify, then revoke old. If any test is
ambiguous, keep the boundary paused and use the incident-response runbook.

After rotation, update the encrypted key-material archive where the recovery runbook requires it. A
credential value itself never belongs in the rotation receipt.
