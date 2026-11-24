"""The review loop (DESIGN.md "Review loop").

review.md keeps one `### Pass <n>` per review pass under `## Review passes`. A pass is a full, independent
review at the guaranteed effort that fixes nothing: it covers every AC of every story, every checklist item
(`CL-n`, in the checklist's order) for every story, and every Definition of Done line (`DoD-n`), and lists
every finding in a table (`RV<n>-<k>`). The session fixes the findings at its own level and marks each row
`fixed: …`; a fresh pass then reviews everything again. The epic is done when the last pass is complete and
found nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

PASSES_RE = re.compile(r"^##\s+Review passes\s*$", re.M | re.I)
PASS_RE = re.compile(r"^###\s+Pass\s+(\d+)\b.*$", re.M | re.I)
FINDING_RE = re.compile(r"^\|\s*\**`?(RV\d+-\d+)`?\**\s*\|(.*)\|\s*$")
STORY_RE = re.compile(r"(?:[A-Za-z][A-Za-z0-9]*:)?(E\d{2}-S\d{2})\b")
CHECK_ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+\[[ xX]\]")


def named_refs(text: str, prefix: str) -> set[int]:
    """The numbers `text` names for `prefix` (AC, CL, DoD): one by one, in ranges and in pairs."""
    p = re.escape(prefix)
    out = {int(n) for n in re.findall(r"(?<![\w-])" + p + r"-(\d+)(?!\d)", text)}
    for a, b in re.findall(r"(?<![\w-])" + p + r"-(\d+)\s*(?:to|through|…|\.\.\.|–)\s*(?:" + p + r"-)?(\d+)(?!\w|\.\d)", text):
        if 0 <= int(b) - int(a) <= 200:
            out.update(range(int(a), int(b) + 1))
    for m in re.finditer(r"(?<![\w-])" + p + r"-\d+(?:/(?:" + p + r"-)?\d+)+(?!\w|\.\d)", text):
        out.update(int(n) for n in re.findall(r"\d+", m.group(0)))
    return out


def split_passes(text: str) -> tuple[str, list[dict]]:
    """review.md without its `## Review passes` section, and the passes in it, in file order."""
    m = PASSES_RE.search(text)
    if not m:
        return text, []
    nxt = re.search(r"^##\s+(?!#)", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    section = text[m.end():end]
    heads = list(PASS_RE.finditer(section))
    passes = []
    for k, h in enumerate(heads):
        body = section[h.end(): heads[k + 1].start() if k + 1 < len(heads) else len(section)]
        findings = []
        for line in body.split("\n"):
            fm = FINDING_RE.match(line.strip())
            if not fm:
                continue
            cells = [c.strip() for c in fm.group(2).split("|")]
            status = (cells[-1] if cells else "").strip("*` ").lower()
            state = ("accepted" if status.startswith("accepted") else
                     "fixed" if status.startswith("fixed") else "open")
            findings.append({"id": fm.group(1), "status": state, "text": line.strip()[:300]})
        cov = body.split("Findings", 1)[0]
        passes.append({"pass": int(h.group(1)), "findings": findings, "coverage": cov,
                       "open": [f["id"] for f in findings if f["status"] == "open"],
                       "clean": all(f["status"] == "accepted" for f in findings)})
    return text[:m.start()] + text[end:], passes


def checklist_items(path: Path) -> int:
    """The review checklist's items (`- [ ] …` lines), numbered CL-1… in file order."""
    if not path.is_file():
        return 0
    return sum(1 for ln in path.read_text(encoding="utf-8").split("\n") if CHECK_ITEM_RE.match(ln))


def unanswered(coverage: str, acs: dict, n_items: int, n_dod: int) -> list[str]:
    """What a pass's coverage does not name: per story every AC and checklist item, and every DoD line."""
    marks = [(m.start(), m.group(1)) for m in STORY_RE.finditer(coverage)]
    segments: dict = {}
    for k, (pos, sid) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(coverage)
        segments[sid] = segments.get(sid, "") + coverage[pos:end]
    missing = []
    for sid, story_acs in acs.items():
        seg = segments.get(sid, "")
        named = named_refs(seg, "AC")
        for ac in story_acs:
            n = ac.rpartition("-")[2]
            if not (n.isdecimal() and int(n) in named):
                missing.append(f"{sid} {ac}")
        cl = named_refs(seg, "CL")
        missing += [f"{sid} CL-{i}" for i in range(1, n_items + 1) if i not in cl]
    dod = named_refs(coverage, "DoD")
    missing += [f"DoD-{i}" for i in range(1, n_dod + 1) if i not in dod]
    return missing


def loop_state(review: Path, acs: dict, n_items: int, n_dod: int) -> dict:
    """Where the loop stands: `review` (a fresh full pass is next), `fix` (the last pass has open
    findings), or `done` (the last pass is complete and found nothing)."""
    text = review.read_text(encoding="utf-8") if review.is_file() else ""
    _, passes = split_passes(text)
    if not passes:
        return {"passes": 0, "open": [], "incomplete": [], "clean": False, "next": "review", "next_pass": 1, "history": []}
    last = passes[-1]
    incomplete = unanswered(last["coverage"], acs, n_items, n_dod)
    clean = last["clean"] and not incomplete
    nxt = "fix" if last["open"] else "done" if clean else "review"
    return {"passes": len(passes), "open": last["open"], "incomplete": incomplete[:30], "clean": clean,
            "next": nxt, "next_pass": last["pass"] + 1,
            "history": [{"pass": p["pass"], "findings": len(p["findings"]), "open": len(p["open"])} for p in passes]}
