# Changelog

## 0.5.6 — 2026-11-17

The dashboard shows the delivery plan: sprints, epic sizes and the critical path, with a forecast per
sprint. What needs you comes first.

- The blueprint's delivery plan (§16) is read for the dashboard: the epic order table's goal, size and
  reason, the sprints table (sprint, epics, milestone, exit criteria) and the critical path. An epic two
  sprints list belongs to the last one, where it is finished. None of it changes what an epic is built
  from or what `next` picks.
- `estimate` gives each sprint a P50–P90 of the agent time and cost its open epics still need and of the
  date its last open epic is done, from the same runs as the project's (`sprints`).
- New sections. "Needs you": sessions waiting for your answer, each blueprint's next command, reviews and
  recompiles, the epics nothing blocks that could be built in a second worktree, and open epics no sprint
  lists. "Sprints": one bar of epics per sprint, each block as wide as its acceptance criteria and colored
  by state, with the exit criteria and a P50–P90 finish. "What an acceptance criterion costs": the per-AC
  cost of every finished epic, the rates each estimate multiplies by, so the spread behind a cost range
  is visible.
- The epics are grouped by sprint in delivery order, finished sprints closed. A row shows the epic's size
  (S/M/L from the plan, and its acceptance criteria on one scale for every epic), whether it is on the
  critical path, what it waits for and how many epics wait for it; opened, it shows the plan's goal and
  reason. A blueprint without a sprints table keeps one list, now in delivery order.
- The timeline groups the epics by sprint, folds finished sprints into one row and marks where each sprint
  ends. With every blueprint shown, it has one row per sprint.
- The timeline scheduled its forecast in blueprint order, so an epic written before the one it depends on
  started at "now", beside it. It follows the delivery order now, as `estimate` does.
- With every blueprint shown, each section folds each blueprint into one row (its sprints as a ribbon, its
  progress and finish), and an epic being built no longer opens with all its tasks.
- Tabs replace the blueprint selector. The live indicator shows the time of the last update, one width at
  every tick, so the tabs beside it stay still.
- Smaller fixes: a token total over a billion reads `5.70B`, not `5681.9M`; estimate ranges in rows are
  rounded to hours and dollars, with the exact range in the tooltip; a done epic with unchecked tasks says
  so; repository paths start at `~`; Spent says "No recorded sessions yet" instead of "0s agent time"; a
  link to `#bp=<id>` switches the tab.

## 0.5.5 — 2026-11-16

Metrics: agent time is the agent's time in its turns. The time between turns, and the wait for an answer
to its questions, are no longer counted as work.

- Once a session's first message had been read, every stretch until the next entry in its transcript
  counted as the agent at work, unless the entry was a typed message. Claude Code writes plenty between
  turns: the stop hook's record, links, file snapshots, and in the desktop app a queue operation for each
  message before it is read. So the 16 h from a stop hook's record to the queued next message counted as
  16 h of agent time. One epic recorded a single 63 h stretch and a session with no epic 200 h; one
  project's 112 sessions added up to 883 h of agent time, which read again are 127 h. A turn now ends at
  the model's last answer (`end_turn`), the stop hook's record (`stop_hook_summary`) or an interruption.
  Nothing Claude Code writes after that counts. The time until the user's next message is waiting, up to
  4 h as before, and that message is a human gate.
- A question (`AskUserQuestion`, `ExitPlanMode`) waits for the user. The time to its answer counted as
  tool time: 8 h for a question answered the next morning. It is waiting now (up to 4 h), and a human gate.
- Inside a turn, a stretch of more than 30 minutes in which neither the session nor its subagents wrote
  anything is idle: requests that hung for 2 to 3 h counted as model time. A subagent that runs longer
  keeps counting while it writes. A command with nothing written for more than 30 minutes does not count.
  The longest real tool calls measured ran 12 min, so a limit of a few minutes would drop real work.
- A background task's report (`<task-notification>`) is not a human gate. The time from the end of the
  turn to the report is the agent's tool time (it waited for its own task), split the same way. A
  scheduled task is no one's message, and the time before it is idle. An interruption is not a prompt.
  Neither is a compaction's summary, which carries the time of the conversation it summarises and set the
  clock back hours.
- Tool time is the time a tool call runs, counted once for calls made together. It never includes the
  wait for an answer or an idle stretch, and neither does a repository's tool time.
- The dashboard showed a session as "Thinking" once anything followed the stop hook's record, and an open
  question as a running tool. Both now show "Waiting for you", since the end of the turn or the question.
- After updating, run `blueprint.py metrics --backfill` once: it reads every recorded session again with
  these rules. Cost does not change: it comes from tokens.
- The package's `__version__` said 0.5.3 in 0.5.4.

## 0.5.4 — 2026-11-16

Metrics: work counts for the active feature only, and a cost no session recorded is unknown, not $0.00.

