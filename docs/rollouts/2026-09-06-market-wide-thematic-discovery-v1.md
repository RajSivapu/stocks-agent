# Market-Wide Thematic Discovery V1 — Release Candidate Record

Status: **Tasks 1–10 are complete locally; exact-candidate review, protected release, and normal
scheduled receipts remain pending.**

This record defines the V1-C3 market-wide release boundary. It does not authorize deployment,
provider collection, Telegram delivery, schedule changes, brokerage execution, Alert V3, or any
production mutation. Production checkpoints remain open until the evidence below exists.

## Immutable implementation boundary

- Plan base: `17cbe10` on `origin/main`.
- Locally reviewed Tasks 1–10 boundary: `18386b5c76c7a7aa8b3eeda870b12d3aba2399e1`.
- Local Task 11 release-candidate identity: the commit containing this record. Its exact SHA must be
  captured in the independent review, protected CI, and release receipts.
- Product boundary: owner-only, suggestion-only, brokerage-free, and zero incremental cost.
- Market claim: bounded cross-sector discovery. `complete_market_coverage` is always `false`.
- Alert V3 remains disabled and shadow-only.

| Task | Closing commit | Implemented result |
|---|---|---|
| 1 — capability planner | `cffb47d` | Versioned source capabilities, fair stages, phase budgets, and required-baseline fail-closed planning. |
| 2 — protected discovery ledgers | `e0411ae` | Additive persistence, exact hashes, ACLs, export, restore, and recovery. |
| 3 — dated security reference | `2e6b8ff` | SEC reference transfer, immutable pins, restarts, and bounded capacity. |
| 4 — official sources and cursors | `899b69d` | Bounded official-feed collection, durable cursors, request receipts, and truthful partial coverage. |
| 5 — event-first resolution | `284ad02` | Ticker-independent events, issuer resolution, reverse discovery, value chains, and syndication controls. |
| 6 — adaptive enrichment | `157a826` | Frozen enrichment stages and primary-document exposure facts with explicit materiality. |
| 7 — broad screens | `e2f734f` | Durable feasibility states with zero transport for unavailable screens. |
| 8 — research/action split | `9f71e61` | Research candidates survive unknown suitability; unbacked action eligibility is rejected. |
| 9 — theme memory | `95ffbfb` | Bounded cross-run episodes, research nominations, owner projection, and exact recovery. |
| 10 — acceptance/calendar | `18386b5` | Thematic, held-out, adversarial, pressure, recovery, capability, and 2026–2028 NYSE calendar gates. |

The latest full local Task 10 gate passed Python 1,469 tests with 3 skipped and 4 credentialed tests
deselected, Node 71, Deno 332, package tests 7 + 53, and Playwright 24 with 1 skipped. These are local
results and do not establish deployment or live provider behavior. The Task 11 release-candidate
gate passed Python 1,480 tests with 3 skipped and 4 credentialed tests deselected, Node 71, Deno 333,
package tests 7 + 53, and Playwright 24 with 1 skipped; typecheck, lint, license, build, and bundle
checks also passed.

## Additive migration boundary

Market-wide discovery starts at `20261005_market_wide_discovery.sql` and ends at
`20261012_theme_memory_research_nominations.sql`. The `20261004` artifact is the separate immutable
production-schema reconciliation baseline. There is no `20261004_market_wide_discovery.sql`, and the
release must not invent or apply one.

| Migration | SHA-256 |
|---|---|
| `20261005_market_wide_discovery.sql` | `708df0bf998e025158294c1902d147cead6cc1e08dd3e246aa9dc8f5465dafea` |
| `20261006_reference_snapshot_transfer.sql` | `97548a0dbd92a19b7a8e8cac60fe5024a93ee93eaa5257c71932274e3cd25f33` |
| `20261007_discovery_cursor_context.sql` | `789896fe6eef69de41eca22339c98e1f46f6f717ca086878c79910572f89a8a6` |
| `20261008_official_source_completion_contract.sql` | `4e63c3aea0ba21b1e646f550fb712a91cd4dd98a0c4618a3657cb7171263d8f8` |
| `20261009_reference_issuer_names.sql` | `a6f20e7b50f6ecf091e0d5ef73c2772587caebc30135e3add8adefecab2d6094` |
| `20261010_bounded_adaptive_enrichment.sql` | `a3a195b99b2059125bd595f2ca2f1a382fb2c84b8a8a212add7ff8144c5a5d65` |
| `20261011_research_suitability_packet_contract.sql` | `f6e178ef986265d6008dcc381293e05e61802c2f90465b4c69b7410ab668dbe6` |
| `20261012_theme_memory_research_nominations.sql` | `a7274c8c4af3046cbbf6145e169a79e1852edcd8ccbdfa0b8885a3da8c9c365d` |

Every migration above and `sql/schema.sql` must remain byte-identical to the Task 10 parent while
Task 11 changes only the release controls and records. The protected release uses the repository's
complete immutable migration manifest and applies only byte-verified pending versions.

