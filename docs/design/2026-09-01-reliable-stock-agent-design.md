# Reliable Stock Agent Design

**Date:** 2026-09-01
**Status:** Approved for implementation
**Owner:** Rajrupesh

## Objective

Upgrade the existing suggestion-only stocks agent so that:

1. every scheduled market decision is based on a fresh analysis rather than blindly reusing a
   morning plan;
2. portfolio changes can be recorded safely from Telegram without a model API;
3. ChatGPT provides a usage-conscious independent weekly review and an optional manual second
   opinion before the owner acts on a recommendation; and
4. every model run and portfolio mutation is auditable, fail-closed, and incapable of placing a
   brokerage order.

The system remains a decision-support and recordkeeping tool. It never places, modifies, or cancels
a trade.

## Constraints

- No OpenAI, Anthropic, or other paid model API is introduced.
- Existing Claude Cloud Routines remain the scheduled analyst.
- Telegram's free Bot API is the transport for notifications and portfolio-record commands.
- Supabase's existing project is the persistent store and hosts the Telegram webhook as an Edge
  Function.
- ChatGPT scheduled work uses the owner's included Pro allowance and runs only once weekly.
- The three routine runs must be independent. Earlier recommendations are historical evidence, not
  instructions for a later run.
- Missing, stale, or contradictory market data cannot produce a new Buy/Add/Trim/Exit alert.
- Portfolio-changing Telegram commands require an explicit Confirm action.
- The first release is single-owner. Friend sharing is intentionally deferred until holdings and
  row-level policies are redesigned around an owner identifier.

## Architecture

### 1. Claude analyst runs

Claude Cloud Routines continue to run three times on weekdays:

| Run | Time (CT) | Purpose | Actionability |
|---|---:|---|---|
| Pre-market | 06:30 | Overnight news, portfolio risk, scenarios, monthly planning | Provisional; no new intraday Buy trigger from a previous-close quote |
| Intraday | 12:00 | Fresh macro, holdings, open ideas, and bounded opportunity re-underwrite | Actionable only after a fresh evidence and risk pass |
| Post-market | 15:10 | Close-based portfolio review, snapshots, grading, and learning | Actionable only for a newly confirmed stop/target/thesis break |

Each run creates an `analysis_runs` row before analysis and finalizes it after delivery/writes. The
row records run kind, timestamps, data-as-of time, status, symbols, source health, write counts,
Telegram message IDs, and a concise summary. Suggestions and observations optionally reference the
run ID.

The intraday run must not treat entry into a morning zone as sufficient evidence. An open suggestion
only adds the ticker to the fresh candidate set. Before an alert, the routine must fetch a fresh
quote, refresh relevant news and macro inputs, re-run the bull/bear/risk analysis, recompute the
zone/stop/target, and pass a checker step. A materially changed thesis, stale quote, failed source,
or risk veto suppresses the alert and records the reason.

### 2. Data freshness contract

`lib.marketdata.quote()` will return:

- `price`
- `prev_close`
- `day_pct`
- `as_of` as an ISO-8601 UTC timestamp when Yahoo supplies one
- `market_state` when Yahoo supplies it
- `source` equal to `yahoo-chart`

The analyst skill applies these rules:

- Pre-market quotes may represent the prior close and must be labeled provisional.
- Intraday and post-market actionable conclusions require a quote timestamp no older than 20
  minutes during regular market hours.
- If freshness cannot be established, the run may report observations but cannot issue a new
  Buy/Add/Trim/Exit recommendation.
- A recommendation records its evidence timestamp in the run log.
- Conflicting primary and fallback prices create a data-quality veto rather than a guessed value.

### 3. Analyst and checker passes

Every actionable conclusion uses two explicit passes within the same Routine session:

1. **Analyst pass:** builds the bull case, bear case, valuation/technical context, catalyst,
   invalidation, position sizing, and confidence from freshly fetched evidence.
