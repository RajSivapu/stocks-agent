# Deployment rollback

Rollback restores a reviewed application bundle; it does **never destructively down-migrate** the
database. All schema changes through the owner launch are additive and remain in place so ledger and
audit history are not discarded.

1. Keep **triggers remain paused**: disable scheduler calls, Run Now, new connection handshakes, and the
   Telegram webhook mutation route.
2. Confirm the exact 40-character **rollback reference** recorded by the production gate. It must differ
   from the failed deployment and resolve to a reviewed commit.
3. Rerun the protected production workflow for that reference, without reversing migrations. Deploy the
   previous four function bundles and static assets, then rotate runtime login credentials.
4. Run public health, owner-only read smoke, ledger/projection checks, and tenant-isolation probes. Do not
   perform a portfolio write while integrity is uncertain.
5. Resume one path at a time only after the incident owner signs off: web reads, web recordkeeping,
   Telegram recordkeeping, then scheduled phases. Keep friend invitations disabled through the next
   complete market cycle.

If additive schema is incompatible with the previous application bundle, restore the database into an
isolated staging project from the encrypted archive and produce a corrected forward migration. Never
overwrite production with an unverified restore and never delete evidence to make a rollback appear
clean.
