# speckit-blueprint design (0.5)

This document records how the extension works and why. It is for maintainers; the README is for users.
It is not installed into projects (`.extensionignore`).

## Principles

1. **Deterministic first.** Anything a script can decide (state, next step, paths, branches, readiness,
   gates, metrics) is decided by `blueprint.py`, never by the model reading prose. Commands tell the model
   what to run and how to act on its JSON.
2. **One model for one to many repositories.** A *hub* holds the planning: blueprints, specs, the
   constitution and the repository list. Code repositories stay ordinary repositories; nothing of the
   extension is installed in them. A single-repository project is a hub whose only repository is itself.
   A code repository on its own is one clone away from its hub, never a place to build from.
3. **Work is planned in exactly one hub.** An epic belongs to one blueprint of one hub.
4. **Guaranteed reasoning where it pays, the user's level everywhere else.**
5. **Local, private by default.** Metrics and coordination state live in the hub's git directory and are
   never committed unless the project asks for per-epic summaries.
6. **English** in everything the extension writes (code, commands, generated files, dashboard).
7. **Standard.** Spec Kit conventions (feature folder = branch name, hooks, events), Claude Code
   conventions (skills, hooks, subagents), a versioned config schema and a `migrate` command.

## Concepts

| Term | Meaning |
|---|---|
| Hub | The repository where Spec Kit and this extension are installed. Owns blueprints and specs. |
| Repository | A code root listed under `repositories:`. Usually a sibling git repository; may be a folder of a monorepo or the hub itself (`path: .`). Tasks name it in brackets: `[evida-desk]`. |
| Blueprint | One product's blueprint (`blueprints:` for several). |
| Epic | `## Epic NN` of a blueprint, compiled into one feature folder in the hub. |
| Workspace of an epic | One git worktree per repository the epic touches (plus the hub), all on the epic's branch. |
| Board | `<git-common-dir>/speckit-blueprint/board.json`: which worktree builds which epic, live sessions, gate results. Shared by every worktree of the hub, never committed (outside git: `.specify/blueprint-state/`). |

## Config, schema 2

`.specify/extensions/blueprint/blueprint-config.yml` (template: `config-template.yml`). Every key is
optional. New keys in schema 2: `schema`, `repositories`, `branch_prefix`, `worktrees_dir`, `effort`
(new meaning), `effort_model`, `metrics`, per-blueprint `sources` as repository names. Removed:
`effort_check`.

```yaml
schema: 2
repositories:
  evida-wiki: {path: ., branch_prefix: docs/}
  evida-desk: {path: ../evida-desk, gate: make check, base: feat/desk-release-1, branch_prefix: feat/}
  evida-id:   {path: ../evida-id, gate: make lint test, remote: git@github.com:org/evida-id.git}
branch_prefix: ""          # default for repositories that set none (else Spec Kit's git extension's)
worktrees_dir: .worktrees  # epic worktrees go to <repository parent>/<worktrees_dir>/
effort: {discover: max, amend: max, review: max}
effort_model: inherit
metrics: local             # local | commit | off
```

A config without `schema` is read as schema 2 when it has nothing older; `blueprint.py migrate` upgrades
older projects (see Migration).

A repository's `remote` may be relative to the hub's own URL (`../evida-desk.git`), resolved the way git
resolves relative submodule URLs, so one shared config serves SSH and HTTPS clones. The `.local.yml` file
overrides `repositories:` setting by setting: `evida-id: {remote: …}` there keeps its path and gate.

## Blueprint conventions the scripts read

- `**Depends on** E00, E02` — epic IDs (`one:E18` for another blueprint's epic). Free text may follow;
  `—` means none. Readiness uses only the IDs: each comma- or semicolon-separated part that starts with
  one, and the epics joined to it, a range (`E00 to E17`, `E02–E07`) and `E20 and E21` (`&`, `/`, `+`).
- `**Repositories**` — a table, one row per repository the epic changes:

  ```
  | Repository | Order | Role | Base | Branch | Contracts |
  |---|---|---|---|---|---|
  | `evida-id` | 1 | Provides the session API | main | | provides API-21 |
  | `evida-desk` | 2 | Consumes it | feat/desk-release-1 | | consumes API-21 |
  ```

  `Branch` is optional: the default is the repository's `branch_prefix` plus the feature folder name.
  `Base` defaults to the repository's `base`. Compile writes spec.md "Target Repositories" (repository,
  role) and plan.md "Repositories" (order, base, branch, contracts) from it and records the parsed rows in
  `.blueprint.json`. In a hub with `repositories:`, every name must be listed there (compile and lint
  fail otherwise); an epic without the table uses the hub alone.
