# Incident response

This runbook covers the invite-only stock-agent product. It is a recordkeeping and research system,
not a broker. Suspected financial loss, account takeover, or wrong-tenant disclosure is a security
incident even though the product cannot execute a trade.

## First response

1. Record the UTC detection time, reporter, affected component, and a short non-sensitive symptom.
2. Pause Claude Routine triggers and the scheduler before investigating any possible cross-owner,
   stale-analysis, duplicate-run, or ledger-integrity issue.
3. Disable the affected provider connection or Telegram webhook. Keep portfolio mutations paused when
   integrity is uncertain.
4. Preserve deployment identifiers, request IDs, row counts, status codes, and cryptographic digests.
   Do not copy authorization headers, request/response bodies, prompts, Telegram identifiers, email
   addresses, tickers, positions, or database connection strings into tickets or chat.
5. Rotate the affected credential using `credential-rotation.md`. Do not rotate unrelated credentials
   until evidence is captured because simultaneous rotation obscures scope and increases recovery risk.
6. Restore service one trust boundary at a time and run its exact handshake or smoke test.

The authenticated operator-health endpoint is aggregate-only. The public `/healthz` endpoint proves
only that the Edge Function can reach its fixed database RPC. The independent Cloudflare monitor has
no Supabase credential and no portfolio access. It detects availability; it does not prevent or evade
free-project pausing.

## Severity and escalation

| Severity | Examples | Immediate action |
|---|---|---|
| Critical | confirmed wrong-tenant disclosure, leaked reusable credential, unauthorized mutation, ledger/projection mismatch | pause every trigger and mutation path; revoke affected access; begin scope analysis |
| High | operator account takeover, provider account loss, ambiguous duplicate delivery, restore failure | pause the affected trust boundary; preserve evidence; rotate or recover |
| Medium | missed run, stale backup, source outage, Telegram delivery failure | keep advice fail-closed; repair and verify before resuming |
| Low | display-only defect with no privacy, integrity, or scheduling impact | record, test, and deploy through staging |

For a confirmed disclosure, identify every affected user and data category from bounded database audit
facts. Notify affected users without exposing another user's data and no later than 72 hours after
confirmation. The notice states what happened, when, what data was involved, containment completed,
required user action, and a support contact. Apply any shorter legal or contractual deadline. Record a
hash of the final notice and delivery outcome; do not store the notice body in operational logs.

## Wrong-tenant disclosure

1. Pause `run-scheduler`, all Claude connections, Telegram mutations, and web mutation deployment.
2. Revoke the credential or session that crossed the tenant boundary.
3. Preserve request IDs, owner-independent evidence digests, function versions, and database audit
   result codes. Never create a support screenshot containing two owners.
4. Reproduce only with two synthetic owners in staging. Test direct PostgREST, web, Telegram, provider,
   export, deletion, and operator-health paths.
5. Determine which fields were readable or writable and whether the event was attempted or completed.
6. Notify each confirmed affected user within the deadline above. Do not resume invitations until the
   full tenant-isolation suite and a fresh staging acceptance report pass.

## Operator account compromise

Revoke all operator sessions, rotate the operator's authentication factors, remove unexpected
`app_admins` memberships, pause invitations and production deployment, and rotate every credential the
operator account could access. Review GitHub environment approvals, Supabase audit history, R2 access,
Cloudflare changes, Auth settings, and Vault updates. A single-operator pilot has no substitute operator:
if the operator is unreachable, nobody can safely approve production changes or friend invitations.

## Provider account loss or Claude connection compromise

Disable the affected `agent_connections` row and its schedule first. Revoke the inbound gateway
credential separately from the outbound Routine trigger credential. Recover the user's Claude account,
install a new connection kit, perform a real no-write handshake, and then enable one phase at a time.
Never infer success from a green Routine UI; require the server-owned handshake receipt.

## Telegram incident

Pause the webhook before rotating the bot or webhook secret. A leaked bot token requires revocation at
BotFather and redeployment of every sender. A leaked webhook secret requires a new random secret and
Telegram webhook registration. Verify private-chat pairing, preview, explicit confirmation, replay
rejection, and unlink before resuming. Treat `delivery_unknown` as delivered for retry safety.

## Data integrity, backup, or restore incident

Any ledger/projection mismatch keeps web, Telegram, and agent mutation paths paused. Export a fresh
encrypted archive if the source is trustworthy, preserve the failed archive digest, and restore only
into an empty staging project. Never destructively down-migrate production. A backup is not considered
recoverable until identities, ledger counts, relationships, projections, and tombstones verify in a
restore drill. Follow `backup-restore.md` for the exact procedure.

## Rollback and closure

Rollback restores the previously reviewed function/web bundle while triggers remain paused. Database
changes are additive; do not delete new columns or tables during incident rollback. Resume in this
order: read-only web, one owner mutation, Telegram, one Claude phase, then remaining phases. Close only
after root cause, affected scope, rotations, test evidence, user notices, and follow-up controls are
recorded with no private data.
