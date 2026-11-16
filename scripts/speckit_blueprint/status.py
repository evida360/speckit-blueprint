"""Project state per epic and the next step (DESIGN.md "Lifecycle and next step").

Epic states: not-compiled, compiled, stale, tasked, in-progress, implemented, done. Overlays: `blocked`
(a dependency is not done in this checkout), `elsewhere` (another worktree of the hub builds it) and
`started` (not compiled here, but its hub branch exists here or on a remote: in progress on that branch,
or merged into the base without this checkout having it).
"""

from __future__ import annotations

import re
from pathlib import Path

from .core import IMPLICIT_ID, TRACE_FILE, active_feature, read_trace, review_sha, review_status, task_counts
from .config import COMMAND_STEP, bp_config, resolve_id
from .model import Blueprint
from .blocks import CONSTITUTION, constitution_state, managed_blocks
from .board import elsewhere, epic_key, load as load_board, refresh
from .compiler import find_feature_dir, stale_units, started_folders
from .bootstrap import rules_body
from .repos import registry, rel_or_abs

SCRIPT = "python3 .specify/extensions/blueprint/scripts/blueprint.py"

STORY_ID_RE = re.compile(r"(?:[A-Za-z][A-Za-z0-9]*:)?E\d{2}-S\d{2}\b")


# An AC in review evidence: "AC-3"; every AC of a range: "AC-1 to AC-5", "AC-1 to 5", "AC-1 through AC-5",
# "AC-1–AC-4", "AC-1–4", "AC-1…3", "AC-1...3"; each AC of a pair: "AC-5/6", "AC-5/AC-6", "AC-1/2/3".
# A number ends where its digits do: AC-12 never names AC-1, nor AC-1 AC-12.
AC_REF_RE = re.compile(r"(?<![\w-])AC-(\d+)(?!\d)")
AC_RANGE_RE = re.compile(r"(?<![\w-])AC-(\d+)\s*(?:to|through|…|\.\.\.|–)\s*(?:AC-)?(\d+)(?!\w|\.\d)")
AC_PAIR_RE = re.compile(r"(?<![\w-])AC-\d+(?:/(?:AC-)?\d+)+(?!\w|\.\d)")


def named_acs(text: str) -> set[int]:
    """The numbers of the ACs that evidence text names, one by one, in ranges and in pairs."""
    out = {int(n) for n in AC_REF_RE.findall(text)}
    for a, b in AC_RANGE_RE.findall(text):
        if 0 <= int(b) - int(a) <= 200:
            out.update(range(int(a), int(b) + 1))
    for m in AC_PAIR_RE.finditer(text):
        out.update(int(n) for n in re.findall(r"\d+", m.group(0)))
    return out


def review_check(review: Path, acs: dict) -> tuple[str | None, list[str]]:
    """review.md's status, and the ACs a DONE review gives no evidence for. Evidence for a story is the
    text from each mention of its ID to the next story ID; it must name every AC of the story, as `AC-n`
    or within a range or pair (`AC-1 to AC-5`, `AC-1–4`, `AC-1…3`, `AC-5/6`)."""
    status = review_status(review)
    if status != "DONE" or not acs:
        return status, []
    text = review.read_text(encoding="utf-8")
    marks = [(m.start(), m.group(0).split(":")[-1]) for m in STORY_ID_RE.finditer(text)]
    segments: dict = {}
    for k, (pos, sid) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(text)
        segments[sid] = segments.get(sid, "") + text[pos:end]
    missing = []
    for sid, story_acs in acs.items():
        named = named_acs(segments.get(sid, ""))
        for ac in story_acs:
            n = ac.rpartition("-")[2]
            if not (n.isdecimal() and int(n) in named):
                missing.append(f"{sid} {ac}")
    return status, missing


def before_0_5(d: Path, trace: dict) -> bool:
    """review.md is the DONE review `blueprint.py migrate` recorded in a folder compiled before 0.5, unchanged
    since: it counts as done without an evidence entry per AC, as it did under 0.4."""
    rec = trace.get("review_before_0_5") or {}
    return rec.get("status") == "DONE" and bool(rec.get("sha")) and rec["sha"] == review_sha(d / "review.md")


def epic_acs(epic: dict) -> dict:
    return {st["id"]: st["acs"] for st in epic["stories"] if st["acs"]}


