"""The blueprint document model: headings, epics, stories, ID definitions, rendering helpers."""

from __future__ import annotations

import re
from pathlib import Path

from .core import BOLD_ID_RE, EPIC_FOOTER_RE, EPIC_RE, GENERATED_MARK, HEADING_RE, ID_RE, RANGE_RE, SECTION_NUM_RE, STORY_RE, WILDCARD_RE, sha


class Heading:
    def __init__(self, idx: int, level: int, text: str):
        self.idx, self.level, self.text = idx, level, text
        self.end = idx  # exclusive, set later
        m = SECTION_NUM_RE.match(text)
        self.number = m.group(1) if m else None


class Unit:
    """A span of blueprint text that defines one or more IDs."""

    def __init__(self, kind: str, start: int, end: int, header: tuple[int, int] | None = None):
        self.kind, self.start, self.end, self.header = kind, start, end, header

    def text(self, lines: list[str]) -> str:
        return "\n".join(lines[self.start : self.end]).rstrip()

    def contains(self, other: "Unit") -> bool:
        return self.start <= other.start and other.end <= self.end and self is not other


class Blueprint:
    def __init__(self, path: Path, text: str | None = None):
        """`text` instead of the file's content: migrate reads the blueprint it converts before writing it."""
        self.path = path
        self.raw = path.read_text(encoding="utf-8") if text is None else text
        self.lines = self.raw.split("\n")
        self.sha = sha(self.raw)
        self.headings: list[Heading] = []
        self._scan_headings()
        self.epics = self._scan_epics()
        self.defs: dict[str, list[Unit]] = {}
        self._index_definitions()

    # -- structure

    def _scan_headings(self) -> None:
        fenced = False
        for i, line in enumerate(self.lines):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            m = HEADING_RE.match(line)
            if m:
                self.headings.append(Heading(i, len(m.group(1)), m.group(2)))
        for k, h in enumerate(self.headings):
            h.end = len(self.lines)
            for nxt in self.headings[k + 1 :]:
                if nxt.level <= h.level:
                    h.end = nxt.idx
                    break

    def title(self) -> str:
        for h in self.headings:
            if h.level == 1:
                return re.split(r"\s+[—–-]\s+", h.text)[0].strip()
        return self.path.stem

    def section(self, key: str) -> Heading | None:
        key = key.strip().rstrip(".")
        for h in self.headings:
            if h.number == key:
                return h
        low = key.lower()
        for h in self.headings:
            if h.level <= 3 and (f"({low})" in h.text.lower() or h.text.lower().endswith(low)):
                return h
        return None

    def section_text(self, key: str) -> str | None:
        h = self.section(key)
        return "\n".join(self.lines[h.idx : h.end]).rstrip() if h else None

    def epics_zone(self) -> tuple[int, int]:
        """Line span of the Epics section (§17), where IDs are used rather than defined."""
        starts = [h for h in self.headings if h.level == 2 and EPIC_RE.match(self.lines[h.idx])]
        if not starts:
            return (0, 0)
        first = starts[0].idx
        sec = self.section("Epics")
        if sec and sec.idx < first:
            first = sec.idx
        end = len(self.lines)
        for h in self.headings:
            if h.level == 2 and h.idx > starts[-1].idx and not EPIC_RE.match(self.lines[h.idx]):
                end = h.idx
                break
        return (first, end)

    def _scan_epics(self) -> list[dict]:
        epics = []
        for h in self.headings:
            m = EPIC_RE.match(self.lines[h.idx]) if h.level == 2 else None
            if not m:
                continue
            start, end = h.idx, h.end
            stories = []
            story_heads = [s for s in self.headings if s.level == 3 and start < s.idx < end]
            footer = end
            first_story = next((s.idx for s in story_heads if STORY_RE.match(self.lines[s.idx])), None)
            if first_story is not None:
                for i in range(first_story + 1, end):
                    if EPIC_FOOTER_RE.match(self.lines[i]):
                        footer = i
                        break
            for s in story_heads:
                sm = STORY_RE.match(self.lines[s.idx])
                if not sm:
                    continue
                s_end = min(s.end, footer) if s.idx < footer else s.end
                body = "\n".join(self.lines[s.idx + 1 : s_end])
                stories.append({
                    "id": sm.group(1), "title": sm.group(4), "start": s.idx, "end": s_end,
                    "acs": story_acs(body), "seeds": story_seeds(body),
                })
            header_end = stories[0]["start"] if stories else end
            header_text = "\n".join(self.lines[start + 1 : header_end])
            repo_block = label_block(header_text, "Repositories")
            repos, repo_errors = parse_repo_table(repo_block)
            epics.append(
                {
                    "id": f"E{m.group(1)}",
                    "num": int(m.group(1)),
                    "title": m.group(2),
                    "retired": retired_title(m.group(2)),
                    "start": start,
                    "end": end,
                    "header": (start + 1, header_end),
                    "footer": (footer, end) if stories else (end, end),
                    "stories": stories,
                    "depends": parse_depends(label_block(header_text, "Depends on")),
                    "repos": repos,
                    "repo_errors": repo_errors,
                    "repo_notes": repo_notes(repo_block) if repos else "",
                    "legacy_target_repos": bool(label_block(header_text, "Target repositories")),
                }
            )
        return epics

    def epic(self, key: str) -> dict | None:
        m = re.match(r"^[Ee]?(\d{1,2})$", key.strip())
        if not m:
            return None
        num = int(m.group(1))
        return next((e for e in self.epics if e["num"] == num), None)

    def epic_text(self, epic: dict) -> str:
        return "\n".join(self.lines[epic["start"] : epic["end"]])

    def find_table(self, *columns: str, within: Heading | None = None) -> tuple[list[str], list[list[str]]] | None:
        """The first table outside code fences whose header has every one of `columns` (lowercase, without
        `*` and backticks), within a heading's span when given: its header and the cells of its rows."""
        lo, hi = (within.idx, within.end) if within else (0, len(self.lines))
        fenced = False
        for i in range(lo, hi):
            line = self.lines[i]
            if line.lstrip().startswith("```"):
                fenced = not fenced
            if fenced or not line.strip().startswith("|") or i + 1 >= hi:
                continue
            if not re.match(r"^\|\s*:?-{2,}", self.lines[i + 1].strip()):
                continue
            header = [re.sub(r"[*`]", "", c).strip().lower() for c in table_cells(line)]
            if not all(c in header for c in columns):
                continue
            rows = []
            for row in self.lines[i + 2 : hi]:
                if not row.strip().startswith("|"):
                    break
                rows.append(table_cells(row))
            return header, rows
        return None

    def epic_order(self) -> list[int] | None:
        """Epic numbers in the delivery plan's epic order (§16.1, `| Order | Epic | Goal | … |`): sorted by
        the Order number, rows without one last, table position breaking ties. None when the blueprint has
        no table with both an Order and an Epic column outside code fences."""
        found = self.find_table("order", "epic")
        if found is None:
            return None
        header, table = found
        col_order, col_epic = header.index("order"), header.index("epic")
        rows = []
        for k, cells in enumerate(table):
            if len(cells) <= max(col_order, col_epic):
                continue
            order = re.search(r"\d+", cells[col_order])
            for num in order_cell_epics(cells[col_epic]):
                rows.append((int(order.group(0)) if order else float("inf"), k, num))
        out: list[int] = []
        for _, _, num in sorted(rows):
            if num not in out:
                out.append(num)
        return out

    def delivery_plan(self) -> dict:
        """What the delivery plan (§16) tells people about the epics, read for the dashboard: from the epic
        order table each epic's goal, size and reason; the sprints table (`| Sprint | Epics | Milestone |
        Exit criteria |`); the critical path (`Critical path: E00 → E02 → …`). Parts the blueprint does not
        have are empty. An epic a sprint lists belongs to the last sprint that lists it (`home`), where it
        is finished; an earlier listing carries its note (`E04 (design work starts)`)."""
        plan = next((h for h in self.headings if h.level == 2 and "delivery plan" in h.text.lower()), None)
        epics: dict[str, dict] = {}
        found = self.find_table("order", "epic")
        if found:
            header, rows = found
            col = {key: next((i for i, h in enumerate(header) if h.startswith(key)), None)
                   for key in ("order", "epic", "goal", "size", "why")}
            for cells in rows:
                if len(cells) <= col["epic"]:
                    continue
                cell = lambda key: plain_cell(cells[col[key]]) if col[key] is not None and col[key] < len(cells) else ""  # noqa: E731
                order = re.search(r"\d+", cell("order"))
                for num in order_cell_epics(cells[col["epic"]]):
                    epics.setdefault(f"E{num:02d}", {"order": int(order.group(0)) if order else None, "goal": cell("goal"),
                                                     "size": size_cell(cell("size")), "why": cell("why")})
        sprints: list[dict] = []
        found = self.find_table("sprint", "epics", within=plan) or (self.find_table("sprint", "epics") if plan else None)
        if found:
            header, rows = found
            col = {key: next((i for i, h in enumerate(header) if h.startswith(key)), None)
                   for key in ("sprint", "epics", "milestone", "exit", "goal")}
            for cells in rows:
                if len(cells) <= col["epics"]:
                    continue
                cell = lambda key: plain_cell(cells[col[key]]) if col[key] is not None and col[key] < len(cells) else ""  # noqa: E731
                listed = sprint_cell_epics(cells[col["epics"]])
                if not listed:
                    continue
                sprints.append({"label": cell("sprint") or str(len(sprints) + 1), "epics": [e for e, _ in listed],
                                "notes": {e: n for e, n in listed if n}, "milestone": cell("milestone") or cell("goal"),
                                "exit": cell("exit")})
        home = {}
        for s in sprints:
            for e in s["epics"]:
                home[e] = s["label"]
        return {"epics": epics, "sprints": sprints, "home": home, "critical_path": self.critical_path(plan)}

    def critical_path(self, within: Heading | None = None) -> list[str]:
        """The epics of the first `critical path` line with arrows (`→` or `->`), in order: `Critical path:
        E00 → E02 → E04 approval → E05 (amended A-13). E20-S01 …` → E00, E02, E04, E05. The chain ends where
        its sentence does. The delivery plan's own line wins over one elsewhere in the blueprint."""
        spans = ([(within.idx, within.end)] if within else []) + [(0, len(self.lines))]
        for lo, hi in spans:
            fenced = False
            for line in self.lines[lo:hi]:
                if line.lstrip().startswith("```"):
                    fenced = not fenced
                m = None if fenced else re.search(r"critical path", line, re.I)
                if not m:
                    continue
                parts = re.split(r"\s*(?:→|->|⟶|⇒)\s*", line[m.end():])
                first = re.findall(r"\bE(\d{2})\b", parts[0])
                if len(parts) < 2 or not first:
                    continue
                out = [f"E{first[-1]}"]
                for p in parts[1:]:
                    nxt = re.match(r"[\s*`]*E(\d{2})\b", p)
                    if not nxt:
                        break
                    if f"E{nxt.group(1)}" not in out:
                        out.append(f"E{nxt.group(1)}")
                    if re.search(r"[.;!?](\s|$)|\bE\d{2}\b", p[nxt.end():]):  # the sentence (or the chain) ends here
                        break
                if len(out) > 1:
                    return out
        return []

    # -- definitions

    def _add(self, ident: str, unit: Unit) -> None:
        self.defs.setdefault(ident, []).append(unit)

    def _index_definitions(self) -> None:
        z0, z1 = self.epics_zone()
        in_zone = lambda i: z0 <= i < z1  # noqa: E731
        appendix = next((h.idx for h in self.headings if h.level == 2 and h.text.lower().startswith("appendix")), None)

        # a) headings whose title starts with an ID ("#### DR-01 — …", "### 11.1 SCR-01 Welcome")
        for h in self.headings:
            if in_zone(h.idx) or (appendix is not None and h.idx >= appendix) or h.level < 3:
                continue
            title = SECTION_NUM_RE.sub("", h.text, count=1) if h.number else h.text
            m = ID_RE.match(title.strip())
            if m:
                self._add(m.group(1), Unit("heading", h.idx, h.end))

        # b) bold-led blocks ("**ENT-Order** (`order`, owner …)", "**INT-sms — …**")
        bold_id = BOLD_ID_RE
        heading_idx = {h.idx for h in self.headings}
        i = 0
        fenced = False
        while i < len(self.lines):
            line = self.lines[i]
            if line.lstrip().startswith("```"):
                fenced = not fenced
            if fenced or in_zone(i) or (appendix is not None and i >= appendix):
                i += 1
                continue
            m = bold_id.match(line)
            if m:
                j = i + 1
                while j < len(self.lines) and j not in heading_idx and not bold_id.match(self.lines[j]):
                    j += 1
                self._add(m.group(1), Unit("block", i, j))
            i += 1

        # c) table rows whose first cell is an ID
        header = None
        for i, line in enumerate(self.lines):
            s = line.strip()
            if not s.startswith("|"):
                header = None
                continue
            if i + 1 < len(self.lines) and re.match(r"^\|\s*:?-{2,}", self.lines[i + 1].strip()):
                header = (i, i + 2)
                continue
            if header is None or in_zone(i) or (appendix is not None and i >= appendix):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            first = re.sub(r"[*`]", "", cells[0]).strip() if cells else ""
            # "EC-BOT-10" or "EC-BOT-10 (new)": the cell starts with exactly one ID
            m = ID_RE.match(first)
            if m and (len(first) == m.end() or first[m.end()] in " ("):
                self._add(m.group(1), Unit("row", i, i + 1, header))

    def resolve(self, ids: list[str]) -> list[tuple[str, Unit]]:
        out = []
        for ident in ids:
            for u in self.defs.get(ident, []):
                out.append((ident, u))
        return out