## Changed release components

The approved candidate changes protected database state, runtime behavior, and the owner audit UI.
It therefore requires the protected multi-component release path for the exact reviewed SHA:

- the eight additive discovery migrations above;
- `market-briefing-gateway`, `owner-dashboard-api`, and `telegram-portfolio` Edge Functions;
- scheduled-runtime approved-domain and credential-presence configuration, without recording secret
  values;
- the static owner Site built by the checked-in supply-chain verifier.

A Site-only publication cannot release this candidate. The protected path must capture encrypted
pre-release database/function/runtime state, validate the current deployment, apply the exact
candidate, and read back migration bytes, protected relations and ACLs, runtime source configuration,
all three Edge source trees/configuration, canaries, and recovery identities. The owner Site then
needs a separate exact-asset receipt proving one allowed owner, no groups, zero external visitors,
the expected API origin, and the login/recovery flow.

## Source capability status and limits

The required zero-key baseline is SEC company-ticker reference plus GDELT thematic search. A normal
V1-C3 receipt must independently derive the due required-baseline capabilities from the checked-in
policy and prove each one with parsed, persisted `success_empty` or receipt/item/provenance-backed
`success_nonempty` evidence. An empty market result is valid; an empty or missing receipt is not.

Bounded optional routes include Federal Register, White House, DOE, keyless EIA news, Defense,
shortlisted SEC submissions/Company Facts/filings, and only explicitly configured macro or existing
free-key enrichment. Optional failures remain visible and do not silently become baseline success.
The Nasdaq symbol directory remains disabled pending an exact HTTPS route and terms review. EIA
statistics remains `configuration_missing` without an existing owner-approved free key.

Known limits are part of the release contract:

- all six Yahoo broad-screen transports are disabled because their automation, API, and robots
  boundaries are not approved;
- SEC Form 4 transport is unsupported; only offline parsing and insider-cluster semantics exist;
- SEC's company-ticker file reports `scope_not_guaranteed` and is not claimed as a complete exchange
  universe;
- primary materiality accepts only explicit, canonical percentage-of-revenue claims from supported
  primary documents;
- every V2 action lane remains empty because there is no protected typed issuer-valuation ledger;
- sources, requests, items, pages, bytes, themes, candidates, and memory are deliberately bounded;
- the canonical NYSE calendar covers 2026–2028 and fails closed beyond the maintained range.

## Exact protected release sequence

1. Commit one final local candidate and complete independent whole-change plus focused
   security/recovery review on that exact SHA. Resolve every Critical, Important, P1, and P2 finding.
2. Push the reviewed PR head, pass CI on that exact head, merge without an unreviewed descendant,
   then pass the named CI workflow on the exact `main` SHA.
3. Run the manual protected release with the exact approved PR head, exact `main` candidate, exact
   merged PR, and exact-main CI run. Release the migration manifest, all three Edge Functions,
   protected runtime configuration, and owner Site as one candidate-bound operation.
4. Retain the encrypted pre-release component capture until migration/function/runtime readback,
   owner/anonymous/non-owner canaries, and the separate owner Site access/asset receipt all pass.
   Recover from the captured identity if any post-mutation gate fails.
5. Wait for the existing normal schedule. Do not trigger collector, provider, Telegram, or another
   scheduled run to manufacture evidence.
6. Verify the normal run twice: first as an operational chain, then as the V1-C3 discovery capability
   chain. Keep both results separate from the protected release receipt.
7. Close V1-C3 and any reopened V1 checkpoint only after its own production evidence passes.

The existing-runtime attestation and Site v9/v8 receipts describe historical/current-runtime state.
They are not the rollback identity for a future market-wide release. The future protected release
must record its own exact pre-release component identities and encrypted artifacts before mutation.

## Evidence ledger

| Evidence class | Status | What it proves |
|---|---|---|
| September 8 current-runtime operational receipt | Historical/current-runtime only; preserve separately | The already deployed chain operated normally. It cannot prove the new source plan or V1-C3 capability. |
| Candidate protected release receipt | **Pending** | Exact reviewed SHA, main/PR/CI binding, migration/function/runtime readback, owner API canaries, rollback readiness, and separate owner Site receipt. |
| Candidate normal operational receipt | **Pending** | One existing scheduled analysis/intelligence/report/publication or explicit-suppression chain completed without duplicate dispatch. |
| Candidate V1-C3 capability receipt | **Pending** | Required capability plan, reference/cursor/stage lineage, outside-watchlist discovery semantics, research/action integrity, and every due required baseline source outcome. |

V1-C3 and all reopened production checkpoints remain unchecked. Failed, disabled, unsupported,
uncertain, deferred, configuration-missing, or quota-blocked required capabilities keep V1-C3 open.
Alert V3 remains a later, separately approved shadow/canary rollout.
