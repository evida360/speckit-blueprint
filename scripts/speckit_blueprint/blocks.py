"""Managed blocks in the constitution and CLAUDE.md, and the constitution's state.

Every block carries its blueprint's id (`<!-- speckit-blueprint:rules:start id=desk -->`; `id=default` in a
project with one blueprint). Blocks without an id come from schema 1; `blueprint.py migrate` marks them.
"""

from __future__ import annotations

import re
from pathlib import Path

from .core import GENERATED_MARK

CONSTITUTION = Path(".specify") / "memory" / "constitution.md"


def marker(kind: str, which: str, block_id: str | None) -> str:
    """kind "" (CLAUDE.md) or "rules:" (constitution); which "start" or "end"."""
    return f"<!-- speckit-blueprint:{kind}{which}{'' if block_id is None else ' id=' + block_id} -->"


def managed_blocks(text: str, kind: str) -> list[tuple[str | None, int, int]]:
    """(id or None, start, end) of every managed block of this kind, markers included."""
    out = []
    pos = 0
    for m in re.finditer(r"<!-- speckit-blueprint:" + re.escape(kind) + r"start(?: id=([a-z0-9][a-z0-9-]*))? -->", text):
        if m.start() < pos:
            continue
        end = marker(kind, "end", m.group(1))
        j = text.find(end, m.end())
        if j >= 0:
            pos = j + len(end)
            out.append((m.group(1), m.start(), pos))
    return out


def unmarked_blocks(text: str, kind: str) -> int:
    """Schema-1 blocks without an id."""
    return sum(1 for i, _, _ in managed_blocks(text, kind) if i is None)


def block_id(bcfg: dict) -> str:
    return bcfg["id"]


def owns_block(bcfg: dict, found_id: str | None) -> bool:
    return found_id == block_id(bcfg)


def find_block(text: str, kind: str, bcfg: dict) -> tuple[int, int] | None:
    return next(((s, e) for i, s, e in managed_blocks(text, kind) if owns_block(bcfg, i)), None)


def own_owner(text: str) -> str | None | bool:
    """For a constitution compiled whole (own mode): the blueprint id in its header, None for a header
    without one (schema 1); False when the file is not compiled whole."""
    if GENERATED_MARK not in text:
        return False
    m = re.search(r"blueprint-id ([a-z0-9][a-z0-9-]*)", text.split("\n", 1)[0])
    return m.group(1) if m else None


def owns_constitution(bcfg: dict, text: str) -> bool:
    return own_owner(text) == bcfg["id"]


def recorded_content_sha(text: str, bcfg: dict) -> str | None:
    """The content hash of the configured sections when the rules were last written."""
    span = find_block(text, "rules:", bcfg)
    if span:
        m = re.search(r"content-sha ([0-9a-f]+)", text[span[0]:span[1]])
        return m.group(1) if m else None
    if owns_constitution(bcfg, text):
        m = re.search(r"content-sha ([0-9a-f]+)", text.split("\n", 1)[0])
        return m.group(1) if m else None
    return None


def constitution_state(root: Path, bcfg: dict, content_sha: str | None = None) -> str:
    """missing | appended | compiled | outdated (its blueprint sections changed since) | not-from-blueprint."""
    c = root / CONSTITUTION
    if not c.is_file():
        return "missing"
    text = c.read_text(encoding="utf-8")
    if find_block(text, "rules:", bcfg):
        state = "appended"
    elif owns_constitution(bcfg, text):
        state = "compiled"
    else:
        return "not-from-blueprint"
    if content_sha and recorded_content_sha(text, bcfg) not in (None, content_sha):
        return "outdated"
    return state


def block_content_sha(text: str, cfg: dict) -> str | None:
    span = find_block(text, "rules:", cfg)
    m = re.search(r"content-sha ([0-9a-f]+)", text[span[0]:span[1]]) if span else None
    return m.group(1) if m else None


def rules_text(root: Path, bcfg: dict) -> str | None:
    """The constitution rules that govern this blueprint's features: its managed block, or the whole
    constitution when it was compiled from this blueprint."""
    c = root / CONSTITUTION
    text = c.read_text(encoding="utf-8") if c.is_file() else ""
    span = find_block(text, "rules:", bcfg)
    if span:
        return text[span[0]:span[1]]
    if owns_constitution(bcfg, text):
        return "".join(
            part for k, part in enumerate(re.split(r"(<!-- speckit-blueprint:rules:start.*?<!-- speckit-blueprint:rules:end[^>]*-->)", text, flags=re.S))
            if k % 2 == 0
        )
    return None
