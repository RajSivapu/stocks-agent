# Stock Agent privacy and data lifecycle

Last updated: 2026-09-03

Stock Agent is an invite-only, suggestion-only portfolio research and recordkeeping product. It does not request brokerage credentials and cannot place, modify, or cancel brokerage orders.

## Data handled

The service stores your account profile, consent receipts, notification and schedule settings, portfolio transaction ledger and derived holdings, recurring-investment plans, research evidence and recommendations, run status, provider-connection status, and Telegram delivery state. Reusable provider credentials and raw Telegram identifiers are excluded from user exports. Secrets are stored only in server-side secret storage or as one-way digests.

The operator can access production records and encrypted recovery archives when necessary to operate, secure, restore, export, or delete the service. Access must use the documented operator procedures and must not be performed through the public web application.

Each owner uses a **separate provider account**, environment, Routine, and credentials. Owners are not
batched into provider prompts or support evidence. The operator can access only what is necessary for
operation and incident response; this is a technical capability, not permission to inspect a
portfolio casually. Stock Agent does not sell portfolio or identity data or use it for advertising.

## Other services

- Supabase hosts authentication, database records, server functions, schedules, and server-side secrets.
- The connected analysis provider receives the bounded portfolio and research context needed for a run. Provider transcripts also exist in your provider account. Stock Agent cannot delete or change those transcripts; the provider's retention settings and policies apply.
- Market-data and research vendors receive requested ticker symbols and related market-data queries. They do not receive your Stock Agent identity merely because a ticker is requested.
- Telegram receives bot messages and the identifiers needed to deliver them. During account deletion, Stock Agent attempts to delete up to 100 eligible bot messages from the preceding 48 hours. Telegram may reject a deletion, and older chat history must be removed manually by you. Telegram may retain data under its own policies.
- Cloudflare serves the static web application. Portfolio data is fetched directly from authenticated Supabase endpoints and is not embedded in the static site.

## Export, retention, and deletion

You can download a JSON account export and an immutable-ledger CSV from Settings. Exports are generated for the signed-in owner, are limited to 5 MiB, and are returned with no-store response controls.

Confirming account deletion immediately disables provider triggers, schedules, notifications, normal application access, and the Telegram link. It cancels unconfirmed portfolio commands and starts a 72-hour cancellation window. After that grace period, active account data is purged and the Auth identity is deleted no later than seven days after the original request. Canceling deletion does not restore revoked credentials; provider and Telegram connections must be created again.

Encrypted recovery archives can retain deleted data for at most 35 days. Restore procedures must apply deletion tombstones before owner data, so a deleted account is not resurrected. The minimal tombstone contains only the former owner UUID, deletion-request UUID, deletion time, and archive-expiry time; it is retained to enforce that guarantee.

Failed or unidentified Telegram pairing-attempt receipts are not associated with an owner and are removed by the service retention process. Operational logs must not include request bodies, financial values, reusable credentials, email addresses, or raw Telegram identifiers.

## Your responsibilities

Keep your email, Telegram, and separate provider account secure, review exports before relying on
them, and remove older Telegram messages manually when desired. Never send the operator your OTP or
provider credential. Contact the service operator through the invitation channel for privacy
requests that cannot be completed in the application.
