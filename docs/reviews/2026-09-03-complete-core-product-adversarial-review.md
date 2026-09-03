# Adversarial Review: "Complete Core Product: Multi-User, Multi-Provider Stock Agent"

**Reviewed document:** `docs/superpowers/specs/2026-09-02-complete-core-product-design.md` (draft dated 2026-09-02)
**Review date:** 2026-09-03
**Reviewer stance:** principal security architect / financial-systems engineer / adversarial product reviewer
**Supporting context read:** `README.md`, `docs/ROADMAP.md`, `docs/superpowers/specs/2026-09-02-decision-safety-gateway-design.md`

Every platform claim below is labelled **[Verified]** (read today in the vendor's official documentation, link given), **[Inferred]** (follows from verified facts but not stated by the vendor), or **[Unverified]** (I could not confirm it today; treat as an assumption that needs a spike).

---

## A. Executive verdict

**Verdict: major redesign required in two areas (provider onboarding and recovery); the rest is "ready with changes."**

The security posture of the document is unusually good for a draft: owner-derived identity, no browser secrets, fixed RPCs, fail-closed evidence, exactly-once publication. The tenancy plan is sound in shape. What is not sound is the set of assumptions about the world around the design: how friends' subscriptions can actually reach the gateway, how Supabase Free actually delivers email, and whether the proposed backup is recoverable by one person in one business day. Those are not polish items; two of them make the release-one promise ("each user connects their own Claude or ChatGPT account, no API spend, no always-on Mac") false as written.

The five most important conclusions:

1. **The ChatGPT connector as specified cannot exist without an OAuth 2.1 authorization server and a hosted MCP server.** The document's `connection_id.secret` static bearer credential (§6.3) matches Claude Routines, but OpenAI's current plugin platform authenticates MCP servers with OAuth 2.1 (dynamic client registration, resource-server token verification), and classic ChatGPT scheduled tasks only reach Gmail/Slack/GitHub connectors. "Plugin + scheduled task" is therefore a second product (MCP server + OAuth AS + plugin listing), not an adapter. It must leave release one or be re-scoped as its own gated project. **[Verified]**

2. **Friend onboarding through Supabase's built-in email is impossible, not merely rate-limited.** Supabase's default email service sends only to addresses of the project's organization team members and is capped at 2 messages/hour. Invite-only magic-link onboarding (§4.1) requires custom SMTP before the first friend, which the document lists as a "potential future cost" (§21). Custom SMTP (plus, in practice, a verified sending domain) is a release-one prerequisite. **[Verified]**

3. **Each friend must independently build a Claude Code cloud environment (custom network allowlist, an API credential, a routine per phase, repository access) on a Pro/Max plan, in a research-preview feature with a per-account daily run cap that draws down their own subscription usage.** The document treats this as "install the task in the provider's supported interface" (§9.3). It is a 30–60 minute technical setup per person with several silent-failure modes (a green run status does not mean the task succeeded). The product must either ship a verified, screenshot-level connection kit and a server-side handshake that proves each of these steps, or accept that release one is Raj-plus-one-technical-friend. **[Verified]**

4. **The backup plan is not recoverable within the stated RTO and has two silent-failure modes.** The repository is public; GitHub disables scheduled workflows in public repositories after 60 days without repository activity, and artifacts of a public repository are downloadable by anyone who can read the repository. A restore also requires the token-digest pepper and the migration of every friend's routine endpoint/token to a new project URL, neither of which the plan backs up or budgets time for. Move backups to a private location (Cloudflare R2's free tier, or a private repo), back up the pepper, and rewrite the RTO. **[Verified for GitHub/R2 facts; Inferred for restore-path consequences]**

5. **The ledger and run-lifecycle designs are one invariant short of correct each.** Holdings must be a deterministic projection of the transaction ledger (otherwise corrections drift the two apart), the first Buy of a ticker has no row to lock (concurrent first buys race), fills with fees will fail the amount×quantity reconciliation unless fees are a first-class field, and there is no "at most one active run per (owner, market date, phase)" claim, so a routine's "Run now" plus its schedule can produce two persisted analyses and two alert-state transitions for one phase. All four are one-paragraph fixes but all four are release blockers because they corrupt the only data the user cannot recreate.