def epic_row(root: Path, cfg: dict, bp: Blueprint, e: dict) -> dict:
    """One epic's state in this checkout."""
    d = find_feature_dir(root, cfg, e["id"])
    row = {"epic": e["id"], "title": e["title"], "stories": len(e["stories"]),
           "acs": sum(len(s["acs"]) for s in e["stories"]),
           "depends": [f"{b}:{x}" if b else x for b, x in e["depends"]]}
    if e["repos"]:
        row["repositories"] = [r["name"] for r in e["repos"]]
    if d is None:
        row["state"] = "not-compiled"
        return row
    trace = read_trace(d) or {}
    row["feature_directory"] = d.relative_to(root).as_posix()
    if trace.get("branch"):
        row["branch"] = trace["branch"]
    total, done = task_counts(d / "tasks.md")
    rv, missing = review_check(d / "review.md", trace.get("acs") or epic_acs(e))
    changed = stale_units(bp, trace)
    row.update(tasks_total=total, tasks_done=done, review=rv)
    if rv == "DONE" and before_0_5(d, trace):
        row["review_before_0_5"] = True
        missing = []
    if rv == "DONE" and not missing:
        row["state"] = "done"
    elif total and done == total:
        row["state"] = "implemented"
    elif done:
        row["state"] = "in-progress"
    elif total:
        row["state"] = "tasked"
    else:
        row["state"] = "compiled"
    if missing:
        row["review_missing"] = missing[:30]
    if changed and row["state"] in ("compiled", "tasked"):
        row["state"] = "stale"
    if changed:
        row["changed_since_compile"] = changed[:20]
    return row


def missing_repositories(root: Path, cfg: dict, epic: dict) -> list[dict]:
    """The epic's code repositories that are not on this machine (`workspace setup` clones them)."""
    reg = registry(cfg, root)
    out = []
    for r in epic.get("repos") or []:
        e = reg.get(r["name"])
        if e and not e["hub"] and not Path(e["main_path"]).is_dir():
            out.append({"name": r["name"], "path": rel_or_abs(e["main_path"], root), "remote": bool(e.get("remote"))})
    return out


def started_note(ref: str, title: str, s: dict) -> str:
    """The brief's line for an epic in progress on a branch that is not this checkout's."""
    if s.get("merged_into"):
        return f"Merged: {ref} {title} is in {s['merged_into']} but not in this checkout; bring it in (pull or merge)."
    if s.get("local"):
        return (f"On a branch: {ref} {title} is in progress on {s['branch']}, not merged here; continue it on that "
                f"branch, or in its own worktree: `blueprint.py workspace {ref} --create`.")
    return (f"Pushed: {ref} {title} is on {s['remote']}, started on another machine or by someone else; continue it "
            f"here with `blueprint.py workspace {ref} --create`.")


def dependency_done(root: Path, cfg: dict, dep: str, rows: dict) -> bool:
    """A dependency is done when its feature folder in this checkout has a valid DONE review."""
    other, sep, eid = dep.partition(":")
    if not sep:
        return rows.get(other, {}).get("state") == "done"
    try:
        ocfg = bp_config(cfg, resolve_id(cfg, other))
    except Exception:  # noqa: BLE001 - an unknown blueprint never counts as done
        return False
    d = find_feature_dir(root, ocfg, eid)
    if d is None:
        return False
    trace = read_trace(d) or {}
    rv, missing = review_check(d / "review.md", trace.get("acs") or {})
    return rv == "DONE" and (not missing or before_0_5(d, trace))


def own_dependencies(cfg: dict, epic: dict) -> list[str]:
    """The epic's dependencies in its own blueprint: `E08`, or `desk:E08` written in desk's blueprint."""
    out = []
    for other, eid in epic["depends"]:
        if other:
            try:
                if resolve_id(cfg, other) != cfg["id"]:
                    continue
            except Exception:  # noqa: BLE001 - another blueprint's epic, or an unknown one
                continue
        out.append(eid)
    return out


def delivery_order(cfg: dict, bp: Blueprint, rows: dict) -> dict:
    """The order `next` takes the epics in: the delivery plan's epic order table when the blueprint has one
    (the epics it leaves out follow, in blueprint order), otherwise blueprint order. Either way, an epic's
    dependencies in this blueprint that are not done come right before it, so an epic added at the end
    that an earlier one waits for is built first."""
    table = bp.epic_order()
    ids = {e["num"]: e["id"] for e in bp.epics}
    base = [ids[n] for n in table or [] if n in ids]
    base += [e["id"] for e in bp.epics if e["id"] not in base]
    deps = {e["id"]: [d for d in own_dependencies(cfg, e) if d in rows] for e in bp.epics}
    out: list[str] = []
    seen: set = set()

    def visit(eid: str) -> None:
        if eid in seen:
            return
        seen.add(eid)
        if rows[eid]["state"] != "done":
            for dep in sorted(deps[eid], key=base.index):
                if rows[dep]["state"] != "done":
                    visit(dep)
        out.append(eid)

    for eid in base:
        visit(eid)
    return {"source": "table" if table else "blueprint", "epics": out}


