"""Guaranteed effort (DESIGN.md "Guaranteed effort").

Steps listed under `effort:` run their reasoning in a subagent started at that level. The subagent's first
command is `blueprint.py require-effort <step>`; the PreToolUse hook reads the real level from its input
and blocks the command when it is lower. The guard applies the same check to writing a blueprint and to
writing `Status: DONE` into a review.
"""

from __future__ import annotations

import os
import re

from .config import EFFORT_STEPS, effort_rank

REQUIRE_RE = re.compile(r"blueprint\.py[\"']?\s+require-effort\s+([a-z]+)")


def require_effort(cfg: dict, step: str) -> int:
    """The command only states the requirement; the hook enforces it before the command runs."""
    if step not in EFFORT_STEPS:
        print(f"error: require-effort takes one of {', '.join(EFFORT_STEPS)}", file=__import__("sys").stderr)
        return 1
    level = cfg["effort"].get(step)
    if level is None:
        print(f"require-effort: {step} runs at the session's level (it is not listed under `effort:`).")
    else:
        print(f"require-effort: {step} runs at effort {level}; the PreToolUse hook checked this agent's real level.")
    return 0


def payload_level(payload: dict) -> str | None:
    e = payload.get("effort")
    if isinstance(e, dict):
        return e.get("level")
    return e if isinstance(e, str) else None


def shortfall(cfg: dict, step: str, payload: dict, what: str) -> str | None:
    """A refusal message when the agent behind this tool call runs below the step's level. None when it
    does not, when the step is not guaranteed, or when the hook input carries no effort (older Claude
    Code: nothing can be checked, so nothing is blocked)."""
    need = cfg["effort"].get(step)
    have = payload_level(payload)
    if not need or have is None or effort_rank(have) >= effort_rank(need):
        return None
    env = os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")
    agent = payload.get("agent_type")
    if env:
        cause = f"CLAUDE_CODE_EFFORT_LEVEL={env} is set and overrides every subagent's level; unset it"
    elif agent:
        cause = f"the subagent was not started with effort {need}, or an organization cap or the model limits it"
    else:
        cause = "it runs in the main session"
    who = f"this {agent} subagent" if agent else "this session"
    fix = (f"Start {step} in a subagent with the Agent tool's `effort` set to {need}"
           + (f" (or ask the user to run `/effort {need}` and reply continue)" if not agent else "") + ".")
    return f"speckit-blueprint: {what} needs effort {need} ({step}), but {who} runs at {have} ({cause}). {fix} Nothing was done."
