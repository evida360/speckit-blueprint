---
description: "Before /speckit-implement on a blueprint feature (hook): load its spec and rules and check its preconditions. Does nothing for other features."
---

# Load build context (before_implement hook)

`/speckit-implement` reads tasks.md, plan.md and the design files, but not spec.md, which holds the
acceptance criteria, edge cases and exact copy that decide whether the work is right. This hook puts it
in front of the implementation. Follow `.specify/extensions/blueprint/references/conventions.md`.

## User Input

```text
$ARGUMENTS
```

## Steps

1. Run `blueprint.py context`. If `applies` is false, say nothing, change nothing and return to
   `/speckit-implement`.
2. Run its `rules_command`: the rules that govern this feature (other blueprints' blocks do not apply).
3. Read the file at `spec` in full, once.
4. If `problems` is not empty (dependencies not done here, the blueprint changed since compile, a
   repository missing or on the wrong branch), stop and report them; the user decides.
5. Re-check every `[VERIFY-AT-BUILD]` item in `preconditions_and_verify_at_build` that this epic depends
   on (a search or a sandbox request), and record `✔ date` or `✘ date + what changed` under a
   `## VERIFY-AT-BUILD` heading at the top of the feature's `review.md` (create it with
   `Status: IN PROGRESS` as its first line). A `✘`: stop and report.
6. Hold these rules for the whole implementation (the constitution has the rest):
   - Tick each task `[X]` in tasks.md as soon as it is done and its gate is green, one at a time. The ticks
     are the resume point, and the progress and story timings in status, metrics and the dashboard.
   - Each task works in its repository (`repositories[].path` in the context facts) and gives paths
     relative to it. After a task, run that repository's gate (`blueprint.py gate <epic> --repo <name>`
     when the config has one); keep it green and never weaken it.
   - A decision the spec does not cover: the next ID is `next_decision_id`; add its record and index row
     to `decisions_log` in the same commit. An AC that is wrong or impossible: stop and use
     `/speckit-blueprint-amend`.
   - Read source files over 200 lines by range (`grep -n` first); do not re-read unchanged files. Anything
     outside the feature folder: `blueprint.py section` / `find`, not the blueprint.
   - Lines that mention `PROGRESS.md` map to ticked tasks and `review.md`.
7. If the session is already long (another epic was built in it, or a few hundred thousand tokens of
   context), say so and suggest finishing the current story, then continuing in a fresh session with
   "continue"; `/speckit-implement` resumes from the first unchecked task.
8. Return to `/speckit-implement`.
