# speckit-blueprint

A [Spec Kit](https://github.com/github/spec-kit) extension that combines two things that are usually
traded off against each other:

- **Deep product discovery** from [idea-to-blueprint](https://github.com/iniesohidham/idea-to-blueprint):
  intake, web research, competitor benchmarking, ranked architecture drivers, a verified and pinned stack,
  the quality gate, personas, flows, screens, UX copy, edge cases, and epics → stories → Given/When/Then
  acceptance criteria → tests. The result is one evidence-backed blueprint per product.
- **Token-efficient, drift-free implementation** from Spec Kit: each epic is built from a small feature
  folder (`spec.md`, `plan.md`, `tasks.md`, …) instead of a 100-page document.

The bridge is a deterministic compiler. `/speckit-blueprint-compile E03` resolves every ID the epic uses
(personas, flows, screens, copy lines, entities, endpoints, integrations, edge cases, decisions, risks,
open questions, sources) to its definition and writes a Spec Kit feature folder with exactly that. Around
it: one model for one to many repositories, epics built in parallel, reasoning effort guaranteed where it
pays, and time, token and cost metrics with estimates and a live dashboard.

Status: **0.5.2, Claude Code only.** Tested against Spec Kit 1.2.4 and idea-to-blueprint v1.1.1.

## Tested end to end

With 0.2, a one-paragraph idea in Persian (a personal expense tracker in tomans with Jalali-month
reports) was taken from nothing to a finished v1 with Claude Code (Sonnet), every session started with
"continue" in a fresh session:

| Stage | Outcome |
|---|---|
| Discover | 7 intake questions with defaults, web research with pinned versions, decision brief, a 133 KB blueprint (4 epics, 12 stories) that lints clean with every ID resolvable |
| Bootstrap | Constitution, review checklist, decisions log, CLAUDE.md block |
| 4 epics × (plan session + build session) | 130/130 tasks ticked, every epic reviewed `Status: DONE`, 217 tests green at 97.8% coverage |
| Human gates | One manual terminal check stopped the flow until the user confirmed; no commit or push without approval |

The whole run used about $13 of Sonnet usage at API prices, discovery included.

With 0.3, two more runs, every step a fresh headless session: an amendment under a finished epic
(reported and decided by the user, not recompiled silently), and a project built with plain Spec Kit
that adopted the extension (its constitution and hand-written feature untouched). A planning repository
with two code repositories (provider `core`, consumer `cli`) built a cross-repository feature with every
task naming its repository and the provider changed first. Since 0.4 the extension runs a real
three-product, nine-repository project.

## Why

In blueprint-driven builds, most of a session's cost is re-reading context: every model call re-reads
everything already loaded. A session that opens a 100-page blueprint and greps it for the sections its
epic needs pays for those sections on every call that follows. On the example blueprint shipped with
idea-to-blueprint (581 KB, 9 epics), compiled feature folders are 63–127 KB each, and the compile itself
costs no model tokens. The extension keeps that rule everywhere: whatever a script can decide (the next
step, paths, branches, readiness, gates, metrics) is decided by the script, not by the model reading prose.

## Install

Requires Python 3.9+, Spec Kit 1.2+ and git.

```bash
uv tool install specify-cli --from git+https://github.com/github/spec-kit.git@v1.2.4
specify init my-product --integration claude
cd my-product
specify extension add blueprint --from https://github.com/mohsensamiei/speckit-blueprint/archive/refs/tags/v0.5.2.zip
```

Installing from a URL asks for confirmation once (answer `y`). For development, install from a local
clone with `specify extension add --dev /path/to/speckit-blueprint`.

Installing registers seven commands as Claude Code skills (`/speckit-blueprint-…`), two Spec Kit hooks
around `/speckit-implement`, and three Claude Code hooks in `.claude/settings.json` (session start, tool
use, stop) behind one internal handler.

## How you use it

You mostly talk in plain language; the `speckit-blueprint-flow` skill reads the project state
(`blueprint.py status`) and runs the right step.

```
"I want an app that …"          → /speckit-blueprint-discover   intake → research → decision brief → blueprint
                                  blueprint.py bootstrap         constitution rules, checklist, decisions log, CLAUDE.md
"continue"  (per epic)          → /speckit-blueprint-compile E03
                                  /speckit-tasks → /speckit-analyze
"continue"  (fresh session)     → /speckit-implement
                                     before: /speckit-blueprint-context  spec.md, rules, preconditions
                                     after:  /speckit-blueprint-review   independent review, review.md handoff
"clients should also get SMS"   → /speckit-blueprint-amend       edit the blueprint in place, recompile affected epics
"build E04 next to E03"         → blueprint.py workspace E04 --create
"I'm on a new machine"          → blueprint.py workspace setup, then workspace E03 --create
"where are we?"                 → a table per blueprint; or blueprint.py dashboard
```

Every new session starts with a short status block, so "continue" is enough. You still approve the
decision brief, product-changing amendments, migrations, and every commit and push.

| Command | Role |
|---|---|
| `speckit-blueprint-flow` | The natural-language entry; routes by the project's state |
| `speckit-blueprint-discover` | Idea → blueprint, at a guaranteed effort |
| `speckit-blueprint-amend` | Deliberate change to a blueprint, at a guaranteed effort |
| `speckit-blueprint-compile` | One epic → feature folder |
| `speckit-blueprint-context` | `before_implement` hook |
| `speckit-blueprint-review` | `after_implement` hook: an independent review at a guaranteed effort |
| `speckit-blueprint-hook` | Internal: Claude Code events |

## One model for one to many repositories

A **hub** is the repository where Spec Kit and this extension are installed: it holds the blueprints,
specs, constitution and the list of repositories. Code repositories stay ordinary repositories; nothing of
the extension is installed in them. A single-repository project is a hub whose only repository is itself;
a monorepo lists its folders as repositories.

```yaml
# .specify/extensions/blueprint/blueprint-config.yml
repositories:
  wiki: {path: ., branch_prefix: docs/}
  desk: {path: ../desk, remote: ../desk.git, gate: make check, base: main, branch_prefix: feat/}
  id:   {path: ../id, remote: git@github.com:acme/id.git, gate: make lint test}
```

`path` defaults to `../<name>`. `remote` is where `workspace setup` clones the repository from: a URL, or
one relative to the hub's own (`../desk.git`, as git's submodules allow), so the same config serves SSH
and HTTPS clones.

Each epic says which repositories it changes, in order (providers before consumers):

```
**Repositories**

| Repository | Order | Role | Base | Branch | Contracts |
|---|---|---|---|---|---|
| `id` | 1 | Provides the session API | | | provides API-21 |
| `desk` | 2 | Consumes it | | | consumes API-21 |
```

- Compile checks the names, gives each repository its branch (`<branch_prefix><feature folder>`, Spec
  Kit's convention; a Branch cell wins) and base, and writes spec.md "Target Repositories" and plan.md
  "Repositories". Every task starts with its repository in brackets and gives paths relative to it.
- `blueprint.py repos E03` shows each repository's branch, uncommitted files, ahead/behind and merged
  state; `blueprint.py gate E03` runs every repository's gate and records the result.
- Sessions start in the hub, even for an epic that changes one repository: the specs, hooks and metrics
  are there. Merge the repositories in the table's order and the hub's branch last, so a DONE visible in
  the hub means the code is merged everywhere.
- Work that belongs to no blueprint stays each repository's own business.

## Parallel epics

- `**Depends on** E00, E02` is read: an epic is `blocked` until its dependencies are DONE in the checkout.
- `blueprint.py workspace E04 --create` makes the epic's worktrees, in the hub and in every repository it
  changes, all on its branch, and prints how to open a session there (`claude --add-dir …`).
- A board in the hub's git directory (shared by every worktree, never committed) records which worktree
  builds which epic. Status shows them as `elsewhere` and moves on to the next ready epic; compile
  refuses an epic another worktree holds (`--take` moves it); the hook refuses a compile that would switch
  the active feature under another live session in the same worktree.
- Decisions and amendments made while building are numbered within the epic (`DR-E03-1`, `A-E03-1`), so
  parallel branches never take the same number.

## A new machine, or one repository on its own

A code repository on its own holds code only; features are built from the hub, and getting it is one
clone:

1. Clone the hub next to the repository you have (`git clone <hub url> ../wiki`). Paths default to
   `../<name>`, so the hub finds that repository without a setting.
2. `blueprint.py workspace setup` clones every repository missing on this machine from its `remote` and
   fetches the others; `workspace setup E03` limits it to one epic's repositories.
3. `blueprint.py workspace E03 --create` continues the epic from where it was pushed: a branch a remote
   has (the hub's, with its ticked tasks, and each repository's) is checked out and tracked, not started
   again from the base.

`status` marks an epic whose branch exists, here or on a remote, but whose folder is not in this checkout
as `started`: in progress on that branch (beside the one you are on, on another machine, or by someone
else). It moves on to the next ready epic instead of offering to compile that one again, and says how to
continue it, or, when the base branch already has its folder, that a pull or merge is all it takes. The
same holds in a single repository with a branch per feature: back on main, an epic in progress on its
branch shows as `started`. Status also names the repositories the next epic needs that are not on this
machine.

So that a session opened in a code repository knows where to go, `blueprint.py workspace link` writes a
short block into each code repository's `CLAUDE.md`: which hub specifies its features, how to clone it,
and that features are built from there. It never commits; commit the file in each repository.

Not supported, on purpose: building a feature without access to the hub (give read access; the hub holds
documents), and specs kept inside the code repositories. An epic that changes two repositories would need
its spec split or copied, and status, the board and metrics would be scattered; see
[docs/DESIGN.md](docs/DESIGN.md).

## Guaranteed effort

Discovery decides the product and a review is the last line before a human reads a large diff, so they
are worth the highest effort; implementation follows a decided spec and is where most tokens go.

```yaml
effort:          # steps that must run at a set level; everything else uses the session's level
  discover: max
  amend: max
  review: max
effort_model: inherit
```

A listed step runs its reasoning in a subagent started at that level, whatever the session's level is.
Subagents cannot talk to the user, so discover and amend run in rounds: the subagent works up to a
checkpoint (intake questions, the decision brief, a drafted amendment) and returns; the main session asks
you; your answer goes back to the same subagent. The review runs in a fresh subagent that did not write
the code. A Claude Code hook checks the agent's real level: if something lowers it (`CLAUDE_CODE_EFFORT_LEVEL`,
an organization cap, the model), the step stops and says why, and the guard refuses to write a blueprint
or a DONE review below the level.

## Metrics, estimates and the dashboard

At the end of every turn the stop hook reads the session's Claude Code transcript and records, per epic
(and per blueprint for discovery and amendments): agent time split into model and tool time, time spent
waiting for you and the number of human gates, tokens, cost, models, effort levels, phases, repositories,
subagents, task ticks and time per story. It stays in the hub's git directory unless `metrics: commit`
also writes a summary into each finished epic's folder. `blueprint.py metrics --backfill` reads the
transcripts Claude Code still keeps.

`blueprint.py estimate` turns finished epics into per-AC rates and gives every remaining epic a P50–P90
range of agent time, cost and calendar time, and the project a finish date that follows the `Depends on`
graph and the parallelism seen so far. Compile records the estimate before building; the review adds
"Estimate vs actual". Three finished epics are needed before the ranges are more than low confidence.

```bash
python3 .specify/extensions/blueprint/scripts/blueprint.py dashboard            # live, on 127.0.0.1:8765
python3 .specify/extensions/blueprint/scripts/blueprint.py dashboard --export status.html   # a snapshot to share
```

The dashboard shows totals, remaining ranges and the finish date; every live session (what it is doing,
or how long it has waited for you); epics expandable to stories and tasks with their repositories, branches
and gates; a timeline with forecast ranges; where each epic's time went; and which epics touch each
repository. For Claude Code's status line, add to `.claude/settings.json`:

```json
{"statusLine": {"type": "command", "command": "python3 .specify/extensions/blueprint/scripts/blueprint.py statusline"}}
```

## Adding it to an existing Spec Kit project

The extension can join a project that already has its own constitution, features and workflow:

- **Constitution.** Bootstrap does not rewrite a constitution the project wrote. It adds one managed block
  per blueprint (before `## Governance`) that applies only to features compiled from that blueprint and
  yields to the project's principles, bumps the version (`constitution_version_bump`) and sets Last
  Amended. Set `constitution_mode` to force `own` or `append`.
- **Existing features.** The hooks do nothing for feature folders without `.blueprint.json`. Compiled
  epics continue the project's feature numbering.
- **Project rules.** The commands read the constitution's own principles, `CLAUDE.md` and `AGENTS.md`
  first and follow them where they are stricter.
- **Existing files.** `docs/REVIEW-CHECKLIST.md` is left alone if the project has its own; `CLAUDE.md`
  only gains a managed block (or nothing, with `claude_md: false`); `docs/DECISIONS.md` is created only if
  missing. Every path can move (`blueprint_path`, `decisions_path`, `review_checklist_path`).
- **Discovery.** With an existing product, discover runs in brownfield mode: the repositories are the
  first source, what exists is summarised rather than re-decided, and the blueprint covers only the new
  idea.
- **Session start.** Without a blueprint, the status block says nothing in a project that already has
  features or its own constitution.

## Several blueprints in one project

A hub can hold one blueprint per product:

```yaml
default_blueprint: one
blueprints:
  one:
    path: docs/BLUEPRINT.md
  desk:
    path: docs/desk/BLUEPRINT.md
    number_base: 500
    sources: [desk]
```

Each blueprint has its own decisions log and review checklist (next to its file by default), its own
constitution and `CLAUDE.md` blocks, its own folder numbers (`number_base`) and an ID prefix used where
two products' IDs meet (`DESK:DR-03`). Every command takes `--blueprint <id>` or a qualified argument
(`compile desk:E01`, `find DESK:DR-03`) and defaults to the active feature's blueprint. An epic may depend
on another blueprint's epic (`**Depends on** one:E18`).

### Next to the idea-to-blueprint skill

The extension ships idea-to-blueprint's method unmodified, so the standalone skill is not needed in a Spec
Kit project, but it does not have to be removed: inside a project with the extension, the flow and
discover skills say to use them instead. Outside Spec Kit projects the standalone skill works as before.

## What compile produces

| File | Content |
|---|---|
| `spec.md` | Epic context; Target Repositories; each story as a prioritised user story with its ACs, edge cases and tests verbatim; resolved edge cases; Definition of Done as success criteria; personas, flows, screens with their copy tables, metrics; open questions with defaults |
| `plan.md` | Goal; blueprint §12.2, §13.1, §13.3, §13.4 (configurable); Repositories (order, base, branch, contracts); the stories' tasks as "Blueprint Task Seeds" that `/speckit-tasks` keeps by ID; preconditions and `[VERIFY-AT-BUILD]` lines; risks |
| `research.md` | Decision records, architecture drivers, fitness functions and sources the epic references |
| `data-model.md` | §12.4 conventions and the entities the epic touches |
| `contracts/` | API rows (with §12.5 conventions) and integration cards |
| `quickstart.md` | Epic-level tests and manual checks |
| `.blueprint.json` | Trace: blueprint id and hash, a hash per resolved definition (staleness), dependencies, repositories with branches, ACs per story, the estimate made before building |

The constitution rules come from §12.10, §13.2, §13.5–13.7, §13.9–13.10, §14.2, §14.3 and §14.8 plus five
fixed principles; the review checklist is §14.4.

## Configuration

`.specify/extensions/blueprint/blueprint-config.yml` (see [config-template.yml](config-template.yml)):
blueprints, repositories, branch prefix and worktree folder, sections compiled into the constitution and
every plan, guaranteed effort per step, metrics, the guard. A `.local.yml` next to it overrides it for one
machine (a repository's settings one by one: `desk: {remote: …}` there keeps its path and gate).

## Helper script

`.specify/extensions/blueprint/scripts/blueprint.py` (standard library only):

```bash
blueprint.py status [--json|--brief]        # state per epic and the next step
blueprint.py compile E03 [--force] [--take] # feature folder for one epic
blueprint.py bootstrap [--skip-lint]        # constitution rules, checklist, decisions log, CLAUDE.md block
blueprint.py activate E03                   # make a compiled epic the active feature
blueprint.py context | review-facts         # facts for the implement hooks
blueprint.py section 14.4 | find ENT-Order  # read part of a blueprint
blueprint.py lint | refs | stale | epics    # structure, references, staleness
blueprint.py accept E01 --note "…"          # a built epic stays as built after a blueprint change
blueprint.py repos E03 | gate E03           # repositories' git state; run their gates
blueprint.py workspace E03 [--create]       # the epic's worktrees (a pushed branch is continued)
blueprint.py workspace setup [E03] | link   # clone and fetch the repositories here; point them to the hub
blueprint.py claims [--release KEY]         # which worktree builds which epic
blueprint.py metrics [E03] [--brief|--write|--backfill]
blueprint.py estimate [E05]
blueprint.py dashboard [--export FILE] | statusline
blueprint.py migrate [--apply]              # upgrade a 0.4 project
# with several blueprints: --blueprint <id> on any command, or desk:E03 / DESK:DR-03
```

## Upgrading from 0.4

1. Update the extension (`specify extension add … v0.5.2`).
2. Run `blueprint.py migrate`: a dry run listing what it will change (config `schema: 2`, a
   `repositories:` map from the names your blueprints and tasks use, each epic's repository fields as a
   table with the old prose kept as notes, ids on traces and managed blocks) and anything it could not
   convert. Compiled epics stay as built: the conversion is recorded in each trace's `accepted` list
   instead of reporting the epic changed since compile. A review that said `Status: DONE` keeps counting
   as done without an evidence entry per AC while review.md is unchanged (`review_before_0_5` in the
   trace). Review it, then `blueprint.py migrate --apply` and check the diff.
3. Run `blueprint.py bootstrap --blueprint <id>` for each blueprint, so the managed blocks use this
   version's wording; then `blueprint.py status`.
4. Optional: `blueprint.py metrics --backfill` to start the metrics from the transcripts Claude Code still
   keeps.

Migrated with 0.5.0 already? After updating, status asks for `blueprint.py migrate` once more: it records
the reviews written before 0.5 and changes nothing else. An epic 0.5.0 left "changed since compile" needs
`blueprint.py accept <epic> --note "…"`.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests run the script against [tests/fixtures/mini-blueprint.md](tests/fixtures/mini-blueprint.md),
real git repositories and worktrees in temporary folders, and synthetic Claude Code transcripts.
[docs/DESIGN.md](docs/DESIGN.md) records the design and the reasons behind it.

## Keeping up with idea-to-blueprint

[upstream/idea-to-blueprint/](upstream/idea-to-blueprint) is an exact copy of the skill (its `SKILL.md`,
`references/`, `scripts/`, `assets/` and `LICENSE`) at the commit pinned in
[upstream/UPSTREAM.json](upstream/UPSTREAM.json). Nothing in it is edited here. This extension's changes
live in its own files, each of which reads the upstream copy and states where it differs:

| File | Changes to upstream |
|---|---|
| [commands/discover.md](commands/discover.md) | Runs upstream `SKILL.md` in subagent rounds; adds the incremental and brownfield modes; replaces phase 4 (lint, bootstrap) and phase 5 (Spec Kit flow) |
| [references/speckit-flow.md](references/speckit-flow.md) | Overrides `blueprint-template.md` where compile and the Spec Kit flow need it |
| [scripts/lint_blueprint.py](scripts/lint_blueprint.py) | Loads the upstream linter and drops the warnings it lists in `DROPPED` |

To update:

```bash
python3 tools/sync_upstream.py            # or a tag/commit: tools/sync_upstream.py <ref>
python3 -m unittest discover -s tests -v
git diff -- upstream/
```

The sync replaces the copy, rewrites the manifest, prints the added, removed and changed files, and lists
any anchor the overlay relies on that upstream removed. The tests fail if the copy was edited by hand or
an anchor is gone. A weekly workflow ([.github/workflows/upstream.yml](.github/workflows/upstream.yml))
opens one issue when upstream moves past the pinned commit.

## Limitations

- Claude Code only. Other agents would need the hooks, the subagent rounds and the flow skill adapted;
  they keep the project's own Spec Kit workflow.
- Metrics read Claude Code's transcripts, which are not a public API: the parser is defensive and records
  the Claude Code version, but a format change can leave gaps until it is updated. The live view sees the
  sessions of this machine; other people's work arrives with the per-epic summaries (`metrics: commit`).
- Compile relies on the blueprint conventions in `upstream/idea-to-blueprint/references/blueprint-template.md`
  and `references/speckit-flow.md`. Blueprints written by hand in another shape resolve fewer IDs;
  `blueprint.py refs` shows which.

## Credits and license

MIT — see [LICENSE](LICENSE). The discovery method, references and linter come from
[idea-to-blueprint](https://github.com/iniesohidham/idea-to-blueprint) (MIT, © 2026 Mahdi); see
[NOTICE](NOTICE).
