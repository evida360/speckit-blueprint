"""Constants, ID patterns and small helpers shared by every module."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

# The extension's folder (the repository, or .specify/extensions/blueprint once installed) and its scripts.
EXT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parents[1]

EXT_ID = "blueprint"


TRACE_FILE = ".blueprint.json"


GENERATED_MARK = "speckit-blueprint:generated"


# Managed blocks: one per blueprint in CLAUDE.md (kind "") and in a constitution the project owns (kind
# "rules:", constitution_mode: append). A project with a `blueprints:` map marks each block with its id
# (`<!-- speckit-blueprint:rules:start id=desk -->`); a single-blueprint config keeps the bare markers.
CLAUDE_START = "<!-- speckit-blueprint:start -->"


CLAUDE_END = "<!-- speckit-blueprint:end -->"


RULES_START = "<!-- speckit-blueprint:rules:start -->"


RULES_END = "<!-- speckit-blueprint:rules:end -->"


# The id of the one blueprint of a config without a `blueprints:` map.
IMPLICIT_ID = "default"


BLUEPRINT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# Placeholder that `specify init` leaves in its template constitution.
TEMPLATE_MARK = "[PROJECT_NAME]"


COMPILED_FILES = ("spec.md", "plan.md", "research.md", "data-model.md", "quickstart.md")


ID_PATTERNS = [
    ("CP", r"CP-[A-Za-z0-9]+-\d+"),
    ("SCR", r"SCR-\d+"),
    ("ENT", r"ENT-[A-Z][A-Za-z0-9]*"),
    ("API", r"API-\d+"),
    ("INT", r"INT-[a-z0-9][a-z0-9-]*[a-z0-9]"),
    ("EC", r"EC-[A-Z0-9]+-\d+"),
    ("F", r"F-\d{2,}"),
    ("FF", r"FF-\d+"),
    ("AD", r"AD-\d+"),
    ("DR", r"DR-(?:E\d{2}-)?\d+"),  # DR-07 (blueprint), DR-E08-1 (taken while building E08)
    ("OQ", r"OQ-\d+"),
    ("R", r"R-\d+"),
    ("S", r"S-\d+"),
    ("M", r"M-\d+(?:\.\d+)?"),
    ("CE", r"CE-\d+"),
    ("G", r"G\d+"),
    ("AP", r"AP\d+"),
    # P50/P75/P90/P95/P99/P999 are percentiles, not personas.
    ("P", r"P(?!(?:50|75|90|95|99|999)\b)\d{1,2}"),
]


ID_ALT = "|".join(p for _, p in ID_PATTERNS)


ID_RE = re.compile(r"(?<![A-Za-z0-9_-])(" + ID_ALT + r")(?![A-Za-z0-9_])")


BOLD_ID_RE = re.compile(r"^\*\*(" + ID_ALT + r")(?![A-Za-z0-9_])")


TYPE_RES = [(t, re.compile(r"^" + p + r"$")) for t, p in ID_PATTERNS]


# "SCR-01 to SCR-06", "CP-SCR20-1 to CP-SCR20-12", "EC-BOT-01 to 04", "CP-SCR03-1…7"
RANGE_RE = re.compile(
    r"((?:CP-[A-Za-z0-9]+|EC-[A-Z0-9]+|SCR|API|F|FF|AD|DR|OQ|R|S|CE)-)(\d+)"
    r"\s*(?:to|through|…|\.\.\.|–)\s*(?:\1)?(\d+)(?![\d.])"
)


WILDCARD_RE = re.compile(r"((?:CP-[A-Za-z0-9]+|EC-[A-Z0-9]+)-)\*")


REFS_HINT = (
    "Define each unresolved ID once, outside the Epics section, in one of three shapes: a heading that starts "
    "with it ('### F-03 — …'), a paragraph that starts with it in bold ('**ENT-Order** …'), or a table row "
    "whose first cell is it ('| CP-SCR04-2 | …'). Put it in its home section (copy in §11, entities in §12.4, "
    "edge cases in §15, critical e2e paths CE-n in §14.3, …). See references/speckit-flow.md §1."
)


EPIC_RE = re.compile(r"^## Epic (\d{2})\s*[—–-]\s*(.+?)\s*$")


STORY_RE = re.compile(r"^### (E(\d{2})-S(\d{2}))\s*[—–-]\s*(.+?)\s*$")


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")


EPIC_FOOTER_RE = re.compile(r"^\*\*(Epic-level tests|Definition of Done|Session handoff checklist)\*\*")


TASK_LINE_RE = re.compile(r"^\s*-\s*\[( |x|X)\]\s*T\d+")


VERIFY_RE = re.compile(r"\[VERIFY-AT-BUILD\]")


def id_type(token: str) -> str | None:
    for t, rx in TYPE_RES:
        if rx.match(token):
            return t
    return None


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def find_root(start: Path) -> Path:
    p = start.resolve()
    for cand in (p, *p.parents):
        if (cand / ".specify").is_dir():
            return cand
    return p


class ConfigError(Exception):
    pass


def read_trace(folder: Path) -> dict | None:
    t = folder / TRACE_FILE
    if not t.is_file():
        return None
    try:
        return json.loads(t.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def active_feature(root: Path) -> str | None:
    fj = root / ".specify" / "feature.json"
    if not fj.is_file():
        return None
    try:
        return json.loads(fj.read_text(encoding="utf-8")).get("feature_directory")
    except (OSError, ValueError):
        return None


def task_counts(tasks: Path) -> tuple[int, int]:
    if not tasks.is_file():
        return (0, 0)
    total = done = 0
    for ln in tasks.read_text(encoding="utf-8").split("\n"):
        m = TASK_LINE_RE.match(ln)
        if m:
            total += 1
            done += m.group(1) in "xX"
    return (total, done)


def review_status(review: Path) -> str | None:
    if not review.is_file():
        return None
    m = re.search(r"Status:\s*\**\s*(DONE|IN PROGRESS)", review.read_text(encoding="utf-8"))
    return m.group(1) if m else "UNKNOWN"


def review_sha(review: Path) -> str | None:
    """review.md's hash, blind to line endings (text mode reads CRLF as LF) and trailing spaces, so the same
    review checked out on another machine, or saved by an editor that trims lines, keeps its hash."""
    if not review.is_file():
        return None
    lines = review.read_text(encoding="utf-8").split("\n")
    return sha("\n".join(ln.rstrip() for ln in lines).strip())
