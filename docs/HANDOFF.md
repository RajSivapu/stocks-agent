# v2.1 Handoff — continue from here (as of 2026-06-18)

Paste the prompt block below into a fresh Claude Code chat to EXECUTE the approved v2.1 plan via
subagent-driven development. (The earlier v2 go-live handoff is preserved at the bottom.)

---

```
Continue my personal stocks-agent v2.1 work (Claude Code, suggestion-only investing assistant).
Project: /Users/rajrupesh/Documents/Raj/stocks-agent

EXECUTE the approved v2.1 plan via superpowers:subagent-driven-development — dispatch a fresh
subagent per task, review between tasks. Invoke that skill FIRST. For ANY SKILL.md create/edit,
invoke superpowers:writing-skills FIRST, applied the LIGHT way (clean structure + the task's
grep/JSON verify + the Task 13 dry run as the real behavioral test — NOT heavyweight baseline cycles).

READ FIRST, in order:
1. Memory: stocks-agent-project.md — the v2 blocks + the latest v2.1 block.
2. APPROVED spec: docs/superpowers/specs/2026-06-18-project1-v2.1-intraday-discovery-and-learning-loop-design.md
3. APPROVED plan (execute this): docs/superpowers/plans/2026-06-18-project1-v2.1-intraday-discovery-and-learning-loop-implementation.md

WHAT v2.1 ADDS (4 additive features, NO rebuild of the v2 core):
1) Intraday run becomes a BOUNDED opportunity hunter (monitor open zones/holdings + a ≤25-call,
   ≤3-deep, compact-depth discovery scan; alert only on a real buy / zone trigger / stop hit; else
   silent radar+Watch logging). 2) New paper_watches table — owner's own hypotheses, separate from
   radar/holdings, with a you-vs-agent snapshot, shown in every daily brief. 3) Every buy shows
   entry + target + stop + valid-until (+ late-look safety). 4) Trailing stop on holdings (advisory,
   ratchet-UP-only; raise-suggestion in the daily brief, stop-HIT alert in the intraday).
Cadence drops to 3 runs/day (06:30 + ~12:00 intraday + 15:10).

CRITICAL ENV NOTES:
- Run all DB/lib code via .venv/bin/python locally (cloud Routine uses `python`). Prefix Bash with
  `cd /Users/rajrupesh/Documents/Raj/stocks-agent` (cwd resets between calls).
- DB tests run against LIVE Supabase and create+DELETE temp rows (TST*/TSTH/TSTP) — keep prod clean.
- Secrets: lib.config.secret() env-var-first, file-fallback. NEVER commit config/secrets.local.json,
  .mcp.json, .claude/. Always `git status` before committing; stage files explicitly (no git add -A).
  data/ (except tracked lessons.md) + config/portfolio.json are intentionally UNTRACKED — leave them.
- Each commit ends with: Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- A security-review hook runs on commits — address findings.
- The v2.1 spec + plan are already committed LOCALLY (633062c, 42deee0) but NOT pushed — pushing to
  main needs my explicit OK. Ask before pushing.

GUARDRAIL (unchanged): suggestion-only, read-only. NEVER add/use a trade- or stop-order tool.

AFTER all 13 tasks pass + the Task 13 dry run is green: invoke superpowers:finishing-a-development-branch,
then resume the OWNER-MANUAL go-live (healthcheck Routine + the THREE scheduled Routines in
claude.ai → Code → Routines per routines/README.md) — guide me, don't attempt to create them.

Begin by invoking superpowers:subagent-driven-development, reading the three files above, then
executing Task 1.
```

---

## State at this handoff (2026-06-18)

- **v2 = code/skills/data ALL implemented, committed & pushed on `main`.** Go-live (cloud Routines) is
  owner-manual and still pending.
- **v2.1 = brainstormed → spec → plan, all DONE and committed LOCALLY (not pushed):**
  - Spec `633062c`: `docs/superpowers/specs/2026-06-18-project1-v2.1-...-design.md`
  - Plan `42deee0`: `docs/superpowers/plans/2026-06-18-project1-v2.1-...-implementation.md` (13 tasks)
- **Next:** execute the 13-task v2.1 plan (subagent-driven), then `finishing-a-development-branch`, then
  the owner-manual go-live with the **3-run** cadence (cadence changed from 4→3 in v2.1).
- Today's data is **real prod data** (5 suggestions dated 2026-06-18, radar MU/TSM, 11 daily_snapshots, a
  regime line) — NOT test rows to clean up. Only the TST* rows created by the plan's tests get deleted.

---

## (Archived) v2 go-live handoff

The four scheduled Routines were re-cut to **three** in v2.1 (06:30 + one ~12:00 intraday + 15:10) — use
the updated `routines/README.md` after v2.1 ships. The original v2 go-live instructions (healthcheck
Routine first, then the scheduled Routines, Custom network allowlist incl. the Supabase host + 5 env-var
secrets, pooler fallback if the free-tier direct Postgres conn is IPv6-only, then a ~2-week Pro-usage
measurement) still apply for the manual claude.ai → Code → Routines step.