def choose(order: list[str], rows: dict) -> tuple[dict | None, list[str]]:
    """The epic `next` names, and why each other epic that is not done waits. An epic compiled in this
    checkout comes first, so one in progress is finished before another starts; then the rest in delivery
    order. Epics built elsewhere, started on another branch or blocked are skipped."""
    ready, waiting = [], []
    for eid in order:
        row = rows[eid]
        if row["state"] == "done":
            continue
        if row.get("elsewhere"):
            waiting.append(f"{eid} is being built in {row['elsewhere']['worktree']}")
        elif row.get("started"):
            p = row["started"]
            waiting.append(f"{eid} is merged into {p['merged_into']} but not in this checkout" if p.get("merged_into")
                           else f"{eid} is in progress on {p['branch'] if p.get('local') else p['remote']}")
        elif row.get("blocked_by"):
            waiting.append(f"{eid} waits for {', '.join(row['blocked_by'])}")
        else:
            ready.append(row)
    return next((r for r in ready if r["state"] != "not-compiled"), ready[0] if ready else None), waiting


def migration_reasons(root: Path, cfg: dict, bp: Blueprint | None) -> list[str]:
    """Schema-1 leftovers that `blueprint.py migrate` converts."""
    out = []
    multi = not cfg["implicit"]

    def legacy_id(bid) -> bool:
        return not bid or (multi and bid == IMPLICIT_ID)

    specs = root / cfg["specs_dir"]
    if specs.is_dir():
        traces = {d.name: read_trace(d) for d in sorted(specs.iterdir()) if (d / TRACE_FILE).is_file()}
        legacy = [n for n, t in traces.items() if legacy_id((t or {}).get("blueprint_id"))]
        if legacy:
            out.append(f"{len(legacy)} feature folder(s) without their blueprint's id ({', '.join(legacy[:3])}…)")
        # compile writes `acs` since 0.5; migrate records the review of every folder compiled before
        unrecorded = [n for n, t in traces.items() if t is not None and "acs" not in t and "review_before_0_5" not in t
                      and (t.get("blueprint_id") or cfg["id"]) == cfg["id"]]
        if unrecorded:
            out.append(f"{len(unrecorded)} feature folder(s) compiled before 0.5 without their review recorded "
                       f"({', '.join(unrecorded[:3])}…)")
    for rel in (CONSTITUTION, Path("CLAUDE.md")):
        p = root / rel
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            n = sum(1 for kind in ("rules:", "") for i, _, _ in managed_blocks(text, kind) if legacy_id(i))
            if n:
                out.append(f"{n} managed block(s) without their blueprint's id in {rel.as_posix()}")
    if bp is not None:
        prose = [e["id"] for e in bp.epics if e["repo_errors"] or (e["legacy_target_repos"] and not e["repos"])]
        if prose:
            out.append(f"{len(prose)} epic(s) with schema-1 repository fields ({', '.join(prose[:5])}…)")
    return out