2. **Checker pass:** verifies evidence freshness, ownership and concentration, arithmetic,
   earnings/event collision, zone/stop/target derivation, prior-plan leakage, and notification
   policy.

The checker can approve, downgrade to Watch/Hold, or veto. It cannot upgrade the analyst's
confidence. This is a structured second pass, not a claim that two independent models were used.
The optional ChatGPT manual review is the genuinely separate-model opinion before a real-money
decision.

### 4. Telegram portfolio recorder

The current bot is extended from send-only to two-way communication through the Telegram Bot API.
Telegram posts each message or button callback to a Supabase Edge Function named
`telegram-portfolio`.

The function has no model. A deterministic parser accepts these operations:

- `bought 2 AAPL at 210 growth`
- `/buy AAPL 2 210 growth`
- `sold 1.5 AAPL at 225`
- `sold all NVDA at 210`
- `/sell NVDA all 210`
- `move AAPL stop to 195`
- `/stop AAPL 195`
- `/portfolio`
- `/help`

Ticker symbols are normalized to uppercase. Quantities and prices must be positive finite decimals.
A new holding requires `core`, `growth`, or `speculative`; an existing holding keeps its current
bucket. A sell cannot exceed the recorded shares. Ambiguous or unsupported text changes nothing and
returns a short usage example.

### 5. Confirmation and atomic mutation

For Buy, Sell, and Stop changes:

1. The webhook verifies the Telegram webhook secret, owner chat ID, and owner user ID.
2. The Telegram `update_id` is inserted once; duplicates return safely without reapplying work.
3. The parser and current holdings produce a preview.
4. A pending command row stores the parsed values, expected current shares, expiry, and preview.
5. The bot replies with Confirm and Cancel inline buttons.
6. Confirm invokes a `SECURITY DEFINER` PostgreSQL function that locks the command and holding,
   checks identity, expiry, pending status, and expected shares, then updates `transactions` and
   `holdings` in one database transaction.
7. The function marks the command applied and returns the resulting position and realized P&L.
8. The bot edits the preview message with the final result. Cancel marks the command cancelled.

Pending commands expire after 15 minutes. A changed holding makes an older confirmation stale and
forces the owner to submit the command again. Telegram never calls a brokerage.

### 6. Telegram security

- Rotate the Telegram bot token before deploying the webhook because the previous local token was
  exposed during repository inspection.
- Rotate the Finnhub and Alpha Vantage keys found in plaintext local Codex configuration.
- Add `.codex/` and `.DS_Store` to `.gitignore`; never commit machine-local MCP configuration.
- Configure Telegram `setWebhook` with a high-entropy `secret_token`.
- Supabase Edge Function JWT verification is disabled only because Telegram cannot send a Supabase
  JWT. The function instead requires the exact Telegram secret header and owner identifiers.
- Keep `SUPABASE_SERVICE_ROLE_KEY`, bot token, webhook secret, owner chat ID, and owner user ID only
  in Supabase function secrets.
- Use constant-time comparison for the webhook secret.
- Restrict accepted update types to `message` and `callback_query`.
- Do not log tokens, authorization headers, or full Supabase responses.
- The command audit table stores user-entered trade records; it does not store unrelated chat text.

### 7. Weekly ChatGPT audit

A read-only script builds a compact weekly packet from bounded Supabase queries:

- current holdings;
- recent owner transactions;
- recent Buy/Watch/Avoid suggestions;
- their available grades;
- recent regime lessons/post-mortems; and
- the latest relevant snapshots.

A local ChatGPT scheduled task runs Friday at 16:30 America/Chicago using GPT-5.6 Terra with high
reasoning. It reads the weekly-audit skill, runs the packet builder, and reports:

- stale or contradictory holdings/risk fields;
- recommendations whose evidence or outcome conflicts with the stated thesis;
- concentration, missing stops, and upcoming-event gaps;
- repeated analytical failure patterns; and
- concrete process improvements.

