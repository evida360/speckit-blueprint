---
description: "Internal: speckit-blueprint's handler for Claude Code events (session start, tool use, stop). Not meant to be run by hand."
scripts:
  py: scripts/blueprint.py hook
---

# Blueprint event handler (internal)

This command exists so Spec Kit can register `blueprint.py hook` for Claude Code's `SessionStart`,
`PreToolUse` and `Stop` events. The script reads the event from its input and:

- at session start, registers the session and prints a short status block;
- before a tool runs, blocks hand edits of compiled files and managed blocks, writes of a blueprint or a
  DONE review below the guaranteed effort, a `require-effort` command below it, and a compile that would
  switch the active feature under another live session in the same worktree;
- when a turn stops, summarises the session's transcript into the local metrics.

If you were invoked directly: explain this and stop. The guard can be switched off with `guard: false`
and metrics with `metrics: off` in `.specify/extensions/blueprint/blueprint-config.yml`.
