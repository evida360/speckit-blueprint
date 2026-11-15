# Changelog

## 0.5.0 — 2026-11-16

One model for one to many repositories, epics built in parallel, guaranteed reasoning effort where it
pays, metrics and estimates from Claude Code's own transcripts, and a live dashboard. Breaking: run
`blueprint.py migrate` once in a project made with 0.4 (see "Upgrading from 0.4" in the README).

**Commands: ten → seven.** `flow`, `discover`, `amend`, `compile`, `context` and `review`, plus the
internal `hook`. `lint`, `bootstrap` and `status` are script subcommands that the commands run;
`guard` and the session-start status merged into `hook`, which dispatches on the event. The optional
`before_specify` hook is gone. What the commands repeated (paths, several blueprints, `check`, IDs) lives
once in `references/conventions.md`; only `flow` carries natural-language triggers.

**Repositories.** `repositories:` in the config (name → path, gate, base, branch_prefix, remote); each
epic's `**Repositories**` table (Repository, Order, Role, Base, Branch, Contracts) replaces the prose
`**Target repositories**` / `**Repositories**` fields. Compile validates the names, computes each
repository's base and branch (`<branch_prefix><feature folder>`, Spec Kit's convention; a Branch cell
wins) and writes spec.md "Target Repositories" and plan.md "Repositories". New: `blueprint.py repos`
(each repository's branch, uncommitted files, ahead/behind, merged), `blueprint.py gate` (runs and records
every repository's gate), lint reports unknown repositories.

**Parallel epics.** `**Depends on**` is read: an epic is `blocked` until its dependencies are DONE in the
checkout. A board in the hub's git directory records which worktree builds which epic: status marks
those `elsewhere` and `next` skips them; `compile` claims an epic (`--take` moves a claim);
`blueprint.py workspace <epic> --create` makes the epic's worktrees in the hub and every repository it
changes. The hook refuses a compile that would switch the active feature under another live session in
the same worktree. Decisions and amendments made on an epic's branch are numbered within the epic
(`DR-E03-1`, `A-E03-1`), so parallel branches never collide. Feature numbers skip the folders other
worktrees compiled.

**A new machine, or one repository on its own.** Features are built from the hub only; getting it is one
clone. `blueprint.py workspace setup [epic]` clones the repositories missing on this machine from their
`remote` and fetches the others; a `remote` may be relative to the hub's own URL (`../desk.git`, resolved
like git's relative submodule URLs). `workspace <epic> --create` continues a branch a remote has (the
hub's, with its ticked tasks, and each repository's) instead of starting it again from the base, and
takes the feature folder's name from the epic's branch. Status marks an epic whose branch exists, here or
on a remote, but whose folder is not in this checkout as `started` (in progress on that branch, or merged
into the base and only a pull or merge away) and moves on instead of offering to compile it again, also
in a single repository with a branch per feature; it names the repositories the next epic needs that are not on this machine (`next.missing_repositories`,
`next.setup`), and the dashboard shows both. `blueprint.py workspace link` writes a block into each code
repository's `CLAUDE.md` that names its hub and how to clone it, so a session opened there points the
user to the hub. The `.local.yml` file now overrides a repository's settings one by one instead of
replacing its entry.

**Guaranteed effort.** `effort:` now lists only the steps that must run at a set level (default
discover, amend and review at `max`; `session` removes one); every other step runs at the session's level
with no stop and no question. Those steps run their reasoning in a subagent started at that level
(discover and amend in rounds: the subagent stops at a checkpoint, the main session asks the user, the
answer goes back to it); the review runs in a fresh subagent that did not write the code. The PreToolUse
hook reads the agent's real level: `blueprint.py require-effort <step>` and writes of a blueprint, its
discovery checkpoints or a DONE review are refused below the level, naming the cause
(`CLAUDE_CODE_EFFORT_LEVEL`, an organization cap, the model). `effort_model` picks the subagents' model.
`effort_check` is gone.

**Deterministic checks.** Bootstrap refuses a blueprint with lint errors or unresolved IDs
(`--skip-lint` to override). Status reports the constitution `outdated` when the sections it is built
from changed, and a review DONE only when review.md has evidence for every AC of every story. New
`blueprint.py activate`, `context` and `review-facts` give the hooks their facts (repositories and paths,
preconditions, VERIFY-AT-BUILD lines, the next decision ID, the ACs needing evidence, diffs).