def project_status(root: Path, cfg: dict, board: dict | None = None, write_board: bool = False) -> dict:
    """Status of one blueprint (`cfg` from bp_config)."""
    bp_path = root / cfg["blueprint_path"]
    st: dict = {"root": str(root), "blueprint_id": cfg["id"], "prefix": cfg["prefix"], "blueprint": cfg["blueprint_path"],
                "decisions": cfg["decisions_path"], "review_checklist": cfg["review_checklist_path"]}
    if cfg["sources"]:
        st["sources"] = cfg["sources"]
    st["effort"] = dict(cfg["effort"])
    st["effort_model"] = cfg.get("effort_model", "inherit")
    st["active_feature"] = active_feature(root)
    board = board if board is not None else load_board(root)

    def nxt(cmd: str, why: str, epic: str | None = None, new_session: bool = False, run: str | None = None) -> dict:
        d = {"command": cmd, "why": why, "new_session": new_session}
        step = COMMAND_STEP.get(cmd)
        if step and step in cfg["effort"]:
            d["effort"] = cfg["effort"][step]
        if epic:
            d["epic"] = epic
        if run:
            d["run"] = run
        return d

    flag = f" --blueprint {cfg['id']}" if cfg.get("multi") else ""
    if not bp_path.is_file():
        st.update(state="no-blueprint", next=nxt("speckit-blueprint-discover", "No blueprint yet: start product discovery."))
        return st
    bp = Blueprint(bp_path)
    st["product"] = bp.title()
    body, _, content_sha = rules_body(bp, cfg)
    st["constitution"] = constitution_state(root, cfg, content_sha)
    reasons = migration_reasons(root, cfg, bp)
    if reasons:
        st["migration_needed"] = reasons
    rows = {e["id"]: epic_row(root, cfg, bp, e) for e in bp.epics}
    others = elsewhere(board, root)
    started = started_folders(root, cfg) if any(r["state"] == "not-compiled" for r in rows.values()) else {}
    for e in bp.epics:
        row = rows[e["id"]]
        held = others.get(epic_key(cfg["id"], e["id"]))
        if held and row["state"] != "done":
            row["elsewhere"] = {k: held.get(k) for k in ("worktree", "branch", "state", "tasks_done", "tasks_total", "updated")}
        elif row["state"] == "not-compiled" and e["id"] in started:
            row["started"] = started[e["id"]]
        blocked = [dep for dep in row["depends"] if not dependency_done(root, cfg, dep, rows)]
        if blocked and row["state"] != "done":
            row["blocked_by"] = blocked
    st["epics"] = list(rows.values())
    st["epic_order"] = delivery_order(cfg, bp, rows)
    st["state"] = "building"
    if write_board:
        mine = {epic_key(cfg["id"], r["epic"]): {k: r.get(k) for k in ("state", "tasks_done", "tasks_total", "review")}
                for r in rows.values() if r.get("feature_directory")}
        refresh(root, mine)

    if reasons:
        st["next"] = nxt("migrate", "The project has schema-1 leftovers: " + "; ".join(reasons) + ".",
                         run="python3 .specify/extensions/blueprint/scripts/blueprint.py migrate")
        return st
    if st["constitution"] not in ("compiled", "appended"):
        why = ("The blueprint sections the constitution is built from changed; rebuild its rules."
               if st["constitution"] == "outdated" else
               "Blueprint exists but the constitution and project files are not compiled from it yet.")
        st["next"] = nxt("bootstrap", why, run=f"python3 .specify/extensions/blueprint/scripts/blueprint.py bootstrap{flag}")
        return st
    row, waiting = choose(st["epic_order"]["epics"], rows)
    if row is not None:
        s = row["state"]
        ref = f"{cfg['id']}:{row['epic']}" if cfg.get("multi") else row["epic"]
        if s == "stale":
            st["next"] = nxt("speckit-blueprint-compile", "Blueprint changed after this epic was compiled; recompile it.", row["epic"])
        elif s == "not-compiled":
            after = [r["epic"] for r in rows.values() if row["epic"] in r.get("blocked_by", [])]
            why = f"Next epic: {row['epic']} — {row['title']}."
            if after:
                names = ", ".join(after[:10]) + (f" and {len(after) - 10} more" if len(after) > 10 else "")
                why += f" {names} wait{'s' if len(after) == 1 else ''} for it."
            st["next"] = nxt("speckit-blueprint-compile", why, row["epic"])
        elif s == "compiled":
            st["next"] = nxt("speckit-tasks", "Feature compiled; generate tasks.md, then run /speckit-analyze.", row["epic"])
        elif s in ("tasked", "in-progress"):
            st["next"] = nxt(
                "speckit-implement",
                f"{row['tasks_done']}/{row['tasks_total']} tasks done; continue implementation (start a fresh session if the current one is long).",
                row["epic"],
                new_session=(s == "tasked"),
            )
        elif s == "implemented":
            why = ("All tasks are checked; run the review." if not row.get("review_missing") else
                   f"review.md says DONE but has no evidence for {', '.join(row['review_missing'][:5])}; finish the review.")
            st["next"] = nxt("speckit-blueprint-review", why, row["epic"])
        if row.get("feature_directory") and row["feature_directory"] != st["active_feature"] and st["next"]["command"] != "speckit-blueprint-compile":
            st["next"]["activate"] = row["feature_directory"]
        missing = missing_repositories(root, cfg, next(e for e in bp.epics if e["id"] == row["epic"]))
        if missing:
            st["next"]["missing_repositories"] = missing
            st["next"]["setup"] = f"{SCRIPT} workspace setup {ref}"
        return st
    if waiting:
        st["state"] = "waiting"
        st["next"] = nxt("wait", "Every remaining epic is blocked or in progress elsewhere: " + "; ".join(waiting) + ".")
        return st
    st["state"] = "all-done"
    st["next"] = nxt("speckit-blueprint-discover", "Every epic in the blueprint is done. New scope starts with discover in incremental mode.")
    return st


