"""Answers come from the blueprint (DESIGN.md "Answers from the blueprint").

Every question asked while building a blueprint feature, by this extension or by Spec Kit's own commands
(clarify's Recommended option, specify's [NEEDS CLARIFICATION], analyze's recommendations, checklist's
questions, a plan unknown, implement's "proceed?"), carries the default the blueprint already decides,
tagged with where it says so. Only when nothing decides it does the question say so, and the suggestion is
marked as the model's own. The Stop hook checks Spec Kit's recommendation markers for a tag.
"""

from __future__ import annotations

import re

TAGS = {"grounded": "[BLUEPRINT —", "silent": "[NOT IN BLUEPRINT —"}

RULE = ("Every default or recommended answer comes from the feature folder, the decisions log or the "
        "blueprint and is tagged `[BLUEPRINT — <section or ID>]`; when none decides it, say "
        "`[NOT IN BLUEPRINT — searched: …]` and mark any suggestion as your own. A question the blueprint "
        "already answers is not asked. An accepted answer becomes an amendment (product) or a decision "
        "record (technical), never an edit to a compiled file.")

STEPS = ("specify", "clarify", "plan", "tasks", "analyze", "checklist")

# Spec Kit's recommendation markers: clarify's "**Recommended:** Option B - …" and "**Suggested:** …",
# specify's "**Suggested Answers**" table. Translated markers are not recognised (the check fails open).
MARKER_RE = re.compile(r"\*\*(Recommended|Suggested)(?: Answers?)?:?\*\*:?", re.I)


def tagged(text: str) -> bool:
    return any(t in text for t in TAGS.values()) or "[NOT IN BLUEPRINT" in text or "[BLUEPRINT" in text


def uncited(message: str) -> list[str]:
    """The recommendation lines of a message that name no source. A `**Suggested Answers**` block counts
    as cited when any of its next lines (up to a blank line after the table) carries a tag."""
    lines = (message or "").split("\n")
    out = []
    for i, line in enumerate(lines):
        if not MARKER_RE.search(line):
            continue
        window = [line]
        if re.search(r"Suggested Answers", line, re.I):
            for nxt in lines[i + 1 : i + 15]:
                window.append(nxt)
                if not nxt.strip() and len(window) > 3:
                    break
        else:
            window += lines[i + 1 : i + 2]
        if not tagged("\n".join(window)):
            out.append(line.strip()[:160])
    return out


def open_questions(units_text: list[str]) -> list[dict]:
    """OQ table rows as {id, question, default}, from the header row of each row's table."""
    out = []
    for header, row in units_text:
        cols = [c.strip().lower() for c in header.strip().strip("|").split("|")]
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if not cells:
            continue
        rec = {"id": re.sub(r"[*`]", "", cells[0]).strip()}
        for name, key in (("question", "question"), ("default", "default")):
            k = next((j for j, c in enumerate(cols) if name in c), None)
            if k is not None and k < len(cells):
                rec[key] = cells[k]
        out.append(rec)
    return out
