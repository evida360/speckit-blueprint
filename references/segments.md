# Building an epic in segments

With `build: {mode: segments}` in the extension config, `/speckit-implement` on a blueprint feature does
not build task after task in one session. `blueprint.py segments start` plans the open tasks of tasks.md
into **stages**. A stage is one **serial segment** (the trunk that opens the chains, or a join that needs
several of them) or several **chains** that run at the same time. **Every segment is built by exactly one
subagent, task after task**, at the session's effort. The session that runs `/speckit-implement` only
orchestrates.

`blueprint.py` below is `python3 .specify/extensions/blueprint/scripts/blueprint.py`.

## Orchestrator (the session that runs /speckit-implement)

You write no code, tick no task and edit no file while a stage runs; the hook refuses it. You start
subagents, relay questions and answers, and run the joins. The script decides everything else.

1. `blueprint.py segments start`. With `ok: false`, fix tasks.md as its `errors` say (`blueprint.py
   tasks-graph` shows them) and run it again. Tell the user the `plan` line and `effort`, in one line. A
   `warning` about the permission mode: tell the user before starting anything (every subagent edit would
   ask here). `done: true`: nothing is open; return to `/speckit-implement`.
2. `blueprint.py segments next`. Start **every** item of `start` with the Agent tool **in one message**:
   `subagent_type`, `description`, `prompt` and `effort` exactly as printed (no `model`: they run on the
   session's). Then end your turn. Never start a segment yourself in the session, never give one segment
   to two subagents and never two segments to one subagent.
3. Each completion notification starts a turn: run `blueprint.py segments status` and do what its `next`
   says:
   - `waiting` segments: ask the user **all** their questions at once, each with its default and tag
     (AskUserQuestion for closed ones, four per call; open ones in prose). Do not wait for other segments
     to ask theirs. Then per segment: `blueprint.py segments answer <S>` with the answers verbatim on
     stdin, and SendMessage the printed `send` text to the printed `agent`. A product answer (behaviour,
     an AC, copy, data, scope) is an amendment: tell the user, let the other segments reach the join, then
     `blueprint.py segments stop` and `/speckit-blueprint-amend` (a gate, flow §4).
   - a subagent that returned without `segment done`: SendMessage it to finish the protocol. One that
     failed or cannot be resumed (a new session): `blueprint.py segments next --retry <S>`, and start the
     fresh subagent it prints.
   - every segment of the stage done: `blueprint.py segments join`. It checks the ticks and runs the gate
     of every repository the stage touched. Red: it adds a fix segment; run `segments next` and start it.
   - then `segments next` again, until it says every stage is built.
4. When `segments next` reports `done`, return to `/speckit-implement`: every task is ticked, and the
   review loop (`after_implement`) follows as usual.

A new session ("continue") on a half-built epic: `blueprint.py segments start --resume` plans the open
tasks again; the earlier subagents are lost and every unfinished segment gets a fresh one.

## Segment agent

You are the one subagent of one segment. You cannot ask the user anything; you cannot see the
orchestrator's conversation.

1. Your first command is `blueprint.py segment start <S>`. If it is refused, stop and return the refusal.
   It prints your tasks (with those already ticked), the files you own, the files of the segments
   running beside you, the notes of earlier segments, the answers you already have, and the skills to
   load.
2. Load every listed skill with the Skill tool before your first edit (the hook refuses edits until you
   do). Read only your stories in spec.md, plan.md, and the rules (`blueprint.py rules`).
3. Work through your tasks **one at a time, in the printed order**. Each works in its repository (the
   `[name]` tag; `blueprint.py context` gives the paths) with paths relative to it.
4. After each task: its checks, then `blueprint.py tick <ID>`. In a serial segment the checks are the
   repository gate (`blueprint.py gate <epic> --repo <name>` when configured). In a parallel stage other
   segments are mid-work in the same tree: run only the tests and lint of your own files, never a
   repository-wide formatter in fix mode; the full gate runs at the join.
5. Commands that share ports, a test database or caches with other segments:
   `blueprint.py exclusive <name> -- <command>` (one at a time across segments).
6. Never commit, push, stash, switch branches or reset; never write tasks.md, build.md, review.md, the
   decisions log, compiled files or the blueprint; in a parallel stage never install or add packages.
   The hook refuses them; leave your changes in the working tree.
7. A file of another running segment that your task needs, a package to add, a product question, an
   amendment, or an AC that looks wrong: do not work around it. Write a checkpoint:

   ```
   blueprint.py segment ask <S> <<'EOF'
   Q1 | T014 | closed | Which rounding does the daily total use? | A half-up · B banker's | A [BLUEPRINT — §12.4 ENT-Sale]
   Q2 | T015 | open | … | [NOT IN BLUEPRINT — searched: search "rounding", DECISIONS.md]
   Done: T012, T013 · Next: T014
   EOF
   ```

   One line per question: task, `closed` or `open`, the question, the options, and the default the
   blueprint decides with its tag (conventions §8). Then return `CHECKPOINT <S>: <IDs>` and stop. The
   answers come back to you in a message; continue from the task it names.
8. A technical decision nothing covers (conventions §8): `blueprint.py decision add` with the title on the
   first line of stdin, then **Context** (keeping `[NOT IN BLUEPRINT — searched: …]`), **Options**,
   **Decision**, **Consequences**. It numbers the record and indexes it; parallel segments never collide.
9. When every task is ticked: `blueprint.py segment done <S>` with your handoff notes on stdin: what you
   built, conventions the next segments must follow, decision IDs, the files you changed, anything left
   for the join. Then return `DONE <S>` and at most ten lines.

A fix segment (after a red join) has no tasks: its work is in its prompt (the gate output). It makes the
gate green without weakening it and ends with `segment done` like any other.

## How the plan is made

`blueprint.py tasks-graph` (also the `after_tasks` hook) reads tasks.md:

- A task without a suffix depends on the task before it; a `[P]` task on the task before its `[P]` group;
  the task after a `[P]` group on the whole group. `(depends on T004, T009)` replaces that default.
- Setup and Foundational come before every story, every story before Polish. Two tasks that name the
  same file are ordered (the earlier first).
- The open tasks in a topological order are cut wherever every task before the cut is an ancestor of
  every task after it. A block between cuts whose tasks fall apart into groups sharing no file and no
  dependency becomes a parallel stage, one chain per group (small chains joined, at most
  `build.max_parallel`); a block with a task that names no file stays serial. Neighbouring serial blocks
  are one segment.
