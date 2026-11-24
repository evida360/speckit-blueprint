---
description: "Change a blueprint deliberately (an amendment) and recompile the epics it affects: new or changed behaviour, an AC, copy, data or a decision. Usually started by speckit-blueprint-flow."
---

# Amend the blueprint

The blueprint is the source of truth. When reality or the user disagrees with it, the blueprint changes
first, explicitly, and the compiled specs follow. This is the only way spec files change. Follow
`.specify/extensions/blueprint/references/conventions.md`.

## User Input

```text
$ARGUMENTS
```

Scope additions (a new capability, more than a story or two) are not amendments: use
`/speckit-blueprint-discover` in incremental mode. A change that spans two products is two amendments,
one per blueprint.

## Rounds

An amendment re-decides part of the product, so its thinking runs at `effort.amend` (default `max`) in a
subagent (conventions §3); if `amend` is not listed under `effort`, do the same here. Give the subagent the
user's words verbatim, the blueprint id and path, the active feature and its branch (from
`status --json`) and this file's path.

1. **Analyse and draft (subagent).** It restates the change in one or two lines; finds what it touches
   with `blueprint.py find <ID>` / `section <n>` (never the whole file); decides whether it changes
   user-visible behaviour, money, data retention or security; and drafts every edit exactly (location, old
   text, new text), the amendment ID (`A-E03-1` when the change is made on an epic's branch, `A-nn` on the
   integration branch; conventions §2) and the decision records it supersedes or adds. The draft quotes
   the blueprint text it changes with its section or ID. It returns the draft without editing anything.
   **You:** if the change alters behaviour, money, data retention or security and the user's message did
   not already decide it, show the draft and ask, with the recommended answer tagged (conventions §8);
   send the reply verbatim. Technical amendments go ahead.
2. **Apply and lint (same subagent).** It edits the blueprint in place, marks each change
   `(amended A-…)` (never a "version 2" section), adds the Amendments log row
   (`| A-… | date | epic/story | what changed | why | approved by |`), marks a superseded DR and adds the
   new one, keeps the evidence tags (`[VERIFIED — S-nn, date]` / `[ASSUMED — why]`), runs
   `blueprint.py lint --blueprint <id>` until it exits 0, then `blueprint.py stale --blueprint <id>`, and
   returns that list. An epic the change removes is retired, never deleted or renumbered: its heading
   becomes `## Epic 08 — Retired (amended A-…)`, its Goal says why, one marker story keeps the linter
   satisfied, and the delivery plan's tables and every other epic's **Depends on** stop naming it.
3. **Propagate (you).** For each epic `stale` lists:
   - no tasks done: `/speckit-blueprint-compile <epic>`, and `/speckit-tasks` again if it had a tasks.md;
   - partly or fully built: never recompile over finished work silently. Tell the user what changed under
     built code and offer: recompile with `--force` and regenerate tasks (implement then reworks only what
     changed), a follow-up story in a later epic, or keeping the epic as built
     (`blueprint.py accept <epic> --note "<why>"`). The user decides; say what the blueprint says of each
     option (its §16 next epics, the decision's reversibility) and tag the recommended one.
   - A retired epic is never recompiled (compile refuses it, `stale` leaves it out). When status shows a
     `feature_directory` for it, it was compiled before: tell the user what was built for it
     (`tasks_done`) and let them decide what happens to that work.
   - If status now reports the constitution `outdated`, run `blueprint.py bootstrap --blueprint <id>`.
4. Report the amendment ID, what changed, which epics were recompiled, and what waits on the user.
