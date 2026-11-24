---
description: "Compile one blueprint epic into a Spec Kit feature folder, claim it for this worktree and make it the active feature. Usually run by speckit-blueprint-flow."
---

# Compile an epic

A script turns one `## Epic nn` into a Spec Kit feature folder: every ID the epic uses (personas, flows,
screens, copy lines, entities, endpoints, integrations, edge cases, decisions, drivers, risks, open
questions, sources) is resolved to its definition, so the build session reads only what this epic needs.
Follow `.specify/extensions/blueprint/references/conventions.md`.

## User Input

```text
$ARGUMENTS
```

The input names the epic (`E03`, `3`), qualified in a project with several blueprints (`desk:E03`). Empty
input: take `next.epic` from `blueprint.py status --json`.

## Steps

1. Run `blueprint.py compile <epic>`. It writes `spec.md`, `plan.md`, `research.md`, `quickstart.md`,
   `data-model.md` and `contracts/` when the epic touches them, and the `.blueprint.json` trace; claims the
   epic on the board for this worktree; and makes the folder the active feature. Its JSON names the
   folder, the hub branch and, per repository, the base and branch.
2. Refusals (when you ask the user, the default is the refusal's own reason, conventions §8):
   - completed tasks in the folder: the epic is partly built and a product change belongs in
     `/speckit-blueprint-amend`. Never add `--force` on your own.
   - another worktree builds the epic: continue it there. `--take` moves it here only when the user says
     so.
   - unresolved IDs, a repository the config does not list, or a schema-1 field: fix the blueprint
     through `/speckit-blueprint-amend` (or run `blueprint.py migrate`), lint, compile again. Never build on
     an unresolved ID.
3. Never edit the compiled files: they are regenerated on every compile, and a hook blocks hand edits.
4. Branches: if the project creates a branch per feature (Spec Kit's git extension, or its rules), use the
   branch names in the JSON. To build this epic beside another one, use `blueprint.py workspace <epic>
   --create` instead: it makes the worktrees on those branches.
5. Report the folder, the files with their sizes against the blueprint's size, the repositories with
   their branches, and the next step: `/speckit-tasks`, then `/speckit-analyze`.