Also worth saying plainly: tenant isolation as designed protects friends from each other. It does not protect them from the operator (Supabase dashboard, service role, backup private key) or from their own provider (the routine transcript on Anthropic's servers contains their portfolio context). Both are acceptable for an invite-only pilot among friends, but both must be written into the consent screen, not implied away.

---

## B. Blocking findings

Severity scale: **Critical** = release cannot ship or promise is false; **High** = a realistic single failure exposes or corrupts a user's data or money-relevant advice; **Medium** = operational failure with recovery path; **Low** = hygiene.

### B1. ChatGPT connection mode does not match OpenAI's platform — Critical

- **Section:** §5 (diagram: "ChatGPT web scheduled task + stock-agent plugin"), §6.3, §9.1–9.3, §22 Gate C, §24 decision 4.
- **Failure scenario:** The team builds the static `connection_id.secret` gateway and asks a friend on ChatGPT Plus to "install the plugin and schedule a task." OpenAI's help article on classic ChatGPT scheduled tasks names only Gmail, Slack, and GitHub as connectable accounts and gives no path to a custom MCP server; the only documented route for plugins in scheduled tasks is ChatGPT Work (web) or the desktop app. ChatGPT Work scheduled tasks can use plugins, but a plugin is an MCP server at a public HTTPS endpoint, and OpenAI's plugin authentication guide specifies OAuth 2.1 with the server verifying issuer/audience/expiry/scopes on every request, not a user-pasted static bearer secret. There is no place in the ChatGPT UI for the friend to paste `connection_id.secret`.
- **Consequence:** Gate C's "ChatGPT connector spike either passes and ships as experimental… or is visibly unavailable" will land on "unavailable" after real engineering time, and the product's headline claim ("connect your own Claude or ChatGPT") is misleading to friends who only have ChatGPT.
- **Design change:** Remove ChatGPT from the release-one contract. Re-scope it as a separate post-launch project with its own design: a streamable-HTTP MCP server (an Edge Function can host it) protected by Supabase Auth's OAuth 2.1 server (which supports PKCE and dynamic client registration and is explicitly aimed at MCP clients), a plugin manifest, a ChatGPT Work scheduled task, and an end-to-end unattended proof on a Plus account. Keep `agent_connections.provider` extensible but make `claude` the only allow-listed value in release one. Replace §9.1's "three modes" with two credential types: **static scoped token** (Claude Routine API credential) and **OAuth 2.1 access token** (future MCP hosts). The gateway must accept both in the same handler only after the OAuth path has its own review.
- **Gate/test:** Release one contract test asserts that selecting `provider = chatgpt` is rejected server-side. A ChatGPT spike is complete only when a ChatGPT Work scheduled task, on the target plan, with no desktop app running, calls the MCP server via OAuth and the server receives a receipt with the run's owner resolved from the OAuth token subject.
- **Sources:** [OpenAI Help: Scheduled tasks in ChatGPT](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt) (connected accounts limited to Gmail/Slack/GitHub; 3/5/10/15 active tasks by plan); [ChatGPT Learn: Scheduled tasks](https://learn.chatgpt.com/docs/automations) ("Scheduled tasks created with ChatGPT Work on the web… can use plugins"); [OpenAI plugins: MCP server](https://developers.openai.com/plugins/build/mcp-server) (streamable HTTP, publicly reachable HTTPS; OAuth 2.1 when the plugin requires user authentication); [OpenAI plugins: Authentication](https://developers.openai.com/plugins/build/auth); [Developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) (write-capable MCP is Business/Enterprise/Edu beta; Pro read-only; "Agent mode will not use custom apps"); [Supabase OAuth 2.1 Server](https://supabase.com/docs/guides/auth/oauth-server).

### B2. Invite emails cannot be delivered by Supabase's built-in mailer — Critical

- **Section:** §4.1 Accounts and onboarding; §21 ("Custom SMTP for reliable invitation delivery at wider scale" listed as future).
- **Failure scenario:** Raj invites the first friend. Supabase Auth's default email service only sends to addresses listed as members of the Supabase organization; everyone else gets "Email address not authorized." Even if the friend were added as an org member (which grants dashboard access — an isolation failure in itself), the limit is 2 messages/hour, and OTP re-sends during a phone sign-in will hit it.
- **Consequence:** Gate G cannot start. Adding friends as Supabase org members to work around it would hand them the service role.
- **Design change:** Custom SMTP is a Gate D prerequisite. Use a transactional provider's free tier and a domain Raj controls for SPF/DKIM (most providers require a verified sending domain; a domain is likely the first unavoidable non-zero cost — budget it). Prefer **6-digit email OTP** over magic links for the primary flow (see B12). Configure Supabase Auth rate limits deliberately.
- **Gate/test:** An invitation and an OTP sign-in to a non-team Gmail address from a phone succeed from staging before Gate D closes.
- **Sources:** [Supabase: Send emails with custom SMTP](https://supabase.com/docs/guides/auth/auth-smtp) ("Supabase Auth will only send messages to these addresses"; "Currently this value is set to 2 messages per hour"; custom SMTP urged for production).

### B3. Per-friend Claude Routine onboarding is under-specified and has silent failure modes — Critical

- **Section:** §9.2 Claude, §9.3 steps 2–5, §10, §21.
- **Failure scenario (verified facts):** Routines are in research preview; available on Pro/Max/Team/Enterprise; belong to one individual claude.ai account; count against that account's daily routine run cap and subscription usage; a Default environment allows only the Trusted allowlist, so the friend must create a **Custom** network environment listing the Supabase functions host plus Finnhub and Yahoo; environment variables are visible to anyone using the environment, so the scoped token must be stored as an **API credential** (requires an org-admin role, which Pro/Max users hold for themselves); schedule presets are hourly/daily/weekdays/weekly with a minimum interval of one hour and a per-routine stagger of "a few minutes"; a green run status means "no infrastructure error," not "task succeeded." If the friend skips the allowlist, every gateway call fails with 403 `host_not_allowed` inside the transcript while the run shows green, and the app sees only "missed window."
- **Consequence:** The connection state machine in §9.3 cannot distinguish "friend never scheduled," "friend's environment blocks the host," "friend hit the daily cap," and "provider outage." Support burden lands on Raj. If a friend's usage limit is hit mid-week, their runs are silently rejected until the window resets.
- **Design change:** (a) Publish a versioned, screenshot-level **connection kit** per provider with the exact environment JSON (Custom allowlist, no env vars, API credential host = the functions host only), the routine prompt, and the three schedules. (b) The handshake test run (§9.3 step 5) must be a real routine run, not a curl, and must verify: token is delivered as a header by the credential proxy, network egress to Finnhub/Yahoo works, contract version matches, and the run starts within the expected window. (c) Record `provider_run_url` (the session URL returned by the routine) in the run row so the user can open the transcript from the Runs screen. (d) Show "your provider's daily cap or usage window may reject runs; this app cannot see that" in the Connections screen copy. (e) Bind the API credential to the **functions host only** (`<ref>.functions.supabase.co` or a custom functions domain), never to `<ref>.supabase.co`, so the proxy never attaches the token to REST/Auth calls the model might make.
- **Gate/test:** Gate C closes only when one non-Raj account (staging synthetic user on a second Claude account) completes the kit without help and the server sees a conforming no-write run.
- **Sources:** [Claude Code: Automate work with routines](https://code.claude.com/docs/en/routines) (research preview; Pro/Max/Team/Enterprise; individual account; daily cap; usage draw-down; stagger; one-hour minimum; green ≠ success; Default = Trusted allowlist; 403 `host_not_allowed`); [Claude Code: Configure cloud environments](https://code.claude.com/docs/en/cloud-environments) (env vars visible to anyone using the environment; API credentials attached by the agent proxy for listed hosts; org-admin role requirement; "Pro and Max, you hold it in your own organization").

### B4. Backup is stored in a public repository's artifacts and will silently stop — Critical

- **Section:** §16 Free-tier backup plan steps 1–3, §22 Gate E, §23.
- **Failure scenario:** `README.md` shows the repository is public (`git clone https://github.com/RajSivapu/stocks-agent.git`, MIT). GitHub disables scheduled workflows automatically after 60 days without repository activity in public repositories; a quiet pilot in which friends use the app but nobody commits stops backing up with no alert. Separately, artifacts of a public repository are retrievable by anyone with read access, i.e. any GitHub user; the plan relies entirely on the encryption, so the ciphertext of every friend's ledger is public. GitHub Free also caps Actions artifact storage at 500 MB shared with Packages.
- **Consequence:** After the first quiet two months the RPO becomes unbounded, and the admin health view ("backup age") is the only detector — if it is wired at all.
- **Design change:** Put backups in a **private** store. Cloudflare R2's free tier is 10 GB-month, 1M Class A / 10M Class B operations, free egress, and Cloudflare is already in the stack; write from a GitHub Action in a **separate private repository** (private repos get 2,000 free minutes/month) or from Supabase Cron via `pg_net`, with a scoped R2 API token. Encrypt with `age` to an offline key. Keep 14 daily + 4 weekly. Have the app's Cron job record `last_backup_ok_at`, and page Raj on Telegram (operational channel) when it exceeds 36 hours. Also back up the **token-digest pepper**, the **pairing-code pepper**, and the Edge secret manifest in a separate encrypted "key material" object; without the pepper a restored database has no valid connections or pairings.
- **Gate/test:** Restore drill must start from *only* the R2 objects plus the offline key on a machine that has never seen production, and must end with a friend-equivalent synthetic user's routine token still authenticating.
- **Sources:** [GitHub: Events that trigger workflows — schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) ("Scheduled workflows are disabled automatically when there has been no repository activity for more than 60 days"; may be delayed during high load); [GitHub: About billing for GitHub Actions](https://docs.github.com/billing/managing-billing-for-github-actions/about-billing-for-github-actions) (Free: 500 MB storage, 2,000 minutes); [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/) (free tier). Artifact download visibility on public repos: **[Unverified today]** — treat as true until shown otherwise; it does not change the recommendation.

### B5. No run-level exclusivity: duplicate runs corrupt alert state — High

- **Section:** §10 run lifecycle; §7.2 ("publication idempotency includes owner_id, market date, phase, kind"); §9.7 ("owns holding alert transitions").
- **Failure scenario:** A friend clicks **Run now** on the pre-market routine at 07:40 while the scheduled 07:33 run is still executing (routines stagger by a few minutes; runs take minutes). Both call `start_run` for (owner, 2026-09-08, pre-market). Publication uniqueness stops the second Telegram message, but both runs persist `decision_evaluations`, both refresh quotes, and both apply "holding alert transitions" (edge-trigger dedup state). The second run may mark an edge as already-notified based on the first's state, or vice versa; grading later sees two decisions for one phase.
- **Consequence:** Duplicate or contradictory recommendation history; the audit trail no longer identifies "the" decision for the day; outcome grades double count.
- **Design change:** `start_run` claims a unique `(owner_id, market_date, phase)` row with status `active`; a second claim returns the existing run id and `already_active: true` (idempotent), and `submit_analysis` for a run whose claim is not the active one is rejected `run_superseded`. `on-demand` phase is exempt but rate-limited per owner per hour. Add a lease expiry so a crashed run does not block the phase forever (expiry = window end).
- **Gate/test:** Two concurrent `start_run` calls for the same key → exactly one active run; the loser's `submit_analysis` is rejected with no writes.

### B6. Stale-evidence rule is a prompt promise, not a server check — High

- **Section:** §4.1 "Independent fresh evidence for every intraday run", §9.4, §10 ("never republishes the morning recommendation as the midday decision"), §15.
- **Failure scenario:** The Checker is "a structured second pass by the same model" (ROADMAP). Under prompt injection or a lazy run, the intraday submission cites morning evidence blocks with morning timestamps and a fresh-looking quote copied from the morning brief. The gateway refreshes the *quote* independently (good) but nothing in §9.4 states that evidence `retrieved_at` must post-date `run.started_at`, or that evidence IDs must be new to this run.
- **Design change:** The envelope validator requires, for any actionable candidate: every cited evidence block has `retrieved_at >= run.started_at - 5 min`, at least one `market`-type block and one `news/filing`-type block are current-run, and `prior_suggestion_ids` are the only permitted references to earlier runs. Reuse of an evidence ID from a prior run is a hard reject (`evidence_reused`). Record the veto reason on the run so the Runs screen shows "vetoed: stale evidence," not "failed."
- **Gate/test:** Conformance fixture "morning replay at midday" must be vetoed with `evidence_reused`/`evidence_stale`.

### B7. Holdings and ledger are two sources of truth — High

- **Section:** §4.1 Portfolio recordkeeping (correction workflow), §11 ("updates the holding, appends the transaction"), §18 Portfolio invariants.
- **Failure scenario:** A user voids a Buy from three weeks ago (wrong price). The correction RPC "recomputes the affected holding lifecycle" — but the holding row was updated incrementally by every later Sell using the old average cost, and realized P&L stored on those Sell commands is now wrong. If the recompute has any bug, `holdings` and `transactions` disagree permanently and nothing detects it.
- **Design change:** Define `holdings` as a **deterministic projection** of the owner's transaction ledger: the RPC rebuilds `(shares, cost_basis, avg_cost, realized_pnl)` for that owner/ticker from the full ordered ledger inside the same transaction and writes the projection; `expected_version` becomes the ledger's per-owner sequence number. Store realized P&L only in the projection, never on commands. Add a nightly Cron invariant check that re-projects every owner/ticker and alerts on mismatch. Pick and document the cost method (average cost is the only sane one for a reminder-only ledger; lot-level is a non-goal).
- **Gate/test:** Property test: any sequence of buys/sells/corrections, applied in any interleaving that the version check admits, yields the same projection as a fold over the final ledger.

### B8. First-buy race and fee reconciliation — High

- **Section:** §11 ("locks the owner/ticker pair"), §4.1 arithmetic reconciliation.
- **Failure scenario A:** Two Buy confirmations for a ticker the user has never held (web + Telegram, or a double tap on a slow phone) — there is no `holdings` row to lock, so both pass the version check (expected version = "none"), both insert, one dies on the unique constraint and the user sees an opaque error while the other applied. Worse: if the RPC catches the error and retries, both transactions can be appended.
  **Failure scenario B:** The user records "bought 3.2 shares at 210.13 for $672.99" — a real fill with a $0.57 SEC/regulatory fee on a sale, or a brokerage commission, fails the "material mismatch" check and blocks confirmation; the user then lies about the price to get past it, corrupting cost basis.
- **Design change:** Take `pg_advisory_xact_lock(hashtext(owner_id::text || ticker))` at the top of the apply RPC regardless of row existence. Add an explicit optional `fees` field (non-negative, bounded) and define reconciliation as `|amount − (qty × price ± fees)| ≤ max($0.05, 0.1%)`; anything above is a hard block with the three values echoed back. Define numeric types once: `NUMERIC(20,8)` for shares (brokerages report up to 6–8 decimals), `NUMERIC(20,4)` for prices, `NUMERIC(20,2)` for amounts; reject shares with more than 8 decimals or > 1,000,000, and prices outside `[0.0001, 1,000,000]`.
- **Gate/test:** Concurrency test with two first-buys on the same ticker → exactly one transaction row; fee case passes reconciliation; back-dated Sell earlier than any Buy is rejected (`negative_historical_balance`) for ordinary commands, not only corrections.

### B9. Service-role RPCs trust a caller-supplied owner — High

- **Section:** §6.2 steps 4–5, §6.4, §19 row "Service role bypass causes IDOR".
- **Failure scenario:** Every "fixed RPC" called under the service role receives `p_owner_id` as a parameter; `auth.uid()` is null in that context, so "validates ownership internally" can only mean "checks that the row's owner equals the parameter." A single Edge Function bug that resolves the owner from the wrong variable (a copy-paste from the Telegram handler into the export handler, a merged request object) is a full IDOR with no defence in depth.
- **Design change:** Split RPCs by trust path. **User-initiated mutations (web) run as the user**: user-context client, `SECURITY INVOKER` RPC, owner = `auth.uid()` inside SQL, RLS enforced. No service role in `app-api` at all except for admin invitations. **Machine paths (agent-gateway, telegram-portfolio)** keep service role but must call `set_config('app.owner_id', <resolved>, true)` at transaction start, and every owner-aware RPC asserts `p_owner_id = current_setting('app.owner_id')` and RLS policies include `OR owner_id = current_setting('app.owner_id', true)::uuid` for the service path — so an RPC can never act on an owner the request did not resolve. Add a static test that greps `app-api` for `SERVICE_ROLE`/`sb_secret` usage outside the invitation module.
- **Gate/test:** Cross-tenant suite includes "service-role client, resolved owner A, RPC called with `p_owner_id = B`" → rejected.

### B10. Invoker-security views still require base-table grants — High (isolation correctness)

- **Section:** §6.1, §7.4, §18 Database and RLS.
- **Failure scenario:** A `security_invoker` view only works if the invoking role (`authenticated`) has `SELECT` on the base tables. If the base tables live in `public`, they are also directly exposed through PostgREST, so "read-only screens query views" is not a boundary, and any table where RLS is accidentally disabled (the current schema has RLS enabled but no policies for the browser — §3) is fully readable. The Supabase advisors flag `security_definer_view`, `rls_disabled_in_public`, `auth_users_exposed`, and `function_search_path_mutable`; none are mentioned as gates.
- **Design change:** Put all user-specific base tables in a non-exposed schema (`app`), grant `SELECT` on them to `authenticated` with RLS `USING (auth.uid() IS NOT NULL AND owner_id = (select auth.uid()))`, and expose only `security_invoker` views in the exposed `api` schema (set PostgREST exposed schemas to `api` only). Force RLS on every `app` table (`ALTER TABLE … FORCE ROW LEVEL SECURITY`) so even table owners cannot bypass. Run the Supabase security advisor in CI against staging and fail on any security-category finding.
- **Gate/test:** RLS tests must be executed **through PostgREST with a real user JWT**, not only in SQL, for every exposed view and RPC; plus an "RLS disabled on any app table" assertion.
- **Sources:** [Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security) (views bypass RLS by default; `security_invoker = true`; wrap `auth.uid()` in `select`; null-uid pitfall); [Supabase database advisors](https://supabase.com/docs/guides/database/database-advisors?lint=0010_security_definer_view).

### B11. Telegram: dedupe by high-water mark breaks; group chats leak previews — High

- **Section:** §12 Commands ("`telegram_update_id` remains an idempotency boundary"), Pairing.
- **Failure scenario A:** Telegram states that if there are no new updates for at least a week, the next `update_id` is chosen randomly. A quiet bot (holiday week) then receives an `update_id` lower than the stored maximum; a "greater than last seen" check drops every subsequent command silently.
  **Failure scenario B:** A friend adds the bot to a family group and runs `/portfolio`; the pairing binds `(user_id, chat_id)` where chat is the group, and previews (holdings, cost basis) are posted to the group. §12 says "same paired user and chat" but never restricts chat type.
  **Failure scenario C:** After `/unlink`, an old inline Confirm button in the chat history is still clickable; if the callback handler checks "same user and chat" against the *command row* rather than an *active link*, it applies.
- **Design change:** Dedupe with a set: `telegram_updates(update_id PRIMARY KEY, received_at)` and a 30-day retention; never compare ordering. Accept commands and pairing only where `chat.type = 'private'` and `chat.id = from.id`; reply "this bot only works in a private chat" elsewhere and ignore callbacks from non-private chats. On callback: require an active `telegram_links` row matching `from.id` **at callback time** and mark all pending commands `cancelled` on unlink. Keep `callback_data` to an opaque ≤64-byte command token (Telegram's limit), never the command payload. Call `answerCallbackQuery` before applying, and make the apply idempotent on `(command_id, action)`.
- **Gate/test:** Fixture with `update_id` regression; group-chat fixture; post-unlink callback fixture.
- **Sources:** [Telegram Bot API](https://core.telegram.org/bots/api) (`secret_token` 1–256 chars; random `update_id` after a week; callback_query fields).

### B12. Magic-link + PKCE on phones will fail for exactly the target users — Medium

- **Section:** §4.1 (magic link or OTP), §13.3 ("Supabase PKCE sign-in with callback URLs allow-listed").
- **Failure scenario:** PKCE requires the link to be opened in the same browser context that started sign-in. A friend requests a link in the PWA, taps it in Gmail on iOS, and it opens in Gmail's in-app browser (or Safari when the PWA is a home-screen app) — different context, the code verifier is missing, sign-in fails with a confusing error. Corporate mail scanners can also pre-open one-time links.
- **Design change:** Make **6-digit email OTP** the primary flow (typed into the same screen; no callback, no verifier), keep magic link as a desktop-only fallback. This also removes the "auth parameters in the visible URL" concern from §13.3. OTP expiry: 10 minutes (override the 1-hour default), 60-second resend limit is default.
- **Gate/test:** Manual test matrix: iOS Safari PWA, Android Chrome PWA, Gmail app, Outlook app.
- **Sources:** [Supabase passwordless email](https://supabase.com/docs/guides/auth/auth-email-passwordless) (OTP vs magic link; PKCE same-context requirement; 60-second/1-hour defaults).

### B13. "Reauthentication" has no defined mechanism — Medium

- **Section:** §13.3 last bullet; §4.1 account deletion.
- **Failure scenario:** Supabase JWTs carry no "authenticated at" claim usable for step-up; a stolen long-lived session (refresh token in `localStorage`, the supabase-js default) can rotate the provider token or delete the account.
- **Design change:** Define step-up as: the Edge Function issues a fresh OTP, the user verifies it, and the server records `stepup_verified_at` in `profiles` (server-side, keyed by session id) with a 5-minute validity; destructive RPCs check that timestamp. Store the Supabase session in memory plus a same-site cookie is not available for a static SPA, so accept `localStorage` but shorten the JWT to 15 minutes and the refresh token reuse interval to default, and revoke all sessions on deletion/rotation.
- **Gate/test:** Deletion and token rotation with an expired step-up → rejected.

### B14. Free-project pausing and the two-project budget conflict with the plan — Medium

- **Section:** §17, §16 step 7, §21.
- **Failure scenario:** Supabase Free allows 2 active projects, pauses a project after one week without "sufficient user database activity," and gives no automatic backups. Staging will be idle most weeks and will pause (harmless, but every drill starts with a resume). Production activity is three runs a day plus cron; it is **[Unverified]** whether `pg_cron`-internal activity counts as user activity, so a week in which every friend's routine fails (e.g., all hit usage limits during a holiday week) could pause production, after which Telegram webhooks 5xx and runs report "missed." A restore drill that must "restore into the second Supabase project" destroys staging each time and there is no third project for a real disaster.
- **Design change:** Accept and document: production may pause; add an external free uptime pinger hitting a public `healthz` Edge Function daily (this counts as activity) and alerting via Telegram — but note this is a third-party dependency and must be disclosed. Write the DR plan as "restore into staging, repoint," and accept that staging is sacrificed. Put "Supabase Pro ($25/mo) if a paused week ever happens" as the pre-agreed escalation, not a surprise.
- **Sources:** [Supabase pricing](https://supabase.com/pricing) (2 active projects; 500 MB; 500K Edge invocations; 1-day logs; no automatic backups on Free; Pro daily backups 7 days); [Supabase: Project pausing](https://supabase.com/docs/guides/platform/free-project-pausing).

### B15. Backup credential path over IPv4 — Medium

- **Section:** §16 step 1 ("least-privilege backup credential").
- **Failure scenario:** GitHub-hosted runners and most CI are IPv4-only; Supabase direct connections are IPv6 unless the paid IPv4 add-on is bought. `pg_dump` against the direct host fails; someone "fixes" it by using the service role through the REST API instead, losing the least-privilege property.
- **Design change:** Use the Supavisor **session-mode** pooler (IPv4 on every tier) with a dedicated `backup_reader` role that has `SELECT` on the enumerated `app` tables only, `pg_dump --schema=app --no-owner --no-privileges -t` list, and `vault.*`/`auth.*` excluded except for a generated identity map (email, owner_id) produced by a `SECURITY DEFINER` function callable only by `backup_reader`.
- **Sources:** [Supabase: Connecting to Postgres](https://supabase.com/docs/guides/database/connecting-to-postgres) (direct connections IPv6; Shared Pooler IPv4 on every tier).

### B16. Corporate-action normalization has no data source — Medium

- **Section:** §9.6 "Corporate actions are first-class inputs."
- **Failure scenario:** The spec requires split/symbol-change normalization before stop/target comparison, but the current stack is Yahoo chart endpoints (unofficial) plus Finnhub free. Neither is named as the corporate-action source, and Yahoo's unadjusted intraday quote will breach a stored stop the morning of a 10:1 split; the README already says "Splits require review."
- **Design change:** Name the source (Finnhub `/stock/split` on the free tier — **[Unverified today]** that it is included in free; spike it) and the rule: if a split/symbol-change event is detected in `[last_run, now]` for a held ticker, the holding enters `needs_review`, alerts for that ticker are suppressed with a `corporate_action_pending` data warning, and the user must confirm the adjusted share count/stop through the command workflow. Never auto-adjust the ledger.
- **Gate/test:** Fixture: 10:1 split day → no `stop_breach` alert; a `data_warning` publication instead.

### B17. Provider context consent omits the two largest disclosures — Medium (privacy)

- **Section:** §2, §4.1 acknowledgement, §12 Notifications, §20.
- **Failure scenario:** A friend consents to "bounded portfolio fields leaving Supabase for Anthropic." Not disclosed: (1) the full run transcript, including the bounded packet and the model's reasoning about their holdings, is stored as a session in *their* claude.ai account under Anthropic's retention; (2) Raj, as operator, can read every user's holdings through the Supabase dashboard, backups, and logs; (3) Telegram message history cannot be deleted by the bot after 48 hours, so "account deletion" cannot remove previews and briefs from the friend's Telegram chat; (4) quotes for their tickers are fetched from Yahoo/Finnhub, which see the ticker set.
- **Design change:** Add these four statements to the consent screen and privacy notice verbatim. Add "delete my Telegram chat history yourself" to the deletion runbook.

### B18. Rate limiting is unspecified where it matters — Medium

- **Section:** §6.2 step 3, §6.3.
- **Failure scenario:** A misbehaving or injected routine loops on `read_bounded_context`/`submit_analysis`; Supabase Free includes 500K Edge invocations/month and Yahoo/Finnhub have their own limits; one friend's runaway routine exhausts the project-wide quota and every other user's runs fail. There is also no per-owner cap on `on-demand` runs.
- **Design change:** Per-connection limits stored in Postgres (not in-memory, since Edge isolates are ephemeral): max 12 gateway operations per run, max 6 runs per owner per market day, max 1 on-demand run per owner per hour, and a global circuit breaker at 60% of the monthly Edge quota that disables non-Raj connections with an operational alert. Return `429` with `Retry-After` and record the event on the connection.
- **Sources:** [Supabase Edge Function limits](https://supabase.com/docs/guides/functions/limits) (Free 150 s wall clock, 2 s CPU, 256 MB).

### B19. Log retention and the "observability" section are inconsistent with Free — Low

- **Section:** §20.
- **Failure scenario:** Supabase Free keeps logs for one day; the incident runbook that says "inspect logs" for a report a friend makes on Monday about Friday cannot succeed.
- **Design change:** Everything the runbook needs must be in Postgres tables under retention, not platform logs: `gateway_requests` (status, error code, duration, owner hash), `auth_events` (sign-in success/fail by owner hash), `webhook_events`. Treat platform logs as a debugging convenience only.

---

## C. Unsupported or uncertain platform assumptions

| Provider/platform | Assumption in the document | Verified reality (today) | Source | Required adjustment |
|---|---|---|---|---|
| OpenAI ChatGPT | "ChatGPT web scheduled task + stock-agent plugin" can call the gateway with a static scoped credential (§5, §9.2) | Classic scheduled tasks: eligible on Free/Go/Plus/Pro/Business/Enterprise/Edu; 3/5/10/15 active tasks; help article names only Gmail, Slack, GitHub as connectable accounts; no GPTs. Plugins usable in scheduled tasks only via ChatGPT Work (web) or desktop app. Plugin MCP servers must be public streamable-HTTP HTTPS; user auth via OAuth 2.1. Write-capable custom MCP is Business/Enterprise/Edu beta; Pro read-only; agent mode does not use custom apps. **[Verified]** | [Help: scheduled tasks](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt); [Learn: scheduled tasks](https://learn.chatgpt.com/docs/automations); [Plugins: MCP server](https://developers.openai.com/plugins/build/mcp-server); [Plugins: auth](https://developers.openai.com/plugins/build/auth); [Developer mode/MCP](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt); [ChatGPT Work announcement](https://openai.com/index/chatgpt-for-your-most-ambitious-work/) | Remove ChatGPT from release one; separate OAuth/MCP project (B1). |
| OpenAI ChatGPT Work | Available to Plus users unattended in the cloud | Announcement: rolling out to Pro/Enterprise/Edu first, then Plus/Business; usage "may use more of your plan's included usage" like Codex; web/mobile and desktop. Whether a Plus user's Work scheduled task runs a plugin **unattended with no desktop app** is **[Unverified]** and must be spiked. | same | Spike before any ChatGPT claim in UI copy. |
| Anthropic Claude Routines | "Supported through the existing Cloud Routine workflow" is a light per-user step | Research preview; Pro/Max/Team/Enterprise only; per-account daily run cap; draws down subscription usage; overage only with usage credits; one-hour minimum; stagger; Default env is Trusted allowlist (custom domains needed); env vars readable by anyone in the environment; API credentials require org-admin role (self on Pro/Max); `/fire` API trigger exists under a beta header. **[Verified]** | [Routines](https://code.claude.com/docs/en/routines); [Cloud environments](https://code.claude.com/docs/en/cloud-environments) | Connection kit + real handshake (B3); document "research preview, may change" in the product. |
| Anthropic Claude Routines | Schedules survive DST correctly | "Times are entered in your local zone and converted automatically, so the routine runs at that wall-clock time regardless of where the cloud infrastructure is located." Whether the stored schedule is local-zone-aware or a fixed UTC cron is **[Unverified]**; the CLI accepts raw cron expressions, which are typically UTC. | [Routines](https://code.claude.com/docs/en/routines) | Expected-window monitor must tolerate ±60 min for the two weeks after each DST change and alert the user to re-check; test on 2026-11-01. |
| Anthropic billing | Subscription covers routines; no API charge | Confirmed: paid Claude subscription "doesn't include access to the Claude API or Console"; they are billed separately. Routines draw from subscription usage. **[Verified]** | [Support article](https://support.claude.com/en/articles/9876003-i-have-a-paid-claude-subscription-pro-max-team-or-enterprise-plans-why-do-i-have-to-pay-separately-to-use-the-claude-api-and-console) | None; keep "no API key" invariant; add "usage credits/overage is your choice" copy. |
| xAI Grok | "Adapter-ready" | API is prepaid-credit or invoiced; no evidence of subscription-backed scheduled agent runs calling third-party endpoints. **[Verified for billing; Unverified for any agent route]** | [xAI billing](https://docs.x.ai/console/billing) | Keep out of release one entirely; delete from UI. |
| Supabase Auth email | Magic-link invites to friends | Default mailer sends only to org team members; 2 messages/hour; custom SMTP urged for production. **[Verified]** | [Custom SMTP](https://supabase.com/docs/guides/auth/auth-smtp) | Custom SMTP + domain before Gate D (B2). |
| Supabase Free | "Within current quotas," pausing acknowledged | 2 active projects; 500 MB DB; 5 GB egress; 500K Edge invocations; 1-day log retention; no automatic backups; paused after 1 week of inactivity; 1-year window to restore a paused project. **[Verified]** | [Pricing](https://supabase.com/pricing); [Pausing](https://supabase.com/docs/guides/platform/free-project-pausing) | B14, B19. |
| Supabase Edge Functions | Gateway does quote refresh + policy in one call | Free: 150 s wall clock, 2 s CPU per request, 256 MB. Quote refresh is I/O (fine); policy over N holdings plus rendering must stay under 2 s CPU. **[Verified]** | [Limits](https://supabase.com/docs/guides/functions/limits) | Add a CPU budget test with 40 holdings; split `submit_analysis` from `publish` if needed. |
| Supabase API keys | "Publishable key" in browser | New `sb_publishable_`/`sb_secret_` keys are not JWTs; legacy `anon`/`service_role` deprecated by end of 2026; Edge Functions using new keys should set `verify_jwt = false` and verify in code. **[Verified]** | [API keys](https://supabase.com/docs/guides/api/api-keys) | All functions deploy with `--no-verify-jwt` and verify the user JWT in code via JWKS (`getClaims`); add a test that a forged HS256 token is rejected. |
| Supabase Cron | Cron on Free | Docs show scheduling via `pg_cron` + `pg_net` calling Edge Functions with the publishable key stored in Vault; plan gating not stated on the pages read. **[Verified mechanism; plan availability Unverified]** | [Schedule functions](https://supabase.com/docs/guides/functions/schedule-functions); [Cron quickstart](https://supabase.com/docs/guides/cron/quickstart) | Confirm `pg_cron` is enabled on the free production project during Gate A. |
| Supabase OAuth 2.1 server | (not in doc) | Exists: authorization code + PKCE, dynamic client registration, "authenticate AI agents, LLM tools, and MCP servers." Beta/GA status and plan gating not stated on the page read. **[Verified existence]** | [OAuth server](https://supabase.com/docs/guides/auth/oauth-server) | Candidate AS for the future ChatGPT MCP project. |
| Supabase direct DB from CI | `pg_dump` from GitHub Actions | Direct connections are IPv6 unless IPv4 add-on; Shared Pooler is IPv4 on every tier. **[Verified]** | [Connecting](https://supabase.com/docs/guides/database/connecting-to-postgres) | Use session pooler (B15). |
| Cloudflare Workers Static Assets | Free static hosting + security headers | Static asset requests free and unlimited; `_headers` supported (100 rules, 2,000 chars/line) but applies only to static responses, not Worker-generated responses; `run_worker_first` on Free returns 429 when limits are exceeded. **[Verified]** | [Billing & limitations](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/); [Headers](https://developers.cloudflare.com/workers/static-assets/headers/) | Ship a pure static asset bundle with `_headers`; do not add a Worker script in release one. |
| Cloudflare R2 | (not in doc) | Free tier: 10 GB-month, 1M Class A, 10M Class B, free egress. **[Verified]** | [R2 pricing](https://developers.cloudflare.com/r2/pricing/) | Use for backups (B4). |
| GitHub Actions | Daily backup workflow in this repo | Scheduled workflows disabled after 60 days without activity in public repos; may be delayed under load; Free: 500 MB artifact storage, 2,000 min/month (private). **[Verified]** | [Schedule event](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows); [Actions billing](https://docs.github.com/billing/managing-billing-for-github-actions/about-billing-for-github-actions) | Private repo or Cron→R2 (B4). |
| Telegram Bot API | update_id idempotency; same-user callbacks | `secret_token` header 1–256 chars; `update_id` may reset randomly after a week idle; callback_query carries `from`, `message`, `chat_instance`, `data`. **[Verified]** | [Bot API](https://core.telegram.org/bots/api) | Set-based dedupe; private-chat only (B11). |
| Yahoo Finance / Finnhub | Free quote/corporate-action sources | Yahoo chart endpoints are unofficial and undocumented; Finnhub free-tier terms and split endpoint availability were not re-verified today. **[Unverified]** | — | Treat both as "may break without notice"; the source-health state already planned must drive fail-closed; spike Finnhub splits (B16). |

---

## D. Missing requirements and tests

Requirements the document needs and does not have:

1. **Run exclusivity** per (owner, market date, phase) with lease expiry (B5).
2. **Server-side evidence freshness** rule tied to `run.started_at` and evidence-ID novelty (B6).
3. **Ledger projection invariant** and cost-basis method (B7); fee field and numeric domains (B8).
4. **Trust-path split for RPCs** (user-context for web, `app.owner_id` transaction setting for machine paths) and a static prohibition on service role in `app-api` (B9).
5. **Schema layout**: `app` (private, RLS forced) vs `api` (exposed, invoker views only); PostgREST exposed schemas limited to `api` (B10).
6. **Telegram private-chat-only rule, set-based update dedupe, unlink cancels pending commands** (B11).
7. **OTP-first sign-in; step-up definition** (B12, B13).
8. **Key-material backup** (peppers, Edge secret manifest) separate from data backup; R2 or private repo destination; backup-age alert (B4).
9. **Custom SMTP + sending domain** as a prerequisite (B2).
10. **Per-connection and global quotas** in Postgres (B18).
11. **Operational data in tables, not logs** (B19).
12. **Corporate-action `needs_review` state** and data source (B16).
13. **Consent text** covering provider transcript retention, operator access, Telegram history, and market-data providers (B17).
14. **Early-close handling**: intraday anchor at open+210 min equals the 13:00 ET close on early-close days; define that `intraday` on an early-close day runs at open+120 min and `post-market` at close+10 (13:10 ET). §10 currently says anchors are "resolved against the actual session" but the provider schedule is a fixed wall-clock time — the server must translate: expected windows are computed per day from the calendar, and the provider's fixed time is validated against them at `start_run` (a 13:00 ET run on an early-close day is accepted as `post-market`, not `intraday`).
15. **Phase derivation is server-side only**: `start_run` takes no phase from the caller for scheduled connections; it derives the phase from the current session state and calendar. "Run now" at 10:00 ET becomes `on-demand`, never `pre-market`.
16. **Deletion runbook specifics**: cascade order, publication bodies, Telegram unlink and `deleteMessage` limits, backup tombstone check at restore time, provider connection revocation confirmation to the user.
17. **Admin screen**: aggregate-only; the SQL definition of every admin view must be reviewed for per-user fields (a "missed runs by user" table is a surveillance surface).
18. **Threat scenarios to add to §19**: (a) friend's Claude account compromised → attacker reads the friend's bounded context and can submit analyses (blast radius = one owner; token revocation path); (b) shared bot token leak → all users' Telegram; rotation runbook; (c) operator laptop compromise → service role + backup key; (d) wrong-tenant data exposure incident → notification to affected users within 72 hours; (e) provider prompt-injection that attempts to call `finish_run` with fabricated receipts (already handled) *and* attempts to change the user's `notification_preferences` via a chat instruction (must be impossible: preferences are web-only); (f) a friend records trades to "test" and later asks for a full reset — a `reset_ledger` admin runbook with export first.

Tests the document needs and does not list:

- Concurrent first-buy, concurrent `start_run`, unlink-then-callback, `update_id` regression, group-chat command, early-close calendar day, DST-week expected-window tolerance (2026-11-01), fee reconciliation, 8-decimal fractional share, split-day suppression.
- RLS via PostgREST with real JWTs for each `api` view; forged-JWT rejection; Supabase security advisor clean run in CI.
- Edge Function CPU budget with 40 holdings and a 20-candidate submission.
- Service-worker cache audit: no response from the Supabase origin is ever cached (and, per E below, prefer no service worker at all).
- Restore drill from R2 objects + offline key only, ending with a working synthetic-user routine token.
- Backup-age alert fires when the job is disabled.
- Consent screen snapshot test (copy invariants, like the "record" wording test already planned).

Monitoring and runbooks to add: backup age; per-user missed-window counts (aggregate to admin, individual to the user); Edge quota consumption; source-health (Yahoo/Finnhub) daily; Telegram webhook error rate; token-leak rotation runbook per boundary (connection token, bot token, Supabase secret key, R2 token, SMTP key); "friend lost access to their Claude account" runbook (revoke, reconnect); "operator unavailable" note (who can pause routines if Raj is unreachable — with one operator the honest answer is "nobody"; say so in the pilot terms).

---

## E. Complexity and scope assessment

**Must remain in release one** (each is either a blocker for isolation/safety or the reason the product exists):

Tenancy migration with parity checks; RLS + schema split + PostgREST-level attack suite; owner-aware gateway with run exclusivity and evidence freshness; Claude Routine connection lifecycle with a real handshake and connection kit; ledger projection with corrections, fees, and fractional shares; web + Telegram command parity; Telegram pairing hardened as in B11; OTP sign-in with custom SMTP; the Today/Portfolio/Activity/Research/Runs/Connections screens; CSV/JSON export; deactivate + deletion request (runbook-backed, 7-day SLA is fine); encrypted backups to R2 with a successful restore drill; consent screens; operational alerts for missed runs and disconnected providers.

**Should be delayed** (tie to concrete risk or dependency):

- ChatGPT connector (B1) — different auth model; separate project.
- Grok — no route exists.
- Second-opinion runs (§9.8) — doubles provider-contract surface and adds a comparison UI; friends have one subscription each; ship after the first month of graded results.
- Personal stricter risk preferences (§14) — adds a policy-versioning UI and a second policy source; platform ceilings alone are safe for a pilot. Keep the *schema* (owner policy overrides table) but no UI.
- Base currency (§4.1, §13.1 Settings) — the product is US equities only; FX display invites incorrect P&L. Remove the field.
- Settings-editable schedules (§13.1) — the provider owns the schedule; the app only needs "which phases do you expect" toggles.
- Admin screen (§13.1 item 8) — invitations via a trusted SQL/CLI workflow are sufficient for ≤10 users and remove a privileged UI from the attack surface. Keep the aggregate health *view* as a read-only page.
- PWA offline shell / service worker (§5, §13.2) — the only benefit is an offline splash; the cost is a caching layer that §13.3 then has to defend. Ship a plain SPA with `no-store` on API calls; add a manifest for home-screen install (no service worker required for install prompts on iOS; Android install prompts do want a SW — accept the missing prompt).
- Full-evidence retention compaction after 12 months (§16) — a Cron job you will not need for a year.
- Recurring-plan reminders via Cron (§10) — plans already surface in the pre-market brief; a separate reminder channel is notification noise until asked for.

**Can be simplified without weakening safety:**

- Two Edge Functions, not three plus an app-api: `agent-gateway` (machine credential, service role with `app.owner_id`) and `app-api` (user JWT, user-context client, no service role) with `telegram-portfolio` kept as-is. This preserves the security domains the document insists on while cutting deployment surface. (Keeping Telegram separate is right: different credential, different caller.)
- One `owner_policy_overrides` table with no UI instead of the full personal-policy layer.
- Replace the GitHub Actions backup pipeline with a Supabase Cron job that calls a backup Edge Function which streams a `COPY … TO STDOUT` of `app` tables, encrypts with `age` to the offline public key, and PUTs to R2 with a scoped token — one fewer platform, no 60-day disable, private by default. (If the 150 s wall clock is a concern at pilot scale it is not — the DB is well under 500 MB.)
- The "identity-recovery map" reduces to a table `app.owner_identity(owner_id, email_hash, email_encrypted)` that lives inside the data backup; on restore, invite by email, then update `auth.users.id` mapping through one SQL script.

---

## F. Recommended architecture

The corrected architecture keeps the document's four layers and its non-negotiables (no brokerage, record-only ledger, model proposes / server decides, no browser secrets, one owner per run) and changes six things.

**1. One launch provider, one credential type.** Release one supports Claude Routines only, using a scoped static token delivered by the Claude cloud environment's API-credential proxy, bound to the functions host. The `agent_connections` table keeps `provider` and `credential_type` columns (`static_token` now; `oauth_access_token` reserved) so the future MCP/OAuth project is additive. The gateway is the only thing a provider can talk to; that does not change.

**2. Two trust paths, made structural.** Browser → `app-api` runs as the user: user-context client, `SECURITY INVOKER` RPCs, RLS enforced, no service role anywhere in that function. Machines (routine, Telegram) → their dedicated function resolves the owner from the credential, opens a transaction, sets `app.owner_id`, and calls RPCs that assert the setting; RLS policies honour the setting on the service path. An owner can therefore never be chosen by a request body on any path, and a bug in one function cannot widen the other.

**3. Postgres as the only place invariants live.** Schema `app` (private, RLS forced on every table, composite `(owner_id, …)` keys and FKs) and schema `api` (exposed, invoker-security views and wrapper functions only). Holdings are a projection of the ledger, rebuilt inside the apply RPC under an advisory lock keyed on (owner, ticker). Runs have a unique active claim per (owner, market date, phase). Publications keep their existing exactly-once claim. Quotas live in tables, not memory.

**4. Fresh-evidence enforcement in the envelope validator**, not in the prompt: evidence timestamps relative to `run.started_at`, evidence-ID novelty per run, server-derived phase, and a `market_closed` short-circuit before any research. The Checker remains a same-model second pass and the UI says so.

**5. Onboarding that can actually complete.** Custom SMTP on a domain Raj controls; OTP-first sign-in; a versioned connection kit per provider; a handshake that is a real routine run; per-user Connections screen showing the last run's provider session link and the expected-window status.

**6. Recovery that a single operator can execute.** Cron-driven encrypted backup to private R2 (data object + key-material object), a backup-age alert, a written restore that ends with routine tokens still valid, and an honest RTO of **three business days** for a full production loss (restore, re-point Telegram webhook, re-issue nothing — tokens survive because the pepper is restored — but re-verify each friend's connection window).

Material differences from the submitted plan: ChatGPT and Grok leave release one; service role leaves the web path; holdings become derived; run exclusivity and evidence-freshness become server rules; GitHub Actions leaves the backup path; magic links become a fallback; several UI surfaces (admin, personal policy, second opinion, offline shell, base currency, schedules) are removed from release one. The Mac remains out of the production path, the PWA remains static on Cloudflare, Supabase remains the only backend, and nothing in this architecture can reach a broker.

---

## G. Exact specification changes

Section-by-section replacement text. Apply as replacements of the named sections; unnamed sections stand.

### §1 Executive decision — replace the sentence beginning "Claude, ChatGPT, and future model runtimes…"

> **Provider-neutral analysis layer:** model runtimes communicate through one versioned, narrowly scoped analysis protocol. Release one ships exactly one verified adapter (Claude Routines, static scoped token). The protocol and schema reserve an OAuth 2.1 credential type for future MCP-hosted providers (ChatGPT plugins), which are a separate, separately reviewed project and are not promised in the product.

### §4.1 Accounts and onboarding — replace the first three bullets

> - Invite-only Supabase Auth accounts created through a trusted admin CLI/SQL workflow (no admin web screen in release one).
> - Six-digit email one-time-password sign-in as the primary flow; magic link only as a desktop fallback. Email is delivered through custom SMTP on an operator-controlled domain; the Supabase default mailer is not used for any user-facing message.
> - A default profile, timezone, and notification preferences per user. Base currency is not a release-one setting; the product is US-listed equities and ETFs in USD.

Add at the end of "Accounts and onboarding":

> - Consent, before a provider is connected, states in plain language: the bounded portfolio packet and the model's analysis of it are stored in the user's own provider account (for Claude, as a routine session on Anthropic's infrastructure) under that provider's retention; the operator can read all application data through the database, backups, and logs; tickers held or watched are sent to the market-data providers; and Telegram messages older than 48 hours cannot be deleted by the bot.

### §4.1 Portfolio recordkeeping — replace the "Arithmetic reconciliation" bullet and add two bullets

> - Every Buy/Sell records `quantity`, `price`, optional `fees` (≥ 0), and `amount`. Reconciliation requires `|amount − (quantity × price ± fees)| ≤ max($0.05, 0.1% of amount)`. Failures block confirmation and echo all four values; the system never guesses which one is wrong. Shares use `NUMERIC(20,8)` (max 8 decimals, max 1,000,000), prices `NUMERIC(20,4)` in `[0.0001, 1,000,000]`, amounts `NUMERIC(20,2)`.
> - Holdings are a deterministic projection of the owner's append-only transaction ledger using average cost. The apply and correction RPCs rebuild the affected owner/ticker projection from the full ledger inside the same transaction; realized P&L exists only in the projection. A nightly integrity job re-projects every owner/ticker and raises an operational alert on any mismatch.
> - A back-dated Sell that would make the historical balance negative at any point is rejected for ordinary commands, not only corrections.

### §4.1 Analysis and decision support — replace the first bullet

> - Pre-market, intraday, post-market, and user-initiated runs submitted from a connected provider. The phase of a scheduled run is derived by the server from the exchange calendar and current session at `start_run`; the caller cannot assert a phase. A run outside any expected window is `on-demand`. The web app does not claim it can trigger a provider run.

### §4.1 User control and recovery — replace the backup bullet

> - Encrypted application-data backups written daily by a server-side job to private object storage (Cloudflare R2 free tier), a separately encrypted key-material object (token and pairing-code peppers, Edge secret manifest), a backup-age alert, documented restore steps, and a restore drill that ends with a synthetic user's provider token still authenticating.

### §5 Proposed architecture — replace the provider list in the diagram and the paragraph after it

> ```
>   +-- Claude Cloud Routine (release one; static scoped token via API-credential proxy)
>   +-- Future OAuth 2.1 MCP hosts (ChatGPT plugin; separate project, not promised)
> ```
> The frontend is a static React/TypeScript/Vite SPA served as Cloudflare Workers static assets with a `_headers` file; no Worker script and no service worker in release one. A web-app manifest enables home-screen install; there is no offline shell. All API responses carry `Cache-Control: no-store`.

### §6.1 Browser — replace the last paragraph

> Read-only screens query invoker-security views in the exposed `api` schema. All user-specific base tables live in the private `app` schema with row-level security enabled and forced; PostgREST exposes only `api`. Every mutation calls `app-api`, which runs as the signed-in user (user-context client, `SECURITY INVOKER` RPCs, RLS enforced). `app-api` holds no service-role or secret key.

### §6.3 External model runtimes — replace the first paragraph and add a closing paragraph

> Each provider connection has a `credential_type`. Release one implements `static_token`: one high-entropy random, revocable credential `connection_id.secret`, stored as a keyed digest with a server-held pepper, compared in constant time, shown once, and sent only in an authorization header. For Claude Routines it is stored as a cloud-environment **API credential** bound to the gateway's functions host only, never as an environment variable and never bound to the project's REST/Auth host. `oauth_access_token` is reserved for future MCP hosts and is rejected until separately reviewed.
>
> Per-connection quotas are enforced from Postgres: at most 12 gateway operations per run, 6 runs per owner per market day, 1 on-demand run per owner per hour; a global breaker disables non-operator connections at 60% of the monthly Edge Function quota and raises an operational alert.

### §6.4 Service-role use — replace entirely

> The service role is used only by `agent-gateway` and `telegram-portfolio`. Each request resolves exactly one owner from its credential or pairing before any database call, opens a transaction, and sets `app.owner_id` with `set_config(…, true)`. Every owner-aware RPC asserts that its owner argument equals `current_setting('app.owner_id')` and RLS policies on `app` tables accept the service path only for that owner. RPCs have a fixed `search_path`, are owned by a non-superuser role, and have `EXECUTE` revoked from `anon` and `authenticated` unless intentionally wrapped in `api`. A static test fails the build if `app-api` references a secret or service-role key outside the invitation module.

### §7.4 RLS policy shape — replace the SQL and the paragraph

> ```sql
> -- user path
> using (auth.uid() is not null and owner_id = (select auth.uid()))
> with check (auth.uid() is not null and owner_id = (select auth.uid()))
> -- machine path (service role inside a transaction that set app.owner_id)
> or owner_id = nullif(current_setting('app.owner_id', true), '')::uuid
> ```
> Every `app` table has `ENABLE` and `FORCE ROW LEVEL SECURITY`. Grants and policies are tested through PostgREST with real user JWTs, not only in SQL. The Supabase security advisor runs in CI against staging and any security-category finding fails the gate.

### §9.1 Connection modes — replace entirely

> Two credential types sit behind one contract: `static_token` (release one; Claude Routines) and `oauth_access_token` (reserved; MCP-hosted providers such as ChatGPT plugins, which require a public streamable-HTTP MCP server and an OAuth 2.1 authorization server — a separate project). User-funded API runners and local runners remain out of scope.

### §9.2 Launch provider support — replace entirely

> - **Claude:** supported through Claude Routines (research preview; Pro/Max/Team/Enterprise). The user creates a Custom-network cloud environment from the published connection kit, stores the scoped token as an API credential, and creates three weekday routines. Runs draw down the user's subscription and daily routine cap; the product shows this limitation.
> - **ChatGPT:** not available in release one. Classic scheduled tasks cannot call custom servers; ChatGPT Work plugins require OAuth 2.1 MCP hosting. Tracked as a separate design.
> - **Grok/xAI:** not available; no subscription-backed scheduled route exists.
> - Unsupported providers cannot be selected; the allow-list contains `claude` only.

### §9.3 step 5 — replace

> 5. A no-write handshake run executed **by the provider's real scheduler or "Run now"**, not by curl, proves: the token arrives via header, egress to the market-data hosts works, the contract version matches, the server-derived phase equals the expected one, and the run started inside the expected window. The server stores the provider's session URL on the run for the user's Runs screen.

### §9.4 Standard analysis submission — add after the rejection sentence

> For any actionable candidate the validator additionally requires every cited evidence block to have `retrieved_at ≥ run.started_at − 5 minutes`, at least one current-run market block and one current-run news/filing block, and rejects any evidence ID previously seen in another run (`evidence_reused`). Prior suggestions may be referenced only through `prior_suggestion_ids`.

### §9.6 — add a paragraph

> If a split, reverse split, symbol change, merger, or delisting is detected for a held ticker between the previous run and now, the holding enters `needs_review`: stop/target alerts for it are suppressed with a `corporate_action_pending` data warning, and the user must confirm the adjusted share count and levels through the command workflow. The ledger is never auto-adjusted.

### §9.8 Primary provider and second opinion — replace entirely

> Each user has one primary connection. Second-opinion runs are deferred past release one. There is no failover, voting, or averaging.

### §10 — insert after the run diagram

> `start_run` claims a unique active run per `(owner_id, market_date, phase)` with a lease that expires at the window's end. A second claim returns the existing run (`already_active`); `submit_analysis` for a superseded claim is rejected without writes. Expected windows are computed per calendar day, including early closes (intraday = open + 120 minutes and post-market = close + 10 minutes on early-close days), and are widened by ±60 minutes for 14 days after each daylight-saving change, during which a provider-side schedule mismatch produces a user-facing "re-check your schedule" notice rather than a missed-run alert.

### §11 — replace the second paragraph

> The atomic RPC takes a transaction-scoped advisory lock keyed on `(owner_id, ticker)` (so a first Buy with no holding row is still serialized), verifies the owner's ledger sequence equals `expected_version`, appends the transaction, rebuilds the owner/ticker projection from the ledger, advances at most one matching recurring plan, and records the receipt in one transaction. A retry with the same idempotency key returns the original receipt.

### §12 — add to Pairing and Commands

> Pairing and all commands are accepted only in a private chat where `chat.id = from.id`; group and channel updates are answered with a fixed refusal and never create commands. `telegram_updates` stores each `update_id` as a primary key with 30-day retention; ordering is never assumed. Callback confirmation requires an active link for `from.id` at callback time; `/unlink` cancels all pending commands. `callback_data` is an opaque ≤64-byte token.

### §13.1 — replace the screen list

> 1. Today 2. Portfolio 3. Activity 4. Research 5. Runs 6. Connections 7. Settings (timezone, notification preferences, expected phases). Admin functions (invitations, aggregate health) are a read-only health page plus a trusted CLI; there is no admin mutation UI in release one.

### §13.3 — replace the PKCE bullet and the reauthentication bullet

> - Email OTP is the primary sign-in; magic link (PKCE) is a desktop-only fallback with allow-listed callbacks. JWT lifetime is 15 minutes with refresh-token rotation; all sessions are revoked on deletion or token rotation.
> - Step-up: destructive actions (deletion, provider-token rotation, Telegram relink) require a fresh OTP verified within the last 5 minutes, recorded server-side per session.

### §14 — replace entirely

> Release one applies platform hard limits only. The schema includes `owner_policy_overrides` (may only be stricter) but no UI or command writes to it; historical decisions retain their policy version. Self-tuning remains disabled.

### §16 Free-tier backup plan — replace entirely

> A Supabase Cron job invokes a backup Edge Function daily. Using a `backup_reader` role through the session-mode pooler, it exports the enumerated `app` tables and an identity map (owner_id, encrypted email), encrypts the archive with `age` to an offline-held public key, and uploads it to a private Cloudflare R2 bucket with a scoped write-only token. A separate key-material object (peppers, Edge secret manifest, R2/SMTP token inventory — never Vault plaintext) is maintained the same way whenever a secret changes. Retention: 14 daily and 4 weekly. `last_backup_ok_at` is recorded; the operational channel is alerted if it exceeds 36 hours. Restore is exercised into staging before launch and quarterly, starting from R2 objects and the offline key only, and passes only if row/relationship digests match and a synthetic user's provider token still authenticates. Recovery point objective 24 hours; recovery time objective three business days for full production loss (including Telegram webhook re-registration and re-verification of each user's connection window). If R2's free tier ever ceases to cover this, the specification is amended before any change.

### §17 — add

> Custom SMTP on an operator-controlled domain is configured on both projects before Gate D. PostgREST exposed schemas are `api` only. All Edge Functions deploy with `--no-verify-jwt` and verify user JWTs in code against the project JWKS.

### §20 — replace the second paragraph

> Everything an incident runbook needs is stored in Postgres under retention (`gateway_requests`, `auth_events`, `webhook_events`, backup status) because platform logs are retained for one day on the current plan.

### §21 — replace the last bullet list

> Known and accepted non-zero costs: a domain for sending email (and optionally the app). Pre-agreed escalations: Supabase Pro if production is ever paused or exceeds 400 MB; a transactional-email paid tier if invitations exceed the free quota. No model API is used on any path.

### §22 — add to Gate C, D, E and G

> Gate C: a second Claude account completes the connection kit unassisted and produces a conforming handshake run. Gate D: custom SMTP delivers an OTP to a non-team address on iOS and Android. Gate E: backup-age alert proven by disabling the job; restore from R2 only. Gate G: consent-screen copy reviewed against §4.1.

### §24 decision 4 — replace

> 4. Claude Routines as the sole release-one connector; ChatGPT (OAuth/MCP) and Grok are separate future designs.

### §26 — add sources

> Add the OpenAI plugins MCP-server and authentication pages, the OpenAI scheduled-tasks help article, the Claude Code routines and cloud-environments pages, Supabase custom SMTP, passwordless, API keys, Edge Function limits, connecting-to-Postgres, OAuth 2.1 server, Cloudflare R2 pricing and static-asset headers, and GitHub schedule-event pages (links in the review's section C).

---

## H. Final implementation recommendation

**Explicit prerequisites before coding begins**

1. Amend the specification per section G and re-run the internal consistency review; the ChatGPT/Grok removal and the trust-path split change the implementation plan's shape, so the plan must be rewritten, not patched.
2. Register a sending domain and configure custom SMTP on staging; send one OTP to a non-team address.
3. Create the R2 bucket and scoped token; generate the offline `age` key pair and store the private key offline in two places.
4. Confirm `pg_cron`/`pg_net` are enabled on the free production project and that a daily Cron job counts as activity (observe one quiet week on staging).
5. Create a second Claude account (or borrow one) for the unassisted connection-kit test; write the kit first, then test it, then code the handshake against what actually arrives.
6. Decide the cost-basis method (average cost) and numeric domains in writing.
7. Freeze the release-one screen list and delete the deferred items from the design so no time is spent on them.

**Ordered internal delivery gates**

- **Gate A — Tenancy foundation:** `app`/`api` schema split, owner columns, composite keys, forced RLS with the two-path policy, migration verifier with parity digests, PostgREST-level cross-tenant suite (anon, A, B, revoked, forged JWT), advisor clean in CI.
- **Gate B — Owner-aware control plane:** ledger projection RPCs with advisory lock, fees, and back-dated checks; run exclusivity; evidence-freshness validator; server-derived phase; quotas in tables; `app.owner_id` machine path; static "no service role in app-api" test; Telegram hardening (private chat, set-based dedupe, unlink cancels).
- **Gate C — Claude connection lifecycle:** connection kit, API-credential-bound token, real handshake run, provider session URL on runs, expected-window monitor with DST/early-close calendar, conformance fixtures including morning-replay and reused-evidence vetoes; unassisted second-account test.
- **Gate D — Web product:** OTP sign-in via custom SMTP, seven screens, consent screens, export, deactivation/deletion request, step-up, CSP/`_headers`, no service worker, phone matrix test.
- **Gate E — Operations and recovery:** Cron→R2 backup with key-material object, backup-age alert proven, restore drill from R2 only, incident and rotation runbooks per boundary, rollback drill with routines paused.
- **Gate F — Controlled cutover:** as written in the document, plus verifying that Raj's own routine now uses the API-credential path and that the old `MARKET_AGENT_SECRET` is revoked.
- **Gate G — Invite-only launch:** one synthetic account through the whole kit, then one friend; re-run isolation, deletion, and export checks after the friend's first full market week.

**Conditions that must block friend onboarding**

- Any failing PostgREST-level cross-tenant test, forged-JWT test, or advisor security finding.
- Custom SMTP not delivering OTPs to non-team addresses on a phone.
- No successful restore from R2 objects alone within the last 30 days, or backup age over 36 hours at the moment of inviting.
- The unassisted connection-kit test not completed by a non-Raj Claude account.
- Any ledger integrity mismatch from the nightly re-projection during the owner soak.
- Consent screen not reviewed against §4.1's four disclosures.
- Any remaining `chatgpt`/`grok` option visible in the UI or selectable in the API.
- Any code path in the repository that can reach a brokerage — this remains, as the document says, an unchanging guardrail.