def extract_ids(text: str, known: dict[str, list[Unit]] | None = None) -> list[str]:
    found: list[str] = []
    seen = set()

    def add(x: str) -> None:
        if x not in seen:
            seen.add(x)
            found.append(x)

    for m in RANGE_RE.finditer(text):
        prefix, a, b = m.group(1), m.group(2), m.group(3)
        width = len(a)
        lo, hi = int(a), int(b)
        if 0 <= hi - lo <= 200:
            for n in range(lo, hi + 1):
                add(f"{prefix}{str(n).zfill(width)}")
    if known:
        for m in WILDCARD_RE.finditer(text):
            for k in known:
                if k.startswith(m.group(1)):
                    add(k)
    for m in ID_RE.finditer(text):
        add(m.group(1))
    return found


def render_units(bp: Blueprint, pairs: list[tuple[str, Unit]]) -> str:
    """Render definition units, merging table rows that share a header and dropping nested spans."""
    units: list[Unit] = []
    seen_spans = set()
    for _, u in pairs:
        if (u.start, u.end) not in seen_spans:
            seen_spans.add((u.start, u.end))
            units.append(u)
    units = [u for u in units if not any(o.contains(u) for o in units)]
    units.sort(key=lambda u: u.start)
    chunks: list[str] = []
    last_header = None
    for u in units:
        if u.kind == "row":
            if u.header != last_header:
                hs, he = u.header
                chunks.append("\n".join(bp.lines[hs:he]))
                last_header = u.header
            chunks[-1] += "\n" + u.text(bp.lines)
        else:
            last_header = None
            chunks.append(demote(u.text(bp.lines), 4))
    return "\n\n".join(chunks)