- Task lines keep their repository prefix (`- [ ] T004 [P] [US1] [evida-desk] DESK:E08-S01-T2 …`).
  `status` warns about prefixes that name no configured repository.

## Commands and hooks

Seven commands (skills), down from ten:

| Command | Role |
|---|---|
| `speckit-blueprint-flow` | The only natural-language entry: new idea, continue, where are we, change requests. Routes by `status --json`. |
| `speckit-blueprint-discover` | Idea → blueprint. Thinks in subagents, talks in the main session. |
| `speckit-blueprint-amend` | Deliberate blueprint change. Same split. |
| `speckit-blueprint-compile` | One epic → feature folder (thin wrapper over the script). |
| `speckit-blueprint-context` | `before_implement` hook: facts from `blueprint.py context`, then the spec. |
| `speckit-blueprint-review` | `after_implement` hook: facts from `blueprint.py review-facts`, the review itself in a subagent. |
| `speckit-blueprint-hook` | Internal carrier for Claude Code events; its script dispatches on `hook_event_name`. |

Removed as commands: `lint`, `bootstrap`, `status` (script subcommands, run by the commands above),
`guard` (merged into `hook`). The optional `before_specify` hook is gone: flow covers "build the next
epic", and the prompt it printed on every `/speckit-specify` was noise in projects with other features.

Events: `session_start`, `pre_tool_use` (matcher `Edit|Write|MultiEdit|NotebookEdit|Bash`) and `stop`
all point at `speckit.blueprint.hook`.

## Lifecycle and next step

Epic states: `not-compiled`, `compiled`, `stale`, `tasked`, `in-progress`, `implemented`, `done`, and three
overlays: `blocked` (a dependency is not done in this checkout), `elsewhere` (claimed on the board by
another worktree) and `started` (not compiled in this checkout, but its hub branch exists, locally other
than the branch this checkout is on, or on a remote: in progress on that branch). `next` is the first
epic in delivery order that is none of these and is compiled in this checkout, so an epic in progress is
finished before another starts; without one, the first in delivery order that is none of these. When
every remaining epic is blocked, elsewhere or started, `next.command` is `wait` with the reason. Script
steps appear as `next.command` `migrate` or `bootstrap` with the exact command in `next.run`; they run
before any epic. When the next epic changes repositories that are not on this machine, `next` lists them
(`missing_repositories`) with the command that clones them (`next.setup`).

Delivery order is the blueprint's epic order table (§16.1 of the template: the first table with an
`Order` and an `Epic` column), by the Order number; rows without a number go last and rows with the same
number keep their places. An Epic cell names the epic it starts with (`E20 Workspace branches`, `**E20**`,
`Epic 20 — …`) and any joined to it (`E05, E06 …`). The epics the table leaves out follow it, in blueprint
order. Without such a table, delivery order is blueprint order. Either way an epic's dependencies in the
same blueprint that are not done move right before it: an epic an amendment appended, which an earlier
epic now depends on, is built before that one rather than after every epic that happens to be free.
Status reports the order as `epic_order` (`source`: `table` or `blueprint`), and a `next` that compiles
an epic names the epics waiting for it. The table decides because it is where the blueprint states the
order and the reasons; the epics' places in the file only record when each was written.

A started epic is skipped, not continued, because status cannot tell a teammate's branch from your own
from another machine; the brief says how to continue it (on its branch, or `workspace <epic> --create`),
and the flow asks. Without this overlay a checkout on the base branch would offer to compile the epic
again, which matters most in the plainest setup: one repository, a branch per feature (Spec Kit's git
extension), and a review pending on the epic's branch while the next epic starts from main. When the base
branch, local or remote, already has the epic's folder (`started.merged_into`), the brief asks for a pull
or merge instead. `workspace setup` fetches with `--prune`, so branches deleted after their merge stop
showing.

A dependency is done when its feature folder in *this* checkout says `Status: DONE` (a valid review, see
below). Since a branch only builds on what is merged into it, this is the right test for one repository;
for several, the hub branch of an epic is merged last, after every repository branch, so a DONE visible
in the hub implies the code is merged everywhere.

The constitution has a sixth state, `outdated`: the content hash of the configured sections differs from
the one recorded in the managed block. `next` is then bootstrap, before anything else.

