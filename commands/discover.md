---
description: "Turn an idea into an evidence-backed build blueprint (intake, research, decision brief, epics) in a Spec Kit project with this extension: the idea-to-blueprint method, run at a guaranteed reasoning level, handing its epics to Spec Kit. Usually started by speckit-blueprint-flow."
---

# Idea → Build Blueprint

Turn a raw idea (even 2–3 lines) into one markdown document complete enough to build the product epic
by epic without guessing. Build sessions never read the whole blueprint; compile hands each epic exactly
the parts it needs, so be complete here, not brief. Follow
`.specify/extensions/blueprint/references/conventions.md`.

## User Input

```text
$ARGUMENTS
```

## The method comes from idea-to-blueprint

The method is the idea-to-blueprint skill, shipped unmodified with this extension:
`.specify/extensions/blueprint/upstream/idea-to-blueprint/SKILL.md`. Paths it mentions (`references/…`,
`scripts/…`, `assets/…`) are relative to that folder. Read each reference when its phase starts. Where this
file and the skill disagree, this file wins; where `references/speckit-flow.md` and the skill's
`references/blueprint-template.md` disagree, `speckit-flow.md` wins. The skill's language convention,
market detection, design reference handling and guardrails apply unchanged; ignore its "Test prompts".

## 1. Which blueprint, which mode (here, before anything else)

Run `blueprint.py status --json`. The target is the blueprint the input names (`--blueprint <id>`, or a
product name), else the one the idea extends. An idea for a product no blueprint covers is a **new
blueprint**: ask the user for its id (default: the product name in lowercase), its path (default: its own
folder, `docs/<id>/BLUEPRINT.md`), and the repositories that hold the existing product (default: none, or
those `repositories:` already lists); add the entry under `blueprints:` (and any
new repository under `repositories:`) in `.specify/extensions/blueprint/blueprint-config.yml`. Never add
one product's epics to another product's blueprint.

- **New product**: the target's file does not exist and no product backs it.
- **Incremental**: the blueprint exists and the idea adds scope. Read only what you must stay consistent
  with (`section`, `find`), continue every ID series, add epics after the last one, add rows to the
  Amendments log; decided sections change only through `/speckit-blueprint-amend`.
- **Brownfield**: the product exists, in this repository or in the blueprint's `sources` (repository
  names from `repositories:`, or paths). The product is the first source: read the constitution,
  `CLAUDE.md`/`AGENTS.md`, README, docs, existing specs and every source before intake, and cite them as
  `[VERIFIED — repo:<repository>/<path>]`. Do not re-decide what exists (stack, architecture, gate,
  conventions are summarised with paths); research only what the idea adds; never invent the existing
  product (`[UNKNOWN → OQ-nn]` instead). Epic 00 is a short **Baseline** (the existing gate is green,
  bootstrap's outputs exist). Check every decision and AC against the project's principles.

## 2. The rounds

Discovery decides the product, so it runs at the level `effort.discover` gives (default `max`), in a
subagent (conventions §3). The subagent works up to a checkpoint and returns; you talk to the user; the
answers go back to the same subagent. Checkpoints are files in `<blueprint folder>/discovery/`
(`intake.md`, `brief.md`, `sources.md`), written by the subagent, so a lost session resumes from them. If
`discover` is not listed under `effort`, run the same rounds in this session.

Give the subagent: the idea (verbatim), the target id, path, mode and sources, this file's path and the
skill's, and the round to do.

1. **Intake (subagent).** Phase 0 of the skill: the warm-up searches and ONE batch of questions with a
   default each (closed questions marked as such), saved to `discovery/intake.md`; it returns the batch.
   Each default names its source: in incremental or brownfield mode `[BLUEPRINT — …]` or
   `[VERIFIED — repo:<repository>/<path>]` (a question either already answers is not asked); in a new
   product a source `S-nn` or `[ASSUMED — why]`. The brief's assumptions keep those tags.
   **You:** ask the user (AskUserQuestion for the closed ones, prose for the rest) and send their answers,
   verbatim, back.
2. **Research and brief (same subagent).** It records the answers (unanswered → the defaults, as
   `[ASSUMED]` rows), runs phase 1 (sources logged in `discovery/sources.md`) and phase 2, saves the
   brief to `discovery/brief.md` and returns it. **You:** post the brief and wait for "go" or corrections
   (unless the user pre-authorised writing); send the reply verbatim. Corrections → it revises and returns
   the brief again.
3. **Write and lint (same subagent).** Phase 3, with these changes:
   - Read `.specify/extensions/blueprint/references/speckit-flow.md` with the template. It replaces the
     session protocol (§18), the appendices and Epic 00, and sets the shapes compile relies on: every ID
     defined once in its home section, `**Depends on**` with epic IDs, and in a project with
     `repositories:` a `**Repositories**` table in every epic.
   - Do not read the skill's `references/session-protocol.md` or use its `assets/`.
   - Save to the target's path, in chunks.
   - Phase 4 is: `blueprint.py lint --blueprint <id>` until it exits 0 (conventions §6), then the human
     pass (the Executive summary, one persona, one flow and one epic read as a stranger). It returns a
     5–10 line wrap-up: the Assumptions table and Open Questions to look at, and the two or three things
     it is least sure about.
4. **Bootstrap and hand off (you).** Run `blueprint.py bootstrap --blueprint <id>` (incremental mode:
   only when status reports the constitution `outdated`). Do not create `PROGRESS.md`, `AGENTS.md` or
   other files from the skill's `assets/`. Relay the wrap-up without re-summarising the document, and
   say the next step: a fresh session, then "continue". Do not commit unless the user asks.

Phase 5 (build sessions) is the Spec Kit flow that `speckit-blueprint-flow` runs; the skill's session
prompts appendix is not written.
