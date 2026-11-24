---
description: "After /speckit-implement on a blueprint feature (hook), or when the flow says so: the review loop. Each pass is a full, independent review at a guaranteed effort that fixes nothing and lists every defect; this session then fixes them all at its own level; a fresh pass reviews everything again, until one finds nothing. Then the handoff in review.md. Does nothing for other features."
---

# Review loop and hand off (after_implement hook)

The human reviewer sees a large diff produced quickly; their attention is the scarcest resource in the
process. A reviewer that did not write the code, is not swayed by the reasoning that produced it and does
not stop to fix things catches what the implementation missed, all of it in one pass. Follow
`.specify/extensions/blueprint/references/conventions.md`.

## User Input

```text
$ARGUMENTS
```

## You (the session that ran /speckit-implement)

1. Run `blueprint.py review-facts`. If `applies` is false, say nothing, change nothing and return.
   `review_loop` says where the loop stands: `next` is `review` (a fresh pass), `fix` (the last pass has
   `open` findings) or `done`; `next_pass` is the number of the next pass; `max_passes` the cap.
2. **Review pass.** Start a NEW subagent for every pass (conventions §3): `effort: <effort>` (default
   `max`), `model: <effort_model>` unless `inherit`. Never SendMessage to an earlier reviewer, and never
   give a pass the earlier findings as its scope: every pass reviews everything. Its prompt: "First run
   `python3 .specify/extensions/blueprint/scripts/blueprint.py require-effort review --pass <next_pass>`;
   if it is refused, stop and return the refusal.", then the facts JSON, this file's path, and "do the
   reviewer's steps below and return the pass number, the findings and the manual checks". If `effort`
   is not set, run the pass yourself with the same rules.
3. **Fix.** When the pass has findings, fix them all here, at this session's level (conventions §3.7):
   no subagent with an `effort`, nothing to ask the user about levels. Work in severity order; after each
   fix run that repository's gate; a test that proves a fix is seen red first. Mark each row's Status
   `fixed: <what, where>` in review.md; never rewrite the reviewer's text and never write `Status: DONE`.
   A finding only the user can decide (an amendment, an accepted risk, a manual check) is fixed last:
   fix the rest, then ask once, with the recommended answer tagged (conventions §8). Only the user accepts
   a finding: its Status becomes `accepted (<who>, <date>): <reason>`.
4. **Loop.** Run `review-facts` again and go back to step 2 for a fresh full pass. Stop when:
   - a pass is complete and found nothing (`next: done`): it wrote `Status: DONE` and the handoff;
   - this run has made `max_passes` passes;
   - no progress: two passes in a row without fewer findings, or the same finding back after it was
     fixed twice;
   - only findings the user must decide are left.
5. At a stop with findings left, `Status: IN PROGRESS` stays. List the open findings and the choices,
   each with its tagged recommendation. Never start another run on your own.
6. Tell the user, in their language: the passes (how many findings each), the status, what they must
   check by hand, and that the next epic starts in a fresh session with "continue". Do not commit or push
   unless they ask. With several repositories, merge in the table's order and the hub's branch last.

## The reviewer (one pass)

Review the epic as if someone else wrote it; the facts JSON lists the ACs, the checklist size, the
Definition of Done lines, repositories, diffs, gates and decisions.

- **You fix nothing.** You write only your own pass into `review.md`. Run read-only commands, the gates
  and the tests; no formatter or linter in fix mode, no `git add`, `commit`, `stash`, `checkout`, `reset`
  or `restore`. A hook refuses any other write.
- **You never stop at the first defect.** Go through every story, every AC, every checklist item and
  every Definition of Done line, to the end, and list every defect you find. The review checklist's
  "Stop at the first unanswered item and fix it; do not batch" is for an author reviewing their own work;
  it does not apply to you.
- A finding cites what it violates: an AC, an edge case, a checklist item (`CL-n`), a Definition of Done
  line (`DoD-n`), a constitution rule, a decision record or a blueprint ID (conventions §8). A preference
  that violates nothing goes under "Suggestions", not in the findings.
- Read earlier passes in `review.md` only after your own review is complete. A finding of theirs still
  present is reported again in your pass (`was RV1-3`).