**Metrics and estimates.** The `stop` event summarises the session's transcript into the hub's git
directory: per epic (and per blueprint for discovery and amendments) agent time split into model and
tool time, time waiting for the user, human gates, tokens, cost, models, effort levels, phases,
repositories, subagents, task ticks and story times (stories, not tasks: ticks come in batches).
`blueprint.py metrics [<epic>] [--brief] [--write] [--backfill]`, `blueprint.py estimate [<epic>]`
(P50–P90 from finished epics; what remains by seeded Monte Carlo over the `Depends on` graph with the
parallelism seen so far). Compile records the estimate made before building; the review adds
"Estimate vs actual". `metrics: local | commit | off`.

**Dashboard.** `blueprint.py dashboard` serves a live page on 127.0.0.1: totals, remaining ranges and the
finish date, live sessions (what each is doing, or how long it has waited for you), epics expandable to
stories and tasks with their repositories and gates, a timeline with forecast ranges, where each epic's
time went, and a per-repository view. `--export FILE` writes a single-file snapshot.
`blueprint.py statusline` prints one line for Claude Code's status line. Everything in English with units.

**Standard and public.** Config `schema: 2`; `blueprint.py migrate` (dry run, `--apply`) converts a 0.4
project: config, repository fields (keeping the prose as notes under the table), traces and managed
blocks without ids. The runtime no longer guesses for those layouts; status reports `migration_needed`.
Managed blocks always carry their blueprint id (`id=default` with one blueprint). The script is a package
(`scripts/speckit_blueprint/`) behind `scripts/blueprint.py`, and writes no `__pycache__` into projects.
The YAML subset reads inline maps. `docs/DESIGN.md` records the design. Up to date with idea-to-blueprint
v1.1.1 (38cbec9), unchanged.

## 0.4.0 — 2026-11-04

Several blueprints in one project (one per product), without one touching another's files.

- `blueprints:` in the config maps each id to its `path`, `decisions`, `checklist`, `prefix`, `sources`,
  `number_base` and, optionally, its own `constitution_sections` / `plan_sections`; `default_blueprint`
  picks the default. A config without it has one implicit blueprint and works as before. Two blueprints
  sharing a file, or path keys at the top level next to `blueprints:`, are config errors.
- `.blueprint.json` records `blueprint_id`. Compile, status, stale, accept and the session-start hook find
  an epic's folder by blueprint and epic together. Before, `compile E01` for a second blueprint reached the
  first one's `E01` folder and could overwrite its compiled files. Folders compiled before ids belong to
  the default blueprint.
- New folders are `NNN-<id>-eNN-<slug>` when the config uses ids; `number_base` gives each blueprint its own
  range of feature numbers.
- Commands take `--blueprint <id>` or qualified arguments (`desk:E01`, `DESK:DR-03`, `one:13.4`) and default
  to the active feature's blueprint (`SPECKIT_BLUEPRINT` also works). `status` lists every blueprint under
  `blueprints`; the brief names them all.
- Bootstrap keeps one constitution block and one CLAUDE.md block per blueprint, with the id in the markers;
  an unmarked block is the default blueprint's and gets its id on its next bootstrap. A constitution
  compiled whole from one blueprint keeps the other blueprints' blocks, and `constitution_mode: own` refuses
  to replace another blueprint's constitution. Scope sentences name the blueprint id, not just the
  presence of `.blueprint.json`.
- Each blueprint has its own decisions log and checklist (default: next to its file); with a prefix, DR
  index rows and task seeds carry it (`DESK:DR-01`, `DESK:E01-S02-T3`).
- `blueprint.py rules` prints the rules for the active feature's blueprint; the context hook uses it. The
  guard protects every block and names the right blueprint.
- No command, prompt or guard message names `docs/BLUEPRINT.md` any more; it is only the default path.
- Discover settles the target blueprint first (adding a config entry for a new product), decides its mode
  for that blueprint alone, and in brownfield mode reads the blueprint's `sources` outside the repository.
- The config parser reads nested maps.

## 0.3.2 — 2026-10-31

- Bootstrap no longer creates a Sync Impact Report comment in a project-owned constitution. Spec Kit
  treats that comment as scratch for reviewing one amendment and removes it before the commit, so a new
  one after every bootstrap was clutter to clean up. The version bump and Last Amended stay; the
  amendment is reported in bootstrap's output (`constitution.amendment`), and a line is added only to a
  report that is already open.

## 0.3.1 — 2026-10-31

- The CLAUDE.md block no longer names `AGENTS.md`; it defers to the rest of CLAUDE.md and what it imports.
- The flow and context commands name the project's agent instructions generically (`CLAUDE.md` and what it
  imports, `AGENTS.md` where present).

