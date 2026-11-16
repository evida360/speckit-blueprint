---
description: "After /speckit-implement on a blueprint feature (hook), or when the flow says so: an independent review of the epic at a guaranteed effort, against its ACs and the review checklist, then the handoff in review.md. Does nothing for other features."
---

# Review and hand off (after_implement hook)

The human reviewer sees a large diff produced quickly; their attention is the scarcest resource in the
process. A reviewer that did not write the code and is not swayed by the reasoning that produced it
catches what the implementation missed. Follow `.specify/extensions/blueprint/references/conventions.md`.

## User Input

```text
$ARGUMENTS
```

## You (the session that ran /speckit-implement)

1. Run `blueprint.py review-facts`. If `applies` is false, say nothing, change nothing and return.
2. If `effort` is set (default `max`), start the review in a fresh subagent at that level (conventions
   §3). Its prompt: the facts JSON, this file's path, and "do the reviewer's steps below and return the
   review status and the manual checks for the user". If `effort` is not set, do the steps yourself.
3. Tell the user, in their language: the status, what they must check by hand, and that the next epic
   starts in a fresh session with "continue". Do not commit or push unless they ask. With several
   repositories, merge in the table's order and the hub's branch last.

## The reviewer

Review the epic as if someone else wrote it; the facts JSON lists the ACs, repositories, diffs, gates and
decisions.

1. **Gate.** Run `blueprint.py gate <epic>` (every repository with a configured gate; the plan's `check`
   for the rest), the epic-level tests in `quickstart.md` and the whole regression suite. Red → fix, never
   continue with red.
2. **Coverage.** For every AC in `acs_needing_evidence`: the passing test that proves it, or the manual
   check with date and observation. An AC with neither is not done. Check each story's edge cases the same
   way.
3. **Checklist.** Walk the review checklist (`review_checklist`) over the diff of every repository,
   story by story, and answer every item; fix what fails instead of noting it. Watch the agent-code failure
   profile: invented APIs (every external symbol traces to a pinned dependency), duplicated helpers,
   authorization and input-handling defects behind clean code, tests that assert too little.
4. **Diff size.** A story over the checklist's limit was mis-sized; say so (worth an amendment for later
   epics).
5. **Definition of Done.** Every line of the epic's Definition of Done (spec.md "Success Criteria") gets ✔
   with evidence, or the epic stays IN PROGRESS. Lines that mention `PROGRESS.md` are met by tasks.md and
   this review.
6. **Decisions and amendments.** Every decision the spec did not cover has its record (`DR-E03-n`) and
   index row; every changed AC went through `/speckit-blueprint-amend`.
7. **Write `review.md`** (keep its `## VERIFY-AT-BUILD` section). `Status: DONE` only when 1–6 all pass;
   status counts it only if every AC of every story has an evidence entry on the story's line(s). One entry
   may name a range (`AC-1 to AC-4`, `AC-1–4`, `AC-1…3`) or a pair (`AC-5/6`) when one proof covers each
   AC in it:

   ```
   Status: DONE | IN PROGRESS
   # Handoff — E03 <Title> — <date>
   Stories: E03-S01 DONE · E03-S02 DONE · …
   AC evidence:
   E03-S01 AC-1 test/…::name · AC-2 manual 2026-11-16, observed … (one line per story, every AC)
   E03-S02 AC-1 to AC-3 test/…::name · AC-4/5 manual 2026-11-16, observed …
   Repositories: evida-id `feat/…` gate green (abc123) · evida-desk `feat/…` gate green (def456)
   CI: green | not run · staging: deployed | n/a, smoke n/n
   Self-review: <n> items failed and fixed (list), <n> accepted with reason (list)
   Diff size: within limit | E03-S02 620 lines, over the 400 limit
   Deviations / amendments: none | A-E03-1, DR-E03-2
   Accepted risks: none | …
   Next epic preconditions: …
   Manual checks for you: …
   ```

8. With metrics on, add `Estimate vs actual:` and the line `blueprint.py metrics <epic> --brief` prints;
   with `metrics: commit`, also run `blueprint.py metrics <epic> --write`.
9. Return the status and the manual checks.