def summary(st: dict) -> dict:
    epics = st.get("epics", [])
    out = {k: st[k] for k in ("blueprint_id", "prefix", "product", "blueprint", "decisions", "review_checklist", "sources", "state", "constitution", "migration_needed") if k in st}
    out.update(epics_done=sum(1 for e in epics if e["state"] == "done"), epics_total=len(epics), next=st.get("next"))
    return out


def full_status(root: Path, cfg: dict, bid: str, write_board: bool = False) -> dict:
    """The status of blueprint `bid` (the active feature's, unless chosen), plus a summary of every
    configured blueprint under `blueprints`."""
    board = load_board(root)
    st = project_status(root, bp_config(cfg, bid), board, write_board)
    rows = []
    for other in cfg["blueprints"]:
        rows.append(summary(st if other == bid else project_status(root, bp_config(cfg, other), board, write_board)))
    st["blueprints"] = rows
    return st


def epic_ref(st: dict, epic: str, multi: bool) -> str:
    return f"{st['blueprint_id']}:{epic}" if multi else epic


def next_label(n: dict, st: dict, multi: bool) -> str:
    cmd = n.get("command", "")
    if cmd in ("bootstrap", "migrate"):
        return f"`blueprint.py {cmd}`"
    if cmd == "wait":
        return "wait"
    tgt = f" {epic_ref(st, n['epic'], multi)}" if n.get("epic") and cmd == "speckit-blueprint-compile" else ""
    return f"/{cmd}{tgt}"


def brief(st: dict) -> str:
    rows = st.get("blueprints") or []
    multi = len(rows) > 1
    label = f" ({st['blueprint_id']})" if multi else ""
    if st.get("state") == "no-blueprint":
        lines = [f"[speckit-blueprint] No blueprint{label} at {st['blueprint']}. Next: /speckit-blueprint-discover with the idea."]
    else:
        done = sum(1 for e in st.get("epics", []) if e["state"] == "done")
        total = len(st.get("epics", []))
        lines = [f"[speckit-blueprint] {st.get('product')}{label}: {done}/{total} epics done. Blueprint: {st['blueprint']}."]
        if st.get("migration_needed"):
            lines.append("Migration needed: " + "; ".join(st["migration_needed"]) + ". Run `blueprint.py migrate`.")
        building = [e for e in st.get("epics", []) if e.get("elsewhere")]
        for e in building:
            w = e["elsewhere"]
            lines.append(f"Elsewhere: {epic_ref(st, e['epic'], multi)} {e['title']} in {w.get('worktree')} ({w.get('state') or 'claimed'}).")
        for e in st.get("epics", []):
            if e.get("started"):
                lines.append(started_note(epic_ref(st, e["epic"], multi), e["title"], e["started"]))
        by_epic = {e["epic"]: e for e in st.get("epics", [])}
        cur = choose((st.get("epic_order") or {}).get("epics") or list(by_epic), by_epic)[0]
        if cur:
            extra = f", tasks {cur['tasks_done']}/{cur['tasks_total']}" if cur.get("tasks_total") else ""
            lines.append(f"Current: {epic_ref(st, cur['epic'], multi)} {cur['title']} — {cur['state']}{extra}.")
        n = st.get("next") or {}
        if n:
            hint = f" Runs at effort {n['effort']} in a subagent." if n.get("effort") else ""
            lines.append(f"Next: {next_label(n, st, multi)} — {n['why']}{hint}")
        if n.get("missing_repositories"):
            names = ", ".join(f"{m['name']} ({m['path']})" for m in n["missing_repositories"])
            lines.append(f"Not on this machine: {names}. `blueprint.py workspace setup` clones the repositories that "
                         "have a `remote` in the config.")
        stale = [e["epic"] for e in st.get("epics", []) if e.get("changed_since_compile")]
        if stale:
            lines.append(f"Blueprint changed since compile for: {', '.join(stale)}.")
    for o in rows:
        if o["blueprint_id"] == st["blueprint_id"]:
            continue
        if o["state"] == "no-blueprint":
            lines.append(f"Also: blueprint {o['blueprint_id']} — none yet at {o['blueprint']}.")
        else:
            n = o.get("next") or {}
            lines.append(f"Also: {o.get('product')} ({o['blueprint_id']}) — {o['epics_done']}/{o['epics_total']} epics done; next {next_label(n, o, True) if n else '—'}.")
    if multi:
        lines.append("Several blueprints: name one with `<id>:E01` or `--blueprint <id>`; the default is the active feature's.")
    lines.append("Workflow rules: use the speckit-blueprint-flow skill; never hand-edit compiled spec files.")
    return "\n".join(lines)