## 0.3.0 — 2026-10-31

Safe to add to an existing Spec Kit project, and easy to keep in step with idea-to-blueprint.

- Bootstrap keeps a constitution the project wrote and maintains one managed `speckit-blueprint:rules`
  block in it, scoped to blueprint features and yielding to the project's principles; the project's
  `constitution_mode: auto|own|append` in the config. A constitution that is
  missing or still the `specify init` template is compiled whole, as before.
- The guard protects only that block in a project-owned constitution; the rest stays editable.
- An existing `docs/REVIEW-CHECKLIST.md` is no longer overwritten.
- The `context` and `review` hooks do nothing for features without `.blueprint.json`.
- The flow skill follows the project's constitution, `AGENTS.md` and `CLAUDE.md` first; small fixes are
  recorded the way the project requires instead of skipped.
- Discover has a brownfield mode for an existing product: repository first, nothing existing re-decided
  or invented, Epic 00 as a short baseline.
- The session-start hook stays silent in an existing project without a blueprint.
- idea-to-blueprint is vendored unmodified under `upstream/idea-to-blueprint/` (pinned in
  `upstream/UPSTREAM.json`, still commit `38cbec9`). `discover.md` runs its `SKILL.md` with listed
  overrides, and the linter change became a wrapper. `tools/sync_upstream.py` updates the copy and
  reports the anchors the extension relies on; tests and a weekly workflow catch drift.
- `blueprint.py accept Enn --note "…"` records that a built epic stays as built after a blueprint change
  (for example, covered by a later epic), so status stops reporting it; `amend` uses it after the user
  decides.
- The commands say that `check` is the project's gate command from plan.md, not a `blueprint.py`
  subcommand.
- The lint wrapper no longer writes `__pycache__` into `upstream/`.
- In append mode, adding or changing the managed block bumps the project's constitution version
  (`constitution_version_bump: minor|patch|none`), sets Last Amended and adds a Sync Impact Report line;
  the user no longer has to do it by hand.
- Multi-repository projects: optional epic header fields `**Target repositories**` and `**Repositories**`
  compile into spec.md's "Target Repositories" and plan.md's "Repositories" sections (order, branches,
  contracts per repository); brownfield discover writes them when the constitution asks for them.
- `decisions_path`, `review_checklist_path` and `claude_md: false` in the config, for projects that keep
  `docs/` for other material or whose CLAUDE.md only imports other files; `blueprint_path` can already
  point under `specs/`.
- The discover and flow skills say to use them instead of the idea-to-blueprint skill inside a Spec Kit
  project with this extension; the two can stay installed side by side.
- Brownfield discover checks each decision and AC against the project's principles, so an AC that keeps
  existing tests unchanged cannot sit next to a decision that changes what they observe.

## 0.2.0 — 2026-10-28

First release validated end to end with Claude Code: a Persian one-paragraph idea taken through
discover (intake, research, decision brief, a 133 KB blueprint, lint clean), bootstrap, and four epics of
compile → tasks → analyze → implement → review, every session started with "continue" only. Result:
130/130 tasks ticked, 4 reviews DONE, 217 tests green at 97.8% coverage, no compiled file edited by hand.

- The context hook requires ticking each task in tasks.md as soon as it is done (resume point and
  progress source), never in bulk at the end.

## 0.1.1 — 2026-10-27

From an end-to-end run with Claude Code (bootstrap → compile → tasks → analyze → implement → review):

- `blueprint.py lint` runs the linter and the reference check in one command; commands use it.
- `refs`/`lint` output explains the definition shapes compile recognises when an ID is unresolved.
- The flow skill keeps going through steps on "continue" and stops only at the plan/build session
  boundary, a user gate or an unfixable problem; compiling no longer asks for a fresh session.

## 0.1.0 — 2026-10-27

First release, Claude Code only, tested with Spec Kit 1.2.4.

- `discover`, `lint`: the idea-to-blueprint method (intake, research, decision brief, blueprint, linter),
  adapted to the spec-kit build flow (`references/speckit-flow.md`).
- `compile`: one epic → spec-kit feature folder, with every referenced ID resolved; trace for staleness.
- `bootstrap`: constitution, review checklist, decisions log and a managed CLAUDE.md block.
- `context` (before_implement) and `review` (after_implement) hooks.
- `amend`: in-place blueprint amendments with recompilation of affected epics.
- `flow` skill and `status`; Claude Code SessionStart status and a PreToolUse guard for compiled files.
