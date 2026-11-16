"""Upgrade a project to config schema 2 (DESIGN.md "Migration and versioning").

Dry run by default: prints what it would change. `--apply` writes. It converts:
- the config: `schema: 2`, `effort:` reduced to the guaranteed steps, `effort_check` dropped, and a
  `repositories:` map built from the names the blueprints and tasks use (siblings of the hub);
- each epic's `**Target repositories**` table and prose `**Repositories**` line into one table;
- feature traces without `blueprint_id` (or with `default` in a project that now has several blueprints);
- the epic hash of a compiled epic whose repository fields it converted, when nothing else changed since
  compile, so the epic is not reported as changed (recorded in the trace's `accepted` list);
- the review of each folder compiled before 0.5 (a trace without `acs`), as `review_before_0_5`: a review
  that said DONE keeps counting as done without an evidence entry per AC while review.md is unchanged;
- managed blocks without an id (or `id=default`, same case) in the constitution and CLAUDE.md, and a
  compiled constitution's header without `blueprint-id`.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

from .core import EXT_ID, GENERATED_MARK, IMPLICIT_ID, TRACE_FILE, ConfigError, review_sha, review_status, sha
from .config import EFFORT_STEPS, SCHEMA, bp_config, config_from, load_simple_yaml, parse_simple_yaml
from .model import Blueprint, field_text, label_block
from .blocks import CONSTITUTION, managed_blocks, marker
from .repos import main_root

BOLD_LABEL_RE = re.compile(r"^\*\*[A-Z][^*]*\*\*")
TASK_REPO_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*T\d+\s+(?:\[P\]\s*)?(?:\[(?:US\d+|E\d{2}-S\d{2})\]\s*)?\[([a-z][a-z0-9._-]*)\]", re.M)


def top_block(text: str, key: str) -> tuple[int, int] | None:
    """Line span [start, end) of a top-level YAML key and its indented body."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if re.match(rf"^{re.escape(key)}:", ln):
            j = i + 1
            while j < len(lines) and (lines[j].startswith((" ", "\t")) or not lines[j].strip()):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            return i, j
    return None


def migrate_config(text: str, raw: dict, changes: list) -> str:
    lines = text.split("\n")
    if str(raw.get("schema", "")).strip() != str(SCHEMA):
        span = top_block(text, "schema")
        if span:
            lines[span[0]:span[1]] = [f"schema: {SCHEMA}"]
        else:
            k = 0
            while k < len(lines) and lines[k].startswith("#"):
                k += 1
            lines[k:k] = [f"schema: {SCHEMA}", ""]
        changes.append({"file": "blueprint-config.yml", "change": f"schema: {SCHEMA}"})
    text = "\n".join(lines)
    span = top_block(text, "effort_check")
    if span:
        lines = text.split("\n")
        del lines[span[0]:span[1]]
        text = "\n".join(lines)
        changes.append({"file": "blueprint-config.yml", "change": "removed effort_check (every step now runs at the session's level unless listed under effort:)"})
    eff = raw.get("effort")
    if isinstance(eff, dict) and set(eff) - set(EFFORT_STEPS):
        span = top_block(text, "effort")
        kept = {k: v for k, v in eff.items() if k in EFFORT_STEPS}
        lines = text.split("\n")
        lines[span[0]:span[1]] = ["effort:"] + [f"  {k}: {v}" for k, v in kept.items()] if kept else []
        text = "\n".join(lines)
        changes.append({"file": "blueprint-config.yml", "change": f"effort: kept {', '.join(kept) or 'nothing'}; "
                        f"dropped {', '.join(sorted(set(eff) - set(EFFORT_STEPS)))} (they run at the session's level)"})
    return text


def norm_name(n: str) -> str:
    """`github.com/org/gnata` → gnata; `evida-desk` → evida-desk."""
    n = n.strip().strip("`*").rstrip("/")
    if "/" in n:
        n = n.split("/")[-1]
    return n[:-4] if n.endswith(".git") else n


def target_table(header: str) -> dict:
    """Schema-1 `**Target repositories**` table: name -> role."""
    roles: dict = {}
    for ln in label_block(header, "Target repositories").split("\n"):
        if not ln.strip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 2 and not set(cells[0]) <= set("-: ") and cells[0].lower().strip("*") not in ("repository", "repo"):
            roles[norm_name(cells[0])] = cells[1].replace("|", "/")
    return roles


def order_names(prose: str) -> tuple[list[str], int]:
    """Names in the `Order:` sentence of a schema-1 `**Repositories**` line, and where the sentence ends."""
    m = re.search(r"Order:\s*(.+?)(?:\.\s|\.$|$)", prose)
    if not m:
        return [], 0
    return [norm_name(n) for n in re.findall(r"`([^`]+)`", m.group(1))], m.end()


