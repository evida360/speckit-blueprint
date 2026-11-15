## {{PRODUCT}} — blueprint-driven features (speckit-blueprint)

{{SCOPE}} are compiled from `{{BLUEPRINT}}`, one epic at a time, with Spec Kit. Other features follow
this project's own workflow and rules.
{{ID_NOTE}}
- For a new idea, "continue" on a blueprint epic, a change to an epic's scope or ACs, "what's next", or
  building epics in parallel, use the `speckit-blueprint-flow` skill. It reads the project state and runs
  the next step.
- Flow per epic: `/speckit-blueprint-compile {{EPIC}}` → `/speckit-tasks` → `/speckit-analyze` →
  `/speckit-implement` (its hooks load the spec and run `/speckit-blueprint-review`).
- In those feature folders every file other than `tasks.md` and `review.md` is compiled, and so is the
  blueprint part of `.specify/memory/constitution.md`. Never edit them by hand: product or AC changes go
  through `/speckit-blueprint-amend`.
- Do not read `{{BLUEPRINT}}` whole. Use `blueprint.py section <n>{{FLAG}}` or `blueprint.py find <ID>{{FLAG}}`
  (in `.specify/extensions/blueprint/scripts/`) for anything outside the active feature folder.
- Tasks name their repository in brackets and give paths relative to it. One epic per worktree:
  `blueprint.py workspace <epic> --create` sets one up.
- Decisions taken while building: `DR-<epic>-<n>` in `{{DECISIONS}}` (index row too); `blueprint.py context`
  gives the next ID.
- Start a fresh session for each epic's implementation.
- Where the rest of this file (and anything it imports) or the constitution's own principles set a
  stricter rule, that rule wins.
