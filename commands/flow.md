---
description: "Use for anything in a blueprint-driven Spec Kit project: a new product or feature idea, 'continue' / 'next' / 'build the next epic', 'where are we', a change to an epic's scope, acceptance criteria or copy, building several epics in parallel, or migrating the extension. Reads the project state and runs the right step. In a project with this extension, prefer it over the idea-to-blueprint skill. Not for features that were not compiled from a blueprint: those follow the project's own workflow."
---

# Blueprint flow

You conduct the blueprint-driven part of the project. The user talks in plain language; the script
decides the next step and you run it. Follow `.specify/extensions/blueprint/references/conventions.md`
(read it once per session). Features whose folder has no `.blueprint.json` are not this skill's business.

## User Input

```text
$ARGUMENTS
```

## 1. Read the state

```bash
python3 .specify/extensions/blueprint/scripts/blueprint.py status --json
```

Do not read the blueprint to work out the state. When the user names another product, or the request
belongs to another blueprint's epics, use that blueprint (`--blueprint <id>`).

## 2. Classify the request

| The user… | Do |
|---|---|
| shares a new idea and there is no blueprint (`state: no-blueprint`) | `/speckit-blueprint-discover` with the idea (it detects an existing product by itself) |
| shares big new scope for a product that has a blueprint | `/speckit-blueprint-discover --blueprint <id>` (incremental) |
| shares an idea for a product no blueprint covers | ask whether it is a new blueprint; if so, `/speckit-blueprint-discover` for a new id |
| wants to change behaviour, an AC, copy, data or scope of an epic, or reports that the spec is wrong | `/speckit-blueprint-amend` with their words |
| says continue / next / go / build, or asks what is next | follow `next` (section 3) |
| asks where things stand | a short table per blueprint: epic, state, tasks, repositories; mark `elsewhere` (with its worktree), `started` (with its branch) and `blocked_by`; then `next`. Stop. |
| wants to build another epic at the same time | `blueprint.py workspace <epic>`; show the plan; on their yes, `--create`, then tell them to open a session there with the printed command |
| is on a new machine, or wants to continue a `started` epic | on its local branch (`started.local`) in a single repository: switch to that branch. Otherwise `blueprint.py workspace setup` (clones and fetches; see section 3), then `blueprint.py workspace <epic>`; show the plan (each worktree's start: `remote` continues the pushed branch); on their yes, `--create` and the printed command |
| asks for a small fix to built code that changes no AC | record nothing in the blueprint; do it the way the project's rules say |
| runs `/speckit-clarify` or `/speckit-checklist` on a compiled epic | let it run; its answers take their defaults from the blueprint (conventions §8) and become amendments or decision records, never edits to spec.md |
| works on a feature without `.blueprint.json` | leave it to the project's own workflow |

When unsure whether a request changes the product, treat it as an amendment and ask, with the recommended
answer tagged (conventions §8).

## 3. Run the next step

| `next.command` | Do |
|---|---|
| `migrate` | Run `blueprint.py migrate` (a dry run), show the user the changes and warnings, and on their yes run it with `--apply`. Then status again. |
| `bootstrap` | Run `next.run`. Tell the user the constitution amendment it reports (`constitution.amendment`) and anything their governance asks beyond a version bump. In `own` mode never run `/speckit-constitution` afterwards. |
| `speckit-blueprint-compile` | `/speckit-blueprint-compile <epic>` (`<id>:<epic>` with several blueprints), then `/speckit-tasks` and `/speckit-analyze` in this session. The answers hook runs before each. A defect analyze finds in spec.md, plan.md or the data model is an amendment naming the blueprint text it changes; one only in tasks.md is fixed in tasks.md. Never `/speckit-specify` or `/speckit-plan` for a compiled epic. |
| `speckit-tasks` | `/speckit-tasks`, then `/speckit-analyze`. |
| `speckit-implement` | If `next.activate` is set, first `blueprint.py activate <next.activate>`. Then `/speckit-implement`. |
| `speckit-blueprint-review` | `/speckit-blueprint-review`: the review loop. When it stops with open findings, that is a gate (section 4). |
| `wait` | Every remaining epic is blocked, built elsewhere or started on another branch: say which and why; offer to continue a started epic, to build a ready epic of another blueprint, or to finish the dependency first. |
| `speckit-blueprint-discover` with `state: all-done` | Every epic of this blueprint is done: offer incremental discovery; name any other blueprint with work left. |

The script picks the epic: one compiled in this checkout first, then `epic_order` (the blueprint's epic
order table, with each epic's open dependencies before it). Do not reorder it from the blueprint; when
the user wants another order, that is an amendment to the table.

Before an epic step, two checks:

- `next.missing_repositories`: the epic changes repositories that are not on this machine. Name them and
  run `next.setup` (it clones those with a `remote` and fetches the rest). For a repository without a
  `remote`, ask the user for its URL or to clone it to the printed path.
- An epic marked `started` comes before `next` in `epic_order`: it is in progress on another branch
  (here, on another machine or by someone else). Never compile it again. Ask whether to continue it (on
  its branch, or `workspace <epic> --create`) or to go on with `next`; with `started.merged_into`, ask to
  pull or merge that branch first.

**Keep going.** After a step, read the status again and run the next step yourself, until:

- a gate from section 4;
- `next.new_session` is true and this session already did work (bootstrap, a compile+tasks pass, an
  implementation, a review): that is the boundary between planning an epic and building it, and between
  epics. Tell the user, in their language: "Open a fresh session in this project and say: continue.";
- a step reported a problem it could not fix (a precondition, an unresolved ID, a red gate).

Steps listed under `effort` (discover, amend, review by default) start their own subagent at that level
(conventions §3); the session's own level does not matter and there is nothing to ask the user about it.
A review starts a new subagent for every pass and makes its fixes in this session, at its own level.

## 4. Gates you never pass for the user

- Approval of the decision brief in discover.
- Amendments that change user-visible behaviour, money, data retention or security.
- `migrate --apply`, and creating worktrees with `workspace --create` unless they asked for parallel work.
- A review that stopped with open findings: the user chooses among the listed options.
- `git commit` and `git push`.

## 5. When you stop

Run `blueprint.py status --brief` and tell the user in a few lines what was done, what they must check or
decide (each choice with its recommended answer, conventions §8), and what comes next.