- The stop hook gave a session's time and tokens to the last feature folder its prompts or tool calls
  named, so reading an epic's files made the session that epic's work, and `estimate` counted it as a
  finished epic's history: a migration session that read three DESK epics became the history of all
  three. Work now counts for the feature active in the checkout (`.specify/feature.json`) at the time, and
  for no epic when that feature was not compiled from a blueprint. The hook records the active feature at
  the end of every turn; a compile or activate that ran, or Spec Kit's own report of the active feature
  (`check-prerequisites`, `setup-plan`, `create-new-feature`), marks the moment it changed, and earlier
  turns keep their epic. Work before a session's first compile still goes to the epic it compiles.
- Discovery and amendment go to `<blueprint>:discover` / `<blueprint>:amend` while no epic is active; an
  amendment made while an epic is active counts for that epic, as before.
- Sessions the hook never saw (recorded before 0.5.4, or read by `metrics --backfill`) use the board's
  last record of the session's active feature, else compiles, activates, Spec Kit's reports and writes
  into a feature folder. Reading a folder or naming it never counts.
- A missing cost was summed as zero, so with no recorded cost `estimate` printed `$0.00` and the dashboard
  showed $0.00 spent. It is unknown now: `null` in JSON, "cost unknown" in briefs, "unknown" on the
  dashboard. Cost ranges come from the finished epics that have a cost, and a total with estimated or
  unknown parts is marked "+".
- Sessions get their cost once they end. Claude Code records a session's cost (`cost-state`) when the
  session ends, after its last stop event, so the hook never saw it. Each stop event now also reads again
  the sessions that ended since their last summary. In a session resumed after its cost was recorded, the
  record covers the work before it and the rest is priced at the learned rate ("+").
- After updating, run `blueprint.py metrics --backfill` once: it reads the recorded sessions again with
  these rules, and with their cost.

## 0.5.3 — 2026-11-16

- `next` follows the blueprint's epic order. It took the epics as they appear in the file and skipped the
  blocked ones, so an epic an amendment appended at the end waited behind every epic that happened to be
  free, even when the delivery plan put it first and earlier epics depend on it: a blueprint with E20 and
  E21 appended, ranked 10 and 11 in its epic order table and needed by E09, was offered E12, ranked 15.
  The order now comes from the epic order table (§16.1: the first table with an `Order` and an `Epic`
  column), by the Order number, with the epics it leaves out after it in blueprint order; without such a
  table it is blueprint order. Either way an epic's dependencies in the same blueprint that are not done
  come right before it. Status reports the order as `epic_order`; a `next` that compiles an epic names
  the epics waiting for it, and the brief's `Current:` line names the epic `next` does.
- `next` finishes an epic compiled in this checkout before it starts another. An epic in progress that
  came later in the order than a free epic was left half done while status offered to compile the free
  one.
- `**Depends on**` reads every epic a part names. Only the ID a comma-separated part starts with counted,
  so `E20 and E21` made an epic wait for E20 alone and `E02 to E07` for E02 alone. A range (`E00 to E17`,
  `E02–E07`, `E00-E02`, `E03 through E05`) now counts for each epic in it, and `E20 and E21` (or `&`, `/`,
  `+`) for both. The estimate's finish date schedules the remaining epics in the same delivery order.
- `migrate` adds `schema: 2` under the config's header comment with one blank line after it, not two.

## 0.5.2 — 2026-11-16

- Review evidence may name several ACs at once. Status counted an AC only when its story's evidence named
  it on its own (`AC-3`), so a finished review that wrote `AC-1 to AC-5 x_test.dart` left AC-2 to AC-4
  without evidence and the epic `implemented`, asking for the review again. A range in a story's evidence
  now counts for every AC in it (`AC-1 to AC-5`, `AC-1 to 5`, `AC-1 through AC-5`, `AC-1–AC-4`, `AC-1–4`,
  `AC-1…3`, `AC-1...3`) and a pair for each of its ACs (`AC-5/6`, `AC-1/2/3`). A number ends where its
  digits do: `AC-12` never counts for `AC-1`. The review template shows both forms.

## 0.5.1 — 2026-11-16

Two fixes to upgrading a project built with 0.4.

- `migrate` no longer leaves every compiled epic "changed since compile". Converting an epic's repository
  fields rewrites its text, not what it was built from: when the trace matched the epic's text before the
  conversion, its hash moves to the converted text, recorded in the trace's `accepted` list as
  `blueprint.py accept` would. An epic that had changed before the migration stays reported, with a
  warning.
- Reviews written before 0.5 keep counting. 0.4 counted any `Status: DONE`; 0.5 wants an evidence entry
  for every AC, so epics reviewed under 0.4 dropped back to `implemented` or `in-progress` and blocked the
  epics that depend on them. `migrate` now records the review of every folder compiled before 0.5
  (`review_before_0_5` in `.blueprint.json`). A review that said DONE keeps counting as done while
  review.md is unchanged (line endings and trailing spaces aside); status marks the epic
  `review_before_0_5`. Once the review is edited, and for any review finished after the migration, 0.5's
  rule applies.
- A project already migrated with 0.5.0: status reports the folders whose review is not recorded; run
  `blueprint.py migrate` again, which changes nothing else. An epic that 0.5.0 left "changed since
  compile" needs `blueprint.py accept`: the text before the conversion is gone.

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
