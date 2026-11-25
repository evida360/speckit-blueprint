---
description: "Before a Spec Kit command that asks questions (hook: specify, clarify, plan, tasks, analyze, checklist): its proposed answers come from the blueprint, tagged with where it says so, never from a guess. Does nothing for features and projects the blueprint does not cover."
---

# Answers from the blueprint (before_* hooks)

Spec Kit's commands propose answers "based on best practices": clarify's Recommended option, specify's
Suggested Answers, analyze's recommendations, checklist's defaults. In a blueprint feature those answers
were already decided, with evidence, by the blueprint. This hook makes the command that follows use them.
Follow `.specify/extensions/blueprint/references/conventions.md` (§8 is this command's rule).

## User Input

```text
$ARGUMENTS
```

## Steps

1. Run `blueprint.py answers --step <step>`, where `<step>` is the Spec Kit command this hook runs
   before (specify, clarify, plan, tasks, analyze or checklist). If `applies` is false, say nothing,
   change nothing and return to it.
2. Hold conventions §8 for the whole command that follows:
   - Every proposed, recommended or default answer is looked up in `look_in`, in its order, and carries
     its tag: `[BLUEPRINT — <section or ID>]`, or `[NOT IN BLUEPRINT — searched: …]` with the suggestion
     marked as your own.
   - A question the blueprint already answers is not asked; state the answer with its tag.
   - `open_questions` are the blueprint's open questions for this epic with their defaults: a question
     about one of them proposes that default, tagged `[BLUEPRINT — OQ-nn default]`.
3. Then, by `scope` and `step`:

| scope | step | Do |
|---|---|---|
| feature | clarify | Let clarify ask, with tagged recommendations. Clarify writes nothing into spec.md (a hook refuses it): each accepted answer goes to `record` (an amendment for a product answer, `next_decision_id` in `decisions_log` for a technical one). Report those instead of "sections touched". |
| feature | analyze | Each Recommendation names the blueprint section or ID the spec, plan, research, data-model or contracts text was compiled from, and the route `/speckit-blueprint-amend` (never `/speckit-specify`, `/speckit-plan` or a hand edit). A finding only in tasks.md is fixed in tasks.md, naming the task seed or AC it must cover; `blueprint.py tasks-graph` findings (a missing dependency, a task with no file) are tasks.md findings. A vague term the blueprint quantifies elsewhere (a driver target, a budget) is a missing reference, not a number to invent. The remediation offer's recommended answer is tagged too. |
| feature | checklist | Questions take their defaults from the epic (its risks, edge cases, the quality rules). Items cite blueprint IDs (`[E03-S02 AC-4]`, `[§12.5 API-07]`) where Spec Kit cites `Spec §FR-n`. |
| feature | plan | The plan and its design files are compiled: stop and say `/speckit-tasks` is next. A real unknown becomes a decision record or an amendment. |
| feature | tasks | The scope is every story of the epic, not "User Story 1". Task seeds come from plan.md, paths from its Technical Context. A choice none of them makes is a decision record. Every task names the files it writes; the first task of each story phase ends with `(depends on …)` naming the earlier tasks it really needs (plan.md's task-seed instructions). The after_tasks hook checks the graph. |
| project | specify, plan | The project's own workflow decides. A question about a product in `blueprints` takes its default from that blueprint, tagged; research agents get the same instruction. If the description names one of its `epics`, stop: an epic is compiled (`/speckit-blueprint-compile`), never specified. |

4. Return to the Spec Kit command.