def demote(text: str, min_level: int) -> str:
    """Shift headings so the shallowest becomes `min_level` (keeps nesting, avoids clashes)."""
    levels = [len(m.group(1)) for m in re.finditer(r"^(#{1,6})\s", text, re.M)]
    if not levels:
        return text
    shift = max(0, min_level - min(levels))
    return re.sub(r"^(#{1,6})(\s)", lambda m: "#" * min(6, len(m.group(1)) + shift) + m.group(2), text, flags=re.M)


def strip_label_block(text: str, label: str) -> str:
    """Remove a `**Label**` block (until the next bold label at line start) from a story."""
    out, skip = [], False
    for line in text.split("\n"):
        if re.match(r"^\*\*[A-Z][^*]*\*\*", line):
            skip = line.startswith(f"**{label}**")
        if not skip:
            out.append(line)
    return "\n".join(out)


def label_block(text: str, label: str) -> str:
    out, keep = [], False
    for line in text.split("\n"):
        if re.match(r"^\*\*[A-Z][^*]*\*\*", line):
            keep = line.startswith(f"**{label}**")
        if keep:
            out.append(line)
    return "\n".join(out).strip()


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return "-".join(s.split("-")[:5]) or "epic"


def gen_header(rel_blueprint: str, bp_sha: str, what: str) -> str:
    return (
        f"<!-- {GENERATED_MARK} — {what} compiled from {rel_blueprint} (sha {bp_sha}). "
        "Do not edit by hand: change the blueprint through /speckit-blueprint-amend and recompile. -->"
    )


