# Personal Stock Agent V1 — Protected Rollout Record

Status: **implementation deployed; the next existing scheduled V1 intelligence/report receipt is pending**.

## Immutable release boundary

- Owner-only, friend invitations disabled, suggestion-only, brokerage-free, and zero incremental cost.
- Browser surfaces are authenticated GET-only and cannot trigger research, Telegram, or financial mutation.
- Alert-class enablement remains disabled pending a real shadow example and separate owner approval.
- No duplicate live scheduled run was started for verification.

## Candidate, review, and CI

- Deployed implementation SHA: `688d473b4696ce699965adc16c213cefdeb4dc6a`.
- GitHub merge commit on `main`: `681905290510790160b5d3f712f71187527cf720`.
- Pull request: `https://github.com/RajSivapu/stocks-agent/pull/1` (merged 2026-09-04).
- Exact-candidate CI: `https://github.com/RajSivapu/stocks-agent/actions/runs/33916684106`, success at `688d473b4696ce699965adc16c213cefdeb4dc6a`.
- Local full gate: 699 passed and 20 skipped; typecheck, lint, license, production build, bundle scan, and Playwright gates passed.
- Independent review: the original `a5d4296` candidate was blocked. The five blocking boundaries were fixed and accepted; subsequent migration/runtime deltas through `688d473` were independently accepted with no unresolved Critical or Important finding.

## Protected dry-run

- Fixture-backed on-demand run ID: `00000000-0000-4000-8000-00000000c600`.
- Packet ID: `1c2e78ca-995a-5aa0-9457-75877fe1802b`.
- Twelve protected table deltas were zero; Telegram message IDs were empty; holdings and plans were unchanged.
- The dry-run was deliberately write-free/send-free. Provider quota/source receipts remain a scheduled-production gate because this fixture had no live source context.

## Database and function deployment

- `20260906_owner_dashboard_read_role.sql`: applied, SHA-256 `7aa056b052a5e5b8cb85a1afdf047acfbe7b93c9c789aef64d48d77b7e9ad2c0`.
- `20260907_market_intelligence.sql`: applied, SHA-256 `79aeb682eba5ddaa2832d72c8ffea24caa2b7904e19c15704605bd64953367e0`.
- `20260908_owner_dashboard_intelligence_read_role.sql`: applied, SHA-256 `2fb4feb46b495afbea32701f5a9e102d8377d5044d178fee93b15d5842ed191c`.
- Scoped dashboard runtime login is enabled and can read the allowlisted direct columns; it has no financial-write or application-function execution authority.
- `market-briefing-gateway`: active version 30 after secret refresh; deployed source tree SHA-256 `58d7a7a01d164279539e3b57f2b0b9169b75f97e6c33742bda7551b005dce8b2`.
- `owner-dashboard-api`: active version 3; deployed source tree SHA-256 `f0e649361555847b51f81d73f2be979b7c940aee907d1727a1dcced34ff041c8`.
- A fresh management-plane download compared 16 gateway files and 11 dashboard files against the candidate with zero mismatches.

## Static site and access

- Private Site version 2: `appgprj_6a9a35fb60648191b30e2bb973a2347f~appgver_86bd49ad514c81918e96830844768a4d`.
- Archive content hash: `sha256:ee61fff48177c61b210394c59bcd53a2f4d283feb4975195760055ce983ff341`.
- Production deployment: `appgdep_6a9b2be35a388191b24c4fd870d87d7d`, succeeded.
- URL: `https://personal-stock-agent.rupesh-sivapu.chatgpt.site`.
- Deployment used the verified private-owner path: custom access, one allowed owner account, no groups, and no external visitors.

## Production canaries

- Anonymous `/v1/meta`: 401 `unauthorized`.
- Authenticated temporary non-owner `/v1/meta`: 403 `owner_only`; the temporary user was then deleted.
- Owner `/v1/meta`: 200, contract version 1, request ID present, fresh policy metadata, exact Site CORS, and `cache-control: no-store`.
- Owner GETs for Today, Portfolio, Ideas, System, Intelligence, and Reports: all 200.
- The initial owner read exposed an Edge-runtime receiver-binding fault in the default UUID generator. Commit `688d473` fixed it, focused Deno verification passed 11/11 plus typecheck, exact-SHA CI passed, and the production canary then passed.

## Rollback evidence

- Two failed initial dashboard attempts exercised the production rollback path.
- Each exercise removed the new dashboard authority, disabled the runtime login, and restored the prior gateway from the verified rollback checkout/source hash.
- The successful deployment was performed only after rollback recovery and blocker correction.

## Remaining scheduled gate

The 2026-09-04 post-market run (`9dfee973-e678-4910-8e1e-a1f541a68806`) completed successfully but began before V1 reached `main`; it produced no V1 intelligence or report rows and is not claimed as V1 evidence. The existing rollout heartbeat is scheduled to inspect the next eligible routine after the merge, without starting a duplicate. C6 and the full V1 completion claim remain fail-closed until one scheduled run provides the linked analysis run, intelligence run, packet/hash, report/hash, gateway publication receipt, source/quota receipts, and dashboard/database reconciliation.
