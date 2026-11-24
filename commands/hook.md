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
- before a tool runs in a review pass's subagent, refuses any write but the feature's review.md, commands
  that change the work, and a second pass in the same subagent;
- when a turn stops, summarises the session's transcript into the local metrics, its work counting for
  the feature active in the checkout, and reads again the sessions that ended since, for their cost; in a
  blueprint feature it sends back once a Spec Kit recommendation that names no source.

If you were invoked directly: explain this and stop. The guard can be switched off with `guard: false`
and metrics with `metrics: off` in `.specify/extensions/blueprint/blueprint-config.yml`.