# --------------------------------------------------------------------------------------------
# Epic fields the scripts read (DESIGN.md "Blueprint conventions the scripts read")

DEP_RE = re.compile(r"^(?:([A-Za-z][A-Za-z0-9-]*):)?E(\d{2})\b")
# after a dependency: a range to another epic (group 1), or another epic joined by and, &, / or +
DEP_MORE_RE = re.compile(r"\s*(?:(–|-|to\b|through\b|…|\.\.\.)|and\b|&|/|\+)\s*(?:([A-Za-z][A-Za-z0-9-]*):)?E(\d{2})\b")
AC_LINE_RE = re.compile(r"^\s*[-*]\s*\**(AC-\d+)\b", re.M)
SEED_LINE_RE = re.compile(r"^\s*[-*]\s*\**((?:[A-Za-z][A-Za-z0-9]*:)?E\d{2}-S\d{2}-T\d+)\b", re.M)
REPO_COLUMNS = {
    "repository": "name", "repo": "name", "order": "order", "role": "role", "role in this epic": "role",
    "base": "base", "base branch": "base", "branch": "branch", "working branch": "branch",
    "contracts": "contracts", "contract": "contracts",
}
# An epic an amendment retires keeps its heading as a marker, its title the word Retired
RETIRED_RE = re.compile(r"^[*_`]*retired[*_`]*(?:$|\s*[(:—–]|\s+-)", re.I)