def repo_names(root: Path, cfg: dict, blueprints: dict) -> list[str]:
    """Repository names the project declares: Target repositories tables, `Order:` sentences, task
    prefixes and `sources`. Other backticked words in the prose are contracts and fields, not names."""
    found: list[str] = []
    prefixes: set = set()

    def add(n: str) -> None:
        n = norm_name(n)
        if n and n not in found:
            found.append(n)

    specs = root / cfg["specs_dir"]
    if specs.is_dir():
        for tasks in specs.glob("*/tasks.md"):
            prefixes.update(TASK_REPO_RE.findall(tasks.read_text(encoding="utf-8")))
    hub = main_root(root)
    for bp in blueprints.values():
        for e in bp.epics:
            header = "\n".join(bp.lines[e["header"][0]:e["header"][1]])
            for n in target_table(header):
                add(n)
            prose = field_text(label_block(header, "Repositories"), "Repositories").replace("\n", " ")
            for n in order_names(prose)[0]:
                if n in prefixes or (hub.parent / n).is_dir() or n == hub.name:
                    add(n)
    for n in sorted(prefixes):
        add(n)
    for bid in cfg["blueprints"]:
        for s in bp_config(cfg, bid)["sources"]:
            add(s)
    return found


def repositories_block(root: Path, names: list[str], warnings: list) -> list[str]:
    hub = main_root(root)
    out = ["", "# Repositories the epics change (added by `blueprint.py migrate`). Paths are relative to the hub.", "repositories:"]
    for n in names:
        if n == hub.name:
            out += [f"  {n}:", "    path: ."]
        elif (hub.parent / n).is_dir():
            out += [f"  {n}:", f"    path: ../{n}"]
        else:
            warnings.append(f"repository `{n}` is named by the blueprints or tasks but has no folder next to the hub; "
                            "add it under `repositories:` by hand if it still exists, or rename it where it is used")
    return out if len(out) > 3 else []


def find_branch(seg: str):
    m = re.search(r"[Ww]orking\s+branch\s+`([^`]+)`", seg) or re.search(r"(?<![Ii]ntegration )\bbranch\s+`([^`]+)`", seg)
    return m.group(1) if m else ""


def convert_repositories(header: str, known: set, hub_name: str = "") -> tuple[list[str] | None, list[str]]:
    """Schema-1 `**Target repositories**` + prose `**Repositories**` → the schema-2 table. Only what the
    prose states for a named repository goes into the table; the prose itself stays under the table as
    notes (compile copies them into plan.md), so nothing is lost when a sentence is ambiguous."""
    notes = []
    roles = target_table(header)
    prose_block = field_text(label_block(header, "Repositories"), "Repositories")
    prose = prose_block.replace("\n", " ")
    listed, order_end = order_names(prose)
    order = [n for n in listed if n in known or n in roles]
    for n in roles:
        if n not in order:
            order.append(n)
    if not order:
        return None, ["no repository names found"]
    marks = [(mm.start(), norm_name(mm.group(1))) for mm in re.finditer(r"`([^`]+)`\s*:", prose) if norm_name(mm.group(1)) in order]
    active = [n for n in listed if n in order and n != hub_name]
    each = re.search(r"[Ee]ach on\s+`([^`]+)`", prose)
    each_base = re.search(r"based on\s+`([^`]+)`", prose)
    rows = []
    for k, name in enumerate(order, 1):
        segs = [prose[pos:(marks[i + 1][0] if i + 1 < len(marks) else len(prose))] for i, (pos, n) in enumerate(marks) if n == name]
        seg = " ".join(segs) if segs else (prose[order_end:] if not marks and len(active) == 1 and name == active[0] else "")
        base = re.search(r"\b[Bb]ase\s+`([^`]+)`", seg)
        base = base.group(1) if base else (each_base.group(1) if each_base and name in listed else "")
        branch = find_branch(seg) or (each.group(1) if each and name in listed else "")
        contracts = re.search(r"[Cc]ontracts?[^:]*:\s*(.+?)(?=\s*`[^`]+`\s*:|$)", seg)
        contracts_txt = (contracts.group(1).strip().rstrip(".") if contracts else "").replace("|", "/")
        if contracts_txt.lower().startswith("none"):
            rest = contracts_txt[4:].strip(" .()")
            contracts_txt = f"none ({rest})" if rest else ""
        if name != hub_name and name in listed and not (base or branch):
            notes.append(f"`{name}`: base and branch not stated for it alone; see the notes under the table")
        rows.append(f"| `{name}` | {k} | {roles.get(name, '')} | {base} | {branch} | {contracts_txt} |")
    table = ["**Repositories**", "", "| Repository | Order | Role | Base | Branch | Contracts |", "|---|---|---|---|---|---|", *rows, ""]
    if prose_block.strip():
        table += [f"Notes (schema 1, kept by `blueprint.py migrate`): {prose.strip()}", ""]
    return table, notes