1. **Gate.** Run `blueprint.py gate <epic>` (every repository with a configured gate; the plan's `check`
   for the rest), the epic-level tests in `quickstart.md` and the whole regression suite. Red is a
   finding; go on with the review.
2. **Coverage.** For every AC in `acs_needing_evidence`: the passing test that proves it, or the manual
   check with date and observation. An AC with neither is a finding. Check each story's edge cases the
   same way.
3. **Checklist.** Number the items of the review checklist (`review_checklist`) CL-1, CL-2, … in file
   order. Walk every item over the diff of every repository, story by story, and answer each one. Watch
   the agent-code failure profile: invented APIs (every external symbol traces to a pinned dependency),
   duplicated helpers, authorization and input-handling defects behind clean code, tests that assert too
   little.
4. **Diff size.** A story over the checklist's limit was mis-sized: a finding (worth an amendment for
   later epics).
5. **Definition of Done.** Every line in `review_loop.scope.dod` (DoD-1, DoD-2, …; spec.md "Success
   Criteria") gets ✔ with evidence or is a finding. Lines that mention `PROGRESS.md` are met by tasks.md
   and this review.
6. **Decisions and amendments.** Every decision the spec did not cover has its record (`DR-E03-n`) and
   index row; every changed AC went through `/speckit-blueprint-amend`.
7. **Write your pass** at the end of `review.md`, under `## Review passes` (create the heading once; keep
   `## VERIFY-AT-BUILD` and the passes before yours). The coverage lines must name every AC and every
   checklist item for every story, and every DoD line; status counts the epic done only when the last
   pass names them all and has no finding:

   ```
   ### Pass 2 — 2026-11-24 — effort max
   Coverage:
   E03-S01 AC-1 ✔ · AC-2 ✘ RV2-1 · CL-1 to CL-9 ✔ · CL-10 ✘ RV2-2
   E03-S02 AC-1 to AC-4 ✔ · CL-1 to CL-10 ✔
   DoD-1 to DoD-3 ✔ · Gates: evida-id green · evida-desk red (RV2-3)
   Findings: 3
   | ID | Severity | Where | Finding | Violates | Status |
   |---|---|---|---|---|---|
   | RV2-1 | high | evida-desk src/sale.ts:42 | … (was RV1-3) | E03-S01 AC-2 | open |
   Suggestions (not findings): …
   ```

   `Findings: none` and no table when the pass found nothing.
8. **Clean pass only:** when your pass found nothing and covers everything, write the handoff at the top
   of `review.md` (first line `Status: DONE`; keep the passes below). Status counts it only if every AC of
   every story has an evidence entry on the story's line(s); one entry may name a range (`AC-1 to AC-4`,
   `AC-1–4`, `AC-1…3`) or a pair (`AC-5/6`) when one proof covers each AC in it:

   ```
   Status: DONE
   # Handoff — E03 <Title> — <date>
   Stories: E03-S01 DONE · E03-S02 DONE · …
   AC evidence:
   E03-S01 AC-1 test/…::name · AC-2 manual 2026-11-16, observed … (one line per story, every AC)
   E03-S02 AC-1 to AC-3 test/…::name · AC-4/5 manual 2026-11-16, observed …
   Repositories: evida-id `feat/…` gate green (abc123) · evida-desk `feat/…` gate green (def456)
   CI: green | not run · staging: deployed | n/a, smoke n/n
   Review passes: 3 (pass 1: 7 findings, pass 2: 2, pass 3: none) · accepted: none | RV2-4 (…)
   Diff size: within limit | E03-S02 620 lines, over the 400 limit
   Deviations / amendments: none | A-E03-1, DR-E03-2
   Accepted risks: none | …
   Next epic preconditions: …
   Manual checks for you: …
   ```

   Otherwise the first line stays `Status: IN PROGRESS`.
9. With metrics on, at the clean pass add `Estimate vs actual:` and the line `blueprint.py metrics <epic>
   --brief` prints; with `metrics: commit`, also run `blueprint.py metrics <epic> --write`.
10. Return the pass number, the findings (ID, severity, where, what, what it violates) and the manual
    checks.