`Status: DONE` counts only when review.md has an `AC evidence` entry for every `AC-n` of every story of
the epic; otherwise status reports the epic `implemented` with the missing ACs. The one exception is a
review written before 0.5, when any DONE counted: `migrate` records it (`review_before_0_5` in the trace,
with review.md's hash), and it counts while review.md is unchanged. Status marks such an epic
`review_before_0_5`; an edited review, or one finished after the migration, needs the evidence.

One entry may name several ACs of its story: a range (`AC-1 to AC-5`, `AC-1 to 5`, `AC-1 through AC-5`,
`AC-1–AC-4`, `AC-1–4`, `AC-1…3`, `AC-1...3`) counts for every AC in it, a pair (`AC-5/6`) for both. A
number ends where its digits do: `AC-12` never counts for `AC-1`, nor `AC-1` for `AC-12`.

## Parallel epics

- `blueprint.py workspace <epic> [--create]` plans (and with `--create` makes) the epic's worktrees: the
  hub at `<hub parent>/<worktrees_dir>/<hub>-<id>-eNN`, each repository at
  `<repo parent>/<worktrees_dir>/<repo>-<id>-eNN`, every one on the epic branch. Each starts from the
  local branch if there is one, else from the same branch on a remote (tracked: work pushed from another
  machine, with the hub's ticked tasks, is continued), else as a new branch from its base. The feature
  folder's name comes from this checkout, else from the epic's hub branch (local or pushed), else from the numbering
  compile would use. It prints the command to open the session (`claude --add-dir …`).
- Compile claims the epic on the board for the current worktree; another worktree's compile refuses
  (`--take` moves the claim). Status refreshes the worktree's own entries; other worktrees show them as
  `elsewhere`.
- Two live sessions in one worktree on different epics: compile and activate refuse and point to
  `workspace`. Liveness = the session's transcript was written in the last 15 minutes.
- Decisions taken while building are epic-scoped: `DR-E08-1` (`DESK:DR-E08-1` in shared files), so
  branches never collide. Amendments made on an epic branch are `A-E08-1`; blueprint-wide `A-nn` and
  `DR-nn` are for the integration branch. Logs are append-only tables; a merge conflict there is resolved
  by keeping both rows.

  This relaxes the earlier proposal (blueprint edits only on the integration branch): in practice
  amendments are found mid-epic (evida desk: A-30 to A-42 during E06–E08), and scoped IDs remove the
  collision without stopping the epic.

## A machine, or one repository on its own

Someone with only a code repository (a new machine, a new teammate, a cloud session that clones one
repository) cannot build a feature from it: the spec, tasks, constitution, board and metrics are in the
hub. That is deliberate. The hub is the workspace's manifest repository, the pattern multi-repository
projects use (Android's `repo`, Zephyr's `west`): it lists every repository and rebuilds the workspace
with one command.

- **Getting there is one clone.** Clone the hub next to the repository; paths default to `../<name>`, so
  the hub finds it. `blueprint.py workspace setup [epic]` clones the missing repositories from their
  `remote` and fetches the others (`GIT_TERMINAL_PROMPT=0`: a missing credential fails instead of
  hanging). Then `workspace <epic> --create` continues the pushed branches.
- **Finding the hub.** `blueprint.py workspace link` writes a managed block
  (`<!-- speckit-blueprint:hub:start id=<hub> -->`) into each code repository's `CLAUDE.md`: which hub
  specifies its features, the clone command, and that features are built from there. Claude Code reads
  that file in every session, so a session opened in the repository by mistake points the user to the
  hub. The command never commits; the block is the only thing the extension writes into a code
  repository, and only when asked.
- **Not supported:** building without access to the hub (give read access: the hub holds documents; a
  repository whose people must not see the hub is not part of the workspace), and a repository leaving
  the workspace (it becomes a hub of its own; copy the spec folders it needs by hand).

Rejected: keeping the compiled specs in the code repositories (with the blueprints in the hub), a hybrid
where single-repository epics keep their specs in their repository, git submodules of the hub, and
read-only copies of the spec in each repository. On the first real project (51 epics), 32 change one
code repository, 15 change two or three and 4 only the hub. Specs in the repositories would serve the
first group and split or copy the second; every repository would need Spec Kit, the extension and a
constitution copy; status, the board, metrics and the decisions log would be scattered across
repositories; and the hub would still be needed to compile, amend or record a decision. All of that to
save one clone.

## Guaranteed effort

- `effort:` lists only the steps that must run at a set level (default discover, amend, review: max).
  Every other step runs at the session's level with no stop and no hint.
- Those steps run their reasoning in a subagent started with the Agent tool's `effort` (and `model`, from
  `effort_model`) parameters, which apply to the whole subagent run and survive a resume. Subagents cannot
  ask the user anything, so discover and amend split into rounds: the subagent works up to a checkpoint
  and returns; the main session asks; the answer goes back to the same subagent (resume) or, after a lost
  session, to a new one that reads the checkpoint files (`<blueprint folder>/discovery/`).
- Verification (deterministic): the subagent's first command is `blueprint.py require-effort <step>`. The
  PreToolUse hook reads the real level from its input (`effort.level`) and blocks the command when it is
  lower, naming the cause (`CLAUDE_CODE_EFFORT_LEVEL`, an organization cap, the model). The guard also
  refuses an Edit/Write of a blueprint file, or of `Status: DONE` into review.md, below the configured
  level. When the hook input carries no effort (older Claude Code), nothing is blocked.

## Metrics

Source: Claude Code transcripts (JSONL). Per assistant message they carry the timestamp, model, `effort`,
token usage; per tool call the input and, in the result entry, the end time; subagents have their own
transcripts under `<session>/subagents/` with a `.meta.json`; a `cost-state` entry, written when the
session ends, carries its cost so far. The format is not a public API: the parser is defensive, records
the Claude Code version, and is tested on fixtures.

- The `stop` event summarises the session's transcript (and its subagents) into
  `<git-common-dir>/speckit-blueprint/metrics/sessions/<session>.json`. Re-summarising the whole file is
  idempotent and takes well under a second for multi-megabyte transcripts.
- Attribution: work belongs to the feature active in the session's checkout (`.specify/feature.json`) at
  the time. A folder without `.blueprint.json`, or no active feature, is no epic: that work counts for no
  epic. Reading an epic's files or naming it moves nothing, since a session reads other epics for
  reference and a migration reads them all. The `stop` event records the active feature at the end of
  every turn (`active_seen` in the summary). Within a turn, a `blueprint.py compile` or `activate` that
  ran, or Spec Kit's own report of the active feature (`check-prerequisites` and `setup-plan` print
  `FEATURE_DIR`; `create-new-feature` makes its new feature active and prints its `SPEC_FILE`), marks the
  moment it changed. A change the transcript does not show moves the work from the start of the turn
  that ended with it, so earlier turns keep their epic. Work before a session's first compile belongs to
  the epic it compiles (it was getting it ready).