def migrate_blueprint(bp: Blueprint, known: set, changes: list, warnings: list) -> str | None:
    lines = bp.lines[:]
    touched = False
    for e in reversed(bp.epics):
        if not (e["legacy_target_repos"] or e["repo_errors"]):
            continue
        h0, h1 = e["header"]
        header = "\n".join(lines[h0:h1])
        table, notes = convert_repositories(header, known, main_root(Path(bp.path).parent).name)
        if table is None:
            warnings.append(f"{bp.path.name} {e['id']}: could not convert the repository fields ({'; '.join(notes)}); edit by hand")
            continue
        out, skip, placed = [], False, False
        for ln in lines[h0:h1]:
            if BOLD_LABEL_RE.match(ln):
                label = re.match(r"^\*\*([^*]+)\*\*", ln).group(1)
                skip = label in ("Repositories", "Target repositories")
                if skip and not placed:
                    out += table
                    placed = True
            if not skip:
                out.append(ln)
        lines[h0:h1] = out
        touched = True
        changes.append({"file": str(bp.path), "change": f"{e['id']}: repository fields → table"
                        + (f" ({'; '.join(notes)})" if notes else "")})
    return "\n".join(lines) if touched else None


def owner_by_path(cfg: dict, text: str) -> str | None:
    for bid in cfg["blueprints"]:
        path = bp_config(cfg, bid)["blueprint_path"]
        if path in text:
            return bid
    return None


def migrate_blocks(root: Path, cfg: dict, changes: list) -> dict:
    """Give every managed block its blueprint id; returns path -> new text for changed files."""
    out = {}
    default = cfg.get("default_blueprint") or next(iter(cfg["blueprints"]))
    for rel, kind in ((CONSTITUTION, "rules:"), (Path("CLAUDE.md"), "")):
        p = root / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        new = text
        for bid_found, s, e in reversed(managed_blocks(text, kind)):
            if bid_found is not None and not (bid_found == IMPLICIT_ID and not cfg["implicit"]):
                continue
            block = new[s:e]
            owner = owner_by_path(cfg, block) or default
            block = block.replace(marker(kind, "start", bid_found), marker(kind, "start", owner), 1)
            block = block.replace(marker(kind, "end", bid_found), marker(kind, "end", owner), 1)
            new = new[:s] + block + new[e:]
            changes.append({"file": rel.as_posix(), "change": f"managed block marked id={owner}"})
        first = new.split("\n", 1)[0]
        if kind == "rules:" and GENERATED_MARK in first and "blueprint-id" not in first:
            owner = owner_by_path(cfg, first) or default
            new = re.sub(r"\((sha [0-9a-f]+)", rf"(blueprint-id {owner}, \1", new, count=1)
            changes.append({"file": rel.as_posix(), "change": f"compiled constitution header names blueprint-id {owner}"})
        if new != text:
            out[p] = new
    return out


def move_epic_sha(trace: dict, old: Blueprint, new: Blueprint, rel: str, today: str, changes: list, warnings: list) -> None:
    """The conversion rewrites an epic's repository fields, not what the epic was built from. When the
    trace matches the epic's text before the conversion, its hash moves to the converted text, recorded in
    `accepted` as `blueprint.py accept` would. A change made before the migration stays reported."""
    eid = str(trace.get("epic") or "")
    before, after = old.epic(eid[1:]), new.epic(eid[1:])
    if before is None or after is None:
        return
    old_sha, new_sha = sha(old.epic_text(before)), sha(new.epic_text(after))
    if old_sha == new_sha:
        return
    if trace.get("epic_sha") != old_sha:
        warnings.append(f"{rel}: {eid} had changed since compile before the migration; review the change, then "
                        "`blueprint.py accept` or recompile")
        return
    trace["epic_sha"] = new_sha
    trace.setdefault("accepted", []).append({
        "date": today, "blueprint_sha": new.sha, "changed": [eid],
        "note": "blueprint.py migrate: repository fields converted to the schema-2 table; the epic stays as built",
    })
    changes.append({"file": rel, "change": f"epic_sha: {eid} as converted (recorded in accepted)"})