def retired_title(title: str) -> bool:
    """An epic title that retires the epic: `Retired`, alone or followed by a parenthesis, a colon or a dash
    (`Retired (amended A-01)`, `Retired: replaced by E12`, `Retired — …`). A title that only starts with
    the word (`Retired accounts cleanup`) names an ordinary epic."""
    return bool(RETIRED_RE.match(title.strip()))


EPIC_CELL_RE = re.compile(r"(?:Epic\s+|E)(\d{1,2})\b")
EPIC_CELL_MORE_RE = re.compile(r"\s*(?:,|&|/|\+|and)\s*(?:Epic\s+|E)(\d{1,2})\b")


def table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def order_cell_epics(cell: str) -> list[int]:
    """The epics an epic order row names: the ID its Epic cell starts with (`E20 Workspace branches`,
    `**E20** — …`, `Epic 20 — …`), and any joined to it (`E05, E06 Shell`, `E10 & E11 …`). Epics the title
    mentions later (`E17 Fixes (after E16)`) are not that row's."""
    text = re.sub(r"[*`\[\]]", "", cell).strip()
    out = []
    m = EPIC_CELL_RE.match(text)
    while m:
        out.append(int(m.group(1)))
        text = text[m.end():]
        m = EPIC_CELL_MORE_RE.match(text)
    return out


SIZE_CELL_RE = re.compile(r"^(XXS|XS|S|M|L|XL|XXL)\b", re.I)
SIZE_WORDS = {"small": "S", "medium": "M", "large": "L"}
SPRINT_RANGE_RE = re.compile(r"(?<!:)\bE(\d{2})\s*(?:–|—|-|to\b|through\b)\s*E?(\d{2})\b")


def size_cell(cell: str) -> str | None:
    """An epic order row's size: `M`, `L (two weeks)` → L, `Large` → L; anything else is no size."""
    m = SIZE_CELL_RE.match(cell)
    if m:
        return m.group(1).upper()
    word = re.match(r"[A-Za-z]+", cell)
    return SIZE_WORDS.get(word.group(0).lower()) if word else None


