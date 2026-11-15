# Conventions every blueprint command follows

Commands link here instead of repeating it. `blueprint.py` below is
`python3 .specify/extensions/blueprint/scripts/blueprint.py`.

## 1. Facts come from the script

- `blueprint.py status --json` is the state: the current blueprint (`blueprint_id`, `prefix`,
  `blueprint`, `decisions`, `review_checklist`, `sources`), every epic (`state`, `depends`,
  `blocked_by`, `elsewhere`, `started`, `repositories`, `tasks_done`/`tasks_total`, `review_missing`),
  `next` (with `missing_repositories` and `setup` when the next epic needs repositories this machine
  lacks), the guaranteed `effort` per step and `effort_model`, and `blueprints` (every product in the
  project).
- Several blueprints: pass `--blueprint <id>` to every command, or qualify IDs (`desk:E03`). In files
  shared by blueprints (tasks, commits, the constitution) write IDs with the blueprint's prefix
  (`DESK:DR-03`).
- Never read a blueprint whole. Use `blueprint.py section <n>` and `blueprint.py find <ID>`.
- `check` is the project's gate (the blueprint's §13.4, repeated in each plan.md). With `repositories:` in
  the config, `blueprint.py gate <epic>` runs every repository's gate and records the result.

## 2. IDs written while building

| What | On an epic's branch | On the integration branch |
|---|---|---|
| A decision the spec does not cover | `DR-E03-1`, `DR-E03-2`, … (`blueprint.py context` gives the next) | `DR-nn` |
| An amendment | `A-E03-1`, … | `A-nn` |

Epic-scoped IDs never collide when epics are built in parallel. The decisions log and the Amendments log
are append-only tables; a merge conflict there is resolved by keeping both rows.

## 3. Steps that run at a guaranteed effort

`effort` in the status lists the steps that must run at a set reasoning level (by default discover,
amend and review at `max`). A listed step does its reasoning in a subagent started at that level, so it
does not depend on the session's level. A step not listed runs here, at the session's level.

1. Start a subagent with the Agent tool: `subagent_type: general-purpose`, `effort: <level>`, and
   `model: <effort_model>` unless that is `inherit`.
2. Its prompt starts with: "First run `python3 .specify/extensions/blueprint/scripts/blueprint.py
   require-effort <step>`; if it is refused, stop and return the refusal." A Claude Code hook checks the
   subagent's real level there. If the subagent returns a refusal, tell the user its reason (for example
   `CLAUDE_CODE_EFFORT_LEVEL` is set) and stop.
3. The subagent cannot see this conversation: give it everything it needs (the user's words verbatim,
   the blueprint id and paths, decisions so far, the checkpoint files to read) and say exactly where to
   stop and what to return.
4. Subagents cannot ask the user anything. At a checkpoint the subagent returns its questions or its
   proposal; you put them to the user (AskUserQuestion for closed questions) and send the answers back
   verbatim to the same subagent (SendMessage resumes it with its context and its effort). If it cannot be
   resumed (a new session), start a new one at the same level that reads the checkpoint files.
5. Relay faithfully: never shorten, reword or decide anything on the user's behalf in between.

The guard refuses to write a blueprint, its discovery checkpoints or `Status: DONE` in a review below the
configured level, so work done at a lower level cannot slip in.

## 4. Several repositories

- The config's `repositories:` lists them; each epic's `**Repositories**` table says which it changes, in
  which order (providers before consumers), on which base and branch.
- Every task starts with its repository in brackets (`[evida-desk]`) and gives paths relative to that
  repository's root. Work in the path `blueprint.py context` gives for it.
- `blueprint.py repos <epic>`: each repository's branch, uncommitted files, ahead/behind, merged state and
  last gate. Merge the repositories in the table's order and the hub's feature branch last: a DONE visible
  in the hub then means the code is merged everywhere.
- Features are built from the hub only. A session opened in a code repository whose `CLAUDE.md` points to
  a hub (`workspace link` writes that block) tells the user to open it in the hub instead.
- A repository missing on this machine: `blueprint.py workspace setup [<epic>]` clones it from its
  `remote` and fetches the others.

## 5. Parallel epics

- One epic per worktree. `blueprint.py workspace <epic> --create` makes the epic's worktrees (the hub
  and every repository it changes, all on its branch) and prints how to open a session there. A branch
  pushed from another machine is continued, not started again.
- `status` marks epics claimed by another worktree as `elsewhere`, epics in progress on another branch
  (here or on a remote) but not in this checkout as `started`, and epics whose dependencies are not done
  in this checkout as `blocked`; `next` skips all three. Never compile a `started` epic again: continue
  it on its branch.
- Compile and activate refuse to switch the active feature under another live session in the same
  worktree.

## 6. Lint findings

`blueprint.py lint` runs the structural linter, then checks that compile can resolve every ID each epic
uses and that every repository in the epics' tables is configured. Fix every error (missing ACs, an
`[UNKNOWN → OQ-nn]` with no OQ row, an undefined persona, a stack row without evidence) and every
unresolved ID (define it once in its home section, in a shape compile finds: `how_to_define` in the
output). Fix each warning or justify it in one line. Repeat until it exits 0.

## 7. Always

- The project's own rules come first: the constitution's principles outside the managed blocks,
  `CLAUDE.md` and what it imports, `AGENTS.md`. Where they are stricter, follow them.
- Never edit compiled files (`spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`,
  `contracts/`, the managed blocks); change the blueprint through `/speckit-blueprint-amend`.
- Never commit or push unless the user asks; follow their git rules and each repository's.
- Everything written into the repositories is in English; talk to the user in their language.