- Discovery and amendment: while no epic is active (or before that first compile), work in a discover or
  amend step that wrote a blueprint goes to `<blueprint>:discover` / `<blueprint>:amend`. An amendment made
  while an epic is active counts for that epic, which the active feature decides; the step a session is in
  is only known until the next skill it runs, so it cannot bound the amendment.
- Sessions the `stop` event never saw (recorded before 0.5.4, or transcripts `--backfill` reads): the
  board's last record of the session's active feature, when it has one; otherwise compiles, activates,
  Spec Kit's reports and writes into a feature folder (tasks.md ticks, review.md).
- Turns: a turn starts at a message (the user's, or the report of a background task the session started)
  and ends at the model's last answer (`end_turn`), the stop hook's record (`stop_hook_summary`) or an
  interruption. What Claude Code writes between turns (queue operations, links, file snapshots, a
  compaction's summary dated back to the conversation it summarises) is no work and moves nothing. A turn
  that goes on without a message (a stop hook sent the agent on) starts at its next answer.
- Time split: tool time (while a tool call runs, once for calls made together), model time (the rest of
  the turn), waiting for the user (end of turn to the user's next message, and a question,
  `AskUserQuestion` or `ExitPlanMode`, to its answer: up to 4h each, each a human gate), idle (anything
  longer, gaps between sessions, and inside a turn a stretch of more than 30 min in which neither the
  session nor its subagents wrote anything). After a turn, the wait for a background task up to its report
  is tool time, split the same way; the time before a scheduled task's wake-up is idle. Measured on 112
  sessions: tool calls ran up to 12 min and answers took up to 8 min with nothing written; the longer
  stretches were requests that hung for hours.
- Stories: task ticks arrive in batches (measured: up to 19 tasks in 30 s), so durations are measured per
  tick batch and reported per story, never per task. Ticks are listed with their time.
- Repositories: tool calls are attributed by path (repository and worktree paths) and by `cd`/`-C` in
  Bash commands.
- Cost: the session's last `cost-state` total, shared out by weighted tokens (input 1, cache write 1.25,
  cache read 0.1, output 5) over the work before it. Claude Code writes it when the session ends, after
  the last `stop` event, so each `stop` event also summarises again the sessions of the last seven days
  that ended since their summary (up to five). Work no record covers (a session still running, or resumed
  after its record) is priced at the rate learned from the priced work and marked "+". With no priced
  work the cost is unknown: `null` in JSON, "cost unknown" in briefs and the dashboard, never $0.00.
- `metrics: commit` also writes `<feature>/metrics.json` when the review is DONE.
- `blueprint.py metrics --backfill` summarises the transcripts Claude Code still keeps for the hub and its
  worktrees.

Units are formatted in one place: `2h 5m`, `45m`, `38s`, `1d 4h`, `$3.10`, `2.4M`, `310k`, ranges
`45m – 1h 20m`.

## Estimates

- Size, known before building: ACs, stories, edge cases, task seeds, repositories, contracts, IDs by type.
- Rates from finished epics, of every blueprint in the project (so a blueprint none of whose epics is
  finished has estimates from the others): agent time, cost, tokens and calendar time per AC; human gates
  per story. A cost comes only from the finished epics that have one; when none has, cost ranges are
  `null` ("cost unknown"), not zero.
- An epic's estimate is a P50–P90 range of rate × ACs. The remaining project is a seeded Monte Carlo over
  the remaining epics; the finish date schedules them, in status's delivery order, on the `Depends on`
  graph with the parallelism observed so far. Fewer than three finished epics: the range is marked low
  confidence and P90 is widened by half, since one or two epics say nothing about the spread.

## Dashboard

`blueprint.py dashboard` serves a single static page and `/api/state` on `127.0.0.1` (stdlib
`http.server`), recomputing the state on request with a short cache. `--export FILE` writes the page with
the state embedded (for managers). `blueprint.py statusline` prints one line for Claude Code's status
line. The page is English, uses units everywhere, has no external dependencies, and shows: totals,
remaining ranges and the finish date; one row per live session (worktree, epic, story, current activity,
waiting for you); epics expandable to stories and tasks with their repositories; a timeline with forecast
ranges; where each epic's time went; a per-repository view. A blueprint selector covers several products.

## Migration and versioning

- Semver; this release is 0.5.5 (0.5.0 was breaking). 1.0.0 once the model has run on a real project.
- `blueprint.py migrate [--apply]` (dry run by default) upgrades a project to schema 2: adds `schema: 2`;
  turns `sources` paths into `repositories:` entries; converts `**Target repositories**` +
  prose `**Repositories**` into the table (keeping explicit branch names in the Branch column); adds
  `blueprint_id` to traces that lack it; adds ids to unmarked managed blocks. It prints what it could not
  convert. The runtime no longer guesses for legacy layouts; status reports `migration_needed`.
- The conversion is not a change to what a compiled epic was built from. When a trace's `epic_sha`
  matches the epic's text before the conversion, migrate moves it to the converted text and adds an
  `accepted` entry, as `blueprint.py accept` would; a trace that already differed keeps its hash, so the
  earlier change stays reported.
- A folder compiled before 0.5 is one whose trace has no `acs` (compile writes it since 0.5). Migrate
  records its review then (`review_before_0_5`: the status, the hash of a DONE review, the date), for
  every such folder, so a review finished after the migration is told apart and held to the evidence
  rule. Status reports folders without the record as `migration_needed`, which brings a project
  migrated with 0.5.0 back to migrate.

## Later

- **Spec Kit passes skill frontmatter through.** Spec Kit rebuilds an extension command's SKILL.md
  frontmatter (name, description, compatibility, metadata) and sets `user-invocable: true` and
  `disable-model-invocation: false` itself, so an extension cannot set `effort`, `model` or hide its
  internal `hook` command. A small upstream change would let `review` carry `effort: max` directly and
  keep `hook` out of the `/` menu and the model's skill list.
- **Headless runs.** Spec Kit 1.3.3 workflows take a per-step `reasoning_effort` (Claude: `--effort`). A
  workflow per epic (compile as a shell step, then tasks, analyze, implement, review, a human gate) would
  give unattended runs the same guarantees without subagents.
- **Team-wide metrics.** Claude Code can export OpenTelemetry; a collector could carry every developer's
  sessions to one dashboard. Today the live view covers one machine and others arrive through
  `metrics: commit` summaries.