def record_review(folder: Path, trace: dict, rel: str, today: str, changes: list) -> None:
    """What the review of a folder compiled before 0.5 says now. 0.4 counted any DONE review; 0.5 wants an
    evidence entry for every AC. A DONE review recorded here keeps counting as done while review.md is
    unchanged; any other review is held to 0.5's rule once it says DONE."""
    review = folder / "review.md"
    status = review_status(review)
    rec: dict = {"status": status}
    if status == "DONE":
        rec["sha"] = review_sha(review)
    rec["date"] = today
    trace["review_before_0_5"] = rec
    changes.append({"file": rel, "change": "review_before_0_5: DONE, counted as done while review.md is unchanged"
                    if status == "DONE" else
                    f"review_before_0_5: {status or 'no review.md'}; a DONE review written later needs evidence for every AC"})


def migrate_traces(root: Path, cfg: dict, old: dict, new: dict, changes: list, warnings: list) -> dict:
    """Each feature folder's trace: its blueprint id, the epic hash the conversion changed (`old` and `new`:
    blueprint id -> the blueprint before and after it), and the review of a folder compiled before 0.5."""
    out = {}
    default = cfg.get("default_blueprint") or next(iter(cfg["blueprints"]))
    specs = root / cfg["specs_dir"]
    if not specs.is_dir():
        return out
    today = _dt.date.today().isoformat()
    for t in sorted(specs.glob(f"*/{TRACE_FILE}")):
        try:
            trace = json.loads(t.read_text(encoding="utf-8"))
        except ValueError:
            continue
        rel = t.relative_to(root).as_posix()
        n = len(changes)
        bid = trace.get("blueprint_id")
        if not bid or (bid == IMPLICIT_ID and not cfg["implicit"]):
            bid = owner_by_path(cfg, str(trace.get("blueprint") or "")) or default
            trace["blueprint_id"] = bid
            changes.append({"file": rel, "change": f"blueprint_id: {bid}"})
        if bid in new:
            move_epic_sha(trace, old[bid], new[bid], rel, today, changes, warnings)
        if "acs" not in trace and "review_before_0_5" not in trace:  # compile writes `acs` since 0.5
            record_review(t.parent, trace, rel, today, changes)
        if len(changes) > n:
            out[t] = json.dumps(trace, indent=2, ensure_ascii=False) + "\n"
    return out


def migrate(root: Path, apply: bool = False) -> int:
    ext_dir = root / ".specify" / "extensions" / EXT_ID
    cfg_path = ext_dir / f"{EXT_ID}-config.yml"
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.is_file() else ""
    raw = parse_simple_yaml(text)
    changes: list = []
    warnings: list = []
    new_cfg_text = migrate_config(text, raw, changes) if text else text
    try:
        cfg = config_from([parse_simple_yaml(new_cfg_text), load_simple_yaml(ext_dir / f"{EXT_ID}-config.local.yml")])
    except ConfigError as exc:
        print(json.dumps({"applied": False, "error": str(exc)}, indent=2))
        return 1
    blueprints = {bid: Blueprint(root / bp_config(cfg, bid)["blueprint_path"])
                  for bid in cfg["blueprints"] if (root / bp_config(cfg, bid)["blueprint_path"]).is_file()}
    writes: dict = {}
    names = repo_names(root, cfg, blueprints)
    if not raw.get("repositories") and len(names) > 1:
        block = repositories_block(root, names, warnings)
        if block:
            new_cfg_text = new_cfg_text.rstrip("\n") + "\n" + "\n".join(block) + "\n"
            changes.append({"file": "blueprint-config.yml", "change": "repositories: " + ", ".join(
                n for n in names if n not in " ".join(warnings))})
    if new_cfg_text != text and text:
        writes[cfg_path] = new_cfg_text
    known = set(names)
    converted = {}
    for bid, bp in blueprints.items():
        new = migrate_blueprint(bp, known, changes, warnings)
        if new is not None:
            writes[bp.path] = new
            converted[bid] = Blueprint(bp.path, new)
    writes.update(migrate_traces(root, cfg, blueprints, converted, changes, warnings))
    writes.update(migrate_blocks(root, cfg, changes))
    if apply:
        for p, content in writes.items():
            p.write_text(content, encoding="utf-8")
    flag = "" if cfg["implicit"] else " --blueprint <id>"
    if not writes:
        nxt = "Nothing to migrate."
    elif not apply:
        nxt = "Run `blueprint.py migrate --apply` to write these changes."
    elif all(p.name == TRACE_FILE for p in writes):  # a project migrated before: its blocks are as they were
        nxt = "Review the diff, then run `blueprint.py status`."
    else:
        nxt = (f"Review the diff, then run `blueprint.py bootstrap{flag}` for each blueprint so the managed blocks use "
               "this version's wording, and `blueprint.py status`.")
    print(json.dumps({"applied": apply, "changes": changes, "warnings": warnings, "next": nxt}, indent=2, ensure_ascii=False))
    return 0
