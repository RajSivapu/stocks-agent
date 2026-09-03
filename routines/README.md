# Claude Routine adapter setup

The V2 provider contract is neutral, but release one supports Claude Routines only. Configure **one
unscheduled Routine per owner**. The **application owns every market schedule**, computes New York
holidays/DST/early closes, creates canonical run slots, and fires the Routine through its API trigger.
Do not create separate morning, midday, or evening provider schedules.

Use the screenshot-level [Claude connection kit](../docs/connection-kits/claude-routine-v1.md) for the
reviewed prompt and exact UI sequence. This file is the operator checklist.

## Required owner boundary

Each owner needs an eligible, separate Claude account. Provider runs consume that account's
subscription usage and **daily Routine allowance**. A friend must never reuse the operator's account,
environment, Routine, inbound credential, outbound trigger token, or transcript.

The connected environment must have:

- custom network access limited to the Stock Agent project host and reviewed public research hosts;
- keep **environment variables empty**;
- keep **setup script empty**;
- **connectors off**;
- unrestricted branch pushes off; and
- provider **schedule off**, with only the API trigger enabled.

The repository and Claude's GitHub proxy provide the checked-in client. The Routine does not need a
database URL, Supabase key, Telegram credential, market-data key, broker credential, or LLM API key.

## Two credentials, opposite directions

1. Stock Agent displays `connection_id.secret` once. Save it only as Claude's **host-bound API
   credential** for the exact Supabase project host. Claude cannot read it back; Stock Agent stores
   only its SHA-256 digest.
2. Claude displays the Routine `/fire` URL and trigger token once. Paste both into Stock Agent during
   fresh-OTP setup. The application validates the exact Anthropic host/path and stores the token in
   Supabase Vault for the scheduler role only.

Neither credential is a model API key. Neither can be substituted for the other. Never put either in
environment variables, source control, chat, screenshots, shell history, or application logs.

## Real handshake

Use **Test connection** in Stock Agent. Do not use curl or provider Run Now, because only an
application-fired run proves the outbound trigger and inbound callback together. The handshake uses
an empty synthetic portfolio, checks the V2 contract and allow-listed source connectivity, permits no
domain writes/publications/messages, and must return a challenge-bound callback receipt.

**Green provider status is not proof.** The connection remains `testing` until the application sees
the exact successful callback with zero side effects. The owner must then explicitly activate the
`ready` connection. A rejected, unknown, timed-out, stale, or allowance-limited trigger stays disabled.

## Runtime behavior

Every trigger contains only an opaque request UUID and fixed untrusted-input wording. The Routine
invokes `scripts/agent_gateway_v2.py`, uses the server-owned run/phase/context, gathers fresh evidence,
submits separate Analyst and same-model Checker records, and treats policy/persistence/publication
receipts as final. It cannot call base tables or Telegram and cannot edit/push the repository.

Intraday performs new research and never copies the morning conclusion. On-demand output is
session-only. Quiet intraday is silent. The server owns all message text, write counts, message IDs,
and delivery classification.

## Pause, revoke, and rotate

Pause the application schedule before maintenance or rotation; provider schedule stays off. Revoke in
Stock Agent first so the active schedule is cleared, inbound digest is invalidated, and Vault trigger
secret is deleted. Then delete the Claude API credential and API trigger token and archive the
Routine/environment if unused.

Rotation always creates a new connection, new inbound credential, new trigger token, and fresh real
handshake. Never reactivate a revoked connection or reuse a credential. If usage is constrained,
disable intraday first; never remove fresh evidence, Checker, deterministic policy, or receipt checks.