def plain_cell(cell: str) -> str:
    """A table cell as plain text: no bold, code or link markup."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cell)
    return re.sub(r"[*`]", "", text).strip()


def sprint_cell_epics(cell: str) -> list[tuple[str, str]]:
    """The epics a sprints table row lists, each with its note: `E00, E01, E04 (design work starts)` →
    [('E00', ''), ('E01', ''), ('E04', 'design work starts')]; `E04 approval` → [('E04', 'approval')];
    `E02–E05` → every epic in the range. Another blueprint's epic (`one:E18`) is not this one's."""
    out: list[tuple[str, str]] = []
    for part in re.split(r"[,;]", plain_cell(cell)):
        ids: list[str] = []
        for m in SPRINT_RANGE_RE.finditer(part):
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < b - a <= 100:
                ids += [f"E{n:02d}" for n in range(a, b + 1)]
        rest = SPRINT_RANGE_RE.sub(" ", part)
        ids += [f"E{n}" for n in re.findall(r"(?<!:)\bE(\d{2})\b", rest)]
        note = re.sub(r"\s+", " ", re.sub(r"(?:\b[A-Za-z][A-Za-z0-9]*:)?\bE\d{2}\b", " ", rest)).strip(" —–-:;.,")
        if note.startswith("(") and note.endswith(")") and note.count("(") == 1:
            note = note[1:-1].strip()
        note = re.sub(r"^S(\d{2})\b", r"story S\1", note)  # `E20-S01`: one story of the epic
        for e in ids:
            if all(e != x for x, _ in out):
                out.append((e, note))
    return out


def field_text(block: str, label: str) -> str:
    """A `**Label**` block without its label (and a leading dash)."""
    return re.sub(r"^\*\*" + re.escape(label) + r"\*\*\s*[—–:-]?\s*", "", block.strip(), count=1).strip()


def parse_depends(block: str) -> list[tuple[str | None, str]]:
    """`**Depends on** E02, E01 (built); one:E18` → [(None, 'E02'), (None, 'E01'), ('one', 'E18')].
    Each comma- or semicolon-separated part counts only if it starts with an epic ID; prose such as
    "admin APIs in evida-id" or "— (design runs first)" is for people. Epics joined to that ID count too:
    a range (`E00 to E17`, `E02–E07`, `E00-E02`, `E03 through E05`) for each epic in it, and `E20 and E21`
    (or `&`, `/`, `+`) for both; a later ID without a blueprint id takes the first one's."""
    text = field_text(block, "Depends on")
    out: list[tuple[str | None, str]] = []

    def add(dep: tuple[str | None, str]) -> None:
        if dep not in out:
            out.append(dep)

    for part in re.split(r"[,;]", text):
        rest = part.strip().strip("`*")
        m = DEP_RE.match(rest)
        if not m:
            continue
        bid, last = m.group(1), int(m.group(2))
        add((bid, f"E{last:02d}"))
        rest = rest[m.end():]
        m = DEP_MORE_RE.match(rest)
        while m:
            other, num = m.group(2) or bid, int(m.group(3))
            if m.group(1) and other == bid and 0 < num - last <= 100:
                for n in range(last + 1, num + 1):
                    add((bid, f"E{n:02d}"))
            else:
                add((other, f"E{num:02d}"))
            bid, last = other, num
            rest = rest[m.end():]
            m = DEP_MORE_RE.match(rest)
    return out


def parse_repo_table(block: str) -> tuple[list[dict], list[str]]:
    """The `**Repositories**` table → rows {name, order, role, base, branch, contracts}, and errors.
    A field written as prose (schema 1) is an error that `blueprint.py migrate` converts."""
    if not block.strip():
        return [], []
    rows_txt = [ln.strip() for ln in block.split("\n") if ln.strip().startswith("|")]
    if len(rows_txt) < 2 or not re.match(r"^\|\s*:?-{2,}", rows_txt[1]):
        return [], ["the **Repositories** field is not a table (run `blueprint.py migrate`)"]
    header = [REPO_COLUMNS.get(c.strip().strip("*`").lower(), c.strip().lower()) for c in rows_txt[0].strip("|").split("|")]
    if "name" not in header:
        return [], ["the **Repositories** table has no Repository column"]
    rows, errors = [], []
    for n, line in enumerate(rows_txt[2:], 1):
        cells = [c.strip() for c in line.strip("|").split("|")]
        row = dict(zip(header, cells))
        name = re.sub(r"[`*]", "", row.get("name", "")).strip()
        if not name:
            errors.append(f"row {n} of the **Repositories** table names no repository")
            continue
        order = re.sub(r"\D", "", row.get("order", ""))
        clean = lambda v: re.sub(r"^[`*]+|[`*]+$", "", (v or "").strip()).strip()  # noqa: E731
        rows.append({
            "name": name,
            "order": int(order) if order else n,
            "role": (row.get("role") or "").strip(),
            "base": "" if clean(row.get("base")) in ("", "—", "-") else clean(row.get("base")),
            "branch": "" if clean(row.get("branch")) in ("", "—", "-") else clean(row.get("branch")),
            "contracts": "" if (row.get("contracts") or "").strip() in ("", "—", "-") else row["contracts"].strip(),
        })
    rows.sort(key=lambda r: r["order"])
    return rows, errors


def repo_notes(block: str) -> str:
    """Prose under the **Repositories** table (kept for people; compile copies it into plan.md)."""
    text = field_text(block, "Repositories")
    return "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith("|")).strip()


def story_acs(body: str) -> list[str]:
    block = label_block(body, "Acceptance Criteria")
    out = []
    for ac in AC_LINE_RE.findall(block):
        if ac not in out:
            out.append(ac)
    return out


def story_seeds(body: str) -> list[str]:
    return [s.split(":")[-1] for s in SEED_LINE_RE.findall(label_block(body, "Tasks"))]
