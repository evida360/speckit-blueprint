---
description: "After /speckit-tasks on a blueprint feature (hook): check the task graph (dependencies, file paths) and show the stages the epic builds in. Does nothing for other features."
---

# Task graph (after_tasks hook)

tasks.md decides what can be built at the same time. This hook checks it deterministically and shows the
plan. Follow `.specify/extensions/blueprint/references/conventions.md`.

## User Input

```text
$ARGUMENTS
```

## Steps

1. Run `blueprint.py tasks-graph --check`. If `applies` is false, say nothing, change nothing and return.
2. For every entry in `errors` (an unknown or later task named in `(depends on …)`, a cycle, a duplicate
   ID, a compiled file in a task): fix tasks.md and run the command again, until it exits 0.
3. Read `warnings`:
   - "names no file": give the task its file paths (in backticks, relative to its repository). A task that
     names no file never runs beside another one.
   - "depends on no task of the repositories that provide for it": add the `(depends on …)` it needs on
     the provider's task, or explain in one line why the consumer task does not need it.
   - "no stage runs in parallel": look at each story's first task. If it needs only Setup and
     Foundational work, write that with `(depends on …)` naming those tasks; if it really needs an earlier
     story, leave it. Never invent independence to gain a stage.
4. Report the `plan` line (`S1(4) → S2a(3) ∥ S2b(2) → S3(3)`: a serial segment, then two chains at the same
   time, then a serial one) and `stats.speedup`, in one or two lines. With `mode: sequential` add that the
   epic builds task after task unless `build: {mode: segments}` is set in the extension config.
5. Return to `/speckit-tasks`.

## How dependencies are written (for step 2 and 3)

- A task without a suffix depends on the task before it. A `[P]` task depends on the task before its `[P]`
  group; the task after a `[P]` group depends on the whole group.
- `(depends on T004, T009)` at the end of a line replaces that default with the tasks it names, which
  must come earlier in the file. Write it on the first task of every story phase.
- Setup and Foundational come before every story; every story before Polish. Two tasks that name the
  same file never run at the same time.