It performs no DB writes, sends no Telegram messages, and makes no trade recommendation. The owner
uses GPT-5.6 Sol manually only when requesting a separate opinion before acting on an actionable
trade. The scheduled task requires the computer and ChatGPT desktop app to be running.

### 8. Failure behavior

- **Telegram authentication failure:** return a generic 401/403; no Telegram reply and no DB write.
- **Duplicate Telegram update:** return success without repeating the command or reply.
- **Parser ambiguity:** send help text; no pending command.
- **DB failure before confirmation:** send a temporary-error reply; no portfolio mutation.
- **DB failure during confirmation:** PostgreSQL rolls back both transaction and holding changes;
  command remains pending or records an error without a partial trade.
- **Stale confirmation:** mark rejected and ask the owner to resubmit.
- **Market data stale/missing:** suppress new actionable advice and record `partial`/`failed` source
  status in `analysis_runs`.
- **Telegram delivery failure:** record the failed status; never claim a message was sent.
- **Routine crash:** leave the run status as `failed` with a bounded error summary on the next safe
  finalization attempt.
- **Weekly audit unavailable:** the stock routines continue normally; no hidden fallback API call.

## Data model

### `analysis_runs`

One row per Claude run with a UUID primary key, run kind, lifecycle timestamps, status, data-as-of,
source status JSON, symbols JSON, write-count JSON, Telegram message IDs JSON, summary, and bounded
error text.

### `portfolio_commands`

One row per accepted Telegram mutation with UUID primary key, unique Telegram update ID, owner
identifiers, operation, ticker, quantity, price, bucket, expected shares, stop, status, preview JSON,
confirmation message ID, expiry, application timestamp, realized P&L, result JSON, and bounded error
text.

### Existing tables

- `transactions` receives `source='telegram'` for applied Telegram trades.
- `suggestions` gains nullable `run_id` and `evidence_as_of`.
- `stock_observations` gains nullable `run_id`.
- `holdings` remains the current-position source of truth.

## Testing strategy

### Deterministic tests

- Parser tests cover natural phrases, slash commands, `all`, decimals, ticker normalization,
  missing bucket for a new buy, malformed/negative values, unsupported text, and injection-like
  input.
- Market-data tests verify timestamp and market-state extraction.
- Weekly packet tests verify bounded, redacted, stable output.
- DB helper tests verify analysis-run lifecycle and bounded query construction where credentials are
  available.

### Database verification

After applying the migration to Supabase:

- create a disposable pending Buy and confirm it;
- verify exactly one transaction and one holding mutation;
- replay the same update and verify no duplicate;
- create a stale Sell and verify rejection;
- exercise Cancel and expiry;
- delete all disposable `TST*` data.

### Behavioral evals

- Intraday cannot alert solely because a morning zone was entered.
- Stale or missing timestamps veto actionable advice.
- Checker cannot upgrade confidence.
- Run summaries match actual Telegram and DB calls.
- Telegram mutations require confirmation and owner identity.
- The weekly ChatGPT audit is read-only.

## Deployment sequence

1. Rotate exposed credentials and update local/cloud secret stores.
2. Apply the SQL migration through the Supabase SQL editor.
3. Set Supabase Edge Function secrets.
4. Deploy `telegram-portfolio`.
5. Register the Telegram webhook with the secret token and restricted update types.
6. Test `/help`, `/portfolio`, Cancel, a disposable Buy/Sell, duplicate replay, and unauthorized
   input.
7. Update the three Claude Routine instructions and manually dry-run each revised kind.
8. Enable the weekly ChatGPT scheduled task.
9. Observe one week of run logs before treating the new alerting behavior as operationally proven.

## Non-goals

- Brokerage integration or trade execution.
- Free-form model interpretation inside Telegram.
- Multi-user/friend accounts in the current schema.
- Automatic numerical strategy tuning.
- A promise that analyst output is correct or risk-free.
